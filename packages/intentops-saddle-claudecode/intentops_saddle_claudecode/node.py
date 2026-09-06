"""Node discovery -- where this saddle finds the node it is riding.

PURPOSE
    The gate in ``intentops_core`` is pure: it takes a tool name, an input, and
    a set of estate indicators, and returns a Verdict. Somebody has to go to
    disk and find those indicators, find the stand-down marker, and find the
    rules a fresh window should read. That is host-side work, so it lives here
    rather than in the core, and this module is the only place in the adapter
    that touches the filesystem for anything other than the ledger.

WRITE MODEL
    None -- read-only. This module opens files and never writes one. The one
    thing the adapter writes is the ledger, and that has its own module and its
    own write model.

BLIND SPOTS
    * Root resolution walks upward looking for markers. A node whose runtime
      state directory has been moved without setting ``INTENTOPS_NODE_ROOT``
      resolves to the wrong root and reads an empty estate -- which fails
      toward gating, but silently reads the wrong node.
    * An absent estate map is reported as absent, not treated as an error. A
      node before its estate is elicited genuinely has no map, and the generic
      operation patterns still fire without one. An UNREADABLE map is a
      different thing and raises.
    * ``boot_corpus`` reports which files exist. It cannot tell whether the
      host actually put them in the window, which is exactly the gap the
      contract test cannot close either.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from intentops_core.gate import RealityIndicators, indicators_from_estate_map

__all__ = [
    "EstateUnreadable",
    "NodeContext",
    "boot_corpus",
    "halt_reason",
    "resolve_root",
]

#: Marker directories that identify a node root, in the order they are tried.
_ROOT_MARKERS: Tuple[str, ...] = (".intentops", ".git")

#: Where the imprint's rules land on a node at G3, and where a fresh window
#: is expected to read them.
_RULES_DIR = ".intentops-rules"

_HALT_MARKER = Path(".intentops") / "halt.marker"
_ESTATE_MAP = Path("estate") / "ESTATE-MAP.yaml"


class EstateUnreadable(RuntimeError):
    """The estate map exists and cannot be read.

    Distinct from "there is no estate map", which is a true statement about a
    new node. A file that is present and unparseable is a field nobody read,
    and the adapter refuses rather than classifying against a half-loaded map.
    """


def resolve_root(payload_cwd: Optional[str] = None) -> Tuple[Path, str]:
    """Find the node root. Returns ``(root, how_it_was_found)``.

    The order is a stated rule, not a fall-through -- each step is a place a
    node root is legitimately declared, and the provenance string travels into
    the ledger so a later reader can see which one answered:

      1. ``INTENTOPS_NODE_ROOT`` -- an explicit override, never persisted
      2. the nearest ancestor of the working directory holding ``.intentops/``
      3. the nearest ancestor holding ``.git/``
      4. the working directory itself

    Step 4 is what lets a freshly cloned, not-yet-born node run ``genesis`` at
    all. Without it the gate would refuse every call including the one that
    creates the state it is looking for, which is a deadlock dressed as rigour.
    """
    override = os.environ.get("INTENTOPS_NODE_ROOT")
    if override:
        return Path(override).resolve(), "env:INTENTOPS_NODE_ROOT"

    start = Path(payload_cwd).resolve() if payload_cwd else Path.cwd().resolve()
    for marker in _ROOT_MARKERS:
        here = start
        while True:
            if (here / marker).is_dir():
                return here, f"ancestor with {marker}/"
            if here.parent == here:
                break
            here = here.parent
    return start, "working directory (no marker found)"


def halt_reason(root: Path) -> Optional[str]:
    """S5. Return the stand-down reason when the node is stood down.

    The marker's CONTENT is never required and never demanded. A stand-down
    takes no reason from the operator -- being asked to justify switching a
    thing off, to the thing being switched off, is the pressure this design
    refuses to apply. If the file happens to carry text it is echoed as
    context; an empty file stands the node down exactly as firmly.
    """
    marker = root / _HALT_MARKER
    if not marker.exists():
        return None
    try:
        text = marker.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        text = ""
    return text or "the node is stood down (stand-down marker present)"


@dataclass(frozen=True)
class NodeContext:
    """Everything the adapter had to go to disk for, gathered once."""

    root: Path
    root_provenance: str
    indicators: RealityIndicators
    estate_map_state: str  # "loaded" | "absent"

    @property
    def halted(self) -> Optional[str]:
        return halt_reason(self.root)


def load_context(payload_cwd: Optional[str] = None) -> NodeContext:
    """Resolve the root and load the estate indicators from it."""
    root, provenance = resolve_root(payload_cwd)
    map_path = root / _ESTATE_MAP
    if not map_path.is_file():
        # A node before its estate is elicited has no map, and that is the
        # honest state. Empty indicators narrow nothing: the generic operation
        # patterns (a push, a recursive delete, a destructive statement) still
        # fire, so a blank node is gated on operations rather than on names.
        return NodeContext(root, provenance, RealityIndicators(), "absent")

    try:
        import yaml  # type: ignore[import-untyped]

        data = yaml.safe_load(map_path.read_text(encoding="utf-8"))
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise EstateUnreadable(
            f"{map_path} exists but PyYAML is not installed, so the estate "
            "cannot be read. Remedy: pip install pyyaml. The adapter will not "
            "classify against an estate it could not load."
        ) from exc
    except Exception as exc:
        raise EstateUnreadable(f"{map_path} could not be read: {exc}") from exc

    if not isinstance(data, dict):
        raise EstateUnreadable(f"{map_path}: top level must be a mapping")
    return NodeContext(root, provenance, indicators_from_estate_map(data), "loaded")


def boot_corpus(root: Path) -> Tuple[Path, ...]:
    """S4. The files a fresh window must be given before it does anything.

    Returns what EXISTS, sorted and deduplicated. An empty tuple is a real and
    reportable state -- a node whose rules are not on disk has them in name
    only -- so callers must render zero as a warning and never as success.
    """
    found: list[Path] = []
    rules = root / _RULES_DIR
    if rules.is_dir():
        found.extend(sorted(p for p in rules.glob("*.md") if p.is_file()))
    imprint = root / "genesis" / "imprint" / "IMPRINT.md"
    if imprint.is_file():
        found.append(imprint)
    seen: set[Path] = set()
    ordered: list[Path] = []
    for p in found:
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    return tuple(ordered)
