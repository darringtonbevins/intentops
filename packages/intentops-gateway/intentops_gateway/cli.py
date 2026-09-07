"""``intentops gateway serve | status | token rotate | mcp | selftest``.

PURPOSE
    The operator's surface. Four verbs and one diagnostic, each doing exactly
    what its name says.

      serve              run the HTTP JSON-RPC gateway in the foreground
      status             what this gateway would be if it started
      token rotate       mint (or re-mint) this node's bearer token, ONCE
      token show-digest  the stored digest and when it was disclosed
      mcp                the same tools over MCP stdio, for a client that
                         spawns us
      selftest           prove the refusal paths can actually fire

    THE TOKEN IS USUALLY MINTED BEFORE THIS COMMAND EVER RUNS. Since
    2026-09-06 genesis OFFERS the mint at G2, at an attended terminal, and
    shows the value once; ``rotate`` remains the out-of-band path and the only
    way to replace a lost one. Both record ``disclosed_at``, because they both
    show the value; anything that minted without showing must not.

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
from datetime import datetime, timezone
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
    show = token_sub.add_parser(
        "show-digest",
        help=("print the stored DIGEST and the mint/disclosure stamps -- "
              "never the token, which is not recoverable"))
    show.add_argument("--json", action="store_true",
                      help="emit the record as JSON, digest included")

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
    if args.token_command not in ("rotate", "show-digest"):
        print("intentops gateway token: the verbs are `rotate` and "
              "`show-digest`", file=sys.stderr)
        return 2
    try:
        gctx = load_gateway_context()
    except Exception as exc:
        print(f"intentops gateway: cannot read this node: {exc}", file=sys.stderr)
        return 2
    if args.token_command == "show-digest":
        return _cmd_token_show_digest(args, gctx)
    if not args.yes:
        print("Rotating this node's gateway token REPLACES the stored digest, "
              "so every client holding the current token is locked out at the "
              "same instant.", file=sys.stderr)
        print(f"Node: {gctx.node_root}", file=sys.stderr)
        print("Re-run with --yes to proceed. Nothing was changed.",
              file=sys.stderr)
        return 1
    # This command SHOWS the value, so it records that a disclosure happened.
    # A mint that does not show must leave the stamp empty rather than write
    # one nobody earned -- that is the difference `show-digest` reports.
    disclosed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    value = tokens.mint(gctx.node_root, disclosed_at=disclosed_at)
    record = tokens.load_record(gctx.node_root)
    print(f"node root : {gctx.node_root} ({gctx.node.root_source})", file=sys.stderr)
    print(f"rotation  : {record.rotation}", file=sys.stderr)
    print(f"disclosed : {record.disclosed_at}", file=sys.stderr)
    print("Give this token to the client. It is shown ONCE and only its digest "
          "is stored; re-run to rotate. It is not written to any file by this "
          "command.", file=sys.stderr)
    print(value)
    return 0


def _cmd_token_show_digest(args: argparse.Namespace, gctx) -> int:
    """What is on disk, and when a human was shown it. Never the token.

    The digest is not itself a secret, but it is the oracle an offline
    guesser wants, which is why ``status`` withholds it and this deliberate
    verb prints it: an operator comparing two nodes needs it, and a status
    payload handed to a harness does not.
    """
    try:
        record = tokens.load_record(gctx.node_root)
    except tokens.AuthUnavailable as exc:
        print(f"intentops gateway: {exc}", file=sys.stderr)
        # NOT MINTED is a posture, not a crash: the gateway is fail-closed and
        # the remedy is in the message above. Exit 1 says "no token", not
        # "something broke", which is exit 2.
        return 1
    if args.json:
        print(json.dumps({"digest": record.token_sha256, **record.public()},
                         indent=2))
        return 0
    print(f"node root : {gctx.node_root} ({gctx.node.root_source})", file=sys.stderr)
    print(f"algorithm : {record.algorithm}", file=sys.stderr)
    print(f"rotation  : {record.rotation}", file=sys.stderr)
    print(f"minted    : {record.minted_at}", file=sys.stderr)
    print("disclosed : "
          + (record.disclosed_at
             or "not recorded -- this token was minted without being shown"),
          file=sys.stderr)
    print(record.token_sha256)
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
