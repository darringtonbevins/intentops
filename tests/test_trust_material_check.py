"""The zero-tolerance trust-material scan.

Nothing in this file contains a real key, and nothing here plants a key-shaped
file inside the repository. Every red case is constructed under ``tmp_path``,
because the checker under test forbids exactly that shape wherever it appears,
including in tests -- and a test that has to be excluded from the scan it tests
is the exclusion this scanner exists to not have.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "ops"))

import trust_material_check as tmc  # noqa: E402

PEM_HEADER = tmc._PEM_OPEN + " " + tmc._PEM_PRIVATE_TAIL


# --------------------------------------------------------------------------
# each shape class fires
# --------------------------------------------------------------------------


def test_a_pem_private_block_fires() -> None:
    findings = tmc.scan_text("x.txt", PEM_HEADER + "\nMC4CAQAw" + "A" * 40 + "\n")
    assert [f.code for f in findings] == ["PEM-PRIVATE"]


def test_a_public_pem_block_does_not_fire() -> None:
    body = tmc._PEM_OPEN + " PUBLIC KEY-----\nMCowBQYDK2VwAyEA" + "B" * 20 + "\n"
    assert tmc.scan_text("x.pub", body) == []


def test_a_certificate_does_not_fire() -> None:
    body = tmc._PEM_OPEN + " CERTIFICATE-----\nMIIB" + "C" * 40 + "\n"
    assert tmc.scan_text("x.crt", body) == []


def test_a_64_hex_seed_beside_a_secret_word_fires() -> None:
    findings = tmc.scan_text("notes.md", "private_seed: " + ("ab12" * 16) + "\n")
    assert [f.code for f in findings] == ["SEED-HEX"]


def test_a_64_hex_digest_with_no_secret_word_does_not_fire() -> None:
    """Otherwise every manifest hash fires, and a detector that cries wolf on
    its own manifests is a detector somebody switches off."""
    assert tmc.scan_text("MANIFEST.yaml", "sha256: " + ("cd34" * 16) + "\n") == []


@pytest.mark.parametrize(
    "line",
    [
        '{"token": "' + "xox" + "b-" + "0123456789abcdef" + '"}',
        "TOKEN=" + "gh" + "p_" + "A" * 36,
        "key_id: " + "AKI" + "A" + "ABCDEFGHIJKLMNOP",
        "api_key = '" + "sk-" + "a" * 30 + "'",
    ],
)
def test_each_bearer_token_shape_fires(line: str) -> None:
    findings = tmc.scan_text("settings.json", line + "\n")
    assert "TOKEN-SHAPE" in [f.code for f in findings]


def test_a_key_shaped_filename_fires(tmp_path: Path) -> None:
    (tmp_path / "operator-root.key").write_text("not actually a key\n", encoding="utf-8")
    findings, _scanned, _unscanned = tmc.scan_tree(tmp_path)
    assert [f.code for f in findings] == ["KEY-FILENAME"]


@pytest.mark.parametrize("name", ["secrets.yaml", "credentials.json", ".env",
                                  ".env.local", "id_ed25519", "store.p12"])
def test_a_container_filename_fires(tmp_path: Path, name: str) -> None:
    (tmp_path / name).write_text("placeholder\n", encoding="utf-8")
    findings, _scanned, _unscanned = tmc.scan_tree(tmp_path)
    assert [f.code for f in findings] == ["KEY-FILENAME"]


@pytest.mark.parametrize("name", ["secret-scan.yml", "keystore-notes.md",
                                  "credentialing.md", "env.example"])
def test_a_filename_merely_ABOUT_secrets_does_not_fire(tmp_path: Path, name: str) -> None:
    """The narrowing that replaced a substring match. Without this control the
    scanner flags a workflow whose whole job is to look for secrets."""
    (tmp_path / name).write_text("prose\n", encoding="utf-8")
    findings, _scanned, _unscanned = tmc.scan_tree(tmp_path)
    assert findings == []


def test_a_trust_projection_carrying_a_private_key_name_fires() -> None:
    body = "roots:\n  - key_id: EXAMPLE\n    private_key: PLACEHOLDER\n"
    findings = tmc.scan_text("config/trust-roots.yaml", body)
    assert [f.code for f in findings] == ["TRUST-KEYNAME"]


def test_the_jwk_private_scalar_is_one_letter_and_still_fires() -> None:
    findings = tmc.scan_text("node/trust/key.json", '{\n  "d": "PLACEHOLDER"\n}\n')
    assert [f.code for f in findings] == ["TRUST-KEYNAME"]


def test_a_did_key_is_not_mistaken_for_the_jwk_scalar() -> None:
    assert tmc.scan_text("node/trust/key.json", '{\n  "did": "did:key:PLACEHOLDER"\n}\n') == []


def test_the_keyname_rule_only_applies_to_trust_paths() -> None:
    body = "private_notes: a description of how key storage works\n"
    assert tmc.scan_text("docs/design.md", body) == []


# --------------------------------------------------------------------------
# what must NOT fire
# --------------------------------------------------------------------------


def test_prose_about_private_keys_does_not_fire() -> None:
    body = (
        "A private key never lives in a repository. It lives in a hardware token,\n"
        "a TPM, or the operating system credential store.\n"
    )
    assert tmc.scan_text("docs/trust.md", body) == []


def test_the_scanner_does_not_flag_itself() -> None:
    """The patterns are assembled from fragments precisely so this holds. A
    scanner that flags itself gets an exclusion, and an exclusion is how zero
    tolerance stops being zero."""
    source = (REPO_ROOT / "scripts" / "ops" / "trust_material_check.py")
    findings = tmc.scan_text("scripts/ops/trust_material_check.py",
                             source.read_text(encoding="utf-8"))
    assert findings == []


# --------------------------------------------------------------------------
# denominator honesty
# --------------------------------------------------------------------------


def test_a_binary_file_is_reported_unscanned_not_silently_skipped(tmp_path: Path) -> None:
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02" * 100)
    _findings, scanned, unscanned = tmc.scan_tree(tmp_path)
    assert scanned == 0
    assert unscanned == ["blob.bin"]


def test_dependency_directories_are_pruned(tmp_path: Path) -> None:
    node_modules = tmp_path / "node_modules" / "pkg"
    node_modules.mkdir(parents=True)
    (node_modules / "x.txt").write_text("hello\n", encoding="utf-8")
    (tmp_path / "kept.txt").write_text("hello\n", encoding="utf-8")
    _findings, scanned, _unscanned = tmc.scan_tree(tmp_path)
    assert scanned == 1


# --------------------------------------------------------------------------
# the whole repository, and the selftest
# --------------------------------------------------------------------------


def test_the_repository_carries_no_trust_material() -> None:
    findings, scanned, _unscanned = tmc.scan_tree(REPO_ROOT)
    assert scanned > 0, "an empty population is not a clean tree"
    assert findings == [], "\n".join(str(f) for f in findings)


def test_the_selftest_passes() -> None:
    assert tmc.run_selftest() == 0
