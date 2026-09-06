"""The documentation-truth check, and the defect it exists for.

The defect: ``pip install -e packages/intentops-core`` shipped in README.md and
in docs/quick-start.md as the one command every stranger runs. There is no
packaging file at that path, so it could not work -- and the directory *does*
exist, so a plain existence check would have passed it. The suite was green
throughout, because the suite installs nothing.

Every red case below is constructed under ``tmp_path``. Nothing here plants a
broken claim inside the repository.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "docs"))

import claims_check as cc  # noqa: E402


# --------------------------------------------------------------------------
# the selftest is the contract; a detector that cannot fire is not evidence
# --------------------------------------------------------------------------


def test_the_selftest_passes_and_every_path_fires() -> None:
    ok, text = cc.selftest()
    assert ok, text
    # the summary states its own denominator
    assert "paths behaved as declared" in text
    assert text.count("PASS  ") >= 20


def test_a_path_with_no_claim_word_is_still_checked(tmp_path: Path) -> None:
    """There is no claim-marker gate, and that is the point.

    A marker gate was tried first and measured against this repository: it let
    nine of twelve real path claims through, including four broken module
    pointers on adjacent table rows, because whether a row happens to contain
    the word "reads" is uncorrelated with whether its path is real.
    """
    (tmp_path / "A.md").write_text(
        "Someday we might add `packages/nope/pyproject.toml`.\n",
        encoding="utf-8")
    report = cc.check(tmp_path, ["A.md"])
    assert len(report.findings) == 1
    assert report.claim_lines == 0  # labelling still works; it just does not gate


def test_a_link_resolves_from_its_own_documents_directory(tmp_path: Path) -> None:
    """Resolving only from the repository root called three correct links broken."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "sib.md").write_text("x\n", encoding="utf-8")
    (docs / "A.md").write_text("See [it](sib.md).\n", encoding="utf-8")
    assert cc.check(tmp_path, ["docs/A.md"]).clean


def test_a_schema_id_is_not_a_path(tmp_path: Path) -> None:
    (tmp_path / "A.md").write_text(
        "Schema `operator-root/v1`.\n", encoding="utf-8")
    report = cc.check(tmp_path, ["A.md"])
    assert report.clean
    assert report.exempted[cc._SCHEMA_ID] == 1


def test_an_angle_bracket_placeholder_is_not_a_path(tmp_path: Path) -> None:
    """The house convention for placeholders inside a code block.

    ``N/N`` is slash-shaped and reads as a path; ``<n>/<n>`` does not.
    """
    (tmp_path / "A.md").write_text(
        "```\nprobes <n>/<n> PASS\n```\n", encoding="utf-8")
    assert cc.check(tmp_path, ["A.md"]).clean
    (tmp_path / "B.md").write_text(
        "```\nprobes N/N PASS\n```\n", encoding="utf-8")
    assert not cc.check(tmp_path, ["B.md"]).clean


# --------------------------------------------------------------------------
# the shipped defect, reproduced
# --------------------------------------------------------------------------


def test_an_install_target_that_exists_but_carries_no_packaging_file_fires(
        tmp_path: Path) -> None:
    """The exact shape of the README/quick-start defect."""
    (tmp_path / "packages" / "intentops-core").mkdir(parents=True)
    (tmp_path / "D.md").write_text(
        "Install it:\n\n```\npip install -e packages/intentops-core\n```\n",
        encoding="utf-8")

    report = cc.check(tmp_path, ["D.md"])

    assert not report.clean
    assert [f.kind for f in report.findings] == ["uninstallable-target"]
    assert report.findings[0].path == "packages/intentops-core"
    # and existence alone would NOT have caught it
    assert cc.path_exists(tmp_path, "packages/intentops-core")


def test_the_same_target_is_clean_once_it_is_installable(tmp_path: Path) -> None:
    pkg = tmp_path / "packages" / "intentops-core"
    pkg.mkdir(parents=True)
    (pkg / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (tmp_path / "D.md").write_text(
        "Install it:\n\n```\npip install -e packages/intentops-core\n```\n",
        encoding="utf-8")

    assert cc.check(tmp_path, ["D.md"]).clean


@pytest.mark.parametrize("target", ["pkg", "./pkg", "pkg/"])
def test_a_bare_directory_target_is_recognised_as_local(tmp_path: Path,
                                                        target: str) -> None:
    """A token with no slash is still a local target if it names a directory.

    Deciding this on shape alone missed the bare-dirname form entirely -- the
    same class as the defect this module exists for.
    """
    (tmp_path / "pkg").mkdir()
    assert cc.install_targets(f"pip install -e {target}", tmp_path) == [target]


def test_a_pypi_name_is_not_a_local_target(tmp_path: Path) -> None:
    assert cc.install_targets("pip install pytest", tmp_path) == []


def test_flags_are_never_install_targets(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    assert cc.install_targets("pip install --upgrade -e pkg", tmp_path) == ["pkg"]


# --------------------------------------------------------------------------
# refusals: a load-bearing input missing is a HALT, never a skip
# --------------------------------------------------------------------------


def test_a_missing_declared_document_halts(tmp_path: Path) -> None:
    with pytest.raises(cc.ClaimsHalt):
        cc.check(tmp_path, ["nope.md"])


def test_an_empty_population_halts(tmp_path: Path) -> None:
    with pytest.raises(cc.ClaimsHalt):
        cc.check(tmp_path, [])


def test_the_cli_exits_two_on_a_halt(tmp_path: Path, capsys) -> None:
    """A HALT is distinguishable from a finding by exit code, not only prose."""
    code = cc.main(["--repo-root", str(tmp_path)])
    assert code == 2
    assert "HALT" in capsys.readouterr().out


# --------------------------------------------------------------------------
# population and blind spots are reported, never implied
# --------------------------------------------------------------------------


def test_an_exempt_path_leaves_the_population_and_is_counted(
        tmp_path: Path) -> None:
    (tmp_path / "A.md").write_text(
        "Genesis writes `.intentops/genesis/journal.jsonl` at birth.\n",
        encoding="utf-8")
    report = cc.check(tmp_path, ["A.md"])
    assert report.clean
    assert report.checked == 0
    assert report.exempted[".intentops/"] == 1


def test_the_render_states_its_denominator_and_its_blind_spots(
        tmp_path: Path) -> None:
    (tmp_path / "A.md").write_text("This ships `A.md`.\n", encoding="utf-8")
    text = cc.render(cc.check(tmp_path, ["A.md"]))
    assert "population:" in text
    assert "exemptions:" in text
    assert "does and does not say" in text
    # every declared exemption prefix is printed WITH its count, even at zero
    for prefix, _reason in cc.EXEMPT_PREFIXES:
        assert prefix in text


def test_a_path_escaping_the_root_is_a_finding_and_is_never_followed(
        tmp_path: Path) -> None:
    (tmp_path / "A.md").write_text(
        "This ships `../outside/x.md`.\n", encoding="utf-8")
    report = cc.check(tmp_path, ["A.md"])
    assert len(report.findings) == 1
    assert not cc.path_exists(tmp_path, "../outside/x.md")


# --------------------------------------------------------------------------
# the live reading over this repository
# --------------------------------------------------------------------------


def test_the_declared_documents_all_exist() -> None:
    for rel in cc.DOCUMENTS:
        assert (REPO_ROOT / rel).is_file(), rel


def test_this_repository_documents_only_paths_that_exist() -> None:
    """The gate this leg exists to arm. A finding here is a real broken claim."""
    report = cc.check(REPO_ROOT)
    assert report.clean, "\n".join(f.render() for f in report.findings)
    # the population is non-empty, so a green reading means something
    assert report.checked > 0, "nothing was checked; a clean reading of an " \
                               "empty population is not evidence"
