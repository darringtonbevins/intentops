#!/usr/bin/env python
"""Zero-tolerance scan for private-key and credential SHAPES in the repo tree.

PURPOSE
    Answer one question mechanically, with no model in the path: does any file
    in this tree carry something shaped like a private key or a credential?

    The rule it enforces is absolute and has no exception list: a private key
    never lives in a repository, public or private -- it lives in a hardware
    token, an OS credential store, a TPM, or a passphrase-wrapped file that is
    not tracked. "Wherever it appears, including tests and fixtures" is the
    whole point: an exclusion list is how a zero-tolerance scanner becomes a
    scanner with one blind spot that somebody chose.

    This is why the negative fixtures in this repository contain NO key-shaped
    file. Committing a key-shaped file to prove a checker that forbids
    key-shaped files is the defect the checker exists to prevent, so every
    key-shape case is constructed in a temporary directory by ``--selftest``
    and by the unit tests, and nothing of that shape is ever committed.

    Five classes, each of which can fire independently:

      PEM-PRIVATE    a PEM block whose header says PRIVATE KEY
      SEED-HEX       a 64-hex string sitting next to the word private/secret/seed
      TOKEN-SHAPE    a bearer-token shape (four well-known issuer prefixes)
      KEY-FILENAME   a filename that is a key or secret container by extension
      TRUST-KEYNAME  a trust file carrying a key named private/secret/seed/d/passphrase

WRITE MODEL
    Not a store. This script writes nothing outside a temporary directory it
    creates and removes during ``--selftest``.

EXIT CODES
    0  no finding
    1  at least one finding, or a selftest case that failed to fire

BLIND SPOTS
    * It reads SHAPES, never meaning. A private key stored base64-wrapped
      inside a JSON string field with no recognisable header, split across
      lines, or encrypted at rest is invisible to it. This is a floor under a
      real secret scanner in CI, never a replacement for one.
    * Binary files are skipped after a null-byte sniff. A key inside a
      compiled artifact, an archive, or an image is not read.
    * The 64-hex class requires a keyword within one line of the digest, so an
      unlabelled raw seed is missed. Without that proximity rule every SHA-256
      hash in every manifest would fire, and a detector that cries wolf on its
      own manifests is a detector somebody switches off.
    * A file larger than ``MAX_BYTES`` is reported as UNSCANNED and stays in
      the denominator. It is never silently skipped: a refusal must remain
      countable, or the clean number is a smaller population wearing a better
      score.
"""

from __future__ import annotations

import argparse
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: Directories never walked. Each is either not source (``.git`` objects), a
#: dependency tree, or a build artifact -- and walking them is also the
#: whole-tree walk that wedges a machine.
SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "__pycache__", ".venv", "venv", "node_modules",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build", ".tox",
})

#: Extensions read as text. Everything else is sniffed for a null byte first.
MAX_BYTES = 2_000_000

# --------------------------------------------------------------------------
# the shapes
#
# Every pattern below is assembled from fragments so that THIS FILE does not
# match its own detectors. A scanner that flags itself gets an exclusion, and
# an exclusion is how zero tolerance stops being zero.
# --------------------------------------------------------------------------

_PEM_OPEN = "-----BEGIN"
_PEM_PRIVATE_TAIL = "PRIVATE KEY-----"
PEM_PRIVATE = re.compile(
    re.escape(_PEM_OPEN) + r"[ A-Z0-9]{0,24}" + re.escape(_PEM_PRIVATE_TAIL)
)

HEX64 = re.compile(r"\b[0-9a-fA-F]{64}\b")
SECRET_WORD = re.compile(r"private|secret|seed|passphrase|priv_key|privkey", re.IGNORECASE)

TOKEN_SHAPES: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("slack-style token", re.compile(r"xox" + r"[abprs]-[A-Za-z0-9-]{10,}")),
    ("code-forge token", re.compile(r"gh" + r"[pousr]_[A-Za-z0-9]{20,}")),
    ("cloud access key id", re.compile(r"AKI" + r"A[0-9A-Z]{16}")),
    ("model-api key", re.compile(r"(?<![A-Za-z0-9_-])sk-" + r"[A-Za-z0-9_-]{24,}")),  # left-anchored 2026-09-06: unanchored, it fired from the 4th char of "task" and "risk"
)

#: Filename shapes that ARE a key or secret container, whatever they hold.
#:
#: OPERATING POINT (SIG-1 clause 7). The first draft matched the word "secret"
#: anywhere in a filename and flagged ``.github/workflows/secret-scan.yml`` --
#: a workflow ABOUT secrets, holding none. The pattern is therefore narrowed to
#: names that ARE the container: a key extension, a well-known private-key
#: filename, a dotenv file, or a file whose whole stem is secret(s) or
#: credential(s). The error it trades against is a container with a creative
#: name (``my-notes-really.txt`` holding a key), which the CONTENT classes above
#: are there to catch. Narrowing beat excluding on purpose: an exclusion list is
#: how a zero-tolerance scanner acquires the one blind spot somebody chose.
KEY_FILENAME = re.compile(
    r"\.(?:key|pfx|p12|jks|keystore)$"
    r"|^id_(?:rsa|dsa|ecdsa|ed25519)$"
    r"|^\.env(?:\.[\w-]+)?$"
    r"|^(?:secrets?|credentials?)(?:\.(?:ya?ml|json|txt|ini|env))?$",
    re.IGNORECASE,
)

#: Key names that must never appear in a trust projection. ``d`` is the private
#: scalar of a JWK: one letter, and the whole key.
TRUST_KEYNAMES = re.compile(
    r"^\s*[\"']?(private[\w-]*|[\w-]*secret[\w-]*|seed|d|passphrase)[\"']?\s*:",
    re.IGNORECASE | re.MULTILINE,
)

#: Files whose keys are checked by TRUST-KEYNAME, by normalized path fragment.
TRUST_PATH_HINTS = ("config/trust-", "/trust/", "trust-roots", "trust-revocation")


@dataclass(frozen=True)
class Finding:
    code: str
    path: str
    line: int
    detail: str

    def __str__(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"{self.code} {where} -- {self.detail}"


def _norm(path: Path, root: Path) -> str:
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        rel = path
    return str(rel).replace("\\", "/")


def iter_files(root: Path) -> Iterable[Path]:
    """Walk the tree, pruning dependency and VCS directories at the top."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            yield Path(dirpath) / name


def _read_text(path: Path) -> Optional[str]:
    """Return the file's text, or None when it is binary or oversized."""
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size > MAX_BYTES:
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:4096]:
        return None
    return raw.decode("utf-8", errors="replace")


def scan_text(rel_path: str, text: str) -> List[Finding]:
    """Every shape finding in one file's text. Pure: same text in, same list out."""
    findings: List[Finding] = []
    lines = text.splitlines()

    for index, line in enumerate(lines, start=1):
        if PEM_PRIVATE.search(line):
            findings.append(Finding(
                "PEM-PRIVATE", rel_path, index,
                "a PEM block whose header says PRIVATE KEY. A private key never "
                "lives in a repository; move it to a token, a TPM, or the OS "
                "credential store and rotate it, because it is now disclosed.",
            ))
        for match in HEX64.finditer(line):
            window = line + " " + (lines[index - 2] if index >= 2 else "")
            if SECRET_WORD.search(window):
                findings.append(Finding(
                    "SEED-HEX", rel_path, index,
                    f"a 64-hex value beside a private/secret/seed word "
                    f"({match.group(0)[:8]}...) -- that is the shape of a raw key seed",
                ))
                break
        for label, pattern in TOKEN_SHAPES:
            if pattern.search(line):
                findings.append(Finding(
                    "TOKEN-SHAPE", rel_path, index,
                    f"a {label} shape. Rotate it, then move it to the vault chain.",
                ))

    lowered = rel_path.lower()
    if any(hint in lowered for hint in TRUST_PATH_HINTS):
        for match in TRUST_KEYNAMES.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            findings.append(Finding(
                "TRUST-KEYNAME", rel_path, line_no,
                f"a trust projection carries the key {match.group(1)!r}. Trust "
                "files in a repository hold PUBLIC material only.",
            ))
    return findings


def scan_tree(root: Path) -> Tuple[List[Finding], int, List[str]]:
    """Scan every readable file. Returns ``(findings, scanned, unscanned)``."""
    findings: List[Finding] = []
    scanned = 0
    unscanned: List[str] = []
    for path in iter_files(root):
        rel = _norm(path, root)
        if KEY_FILENAME.search(path.name):
            findings.append(Finding(
                "KEY-FILENAME", rel, 0,
                "the filename itself is a key or secret container. Whatever it "
                "holds today, this name must not be tracked.",
            ))
        text = _read_text(path)
        if text is None:
            unscanned.append(rel)
            continue
        scanned += 1
        findings.extend(scan_text(rel, text))
    return findings, scanned, unscanned


# --------------------------------------------------------------------------
# selftest -- every class constructed in a temp tree
# --------------------------------------------------------------------------


def _plant_pem(root: Path) -> None:
    body = "\n".join(["MC4CAQAwBQYDK2VwBCIEI" + "A" * 40])
    (root / "leaked.txt").write_text(
        _PEM_OPEN + " " + _PEM_PRIVATE_TAIL + "\n" + body + "\n", encoding="utf-8"
    )


def _plant_seed_hex(root: Path) -> None:
    (root / "notes.md").write_text(
        "private_seed: " + ("ab12" * 16) + "\n", encoding="utf-8"
    )


def _plant_token(root: Path) -> None:
    (root / "settings.json").write_text(
        '{"token": "' + "xox" + "b-" + "1234567890abcdef" + '"}\n', encoding="utf-8"
    )


def _plant_key_filename(root: Path) -> None:
    (root / "operator-root.key").write_text("not a key, but the name is\n", encoding="utf-8")


def _plant_trust_keyname(root: Path) -> None:
    trust = root / "config"
    trust.mkdir(parents=True, exist_ok=True)
    (trust / "trust-roots.yaml").write_text(
        "roots:\n  - key_id: EXAMPLE\n    private_key: PLACEHOLDER\n", encoding="utf-8"
    )


def _plant_clean(root: Path) -> None:
    """The control. Everything here LOOKS like the shapes above and is not one."""
    (root / "README.md").write_text(
        "A public tree. It talks ABOUT private keys, which must not fire:\n"
        "a private key never lives in a repository.\n", encoding="utf-8"
    )
    (root / "MANIFEST.yaml").write_text(
        "artifacts:\n  - path: a.md\n    sha256: " + ("cd34" * 16) + "\n",
        encoding="utf-8",
    )
    # A workflow ABOUT secrets holds none. This is the case that narrowed the
    # filename pattern; without it in the control, the narrowing is untested.
    (root / "secret-scan.yml").write_text("name: secret scan\n", encoding="utf-8")
    (root / "keystore-notes.md").write_text("How keystores work.\n", encoding="utf-8")


SELFTEST_CASES: Tuple[Tuple[str, str, Callable[[Path], None]], ...] = (
    ("a PEM private-key block", "PEM-PRIVATE", _plant_pem),
    ("a 64-hex seed beside a secret word", "SEED-HEX", _plant_seed_hex),
    ("a bearer-token shape", "TOKEN-SHAPE", _plant_token),
    ("a key-shaped filename", "KEY-FILENAME", _plant_key_filename),
    ("a private key name in a trust file", "TRUST-KEYNAME", _plant_trust_keyname),
    ("a clean tree that only TALKS about keys", "", _plant_clean),
)


def run_selftest() -> int:
    print("trust_material_check selftest:")
    failures: List[str] = []
    for label, expected, plant in SELFTEST_CASES:
        with tempfile.TemporaryDirectory(prefix="intentops-trust-selftest-") as tmp:
            root = Path(tmp)
            plant(root)
            findings, scanned, _unscanned = scan_tree(root)
            codes = sorted({f.code for f in findings})
            if expected:
                ok = expected in codes
                detail = "" if ok else f"expected {expected}, saw {codes or 'nothing'}"
            else:
                ok = not findings
                detail = "" if ok else f"false positive: {codes}"
            print(f"  [{'FIRED ' if ok else 'MISSED'}] {label} "
                  f"(scanned {scanned}) {detail}".rstrip())
            if not ok:
                failures.append(label)
    total = len(SELFTEST_CASES)
    print(f"selftest: {total - len(failures)}/{total} cases behaved as declared")
    if failures:
        print("SELFTEST FAILED: " + ", ".join(failures))
        return 1
    print("SELFTEST PASS -- every shape class can fire, and prose about keys does not")
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _report(root: Path, findings: Sequence[Finding], scanned: int,
            unscanned: Sequence[str]) -> int:
    print(f"tree: {root}")
    print(f"  files scanned {scanned}; unscanned (binary or oversized) {len(unscanned)}")
    for rel in unscanned[:10]:
        print(f"    UNSCANNED {rel}")
    if len(unscanned) > 10:
        print(f"    ... and {len(unscanned) - 10} more, all counted above")
    if not findings:
        print(f"  findings 0 / {scanned} scanned files")
        print("VERDICT: CLEAN")
        return 0
    for finding in findings:
        print(f"  {finding}")
    print(f"  findings {len(findings)} / {scanned} scanned files")
    print("VERDICT: TRUST MATERIAL PRESENT -- this is zero tolerance; there is no "
          "exception list to add to")
    return 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="zero-tolerance private-key and credential shape scan (Code rung)"
    )
    parser.add_argument("--root", type=Path, default=_REPO_ROOT,
                        help="tree to scan (default: the repository root)")
    parser.add_argument("--selftest", action="store_true",
                        help="plant each shape in a temp tree and prove the scan fires")
    args = parser.parse_args(argv)

    if args.selftest:
        return run_selftest()
    findings, scanned, unscanned = scan_tree(args.root)
    return _report(args.root, findings, scanned, unscanned)


if __name__ == "__main__":
    raise SystemExit(main())
