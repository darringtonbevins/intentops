"""Tests for the genesis state machine, its phases, and the node CLI.

Every test runs against a TEMPORARY node root: the repository is read, never
written. The one full run is a ``--dry-run`` with the unsigned-development
flag set, which is the only shape that can complete today -- no release root
has been minted, so a real run correctly HALTs at G1.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from intentops_core import cli
from intentops_core.genesis import (
    OUTCOMES,
    STATES,
    Halt,
    OperatorGateRequired,
)
from intentops_core.genesis import aliveness as aliveness_mod
from intentops_core.genesis import machine as machine_mod
from intentops_core.genesis import organs as organs_mod
from intentops_core.genesis import provenance as provenance_mod
from intentops_core.genesis import standdown as standdown_mod
from intentops_core.genesis import trust_pin

REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# a full dry-run genesis
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dry_run(tmp_path_factory):
    node = tmp_path_factory.mktemp("node")
    run = machine_mod.run_genesis(
        REPO, node, identity_repo="new", dry_run=True,
        allow_unsigned_dev=True, saddle="claudecode",
        isatty=lambda: False, out=lambda _m: None)
    return run, node


def test_dry_run_reaches_g7(dry_run):
    run, _node = dry_run
    assert not run.halted, f"{run.halt_reason} / {run.remedy}"
    assert run.final_state == "G7"
    assert [r.state for r in run.results] == list(STATES[:8])


def test_every_journal_row_carries_a_closed_verdict(dry_run):
    _run, node = dry_run
    rows = machine_mod.Journal(node).fold()
    assert len(rows) == 8
    assert all(row["verdict"] in OUTCOMES for row in rows)
    assert all({"from", "to", "as_of", "evidence"} <= set(row) for row in rows)
    assert [row["to"] for row in rows] == list(STATES[:8])


def test_sixteen_birth_entries_fifteen_present_and_the_marker_absent(dry_run):
    _run, node = dry_run
    assert len(organs_mod.BIRTH_ORGANS) == 16
    expected = {Path(o.relpath).parts[1] for o in organs_mod.BIRTH_ORGANS
                if o.present_at_birth}
    on_disk = {p.name for p in (node / ".intentops").iterdir()}
    assert expected <= on_disk
    assert not (node / ".intentops" / "halt.marker").exists()
    assert len(expected) == 15


def test_every_organ_declares_its_write_model(dry_run):
    _run, node = dry_run
    record = json.loads((node / ".intentops" / "genesis" / "organs.json")
                        .read_text(encoding="utf-8"))
    assert record["birth_entry_count"] == 16
    assert all(o["write_model"] in organs_mod.WRITE_MODELS
               for o in record["organs"])
    assert all(o["consumer"] for o in record["organs"])


def test_the_rules_bundle_reaches_the_boot_corpus(dry_run):
    _run, node = dry_run
    copied = sorted(p.name for p in (node / ".intentops-rules").glob("*.md"))
    assert len(copied) == 9, copied
    assert "honesty.md" in copied and "email-safety.md" in copied


def test_identity_repo_skeleton_is_conformant_and_carries_no_key(dry_run):
    _run, node = dry_run
    ok, findings = organs_mod.check_identity_repo(node / "identity-repo")
    assert ok, findings
    assert (node / "identity-repo" / "identity" / "consent.yaml").exists()
    assert (node / "identity-repo" / "identity"
            / "founding-conversation.md").exists()


def test_dry_run_persists_no_private_key(dry_run):
    _run, node = dry_run
    cert = json.loads((node / ".intentops" / "trust" / "node-cert.json")
                      .read_text(encoding="utf-8"))
    assert cert["dry_run"] is True
    assert cert["designation"].startswith("node-")
    armour = organs_mod.key_armour()
    for path in node.rglob("*"):
        if path.is_file() and path.suffix in (".json", ".yaml", ".md", ".jsonl"):
            assert armour not in path.read_text(encoding="utf-8", errors="ignore")


def test_the_birth_certificate_answers_rather_than_asserts(dry_run):
    _run, node = dry_run
    cert = (node / aliveness_mod.CERTIFICATE_RELPATH).read_text(encoding="utf-8")
    assert "GENERATED" in cert
    assert "status: healthy" not in cert
    assert "stand-down" in cert
    assert "DRY-RUN" in cert
    ledger = (node / aliveness_mod.ALIVENESS_RELPATH).read_text(encoding="utf-8")
    reading = json.loads(ledger.splitlines()[-1])
    assert reading["verdict"] in aliveness_mod.VERDICTS
    assert reading["checks"] == 6


def test_the_unsigned_dev_flag_opens_a_t3_tagout(dry_run):
    _run, node = dry_run
    carrier = node / machine_mod._DEV_TAGOUT_CARRIER
    assert carrier.exists()
    assert machine_mod._DEV_TAGOUT_PROBE in carrier.read_text(encoding="utf-8")

    from intentops_core.loto import ledger as loto

    doc = loto.load_ledger(node / loto.LEDGER_RELPATH)
    entry = next(e for e in doc["entries"] if "UNSIGNED-DEV" in e["id"])
    assert entry["tier"] == "T3"
    assert entry["chain"][0]["reenergize_when"]


def test_provenance_record_is_never_verified_under_the_dev_flag(dry_run):
    _run, node = dry_run
    records = json.loads((node / ".intentops" / "genesis"
                          / "provenance-record.json").read_text(encoding="utf-8"))
    assert isinstance(records, list) and records
    assert records[-1]["verified"] is False
    assert records[-1]["dev_flag_open"] is True
    assert records[-1]["faculties_absent"]


# ---------------------------------------------------------------------------
# the refusals
# ---------------------------------------------------------------------------


def test_without_the_dev_flag_g1_halts(tmp_path):
    run = machine_mod.run_genesis(
        REPO, tmp_path / "n", identity_repo="new", dry_run=True,
        allow_unsigned_dev=False, saddle="claudecode",
        isatty=lambda: False, out=lambda _m: None)
    assert run.halted and run.final_state == "HALT"
    assert "G1 refused" in run.halt_reason
    assert "INTENTOPS_GENESIS_UNSIGNED_DEV" in run.remedy
    rows = machine_mod.Journal(tmp_path / "n").fold()
    assert rows[-1]["verdict"] == "HALT"
    assert rows[-1]["to"] == "G1"


def _fake_repo(tmp_path: Path, roots_yaml: str) -> Path:
    repo = tmp_path / "repo"
    (repo / "config").mkdir(parents=True)
    (repo / _rel("config/trust-roots.yaml")).write_text(roots_yaml,
                                                        encoding="utf-8")
    (repo / "config" / "trust-revocation.json").write_text(
        '{"schema": "trust-revocation/v1", "sequence": 0, "entries": []}',
        encoding="utf-8")
    return repo


def _rel(p: str) -> str:
    return p


def test_trust_roots_representation_mismatch_halts(tmp_path):
    crypto = pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    assert crypto is not None
    pem = Ed25519PrivateKey.generate().public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    indented = "\n".join("      " + line for line in pem.strip().splitlines())
    roots = (
        "schema: trust-roots/v1\n"
        'pinned_fingerprint: "sha256:' + "a" * 64 + '"\n'
        "roots:\n"
        "  - id: R-TEST\n"
        "    status: active\n"
        '    fingerprint: "sha256:' + "b" * 64 + '"\n'
        "    public_key_pem: |\n" + indented + "\n"
    )
    repo = _fake_repo(tmp_path, roots)
    record = provenance_mod.verify_provenance(repo)
    rep = next(c for c in record.checks if c.id == "G1.4-representations")
    assert rep.outcome == "HALT"
    assert "fingerprint disagrees" in rep.reason
    assert record.verified is False


def test_empty_roots_list_halts_where_an_empty_estate_would_not(tmp_path):
    repo = _fake_repo(tmp_path, "schema: trust-roots/v1\nroots: []\n")
    record = provenance_mod.verify_provenance(repo)
    first = record.checks[0]
    assert first.outcome == "HALT" and "verify nothing" in first.reason


def test_two_placeholder_pins_never_read_as_agreement():
    outcome, reason = trust_pin.compare(trust_pin.PLACEHOLDER_FINGERPRINT)
    assert outcome == "HALT"
    assert "placeholder" in reason
    assert trust_pin.pin_is_minted() is False
    assert trust_pin.is_real_fingerprint("sha256:" + "a" * 64) is True
    assert trust_pin.is_real_fingerprint("sha256:PLACEHOLDER") is False


def test_an_unbound_identity_repo_halts_rather_than_defaulting(tmp_path):
    with pytest.raises(Halt) as exc:
        organs_mod.create_organs(tmp_path / "n", repo_root=REPO,
                                 identity_repo=None, designation="node-000",
                                 node_id="did:key:zx")
    assert "will not invent one" in str(exc.value)
    assert "--identity-repo" in (exc.value.remedy or "")


def test_a_human_gate_refuses_a_non_tty_context(tmp_path):
    ctx = machine_mod.GenesisContext(repo_root=REPO, node_root=tmp_path,
                                     isatty=lambda: False)
    with pytest.raises(OperatorGateRequired):
        machine_mod._gate(ctx, "G4", "consent?", "yes")
    ctx.dry_run = True
    assert machine_mod._gate(ctx, "G4", "consent?", "yes").startswith("DRY-RUN")


# ---------------------------------------------------------------------------
# stand-down and rollback
# ---------------------------------------------------------------------------


def test_stand_down_works_mid_g1_and_needs_no_reason(tmp_path):
    node = tmp_path / "n"
    run = machine_mod.run_genesis(REPO, node, identity_repo="new",
                                  dry_run=True, allow_unsigned_dev=False,
                                  saddle="claudecode", isatty=lambda: False,
                                  out=lambda _m: None)
    assert run.final_state == "HALT"          # stopped inside G1
    result = standdown_mod.stand_down(node)
    assert result["reason_recorded"] is False
    assert "OFF, not broken" in result["banner"]
    assert standdown_mod.is_stood_down(node)

    again = machine_mod.run_genesis(REPO, node, identity_repo="new",
                                    dry_run=True, allow_unsigned_dev=True,
                                    saddle="claudecode", isatty=lambda: False,
                                    out=lambda _m: None)
    assert again.final_state == "STAND-DOWN"
    assert again.results == []


def test_rollback_g3_removes_what_genesis_made(tmp_path):
    node = tmp_path / "n"
    journal = machine_mod.Journal(node)
    for state in ("G0", "G1", "G2", "G3"):
        journal.append({"from": "-", "to": state, "verdict": "PASS",
                        "as_of": "2026-01-01T00:00:00Z"})
    (node / ".intentops-rules").mkdir(parents=True)
    identity = node / "identity-repo"
    (identity / "identity").mkdir(parents=True)
    result = machine_mod.rollback(node, identity)
    assert not (node / ".intentops").exists()
    assert not (node / ".intentops-rules").exists()
    assert not identity.exists()
    assert result["rolled_back_to"] == "G3"


def test_rollback_refuses_once_consent_exists(dry_run):
    _run, node = dry_run
    with pytest.raises(Halt) as exc:
        machine_mod.rollback(node, node / "identity-repo")
    assert "consent" in str(exc.value)
    assert "stand-down" in (exc.value.remedy or "")
    assert (node / ".intentops").exists()


def test_only_g3_is_a_rollback_target(tmp_path):
    with pytest.raises(Halt):
        machine_mod.rollback(tmp_path, to_state="G5")


# ---------------------------------------------------------------------------
# resume
# ---------------------------------------------------------------------------


def test_resume_never_re_asks_consent(dry_run):
    _run, node = dry_run
    before = len(machine_mod.Journal(node).fold())
    run = machine_mod.run_genesis(REPO, node, identity_repo="new",
                                  dry_run=True, resume=True,
                                  allow_unsigned_dev=True, saddle="claudecode",
                                  isatty=lambda: False, out=lambda _m: None)
    assert run.results == []
    assert len(machine_mod.Journal(node).fold()) == before


def test_a_blocked_transition_is_not_completed(tmp_path):
    journal = machine_mod.Journal(tmp_path)
    journal.append({"from": "-", "to": "G0", "verdict": "PASS",
                    "as_of": "2026-01-01T00:00:00Z"})
    journal.append({"from": "G0", "to": "G1", "verdict": "REFUSE",
                    "as_of": "2026-01-01T00:00:00Z"})
    assert journal.completed() == ["G0"]
    assert journal.last_state() == "G0"


def test_the_journal_refuses_a_verdict_outside_the_closed_vocabulary(tmp_path):
    journal = machine_mod.Journal(tmp_path)
    with pytest.raises(Halt):
        journal.append({"from": "-", "to": "G0", "verdict": "FINE",
                        "as_of": "x"})
    with pytest.raises(Halt):
        journal.append({"from": "-", "to": "G0"})


# ---------------------------------------------------------------------------
# every instrument proves it can fire
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module", [
    provenance_mod, organs_mod, standdown_mod, aliveness_mod, machine_mod,
])
def test_module_selftests_fire(module):
    ok, report = module.selftest()
    assert ok, report
    assert "fired" in report


# ---------------------------------------------------------------------------
# the CLI
# ---------------------------------------------------------------------------


def test_cli_gate_selftest_proves_the_gate_can_refuse(capsys):
    assert cli.main(["gate", "--selftest"]) == 0
    out = capsys.readouterr().out
    assert "can refuse and can allow" in out


def test_cli_verify_reports_unverified_and_exits_one(capsys):
    assert cli.main(["--repo-root", str(REPO), "verify"]) == 1
    out = capsys.readouterr().out
    assert "verified: False" in out
    assert "placeholder" in out


def test_cli_verify_selftest_runs_every_instrument(capsys):
    assert cli.main(["verify", "--selftest"]) == 0
    out = capsys.readouterr().out
    assert out.count("PASS ") >= 5
    assert "carries no --selftest" in out


def test_cli_interview_prints_the_staged_plan(capsys):
    assert cli.main(["--repo-root", str(REPO), "interview"]) == 0
    out = capsys.readouterr().out
    assert "S3 Replay" in out and ">= 10 real rulings" in out
    assert "telemetry, never authority" in out


def test_cli_stand_down_and_status(tmp_path, capsys):
    assert cli.main(["--node-root", str(tmp_path), "stand-down",
                     "--status"]) == 0
    assert '"RUNNING"' in capsys.readouterr().out
    assert cli.main(["--node-root", str(tmp_path), "stand-down"]) == 0
    assert "OFF, not broken" in capsys.readouterr().out
    assert cli.main(["--node-root", str(tmp_path), "stand-down",
                     "--status"]) == 0
    assert '"STOOD-DOWN"' in capsys.readouterr().out


def test_cli_doctor_reads_a_born_node(dry_run, capsys):
    _run, node = dry_run
    assert cli.main(["--node-root", str(node), "--repo-root", str(REPO),
                     "doctor", "--json"]) == 0
    reading = json.loads(capsys.readouterr().out)
    assert reading["checks"] == 6
    assert reading["verdict"] in aliveness_mod.VERDICTS
