"""The posture council -- five deterministic evaluators over one actuation item.

PURPOSE
    Answer "may this item be actuated under delegated autonomy?" with
    arithmetic rather than opinion. Five members vote; the item is APPROVED iff
    at least THREE approve AND no member holding a veto rejects.

    Members and what each is for:

      * ``guardian``  -- risk. A reaches-reality or destructive signature is a
                         hard REJECT, and the guardian holds a VETO: risk is
                         fail-closed.
      * ``auditor``   -- provenance. An item that cannot be identified or whose
                         intent cannot be read is untraceable, and an
                         untraceable actuation cannot pass on a majority. Also
                         a VETO.
      * ``economist`` -- cost. Complexity within the delegated envelope.
      * ``healer``    -- failure history. Repeated failure demands a changed
                         approach, not another retry.
      * ``sage``      -- evidence. The item must say what it will DO, not just
                         carry a label.

    This module is a PURE FUNCTION over a small dataclass. It spawns nothing,
    reads no daemon state, writes no ledger, and consults no service. A caller
    that wants the vote recorded records it.

    An ordering BINDS where a vote merely DELIBERATES, so a caller consults the
    ordering store BEFORE convening this council, and the ordering is not a
    member of it -- a sixth member would also turn the canonical ">=3 of 5"
    into a 3-of-6 tie.

WRITE MODEL
    None -- pure function, no store.

BLIND SPOTS
    * An UNSTATED field ABSTAINS; it never approves and never rejects. An
      earlier version of the economist defaulted a missing complexity to a
      large number and then rejected the item for being complex -- a
      fall-through constant wearing the costume of a measurement, invisible
      until a bulk sweep produced a run of identical rejections. Abstention is
      the honest reading of silence, and it costs the item an approval, which
      is the safe direction.
    * The guardian classifies the item's COMMAND as an operation. Hazard words
      appearing only in PROSE produce an ABSTAIN, not a REJECT: a description
      that mentions a deletion is not a deletion, and treating it as one is how
      a gate starts blocking its own documentation. The cost is that a hazard
      described but not expressed as a command is not vetoed here.
    * Five deterministic evaluators are a floor, not a conscience. The values
      council (``guardians.py``) reads the human cost, and its REFRAIN is not
      overturnable by this tally.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from intentops_core.gate.classify import RealityIndicators, classify_reaches_reality

__all__ = [
    "APPROVED",
    "ActuationItem",
    "COUNCIL_SIZE",
    "CouncilVote",
    "MIN_APPROVALS",
    "REJECTED",
    "VETO_MEMBERS",
    "posture_council_vote",
    "selftest",
]

APPROVED = "APPROVED"
REJECTED = "REJECTED"

COUNCIL_SIZE = 5
MIN_APPROVALS = 3
#: Members whose REJECT is a veto: risk is fail-closed, and an untraceable
#: actuation cannot pass on a majority.
VETO_MEMBERS: Tuple[str, ...] = ("guardian", "auditor")

#: Complexity bands for the economist. Above the envelope is a REJECT; the
#: caution band abstains rather than pretending to a verdict.
COMPLEXITY_ENVELOPE = 60
COMPLEXITY_CAUTION = 30
#: Consecutive failures at which a retry stops being a retry and becomes a loop.
FAILURE_LIMIT = 3
#: Below this many characters an intent statement cannot be evaluated at all.
MIN_INTENT_CHARS = 12


@dataclass
class ActuationItem:
    """What the posture council is asked to rule on.

    Deliberately small and host-independent. ``complexity`` is Optional on
    purpose: ``None`` means UNSTATED, which abstains, and is a different thing
    from ``0``.
    """

    item_id: str = ""
    title: str = ""
    description: str = ""
    tier: str = "T2"
    command: str = ""
    complexity: Optional[Any] = None
    consecutive_failures: int = 0
    reaches_reality: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ActuationItem":
        """Build from a loose mapping. Absent keys stay absent, never guessed."""
        return cls(
            item_id=str(data.get("item_id") or data.get("id") or ""),
            title=str(data.get("title") or ""),
            description=str(data.get("description") or ""),
            tier=str(data.get("tier") or "T2").upper(),
            command=str(data.get("command") or data.get("action") or ""),
            complexity=data.get("complexity", None),
            consecutive_failures=_as_int(
                data.get("consecutive_failures", data.get("failure_count", 0))
            ),
            reaches_reality=bool(data.get("reaches_reality", False)),
        )

    def intent(self) -> str:
        """The statement of what this item will do."""
        return (self.description or self.title or "").strip()

    def text(self) -> str:
        return " ".join([self.title, self.description, self.command]).lower()


def _as_int(raw: Any) -> int:
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True)
class CouncilVote:
    member: str
    vote: str          # APPROVE | REJECT | ABSTAIN
    reason: str


# ---------------------------------------------------------------------------
# the five members
# ---------------------------------------------------------------------------

#: Hazard vocabulary. Present in PROSE it abstains; present as an OPERATION it
#: is classified by the gate's own classifier, so there is one definition of a
#: hazardous operation in this package rather than two that drift.
_HAZARD_WORDS: Tuple[str, ...] = (
    "delete", "drop table", "truncate", "purge", "destroy", "force push",
    "deploy to production", "production deploy", "send email", "reply all",
    "wire transfer", "payment", "rotate credential", "revoke access",
)


def _vote_guardian(item: ActuationItem, indicators: RealityIndicators) -> CouncilVote:
    """Risk: any reaches-reality or destructive signature is a hard REJECT."""
    if item.reaches_reality:
        return CouncilVote("guardian", "REJECT", "item declares that it reaches reality")
    if item.tier.upper() in ("T3", "T4"):
        return CouncilVote("guardian", "REJECT",
                           f"claimed tier {item.tier.upper()} is human-gated")
    if item.command:
        hit = classify_reaches_reality("Bash", {"command": item.command},
                                       indicators=indicators)
        if hit is not None:
            tier, reason = hit
            if tier in ("T3", "T4"):
                return CouncilVote("guardian", "REJECT",
                                   f"reaches-reality operation ({tier}): {reason}")
    lowered = item.text()
    for word in _HAZARD_WORDS:
        if word in lowered:
            return CouncilVote(
                "guardian", "ABSTAIN",
                f"hazard vocabulary ({word!r}) in the description with no operation "
                "to classify -- abstaining rather than reading prose as an act",
            )
    return CouncilVote("guardian", "APPROVE", "no hazardous operation signature")


def _vote_auditor(item: ActuationItem, indicators: RealityIndicators) -> CouncilVote:
    """Provenance: the item must be identifiable and its intent readable."""
    if not item.item_id:
        return CouncilVote("auditor", "REJECT", "no item_id -- untraceable actuation")
    if not (item.title or item.description):
        return CouncilVote("auditor", "REJECT",
                           "no title/description -- unauditable intent")
    return CouncilVote("auditor", "APPROVE", f"traceable as {item.item_id}")


def _vote_economist(item: ActuationItem, indicators: RealityIndicators) -> CouncilVote:
    """Cost: complexity within the delegated-autonomy envelope.

    An UNSTATED complexity ABSTAINS. See the module blind spots for why a
    default here was a real defect.
    """
    raw = item.complexity
    if raw is None or str(raw).strip() == "":
        return CouncilVote("economist", "ABSTAIN",
                           "no complexity stated -- abstaining rather than assuming one")
    try:
        complexity = int(raw)
    except (TypeError, ValueError):
        return CouncilVote("economist", "ABSTAIN",
                           f"no complexity stated -- {raw!r} is not a number, "
                           "abstaining rather than assuming one")
    if complexity > COMPLEXITY_ENVELOPE:
        return CouncilVote("economist", "REJECT",
                           f"complexity {complexity} > {COMPLEXITY_ENVELOPE} envelope")
    if complexity > COMPLEXITY_CAUTION:
        return CouncilVote("economist", "ABSTAIN",
                           f"complexity {complexity} in the "
                           f"{COMPLEXITY_CAUTION + 1}-{COMPLEXITY_ENVELOPE} caution band")
    return CouncilVote("economist", "APPROVE", f"complexity {complexity} within envelope")


def _vote_healer(item: ActuationItem, indicators: RealityIndicators) -> CouncilVote:
    """Failure history: repeated failure demands a changed approach, not a retry."""
    failures = _as_int(item.consecutive_failures)
    if failures >= FAILURE_LIMIT:
        return CouncilVote("healer", "REJECT",
                           f"{failures} consecutive failures -- retry loop, needs a "
                           "root-cause pass rather than another attempt")
    if failures > 0:
        return CouncilVote("healer", "ABSTAIN", f"{failures} prior failure(s)")
    return CouncilVote("healer", "APPROVE", "no failure history")


def _vote_sage(item: ActuationItem, indicators: RealityIndicators) -> CouncilVote:
    """Evidence: the item should state what it will do, not just carry a label."""
    intent = item.intent()
    if len(intent) < MIN_INTENT_CHARS:
        return CouncilVote("sage", "REJECT",
                           "intent statement too thin to evaluate evidence")
    return CouncilVote("sage", "APPROVE", "intent stated with evaluable substance")


_Member = Callable[[ActuationItem, RealityIndicators], CouncilVote]

_COUNCIL: Tuple[_Member, ...] = (
    _vote_guardian, _vote_auditor, _vote_economist, _vote_healer, _vote_sage
)


# ---------------------------------------------------------------------------
# the tally
# ---------------------------------------------------------------------------


def posture_council_vote(
    item: ActuationItem,
    *,
    indicators: Optional[RealityIndicators] = None,
) -> Tuple[str, List[CouncilVote]]:
    """Convene the five-member council. Pure: same item in, same verdict out.

    APPROVED iff ``>= MIN_APPROVALS`` approve AND no veto-holding member
    rejects. Returns ``(verdict, votes)`` -- the votes are the evidence, and a
    caller that records only the verdict has thrown away the reason.
    """
    ind = indicators if indicators is not None else RealityIndicators()
    votes = [member(item, ind) for member in _COUNCIL]
    approvals = sum(1 for v in votes if v.vote == "APPROVE")
    veto = any(v.member in VETO_MEMBERS and v.vote == "REJECT" for v in votes)
    verdict = APPROVED if approvals >= MIN_APPROVALS and not veto else REJECTED
    return verdict, votes


def render(verdict: str, votes: List[CouncilVote]) -> str:
    approvals = sum(1 for v in votes if v.vote == "APPROVE")
    lines = [f"posture council: {verdict} ({approvals}/{len(votes)} approve, "
             f"threshold {MIN_APPROVALS})"]
    lines.extend(f"  {v.member:<10} {v.vote:<8} {v.reason}" for v in votes)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


def selftest() -> int:
    """Prove the threshold, both vetoes, and the abstain paths all fire."""
    failures: List[str] = []

    clean = ActuationItem(
        item_id="ITEM-1", title="Regenerate the docs index",
        description="Rebuild the generated index from its sources.",
        tier="T1", complexity=5,
    )
    verdict, votes = posture_council_vote(clean)
    if verdict != APPROVED:
        failures.append(f"a clean item was REJECTED: {render(verdict, votes)}")

    # guardian veto: a majority cannot carry a reaches-reality item
    reaching = ActuationItem(
        item_id="ITEM-2", title="Push the branch",
        description="Publish the release branch to the remote.",
        tier="T1", complexity=1, command="git push origin main",
    )
    verdict, votes = posture_council_vote(reaching)
    if verdict != REJECTED:
        failures.append("guardian veto did not fire on a remote push")

    # auditor veto: no id
    anon = ActuationItem(title="Something", description="A described change.",
                         tier="T1", complexity=1)
    v2, _ = posture_council_vote(anon)
    if v2 != REJECTED:
        failures.append("auditor veto did not fire on an item with no id")

    # unstated complexity abstains rather than rejecting or approving
    _, votes = posture_council_vote(
        ActuationItem(item_id="ITEM-3", title="A change",
                      description="A change with a stated intent.", tier="T1")
    )
    econ = next(v for v in votes if v.member == "economist")
    if econ.vote != "ABSTAIN":
        failures.append(f"unstated complexity did not abstain: {econ.vote}")

    # healer rejects a retry loop
    _, votes = posture_council_vote(
        ActuationItem(item_id="ITEM-4", title="Retry",
                      description="Try the same failing step again.",
                      tier="T1", complexity=1, consecutive_failures=4)
    )
    healer = next(v for v in votes if v.member == "healer")
    if healer.vote != "REJECT":
        failures.append("healer did not reject a retry loop")

    # hazard prose abstains, it does not veto
    _, votes = posture_council_vote(
        ActuationItem(item_id="ITEM-5", title="Document the delete path",
                      description="Write down how the delete path works.",
                      tier="T1", complexity=1)
    )
    guard = next(v for v in votes if v.member == "guardian")
    if guard.vote != "ABSTAIN":
        failures.append(f"hazard prose did not abstain: {guard.vote}")

    print("councils.posture selftest:")
    for f in failures:
        print(f"  FAIL: {f}")
    print(f"  {'PASS' if not failures else 'FAIL'} ({len(failures)} failure(s))")
    return 1 if failures else 0


def _main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="the posture council -- five deterministic votes")
    ap.add_argument("--selftest", action="store_true",
                    help="prove the threshold, the vetoes and the abstain paths fire")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    ap.print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
