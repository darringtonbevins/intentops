"""Tests for portable loop scheduling: charters, adapters, engine, conformance.

The shape of most tests below is the same one the estate-manifest tests use:
take a good charter as the baseline, break exactly one thing, and assert the
loader halts for THAT reason and not by accident. A test that only proved
"something went wrong" would pass against a loader that halts on everything.

No live services, no network, no model, no scheduler. Every write goes to a
pytest temp directory.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any, Dict, List
from xml.etree import ElementTree

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "packages" / "intentops-core"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from intentops_core.loops import (  # noqa: E402
    ADAPTER_KINDS,
    AdapterError,
    BindingRecord,
    Charter,
    CharterError,
    HostContext,
    check,
    load_charters,
    parse_bindings,
    parse_charters,
    render,
    render_all,
    undisabled,
)
from intentops_core.loops import adapters as adapters_mod  # noqa: E402
from intentops_core.loops import charters as charters_mod  # noqa: E402
from intentops_core.loops import cli as loops_cli  # noqa: E402
from intentops_core.loops import conformance as conformance_mod  # noqa: E402
from intentops_core.loops import engine as engine_mod  # noqa: E402

TEMPLATE = REPO_ROOT / "config" / "loop-charters.template.yaml"
CONTEXT = HostContext(node_root="/srv/node",
                      tick_command="intentops loops tick",
                      user="loops",
                      windows_wrapper="scripts/launch-hidden.vbs")

GOOD: Dict[str, Any] = {
    "id": "loop-doc-drift",
    "purpose": "Compare the docs against the code and file each mismatch.",
    "cadence": {"kind": "interval", "every": "30m"},
    "tier_ceiling": "T1",
    "enabled": False,
    "budget_share": 0.1,
    "kill_switch": ".intentops/loops/doc-drift.disabled",
    "requires_evidence": ["the file and line of each claimed mismatch"],
}


def doc(*entries: Dict[str, Any]) -> Dict[str, Any]:
    return {"schema": charters_mod.SCHEMA, "as_of": "2026-09-06",
            "entries": [dict(e) for e in entries]}


def one(**over: Any) -> Charter:
    entry = dict(GOOD)
    entry.update(over)
    return parse_charters(doc(entry))[0]


# ---------------------------------------------------------------------------
# the shipped template
# ---------------------------------------------------------------------------


def test_the_shipped_template_exists_and_is_empty_and_valid():
    """`entries: []` is the honest state for a node that runs no loops."""
    charters = load_charters(TEMPLATE, birth=True)
    assert charters == []


def test_the_shipped_template_declares_its_schema_and_a_date():
    text = TEMPLATE.read_text(encoding="utf-8")
    assert f"schema: {charters_mod.SCHEMA}" in text
    assert "entries: []" in text
    # The example must stay commented out: a live example is a loop nobody
    # chose that ships enabled-shaped in every clone.
    assert "\n- id:" not in text


def test_a_missing_charter_file_halts_rather_than_reading_as_no_loops(tmp_path):
    with pytest.raises(CharterError) as excinfo:
        load_charters(tmp_path / "absent.yaml")
    assert "does not exist" in str(excinfo.value)
    assert "different facts" in str(excinfo.value)


# ---------------------------------------------------------------------------
# the loader
# ---------------------------------------------------------------------------


def test_an_empty_charter_list_is_valid():
    assert parse_charters(doc()) == []


def test_a_good_charter_round_trips():
    charter = one()
    assert charter.id == "loop-doc-drift"
    assert charter.cadence.interval_parts() == (30, "m")
    assert charter.cadence.scheduled is True
    assert charter.requires_evidence


def test_every_charter_is_born_disabled():
    """enabled: false at birth for EVERY charter -- and an enabled one HALTs
    when the file is loaded as a birth artifact."""
    assert one().enabled is False
    assert undisabled([one()]) == []
    with pytest.raises(CharterError) as excinfo:
        parse_charters(doc({**GOOD, "enabled": True}), birth=True)
    assert "born disabled" in str(excinfo.value)
    # Outside birth, an enabled charter is the operator's own reviewed act.
    assert undisabled(parse_charters(doc({**GOOD, "enabled": True}))) \
        == ["loop-doc-drift"]


def test_enabled_is_required_and_is_never_defaulted():
    entry = {k: v for k, v in GOOD.items() if k != "enabled"}
    with pytest.raises(CharterError) as excinfo:
        parse_charters(doc(entry))
    assert "'enabled'" in str(excinfo.value)


@pytest.mark.parametrize("field", sorted(charters_mod.REQUIRED_FIELDS))
def test_every_required_field_halts_when_missing(field):
    entry = {k: v for k, v in GOOD.items() if k != field}
    with pytest.raises(CharterError) as excinfo:
        parse_charters(doc(entry))
    assert repr(field) in str(excinfo.value)


def test_an_undeclared_field_halts():
    with pytest.raises(CharterError) as excinfo:
        parse_charters(doc({**GOOD, "budget-share": 0.2}))
    assert "undeclared field" in str(excinfo.value)
    assert "budget-share" in str(excinfo.value)


def test_an_undeclared_cadence_field_halts():
    with pytest.raises(CharterError):
        parse_charters(doc({**GOOD, "cadence": {"kind": "daily",
                                                "at": "03:15",
                                                "every": "5m"}}))


@pytest.mark.parametrize("ceiling", ["T3", "T4", "t1", "high"])
def test_a_ceiling_above_t2_or_undeclared_halts(ceiling):
    with pytest.raises(CharterError) as excinfo:
        parse_charters(doc({**GOOD, "tier_ceiling": ceiling}))
    assert "tier_ceiling" in str(excinfo.value)


@pytest.mark.parametrize("switch", ["/var/run/x.disabled", "C:/node/x.disabled",
                                    "\\\\host\\share\\x", "../x.disabled", ""])
def test_an_unusable_kill_switch_halts(switch):
    with pytest.raises(CharterError):
        parse_charters(doc({**GOOD, "kill_switch": switch}))


@pytest.mark.parametrize("share", [0, -0.1, 1.5, "half", True])
def test_a_budget_share_outside_the_unit_interval_halts(share):
    with pytest.raises(CharterError):
        parse_charters(doc({**GOOD, "budget_share": share}))


def test_over_subscribed_enabled_charters_halt_and_disabled_ones_do_not():
    over = doc({**GOOD, "enabled": True, "budget_share": 0.7},
               {**GOOD, "id": "loop-other", "enabled": True,
                "budget_share": 0.7})
    with pytest.raises(CharterError) as excinfo:
        parse_charters(over)
    assert "loop budget" in str(excinfo.value)
    # The same shares, disabled, load clean: none of them can spend anything.
    parked = doc({**GOOD, "budget_share": 0.7},
                 {**GOOD, "id": "loop-other", "budget_share": 0.7})
    assert len(parse_charters(parked)) == 2


def test_requires_evidence_must_be_a_non_empty_list():
    for value in ([], "a string", [""], None):
        with pytest.raises(CharterError):
            parse_charters(doc({**GOOD, "requires_evidence": value}))


def test_a_duplicate_id_halts():
    with pytest.raises(CharterError) as excinfo:
        parse_charters(doc(GOOD, GOOD))
    assert "duplicate charter id" in str(excinfo.value)


def test_an_unknown_schema_or_missing_entries_key_halts():
    with pytest.raises(CharterError):
        parse_charters({**doc(), "schema": "loop-charters/v2"})
    with pytest.raises(CharterError) as excinfo:
        parse_charters({"schema": charters_mod.SCHEMA, "as_of": "2026-09-06"})
    assert "entries is missing" in str(excinfo.value)


# ---------------------------------------------------------------------------
# adapters
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ADAPTER_KINDS)
@pytest.mark.parametrize("cadence", [
    {"kind": "interval", "every": "30m"},
    {"kind": "interval", "every": "2h"},
    {"kind": "interval", "every": "1d"},
    {"kind": "daily", "at": "03:15"},
    {"kind": "weekly", "at": "06:00", "weekday": "mon"},
])
def test_every_adapter_renders_every_expressible_cadence(kind, cadence):
    binding = render(kind, one(cadence=cadence), CONTEXT)
    assert binding.kind == kind
    assert binding.files
    assert all(text.strip() for text in binding.files.values())
    assert any("/srv/node" in text for text in binding.files.values())


def test_the_cron_line_carries_the_charter_and_pins_a_directory():
    text = render("cron", one(), CONTEXT).single_text()
    assert "*/30 * * * *" in text
    assert "cd /srv/node &&" in text
    assert "loop-doc-drift" in text
    assert ".intentops/loops/doc-drift.disabled" in text


def test_systemd_renders_a_timer_and_a_oneshot_service():
    binding = render("systemd", one(), CONTEXT)
    assert set(binding.files) == {"intentops-loop-loop-doc-drift.service",
                                  "intentops-loop-loop-doc-drift.timer"}
    service = binding.files["intentops-loop-loop-doc-drift.service"]
    timer = binding.files["intentops-loop-loop-doc-drift.timer"]
    assert "Type=oneshot" in service
    assert "WorkingDirectory=/srv/node" in service
    assert "OnUnitActiveSec=30min" in timer
    assert "WantedBy=timers.target" in timer


def test_systemd_uses_oncalendar_for_a_weekly_cadence():
    timer = render("systemd",
                   one(cadence={"kind": "weekly", "at": "06:05",
                                "weekday": "thu"}),
                   CONTEXT).files["intentops-loop-loop-doc-drift.timer"]
    assert "OnCalendar=Thu *-*-* 06:05:00" in timer
    assert "Persistent=true" in timer


def test_the_windows_task_is_well_formed_xml_with_a_windowless_action():
    """The action is the wrapper launcher, never a shell or interpreter."""
    text = render("windows-task", one(), CONTEXT).single_text()
    # The input is text this process just generated from a validated charter,
    # never an untrusted document, so the stdlib parser's entity-expansion
    # exposure is not reachable here and no extra dependency is taken for it.
    root = ElementTree.fromstring(text)
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    command = root.find(".//t:Exec/t:Command", ns)
    assert command is not None and command.text == "wscript.exe"
    arguments = root.find(".//t:Exec/t:Arguments", ns)
    assert arguments is not None
    assert "launch-hidden.vbs" in (arguments.text or "")
    workdir = root.find(".//t:Exec/t:WorkingDirectory", ns)
    assert workdir is not None and workdir.text == "/srv/node"
    assert root.find(".//t:TimeTrigger/t:Repetition/t:Interval", ns) is not None


@pytest.mark.parametrize("wrapper", [None, "", "C:/w/pwsh.exe",
                                     "C:/w/powershell.exe", "cmd.exe",
                                     "C:\\w\\python.exe"])
def test_a_windows_task_refuses_a_bare_console_engine(wrapper):
    context = HostContext(node_root="C:/node",
                          tick_command="intentops loops tick",
                          windows_wrapper=wrapper)
    with pytest.raises(AdapterError):
        render("windows-task", one(), context)


@pytest.mark.parametrize("every", ["7m", "5h", "3d", "90m"])
def test_cron_refuses_a_cadence_it_cannot_express_exactly(every):
    """Refuse rather than round: a line that fires early every hour is worse
    than no line."""
    with pytest.raises(AdapterError) as excinfo:
        render("cron", one(cadence={"kind": "interval", "every": every}),
               CONTEXT)
    assert "cannot express this charter exactly" in str(excinfo.value)


@pytest.mark.parametrize("every,expected", [
    ("1m", "*/1 * * * *"), ("15m", "*/15 * * * *"), ("60m", "0 * * * *"),
    ("6h", "0 */6 * * *"), ("24h", "0 0 * * *"), ("1d", "0 0 * * *"),
])
def test_cron_renders_the_intervals_it_can_express(every, expected):
    assert expected in render(
        "cron", one(cadence={"kind": "interval", "every": every}),
        CONTEXT).single_text()


@pytest.mark.parametrize("kind", ADAPTER_KINDS)
def test_no_adapter_renders_a_manual_cadence(kind):
    with pytest.raises(AdapterError):
        render(kind, one(cadence={"kind": "manual"}), CONTEXT)


def test_an_undeclared_adapter_kind_halts():
    with pytest.raises(AdapterError):
        render("launchd", one(), CONTEXT)


def test_a_refusal_stays_in_the_population_rather_than_improving_the_number():
    bindings, refusals = render_all(
        one(cadence={"kind": "interval", "every": "7m"}), CONTEXT)
    assert "cron" in refusals and "cron" not in bindings
    assert {"systemd", "windows-task"} <= set(bindings)


def test_rendering_is_deterministic_and_reads_no_clock():
    """Two renders of one charter are byte-identical, so a diff against the
    installed artifact means 'somebody edited this' and nothing else."""
    charter = one(cadence={"kind": "daily", "at": "03:15"})
    for kind in ADAPTER_KINDS:
        first = render(kind, charter, CONTEXT).files
        second = render(kind, charter, CONTEXT).files
        assert first == second


# ---------------------------------------------------------------------------
# the engine
# ---------------------------------------------------------------------------


def test_a_tick_is_a_dry_run_and_is_journalled(tmp_path):
    record = engine_mod.tick(one(enabled=True), tmp_path,
                             now="2026-09-06T00:00:00Z")
    assert record.verdict == "DRY-RUN"
    assert record.mode == "dry-run"
    assert record.ran is True
    rows = engine_mod.read_journal(tmp_path)
    assert len(rows) == 1
    assert rows[0]["charter_id"] == "loop-doc-drift"
    assert rows[0]["tier_ceiling"] == "T1"
    assert rows[0]["requires_evidence"] == list(GOOD["requires_evidence"])


def test_the_engine_has_no_execute_verdict_and_no_execute_argument():
    assert set(engine_mod.TICK_VERDICTS) == {"DRY-RUN", "REFUSED"}
    import inspect

    signature = inspect.signature(engine_mod.tick)
    assert "execute" not in signature.parameters


def test_the_engine_imports_nothing_that_could_reach_a_model_or_a_network():
    """Bias LEFT on the ladder: no LLM calls in the seed, proven mechanically
    rather than promised in a docstring."""
    forbidden = {"socket", "http", "httpx", "requests", "urllib", "ssl",
                 "subprocess", "asyncio", "anthropic", "openai"}
    source = Path(engine_mod.__file__).read_text(encoding="utf-8")
    imported: List[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported += [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module.split(".")[0])
    assert forbidden.isdisjoint(imported), sorted(set(imported) & forbidden)


@pytest.mark.parametrize("case", ["disabled", "kill-switch", "halt-marker",
                                  "scheduled-manual"])
def test_every_refusal_fires_and_is_recorded_with_its_reason(tmp_path, case):
    charter = one(enabled=True)
    if case == "disabled":
        charter = one(enabled=False)
    elif case == "kill-switch":
        switch = charter.kill_switch_path(tmp_path)
        switch.parent.mkdir(parents=True, exist_ok=True)
        switch.write_text("off", encoding="utf-8")
    elif case == "halt-marker":
        marker = tmp_path / engine_mod.HALT_MARKER_RELPATH
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("stood down", encoding="utf-8")
    else:
        charter = one(enabled=True, cadence={"kind": "manual"})

    record = engine_mod.tick(charter, tmp_path, now="2026-09-06T00:00:00Z")
    assert record.verdict == "REFUSED"
    assert record.reason
    rows = engine_mod.read_journal(tmp_path)
    assert len(rows) == 1 and rows[0]["verdict"] == "REFUSED"


def test_the_halt_marker_outranks_an_enabled_charter(tmp_path):
    marker = tmp_path / engine_mod.HALT_MARKER_RELPATH
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("stood down", encoding="utf-8")
    record = engine_mod.tick(one(enabled=True), tmp_path,
                             now="2026-09-06T00:00:00Z")
    assert "stood down" in record.reason


def test_an_explicit_manual_run_is_the_only_way_a_manual_charter_ticks(tmp_path):
    charter = one(enabled=True, cadence={"kind": "manual"})
    assert engine_mod.tick(charter, tmp_path, now="2026-09-06T00:00:00Z"
                           ).verdict == "REFUSED"
    assert engine_mod.tick(charter, tmp_path, now="2026-09-06T00:01:00Z",
                           manual_run=True).verdict == "DRY-RUN"


def test_the_journal_is_append_only_and_folds(tmp_path):
    charter = one(enabled=True)
    for minute in range(3):
        engine_mod.tick(charter, tmp_path, now=f"2026-09-06T00:0{minute}:00Z")
    assert len(engine_mod.read_journal(tmp_path)) == 3
    assert engine_mod.last_tick(tmp_path, "loop-doc-drift")["at"] \
        == "2026-09-06T00:02:00Z"
    assert engine_mod.last_tick(tmp_path, "loop-absent") is None


def test_an_unreadable_journal_line_stays_in_the_population(tmp_path):
    engine_mod.tick(one(enabled=True), tmp_path, now="2026-09-06T00:00:00Z")
    with engine_mod.journal_path(tmp_path).open("a", encoding="utf-8") as fh:
        fh.write("{not json}\n")
    rows = engine_mod.read_journal(tmp_path)
    assert len(rows) == 2
    assert rows[1]["unreadable"] is True


def test_tick_refuses_anything_that_is_not_a_validated_charter(tmp_path):
    with pytest.raises(CharterError):
        engine_mod.tick("loop-doc-drift", tmp_path)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# conformance -- charter versus binding
# ---------------------------------------------------------------------------


def bind(**over: Any) -> BindingRecord:
    base: Dict[str, Any] = {"charter_id": "loop-doc-drift", "kind": "cron",
                            "enabled": True, "cadence": "*/30 * * * *"}
    base.update(over)
    return BindingRecord(**base)


def codes(report) -> List[str]:
    return [f.code for f in report.findings]


def test_a_matching_pair_is_conformant():
    report = check([one(enabled=True)], [bind()], host_kind="cron",
                   context=CONTEXT)
    assert codes(report) == ["OK"]
    assert report.posture == "CONFORMANT"
    assert report.exit_code == 0


def test_a_charter_with_no_binding_is_absent_not_disabled():
    """The finding this whole module exists for."""
    report = check([one(enabled=True)], [], host_kind="cron", context=CONTEXT)
    assert codes(report) == ["ABSENT"]
    assert report.posture == "DRIFTED"
    assert report.exit_code == 1
    detail = report.findings[0].detail
    assert "not disabled" in detail
    assert "no tagout" in detail and "no way back" in detail


def test_a_disabled_charter_with_no_binding_and_no_tagout_is_still_absent():
    report = check([one(enabled=False)], [], host_kind="cron", context=CONTEXT)
    assert codes(report) == ["ABSENT"]


def test_a_disabled_charter_with_a_tagout_is_parked_and_only_attention():
    report = check([one(enabled=False, tagout="LOTO-2026-09-06-DOC-DRIFT")],
                   [], host_kind="cron", context=CONTEXT)
    assert codes(report) == ["PARKED"]
    assert report.posture == "ATTENTION"
    assert report.exit_code == 0


def test_a_binding_with_no_charter_is_an_orphan():
    report = check([], [bind(charter_id="loop-nobody")], host_kind="cron",
                   context=CONTEXT)
    assert codes(report) == ["ORPHAN"]
    assert report.posture == "DRIFTED"


def test_a_host_running_a_disabled_charter_is_the_dangerous_direction():
    report = check([one(enabled=False)], [bind()], host_kind="cron",
                   context=CONTEXT)
    assert codes(report) == ["UNCHARTERED-RUN"]
    assert report.posture == "DRIFTED"


def test_a_binding_switched_off_needs_a_tagout_to_be_governed():
    off = check([one(enabled=True)], [bind(enabled=False)], host_kind="cron",
                context=CONTEXT)
    assert codes(off) == ["BINDING-OFF"] and off.posture == "DRIFTED"
    parked = check([one(enabled=True)],
                   [bind(enabled=False, tagout="LOTO-2026-09-06-X")],
                   host_kind="cron", context=CONTEXT)
    assert codes(parked) == ["BINDING-PARKED"] and parked.posture == "ATTENTION"


def test_a_manual_charter_is_correct_unbound_and_drift_when_bound():
    manual = one(enabled=True, cadence={"kind": "manual"})
    unbound = check([manual], [], host_kind="cron", context=CONTEXT)
    assert codes(unbound) == ["MANUAL-UNBOUND"]
    assert unbound.posture == "CONFORMANT"
    bound = check([manual], [bind(cadence=None)], host_kind="cron",
                  context=CONTEXT)
    assert codes(bound) == ["MANUAL-BOUND"]
    assert bound.posture == "DRIFTED"


def test_a_hand_edited_schedule_is_cadence_drift():
    report = check([one(enabled=True)], [bind(cadence="*/5 * * * *")],
                   host_kind="cron", context=CONTEXT)
    assert codes(report) == ["CADENCE-DRIFT"]
    assert "*/30 * * * *" in report.findings[0].detail


def test_a_cadence_the_host_cannot_express_stays_in_the_denominator():
    report = check([one(enabled=True,
                        cadence={"kind": "interval", "every": "7m"})],
                   [bind(cadence="*/7 * * * *")], host_kind="cron",
                   context=CONTEXT)
    assert codes(report) == ["UNRENDERABLE"]
    assert report.charters_seen == 1


def test_an_unreadable_source_is_degraded_and_outranks_everything():
    report = check([one(enabled=True)], [bind()], host_kind="cron",
                   context=CONTEXT, unreadable=["the host export"])
    assert "UNREADABLE" in codes(report)
    assert report.posture == "DEGRADED"
    assert report.exit_code == 1


def test_cadence_drift_that_was_not_checked_says_so_rather_than_reading_clean():
    report = check([one(enabled=True)], [bind(cadence="*/5 * * * *")],
                   host_kind="cron", context=None)
    assert report.cadence_checked is False
    assert "UNCHECKED, not clean" in report.render()


def test_a_binding_for_another_host_is_counted_as_skipped_never_dropped():
    report = check([one(enabled=True)], [bind(kind="systemd")],
                   host_kind="cron", context=CONTEXT)
    assert report.bindings_skipped == 1
    assert report.bindings_seen == 0
    assert codes(report) == ["ABSENT"]


def test_the_report_states_its_denominator_beside_its_verdict():
    report = check([one(enabled=True), one(id="loop-other", enabled=True)],
                   [bind()], host_kind="cron", context=CONTEXT)
    rendered = report.render()
    assert "charters: 2" in rendered and "bindings: 1" in rendered
    assert "posture:" in rendered
    assert report.counts == {"OK": 1, "ABSENT": 1}


def test_every_finding_code_maps_to_a_declared_posture():
    for code, posture in conformance_mod.FINDING_CODES.items():
        assert posture in conformance_mod.POSTURES, code


def test_an_undeclared_host_kind_halts():
    with pytest.raises(CharterError):
        check([], [], host_kind="launchd")


# ---------------------------------------------------------------------------
# the bindings export
# ---------------------------------------------------------------------------


def bindings_doc(*entries: Dict[str, Any]) -> Dict[str, Any]:
    return {"schema": conformance_mod.BINDINGS_SCHEMA, "as_of": "2026-09-06",
            "host": "cron", "entries": [dict(e) for e in entries]}


def test_a_bindings_export_round_trips_and_an_empty_host_is_valid():
    host, records = parse_bindings(bindings_doc())
    assert host == "cron" and records == []
    host, records = parse_bindings(bindings_doc(
        {"charter_id": "loop-doc-drift", "kind": "cron", "enabled": True,
         "cadence": "*/30 * * * *"}))
    assert records[0].charter_id == "loop-doc-drift"


@pytest.mark.parametrize("mutation", [
    {"cadance": "typo"},
    {"charter_id": None},
    {"kind": "launchd"},
    {"enabled": "yes"},
])
def test_the_bindings_reader_halts_on_a_bad_entry(mutation):
    entry = {"charter_id": "loop-doc-drift", "kind": "cron", "enabled": True}
    entry.update(mutation)
    with pytest.raises(CharterError):
        parse_bindings(bindings_doc(entry))


def test_the_bindings_reader_halts_on_a_bad_document():
    for document in ({**bindings_doc(), "schema": "loop-bindings/v2"},
                     {**bindings_doc(), "host": "launchd"},
                     {"schema": conformance_mod.BINDINGS_SCHEMA,
                      "as_of": "2026-09-06", "host": "cron"},
                     {"schema": conformance_mod.BINDINGS_SCHEMA,
                      "as_of": "not-a-date", "host": "cron", "entries": []}):
        with pytest.raises(CharterError):
            parse_bindings(document)


def test_a_missing_bindings_file_halts(tmp_path):
    with pytest.raises(CharterError) as excinfo:
        conformance_mod.load_bindings(tmp_path / "absent.yaml")
    assert "different facts" in str(excinfo.value)


# ---------------------------------------------------------------------------
# selftests and the CLI
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module", [charters_mod, adapters_mod, engine_mod,
                                    conformance_mod])
def test_every_module_ships_a_selftest_that_passes(module):
    ok, report = module.selftest()
    assert ok, report
    assert report


def test_the_package_selftest_runs_every_member():
    from intentops_core import loops

    ok, report = loops.selftest()
    assert ok, report
    assert report.count("PASS") == 4


def test_the_cli_check_verb_selftests(capsys):
    assert loops_cli.main(["check", "--selftest"]) == 0
    assert "PASS" in capsys.readouterr().out


def test_the_cli_check_verb_reports_drift_and_exits_nonzero(tmp_path, capsys):
    (tmp_path / "config").mkdir()
    charters_file = tmp_path / "config" / "loop-charters.yaml"
    charters_file.write_text(
        "schema: loop-charters/v1\nas_of: '2026-09-06'\nentries:\n"
        "  - id: loop-doc-drift\n"
        "    purpose: Compare the docs against the code.\n"
        "    cadence: {kind: interval, every: 30m}\n"
        "    tier_ceiling: T1\n"
        "    enabled: true\n"
        "    budget_share: 0.1\n"
        "    kill_switch: .intentops/loops/doc-drift.disabled\n"
        "    requires_evidence: ['the file and line of each mismatch']\n",
        encoding="utf-8")
    bindings_file = tmp_path / "bindings.yaml"
    bindings_file.write_text(
        "schema: loop-bindings/v1\nas_of: '2026-09-06'\nhost: cron\n"
        "entries: []\n", encoding="utf-8")

    rc = loops_cli.main(["--node-root", str(tmp_path), "check",
                         "--bindings", str(bindings_file)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "ABSENT" in out and "posture: DRIFTED" in out


def test_the_cli_check_verb_grades_an_unreadable_bindings_file_degraded(
        tmp_path, capsys):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "loop-charters.yaml").write_text(
        "schema: loop-charters/v1\nas_of: '2026-09-06'\nentries: []\n",
        encoding="utf-8")
    rc = loops_cli.main(["--node-root", str(tmp_path), "check",
                         "--bindings", str(tmp_path / "absent.yaml")])
    out = capsys.readouterr().out
    assert rc == 1
    assert "UNREADABLE" in out and "DEGRADED" in out


def test_the_cli_render_verb_writes_the_artifacts_it_names(tmp_path, capsys):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "loop-charters.yaml").write_text(
        "schema: loop-charters/v1\nas_of: '2026-09-06'\nentries:\n"
        "  - id: loop-doc-drift\n"
        "    purpose: Compare the docs against the code.\n"
        "    cadence: {kind: daily, at: '03:15'}\n"
        "    tier_ceiling: T1\n"
        "    enabled: false\n"
        "    budget_share: 0.1\n"
        "    kill_switch: .intentops/loops/doc-drift.disabled\n"
        "    requires_evidence: ['the file and line of each mismatch']\n",
        encoding="utf-8")
    out_dir = tmp_path / "rendered"
    rc = loops_cli.main(["--node-root", str(tmp_path), "render", "--all",
                         "--host", "systemd", "--out", str(out_dir)])
    assert rc == 0
    capsys.readouterr()
    written = sorted(p.name for p in out_dir.iterdir())
    assert written == ["intentops-loop-loop-doc-drift.service",
                       "intentops-loop-loop-doc-drift.timer"]


def test_the_cli_tick_verb_journals_a_dry_run(tmp_path, capsys):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "loop-charters.yaml").write_text(
        "schema: loop-charters/v1\nas_of: '2026-09-06'\nentries:\n"
        "  - id: loop-doc-drift\n"
        "    purpose: Compare the docs against the code.\n"
        "    cadence: {kind: interval, every: 30m}\n"
        "    tier_ceiling: T1\n"
        "    enabled: true\n"
        "    budget_share: 0.1\n"
        "    kill_switch: .intentops/loops/doc-drift.disabled\n"
        "    requires_evidence: ['the file and line of each mismatch']\n",
        encoding="utf-8")
    rc = loops_cli.main(["--node-root", str(tmp_path), "tick",
                         "loop-doc-drift"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "DRY-RUN" in out
    assert len(engine_mod.read_journal(tmp_path)) == 1


def test_the_cli_names_an_unknown_charter_rather_than_guessing(tmp_path,
                                                              capsys):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "loop-charters.yaml").write_text(
        "schema: loop-charters/v1\nas_of: '2026-09-06'\nentries: []\n",
        encoding="utf-8")
    rc = loops_cli.main(["--node-root", str(tmp_path), "tick", "loop-absent"])
    assert rc == 1
    assert "loop-absent" in capsys.readouterr().out


def test_the_node_cli_registers_the_loops_verb():
    from intentops_core import cli as node_cli

    assert "loops" in node_cli._COMMANDS
    parser = node_cli.build_parser()
    args = parser.parse_args(["loops", "check", "--selftest"])
    assert args.command == "loops" and args.loops_command == "check"
