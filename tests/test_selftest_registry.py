"""The selftest registry -- does one verb really reach every instrument?

The registry exists because two hand-written lists (``intentops verify
--selftest``, which named seven genesis instruments, and a CI step that named
ten) were the only things that ran the tree's selftests, and a hand list goes
stale in the direction that looks green. So the tests that matter here are the
ones about the POPULATION, not about any single instrument:

  * the discovered set is non-empty and contains the instruments this seed
    ships (named explicitly -- a test that only asserts "more than zero" would
    pass on a discovery that found one thing);
  * an instrument that cannot answer stays in the denominator as ERROR rather
    than leaving the count, which is the whole failure class;
  * every status in the closed vocabulary can actually fire.

No live services, no network. Every subprocess is this interpreter running a
module out of this tree or a planted temporary one.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from intentops_core.selftests import registry as reg
from intentops_core.selftests import _child

REPO = Path(__file__).resolve().parents[1]

#: Instruments this seed ships. Written out rather than derived, on purpose:
#: deriving the expectation from the same discovery it is checking would make
#: the test pass on an empty tree. A name that moves breaks this list, which
#: is the point -- an instrument that quietly stops being discovered is
#: exactly what the registry exists to catch.
REQUIRED = (
    # gates and scripts
    "scripts/ops/exposure_gate.py",
    "scripts/ops/trust_material_check.py",
    "scripts/genesis/build_manifest.py",
    "scripts/estate/estate_manifest_check.py",
    "scripts/genesis/probe_coverage_check.py",
    "scripts/docs/claims_check.py",
    # genesis and governance
    "intentops_core.genesis.provenance",
    "intentops_core.genesis.integrity",
    "intentops_core.governance.values_council",
    "intentops_core.gate.classify",
    # knowledge
    "intentops_core.knowledge.rings",
    "intentops_core.knowledge.collections",
    "intentops_core.knowledge.coverage",
    "intentops_core.knowledge.absorber",
    "intentops_core.knowledge.embedder",
    # metabolism
    "intentops_core.metabolism.heartbeat",
    "intentops_core.metabolism.cadence",
    # presence
    "intentops_core.presence.router",
    "intentops_core.presence.journal",
    "intentops_core.presence.intents",
    "intentops_core.presence.operator_rule",
    "intentops_core.presence.channels",
    # validation and alignment
    "intentops_core.validation.belief_carriers",
    "intentops_core.validation.responsiveness",
    "intentops_core.alignment.calibration",
    # loops, routing, the gateway
    "intentops_core.loops.conformance",
    "intentops_core.routing.policy",
    "intentops_gateway.server",
)


@pytest.fixture(scope="module")
def discovered():
    instruments, findings = reg.discover(REPO)
    return instruments, findings


# ---------------------------------------------------------------------------
# the population
# ---------------------------------------------------------------------------


def test_the_discovered_set_is_not_empty(discovered):
    instruments, _ = discovered
    assert instruments, "discovery found no instrument at all"
    assert len(instruments) >= 40


def test_every_shipped_instrument_is_discovered(discovered):
    instruments, _ = discovered
    names = {i.name for i in instruments}
    missing = [n for n in REQUIRED if n not in names]
    assert not missing, f"the registry cannot reach: {missing}"


def test_both_kinds_are_represented(discovered):
    instruments, _ = discovered
    kinds = {i.kind for i in instruments}
    assert kinds == {"module", "script"}


def test_discovery_reports_no_unreachable_instrument(discovered):
    """A module advertising ``--selftest`` with no callable is a finding.

    It is not an error to HAVE one -- it is an error to have one nobody
    declared. The two aggregate command surfaces are declared in
    ``AGGREGATE_CLIS`` with reasons; anything else here is a real gap.
    """
    _, findings = discovered
    assert findings == [], [f.to_row() for f in findings]


def test_every_aggregate_exemption_carries_a_reason():
    for name, reason in reg.AGGREGATE_CLIS.items():
        assert reason.strip(), f"{name}: an exemption with no reason"
        assert len(reason.strip()) > 40, (
            f"{name}: 'will do it later' is a deferral, not a reason")


def test_the_aggregate_exemptions_still_name_real_modules(discovered):
    instruments, _ = discovered
    names = {i.name for i in instruments}
    for name in reg.AGGREGATE_CLIS:
        module = REPO / "packages" / "intentops-core" / Path(
            *name.split(".")).with_suffix(".py")
        assert module.is_file(), f"{name}: exempted module does not exist"
        assert name not in names, (
            f"{name}: exempted, yet discovered -- it now owns a selftest() "
            f"callable, so the exemption is stale")


def test_a_stale_build_copy_is_outside_the_population(discovered):
    instruments, _ = discovered
    assert not [i for i in instruments
                if "/build/" in i.source or i.source.startswith("build/")]


def test_the_registry_discovers_itself(discovered):
    instruments, _ = discovered
    names = {i.name for i in instruments}
    assert "intentops_core.selftests.registry" in names


# ---------------------------------------------------------------------------
# the verdict shapes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value,ok", [
    (0, True),
    (1, False),
    (True, True),
    (False, False),
    ((True, "fine"), True),
    ((False, "broken"), False),
    ([0, "fine"], True),
    ([2, "broken"], False),
])
def test_the_three_return_shapes_normalise(value, ok):
    assert _child.interpret(value)["ok"] is ok


@pytest.mark.parametrize("value", [None, (), "ok", {"ok": True}, (None, "x")])
def test_a_shape_carrying_no_verdict_is_not_coerced_to_false(value):
    """``ok is None`` is ERROR, not FAIL. They are different findings."""
    assert _child.interpret(value)["ok"] is None


def test_the_status_vocabulary_is_closed():
    assert reg.STATUSES == (reg.PASS, reg.FAIL, reg.ERROR, reg.TIMEOUT)


# ---------------------------------------------------------------------------
# running one, on planted instruments
# ---------------------------------------------------------------------------


def _plant(tmp_path: Path) -> Path:
    pkg = tmp_path / "packages" / "planted" / "planted_here"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "good.py").write_text(
        "def selftest():\n    return True, 'ok'\n", encoding="utf-8")
    (pkg / "bad.py").write_text(
        "def selftest():\n    return False, 'planted failure'\n",
        encoding="utf-8")
    (pkg / "raiser.py").write_text(
        "def selftest():\n    raise RuntimeError('planted')\n",
        encoding="utf-8")
    (pkg / "importfail.py").write_text(
        "import a_module_that_does_not_exist  # noqa\n\n\n"
        "def selftest():\n    return True, 'never reached'\n",
        encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("module,status", [
    ("planted_here.good", reg.PASS),
    ("planted_here.bad", reg.FAIL),
    ("planted_here.raiser", reg.ERROR),
    ("planted_here.importfail", reg.ERROR),
])
def test_each_status_fires_on_a_planted_instrument(tmp_path, module, status):
    root = _plant(tmp_path)
    instrument = reg.Instrument(module, "module",
                               f"packages/planted/{module}.py")
    result = reg.run_instrument(instrument, root=root, timeout=90.0)
    assert result.status == status, result.report


def test_an_instrument_that_cannot_run_stays_in_the_denominator(tmp_path):
    """The failure class this whole module exists for.

    Three instruments in, three results out -- the two that could not answer
    are graded, not dropped. A registry that quietly skipped them would
    report ``1/1 PASS`` and be worse than no registry at all.
    """
    root = _plant(tmp_path)
    instruments, _ = reg.discover(root)
    report = reg.run_all(root=root, timeout=90.0, instruments=instruments,
                         findings=[])
    assert report.total == len(instruments) >= 4
    assert report.count(reg.PASS) == 1
    assert report.count(reg.ERROR) == 2
    assert report.count(reg.FAIL) == 1
    assert not report.ok
    assert "ERROR" in report.render()


def test_a_wedged_instrument_reports_timeout_rather_than_hanging(tmp_path):
    root = _plant(tmp_path)
    (root / "packages" / "planted" / "planted_here" / "slow.py").write_text(
        "import time\n\n\ndef selftest():\n    time.sleep(60)\n"
        "    return True, 'never'\n", encoding="utf-8")
    instrument = reg.Instrument("planted_here.slow", "module",
                                "packages/planted/planted_here/slow.py")
    result = reg.run_instrument(instrument, root=root, timeout=2.0)
    assert result.status == reg.TIMEOUT
    assert "headless" in result.report


def test_a_selftest_that_wants_a_terminal_is_a_defect_not_a_skip(tmp_path):
    """stdin is closed for every child, so a prompt reads EOF and reports."""
    root = _plant(tmp_path)
    (root / "packages" / "planted" / "planted_here" / "tty.py").write_text(
        "import sys\n\n\ndef selftest():\n"
        "    line = sys.stdin.readline()\n"
        "    if not line:\n"
        "        raise RuntimeError('needed a TTY')\n"
        "    return True, 'read'\n", encoding="utf-8")
    instrument = reg.Instrument("planted_here.tty", "module",
                                "packages/planted/planted_here/tty.py")
    result = reg.run_instrument(instrument, root=root, timeout=30.0)
    assert result.status == reg.ERROR


def test_a_script_off_the_exit_convention_reports_error(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "odd.py").write_text(
        "import sys\n"
        "if '--selftest' in sys.argv:\n"
        "    print('neither pass nor fail')\n"
        "    raise SystemExit(7)\n", encoding="utf-8")
    instruments, _ = reg.discover(tmp_path)
    assert [i.name for i in instruments] == ["scripts/odd.py"]
    result = reg.run_instrument(instruments[0], root=tmp_path, timeout=60.0)
    assert result.status == reg.ERROR
    assert "exit 7" in result.report


# ---------------------------------------------------------------------------
# the registry's own detector, and the verb
# ---------------------------------------------------------------------------


def test_the_registry_selftest_plants_a_failure_and_reports_it():
    ok, report = reg.selftest()
    assert ok, report
    assert "0 failed" in report


def test_the_verb_lists_the_population():
    proc = subprocess.run(
        [sys.executable, "-m", "intentops_core.selftests.registry",
         "--list", "--root", str(REPO)],
        cwd=str(REPO), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=180, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    assert "instrument(s)" in proc.stdout
    assert "scripts/ops/exposure_gate.py" in proc.stdout
    # A RuntimeWarning here means the package __init__ imported the module
    # runpy is about to execute. It is cosmetic and it is also a standing
    # warning on the estate's own verb, so it stays caught.
    assert "RuntimeWarning" not in proc.stderr


def test_intentops_verify_exposes_all_selftests():
    from intentops_core.cli import build_parser

    args = build_parser().parse_args(["verify", "--all-selftests"])
    assert args.all_selftests is True
