"""The same gateway tools over MCP stdio, on the saddle's protocol contract.

PURPOSE
    Serve the identical closed tool surface to an MCP client that speaks stdio,
    without a second implementation of anything that matters. Two rules make
    that a fact rather than an intention:

      1. **The method vocabulary comes from ``tools.handle_rpc``.** The HTTP
         server and this module call the same function with the same arguments.
         A tool that appears on one surface appears on the other because there
         is only one list.

      2. **The wire contract comes from ``intentops_saddle_mcp.protocol``.**
         The protocol revision, the JSON-RPC error codes, the ``Session``
         shape, and the ``_meta.authorization`` location of the bearer token
         are IMPORTED from the module that implements the MCP subset. They are
         not copied here, because a second copy of a wire contract is a second
         thing to drift and nothing would notice when it did.

    WHAT THIS MODULE DOES OWN, AND WHY. It owns the ordering of the checks and
    the identity of the credential:

        stand-down  ->  authentication  ->  handshake  ->  method

    A stood-down node does not authenticate anybody, and an unauthenticated
    caller never reaches a tool. The credential is the GATEWAY's token, not
    the MCP saddle's -- two surfaces that rotate independently must not share
    one secret, or revoking a client on one locks out every caller on the
    other.

    WHY NOT SIMPLY CALL THE SADDLE'S ``handle_message``. It dispatches to the
    saddle's own six governance tools, which are a different surface with a
    different purpose. Calling it would serve those tools, not these. What is
    reusable there is the CONTRACT, and that is exactly what is imported.

WRITE MODEL
    None -- one in-memory ``Session`` per process, nothing persisted here.
    Appends go through ``ledger.py`` and approvals through the queue, each
    declaring its own model.

BLIND SPOTS
    * A bearer token over stdio is two processes on one machine sharing a
      pipe; the token adds little there and everything over a network
      transport, which this module does not provide.
    * ``clientInfo.name`` is self-reported and is used as the harness id. It is
      authorisation on top of authentication, never identification.
    * A malformed frame is answered with a JSON-RPC error and the loop
      continues. A caller that floods malformed frames is a denial of service
      this layer does not bound.
    * NOTHING ON STDOUT BUT FRAMES. A stray print corrupts the stream and the
      client sees a parse error it cannot attribute; diagnostics go to stderr.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Optional, TextIO

# The wire contract, imported and not forked. Every name below is the MCP
# saddle's definition; this module adds no code of its own to any of them.
from intentops_saddle_mcp.protocol import (
    ERR_INTERNAL,
    ERR_INVALID_PARAMS,
    ERR_INVALID_REQUEST,
    ERR_NOT_INITIALIZED,
    ERR_PARSE,
    ERR_STOOD_DOWN,
    ERR_UNAUTHENTICATED,
    PROTOCOL_VERSION,
    Session,
)

from . import ledger, tokens
from .tools import (
    BUILTIN_TOOL_NAMES,
    GatewayContext,
    RpcError,
    ToolRefused,
    handle_rpc,
    list_tools,
    load_gateway_context,
)

__all__ = [
    "PROTOCOL_VERSION",
    "SERVER_INFO",
    "Session",
    "handle_message",
    "run_stdio",
]

SERVER_INFO: Dict[str, Any] = {
    "name": "intentops-gateway",
    "title": "IntentOps governed tool gateway",
    "version": "0.1.0",
}


def _result(req_id: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": payload}


def _error(req_id: Any, code: int, message: str,
           data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    body: Dict[str, Any] = {"code": code, "message": message}
    if data:
        body["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": body}


def _presented_token(params: Mapping[str, Any]) -> Optional[str]:
    """Pull the bearer token out of the request's ``_meta``.

    The MCP specification reserves ``_meta`` on every request for exactly this
    kind of transport-independent metadata, which is why the token rides there
    rather than in a tool argument: a credential in an argument ends up in tool
    logs, in transcripts, and in this gateway's own ledger.

    The header parsing itself is ``tokens.bearer_from_header`` -- one
    definition of "how a bearer value is read", shared with the HTTP surface.
    """
    meta = params.get("_meta")
    if not isinstance(meta, Mapping):
        return None
    for key in ("authorization", "intentops/authorization", "Authorization"):
        raw = meta.get(key)
        if isinstance(raw, str) and raw.strip():
            return tokens.bearer_from_header(raw)
    return None


def handle_message(message: Any, *, session: Session,
                   gctx: GatewayContext) -> Optional[Dict[str, Any]]:
    """Handle one decoded JSON-RPC message. Returns a frame, or None for a notification."""
    if not isinstance(message, Mapping):
        return _error(None, ERR_INVALID_REQUEST,
                      "a JSON-RPC message must be an object")
    req_id = message.get("id")
    method = message.get("method")
    is_notification = "id" not in message
    if not isinstance(method, str) or not method:
        return None if is_notification else _error(
            req_id, ERR_INVALID_REQUEST,
            "`method` is required and must be a string")
    params = message.get("params")
    if params is None:
        params = {}
    if not isinstance(params, Mapping):
        return None if is_notification else _error(
            req_id, ERR_INVALID_PARAMS,
            "`params` must be an object when present")

    # --- 1. stand-down outranks everything, including authentication -------
    if gctx.node.halted:
        _record(gctx, {"kind": "refusal", "method": method, "why": "stood-down",
                       "transport": "mcp-stdio"})
        return None if is_notification else _error(
            req_id, ERR_STOOD_DOWN,
            f"this node is stood down: {gctx.node.halted}",
            {"remedy": "remove the stand-down marker deliberately; a "
                       "stood-down node is OFF, not broken"})

    # --- 2. authentication, on every request, with no anonymous fall-through
    presented = _presented_token(params)
    try:
        authenticated = tokens.verify(gctx.node_root, presented)
    except tokens.AuthUnavailable as exc:
        _record(gctx, {"kind": "refusal", "method": method,
                       "why": "auth-unavailable", "transport": "mcp-stdio"})
        return None if is_notification else _error(
            req_id, ERR_UNAUTHENTICATED, str(exc))
    if not authenticated:
        _record(gctx, {"kind": "refusal", "method": method,
                       "why": "unauthenticated", "transport": "mcp-stdio",
                       "token_presented": presented is not None})
        return None if is_notification else _error(
            req_id, ERR_UNAUTHENTICATED,
            "every request must carry this node's gateway token in "
            "`params._meta.authorization`; there is no anonymous identity on "
            "this gateway",
            {"presented": presented is not None})

    # --- 3. the handshake ---------------------------------------------------
    if method == "initialize":
        return _handle_initialize(req_id, params, session=session, gctx=gctx)
    if method.startswith("notifications/"):
        if method == "notifications/initialized":
            session.initialized = True
        return None
    if not session.initialized:
        return _error(req_id, ERR_NOT_INITIALIZED,
                      "`initialize` must complete before any other method")

    # --- 4. the method, through the SAME dispatch the HTTP surface uses -----
    harness = session.client_name.strip() or None
    try:
        payload = handle_rpc(gctx, method, params, harness=harness)
    except ToolRefused as exc:
        # A tool-level refusal is a RESULT with isError, per the MCP tool-error
        # convention: the client is meant to see it, not treat it as a
        # transport fault. A protocol error would hide a governance answer.
        return _result(req_id, {
            "content": [{"type": "text", "text": str(exc)}],
            "structuredContent": {"error": str(exc), **exc.payload},
            "isError": True,
        })
    except RpcError as exc:
        return _error(req_id, exc.code, str(exc), exc.data)
    except ledger.LedgerUnavailable:
        raise  # the loop owns this: an absent audit trail refuses loudly
    except Exception as exc:  # a failure to answer is never a hollow success
        return _error(req_id, ERR_INTERNAL,
                      f"{method} failed: {type(exc).__name__}: {exc}")
    if method == "ping":
        return _result(req_id, payload)
    if method == "tools/list":
        return _result(req_id, payload)
    return _result(req_id, {
        "content": [{"type": "text",
                     "text": json.dumps(payload, indent=2, default=str)}],
        "structuredContent": payload,
        "isError": False,
    })


def _handle_initialize(req_id: Any, params: Mapping[str, Any], *,
                       session: Session, gctx: GatewayContext) -> Dict[str, Any]:
    info = params.get("clientInfo")
    name = version = ""
    if isinstance(info, Mapping):
        name = str(info.get("name") or "").strip()
        version = str(info.get("version") or "").strip()
    session.client_name = name
    session.client_version = version
    session.initialized = True
    declared = params.get("capabilities")
    session.client_declared = dict(declared) if isinstance(declared, Mapping) else {}
    _record(gctx, {"kind": "session", "method": "initialize",
                   "harness": name or "undeclared", "client_version": version,
                   "transport": "mcp-stdio"})
    return _result(req_id, {
        "protocolVersion": PROTOCOL_VERSION,
        # Tools only. Every capability this server does NOT declare is one it
        # cannot be asked for, which is the cheapest bound there is.
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": SERVER_INFO,
        "instructions": (
            "IntentOps governed tool gateway. A closed built-in surface ("
            + ", ".join(BUILTIN_TOOL_NAMES) +
            ") plus backend tools addressed as <backend>__<tool>, which are "
            "default-deny per harness. An untagged tool classifies T4 and is "
            "refused. Call intentops.boot first; intentops.status states the "
            "one thing this gateway cannot do."
        ),
    })


def _record(gctx: GatewayContext, event: Dict[str, Any]) -> None:
    ledger.append_event(gctx.node_root, event)


def run_stdio(stdin: TextIO, stdout: TextIO, stderr: TextIO,
              *, gctx: Optional[GatewayContext] = None) -> int:
    """The transport loop: newline-delimited JSON-RPC. Returns an exit code."""
    if gctx is None:
        try:
            gctx = load_gateway_context()
        except Exception as exc:
            print(f"intentops gateway (mcp): refusing to serve: {exc}", file=stderr)
            return 2
    try:
        tokens.load_record(gctx.node_root)
    except tokens.AuthUnavailable as exc:
        print(f"intentops gateway (mcp): NO TOKEN MINTED -- every request will "
              f"be refused. {exc}", file=stderr)
    session = Session()
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            _write(stdout, _error(None, ERR_PARSE, f"parse error: {exc}"))
            continue
        try:
            frame = handle_message(message, session=session, gctx=gctx)
        except ledger.LedgerUnavailable as exc:
            print(f"intentops gateway (mcp): ledger unavailable, refusing: "
                  f"{exc}", file=stderr)
            _write(stdout, _error(
                message.get("id") if isinstance(message, dict) else None,
                ERR_INTERNAL, f"audit ledger unavailable: {exc}"))
            continue
        if frame is not None:
            _write(stdout, frame)
    return 0


def _write(stdout: TextIO, frame: Any) -> None:
    stdout.write(json.dumps(frame, default=str) + "\n")
    stdout.flush()


def tool_surface_matches_http() -> bool:
    """True iff the MCP surface is the HTTP surface. Used by the tests.

    Exists as a function rather than a comment because "the same tools over
    both transports" is a CLAIM, and a claim with no instrument is a slogan.
    """
    return {t["name"] for t in list_tools()} == set(BUILTIN_TOOL_NAMES)
