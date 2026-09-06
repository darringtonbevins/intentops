"""``intentops loops`` -- check, render, tick.

PURPOSE
    The operator surface over the loop subsystem, and deliberately the only
    place in it that prints or writes to a path a human chose. Three verbs:

      ``check``   compare the charter file against a host's bindings export and
                  report drift. Exit 1 on DRIFTED or DEGRADED, 0 on CONFORMANT
                  or ATTENTION -- a governed off-state is not a failure, and an
                  unread source is.
      ``render``  emit the scheduling artifact(s) for one charter on one host.
                  Prints by default; ``--out`` is the only path that writes.
      ``tick``    run ONE dry-run tick of one charter and journal the decision.

WRITE MODEL
    None of its own. ``render --out`` writes files the operator named, and
    ``tick`` delegates to the engine's append-only journal, which declares its
    model there.

BLIND SPOTS
    * ``check`` reads a bindings EXPORT, never the host. See
      :mod:`intentops_core.loops.conformance`.
    * ``render --out`` overwrites a file of the same name without asking. The
      artifacts are pure functions of the charter, so an overwrite loses only
      a hand edit -- which is the drift ``check`` exists to find anyway.
    * Exit code 0 means the verb answered, not that the answer was good.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from . import adapters as adapters_mod
from . import conformance as conformance_mod
from . import engine as engine_mod
from .charters import Charter, CharterError, load_charters

__all__ = ["add_parser", "run", "main"]

DEFAULT_CHARTERS_RELPATH = Path("config") / "loop-charters.yaml"
TEMPLATE_RELPATH = Path("config") / "loop-charters.template.yaml"


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register the ``loops`` verb on an existing subparser set."""
    parser = sub.add_parser(
        "loops", help="portable loop scheduling: one charter, N host bindings")
    parser.add_argument("--charters", default=None,
                        help="the charter file (default: "
                             f"<node-root>/{DEFAULT_CHARTERS_RELPATH.as_posix()})")
    verbs = parser.add_subparsers(dest="loops_command")

    check = verbs.add_parser("check", help="charter-versus-binding drift")
    check.add_argument("--bindings", default=None,
                       help="a host's schedule export (loop-bindings/v1)")
    check.add_argument("--host", default=None,
                       help="host kind: " + " | ".join(adapters_mod.ADAPTER_KINDS))
    check.add_argument("--json", action="store_true")
    check.add_argument("--selftest", action="store_true",
                       help="prove every finding class can fire")

    render = verbs.add_parser("render", help="emit a host scheduling artifact")
    render.add_argument("charter_id", nargs="?", default=None)
    render.add_argument("--all", action="store_true",
                        help="render every charter in the file")
    render.add_argument("--host", required=False, default=None)
    render.add_argument("--out", default=None,
                        help="write the artifacts into this directory")
    render.add_argument("--tick-command", default="intentops loops tick")
    render.add_argument("--wrapper", default=None,
                        help="the windowless wrapper a Windows task launches")
    render.add_argument("--user", default=None)
    render.add_argument("--start-date", default="2026-01-01")

    tick = verbs.add_parser("tick", help="one dry-run tick of one charter")
    tick.add_argument("charter_id")
    tick.add_argument("--manual", action="store_true",
                      help="declare this an operator-initiated run, which is "
                           "the only way a manual charter ticks")
    tick.add_argument("--no-record", action="store_true",
                      help="preview the verdict without journalling it")
    tick.add_argument("--json", action="store_true")
    return parser


def _charters_path(args: argparse.Namespace, node_root: Path) -> Path:
    if getattr(args, "charters", None):
        return Path(args.charters)
    return node_root / DEFAULT_CHARTERS_RELPATH


def _one(charters: List[Charter], charter_id: str) -> Charter:
    for charter in charters:
        if charter.id == charter_id:
            return charter
    raise CharterError(
        f"no charter declares the id {charter_id!r}",
        "check the id against the charter file; ids are how a binding names "
        "its charter, so a typo here is a loop nobody can find")


def _context(args: argparse.Namespace, node_root: Path
             ) -> adapters_mod.HostContext:
    return adapters_mod.HostContext(
        node_root=node_root.as_posix(),
        tick_command=getattr(args, "tick_command", "intentops loops tick"),
        user=getattr(args, "user", None),
        windows_wrapper=getattr(args, "wrapper", None),
        start_date=getattr(args, "start_date", "2026-01-01"))


def _cmd_check(args: argparse.Namespace, node_root: Path) -> int:
    if args.selftest:
        ok, report = conformance_mod.selftest()
        print(("PASS " if ok else "FAIL ") + report)
        return 0 if ok else 1
    if not args.bindings:
        print("loops check needs --bindings: a host's own schedule export. "
              "There is no default, because guessing what the host has is the "
              "drift this check exists to find.")
        return 2
    charters = load_charters(_charters_path(args, node_root))
    unreadable: List[str] = []
    host = args.host
    records: List[conformance_mod.BindingRecord] = []
    try:
        host_declared, records = conformance_mod.load_bindings(args.bindings)
        host = host or host_declared
    except CharterError as exc:
        # A source that cannot be read stays in the denominator, graded worst.
        unreadable.append(f"{args.bindings}: {exc.message}")
        host = host or adapters_mod.ADAPTER_KINDS[0]
    report = conformance_mod.check(charters, records, host_kind=host,
                                   context=_context(args, node_root),
                                   unreadable=unreadable)
    if args.json:
        print(json.dumps({
            "posture": report.posture,
            "host": report.host_kind,
            "charters": report.charters_seen,
            "bindings": report.bindings_seen,
            "bindings_skipped": report.bindings_skipped,
            "cadence_checked": report.cadence_checked,
            "counts": report.counts,
            "findings": [{"code": f.code, "subject": f.subject,
                          "detail": f.detail, "remedy": f.remedy}
                         for f in report.findings],
        }, indent=2))
    else:
        print(report.render())
    return report.exit_code


def _cmd_render(args: argparse.Namespace, node_root: Path) -> int:
    if not args.host:
        print("loops render needs --host: " +
              " | ".join(adapters_mod.ADAPTER_KINDS))
        return 2
    charters = load_charters(_charters_path(args, node_root))
    selected = charters if args.all else [_one(charters, args.charter_id or "")]
    context = _context(args, node_root)
    out = Path(args.out) if args.out else None
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
    failures = 0
    for charter in selected:
        try:
            binding = adapters_mod.render(args.host, charter, context)
        except adapters_mod.AdapterError as exc:
            print(f"REFUSED {charter.id}: {exc.message}\n  remedy: "
                  f"{exc.remedy}")
            failures += 1
            continue
        for name, text in binding.files.items():
            if out is None:
                print(f"# ---- {name} ----")
                print(text, end="" if text.endswith("\n") else "\n")
            else:
                (out / name).write_text(text, encoding="utf-8", newline="\n")
                print(f"wrote {(out / name).as_posix()}")
    return 1 if failures else 0


def _cmd_tick(args: argparse.Namespace, node_root: Path) -> int:
    charters = load_charters(_charters_path(args, node_root))
    charter = _one(charters, args.charter_id)
    record = engine_mod.tick(charter, node_root, manual_run=args.manual,
                             record=not args.no_record)
    if args.json:
        print(json.dumps(record.to_row(), indent=2))
    else:
        print(f"{record.verdict}  {record.charter_id}  ({record.cadence}, "
              f"ceiling {record.tier_ceiling})")
        print(f"  {record.reason}")
        if record.ran:
            print("  evidence this charter owes: "
                  + "; ".join(record.requires_evidence))
    return 0


def run(args: argparse.Namespace, node_root: Path) -> int:
    """Dispatch a parsed ``loops`` namespace. HALTs surface as exit 1."""
    verb = getattr(args, "loops_command", None)
    handlers = {"check": _cmd_check, "render": _cmd_render, "tick": _cmd_tick}
    if verb not in handlers:
        print("loops: verbs are check | render | tick "
              "(try: intentops loops check --selftest)")
        return 2
    try:
        return handlers[verb](args, node_root)
    except CharterError as exc:
        print(str(exc))
        return 1


def main(argv: Optional[List[str]] = None) -> int:
    """``python -m intentops_core.loops`` -- the same surface, standalone."""
    parser = argparse.ArgumentParser(prog="intentops-loops")
    parser.add_argument("--node-root", default=".")
    add_parser(parser.add_subparsers(dest="command"))
    raw = list(sys.argv[1:] if argv is None else argv)
    # Standalone, the "loops" word is redundant -- accept it either way rather
    # than making the same command mean two things depending on entry point.
    if "loops" not in raw:
        insert = 0
        while insert < len(raw) and raw[insert].startswith("--"):
            insert += 2 if raw[insert] == "--node-root" else 1
        raw.insert(insert, "loops")
    args = parser.parse_args(raw)
    if getattr(args, "command", None) != "loops":
        parser.print_help()
        return 2
    return run(args, Path(args.node_root))
