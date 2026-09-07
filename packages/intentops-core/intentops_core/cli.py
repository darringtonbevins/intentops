"""The ``intentops`` command line -- the whole node surface.

PURPOSE
    ``genesis`` brings a clone up as a node. ``doctor`` asks the six birth
    questions again at any time. ``stand-down`` switches it off. ``verify``
    re-runs provenance, and ``verify --all-selftests`` runs every
    instrument's selftest in the tree rather than the genesis subset.
    ``gate --selftest`` proves the gate can actually
    refuse. ``interview`` prints the staged alignment plan. ``substrate init``
    renders the service graph and the store schema a node runs on, without
    connecting to either.

    The verbs are deliberately not counted in this line. A count in a
    docstring is a claim nothing checks, and it goes stale on the next verb --
    this line has already said "six" and "seven" while the truth moved.

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
from .genesis import integrity as integrity_mod
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
    v.add_argument("--all-selftests", action="store_true",
                   help="run EVERY instrument's selftest in the tree, "
                        "not just the genesis subset, and report each "
                        "as name | PASS/FAIL/ERROR/TIMEOUT | duration")
    v.add_argument("--timeout", type=float, default=None,
                   help="per-instrument seconds (--all-selftests only)")
    v.add_argument("--only", default=None,
                   help="run only instruments whose name contains this "
                        "(--all-selftests only)")
    v.add_argument("--imprint", action="store_true",
                   help="re-hash the birth bundle and this node's rules copy "
                        "against IMPRINT-MANIFEST.yaml, and name any drift")
    v.add_argument("--node-root", type=Path, default=None,
                   help="the node whose rules copy is checked (--imprint only)")

    gate = sub.add_parser("gate", help="the action gate")
    gate.add_argument("--selftest", action="store_true",
                      help="prove the gate can refuse and can allow")

    iv = sub.add_parser("interview", help="print the staged alignment plan")
    iv.add_argument("--dry-run", action="store_true",
                    help="walk the open sittings (S0-S2) with PLACEHOLDER "
                         "answers, writing nothing, and print the honest "
                         "calibration status")
    iv.add_argument("--identity-repo", default=None,
                    help="read this node's calibration state from a bound "
                         "identity repository (never written by this verb)")
    iv.add_argument("--seed", type=int, default=0,
                    help="seed for the replay shuffler; a shuffle nobody can "
                         "reproduce cannot be audited")

    cal = sub.add_parser("calibration",
                         help="the two-bar delegation gate: the council floor "
                              "AND the twin bar")
    cal_sub = cal.add_subparsers(dest="calibration_command")
    cal_status = cal_sub.add_parser(
        "status", help="print the honest status; it never claims alignment")
    cal_status.add_argument("--identity-repo", default=None,
                            help="the bound identity repository holding the "
                                 "calibration journal")
    cal_status.add_argument("--selftest", action="store_true",
                            help="prove every refusal in the scorer can fire")

    # Additive-elsewhere: every loop verb, flag and behaviour lives in
    # intentops_core.loops.cli. This file gains a registration and a dispatch
    # row, so a defect in the loop surface cannot strand the node CLI.
    from .loops.cli import add_parser as _add_loops_parser

    _add_loops_parser(sub)

    # Same additive-elsewhere seam, same reason: every metabolism verb, flag
    # and behaviour lives in intentops_core.metabolism.cli.
    from .metabolism.cli import add_parser as _add_metabolism_parser

    _add_metabolism_parser(sub)

    r = sub.add_parser("route",
                       help="resolve a routing assignment (shape + estate ring)")
    r.add_argument("--shape", default=None,
                   help="task shape, as declared in config/routing-policy.yaml")
    r.add_argument("--ring", default=None,
                   help="estate ring; there is no default, and an undeclared "
                        "ring HALTs rather than resolving permissively")
    r.add_argument("--policy", default=None,
                   help="path to a routing policy (default: the clone's own)")
    r.add_argument("--example", action="store_true",
                   help="load the policy's fictional example pools and fence")
    r.add_argument("--json", action="store_true")
    r.add_argument("--selftest", action="store_true",
                   help="prove every verdict and every halt can fire")

    sb = sub.add_parser("substrate",
                        help="the service graph and store schema a node runs on")
    sb_sub = sb.add_subparsers(dest="substrate_command")
    sb_init = sb_sub.add_parser(
        "init",
        help="render the store schema and the service plan (connects to nothing)")
    sb_init.add_argument(
        "--dry-run", action="store_true",
        help="render the plan and the SQL and stop. This is the ONLY mode this "
             "seed implements: it ships no database driver, so applying the "
             "schema is `psql -f deploy/schema/genesis.sql` -- named, never "
             "simulated")
    sb_init.add_argument("--json", action="store_true")
    sb_init.add_argument("--sql", action="store_true",
                         help="print the rendered SQL instead of the plan")
    sb_init.add_argument("--check", action="store_true",
                         help="fail if deploy/schema/genesis.sql has drifted "
                              "from its declaration in schema.py")
    sb_init.add_argument("--selftest", action="store_true",
                         help="prove the schema and graph checks can fire")
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


def _integrity_selftest(repo_root: Path) -> tuple:
    """Adapt the integrity selftest to the (ok, report) shape verify expects.

    ``--repo-root`` was resolved and then IGNORED here, so from an installed
    distribution the selftest read its fixture hasher out of site-packages and
    raised a FileNotFoundError traceback instead of a verdict.
    """
    try:
        code = integrity_mod.selftest(repo_root)
    except integrity_mod.IntegrityError as exc:
        return False, f"integrity: the selftest could not run -- {exc}"
    return code == 0, "integrity: every drift class can fire"


def _cmd_verify(args: argparse.Namespace) -> int:
    if getattr(args, "imprint", False):
        repo = _repo_root(args)
        try:
            report = integrity_mod.verify_imprint(repo, args.node_root)
        except integrity_mod.IntegrityError as exc:
            if args.json:
                print(json.dumps({"verdict": integrity_mod.HALT,
                                  "error": str(exc)}, indent=2))
            else:
                print(f"IMPRINT INTEGRITY {integrity_mod.HALT} -- the check "
                      f"could not run: {exc}")
                print("  This is not a pass.")
            return 1
        if args.json:
            print(json.dumps(report.to_row(), indent=2))
        else:
            print(report.render())
        return 0 if report.clean else 1

    if getattr(args, "all_selftests", False):
        # The genesis seven below are a SUBSET, and a hand-written one.
        # This delegates to the registry, which DERIVES the population
        # from the tree, so an instrument born tomorrow is run tomorrow
        # without anyone remembering to add it to a list.
        from .selftests import registry as selftest_registry

        report = selftest_registry.run_all(
            root=_repo_root(args),
            timeout=(args.timeout
                     if args.timeout is not None
                     else selftest_registry.DEFAULT_TIMEOUT_S),
            only=args.only)
        if args.json:
            print(json.dumps(report.to_row(), indent=2))
        else:
            print(report.render())
        return 0 if report.ok else 1

    if args.selftest:
        failures = 0
        selftest_repo = _repo_root(args)
        for name, fn in (("trust_pin", None),
                         ("provenance", provenance_mod.selftest),
                         ("organs", organs_mod.selftest),
                         ("standdown", standdown_mod.selftest),
                         ("aliveness", aliveness_mod.selftest),
                         ("integrity",
                          lambda: _integrity_selftest(selftest_repo)),
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


def _calibration_state(identity_repo: Optional[str]):
    """This node's folded calibration state. No repo bound = an EMPTY state.

    An empty state is not a missing one: it says zero graded rulings, which is
    the honest reading for a node that has journalled none.
    """
    from .alignment import calibration as calibration_mod

    if not identity_repo:
        return calibration_mod.CalibrationState(graded=0, hits=0, accuracy=0.0,
                                                sealed=0)
    return calibration_mod.CalibrationStore(identity_repo).state()


def _interview_dry_run(args: argparse.Namespace, repo: Path,
                       template: Path) -> int:
    """Walk the OPEN sittings with placeholder answers. Writes nothing."""
    import random

    from .alignment import calibration as calibration_mod
    from .alignment import interview as interview_mod

    interview = interview_mod.load_interview(template)   # refusals fire here
    rulings = 0            # a dry run asserts no history; S3/S4 stay shut
    plan = interview_mod.stage_plan(rulings)
    rng = random.Random(args.seed)

    print("Alignment interview -- DRY RUN")
    print("Nothing below is written. Every answer is a PLACEHOLDER, and a "
          "placeholder is not an operator's answer.")
    print(f"Assumed rulings in this node's own history: {rulings} "
          "(a dry run reads no queue)")
    print()
    for stage in plan:
        if not stage.available:
            print(f"  {stage.id}  CLOSED -- {stage.requires}")
            continue
        questions = interview.for_sitting(stage.id)
        print(f"  {stage.id}  OPEN   -- grade {stage.grade}, "
              f"{len(questions)} question(s)")
        for question in questions:
            rendered = interview_mod.render_question(question, rng)
            first_line = question.prompt.splitlines()[0] if question.prompt else ""
            print(f"      {question.id} [{question.kind}/{question.tier}]"
                  f" {first_line}")
            for index, option in enumerate(rendered.options):
                print(f"        ({index}) {option}")
            print("        PLACEHOLDER ANSWER -- not recorded, not graded")
        print()

    state = _calibration_state(getattr(args, "identity_repo", None))
    floors = calibration_mod.default_floors()
    council_floor = calibration_mod.load_council_floor(repo)
    # A dry run takes no council reading, and an absent reading is never a
    # pass -- so the gate below reports the council half as NOT met, honestly.
    result = calibration_mod.two_bar_gate(state, floors, council=None,
                                          council_floor=council_floor)
    print(result.status)
    for reason in result.reasons:
        print(f"  {reason}")
    return 0


def _cmd_calibration(args: argparse.Namespace) -> int:
    from .alignment import calibration as calibration_mod

    if getattr(args, "calibration_command", None) != "status":
        print("usage: intentops calibration status [--identity-repo PATH] "
              "[--selftest]", file=sys.stderr)
        return 2
    if getattr(args, "selftest", False):
        ok, message = calibration_mod.selftest(_repo_root(args))
        print(message)
        return 0 if ok else 1

    repo = _repo_root(args)
    floors = calibration_mod.default_floors()
    council_floor = calibration_mod.load_council_floor(repo,
                                                       floors.council_level)
    state = _calibration_state(getattr(args, "identity_repo", None))
    result = calibration_mod.two_bar_gate(state, floors, council=None,
                                          council_floor=council_floor)
    print("The delegation bar is BOTH gates; either alone is insufficient.")
    print(f"  council floor : {council_floor.level} = "
          f"{council_floor.sigma:g} sigma  (source {council_floor.source})")
    print(f"  twin bar      : {floors.minimum_rulings} rulings at "
          f"{round(floors.minimum_accuracy * 100)}%, forward-only")
    print(f"  sealed        : {state.sealed} prediction(s)")
    print()
    print(result.status)
    for reason in result.reasons:
        print(f"  {reason}")
    return 0


def _cmd_interview(args: argparse.Namespace) -> int:
    repo = _repo_root(args)
    template = repo / "config" / "alignment-interview.template.yaml"
    if getattr(args, "dry_run", False):
        return _interview_dry_run(args, repo, template)
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


def _cmd_route(args: argparse.Namespace) -> int:
    """Resolve one assignment. Pure: no socket, no provider SDK, no model id."""
    from .routing import policy as routing_policy

    policy_path = Path(args.policy) if args.policy else (
        _repo_root(args) / "config" / "routing-policy.yaml")
    if args.selftest:
        ok, report = routing_policy.selftest(policy_path)
        print(report)
        return 0 if ok else 1
    if not args.shape or not args.ring:
        print("route: --shape and --ring are both required (or --selftest). "
              "There is no default shape and no default ring.", file=sys.stderr)
        return 2
    try:
        pol = routing_policy.load_policy(policy_path, use_example=args.example)
        assignment = routing_policy.resolve(pol, args.shape, args.ring)
    except routing_policy.PolicyError as exc:
        print(f"HALT: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(assignment.to_dict(), indent=2, sort_keys=True))
    else:
        print(routing_policy.render(assignment, pol))
    # A fenced or unmet assignment is an ANSWER, not a crash: exit 0 and let
    # the reader see the verdict, the same way a stood-down node exits 0.
    return 0


def _cmd_substrate(args: argparse.Namespace) -> int:
    """Render what a node runs on. Opens no socket, starts no container.

    ``--dry-run`` is the only implemented mode, and saying so is the point:
    this seed carries no database driver, so a verb that claimed to APPLY the
    schema would be claiming something it cannot verify. It prints the exact
    command instead.
    """
    from .substrate import schema as schema_mod
    from .substrate import service_graph as graph_mod

    repo = _repo_root(args)
    if getattr(args, "substrate_command", None) != "init":
        print("substrate: the only verb is `init` (try: substrate init --dry-run)",
              file=sys.stderr)
        return 2

    if args.selftest:
        ok_schema, report_schema = schema_mod.selftest()
        ok_graph, report_graph = graph_mod.selftest(repo)
        print(report_schema)
        print(report_graph)
        return 0 if (ok_schema and ok_graph) else 1

    if args.check:
        ok, report = schema_mod.check_rendered(repo)
        print(report)
        return 0 if ok else 1

    if args.sql:
        print(schema_mod.render_sql(), end="")
        return 0

    try:
        graph_ok, _findings, summary = graph_mod.check_graph(repo)
        plan = graph_mod.render_plan(repo)
    except graph_mod.GraphError as exc:
        print(f"HALT: {exc}", file=sys.stderr)
        return 1
    rendered_ok, rendered_report = schema_mod.check_rendered(repo)

    if args.json:
        print(json.dumps({
            "graph": summary,
            "graph_clean": graph_ok,
            "schema_version": schema_mod.SCHEMA_VERSION,
            "tables": [{"name": t.name, "write_model": t.write_model,
                        "expected_writer": t.expected_writer,
                        "consumer": t.consumer}
                       for t in schema_mod.TABLES],
            "rendered_sql_matches_declaration": rendered_ok,
            "applied": False,
            "apply_command": "psql -f deploy/schema/genesis.sql",
        }, indent=2, sort_keys=True))
    else:
        print(plan)
        print()
        print(f"STORE SCHEMA (version {schema_mod.SCHEMA_VERSION}, "
              f"{len(schema_mod.TABLES)} tables, RENDERED, NOT APPLIED)")
        for table in schema_mod.TABLES:
            print(f"  {table.name:<24} {table.write_model}")
        print()
        print(f"  {rendered_report}")
        print("  apply with: psql -f deploy/schema/genesis.sql")
        print("  this seed ships no database driver on purpose; naming the "
              "command beats")
        print("  simulating an apply nothing here could verify.")

    if not args.dry_run:
        print("\nsubstrate init: --dry-run is the only implemented mode; "
              "nothing was applied.", file=sys.stderr)
        return 2
    return 0 if (graph_ok and rendered_ok) else 1


def _cmd_loops(args: argparse.Namespace) -> int:
    """Delegate to the loop surface. All behaviour lives in its own module."""
    from .loops.cli import run as run_loops

    return run_loops(args, Path(args.node_root))


def _cmd_metabolism(args: argparse.Namespace) -> int:
    """Delegate to the metabolism surface. All behaviour lives in its module."""
    from .metabolism.cli import run as run_metabolism

    return run_metabolism(args, Path(args.node_root))


_COMMANDS = {
    "genesis": _cmd_genesis,
    "doctor": _cmd_doctor,
    "stand-down": _cmd_stand_down,
    "verify": _cmd_verify,
    "gate": _cmd_gate,
    "interview": _cmd_interview,
    "calibration": _cmd_calibration,
    "route": _cmd_route,
    "substrate": _cmd_substrate,
    "loops": _cmd_loops,
    "metabolism": _cmd_metabolism,
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
