"""Shared fixtures. The one that matters here is ``placeholder_tree``.

PURPOSE
    The release-root ceremony has run for this build: ``config/trust-roots.yaml``
    carries active roots and a real pinned fingerprint, the compiled pin says
    ``minted``, and ``genesis/imprint/IMPRINT-MANIFEST.yaml`` carries a real
    signature. Before it ran, a large family of tests asserted the PLACEHOLDER
    behaviour by reading those same live carriers -- G1 halting, two
    placeholders never reading as agreement, the wizard minting from scratch,
    ``intentops verify`` reporting unverified.

    Those refusals still have to be reachable, or the suite would only prove
    that a minted build passes and would say nothing about the state every
    fresh clone is in. So the placeholder state gets a fixture of its own: a
    throwaway copy of this repository, rewritten back to the pre-ceremony
    shape, plus the compiled pin -- carrier #2, which lives in an imported
    module and not in the copied tree -- patched to match. A tree whose files
    say "no ceremony has run" while the imported pin says ``minted`` is not a
    placeholder build; it is an inconsistent one, and it would exercise a
    third state that ships nowhere.

WRITE MODEL
    None into this repository. Every rewrite lands in ``tmp_path``. The live
    carriers are read and never written, which the ceremony wizard's happy-path
    test asserts byte-for-byte at the end of its run.

BLIND SPOTS
    - The rewrite reproduces the placeholder DATA shape, not the placeholder
      PROSE: the header comment in the copied trust-roots file still narrates
      the ceremony that produced the minted state. Nothing under test reads
      those comments, and rewriting prose to make a fixture look tidier is how
      a fixture starts lying about something else.
    - It proves nothing about the real ceremony. That a minted tree verifies is
      a separate, positive assertion made against the LIVE tree (see
      ``tests/test_genesis.py``), and it is the regression guard for the
      ceremony itself.
    - Every rewrite HALTS when the shape it expects is absent rather than
      writing a partial edit. A half-placeholder tree would be the exact tamper
      G1.3 and G1.4 exist to catch, so a fixture that could produce one would
      be manufacturing the attack it is meant to let us test for.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterator, List, Optional

import pytest

from intentops_core.genesis import trust_pin

REPO_ROOT = Path(__file__).resolve().parents[1]

ROOTS_RELPATH = Path("config") / "trust-roots.yaml"
PIN_RELPATH = (Path("packages") / "intentops-core" / "intentops_core"
               / "genesis" / "trust_pin.py")
GENESIS_DOC_RELPATH = Path("docs") / "GENESIS.md"
MANIFEST_RELPATH = Path("genesis") / "imprint" / "IMPRINT-MANIFEST.yaml"
BUILD_MANIFEST_RELPATH = Path("scripts") / "genesis" / "build_manifest.py"

DOC_BEGIN = "<!-- BEGIN release-root-fingerprint -->"
DOC_END = "<!-- END release-root-fingerprint -->"

#: What a working clone needs for ``build_manifest --check``, G1, a genesis
#: dry-run and a full ceremony sitting. Declared rather than "copy everything":
#: ``tests/`` and ``build/`` are several times the size of the rest and nothing
#: under test reads them.
COPY_ENTRIES = (
    "packages", "config", "genesis", "docs", "scripts", "estate", "identity",
    "deploy", "VERSION", "pyproject.toml", "README.md", "GENESIS.md",
)

#: The placeholder values, named once. Each is deliberately invalid: it will
#: not parse as a PEM key, a DER fingerprint or a DID, and no check may treat
#: it as one.
PLACEHOLDER_PEM = '"PLACEHOLDER-NOT-A-KEY"'
PLACEHOLDER_SCALARS: Dict[str, str] = {
    "status": "proposed",
    "fingerprint": '"sha256:PLACEHOLDER"',
    "did": '"did:key:PLACEHOLDER"',
    "key_id": '"PLACEHOLDER16"',
    "valid_from": '"2026-01-01"',
    "valid_until": '"2036-01-01"',
    "evidence": ('"[PLACEHOLDER] no ceremony has occurred; this entry is a '
                 'shape, not a key"'),
}
PLACEHOLDER_CEREMONY = (
    "    ceremony:\n"
    "      minted_at: null\n"
    "      witnessed_by: null\n"
    "      record: null"
)
PLACEHOLDER_DOC_BODY = (
    "No ceremony has run for this build, so there is no fingerprint here to "
    "compare\nagainst. This is a PLACEHOLDER and not a weak pin: it is no pin "
    "at all, and\nG1.3 halts on it rather than letting two placeholders "
    "compare equal and read as\nagreement.\n\n"
    "    intentops-root:v1:sha256:PLACEHOLDER:did:key:PLACEHOLDER\n\n"
)


class PlaceholderTreeError(RuntimeError):
    """A carrier did not have the shape the rewrite expected. Never partial."""


# ---------------------------------------------------------------------------
# carrier surgery -- the inverse of the ceremony's three-carrier edit
# ---------------------------------------------------------------------------


def placeholder_trust_roots(text: str) -> str:
    """Carrier #1: the pin and every root record, back to the shipped shape.

    Textual and line-driven, the same way ``ceremony.wizard.edit_trust_roots``
    is textual, and for the same reason: this file's comments carry the
    reasoning for every closed vocabulary in it and a YAML round-trip would
    delete all of them.
    """
    applied: Dict[str, int] = {}
    out: List[str] = []
    current: Optional[str] = None
    skipping = False

    for line in text.splitlines():
        if skipping:
            # swallow the children of a replaced block (indent >= 6); anything
            # shallower ends it
            if line.strip() == "" or re.match(r"^ {6,}\S", line):
                continue
            skipping = False

        if re.match(r"^pinned_fingerprint:", line):
            out.append('pinned_fingerprint: "sha256:PLACEHOLDER"')
            applied["pin"] = 1
            continue

        new_root = re.match(r"^  - id: (\S+)", line)
        if new_root:
            current = new_root.group(1)
            out.append(line)
            continue

        if current:
            key_match = re.match(r"^    (\w+):(.*)$", line)
            if key_match:
                key, rest = key_match.group(1), key_match.group(2)
                if key == "public_key_pem":
                    out.append(f"    public_key_pem: {PLACEHOLDER_PEM}")
                    applied[f"{current}.public_key_pem"] = 1
                    skipping = rest.strip() in ("|", "|-", ">", ">-")
                    continue
                if key == "ceremony":
                    out.append(PLACEHOLDER_CEREMONY)
                    applied[f"{current}.ceremony"] = 1
                    skipping = True
                    continue
                if key in PLACEHOLDER_SCALARS:
                    out.append(f"    {key}: {PLACEHOLDER_SCALARS[key]}")
                    applied[f"{current}.{key}"] = 1
                    continue
            elif line and not line.startswith(" "):
                current = None
        elif line and not line.startswith(" ") and not line.startswith("#"):
            current = None

        out.append(line)

    root_ids = re.findall(r"^  - id: (\S+)", text, flags=re.MULTILINE)
    if not root_ids:
        raise PlaceholderTreeError(
            f"{ROOTS_RELPATH.as_posix()} declares no roots to rewrite")
    expected = {"pin"}
    for rid in root_ids:
        expected.add(f"{rid}.public_key_pem")
        expected.add(f"{rid}.ceremony")
        for key in PLACEHOLDER_SCALARS:
            expected.add(f"{rid}.{key}")
    missing = sorted(expected - set(applied))
    if missing:
        raise PlaceholderTreeError(
            f"{ROOTS_RELPATH.as_posix()} is missing expected fields, so the "
            f"rewrite would be partial: " + ", ".join(missing))

    result = "\n".join(out)
    return result + "\n" if text.endswith("\n") else result


def placeholder_trust_pin(text: str) -> str:
    """Carrier #2: the compiled pin. Both constants, or neither."""
    new_text, n_fp = re.subn(
        r"^ROOT_FINGERPRINT: str = .*$",
        "ROOT_FINGERPRINT: str = PLACEHOLDER_FINGERPRINT",
        text, count=1, flags=re.MULTILINE)
    new_text, n_state = re.subn(
        r"^PIN_STATE: str = .*$",
        'PIN_STATE: str = "placeholder"',
        new_text, count=1, flags=re.MULTILINE)
    if n_fp != 1 or n_state != 1:
        raise PlaceholderTreeError(
            f"{PIN_RELPATH.as_posix()} does not carry both assignments "
            f"(ROOT_FINGERPRINT={n_fp}, PIN_STATE={n_state})")
    return new_text


def placeholder_genesis_doc(text: str) -> str:
    """Carrier #3: the human-readable fingerprint, between its own markers."""
    if DOC_BEGIN not in text or DOC_END not in text:
        raise PlaceholderTreeError(
            f"{GENESIS_DOC_RELPATH.as_posix()} carries no "
            f"release-root-fingerprint marker pair; refusing to guess which "
            f"line is the fingerprint")
    head, rest = text.split(DOC_BEGIN, 1)
    _body, tail = rest.split(DOC_END, 1)
    return head + DOC_BEGIN + "\n" + PLACEHOLDER_DOC_BODY + DOC_END + tail


def placeholder_manifest(text: str) -> str:
    """The imprint manifest, back to the honest ``signatures: []``.

    The signature covers the GENERATED artifacts block and nothing else, so
    emptying this list changes no hash; the caller still re-runs
    ``build_manifest.py`` afterwards so that a drift introduced any other way
    is a loud failure of the fixture rather than a silent one of the test.
    """
    out: List[str] = []
    applied = 0
    skipping = False
    for line in text.splitlines():
        if skipping:
            if line.startswith("  "):
                continue
            skipping = False
        if re.match(r"^signatures:\s*(\[\])?\s*$", line):
            out.append("signatures: []")
            applied += 1
            skipping = line.rstrip().endswith("signatures:")
            continue
        out.append(line)
    if applied != 1:
        raise PlaceholderTreeError(
            f"{MANIFEST_RELPATH.as_posix()} carries {applied} top-level "
            f"`signatures` keys; exactly one is required")
    result = "\n".join(out)
    return result + "\n" if text.endswith("\n") else result


# ---------------------------------------------------------------------------
# the tree
# ---------------------------------------------------------------------------


def build_placeholder_tree(dest: Path, *, source: Path = REPO_ROOT) -> Path:
    """Copy the repository into ``dest`` and rewrite it to the shipped shape.

    Returns the tree root. Raises :class:`PlaceholderTreeError` rather than
    producing a tree that is placeholder in some carriers and minted in others.
    """
    dest.mkdir(parents=True, exist_ok=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache",
                                    ".ruff_cache")
    for name in COPY_ENTRIES:
        src = source / name
        if not src.exists():  # pragma: no cover - the clone is complete
            continue
        if src.is_dir():
            shutil.copytree(src, dest / name, ignore=ignore)
        else:
            shutil.copy2(src, dest / name)

    for relpath, rewrite in (
        (ROOTS_RELPATH, placeholder_trust_roots),
        (PIN_RELPATH, placeholder_trust_pin),
        (GENESIS_DOC_RELPATH, placeholder_genesis_doc),
        (MANIFEST_RELPATH, placeholder_manifest),
    ):
        path = dest / relpath
        if not path.is_file():
            raise PlaceholderTreeError(
                f"{relpath.as_posix()} is absent from the copied tree")
        path.write_text(rewrite(path.read_text(encoding="utf-8")),
                        encoding="utf-8")

    # The hashes are recomputed by the repository's own hasher rather than by a
    # second definition of what a bundle hash is, and the check that follows is
    # what makes a silent drift impossible to inherit.
    script = dest / BUILD_MANIFEST_RELPATH
    for mode in ("--write", "--check"):
        proc = subprocess.run([sys.executable, str(script), mode],
                              cwd=dest, capture_output=True, text=True,
                              check=False)
        if proc.returncode != 0:
            raise PlaceholderTreeError(
                f"build_manifest.py {mode} failed in the placeholder tree: "
                f"{(proc.stdout + proc.stderr).strip()}")
    return dest


@pytest.fixture()
def placeholder_pin(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The compiled pin as it ships: no ceremony has run for this build."""
    monkeypatch.setattr(trust_pin, "PIN_STATE", "placeholder")
    monkeypatch.setattr(trust_pin, "ROOT_FINGERPRINT",
                        trust_pin.PLACEHOLDER_FINGERPRINT)
    yield


@pytest.fixture()
def placeholder_tree(tmp_path: Path, placeholder_pin: None) -> Path:
    """A pre-ceremony copy of this repository, with a matching compiled pin.

    Both halves are required. The tree carries carriers #1 and #3; carrier #2
    is an imported module, so the copied ``trust_pin.py`` is what a SUBPROCESS
    reads and the patched module attribute is what this process reads. Leave
    either alone and the fixture describes a state that ships nowhere.
    """
    return build_placeholder_tree(tmp_path / "placeholder-tree")
