"""The step-0 bypasses the wave-2 verifier executed, each held closed.

PURPOSE
    Four bypass families, all found by RUNNING the shipped tooling rather than
    reading it, and all of which left the guard green while the guarded thing
    was reachable:

    1. **The pre-commit rename bypass.** `git mv genesis/imprint/rules/x docs/`
       lists only the DESTINATION under git's rename detection, so a core rule
       moved out of the surface staged clean and the hook exited 0.
    2. **The spent override.** The hook counted `kind == "override"` rows and
       never subtracted `override_consumed`, so one override cleared every
       later commit touching that path -- while the gate, reading the same
       journal through its fold, considered it spent.
    3. **The node-root ledger split.** The gate WRITES at
       `INTENTOPS_NODE_ROOT`; the hook READ at the checkout. Wherever those
       differ -- the normal shape for a node whose runtime lives outside its
       clone -- the satisfied path was unreachable by construction.
    4. **Seven path spellings.** `cd genesis && cp x imprint/rules/honesty.md`,
       `$A/$B/rules/...`, `genesis/*/rules`, `genesis/./imprint`,
       `genesis//imprint`, `genesis/{imprint}`, `genesis/imprin?` -- every one
       reached a declared core surface with `CORE=[]`.

    Plus the two verbs that should never have been on the read-only allowlist:
    `find` (`-delete`, `-exec rm`) and `sort` (`-o` overwrites its input).

WRITE MODEL
    None. Reads only, or writes inside a pytest `tmp_path`. The ledger tests
    build a throwaway node root and never touch a real review journal; the
    rename test builds a throwaway git repository with a local identity.

BLIND SPOTS
    - These prove the seven SPELLINGS route. They do not prove the set of
      spellings is complete; a shell has more ways to name a file than any
      finite list, which is why the classifier's design rule is
      "unrecognised -> mine it anyway" and these tests sit beneath that rule
      rather than in place of it.
    - `_resolve_context` resolves only what is visible in one command. A `cd`
      in a previous, separate tool call is invisible to it and always will be.
    - The rename test asserts the STAGED SET includes the deleted path. It does
      not drive a real `git commit`, so it proves the hook's input is right,
      not that git invokes the hook.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, List

import pytest

from intentops_core.governance import values_council as vc

REPO_ROOT = Path(__file__).resolve().parents[1]
SURFACE = REPO_ROOT / "config" / "core-surface.yaml"
CORE_RULE = "genesis/imprint/rules/honesty.md"


def _load(name: str, path: Path) -> Any:
    """Import a module by PATH, never by adding a package to the import path.

    The saddle is deliberately NOT on pytest's `pythonpath`:
    `test_dependency_direction.py` asserts the core package declares no saddle
    dependency, and it reads the pyproject text -- so putting the saddle there
    to make a test convenient would break the invariant the repository exists
    to hold. Loading by path keeps the dependency arrow pointing one way.
    """
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _hook() -> Any:
    return _load("_precommit_core_surface",
                 REPO_ROOT / "scripts" / "hooks" / "pre-commit-core-surface.py")


def _saddle_pre_tool() -> Any:
    saddle = REPO_ROOT / "packages" / "intentops-saddle-claudecode"
    import sys

    if str(saddle) not in sys.path:
        sys.path.insert(0, str(saddle))
    import importlib

    return importlib.import_module("intentops_saddle_claudecode.pre_tool")


def _core_targets(command: str) -> List[str]:
    surface = vc.load_core_surface(SURFACE)
    return [hit.path for hit in vc.target_hits("Bash", {"command": command})
            if surface.match(hit.path)]


# ---------------------------------------------------------------------------
# 4. path spellings -- the classifier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command,shape", [
    (f"cp x {CORE_RULE}", "the plain spelling, as a control"),
    ("cd genesis && cp x imprint/rules/honesty.md", "cd prefix"),
    ("A=genesis B=imprint; cp x $A/$B/rules/honesty.md", "variable expansion"),
    ("cp x genesis/*/rules/honesty.md", "glob star"),
    ("cp x genesis/./imprint/rules/honesty.md", "dot segment"),
    ("cp x genesis//imprint/rules/honesty.md", "doubled slash"),
    ("cp x genesis/{imprint}/rules/honesty.md", "brace fragment"),
    ("cp x genesis/imprin?/rules/honesty.md", "single-char glob"),
    ("pushd genesis; cp x imprint/rules/honesty.md", "pushd prefix"),
    ("cd genesis/imprint && rm rules/honesty.md", "cd two deep"),
    ("Set-Location genesis; Copy-Item x imprint/rules/honesty.md", "PowerShell cd"),
])
def test_every_known_spelling_reaches_the_core_surface(command: str,
                                                       shape: str) -> None:
    assert _core_targets(command), f"{shape} bypassed the surface: {command!r}"


@pytest.mark.parametrize("command", [
    "find genesis/imprint/rules -name honesty.md -delete",
    "find genesis/imprint/rules -name '*.md' -exec rm {} ;",
    f"sort -o {CORE_RULE} {CORE_RULE}",
])
def test_find_and_sort_are_no_longer_read_only(command: str) -> None:
    """Both write. Both sat on the read-only allowlist until 2026-09-06."""
    assert _core_targets(command), f"still treated as read-only: {command!r}"


@pytest.mark.parametrize("command", [
    f"cat {CORE_RULE}",
    f"grep -n fail-closed {CORE_RULE}",
    f"wc -l {CORE_RULE}",
    f"git diff -- {CORE_RULE}",
])
def test_genuine_reads_still_convene_nothing(command: str) -> None:
    """The other half of the trade: a reader must not summon a council."""
    assert _core_targets(command) == [], f"false positive on {command!r}"


def test_normalize_path_collapses_but_never_resolves_dotdot() -> None:
    assert vc.normalize_path("genesis//imprint/x") == "genesis/imprint/x"
    assert vc.normalize_path("genesis/./imprint/x") == "genesis/imprint/x"
    # '..' is preserved on purpose: resolving it would let `a/../genesis/...`
    # normalise into a path the operator never wrote.
    assert ".." in vc.normalize_path("a/../genesis/imprint/x")


# ---------------------------------------------------------------------------
# 8. no declared glob points at nothing
# ---------------------------------------------------------------------------

#: The ONE glob exempt from the tracked-file rule, exempt BY NAME and with its
#: reason, because a node's installed hook chain lives outside the clone. A
#: filter that silently skipped every empty glob is how seven dead `src/`
#: entries survived a build and a public release.
NODE_LOCAL_GLOBS = {".intentops-hooks/**"}


def test_every_declared_core_glob_matches_a_tracked_file() -> None:
    # `git` is not guaranteed on PATH. Where it is absent, `subprocess.run`
    # raises FileNotFoundError rather than returning non-zero, so the
    # git-archive fallback below is unreachable without this check.
    have_git = shutil.which("git") is not None
    proc = subprocess.run(
        ["git", "ls-files"], cwd=str(REPO_ROOT), capture_output=True,
        text=True) if have_git else None
    if proc is not None and proc.returncode == 0:
        tracked = proc.stdout.split()
        # Plus files that EXIST and are not ignored but are not yet committed.
        # Widened 2026-09-06: the population was `git ls-files` alone, which
        # conflates "not staged yet" with "does not exist" -- so a leg could
        # not declare a surface in the same session it wrote the files it
        # covers, which is the house rule (new organ, its declarations, same
        # session). An uncommitted file in the working tree IS something the
        # glob is standing in front of. Nothing is weakened: a glob matching
        # no file that exists at all is still dead, and an IGNORED file is
        # still excluded, so a surface cannot be satisfied by build output.
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=str(REPO_ROOT), capture_output=True, text=True)
        if untracked.returncode == 0:
            tracked += untracked.stdout.split()
    else:
        # A git-archive export (the F1 cold suite) has no .git: walk the tree instead,
        # skipping the same build artifacts the fence skips.
        skip = {".git", "intentops.egg-info", "build", "dist", ".venv", "venv", "__pycache__", ".pytest_cache"}
        tracked = [
            p.relative_to(REPO_ROOT).as_posix()
            for p in REPO_ROOT.rglob("*")
            if p.is_file() and not (set(p.relative_to(REPO_ROOT).parts[:-1]) & skip)
        ]
    dead = []
    for entry in vc.load_core_surface(SURFACE).entries:
        if entry.glob in NODE_LOCAL_GLOBS:
            continue
        if not any(entry.pattern.match(vc.normalize_path(f)) for f in tracked):
            dead.append(entry.glob)
    assert not dead, (
        f"core-surface glob(s) match no tracked file: {dead}. A declared "
        f"surface that matches nothing is a guard nobody is behind."
    )


# ---------------------------------------------------------------------------
# 1. the rename bypass
# ---------------------------------------------------------------------------


def test_a_renamed_core_file_still_appears_in_the_staged_set(tmp_path: Path) -> None:
    """`git mv` a core rule out of the surface: the OLD path must be listed."""
    if shutil.which("git") is None:
        pytest.skip("git is not on PATH")
    repo = tmp_path / "clone"
    (repo / "genesis" / "imprint" / "rules").mkdir(parents=True)
    (repo / "docs").mkdir()
    rule = repo / "genesis" / "imprint" / "rules" / "honesty.md"
    rule.write_text("# honesty\n" + ("body line\n" * 40), encoding="utf-8")

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=str(repo), check=True,
                       capture_output=True)

    git("init", "-q")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    git("add", "-A")
    git("commit", "-qm", "seed")
    git("mv", "genesis/imprint/rules/honesty.md", "docs/honesty.md")

    staged = _hook().staged_paths(repo)
    assert "genesis/imprint/rules/honesty.md" in staged, (
        "the renamed-away core path is invisible to the hook -- this is the "
        "rename bypass, and --no-renames is what closes it"
    )
    assert vc.load_core_surface(SURFACE).match(
        "genesis/imprint/rules/honesty.md") is not None


# ---------------------------------------------------------------------------
# 2 + 3. the ledger: spent overrides, and where it is read
# ---------------------------------------------------------------------------


def test_a_spent_override_no_longer_clears_a_commit(tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch
                                                    ) -> None:
    node = tmp_path / "node"
    node.mkdir()
    monkeypatch.setenv(vc.NODE_ROOT_ENV, str(node))
    hook = _hook()

    vc.record_override(node, CORE_RULE, "reviewed by the operator")
    assert hook.unledgered_core_paths(REPO_ROOT, [CORE_RULE], vc) == [], (
        "a live override must clear the commit")

    # Spend it exactly as gate_values_council does -- the same kind, the same
    # normalized key. There is no public consume helper; the gate appends this
    # row inline, so the test writes the row the gate writes rather than a
    # convenient stand-in for it.
    vc.CoreReviewLedger(node).append({
        "kind": vc.OVERRIDE_CONSUMED_KIND,
        "path": CORE_RULE,
        "normalized_path": vc.normalize_path(CORE_RULE),
        "verdict": vc.Verdict.HOLD.value,
    })
    findings = hook.unledgered_core_paths(REPO_ROOT, [CORE_RULE], vc)
    assert findings, (
        "a SPENT override still cleared the commit -- the hook counted the "
        "grant and never the spend, so one override cleared every later "
        "commit touching this path"
    )


def test_the_hook_reads_the_ledger_where_the_gate_writes_it(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The gate writes at the node root; the hook used to read at the checkout."""
    node = tmp_path / "node"
    node.mkdir()
    monkeypatch.setenv(vc.NODE_ROOT_ENV, str(node))
    hook = _hook()

    assert hook._ledger_root(REPO_ROOT, vc) == node
    vc.record_override(node, CORE_RULE, "reviewed at the node root")
    assert hook.unledgered_core_paths(REPO_ROOT, [CORE_RULE], vc) == [], (
        "an override recorded at the node root did not reach the hook, so the "
        "satisfied path is unreachable whenever node root != checkout"
    )


def test_the_hook_falls_back_to_the_checkout_when_no_node_root_is_set(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(vc.NODE_ROOT_ENV, raising=False)
    assert _hook()._ledger_root(REPO_ROOT, vc) == REPO_ROOT


# ---------------------------------------------------------------------------
# 9. step 0 is actually invoked by a host hook
# ---------------------------------------------------------------------------


def test_the_saddle_pre_tool_calls_the_values_council() -> None:
    """The council was built, tested, and called by nothing.

    Asserted against the SOURCE rather than by driving a payload, because the
    council's own mode (observe at birth) means a live call correctly changes
    no verdict -- so a behavioural test would pass just as happily with the
    call deleted.
    """
    source = (REPO_ROOT / "packages" / "intentops-saddle-claudecode"
              / "intentops_saddle_claudecode" / "pre_tool.py").read_text("utf-8")
    assert "gate_values_council" in source
    assert "_apply_values_council(verdict" in source


def test_a_broken_values_council_escalates_and_never_permits(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """An advisory member that vanishes on error is a step that is decorative."""
    from intentops_core.gate import Decision, Verdict
    pt = _saddle_pre_tool()

    class _Event:
        tool_name = "Bash"
        tool_input = {"command": f"cp x {CORE_RULE}"}

    class _Context:
        root = REPO_ROOT
        indicators = None

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("the council could not convene")

    monkeypatch.setattr("intentops_core.governance.gate_values_council", _boom)
    out = pt._apply_values_council(
        Verdict(decision=Decision.ALLOW, tier="T1"), _Event(), _Context())
    assert out.decision is Decision.ASK
    assert any("could not" in r for r in out.reasons)


def test_the_values_council_never_relaxes_a_refusal() -> None:
    from intentops_core.gate import Decision, Verdict
    pt = _saddle_pre_tool()

    blocked = Verdict(decision=Decision.BLOCK, tier="T4",
                      reasons=("a destructive operation",))
    assert pt._apply_values_council(blocked, None, None) is blocked
