"""``intentops metabolism`` -- cadence, heartbeat, crystallize, grok, assimilate, run.

PURPOSE
    The operator surface over the metabolism, and deliberately the only place
    in the package that prints or writes to a path a human chose. Five verbs,
    each delegating to the module that owns the behaviour, so a defect in one
    of them cannot strand the node CLI (additive-elsewhere: the top-level CLI
    gains one registration row and one dispatch row, and nothing else).

      ``cadence``     load and validate the cadence; ``--run`` passes it
                      through its declared seams, which with nothing wired is
                      the NullStage and therefore WARN, not OK.
      ``heartbeat``   take a reading, append it, and report the three alarms.
      ``crystallize`` render a document template, or validate one.
      ``grok``        record one dry-run cycle; ``--slices`` plans a forge.
      ``assimilate``  decide one request against the four verbs and the
                      artifact boundary.
      ``run``         run ONE enabled stage through its declared `callable`
                      seam, under a declared resource envelope. ``--dry-run``
                      prints the plan and contacts nothing. With no
                      ``--estate`` the provider is the NULL provider, so the
                      stage runs, produces nothing, and renders WARN.

WRITE MODEL
    None of its own. ``heartbeat --append`` delegates to the heartbeat's
    append-only journal, and ``grok --out`` writes the single path the operator
    named. Everything else prints.

BLIND SPOTS
    * Exit code 0 means the verb answered, not that the answer was good. The
      one exception is deliberate: a WARN or DEGRADED cadence, an alarming
      heartbeat, a refused assimilation and an invalid document all exit
      non-zero, because those are the readings a checklist must not step over.
    * ``--selftest`` on any verb proves that verb's detectors can fire. It
      says nothing about the node's actual corpus.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

from . import assimilation as assimilation_mod
from . import cadence as cadence_mod
from . import crystallize as crystallize_mod
from . import grok as grok_mod
from . import heartbeat as heartbeat_mod
from . import runner as runner_mod

__all__ = ["add_parser", "run", "selftest", "DEFAULT_CADENCE_RELPATH",
           "TEMPLATE_RELPATH"]

DEFAULT_CADENCE_RELPATH = Path(".intentops") / "metabolism" / "cadence.yaml"
TEMPLATE_RELPATH = Path("config") / "metabolism-cadence.template.yaml"


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register the ``metabolism`` verb on an existing subparser set."""
    parser = sub.add_parser(
        "metabolism",
        help="absorb -> distill -> crystallize -> method, and its heartbeat")
    verbs = parser.add_subparsers(dest="metabolism_command")

    cad = verbs.add_parser("cadence", help="load, validate, or dry-run the cadence")
    cad.add_argument("--cadence", default=None,
                     help="the cadence file (default: <node-root>/"
                          f"{DEFAULT_CADENCE_RELPATH.as_posix()})")
    cad.add_argument("--birth", action="store_true",
                     help="load under birth rules: every stage must be disabled")
    cad.add_argument("--run", action="store_true",
                     help="run the stages through their declared seams")
    cad.add_argument("--json", action="store_true")
    cad.add_argument("--selftest", action="store_true")

    hb = verbs.add_parser("heartbeat", help="three alarms over the dated series")
    hb.add_argument("--append", action="store_true",
                    help="take a reading and append it to the journal")
    hb.add_argument("--now", default=None,
                    help="ISO stamp for the reading; injected, never read from "
                         "the clock inside the logic")
    hb.add_argument("--json", action="store_true")
    hb.add_argument("--selftest", action="store_true")

    cr = verbs.add_parser("crystallize", help="the pattern document contract")
    cr.add_argument("--template", metavar="CRYST-ID", default=None)
    cr.add_argument("--validate", metavar="PATH", default=None)
    cr.add_argument("--json", action="store_true")
    cr.add_argument("--selftest", action="store_true")

    gk = verbs.add_parser("grok", help="record one dry-run cycle")
    gk.add_argument("--focus", default="")
    gk.add_argument("--slices", nargs="*", default=None)
    gk.add_argument("--out", default=None)
    gk.add_argument("--json", action="store_true")
    gk.add_argument("--selftest", action="store_true")

    asm = verbs.add_parser("assimilate",
                           help="the four verbs, and the artifact boundary")
    asm.add_argument("--verb", default=None)
    asm.add_argument("--source", default="")
    asm.add_argument("--payload-kind", default="none")
    asm.add_argument("--confidence", type=float, default=None)
    asm.add_argument("--governing-infrastructure", action="store_true")
    asm.add_argument("--core-surface", action="store_true")
    asm.add_argument("--json", action="store_true")
    asm.add_argument("--selftest", action="store_true")

    # `run` is the ONLY verb here that can reach a model, and it reaches one
    # only when an operator has declared a pool, enabled it, and named it. With
    # no --estate it uses the null provider, which contacts nothing.
    rn = verbs.add_parser("run",
                          help="run ONE enabled stage through its declared seam")
    rn.add_argument("--stage", default=None,
                    help="absorb | distill | crystallize | method")
    rn.add_argument("--cadence", default=None)
    rn.add_argument("--prompts", default=None)
    rn.add_argument("--estate", default=None,
                    help="the estate directory holding RESOURCES.yaml; omitted "
                         "means the null provider")
    rn.add_argument("--pool", default=None)
    rn.add_argument("--source-dir", default=None)
    rn.add_argument("--max-tokens", type=int, default=None)
    rn.add_argument("--max-items", type=int, default=None)
    rn.add_argument("--duty-pause", type=float, default=None)
    rn.add_argument("--dry-run", action="store_true",
                    help="print the plan; contact nothing, write nothing")
    rn.add_argument("--json", action="store_true")
    rn.add_argument("--selftest", action="store_true")
    return parser


def _argv(args: argparse.Namespace, *names: str) -> list:
    """Rebuild the module CLI's argv from the parsed namespace.

    The modules own their own flags; this surface forwards rather than
    reimplements, so a flag cannot mean one thing here and another there.
    """
    out: list = []
    for name in names:
        value = getattr(args, name.replace("-", "_"), None)
        if value is None or value is False:
            continue
        if value is True:
            out.append(f"--{name}")
        elif isinstance(value, list):
            out.append(f"--{name}")
            out.extend(str(v) for v in value)
        else:
            out.extend([f"--{name}", str(value)])
    return out


def run(args: argparse.Namespace, node_root: Path) -> int:
    """Dispatch one metabolism verb."""
    command = getattr(args, "metabolism_command", None)
    if not command:
        print("usage: intentops metabolism "
              "{cadence,heartbeat,crystallize,grok,assimilate,run}")
        return 0

    if command == "cadence":
        cadence = getattr(args, "cadence", None) or str(
            node_root / DEFAULT_CADENCE_RELPATH)
        argv = ["--cadence", cadence]
        argv += _argv(args, "birth", "run", "json", "selftest")
        if args.selftest:
            argv = ["--selftest"]
        return cadence_mod.main(argv)

    if command == "heartbeat":
        if args.selftest:
            return heartbeat_mod.main(["--selftest"])
        argv = ["--node-root", str(node_root)]
        argv += _argv(args, "append", "now", "json")
        return heartbeat_mod.main(argv)

    if command == "crystallize":
        if args.selftest:
            return crystallize_mod.main(["--selftest"])
        return crystallize_mod.main(_argv(args, "template", "validate", "json"))

    if command == "grok":
        if args.selftest:
            return grok_mod.main(["--selftest"])
        return grok_mod.main(_argv(args, "focus", "slices", "out", "json"))

    if command == "run":
        if args.selftest:
            return runner_mod.main(["--selftest"])
        if not getattr(args, "stage", None):
            print("usage: intentops metabolism run --stage "
                  "{absorb,distill,crystallize,method} [--dry-run]")
            return 1
        argv = ["--node-root", str(node_root)]
        argv += _argv(args, "stage", "cadence", "prompts", "estate", "pool",
                      "source-dir", "max-tokens", "max-items", "duty-pause",
                      "dry-run", "json")
        return runner_mod.main(argv)

    if command == "assimilate":
        if args.selftest:
            return assimilation_mod.main(["--selftest"])
        return assimilation_mod.main(_argv(
            args, "verb", "source", "payload-kind", "confidence",
            "governing-infrastructure", "core-surface", "json"))

    print(f"unknown metabolism verb: {command}")
    return 1


def selftest() -> tuple:
    """Run every module's selftest and fold the results.

    A module with no selftest would be NAMED here rather than omitted; there
    are none today, and this docstring is the warning if one is ever added.
    """
    failures = []
    reports = []
    for name, fn in (("cadence", cadence_mod.selftest),
                     ("heartbeat", heartbeat_mod.selftest),
                     ("crystallize", crystallize_mod.selftest),
                     ("grok", grok_mod.selftest),
                     ("assimilation", assimilation_mod.selftest),
                     ("runner", runner_mod.selftest)):
        ok, report = fn()
        reports.append(("PASS " if ok else "FAIL ") + report)
        if not ok:
            failures.append(name)
    return (not failures, "\n".join(reports))


def main(argv: Optional[list] = None) -> int:  # pragma: no cover - thin shim
    parser = argparse.ArgumentParser(prog="intentops metabolism")
    sub = parser.add_subparsers(dest="command")
    add_parser(sub)
    args = parser.parse_args(argv)
    return run(args, Path("."))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
