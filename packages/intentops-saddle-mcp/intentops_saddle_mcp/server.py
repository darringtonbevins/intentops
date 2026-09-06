"""The stdio server loop, the token minter, and the selftest.

PURPOSE
    Own the transport, and only the transport. MCP's stdio transport is
    newline-delimited JSON-RPC: one JSON object per line on stdin, one per line
    on stdout, and NOTHING else on stdout ever -- a stray print corrupts the
    stream and the client sees a parse error it cannot attribute. Diagnostics
    go to stderr, always.

WRITE MODEL
    None here. ``--mint-token`` delegates to ``auth.py`` (locked whole-file
    replace) and the request path appends through ``ledger.py`` (append-only
    JSONL). This module writes nothing itself.

BLIND SPOTS
    * The loop is single-threaded and handles one request at a time. That is
      correct for stdio (one client, one pipe) and would be wrong for any
      network transport, which this module does not provide.
    * A client that closes stdin mid-frame ends the loop. Partial frames are
      not buffered across a close.
    * ``--selftest`` proves the refusal paths CAN fire. It does not prove the
      host honours a refusal, which is the S2 gap and is not observable here.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, List, Optional, TextIO, Tuple

from . import ledger
from .auth import mint
from .node import EstateUnreadable, NodeContext, load_context
from .protocol import PROTOCOL_VERSION, Session, handle_message
from .registry import RegistryUnavailable, SaddleRow, load_row

__all__ = ["main", "run_stdio", "selftest"]


def _load_row_or_reason(ctx: NodeContext) -> Tuple[Optional[SaddleRow], Optional[str]]:
    try:
        return load_row(ctx.repo_root), None
    except RegistryUnavailable as exc:
        return None, str(exc)


def run_stdio(stdin: TextIO, stdout: TextIO, stderr: TextIO) -> int:
    """The transport loop. Returns an exit code."""
    try:
        ctx = load_context()
    except EstateUnreadable as exc:
        print(f"intentops-saddle-mcp: refusing to serve: {exc}", file=stderr)
        return 2
    row, row_error = _load_row_or_reason(ctx)
    if row_error:
        print(f"intentops-saddle-mcp: allowlist unreadable, every call will be "
              f"refused: {row_error}", file=stderr)
    session = Session()
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            _write(stdout, {"jsonrpc": "2.0", "id": None,
                            "error": {"code": -32700,
                                      "message": f"parse error: {exc}"}})
            continue
        try:
            frame = handle_message(message, session=session, ctx=ctx,
                                   row=row, row_error=row_error)
        except ledger.LedgerUnavailable as exc:
            # The audit trail is not optional. A governance surface whose
            # ledger has gone away refuses loudly rather than serving blind.
            print(f"intentops-saddle-mcp: ledger unavailable, refusing: {exc}",
                  file=stderr)
            _write(stdout, {"jsonrpc": "2.0", "id": message.get("id")
                            if isinstance(message, dict) else None,
                            "error": {"code": -32603,
                                      "message": f"audit ledger unavailable: {exc}"}})
            continue
        if frame is not None:
            _write(stdout, frame)
    return 0


def _write(stdout: TextIO, frame: Any) -> None:
    stdout.write(json.dumps(frame, default=str) + "\n")
    stdout.flush()


# ---------------------------------------------------------------------------
# selftest -- a detector that has never fired is indistinguishable from a
# broken one
# ---------------------------------------------------------------------------


def selftest() -> Tuple[bool, str]:
    """Prove every refusal path actually fires. Returns ``(ok, report)``."""
    import tempfile

    from intentops_core.gate import RealityIndicators

    from .discover import PayloadRefused, describe_host

    failures: List[str] = []
    notes: List[str] = []

    def expect(label: str, cond: bool) -> None:
        (notes if cond else failures).append(
            f"{'ok  ' if cond else 'FAIL'} {label}")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "node"
        (root / ".intentops").mkdir(parents=True)
        ctx = NodeContext(node_root=root, repo_root=None, root_source="selftest",
                          indicators=RealityIndicators(),
                          estate_map_present=False, halted=None)
        row = SaddleRow(host_id="mcp-hosted", grade="candidate",
                        allowed_clients=("known-client",), source="selftest")
        closed = SaddleRow(host_id="mcp-hosted", grade="candidate",
                           allowed_clients=(), source="selftest")

        # 1. no auth record at all -> every request refused, never anonymous.
        frame = handle_message({"jsonrpc": "2.0", "id": 1, "method": "ping"},
                               session=Session(), ctx=ctx, row=row)
        expect("an unminted node refuses ping",
               bool(frame) and frame.get("error", {}).get("code") == -32001)

        token = mint(root)
        meta = {"_meta": {"authorization": f"Bearer {token}"}}

        # 2. a wrong token is refused.
        frame = handle_message(
            {"jsonrpc": "2.0", "id": 2, "method": "ping",
             "params": {"_meta": {"authorization": "Bearer not-the-token"}}},
            session=Session(), ctx=ctx, row=row)
        expect("a wrong token is refused",
               bool(frame) and frame.get("error", {}).get("code") == -32001)

        # 3. an empty allowlist refuses everything.
        frame = handle_message(
            {"jsonrpc": "2.0", "id": 3, "method": "initialize",
             "params": dict(meta, clientInfo={"name": "known-client",
                                              "version": "1"})},
            session=Session(), ctx=ctx, row=closed)
        expect("an empty allowlist refuses a known client",
               bool(frame) and frame.get("error", {}).get("code") == -32002)

        # 4. a good handshake succeeds and negotiates the version.
        session = Session()
        frame = handle_message(
            {"jsonrpc": "2.0", "id": 4, "method": "initialize",
             "params": dict(meta, clientInfo={"name": "known-client",
                                              "version": "1"})},
            session=session, ctx=ctx, row=row)
        expect("a good handshake succeeds",
               bool(frame) and frame.get("result", {}).get(
                   "protocolVersion") == PROTOCOL_VERSION)

        # 5. an unallowlisted client is refused at initialize.
        frame = handle_message(
            {"jsonrpc": "2.0", "id": 5, "method": "initialize",
             "params": dict(meta, clientInfo={"name": "stranger", "version": "1"})},
            session=Session(), ctx=ctx, row=row)
        expect("an unallowlisted client is refused",
               bool(frame) and frame.get("error", {}).get("code") == -32002)

        # 6. tools/list serves the closed surface.
        frame = handle_message({"jsonrpc": "2.0", "id": 6, "method": "tools/list",
                                "params": dict(meta)},
                               session=session, ctx=ctx, row=row)
        names = {t["name"] for t in frame.get("result", {}).get("tools", [])}
        expect("the tool surface is exactly six named tools",
               names == {"intentops.classify", "intentops.gate", "intentops.status",
                         "intentops.council", "intentops.imprint_verify",
                         "intentops.discover"})

        # 7. a stood-down node refuses everything, ahead of auth.
        (root / ".intentops" / "halt.marker").write_text("selftest", encoding="utf-8")
        halted = NodeContext(node_root=root, repo_root=None, root_source="selftest",
                             indicators=ctx.indicators, estate_map_present=False,
                             halted="selftest")
        frame = handle_message({"jsonrpc": "2.0", "id": 7, "method": "tools/list",
                                "params": dict(meta)},
                               session=session, ctx=halted, row=row)
        expect("a stood-down node refuses tools/list",
               bool(frame) and frame.get("error", {}).get("code") == -32003)
        (root / ".intentops" / "halt.marker").unlink()

        # 8. S6 refuses an artifact and accepts a descriptor.
        refused = False
        try:
            describe_host({"skill_body": "#!/bin/sh\nrm -rf /"})
        except PayloadRefused:
            refused = True
        expect("S6 refuses an executable-shaped declaration", refused)
        caps = describe_host({"supports_subagents": True,
                              "context_ceiling_tokens": 1_000_000})
        expect("S6 accepts a typed descriptor",
               caps.supports_subagents is True
               and caps.context_ceiling_tokens == 1_000_000)

        # 9. an unknown tool is refused, never guessed.
        frame = handle_message(
            {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
             "params": dict(meta, name="intentops.not_a_tool", arguments={})},
            session=session, ctx=ctx, row=row)
        expect("an unknown tool is refused",
               bool(frame) and frame.get("result", {}).get("isError") is True)

    report = "\n".join(notes + failures)
    return (not failures), report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="intentops-saddle-mcp",
        description=("The IntentOps hosted saddle: an MCP stdio server that "
                     "exposes the gate, the councils and the imprint check to "
                     "any MCP client, with authentication and a client "
                     "allowlist ON by default."))
    parser.add_argument("--mint-token", action="store_true",
                        help=("mint (or rotate) this node's bearer token and "
                              "print it ONCE; only its digest is stored"))
    parser.add_argument("--selftest", action="store_true",
                        help="prove every refusal path can actually fire")
    args = parser.parse_args(argv)

    if args.selftest:
        ok, report = selftest()
        print(report, file=sys.stderr)
        return 0 if ok else 1

    if args.mint_token:
        node_root, source = load_context().node_root, "resolved"
        token = mint(node_root)
        print(f"node root : {node_root} ({source})", file=sys.stderr)
        print("Give this token to the MCP client. It is shown once and only "
              "its digest is stored; re-run to rotate.", file=sys.stderr)
        print(token)
        return 0

    return run_stdio(sys.stdin, sys.stdout, sys.stderr)


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
