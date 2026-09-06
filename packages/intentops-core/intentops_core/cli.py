"""The ``intentops`` command line -- the whole node surface, six verbs.

PURPOSE
    ``genesis`` brings a clone up as a node. ``doctor`` asks the six birth
    questions again at any time. ``stand-down`` switches it off. ``verify``
    re-runs provenance. ``gate --selftest`` proves the gate can actually
    refuse. ``interview`` prints the staged alignment plan.

    Two properties are deliberate. First, every command prints the
    unverified-provenance banner while the development flag is open -- not
    once at genesis, every time, because a warning shown only at birth is a
    warning nobody sees. Second, no verb here can switch a stood-down node
    back on: removing the marker is the operator's own manual act.

WRITE MODEL
    None of its own. Every write is delegated to the module that owns the
    store and declares its model there.

BLIND SPOTS
    - ``--node-root`` defaults to the current directory. A command run from
      the wrong directory reads a different node's stores; the doctor prints
      the resolved path for exactly that reason.
    - Exit codes: 0 means the command answered, not that the answer was good.
      A stood-down node and a degraded node both exit 0, because OFF is not a
      defect and a named absent faculty is an honest reading.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Optional

from .genesis import UNSIGNED_DEV_ENV
from .genesis import GenesisError
from .genesis import aliveness as aliveness_mod
from .genesis import machine as machine_mod
from .genesis import organs as organs_mod
from .genesis import provenance as provenance_mod
from .genesis import standdown as standdown_mod
from .genesis import trust_pin

__all__ = ["main", "build_parser"]


def _dev_flag_open() -> bool:
    return os.environ.get(UNSIGNED_DEV_ENV) == "1"


def _banner() -> None:
    """Loud, on every command, for as long as the flag is open."""
    if _dev_flag_open():
        print(machine_mod.UNSIGNED_DEV_BANNER, file=sys.stderr)


def _repo_root(args: argparse.Namespace) -> Path:
    if getattr(args, "repo_root", None):
        return Path(args.repo_root)
    # the package lives at <repo>/packages/intentops-core/intentops_core
    return Path(__file__).resolve().parents[3]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="intentops",
        description="Governed intent, deterministic execution. A node's own "
                    "command surface.")
    parser.add_argument("--node-root", default=".",
                        help="the node's own root (where .intentops/ lives)")
    parser.add_argument("--repo-root", default=None,
                        help="the clone carrying config/, genesis/ and estate/")
    sub = parser.add_subparsers(dest="command")

    g = sub.add_parser("genesis", help="bring this clone up as a node")
    g.add_argument("--identity-repo", default=None,
                   help="new | <path> -- there is no default, and a node with "
                        "nothing bound HALTS rather than inventing one")
    g.add_argument("--dry-run", action="store_true",
                   help="ask nothing, persist no private key, mark every "
                        "recorded answer DRY-RUN")
    g.add_argument("--resume", action="store_true",
                   help="fold the journal and re-enter at the last completed "
                        "state; consent is never re-asked")
    g.add_argument("--rollback", metavar="STATE", default=None,
                   help="G3 -- remove .intentops/ and the unsigned skeleton; "
                        "refused once consent exists")
    g.add_argument("--saddle", default=None, help="bind a host adapter by id")

    d = sub.add_parser("doctor", help="ask the six birth questions")
    d.add_argument("--birth", action="store_true",
                   help="also write the birth certificate")
    d.add_argument("--json", action="store_true")

    s = sub.add_parser("stand-down", help="switch this node off")
    s.add_argument("--status", action="store_true",
                   help="print whether the marker exists")

    v = sub.add_parser("verify", help="re-run the provenance checks")
    v.add_argument("--json", action="store_true")
    v.add_argument("--selftest", action="store_true",
                   help="prove every genesis instrument can fire")

    gate = sub.add_parser("gate", help="the action gate")
    gate.add_argument("--selftest", action="store_true",
                      help="prove the gate can refuse and can allow")

    sub.add_parser("interview", help="print the staged alignment plan")
    return parser


# ---------------------------------------------------------------------------
# verbs
# ---------------------------------------------------------------------------


def _cmd_genesis(args: argparse.Namespace) -> int:
    repo = _repo_root(args)
    node = Path(args.node_root)
    identity = args.identity_repo
    if args.rollback:
        resolved = None
        if identity and identity != "new":
            resolved = Path(identity)
        elif identity == "new":
            resolved = node / "identity-repo"
        result = machine_mod.rollback(node, resolved, to_state=args.rollback)
        print(json.dumps(result, indent=2))
        return 0

    run = machine_mod.run_genesis(
        repo, node, identity_repo=identity, dry_run=args.dry_run,
        resume=args.resume, saddle=args.saddle,
        # the banner already fired once for this command; the phase's own copy
        # is for library callers, who get no CLI banner at all
        out=lambda _m: None)
    if run.final_state == "STAND-DOWN":
        print(run.halt_reason)
        return 0
    for result in run.results:
        print(f"{result.state}  {result.verdict:6}  "
              + "; ".join(result.notes[:2]))
    if run.halted:
        print(f"\nHALT at {run.final_state}: {run.halt_reason}", file=sys.stderr)
        if run.remedy:
            print(f"  remedy: {run.remedy}", file=sys.stderr)
        return 1
    print(f"\nfinal state: {run.final_state}")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    node = Path(args.node_root)
    repo = _repo_root(args)
    identity = None
    cfg = node / ".intentops" / "config" / "node.yaml"
    designation = ""
    if cfg.exists():
        import yaml

        doc = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        identity = doc.get("identity_repo")
        designation = doc.get("designation") or ""
    reading = aliveness_mod.take_reading(node, repo_root=repo,
                                         identity_repo=identity,
                                         designation=designation)
    if args.birth:
        aliveness_mod.append_reading(reading, node)
    if args.json:
        print(json.dumps(reading.to_row(), indent=2))
    else:
        print(f"node root: {node.resolve()}")
        print(aliveness_mod.render_certificate(reading))
    # OFF is not a defect and a named absent faculty is an honest reading.
    return 0 if reading.verdict != "NOT-ALIVE" else 1


def _cmd_stand_down(args: argparse.Namespace) -> int:
    node = Path(args.node_root)
    if args.status:
        print(json.dumps(standdown_mod.status(node), indent=2))
        return 0
    identity = None
    cfg = node / ".intentops" / "config" / "node.yaml"
    if cfg.exists():
        import yaml

        identity = (yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}) \
            .get("identity_repo")
    result = standdown_mod.stand_down(node, identity)
    print(result["banner"])
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    if args.selftest:
        failures = 0
        for name, fn in (("trust_pin", None),
                         ("provenance", provenance_mod.selftest),
                         ("organs", organs_mod.selftest),
                         ("standdown", standdown_mod.selftest),
                         ("aliveness", aliveness_mod.selftest),
                         ("machine", machine_mod.selftest)):
            if fn is None:
                # Named rather than omitted: a module with no selftest is a
                # finding, not an absence.
                print(f"{name}: carries no --selftest (it holds a constant, "
                      "not a detector)")
                continue
            ok, report = fn()
            print(("PASS " if ok else "FAIL ") + report)
            failures += 0 if ok else 1
        return 0 if failures == 0 else 1

    repo = _repo_root(args)
    record = provenance_mod.verify_provenance(
        repo, allow_unsigned_dev=_dev_flag_open())
    if args.json:
        print(json.dumps(record.to_row(), indent=2))
    else:
        print(f"pin: {trust_pin.PIN_STATE} ({trust_pin.ROOT_FINGERPRINT})")
        for check in record.checks:
            suffix = (f" [downgraded from {check.downgraded_from}]"
                      if check.downgraded_from else "")
            print(f"{check.outcome:7} {check.id}: {check.reason}{suffix}")
        print(f"\nverified: {record.verified}")
    return 0 if record.verified else 1


def _cmd_gate(args: argparse.Namespace) -> int:
    if not args.selftest:
        print("nothing to do: `intentops gate --selftest` is the only mode "
              "that runs without a host adapter", file=sys.stderr)
        return 1
    failures: List[str] = []
    try:
        from .gate import classify as classify_mod
    except Exception as exc:  # noqa: BLE001
        print(f"the gate is unimportable: {exc}", file=sys.stderr)
        return 1
    rc = classify_mod.selftest()
    print(f"classify --selftest: {'PASS' if rc == 0 else 'FAIL'}")
    if rc != 0:
        failures.append("classify")

    refused = classify_mod.verdict_for("Bash", {"command": "git push origin main"})
    allowed = classify_mod.verdict_for("Read", {"file_path": "README.md"})
    ref_dec = aliveness_mod.decision_name(refused)
    all_dec = aliveness_mod.decision_name(allowed)
    print(f"reaches-reality operation -> {refused.tier} / {ref_dec}")
    print(f"read operation            -> {allowed.tier} / {all_dec}")
    if ref_dec == "ALLOW":
        failures.append("a reaches-reality operation was allowed")
    if all_dec != "ALLOW":
        failures.append("a plain read was not allowed")

    try:
        from .gate import verdict as verdict_mod
    except Exception:  # noqa: BLE001
        verdict_mod = None  # type: ignore[assignment]
    if verdict_mod is not None and not hasattr(verdict_mod, "selftest"):
        print("verdict: carries no --selftest -- FINDING, not a pass "
              "(a detector that has never fired is indistinguishable from a "
              "broken one)")
    if failures:
        print("FAIL: " + "; ".join(failures), file=sys.stderr)
        return 1
    print("PASS: the gate can refuse and can allow")
    return 0


def _cmd_interview(args: argparse.Namespace) -> int:
    repo = _repo_root(args)
    template = repo / "config" / "alignment-interview.template.yaml"
    print("Alignment interview -- STAGED PLAN (this verb prints; it does not ask)")
    print()
    print("At genesis there are NO replays. A fresh operator has ruled nothing,")
    print("so the highest-value elicitation instrument is unavailable on day one")
    print("by construction. The stages open by EVIDENCE AVAILABLE, never by clock:")
    print()
    rows = [
        ("S0 Consent", "immediately", "permission and fences",
         "recorded fact"),
        ("S1 Boundary", "immediately",
         ("the four founding questions, the estate map, notification posture"),
         "recorded fact"),
        ("S2 Domain", "immediately",
         ("risk by domain against explicitly hypothetical cases"),
         "INFERRED, never OBSERVED"),
        ("S3 Replay", ">= 10 real rulings",
         ("the operator's own decisions, options shuffled, prior ruling "
          "revealed only after the answer"),
         "OBSERVED"),
        ("S4 Commitment", ">= 20 real rulings",
         ("real pending approvals ruled as conduct, the prediction sealed "
          "first"),
         "OBSERVED"),
    ]
    for stage, when, what, grade in rows:
        print(f"  {stage:<15} available: {when}")
        print(f"  {'':<15} contains : {what}")
        print(f"  {'':<15} grade    : {grade}")
        print()
    print("Calibration bar: 20 real rulings by this operator, scored >= 80% by "
          "the forward-only scorer, on this node.")
    print("Below the bar the twin is telemetry, never authority, and the honest "
          "string is 'uncalibrated: N of 20 rulings'.")
    print()
    print(f"template: {template.as_posix()} "
          f"({'present' if template.exists() else 'ABSENT'})")
    return 0 if template.exists() else 1


_COMMANDS = {
    "genesis": _cmd_genesis,
    "doctor": _cmd_doctor,
    "stand-down": _cmd_stand_down,
    "verify": _cmd_verify,
    "gate": _cmd_gate,
    "interview": _cmd_interview,
}


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    _banner()
    try:
        return _COMMANDS[args.command](args)
    except GenesisError as exc:
        print(exc.render(), file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
