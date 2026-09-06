"""Tests for core lift B: the validation family, self_probe, approvals, LOTO,
the wish-register validator, and substrate detection.

Every instrument in this slice ships a ``--selftest`` that proves it can fire,
because a detector that has never fired is indistinguishable from a broken one.
These tests do two things: run every one of those selftests (so a selftest that
silently stops firing is a red test rather than a quiet green), and then pin the
specific behaviours whose absence has cost something before -- an expiry that
destroys a ruling, a T3 tagout with no way back, a probe suite scoring 100% over
zero probes, a register naming a carrier that does not exist.

No live services, no network, no model calls. Everything runs against temporary
directories.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Tuple

import pytest
import yaml
from intentops_core.approvals import council, queue
from intentops_core.continuity import self_probe
from intentops_core.loto import ledger as loto
from intentops_core.substrate import hardware_detector, mode_detector
from intentops_core.validation import grounded_signals, still_true, witness

REPO = Path(__file__).resolve().parents[1]


def _load_script(relpath: str, module_name: str):
    """Load a ``scripts/`` entry point as a module -- they are not a package."""
    path = REPO / relpath
    assert path.is_file(), f"script not found: {path}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


wish_register_check = _load_script("scripts/wish_register_check.py",
                                   "_test_wish_register_check")


# ---------------------------------------------------------------------------
# every instrument's selftest must pass -- and must actually be exercising
# something
# ---------------------------------------------------------------------------

SELFTESTS: Tuple[Tuple[str, Callable[[], Tuple[bool, str]]], ...] = (
    ("grounded_signals", grounded_signals.selftest),
    ("witness", witness.selftest),
    ("still_true", still_true.selftest),
    ("self_probe", self_probe.selftest),
    ("approval council", council.selftest),
    ("approval queue", queue.selftest),
    ("loto ledger", loto.selftest),
    ("wish_register_check", wish_register_check.selftest),
)


@pytest.mark.parametrize("name,fn", SELFTESTS, ids=[n for n, _ in SELFTESTS])
def test_selftest_passes(name: str, fn: Callable[[], Tuple[bool, str]]) -> None:
    ok, message = fn()
    assert ok, f"{name} selftest FAILED: {message}"
    # A selftest whose message says nothing proved nothing. Guard against a
    # future edit hollowing one out into `return True, ""`.
    assert len(message) > 40, f"{name} selftest reports no coverage: {message!r}"


def test_every_lifted_instrument_ships_a_selftest() -> None:
    """The gap this slice closes: three of the four validation modules had no
    selftest at all, while a birth check depended on all of them."""
    for module in (grounded_signals, witness, still_true, self_probe, council,
                   queue, loto):
        assert callable(getattr(module, "selftest", None)), (
            f"{module.__name__} ships no selftest")


# ---------------------------------------------------------------------------
# self_probe: an empty suite is a refusal, not a perfect score
# ---------------------------------------------------------------------------


def _write_suite(tmp_path: Path, probes: Any, corpus: Any = None) -> Path:
    (tmp_path / "config").mkdir(exist_ok=True)
    path = tmp_path / "config" / "genesis-probes.yaml"
    path.write_text(yaml.safe_dump({
        "schema": "self-probe/v1",
        "boot_corpus": corpus if corpus is not None else {"workspace": []},
        "probes": probes,
    }, sort_keys=False), encoding="utf-8")
    return path


def test_self_probe_refuses_an_empty_suite(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="zero probes"):
        self_probe.run_suite(tmp_path, _write_suite(tmp_path, []))
    with pytest.raises(ValueError, match="zero probes"):
        self_probe.run_suite(tmp_path, _write_suite(tmp_path, None))


def test_self_probe_keeps_absent_and_fail_apart(tmp_path: Path) -> None:
    (tmp_path / "boot").mkdir()
    (tmp_path / "boot" / "anchors.md").write_text(
        "the gate is at src/gate.py\n", encoding="utf-8")
    suite = self_probe.run_suite(tmp_path, _write_suite(
        tmp_path,
        [{"id": "coverage-gap", "expect_in_boot": {"all_of": ["never written"]}},
         {"id": "truth-gap", "expect_in_boot": {"all_of": ["src/gate.py"]},
          "truth": [{"path": "src/gate.py", "contains": "x"}]}],
        corpus={"workspace": ["boot/anchors.md"]}))
    statuses = {r.id: r.status for r in suite.results}
    assert statuses["coverage-gap"] == "ABSENT"
    assert statuses["truth-gap"] == "FAIL"
    assert len(suite.coverage_failures) == 1
    assert len(suite.truth_failures) == 1
    # the rate never travels without its denominator
    report = suite.to_dict()
    assert report["population"] == 2 and report["passed"] == 0


def test_the_shipped_genesis_probe_suite_runs() -> None:
    """The suite genesis actually reads must satisfy this runner's contract."""
    probes_path = REPO / "config" / "genesis-probes.yaml"
    if not probes_path.is_file():
        pytest.skip("config/genesis-probes.yaml is another leg's file and is "
                    "not present in this tree")
    suite = self_probe.run_suite(REPO, probes_path)
    assert suite.results, "the shipped suite defines zero probes"
    for r in suite.results:
        assert r.status in self_probe.STATUSES, f"{r.id} -> {r.status}"


# ---------------------------------------------------------------------------
# approvals: the DECAY lens, and the expiry that must never destroy a ruling
# ---------------------------------------------------------------------------

_RULABLE = (
    "SITUATION: a cache directory has grown past its bound.\n"
    "THE ASK: approve removing the cache directory.\n"
    "IF APPROVED: it is removed and rebuilt on the next run; reversible.\n"
    "IF DENIED: it keeps growing.\n"
    "Verified by exit 0 on scripts/cache_probe.py:41.\n"
)


def _votes(item: Dict[str, Any], tier: str = "T1") -> Dict[str, str]:
    _verdict, votes = council.council_vote(item, tier)
    return {v.member: v.vote for v in votes}


def test_decay_lens_fires_on_a_stale_item() -> None:
    fresh = {"proposal_summary": "DECISION: remove the cache directory",
             "rationale": _RULABLE}
    assert _votes(fresh)["decay"] == "APPROVE"

    stale = dict(fresh)
    stale["notes"] = "this finding is 40 days old and may be stale"
    assert _votes(stale)["decay"] == "ESCALATE"

    superseded = dict(fresh)
    superseded["notes"] = "the plan it rests on has been superseded"
    assert _votes(superseded)["decay"] == "ESCALATE"


def test_decay_is_outvotable_but_reserved_is_not() -> None:
    stale = {"proposal_summary": "DECISION: remove the cache directory",
             "rationale": _RULABLE,
             "notes": "this finding is 40 days old"}
    verdict, _ = council.council_vote(stale, "T1")
    assert verdict == "APPROVED", "decay alone must not block a clean item"

    reserved = {"proposal_summary": "DECISION: write a calendar entry",
                "rationale": _RULABLE,
                "tool_call": {"tool": "calendar_create_event",
                              "params": {"subject": "sync"}}}
    verdict, votes = council.council_vote(reserved, "T1")
    approvals = sum(1 for v in votes if v.vote == "APPROVE")
    assert verdict == "ESCALATE", (
        f"a dispositive ESCALATE was outvoted by {approvals} approvals")


def test_rewording_prose_does_not_move_a_call_based_verdict() -> None:
    """The failure this closes: rewriting a request in better prose once
    flipped a council from ESCALATE to APPROVED without changing the action."""
    base = {"proposal_summary": "DECISION: write a calendar entry",
            "rationale": _RULABLE,
            "tool_call": {"tool": "calendar_create_event", "params": {}}}
    polished = dict(base, rationale=_RULABLE + "Impeccably argued.\n")
    assert (council.council_vote(base, "T1")[0]
            == council.council_vote(polished, "T1")[0] == "ESCALATE")


def test_expiry_preserves_decided_by(tmp_path: Path) -> None:
    """THE 181-of-182 WOUND.

    An expiry sweep once overwrote ``decided_by`` with ``system:expired``, so a
    store of grants the operator had personally made read afterwards as a store
    of questions that died unruled. An expiry is a decision nobody was present
    for: it may record that it happened, and it may not impersonate the person
    who was.
    """
    q = queue.ApprovalQueue(tmp_path)
    item = q.submit_decision(slug="rotate-key", tier="T3",
                             summary="rotate the signing key",
                             rationale=_RULABLE, expires_days=30)
    granted = q.approve(item.id, decided_by="the operator", comment="go ahead")
    assert granted.decided_by == "the operator"
    ruled_at = granted.decided_at

    # lapse the window exactly as a real grant lapses
    path = q.approved_dir / f"{granted.id}.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["expires_at"] = (datetime.now(timezone.utc)
                            - timedelta(days=1)).isoformat()
    path.write_text(json.dumps(record), encoding="utf-8")

    assert q.expire_stale() == [granted.id]

    after = q.get(granted.id)
    assert after is not None
    assert after.status == queue.ApprovalStatus.EXPIRED.value
    # the ruling survives, in both fields
    assert after.decided_by == "the operator", (
        "the expiry overwrote decided_by -- an expired grant now reads as a "
        "question that died unruled")
    assert after.decided_at == ruled_at
    # and the expiry records ITSELF, separately
    assert after.expired_by == "system:expired"
    assert after.expired_at
    assert after.lapsed_from == queue.ApprovalStatus.APPROVED.value

    # the history row carries the ruling too -- it is the copy of record
    expired_rows = [h for h in q.history() if h.get("event") == "expired"]
    assert expired_rows and expired_rows[-1]["decided_by"] == "the operator"


def test_expiry_of_an_unruled_item_leaves_decided_by_empty(tmp_path: Path) -> None:
    """The mirror case: an item that really WAS never ruled must not acquire a
    decider from its expiry either."""
    q = queue.ApprovalQueue(tmp_path)
    item = q.submit_decision(slug="never-seen", tier="T2", summary="s",
                             rationale=_RULABLE)
    path = q.pending_dir / f"{item.id}.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["expires_at"] = (datetime.now(timezone.utc)
                            - timedelta(days=1)).isoformat()
    path.write_text(json.dumps(record), encoding="utf-8")

    assert q.expire_stale() == [item.id]
    after = q.get(item.id)
    assert after is not None
    assert after.decided_by is None
    assert after.expired_by == "system:expired"
    assert after.lapsed_from == queue.ApprovalStatus.PENDING.value


def test_submit_decision_requires_an_explicit_positive_window(tmp_path: Path) -> None:
    q = queue.ApprovalQueue(tmp_path)
    with pytest.raises(queue.QueueError):
        q.submit_decision(slug="x", tier="T3", summary="s", rationale="r",
                          expires_days=0)


# ---------------------------------------------------------------------------
# LOTO: a safety control switched off with no way back is abandoned
# ---------------------------------------------------------------------------


def _ledger_at(tmp_path: Path) -> Tuple[Path, list]:
    path = tmp_path / loto.LEDGER_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    loto._write(path, loto.new_ledger())
    switch = tmp_path / "src" / "detector.py"
    switch.parent.mkdir(parents=True, exist_ok=True)
    switch.write_text("# TAGOUT: LOTO-2026-09-06-DETECTOR-OFF\nENABLED = False\n",
                      encoding="utf-8")
    return path, [{"path": "src/detector.py",
                   "probe": "LOTO-2026-09-06-DETECTOR-OFF"}]


@pytest.mark.parametrize("tier", ["T3", "T4"])
def test_loto_refuses_an_ungoverned_safety_tagout(tmp_path: Path, tier: str) -> None:
    path, carriers = _ledger_at(tmp_path)
    with pytest.raises(loto.LedgerError, match="reenergize_when"):
        loto.create_entry(path, "LOTO-2026-09-06-DETECTOR-OFF",
                          what="the detector", tier=tier, carriers=carriers,
                          by="node", authority="the operator",
                          reason="noisy during the migration")
    # nothing was written
    assert loto.load_ledger(path)["entries"] == []

    # a placeholder is not a way back either
    with pytest.raises(loto.LedgerError, match="reenergize_when"):
        loto.create_entry(path, "LOTO-2026-09-06-DETECTOR-OFF",
                          what="the detector", tier=tier, carriers=carriers,
                          by="node", authority="the operator", reason="noisy",
                          reenergize_when="TBD")
    assert loto.load_ledger(path)["entries"] == []


def test_loto_postures(tmp_path: Path) -> None:
    path, carriers = _ledger_at(tmp_path)
    assert loto.posture(loto.load_ledger(path))[0] == "REMEDIATED"

    loto.create_entry(path, "LOTO-2026-09-06-DETECTOR-OFF",
                      what="the detector", tier="T3", carriers=carriers,
                      by="node", authority="the operator",
                      reason="noisy during the migration",
                      reenergize_when="the migration completes")
    doc = loto.load_ledger(path)
    assert loto.check_schema(doc) == []
    assert loto.check_carriers(doc, tmp_path) == []
    assert loto.posture(doc)[0] == "ATTENTION"

    loto.set_reenergize_status(path, "LOTO-2026-09-06-DETECTOR-OFF", "ready",
                               by="node", authority="the operator",
                               reason="the migration completed")
    assert loto.posture(loto.load_ledger(path))[0] == "BLOCKED"

    loto.append_chain_link(path, "LOTO-2026-09-06-DETECTOR-OFF",
                           {"action": "tagged_in", "by": "node",
                            "authority": "the operator",
                            "reason": "the migration completed"})
    doc = loto.load_ledger(path)
    assert doc["entries"][0]["state"] == "tagged_in"
    assert len(doc["entries"][0]["chain"]) == 2, "the chain must be append-only"
    assert loto.posture(doc)[0] == "REMEDIATED"


def test_loto_catches_a_tag_that_is_not_at_the_switch(tmp_path: Path) -> None:
    path, carriers = _ledger_at(tmp_path)
    loto.create_entry(path, "LOTO-2026-09-06-DETECTOR-OFF",
                      what="the detector", tier="T2", carriers=carriers,
                      by="node", authority="the operator", reason="noisy")
    (tmp_path / "src" / "detector.py").write_text("ENABLED = True\n",
                                                  encoding="utf-8")
    findings = loto.check_carriers(loto.load_ledger(path), tmp_path)
    assert any("NOT FOUND" in x for x in findings), findings


# ---------------------------------------------------------------------------
# the wish register: a carrier that does not exist is a fabricated citation
# ---------------------------------------------------------------------------


def _register(tmp_path: Path, wishes: list) -> Path:
    wishes_dir = tmp_path / "identity" / "wishes"
    wishes_dir.mkdir(parents=True, exist_ok=True)
    path = wishes_dir / "REGISTER.yaml"
    path.write_text(yaml.safe_dump({
        "schema": "wish-register/v1", "updated": "2026-09-06",
        "decisions_open": [], "wishes": wishes}, sort_keys=False),
        encoding="utf-8")
    return path


def test_wish_check_fails_on_a_missing_carrier(tmp_path: Path) -> None:
    path = _register(tmp_path, [{
        "id": "W-2026-09-06-FIRST", "wisher": "operator", "date": "2026-09-06",
        "carriers": [{"path": "identity/wishes/never-written.md",
                      "probe": "PROBE"}],
        "lineage": {}, "grant": {"status": "OPEN"}}])
    results = wish_register_check.run_checks(path, tmp_path)
    assert any("does not exist" in x for x in results["carriers"]), results
    assert wish_register_check.main([str(path), "--workspace", str(tmp_path)]) == 1


def test_wish_check_fails_when_a_carrier_lost_its_probe(tmp_path: Path) -> None:
    path = _register(tmp_path, [{
        "id": "W-2026-09-06-FIRST", "wisher": "operator", "date": "2026-09-06",
        "carriers": [{"path": "identity/wishes/first.md", "probe": "PROBE-ONE"}],
        "lineage": {}, "grant": {"status": "OPEN"}}])
    (tmp_path / "identity" / "wishes" / "first.md").write_text(
        "a wish whose probe string was edited away\n", encoding="utf-8")
    results = wish_register_check.run_checks(path, tmp_path)
    assert any("probe string not found" in x for x in results["carriers"]), results


def test_the_shipped_empty_register_template_is_valid(tmp_path: Path) -> None:
    """An empty-but-valid register is what genesis copies into a new node."""
    template = REPO / "genesis" / "templates" / "wishes-REGISTER.template.yaml"
    assert template.is_file(), f"template missing: {template}"
    doc = yaml.safe_load(template.read_text(encoding="utf-8"))
    assert doc["schema"] == "wish-register/v1"
    assert doc["wishes"] == [], "the template must ship with zero wishes"
    assert "decisions_open" in doc and "updated" in doc

    wishes_dir = tmp_path / "identity" / "wishes"
    wishes_dir.mkdir(parents=True)
    target = wishes_dir / "REGISTER.yaml"
    target.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
    results = wish_register_check.run_checks(target, tmp_path)
    assert all(not v for v in results.values()), results


# ---------------------------------------------------------------------------
# substrate: the detectors return a profile, and a mode falls out of it
# ---------------------------------------------------------------------------


def test_detectors_return_a_profile() -> None:
    profile = hardware_detector.detect_hardware(probe_network=False)
    assert isinstance(profile, hardware_detector.HardwareProfile)
    assert profile.cpu_cores >= 1
    assert isinstance(profile.cpu_model, str) and profile.cpu_model
    assert profile.ram_mb >= 0.0
    assert profile.gpu_vram_mb >= 0.0
    assert profile.power_source in hardware_detector.POWER_SOURCES
    assert profile.platform == sys.platform
    # the probe is opt-in, and the profile SAYS it was not taken
    assert profile.network_probed is False
    assert profile.has_network is False
    assert profile.summary()
    assert "not probed" in profile.summary()
    # the profile carries no host identifier
    assert not hasattr(profile, "hostname")


def test_mode_detection_is_a_pure_function_of_the_profile() -> None:
    big = hardware_detector.HardwareProfile(
        cpu_cores=24, ram_mb=64_000, gpu_vram_mb=24_000,
        network_probed=True, has_network=True)
    assert mode_detector.detect_mode(big).mode is mode_detector.SeedMode.FULL

    mid = hardware_detector.HardwareProfile(
        ram_mb=16_000, network_probed=True, has_network=True)
    assert mode_detector.detect_mode(mid).mode is mode_detector.SeedMode.STANDARD

    # unprobed network can never read above MINIMAL: sizing up on a measurement
    # nobody took is the dangerous direction
    unprobed = hardware_detector.HardwareProfile(
        ram_mb=64_000, gpu_vram_mb=24_000, network_probed=False)
    assert mode_detector.detect_mode(unprobed).mode is mode_detector.SeedMode.MINIMAL

    # an unreadable RAM figure falls to SURVIVAL, never up
    assert (mode_detector.detect_mode(hardware_detector.HardwareProfile()).mode
            is mode_detector.SeedMode.SURVIVAL)

    # STEALTH is never inferred
    assert all(mode_detector.detect_mode(p).mode is not mode_detector.SeedMode.STEALTH
               for p in (big, mid, unprobed))
    assert (mode_detector.detect_mode(big, force_stealth=True).mode
            is mode_detector.SeedMode.STEALTH)

    for cfg in mode_detector.list_modes():
        assert cfg.services and cfg.description and cfg.summary()


# ---------------------------------------------------------------------------
# store contracts: nothing in this slice writes a shared store unlocked
# ---------------------------------------------------------------------------


def test_witness_and_still_true_declare_their_write_model() -> None:
    for module in (witness, still_true, queue, loto):
        doc = module.__doc__ or ""
        assert "WRITE MODEL" in doc, f"{module.__name__} declares no write model"


def test_witness_refuses_a_claim_with_no_instrument(tmp_path: Path) -> None:
    register = witness.WitnessRegister(tmp_path)
    with pytest.raises(ValueError, match="cheap talk"):
        register.register_claim("W-1", "t", "healed", "abc123", "", "cond", "node")
    assert register.open_claims() == []


def test_still_true_reports_a_missing_source_rather_than_passing(tmp_path: Path) -> None:
    reading = still_true.take_reading(
        workspace=tmp_path, records=tmp_path / "absent.jsonl",
        tracked_fn=still_true.Tracked(()), last_commit_fn=lambda p, s: {})
    assert reading.unreadable, "a missing source left the population silently"
    assert still_true.posture(reading, [])[0] == "DEGRADED"
