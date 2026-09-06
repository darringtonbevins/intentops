"""Core lift A -- the refusals, the arithmetic, and the classifier.

These tests exist to prove that the parts which are supposed to REFUSE can
actually refuse. A detector that has never fired is indistinguishable from a
broken one, so every guard here is exercised in the direction where it says no.

No live services, no network, no fixtures outside a tmp_path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from intentops_core.councils.guardians import (
    CouncilVerdict,
    MoralCompassCouncil,
    ProposedAction,
    ROGERS,
    Stance,
)
from intentops_core.councils.posture import (
    APPROVED,
    REJECTED,
    ActuationItem,
    posture_council_vote,
)
from intentops_core.estate_classifier import (
    EstateMap,
    EstateMapError,
    load_estate_map,
)
from intentops_core.gate.classify import RealityIndicators, classify_reaches_reality, verdict_for
from intentops_core.gate.verdict import Decision, Verdict, merge
from intentops_core.priority import UIES, ScoredItem, cost_of_delay, job_size, priority, rank
from intentops_core.store_guard import StoreLock, lock_for, locked_rmw
from intentops_core.wisdom.ordering import (
    Ordering,
    OrderingState,
    OrderingStore,
    ValidationError,
    posture,
)

# ---------------------------------------------------------------------------
# wisdom.ordering -- both refusals, predicate-gated serving, on_match default
# ---------------------------------------------------------------------------

_RULING = dict(
    statement="A ruling",
    tension="speed vs reversibility",
    ordering="reversibility over speed",
    conditions="when the act reaches another party",
    authority="the operator",
)


def test_ordering_refuses_a_preference_no_tension():
    with pytest.raises(ValidationError, match="tension"):
        Ordering.create(**{**_RULING, "tension": "  "})


def test_ordering_refuses_a_slogan_no_conditions():
    with pytest.raises(ValidationError, match="conditions"):
        Ordering.create(**{**_RULING, "conditions": ""})


def test_ordering_refuses_a_ruling_with_no_authority():
    with pytest.raises(ValidationError, match="authority"):
        Ordering.create(**{**_RULING, "authority": ""})


def test_ordering_does_not_refuse_a_missing_reopens_when():
    """Never fabricate a way back. It is surfaced as debt, not invented."""
    o = Ordering.create(**_RULING)
    assert o.reopens_when == ""
    assert "reopens_when" in o.incomplete()


def test_on_match_defaults_to_escalate_and_rejects_unknown_values():
    assert Ordering.create(**_RULING).on_match == "escalate"
    with pytest.raises(ValidationError, match="on_match"):
        Ordering.create(**_RULING, on_match="allow")


def test_prose_only_ruling_never_serves_itself():
    prose = Ordering.create(**_RULING)
    state = OrderingState(records={prose.id: prose})
    assert prose.matches({"estate": "served"}) is False
    assert state.governing({"estate": "served"}) == []
    assert state.unruled({"estate": "served"}) is True


def test_compiled_predicate_serves_only_its_own_facts():
    compiled = Ordering.create(**_RULING, predicate={"estate": "served", "tier": "T3"})
    assert compiled.matches({"estate": "served", "tier": "T3", "extra": 1})
    assert not compiled.matches({"estate": "served"})          # missing key
    assert not compiled.matches({"estate": "internal", "tier": "T3"})


def test_posture_blocks_on_conflicting_rulings():
    a = Ordering.create(**{**_RULING, "statement": "A"}, predicate={"estate": "served"},
                        reopens_when="x", evidence=["e"])
    b = Ordering.create(**{**_RULING, "statement": "B"}, predicate={"estate": "served"},
                        reopens_when="x", evidence=["e"])
    object.__setattr__(b, "ordering", "speed over reversibility")
    verdict, lines = posture(OrderingState(records={a.id: a, b.id: b}))
    assert verdict == "BLOCKED"
    assert any("CONFLICT" in line for line in lines)


def test_ordering_store_round_trips_and_supersession_keeps_lineage(tmp_path: Path):
    store = OrderingStore(tmp_path)
    assert store.path == tmp_path / ".intentops" / "wisdom" / "orderings.jsonl"

    first = store.rule(Ordering.create(**_RULING))
    second = Ordering.create(**{**_RULING, "statement": "The successor"},
                             supersedes=first.id)
    store.rule(second)

    state = store.load()
    assert state.by_id(first.id).status == "superseded"
    assert state.by_id(first.id).superseded_by == second.id
    assert [o.id for o in state.active()] == [second.id]
    # append-only: the superseded ruling is retained, never deleted
    assert state.by_id(first.id) is not None


def test_reaffirm_refuses_a_blank_reason(tmp_path: Path):
    store = OrderingStore(tmp_path)
    ruling = store.rule(Ordering.create(**_RULING))
    with pytest.raises(ValidationError, match="reason"):
        store.reaffirm(ruling.id, reason="   ", by="the operator")
    store.reaffirm(ruling.id, reason="still true, nothing moved", by="the operator")
    assert store.load().by_id(ruling.id).reaffirm_reason == "still true, nothing moved"


def test_compile_predicate_is_attributable(tmp_path: Path):
    store = OrderingStore(tmp_path)
    ruling = store.rule(Ordering.create(**_RULING))
    with pytest.raises(ValidationError, match="rationale"):
        store.compile_predicate(ruling.id, {"estate": "served"}, "escalate",
                                by="the operator", rationale="")
    with pytest.raises(ValidationError, match="by"):
        store.compile_predicate(ruling.id, {"estate": "served"}, "escalate",
                                by="", rationale="because")
    store.compile_predicate(ruling.id, {"estate": "served"}, "escalate",
                            by="the operator", rationale="the condition means this fact")
    got = store.load().by_id(ruling.id)
    assert got.predicate == {"estate": "served"}
    assert got.compiled_by == "the operator"


def test_ordering_store_refuses_to_guess_a_node_root():
    with pytest.raises(ValidationError):
        OrderingStore("")


def test_ordering_selftest_passes():
    from intentops_core.wisdom.ordering import selftest
    assert selftest() == 0


# ---------------------------------------------------------------------------
# store_guard -- the .lock refusal
# ---------------------------------------------------------------------------


def test_storelock_refuses_a_non_lock_path(tmp_path: Path):
    """StoreLock TRUNCATES what it is given; a store path must be refused."""
    store = tmp_path / "LEDGER.yaml"
    store.write_text("entries: [1, 2, 3]\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"\.lock"):
        StoreLock(store)
    assert store.read_text(encoding="utf-8") == "entries: [1, 2, 3]\n"


def test_storelock_accepts_the_sibling_lock_path(tmp_path: Path):
    store = tmp_path / "LEDGER.yaml"
    with StoreLock(lock_for(store)):
        pass
    assert lock_for(store).name == "LEDGER.yaml.lock"


def test_locked_rmw_reads_inside_the_lock(tmp_path: Path):
    store = tmp_path / "counter.txt"
    for _ in range(3):
        locked_rmw(store, lambda cur: str(int(cur or "0") + 1),
                   loader=lambda p: p.read_text(encoding="utf-8"),
                   dumper=lambda v: v)
    assert store.read_text(encoding="utf-8") == "3"


def test_store_guard_selftest_passes():
    from intentops_core.store_guard import selftest
    assert selftest() == 0


# ---------------------------------------------------------------------------
# councils -- the tally arithmetic and the conscience hold
# ---------------------------------------------------------------------------


def test_posture_council_approves_a_clean_item():
    verdict, votes = posture_council_vote(
        ActuationItem(item_id="I-1", title="Regenerate an index",
                      description="Rebuild a generated file from its sources.",
                      tier="T1", complexity=4)
    )
    assert verdict == APPROVED
    assert sum(1 for v in votes if v.vote == "APPROVE") >= 3


def test_posture_council_guardian_veto_beats_a_majority():
    """Four approvals cannot carry an item the guardian rejects."""
    verdict, votes = posture_council_vote(
        ActuationItem(item_id="I-2", title="Publish the branch",
                      description="Push the release branch to the remote.",
                      tier="T1", complexity=1, command="git push origin main")
    )
    guardian = next(v for v in votes if v.member == "guardian")
    assert guardian.vote == "REJECT"
    assert verdict == REJECTED


def test_posture_council_auditor_veto_on_an_untraceable_item():
    verdict, votes = posture_council_vote(
        ActuationItem(title="Anonymous", description="A described change.",
                      tier="T1", complexity=1)
    )
    assert next(v for v in votes if v.member == "auditor").vote == "REJECT"
    assert verdict == REJECTED


def test_unstated_complexity_abstains_rather_than_defaulting():
    """A fall-through constant wearing a measurement's costume is the defect."""
    _, votes = posture_council_vote(
        ActuationItem(item_id="I-3", title="A change",
                      description="A change with a stated intent.", tier="T1")
    )
    assert next(v for v in votes if v.member == "economist").vote == "ABSTAIN"


def test_posture_council_threshold_is_three_of_five():
    """Two abstentions still clear the bar; three do not."""
    two_abstain, _ = posture_council_vote(
        ActuationItem(item_id="I-4", title="A change",
                      description="A change with a stated intent.",
                      tier="T1", consecutive_failures=1)  # economist + healer abstain
    )
    assert two_abstain == APPROVED
    three_abstain, votes = posture_council_vote(
        ActuationItem(item_id="I-5", title="Document the delete path",
                      description="Write down how the delete path works.",
                      tier="T1", consecutive_failures=1)  # + guardian abstains on prose
    )
    assert sum(1 for v in votes if v.vote == "APPROVE") == 2
    assert three_abstain == REJECTED


def test_rogers_conscience_hold_cannot_be_outvoted_down_to_bless():
    lone = MoralCompassCouncil(guardians=[ROGERS])
    action = ProposedAction(
        summary="quietly revoke access for a vulnerable student",
        tier="T1", evidence="a ticket", reversible=True,
    )
    reading = lone.deliberate(action)
    assert reading.conscience_hold is True
    assert reading.verdict is not CouncilVerdict.PROCEED


def test_values_council_refrains_on_two_objections():
    reading = MoralCompassCouncil().deliberate(ProposedAction(
        summary="quietly delete the records for everyone",
        tier="T4", reaches_reality=True, reversible=False,
        affects_people=("a person downstream",),
    ))
    assert reading.verdict is CouncilVerdict.REFRAIN
    assert reading.conscience_hold is True
    assert any(v.stance is Stance.OBJECT for v in reading.votes)


def test_values_council_never_blesses_a_gated_action():
    reading = MoralCompassCouncil().deliberate(ProposedAction(
        summary="deploy the release", tier="T3", reaches_reality=True,
        evidence="the run log", affects_people=("an operator",),
    ))
    assert reading.verdict is not CouncilVerdict.PROCEED
    assert any("human authorization" in m for m in reading.must_address)


def test_council_selftests_pass():
    from intentops_core.councils.guardians import selftest as guardians_selftest
    from intentops_core.councils.posture import selftest as posture_selftest
    assert guardians_selftest() == 0
    assert posture_selftest() == 0


# ---------------------------------------------------------------------------
# priority -- IPI-1 arithmetic
# ---------------------------------------------------------------------------


def test_priority_formula_is_cod_over_job_size():
    s = UIES(urgency=8, importance=9, ease=6, synergy=7)
    assert cost_of_delay(s) == 24
    assert job_size(s) == 5
    assert priority(s) == 4.8


def test_priority_bounds():
    assert priority(UIES(1, 1, 1, 1)) == round(3 / 10, 2)
    assert priority(UIES(10, 10, 10, 10)) == 30.0


def test_priority_is_deterministic_and_ease_is_a_divisor():
    """Equal value, easier work -> higher priority. That is the whole point."""
    hard = UIES(urgency=7, importance=7, ease=2, synergy=6)
    easy = UIES(urgency=7, importance=7, ease=9, synergy=6)
    assert priority(easy) > priority(hard)
    assert priority(easy) == priority(UIES(7, 7, 9, 6))


def test_priority_refuses_an_out_of_range_score():
    with pytest.raises(ValueError):
        UIES(urgency=11, importance=5, ease=5, synergy=5)
    with pytest.raises(TypeError):
        UIES(urgency=True, importance=5, ease=5, synergy=5)  # type: ignore[arg-type]


def test_rank_is_a_total_order():
    items = [
        ScoredItem(id="b", uies=UIES(5, 5, 5, 5), created_at="2026-01-02"),
        ScoredItem(id="a", uies=UIES(5, 5, 5, 5), created_at="2026-01-01"),
        ScoredItem(id="c", uies=UIES(9, 9, 9, 9)),
    ]
    assert [i.id for i in rank(items)] == ["c", "a", "b"]
    assert rank(items) == rank(list(reversed(items)))


# ---------------------------------------------------------------------------
# gate -- verdict and the reaches-reality classifier
# ---------------------------------------------------------------------------


def test_a_refusal_must_state_a_reason():
    with pytest.raises(ValueError, match="reason"):
        Verdict(Decision.BLOCK, tier="T3")
    assert Verdict(Decision.ALLOW, tier="T0").reasons == ()


def test_merge_takes_the_strictest_and_refuses_an_empty_population():
    from intentops_core.gate.verdict import allow as _allow, ask as _ask
    merged = merge([_allow("T1"), _ask("T3", "a remote push")])
    assert merged.decision is Decision.ASK
    assert merged.tier == "T3"
    assert merged.reaches_reality is True
    with pytest.raises(ValueError, match="not permission"):
        merge([])


def test_a_remote_push_reaches_reality():
    hit = classify_reaches_reality("Bash", {"command": "git push origin main"})
    assert hit is not None and hit[0] == "T3"
    assert verdict_for("Bash", {"command": "git push origin main"}).decision is Decision.ASK


def test_a_local_read_does_not_reach_reality():
    assert classify_reaches_reality("Bash", {"command": "cat README.md"}) is None
    assert classify_reaches_reality("Read", {"file_path": "docs/notes.md"}) is None
    v = verdict_for("Bash", {"command": "ls -la"})
    assert v.allowed is True and v.reaches_reality is False


def test_a_workspace_write_is_not_bricked_by_its_own_content():
    """Classify off the operation, never off content. Data is not an act."""
    assert classify_reaches_reality(
        "Bash", {"command": 'git commit -m "document the rm -rf recovery path"'}
    ) is None
    assert classify_reaches_reality(
        "Write", {"file_path": "docs/runbook.md"}
    ) is None


@pytest.mark.parametrize(
    "command, tier",
    [
        ("git push --force origin main", "T4"),
        ("rm -rf ./build", "T4"),
        ("kubectl apply -f deploy.yaml", "T3"),
        ("terraform apply", "T3"),
        ('psql -c "DELETE FROM sessions"', "T4"),
        ('sqlcmd -Q "SELECT * FROM trust_accounts"', "T4"),
        ("curl -X POST https://api.example.test/v1/items", "T3"),
        ("git reset --hard HEAD~3", "T3"),
    ],
)
def test_each_reaches_reality_class_fires(command: str, tier: str):
    hit = classify_reaches_reality("Bash", {"command": command})
    assert hit is not None, f"no class fired for: {command}"
    assert hit[0] == tier, f"{command} -> {hit}"


def test_git_rm_is_not_a_recursive_delete():
    assert classify_reaches_reality("Bash", {"command": "git rm --cached notes.md"}) is None


def test_outbound_send_is_gated_but_a_draft_is_not():
    assert classify_reaches_reality("mcp__mail__send_message", {"to": "x"})[0] == "T3"
    assert classify_reaches_reality("mcp__mail__send-message", {"to": "x"})[0] == "T3"
    assert classify_reaches_reality("mcp__mail__create_draft", {"to": "x"}) is None
    # the one verb that both drafts AND transmits is a send, not a draft
    assert classify_reaches_reality("mcp__mail__send_draft", {"id": "d1"})[0] == "T3"


def test_another_persons_mailbox_is_a_third_party_disclosure():
    ind = RealityIndicators(self_identities=("operator-identity",))
    assert classify_reaches_reality(
        "mcp__mail__get_messages", {"userId": "another-identity"}, indicators=ind
    )[0] == "T3"
    assert classify_reaches_reality(
        "mcp__mail__get_messages", {"userId": "operator-identity"}, indicators=ind
    ) is None


def test_a_delegated_remote_owner_lowers_only_a_provable_push():
    ind = RealityIndicators(delegated_remote_owners=("my-own-org",))
    proven = classify_reaches_reality(
        "Bash", {"command": 'cd "/repos/my-own-org/app" && git push origin main'},
        indicators=ind)
    assert proven[0] == "T2"
    unproven = classify_reaches_reality("Bash", {"command": "git push origin main"},
                                        indicators=ind)
    assert unproven[0] == "T3"
    forced = classify_reaches_reality(
        "Bash", {"command": 'cd "/repos/my-own-org/app" && git push --force'},
        indicators=ind)
    assert forced[0] == "T4", "a delegation must never weaken a force push"


def test_writing_a_secret_file_reaches_reality():
    assert classify_reaches_reality("Write", {"file_path": "/srv/app/id_ed25519"})[0] == "T4"


def test_gate_selftest_passes():
    from intentops_core.gate.classify import selftest
    assert selftest() == 0


# ---------------------------------------------------------------------------
# estate_classifier -- the map is the source, code holds only the rules
# ---------------------------------------------------------------------------

_FIXTURE_MAP = {
    "schema": "estate-map/v1",
    "as_of": "2026-09-06",
    "entries": [
        {"id": "this-node", "label": "This node", "kind": "internal",
         "keywords": ["the gate", "ordering store"]},
        {"id": "side-venture", "label": "A venture", "kind": "venture",
         "keywords": ["side venture"]},
        {"id": "counterparty", "label": "A served organisation", "kind": "served",
         "keywords": ["the engagement", "side venture"]},
        {"id": "home", "label": "The household", "kind": "household",
         "keywords": ["home network"]},
    ],
}


def _fixture() -> EstateMap:
    return EstateMap.from_mapping(_FIXTURE_MAP, source="fixture")


@pytest.mark.parametrize(
    "text, kind",
    [
        ("edit the gate chain", "internal"),
        ("ship the side venture roadmap", "served"),   # precedence: served wins
        ("review the engagement scope", "served"),
        ("change the home network", "household"),
        ("a sentence with no declared vocabulary", "unknown"),
    ],
)
def test_estate_classification_from_the_fixture_map(text: str, kind: str):
    assert _fixture().classify_text(text).kind == kind


def test_unknown_is_omitted_from_the_fact_vocabulary():
    """A default here would let a predicate fire on an estate nobody observed."""
    em = _fixture()
    assert em.classify_text("nothing decisive").as_fact() is None
    assert em.fact_for({"title": "the engagement kickoff"}) == {"estate": "served"}


def test_estate_precedence_is_served_venture_household_internal():
    em = _fixture()
    assert em.classify_text("side venture and the gate").kind == "served"
    assert em.classify_text("home network and the gate").kind == "household"


def test_keyword_collisions_are_surfaced_never_silently_resolved():
    assert _fixture().keyword_collisions() == [
        ("side venture", ["side-venture", "counterparty"])
    ]


def test_explicit_estate_on_an_item_wins_but_an_unknown_one_halts():
    em = _fixture()
    assert em.classify_item({"estate": "counterparty", "title": "the gate"}).kind == "served"
    assert em.classify_item({"estate": "household"}).kind == "household"
    with pytest.raises(EstateMapError):
        em.classify_item({"estate": "somewhere-else"})


@pytest.mark.parametrize(
    "bad, why",
    [
        ({"schema": "estate-map/v1"}, "missing entries key"),
        ({"entries": [{"id": "a", "label": "A", "kind": "cloud", "keywords": ["x"]}]},
         "undeclared kind"),
        ({"entries": [{"id": "a", "label": "A", "kind": "internal"}]}, "missing field"),
        ({"entries": [{"id": "a", "label": "A", "kind": "internal", "keywords": []}]},
         "empty keywords"),
    ],
)
def test_a_missing_or_undeclared_field_halts(bad: dict, why: str):
    with pytest.raises(EstateMapError):
        EstateMap.from_mapping(bad)


def test_an_empty_entries_list_is_valid_but_a_missing_file_halts(tmp_path: Path):
    em = EstateMap.from_mapping({"schema": "estate-map/v1", "entries": []})
    assert em.entries == []
    assert em.classify_text("anything at all").kind == "unknown"
    with pytest.raises(EstateMapError, match="not found"):
        load_estate_map(tmp_path / "ESTATE-MAP.yaml")


def test_estate_classifier_selftest_passes():
    from intentops_core.estate_classifier import selftest
    assert selftest() == 0


# ---------------------------------------------------------------------------
# the seam -- indicators built from an estate map
# ---------------------------------------------------------------------------


def test_served_estate_keywords_become_production_indicators():
    from intentops_core.gate.classify import indicators_from_estate_map
    ind = indicators_from_estate_map(_FIXTURE_MAP)
    assert "the engagement" in ind.production_keywords
    assert "the gate" not in ind.production_keywords  # internal is not production


def test_indicator_block_refuses_an_undeclared_key():
    from intentops_core.gate.classify import indicators_from_estate_map
    with pytest.raises(ValueError, match="unknown key"):
        indicators_from_estate_map({"entries": [], "reality_indicators": {"oops": []}})


def test_journal_rows_are_json_and_append_only(tmp_path: Path):
    """The write model is a journal: every event is one readable line."""
    store = OrderingStore(tmp_path)
    store.rule(Ordering.create(**_RULING))
    store.reopen(Ordering.mint_id(_RULING["statement"]), reason="conditions moved",
                 by="the operator")
    lines = store.path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["event"] for line in lines] == ["rule", "reopen"]
