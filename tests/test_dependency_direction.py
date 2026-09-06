"""The dependency arrow points one way, and a test says so.

PURPOSE
    ``intentops_core`` must import nothing from any saddle. That single rule is
    what makes a second host a second PACKAGE rather than an edit to the gate,
    and it is the reason the gate can be unit-tested without a subprocess and
    reasoned about without a runtime.

    It is also the rule most likely to be broken by accident and least likely
    to be noticed: an import added for one convenient constant does not break
    any test, does not change any verdict, and quietly welds the gate to one
    host forever. Nothing else in the suite would fail. So this file greps.

WHY A GREP AND NOT AN IMPORT CHECK
    Importing the core and inspecting ``sys.modules`` only catches an inversion
    on a path that actually executes. A conditional import inside a rarely
    taken branch would pass that check and still be an inversion. Reading the
    source finds it wherever it is written.

BLIND SPOTS
    * It reads import STATEMENTS. A dynamic ``importlib.import_module`` built
      from a string is invisible to it, and so is a plugin lookup by entry
      point. Neither exists today; if one is ever added, this test will not
      notice and this paragraph is the warning.
    * It checks the direction of the arrow, not the size of it. A saddle that
      reaches deep into the core's private internals passes here.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGES = REPO_ROOT / "packages"
CORE_PKG = PACKAGES / "intentops-core"
SADDLE_GLOB = "intentops-saddle-*"

#: Matches `import intentops_saddle_x`, `from intentops_saddle_x import ...`,
#: and the dotted forms of both.
_SADDLE_IMPORT = re.compile(
    r"^\s*(?:from|import)\s+(intentops_saddle[\w.]*)", re.MULTILINE
)
_CORE_IMPORT = re.compile(
    r"^\s*(?:from|import)\s+(intentops_core[\w.]*)", re.MULTILINE
)


def _python_files(root: Path) -> List[Path]:
    return sorted(
        p for p in root.rglob("*.py")
        if "__pycache__" not in p.parts and ".venv" not in p.parts
    )


def _hits(paths: List[Path], pattern: "re.Pattern[str]") -> List[Tuple[Path, int, str]]:
    found: List[Tuple[Path, int, str]] = []
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in pattern.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            found.append((path, line_no, match.group(1)))
    return found


def test_the_core_imports_no_saddle() -> None:
    """The inversion this whole package split exists to prevent."""
    files = _python_files(CORE_PKG)
    assert files, f"no core sources were found under {CORE_PKG}; the check is vacuous"

    inversions = _hits(files, _SADDLE_IMPORT)
    rendered = "\n".join(
        f"  {p.relative_to(REPO_ROOT).as_posix()}:{n} imports {mod}"
        for p, n, mod in inversions
    )
    assert not inversions, (
        "the core imports a saddle, which welds the gate to one host:\n"
        f"{rendered}\n"
        "The gate must return a Verdict and know nothing about any runtime. "
        "Whatever the saddle was providing belongs in the saddle, or as a "
        "parameter passed INTO the gate."
    )


def test_a_saddle_does_import_the_core() -> None:
    """The arrow exists at all.

    Without this the previous test passes for the wrong reason: two packages
    that never speak to each other satisfy 'the core imports no saddle'
    perfectly, and a detector that cannot distinguish that from a healthy tree
    has told you nothing.
    """
    saddle_roots = sorted(
        p for p in PACKAGES.glob(SADDLE_GLOB)
        if p.is_dir() and any(p.rglob("*.py"))
    )
    assert saddle_roots, (
        "no implemented saddle was found; the direction check has no arrow to "
        "measure and would pass over an empty tree"
    )

    for root in saddle_roots:
        hits = _hits(_python_files(root), _CORE_IMPORT)
        assert hits, (
            f"{root.name} contains Python but never imports intentops_core. "
            "A saddle that does not use the gate is not a saddle -- it is a "
            "second, unreviewed implementation of one."
        )


def test_the_core_package_declares_no_saddle_dependency() -> None:
    """The same rule at the packaging layer, where it is easier to break quietly."""
    for manifest in [REPO_ROOT / "pyproject.toml", CORE_PKG / "pyproject.toml"]:
        if not manifest.is_file():
            continue
        text = manifest.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert "intentops-saddle" not in stripped, (
                f"{manifest.relative_to(REPO_ROOT).as_posix()} declares a "
                f"dependency on a saddle: {stripped!r}. Installing the core "
                "must never pull a host adapter in with it."
            )


def test_the_check_can_actually_fail(tmp_path: Path) -> None:
    """A detector that has never fired is indistinguishable from a broken one."""
    planted = tmp_path / "fake_core.py"
    planted.write_text(
        "from intentops_saddle_somehost import thing\n", encoding="utf-8"
    )
    assert _hits([planted], _SADDLE_IMPORT), (
        "the inversion pattern did not match a deliberate inversion; the "
        "green result above would have meant nothing"
    )

    plain = tmp_path / "plain.py"
    plain.write_text("import json\n# from intentops_saddle_x import y\n", encoding="utf-8")
    assert not _hits([plain], _SADDLE_IMPORT), (
        "the pattern matched a commented-out line, so every future green run "
        "would be one stray comment away from a false alarm"
    )
