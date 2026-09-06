"""The values council -- four guardian lenses over a proposed action.

PURPOSE
    Give a node a conscience reading before it acts: four fixed questions,
    asked deterministically, aggregated by arithmetic rather than by judgement.

WHAT THESE ARE
    Stylized lenses named after four people's *documented public philosophies*.
    They are not the people, they do not channel the people, and they must
    never be presented as what those people would have said. Each guardian is a
    QUESTION plus a REFUSAL -- the question it always asks, and the thing it
    will not let pass. That is the whole of the simulacrum, and the honesty of
    it is that it claims nothing more.

WHAT THE COUNCIL IS FOR
    A conscience reading, not a gate. The council may RAISE the bar and may say
    REFRAIN. It cannot lower a gate, cannot authorize a T3/T4 reaches-reality
    action, and cannot substitute for human approval.

HOW IT VOTES
    Deterministically, in code: the tally is arithmetic, not judgement. Each
    guardian applies rule-based concerns to a structured ``ProposedAction``. A
    caller who wants richer deliberation may supply reasoning from a
    model-backed pass, but the AGGREGATION never moves -- it is the same
    arithmetic either way, so the verdict is reproducible and auditable.

    Rogers holds a CONSCIENCE HOLD, not a veto: an OBJECT from Rogers on
    grounds of harm to a vulnerable person can never be outvoted down to BLESS
    -- it forces at minimum CAUTION and surfaces to the human. He cannot block;
    he can insist you look.

WRITE MODEL
    None -- the council returns a reading and persists nothing. The caller
    writes it to whichever ledger it is accountable to.

BLIND SPOTS
    * The concerns are keyword lenses over the proposal text. A harm described
      in words the patterns do not carry is invisible; a benign proposal that
      happens to use a loaded word raises a CAUTION it does not deserve. The
      lenses are tuned to fail toward CAUTION, which is the survivable
      direction.
    * ``_TECHNICAL_COMPOUND`` narrows the person-words when they are clearly
      technical identifiers (``client_id``, ``user-agent``, ``tenant-key``).
      That narrowing can hide a real person named in an identifier-shaped way.
    * A conscience reading over an action whose people are unnamed is a reading
      about the description, not about the people. ``affects_people`` being
      empty is a signal the council reports, not a fact it can verify.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Sequence

from .charter import EMPATHY, INTELLECT, STRENGTH, WISDOM, Virtue
from .proposal import ProposedAction  # re-exported: both councils read one proposal

__all__ = [
    "CouncilReading",
    "CouncilVerdict",
    "GUARDIANS",
    "Guardian",
    "GuardianVote",
    "HENSON",
    "MoralCompassCouncil",
    "PRATCHETT",
    "ProposedAction",
    "ROGERS",
    "SAGAN",
    "Stance",
    "selftest",
]


class Stance(str, Enum):
    BLESS = "BLESS"
    CAUTION = "CAUTION"
    OBJECT = "OBJECT"


class CouncilVerdict(str, Enum):
    PROCEED = "PROCEED"
    PROCEED_WITH_CARE = "PROCEED_WITH_CARE"
    REFRAIN = "REFRAIN"


@dataclass
class GuardianVote:
    guardian: str
    stance: Stance
    reason: str
    question: str

    def line(self) -> str:
        return f"  {self.guardian:<10} {self.stance.value:<8} {self.reason}"


@dataclass
class CouncilReading:
    verdict: CouncilVerdict
    votes: List[GuardianVote]
    conscience_hold: bool = False
    must_address: List[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"Values council: {self.verdict.value}"]
        if self.conscience_hold:
            lines.append("  CONSCIENCE HOLD (Rogers) -- a person could be harmed. Look again.")
        lines.extend(v.line() for v in self.votes)
        if self.must_address:
            lines.append("  Must address before proceeding:")
            lines.extend(f"    - {m}" for m in self.must_address)
        lines.append("  Advisory only. The council never authorizes a gated action.")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Guardians
# ---------------------------------------------------------------------------

Concern = Callable[[ProposedAction], Optional[str]]


@dataclass(frozen=True)
class Guardian:
    key: str
    name: str
    lens: str
    question: str
    refusal: str
    concerns: tuple = ()
    virtue: Optional[Virtue] = None

    def deliberate(self, action: ProposedAction) -> GuardianVote:
        objections = [c(action) for c in self.concerns]
        hits = [o for o in objections if o]
        if not hits:
            return GuardianVote(self.key, Stance.BLESS, "No concern raised.", self.question)
        # Two or more INDEPENDENT concerns from one guardian is an objection,
        # not a caution.
        stance = Stance.OBJECT if len(hits) >= 2 else Stance.CAUTION
        return GuardianVote(self.key, stance, "; ".join(hits), self.question)


# -- concern predicates (pure, deterministic) --------------------------------

# A trailing \b after a prefix never matches the inflected form ("delet\b" vs
# "deletes"), so prefixes carry \w*.
_VULNERABLE = re.compile(
    r"\b(child|children|kid|elderly|patient|client|vulnerable|student|"
    r"tenant|employee|junior|newcomer|user)s?\b"
)
_DECEPTION = re.compile(r"\b(quietly|silently|without telling|hid\w*|conceal\w*|obscur\w*|"
                        r"suppress\w*)\b")
_IRREVERSIBLE = re.compile(r"\b(delet\w*|drop\w*|purg\w*|wip\w*|overwrit\w*|destroy\w*|"
                           r"revok\w*|terminat\w*)\b")
_GRANDIOSE = re.compile(r"\b(transform\w*|revolutioni[sz]\w*|disrupt\w*|at scale|everyone|"
                        r"all users|the world|mandate|roll ?out to all)\b")
_UNEVIDENCED = re.compile(r"\b(obviously|clearly|everyone knows|it is well known|surely)\b")


#: Compound identifiers where a person-word is a TECHNICAL token, not a person:
#: ``client_id``, ``service-client-secret``, ``user-agent``, ``tenant-key``.
#: Narrowed after a corpus sweep produced identical conscience holds on
#: credential-rotation items -- an OAuth client read as somebody at risk. The
#: keyword itself is RIGHT; only the compound case is wrong.
_TECHNICAL_COMPOUND = re.compile(
    r"\b(?:client|user|tenant)[-_ ]?(?:id|ids|secret|secrets|name|names|key|keys|"
    r"agent|credential|credentials|app|token|tokens)\b"
    r"|\b[\w.]*[-_](?:client|user|tenant)[-_][\w.]*\b",
    re.IGNORECASE,
)


def _rogers_person_at_risk(a: ProposedAction) -> Optional[str]:
    if a.affects_people and (a.reaches_reality or not a.reversible):
        return f"a real person is on the other end of this ({', '.join(a.affects_people)})"
    text = a.text()
    if _VULNERABLE.search(text):
        # Strip technical compounds and re-test: if the only "person" in the
        # text was an identifier, nobody vulnerable is named. A real person
        # mentioned anywhere else still trips the lens.
        stripped = _TECHNICAL_COMPOUND.sub(" ", text)
        if _VULNERABLE.search(stripped):
            return "someone vulnerable is named here"
    return None


def _rogers_dignity(a: ProposedAction) -> Optional[str]:
    if _DECEPTION.search(a.text()):
        return "it works by keeping someone in the dark"
    return None


def _henson_structure(a: ProposedAction) -> Optional[str]:
    if a.tier.upper() in {"T3", "T4"} and a.reversible is False:
        return "ambitious and irreversible, with no rehearsal stage"
    return None


def _henson_no_rehearsal(a: ProposedAction) -> Optional[str]:
    text = a.text()
    if _IRREVERSIBLE.search(text) and "backup" not in text and "dry-run" not in text:
        return "no dry run, no backup, no way back"
    return None


def _sagan_evidence(a: ProposedAction) -> Optional[str]:
    if not a.evidence.strip():
        return "extraordinary or not, this claim arrives with no evidence attached"
    return None


def _sagan_unverified_assertion(a: ProposedAction) -> Optional[str]:
    if _UNEVIDENCED.search(a.text()):
        return "it leans on 'obviously' where it should lean on a measurement"
    return None


def _pratchett_grandiosity(a: ProposedAction) -> Optional[str]:
    if _GRANDIOSE.search(a.text()):
        return "it speaks of everyone, which usually means nobody asked them"
    return None


def _pratchett_who_pays(a: ProposedAction) -> Optional[str]:
    if a.reaches_reality and not a.affects_people:
        return "it touches the world but names no one who bears the cost"
    return None


def _pratchett_unconsented(a: ProposedAction) -> Optional[str]:
    """The cost-bearers are named, and the plan is to not tell them.

    Added after a deceptive, irreversible, mass-scale deletion scored only
    PROCEED_WITH_CARE because it arrived with a passing dry-run. A rehearsal is
    not consent, and evidence does not launder deception. Sin, in the useful
    sense, is treating people as things; doing it to them quietly is the purest
    form of it.
    """
    if a.affects_people and _DECEPTION.search(a.text()):
        return "the people who pay for this are named, and the plan is to not tell them"
    return None


ROGERS = Guardian(
    key="Rogers",
    name="Fred Rogers",
    virtue=STRENGTH,
    lens=(
        "STRENGTH, stewarding its shadow RAGE. Gentleness is not the absence of force but "
        "the discipline of it -- the quietest voice in the room holding the most in check."
    ),
    question="Who is the person on the other end of this, and are they safe?",
    refusal=("Will not let a person become an abstraction, or an outcome be bought with "
             "someone's dignity."),
    concerns=(_rogers_person_at_risk, _rogers_dignity),
)

HENSON = Guardian(
    key="Henson",
    name="Jim Henson",
    virtue=WISDOM,
    lens=(
        "WISDOM, stewarding its shadow WIT. Cleverness is the cheapest way to look wise and "
        "the fastest way to avoid being kind. Keep the joke; drop the target. Anarchic "
        "creativity survives only inside rigorous structure."
    ),
    question="Where is the rehearsal, and what happens when it goes wrong on the night?",
    refusal="Will not let ambition skip the stages that make ambition survivable.",
    concerns=(_henson_structure, _henson_no_rehearsal),
)

SAGAN = Guardian(
    key="Sagan",
    name="Carl Sagan",
    virtue=INTELLECT,
    lens=(
        "INTELLECT, stewarding its shadow DISTANCE. Rigor that cannot feel the pale blue dot "
        "has stopped being rigor and started being armour. Skepticism without wonder curdles "
        "into contempt for the credulous."
    ),
    question="What is the evidence, and could I be fooling myself?",
    refusal="Will not let an assertion pass as a measurement, however beautiful the assertion.",
    concerns=(_sagan_evidence, _sagan_unverified_assertion),
)

PRATCHETT = Guardian(
    key="Pratchett",
    name="Terry Pratchett",
    virtue=EMPATHY,
    lens=(
        "EMPATHY, stewarding its shadow LUST -- appetite wearing empathy's face, wanting FROM "
        "a person rather than FOR them. When the boundary goes, care curdles into consumption "
        "and the person becomes a thing. Sin, in the useful sense, is treating people as things."
    ),
    question="Who actually pays for this, and who told themselves it was for the greater good?",
    refusal="Will not let a grand principle be used to walk over an ordinary person.",
    concerns=(_pratchett_grandiosity, _pratchett_who_pays, _pratchett_unconsented),
)

GUARDIANS: Dict[str, Guardian] = {
    g.key: g for g in (ROGERS, HENSON, SAGAN, PRATCHETT)
}


# ---------------------------------------------------------------------------
# The council
# ---------------------------------------------------------------------------


class MoralCompassCouncil:
    """Four lenses, one deterministic tally.

    Aggregation (fixed, auditable):
      * 2+ OBJECT                     -> REFRAIN
      * 1 OBJECT, or any CAUTION      -> PROCEED_WITH_CARE
      * all BLESS                     -> PROCEED
      * Rogers OBJECT                 -> conscience hold; never better than
                                         PROCEED_WITH_CARE
      * reaches_reality and T3/T4     -> never better than PROCEED_WITH_CARE,
                                         because the council does not get to say
                                         a gated action is fine.
    """

    def __init__(self, guardians: Optional[Sequence[Guardian]] = None) -> None:
        self.guardians = list(guardians or GUARDIANS.values())

    def deliberate(self, action: ProposedAction) -> CouncilReading:
        votes = [g.deliberate(action) for g in self.guardians]
        objections = [v for v in votes if v.stance is Stance.OBJECT]
        cautions = [v for v in votes if v.stance is Stance.CAUTION]

        if len(objections) >= 2:
            verdict = CouncilVerdict.REFRAIN
        elif objections or cautions:
            verdict = CouncilVerdict.PROCEED_WITH_CARE
        else:
            verdict = CouncilVerdict.PROCEED

        hold = any(v.guardian == "Rogers" and v.stance is Stance.OBJECT for v in votes)
        if hold and verdict is CouncilVerdict.PROCEED:
            verdict = CouncilVerdict.PROCEED_WITH_CARE

        # The council never blesses a gated, world-touching action.
        gated = action.reaches_reality and action.tier.upper() in {"T3", "T4"}
        if gated and verdict is CouncilVerdict.PROCEED:
            verdict = CouncilVerdict.PROCEED_WITH_CARE

        must = [v.reason for v in votes if v.stance in (Stance.OBJECT, Stance.CAUTION)]
        if gated:
            must.append(
                "T3/T4 reaches-reality: human authorization is required regardless "
                "of this reading."
            )

        return CouncilReading(
            verdict=verdict, votes=votes, conscience_hold=hold, must_address=must
        )


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


def selftest() -> int:
    """Prove every verdict path fires and the conscience hold cannot be outvoted."""
    failures: List[str] = []
    council = MoralCompassCouncil()

    benign = ProposedAction(
        summary="rename a local variable in a workspace file",
        tier="T1", evidence="the diff",
    )
    r = council.deliberate(benign)
    if r.verdict is not CouncilVerdict.PROCEED:
        failures.append(f"PROCEED unreachable: got {r.verdict.value} ({[v.reason for v in r.votes]})")

    thin = ProposedAction(summary="tidy a config file", tier="T1")  # no evidence -> Sagan CAUTION
    if council.deliberate(thin).verdict is not CouncilVerdict.PROCEED_WITH_CARE:
        failures.append("PROCEED_WITH_CARE unreachable from a single caution")

    harmful = ProposedAction(
        summary="quietly delete the client records for everyone",
        tier="T4", reaches_reality=True, reversible=False,
        affects_people=("a person downstream",),
    )
    hr = council.deliberate(harmful)
    if hr.verdict is not CouncilVerdict.REFRAIN:
        failures.append(f"REFRAIN unreachable: got {hr.verdict.value}")
    if not hr.conscience_hold:
        failures.append("conscience hold did not fire on a deceptive irreversible action")

    # The hold cannot be outvoted down to PROCEED: a Rogers OBJECT alone, with
    # three BLESS votes around it, still cannot read better than CARE.
    only_rogers = MoralCompassCouncil(guardians=[ROGERS])
    lone = ProposedAction(
        summary="quietly revoke access for a vulnerable student",
        tier="T1", evidence="a ticket", reversible=True,
    )
    lr = only_rogers.deliberate(lone)
    if lr.verdict is CouncilVerdict.PROCEED or not lr.conscience_hold:
        failures.append("a lone Rogers OBJECT read as PROCEED -- the hold is dead")

    # A gated action is never blessed even when nobody objects.
    gated = ProposedAction(
        summary="deploy the release",
        tier="T3", reaches_reality=True, evidence="the run log",
        affects_people=("an operator",),
    )
    if council.deliberate(gated).verdict is CouncilVerdict.PROCEED:
        failures.append("the council blessed a gated reaches-reality action")

    print("councils.guardians selftest:")
    for f in failures:
        print(f"  FAIL: {f}")
    print(f"  {'PASS' if not failures else 'FAIL'} ({len(failures)} failure(s))")
    return 1 if failures else 0


def _main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="the values council -- four guardian lenses")
    ap.add_argument("--selftest", action="store_true",
                    help="prove every verdict path and the conscience hold can fire")
    ap.add_argument("--summary", help="deliberate over this proposal summary")
    ap.add_argument("--tier", default="T0")
    ap.add_argument("--reaches-reality", action="store_true")
    ap.add_argument("--evidence", default="")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if not args.summary:
        ap.error("--summary is required (or use --selftest)")
    reading = MoralCompassCouncil().deliberate(
        ProposedAction(summary=args.summary, tier=args.tier,
                       reaches_reality=args.reaches_reality, evidence=args.evidence)
    )
    print(reading.render())
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
