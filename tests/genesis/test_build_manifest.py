"""Tests for the imprint manifest builder.

These exist because the manifest is the artifact G1 verifies before a node
mints an identity. A builder that cannot detect a changed byte would let an
altered imprint verify, and nothing else in the genesis would notice.

The suite deliberately tests that the checker FAILS on each of its four
findings, not only that it passes on a clean tree: a detector that has never
fired is indistinguishable from a broken one
(genesis/imprint/rules/no-silent-failures.md rule 3).

No live services, no network, no writes outside a temporary directory. The
real bundle is copied into a fixture; the repository's own manifest is only
ever READ.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "genesis" / "build_manifest.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("build_manifest", MODULE_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - packaging failure
        raise RuntimeError(f"cannot load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_manifest"] = module
    spec.loader.exec_module(module)
    return module


bm = _load_module()


@pytest.fixture()
def fixture_repo(tmp_path: Path) -> Path:
    """A throwaway repo holding a copy of the real bundle, freshly hashed."""
    root = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / bm.BUNDLE_DIR_RELPATH, root / bm.BUNDLE_DIR_RELPATH)
    bm.write(root)
    return root


# --------------------------------------------------------------------------
# the repository as it stands
# --------------------------------------------------------------------------


def test_the_shipped_manifest_matches_the_shipped_bundle() -> None:
    ok, findings = bm.check(REPO_ROOT)
    assert ok, "the committed manifest has drifted from the bundle:\n" + "\n".join(findings)


def test_every_bundle_member_is_hashed_and_claimed() -> None:
    artifacts = bm.compute_artifacts(REPO_ROOT)
    paths = {a.path for a in artifacts}
    # The nine rules of genesis-design sect. 4, by name, so a dropped rule fails
    # here rather than silently shrinking the bundle.
    for name in (
        "honesty",
        "no-silent-failures",
        "knowledge-retention",
        "store-write-discipline",
        "tagout",
        "minimize-llm-dependency",
        "no-skill-marketplace",
        "admin-actions",
        "email-safety",
    ):
        assert f"genesis/imprint/rules/{name}.md" in paths, f"rule {name} is not in the bundle"
    for name in ("core", "gide", "tapch", "vocabulary"):
        assert f"genesis/imprint/invariants/{name}.yaml" in paths
    assert "genesis/imprint/IMPRINT.md" in paths
    assert "genesis/imprint/archetypes/CATALOGUE.yaml" in paths


def test_the_manifest_itself_is_not_hashed() -> None:
    paths = {a.path for a in bm.compute_artifacts(REPO_ROOT)}
    assert bm.MANIFEST_RELPATH not in paths, "a manifest that hashes itself can never be written"


# --------------------------------------------------------------------------
# determinism
# --------------------------------------------------------------------------


def test_hashing_is_deterministic_and_ordered(fixture_repo: Path) -> None:
    first = bm.compute_artifacts(fixture_repo)
    second = bm.compute_artifacts(fixture_repo)
    assert first == second
    assert [a.path for a in first] == sorted(a.path for a in first)


def test_write_is_idempotent(fixture_repo: Path) -> None:
    manifest = fixture_repo / bm.MANIFEST_RELPATH
    before = manifest.read_text(encoding="utf-8")
    bm.write(fixture_repo)
    assert manifest.read_text(encoding="utf-8") == before


def test_write_preserves_everything_outside_the_generated_block(fixture_repo: Path) -> None:
    manifest = fixture_repo / bm.MANIFEST_RELPATH
    text = manifest.read_text(encoding="utf-8")
    head = text.split(bm.BEGIN_MARKER, 1)[0]
    bm.write(fixture_repo)
    assert manifest.read_text(encoding="utf-8").split(bm.BEGIN_MARKER, 1)[0] == head


# --------------------------------------------------------------------------
# the checker must be able to fail -- one test per finding class
# --------------------------------------------------------------------------


def test_changed_byte_is_caught(fixture_repo: Path) -> None:
    victim = fixture_repo / bm.BUNDLE_DIR_RELPATH / "rules" / "honesty.md"
    victim.write_bytes(victim.read_bytes() + b"\n")
    ok, findings = bm.check(fixture_repo)
    assert not ok
    assert any(f.startswith("CHANGED") and "honesty.md" in f for f in findings)


def test_missing_file_is_caught(fixture_repo: Path) -> None:
    (fixture_repo / bm.BUNDLE_DIR_RELPATH / "rules" / "email-safety.md").unlink()
    ok, findings = bm.check(fixture_repo)
    assert not ok
    assert any(f.startswith("MISSING") and "email-safety.md" in f for f in findings)


def test_untracked_file_is_caught(fixture_repo: Path) -> None:
    planted = fixture_repo / bm.BUNDLE_DIR_RELPATH / "rules" / "zz-planted.md"
    planted.write_text("an artifact nobody signed\n", encoding="utf-8")
    ok, findings = bm.check(fixture_repo)
    assert not ok
    assert any(f.startswith("UNTRACKED") and "zz-planted.md" in f for f in findings)


def test_a_hashed_file_no_bundle_claims_is_caught(fixture_repo: Path) -> None:
    """UNCLAIMED: hashed, but no I1-I9 bundle names it -- the denominator failure."""
    planted = fixture_repo / bm.BUNDLE_DIR_RELPATH / "rules" / "zz-unclaimed.md"
    planted.write_text("hashed by the walker, named by no bundle\n", encoding="utf-8")
    bm.write(fixture_repo)  # now it IS tracked, so UNTRACKED cannot be what fires
    ok, findings = bm.check(fixture_repo)
    assert not ok
    assert any(f.startswith("UNCLAIMED") and "zz-unclaimed.md" in f for f in findings)
    assert not any(f.startswith("UNTRACKED") for f in findings)


def test_check_exits_1_on_drift_and_0_when_clean(fixture_repo: Path) -> None:
    assert bm.main(["--root", str(fixture_repo), "--check"]) == 0
    victim = fixture_repo / bm.BUNDLE_DIR_RELPATH / "invariants" / "core.yaml"
    victim.write_bytes(victim.read_bytes() + b"# drift\n")
    assert bm.main(["--root", str(fixture_repo), "--check"]) == 1


def test_bare_invocation_defaults_to_check_and_never_mutates(fixture_repo: Path) -> None:
    manifest = fixture_repo / bm.MANIFEST_RELPATH
    victim = fixture_repo / bm.BUNDLE_DIR_RELPATH / "invariants" / "gide.yaml"
    victim.write_bytes(victim.read_bytes() + b"# drift\n")
    before = manifest.read_text(encoding="utf-8")
    assert bm.main(["--root", str(fixture_repo)]) == 1
    assert manifest.read_text(encoding="utf-8") == before


# --------------------------------------------------------------------------
# HALT rather than default (no-silent-failures rule 6)
# --------------------------------------------------------------------------


def test_empty_bundle_halts_instead_of_verifying_vacuously(tmp_path: Path) -> None:
    root = tmp_path / "empty"
    (root / bm.BUNDLE_DIR_RELPATH).mkdir(parents=True)
    with pytest.raises(bm.ManifestError, match="no hashable files"):
        bm.compute_artifacts(root)


def test_missing_bundle_directory_halts(tmp_path: Path) -> None:
    with pytest.raises(bm.ManifestError):
        bm.compute_artifacts(tmp_path / "nowhere")


def test_parser_halts_on_an_unrecognised_line() -> None:
    text = f"{bm.BEGIN_MARKER}\nartifacts:\n  unexpected: 1\n{bm.END_MARKER}\n"
    with pytest.raises(bm.ManifestError, match="unrecognised line"):
        bm.parse_block(text)


def test_parser_halts_on_a_truncated_entry() -> None:
    text = f'{bm.BEGIN_MARKER}\nartifacts:\n  - path: "a"\n{bm.END_MARKER}\n'
    with pytest.raises(bm.ManifestError, match="ends mid-entry"):
        bm.parse_block(text)


def test_parser_halts_when_the_block_is_absent() -> None:
    with pytest.raises(bm.ManifestError, match="no generated artifacts block"):
        bm.parse_block("schema: imprint-manifest/v1\n")


def test_write_refuses_a_manifest_with_no_markers(fixture_repo: Path) -> None:
    manifest = fixture_repo / bm.MANIFEST_RELPATH
    manifest.write_text("schema: imprint-manifest/v1\n", encoding="utf-8")
    with pytest.raises(bm.ManifestError, match="no generated block"):
        bm.write(fixture_repo)


def test_lock_refuses_the_store_path_itself(tmp_path: Path) -> None:
    """A lock primitive handed the store path is one truncation from destroying it."""
    store = tmp_path / "LEDGER.yaml"
    store.write_text("payload\n", encoding="utf-8")
    with pytest.raises(bm.ManifestError, match="must end in .lock"):
        with bm._exclusive_lock(store):
            pass
    assert store.read_text(encoding="utf-8") == "payload\n"


# --------------------------------------------------------------------------
# the selftest is itself the guard that the guard can fire
# --------------------------------------------------------------------------


def test_selftest_passes_against_the_real_bundle(capsys: pytest.CaptureFixture[str]) -> None:
    assert bm.selftest(REPO_ROOT) == 0
    out = capsys.readouterr().out
    assert "FAIL" not in out
    assert "paths fired as specified" in out
