"""JSON-RPC over HTTP, stdlib only, authenticated on every request.

PURPOSE
    Own the transport, and only the transport. One endpoint, one method
    (``POST``), one content type, one credential check. The port comes from
    ``config/ports.yaml`` and the binding is loopback unless an operator says
    otherwise -- a governance surface that binds a public interface by default
    is a public API returning opinions about other people's actions.

    AUTHENTICATION IS THE FIRST THING AND THE ONLY UNCONDITIONAL THING. No
    ``Authorization`` header, a blank one, or a wrong token is **401**. There
    is no anonymous identity, no localhost bypass, no ``--no-auth`` flag, and
    no environment variable that opens one. The reference gateway resolved an
    absent identity to the literal string "anonymous" and shipped its
    enforcement behind a flag that was never flipped; this file is the
    correction, and the correction has no switch.

    STATELESS. Each request is judged on its own: credential, harness name,
    method, done. Nothing is remembered between requests, so there is no
    session to steal and no trust that accrues to a caller because it has been
    connected for a while. That is the transport-layer expression of the
    tier model -- "every request judged on its own" beats "a session, once
    opened, is trusted".

    WHY http.server AND NOT A FRAMEWORK. The whole surface is: parse a JSON
    body, read one header, call ``handle_rpc``, write a JSON body. Every
    dependency added here enlarges the trusted base of the one process whose
    job is refusing unreviewed third-party artifacts.

WRITE MODEL
    None here. Every request appends through ``ledger.py`` (append-only JSONL
    under StoreLock); the token ceremony writes through ``tokens.py`` (locked
    whole-file replace). This module writes nothing itself.

BLIND SPOTS
    * A bearer token over CLEARTEXT HTTP is theatre. The default binding is
      loopback for that reason. Any other binding owes a TLS terminator in
      front of it and nothing here can check that one exists -- the server
      WARNS on a non-loopback bind rather than pretending it is fine.
    * The harness name in ``X-Harness-Id`` is SELF-REPORTED. The bearer token
      is the authentication; the name is authorisation on top of it, and a
      caller with a valid token may present any name. Stated because
      "allowlisted" reads like "verified" and is not.
    * Nothing here rate-limits. Loopback plus 256 bits of entropy is the whole
      defence against guessing; a network-facing deployment owes a limiter of
      its own.
    * ``ThreadingHTTPServer`` gives one thread per connection with no cap. A
      caller that opens many connections is a denial of service this layer
      does not bound.
"""

from __future__ import annotations

import io
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, TextIO, Tuple

from . import ledger, tokens
from .tools import (
    ERR_INTERNAL,
    ERR_INVALID_PARAMS,
    ERR_UNAUTHENTICATED,
    GatewayContext,
    RpcError,
    ToolRefused,
    handle_rpc,
    load_gateway_context,
)

__all__ = [
    "DEFAULT_BINDING",
    "DEFAULT_PORT",
    "GatewayServer",
    "HARNESS_HEADER",
    "resolve_port",
    "selftest",
    "serve",
]

#: The one place a default lives. ``config/ports.yaml`` is the registry; this
#: constant is the fallback used only when the registry cannot be read, and it
#: is the same number the registry carries.
DEFAULT_PORT = 8000
DEFAULT_BINDING = "127.0.0.1"

HARNESS_HEADER = "X-Harness-Id"
_AUTH_HEADER = "Authorization"

#: Bodies larger than this are refused before they are parsed. A governance
#: surface has no legitimate megabyte-sized request, and an unbounded read is
#: a memory exhaustion nobody declared.
_MAX_BODY_BYTES = 1_000_000

_ERR_PARSE = -32700
_ERR_INVALID_REQUEST = -32600

_LOOPBACK = ("127.0.0.1", "::1", "localhost")


def resolve_port(repo_root: Optional[Path]) -> Tuple[int, str]:
    """The gateway port, and where the number came from.

    Reads ``config/ports.yaml``. A missing or malformed registry falls back to
    :data:`DEFAULT_PORT` AND SAYS SO in the returned source string, so a
    surprising port is traceable to the rule that produced it rather than to a
    silent default.
    """
    if repo_root is None:
        return DEFAULT_PORT, "built-in default (no framework checkout located)"
    path = Path(repo_root) / "config" / "ports.yaml"
    if not path.is_file():
        return DEFAULT_PORT, f"built-in default ({path} is missing)"
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        row = ((data.get("services") or {}).get("gateway") or {})
        port = row.get("port")
        if not isinstance(port, int):
            return DEFAULT_PORT, f"built-in default ({path} declares no integer gateway port)"
        return port, str(path)
    except Exception as exc:  # a malformed registry must not wedge the server
        return DEFAULT_PORT, f"built-in default ({path} is unreadable: {exc})"


class _Handler(BaseHTTPRequestHandler):
    """One request. Auth first, then the method, then nothing remembered."""

    server_version = "IntentOpsGateway/0.1"
    sys_version = ""  # never advertise the interpreter build

    # -- plumbing ---------------------------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:
        """Diagnostics go to the server's stderr sink, never to stdout."""
        stream: TextIO = getattr(self.server, "diag", sys.stderr)
        stream.write("gateway: " + (fmt % args) + "\n")

    def _send(self, status: int, payload: Mapping[str, Any]) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # A governance answer is never cacheable: a stale verdict is a wrong one.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, req_id: Any, code: int, message: str,
               data: Optional[Dict[str, Any]] = None) -> None:
        frame: Dict[str, Any] = {"jsonrpc": "2.0", "id": req_id,
                                 "error": {"code": code, "message": message}}
        if data:
            frame["error"]["data"] = data
        self._send(status, frame)

    # -- the surface ------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's contract
        """There is no GET surface. Stated, rather than 404'd as if by accident."""
        self._error(405, None, _ERR_INVALID_REQUEST,
                    "this gateway serves JSON-RPC over POST only; there is no "
                    "GET surface, deliberately -- a browsable governance "
                    "endpoint is an invitation")

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's contract
        gctx: GatewayContext = self.server.gctx  # type: ignore[attr-defined]

        # 1. AUTHENTICATION. Before the body is read, before the method is
        #    known, and with no path that skips it.
        presented = tokens.bearer_from_header(self.headers.get(_AUTH_HEADER))
        harness = (self.headers.get(HARNESS_HEADER) or "").strip() or None
        try:
            authenticated = tokens.verify(gctx.node_root, presented)
        except tokens.AuthUnavailable as exc:
            self._audit(gctx, {"kind": "refusal", "why": "auth-unavailable",
                               "harness": harness or "undeclared"})
            self._error(401, None, ERR_UNAUTHENTICATED, str(exc))
            return
        if not authenticated:
            self._audit(gctx, {"kind": "refusal", "why": "unauthenticated",
                               "harness": harness or "undeclared",
                               "token_presented": presented is not None})
            self._error(401, None, ERR_UNAUTHENTICATED,
                        "every request must carry this node's gateway token as "
                        "`Authorization: Bearer <token>`; there is no anonymous "
                        "identity on this gateway",
                        {"presented": presented is not None})
            return

        # 2. the body.
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._error(400, None, _ERR_INVALID_REQUEST,
                        "Content-Length is not an integer")
            return
        if length > _MAX_BODY_BYTES:
            self._error(413, None, _ERR_INVALID_REQUEST,
                        f"request body exceeds {_MAX_BODY_BYTES} bytes")
            return
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            message = json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._error(400, None, _ERR_PARSE, f"parse error: {exc}")
            return
        if not isinstance(message, dict):
            self._error(400, None, _ERR_INVALID_REQUEST,
                        "a JSON-RPC message must be an object")
            return

        req_id = message.get("id")
        method = message.get("method")
        if not isinstance(method, str) or not method:
            self._error(400, req_id, _ERR_INVALID_REQUEST,
                        "`method` is required and must be a string")
            return
        params = message.get("params")
        if params is None:
            params = {}
        if not isinstance(params, Mapping):
            self._error(400, req_id, ERR_INVALID_PARAMS,
                        "`params` must be an object when present")
            return

        # 3. the method.
        try:
            payload = handle_rpc(gctx, method, params, harness=harness)
        except ToolRefused as exc:
            # A governance refusal is a RESULT, not a fault. Rendering it as a
            # transport error teaches clients to retry it.
            self._send(200, {"jsonrpc": "2.0", "id": req_id, "result": {
                "content": [{"type": "text", "text": str(exc)}],
                "structuredContent": {"error": str(exc), **exc.payload},
                "isError": True,
            }})
            return
        except RpcError as exc:
            # JSON-RPC's own convention: a well-formed request that the server
            # answers with an error is HTTP 200 carrying an `error` object. The
            # ONE exception above this line is authentication, which is a real
            # 401 -- a transport-level refusal deserves a transport-level code,
            # and a client should not have to parse a body to learn it was not
            # let in.
            self._error(200, req_id, exc.code, str(exc), exc.data)
            return
        except ledger.LedgerUnavailable as exc:
            self._error(500, req_id, ERR_INTERNAL,
                        f"audit ledger unavailable, refusing to serve blind: {exc}")
            return
        except Exception as exc:  # a failure to answer is never a hollow success
            self.log_message("unhandled %s on %s", type(exc).__name__, method)
            self._error(500, req_id, ERR_INTERNAL,
                        f"{method} failed: {type(exc).__name__}: {exc}")
            return
        self._send(200, {"jsonrpc": "2.0", "id": req_id, "result": payload})

    def _audit(self, gctx: GatewayContext, event: Dict[str, Any]) -> None:
        """Record a door-level refusal. A ledger failure is surfaced, not hidden."""
        try:
            ledger.append_event(gctx.node_root, event)
        except ledger.LedgerUnavailable as exc:
            self.log_message("LEDGER UNAVAILABLE: %s", exc)


class GatewayServer:
    """A running gateway, on a real socket. Context-managed, always closed."""

    def __init__(self, gctx: GatewayContext, *, host: str = DEFAULT_BINDING,
                 port: int = DEFAULT_PORT, diag: Optional[TextIO] = None) -> None:
        self.gctx = gctx
        self._httpd = ThreadingHTTPServer((host, port), _Handler)
        self._httpd.gctx = gctx  # type: ignore[attr-defined]
        self._httpd.diag = diag or sys.stderr  # type: ignore[attr-defined]
        self._thread: Optional[threading.Thread] = None

    @property
    def host(self) -> str:
        return self._httpd.server_address[0]

    @property
    def port(self) -> int:
        return int(self._httpd.server_address[1])

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    @property
    def is_loopback(self) -> bool:
        return self.host in _LOOPBACK

    def start(self) -> "GatewayServer":
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        name="intentops-gateway", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> "GatewayServer":
        return self.start()

    def __exit__(self, *exc: Any) -> bool:
        self.stop()
        return False


def serve(*, host: str = DEFAULT_BINDING, port: Optional[int] = None,
          start: Optional[str] = None, diag: Optional[TextIO] = None) -> int:
    """Run the gateway in the foreground. Returns an exit code."""
    stream = diag or sys.stderr
    try:
        gctx = load_gateway_context(start)
    except Exception as exc:
        print(f"intentops gateway: refusing to serve: {exc}", file=stream)
        return 2
    chosen, source = (port, "--port") if port else resolve_port(gctx.repo_root)
    try:
        server = GatewayServer(gctx, host=host, port=chosen, diag=stream)
    except OSError as exc:
        print(f"intentops gateway: cannot bind {host}:{chosen}: {exc}", file=stream)
        return 2

    print(f"intentops gateway: node {gctx.node_root} "
          f"({gctx.node.root_source})", file=stream)
    print(f"intentops gateway: listening on {server.url} (port from {source})",
          file=stream)
    if not server.is_loopback:
        print("intentops gateway: WARNING -- this is not a loopback binding. A "
              "bearer token over cleartext HTTP is theatre; put a TLS "
              "terminator in front of it. Nothing here can check that you did.",
              file=stream)
    try:
        tokens.load_record(gctx.node_root)
    except tokens.AuthUnavailable as exc:
        print(f"intentops gateway: NO TOKEN MINTED -- every request will be "
              f"refused. {exc}", file=stream)
    if gctx.policy_error:
        print(f"intentops gateway: allowlist unreadable, every backend call "
              f"will be refused: {gctx.policy_error}", file=stream)
    if gctx.registry_error:
        print(f"intentops gateway: backend registry unreadable: "
              f"{gctx.registry_error}", file=stream)

    with server:
        try:
            while True:
                threading.Event().wait(3600)
        except KeyboardInterrupt:
            print("intentops gateway: stopping", file=stream)
    return 0


# ---------------------------------------------------------------------------
# selftest -- a detector that has never fired is indistinguishable from a
# broken one
# ---------------------------------------------------------------------------


def selftest() -> Tuple[bool, str]:
    """Prove the refusal paths fire against a REAL socket. ``(ok, report)``."""
    import tempfile
    import urllib.error
    import urllib.request

    from intentops_core.gate import RealityIndicators
    from intentops_saddle_mcp.node import NodeContext

    from . import backends as backends_mod

    failures = []
    notes = []

    def expect(label: str, cond: bool) -> None:
        (notes if cond else failures).append(f"{'ok  ' if cond else 'FAIL'} {label}")

    def call(url: str, body: Dict[str, Any],
             headers: Optional[Dict[str, str]] = None) -> Tuple[int, Dict[str, Any]]:
        request = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "node"
        (root / ".intentops").mkdir(parents=True)
        gctx = GatewayContext(
            node=NodeContext(node_root=root, repo_root=None,
                             root_source="selftest",
                             indicators=RealityIndicators(),
                             estate_map_present=False, halted=None),
            registry=backends_mod.BackendRegistry(backends={}, source="selftest"),
            policy=backends_mod.HarnessPolicy(grants={}, always_denied=(),
                                              source="selftest"))
        with GatewayServer(gctx, host=DEFAULT_BINDING, port=0,
                           diag=io.StringIO()) as server:
            url = server.url
            status, frame = call(url, {"jsonrpc": "2.0", "id": 1, "method": "ping"})
            expect("an unminted node refuses ping with 401",
                   status == 401 and frame["error"]["code"] == ERR_UNAUTHENTICATED)

            token = tokens.mint(root)
            status, frame = call(url, {"jsonrpc": "2.0", "id": 2, "method": "ping"},
                                 {"Authorization": "Bearer not-the-token"})
            expect("a wrong token is refused with 401", status == 401)

            auth = {"Authorization": f"Bearer {token}"}
            status, frame = call(url, {"jsonrpc": "2.0", "id": 3, "method": "ping"},
                                 auth)
            expect("a good token reaches ping",
                   status == 200 and frame.get("result") == {})

            status, frame = call(url, {"jsonrpc": "2.0", "id": 4,
                                       "method": "tools/call",
                                       "params": {"name": "anything__do_it"}}, auth)
            expect("an ungranted backend is refused",
                   frame.get("result", {}).get("isError") is True)

            status, frame = call(url, {"jsonrpc": "2.0", "id": 5, "method": "GET"},
                                 auth)
            expect("an unknown method is refused",
                   frame.get("error", {}).get("code") == -32601)
    report = "\n".join(notes + failures)
    return (not failures), report
