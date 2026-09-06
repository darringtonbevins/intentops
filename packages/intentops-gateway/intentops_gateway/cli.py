"""``intentops gateway serve | status | token rotate | mcp | selftest``.

PURPOSE
    The operator's surface. Four verbs and one diagnostic, each doing exactly
    what its name says.

      serve         run the HTTP JSON-RPC gateway in the foreground
      status        what this gateway would be if it started, without starting
      token rotate  mint (or re-mint) this node's bearer token, printed ONCE
      mcp           the same tools over MCP stdio, for a client that spawns us
      selftest      prove the refusal paths can actually fire

    WHY THIS IS ITS OWN EXECUTABLE AND NOT A SUBCOMMAND OF ``intentops``.
    The dependency arrow points one way: the gateway imports the core, and the
    core imports no gateway. Welding ``gateway`` into the core's argument
    parser would invert that -- and doing it by a lazy or dynamic import would
    invert it INVISIBLY, since the direction check reads import statements and
    says so in its own blind-spot list. So the parser's ``prog`` is
    ``intentops gateway``, the help reads as one command, and the entry point
    is ``intentops-gateway``. An operator types one extra hyphen; the layering
    stays checkable.

    THE TOKEN IS PRINTED TO STDOUT AND NOWHERE ELSE. Everything explanatory
    goes to stderr, so ``intentops-gateway token rotate > /dev/null`` is a
    quiet rotation and ``$(intentops-gateway token rotate)`` captures exactly
    the credential. It is never written to a file by this tool -- that would
    re-introduce the exposure the digest-only store removes.

WRITE MODEL
    None here. ``token rotate`` delegates to ``tokens.mint`` (locked whole-file
    replace); ``serve`` and ``mcp`` append through ``ledger.py``.

BLIND SPOTS
    * ``status`` reports what could be READ, not what is running. It does not
      probe the port, because a port answering is not the same claim as this
      node's gateway answering.
    * ``token rotate`` locks out every existing holder at the instant it
      lands. That is the intended blast radius of a rotation and the command
      says so before it does it.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from . import server as server_mod
from . import tokens
from .tools import load_gateway_context

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="intentops gateway",
        description=("The IntentOps governed tool gateway: one JSON-RPC seam "
                     "every tool call passes through. Authentication is "
                     "unconditional and the backend allowlist is default-deny; "
                     "neither has an off switch."))
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="run the HTTP gateway in the foreground")
    serve.add_argument("--host", default=server_mod.DEFAULT_BINDING,
                       help=("interface to bind (default loopback). A "
                             "non-loopback bind WARNS: a bearer token over "
                             "cleartext HTTP is theatre without a TLS "
                             "terminator in front of it."))
    serve.add_argument("--port", type=int, default=None,
                       help="override the port from config/ports.yaml")

    status = sub.add_parser("status", help="what this gateway would be, without starting it")
    status.add_argument("--json", action="store_true", help="emit the raw status payload")

    token = sub.add_parser("token", help="the bearer credential")
    token_sub = token.add_subparsers(dest="token_command")
    rotate = token_sub.add_parser(
        "rotate", help="mint (or re-mint) this node's token and print it ONCE")
    rotate.add_argument("--yes", action="store_true",
                        help=("confirm the rotation. Without it the command "
                              "explains the blast radius and does nothing -- "
                              "a rotation locks out every existing holder."))

    sub.add_parser("mcp", help="serve the same tools over MCP stdio")
    sub.add_parser("selftest", help="prove the refusal paths can actually fire")
    return parser


def _cmd_serve(args: argparse.Namespace) -> int:
    return server_mod.serve(host=args.host, port=args.port)


def _cmd_status(args: argparse.Namespace) -> int:
    from .tools import _status  # the same payload intentops.status returns

    try:
        gctx = load_gateway_context()
    except Exception as exc:
        print(f"intentops gateway: cannot read this node: {exc}", file=sys.stderr)
        return 2
    payload = _status(gctx, harness=None)
    port, source = server_mod.resolve_port(gctx.repo_root)
    payload["port"] = port
    payload["port_source"] = source
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
        return 0
    print(f"node root      : {payload['node_root']} ({payload['node_root_source']})")
    print(f"repo root      : {payload['repo_root']}")
    print(f"port           : {port}  (from {source})")
    print(f"stood down     : {payload['stood_down'] or 'no'}")
    token_state = payload["token"]
    print("token          : "
          + (f"minted, rotation {token_state.get('rotation')}, "
             f"{token_state.get('minted_at')}"
             if token_state.get("minted")
             else "NOT MINTED -- every request is refused"))
    registry = payload["registry"]
    print("backends       : "
          + (f"{registry['count']} declared ({registry['source']})"
             if "count" in registry else f"UNREADABLE -- {registry.get('error')}"))
    policy = payload["policy"]
    print("allowlist      : "
          + (f"default-deny, {len(policy['granted_harnesses'])} harness(es) "
             f"granted ({policy['source']})"
             if "granted_harnesses" in policy
             else f"UNREADABLE -- {policy.get('error')} (every backend refused)"))
    print(f"invoker        : {payload['invoker']}")
    print(f"limitation     : {payload['limitation']}")
    return 0


def _cmd_token(args: argparse.Namespace) -> int:
    if args.token_command != "rotate":
        print("intentops gateway token: the only verb is `rotate`",
              file=sys.stderr)
        return 2
    try:
        gctx = load_gateway_context()
    except Exception as exc:
        print(f"intentops gateway: cannot read this node: {exc}", file=sys.stderr)
        return 2
    if not args.yes:
        print("Rotating this node's gateway token REPLACES the stored digest, "
              "so every client holding the current token is locked out at the "
              "same instant.", file=sys.stderr)
        print(f"Node: {gctx.node_root}", file=sys.stderr)
        print("Re-run with --yes to proceed. Nothing was changed.",
              file=sys.stderr)
        return 1
    value = tokens.mint(gctx.node_root)
    record = tokens.load_record(gctx.node_root)
    print(f"node root : {gctx.node_root} ({gctx.node.root_source})", file=sys.stderr)
    print(f"rotation  : {record.rotation}", file=sys.stderr)
    print("Give this token to the client. It is shown ONCE and only its digest "
          "is stored; re-run to rotate. It is not written to any file by this "
          "command.", file=sys.stderr)
    print(value)
    return 0


def _cmd_mcp(_args: argparse.Namespace) -> int:
    from . import mcp as mcp_mod
    return mcp_mod.run_stdio(sys.stdin, sys.stdout, sys.stderr)


def _cmd_selftest(_args: argparse.Namespace) -> int:
    ok, report = server_mod.selftest()
    print(report, file=sys.stderr)
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 1
    handlers = {"serve": _cmd_serve, "status": _cmd_status, "token": _cmd_token,
                "mcp": _cmd_mcp, "selftest": _cmd_selftest}
    return handlers[args.command](args)


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
