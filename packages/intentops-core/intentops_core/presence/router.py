"""The presence router -- one front door, deterministic, tier-classified before any handler.

PURPOSE
    Every inbound message enters here and leaves as a :class:`Route`: an intent
    id, a tier, a handler NAME, whether taking it reaches outside the machine,
    and the reasons. Nothing downstream re-decides any of those, and nothing
    upstream may skip this to reach a handler directly.

    The reference implementation this generalises from had THREE overlapping
    routers and a documentation note asking people not to confuse them. One is
    the design; the note was the symptom.

THE ORDER IS THE DESIGN
    1. **The operator rule first.** Who is speaking is decided before what they
       said, because a stranger's message must not be classified into a handler
       at all -- classification is where a message acquires momentum.
    2. **Declared intent, then keywords, then the classifier seam.** Bias LEFT:
       a caller that declares an intent id is believed (and validated); text
       falls to word-boundary keyword matching declared in the taxonomy; only
       what neither answers reaches the classifier seam.
    3. **The seam ships as :class:`NullClassifier` and returns nothing.** There
       is no model call anywhere in this package. A node that wants one
       installs a classifier that satisfies the protocol, and its answer is
       still capped: a classifier can only propose a DECLARED intent id, and
       its route is marked with the rung that produced it.
    4. **Escalation raises; the ceiling refuses.** Escalation rules can only
       raise a tier. The channel ceiling never LOWERS one -- an action above
       the ceiling is refused and surfaced, because silently downgrading a tier
       is how an action walks under its own gate.

DETERMINISM
    ``route`` is a pure function of (message, taxonomy, binding, descriptor,
    workspace) and returns an identical :class:`Route` every time, including
    the ORDER of ``reasons``. Two matching intents never resolve by coin flip:
    the route becomes a disambiguation ask carrying the strictest tier of the
    candidates.

WRITE MODEL
    The router itself holds no store. :func:`route_inbound` writes exactly one
    record per message to the interaction journal (append-only JSONL under
    StoreLock) and returns the route. Journaling is inside the same call as the
    decision on purpose: two steps drift, and the one that drifts is the record.

BLIND SPOTS
    1. Keyword matching is a floor, not a classifier (see
       :mod:`intentops_core.presence.intents` blind spot 2). Its failures are
       biased toward the operator's inbox, never toward a handler.
    2. ``target`` is whatever the caller supplies. The escalation rules read
       it, so a caller that never populates it disables the target-shaped
       escalations without anything reporting it. ``Route.reasons`` says when
       no target was given.
    3. The router returns a handler NAME. It resolves nothing, imports nothing
       and calls nothing -- so it also cannot tell you the handler exists.
    4. Nothing here is a gate. A route is an intention; the gate
       (:mod:`intentops_core.gate`) is what refuses an action, and a T3 route
       is surfaced for approval rather than executed by anything in this
       package.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple

from .channels import ChannelDescriptor, InboundMessage
from .intents import IntentSpec, Taxonomy, TaxonomyHalt, TIERS, tier_max
from .journal import InteractionJournal
from .operator_rule import OperatorBinding, OperatorDecision, OperatorVerdict, decide

__all__ = [
    "Classifier",
    "NullClassifier",
    "Route",
    "UNCLASSIFIED",
    "HANDLER_SURFACE",
    "HANDLER_IGNORE",
    "HANDLER_DISAMBIGUATE",
    "route",
    "route_inbound",
    "selftest",
    "main",
]

#: The intent id used when nothing classified the message. It is a VALUE, not
#: an absence: an unclassified message stays in the journal's population and is
#: counted, rather than leaving it and making the numbers look better.
UNCLASSIFIED = "unclassified"

HANDLER_SURFACE = "surface_to_operator"
HANDLER_IGNORE = "ignore"
HANDLER_DISAMBIGUATE = "disambiguate"

_TIER_ORDER: Dict[str, int] = {t: i for i, t in enumerate(TIERS)}

#: What a channel's classification permits without the operator ruling first.
#: A PRIVATE channel whose speaker is the verified operator may reach T2 (the
#: delegated band); everything else stops at T0. This is a CEILING on
#: autonomous action, never a downgrade of an action's tier.
_CEILING_BY_CLASS: Dict[str, str] = {
    "private": "T2",
    "shared": "T0",
    "unknown": "T0",
}


class Classifier(Protocol):
    """The declared model seam.

    ``classify`` returns a DECLARED intent id or None. It may not invent an id,
    may not return a tier, and may not decide anything: the router validates
    whatever comes back against the taxonomy and applies the same escalation
    and ceiling it applies to a keyword match.
    """

    rung: str

    def classify(self, text: str, candidates: Sequence[str]) -> Optional[str]:
        ...


class NullClassifier:
    """The shipped classifier: it classifies nothing, and says so.

    This is what "bias LEFT" looks like in code. The seed makes no model call,
    so the seam is filled by something that returns None and is honest about
    it, rather than by a stub that guesses.
    """

    rung = "null"

    def classify(self, text: str, candidates: Sequence[str]) -> Optional[str]:
        return None


@dataclass(frozen=True)
class Route:
    """What the router decided. Every field is stated, none inferred later."""

    intent_id: str
    tier: str
    handler: str
    reaches_reality: bool
    permitted: bool
    operator_verdict: str
    speaker_is_operator: bool
    classifier_rung: str
    channel_id: str
    channel_class: str
    ceiling: str
    reasons: Tuple[str, ...] = ()
    escalations: Tuple[str, ...] = ()
    candidates: Tuple[str, ...] = ()

    @property
    def decision(self) -> str:
        """The gate-vocabulary word for this route.

        ALLOW only when the operator asked, the intent is classified, and its
        tier is inside the channel ceiling. Everything else ASKs -- there is no
        BLOCK here, because refusing to answer is not the same as refusing the
        action, and the operator still gets to rule.
        """
        if self.handler == HANDLER_IGNORE:
            return "ALLOW"
        if self.permitted and self.intent_id != UNCLASSIFIED \
                and self.handler not in (HANDLER_SURFACE, HANDLER_DISAMBIGUATE):
            return "ALLOW"
        return "ASK"

    @property
    def disposition(self) -> str:
        """What the caller will DO about it, in the journal's vocabulary."""
        if self.handler == HANDLER_IGNORE:
            return "ignored"
        if not self.permitted:
            return "queued_for_approval"
        if self.handler in (HANDLER_SURFACE, HANDLER_DISAMBIGUATE):
            return "surfaced"
        return "answered"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "tier": self.tier,
            "handler": self.handler,
            "reaches_reality": self.reaches_reality,
            "permitted": self.permitted,
            "operator_verdict": self.operator_verdict,
            "speaker_is_operator": self.speaker_is_operator,
            "classifier_rung": self.classifier_rung,
            "channel_id": self.channel_id,
            "channel_class": self.channel_class,
            "ceiling": self.ceiling,
            "decision": self.decision,
            "disposition": self.disposition,
            "reasons": list(self.reasons),
            "escalations": list(self.escalations),
            "candidates": list(self.candidates),
        }


def _inside_workspace(target: str, workspace: Optional[Path]) -> bool:
    """Is ``target`` inside the node's workspace?

    An empty target is INSIDE by convention -- there is nothing to be outside
    of -- and the caller is told in ``reasons`` that no target was supplied, so
    the convention is never mistaken for a check that passed.
    """
    if not target or workspace is None:
        return True
    try:
        resolved = Path(target)
        if not resolved.is_absolute():
            resolved = Path(workspace) / resolved
        resolved.resolve().relative_to(Path(workspace).resolve())
        return True
    except (ValueError, OSError):
        return False


def route(
    message: InboundMessage,
    *,
    taxonomy: Taxonomy,
    binding: OperatorBinding,
    descriptor: ChannelDescriptor,
    workspace: Optional[Path] = None,
    classifier: Optional[Classifier] = None,
) -> Route:
    """Classify one message into a :class:`Route`. Pure and deterministic."""
    if message.channel_id != descriptor.channel_id:
        raise ValueError(
            f"router: message is from {message.channel_id!r} but the descriptor "
            f"describes {descriptor.channel_id!r}. Routing a message with "
            "another channel's classification is how a shared surface gets "
            "treated as a private one.")

    classifier = classifier or NullClassifier()
    reasons: List[str] = []
    ceiling = _CEILING_BY_CLASS[descriptor.channel_class]

    # 1. who is speaking, before what they said
    op: OperatorDecision = decide(binding, descriptor, message.speaker_ref)
    reasons.append(f"operator rule: {op.verdict.value} -- {op.reason}")

    if op.verdict is OperatorVerdict.IGNORE:
        return Route(
            intent_id=UNCLASSIFIED, tier="T0", handler=HANDLER_IGNORE,
            reaches_reality=False, permitted=True,
            operator_verdict=op.verdict.value, speaker_is_operator=False,
            classifier_rung="none", channel_id=descriptor.channel_id,
            channel_class=descriptor.channel_class, ceiling=ceiling,
            reasons=tuple(reasons))

    if op.verdict is not OperatorVerdict.ANSWER:
        # A non-operator message is NEVER classified into a handler. It becomes
        # an ask. Classification is where a message acquires momentum, so the
        # cheapest place to stop it is before it has any.
        reasons.append(
            "not classified: a message from anyone but the bound operator is "
            "surfaced as an ask, never routed to a responder")
        return Route(
            intent_id=UNCLASSIFIED, tier=ceiling, handler=HANDLER_SURFACE,
            reaches_reality=False, permitted=True,
            operator_verdict=op.verdict.value, speaker_is_operator=False,
            classifier_rung="none", channel_id=descriptor.channel_id,
            channel_class=descriptor.channel_class, ceiling=ceiling,
            reasons=tuple(reasons))

    # 2. classification: declared, then keywords, then the seam
    spec: Optional[IntentSpec] = None
    rung = "none"
    candidates: Tuple[IntentSpec, ...] = ()

    if message.declared_intent:
        spec = taxonomy.get(message.declared_intent)
        if spec is None:
            reasons.append(
                f"declared intent {message.declared_intent!r} is not in the "
                "taxonomy; an undeclared id is refused, never accepted on the "
                "caller's word")
        else:
            rung = "declared"
            reasons.append(f"intent declared by the caller: {spec.intent_id}")

    if spec is None:
        candidates = taxonomy.candidates(message.text)
        if len(candidates) == 1:
            spec = candidates[0]
            rung = "keyword"
            reasons.append(f"keyword match: {spec.intent_id}")
        elif len(candidates) > 1:
            strictest = tier_max(*(c.tier for c in candidates))
            reasons.append(
                "ambiguous: " + ", ".join(c.intent_id for c in candidates)
                + " all matched, so the message is surfaced for the operator to "
                  "disambiguate rather than resolved by order of declaration")
            return Route(
                intent_id=UNCLASSIFIED, tier=strictest,
                handler=HANDLER_DISAMBIGUATE,
                reaches_reality=any(c.reaches_reality for c in candidates),
                permitted=True, operator_verdict=op.verdict.value,
                speaker_is_operator=True, classifier_rung="keyword",
                channel_id=descriptor.channel_id,
                channel_class=descriptor.channel_class, ceiling=ceiling,
                reasons=tuple(reasons),
                candidates=tuple(c.intent_id for c in candidates))

    if spec is None:
        proposed = classifier.classify(message.text, tuple(sorted(taxonomy.intents)))
        if proposed is not None:
            spec = taxonomy.get(proposed)
            if spec is None:
                reasons.append(
                    f"classifier proposed {proposed!r}, which is not a declared "
                    "intent; refused")
            else:
                rung = getattr(classifier, "rung", "classifier")
                reasons.append(f"classifier ({rung}) proposed {spec.intent_id}")
        else:
            reasons.append(
                f"classifier ({getattr(classifier, 'rung', 'unknown')}) "
                "classified nothing")

    if spec is None:
        reasons.append(
            "unclassified: surfaced to the operator rather than guessed at")
        return Route(
            intent_id=UNCLASSIFIED, tier=ceiling, handler=HANDLER_SURFACE,
            reaches_reality=False, permitted=True,
            operator_verdict=op.verdict.value, speaker_is_operator=True,
            classifier_rung=getattr(classifier, "rung", "unknown"),
            channel_id=descriptor.channel_id,
            channel_class=descriptor.channel_class, ceiling=ceiling,
            reasons=tuple(reasons))

    # 3. escalation -- raise only
    if not message.target:
        reasons.append(
            "no target supplied, so target-shaped escalation rules could not "
            "fire")
    inside = _inside_workspace(message.target, workspace)
    tier = spec.tier
    fired: List[str] = []
    for rule in taxonomy.escalation:
        if rule.applies(intent=spec, target=message.target,
                        inside_workspace=inside):
            raised = tier_max(tier, rule.escalate_to)
            if raised != tier:
                fired.append(rule.rule_id)
                reasons.append(
                    f"escalation {rule.rule_id}: {tier} -> {raised} ({rule.reason})")
                tier = raised

    # 4. the ceiling refuses; it never downgrades
    reaches = spec.reaches_reality or (
        descriptor.reaches_reality and spec.domain == "communication")
    permitted = _TIER_ORDER[tier] <= _TIER_ORDER[ceiling]
    handler = spec.handler
    if not permitted:
        handler = HANDLER_SURFACE
        reasons.append(
            f"{tier} exceeds this channel's {ceiling} ceiling, so the route is "
            "queued for the operator rather than taken. A ceiling refuses; it "
            "never lowers a tier to fit.")

    return Route(
        intent_id=spec.intent_id, tier=tier, handler=handler,
        reaches_reality=reaches, permitted=permitted,
        operator_verdict=op.verdict.value, speaker_is_operator=True,
        classifier_rung=rung, channel_id=descriptor.channel_id,
        channel_class=descriptor.channel_class, ceiling=ceiling,
        reasons=tuple(reasons), escalations=tuple(fired),
        candidates=tuple(c.intent_id for c in candidates))


def route_inbound(
    message: InboundMessage,
    *,
    taxonomy: Taxonomy,
    binding: OperatorBinding,
    descriptor: ChannelDescriptor,
    journal: InteractionJournal,
    workspace: Optional[Path] = None,
    classifier: Optional[Classifier] = None,
) -> Route:
    """Route one message AND journal it. The front door adapters call.

    One call, one record. The journal write happens whatever the route -- an
    ignored echo and a refused stranger are both recorded, because a message
    that leaves the population makes the numbers look better than they are.
    """
    decided = route(message, taxonomy=taxonomy, binding=binding,
                    descriptor=descriptor, workspace=workspace,
                    classifier=classifier)
    journal.record(
        channel_id=descriptor.channel_id,
        channel_class=descriptor.channel_class,
        speaker_ref=message.speaker_ref,
        speaker_is_operator=decided.speaker_is_operator,
        intent=decided.intent_id,
        tier=decided.tier,
        reaches_reality=decided.reaches_reality,
        decision=decided.decision,
        disposition=decided.disposition,
        handler=decided.handler,
        reasons=decided.reasons,
        body=message.text,
        detail={"classifier_rung": decided.classifier_rung,
                "ceiling": decided.ceiling,
                "escalations": ",".join(decided.escalations)},
        ts=message.received_at,
    )
    return decided


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


def _selftest_taxonomy(tmp: Path) -> Taxonomy:
    from .intents import load_taxonomy

    text = """\
schema: intent-taxonomy/v1
as_of: "2026-09-06"
domains:
  query:
    description: "questions"
    handler: retrieval
    actions:
      status:
        description: "report state"
        tier: T0
        reversible: true
        rollback: null
        reaches_reality: false
        match: ["status"]
  file:
    description: "files"
    handler: filesystem
    actions:
      delete:
        description: "delete a file"
        tier: T1
        reversible: true
        rollback: "restore from version control"
        reaches_reality: false
        match: ["delete"]
      modify:
        description: "modify a file"
        tier: T1
        reversible: true
        rollback: "restore from version control"
        reaches_reality: false
        match: ["delete", "amend"]
escalation:
  - id: destructive-verb
    when:
      action_in: ["delete"]
    escalate_to: T4
    reason: "loss is the failure mode"
"""
    path = tmp / "tax.yaml"
    path.write_text(text, encoding="utf-8")
    return load_taxonomy(path)


def selftest() -> int:
    """Every branch of the router must fire, and the invariant must refuse."""
    import tempfile

    failures: List[str] = []

    def expect(name: str, ok: bool) -> None:
        if not ok:
            failures.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        tax = _selftest_taxonomy(root)
        binding = OperatorBinding("", "fp", {"chan": ("op",)}, None)
        shared = ChannelDescriptor("chan", "shared", self_ref="me")
        private = ChannelDescriptor("chan", "private", self_ref="me")

        def msg(text: str, speaker: str = "op", **kw: Any) -> InboundMessage:
            return InboundMessage("chan", speaker, text, **kw)

        r = route(msg("status please"), taxonomy=tax, binding=binding,
                  descriptor=private, workspace=root)
        expect("operator-keyword-route",
               r.intent_id == "query.status" and r.handler == "retrieval"
               and r.tier == "T0" and r.permitted and r.decision == "ALLOW")

        r2 = route(msg("status please"), taxonomy=tax, binding=binding,
                   descriptor=private, workspace=root)
        expect("deterministic", r.to_dict() == r2.to_dict())

        stranger = route(msg("status please", speaker="somebody"), taxonomy=tax,
                         binding=binding, descriptor=shared, workspace=root)
        expect("stranger-never-reaches-a-responder",
               stranger.handler == HANDLER_SURFACE
               and stranger.intent_id == UNCLASSIFIED
               and stranger.disposition == "surfaced"
               and stranger.speaker_is_operator is False)

        echo = route(msg("status", speaker="me"), taxonomy=tax, binding=binding,
                     descriptor=shared, workspace=root)
        expect("echo-ignored", echo.handler == HANDLER_IGNORE
               and echo.disposition == "ignored")

        ambiguous = route(msg("delete it"), taxonomy=tax, binding=binding,
                          descriptor=private, workspace=root)
        expect("ambiguity-surfaces",
               ambiguous.handler == HANDLER_DISAMBIGUATE
               and len(ambiguous.candidates) == 2)

        escalated = route(msg("", declared_intent="file.delete"), taxonomy=tax,
                          binding=binding, descriptor=private, workspace=root)
        expect("escalation-raises-only",
               escalated.tier == "T4" and "destructive-verb" in escalated.escalations)
        expect("ceiling-refuses-rather-than-downgrades",
               escalated.permitted is False
               and escalated.tier == "T4"
               and escalated.handler == HANDLER_SURFACE
               and escalated.disposition == "queued_for_approval")

        shared_ceiling = route(msg("", declared_intent="file.modify"),
                               taxonomy=tax, binding=binding,
                               descriptor=shared, workspace=root)
        expect("shared-channel-ceiling-is-T0",
               shared_ceiling.ceiling == "T0" and shared_ceiling.permitted is False)

        bad_declared = route(msg("nothing here", declared_intent="file.explode"),
                             taxonomy=tax, binding=binding, descriptor=private,
                             workspace=root)
        expect("undeclared-intent-refused",
               bad_declared.intent_id == UNCLASSIFIED
               and any("not in the taxonomy" in x for x in bad_declared.reasons))

        unclassified = route(msg("hello there"), taxonomy=tax, binding=binding,
                             descriptor=private, workspace=root)
        expect("unclassified-surfaces",
               unclassified.handler == HANDLER_SURFACE
               and unclassified.classifier_rung == "null")

        class Liar:
            rung = "test"

            def classify(self, text: str, candidates: Sequence[str]) -> Optional[str]:
                return "file.explode"

        lied = route(msg("hello there"), taxonomy=tax, binding=binding,
                     descriptor=private, workspace=root, classifier=Liar())
        expect("classifier-cannot-invent-an-intent",
               lied.intent_id == UNCLASSIFIED
               and any("not a declared intent" in x for x in lied.reasons))

        try:
            route(InboundMessage("other", "op", "status"), taxonomy=tax,
                  binding=binding, descriptor=private, workspace=root)
            expect("channel-mismatch-refused", False)
        except ValueError:
            expect("channel-mismatch-refused", True)

        journal = InteractionJournal(root / "node")
        route_inbound(msg("status"), taxonomy=tax, binding=binding,
                      descriptor=private, journal=journal, workspace=root)
        route_inbound(msg("status", speaker="stranger"), taxonomy=tax,
                      binding=binding, descriptor=shared, journal=journal,
                      workspace=root)
        state = journal.state()
        expect("every-message-is-journaled", state.population == 2)
        expect("no-stranger-was-answered", journal.invariant_breaches() == [])
        expect("journal-holds-no-text",
               "status" not in journal.path.read_text(encoding="utf-8").replace(
                   '"query.status"', "").replace("query.status", ""))

    total = 16
    if failures:
        print(f"SELFTEST FAIL -- {len(failures)} of {total}: "
              + ", ".join(failures))
        return 1
    print(f"SELFTEST PASS -- {total}/{total} paths behaved as declared")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="the presence router")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--taxonomy", default="config/intent-taxonomy.template.yaml")
    parser.add_argument("--identity-repo", default=None)
    parser.add_argument("--channel", default="cli")
    parser.add_argument("--channel-class", default="unknown")
    parser.add_argument("--speaker", default="")
    parser.add_argument("--text", default="")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.selftest:
        return selftest()

    from .intents import load_taxonomy
    from .operator_rule import OperatorSlotMissing, load_operator_binding

    try:
        taxonomy = load_taxonomy(args.taxonomy)
    except TaxonomyHalt as exc:
        print(f"HALT: {exc}")
        return 1
    if not args.identity_repo:
        print("HALT: --identity-repo is required. There is no default operator, "
              "and a router with no operator record cannot decide who it may "
              "answer.")
        return 1
    try:
        binding = load_operator_binding(args.identity_repo)
    except OperatorSlotMissing as exc:
        print(f"HALT: {exc}")
        return 1
    if not args.speaker or not args.text:
        print(f"taxonomy: {len(taxonomy)} intents; operator root "
              f"{binding.root_fingerprint}")
        return 0
    decided = route(
        InboundMessage(args.channel, args.speaker, args.text),
        taxonomy=taxonomy, binding=binding,
        descriptor=ChannelDescriptor(args.channel, args.channel_class))
    print(f"{decided.decision} [{decided.tier}] intent={decided.intent_id} "
          f"handler={decided.handler} "
          f"{'reaches-reality' if decided.reaches_reality else 'local'}")
    for reason in decided.reasons:
        print(f"  - {reason}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
