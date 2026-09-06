"""The MCP protocol subset, over JSON-RPC 2.0, with the gate in front of it.

PURPOSE
    Implement exactly the part of the Model Context Protocol (revision
    2025-06-18) that a governance server needs -- ``initialize`` with
    capability negotiation, ``tools/list``, ``tools/call``, ``ping`` -- and
    nothing else. No resources, no prompts, no sampling, no roots, no
    completion. Every one of those would be a surface with its own blast
    radius, and a governance server that grows surfaces it does not need is
    arguing against itself.

    NO MCP SDK DEPENDENCY. The subset is four methods of framed JSON over a
    pipe; the stdlib does it in a page. An SDK here would be a third-party
    artifact loading into the one process whose whole job is to say no to
    third-party artifacts, and its transitive dependencies would be a larger
    trusted base than the server itself.

    THE TWO CONTROLS THAT SHIPPED DISABLED UPSTREAM ARE ON HERE, AND FIRST:

      1. authentication -- every request carries the per-node bearer token or
         it is refused. There is no anonymous identity to resolve to.
      2. the client allowlist -- default-deny by name, and an empty list
         refuses everything.

    Order is load-bearing: stand-down, then authentication, then the
    allowlist, then the method. A stood-down node does not authenticate
    anybody, and an unauthenticated caller never reaches a tool.

WRITE MODEL
    None -- this module holds one in-memory ``Session`` per process and writes
    nothing. Appends go through ``ledger.py``, which declares its own model.

BLIND SPOTS
    * A bearer token over stdio is two processes on one machine sharing a
      pipe; the token adds little there and everything over a network
      transport, where the channel MUST be encrypted by a layer beneath this
      one. Nothing here can verify that it is.
    * ``clientInfo.name`` is self-reported. The allowlist is AUTHORISATION on
      top of authentication, never identification -- a caller with a valid
      token may present any name.
    * A malformed frame is refused with a JSON-RPC error and the connection
      continues. A caller that floods malformed frames is a denial of service
      this layer does not bound.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple

from . import ledger
from .auth import AuthUnavailable, verify
from .node import NodeContext
from .registry import RegistryUnavailable, SaddleRow
from .tools import ToolError, dispatch, list_tools

__all__ = [
    "ERR_ALLOWLIST",
    "ERR_UNAUTHENTICATED",
    "PROTOCOL_VERSION",
    "SERVER_INFO",
    "Session",
    "handle_message",
]

#: The MCP revision this subset implements. A client asking for a different
#: revision is answered with OURS, per the specification's negotiation rule --
#: the client then decides whether it can proceed.
PROTOCOL_VERSION = "2025-06-18"

SERVER_INFO: Dict[str, Any] = {
    "name": "intentops-saddle-mcp",
    "title": "IntentOps hosted saddle",
    "version": "0.1.0",
}

# JSON-RPC standard codes.
ERR_PARSE = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL = -32603
# Implementation-defined server errors, in the reserved -32000..-32099 band.
ERR_UNAUTHENTICATED = -32001
ERR_ALLOWLIST = -32002
ERR_STOOD_DOWN = -32003
ERR_NOT_INITIALIZED = -32004


@dataclass
class Session:
    """One connection's state. In memory only; nothing survives the process."""

    initialized: bool = False
    client_name: str = ""
    client_version: str = ""
    client_declared: Dict[str, Any] = field(default_factory=dict)


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
    logs, in transcripts, and in this server's own ledger.
    """
    meta = params.get("_meta")
    if not isinstance(meta, Mapping):
        return None
    for key in ("authorization", "intentops/authorization", "Authorization"):
        raw = meta.get(key)
        if isinstance(raw, str) and raw.strip():
            value = raw.strip()
            if value.lower().startswith("bearer "):
                return value[7:].strip()
            return value
    return None


def handle_message(
    message: Any,
    *,
    session: Session,
    ctx: NodeContext,
    row: Optional[SaddleRow],
    row_error: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Handle one decoded JSON-RPC message. Returns a frame, or None for a notification.

    ``row`` is the governing allowlist row; ``row_error`` states why it could
    not be read. Exactly one of the two is set, and an unreadable row refuses
    everything -- a server that cannot read its own allowlist has no basis for
    letting anything through.
    """
    if not isinstance(message, Mapping):
        return _error(None, ERR_INVALID_REQUEST, "a JSON-RPC message must be an object")
    req_id = message.get("id")
    method = message.get("method")
    is_notification = "id" not in message
    if not isinstance(method, str) or not method:
        return None if is_notification else _error(
            req_id, ERR_INVALID_REQUEST, "`method` is required and must be a string")
    params = message.get("params")
    if params is None:
        params = {}
    if not isinstance(params, Mapping):
        return None if is_notification else _error(
            req_id, ERR_INVALID_PARAMS, "`params` must be an object when present")

    # --- 1. stand-down outranks everything, including authentication -------
    if ctx.halted:
        _record(ctx, {"kind": "refusal", "method": method, "why": "stood-down"})
        return None if is_notification else _error(
            req_id, ERR_STOOD_DOWN,
            f"this node is stood down: {ctx.halted}",
            {"remedy": "remove the stand-down marker deliberately; a stood-down "
                       "node is OFF, not broken"})

    # --- 2. authentication, on every request, with no anonymous fall-through
    token = _presented_token(params)
    try:
        authenticated = verify(ctx.node_root, token)
    except AuthUnavailable as exc:
        _record(ctx, {"kind": "refusal", "method": method, "why": "auth-unavailable"})
        return None if is_notification else _error(
            req_id, ERR_UNAUTHENTICATED, str(exc))
    if not authenticated:
        _record(ctx, {"kind": "refusal", "method": method, "why": "unauthenticated",
                      "token_presented": token is not None})
        return None if is_notification else _error(
            req_id, ERR_UNAUTHENTICATED,
            "every request must carry this node's bearer token in `params._meta."
            "authorization`; there is no anonymous identity on this server",
            {"presented": token is not None})

    # --- 3. the client allowlist, default-deny ------------------------------
    if row is None:
        _record(ctx, {"kind": "refusal", "method": method, "why": "registry-unreadable"})
        return None if is_notification else _error(
            req_id, ERR_ALLOWLIST,
            row_error or "the client allowlist could not be read; every call is refused")

    if method == "initialize":
        return _handle_initialize(req_id, params, session=session, ctx=ctx, row=row)

    if method.startswith("notifications/"):
        if method == "notifications/initialized":
            session.initialized = True
        return None

    if not session.initialized:
        return _error(req_id, ERR_NOT_INITIALIZED,
                      "`initialize` must complete before any other method")

    permitted, reason = row.permits(session.client_name)
    if not permitted:
        _record(ctx, {"kind": "refusal", "method": method, "why": "allowlist",
                      "client": session.client_name})
        return _error(req_id, ERR_ALLOWLIST, reason)

    if method == "ping":
        return _result(req_id, {})
    if method == "tools/list":
        return _result(req_id, {"tools": list_tools()})
    if method == "tools/call":
        return _handle_tools_call(req_id, params, session=session, ctx=ctx)

    return _error(req_id, ERR_METHOD_NOT_FOUND,
                  f"method {method!r} is not part of this server's subset "
                  "(initialize, notifications/initialized, tools/list, "
                  "tools/call, ping)")


def _handle_initialize(req_id: Any, params: Mapping[str, Any], *,
                       session: Session, ctx: NodeContext,
                       row: SaddleRow) -> Dict[str, Any]:
    info = params.get("clientInfo")
    name = ""
    version = ""
    if isinstance(info, Mapping):
        name = str(info.get("name") or "").strip()
        version = str(info.get("version") or "").strip()
    permitted, reason = row.permits(name)
    if not permitted:
        _record(ctx, {"kind": "refusal", "method": "initialize", "why": "allowlist",
                      "client": name})
        return _error(req_id, ERR_ALLOWLIST, reason)
    session.client_name = name
    session.client_version = version
    session.initialized = True
    declared = params.get("capabilities")
    session.client_declared = dict(declared) if isinstance(declared, Mapping) else {}
    _record(ctx, {"kind": "session", "method": "initialize", "client": name,
                  "client_version": version})
    return _result(req_id, {
        "protocolVersion": PROTOCOL_VERSION,
        # Tools only. Every capability this server does NOT declare is one it
        # cannot be asked for, which is the cheapest bound there is.
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": SERVER_INFO,
        "instructions": (
            "IntentOps governance surface. Six tools, a closed set. This server "
            "computes verdicts; it cannot refuse a call the host never routes "
            "through it -- see intentops.status for the stated S2 limitation."
        ),
    })


def _handle_tools_call(req_id: Any, params: Mapping[str, Any], *,
                       session: Session, ctx: NodeContext) -> Dict[str, Any]:
    name = params.get("name")
    if not isinstance(name, str) or not name:
        return _error(req_id, ERR_INVALID_PARAMS, "`name` is required")
    args = params.get("arguments")
    if args is None:
        args = {}
    if not isinstance(args, Mapping):
        return _error(req_id, ERR_INVALID_PARAMS, "`arguments` must be an object")
    if name == "intentops.discover":
        args = dict(args)
        args.setdefault("client_name", session.client_name)
        args.setdefault("client_version", session.client_version)
        args.setdefault("protocol_version", PROTOCOL_VERSION)
    try:
        payload = dispatch(name, ctx, args)
    except ToolError as exc:
        _record(ctx, {"kind": "tool", "tool": name, "outcome": "REFUSED"})
        # A tool-level refusal is a RESULT with isError, per the MCP tool
        # error convention: the client is meant to see it, not treat it as a
        # transport fault. A protocol error would hide a governance answer.
        return _result(req_id, {
            "content": [{"type": "text", "text": str(exc)}],
            "structuredContent": {"error": str(exc), "tool": name},
            "isError": True,
        })
    except Exception as exc:  # a failure to answer is never a hollow success
        _record(ctx, {"kind": "tool", "tool": name, "outcome": "ERROR"})
        return _error(req_id, ERR_INTERNAL,
                      f"{name} failed: {type(exc).__name__}: {exc}")
    _record(ctx, {"kind": "tool", "tool": name, "outcome": "OK"})
    return _result(req_id, {
        "content": [{"type": "text", "text": json.dumps(payload, indent=2,
                                                        default=str)}],
        "structuredContent": payload,
        "isError": False,
    })


def _record(ctx: NodeContext, event: Dict[str, Any]) -> None:
    """S3. A ledger that cannot be written must not silently be skipped."""
    try:
        ledger.append_event(ctx.node_root, event)
    except ledger.LedgerUnavailable:
        # Re-raised would kill a session over a disk problem the caller cannot
        # fix mid-request; swallowed silently would be the failure this project
        # refuses. So it is surfaced on stderr by the server loop's own
        # handler, which owns the transport.
        raise
