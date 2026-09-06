"""The alignment interview and the two-bar delegation gate.

Every test here asserts a REFUSAL, a GATE, or a string that must never appear.
There is deliberately no test of the form "a good template loads and nothing
happens" alone: a loader that only ever consents is indistinguishable from one
that is switched off, so each consent case also asserts what it produced.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest
import yaml

from intentops_core.alignment import calibration as cal
from intentops_core.alignment.interview import (
    ActorPolicy,
    AnswerJournal,
    InterviewError,
    STAGE_THRESHOLDS,
    grade_for_sitting,
    load_interview,
    operator_rulings,
    render_question,
    reveal_prior_ruling,
    stage_plan,
)
from intentops_core.loto import ledger as loto_ledger

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = REPO_ROOT / "config" / "alignment-interview.template.yaml"
AT = "2026-09-06T12:00:00+00:00"


# ---------------------------------------------------------------------------
# fixtures -- a minimal conformant interview, mutated per refusal
# ---------------------------------------------------------------------------


def _doc() -> dict:
    return {
        "schema": "alignment-interview/v1",
        "kinds": ["consent", "boundary", "domain", "replay", "relationship",
                  "review"],
        "tiers": ["T0", "T1", "T2", "T3", "T4", "T5"],
        "sittings": [{"id": s} for s in ("S0", "S1", "S2", "S3", "S4")],
        "variables": {"estate": "which estate", "reaches_person": "a person?",
                      "their_visibility": "do they learn of it?"},
        "variables_floor": {"cannot_remove": ["reaches_person",
                                              "their_visibility"]},
        "calibration_bar": {"minimum_rulings": 20, "minimum_accuracy": 0.80},
        "modules": [
            {"id": "m-consent", "title": "Consent", "sitting": "S0",
             "why": "consent first",
             "questions": [{
                 "id": "q-consent", "kind": "consent", "tier": "T3",
                 "sitting": "S0", "variables": [], "technique": "consent-first",
                 "provenance": "durable", "prompt": "May it be built?",
                 "options": ["Yes", "No"], "recommended": None,
                 "rationale": "a consent instrument carries no recommendation"}]},
            {"id": "m-replay", "title": "Replay", "sitting": "S3",
             "why": "revealed preference",
             "questions": [{
                 "id": "q-replay", "kind": "replay", "tier": "T3",
                 "sitting": "S3", "variables": ["estate"],
                 "technique": "replay", "provenance": "durable",
                 "prompt": "Rule again.",
                 "options": ["A", "B", "C", "D"], "recommended": None,
                 "replay": {"approval_id": "appr-1", "shuffle_options": True,
                            "reveal_prior_ruling": "after_answer"},
                 "rationale": "the shuffle is not decoration"}]},
        ],
    }


def _write(tmp_path: Path, doc: dict) -> Path:
    path = tmp_path / "interview.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


def _load(tmp_path: Path, mutate=None):
    doc = _doc()
    if mutate is not None:
        mutate(doc)
    return load_interview(_write(tmp_path, doc))


# ---------------------------------------------------------------------------
# the shipped template
# ---------------------------------------------------------------------------


def test_the_shipped_template_loads_and_carries_questions():
    interview = load_interview(TEMPLATE)
    assert interview.questions(), "a template with no questions elicits nothing"
    assert "reaches_person" in interview.variables_floor["cannot_remove"]


# ---------------------------------------------------------------------------
# loader refusals -- every one must fire
# ---------------------------------------------------------------------------


def test_a_question_with_no_id_is_refused(tmp_path):
    with pytest.raises(InterviewError, match="no id"):
        _load(tmp_path, lambda d: d["modules"][0]["questions"][0].pop("id"))


def test_a_duplicate_id_is_refused(tmp_path):
    def mutate(doc):
        doc["modules"][1]["questions"][0]["id"] = "q-consent"

    with pytest.raises(InterviewError, match="duplicate id"):
        _load(tmp_path, mutate)


def test_an_unknown_kind_is_refused(tmp_path):
    with pytest.raises(InterviewError, match="kind"):
        _load(tmp_path,
              lambda d: d["modules"][0]["questions"][0].update(kind="vibes"))


def test_an_unknown_tier_is_refused(tmp_path):
    with pytest.raises(InterviewError, match="tier"):
        _load(tmp_path,
              lambda d: d["modules"][0]["questions"][0].update(tier="T9"))


@pytest.mark.parametrize("field_name", ["prompt", "rationale"])
def test_a_missing_prompt_or_rationale_is_refused(tmp_path, field_name):
    with pytest.raises(InterviewError, match=field_name):
        _load(tmp_path,
              lambda d: d["modules"][0]["questions"][0].update({field_name: ""}))


def test_more_than_four_options_is_refused(tmp_path):
    with pytest.raises(InterviewError, match="options"):
        _load(tmp_path, lambda d: d["modules"][0]["questions"][0].update(
            options=["a", "b", "c", "d", "e"]))


def test_a_recommended_index_outside_the_options_is_refused(tmp_path):
    def mutate(doc):
        question = doc["modules"][0]["questions"][0]
        question["kind"] = "boundary"      # so the consent refusal is not what fires
        question["recommended"] = 7

    with pytest.raises(InterviewError, match="outside"):
        _load(tmp_path, mutate)


def test_an_undeclared_variable_is_refused(tmp_path):
    with pytest.raises(InterviewError, match="variable"):
        _load(tmp_path, lambda d: d["modules"][1]["questions"][0].update(
            variables=["money_origin"]))


def test_a_bad_provenance_is_refused(tmp_path):
    with pytest.raises(InterviewError, match="provenance"):
        _load(tmp_path, lambda d: d["modules"][0]["questions"][0].update(
            provenance="vibes"))


def test_an_undeclared_sitting_is_refused(tmp_path):
    with pytest.raises(InterviewError, match="sitting"):
        _load(tmp_path,
              lambda d: d["modules"][0]["questions"][0].update(sitting="S9"))


def test_a_consent_question_carrying_a_recommendation_is_refused(tmp_path):
    with pytest.raises(InterviewError, match="consent"):
        _load(tmp_path,
              lambda d: d["modules"][0]["questions"][0].update(recommended=0))


def test_a_replay_carrying_a_recommendation_is_refused(tmp_path):
    with pytest.raises(InterviewError, match="replay"):
        _load(tmp_path,
              lambda d: d["modules"][1]["questions"][0].update(recommended=0))


def test_a_replay_naming_no_approval_is_refused(tmp_path):
    with pytest.raises(InterviewError, match="no approval id"):
        _load(tmp_path,
              lambda d: d["modules"][1]["questions"][0]["replay"].pop(
                  "approval_id"))


def test_an_unshuffled_replay_is_refused(tmp_path):
    with pytest.raises(InterviewError, match="shuffle"):
        _load(tmp_path, lambda d: d["modules"][1]["questions"][0][
            "replay"].update(shuffle_options=False))


def test_a_replay_revealing_the_prior_ruling_early_is_refused(tmp_path):
    with pytest.raises(InterviewError, match="after the answer"):
        _load(tmp_path, lambda d: d["modules"][1]["questions"][0][
            "replay"].update(reveal_prior_ruling="before_answer"))


def test_a_missing_closed_vocabulary_halts_rather_than_defaulting(tmp_path):
    with pytest.raises(InterviewError, match="refusing to substitute a default"):
        _load(tmp_path, lambda d: d.pop("kinds"))


def test_a_floor_over_an_undeclared_variable_is_refused(tmp_path):
    with pytest.raises(InterviewError, match="floor over a missing axis"):
        _load(tmp_path, lambda d: d["variables_floor"]["cannot_remove"].append(
            "synchronicity"))


def test_a_bar_that_disagrees_with_the_stage_gate_is_refused(tmp_path):
    """The bar and the S4 gate are one number. Two copies drift; nothing notices."""
    with pytest.raises(InterviewError, match="must be the same number"):
        _load(tmp_path,
              lambda d: d["calibration_bar"].update(minimum_rulings=5))


def test_a_wrong_schema_is_refused(tmp_path):
    with pytest.raises(InterviewError, match="schema"):
        _load(tmp_path, lambda d: d.update(schema="alignment-interview/v0"))


# ---------------------------------------------------------------------------
# gating by evidence available
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rulings,open_stages", [
    (0, {"S0", "S1", "S2"}),
    (9, {"S0", "S1", "S2"}),
    (10, {"S0", "S1", "S2", "S3"}),
    (19, {"S0", "S1", "S2", "S3"}),
    (20, {"S0", "S1", "S2", "S3", "S4"}),
])
def test_stages_open_by_evidence_available_not_by_clock(rulings, open_stages):
    plan = stage_plan(rulings)
    assert {s.id for s in plan if s.available} == open_stages


def test_the_s4_gate_is_the_calibration_bar_itself():
    assert STAGE_THRESHOLDS["S4"] == cal.DEFAULT_MINIMUM_RULINGS


def test_s2_answers_are_inferred_and_never_observed():
    assert grade_for_sitting("S2") == "[INFERRED]"
    assert grade_for_sitting("S3") == "[OBSERVED]"


def test_an_undeclared_sitting_has_no_grade():
    with pytest.raises(InterviewError, match="no declared grade"):
        grade_for_sitting("S9")


def test_a_negative_ruling_count_is_refused():
    with pytest.raises(InterviewError):
        stage_plan(-1)


# ---------------------------------------------------------------------------
# counting real operator rulings
# ---------------------------------------------------------------------------


HISTORY = [
    {"event": "submitted", "id": "a1", "decided_by": None},
    {"event": "approved", "id": "a1", "decided_by": "operator"},
    {"event": "approved", "id": "a1", "decided_by": "operator"},   # same item
    {"event": "rejected", "id": "a2", "decided_by": "operator"},
    {"event": "approved", "id": "a3", "decided_by": "node-x"},     # the node
    {"event": "approved", "id": "a4", "decided_by": "delegated-lane"},
    {"event": "expired", "id": "a5", "decided_by": "operator"},    # a lapse
    {"event": "approved", "id": "a6", "decided_by": None},         # names nobody
    {"corrupt": True},
]


def test_only_real_operator_rulings_count():
    policy = ActorPolicy(node_actors=frozenset({"node-x"}),
                         lane_actors=frozenset({"delegated-lane"}))
    assert operator_rulings(HISTORY, policy) == 2


def test_an_actor_policy_is_required():
    with pytest.raises(InterviewError, match="actor policy"):
        operator_rulings(HISTORY, None)


# ---------------------------------------------------------------------------
# rendering -- the shuffle is seeded and deterministic
# ---------------------------------------------------------------------------


def test_replay_shuffling_is_seeded_and_deterministic(tmp_path):
    interview = _load(tmp_path)
    question = next(q for q in interview.questions() if q.kind == "replay")
    first = render_question(question, random.Random(7))
    again = render_question(question, random.Random(7))
    assert first.order == again.order, "the same seed must render the same order"
    assert first.shuffled is True
    assert sorted(first.options) == sorted(question.options)
    # A shuffler that always returns the identity permutation would satisfy
    # determinism and measure nothing, which is the defect the shuffle exists
    # to prevent -- so prove it actually permutes across seeds.
    orders = {render_question(question, random.Random(seed)).order
              for seed in range(30)}
    assert len(orders) > 1, "the shuffle never permutes; it measures nothing"


def test_a_replay_rendered_with_no_seeded_rng_is_refused(tmp_path):
    interview = _load(tmp_path)
    question = next(q for q in interview.questions() if q.kind == "replay")
    with pytest.raises(InterviewError, match="measures nothing"):
        render_question(question, None)


def test_a_rendered_replay_never_shows_the_prior_ruling(tmp_path):
    interview = _load(tmp_path)
    question = next(q for q in interview.questions() if q.kind == "replay")
    rendered = render_question(question, random.Random(1))
    assert rendered.prior_ruling_visible is False
    assert rendered.recommended is None


def test_the_prior_ruling_is_refused_before_the_answer(tmp_path):
    interview = _load(tmp_path)
    question = next(q for q in interview.questions() if q.kind == "replay")
    with pytest.raises(InterviewError, match="before"):
        reveal_prior_ruling(question, answered=False)
    assert reveal_prior_ruling(question, answered=True)["approval_id"] == "appr-1"


# ---------------------------------------------------------------------------
# the answer journal
# ---------------------------------------------------------------------------


def test_an_answer_is_graded_by_its_sitting_and_journalled_append_only(tmp_path):
    interview = _load(tmp_path)
    question = next(q for q in interview.questions() if q.sitting == "S0")
    journal = AnswerJournal(tmp_path / "identity-repo")
    journal.record(question, "Yes", rulings=0, answered_by="operator", at=AT)
    journal.record(question, "Yes", rulings=0, answered_by="operator", at=AT)
    rows = journal.load()
    assert len(rows) == 2, "the journal appends; it never rewrites"
    assert rows[0]["grade"] == "recorded fact"
    assert journal.path.relative_to(tmp_path / "identity-repo") == Path(
        "identity") / "alignment" / "answers.jsonl"


def test_an_answer_to_a_closed_sitting_is_refused(tmp_path):
    interview = _load(tmp_path)
    question = next(q for q in interview.questions() if q.sitting == "S3")
    journal = AnswerJournal(tmp_path / "identity-repo")
    with pytest.raises(InterviewError, match="not open"):
        journal.record(question, "A", rulings=0, answered_by="operator", at=AT)
    journal.record(question, "A", rulings=10, answered_by="operator", at=AT)
    assert journal.load()[0]["grade"] == "[OBSERVED]"


def test_an_unattributed_answer_is_refused(tmp_path):
    interview = _load(tmp_path)
    question = next(q for q in interview.questions() if q.sitting == "S0")
    journal = AnswerJournal(tmp_path / "identity-repo")
    with pytest.raises(InterviewError, match="who gave it"):
        journal.record(question, "Yes", rulings=0, answered_by=" ", at=AT)


def test_a_corrupt_journal_line_is_counted_never_skipped(tmp_path):
    journal = AnswerJournal(tmp_path / "identity-repo")
    journal.path.parent.mkdir(parents=True, exist_ok=True)
    journal.path.write_text('{"ok": 1}\nnot json\n', encoding="utf-8")
    rows = journal.load()
    assert len(rows) == 2 and rows[1]["corrupt"] is True


# ---------------------------------------------------------------------------
# the council floor
# ---------------------------------------------------------------------------


@pytest.fixture()
def floor():
    return cal.load_council_floor(REPO_ROOT)


def test_the_council_floor_is_read_from_the_birth_bundle(floor):
    assert floor.sigma == 3.0 and floor.level == "standard"
    assert "tapch.yaml" in floor.source


def test_an_absent_tapch_invariant_is_refused(tmp_path):
    with pytest.raises(cal.CalibrationError, match="refusing to invent"):
        cal.load_council_floor(tmp_path)


def test_an_undeclared_confidence_level_is_refused():
    with pytest.raises(cal.CalibrationError, match="not one of"):
        cal.load_council_floor(REPO_ROOT, "vibes")


def test_an_absent_council_reading_is_never_a_pass(floor):
    met, why = cal.council_floor_met(None, floor)
    assert met is False and "absent verdict is not a verdict" in why


@pytest.mark.parametrize("verdict", ["ESCALATE", "REJECTED"])
def test_a_non_approving_reading_is_not_a_pass(floor, verdict):
    met, _ = cal.council_floor_met(cal.CouncilReading(verdict, "high"), floor)
    assert met is False


def test_a_reading_below_the_floor_is_not_a_pass(floor):
    met, why = cal.council_floor_met(
        cal.CouncilReading("APPROVED", "preliminary"), floor)
    assert met is False and "below the" in why


def test_a_reading_at_or_above_the_floor_passes(floor):
    for level in ("standard", "high"):
        met, _ = cal.council_floor_met(cal.CouncilReading("APPROVED", level),
                                       floor)
        assert met is True, level


def test_a_reading_at_an_undeclared_level_is_refused(floor):
    with pytest.raises(cal.CalibrationError, match="does not declare"):
        cal.council_floor_met(cal.CouncilReading("APPROVED", "vibes"), floor)


# ---------------------------------------------------------------------------
# the scorer's refusals
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path):
    return cal.CalibrationStore(tmp_path / "identity-repo",
                                node_actors=["node-x"],
                                lane_actors=["delegated-lane"],
                                bar_since="2026-09-01T00:00:00+00:00")


def test_an_empty_sealed_prediction_is_refused(store):
    with pytest.raises(cal.CalibrationError, match="must say something"):
        store.seal_prediction("a1", "   ", confidence="HIGH", at=AT)


def test_a_sealed_prediction_must_name_an_approval(store):
    with pytest.raises(cal.CalibrationError, match="must name an approval"):
        store.seal_prediction("", "approve", confidence="HIGH", at=AT)


def test_a_grade_no_sealed_prediction_names_is_refused(store):
    with pytest.raises(cal.CalibrationError, match="no sealed prediction"):
        store.record_grade("a1", "hit", ruled_by="operator", ruled_at=AT)


def test_the_node_grading_itself_is_refused(store):
    store.seal_prediction("a1", "approve", confidence="HIGH", at=AT)
    with pytest.raises(cal.CalibrationError, match="agreement with itself"):
        store.record_grade("a1", "hit", ruled_by="node-x", ruled_at=AT)


def test_a_delegated_lane_decision_is_refused(store):
    store.seal_prediction("a1", "approve", confidence="HIGH", at=AT)
    with pytest.raises(cal.CalibrationError, match="delegated lane"):
        store.record_grade("a1", "hit", ruled_by="delegated-lane", ruled_at=AT)


def test_an_unattributed_ruling_is_refused(store):
    store.seal_prediction("a1", "approve", confidence="HIGH", at=AT)
    with pytest.raises(cal.CalibrationError, match="must name who ruled"):
        store.record_grade("a1", "hit", ruled_by="  ", ruled_at=AT)


def test_a_backward_ruling_is_refused(store):
    """The bar is forward-only: a score taken over cases already known is not
    a prediction at all."""
    store.seal_prediction("a1", "approve", confidence="HIGH", at=AT)
    with pytest.raises(cal.CalibrationError, match="forward-only"):
        store.record_grade("a1", "hit", ruled_by="operator",
                           ruled_at="2026-08-01T00:00:00+00:00")


def test_an_unknown_outcome_is_refused(store):
    with pytest.raises(cal.CalibrationError, match="not one of"):
        store.record_grade("a1", "sort-of", ruled_by="operator", ruled_at=AT)


@pytest.mark.parametrize("prediction", ["BASE_RATE", "NO_BASIS", "UNAVAILABLE",
                                        "base_rate", "", "   ", None])
def test_non_concurrence_predictions_never_render_as_concurrence(prediction):
    assert cal.renders_as_concurrence(prediction) is False


def test_a_real_prediction_renders_as_concurrence():
    assert cal.renders_as_concurrence("approve: reversible, inside the node")


def test_the_state_is_a_pure_fold_and_a_later_grade_supersedes(store):
    for i in range(3):
        store.seal_prediction(f"a{i}", "approve", confidence="HIGH", at=AT)
    store.record_grade("a0", "miss", ruled_by="operator", ruled_at=AT)
    store.record_grade("a0", "hit", ruled_by="operator", ruled_at=AT)
    store.record_grade("a1", "partial", ruled_by="operator", ruled_at=AT)
    state = store.state()
    assert state.graded == 2 and state.hits == 1
    assert state.accuracy == pytest.approx(0.5)
    assert state.sealed == 3
    lines = store.path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 6, "every event is appended; nothing is rewritten"
    assert json.loads(lines[0])["event"] == "sealed"


# ---------------------------------------------------------------------------
# floors -- raise freely, lower only through a tagout
# ---------------------------------------------------------------------------


def _fill(n: int, accuracy: float) -> cal.CalibrationState:
    hits = round(n * accuracy)
    return cal.CalibrationState(graded=n, hits=hits, accuracy=hits / n,
                                sealed=n)


def test_raising_any_floor_is_free(floor):
    floors = cal.default_floors()
    raised = cal.raise_floors(floors, cal.Floors("high", 30, 0.95))
    assert raised.minimum_rulings == 30 and raised.council_level == "high"


@pytest.mark.parametrize("proposed", [
    cal.Floors("standard", 5, 0.80),
    cal.Floors("standard", 20, 0.50),
    cal.Floors("preliminary", 20, 0.80),
])
def test_a_lowering_proposal_is_refused_by_the_raise_path(floor, proposed):
    with pytest.raises(cal.CalibrationError, match="LOWERS the bar"):
        cal.raise_floors(cal.default_floors(), proposed)


def test_lowering_without_a_tagout_is_refused(tmp_path, floor):
    ledger_path = tmp_path / "LEDGER.yaml"
    ledger_path.write_text(yaml.safe_dump(loto_ledger.new_ledger()),
                           encoding="utf-8")
    with pytest.raises(cal.CalibrationError, match="no tagout"):
        cal.lower_floors(cal.default_floors(), cal.Floors("standard", 5, 0.80),
                         ledger_path=ledger_path, loto_id="LOTO-2026-09-06-BAR")


def _tagout(tmp_path: Path, tier: str = "T3",
            reenergize: str | None = "when 20 real rulings exist") -> Path:
    ledger_path = tmp_path / "LEDGER.yaml"
    ledger_path.write_text(yaml.safe_dump(loto_ledger.new_ledger()),
                           encoding="utf-8")
    carrier = tmp_path / "switch.py"
    carrier.write_text("# TAGOUT: LOTO-2026-09-06-BAR\n", encoding="utf-8")
    loto_ledger.create_entry(
        ledger_path, "LOTO-2026-09-06-BAR",
        what="the calibration bar is lowered for a bring-up window",
        tier=tier,
        carriers=[{"path": "switch.py", "probe": "LOTO-2026-09-06-BAR"}],
        by="operator", authority="the operator",
        reason="bring-up", reenergize_when=reenergize)
    return ledger_path


def test_lowering_against_a_governed_t3_tagout_is_allowed(tmp_path, floor):
    ledger_path = _tagout(tmp_path)
    lowered = cal.lower_floors(cal.default_floors(),
                               cal.Floors("standard", 5, 0.80),
                               ledger_path=ledger_path,
                               loto_id="LOTO-2026-09-06-BAR")
    assert lowered.minimum_rulings == 5


def test_lowering_against_a_tier_too_low_is_refused(tmp_path, floor):
    ledger_path = _tagout(tmp_path, tier="T1", reenergize=None)
    with pytest.raises(cal.CalibrationError, match="T3 change at least"):
        cal.lower_floors(cal.default_floors(), cal.Floors("standard", 5, 0.80),
                         ledger_path=ledger_path, loto_id="LOTO-2026-09-06-BAR")


def test_lowering_against_a_tagged_in_switch_is_refused(tmp_path, floor):
    ledger_path = _tagout(tmp_path)
    loto_ledger.append_chain_link(
        ledger_path, "LOTO-2026-09-06-BAR",
        {"action": "tagged_in", "by": "operator", "authority": "the operator",
         "reason": "the window closed"})
    with pytest.raises(cal.CalibrationError, match="not actually tagged"):
        cal.lower_floors(cal.default_floors(), cal.Floors("standard", 5, 0.80),
                         ledger_path=ledger_path, loto_id="LOTO-2026-09-06-BAR")


def test_spending_a_tagout_on_a_non_lowering_change_is_refused(tmp_path, floor):
    ledger_path = _tagout(tmp_path)
    with pytest.raises(cal.CalibrationError, match="lowers nothing"):
        cal.lower_floors(cal.default_floors(), cal.Floors("standard", 30, 0.90),
                         ledger_path=ledger_path, loto_id="LOTO-2026-09-06-BAR")


def test_an_unreadable_ledger_refuses_rather_than_permits(tmp_path, floor):
    with pytest.raises(cal.CalibrationError, match="could not be read"):
        cal.lower_floors(cal.default_floors(), cal.Floors("standard", 5, 0.80),
                         ledger_path=tmp_path / "absent.yaml",
                         loto_id="LOTO-2026-09-06-BAR")


# ---------------------------------------------------------------------------
# the two-bar gate
# ---------------------------------------------------------------------------


APPROVED = cal.CouncilReading("APPROVED", "standard")


def test_the_gate_requires_both_bars(floor):
    floors = cal.default_floors()
    at_bar = _fill(20, 0.85)
    below = _fill(6, 0.33)

    both = cal.two_bar_gate(at_bar, floors, council=APPROVED,
                            council_floor=floor)
    assert both.calibrated is True and both.council_met and both.twin_met

    twin_only = cal.two_bar_gate(at_bar, floors, council=None,
                                 council_floor=floor)
    assert twin_only.calibrated is False
    assert twin_only.twin_met is True and twin_only.council_met is False

    council_only = cal.two_bar_gate(below, floors, council=APPROVED,
                                    council_floor=floor)
    assert council_only.calibrated is False
    assert council_only.council_met is True and council_only.twin_met is False

    neither = cal.two_bar_gate(below, floors, council=None,
                               council_floor=floor)
    assert neither.calibrated is False


def test_twenty_rulings_below_eighty_percent_is_not_the_bar(floor):
    result = cal.two_bar_gate(_fill(20, 0.70), cal.default_floors(),
                              council=APPROVED, council_floor=floor)
    assert result.twin_met is False and result.calibrated is False


@pytest.mark.parametrize("state,council", [
    (_fill(1, 1.0), None),
    (_fill(19, 1.0), APPROVED),
    (_fill(20, 0.5), APPROVED),
    (_fill(20, 1.0), None),
    (_fill(20, 1.0), APPROVED),
])
def test_the_status_string_never_claims_alignment(state, council, floor):
    result = cal.two_bar_gate(state, cal.default_floors(), council=council,
                              council_floor=floor)
    assert "aligned" not in result.status.lower()
    assert "alignment" not in result.status.lower()


def test_the_status_is_uncalibrated_until_both_bars_are_met(floor):
    floors = cal.default_floors()
    below = cal.two_bar_gate(_fill(6, 0.33), floors, council=APPROVED,
                             council_floor=floor)
    assert below.status == "uncalibrated: 6 of 20 rulings, 33%"

    twin_only = cal.two_bar_gate(_fill(20, 0.85), floors, council=None,
                                 council_floor=floor)
    assert twin_only.status.startswith("uncalibrated:")
    assert "council floor not met" in twin_only.status

    both = cal.two_bar_gate(_fill(20, 0.85), floors, council=APPROVED,
                            council_floor=floor)
    assert both.status.startswith("calibrated:")


def test_the_gate_reports_a_reason_for_each_bar(floor):
    result = cal.two_bar_gate(_fill(6, 0.33), cal.default_floors(),
                              council=None, council_floor=floor)
    assert len(result.reasons) == 2
    assert any("council floor" in r for r in result.reasons)
    assert any("twin bar" in r for r in result.reasons)


def test_the_calibration_selftest_fires_every_path():
    ok, message = cal.selftest(REPO_ROOT)
    assert ok, message
    assert "paths behaved as declared" in message


# ---------------------------------------------------------------------------
# the CLI surface and the G6 wiring
# ---------------------------------------------------------------------------


def test_the_interview_dry_run_prints_the_honest_status(capsys):
    from intentops_core.cli import main

    assert main(["--repo-root", str(REPO_ROOT), "interview", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "uncalibrated: 0 of 20 rulings, 0%" in out
    assert "aligned" not in out.lower()
    assert "S3  CLOSED" in out and "S4  CLOSED" in out
    for stage in ("S0", "S1", "S2"):
        assert f"{stage}  OPEN" in out
    assert "PLACEHOLDER ANSWER" in out


def test_the_dry_run_writes_nothing(tmp_path, capsys):
    from intentops_core.cli import main

    identity = tmp_path / "identity-repo"
    identity.mkdir()
    assert main(["--repo-root", str(REPO_ROOT), "interview", "--dry-run",
                 "--identity-repo", str(identity)]) == 0
    assert list(identity.rglob("*")) == [], "a dry run persists nothing"


def test_calibration_status_names_both_bars(capsys):
    from intentops_core.cli import main

    assert main(["--repo-root", str(REPO_ROOT), "calibration", "status"]) == 0
    out = capsys.readouterr().out
    assert "BOTH gates" in out
    assert "council floor : standard = 3 sigma" in out
    assert "uncalibrated: 0 of 20 rulings, 0%" in out


def test_g6_records_the_stage_plan_and_the_honest_status(tmp_path):
    from intentops_core.genesis import machine

    identity = tmp_path / "identity-repo"
    ctx = machine.GenesisContext(node_root=tmp_path / "node",
                                 repo_root=REPO_ROOT,
                                 identity_repo=identity, dry_run=True)
    result = machine.g6_alignment(ctx)
    assert result.verdict == "PASS"
    assert result.evidence["status"] == "uncalibrated: 0 of 20 rulings, 0%"
    assert result.evidence["stages_available"] == 3

    doc = yaml.safe_load((identity / "twin" / "interview-state.yaml").read_text(
        encoding="utf-8"))
    assert doc["template_loads"] is True
    assert doc["calibration_bar"]["council_floor_sigma"] == 3.0
    assert {s["id"]: s["available"] for s in doc["stages"]} == {
        "S0": True, "S1": True, "S2": True, "S3": False, "S4": False}


def test_g6_surfaces_a_template_that_exists_but_does_not_load(tmp_path):
    """A template present-but-invalid is worse than absent: it looks accounted
    for. G6 must WARN with the loader's own refusal, never report presence as
    validity."""
    from intentops_core.genesis import machine

    fake_repo = tmp_path / "repo"
    (fake_repo / "config").mkdir(parents=True)
    (fake_repo / "config" / "alignment-interview.template.yaml").write_text(
        "schema: alignment-interview/v1\n", encoding="utf-8")
    (fake_repo / "genesis" / "imprint" / "invariants").mkdir(parents=True)
    (fake_repo / "genesis" / "imprint" / "invariants" / "tapch.yaml").write_text(
        (REPO_ROOT / "genesis" / "imprint" / "invariants"
         / "tapch.yaml").read_text(encoding="utf-8"), encoding="utf-8")

    identity = tmp_path / "identity-repo"
    ctx = machine.GenesisContext(node_root=tmp_path / "node",
                                 repo_root=fake_repo,
                                 identity_repo=identity, dry_run=True)
    result = machine.g6_alignment(ctx)
    assert result.verdict == "WARN"
    assert any("REFUSED by its own loader" in n for n in result.notes)
