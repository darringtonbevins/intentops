"""Tests for the blank-estate manifests, their loader, and their checker.

The shape of every test below is the same: take the SHIPPED estate as the
baseline, break exactly one thing, and assert that the loader halts (or warns)
for that reason and not by accident. A test that only proved "something went
wrong" would pass against a loader that halts on everything.

No live services, no network, no model. Everything runs against a temp copy.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "packages" / "intentops-core"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from intentops_core import VersionUnavailable, read_version  # noqa: E402
from intentops_core.estate import (  # noqa: E402
    ACCOUNTINGS,
    CAPABILITY_KIND_IDS,
    MANIFEST_FILENAMES,
    ManifestError,
    collect_problems,
    load_estate,
)

SHIPPED_ESTATE = REPO_ROOT / "estate"
OPERATOR_ROOT = "aaaabbbbccccdddd"
FOREIGN_ROOT = "ffff0000ffff0000"


def _load_checker() -> Any:
    path = REPO_ROOT / "scripts" / "estate" / "estate_manifest_check.py"
    spec = importlib.util.spec_from_file_location("estate_manifest_check", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


@pytest.fixture()
def estate(tmp_path: Path) -> Path:
    """A pristine copy of the shipped estate, safe to mutate."""
    work = tmp_path / "estate"
    work.mkdir()
    for name in MANIFEST_FILENAMES:
        shutil.copy2(SHIPPED_ESTATE / name, work / name)
    return work


def _problem_texts(estate_dir: Path, operator_root: str | None = OPERATOR_ROOT) -> list[str]:
    problems, _warnings, _estate = collect_problems(estate_dir, operator_root)
    return [str(problem) for problem in problems]


# --------------------------------------------------------------------------
# The shipped state: empty is valid
# --------------------------------------------------------------------------


def test_shipped_estate_is_valid_and_empty() -> None:
    loaded = load_estate(SHIPPED_ESTATE, OPERATOR_ROOT)
    assert set(loaded.manifests) == set(MANIFEST_FILENAMES)
    for name in MANIFEST_FILENAMES:
        assert loaded.entries(name) == (), f"{name} should ship empty"
    assert loaded.applied_grants == ()
    assert loaded.ignored_grants == ()


def test_shipped_capability_kinds_are_the_nine_with_reasons() -> None:
    loaded = load_estate(SHIPPED_ESTATE, OPERATOR_ROOT)
    assert set(loaded.kinds) == set(CAPABILITY_KIND_IDS)
    for kind_id in CAPABILITY_KIND_IDS:
        for accounting in ACCOUNTINGS:
            cell = loaded.kinds[kind_id]["owes"][accounting]
            assert cell["status"] in ("applies", "n/a")
            assert cell["why"].strip(), f"{kind_id}.{accounting} owes a reason"


def test_owes_is_declared_not_defaulted() -> None:
    loaded = load_estate(SHIPPED_ESTATE, OPERATOR_ROOT)
    assert loaded.owes("service", "monitored") is True
    assert loaded.owes("service", "probe") is False
    with pytest.raises(ManifestError, match="undeclared capability kind"):
        loaded.owes("dirigible", "probe")
    with pytest.raises(ManifestError, match="unknown accounting"):
        loaded.owes("service", "vibes")


# --------------------------------------------------------------------------
# HALTs
# --------------------------------------------------------------------------


def test_missing_manifest_file_halts(estate: Path) -> None:
    (estate / "ASSETS.yaml").unlink()
    with pytest.raises(ManifestError) as excinfo:
        load_estate(estate, OPERATOR_ROOT)
    assert "missing" in str(excinfo.value)
    assert "entries: []" in excinfo.value.remedy


def test_missing_directory_halts(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="does not exist"):
        load_estate(tmp_path / "nope", OPERATOR_ROOT)


def test_unknown_schema_halts(estate: Path) -> None:
    checker._mutate(estate, "ASSETS.yaml", lambda d: d.__setitem__("schema", "estate-assets/v99"))
    assert any("unknown schema" in text for text in _problem_texts(estate))


def test_missing_as_of_halts(estate: Path) -> None:
    checker._mutate(estate, "RESOURCES.yaml", lambda d: d.pop("as_of", None))
    assert any("no `as_of` declared" in text for text in _problem_texts(estate))


def test_null_entries_is_not_an_empty_list(estate: Path) -> None:
    checker._mutate(estate, "ASSETS.yaml", lambda d: d.__setitem__("entries", None))
    assert any("not a list" in text for text in _problem_texts(estate))


def test_undeclared_vocabulary_value_halts(estate: Path) -> None:
    checker._seed_then(estate, "ASSETS.yaml", "entries", {"kind": "spaceship"})
    texts = _problem_texts(estate)
    assert any("undeclared value for `kind`: 'spaceship'" in text for text in texts)
    assert any("never a default of everything-applies" in text for text in texts)


def test_null_telemetry_source_halts(estate: Path) -> None:
    checker._seed_then(estate, "RESOURCES.yaml", "entries", {"telemetry_source": None})
    texts = _problem_texts(estate)
    assert any("`telemetry_source` is null and has no default" in text for text in texts)
    assert any("unmetered" in text for text in texts)


def test_missing_max_concurrent_halts(estate: Path) -> None:
    checker._seed_drop(estate, "RESOURCES.yaml", "entries", "max_concurrent")
    assert any("`max_concurrent` is missing" in text for text in _problem_texts(estate))


def test_missing_health_probe_halts(estate: Path) -> None:
    checker._seed_drop(estate, "DEPENDENCIES.yaml", "entries", "health_probe")
    assert any("`health_probe` is missing" in text for text in _problem_texts(estate))


def test_null_health_probe_without_reason_halts(estate: Path) -> None:
    checker._seed_then(estate, "DEPENDENCIES.yaml", "entries", {"health_probe": None})
    texts = _problem_texts(estate)
    assert any("null with no `unprobeable_reason`" in text for text in texts)
    assert any("stay in the denominator" in text for text in texts)


def test_duplicate_entry_id_halts(estate: Path) -> None:
    checker._seed_duplicate(estate)
    assert any("duplicate id" in text for text in _problem_texts(estate))


def test_asset_naming_an_undeclared_estate_halts(estate: Path) -> None:
    checker._seed_then(estate, "ASSETS.yaml", "entries", {"estate": "nowhere"})
    assert any("names no entry in ESTATE-MAP.yaml" in text for text in _problem_texts(estate))


def test_capability_kind_missing_halts(estate: Path) -> None:
    checker._mutate(estate, "CAPABILITIES.yaml", lambda d: d["kinds"].pop("organ", None))
    assert any("declared kind 'organ' is missing" in text for text in _problem_texts(estate))


def test_capability_undeclared_status_halts(estate: Path) -> None:
    checker._mutate(
        estate,
        "CAPABILITIES.yaml",
        lambda d: d["kinds"]["organ"]["owes"]["probe"].__setitem__("status", "maybe"),
    )
    assert any("undeclared status 'maybe'" in text for text in _problem_texts(estate))


def test_capability_na_without_reason_halts(estate: Path) -> None:
    checker._mutate(
        estate,
        "CAPABILITIES.yaml",
        lambda d: d["kinds"]["loop"]["owes"]["probe"].__setitem__("why", "   "),
    )
    texts = _problem_texts(estate)
    assert any("no `why`" in text for text in texts)
    assert any("INCLUDING for `n/a`" in text for text in texts)


# --------------------------------------------------------------------------
# The two non-HALT outcomes, both of which must stay visible
# --------------------------------------------------------------------------


def test_unprobeable_dependency_stays_in_the_population(estate: Path) -> None:
    reason = "no endpoint exists to ask; only a human at the keyboard can say"
    checker._seed_then(
        estate,
        "DEPENDENCIES.yaml",
        "entries",
        {"health_probe": None, "unprobeable_reason": reason},
    )
    problems, warnings, loaded = collect_problems(estate, OPERATOR_ROOT)
    assert problems == []
    assert loaded is not None
    entries = loaded.entries("DEPENDENCIES.yaml")
    assert len(entries) == 1, "the unprobeable dependency must NOT be dropped"
    assert entries[0]["health_probe"] == {"type": "unprobeable", "reason": reason}
    assert [w.code for w in warnings] == ["DEPENDENCY-UNPROBEABLE"]


def test_grant_from_a_foreign_root_raises_nothing_and_is_never_applied(estate: Path) -> None:
    checker._seed_then(estate, "GRANTS.yaml", "entries", {"granted_by": FOREIGN_ROOT})
    problems, warnings, loaded = collect_problems(estate, OPERATOR_ROOT)
    assert problems == [], "a foreign grant raises nothing"
    assert loaded is not None
    assert loaded.applied_grants == ()
    assert len(loaded.ignored_grants) == 1
    codes = [w.code for w in warnings]
    assert codes == ["GRANT-FOREIGN-ROOT"], "and it is never silently dropped"


def test_grant_from_the_operator_root_is_applied(estate: Path) -> None:
    checker._seed_valid_entries(estate)
    loaded = load_estate(estate, OPERATOR_ROOT)
    assert len(loaded.applied_grants) == 1
    assert loaded.ignored_grants == ()
    assert loaded.warnings == ()


def test_no_operator_root_applies_no_grant(estate: Path) -> None:
    checker._seed_valid_entries(estate)
    loaded = load_estate(estate, None)
    assert loaded.applied_grants == ()
    assert [w.code for w in loaded.warnings] == ["GRANT-NO-OPERATOR-ROOT"]


def test_grant_without_expires_at_halts(estate: Path) -> None:
    checker._seed_drop(estate, "GRANTS.yaml", "entries", "expires_at")
    texts = _problem_texts(estate)
    assert any("`expires_at` is missing" in text for text in texts)


def test_grant_with_null_expires_at_halts(estate: Path) -> None:
    checker._seed_then(estate, "GRANTS.yaml", "entries", {"expires_at": None})
    texts = _problem_texts(estate)
    assert any("`expires_at` is null and has no default" in text for text in texts)
    assert any("no perpetual grants" in text for text in texts)


def test_undeclared_tier_ceiling_halts(estate: Path) -> None:
    checker._seed_then(estate, "GRANTS.yaml", "entries", {"tier_ceiling": "T9"})
    assert any("undeclared value for `tier_ceiling`" in text for text in _problem_texts(estate))


# --------------------------------------------------------------------------
# The detector proves it can fire
# --------------------------------------------------------------------------


def test_checker_selftest_passes() -> None:
    assert checker.run_selftest(SHIPPED_ESTATE) == 0


def test_checker_exit_codes(estate: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert checker.main(["--estate", str(estate)]) == 0
    assert "CONFORMANT" in capsys.readouterr().out
    (estate / "GRANTS.yaml").unlink()
    assert checker.main(["--estate", str(estate)]) == 1
    assert "NOT CONFORMANT" in capsys.readouterr().out


# --------------------------------------------------------------------------
# Package version: a missing VERSION is loud, never a 0.0.0 placeholder
# --------------------------------------------------------------------------


def test_read_version_reads_the_nearest_version_file(tmp_path: Path) -> None:
    (tmp_path / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert read_version(nested) == "1.2.3"


def test_missing_version_raises_with_a_remedy(tmp_path: Path) -> None:
    root = Path(tmp_path.anchor)
    if (root / "VERSION").exists():  # pragma: no cover - environment shape
        pytest.skip("a VERSION file exists at the filesystem root")
    with pytest.raises(VersionUnavailable) as excinfo:
        read_version(tmp_path)
    assert "create a VERSION file" in excinfo.value.remedy


def test_empty_version_file_is_a_halt(tmp_path: Path) -> None:
    (tmp_path / "VERSION").write_text("   \n", encoding="utf-8")
    with pytest.raises(VersionUnavailable, match="empty"):
        read_version(tmp_path)
