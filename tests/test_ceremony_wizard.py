"""The ceremony wizard, driven end to end against a throwaway clone.

PURPOSE
    `intentops ceremony` mints the project's publishing key and rewrites the
    three carriers that pin it. There is exactly one honest way to test that:
    run the whole sitting, on real Ed25519 keys, against a real copy of this
    repository in a temporary directory, and then ask the SAME question a
    stranger asks -- does G1.7 read PASS, and does the compiled pin say
    `minted`?

    Every test here therefore drives the wizard through an injected console
    with scripted answers. The console declares its own attendance, which is
    what makes the refusal paths reachable without a terminal; it never makes
    an unattended run succeed, because the production entrypoint builds a real
    `Console` whose attendance is measured rather than declared.

WRITE MODEL
    None into this repository. Every test copies the clone into `tmp_path` and
    operates there; the real `config/trust-roots.yaml`, the real compiled pin
    and the real `docs/GENESIS.md` are asserted byte-unchanged at the end of
    the happy-path test, because a test that could mint the live root would be
    a worse hazard than the defect it is checking for.

BLIND SPOTS
    - These prove the mechanism round-trips on throwaway keys. They prove
      nothing about whether a real ceremony was performed offline, witnessed,
      or on a clean host -- no program can, which is why the wizard records
      those as attributed operator statements and this file does not assert
      them.
    - The passphrase is supplied by a scripted console. A test that can supply
      a passphrase is a shape close to the automation the gate refuses; it is
      acceptable only because attendance is a separate, independently tested
      check.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

from intentops_core.ceremony import wizard as wiz
from intentops_core.genesis import provenance as prov

REPO_ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(not prov.crypto_available(),
                                reason="cryptography is not importable")

#: What a working clone needs for `build_manifest --check`, G1, and a genesis
#: dry-run. Declared rather than "copy everything": `tests/` and `build/` are
#: several times the size of the rest and nothing under test reads them.
COPY_ENTRIES = (
    "packages", "config", "genesis", "docs", "scripts", "estate", "identity",
    "deploy", "VERSION", "pyproject.toml", "README.md", "GENESIS.md",
)

PASSPHRASE = "correct horse battery staple"

#: The preflight answers, in the order the wizard asks for them.
PREFLIGHT_ANSWERS = [
    "standard",                       # assurance level
    "A. Operator",                    # operator
    "",                               # witness (none, standard level)
    "the operator's own desk",        # location
    "a laptop, freshly booted",       # machine
    "cable unplugged, radio off",     # network
    "an encrypted USB volume",        # storage medium
    "a password manager",             # passphrase custody
]


def _copy_clone(dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache",
                                    ".ruff_cache")
    for name in COPY_ENTRIES:
        src = REPO_ROOT / name
        if not src.exists():  # pragma: no cover - the clone is complete
            continue
        if src.is_dir():
            shutil.copytree(src, dest / name, ignore=ignore)
        else:
            shutil.copy2(src, dest / name)
    return dest


@pytest.fixture()
def clone(tmp_path: Path) -> Path:
    return _copy_clone(tmp_path / "clone")


def _console(answers: List[str], secrets: List[str], **kw: Any
             ) -> wiz.ScriptedConsole:
    return wiz.ScriptedConsole(answers, secrets, **kw)


def _happy_console(**kw: Any) -> wiz.ScriptedConsole:
    return _console(PREFLIGHT_ANSWERS + [wiz.CARRIER_PHRASE],
                    [PASSPHRASE, PASSPHRASE], **kw)


# ---------------------------------------------------------------------------
# the instrument's own selftest
# ---------------------------------------------------------------------------


def test_selftest_every_refusal_can_fire() -> None:
    ok, report = wiz.selftest()
    assert ok, report
    assert "0 failed" in report


def test_remedy_table_covers_every_declared_failure() -> None:
    doc = (REPO_ROOT / "docs" / "CEREMONY-REMEDIATION.md").read_text(
        encoding="utf-8")
    for remedy_id, row in wiz.REMEDIES.items():
        assert f"`{remedy_id}`" in doc, f"{remedy_id} has no row in the doc"
        assert f"| {row['code']} |" in doc, f"{remedy_id}'s exit code is absent"


# ---------------------------------------------------------------------------
# attendance -- the refusals that make this attended-only
# ---------------------------------------------------------------------------


def test_non_tty_is_refused(clone: Path, tmp_path: Path) -> None:
    console = _happy_console(is_tty=False)
    wizard = wiz.Wizard(clone, console, out_path=tmp_path / "medium")
    with pytest.raises(wiz.CeremonyRefused) as exc:
        wizard.execute()
    assert exc.value.remedy_id == "not-attended"
    assert exc.value.code == 10
    assert not (tmp_path / "medium").exists()


def test_ci_is_refused(clone: Path, tmp_path: Path) -> None:
    console = _happy_console(env={"CI": "1"})
    wizard = wiz.Wizard(clone, console, out_path=tmp_path / "medium")
    with pytest.raises(wiz.CeremonyRefused) as exc:
        wizard.execute()
    assert exc.value.remedy_id == "ci-set"
    assert exc.value.code == 11


def test_scripted_eof_is_a_refusal_not_a_default() -> None:
    console = _console([], [])
    with pytest.raises(wiz.CeremonyRefused) as exc:
        console.ask("anything")
    assert exc.value.remedy_id == "refused"


# ---------------------------------------------------------------------------
# the medium
# ---------------------------------------------------------------------------


def test_out_path_inside_a_repository_is_refused(clone: Path) -> None:
    # A clone with a .git directory is what the walk-up check looks for, and
    # the whole point is that it refuses ANY repository, not only this one.
    (clone / ".git").mkdir()
    inside = clone / "keys"
    wizard = wiz.Wizard(clone, _happy_console(), out_path=inside)
    with pytest.raises(wiz.CeremonyRefused) as exc:
        wizard.execute()
    assert exc.value.remedy_id == "medium-inside-repo"
    assert exc.value.code == 30
    assert not inside.exists(), "the refusal must precede any directory creation"


# ---------------------------------------------------------------------------
# the passphrase
# ---------------------------------------------------------------------------


def test_passphrase_mismatch_is_retried_then_refused(clone: Path,
                                                     tmp_path: Path) -> None:
    console = _console(
        PREFLIGHT_ANSWERS,
        ["a-long-enough-one", "a-different-one",
         "a-long-enough-one", "another-different-one",
         "a-long-enough-one", "yet-another-one"])
    wizard = wiz.Wizard(clone, console, out_path=tmp_path / "medium")
    with pytest.raises(wiz.CeremonyRefused) as exc:
        wizard.execute()
    assert exc.value.remedy_id == "passphrase-mismatch"
    assert exc.value.code == 41
    # three attempts were actually made, not one
    assert sum("did not match" in line for line in console.transcript) == 3
    assert not list((tmp_path / "medium").glob("*.key"))


def test_short_passphrase_is_refused(clone: Path, tmp_path: Path) -> None:
    console = _console(PREFLIGHT_ANSWERS, ["short", "short", "short"])
    wizard = wiz.Wizard(clone, console, out_path=tmp_path / "medium")
    with pytest.raises(wiz.CeremonyRefused) as exc:
        wizard.execute()
    assert exc.value.remedy_id == "passphrase-mismatch"
    assert any("too short" in line for line in console.transcript)


# ---------------------------------------------------------------------------
# the three-carrier edit
# ---------------------------------------------------------------------------


def test_carriers_are_never_written_without_the_typed_phrase(
        clone: Path, tmp_path: Path) -> None:
    before = {
        p: (clone / p).read_text(encoding="utf-8")
        for p in (wiz.ROOTS_RELPATH, wiz.PIN_RELPATH, wiz.GENESIS_DOC_RELPATH)
    }
    console = _console(PREFLIGHT_ANSWERS + ["yes please"],
                       [PASSPHRASE, PASSPHRASE])
    wizard = wiz.Wizard(clone, console, out_path=tmp_path / "medium")
    with pytest.raises(wiz.CeremonyRefused) as exc:
        wizard.execute()
    assert exc.value.remedy_id == "carrier-not-confirmed"
    assert exc.value.code == 71
    for relpath, text in before.items():
        assert (clone / relpath).read_text(encoding="utf-8") == text, \
            f"{relpath} was written despite the phrase not being typed"
    # the diff WAS shown -- a refusal the operator cannot review is useless
    assert any(line.strip().startswith("+++") for line in console.transcript)


# ---------------------------------------------------------------------------
# the happy path -- the acceptance test for the whole wizard
# ---------------------------------------------------------------------------


@pytest.fixture()
def real_carriers() -> Dict[Path, str]:
    return {p: (REPO_ROOT / p).read_text(encoding="utf-8")
            for p in (wiz.ROOTS_RELPATH, wiz.PIN_RELPATH,
                      wiz.GENESIS_DOC_RELPATH)}


def test_happy_path_standard_level(clone: Path, tmp_path: Path,
                                   real_carriers: Dict[Path, str]) -> None:
    medium = tmp_path / "medium"
    console = _happy_console()
    wizard = wiz.Wizard(clone, console, out_path=medium)

    assert wizard.execute() == 0

    transcript = "\n".join(console.transcript)

    # 1. G1.7 PASS in the temp tree, from a fresh process with the development
    #    flag stripped. This is the acceptance test the runbook names.
    assert "G1.7-imprint-signature : PASS" in transcript
    assert wizard._data("verify")["g17"] == "PASS"

    # 2. a throwaway node reached G7 WITHOUT the development flag
    assert wizard._data("verify")["genesis_final_state"] == "G7"

    # 3. the compiled pin in the CLONE says minted, and carries a real digest
    pin_text = (clone / wiz.PIN_RELPATH).read_text(encoding="utf-8")
    assert 'PIN_STATE: str = "minted"' in pin_text
    assert "ROOT_FINGERPRINT: str = \"sha256:" in pin_text

    # 4. all three carriers agree on the same fingerprint
    root = wizard._data("mint-root")
    fingerprint = root["fingerprint"]
    assert fingerprint in pin_text
    assert fingerprint in (clone / wiz.ROOTS_RELPATH).read_text(encoding="utf-8")
    doc = (clone / wiz.GENESIS_DOC_RELPATH).read_text(encoding="utf-8")
    assert root["authority_string"] in doc
    assert "sha256:PLACEHOLDER" not in doc.split(wiz.DOC_BEGIN)[1].split(
        wiz.DOC_END)[0]

    # 5. both keys exist on the medium, encrypted, and the record is beside them
    for key_id in (wiz.ROOT_ID, wiz.INTERMEDIATE_ID):
        assert (medium / f"{key_id}.key").is_file()
    record = next(medium.glob("ceremony-record-*.md"))
    record_text = record.read_text(encoding="utf-8")
    assert root["authority_string"] in record_text
    assert "operator states: A. Operator" in record_text
    assert "cable unplugged, radio off" in record_text
    # the tool's own observation rides beside the operator's statement
    assert "default_route_present" in record_text
    # a failed `git rev-parse` is recorded as unknown, never as its own error
    # text -- an error string in the tool-version field looks like an answer
    assert "fatal:" not in record_text
    assert "unknown (not a git repository" in record_text

    # 6. the journal exists in both places and carries no secret
    for path in (medium / "ceremony-state.json",
                 clone / wiz.CEREMONY_STATE_RELPATH):
        state = json.loads(path.read_text(encoding="utf-8"))
        assert state["schema"] == wiz.STATE_SCHEMA
        assert all(state["steps"][s]["status"] == "complete" for s in wiz.STEPS)
        assert PASSPHRASE not in path.read_text(encoding="utf-8")

    # 7. nothing that could be a private key appears anywhere in the clone, and
    #    nothing in the record or the journal is a passphrase
    assert PASSPHRASE not in transcript
    findings = _trust_material_findings(clone)
    assert findings == [], findings
    assert PASSPHRASE not in record_text

    # The medium legitimately holds .key files -- scanning it whole would flag
    # those by design. The question worth asking is narrower and sharper: do the
    # two artifacts that LEAVE the medium (the record, which is meant to be
    # shown, and the journal, which is copied into the repository) carry
    # anything key-shaped? Scanned with the repository's own scanner, not by eye.
    for artifact in (record, medium / "ceremony-state.json",
                     clone / wiz.CEREMONY_STATE_RELPATH):
        text = artifact.read_text(encoding="utf-8")
        assert _trust_material_scan(artifact.name, text) == [], artifact
        assert "PRIVATE KEY" not in text
        assert PASSPHRASE not in text

    # 8. the REAL repository's carriers are byte-unchanged
    for relpath, text in real_carriers.items():
        assert (REPO_ROOT / relpath).read_text(encoding="utf-8") == text


def test_resume_after_a_failure_does_not_remint_the_root(
        clone: Path, tmp_path: Path) -> None:
    medium = tmp_path / "medium"

    # Fail at step (e): the intermediate mint. Injected by making the mint tool
    # raise on its SECOND call -- the shape a real medium fault would take.
    console = _happy_console()
    wizard = wiz.Wizard(clone, console, out_path=medium)
    real_load = wiz.load_mint_tool

    class _FailSecondMint:
        def __init__(self, inner: Any) -> None:
            self._inner = inner
            self.calls = 0

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

        def mint(self, out: Path, passphrase: str) -> Dict[str, Any]:
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("injected medium fault")
            return self._inner.mint(out, passphrase)

    wizard.screen_0_gate()
    wizard.mint = _FailSecondMint(real_load(clone))
    wizard.step_preflight()
    wizard.step_medium()
    wizard.step_passphrase()
    first_root = wizard.step_mint_root()
    with pytest.raises(wiz.CeremonyRefused) as exc:
        wizard.step_mint_intermediate()
    assert exc.value.remedy_id == "mint-failed"
    assert exc.value.code == 50

    root_key = medium / f"{wiz.ROOT_ID}.key"
    root_bytes_before = root_key.read_bytes()
    assert not (medium / f"{wiz.INTERMEDIATE_ID}.key").exists()

    # Now resume. The passphrase is asked for again (it is never stored) and is
    # verified against the key already on the medium; the root is NOT re-minted.
    # A resumed run replays the recorded preflight rather than re-asking it,
    # so the only answer it still needs is the carrier phrase.
    resume_console = _console([wiz.CARRIER_PHRASE], [PASSPHRASE])
    resumed = wiz.Wizard(clone, resume_console, out_path=medium, resume=True)
    assert resumed.execute() == 0

    assert root_key.read_bytes() == root_bytes_before, \
        "the root key was re-minted on resume"
    assert resumed._data("mint-root")["fingerprint"] == first_root["fingerprint"]
    joined = "\n".join(resume_console.transcript)
    assert "already minted in this ceremony" in joined
    assert "passphrase verified against the minted key" in joined
    assert resumed._data("verify")["g17"] == "PASS"


def test_resume_with_a_wrong_passphrase_is_refused(clone: Path,
                                                   tmp_path: Path) -> None:
    medium = tmp_path / "medium"
    wizard = wiz.Wizard(clone, _happy_console(), out_path=medium)
    wizard.screen_0_gate()
    wizard.mint = wiz.load_mint_tool(clone)
    wizard.step_preflight()
    wizard.step_medium()
    wizard.step_passphrase()
    wizard.step_mint_root()

    console = _console(PREFLIGHT_ANSWERS, ["not-the-passphrase"] * 3)
    resumed = wiz.Wizard(clone, console, out_path=medium, resume=True)
    with pytest.raises(wiz.CeremonyRefused) as exc:
        resumed.execute()
    assert exc.value.remedy_id == "passphrase-wrong"
    assert exc.value.code == 42


def test_resume_without_a_journal_is_refused(clone: Path,
                                             tmp_path: Path) -> None:
    wizard = wiz.Wizard(clone, _happy_console(), out_path=tmp_path / "medium",
                        resume=True)
    with pytest.raises(wiz.CeremonyRefused) as exc:
        wizard.execute()
    assert exc.value.remedy_id == "state-unreadable"
    assert exc.value.code == 91


# ---------------------------------------------------------------------------
# the CLI surface
# ---------------------------------------------------------------------------


def test_cli_registers_the_verb_and_its_selftest_passes() -> None:
    from intentops_core import cli as cli_mod

    parser = cli_mod.build_parser()
    args = parser.parse_args(["ceremony", "--selftest"])
    assert args.command == "ceremony"
    assert cli_mod.main(["ceremony", "--selftest"]) == 0


def test_cli_ceremony_refuses_a_non_tty_end_to_end() -> None:
    """The production path builds a real console, whose attendance is measured.

    Under pytest, stdin is not a terminal -- which is exactly the condition the
    ceremony refuses. This is the one test that exercises the REAL attendance
    check rather than a declared one.
    """
    from intentops_core import cli as cli_mod

    assert cli_mod.main(["--repo-root", str(REPO_ROOT), "ceremony"]) in (10, 11)


def test_a_null_device_stdin_is_refused_before_any_prompt() -> None:
    """The clause a TTY-only check cannot enforce, measured in a subprocess.

    On Windows `NUL` is a character device, so `isatty()` reports it as a
    terminal. Before the console probe existed, this exact invocation walked
    past the attendance gate, ran the preflight, and reached the assurance-level
    prompt before EOF stopped it -- which means the refusal that makes this verb
    attended-only was not the thing doing the refusing.
    """
    env = dict(os.environ)
    env.pop("CI", None)
    env["PYTHONPATH"] = str(REPO_ROOT / "packages" / "intentops-core")
    proc = subprocess.run(
        [sys.executable, "-m", "intentops_core.cli",
         "--repo-root", str(REPO_ROOT), "ceremony"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, env=env, cwd=str(REPO_ROOT),
        timeout=120)
    assert proc.returncode == 10, proc.stdout
    assert "[not-attended]" in proc.stdout
    # it stopped at the gate, not at a prompt further in
    assert "Which level is this sitting" not in proc.stdout


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _trust_material() -> Any:
    """The repository's own key-shape scanner, imported by path."""
    path = REPO_ROOT / "scripts" / "ops" / "trust_material_check.py"
    spec = importlib.util.spec_from_file_location("_tmc_for_tests", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _trust_material_findings(root: Path) -> List[str]:
    """Scan a whole tree for private-key and credential shapes."""
    findings, _scanned, _skipped = _trust_material().scan_tree(root)
    return [str(f) for f in findings]


def _trust_material_scan(name: str, text: str) -> List[str]:
    """Scan one artifact's text for the same shapes."""
    return [str(f) for f in _trust_material().scan_text(name, text)]
