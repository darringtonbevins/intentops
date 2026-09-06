"""The presence layer: the journal, the taxonomy, the router, the operator rule.

WRITE MODEL: read-only test module. Every store it exercises is created under
pytest's ``tmp_path`` and thrown away. No live services, no network, no fixture
written anywhere in the repository.

BLIND SPOTS: these tests exercise the modules' own logic. They cannot tell you
that an adapter somebody writes later actually calls the router before speaking
-- that is the one thing this layer cannot enforce from inside itself, and it is
stated in docs/PRESENCE.md rather than pretended away here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from intentops_core.genesis import organs as organs_mod
from intentops_core.presence import channels as channels_mod
from intentops_core.presence import intents as intents_mod
from intentops_core.presence import journal as journal_mod
from intentops_core.presence import operator_rule as operator_mod
from intentops_core.presence import router as router_mod

REPO = Path(__file__).resolve().parents[1]
SHIPPED_TAXONOMY = REPO / "config" / "intent-taxonomy.template.yaml"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def taxonomy() -> intents_mod.Taxonomy:
    return intents_mod.load_taxonomy(SHIPPED_TAXONOMY)


@pytest.fixture()
def identity_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "identity-repo"
    (repo / "identity" / "operator").mkdir(parents=True)
    (repo / operator_mod.OPERATOR_RECORD_RELPATH).write_text(
        yaml.safe_dump({
            "schema": "operator/v1",
            "as_of": "2026-09-06",
            "label": None,
            "root_fingerprint": "fp-test",
            "notification_posture": None,
            "off_limits": [],
            "channel_identities": {"chan": ["operator-ref"]},
        }, sort_keys=False),
        encoding="utf-8")
    return repo


@pytest.fixture()
def binding(identity_repo: Path) -> operator_mod.OperatorBinding:
    return operator_mod.load_operator_binding(identity_repo)


def _msg(text: str, speaker: str = "operator-ref", **kw) -> channels_mod.InboundMessage:
    return channels_mod.InboundMessage("chan", speaker, text, **kw)


_PRIVATE = channels_mod.ChannelDescriptor("chan", "private", self_ref="node-self")
_SHARED = channels_mod.ChannelDescriptor("chan", "shared", self_ref="node-self",
                                         reaches_reality=True)


# ---------------------------------------------------------------------------
# every selftest must pass -- a detector that has never fired is not evidence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module", [
    channels_mod, journal_mod, intents_mod, operator_mod, router_mod,
])
def test_every_presence_module_selftest_passes(module, capsys) -> None:
    assert module.selftest() == 0
    assert "SELFTEST PASS" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# the journal is append-only, and it holds no message text
# ---------------------------------------------------------------------------


def _record(j: journal_mod.InteractionJournal, **overrides):
    base = dict(channel_id="chan", channel_class="private",
                speaker_ref="operator-ref", speaker_is_operator=True,
                intent="query.status", tier="T0", reaches_reality=False,
                decision="ALLOW", disposition="answered", handler="retrieval")
    base.update(overrides)
    return j.record(**base)


def test_the_journal_only_ever_appends(tmp_path: Path) -> None:
    j = journal_mod.InteractionJournal(tmp_path)
    _record(j, body="first")
    after_one = j.path.read_bytes()
    _record(j, body="second")
    after_two = j.path.read_bytes()

    assert after_two.startswith(after_one), (
        "an earlier line changed: the journal's whole write model is that "
        "state is a pure fold over records nothing rewrites")
    assert after_two.count(b"\n") == 2
    assert j.state().population == 2


def test_the_journal_holds_no_message_text(tmp_path: Path) -> None:
    j = journal_mod.InteractionJournal(tmp_path)
    secret = "the quarterly numbers are attached"
    _record(j, body=secret)
    raw = j.path.read_text(encoding="utf-8")

    assert secret not in raw
    entry = json.loads(raw.splitlines()[0])
    assert entry["body_chars"] == len(secret)
    assert entry["body_digest"] == journal_mod.digest_body(secret)


def test_the_journal_refuses_a_nested_detail_value(tmp_path: Path) -> None:
    j = journal_mod.InteractionJournal(tmp_path)
    with pytest.raises(ValueError, match="JSON scalars"):
        _record(j, detail={"payload": {"text": "content by the side door"}})
    assert j.state().population == 0


@pytest.mark.parametrize("field,value", [
    ("disposition", "replied"),
    ("tier", "T5"),
    ("decision", "PROBABLY"),
    ("channel_class", "semi-private"),
])
def test_the_journal_halts_on_an_undeclared_vocabulary_value(
        tmp_path: Path, field: str, value: str) -> None:
    j = journal_mod.InteractionJournal(tmp_path)
    with pytest.raises(ValueError):
        _record(j, **{field: value})


def test_a_malformed_line_stays_in_the_denominator(tmp_path: Path) -> None:
    j = journal_mod.InteractionJournal(tmp_path)
    _record(j)
    with open(j.path, "a", encoding="utf-8") as fh:
        fh.write("{ not json\n")
    state = j.state()
    assert state.population == 1 and state.parse_failures == 1, (
        "a line that could not be read must be COUNTED, not skipped in silence")


def test_the_breach_check_can_actually_fire(tmp_path: Path) -> None:
    j = journal_mod.InteractionJournal(tmp_path)
    _record(j)
    assert j.invariant_breaches() == []
    _record(j, speaker_ref="stranger", speaker_is_operator=False)
    assert len(j.invariant_breaches()) == 1, (
        "a green breach check is evidence only when a red one was reachable")


# ---------------------------------------------------------------------------
# the taxonomy: closed vocabulary, or a halt
# ---------------------------------------------------------------------------


def test_the_shipped_taxonomy_loads_and_declares_only_generic_domains(
        taxonomy: intents_mod.Taxonomy) -> None:
    assert len(taxonomy) > 0
    domains = {spec.domain for spec in taxonomy.intents.values()}
    assert domains == {"file", "code", "query", "system", "git", "data",
                       "governance", "communication", "planning"}, (
        "a domain belonging to one operator's line of work is an estate "
        "overlay, never core")
    for spec in taxonomy.intents.values():
        assert spec.tier in intents_mod.TIERS
        assert spec.handler


def test_a_missing_taxonomy_halts_but_an_empty_one_loads(tmp_path: Path) -> None:
    with pytest.raises(intents_mod.TaxonomyHalt, match="no file at"):
        intents_mod.load_taxonomy(tmp_path / "absent.yaml")

    empty = tmp_path / "empty.yaml"
    empty.write_text('schema: intent-taxonomy/v1\nas_of: "2026-09-06"\n'
                     'domains: {}\nescalation: []\n', encoding="utf-8")
    assert len(intents_mod.load_taxonomy(empty)) == 0, (
        "`domains: {}` is a true statement about a new node; a MISSING file is "
        "a field nobody read")


@pytest.mark.parametrize("mutate,expect", [
    (lambda t: t.replace("tier: T0", "tier: T9"), "outside"),
    (lambda t: t.replace("        reaches_reality: false\n", ""), "missing required"),
    (lambda t: t + "extra_key: 1\n", "undeclared key"),
    (lambda t: t.replace("reversible: true", 'reversible: "no"'), "not a boolean"),
    (lambda t: t.replace("tier: T0", "tier: T2"), "names no rollback"),
])
def test_a_load_bearing_field_halts_rather_than_defaulting(
        tmp_path: Path, mutate, expect: str) -> None:
    base = ('schema: intent-taxonomy/v1\nas_of: "2026-09-06"\n'
            'domains:\n'
            '  file:\n'
            '    description: "files"\n'
            '    handler: filesystem\n'
            '    actions:\n'
            '      read:\n'
            '        description: "read"\n'
            '        tier: T0\n'
            '        reversible: true\n'
            '        rollback: null\n'
            '        reaches_reality: false\n'
            '        match: ["read"]\n'
            'escalation: []\n')
    path = tmp_path / "t.yaml"
    path.write_text(mutate(base), encoding="utf-8")
    with pytest.raises(intents_mod.TaxonomyHalt, match=expect):
        intents_mod.load_taxonomy(path)


def test_escalation_can_only_raise(taxonomy: intents_mod.Taxonomy) -> None:
    for rule in taxonomy.escalation:
        assert rule.escalate_to != "T0"
        assert rule.matcher in intents_mod.ESCALATION_MATCHERS, (
            "a prose condition is not a matcher: no code can evaluate it")


# ---------------------------------------------------------------------------
# the operator rule: no path to ANSWER without a declared identity match
# ---------------------------------------------------------------------------


def test_the_operator_slot_missing_halts(tmp_path: Path) -> None:
    with pytest.raises(operator_mod.OperatorSlotMissing, match="identity repository"):
        operator_mod.load_operator_binding(tmp_path / "nowhere")

    repo = tmp_path / "repo"
    (repo / "identity" / "operator").mkdir(parents=True)
    with pytest.raises(operator_mod.OperatorSlotMissing, match="no operator record"):
        operator_mod.load_operator_binding(repo)

    (repo / operator_mod.OPERATOR_RECORD_RELPATH).write_text(
        'schema: operator/v1\nroot_fingerprint: ""\nchannel_identities: {}\n',
        encoding="utf-8")
    with pytest.raises(operator_mod.OperatorSlotMissing, match="root_fingerprint"):
        operator_mod.load_operator_binding(repo)


def test_the_operator_is_answered_and_nobody_else_is(
        binding: operator_mod.OperatorBinding) -> None:
    assert operator_mod.decide(binding, _SHARED, "operator-ref").verdict \
        is operator_mod.OperatorVerdict.ANSWER
    for stranger in ("someone-else", "Operator-Ref", "operator-ref ", ""):
        assert operator_mod.decide(binding, _SHARED, stranger).verdict \
            is not operator_mod.OperatorVerdict.ANSWER, (
                f"{stranger!r} reached ANSWER without a declared identity match")


def test_a_newborn_node_answers_nobody() -> None:
    birth = operator_mod.OperatorBinding("", "fp", {}, None)
    decision = operator_mod.decide(birth, _PRIVATE, "anybody-at-all")
    assert decision.verdict is operator_mod.OperatorVerdict.SURFACE_TO_OPERATOR
    assert "birth state" in decision.reason


def test_the_genesis_operator_skeleton_carries_the_channel_slot(
        tmp_path: Path) -> None:
    """The record genesis writes must be loadable by the rule that reads it."""
    repo = tmp_path / "id"
    organs_mod.create_identity_skeleton(
        repo, designation="node", node_id="n1", imprint_version="v1",
        release_root_fingerprint="rr", operator_fingerprint="fp")
    binding = operator_mod.load_operator_binding(repo)
    assert binding.channel_identities == {}, (
        "the slot exists and is EMPTY at birth -- the emptiness is what makes "
        "the node unable to be impersonated into replying")
    assert binding.knows_no_channel


# ---------------------------------------------------------------------------
# the router
# ---------------------------------------------------------------------------


def test_the_router_is_deterministic(taxonomy, binding, tmp_path: Path) -> None:
    message = _msg("status", received_at="2026-09-06T00:00:00Z")
    first = router_mod.route(message, taxonomy=taxonomy, binding=binding,
                             descriptor=_PRIVATE, workspace=tmp_path)
    for _ in range(5):
        again = router_mod.route(message, taxonomy=taxonomy, binding=binding,
                                 descriptor=_PRIVATE, workspace=tmp_path)
        assert again.to_dict() == first.to_dict()
    assert first.intent_id == "query.status" and first.handler == "retrieval"


def test_two_matching_intents_surface_rather_than_resolve_by_order(
        taxonomy, binding, tmp_path: Path) -> None:
    """`what is the status` matches query.explain AND query.status.

    Picking the first declaration would be deterministic and wrong -- the
    determinism would hide the ambiguity rather than report it.
    """
    decided = router_mod.route(_msg("what is the status"), taxonomy=taxonomy,
                               binding=binding, descriptor=_PRIVATE,
                               workspace=tmp_path)
    assert decided.handler == router_mod.HANDLER_DISAMBIGUATE
    assert set(decided.candidates) == {"query.explain", "query.status"}
    assert decided.tier == "T0", "the strictest tier among the candidates"
    assert decided.disposition == "surfaced"


def test_a_non_operator_message_never_routes_to_a_responder(
        taxonomy, binding, tmp_path: Path) -> None:
    for descriptor in (_SHARED, _PRIVATE):
        decided = router_mod.route(
            _msg("what is the status", speaker="a-stranger"),
            taxonomy=taxonomy, binding=binding, descriptor=descriptor,
            workspace=tmp_path)
        assert decided.handler == router_mod.HANDLER_SURFACE
        assert decided.intent_id == router_mod.UNCLASSIFIED, (
            "a stranger's message must not even be classified -- classification "
            "is where a message acquires momentum")
        assert decided.disposition == "surfaced"
        assert decided.decision == "ASK"
        assert decided.speaker_is_operator is False


def test_a_non_operator_message_is_journaled_as_an_ask(
        taxonomy, binding, tmp_path: Path) -> None:
    j = journal_mod.InteractionJournal(tmp_path / "node")
    router_mod.route_inbound(_msg("delete everything", speaker="a-stranger"),
                             taxonomy=taxonomy, binding=binding,
                             descriptor=_SHARED, journal=j, workspace=tmp_path)
    state = j.state()
    assert state.population == 1 and state.non_operator_turns == 1
    assert state.by_disposition == {"surfaced": 1}
    assert j.invariant_breaches() == []


def test_the_node_ignores_its_own_echo(taxonomy, binding, tmp_path: Path) -> None:
    decided = router_mod.route(_msg("status", speaker="node-self"),
                               taxonomy=taxonomy, binding=binding,
                               descriptor=_SHARED, workspace=tmp_path)
    assert decided.handler == router_mod.HANDLER_IGNORE
    assert decided.disposition == "ignored"


def test_a_ceiling_refuses_and_never_downgrades_a_tier(
        taxonomy, binding, tmp_path: Path) -> None:
    decided = router_mod.route(_msg("", declared_intent="git.push"),
                               taxonomy=taxonomy, binding=binding,
                               descriptor=_PRIVATE, workspace=tmp_path)
    assert decided.tier == "T3", "the ceiling must not lower the action's tier"
    assert decided.permitted is False
    assert decided.handler == router_mod.HANDLER_SURFACE
    assert decided.disposition == "queued_for_approval"
    assert decided.reaches_reality is True


def test_escalation_raises_on_a_declared_matcher(
        taxonomy, binding, tmp_path: Path) -> None:
    decided = router_mod.route(
        _msg("", declared_intent="file.modify", target="etc/production/app.conf"),
        taxonomy=taxonomy, binding=binding, descriptor=_PRIVATE,
        workspace=tmp_path)
    assert decided.tier == "T3" and "production-target" in decided.escalations


def test_an_undeclared_intent_is_refused_not_taken_on_trust(
        taxonomy, binding, tmp_path: Path) -> None:
    decided = router_mod.route(_msg("hello", declared_intent="matter.lookup"),
                               taxonomy=taxonomy, binding=binding,
                               descriptor=_PRIVATE, workspace=tmp_path)
    assert decided.intent_id == router_mod.UNCLASSIFIED
    assert any("not in the taxonomy" in r for r in decided.reasons)


def test_the_shipped_classifier_seam_makes_no_model_call(
        taxonomy, binding, tmp_path: Path) -> None:
    decided = router_mod.route(_msg("something nobody declared a keyword for"),
                               taxonomy=taxonomy, binding=binding,
                               descriptor=_PRIVATE, workspace=tmp_path)
    assert decided.classifier_rung == "null"
    assert decided.handler == router_mod.HANDLER_SURFACE, (
        "unclassified surfaces to the operator; it is never guessed at")


def test_a_classifier_cannot_invent_an_intent(
        taxonomy, binding, tmp_path: Path) -> None:
    class Inventor:
        rung = "test"

        def classify(self, text, candidates):
            return "file.detonate"

    decided = router_mod.route(_msg("do the thing"), taxonomy=taxonomy,
                               binding=binding, descriptor=_PRIVATE,
                               workspace=tmp_path, classifier=Inventor())
    assert decided.intent_id == router_mod.UNCLASSIFIED
    assert any("not a declared intent" in r for r in decided.reasons)


def test_the_router_refuses_a_descriptor_from_another_channel(
        taxonomy, binding, tmp_path: Path) -> None:
    other = channels_mod.InboundMessage("some-other-channel", "operator-ref", "hi")
    with pytest.raises(ValueError, match="another channel's classification"):
        router_mod.route(other, taxonomy=taxonomy, binding=binding,
                         descriptor=_PRIVATE, workspace=tmp_path)


def test_every_route_records_exactly_one_journal_line(
        taxonomy, binding, tmp_path: Path) -> None:
    j = journal_mod.InteractionJournal(tmp_path / "node")
    for message in (_msg("status"),
                    _msg("status", speaker="a-stranger"),
                    _msg("status", speaker="node-self"),
                    _msg("", declared_intent="git.push")):
        router_mod.route_inbound(message, taxonomy=taxonomy, binding=binding,
                                 descriptor=_PRIVATE, journal=j,
                                 workspace=tmp_path)
    assert j.state().population == 4, (
        "an ignored echo and a refused stranger are both recorded -- a message "
        "that leaves the population makes the numbers look better than they are")


# ---------------------------------------------------------------------------
# channels
# ---------------------------------------------------------------------------


def test_an_unknown_channel_class_reads_as_shared() -> None:
    assert channels_mod.ChannelDescriptor("c", "unknown").is_shared is True
    assert channels_mod.ChannelDescriptor("c", "private").is_shared is False
    with pytest.raises(channels_mod.UndeclaredChannelClass):
        channels_mod.ChannelDescriptor("c", "semi-private")


def test_an_unattributed_message_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="speaker_ref"):
        channels_mod.InboundMessage("c", "", "hello")


def test_the_null_channel_reaches_nobody() -> None:
    ch = channels_mod.NullChannel()
    ch.send("this never leaves the process")
    assert ch.descriptor().reaches_reality is False
    assert len(ch.sent) == 1


def test_no_presence_module_imports_a_vendor_sdk_or_a_socket() -> None:
    """Bias LEFT, checked rather than promised."""
    forbidden = ("import socket", "import requests", "import httpx",
                 "urllib.request", "import anthropic", "import openai")
    package = (REPO / "packages" / "intentops-core" / "intentops_core"
               / "presence")
    for module in sorted(package.glob("*.py")):
        text = module.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in text, f"{module.name} carries {needle!r}"


# ---------------------------------------------------------------------------
# the birth organ and its probes
# ---------------------------------------------------------------------------


def test_the_interaction_journal_is_a_birth_organ() -> None:
    organ = next(o for o in organs_mod.BIRTH_ORGANS if o.id == "presence")
    assert organ.relpath == journal_mod.JOURNAL_RELPATH
    assert organ.write_model == "append-only-jsonl"
    assert organ.consumer, "a store with no named consumer is capture, not retention"


def test_the_presence_probes_are_declared_and_their_anchors_reach_the_corpus() -> None:
    doc = yaml.safe_load(
        (REPO / "config" / "genesis-probes.yaml").read_text(encoding="utf-8"))
    corpus = "\n".join(
        (REPO / rel).read_text(encoding="utf-8")
        for rel in doc["boot_corpus"]["workspace"] if (REPO / rel).is_file())
    ids = {p["id"] for p in doc["probes"]}
    for probe_id in ("GEN-operator-rule", "GEN-presence-journal",
                     "GEN-intent-taxonomy"):
        assert probe_id in ids, f"{probe_id}: new organ, new probe, same session"
        probe = next(p for p in doc["probes"] if p["id"] == probe_id)
        assert any(marker.lower() in corpus.lower()
                   for marker in probe["expect_in_boot"]["any_of"]), (
            f"{probe_id} reads ABSENT from the boot corpus: a packaging "
            "failure, not a missing organ")
        for truth in probe["truth"]:
            target = REPO / truth["path"]
            assert target.is_file(), f"{probe_id} truth path missing: {truth['path']}"
            assert truth["contains"].lower() in target.read_text(
                encoding="utf-8").lower(), (
                f"{probe_id} truth string drifted out of {truth['path']}")
