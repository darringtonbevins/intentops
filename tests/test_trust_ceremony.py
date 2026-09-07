"""The trust tooling, under test -- wave-2 verifier corrections.

PURPOSE
    Four defects the wave-2 verifier found by RUNNING the shipped tooling, each
    of which this file now holds closed:

    1. **G1.7 could not tell a valid signature from a forged one.** With a real
       Ed25519 root minted and the pin monkeypatched to `minted`, a correctly
       signed manifest and an all-zero forgery both returned the identical
       `REFUSE`. Fail-closed, but blind: `verified` was unreachable on every
       clone, so the check had never once said PASS and a broken one would have
       looked the same.
    2. **Cross-certification was refused only in prose.** A planted
       `{role: operator_root, issued_by: R-INTENTOPS, status: active}` root
       passed G1.2 and G1.4 with three carriers agreeing. The only carrier of
       the refusal was a `note` string written into a JSON artifact at G2.
    3. **An unparseable manifest or catalogue raised a `yaml.ParserError`**
       out of the phase -- not a `GenesisError`, so the phase never journalled
       and the operator got a traceback instead of a verdict.
    4. **G2 offered four storage media and always wrote an unencrypted key**,
       recording the chosen medium in `operator-root.pub.json` as though it had
       been applied. A recorded claim the implementation does not honour is
       worse than an unencrypted key, because it stops the operator looking.

WRITE MODEL
    None. Every test reads, or writes only inside a pytest `tmp_path`. Keys are
    generated at test time in temp directories and never written into the tree.
    Tests that need a particular pin state mutate `trust_pin` module state and
    restore it in a fixture, because that state is process-global. Both
    directions are now explicit: `minted_pin` for the verification paths, and
    the shared `placeholder_pin` fixture for the refusal that only exists
    before a ceremony has run. Since 2026-09-07 this build's compiled pin is
    minted, so a test that read the live constant would be asserting the state
    of the build rather than the rule under test.

BLIND SPOTS
    - These prove the verifier can distinguish a good signature from a bad one.
      They prove nothing about whether the real ceremony was ever performed
      properly; only a witnessed ceremony record can.
    - The passphrase tests prove the bytes are wrapped, not that the passphrase
      was well chosen or kept.
    - `_read_passphrase` is tested only on its refusal path. Exercising the
      success path would mean driving `getpass` in a test, and a test that can
      supply a passphrase is a shape too close to the automation this gate
      exists to refuse.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, Iterator

import pytest

from intentops_core.genesis import Halt, OperatorGateRequired
from intentops_core.genesis import machine as machine_mod
from intentops_core.genesis import provenance as prov
from intentops_core.genesis import trust_pin

REPO_ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(not prov.crypto_available(),
                                reason="cryptography is not importable")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _keypair() -> Any:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    return Ed25519PrivateKey.generate()


def _pem(private: Any) -> str:
    from cryptography.hazmat.primitives import serialization

    return private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


GENERATED = (
    f"{prov._GENERATED_BEGIN}\nartifacts:\n"
    f"  - path: 'genesis/imprint/IMPRINT.md'\n    sha256: '{'a' * 64}'\n"
    f"    bytes: 12\n{prov._GENERATED_END}\n"
)


def _write_manifest(root: Path, signature: str, key_id: str = "TESTKEY000000001",
                    newline: str = "\n") -> Path:
    path = root / "genesis" / "imprint" / "IMPRINT-MANIFEST.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = ("status: released\nsignatures:\n"
            f"  - key_id: {key_id}\n    signature: '{signature}'\n" + GENERATED)
    path.write_bytes(text.replace("\n", newline).encode("utf-8"))
    return path


def _doc(pem: str, key_id: str = "TESTKEY000000001",
         **overrides: Any) -> Dict[str, Any]:
    root: Dict[str, Any] = {
        "id": "I", "role": "release_intermediate", "status": "active",
        "issued_by": "R", "key_id": key_id, "public_key_pem": pem,
        "signs": ["imprint_manifest"],
    }
    root.update(overrides)
    return {"roots": [root]}


@pytest.fixture()
def minted_pin() -> Iterator[None]:
    """Pretend the release ceremony has run, then put the pin back."""
    saved = trust_pin.PIN_STATE, trust_pin.ROOT_FINGERPRINT
    trust_pin.PIN_STATE = "minted"
    trust_pin.ROOT_FINGERPRINT = "sha256:" + "b" * 64
    try:
        yield
    finally:
        trust_pin.PIN_STATE, trust_pin.ROOT_FINGERPRINT = saved


# ---------------------------------------------------------------------------
# 1. G1.7 actually verifies
# ---------------------------------------------------------------------------


def test_a_valid_signature_passes(tmp_path: Path, minted_pin: None) -> None:
    private = _keypair()
    _write_manifest(tmp_path, "00")
    payload = prov.canonical_imprint_payload(
        (tmp_path / "genesis/imprint/IMPRINT-MANIFEST.yaml").read_text("utf-8"))
    _write_manifest(tmp_path, private.sign(payload).hex())
    check = prov._check_imprint_signature(tmp_path, _doc(_pem(private)))
    assert check.outcome == "PASS", check.reason
    assert check.evidence["key_ids"] == ["TESTKEY000000001"]


def test_a_forged_signature_is_a_contradiction(tmp_path: Path,
                                               minted_pin: None) -> None:
    """The defect in one test: forged and valid must not return the same thing."""
    private = _keypair()
    _write_manifest(tmp_path, "0" * 128)
    check = prov._check_imprint_signature(tmp_path, _doc(_pem(private)))
    assert check.outcome == "HALT"
    # A contradiction is never opened by the development flag. A forgery that
    # the dev flag could downgrade would be a fail-open wearing a warning.
    assert check.contradiction is True


def test_a_signature_by_an_unknown_key_is_a_contradiction(tmp_path: Path,
                                                          minted_pin: None) -> None:
    private = _keypair()
    _write_manifest(tmp_path, "00")
    payload = prov.canonical_imprint_payload(
        (tmp_path / "genesis/imprint/IMPRINT-MANIFEST.yaml").read_text("utf-8"))
    _write_manifest(tmp_path, private.sign(payload).hex())
    check = prov._check_imprint_signature(
        tmp_path, _doc(_pem(private), key_id="SOMEONEELSE00001"))
    assert check.outcome == "HALT" and check.contradiction is True


def test_a_signed_manifest_still_refuses_on_a_placeholder_pin(
        tmp_path: Path, placeholder_pin: None) -> None:
    """No minted root means the answer is REFUSE -- never PASS, never HALT."""
    private = _keypair()
    _write_manifest(tmp_path, "00")
    payload = prov.canonical_imprint_payload(
        (tmp_path / "genesis/imprint/IMPRINT-MANIFEST.yaml").read_text("utf-8"))
    _write_manifest(tmp_path, private.sign(payload).hex())
    check = prov._check_imprint_signature(tmp_path, _doc(_pem(private)))
    assert check.outcome == "REFUSE"
    assert check.evidence["pin"] == "placeholder"


def test_an_unsigned_manifest_is_still_refused(tmp_path: Path,
                                               minted_pin: None) -> None:
    path = tmp_path / "genesis" / "imprint" / "IMPRINT-MANIFEST.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("status: proposed\nsignatures: []\n" + GENERATED,
                    encoding="utf-8")
    assert prov._check_imprint_signature(tmp_path, {}).outcome == "REFUSE"


# ---------------------------------------------------------------------------
# 2. the canonical payload is stable across line endings
# ---------------------------------------------------------------------------


def test_the_canonical_payload_is_line_ending_independent(tmp_path: Path) -> None:
    """The F1 defect, closed at the signing layer before it can be created.

    A signature over raw bytes would verify on exactly one checkout in the
    world, because this repository ships `.gitattributes eol=lf` while the
    build machine's working copy held CRLF.
    """
    lf = _write_manifest(tmp_path / "lf", "00", newline="\n")
    crlf = _write_manifest(tmp_path / "crlf", "00", newline="\r\n")
    assert (prov.canonical_imprint_payload(lf.read_text("utf-8"))
            == prov.canonical_imprint_payload(crlf.read_text("utf-8")))
    assert b"\r" not in prov.canonical_imprint_payload(crlf.read_text("utf-8"))


def test_a_manifest_with_no_generated_block_halts() -> None:
    with pytest.raises(Halt):
        prov.canonical_imprint_payload("status: released\nsignatures: []\n")


# ---------------------------------------------------------------------------
# 3. root policy -- the cross-certification refusal, in code
# ---------------------------------------------------------------------------


def test_an_operator_root_in_the_project_file_is_a_contradiction() -> None:
    check = prov._check_root_policy({"roots": [{
        "id": "R-OPERATOR", "role": "operator_root", "status": "active",
        "issued_by": "R-INTENTOPS", "signs": ["imprint_manifest"]}]})
    assert check.outcome == "HALT" and check.contradiction is True
    assert "operator_root" in check.reason


@pytest.mark.parametrize("root,why", [
    ({"id": "R", "role": "wizard", "issued_by": "self", "signs": []},
     "undeclared role"),
    ({"id": "R", "role": "release_root", "issued_by": "someone", "signs": []},
     "a release root is self-issued"),
    ({"id": "I", "role": "release_intermediate", "issued_by": "self",
      "signs": []}, "an intermediate must name its issuer"),
    ({"id": "R", "role": "release_root", "issued_by": "self",
      "signs": ["everything"]}, "undeclared signs value"),
    ({"id": "R", "role": "release_root", "issued_by": "self", "signs": None},
     "a missing signs list is never assumed"),
])
def test_illegal_roots_halt(root: Dict[str, Any], why: str) -> None:
    assert prov._check_root_policy({"roots": [root]}).outcome == "HALT", why


def test_a_duplicate_fingerprint_halts() -> None:
    fp = "sha256:" + "c" * 64
    check = prov._check_root_policy({"roots": [
        {"id": "A", "role": "release_root", "issued_by": "self", "signs": [],
         "fingerprint": fp},
        {"id": "B", "role": "release_intermediate", "issued_by": "A",
         "signs": [], "fingerprint": fp},
    ]})
    assert check.outcome == "HALT"


def test_the_shipped_trust_roots_file_satisfies_its_own_policy() -> None:
    """The vocabularies this file declares in comments, enforced against it."""
    import yaml

    doc = yaml.safe_load((REPO_ROOT / "config" / "trust-roots.yaml")
                         .read_text(encoding="utf-8"))
    check = prov._check_root_policy(doc)
    assert check.outcome == "PASS", check.reason


def test_the_policy_check_runs_in_the_real_pass() -> None:
    record = prov.verify_provenance(REPO_ROOT)
    assert "G1.2b-root-policy" in [c.id for c in record.checks]


# ---------------------------------------------------------------------------
# 4. unparseable is a graded HALT, never a traceback
# ---------------------------------------------------------------------------


def test_an_unparseable_manifest_halts_instead_of_raising(tmp_path: Path) -> None:
    path = tmp_path / "genesis" / "imprint" / "IMPRINT-MANIFEST.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("signatures: [\n  - {\n", encoding="utf-8")
    check = prov._check_imprint_signature(tmp_path)
    assert check.outcome == "HALT" and check.contradiction is True
    assert "unparseable" in check.reason


def test_an_unparseable_catalogue_halts_instead_of_raising(tmp_path: Path) -> None:
    path = tmp_path / "genesis" / "imprint" / "archetypes" / "CATALOGUE.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("entries: [\n  - {\n", encoding="utf-8")
    check = prov._check_archetypes(tmp_path, None)
    assert check.outcome == "HALT" and check.contradiction is True


# ---------------------------------------------------------------------------
# 5. G2 honours the storage answer, or refuses it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("answer", ["hardware token", "TPM",
                                    "OS credential store", "a shoebox"])
def test_an_unimplemented_storage_medium_halts(answer: str) -> None:
    """Offering a medium and then writing a plain file is the defect."""
    with pytest.raises(Halt):
        machine_mod._resolve_storage(answer)


@pytest.mark.parametrize("answer", ["passphrase-wrapped file",
                                    "a passphrase-wrapped file, please",
                                    "DRY-RUN: none (dry-run: nothing persisted)"])
def test_an_implemented_storage_medium_resolves(answer: str) -> None:
    assert machine_mod._resolve_storage(answer)


def test_a_passphrase_actually_wraps_the_key() -> None:
    """Not 'the field says wrapped' -- the bytes must refuse a null password."""
    from cryptography.hazmat.primitives import serialization

    private = _keypair()
    wrapped = machine_mod._private_bytes(private, "correct horse battery staple")
    with pytest.raises(Exception):
        serialization.load_der_private_key(wrapped, password=None)
    loaded = serialization.load_der_private_key(
        wrapped, password=b"correct horse battery staple")
    assert loaded is not None


def test_an_unattended_run_cannot_supply_a_passphrase(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(machine_mod, "_attended", lambda ctx: False)
    ctx = machine_mod.GenesisContext(repo_root=REPO_ROOT, node_root=tmp_path)
    with pytest.raises(OperatorGateRequired):
        machine_mod._read_passphrase(ctx)


# ---------------------------------------------------------------------------
# 6. the ceremony tooling the carriers claimed existed
# ---------------------------------------------------------------------------


def _ceremony_module() -> Any:
    path = REPO_ROOT / "scripts" / "genesis" / "mint_release_root.py"
    spec = importlib.util.spec_from_file_location("_mint_release_root", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_ceremony_runbook_exists_and_names_the_automation_fence() -> None:
    text = (REPO_ROOT / "docs" / "TRUST-CEREMONY.md").read_text(encoding="utf-8")
    for clause in ("loop tick", "workflow leg", "subagent", "CI",
                   "cross-certification", "config/trust-roots.yaml",
                   "trust_pin.py", "docs/GENESIS.md"):
        assert clause in text, f"the runbook does not mention {clause}"


def test_the_ceremony_tool_selftest_passes() -> None:
    ok, report = _ceremony_module().selftest()
    assert ok, report


def test_the_ceremony_tool_refuses_a_key_inside_the_repository() -> None:
    module = _ceremony_module()
    with pytest.raises(module.CeremonyRefused):
        module.refuse_repo_path(REPO_ROOT / "config" / "leaked.key")


def test_the_ceremony_tool_refuses_an_unencrypted_release_root(
        tmp_path: Path) -> None:
    module = _ceremony_module()
    with pytest.raises(module.CeremonyRefused):
        module.mint(tmp_path / "bare.key", None)
    assert not (tmp_path / "bare.key").exists()


def test_the_ceremony_tool_refuses_an_unattended_arming(
        monkeypatch: pytest.MonkeyPatch) -> None:
    module = _ceremony_module()
    monkeypatch.setattr(module, "attended", lambda: False)
    with pytest.raises(module.CeremonyRefused):
        module.require_arming()
