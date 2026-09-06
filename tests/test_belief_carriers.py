"""Tests for the belief-carrier binding, the probe-coverage audit, and the
responsiveness instrument -- the three things that close the G7 WARN and make
the house rule (*new organ, new probe, same session*) mechanical.

Every test runs against a TEMPORARY node root or a synthetic input. The
repository is read, never written; no live service is contacted.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

from intentops_core.genesis import aliveness as aliveness_mod
from intentops_core.genesis import machine as machine_mod
from intentops_core.genesis import organs as organs_mod
from intentops_core.validation import belief_carriers as bc
from intentops_core.validation import responsiveness as rp
from intentops_core.validation import still_true as st

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / bc.TEMPLATE_RELPATH


def _load_script(relpath: str, name: str):
    """Import a `scripts/` module by path -- they are tools, not a package."""
    spec = importlib.util.spec_from_file_location(name, REPO / relpath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


coverage_mod = _load_script("scripts/genesis/probe_coverage_check.py",
                            "probe_coverage_check")


# ---------------------------------------------------------------------------
# 1. the belief-carrier specification
# ---------------------------------------------------------------------------


def test_belief_carriers_selftest_passes():
    ok, message = bc.selftest()
    assert ok, message


def test_the_shipped_template_loads_and_binds_the_two_birth_carriers():
    binding = bc.load_binding(TEMPLATE)
    assert binding.schema == bc.SCHEMA
    assert [s.id for s in binding.sources] == ["node-orderings",
                                              "founding-conversation"]
    kinds = {s.kind for s in binding.sources}
    assert kinds == {"ruling", "briefing"}
    # The population is never empty by construction: that is the whole point.
    assert binding.sources, "a template binding nothing scores a perfect reading"


def test_the_template_declares_every_kind_with_the_extractor_that_runs():
    doc = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    assert set(doc["kinds"]) == set(bc.KINDS)
    for kind, spec in doc["kinds"].items():
        assert spec["as_of"] == bc.AS_OF_EXTRACTORS[kind]


@pytest.mark.parametrize("drop", ["base", "version_control", "falsifiers",
                                  "kind", "path", "id"])
def test_a_missing_load_bearing_field_halts(tmp_path, drop):
    doc = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    doc["sources"][0].pop(drop, None)
    path = tmp_path / "b.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(bc.BeliefCarrierError):
        bc.load_binding(path)


def test_a_falsifier_that_could_never_fire_is_refused(tmp_path):
    """EVIDENCE-MOVED is read from commit history and from nothing else."""
    doc = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    doc["sources"][1]["falsifiers"] = ["UNDATED", "EVIDENCE-MOVED"]
    assert doc["sources"][1]["version_control"] == "none"
    path = tmp_path / "b.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(bc.BeliefCarrierError, match="EVIDENCE-MOVED"):
        bc.load_binding(path)


def test_conflict_is_refused_on_a_carrier_that_has_no_ordering_store(tmp_path):
    doc = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    doc["sources"][1]["falsifiers"] = ["UNDATED", "CONFLICT"]
    path = tmp_path / "b.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(bc.BeliefCarrierError, match="cannot be"):
        bc.load_binding(path)


def test_bind_template_validates_before_it_writes(tmp_path):
    bad = tmp_path / "bad.yaml"
    doc = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    doc["sources"] = []
    bad.write_text(yaml.safe_dump(doc), encoding="utf-8")
    dest = tmp_path / "out" / "belief-carriers.yaml"
    with pytest.raises(bc.BeliefCarrierError):
        bc.bind_template(bad, dest)
    assert not dest.exists(), "a refused template must leave nothing on disk"


def test_bind_template_stamps_bound_at_and_keeps_the_reviewed_as_of(tmp_path):
    dest = tmp_path / ".intentops" / "config" / "belief-carriers.yaml"
    binding = bc.bind_template(TEMPLATE, dest)
    assert dest.is_file()
    written = yaml.safe_load(dest.read_text(encoding="utf-8"))
    assert written["bound_at"], "the node did not record when it bound this"
    assert written["as_of"] == binding.as_of, (
        "as_of is when the specification was last REVIEWED; copying it did "
        "not review it again")


def test_an_absent_source_stays_in_the_count_rather_than_vanishing(tmp_path):
    """A refusal must be a value the consumer counts, never an absence."""
    node = tmp_path / "node"
    (node / ".intentops" / "wisdom").mkdir(parents=True)
    (node / ".intentops" / "wisdom" / "orderings.jsonl").write_text(
        "", encoding="utf-8")
    binding = bc.load_binding(TEMPLATE)
    reading = bc.take_bound_reading(binding, node_root=node,
                                    identity_repo=tmp_path / "nowhere")
    assert reading.unreadable, "a missing carrier left the denominator"
    assert any("founding-conversation" in u for u in reading.unreadable)


def test_memory_topics_are_dated_from_the_filename(tmp_path):
    topics = tmp_path / "topics"
    topics.mkdir()
    (topics / "a_2026-09-01.md").write_text("x\n", encoding="utf-8")
    (topics / "no-date.md").write_text("x\n", encoding="utf-8")
    beliefs = bc.read_memory_topics(topics, tmp_path, st.Tracked(()))
    by_date = {b.as_of for b in beliefs}
    assert by_date == {"2026-09-01", None}
    # The undated one stays IN the population and says so.
    questions = st.evaluate(beliefs, {})
    assert [q.kind for q in questions] == ["UNDATED"]


def test_the_bound_reading_fills_every_field_still_true_declares(tmp_path):
    """Drift pin: if `still_true.Reading` grows a field, this reading must set
    it too, or the bound reading silently under-reports."""
    node = tmp_path / "node"
    (node / ".intentops" / "wisdom").mkdir(parents=True)
    (node / ".intentops" / "wisdom" / "orderings.jsonl").write_text(
        "", encoding="utf-8")
    idr = tmp_path / "idr" / "identity"
    idr.mkdir(parents=True)
    (idr / "founding-conversation.md").write_text(
        "as_of: 2026-09-06\n\n# Founding conversation\n", encoding="utf-8")
    reading = bc.take_bound_reading(bc.load_binding(TEMPLATE), node_root=node,
                                    identity_repo=tmp_path / "idr")
    default = st.Reading(as_of="x")
    for field in dataclasses.fields(st.Reading):
        if field.name == "as_of":
            continue
        assert hasattr(reading, field.name), field.name
    # and the reading is the honest one the birth check reports
    assert bc.summary_line(reading) == "1 carriers, 1 dated, 0 open questions"
    assert not reading.unreadable
    assert reading.blind_spots and default.blind_spots == []


# ---------------------------------------------------------------------------
# 2. genesis binds it at G3 and reads it at G7
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dry_run(tmp_path_factory):
    node = tmp_path_factory.mktemp("bc-node")
    run = machine_mod.run_genesis(
        REPO, node, identity_repo="new", dry_run=True,
        allow_unsigned_dev=True, saddle="claudecode",
        isatty=lambda: False, out=lambda _m: None)
    return run, node


def test_g3_binds_the_belief_carriers(dry_run):
    _run, node = dry_run
    bound = node / bc.BINDING_RELPATH
    assert bound.is_file(), "G3 did not bind a belief-carrier specification"
    binding = bc.load_binding(bound)
    assert len(binding.sources) == 2


def test_the_founding_conversation_carries_a_machine_readable_as_of(dry_run):
    """A birth text that tells a node every belief must state when it was last
    true has to model both halves."""
    _run, node = dry_run
    text = (node / "identity-repo" / "identity"
            / "founding-conversation.md").read_text(encoding="utf-8")
    assert st._FRONT_AS_OF.search(text), text[:120]


def test_g7_answers_check_two_with_an_honest_reading(dry_run):
    _run, node = dry_run
    rows = [json.loads(x) for x in
            (node / aliveness_mod.ALIVENESS_RELPATH).read_text(
                encoding="utf-8").splitlines() if x.strip()]
    check = [a for a in rows[-1]["answers"] if a["number"] == 2][0]
    assert check["status"] == "ANSWERED", check["detail"]
    assert check["counts"]["open_questions"] == 0
    assert check["counts"]["population"] >= 1
    assert "carriers" in check["detail"] and "dated" in check["detail"]
    # Check 2 specifically must no longer be a named absent faculty. The whole
    # reading's verdict is not asserted here: it folds five other checks, and a
    # failure in one of those is that check's finding, not this one's.
    assert not any("still_true" in f for f in rows[-1]["faculties_absent"]), \
        rows[-1]["faculties_absent"]


def test_the_birth_reading_is_recorded_in_the_still_true_ledger(dry_run):
    _run, node = dry_run
    ledger = node / st.LEDGER_RELPATH
    lines = [x for x in ledger.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert lines, "capture without a consumer is not retention; nothing was written"
    assert json.loads(lines[-1])["open_questions"] == 0


def test_an_unbound_node_still_answers_unprobeable(tmp_path):
    """The old answer survives verbatim for a node with no binding -- an empty
    population would otherwise score a perfect reading."""
    answer = aliveness_mod._check_belief_currency(tmp_path)
    assert answer.status == "UNPROBEABLE"
    assert "no belief-carrier specification is bound at birth" in answer.detail
    assert answer.counts["population"] == 0


def test_check_two_can_actually_fail(dry_run, tmp_path):
    """A detector that has never fired is indistinguishable from a broken one."""
    _run, node = dry_run
    stale = tmp_path / "stale-node"
    (stale / ".intentops" / "wisdom").mkdir(parents=True)
    (stale / ".intentops" / "wisdom" / "orderings.jsonl").write_text(
        "", encoding="utf-8")
    bc.bind_template(TEMPLATE, stale / bc.BINDING_RELPATH)
    idr = tmp_path / "stale-idr" / "identity"
    idr.mkdir(parents=True)
    # a carrier with NO as-of: the UNDATED falsifier must trip
    (idr / "founding-conversation.md").write_text(
        "# Founding conversation\n\nno stamp at all.\n", encoding="utf-8")
    answer = aliveness_mod._check_belief_currency(
        stale, REPO, tmp_path / "stale-idr")
    assert answer.status == "FAILED", answer.detail
    assert "stale" in answer.detail


def test_a_binding_that_does_not_load_fails_rather_than_reading_absent(tmp_path):
    bound = tmp_path / bc.BINDING_RELPATH
    bound.parent.mkdir(parents=True)
    bound.write_text("schema: nope\n", encoding="utf-8")
    answer = aliveness_mod._check_belief_currency(tmp_path)
    assert answer.status == "FAILED"
    assert "REFUSED by its own loader" in answer.detail


# ---------------------------------------------------------------------------
# 3. probe coverage -- new organ, new probe
# ---------------------------------------------------------------------------


def test_probe_coverage_selftest_passes():
    ok, message = coverage_mod.selftest()
    assert ok, message


def test_every_organ_in_this_repository_has_a_probe_row():
    cov = coverage_mod.run(REPO)
    assert not cov.errors, cov.errors
    assert not cov.orphan_covers, cov.orphan_covers
    assert not cov.missing, [m.id for m in cov.missing]
    assert cov.satisfied == cov.owed and cov.owed > 0


def test_every_birth_organ_is_in_the_population():
    cov = coverage_mod.run(REPO)
    ids = {m.id for m in cov.members}
    for organ in organs_mod.BIRTH_ORGANS:
        assert organ.id in ids, organ.id


def test_a_planted_unprobed_organ_is_caught():
    """The point of the instrument, exercised against a synthetic population."""
    doc = {"probes": [{"id": "P", "covers": ["known"]}]}
    cov = coverage_mod.evaluate(["known", "newborn"], [], doc)
    assert [m.id for m in cov.missing] == ["newborn"]
    assert not cov.ok


def test_an_exemption_without_a_reason_is_an_error():
    doc = {"probes": [{"id": "P", "covers": ["a"]}],
           "coverage_exemptions": {"b": ""}}
    cov = coverage_mod.evaluate(["a", "b"], [], doc)
    assert any("no reason" in e for e in cov.errors)


def test_a_name_that_is_both_organ_and_package_is_counted_once():
    pkgs = [coverage_mod.Member("wisdom", "package", "owns a store")]
    cov = coverage_mod.evaluate(["wisdom"], pkgs,
                                {"probes": [{"id": "P", "covers": ["wisdom"]}]})
    assert cov.owed == 1
    assert [m.population for m in cov.members] == ["birth-organ+package"]


def test_the_probe_suite_still_passes_over_this_repository():
    """Adding probe rows is only half of it: their facts must reach a window."""
    from intentops_core.continuity import self_probe

    suite = self_probe.run_suite(REPO, REPO / coverage_mod.PROBES_RELPATH)
    bad = [(r.id, r.status, r.detail) for r in suite.results if r.status != "PASS"]
    assert not bad, bad


def test_the_boot_anchor_index_is_in_the_boot_corpus():
    doc = yaml.safe_load(
        (REPO / coverage_mod.PROBES_RELPATH).read_text(encoding="utf-8"))
    assert "docs/BOOT-ANCHORS.md" in doc["boot_corpus"]["workspace"]
    assert (REPO / "docs" / "BOOT-ANCHORS.md").is_file()


# ---------------------------------------------------------------------------
# 4. responsiveness -- did a tripped falsifier get an answer?
# ---------------------------------------------------------------------------


def _write_run(path: Path, as_of: str, keys) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"as_of": as_of, "verdict": "CURRENT",
                             "question_ids": list(keys)}) + "\n")


def _write_events(path: Path, rows) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


@pytest.fixture
def node(tmp_path):
    (tmp_path / st.LEDGER_RELPATH).parent.mkdir(parents=True)
    (tmp_path / rp.ORDERING_RELPATH).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / st.LEDGER_RELPATH).write_text("", encoding="utf-8")
    (tmp_path / rp.ORDERING_RELPATH).write_text("", encoding="utf-8")
    return tmp_path


def test_responsiveness_selftest_passes():
    ok, message = rp.selftest()
    assert ok, message


def test_a_fresh_node_is_not_yet_armed_rather_than_healthy(node):
    reading = rp.take_reading(node_root=node)
    verdict, _notes = rp.posture(reading, [])
    assert verdict == "NOT-YET-ARMED"
    assert reading.responsiveness is None, "an undefined rate read as a number"


def test_a_silent_close_is_surfaced(node):
    _write_events(node / rp.ORDERING_RELPATH,
                  [{"event": "rule", "id": "ORD-1", "date": "2026-09-01"}])
    _write_run(node / st.LEDGER_RELPATH, "2026-09-05T00:00:00+00:00",
               ["UNDATED:ORD-1"])
    _write_run(node / st.LEDGER_RELPATH, "2026-09-06T00:00:00+00:00", [])
    reading = rp.take_reading(node_root=node)
    assert reading.silent_close == 1 and reading.answered == 0
    verdict, notes = rp.posture(reading, [])
    assert verdict == "ATTENTION"
    assert any("silent close" in n for n in notes)


def test_reaffirm_and_supersede_answer_but_reopen_does_not(node):
    _write_events(node / rp.ORDERING_RELPATH, [
        {"event": "rule", "id": "A", "date": "2026-09-01"},
        {"event": "rule", "id": "B", "date": "2026-09-01"},
        {"event": "rule", "id": "C", "date": "2026-09-01"},
        {"event": "supersede", "id": "A", "superseded_by": "A2",
         "at": "2026-09-06T00:00:00+00:00"},
        {"event": "reaffirm", "id": "B", "reason": "still holds",
         "at": "2026-09-06T00:00:00+00:00"},
        {"event": "reopen", "id": "C", "reason": "re-ask",
         "at": "2026-09-06T00:00:00+00:00"},
    ])
    _write_run(node / st.LEDGER_RELPATH, "2026-09-05T00:00:00+00:00",
               ["UNDATED:A", "UNDATED:B", "UNDATED:C"])
    _write_run(node / st.LEDGER_RELPATH, "2026-09-07T00:00:00+00:00", [])
    reading = rp.take_reading(node_root=node)
    assert reading.answered_amend == 1
    assert reading.answered_continue == 1
    assert reading.reopened == 1
    assert reading.silent_close == 0
    assert reading.responsiveness == pytest.approx(2 / 3)


def test_a_question_with_no_answer_channel_never_reads_as_unanswered(node):
    _write_run(node / st.LEDGER_RELPATH, "2026-09-05T00:00:00+00:00",
               ["UNDATED:docs/brief.md"])
    reading = rp.take_reading(node_root=node)
    assert reading.no_channel == 1
    assert reading.channelled == 0
    assert reading.responsiveness is None
    assert rp.posture(reading, [])[0] == "NOT-YET-ARMED"


def test_an_unreadable_source_degrades_and_stays_in_the_denominator(node):
    (node / st.LEDGER_RELPATH).write_text("{not json\n", encoding="utf-8")
    reading = rp.take_reading(node_root=node)
    verdict, notes = rp.posture(reading, [])
    assert verdict == "DEGRADED"
    assert any("corrupt" in u for u in reading.unreadable)


def test_the_timidity_pair_is_published_beside_the_rate(node):
    _write_events(node / rp.ORDERING_RELPATH, [
        {"event": "rule", "id": "A", "date": "2026-09-01"},
        {"event": "rule", "id": "B", "date": "2026-09-03"},
    ])
    reading = rp.take_reading(node_root=node)
    assert reading.rulings == 2
    assert reading.decision_volume, "the pair is published, never omitted"
    text = rp.render(reading, "NOT-YET-ARMED", [])
    assert "never a verdict" in text


def test_the_ledger_round_trips_and_an_undefined_rate_stays_undefined(node):
    reading = rp.take_reading(node_root=node)
    ledger = rp.default_ledger(node)
    rp.append_reading(reading, "NOT-YET-ARMED", ledger)
    rows = rp.load_history(ledger)
    assert len(rows) == 1
    assert rows[0]["responsiveness_pct"] is None
    assert rows[0]["verdict"] == "NOT-YET-ARMED"


def test_the_responsiveness_organ_is_declared_at_birth():
    organ = [o for o in organs_mod.BIRTH_ORGANS if o.id == "responsiveness"][0]
    assert organ.relpath == rp.LEDGER_RELPATH.as_posix()
    assert organ.write_model == "append-only-jsonl"
