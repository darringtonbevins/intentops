"""Regression tests for the defects an adversarial verification pass found.

PURPOSE
    Each test here corresponds to one finding against the genesis seed, and
    each asserts the CORRECTED behaviour rather than the shape of the fix.
    They are kept in one file so the findings stay legible as a set: a
    verifier's report is only worth what its regressions are worth, and a
    finding with no test that would have caught it is a note, not a closure.

BLIND SPOTS
    - The EOF and attendance tests simulate a Windows null-device stdin by
      pairing a truthful ``isatty`` with a stream at EOF. That is the observed
      shape of the failure, not a live scheduled task; a genuine service
      context is not reproducible from pytest.
    - No test here mints a real identity. A real (non-dry) run writes a private
      key under the operating user's home directory, so these exercise only the
      phases BEFORE G2 on a real run, plus the gates in isolation.
"""

from __future__ import annotations

import builtins
import io
import os
from pathlib import Path

import pytest

from intentops_core.genesis import (
    UNSIGNED_DEV_ENV,
    UNVERIFIED_BOOT_PHRASE,
    Halt,
    OperatorGateRequired,
)
from intentops_core.genesis import machine as machine_mod
from intentops_core.genesis import organs as organs_mod
from intentops_core.genesis import provenance as provenance_mod

REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _real_key_pem() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    return Ed25519PrivateKey.generate().public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


def _tampered_repo(tmp_path: Path) -> Path:
    """A clone whose key material disagrees with its own fingerprint."""
    pem = _real_key_pem()
    indented = "\n".join("      " + line for line in pem.strip().splitlines())
    repo = tmp_path / "tampered"
    (repo / "config").mkdir(parents=True)
    (repo / "config" / "trust-roots.yaml").write_text(
        "schema: trust-roots/v1\n"
        'pinned_fingerprint: "sha256:' + "a" * 64 + '"\n'
        "roots:\n"
        "  - id: R-TEST\n"
        "    status: active\n"
        '    fingerprint: "sha256:' + "b" * 64 + '"\n'
        '    did: "did:key:zNOTTHISKEY"\n'
        "    public_key_pem: |\n" + indented + "\n",
        encoding="utf-8")
    (repo / "config" / "trust-revocation.json").write_text(
        '{"schema": "trust-revocation/v1", "sequence": 0, "entries": []}',
        encoding="utf-8")
    return repo


class _EofStdin(io.StringIO):
    """A stream that claims to be a terminal and is immediately at EOF.

    This is the Windows null device's behaviour: ``isatty()`` returns True for
    ``NUL``, so a TTY-only attendance check waves a scheduled task straight
    through into ``input()``.
    """

    def isatty(self) -> bool:  # noqa: D102 - see class docstring
        return True


# ---------------------------------------------------------------------------
# finding A -- an EOF at an operator gate crashed unjournaled
# ---------------------------------------------------------------------------


def test_a_gate_at_eof_raises_the_genesis_error_not_an_eoferror(tmp_path,
                                                                monkeypatch):
    ctx = machine_mod.GenesisContext(repo_root=REPO, node_root=tmp_path,
                                     isatty=lambda: True)
    monkeypatch.setattr(builtins, "input",
                        lambda *_a, **_k: (_ for _ in ()).throw(EOFError()))
    with pytest.raises(OperatorGateRequired) as exc:
        machine_mod._gate(ctx, "G2", "where should the key live?", "none")
    assert "EOF" in str(exc.value)
    assert "--dry-run" in (exc.value.remedy or "")


def test_a_declared_ci_environment_is_never_attended(tmp_path, monkeypatch):
    ctx = machine_mod.GenesisContext(repo_root=REPO, node_root=tmp_path,
                                     isatty=lambda: True)
    monkeypatch.setenv("CI", "true")
    assert machine_mod._attended(ctx) is False
    with pytest.raises(OperatorGateRequired):
        machine_mod._gate(ctx, "G4", "consent?", "yes")


def test_a_closed_stdin_is_never_attended(tmp_path, monkeypatch):
    ctx = machine_mod.GenesisContext(repo_root=REPO, node_root=tmp_path,
                                     isatty=lambda: True)
    monkeypatch.delenv("CI", raising=False)
    stream = io.StringIO()
    stream.close()
    monkeypatch.setattr("sys.stdin", stream)
    assert machine_mod._attended(ctx) is False


def test_a_real_run_from_a_null_stdin_halts_and_journals(tmp_path, monkeypatch):
    """The shape that produced a traceback: a TTY-claiming stream at EOF.

    Invariant 2 of the design -- a phase that cannot record its own transition
    HALTS -- was broken here, because ``EOFError`` is not a ``GenesisError``
    and so never reached the journal. It reaches it now.
    """
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr("sys.stdin", _EofStdin())
    monkeypatch.setenv(UNSIGNED_DEV_ENV, "1")
    node = tmp_path / "node"
    run = machine_mod.run_genesis(
        REPO, node, identity_repo="new", dry_run=False,
        allow_unsigned_dev=True, saddle="claudecode",
        isatty=lambda: True, out=lambda _m: None)

    assert run.halted and run.final_state == "HALT"
    rows = machine_mod.Journal(node).fold()
    assert rows, "a halt that journals nothing is the defect this test covers"
    assert rows[-1]["verdict"] == "HALT"
    assert rows[-1]["to"] == "G1"
    assert "EOF" in rows[-1]["reason"]
    # G1 precedes G2 always, so nothing was minted.
    assert not (node / ".intentops" / "trust" / "node-cert.json").exists()


# ---------------------------------------------------------------------------
# finding B -- the development flag masked a contradiction
# ---------------------------------------------------------------------------


def test_the_dev_flag_downgrades_absence(tmp_path):
    """A root nobody has minted yet is exactly what the flag is for."""
    repo = tmp_path / "unminted"
    (repo / "config").mkdir(parents=True)
    (repo / "config" / "trust-roots.yaml").write_text(
        "schema: trust-roots/v1\n"
        'pinned_fingerprint: "sha256:PLACEHOLDER"\n'
        "roots:\n"
        "  - id: R-TEST\n"
        "    status: proposed\n"
        '    fingerprint: "sha256:PLACEHOLDER"\n'
        '    public_key_pem: "PLACEHOLDER-NOT-A-KEY"\n',
        encoding="utf-8")
    record = provenance_mod.verify_provenance(repo, allow_unsigned_dev=True)
    status = next(c for c in record.checks if c.id == "G1.2-root-status")
    assert status.outcome == "WARN" and status.downgraded_from == "REFUSE"
    assert status.contradiction is False
    assert record.verified is False


def test_the_dev_flag_never_masks_a_key_that_disagrees_with_itself(tmp_path):
    pytest.importorskip("cryptography")
    record = provenance_mod.verify_provenance(_tampered_repo(tmp_path),
                                              allow_unsigned_dev=True)
    rep = next(c for c in record.checks if c.id == "G1.4-representations")
    assert rep.outcome == "HALT", "a contradiction is not a warning"
    assert rep.contradiction is True
    assert rep.downgraded_from is None
    assert "fingerprint disagrees" in rep.reason
    assert "did:key disagrees" in rep.reason
    assert rep.id in [c.id for c in record.blocking]


def test_g1_halts_on_a_contradiction_even_with_the_flag_open(tmp_path):
    pytest.importorskip("cryptography")
    ctx = machine_mod.GenesisContext(
        repo_root=_tampered_repo(tmp_path), node_root=tmp_path / "node",
        dry_run=True, allow_unsigned_dev=True, isatty=lambda: False,
        out=lambda _m: None)
    with pytest.raises(Halt) as exc:
        machine_mod.g1_provenance(ctx)
    assert "G1 refused" in str(exc.value)
    assert "CONTRADICTION" in (exc.value.remedy or "")
    assert UNSIGNED_DEV_ENV in (exc.value.remedy or "")


def test_a_changed_imprint_byte_is_a_contradiction(tmp_path, monkeypatch):
    """The manifest and the bundle disagreeing is never a warning."""
    class _Stub:
        @staticmethod
        def check(_root):
            return False, ["CHANGED    genesis/imprint/IMPRINT.md -- "
                           "manifest aaaa/1B, disk bbbb/2B"]

    monkeypatch.setattr(provenance_mod, "_load_build_manifest",
                        lambda _root: _Stub())
    check = provenance_mod._check_imprint_hashes(tmp_path)
    assert check.outcome == "HALT" and check.contradiction is True


def test_an_unclaimed_artifact_is_drift_and_not_a_contradiction(tmp_path,
                                                                monkeypatch):
    class _Stub:
        @staticmethod
        def check(_root):
            return False, ["UNCLAIMED  genesis/imprint/extra.md -- hashed, "
                           "but no I1-I9 bundle names it"]

    monkeypatch.setattr(provenance_mod, "_load_build_manifest",
                        lambda _root: _Stub())
    check = provenance_mod._check_imprint_hashes(tmp_path)
    assert check.outcome == "HALT" and check.contradiction is False


def test_an_unparseable_roots_file_is_a_contradiction(tmp_path):
    repo = tmp_path / "broken"
    (repo / "config").mkdir(parents=True)
    (repo / "config" / "trust-roots.yaml").write_text("roots: [\n  - {\n",
                                                      encoding="utf-8")
    check, doc = provenance_mod._check_roots_file(repo)
    assert doc is None
    assert check.outcome == "HALT" and check.contradiction is True


def test_an_absent_roots_file_is_not_a_contradiction(tmp_path):
    check, doc = provenance_mod._check_roots_file(tmp_path / "nothing-here")
    assert doc is None
    assert check.outcome == "HALT" and check.contradiction is False


# ---------------------------------------------------------------------------
# finding C -- the override was an environment variable alone
# ---------------------------------------------------------------------------


def test_the_override_is_refused_from_a_non_interactive_context(tmp_path):
    ctx = machine_mod.GenesisContext(repo_root=REPO, node_root=tmp_path,
                                     dry_run=False, allow_unsigned_dev=True,
                                     isatty=lambda: False, out=lambda _m: None)
    record = provenance_mod.verify_provenance(REPO, allow_unsigned_dev=True)
    with pytest.raises(Halt) as exc:
        machine_mod._require_unverified_boot_consent(ctx, record)
    assert "operator act" in str(exc.value)
    assert UNVERIFIED_BOOT_PHRASE in (exc.value.remedy or "")


def test_the_override_requires_the_exact_phrase(tmp_path, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO())
    ctx = machine_mod.GenesisContext(repo_root=REPO, node_root=tmp_path,
                                     dry_run=False, allow_unsigned_dev=True,
                                     isatty=lambda: True, out=lambda _m: None)
    record = provenance_mod.verify_provenance(REPO, allow_unsigned_dev=True)

    monkeypatch.setattr(builtins, "input", lambda *_a, **_k: "yes")
    with pytest.raises(Halt) as exc:
        machine_mod._require_unverified_boot_consent(ctx, record)
    assert "phrase was not typed" in str(exc.value)

    monkeypatch.setattr(builtins, "input",
                        lambda *_a, **_k: UNVERIFIED_BOOT_PHRASE.upper())
    machine_mod._require_unverified_boot_consent(ctx, record)  # case-insensitive


def test_a_dry_run_records_the_override_rather_than_asking(tmp_path):
    said: list[str] = []
    ctx = machine_mod.GenesisContext(repo_root=REPO, node_root=tmp_path,
                                     dry_run=True, allow_unsigned_dev=True,
                                     isatty=lambda: False, out=said.append)
    record = provenance_mod.verify_provenance(REPO, allow_unsigned_dev=True)
    machine_mod._require_unverified_boot_consent(ctx, record)
    assert any("DRY-RUN" in line for line in said)


def test_the_unverified_banner_is_printed_exactly_once(tmp_path):
    said: list[str] = []
    machine_mod.run_genesis(REPO, tmp_path / "n", identity_repo="new",
                            dry_run=True, allow_unsigned_dev=True,
                            saddle="claudecode", isatty=lambda: False,
                            out=said.append)
    banners = [line for line in said if line == machine_mod.UNSIGNED_DEV_BANNER]
    assert len(banners) == 1, "a banner printed twice reads as two findings"


# ---------------------------------------------------------------------------
# finding D -- the halt line silently truncated its findings
# ---------------------------------------------------------------------------


def test_every_blocking_reason_reaches_the_halt_line(tmp_path):
    run = machine_mod.run_genesis(
        REPO, tmp_path / "n", identity_repo="new", dry_run=True,
        allow_unsigned_dev=False, saddle="claudecode",
        isatty=lambda: False, out=lambda _m: None)
    assert run.halted
    record = provenance_mod.verify_provenance(REPO)
    for check in record.blocking:
        assert check.id in run.halt_reason, (
            f"{check.id} was found and did not reach the line an operator "
            "reads -- the truncation defect")
    assert len(record.blocking) > 3, (
        "this repository must produce more than three blocking findings for "
        "this test to be able to catch a three-item slice")


# ---------------------------------------------------------------------------
# finding E -- lock siblings landed inside the identity repository
# ---------------------------------------------------------------------------


def test_the_identity_repo_gitignore_refuses_lock_files():
    assert "*.lock" in organs_mod._GITIGNORE


def test_a_born_identity_repo_ignores_its_own_lock_siblings(tmp_path):
    organs_mod.create_organs(tmp_path / "node", repo_root=REPO,
                             identity_repo=str(tmp_path / "id"),
                             designation="node-000", node_id="did:key:zx")
    text = (tmp_path / "id" / ".gitignore").read_text(encoding="utf-8")
    assert "*.lock" in text


def test_the_locks_organ_says_what_it_actually_holds():
    organ = next(o for o in organs_mod.BIRTH_ORGANS if o.id == "locks")
    assert "BESIDE" in organ.consumer
    assert "normally empty" in organ.consumer


# ---------------------------------------------------------------------------
# finding F -- the operator-root artifact had two names across three carriers
# ---------------------------------------------------------------------------


_OPERATOR_ROOT_ARTIFACT = ".intentops/trust/operator-root.pub.json"


def test_the_operator_root_artifact_has_one_name_everywhere():
    import yaml

    roots = (REPO / "config" / "trust-roots.yaml").read_text(encoding="utf-8")
    assert _OPERATOR_ROOT_ARTIFACT in roots
    # The superseded spelling may survive only inside the note that records the
    # correction -- a name changed with no lineage is how the drift started.
    stale = ".intentops/trust/operator-root.yaml"
    for line_no, line in enumerate(roots.splitlines(), start=1):
        if stale in line:
            window = "\n".join(roots.splitlines()[max(0, line_no - 4):line_no + 4])
            assert "corrected here" in window, (
                f"trust-roots.yaml:{line_no} still names {stale} as current")

    probes = yaml.safe_load(
        (REPO / "config" / "genesis-probes.yaml").read_text(encoding="utf-8"))
    probe = next(p for p in probes["probes"] if p["id"] == "GEN-operator")
    assert _OPERATOR_ROOT_ARTIFACT in probe["expect_in_boot"]["any_of"]

    machine_src = (REPO / "packages" / "intentops-core" / "intentops_core"
                   / "genesis" / "machine.py").read_text(encoding="utf-8")
    assert "operator-root.pub.json" in machine_src


def test_a_dry_run_writes_the_artifact_the_carriers_name(tmp_path):
    run = machine_mod.run_genesis(
        REPO, tmp_path / "n", identity_repo="new", dry_run=True,
        allow_unsigned_dev=True, saddle="claudecode",
        isatty=lambda: False, out=lambda _m: None)
    assert not run.halted, run.halt_reason
    assert (tmp_path / "n" / Path(_OPERATOR_ROOT_ARTIFACT)).is_file()


# ---------------------------------------------------------------------------
# finding G -- two boot probes read ABSENT on every clone by construction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("probe_id", ["GEN-operator", "GEN-motto"])
def test_the_boot_corpus_carries_the_facts_its_probes_ask_for(probe_id):
    import yaml

    doc = yaml.safe_load(
        (REPO / "config" / "genesis-probes.yaml").read_text(encoding="utf-8"))
    probe = next(p for p in doc["probes"] if p["id"] == probe_id)
    corpus = "\n".join(
        (REPO / rel).read_text(encoding="utf-8")
        for rel in doc["boot_corpus"]["workspace"]
        if (REPO / rel).is_file())
    assert any(marker.lower() in corpus.lower()
               for marker in probe["expect_in_boot"]["any_of"]), (
        f"{probe_id} reads ABSENT from the boot corpus: a packaging failure, "
        "not a missing organ")


# ---------------------------------------------------------------------------
# finding H (documentation honesty) -- the imprint's editorial note
# ---------------------------------------------------------------------------


def test_the_editorial_note_admits_the_redaction_and_the_omission():
    note = (REPO / "genesis" / "imprint" / "IMPRINT.md").read_text(
        encoding="utf-8").split("## Editorial note on this copy")[-1]
    assert "redacted" in note.lower(), "the note must say the name was removed"
    assert "omitted" in note.lower(), "the note must say the record was dropped"
    flat = " ".join(note.split())
    assert "not** a byte-for-byte copy" in flat, (
        "the note must not describe a redacted copy as verbatim")
    assert "Six changes" in flat, "the count in the note must match its list"
    assert "Four mechanical changes" not in note, (
        "the superseded count claimed four changes while carrying six")


def test_the_fence_allows_only_paths_that_exist():
    import yaml

    # Justified 2026-09-06 in config/exposure-fence.yaml's BLIND SPOTS block
    # and allowed_path_tokens header: NOTICE and PROVENANCE.md state the
    # repository owner's name as the copyright holder, which is on the
    # denylist (it doubles as the private estate's operator identity). Every
    # other allowed path still exempts nothing, and these two exempt nothing
    # beyond the two ids named here.
    justified = {
        "NOTICE": ["operator-given", "operator-family"],
        "PROVENANCE.md": ["operator-given", "operator-family"],
        # licence ruling 2026-09-06: copyright-holder line in the Apache appendix + README status
        "LICENSE": ["operator-given", "operator-family"],
        "README.md": ["operator-given", "operator-family"],
        "release/v0.1.0-commit.txt": ["operator-given", "operator-family"],
        "release/v0.3.0-commit.txt": ["operator-given", "operator-family"],
    }
    fence = yaml.safe_load(
        (REPO / "config" / "exposure-fence.yaml").read_text(encoding="utf-8"))
    for rel in fence["allowed_paths"]:
        assert (REPO / rel).is_file(), f"allowed_paths names a missing {rel}"
        expected = justified.get(rel, [])
        assert sorted(fence["allowed_path_tokens"][rel]) == sorted(expected), (
            f"{rel} exempts {fence['allowed_path_tokens'][rel]}, expected "
            f"{expected} -- an allowed path exempts no token beyond a stated, "
            "justified exception"
        )


# ---------------------------------------------------------------------------
# a finding of this pass -- the tagout oracle resolved carriers against the
# wrong tree when pointed at another node's ledger
# ---------------------------------------------------------------------------


def _loto_check_module():
    import importlib.util

    path = REPO / "scripts" / "ops" / "loto_check.py"
    spec = importlib.util.spec_from_file_location("_loto_check_probe", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    import sys as _sys

    _sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_a_ledger_path_implies_its_own_workspace(tmp_path):
    """Otherwise carriers are hunted under the WRONG node and reported absent.

    A true statement about the wrong tree is the most expensive kind of
    finding: it reads as a real defect in the node being inspected.
    """
    mod = _loto_check_module()
    node = tmp_path / "someone-elses-node"
    ledger = node / mod.loto.LEDGER_RELPATH
    ledger.parent.mkdir(parents=True)
    ledger.write_text("schema: loto/v1\nentries: []\n", encoding="utf-8")

    workspace, path = mod._resolve_roots(None, str(ledger))
    assert workspace == node.resolve()
    assert path == ledger.resolve()

    # An explicit workspace always wins, even when the ledger implies another.
    other = tmp_path / "explicit"
    other.mkdir()
    workspace, _p = mod._resolve_roots(str(other), str(ledger))
    assert workspace == other.resolve()


def test_a_ledger_outside_the_canonical_shape_does_not_invent_a_workspace(
        tmp_path):
    mod = _loto_check_module()
    stray = tmp_path / "somewhere" / "LEDGER.yaml"
    stray.parent.mkdir(parents=True)
    stray.write_text("schema: loto/v1\nentries: []\n", encoding="utf-8")
    workspace, _path = mod._resolve_roots(None, str(stray))
    assert workspace == Path.cwd().resolve(), (
        "an unrecognised layout falls back to the stated default, never to a "
        "guess derived from a path that does not have the shape")


# ---------------------------------------------------------------------------
# finding I -- the distribution could not be built at all
# ---------------------------------------------------------------------------


def test_the_version_file_is_a_pep440_version():
    """A non-PEP-440 VERSION makes the whole package unbuildable.

    It read ``0.1.0-genesis``, so ``pip install`` failed at metadata
    generation, the ``intentops`` console script never existed, and nothing in
    the repository noticed -- every test ran from a source checkout on
    PYTHONPATH, which is the one environment that could not fail
    (preflight-validation: measure where the code will RUN).
    """
    packaging = pytest.importorskip("packaging.version")
    import intentops_core

    raw = (REPO / "VERSION").read_text(encoding="utf-8").strip()
    assert raw, "an empty VERSION is a load-bearing field nobody filled in"
    packaging.Version(raw)                      # raises InvalidVersion if not
    assert intentops_core.read_version() == raw


def test_an_installed_package_answers_from_its_own_metadata(monkeypatch,
                                                            tmp_path):
    """No VERSION file above an installed wheel -- and that is not an error."""
    import intentops_core

    monkeypatch.setattr(intentops_core, "version_file_candidates",
                        lambda _start=None: [tmp_path / "VERSION"])
    monkeypatch.setattr(intentops_core, "installed_distribution_version",
                        lambda: "9.9.9a0+test")
    assert intentops_core.read_version() == "9.9.9a0+test"

    monkeypatch.setattr(intentops_core, "installed_distribution_version",
                        lambda: None)
    with pytest.raises(intentops_core.VersionUnavailable) as exc:
        intentops_core.read_version()
    assert "no installed distribution" in str(exc.value)


def test_an_explicit_start_is_a_filesystem_query_and_nothing_else(monkeypatch,
                                                                  tmp_path):
    """A caller asking about a DIRECTORY is never answered about the process.

    Found while fixing the install path: routing the installed-metadata
    fallback through every call made `read_version(some_empty_dir)` return the
    running interpreter's version, which is a different question.
    """
    import intentops_core

    monkeypatch.setattr(intentops_core, "installed_distribution_version",
                        lambda: "9.9.9a0+test")
    empty = tmp_path / "nowhere"
    empty.mkdir()
    monkeypatch.setattr(intentops_core, "version_file_candidates",
                        lambda _start=None: [empty / "VERSION"])
    with pytest.raises(intentops_core.VersionUnavailable):
        intentops_core.read_version(empty)


def test_a_same_named_stranger_distribution_never_answers(monkeypatch):
    """A distribution NAME is not an identity."""
    import intentops_core

    class _Stranger:
        version = "13.0.0"
        files = ["somewhere_else/__init__.py"]

        @staticmethod
        def locate_file(entry):
            return Path("/definitely/not/here") / entry

    assert intentops_core._distribution_owns_this_module(_Stranger()) is False

    monkeypatch.setattr("importlib.metadata.distribution",
                        lambda _name: _Stranger())
    assert intentops_core.installed_distribution_version() is None


def test_the_first_instance_is_named_where_the_fence_says_it_may_be():
    import yaml

    fence = yaml.safe_load(
        (REPO / "config" / "exposure-fence.yaml").read_text(encoding="utf-8"))
    allowed = set(fence["allowed_paths"])
    readme = os.path.relpath(REPO / "identity" / "README.md", REPO)
    assert readme.replace(os.sep, "/") in allowed, (
        "identity/README.md names the first instance; the fence must say so")
