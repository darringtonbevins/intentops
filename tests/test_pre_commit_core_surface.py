"""A core change with no review on record does not become a commit.

The third leg of the imprint-integrity work order. The values-council gate reads
a tool CALL and structurally cannot see a path assembled at runtime, an edit
made outside the agent, or a `git apply` whose targets live inside the patch.
All of them still have to pass through the commit, which is what this hook
audits.

No live services and no real git repository is required for the check itself:
the hook takes ``--paths`` so the staged set can be supplied, and only the
``--install`` test touches a directory layout.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from intentops_core.governance import values_council as vc

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK_PATH = REPO_ROOT / "scripts" / "hooks" / "pre-commit-core-surface.py"

CORE = "genesis/imprint/IMPRINT.md"
OTHER_CORE = "genesis/imprint/invariants/core.yaml"
NOT_CORE = "docs/quick-start.md"


def _load_hook():
    """Import the hook by path -- it is a script, not an installed module."""
    spec = importlib.util.spec_from_file_location("_precommit_core_surface", HOOK_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


hook = _load_hook()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A tree with a declared core surface and an empty review ledger."""
    root = tmp_path / "repo"
    (root / "config").mkdir(parents=True)
    (root / "config" / "core-surface.yaml").write_text(
        "schema: core-surface/v1\n"
        "as_of: '2026-09-06'\n"
        "surface:\n"
        "  - glob: 'genesis/imprint/**'\n"
        "    reason: 'the signed birth bundle'\n"
        "  - glob: 'config/core-surface.yaml'\n"
        "    reason: 'this file declares its own surface'\n",
        encoding="utf-8",
    )
    return root


def review(root: Path, path: str, verdict: vc.Verdict) -> None:
    vc.CoreReviewLedger(root).append({
        "kind": vc.REVIEW_KIND,
        "verdict": verdict.value,
        "path": path,
        "normalized_path": vc.normalize_path(path),
    })


# ---------------------------------------------------------------------------
# the refusal
# ---------------------------------------------------------------------------


def test_an_unledgered_core_diff_is_refused(repo: Path) -> None:
    code = hook.main(["--repo-root", str(repo), "--paths", CORE])
    assert code == 1


def test_the_refusal_names_the_path_the_reason_and_the_override(
    repo: Path, capsys
) -> None:
    hook.main(["--repo-root", str(repo), "--paths", CORE])
    out = capsys.readouterr().out
    assert CORE in out
    assert "the signed birth bundle" in out
    assert "--override" in out and "--reason" in out


def test_a_non_core_diff_is_not_refused(repo: Path) -> None:
    assert hook.main(["--repo-root", str(repo), "--paths", NOT_CORE]) == 0


def test_a_mixed_diff_is_refused_on_the_core_path_alone(repo: Path) -> None:
    findings = hook.unledgered_core_paths(repo, [NOT_CORE, CORE], vc)
    assert [f[0] for f in findings] == [CORE]


def test_a_refused_review_row_does_not_clear_the_commit(repo: Path) -> None:
    """Recording a HOLD is not the same as clearing it."""
    review(repo, CORE, vc.Verdict.ESCALATE)
    findings = hook.unledgered_core_paths(repo, [CORE], vc)
    assert len(findings) == 1
    assert "ESCALATE" in findings[0][2]
    assert hook.main(["--repo-root", str(repo), "--paths", CORE]) == 1


# ---------------------------------------------------------------------------
# the acceptance
# ---------------------------------------------------------------------------


def test_a_consent_row_clears_the_commit(repo: Path) -> None:
    review(repo, CORE, vc.Verdict.CONSENT)
    assert hook.unledgered_core_paths(repo, [CORE], vc) == []
    assert hook.main(["--repo-root", str(repo), "--paths", CORE]) == 0


def test_the_clearance_is_per_path_never_a_standing_lane(repo: Path) -> None:
    review(repo, CORE, vc.Verdict.CONSENT)
    findings = hook.unledgered_core_paths(repo, [CORE, OTHER_CORE], vc)
    assert [f[0] for f in findings] == [OTHER_CORE]


def test_a_row_matches_across_separator_and_case_normalisation(repo: Path) -> None:
    review(repo, "GENESIS\\IMPRINT\\IMPRINT.MD", vc.Verdict.CONSENT)
    assert hook.unledgered_core_paths(repo, [CORE], vc) == []


# ---------------------------------------------------------------------------
# the override -- the council sits below the operator
# ---------------------------------------------------------------------------


def test_an_override_requires_a_reason(repo: Path, capsys) -> None:
    code = hook.main(["--repo-root", str(repo), "--paths", CORE,
                      "--override", "--reason", "   "])
    assert code == 1
    assert "requires --reason" in capsys.readouterr().out


def test_an_override_with_a_reason_records_and_then_clears(repo: Path) -> None:
    assert hook.main(["--repo-root", str(repo), "--paths", CORE,
                      "--override", "--reason",
                      "reviewed by hand; removing a dead field"]) == 0
    rows, malformed = vc.CoreReviewLedger(repo).read()
    assert malformed == 0
    assert any(r["kind"] == "override" and r["reason"].startswith("reviewed")
               for r in rows)
    assert hook.main(["--repo-root", str(repo), "--paths", CORE]) == 0


def test_an_override_over_a_refused_review_clears_it(repo: Path) -> None:
    review(repo, CORE, vc.Verdict.HOLD)
    vc.record_override(repo, CORE, "the hold was read and accepted by the operator")
    assert hook.unledgered_core_paths(repo, [CORE], vc) == []


def test_overriding_with_nothing_to_override_says_so(repo: Path, capsys) -> None:
    review(repo, CORE, vc.Verdict.CONSENT)
    assert hook.main(["--repo-root", str(repo), "--paths", CORE,
                      "--override", "--reason", "belt and braces"]) == 0
    assert "nothing to override" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# a check that could not run is not a check that passed
# ---------------------------------------------------------------------------


def test_a_malformed_ledger_row_halts_rather_than_passing(repo: Path) -> None:
    review(repo, CORE, vc.Verdict.CONSENT)
    with vc.CoreReviewLedger(repo).path.open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")
    with pytest.raises(hook.HookHalt):
        hook.unledgered_core_paths(repo, [CORE], vc)
    assert hook.main(["--repo-root", str(repo), "--paths", CORE]) == 1


def test_a_missing_core_surface_halts(tmp_path: Path, capsys) -> None:
    empty = tmp_path / "bare"
    empty.mkdir()
    assert hook.main(["--repo-root", str(empty), "--paths", CORE]) == 1
    assert "HALT" in capsys.readouterr().out


def test_an_unreadable_kill_switch_halts(repo: Path, monkeypatch, capsys) -> None:
    """An unreadable switch on a control is tagged out, never guessed."""
    monkeypatch.setenv(hook.DISABLE_ENV, "maybe")
    assert hook.main(["--repo-root", str(repo), "--paths", CORE]) == 1
    assert "neither on nor off" in capsys.readouterr().out


def test_the_kill_switch_announces_itself_when_off(repo: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv(hook.DISABLE_ENV, "off")
    assert hook.main(["--repo-root", str(repo), "--paths", CORE]) == 0
    out = capsys.readouterr().out
    assert "DISABLED" in out and "LOTO" in out


# ---------------------------------------------------------------------------
# install, and the detector's own proof
# ---------------------------------------------------------------------------


def test_install_refuses_to_overwrite_another_hook(repo: Path) -> None:
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "pre-commit").write_text("#!/bin/sh\nexec ./somebody-elses-hook\n",
                                      encoding="utf-8")
    ok, note = hook.install(repo)
    assert ok is False
    assert "Refusing to overwrite" in note


def test_install_writes_the_shim(repo: Path) -> None:
    (repo / ".git" / "hooks").mkdir(parents=True)
    ok, note = hook.install(repo)
    assert ok is True
    body = (repo / ".git" / "hooks" / "pre-commit").read_text(encoding="utf-8")
    assert "pre-commit-core-surface.py" in body


def test_install_says_so_when_there_is_no_git_tree(repo: Path) -> None:
    ok, note = hook.install(repo)
    assert ok is False and "does not exist" in note


def test_the_selftest_passes() -> None:
    assert hook.selftest() == 0
