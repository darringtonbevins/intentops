"""The identity-repo contract validator.

The two fixtures do the load-bearing work: one conformant repository that must
still load, and one deliberately non-conformant repository that must be
rejected for exactly the reasons it documents. A validator with only a
conformant fixture proves nothing -- a function that returns CONFORMANT
unconditionally would pass it.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "ops"))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "identity"))

import identity_repo_check as irc  # noqa: E402
import trust_material_check as tmc  # noqa: E402

CONFORMANT = REPO_ROOT / "tests" / "fixtures" / "identity-repo-conformant"
NEGATIVE = REPO_ROOT / "tests" / "fixtures" / "identity-repo-negative"


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A writable copy of the conformant fixture."""
    work = tmp_path / "identity-repo"
    shutil.copytree(CONFORMANT, work)
    return work


def _patch_manifest(root: Path, mutate) -> None:
    path = root / "MANIFEST.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


# --------------------------------------------------------------------------
# the two fixtures
# --------------------------------------------------------------------------


def test_the_conformant_fixture_is_conformant() -> None:
    report = irc.check_repo(CONFORMANT)
    assert report.conformant, "\n".join(str(p) for p in report.problems)
    assert report.members_present == len(irc.REQUIRED_MEMBERS)


def test_the_conformant_fixture_declares_every_required_member() -> None:
    data = yaml.safe_load((CONFORMANT / "MANIFEST.yaml").read_text(encoding="utf-8"))
    declared = {row["path"] for row in data["members"]}
    assert declared == {path for path, _kind in irc.REQUIRED_MEMBERS}


def test_the_negative_fixture_is_rejected_for_its_documented_reasons() -> None:
    report = irc.check_repo(NEGATIVE)
    assert not report.conformant
    assert set(report.codes()) == {
        "MANIFEST-FALSIFIER", "BINDING", "NAME-UNRATIFIED", "MEMBER-WRITE-MODEL",
        "MEMBER-MISSING", "FOREIGN-GRANT", "ORDERING-NO-ROOT",
    }


def test_neither_fixture_carries_a_key_shape() -> None:
    """Committing a key-shaped file to prove a checker that forbids them is the
    defect the checker exists to prevent."""
    for fixture in (CONFORMANT, NEGATIVE):
        findings, scanned, _unscanned = tmc.scan_tree(fixture)
        assert scanned > 0
        assert findings == [], f"{fixture}: {[str(f) for f in findings]}"


# --------------------------------------------------------------------------
# binding precedence -- and no default at the bottom
# --------------------------------------------------------------------------


def test_binding_precedence_is_environment_then_genesis_then_node_config() -> None:
    assert irc.resolve_binding(env_value="a", genesis_argument="b",
                               node_config_value="c")[1] == "environment"
    assert irc.resolve_binding(genesis_argument="b",
                               node_config_value="c")[1] == "genesis-argument"
    assert irc.resolve_binding(node_config_value="c")[1] == "node-config"


def test_a_blank_value_does_not_win_its_rank() -> None:
    path, mechanism = irc.resolve_binding(env_value="   ", node_config_value="c")
    assert mechanism == "node-config" and path == Path("c")


def test_nothing_bound_halts_and_says_what_it_needs() -> None:
    with pytest.raises(irc.BindingError) as exc:
        irc.resolve_binding()
    assert "no default" in str(exc.value)


def test_a_declared_default_binding_is_a_violation(repo: Path) -> None:
    _patch_manifest(repo, lambda d: d["binding"].__setitem__("default", "./identity"))
    assert "BINDING" in irc.check_repo(repo).codes()


def test_a_reordered_precedence_is_a_violation(repo: Path) -> None:
    _patch_manifest(
        repo,
        lambda d: d["binding"].__setitem__(
            "precedence", ["node-config", "genesis-argument", "environment"]),
    )
    assert "BINDING" in irc.check_repo(repo).codes()


# --------------------------------------------------------------------------
# version pins
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "version, requires, ok",
    [
        ("0.1.0", ">=0.1,<0.2", True),
        ("0.1.9", ">=0.1,<0.2", True),
        ("0.2.0", ">=0.1,<0.2", False),
        ("0.0.9", ">=0.1,<0.2", False),
        ("1.0.0", "==1.0.0", True),
        ("1.0.0", "nonsense", False),
    ],
)
def test_pin_satisfaction(version: str, requires: str, ok: bool) -> None:
    assert irc.satisfies(version, requires) is ok


def test_an_unreadable_pin_is_not_a_satisfied_pin() -> None:
    assert irc.satisfies("1.0.0", ">= ") is False
    assert irc.satisfies("1.0.0", "~=1.0") is False


def test_an_incompatible_running_version_is_a_violation(repo: Path) -> None:
    report = irc.check_repo(repo, framework_version="0.9.0")
    assert "PIN-INCOMPATIBLE" in report.codes()


# --------------------------------------------------------------------------
# required members and their write models
# --------------------------------------------------------------------------


@pytest.mark.parametrize("member", [m for m, kind in irc.REQUIRED_MEMBERS if kind == "file"])
def test_every_required_file_is_required(repo: Path, member: str) -> None:
    (repo / member).unlink()
    assert "MEMBER-MISSING" in irc.check_repo(repo).codes()


def test_a_member_of_the_wrong_kind_is_named_as_such(repo: Path) -> None:
    target = repo / "twin"
    shutil.rmtree(target)
    target.write_text("a file where a directory belongs\n", encoding="utf-8")
    assert "MEMBER-KIND" in irc.check_repo(repo).codes()


def test_an_undeclared_write_model_is_a_hard_exit(repo: Path) -> None:
    def apply(data):
        data["members"][0]["write_model"] = "however it happens"

    _patch_manifest(repo, apply)
    assert "MEMBER-WRITE-MODEL" in irc.check_repo(repo).codes()


def test_a_member_with_no_write_model_is_a_violation(repo: Path) -> None:
    def apply(data):
        data["members"][0].pop("write_model")

    _patch_manifest(repo, apply)
    assert "MEMBER-WRITE-MODEL" in irc.check_repo(repo).codes()


@pytest.mark.parametrize("journal", irc.CONTRACT_JOURNALS)
def test_a_contract_journal_must_be_append_only(repo: Path, journal: str) -> None:
    def apply(data):
        for row in data["members"]:
            if row["path"] == journal:
                row["write_model"] = "locked-whole-file"

    _patch_manifest(repo, apply)
    assert "MEMBER-JOURNAL" in irc.check_repo(repo).codes()


def test_a_required_member_missing_from_the_manifest_is_a_violation(repo: Path) -> None:
    def apply(data):
        data["members"] = [r for r in data["members"] if r["path"] != "trust"]

    _patch_manifest(repo, apply)
    assert "MEMBER-UNDECLARED" in irc.check_repo(repo).codes()


def test_the_founding_conversation_is_absent_at_birth_and_that_is_a_note(repo: Path) -> None:
    report = irc.check_repo(repo)
    assert report.conformant
    assert any(n.code == "NOTE-UNWRITTEN" for n in report.notes)


# --------------------------------------------------------------------------
# the manifest's own honesty fields
# --------------------------------------------------------------------------


def test_a_blank_falsifier_is_a_violation(repo: Path) -> None:
    _patch_manifest(repo, lambda d: d["falsifier"].__setitem__("reopens_when", "  "))
    assert "MANIFEST-FALSIFIER" in irc.check_repo(repo).codes()


def test_a_missing_bound_node_key_is_a_violation(repo: Path) -> None:
    _patch_manifest(repo, lambda d: d.pop("bound_node"))
    assert "MANIFEST-FIELD" in irc.check_repo(repo).codes()


def test_an_empty_bound_node_is_allowed_because_unbound_is_a_state(repo: Path) -> None:
    _patch_manifest(repo, lambda d: d.__setitem__("bound_node", ""))
    assert irc.check_repo(repo).conformant


def test_a_name_with_no_ratification_is_a_violation(repo: Path) -> None:
    _patch_manifest(repo, lambda d: d["identity"].__setitem__("name", "a chosen name"))
    assert "NAME-UNRATIFIED" in irc.check_repo(repo).codes()


def test_a_ratified_name_is_accepted(repo: Path) -> None:
    def apply(data):
        data["identity"]["name"] = "a ratified name"
        data["identity"]["name_ratified_by"] = "sha256:PLACEHOLDER-OPERATOR-ROOT"

    _patch_manifest(repo, apply)
    assert irc.check_repo(repo).conformant


def test_an_unparseable_manifest_is_named_not_guessed(repo: Path) -> None:
    (repo / "MANIFEST.yaml").write_text("::: not yaml :::\n{[", encoding="utf-8")
    assert "MANIFEST-UNPARSEABLE" in irc.check_repo(repo).codes()


def test_a_wrong_schema_is_refused(repo: Path) -> None:
    _patch_manifest(repo, lambda d: d.__setitem__("schema", "identity-repo/v9"))
    assert "MANIFEST-SCHEMA" in irc.check_repo(repo).codes()


def test_a_missing_repository_is_named(tmp_path: Path) -> None:
    assert "REPO-MISSING" in irc.check_repo(tmp_path / "nowhere").codes()


# --------------------------------------------------------------------------
# foreign authority
# --------------------------------------------------------------------------


def _write_ruling(repo: Path, **fields) -> None:
    row = {"event": "rule", "id": "ORD-test000001",
           "statement": "A fictional ruling."}
    row.update(fields)
    (repo / "wisdom" / "orderings.jsonl").write_text(
        json.dumps(row) + "\n", encoding="utf-8")


def test_an_active_foreign_ruling_is_refused(repo: Path) -> None:
    _write_ruling(repo, operator_root="sha256:ANOTHER-ROOT", status="active",
                  on_match="escalate")
    assert "FOREIGN-GRANT" in irc.check_repo(repo).codes()


def test_a_permissive_foreign_ruling_is_refused(repo: Path) -> None:
    _write_ruling(repo, operator_root="sha256:ANOTHER-ROOT", status="proposed",
                  on_match="permit")
    assert "FOREIGN-GRANT" in irc.check_repo(repo).codes()


def test_a_foreign_ruling_in_the_contract_shape_is_accepted(repo: Path) -> None:
    """Proposed and escalate-only: kept with lineage, never deleted, never
    answering a live governing question."""
    _write_ruling(repo, operator_root="sha256:ANOTHER-ROOT", status="proposed",
                  on_match="escalate")
    report = irc.check_repo(repo)
    assert report.conformant, "\n".join(str(p) for p in report.problems)
    assert report.ordering_rows == 1


def test_our_own_active_ruling_is_accepted(repo: Path) -> None:
    _write_ruling(repo, operator_root="sha256:PLACEHOLDER-OPERATOR-ROOT",
                  status="active", on_match="permit")
    assert irc.check_repo(repo).conformant


def test_a_ruling_that_names_no_root_is_reported_not_assumed_ours(repo: Path) -> None:
    _write_ruling(repo, status="active")
    assert "ORDERING-NO-ROOT" in irc.check_repo(repo).codes()


def test_an_unreadable_ruling_row_is_surfaced(repo: Path) -> None:
    (repo / "wisdom" / "orderings.jsonl").write_text("{not json\n", encoding="utf-8")
    assert "ORDERING-UNPARSEABLE" in irc.check_repo(repo).codes()


def test_a_foreign_grant_in_the_manifest_is_refused(repo: Path) -> None:
    def apply(data):
        data["grants"] = [{"id": "G1", "granted_by": "sha256:ANOTHER-ROOT",
                           "scope": "delegated autonomy"}]

    _patch_manifest(repo, apply)
    assert "FOREIGN-GRANT" in irc.check_repo(repo).codes()


def test_a_grant_naming_no_root_is_refused(repo: Path) -> None:
    _patch_manifest(repo, lambda d: d.__setitem__("grants", [{"id": "G1"}]))
    assert "ORDERING-NO-ROOT" in irc.check_repo(repo).codes()


# --------------------------------------------------------------------------
# private-key shapes, anywhere in the repository
# --------------------------------------------------------------------------


def test_a_private_key_anywhere_in_the_repo_is_refused(repo: Path) -> None:
    (repo / "trust" / "leaked.txt").write_text(
        tmc._PEM_OPEN + " " + tmc._PEM_PRIVATE_TAIL + "\nMC4CAQAw" + "A" * 32 + "\n",
        encoding="utf-8",
    )
    assert "KEY-SHAPE" in irc.check_repo(repo).codes()


def test_a_key_shaped_filename_anywhere_in_the_repo_is_refused(repo: Path) -> None:
    (repo / "continuity" / "memory" / "operator.key").write_text("x\n", encoding="utf-8")
    assert "KEY-SHAPE" in irc.check_repo(repo).codes()


def test_a_gitignore_that_refuses_nothing_is_a_violation(repo: Path) -> None:
    (repo / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    assert "GITIGNORE-PATTERNS" in irc.check_repo(repo).codes()


# --------------------------------------------------------------------------
# the selftest itself
# --------------------------------------------------------------------------


def test_the_selftest_passes() -> None:
    assert irc.run_selftest(CONFORMANT, NEGATIVE) == 0
