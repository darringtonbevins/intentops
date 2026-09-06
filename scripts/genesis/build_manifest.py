"""Recompute the imprint manifest's artifact hashes, deterministically.

PURPOSE
    ``genesis/imprint/`` is the signed birth bundle: the imprint text, the four
    invariant files, the nine rules, and the archetype catalogue.
    ``IMPRINT-MANIFEST.yaml`` records a SHA-256 over each of those files, and
    G1 verifies the bundle against it before a node mints an identity
    (genesis-design sect. 2.3: provenance precedes keys).

    This module is the only sanctioned writer of that manifest's ``artifacts:``
    block. It has three modes:

        --write      (re)compute the block and replace it in place
        --check      recompute and compare; EXIT 1 on any drift
        --selftest   prove every failure path can actually fire

    ``--check`` is the instrument. It fires on four distinct drifts, and each
    one is reported separately rather than folded into a single "mismatch":
    CHANGED (a byte moved), MISSING (a file the manifest claims is gone),
    UNTRACKED (a file in the bundle that the manifest does not claim), and
    UNCLAIMED (a tracked file that no I1-I9 bundle names). The last one exists
    because a file that is hashed but belongs to no bundle has left the
    population without failing anything -- the denominator failure of
    ``rules/no-silent-failures.md``.

WRITE MODEL
    Locked fresh-read read-modify-write (store-write-discipline.md model 2).
    ``IMPRINT-MANIFEST.yaml`` is a single whole-file store. Every write takes
    an exclusive kernel-released lock on ``.intentops/locks/``
    ``imprint-manifest.lock`` (``msvcrt.locking`` on Windows, ``fcntl.flock``
    elsewhere -- released by the OS on process death, so there is never a
    stale-lock reclaim), re-reads the file INSIDE the lock, mutates, and
    replaces atomically via ``os.replace``. The lock lives OUTSIDE the bundle
    directory on purpose: a runtime file has no business inside a signed
    bundle, where it would either be hashed or need an exclusion nobody reads.
    A value read before the lock was acquired is stale by definition, so the
    read is not hoisted out. ``--check`` takes no lock: it is a pure read and
    never mutates.

BLIND SPOTS
    - It hashes bytes, not meaning. A semantically catastrophic but
      hash-consistent manifest passes.
    - It does not verify SIGNATURES. ``signatures: []`` with
      ``status: proposed`` is the honest birth state, and G1 -- not this tool --
      is what REFUSES an unsigned bundle.
    - The ``artifacts:`` block is parsed with a STRICT, purpose-built reader for
      exactly the grammar this tool emits, so that no YAML dependency is needed
      (stdlib-first). It HALTS on any line it does not recognise rather than
      skipping it; it is not a general YAML parser and must not be used as one.
    - The UNCLAIMED check is a substring search for the path elsewhere in the
      document. It proves a bundle NAMES the file; it does not prove the naming
      is under the right bundle key.
    - It says nothing about files OUTSIDE ``genesis/imprint/``.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, NamedTuple

MANIFEST_RELPATH = "genesis/imprint/IMPRINT-MANIFEST.yaml"
BUNDLE_DIR_RELPATH = "genesis/imprint"
# Node-local runtime state, never inside the signed bundle (see WRITE MODEL).
LOCK_RELPATH = ".intentops/locks/imprint-manifest.lock"

BEGIN_MARKER = "# >>> BEGIN GENERATED artifacts -- written by scripts/genesis/build_manifest.py"
END_MARKER = "# <<< END GENERATED artifacts"

# Files inside the bundle directory that are deliberately NOT hashed.
# The manifest cannot hash itself; detached signatures are produced after it.
EXCLUDED_NAMES = frozenset({"IMPRINT-MANIFEST.yaml"})
EXCLUDED_SUFFIXES = (".sig",)


class Artifact(NamedTuple):
    """One hashed member of the birth bundle."""

    path: str  # repo-relative, POSIX separators, deterministic
    sha256: str
    size: int


class ManifestError(RuntimeError):
    """A halt. The manifest could not be read or written as specified."""


# ---------------------------------------------------------------------------
# locking (kernel-released; see WRITE MODEL)
# ---------------------------------------------------------------------------


@contextmanager
def _exclusive_lock(lock_path: Path) -> Iterator[None]:
    """Hold an exclusive, kernel-released lock on ``lock_path``.

    The lock path must be a sibling ``.lock`` file, never the store itself --
    a primitive handed the store path is one truncation away from destroying
    it (store-write-discipline.md).
    """
    if lock_path.suffix != ".lock":
        raise ManifestError(
            f"refusing to lock {lock_path}: a lock path must end in .lock, "
            "never the store's own path"
        )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _atomic_replace(target: Path, text: str) -> None:
    """Write ``text`` to ``target`` atomically, preserving LF line endings."""
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp_name, target)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


# ---------------------------------------------------------------------------
# computing the truth
# ---------------------------------------------------------------------------


def _is_hashable(path: Path) -> bool:
    if path.name in EXCLUDED_NAMES:
        return False
    return not path.name.endswith(EXCLUDED_SUFFIXES)


def compute_artifacts(root: Path) -> list[Artifact]:
    """Hash every bundle member under ``root``, in deterministic order.

    HALTS on an empty bundle: a manifest over zero files would verify happily
    forever, which is the shape of a detector that cannot fail.
    """
    bundle_dir = root / BUNDLE_DIR_RELPATH
    if not bundle_dir.is_dir():
        raise ManifestError(f"bundle directory not found: {bundle_dir}")

    # Sort by the repo-relative POSIX STRING, not by Path: path comparison is
    # case-folded on Windows and case-sensitive elsewhere, so sorting Paths
    # would make the manifest's order platform-dependent, and a manifest that
    # reorders itself by host is not deterministic.
    found: list[Artifact] = []
    members = [p for p in bundle_dir.rglob("*") if p.is_file() and _is_hashable(p)]
    for path in sorted(members, key=lambda p: p.relative_to(root).as_posix()):
        data = path.read_bytes()
        rel = path.relative_to(root).as_posix()
        found.append(Artifact(rel, hashlib.sha256(data).hexdigest(), len(data)))

    if not found:
        raise ManifestError(
            f"HALT: {bundle_dir} contains no hashable files. An empty manifest "
            "cannot fail, and a check that cannot fail is not a check."
        )
    return found


def render_block(artifacts: list[Artifact]) -> str:
    """Render the generated ``artifacts:`` block, exactly as the reader expects."""
    lines = [BEGIN_MARKER, "artifacts:"]
    for art in artifacts:
        lines.append(f'  - path: "{art.path}"')
        lines.append(f'    sha256: "{art.sha256}"')
        lines.append(f"    bytes: {art.size}")
    lines.append(END_MARKER)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# reading it back (strict; see BLIND SPOTS)
# ---------------------------------------------------------------------------


def parse_block(text: str) -> list[Artifact]:
    """Parse the generated block out of ``text``. Strict: unknown lines HALT."""
    if BEGIN_MARKER not in text or END_MARKER not in text:
        raise ManifestError(
            "HALT: the manifest carries no generated artifacts block. "
            f"Expected the marker line: {BEGIN_MARKER}"
        )
    body = text.split(BEGIN_MARKER, 1)[1].split(END_MARKER, 1)[0]
    lines = [ln for ln in body.splitlines() if ln.strip()]
    if not lines or lines[0].strip() != "artifacts:":
        raise ManifestError("HALT: the generated block must open with 'artifacts:'")

    out: list[Artifact] = []
    pending: dict[str, object] = {}
    for raw in lines[1:]:
        if raw.startswith('  - path: "') and raw.endswith('"'):
            if pending:
                raise ManifestError(f"HALT: incomplete artifact entry before {raw!r}")
            pending = {"path": raw[len('  - path: "') : -1]}
        elif raw.startswith('    sha256: "') and raw.endswith('"'):
            if "path" not in pending or "sha256" in pending:
                raise ManifestError(f"HALT: out-of-order manifest line: {raw!r}")
            pending["sha256"] = raw[len('    sha256: "') : -1]
        elif raw.startswith("    bytes: "):
            if "sha256" not in pending:
                raise ManifestError(f"HALT: out-of-order manifest line: {raw!r}")
            try:
                size = int(raw[len("    bytes: ") :])
            except ValueError as exc:
                raise ManifestError(f"HALT: unparseable byte count: {raw!r}") from exc
            out.append(Artifact(str(pending["path"]), str(pending["sha256"]), size))
            pending = {}
        else:
            # Never skip. A reader that skips a line it does not understand has
            # left that line out of the population.
            raise ManifestError(f"HALT: unrecognised line in the generated block: {raw!r}")
    if pending:
        raise ManifestError("HALT: the generated block ends mid-entry")
    return out


# ---------------------------------------------------------------------------
# the check
# ---------------------------------------------------------------------------


def check(root: Path) -> tuple[bool, list[str]]:
    """Compare the manifest against the bundle on disk.

    Returns ``(ok, findings)``. Every finding names its class, so a caller can
    tell a moved byte from a file that left the count.
    """
    manifest_path = root / MANIFEST_RELPATH
    if not manifest_path.is_file():
        raise ManifestError(f"HALT: manifest not found: {manifest_path}")
    text = manifest_path.read_text(encoding="utf-8")

    recorded = {a.path: a for a in parse_block(text)}
    actual = {a.path: a for a in compute_artifacts(root)}
    outside_block = text.split(BEGIN_MARKER, 1)[0] + text.split(END_MARKER, 1)[1]

    findings: list[str] = []
    for path in sorted(set(recorded) | set(actual)):
        rec, act = recorded.get(path), actual.get(path)
        if rec is None:
            findings.append(f"UNTRACKED  {path} -- present in the bundle, absent from the manifest")
        elif act is None:
            findings.append(f"MISSING    {path} -- claimed by the manifest, absent from the bundle")
        elif rec.sha256 != act.sha256 or rec.size != act.size:
            findings.append(
                f"CHANGED    {path} -- manifest {rec.sha256[:12]}/{rec.size}B, "
                f"disk {act.sha256[:12]}/{act.size}B"
            )
        if act is not None and path not in outside_block:
            findings.append(f"UNCLAIMED  {path} -- hashed, but no I1-I9 bundle names it")

    return (not findings), findings


def write(root: Path) -> list[Artifact]:
    """Recompute the block and replace it in the manifest (locked, atomic)."""
    manifest_path = root / MANIFEST_RELPATH
    with _exclusive_lock(root / LOCK_RELPATH):
        if not manifest_path.is_file():
            raise ManifestError(f"HALT: manifest not found: {manifest_path}")
        # Fresh read INSIDE the lock. A value read before acquisition is stale.
        text = manifest_path.read_text(encoding="utf-8")
        if BEGIN_MARKER not in text or END_MARKER not in text:
            raise ManifestError(
                "HALT: the manifest carries no generated block to replace. "
                "This tool never invents one -- add the marker pair by hand once."
            )
        artifacts = compute_artifacts(root)
        head = text.split(BEGIN_MARKER, 1)[0]
        tail = text.split(END_MARKER, 1)[1]
        _atomic_replace(manifest_path, head + render_block(artifacts) + tail)
    return artifacts


# ---------------------------------------------------------------------------
# selftest -- a detector that has never fired is indistinguishable from a
# broken one (no-silent-failures.md rule 3)
# ---------------------------------------------------------------------------


def selftest(root: Path) -> int:
    """Prove every check path can fire. Returns a process exit code."""
    results: list[tuple[str, bool, str]] = []

    def record(name: str, passed: bool, detail: str = "") -> None:
        results.append((name, passed, detail))

    with tempfile.TemporaryDirectory() as tmp:
        fixture = Path(tmp) / "repo"
        shutil.copytree(root / BUNDLE_DIR_RELPATH, fixture / BUNDLE_DIR_RELPATH)
        write(fixture)

        ok, findings = check(fixture)
        record("clean bundle verifies", ok, "; ".join(findings))

        # CHANGED
        victim = fixture / BUNDLE_DIR_RELPATH / "IMPRINT.md"
        original = victim.read_bytes()
        victim.write_bytes(original + b"\n")
        ok, findings = check(fixture)
        record("CHANGED fires", (not ok) and any(f.startswith("CHANGED") for f in findings))
        victim.write_bytes(original)

        # UNTRACKED
        intruder = fixture / BUNDLE_DIR_RELPATH / "rules" / "zz-intruder.md"
        intruder.write_text("planted by the selftest\n", encoding="utf-8")
        ok, findings = check(fixture)
        record("UNTRACKED fires", (not ok) and any(f.startswith("UNTRACKED") for f in findings))
        record("UNCLAIMED fires", any(f.startswith("UNCLAIMED") for f in findings))
        intruder.unlink()

        # MISSING
        moved = victim.read_bytes()
        victim.unlink()
        ok, findings = check(fixture)
        record("MISSING fires", (not ok) and any(f.startswith("MISSING") for f in findings))
        victim.write_bytes(moved)

        # strict parser HALTs rather than skipping
        try:
            parse_block(f"{BEGIN_MARKER}\nartifacts:\n  surprise: 1\n{END_MARKER}")
            record("strict parser HALTs on an unknown line", False, "it returned instead")
        except ManifestError:
            record("strict parser HALTs on an unknown line", True)

        # empty bundle HALTs rather than passing vacuously
        empty = Path(tmp) / "empty"
        (empty / BUNDLE_DIR_RELPATH).mkdir(parents=True)
        try:
            compute_artifacts(empty)
            record("empty bundle HALTs", False, "it returned an empty list")
        except ManifestError:
            record("empty bundle HALTs", True)

        # the lock refuses a non-.lock path
        try:
            with _exclusive_lock(fixture / MANIFEST_RELPATH):
                pass
            record("lock refuses a non-.lock path", False, "it locked the store itself")
        except ManifestError:
            record("lock refuses a non-.lock path", True)

        ok, findings = check(fixture)
        record("bundle restored clean", ok, "; ".join(findings))

    passed = sum(1 for _, ok, _ in results if ok)
    for name, ok, detail in results:
        marker = "PASS" if ok else "FAIL"
        print(f"  [{marker}] {name}{(' -- ' + detail) if detail else ''}")
    print(f"selftest: {passed}/{len(results)} paths fired as specified")
    return 0 if passed == len(results) else 1


# ---------------------------------------------------------------------------


def _default_root() -> Path:
    return Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=None, help="repository root")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="verify; exit 1 on drift")
    mode.add_argument("--write", action="store_true", help="recompute the block in place")
    mode.add_argument("--selftest", action="store_true", help="prove the checks can fire")
    args = parser.parse_args(argv)
    root = (args.root or _default_root()).resolve()

    try:
        if args.selftest:
            return selftest(root)
        if args.write:
            artifacts = write(root)
            print(f"wrote {len(artifacts)} artifact hashes to {root / MANIFEST_RELPATH}")
            return 0
        # --check is the default: a bare invocation must never mutate a signed bundle.
        ok, findings = check(root)
        if ok:
            recorded = parse_block((root / MANIFEST_RELPATH).read_text(encoding="utf-8"))
            print(f"OK: {len(recorded)}/{len(recorded)} bundle artifacts match the manifest")
            return 0
        print(f"DRIFT: {len(findings)} finding(s) against {root / BUNDLE_DIR_RELPATH}")
        for finding in findings:
            print(f"  {finding}")
        return 1
    except ManifestError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
