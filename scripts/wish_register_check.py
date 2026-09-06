#!/usr/bin/env python3
"""wish_register_check -- the structural validator for a node's wish register.

PURPOSE. A wish register is the record of what a node or its operator asked for,
what was granted, and what carries the grant. The register itself is authored;
this is the instrument that keeps it honest. Code rung: stdlib plus YAML, no
model, no network.

Five checks, and every one prints a line whether or not it found anything -- a
check with zero findings prints OK, never nothing:

``schema``          top-level shape, per-wish required keys, id pattern, wisher
                    vocabulary (CLOSED -- an undeclared wisher is a finding, not
                    a default).
``carriers``        every carrier path EXISTS and CONTAINS its literal probe
                    string. This is the check that matters: a register naming a
                    carrier that does not exist, or that no longer says what the
                    entry claims, is a fabricated citation with a schema.
``orphan_files``    every file beside the register in its directory is
                    referenced by some entry's carriers.
``lineage``         only known edge keys, targets exist, no directed cycles.
``open_decisions``  every wish's ``open_decision`` is listed in
                    ``decisions_open``.

WRITE MODEL. None -- a pure read. The register is authored and this validator
never writes to it.

Exit 0 with per-check OK lines; exit 1 listing EVERY named failure. Deliberately
NOT a blocking pre-tool hook: a wish register must never wedge an unrelated
session. Run it from wrap-up and close-of-day checklists.

BLIND SPOTS.

1. The probe check is a literal substring test. A probe string that also appears
   in an unrelated part of the carrier reads as present, and a carrier that
   contains the string while contradicting it around it still passes.
2. Cycle detection covers DIRECTED edges only; symmetric annotations are
   excluded by design, so a mutual-annotation loop is not a finding.
3. Nothing here judges whether a grant is real. ``status: GRANTED`` with a
   carrier that exists and carries its probe passes, whatever the world says.

Usage::

    python scripts/wish_register_check.py [path-to-REGISTER.yaml]
    python scripts/wish_register_check.py --selftest
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import yaml

__all__ = ["run_checks", "CHECKS", "WISHERS", "ALLOWED_EDGES", "selftest", "main"]

SCHEMA = "wish-register/v1"
ID_PATTERN = re.compile(r"^W-\d{4}-\d{2}-\d{2}-[A-Za-z0-9_-]+$")
#: CLOSED vocabulary. A node's wishes and its operator's are different things,
#: and "joint" is the case where the distinction genuinely does not apply.
WISHERS = frozenset({"node", "operator", "joint"})
DIRECTED_EDGES = frozenset({"frames", "merged_into", "descends_into",
                            "ripens_into", "protects_wisher_via",
                            "fulfilled_via"})
SYMMETRIC_EDGES = frozenset({"mirrors", "same_event_question"})
ALLOWED_EDGES = DIRECTED_EDGES | SYMMETRIC_EDGES

CHECKS = ("schema", "carriers", "orphan_files", "lineage", "open_decisions")

DEFAULT_REGISTER = Path("identity") / "wishes" / "REGISTER.yaml"


def _check_schema(data: dict) -> List[str]:
    failures: List[str] = []
    if data.get("schema") != SCHEMA:
        failures.append(f"schema: top-level 'schema' must be {SCHEMA!r} "
                        f"(got {data.get('schema')!r})")
    for key in ("updated", "decisions_open", "wishes"):
        if key not in data:
            failures.append(f"schema: missing required top-level key {key!r}")
    for i, wish in enumerate(data.get("wishes") or []):
        wid = wish.get("id", f"<wish #{i} missing id>")
        if not isinstance(wish.get("id"), str) \
                or not ID_PATTERN.match(wish.get("id") or ""):
            failures.append(f"schema: {wid}: id does not match W-YYYY-MM-DD-SLUG")
        wisher = wish.get("wisher")
        if wisher not in WISHERS:
            failures.append(f"schema: {wid}: wisher {wisher!r} not in "
                            f"{sorted(WISHERS)}")
        for key in ("date", "carriers", "lineage", "grant"):
            if key not in wish:
                failures.append(f"schema: {wid}: missing required key {key!r}")
        grant = wish.get("grant")
        if isinstance(grant, dict) and "status" not in grant:
            failures.append(f"schema: {wid}: grant missing 'status'")
    return failures


def _check_carriers(data: dict, workspace_root: Path) -> List[str]:
    failures: List[str] = []
    for wish in data.get("wishes") or []:
        wid = wish.get("id", "<no id>")
        for carrier in wish.get("carriers") or []:
            path_str = str(carrier.get("path", ""))
            probe = carrier.get("probe", "")
            p = Path(path_str)
            resolved = p if p.is_absolute() else workspace_root / p
            if not resolved.is_file():
                failures.append(f"carriers: {wid}: carrier does not exist: "
                                f"{path_str}")
                continue
            if not probe:
                failures.append(f"carriers: {wid}: carrier {path_str} declares "
                                "no probe string -- an unverifiable carrier is "
                                "worse than none, because it looks checked")
                continue
            try:
                text = resolved.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                failures.append(f"carriers: {wid}: carrier unreadable ({exc}): "
                                f"{path_str}")
                continue
            if str(probe) not in text:
                failures.append(f"carriers: {wid}: probe string not found in "
                                f"{path_str}: {probe!r}")
    return failures


def _check_orphan_files(data: dict, register_path: Path) -> List[str]:
    failures: List[str] = []
    wishes_dir = register_path.parent
    referenced = {Path(str(c.get("path", ""))).name
                  for wish in data.get("wishes") or []
                  for c in wish.get("carriers") or []}
    if not wishes_dir.is_dir():
        return [f"orphan_files: register directory missing: {wishes_dir}"]
    for f in sorted(wishes_dir.iterdir()):
        if not f.is_file() or f.name == register_path.name:
            continue
        if f.name not in referenced:
            failures.append(f"orphan_files: {f.name} has no register entry "
                            "referencing it")
    return failures


def _check_lineage(data: dict) -> List[str]:
    failures: List[str] = []
    wishes = data.get("wishes") or []
    ids = {w.get("id") for w in wishes}
    directed: Dict[str, List[str]] = {}
    for wish in wishes:
        wid = wish.get("id", "<no id>")
        lineage = wish.get("lineage") or {}
        if not isinstance(lineage, dict):
            failures.append(f"lineage: {wid}: lineage is not a mapping")
            continue
        for edge, targets in lineage.items():
            if edge not in ALLOWED_EDGES:
                failures.append(f"lineage: {wid}: unknown edge key {edge!r} "
                                f"(allowed: {sorted(ALLOWED_EDGES)})")
                continue
            for target in targets or []:
                if target not in ids:
                    failures.append(f"lineage: {wid}: edge {edge} -> {target}: "
                                    "target id not in register")
                elif edge in DIRECTED_EDGES:
                    directed.setdefault(wid, []).append(target)

    # directed-cycle detection: iterative three-colour depth-first search
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {w.get("id"): WHITE for w in wishes}
    for start in list(color):
        if color.get(start) != WHITE:
            continue
        stack: List[tuple] = [(start, 0)]
        color[start] = GRAY
        path: List[str] = [start]
        while stack:
            node, idx = stack[-1]
            children = directed.get(node, [])
            if idx < len(children):
                stack[-1] = (node, idx + 1)
                child = children[idx]
                if color.get(child) == GRAY:
                    cyc = path[path.index(child):] + [child]
                    failures.append("lineage: directed cycle detected: "
                                    + " -> ".join(cyc))
                elif color.get(child) == WHITE:
                    color[child] = GRAY
                    stack.append((child, 0))
                    path.append(child)
            else:
                color[node] = BLACK
                stack.pop()
                path.pop()
    return failures


def _check_open_decisions(data: dict) -> List[str]:
    failures: List[str] = []
    decisions_open = set(data.get("decisions_open") or [])
    for wish in data.get("wishes") or []:
        wid = wish.get("id", "<no id>")
        decision = wish.get("open_decision")
        if decision and decision not in decisions_open:
            failures.append(f"open_decisions: {wid}: open_decision "
                            f"{decision!r} not listed in decisions_open "
                            f"{sorted(decisions_open)}")
    return failures


def run_checks(register_path: Path | str,
               workspace_root: Optional[Path] = None) -> Dict[str, List[str]]:
    """Run every check. An unreadable register is a schema failure, never an
    empty pass."""
    register_path = Path(register_path)
    if workspace_root is None:
        # <root>/identity/wishes/REGISTER.yaml -> three levels up
        workspace_root = register_path.parent.parent.parent
    try:
        data = yaml.safe_load(register_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return {"schema": [f"schema: register unreadable or invalid YAML: {exc}"],
                "carriers": [], "orphan_files": [], "lineage": [],
                "open_decisions": []}
    if not isinstance(data, dict):
        return {"schema": ["schema: register root must be a mapping"],
                "carriers": [], "orphan_files": [], "lineage": [],
                "open_decisions": []}
    return {
        "schema": _check_schema(data),
        "carriers": _check_carriers(data, Path(workspace_root)),
        "orphan_files": _check_orphan_files(data, register_path),
        "lineage": _check_lineage(data),
        "open_decisions": _check_open_decisions(data),
    }


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------

_EMPTY_REGISTER = {"schema": SCHEMA, "updated": "2026-09-06",
                   "decisions_open": [], "wishes": []}


def selftest() -> tuple:
    """Prove every check can fire, and that a valid empty register passes."""
    import tempfile

    f: List[str] = []

    def check(label: str, cond: bool) -> None:
        if not cond:
            f.append(label)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        wishes_dir = root / "identity" / "wishes"
        wishes_dir.mkdir(parents=True)
        reg = wishes_dir / "REGISTER.yaml"

        def write(doc: dict) -> Dict[str, List[str]]:
            reg.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
            return run_checks(reg, root)

        res = write(_EMPTY_REGISTER)
        check(f"a valid EMPTY register did not pass: {res}",
              all(not v for v in res.values()))

        # -- carriers: the check that matters ------------------------------
        carrier = wishes_dir / "first-wish.md"
        carrier.write_text("# the first wish\n\nPROBE-STRING-ONE\n",
                           encoding="utf-8")
        good = {**_EMPTY_REGISTER, "wishes": [{
            "id": "W-2026-09-06-FIRST", "wisher": "operator", "date": "2026-09-06",
            "carriers": [{"path": "identity/wishes/first-wish.md",
                          "probe": "PROBE-STRING-ONE"}],
            "lineage": {}, "grant": {"status": "OPEN"}}]}
        res = write(good)
        check(f"a well-formed register did not pass: {res}",
              all(not v for v in res.values()))

        missing = {**good}
        missing["wishes"] = [{**good["wishes"][0], "carriers": [
            {"path": "identity/wishes/never-written.md", "probe": "x"}]}]
        res = write(missing)
        check("a MISSING carrier was not caught",
              any("does not exist" in x for x in res["carriers"]))
        check("an orphaned carrier file was not caught",
              any("first-wish.md" in x for x in res["orphan_files"]))

        wrong_probe = {**good}
        wrong_probe["wishes"] = [{**good["wishes"][0], "carriers": [
            {"path": "identity/wishes/first-wish.md",
             "probe": "A STRING THAT IS NOT THERE"}]}]
        check("a carrier that lost its probe string was not caught",
              any("probe string not found" in x
                  for x in write(wrong_probe)["carriers"]))

        no_probe = {**good}
        no_probe["wishes"] = [{**good["wishes"][0], "carriers": [
            {"path": "identity/wishes/first-wish.md"}]}]
        check("a carrier with no probe string was accepted",
              any("declares no probe" in x for x in write(no_probe)["carriers"]))

        # -- schema --------------------------------------------------------
        bad_id = {**good}
        bad_id["wishes"] = [{**good["wishes"][0], "id": "wish one"}]
        check("a malformed wish id was accepted",
              any("W-YYYY-MM-DD-SLUG" in x for x in write(bad_id)["schema"]))
        bad_wisher = {**good}
        bad_wisher["wishes"] = [{**good["wishes"][0], "wisher": "somebody"}]
        check("an undeclared wisher was accepted (the vocabulary is closed)",
              any("wisher" in x for x in write(bad_wisher)["schema"]))
        check("a wrong top-level schema was accepted",
              any("top-level 'schema'" in x
                  for x in write({**good, "schema": "nope"})["schema"]))

        # -- lineage -------------------------------------------------------
        cyc = {**_EMPTY_REGISTER, "wishes": [
            {"id": "W-2026-09-06-A", "wisher": "node", "date": "2026-09-06",
             "carriers": [], "grant": {"status": "OPEN"},
             "lineage": {"ripens_into": ["W-2026-09-06-B"]}},
            {"id": "W-2026-09-06-B", "wisher": "node", "date": "2026-09-06",
             "carriers": [], "grant": {"status": "OPEN"},
             "lineage": {"ripens_into": ["W-2026-09-06-A"]}}]}
        check("a directed lineage cycle was not detected",
              any("cycle" in x for x in write(cyc)["lineage"]))
        unknown_edge = {**_EMPTY_REGISTER, "wishes": [
            {"id": "W-2026-09-06-A", "wisher": "node", "date": "2026-09-06",
             "carriers": [], "grant": {"status": "OPEN"},
             "lineage": {"vibes_into": ["W-2026-09-06-A"]}}]}
        check("an unknown lineage edge was accepted",
              any("unknown edge key" in x for x in write(unknown_edge)["lineage"]))
        dangling = {**_EMPTY_REGISTER, "wishes": [
            {"id": "W-2026-09-06-A", "wisher": "node", "date": "2026-09-06",
             "carriers": [], "grant": {"status": "OPEN"},
             "lineage": {"frames": ["W-2026-09-06-NOWHERE"]}}]}
        check("a lineage edge to a missing wish was accepted",
              any("not in register" in x for x in write(dangling)["lineage"]))

        # -- open decisions ------------------------------------------------
        undeclared = {**_EMPTY_REGISTER, "wishes": [
            {"id": "W-2026-09-06-A", "wisher": "node", "date": "2026-09-06",
             "carriers": [], "grant": {"status": "OPEN"}, "lineage": {},
             "open_decision": "D-never-listed"}]}
        check("an open_decision missing from decisions_open was accepted",
              any("not listed in decisions_open" in x
                  for x in write(undeclared)["open_decisions"]))

        # -- an unreadable register is a FAILURE, never an empty pass -------
        reg.write_text("this: [is not: valid yaml\n", encoding="utf-8")
        check("invalid YAML did not read as a schema failure",
              any("unreadable or invalid YAML" in x
                  for x in run_checks(reg, root)["schema"]))
        check("a missing register did not read as a schema failure",
              any("unreadable" in x
                  for x in run_checks(root / "nowhere.yaml", root)["schema"]))

    if f:
        return False, f"{len(f)} check(s) failed: " + "; ".join(f)
    return True, ("all 5 checks fire; a valid empty register passes; a missing "
                  "carrier, a lost probe string and a probeless carrier are "
                  "each caught; a malformed id, an undeclared wisher and a "
                  "wrong schema are caught; lineage cycles, unknown edges and "
                  "dangling targets are caught; an unreadable register is a "
                  "failure rather than an empty pass")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Structural validator for a node's wish register")
    ap.add_argument("register", nargs="?", default=None,
                    help=f"path to REGISTER.yaml (default: {DEFAULT_REGISTER})")
    ap.add_argument("--workspace", default=None,
                    help="node repository root (default: three levels above "
                         "the register)")
    ap.add_argument("--selftest", action="store_true",
                    help="prove every check can fire")
    args = ap.parse_args(list(argv) if argv is not None else None)

    if args.selftest:
        ok, msg = selftest()
        print(f"{'PASS' if ok else 'FAIL'} wish_register_check selftest: {msg}")
        return 0 if ok else 1

    register_path = Path(args.register) if args.register else DEFAULT_REGISTER
    workspace = Path(args.workspace) if args.workspace else None
    results = run_checks(register_path, workspace)

    total = 0
    for name in CHECKS:
        failures = results[name]
        if failures:
            total += len(failures)
            print(f"[FAIL] {name} ({len(failures)})")
            for failure in failures:
                print(f"  FAIL {failure}")
        else:
            print(f"[OK]   {name}")
    if total:
        print(f"RESULT: FAIL ({total} failure(s)) -- {register_path}")
        return 1
    print(f"RESULT: OK -- {register_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
