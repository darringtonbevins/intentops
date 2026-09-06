"""One grok cycle, recorded. Dry-run is the only mode, on purpose.

PURPOSE
    A grok cycle is the metabolism's DELIBERATE pass over its own corpus:
    four phases, in order, each producing findings.

      ``curate``     read what is actually there. Not infer, READ. The phase
                     that most often gets skipped, and skipping it is how a
                     cycle comes to grade its own guesses.
      ``pollinate``  carry a finding from where it was found to where it is
                     also true. Cross-domain transfer is the whole reason a
                     corpus beats a pile of documents.
      ``percolate``  bubble what recurs upward: a finding seen in three places
                     is a pattern, and a pattern is a crystallize candidate.
      ``heal``       repair what the first three phases found, smallest
                     reversible change first.

    ``forge`` is grok AT SCALE: the same four phases run as N parallel legs
    over N slices, each leg adversarially verified by an INDEPENDENT reviewer
    before its finding counts. That invariant -- adversarial verification by a
    reviewer that is not the author -- is the cycle's actual content. The
    specific machinery is not: the reference implementation moved from one
    orchestration primitive to another mid-life and the cycle survived
    unchanged, which is the evidence that the primitive was never the point.

    WHY DRY-RUN IS THE ONLY MODE HERE. Running a cycle for real needs a
    reasoning engine, and this seed has none by construction: model calls are
    DECLARED SEAMS, never dependencies. So this module RECORDS a cycle -- what
    each phase would read, produce, and hand to the next -- and refuses to
    claim any phase ran. A recorder that quietly returned plausible findings
    would be manufacturing exactly the ungrounded output the framework exists
    to detect: an answer produced where the context held no evidence is
    imagined information however confident it sounds.

    A PLAN IS NOT A FINDING. Every phase in a recorded cycle carries
    ``executed: false``, and :meth:`GrokCycle.findings` is empty by
    construction. A leg that returns a plan instead of executed evidence is a
    FAILED leg, and this module makes that structurally true rather than
    something a reviewer has to notice.

WRITE MODEL
    NONE OF ITS OWN. :func:`record_cycle` returns a :class:`GrokCycle`;
    :func:`main` prints it, and ``--out`` writes to a path the operator named.
    A recorder that appended to a store the operator never chose would be a
    store with no declared model, which is the shared-whiteboard shape this
    framework refuses at birth.

BLIND SPOTS
    * A recorded cycle is a PLAN. It proves the shape of the cycle and nothing
      about the corpus it names. Every count in it is zero and says so.
    * The phase order is fixed and validated; the phase CONTENT is prose the
      caller supplies. Nothing here checks that a slice is worth reading.
    * ``forge`` plans legs. It does not spawn them, does not know whether the
      host can spawn them, and its leg count is a declaration rather than a
      capacity check.
    * The adversarial-verification invariant is DECLARED in the plan and
      enforced by nothing here. A host that runs the plan without an
      independent reviewer produces an unverified cycle that this module
      cannot tell from a verified one.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "GrokError",
    "PHASES",
    "PHASE_PURPOSE",
    "PhaseRecord",
    "GrokCycle",
    "ForgePlan",
    "record_cycle",
    "plan_forge",
    "selftest",
    "main",
]

#: The four phases, in order. Fixed by declaration.
PHASES: Tuple[str, ...] = ("curate", "pollinate", "percolate", "heal")

PHASE_PURPOSE: Dict[str, str] = {
    "curate": "read what is actually there -- read, never infer",
    "pollinate": "carry a finding to the other places it is also true",
    "percolate": "bubble up what recurs; three sightings is a pattern",
    "heal": "repair what was found, smallest reversible change first",
}

#: The one thing that makes a leg's output count. Declared in every plan.
VERIFY_INVARIANT = (
    "every finding is refuted by an INDEPENDENT reviewer before it counts; a "
    "silent leg is a FAILED leg, and a returned plan is never graded as a "
    "finding"
)


class GrokError(RuntimeError):
    """A refusal with a remedy attached."""

    def __init__(self, reason: str, remedy: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.remedy = remedy


@dataclass
class PhaseRecord:
    """One phase of a recorded cycle. ``executed`` is False, always, here."""

    name: str
    purpose: str
    reads: List[str] = field(default_factory=list)
    would_produce: List[str] = field(default_factory=list)
    executed: bool = False
    findings: List[str] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        """``PLANNED`` while nothing ran. Never ``OK``.

        A phase that has not run has not passed, and the two must not share a
        word. ``WARN`` is what a phase that RAN and produced nothing reads as;
        this is the state before that question is even askable.
        """
        if not self.executed:
            return "PLANNED"
        return "OK" if self.findings else "WARN"

    def to_row(self) -> Dict[str, Any]:
        return {"phase": self.name, "purpose": self.purpose,
                "reads": list(self.reads),
                "would_produce": list(self.would_produce),
                "executed": self.executed, "verdict": self.verdict,
                "findings": list(self.findings)}


@dataclass
class GrokCycle:
    """A recorded cycle: what it would do, and the honest claim that it did not."""

    focus: str
    dry_run: bool = True
    phases: List[PhaseRecord] = field(default_factory=list)

    @property
    def findings(self) -> List[str]:
        out: List[str] = []
        for phase in self.phases:
            out.extend(phase.findings)
        return out

    @property
    def posture(self) -> str:
        return "DRY-RUN" if self.dry_run else "EXECUTED"

    def to_row(self) -> Dict[str, Any]:
        return {"focus": self.focus, "posture": self.posture,
                "dry_run": self.dry_run,
                "verify_invariant": VERIFY_INVARIANT,
                "findings": self.findings,
                "phases": [p.to_row() for p in self.phases]}

    def render(self) -> str:
        lines = [f"grok cycle [{self.posture}] focus: {self.focus or '(whole corpus)'}"]
        for phase in self.phases:
            lines.append(f"  {phase.name:<11} {phase.verdict:<8} "
                         f"{phase.purpose}")
            for src in phase.reads:
                lines.append(f"      reads: {src}")
        lines.append("  findings: none -- nothing ran. A plan is not a finding.")
        return "\n".join(lines)


@dataclass
class ForgePlan:
    """Grok at scale: N slices, each a cycle, each independently verified."""

    slices: List[str]
    cycles: List[GrokCycle] = field(default_factory=list)
    verify_invariant: str = VERIFY_INVARIANT

    def to_row(self) -> Dict[str, Any]:
        return {"slices": list(self.slices),
                "leg_count": len(self.cycles),
                "verify_invariant": self.verify_invariant,
                "executed": False,
                "cycles": [c.to_row() for c in self.cycles]}


def record_cycle(focus: str = "",
                 reads: Optional[Mapping[str, Sequence[str]]] = None
                 ) -> GrokCycle:
    """Record ONE dry-run cycle over ``focus``.

    ``reads`` optionally names, per phase, what that phase would read. An
    unknown phase name is a HALT rather than an ignored key: a caller who
    misspells a phase would otherwise get a cycle silently missing the sources
    they meant to declare.
    """
    reads = dict(reads or {})
    unknown = sorted(set(map(str, reads)) - set(PHASES))
    if unknown:
        raise GrokError(
            f"unknown phase(s) {unknown}",
            remedy="the four phases are " + " -> ".join(PHASES))

    cycle = GrokCycle(focus=focus, dry_run=True)
    for name in PHASES:
        cycle.phases.append(PhaseRecord(
            name=name,
            purpose=PHASE_PURPOSE[name],
            reads=[str(r) for r in reads.get(name, ())],
            would_produce=_would_produce(name),
        ))
    return cycle


def _would_produce(phase: str) -> List[str]:
    return {
        "curate": ["observations, each with the file it was read from"],
        "pollinate": ["transfers: a finding, and where else it holds"],
        "percolate": ["crystallize candidates: what recurred, and where"],
        "heal": ["proposed repairs, smallest reversible change first"],
    }[phase]


def plan_forge(slices: Sequence[str],
               reads: Optional[Mapping[str, Sequence[str]]] = None
               ) -> ForgePlan:
    """Plan grok at scale: one recorded cycle per slice.

    An EMPTY slice list is a HALT. A forge over nothing that returned a
    well-formed empty plan is the exact shape of a run that succeeds while its
    legs never received their inputs.
    """
    slices = [str(s) for s in slices]
    if not slices:
        raise GrokError(
            "a forge plan over zero slices",
            remedy="name at least one slice; an empty fan-out that returns a "
                   "well-formed result is how a run 'succeeds' having done "
                   "nothing")
    return ForgePlan(slices=slices,
                     cycles=[record_cycle(focus=s, reads=reads) for s in slices])


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


def selftest() -> Tuple[bool, str]:
    """Prove the recorder records, refuses, and never claims a phase ran."""
    fired: List[str] = []
    failures: List[str] = []

    def expect(name: str, ok: bool) -> None:
        fired.append(name)
        if not ok:
            failures.append(name)

    cycle = record_cycle("the metabolism", {"curate": ["config/"]})
    expect("four-phases-in-order",
           [p.name for p in cycle.phases] == list(PHASES))
    expect("dry-run-is-the-posture", cycle.posture == "DRY-RUN")
    expect("no-phase-claims-to-have-run",
           all(not p.executed for p in cycle.phases))
    expect("no-phase-reads-ok-while-planned",
           all(p.verdict == "PLANNED" for p in cycle.phases))
    expect("a-plan-yields-no-findings", cycle.findings == [])
    expect("declared-reads-are-carried",
           cycle.phases[0].reads == ["config/"])

    try:
        record_cycle("x", {"curat": ["config/"]})
        expect("misspelled-phase-halts", False)
    except GrokError:
        expect("misspelled-phase-halts", True)

    plan = plan_forge(["a", "b", "c"])
    expect("forge-is-one-cycle-per-slice", len(plan.cycles) == 3)
    expect("forge-declares-the-verify-invariant",
           "INDEPENDENT" in plan.verify_invariant)
    expect("forge-never-claims-execution",
           plan.to_row()["executed"] is False)

    try:
        plan_forge([])
        expect("empty-forge-halts", False)
    except GrokError:
        expect("empty-forge-halts", True)

    # an EXECUTED phase with no findings reads WARN, never OK -- the same rule
    # the cadence enforces, stated once more where a cycle could hide it
    executed = PhaseRecord("curate", "p", executed=True)
    expect("executed-and-empty-is-warn", executed.verdict == "WARN")
    executed.findings.append("something real")
    expect("executed-with-findings-is-ok", executed.verdict == "OK")

    report = (f"grok selftest: {len(fired)} paths fired, {len(failures)} failed"
              + (" -- " + ", ".join(failures) if failures else ""))
    return (not failures, report)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="record one dry-run grok cycle (the only mode there is)")
    parser.add_argument("--focus", default="",
                        help="what this cycle is over (default: whole corpus)")
    parser.add_argument("--slices", nargs="*", default=None,
                        help="plan a forge: one cycle per slice")
    parser.add_argument("--out", default=None,
                        help="write the record to a path you name; the only "
                             "path this module writes")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        ok, report = selftest()
        print(report)
        return 0 if ok else 1

    try:
        if args.slices is not None:
            row: Dict[str, Any] = plan_forge(args.slices).to_row()
            text = json.dumps(row, indent=2)
        else:
            cycle = record_cycle(args.focus)
            row = cycle.to_row()
            text = json.dumps(row, indent=2) if args.json else cycle.render()
    except GrokError as exc:
        print(f"HALT: {exc.reason}\n  remedy: {exc.remedy}")
        return 1

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(row, indent=2) + "\n",
                                  encoding="utf-8")
        print(f"wrote {args.out}")
        return 0
    print(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
