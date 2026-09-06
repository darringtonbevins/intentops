"""Tests for the metabolism: cadence, heartbeat, crystallize, grok, assimilation.

The shape of most tests below is the one the charter and estate-manifest tests
use: take a good document as the baseline, break exactly one thing, and assert
the loader halts for THAT reason and not by accident. A test that only proved
"something went wrong" would pass against a loader that halts on everything.

No live services, no network, no model, no scheduler, no subprocess. Every
write goes to a pytest temp directory.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Mapping

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "packages" / "intentops-core"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from intentops_core.genesis import organs as organs_mod  # noqa: E402
from intentops_core.metabolism import (  # noqa: E402
    ARTIFACT_KINDS,
    AssimilationError,
    CadenceError,
    CrystError,
    GrokError,
    NullStage,
    PHASES,
    Request,
    STAGE_IDS,
    Stage,
    Verb,
    append_reading,
    check_alarms,
    collect_counts,
    evaluate,
    load_cadence,
    load_history,
    parse_cadence,
    plan_forge,
    posture,
    record_cycle,
    render_template,
    run_cadence,
    validate,
)
from intentops_core.metabolism import cadence as cadence_mod  # noqa: E402
from intentops_core.metabolism import cli as metabolism_cli  # noqa: E402
from intentops_core.metabolism import crystallize as crystallize_mod  # noqa: E402
from intentops_core.metabolism import grok as grok_mod  # noqa: E402
from intentops_core.metabolism import heartbeat as heartbeat_mod  # noqa: E402

TEMPLATE = REPO_ROOT / "config" / "metabolism-cadence.template.yaml"


# ---------------------------------------------------------------------------
# the shipped template
# ---------------------------------------------------------------------------


def test_the_shipped_template_exists_and_loads_under_birth_rules() -> None:
    """Genesis copies this file into every node; if it does not load, no node
    is born with a valid metabolism."""
    cadence = load_cadence(TEMPLATE, birth=True)
    assert [s.id for s in cadence.stages] == list(STAGE_IDS)


def test_every_stage_ships_disabled_and_null_seamed() -> None:
    cadence = load_cadence(TEMPLATE, birth=True)
    assert cadence.enabled_stages == ()
    assert {s.seam for s in cadence.stages} == {"null"}
    # a stage carrying a tagout would be a stage deliberately switched off with
    # a stated way back; at birth nothing was ever on, so nothing is tagged out
    assert all(s.tagout is None for s in cadence.stages)


def test_no_stage_may_declare_ok_on_empty() -> None:
    """The whole point: there is no configuration that renders zero green."""
    assert "ok" not in cadence_mod.ON_EMPTY
    cadence = load_cadence(TEMPLATE, birth=True)
    assert {s.on_empty for s in cadence.stages} <= set(cadence_mod.ON_EMPTY)


def test_the_template_is_the_one_genesis_copies() -> None:
    """The organ seeder and the test read the SAME path.

    A second copy of the cadence somewhere else would drift, and only one of
    the two would be reviewed.
    """
    assert (REPO_ROOT / organs_mod.METABOLISM_CADENCE_TEMPLATE) == TEMPLATE


# ---------------------------------------------------------------------------
# cadence loading: one break at a time
# ---------------------------------------------------------------------------


def _good_doc() -> Dict[str, Any]:
    return yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))


def _halts(doc: Any, needle: str, *, birth: bool = False) -> None:
    with pytest.raises(CadenceError) as exc:
        parse_cadence(doc, birth=birth)
    assert needle in (exc.value.reason + " " + exc.value.remedy), exc.value.reason


def test_a_missing_cadence_file_halts_rather_than_reading_as_empty(tmp_path) -> None:
    with pytest.raises(CadenceError) as exc:
        load_cadence(tmp_path / "nope.yaml")
    assert "missing" in exc.value.reason


def test_enabled_at_birth_halts() -> None:
    doc = _good_doc()
    doc["stages"][0]["enabled"] = True
    _halts(doc, "born disabled", birth=True)


def test_a_string_enabled_is_refused_not_coerced() -> None:
    doc = _good_doc()
    doc["stages"][0]["enabled"] = "false"
    _halts(doc, "not a boolean")


def test_a_tier_ceiling_above_t2_halts() -> None:
    doc = _good_doc()
    doc["stages"][0]["tier_ceiling"] = "T3"
    _halts(doc, "human-gated")


def test_a_stage_with_no_countable_halts() -> None:
    doc = _good_doc()
    doc["stages"][1]["produces"] = []
    _halts(doc, "countable")


def test_a_missing_stage_halts() -> None:
    doc = _good_doc()
    del doc["stages"][2]
    _halts(doc, "four stages")


def test_reordered_stages_halt() -> None:
    doc = _good_doc()
    doc["stages"][0], doc["stages"][3] = doc["stages"][3], doc["stages"][0]
    _halts(doc, "ordered")


def test_an_undeclared_field_halts_rather_than_being_ignored() -> None:
    doc = _good_doc()
    doc["stages"][0]["tier-ceiling"] = "T1"
    _halts(doc, "undeclared")


def test_a_missing_load_bearing_field_halts_and_is_never_defaulted() -> None:
    for field in ("purpose", "seam", "produces", "on_empty", "tier_ceiling"):
        doc = _good_doc()
        del doc["stages"][0][field]
        _halts(doc, "missing required field")


def test_an_unknown_schema_is_refused() -> None:
    doc = _good_doc()
    doc["schema"] = "metabolism-cadence/v2"
    _halts(doc, "does not know")


def test_a_cadence_with_no_as_of_halts() -> None:
    doc = _good_doc()
    del doc["as_of"]
    _halts(doc, "as_of")


# ---------------------------------------------------------------------------
# the verdict rule: WARN on zero output, never OK
# ---------------------------------------------------------------------------


def _enabled_cadence():
    doc = _good_doc()
    for stage in doc["stages"]:
        stage["enabled"] = True
    return parse_cadence(doc)


def test_a_stage_that_produced_nothing_renders_warn_never_ok() -> None:
    run = run_cadence(_enabled_cadence())
    assert [s.verdict for s in run.stages] == ["WARN"] * 4
    assert run.posture == "WARN"
    assert "OK" not in {s.verdict for s in run.stages}


def test_the_warning_says_why_rather_than_being_a_bare_status() -> None:
    run = run_cadence(_enabled_cadence())
    assert any("zero output is a state" in note
               for note in run.stages[0].notes)


def test_a_stage_that_produced_something_renders_ok() -> None:
    run = run_cadence(_enabled_cadence(),
                      {"absorb": lambda st: {"absorbed": 2}})
    assert run.stages[0].verdict == "OK"
    assert run.produced["absorbed"] == 2


def test_a_disabled_stage_is_skipped_and_that_is_not_a_warning() -> None:
    """An INTENTIONAL skip may be quiet; an unexpected failure may not."""
    run = run_cadence(load_cadence(TEMPLATE, birth=True))
    assert [s.verdict for s in run.stages] == ["SKIPPED"] * 4
    assert run.posture == "SKIPPED"


def test_an_unreadable_input_is_degraded_and_outranks_a_zero() -> None:
    def boom(_stage: Stage) -> Mapping[str, int]:
        raise OSError("the corpus directory is unreadable")

    run = run_cadence(_enabled_cadence(), {"distill": boom})
    assert run.stages[1].verdict == "DEGRADED"
    assert run.posture == "DEGRADED"


def test_a_declared_seam_with_no_runner_is_degraded_not_a_silent_no_op() -> None:
    doc = _good_doc()
    for stage in doc["stages"]:
        stage["enabled"] = True
    doc["stages"][0]["seam"] = "command"
    doc["stages"][0]["command"] = "python -m nothing"
    run = run_cadence(parse_cadence(doc))
    assert run.stages[0].verdict == "DEGRADED"


def test_on_empty_halt_stops_the_run_and_records_the_rest() -> None:
    doc = _good_doc()
    for stage in doc["stages"]:
        stage["enabled"] = True
    doc["stages"][0]["on_empty"] = "halt"
    run = run_cadence(parse_cadence(doc))
    assert run.stages[0].verdict == "HALT"
    # the stages after a halt are RECORDED as skipped, never omitted
    assert len(run.stages) == 4
    assert all("not reached" in " ".join(s.notes) for s in run.stages[1:])


def test_the_null_stage_records_intent_rather_than_vanishing() -> None:
    null = NullStage()
    run_cadence(_enabled_cadence(), {sid: null for sid in STAGE_IDS})
    assert [i["stage"] for i in null.intents] == list(STAGE_IDS)
    assert all(i["would_produce"] for i in null.intents)


def test_a_declared_countable_the_runner_omitted_is_recorded_as_zero() -> None:
    run = run_cadence(_enabled_cadence(), {"absorb": lambda st: {}})
    assert run.stages[0].produced == {"absorbed": 0}
    assert any("no value for declared countable" in n
               for n in run.stages[0].notes)


def test_a_non_integer_count_is_degraded_not_coerced() -> None:
    run = run_cadence(_enabled_cadence(),
                      {"absorb": lambda st: {"absorbed": "many"}})
    assert run.stages[0].verdict == "DEGRADED"


# ---------------------------------------------------------------------------
# the heartbeat's three alarms
# ---------------------------------------------------------------------------


def _entry(date: str, **kw: Any) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "as_of": date + "T00:00:00Z", "date": date,
        "counts": {k: 0 for k in heartbeat_mod.COUNTABLES},
        "deltas": {k: 0 for k in heartbeat_mod.COUNTABLES},
        "absent": [], "unreadable": [],
        "registry_highest": None, "corpus_highest": 0,
    }
    row.update(kw)
    return row


def test_the_promotion_flatline_alarm_fires() -> None:
    history = [_entry(f"2026-01-0{d}") for d in range(1, 5)]
    kinds = {a.kind for a in check_alarms(history)}
    assert "promotion_flatline" in kinds


def test_the_promotion_flatline_alarm_is_quiet_while_crystallized_moves() -> None:
    history = [_entry(f"2026-01-0{d}",
                      deltas={**{k: 0 for k in heartbeat_mod.COUNTABLES},
                              "crystallized": 1})
               for d in range(1, 6)]
    kinds = {a.kind for a in check_alarms(history)}
    assert "promotion_flatline" not in kinds


def test_the_input_feed_dry_alarm_fires_on_its_own_longer_threshold() -> None:
    short = [_entry(f"2026-01-0{d}") for d in range(1, 4)]
    kinds = {a.kind for a in check_alarms(short)}
    # the two alarms are independent: the flat-line has already fired at 3
    # dates while the dry-feed threshold of 5 has not been reached
    assert "promotion_flatline" in kinds and "input_feed_dry" not in kinds
    longer = [_entry(f"2026-01-0{d}") for d in range(1, 7)]
    assert "input_feed_dry" in {a.kind for a in check_alarms(longer)}


def test_the_registry_drift_alarm_fires() -> None:
    history = [_entry("2026-01-01", registry_highest=3, corpus_highest=90)]
    alarms = check_alarms(history)
    assert any(a.kind == "registry_drift" for a in alarms)
    assert all(a.threshold for a in alarms), "every alarm names its threshold"


def test_an_absent_registry_is_not_a_maximal_lag() -> None:
    """Absent is not zero. A missing registry must not fire drift."""
    history = [_entry("2026-01-01", registry_highest=None, corpus_highest=90)]
    assert not any(a.kind == "registry_drift" for a in check_alarms(history))


def test_a_short_history_is_quiet_and_quiet_is_not_healthy() -> None:
    assert posture([])[0] == "QUIET"
    assert posture([_entry("2026-01-01")])[0] == "QUIET"


def test_degraded_outranks_every_improvement() -> None:
    history = [_entry(f"2026-01-0{d}",
                      deltas={**{k: 0 for k in heartbeat_mod.COUNTABLES},
                              "crystallized": 1, "absorbed": 1})
               for d in range(1, 4)]
    assert posture(history)[0] == "HEALTHY"
    history[-1]["unreadable"] = ["candidates: unreadable"]
    assert posture(history)[0] == "DEGRADED"


def test_the_heartbeat_journal_is_append_only_and_deltas_are_folded(tmp_path) -> None:
    root = tmp_path / "node"
    root.mkdir()
    append_reading(root, collect_counts(root), "2026-01-01T00:00:00Z")
    cryst = root / ".intentops" / "metabolism" / "crystallized"
    cryst.mkdir(parents=True)
    (cryst / "CRYST-042-a.md").write_text("x", encoding="utf-8")
    second = append_reading(root, collect_counts(root), "2026-01-02T00:00:00Z")
    assert second.deltas["crystallized"] == 1
    assert second.corpus_highest == 42
    assert len(load_history(root)) == 2
    # the first line is untouched by the second write
    lines = heartbeat_mod.store_path(root).read_text(
        encoding="utf-8").splitlines()
    assert json.loads(lines[0])["date"] == "2026-01-01"


def test_a_newborn_node_reports_absent_sources_not_unreadable_ones(tmp_path) -> None:
    counts = collect_counts(tmp_path)
    assert all(v == 0 for v in counts.counts.values())
    assert counts.absent and not counts.unreadable
    assert not counts.degraded


def test_an_unparseable_registry_is_unreadable_never_a_zero(tmp_path) -> None:
    reg = tmp_path / ".intentops" / "metabolism" / "registry.yaml"
    reg.parent.mkdir(parents=True)
    reg.write_text("highest_crystallized_id: [:\n", encoding="utf-8")
    counts = collect_counts(tmp_path)
    assert counts.degraded
    assert counts.registry_highest is None


def test_a_registry_missing_its_field_is_degraded_not_defaulted(tmp_path) -> None:
    reg = tmp_path / ".intentops" / "metabolism" / "registry.yaml"
    reg.parent.mkdir(parents=True)
    reg.write_text("something_else: 4\n", encoding="utf-8")
    assert collect_counts(tmp_path).degraded


def test_a_corrupt_journal_line_stays_in_the_denominator(tmp_path) -> None:
    root = tmp_path / "node"
    root.mkdir()
    append_reading(root, collect_counts(root), "2026-01-01T00:00:00Z")
    with heartbeat_mod.store_path(root).open("a", encoding="utf-8") as fh:
        fh.write("{ not json\n")
    history = load_history(root)
    assert len(history) == 2
    assert any("_corrupt" in row for row in history)


# ---------------------------------------------------------------------------
# the CRYST document contract
# ---------------------------------------------------------------------------


_EVIDENCE = [{"grade": "OBSERVED",
              "claim": "a stage reported success over zero output for six days",
              "citation": "the cadence run records for those dates"}]


def _doc(**overrides: Any) -> str:
    kwargs: Dict[str, Any] = {
        "pattern": "A stage that ran and produced nothing renders WARN.",
        "station": "operative",
        "reopens_when": "a stage is found rendering OK at zero output",
        "evidence": _EVIDENCE,
        "title": "Zero output is a state",
        "as_of": "2026-09-06",
    }
    kwargs.update(overrides)
    return render_template("CRYST-001", **kwargs)


def test_a_complete_document_validates() -> None:
    doc, findings = validate(_doc())
    assert findings == []
    assert doc is not None and doc.station == "operative"
    assert doc.strongest_grade == "OBSERVED"


def test_the_blank_template_is_deliberately_invalid() -> None:
    """A template that validated while empty would let a node crystallize
    nothing and count it."""
    doc, findings = validate(render_template("CRYST-002"))
    assert doc is None and findings


def test_a_document_with_no_reopens_when_is_refused() -> None:
    doc, findings = validate(_doc(reopens_when=""))
    assert doc is None
    assert any("reopens_when" in f for f in findings)


def test_reopens_when_missing_entirely_is_also_refused() -> None:
    text = _doc().replace(
        'reopens_when: "a stage is found rendering OK at zero output"\n', "")
    doc, findings = validate(text)
    assert doc is None
    assert any("missing required field 'reopens_when'" in f for f in findings)


def test_evidence_must_be_graded_and_cited() -> None:
    _, findings = validate(_doc(evidence=[{"grade": "", "claim": "c",
                                           "citation": "s"}]))
    assert any("grade" in f for f in findings)
    _, findings = validate(_doc(evidence=[{"grade": "OBSERVED", "claim": "c",
                                           "citation": ""}]))
    assert any("citation" in f for f in findings)


def test_empty_evidence_is_refused() -> None:
    doc, findings = validate(_doc(evidence=[]))
    assert doc is None
    assert any("evidence" in f for f in findings)


def test_an_unknown_station_is_refused_never_defaulted() -> None:
    doc, findings = validate(_doc(station="probably-described"))
    assert doc is None
    assert any("station" in f for f in findings)


def test_a_malformed_id_is_refused() -> None:
    text = render_template("CRYST-7", pattern="p", station="described",
                           reopens_when="r", evidence=_EVIDENCE)
    doc, findings = validate(text)
    assert doc is None and any("CRYST-<n>" in f for f in findings)


def test_a_duplicate_id_is_refused_against_a_supplied_set() -> None:
    doc, findings = validate(_doc(), known_ids={"CRYST-001"})
    assert doc is None and any("already exists" in f for f in findings)


def test_every_finding_is_collected_not_just_the_first() -> None:
    _, findings = validate(_doc(pattern="", reopens_when="", station="nope"))
    assert len(findings) >= 3


def test_an_unreadable_document_is_a_finding_not_an_absence(tmp_path) -> None:
    doc, findings = crystallize_mod.validate_file(tmp_path / "nothing.md")
    assert doc is None and findings


# ---------------------------------------------------------------------------
# the grok cycle recorder
# ---------------------------------------------------------------------------


def test_a_recorded_cycle_has_four_phases_in_order() -> None:
    cycle = record_cycle("the metabolism")
    assert [p.name for p in cycle.phases] == list(PHASES)


def test_a_recorded_cycle_never_claims_a_phase_ran() -> None:
    """A plan is not a finding, and this is structural rather than a
    reviewer's job to notice."""
    cycle = record_cycle()
    assert cycle.posture == "DRY-RUN"
    assert all(not p.executed for p in cycle.phases)
    assert all(p.verdict == "PLANNED" for p in cycle.phases)
    assert cycle.findings == []


def test_an_executed_phase_with_no_findings_reads_warn_not_ok() -> None:
    phase = grok_mod.PhaseRecord("curate", "p", executed=True)
    assert phase.verdict == "WARN"
    phase.findings.append("a real observation")
    assert phase.verdict == "OK"


def test_a_misspelled_phase_halts_rather_than_being_ignored() -> None:
    with pytest.raises(GrokError):
        record_cycle("x", {"curat": ["config/"]})


def test_forge_is_one_cycle_per_slice_and_declares_the_verify_invariant() -> None:
    plan = plan_forge(["governance", "knowledge", "operations"])
    assert len(plan.cycles) == 3
    assert "INDEPENDENT" in plan.verify_invariant
    assert plan.to_row()["executed"] is False


def test_a_forge_over_zero_slices_halts() -> None:
    """An empty fan-out that returns a well-formed result is how a run
    'succeeds' having done nothing."""
    with pytest.raises(GrokError):
        plan_forge([])


# ---------------------------------------------------------------------------
# the four verbs, and the artifact boundary
# ---------------------------------------------------------------------------


def _request(**kw: Any) -> Request:
    base: Dict[str, Any] = {"verb": "inspiration", "source": "another system",
                            "payload_kind": "descriptor"}
    base.update(kw)
    return Request(**base)


def test_inspiration_is_always_free() -> None:
    assert evaluate(_request()).verdict == "PERMIT"


def test_adoption_needs_a_stated_confidence() -> None:
    with pytest.raises(AssimilationError) as exc:
        evaluate(_request(verb="adoption"))
    assert "confidence" in exc.value.reason


def test_adoption_below_the_threshold_is_refused() -> None:
    assert evaluate(_request(verb="adoption", confidence=1.0)).verdict == "REFUSE"


def test_adoption_of_governing_infrastructure_is_a_proposal_never_a_self_edit() -> None:
    decision = evaluate(_request(verb="adoption", confidence=5.0,
                                 governing_infrastructure=True))
    assert decision.verdict == "PROPOSE"
    assert "approval queue" in " ".join(decision.owed)


def test_integration_on_the_core_surface_is_reviewed_before_it_lands() -> None:
    decision = evaluate(_request(verb="integration", core_surface=True))
    assert decision.verdict == "REVIEW"
    assert any("values-council" in o for o in decision.owed)


def test_capture_is_refused_and_is_not_gated() -> None:
    decision = evaluate(_request(verb="capture"))
    assert decision.verdict == "REFUSE"
    assert "identity" in decision.reason


def test_the_word_assimilation_resolves_to_the_forbidden_verb_and_says_so() -> None:
    decision = evaluate(_request(verb="assimilation"))
    assert decision.verb is Verb.CAPTURE
    assert decision.verdict == "REFUSE"
    assert any("FORBIDDEN" in note for note in decision.notes)


@pytest.mark.parametrize("kind", list(ARTIFACT_KINDS))
@pytest.mark.parametrize("verb", ["inspiration", "adoption", "integration",
                                  "capture"])
def test_an_executable_artifact_is_refused_at_every_verb(verb, kind) -> None:
    """The type boundary is checked before the verb, so no verb -- not even
    inspiration -- can carry an artifact past it."""
    decision = evaluate(_request(verb=verb, payload_kind=kind, confidence=9.0))
    assert decision.verdict == "REFUSE"
    assert "ARTIFACT" in decision.reason


def test_the_refusal_names_the_permitted_alternative() -> None:
    decision = evaluate(_request(payload_kind="skill"))
    assert any("BY HAND" in o for o in decision.owed)


def test_an_undeclared_payload_kind_halts_rather_than_defaulting_to_harmless() -> None:
    with pytest.raises(AssimilationError) as exc:
        evaluate(_request(payload_kind="vibes"))
    assert "undeclared" in exc.value.reason


def test_an_unknown_verb_halts() -> None:
    with pytest.raises(AssimilationError):
        evaluate(_request(verb="borrow"))


def test_a_descriptor_is_permitted_because_facts_are_not_artifacts() -> None:
    for kind in ("descriptor", "capability_list", "measurement", "observation",
                 "none"):
        assert evaluate(_request(payload_kind=kind)).verdict == "PERMIT"


# ---------------------------------------------------------------------------
# birth: the organs, the probes, and the loop charter example
# ---------------------------------------------------------------------------


def test_both_metabolism_stores_are_birth_organs_with_declared_write_models() -> None:
    by_id = {o.id: o for o in organs_mod.BIRTH_ORGANS}
    assert by_id["metabolism-cadence"].write_model == "single-writer-ceremony"
    assert by_id["metabolism-heartbeat"].write_model == "append-only-jsonl"
    assert all(by_id[k].consumer for k in ("metabolism-cadence",
                                           "metabolism-heartbeat"))


def test_the_heartbeat_organ_path_matches_the_module_that_writes_it() -> None:
    by_id = {o.id: o for o in organs_mod.BIRTH_ORGANS}
    assert (by_id["metabolism-heartbeat"].relpath
            == heartbeat_mod.STORE_RELPATH.as_posix())


def test_the_cadence_organ_path_matches_the_cli_default() -> None:
    by_id = {o.id: o for o in organs_mod.BIRTH_ORGANS}
    assert (by_id["metabolism-cadence"].relpath
            == metabolism_cli.DEFAULT_CADENCE_RELPATH.as_posix())


def test_genesis_seeds_a_valid_disabled_cadence_into_a_node(tmp_path) -> None:
    target = tmp_path / "cadence.yaml"
    organs_mod._seed_metabolism_cadence(target, REPO_ROOT)
    cadence = load_cadence(target, birth=True)
    assert cadence.enabled_stages == ()


def test_a_missing_template_halts_rather_than_synthesising_a_cadence(tmp_path) -> None:
    from intentops_core.genesis import Halt

    with pytest.raises(Halt):
        organs_mod._seed_metabolism_cadence(tmp_path / "out.yaml",
                                            tmp_path / "empty-repo")


def test_every_metabolism_probe_names_a_truth_path_that_exists() -> None:
    doc = yaml.safe_load(
        (REPO_ROOT / "config" / "genesis-probes.yaml").read_text(encoding="utf-8"))
    probes = [p for p in doc["probes"] if p.get("category") == "metabolism"]
    assert len(probes) >= 5, "new organ, new probe"
    for probe in probes:
        assert probe.get("expect_in_boot"), probe["id"]
        for truth in probe.get("truth") or []:
            path = REPO_ROOT / truth["path"]
            assert path.is_file(), f"{probe['id']}: {truth['path']}"
            assert truth["contains"] in path.read_text(encoding="utf-8"), \
                f"{probe['id']}: {truth['contains']!r} not in {truth['path']}"


def test_the_loop_charter_example_is_commented_out_and_born_disabled() -> None:
    text = (REPO_ROOT / "config" / "loop-charters.template.yaml").read_text(
        encoding="utf-8")
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if "nightly-metabolism" in line)
    end = next(i for i, line in enumerate(lines)
               if i > start and line.startswith("schema:"))
    example = lines[start:end]
    assert any("enabled: false" in line for line in example)
    # the whole example block is prose: every line of it is commented out, so
    # the shipped file still parses to `entries: []`
    for line in example:
        assert not line.strip() or line.lstrip().startswith("#"), line
    doc = yaml.safe_load(text)
    assert doc["entries"] == []


def test_every_module_selftest_passes() -> None:
    """A detector that has never fired is indistinguishable from a broken one."""
    ok, report = metabolism_cli.selftest()
    assert ok, report
