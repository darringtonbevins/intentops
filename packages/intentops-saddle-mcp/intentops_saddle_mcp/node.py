"""Where this saddle finds the node it is riding, and the repo it was built from.

PURPOSE
    The core gate is pure. Somebody has to go to disk to find the node root,
    the stand-down marker, the estate indicators, and the repository that
    carries the imprint. That is host-side work, so it lives here.

    Two roots, deliberately distinct and never conflated:

      node_root  the runtime state of ONE node -- ``.intentops/`` lives here,
                 and so does the trust material this server authenticates on.
      repo_root  the checkout of the framework -- ``config/`` and
                 ``genesis/imprint/`` live here, and it is read-only to us.

    Conflating them is how a node ends up authenticating against a token that
    belongs to a different node, or verifying an imprint it never installed.

WRITE MODEL
    None -- read-only. This module opens files and never writes one. The only
    writers in this package are ``ledger.py`` (append-only JSONL) and
    ``auth.py`` (locked whole-file replace), each declaring its own model.

BLIND SPOTS
    * Root resolution walks upward for a marker directory. A node whose runtime
      state has been moved without setting ``INTENTOPS_NODE_ROOT`` resolves to
      the wrong root and reads an empty estate. That fails TOWARD gating, but
      it silently reads the wrong node, and nothing here detects it.
    * An absent estate map is reported as absent, never as an error: a node
      before its estate is elicited genuinely has no map, and the generic
      operation patterns still fire without one. An UNREADABLE map raises --
      a field nobody read is not the same as a field that is empty.
    * ``repo_root`` is found by walking up from this package's own file. An
      installed wheel with no ``config/`` beside it resolves to None, and every
      caller must treat None as "cannot answer", never as "nothing to check".
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
    "halt_reason",
    "load_context",
    "resolve_node_root",
    "resolve_repo_root",
]

#: Marker directories that identify a node root, tried in this order.
_ROOT_MARKERS: Tuple[str, ...] = (".intentops", ".git")

_HALT_MARKER = Path(".intentops") / "halt.marker"
_ESTATE_MAP = Path("estate") / "ESTATE-MAP.yaml"

#: Files that must all be present for a directory to be the framework checkout.
_REPO_MARKERS: Tuple[str, ...] = ("config/saddles.yaml", "genesis/imprint")


class EstateUnreadable(RuntimeError):
    """The estate map exists and cannot be read.

    Distinct from "there is no estate map", which is a true statement about a
    new node. A file that is present and unparseable is a field nobody read,
    and this saddle refuses rather than classifying against a half-loaded map.
    """


def resolve_node_root(start: Optional[str] = None) -> Tuple[Path, str]:
    """Find the node root. Returns ``(root, how_it_was_found)``.

    Each step is a stated rule, not a fall-through, so that a wrong answer can
    be traced to the rule that produced it.
    """
    env = os.environ.get("INTENTOPS_NODE_ROOT")
    if env:
        return Path(env).resolve(), "INTENTOPS_NODE_ROOT"
    here = Path(start).resolve() if start else Path.cwd().resolve()
    for candidate in (here, *here.parents):
        for marker in _ROOT_MARKERS:
            if (candidate / marker).is_dir():
                return candidate, f"marker {marker}"
    return here, "cwd (no marker found)"


def resolve_repo_root() -> Optional[Path]:
    """Find the framework checkout, or None. None means CANNOT ANSWER."""
    env = os.environ.get("INTENTOPS_REPO_ROOT")
    if env:
        candidate = Path(env).resolve()
        return candidate if _is_repo(candidate) else None
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if _is_repo(candidate):
            return candidate
    return None


def _is_repo(candidate: Path) -> bool:
    return all((candidate / marker).exists() for marker in _REPO_MARKERS)


def halt_reason(node_root: Path) -> Optional[str]:
    """S5. The stand-down reason, or None. Checked ahead of every other gate."""
    marker = node_root / _HALT_MARKER
    if not marker.exists():
        return None
    try:
        body = marker.read_text(encoding="utf-8").strip()
    except OSError as exc:  # unreadable marker is still a stand-down
        return f"stand-down marker present and unreadable: {exc}"
    return body or "stand-down marker present"


@dataclass(frozen=True)
class NodeContext:
    """Everything from disk that a decision needs, gathered once per request."""

    node_root: Path
    repo_root: Optional[Path]
    root_source: str
    indicators: RealityIndicators
    estate_map_present: bool
    halted: Optional[str]


def load_context(start: Optional[str] = None) -> NodeContext:
    """Gather the on-disk half of a decision. Raises only on an UNREADABLE map."""
    node_root, source = resolve_node_root(start)
    estate = node_root / _ESTATE_MAP
    indicators = RealityIndicators()
    present = estate.is_file()
    if present:
        try:
            import yaml  # imported here so an absent map never needs pyyaml
        except ImportError as exc:  # pragma: no cover - pyyaml is a dependency
            raise EstateUnreadable(f"pyyaml is required to read {estate}: {exc}") from exc
        try:
            data = yaml.safe_load(estate.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            raise EstateUnreadable(f"{estate} is present and unreadable: {exc}") from exc
        if not isinstance(data, dict):
            raise EstateUnreadable(f"{estate} is present and is not a mapping")
        indicators = indicators_from_estate_map(data)
    return NodeContext(
        node_root=node_root,
        repo_root=resolve_repo_root(),
        root_source=source,
        indicators=indicators,
        estate_map_present=present,
        halted=halt_reason(node_root),
    )
