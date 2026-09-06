"""Refuse a commit that changes a core surface with no review on record.

PURPOSE
    The values-council gate reads a tool CALL. It is the right instrument and
    it has a residue it cannot close: a command assembled at runtime, a path in
    a shell variable, an editor outside the agent entirely, a `git apply` whose
    targets live inside the patch. Every one of those reaches a declared core
    surface without a single tool call the gate could see.

    Everything above still has to pass through one place: the commit.

    So this hook asks one question of the staged diff, and refuses on the
    answer: *does every staged path that falls under `config/core-surface.yaml`
    have a matching row in `.intentops/core-review/ledger.jsonl`?* A core change
    with no review on record does not become a commit. This is the out-of-band
    detector named in the values council's own blind spots (genesis-design
    sect. 9.3); it is not a second gate, it is the audit that catches what the
    gate structurally cannot see.

    IT SITS BELOW THE OPERATOR, like everything else here. The refusal names
    the override:

        python scripts/hooks/pre-commit-core-surface.py --override \
            --reason "why this core change is going in unreviewed"

    A blank reason is refused the way a blank reaffirmation is: an override
    with no reason records that the audit was overruled and destroys the only
    evidence of why.

WRITE MODEL
    Read-only in its refusing path -- it reads the staged name list and the
    review ledger and writes nothing. `--override` is the one writing path, and
    it appends through `values_council.record_override`, which is the ledger's
    sanctioned writer (append-only JSONL, StoreLock, pure fold). This script
    never opens the ledger for writing itself.

BLIND SPOTS
    * IT READS PATHS, NOT DIFFS. A core file staged with a whitespace-only
      change needs a review row exactly as a rewrite does. That over-refuses,
      which is the safe direction, and it is a deliberate choice: deciding
      which edits are substantive is judgement, and this is a Code-rung
      instrument.
    * A REVIEW ROW IS NOT A REVIEW. The row proves the council convened on that
      path, not that anybody read the verdict. Pairing that with a HOLD or
      ESCALATE verdict is why a refusal verdict does NOT satisfy this hook
      without an override.
    * IT CANNOT SEE `--no-verify`. A commit that bypasses hooks bypasses this.
      The remedy is the imprint re-hash at session start, which reads the bytes
      on disk and does not care how they got there.
    * A ROW MATCHES BY NORMALIZED PATH, NOT BY CONTENT HASH. A review of an
      earlier edit to the same path satisfies a later one. Closing that needs
      the ledger to carry a content digest, which is a ledger schema change and
      a separate decision.
    * IT IS ONLY INSTALLED IF SOMEONE INSTALLS IT. `--install` is one command
      and nothing runs it for you; an uninstalled hook is indistinguishable
      from a passing one, which is why `--selftest` exists and why the
      integrity re-hash does not depend on this.

CLI
    python scripts/hooks/pre-commit-core-surface.py            # the hook itself
    python scripts/hooks/pre-commit-core-surface.py --install
    python scripts/hooks/pre-commit-core-surface.py --override --reason "..."
    python scripts/hooks/pre-commit-core-surface.py --selftest
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

#: The kill switch. Named here so that switching it off is a taggable act
#: (rules/tagout.md): an open switch carries no information about why it is
#: open, so this one has a name, and turning it off is a LOTO entry.
DISABLE_ENV = "INTENTOPS_CORE_SURFACE_HOOK"

HOOK_SHIM = """#!/bin/sh
# IntentOps: refuse a commit that changes a core surface with no review row.
# Installed by scripts/hooks/pre-commit-core-surface.py --install
exec python "$(git rev-parse --show-toplevel)/scripts/hooks/pre-commit-core-surface.py" "$@"
"""


def _repo_root(start: Optional[Path] = None) -> Path:
    here = Path(start or __file__).resolve()
    for parent in [here] + list(here.parents):
        if (parent / "config" / "core-surface.yaml").is_file():
            return parent
    return here.parents[2] if len(here.parents) > 2 else here.parent


def _bootstrap_imports(repo_root: Path) -> None:
    """Make `intentops_core` importable from a bare clone.

    A hook that only works after `pip install -e` is a hook that is silently
    absent on the machine where somebody is editing the core in a hurry.
    """
    candidate = repo_root / "packages" / "intentops-core"
    if candidate.is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))


class HookHalt(RuntimeError):
    """The hook could not run. It refuses; it never passes on a halt."""


def _load_council(repo_root: Path):
    _bootstrap_imports(repo_root)
    try:
        from intentops_core.governance import values_council as vc
    except ImportError as exc:  # pragma: no cover - environment defect
        raise HookHalt(
            "the values council could not be imported, so this hook cannot tell "
            f"which paths are core-mechanic ({exc}). It refuses rather than "
            "passing: a check that could not run is not a check that found "
            "nothing. Remedy: pip install -e packages/intentops-core, or set "
            f"{DISABLE_ENV}=off and tag the switch."
        ) from exc
    return vc


# ---------------------------------------------------------------------------
# the staged set
# ---------------------------------------------------------------------------


def staged_paths(repo_root: Path) -> List[str]:
    """Paths in the index, from git. Raises rather than returning an empty set."""
    try:
        proc = subprocess.run(
            # --no-renames is load-bearing, not tidiness. With rename detection
            # on, `git mv genesis/imprint/rules/honesty.md docs/` lists ONLY the
            # destination path, so a core rule moved OUT of the surface staged
            # clean and this hook exited 0 -- a bypass the wave-2 verifier
            # executed. Suppressing detection lists the old path as D and the
            # new one as A, and the D is the one that must be reviewed.
            ["git", "diff", "--cached", "--name-only", "-z", "--no-renames",
             "--diff-filter=ACMRTD"],
            cwd=str(repo_root), capture_output=True, check=False,
        )
    except OSError as exc:
        raise HookHalt(
            f"git could not be run to read the staged set: {exc}. An unreadable "
            "index is not an empty one."
        ) from exc
    if proc.returncode != 0:
        raise HookHalt(
            "git refused to list the staged set "
            f"(exit {proc.returncode}): {proc.stderr.decode('utf-8', 'replace').strip()}"
        )
    raw = proc.stdout.decode("utf-8", "replace")
    return [p for p in raw.split("\0") if p.strip()]


# ---------------------------------------------------------------------------
# the check
# ---------------------------------------------------------------------------


def _ledger_root(repo_root: Path, vc) -> Path:
    """Where this node's review ledger actually lives.

    The gate writes at ``INTENTOPS_NODE_ROOT`` (``values_council.
    node_root_from_env``); this hook read at ``repo_root``. Wave-2 verifier
    finding: whenever the node root is not the checkout -- which is the normal
    shape for a node whose runtime state lives outside its clone -- the hook
    read an empty or absent ledger, so the SATISFIED path was unreachable and
    every core commit refused. A gate and its hook must consult one store.

    Falls back to ``repo_root`` when the variable is unset, and the caller
    PRINTS which root was read, because "the ledger was empty" and "the ledger
    was somewhere else" are different findings that looked identical.
    """
    try:
        return vc.node_root_from_env()
    except Exception:  # noqa: BLE001 - CouncilError, or an env with no root
        return repo_root


def unledgered_core_paths(
    repo_root: Path,
    paths: Sequence[str],
    vc,
) -> List[Tuple[str, str, str]]:
    """``[(path, surface_reason, why_unsatisfied)]`` for staged core paths.

    A path is SATISFIED when the review ledger carries either an operator
    override for it, or a `values_council` row whose verdict is CONSENT. A row
    recording a HOLD, an ESCALATE or a HALT is a review that refused, and a
    refused review does not clear a commit -- that is the whole point of
    recording the verdict rather than the fact of the reading.
    """
    surface = vc.load_core_surface(repo_root / "config" / "core-surface.yaml")
    ledger = vc.CoreReviewLedger(_ledger_root(repo_root, vc))
    rows, malformed = ledger.read()
    if malformed:
        raise HookHalt(
            f"{malformed} row(s) in {ledger.path} are unreadable. The ledger is "
            "the only record of this node's core reviews; an unreadable row is "
            "missing history, and this hook will not decide over a record it "
            "half-understands."
        )

    # The override question is answered by the LEDGER'S OWN FOLD, never by
    # counting `kind == "override"` rows. Wave-2 verifier finding: this hook
    # counted the grant and never the spend, so an `override_consumed` row --
    # which the gate writes the moment an override is used -- left the override
    # looking live here forever. One override then cleared every subsequent
    # commit touching that path. `state().outstanding()` is the same fold the
    # gate consults, so the two can no longer disagree about what is spent.
    state = vc.fold(rows, malformed=malformed)

    consented: set = set()
    refused: dict = {}
    for row in rows:
        key = vc.normalize_path(str(row.get("normalized_path") or row.get("path") or ""))
        if not key:
            continue
        kind = str(row.get("kind") or "")
        verdict = str(row.get("verdict") or "").lower()
        if kind == vc.REVIEW_KIND:
            if verdict == vc.Verdict.CONSENT.value:
                consented.add(key)
            elif verdict in (vc.Verdict.HOLD.value, vc.Verdict.ESCALATE.value,
                             vc.Verdict.HALT.value):
                refused[key] = verdict

    findings: List[Tuple[str, str, str]] = []
    for path in paths:
        entry = surface.match(path)
        if entry is None:
            continue
        key = vc.normalize_path(path)
        if state.outstanding(key) > 0:
            continue
        if key in consented:
            continue
        if key in refused:
            findings.append((
                path, entry.reason,
                f"the council reviewed this path and returned {refused[key].upper()} "
                "-- a refused review does not clear a commit",
            ))
            continue
        findings.append((
            path, entry.reason,
            "no values-council row and no operator override names this path",
        ))
    return findings


def render_refusal(findings: Sequence[Tuple[str, str, str]]) -> str:
    lines = [
        "CORE SURFACE REFUSED -- this commit changes the machinery that governs "
        "this node, and the review ledger has no row for it.",
        "",
    ]
    for path, reason, why in findings:
        lines.append(f"  {path}")
        lines.append(f"      core because: {reason}")
        lines.append(f"      unsatisfied:  {why}")
    lines.extend([
        "",
        "  The council sits below you. To proceed, record an override naming a "
        "reason (a blank reason is refused):",
        "",
        "      python scripts/hooks/pre-commit-core-surface.py --override \\",
        '          --reason "why this core change goes in without a review"',
        "",
        "  Or run the change back through the gate so a review row exists.",
    ])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------


def install(repo_root: Path) -> Tuple[bool, str]:
    """Write the shim into `.git/hooks/pre-commit`. Never overwrites blindly."""
    hooks_dir = repo_root / ".git" / "hooks"
    if not hooks_dir.is_dir():
        return False, (
            f"{hooks_dir} does not exist -- this is not a git working tree, or "
            "the hooks path is configured elsewhere (check `git config "
            "core.hooksPath`)."
        )
    target = hooks_dir / "pre-commit"
    if target.exists() and "pre-commit-core-surface.py" not in target.read_text(
        encoding="utf-8", errors="replace"
    ):
        return False, (
            f"{target} already exists and is not this hook. Refusing to "
            "overwrite somebody else's hook. Add this line to it instead:\n"
            '    python "$(git rev-parse --show-toplevel)/scripts/hooks/'
            'pre-commit-core-surface.py" || exit 1'
        )
    target.write_text(HOOK_SHIM, encoding="utf-8", newline="\n")
    try:
        target.chmod(0o755)
    except OSError:
        pass  # Windows; git for Windows does not require the bit
    return True, f"installed {target}"


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


def selftest() -> int:
    """Prove the refusal and the acceptance both fire. 0 = pass."""
    import tempfile

    failures: List[str] = []
    checked: List[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        checked.append(label)
        print(f"  [{'FIRED ' if ok else 'MISSED'}] {label} {detail}".rstrip())
        if not ok:
            failures.append(label)

    real_root = _repo_root()
    vc = _load_council(real_root)

    with tempfile.TemporaryDirectory(prefix="intentops-precommit-") as tmp:
        root = Path(tmp)
        (root / "config").mkdir(parents=True)
        (root / "config" / "core-surface.yaml").write_text(
            "schema: core-surface/v1\n"
            "as_of: '2026-09-06'\n"
            "surface:\n"
            "  - glob: 'genesis/imprint/**'\n"
            "    reason: 'the signed birth bundle'\n",
            encoding="utf-8",
        )
        core = "genesis/imprint/IMPRINT.md"

        findings = unledgered_core_paths(root, ["docs/quick-start.md"], vc)
        check("a non-core path is not refused", not findings)

        findings = unledgered_core_paths(root, [core], vc)
        check("an unledgered core path IS refused", len(findings) == 1,
              findings[0][2][:44] if findings else "")

        vc.CoreReviewLedger(root).append(
            {"kind": vc.REVIEW_KIND, "verdict": vc.Verdict.ESCALATE.value,
             "path": core, "normalized_path": vc.normalize_path(core)}
        )
        findings = unledgered_core_paths(root, [core], vc)
        check("a REFUSED review row does not clear the commit",
              len(findings) == 1 and "ESCALATE" in findings[0][2])

        vc.CoreReviewLedger(root).append(
            {"kind": vc.REVIEW_KIND, "verdict": vc.Verdict.CONSENT.value,
             "path": core, "normalized_path": vc.normalize_path(core)}
        )
        findings = unledgered_core_paths(root, [core], vc)
        check("a CONSENT review row clears the commit", not findings)

        other = "genesis/imprint/invariants/core.yaml"
        findings = unledgered_core_paths(root, [other], vc)
        check("the clearance is per path, never a standing lane", len(findings) == 1)

        blank_refused = False
        try:
            vc.record_override(root, other, "   ")
        except vc.CouncilError:
            blank_refused = True
        check("a blank override reason is refused", blank_refused)

        vc.record_override(root, other, "removing a dead field; reviewed by hand")
        findings = unledgered_core_paths(root, [other], vc)
        check("an override with a reason clears the commit", not findings)

        with vc.CoreReviewLedger(root).path.open("a", encoding="utf-8") as handle:
            handle.write("{not json\n")
        halted = False
        try:
            unledgered_core_paths(root, [core], vc)
        except HookHalt:
            halted = True
        check("an unreadable ledger row HALTs rather than passing", halted)

        text = render_refusal([(core, "the signed birth bundle", "no row")])
        check("the refusal names the override path",
              "--override" in text and "--reason" in text)

    total = len(checked)
    print(f"pre-commit-core-surface selftest: "
          f"{total - len(failures)}/{total} paths behaved as declared")
    if failures:
        print("SELFTEST FAILED: " + ", ".join(failures))
        return 1
    print("SELFTEST PASS -- refusal and acceptance both fire")
    return 0


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="refuse a commit touching a core surface with no review row"
    )
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--install", action="store_true",
                        help="write the shim into .git/hooks/pre-commit")
    parser.add_argument("--print-hook", action="store_true",
                        help="print the shim instead of installing it")
    parser.add_argument("--override", action="store_true",
                        help="record an operator override for every unledgered "
                             "staged core path; requires --reason")
    parser.add_argument("--reason", default="",
                        help="why this core change proceeds without a review")
    parser.add_argument("--paths", nargs="*", default=None,
                        help="check these paths instead of the git index")
    parser.add_argument("--repo-root", type=Path, default=None)
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.selftest:
        return selftest()
    if args.print_hook:
        print(HOOK_SHIM, end="")
        return 0

    root = Path(args.repo_root) if args.repo_root else _repo_root()

    if args.install:
        ok, note = install(root)
        print(note)
        return 0 if ok else 1

    switch = os.environ.get(DISABLE_ENV, "").strip().lower()
    if switch in ("off", "0", "false", "no"):
        print(f"CORE SURFACE HOOK: DISABLED via {DISABLE_ENV}={switch}. This is "
              "a detection control that is switched off; it belongs in the LOTO "
              "ledger with an authority and a stated way back.")
        return 0
    if switch and switch not in ("on", "1", "true", "yes"):
        print(f"CORE SURFACE HOOK HALT: {DISABLE_ENV}={switch!r} is neither on "
              "nor off. An unreadable switch on a control is tagged out, never "
              "guessed.")
        return 1

    try:
        vc = _load_council(root)
        paths = list(args.paths) if args.paths is not None else staged_paths(root)
        # Print WHICH ledger was read, always. "the ledger was empty" and "the
        # ledger was somewhere else" produced identical output before this,
        # and only one of them is a review problem.
        ledger_root = _ledger_root(root, vc)
        if ledger_root != root:
            print(f"CORE SURFACE: reading the review ledger at the node root "
                  f"{ledger_root} (not the checkout).")
        findings = unledgered_core_paths(root, paths, vc)
    except HookHalt as exc:
        print(f"CORE SURFACE HOOK HALT: {exc}")
        return 1
    except Exception as exc:  # the council's own CouncilError, and anything else
        print(f"CORE SURFACE HOOK HALT: {exc}")
        return 1

    if args.override:
        reason = str(args.reason or "").strip()
        if not reason:
            print("--override requires --reason. A blank override records that "
                  "the audit was overruled and discards the only thing that "
                  "made the overrule reviewable.")
            return 1
        if not findings:
            print("nothing to override: every staged core path already has a row.")
            return 0
        for path, _reason, _why in findings:
            vc.record_override(_ledger_root(root, vc), path, reason)
            print(f"override recorded for {path}")
        return 0

    if not findings:
        surface = vc.load_core_surface(root / "config" / "core-surface.yaml")
        core_seen = sum(1 for path in paths if surface.match(path))
        if core_seen:
            print(f"CORE SURFACE: {core_seen} staged core path(s), all with a "
                  "review row on the ledger.")
        return 0

    print(render_refusal(findings))
    return 1


if __name__ == "__main__":
    sys.exit(main())
