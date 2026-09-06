"""The four verbs of learning from something outside, and the gate on each.

PURPOSE
    A node is expected to learn from systems it did not write. This module is
    the graded ladder for that, and the hard type boundary underneath it.

      ``INSPIRATION``  read it, study it, keep NO artifact, change nothing.
                       Gate: NONE. Always free. Reading somebody else's
                       ecosystem and understanding it costs nothing and risks
                       nothing.
      ``ADOPTION``     KEEP a pattern -- translated into this framework's own
                       vocabulary, in a commit, with an author who can be
                       asked why. Gate: a stated confidence, plus a PROPOSAL
                       to the approval queue whenever the target is governing
                       infrastructure. Never a self-edit by the thing doing
                       the adopting.
      ``INTEGRATION``  the adopted pattern is wired into a real code path and
                       exercised. Gate: test-first, adversarial verification,
                       and -- when it touches the declared core surface --
                       values-council review.
      ``CAPTURE``      becoming the external thing: adopting its identity,
                       its goals, its refusals. Gate: NONE, because it is
                       REFUSED. There is no confidence, no approval and no
                       review that permits it.

    A NOTE ON THE WORD, because it is a real collision and hiding it would be
    worse than carrying it. The reference implementation's grok vocabulary used
    "Assimilation" for the identity-loss failure mode -- *"Assimilation: you
    become them (identity loss). Integration: you absorb what is useful while
    remaining yourself (identity preserved)"* -- while the same estate's
    standing directive used "absorb/assimilate" for the ecosystem-mining
    posture it ENCOURAGES. One word, two opposite meanings, one of them
    forbidden. One canonical term per concept: the forbidden mode is
    :attr:`Verb.CAPTURE` here, ``absorb`` is the encouraged posture, and
    ``"assimilation"`` resolves through :data:`ALIASES` to CAPTURE with the
    collision recorded on the decision rather than silently resolved.

    THE HARD TYPE BOUNDARY, which is not a policy and not a threshold.
    A request may carry a **descriptor** -- enumerable facts about the other
    system: booleans, integers, enum values, a capability list. A request may
    NOT carry an **artifact** -- a skill file, a prompt template, a tool or
    agent definition, a rule file, a server definition, a script, an
    executable. Every verb refuses an artifact, INCLUDING inspiration, because
    the boundary is about what enters the machine and not about what the node
    intends to do with it.

    Why it is a type boundary and not a review step: an artifact of these kinds
    becomes INSTRUCTIONS IN A LOOP BEFORE ANY GATE HAS AN OPINION. It is not a
    library the node calls; it is text that enters the reasoning context at
    load time. The measured precedent is a marketplace that permitted
    third-party publishing of artifacts that loaded into an agent's context at
    session start -- 1,184 malicious packages across 12 publisher accounts, one
    uploader responsible for 677. The fence is on EXECUTING someone else's
    artifact, never on studying it: reading that ecosystem and porting an idea
    BY HAND, in a commit, is exactly what INSPIRATION and ADOPTION are for.
    "Downloaded" is not a provenance.

WRITE MODEL
    NONE. This module is a pure decision function: a request in, a decision
    out. It reads nothing, writes nothing, fetches nothing, and executes
    nothing. Persisting a decision is the caller's act into a store that
    declares its own model -- the approval queue for a proposal (per-item
    files), the core-review ledger for a review (append-only JSONL).

BLIND SPOTS -- stated so a PERMIT is not read as a safety claim
    * ``kind`` is DECLARED by the caller. This module cannot look at a payload
      and tell a descriptor from an artifact; it refuses an undeclared kind
      rather than guessing, which converts a lie into a lie somebody made
      rather than an omission nobody noticed.
    * A PERMIT says the verb is permissible at this boundary. It says nothing
      about whether the pattern is good, whether the confidence is honest, or
      whether the reviewer will agree. It is one gate of several, and the ones
      after it are named in the decision.
    * ``confidence`` is a number the caller supplies. Nothing here measures it.
    * The forbidden-kind list is a NAMED list. A novel artifact shape nobody
      has named is not on it -- which is why an UNDECLARED kind halts rather
      than falling through to permitted. The list is a floor; the reviewer is
      the ceiling.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Tuple

__all__ = [
    "Verb",
    "ALIASES",
    "PAYLOAD_KINDS",
    "DESCRIPTOR_KINDS",
    "ARTIFACT_KINDS",
    "VERDICTS",
    "DEFAULT_ADOPTION_CONFIDENCE",
    "Request",
    "Decision",
    "AssimilationError",
    "evaluate",
    "selftest",
    "main",
]


class Verb(Enum):
    """The graded ladder. Ordered: each rung keeps more than the one before."""

    INSPIRATION = "inspiration"
    ADOPTION = "adoption"
    INTEGRATION = "integration"
    #: The forbidden mode. Named CAPTURE rather than "assimilation" so one
    #: concept carries one term; see the module docstring for the collision.
    CAPTURE = "capture"

    @property
    def keeps_artifact(self) -> bool:
        """Whether this verb keeps anything at all. Inspiration keeps nothing."""
        return self is not Verb.INSPIRATION

    @property
    def gate(self) -> str:
        return _GATES[self]


#: Words a caller may plausibly send, mapped to the canonical verb. The
#: ``assimilation`` row is the collision: in the source vocabulary it named the
#: FORBIDDEN identity-loss mode, so it resolves to CAPTURE and the decision
#: records that it was resolved, rather than the caller discovering later that
#: their encouraging-sounding word meant the one refusal.
ALIASES: Dict[str, Verb] = {
    "inspiration": Verb.INSPIRATION,
    "inspire": Verb.INSPIRATION,
    "study": Verb.INSPIRATION,
    "read": Verb.INSPIRATION,
    "absorb": Verb.INSPIRATION,
    "adoption": Verb.ADOPTION,
    "adopt": Verb.ADOPTION,
    "integration": Verb.INTEGRATION,
    "integrate": Verb.INTEGRATION,
    "capture": Verb.CAPTURE,
    "assimilation": Verb.CAPTURE,
    "assimilate": Verb.CAPTURE,
}

_GATES: Dict[Verb, str] = {
    Verb.INSPIRATION:
        "none -- reading and studying are always free, and keep no artifact",
    Verb.ADOPTION:
        "a stated confidence at or above the threshold, PLUS a proposal to the "
        "approval queue when the target is governing infrastructure. Never a "
        "self-edit",
    Verb.INTEGRATION:
        "test-first, adversarially verified, and values-council review when it "
        "touches the declared core surface",
    Verb.CAPTURE:
        "none -- REFUSED. Becoming the external thing is identity loss, and no "
        "confidence, approval or review permits it",
}

#: What a request may carry. Closed vocabulary; an undeclared kind HALTS.
DESCRIPTOR_KINDS: Tuple[str, ...] = (
    "descriptor",        # enumerable facts: booleans, integers, enum values
    "capability_list",   # what the other system can do, as names
    "measurement",       # a number somebody measured
    "observation",       # a written note about the other system
    "none",              # nothing is carried at all
)

#: What a request may NEVER carry, at any verb. These become instructions in a
#: loop before any gate has an opinion.
ARTIFACT_KINDS: Tuple[str, ...] = (
    "skill",
    "prompt_template",
    "tool_definition",
    "agent_card",
    "rule_file",
    "mcp_server_definition",
    "script",
    "executable",
    "binary",
    "notebook",
    "plugin",
)

PAYLOAD_KINDS: Tuple[str, ...] = DESCRIPTOR_KINDS + ARTIFACT_KINDS

VERDICTS: Tuple[str, ...] = ("PERMIT", "PROPOSE", "REVIEW", "REFUSE")

#: The floor a stated confidence must reach before a pattern is kept. A number
#: the caller supplies; nothing here measures it.
DEFAULT_ADOPTION_CONFIDENCE = 3.0


class AssimilationError(RuntimeError):
    """A HALT: the request could not be understood, so it was not decided."""

    def __init__(self, reason: str, remedy: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.remedy = remedy


@dataclass(frozen=True)
class Request:
    """What a caller is asking to do with something outside this node."""

    verb: str
    source: str
    payload_kind: str
    summary: str = ""
    confidence: Optional[float] = None
    #: True when the thing being changed governs the node itself -- rules,
    #: gates, councils, the imprint. Adoption there is a PROPOSAL, never a
    #: self-edit.
    governing_infrastructure: bool = False
    #: True when the change lands on the declared core-mechanic surface.
    core_surface: bool = False


@dataclass
class Decision:
    """The answer, with the reason and the gates still owed."""

    verb: Verb
    verdict: str
    reason: str
    gate: str
    owed: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def permitted(self) -> bool:
        return self.verdict != "REFUSE"

    def to_row(self) -> Dict[str, Any]:
        return {"verb": self.verb.value, "verdict": self.verdict,
                "reason": self.reason, "gate": self.gate,
                "owed": list(self.owed), "notes": list(self.notes)}


def _resolve_verb(word: str, notes: List[str]) -> Verb:
    key = str(word or "").strip().lower()
    if key not in ALIASES:
        raise AssimilationError(
            f"unknown verb {word!r}",
            remedy="one of " + ", ".join(v.value for v in Verb)
                   + " (aliases: " + ", ".join(sorted(ALIASES)) + ")")
    verb = ALIASES[key]
    if key in ("assimilation", "assimilate") and verb is Verb.CAPTURE:
        notes.append(
            "the word 'assimilation' names the FORBIDDEN identity-loss mode in "
            "this vocabulary and resolves to CAPTURE. If the encouraged "
            "ecosystem-mining posture was meant, the word is 'absorb' and the "
            "verb is inspiration or adoption")
    return verb


def evaluate(request: Request,
             *,
             adoption_confidence: float = DEFAULT_ADOPTION_CONFIDENCE
             ) -> Decision:
    """Decide one request. The artifact boundary is checked FIRST, always.

    Order matters and is not an implementation detail: the type boundary is
    evaluated before the verb, so no verb -- not even inspiration -- can carry
    an executable artifact past it.
    """
    notes: List[str] = []
    verb = _resolve_verb(request.verb, notes)

    kind = str(request.payload_kind or "").strip()
    if kind not in PAYLOAD_KINDS:
        raise AssimilationError(
            f"undeclared payload kind {request.payload_kind!r}",
            remedy="declare one of " + ", ".join(PAYLOAD_KINDS)
                   + ". An undeclared kind is a hard exit, never a default of "
                     "'probably harmless': a default here would let the one "
                     "shape nobody named through the one boundary that matters")

    if kind in ARTIFACT_KINDS:
        return Decision(
            verb=verb, verdict="REFUSE",
            reason=(f"the request carries an ARTIFACT ({kind}), not a "
                    "descriptor. An artifact of this kind becomes instructions "
                    "in the reasoning loop before any gate has an opinion, so "
                    "it is refused at every verb -- including inspiration"),
            gate=verb.gate,
            owed=["port the idea BY HAND, in a commit, with an author who can "
                  "be asked why -- reading the source is encouraged; running "
                  "it is not"],
            notes=notes)

    if verb is Verb.CAPTURE:
        return Decision(
            verb=verb, verdict="REFUSE",
            reason=("CAPTURE is becoming the external thing -- adopting its "
                    "identity, goals and refusals. It is the failure mode the "
                    "invariants exist to prevent, and it is refused as a GOAL, "
                    "not gated"),
            gate=verb.gate,
            owed=["use INTEGRATION: absorb what is useful while remaining "
                  "yourself"],
            notes=notes)

    if verb is Verb.INSPIRATION:
        return Decision(
            verb=verb, verdict="PERMIT",
            reason=("reading and studying keep no artifact and change nothing"),
            gate=verb.gate, notes=notes)

    if verb is Verb.ADOPTION:
        confidence = request.confidence
        if confidence is None:
            raise AssimilationError(
                "adoption with no stated confidence",
                remedy="state a confidence. There is no default: an unstated "
                       "confidence is not a low one, it is a field nobody read")
        if float(confidence) < adoption_confidence:
            return Decision(
                verb=verb, verdict="REFUSE",
                reason=(f"stated confidence {confidence} is below the "
                        f"threshold {adoption_confidence}"),
                gate=verb.gate,
                owed=["gather evidence and re-ask, or keep it at INSPIRATION"],
                notes=notes)
        if request.governing_infrastructure:
            return Decision(
                verb=verb, verdict="PROPOSE",
                reason=("the target is governing infrastructure, so adoption "
                        "is a PROPOSAL to the operator, never a self-edit -- a "
                        "thing that can rewrite its own rules has no rules"),
                gate=verb.gate,
                owed=["file it in the approval queue and stop"],
                notes=notes)
        return Decision(
            verb=verb, verdict="PERMIT",
            reason=("the pattern is kept, translated into this framework's own "
                    "vocabulary, in a commit"),
            gate=verb.gate,
            owed=["cite the source; 'downloaded' is not a provenance"],
            notes=notes)

    # INTEGRATION
    owed = ["a failing test first, then the wiring",
            "adversarial verification by a reviewer that is not the author"]
    if request.core_surface:
        owed.append("values-council review: this lands on the declared "
                    "core-mechanic surface")
        return Decision(
            verb=verb, verdict="REVIEW",
            reason=("integration onto the declared core surface is reviewed "
                    "before it lands, not after"),
            gate=verb.gate, owed=owed, notes=notes)
    return Decision(
        verb=verb, verdict="PERMIT",
        reason="the pattern is wired into a real code path and exercised",
        gate=verb.gate, owed=owed, notes=notes)


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


def selftest() -> Tuple[bool, str]:
    """Prove every verdict and every refusal can fire."""
    fired: List[str] = []
    failures: List[str] = []

    def expect(name: str, ok: bool) -> None:
        fired.append(name)
        if not ok:
            failures.append(name)

    def decide(**kwargs: Any) -> Decision:
        base: Dict[str, Any] = {"verb": "inspiration", "source": "some system",
                                "payload_kind": "descriptor"}
        base.update(kwargs)
        return evaluate(Request(**base))

    expect("inspiration-is-free", decide().verdict == "PERMIT")

    d = decide(verb="adoption", confidence=4.0)
    expect("adoption-above-threshold-permits", d.verdict == "PERMIT")

    d = decide(verb="adoption", confidence=1.0)
    expect("adoption-below-threshold-refuses", d.verdict == "REFUSE")

    d = decide(verb="adoption", confidence=4.0, governing_infrastructure=True)
    expect("adoption-of-governing-infra-proposes", d.verdict == "PROPOSE")

    d = decide(verb="integration")
    expect("integration-permits-with-owed-gates",
           d.verdict == "PERMIT" and len(d.owed) >= 2)

    d = decide(verb="integration", core_surface=True)
    expect("integration-on-core-surface-reviews", d.verdict == "REVIEW")

    d = decide(verb="capture")
    expect("capture-is-refused", d.verdict == "REFUSE")

    d = decide(verb="assimilation")
    expect("assimilation-resolves-to-capture-and-is-refused",
           d.verb is Verb.CAPTURE and d.verdict == "REFUSE")
    expect("the-vocabulary-collision-is-recorded",
           any("forbidden" in n.lower() for n in d.notes))

    # the artifact boundary, at EVERY verb
    for verb in ("inspiration", "adoption", "integration", "capture"):
        for kind in ARTIFACT_KINDS:
            d = decide(verb=verb, payload_kind=kind, confidence=9.0)
            if d.verdict != "REFUSE":
                failures.append(f"artifact-{kind}-at-{verb}-not-refused")
        fired.append(f"artifact-refused-at-{verb}")
    expect("an-executable-artifact-is-refused-even-at-inspiration",
           decide(payload_kind="executable").verdict == "REFUSE")
    expect("a-skill-file-is-refused",
           "ARTIFACT" in decide(payload_kind="skill").reason)

    # halts
    for name, kwargs in (("undeclared-payload-kind-halts",
                          {"payload_kind": "vibes"}),
                         ("unknown-verb-halts", {"verb": "borrow"}),
                         ("adoption-with-no-confidence-halts",
                          {"verb": "adoption"})):
        try:
            decide(**kwargs)
            expect(name, False)
        except AssimilationError:
            expect(name, True)

    expect("inspiration-keeps-nothing", not Verb.INSPIRATION.keeps_artifact)
    expect("every-verb-states-a-gate",
           all(bool(v.gate) for v in Verb))

    report = (f"assimilation selftest: {len(fired)} paths fired, "
              f"{len(failures)} failed"
              + (" -- " + ", ".join(failures) if failures else ""))
    return (not failures, report)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="the four verbs of learning from something outside")
    parser.add_argument("--verb", default=None,
                        help="inspiration | adoption | integration | capture")
    parser.add_argument("--source", default="")
    parser.add_argument("--payload-kind", default="none",
                        help="what the request carries; an undeclared kind halts")
    parser.add_argument("--confidence", type=float, default=None)
    parser.add_argument("--governing-infrastructure", action="store_true")
    parser.add_argument("--core-surface", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        ok, report = selftest()
        print(report)
        return 0 if ok else 1

    if not args.verb:
        print("verbs and their gates:")
        for verb in Verb:
            print(f"  {verb.value:<12} {verb.gate}")
        print("\nnever carried, at any verb: " + ", ".join(ARTIFACT_KINDS))
        return 0

    try:
        decision = evaluate(Request(
            verb=args.verb, source=args.source,
            payload_kind=args.payload_kind, confidence=args.confidence,
            governing_infrastructure=args.governing_infrastructure,
            core_surface=args.core_surface))
    except AssimilationError as exc:
        print(f"HALT: {exc.reason}\n  remedy: {exc.remedy}")
        return 1

    if args.json:
        print(json.dumps(decision.to_row(), indent=2))
    else:
        print(f"{decision.verb.value}: {decision.verdict}")
        print(f"  {decision.reason}")
        for item in decision.owed:
            print(f"  owed: {item}")
        for note in decision.notes:
            print(f"  note: {note}")
    return 0 if decision.permitted else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
