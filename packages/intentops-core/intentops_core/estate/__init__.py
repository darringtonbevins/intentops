"""The blank estate: the six manifests a node carries about its own world.

PURPOSE
    Re-export the manifest contract so callers write
    ``from intentops_core.estate import load_estate`` rather than reaching into
    a module path that may later grow siblings.

WRITE MODEL
    Not a store. This package writes nothing; the manifests are single-writer
    operator-authored files and this code only reads them.

BLIND SPOTS
    - Re-export only. Everything that can be wrong is documented in
      ``manifests.py``; nothing is decided here.
"""

from __future__ import annotations

from .manifests import (
    ACCOUNTINGS,
    CAPABILITY_KIND_IDS,
    MANIFEST_FILENAMES,
    MANIFEST_SPECS,
    Estate,
    LoadedManifest,
    ManifestError,
    ManifestSpec,
    ManifestWarning,
    collect_problems,
    load_estate,
)

__all__ = [
    "ACCOUNTINGS",
    "CAPABILITY_KIND_IDS",
    "MANIFEST_FILENAMES",
    "MANIFEST_SPECS",
    "Estate",
    "LoadedManifest",
    "ManifestError",
    "ManifestSpec",
    "ManifestWarning",
    "collect_problems",
    "load_estate",
]
