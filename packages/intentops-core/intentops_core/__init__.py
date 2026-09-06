"""intentops_core -- the estate-independent core of an IntentOps node.

PURPOSE
    The package that carries what is true on every node: the invariants, the
    gate, the councils, the validation family, the estate manifests and the
    genesis state machine. Nothing here may name a particular operator, estate
    or host; per-host enforcement lives in a saddle package, and per-operator
    facts live in the node's identity repo.

WRITE MODEL
    Not a store. This module writes nothing.

VERSION
    The package version is read from the repository's ``VERSION`` file -- one
    file, one number, no duplication -- and, when this package is INSTALLED
    rather than checked out, from the distribution metadata that same file
    produced at build time. It is resolved LAZILY: importing this package must
    never fail because a sibling file is missing, but READING ``__version__``
    when neither source exists raises ``VersionUnavailable`` naming the remedy.
    A ``0.0.0`` placeholder would be a silent default for a load-bearing fact,
    which is the failure this project refuses everywhere else; so the value is
    either the real one or a loud error.

    ``VERSION`` must be a PEP 440 version. It read ``0.1.0-genesis``, which is
    not one, and the whole distribution therefore failed to BUILD -- the
    console script never existed and no clean install was possible. Corrected
    2026-09-06 to ``0.1.0a0+genesis``, which keeps the label as a PEP 440 local
    segment. The imprint carries its own ``imprint_version`` and is a different
    number that does not have to satisfy PEP 440.

BLIND SPOTS
    - ``VERSION`` is located by walking upward from this file and by checking
      the installed-package layout. A node that vendors this package somewhere
      exotic, uninstalled, gets ``VersionUnavailable``, not a guess.
    - The installed-metadata fallback answers with what was recorded at BUILD
      time. A source tree edited after installation reports the built number
      until it is reinstalled -- which is the honest answer for an installed
      distribution, not a stale one.
    - ``__version__`` is not cached against a later edit of ``VERSION``; it is
      read on first access and held. A version bump mid-process is not seen.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = [
    "VersionUnavailable",
    "installed_distribution_version",
    "read_version",
    "version_file_candidates",
]

_VERSION_FILENAME = "VERSION"
_version_cache: str | None = None


class VersionUnavailable(RuntimeError):
    """Raised when the VERSION file cannot be found or is empty.

    Carries ``remedy`` so a caller can print what to do rather than a bare
    traceback.
    """

    def __init__(self, message: str, remedy: str) -> None:
        super().__init__(f"{message}\n  remedy: {remedy}")
        self.remedy = remedy


def version_file_candidates(start: Path | None = None) -> list[Path]:
    """Return, in search order, every place ``VERSION`` is allowed to live.

    Deterministic and side-effect free: it does not touch the filesystem.
    """
    here = (start or Path(__file__).resolve()).resolve()
    base = here if here.is_dir() else here.parent
    candidates: list[Path] = [base / _VERSION_FILENAME]
    candidates.extend(parent / _VERSION_FILENAME for parent in base.parents)
    return candidates


def _distribution_owns_this_module(dist: Any) -> bool:
    """Does this distribution actually ship the file we are executing?

    The check exists because a distribution NAME is not an identity: another
    project installed under the same name would otherwise answer for us, and
    a version borrowed from a stranger is worse than no version at all.
    """
    here = Path(__file__).resolve()
    try:
        files = dist.files or []
    except Exception:  # noqa: BLE001 - a metadata store we cannot read owns nothing
        return False
    for entry in files:
        try:
            if Path(dist.locate_file(entry)).resolve() == here:
                return True
        except Exception:  # noqa: BLE001 - one unresolvable entry is not a verdict
            continue
    return False


def installed_distribution_version() -> str | None:
    """The version this package was INSTALLED as, or None when it is not.

    A checkout reads ``VERSION``; an installed wheel has no repository above it
    and never will. Its version is a recorded fact in the distribution
    metadata, written from that same file at build time, so reading it there is
    reading the same number from the one place it survived -- not defaulting.

    Returns None rather than raising: absence of an install is not an error, it
    is the checkout case. Returns None also when the installed distribution of
    that name does not ship THIS file, which is a name collision, not us.
    """
    try:
        from importlib.metadata import PackageNotFoundError, distribution
    except ImportError:  # pragma: no cover - stdlib since 3.8
        return None
    for name in ("intentops", "intentops-core"):
        try:
            dist = distribution(name)
        except PackageNotFoundError:
            continue
        except Exception:  # noqa: BLE001 - a broken metadata store is not a version
            return None
        if not _distribution_owns_this_module(dist):
            continue
        found = (dist.version or "").strip()
        if found:
            return found
    return None


def read_version(start: Path | None = None) -> str:
    """Read the version string from the nearest ``VERSION`` file.

    With no ``start`` this is the package asking for its own version, so an
    installed distribution's recorded metadata is consulted when no ``VERSION``
    file exists above this file. With an explicit ``start`` it is a pure
    filesystem query over that tree and nothing else -- a caller asking about a
    directory must not be answered with a fact about the running interpreter.

    Raises ``VersionUnavailable`` if no source has it, or if the file is blank.
    An empty VERSION file is a HALT for the same reason a missing one is: it is
    a load-bearing field nobody filled in.
    """
    candidates = version_file_candidates(start)
    for candidate in candidates:
        if not candidate.is_file():
            continue
        text = candidate.read_text(encoding="utf-8").strip()
        if not text:
            raise VersionUnavailable(
                f"VERSION file is empty: {candidate}",
                "write the version string (for example '0.1.0') into that file",
            )
        return text
    if start is None:
        installed = installed_distribution_version()
        if installed:
            return installed
    searched = "\n    ".join(str(path) for path in candidates[:4])
    raise VersionUnavailable(
        "no VERSION file found for intentops_core"
        + ("" if start is not None else
           ", and no installed distribution of it shipped this file"),
        "create a VERSION file at the repository root"
        + ("" if start is not None else ", or install the package")
        + ". Searched:\n    " + searched,
    )


def __getattr__(name: str) -> str:
    # PEP 562. Import stays cheap and total; reading the version is where a
    # missing VERSION becomes loud.
    if name == "__version__":
        global _version_cache
        if _version_cache is None:
            _version_cache = read_version()
        return _version_cache
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted([*__all__, "__version__"])
