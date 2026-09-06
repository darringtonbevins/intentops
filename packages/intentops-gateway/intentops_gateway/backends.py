"""The backend registry, and the per-harness allowlist that is default-deny.

PURPOSE
    Answer two questions and refuse to guess at either:

      1. WHICH BACKENDS EXIST. Not a hand-kept list in this package -- the
         node's own estate. ``estate/CAPABILITIES.yaml`` carries the derived
         capability population, and a backend is a member of it that declares a
         ``gateway_backend`` block. At birth that population is ``[]``, so a
         newborn gateway has NO backends and serves only its built-in
         ``intentops.*`` tools. That is the honest state of a node that has
         been given nothing yet, and it is reached by reading the manifest
         rather than by special-casing a first run.

      2. WHICH BACKENDS A GIVEN HARNESS MAY REACH. ``config/gateway-policy.yaml``,
         default-deny. A harness with no row reaches nothing. A harness whose
         row carries ``backends: []`` reaches nothing, explicitly. There is
         deliberately NO value meaning "all" and no environment flag that
         opens one -- if either existed somebody would reach for it during a
         debugging session and never take it back out, which is exactly how
         the upstream control came to be off for months behind a comment where
         a reason should have been.

    An ABSENT ``backends`` key is a HALT, not an empty list. The first is a
    policy nobody finished; the second is a deliberate closed door; reading the
    first as the second hides an unfinished change behind a control that looks
    armed.

WRITE MODEL
    None -- read-only, both files. ``estate/CAPABILITIES.yaml``'s population is
    written only by an explicit derivation run (declared in that file's own
    header); ``config/gateway-policy.yaml`` is a reviewed, human-authored
    registry with no runtime writer. This module opens files and never writes
    one.

BLIND SPOTS
    * A harness ID is SELF-REPORTED, in an HTTP header or an MCP
      ``clientInfo.name``. It is authorisation on top of authentication, never
      identification: a caller holding a valid bearer token may present any ID
      it likes. Stated plainly because "allowlisted" reads like "verified" and
      is not. The credential is the identity; this list bounds what that one
      identity may reach.
    * Matching is exact on the lowercased ID. A harness that renames itself
      between versions falls off the list and is refused -- the
      fail-toward-refusal direction, and it will look like a bug the first
      time it happens.
    * The registry says a backend is DECLARED. It says nothing about whether
      the process behind it is running; liveness is a health monitor's
      question, and the ``service`` capability kind says so in its own row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

# TIERS is the ladder itself; it lives on the verdict module and is imported
# from there rather than re-declared, so a fifth tier is a change in one file.
from intentops_core.gate.verdict import TIERS

__all__ = [
    "Backend",
    "BackendRegistry",
    "HarnessPolicy",
    "POLICY_RELPATH",
    "PolicyUnavailable",
    "RegistryUnavailable",
    "load_policy",
    "load_registry",
]

POLICY_RELPATH = Path("config") / "gateway-policy.yaml"
_CAPABILITIES_RELPATH = Path("estate") / "CAPABILITIES.yaml"

_POLICY_SCHEMA = "gateway-policy/v1"
_BACKEND_BLOCK = "gateway_backend"


class RegistryUnavailable(RuntimeError):
    """The capability population is absent, malformed, or self-contradictory."""


class PolicyUnavailable(RuntimeError):
    """The harness policy is absent, malformed, or missing a load-bearing key.

    Every one of those refuses every backend. A gateway that cannot read its
    own allowlist has no basis for letting anything through.
    """


# ---------------------------------------------------------------------------
# the registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Backend:
    """One declared backend and the tier of each tool it exposes."""

    id: str
    namespace: str
    transport: str
    tool_tiers: Mapping[str, str] = field(default_factory=dict)
    source: str = ""

    def tier_for(self, tool: str) -> Optional[str]:
        """The DECLARED tier of ``tool``, or None for an undeclared one.

        None means "nobody classified this", never "it is safe". The caller
        (``tiers.classify_call``) turns None into T4/deny; returning a tier
        here would put that ruling in two places.
        """
        return self.tool_tiers.get(str(tool))

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "namespace": self.namespace,
                "transport": self.transport,
                "declared_tools": sorted(self.tool_tiers),
                "source": self.source}


@dataclass(frozen=True)
class BackendRegistry:
    """Every declared backend. ``{}`` at birth, and that is a true statement."""

    backends: Mapping[str, Backend]
    source: str

    @property
    def is_empty(self) -> bool:
        return not self.backends

    def get(self, backend_id: str) -> Optional[Backend]:
        return self.backends.get(str(backend_id).strip().lower())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "count": len(self.backends),
            "backends": [b.to_dict() for b in
                         sorted(self.backends.values(), key=lambda b: b.id)],
            "note": ("An empty registry is the honest state of a node whose "
                     "estate population has not been derived yet; the gateway "
                     "then serves only its built-in intentops.* tools."),
        }


def _read_yaml(path: Path, failure: type) -> Any:
    if not path.is_file():
        raise failure(f"{path} is missing; refusing rather than assuming a default")
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - pyyaml is a dependency
        raise failure(f"pyyaml is required to read {path}: {exc}") from exc
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise failure(f"{path} is present and unreadable: {exc}") from exc


def load_registry(estate_root: Path | str) -> BackendRegistry:
    """Read the declared backends out of the node's capability population.

    ``estate_root`` is the directory that CONTAINS ``estate/``. A missing
    manifest raises; ``population: []`` is a clean, empty load.
    """
    path = Path(estate_root) / _CAPABILITIES_RELPATH
    data = _read_yaml(path, RegistryUnavailable)
    if not isinstance(data, dict):
        raise RegistryUnavailable(f"{path} is not a mapping")
    population = data.get("population")
    if population is None:
        raise RegistryUnavailable(
            f"{path} declares no `population` key. An absent key is not an "
            "empty population -- write `population: []` to mean 'this node has "
            "no capabilities yet', explicitly."
        )
    if not isinstance(population, list):
        raise RegistryUnavailable(
            f"{path} has a non-list `population` ({type(population).__name__}); "
            "refusing rather than coercing")

    found: Dict[str, Backend] = {}
    for entry in population:
        if not isinstance(entry, Mapping) or _BACKEND_BLOCK not in entry:
            continue  # a capability that is not a gateway backend
        found_backend = _backend_from(entry, path)
        if found_backend.id in found:
            raise RegistryUnavailable(
                f"{path} declares backend {found_backend.id!r} twice; a name "
                "collision is reported, never silently resolved")
        found[found_backend.id] = found_backend
    return BackendRegistry(backends=found, source=str(path))


def _backend_from(entry: Mapping[str, Any], path: Path) -> Backend:
    ident = str(entry.get("id") or "").strip().lower()
    if not ident:
        raise RegistryUnavailable(
            f"{path} carries a gateway backend with no `id`; a backend nobody "
            "can name is a backend nobody can grant")
    block = entry.get(_BACKEND_BLOCK)
    if not isinstance(block, Mapping):
        raise RegistryUnavailable(
            f"{path}: `{_BACKEND_BLOCK}` on {ident!r} is "
            f"{type(block).__name__}, not a mapping")
    namespace = str(block.get("namespace") or "").strip()
    if not namespace:
        raise RegistryUnavailable(
            f"{path}: backend {ident!r} declares no `namespace`; tools are "
            "addressed as <namespace>__<tool> and an unnamespaced backend "
            "cannot be addressed at all")
    transport = str(block.get("transport") or "").strip()
    if not transport:
        raise RegistryUnavailable(
            f"{path}: backend {ident!r} declares no `transport`; a missing "
            "load-bearing field HALTs rather than defaulting")

    tools = block.get("tools")
    if tools is None:
        raise RegistryUnavailable(
            f"{path}: backend {ident!r} declares no `tools` key. An absent key "
            "is not an empty list -- write `tools: []` to mean 'this backend "
            "exposes nothing yet', explicitly.")
    if not isinstance(tools, list):
        raise RegistryUnavailable(
            f"{path}: backend {ident!r} has a non-list `tools`; refusing "
            "rather than coercing")
    tiers: Dict[str, str] = {}
    for tool in tools:
        if not isinstance(tool, Mapping):
            raise RegistryUnavailable(
                f"{path}: backend {ident!r} has a tool entry that is not a "
                "mapping")
        name = str(tool.get("name") or "").strip()
        if not name:
            raise RegistryUnavailable(
                f"{path}: backend {ident!r} has a tool with no `name`")
        tier = str(tool.get("tier") or "").strip().upper()
        if tier not in TIERS:
            raise RegistryUnavailable(
                f"{path}: backend {ident!r} tool {name!r} declares tier "
                f"{tier or '(none)'!r}, which is not one of {list(TIERS)}. "
                "An undeclared tier is a HALT here rather than a default, "
                "because the gateway's own default for an unclassified tool "
                "is T4/deny and a typo must not quietly become one.")
        if name in tiers:
            raise RegistryUnavailable(
                f"{path}: backend {ident!r} declares tool {name!r} twice")
        tiers[name] = tier
    return Backend(id=ident, namespace=namespace, transport=transport,
                   tool_tiers=tiers, source=str(path))


# ---------------------------------------------------------------------------
# the allowlist
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HarnessPolicy:
    """The per-harness backend allowlist. Default-deny, with no 'all' value."""

    grants: Mapping[str, Tuple[str, ...]]
    always_denied: Tuple[str, ...]
    source: str

    def permits(self, harness_id: Optional[str],
                backend_id: str) -> Tuple[bool, str]:
        """Returns ``(permitted, reason)``. The reason is never empty."""
        backend = str(backend_id).strip().lower()
        if not isinstance(harness_id, str) or not harness_id.strip():
            return False, (
                "the caller declared no harness id, so no grant can be matched; "
                "backends are default-deny and an undeclared caller reaches "
                "none of them"
            )
        harness = harness_id.strip().lower()
        if backend in self.always_denied:
            return False, (
                f"backend {backend!r} is on the always-denied list in "
                f"{self.source}; no harness grant overrides it"
            )
        granted = self.grants.get(harness)
        if granted is None:
            return False, (
                f"harness {harness_id!r} has no row in {self.source}; backends "
                "are default-deny, so an ungranted harness reaches none of them"
            )
        if not granted:
            return False, (
                f"harness {harness_id!r} has an empty backend list in "
                f"{self.source}, which is a deliberate closed door"
            )
        if backend in granted:
            return True, (f"harness {harness_id!r} is granted backend "
                          f"{backend!r} in {self.source}")
        return False, (
            f"harness {harness_id!r} has no grant for backend {backend!r} in "
            f"{self.source} ({len(granted)} backend(s) granted)"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "granted_harnesses": sorted(self.grants),
            "grant_counts": {h: len(b) for h, b in sorted(self.grants.items())},
            "always_denied": list(self.always_denied),
            "default": "deny",
        }


def load_policy(repo_root: Optional[Path | str]) -> HarnessPolicy:
    """Read the harness policy. Raises rather than returning a permissive default."""
    if repo_root is None:
        raise PolicyUnavailable(
            "the framework checkout could not be located, so the backend "
            "allowlist cannot be read; set INTENTOPS_REPO_ROOT. Every backend "
            "call is refused until it can be read."
        )
    path = Path(repo_root) / POLICY_RELPATH
    data = _read_yaml(path, PolicyUnavailable)
    if not isinstance(data, dict):
        raise PolicyUnavailable(f"{path} is not a mapping")
    schema = str(data.get("schema") or "")
    if schema != _POLICY_SCHEMA:
        raise PolicyUnavailable(
            f"{path} declares schema {schema or '(none)'!r}, and this gateway "
            f"only understands {_POLICY_SCHEMA!r}")
    if "harnesses" not in data:
        raise PolicyUnavailable(
            f"{path} declares no `harnesses` key. An absent key is not an "
            "empty policy -- write `harnesses: []` to mean 'no harness may "
            "reach any backend', explicitly.")
    rows = data.get("harnesses")
    if rows is None:
        raise PolicyUnavailable(
            f"{path} has a null `harnesses`; write an explicit empty list "
            "rather than leaving it unset")
    if not isinstance(rows, list):
        raise PolicyUnavailable(
            f"{path} has a non-list `harnesses` ({type(rows).__name__})")

    grants: Dict[str, Tuple[str, ...]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise PolicyUnavailable(f"{path}: a harness row is not a mapping")
        ident = str(row.get("id") or "").strip().lower()
        if not ident:
            raise PolicyUnavailable(f"{path}: a harness row declares no `id`")
        if ident in grants:
            raise PolicyUnavailable(
                f"{path}: harness {ident!r} appears twice; a collision is "
                "reported, never silently resolved")
        if "backends" not in row:
            raise PolicyUnavailable(
                f"{path}: harness {ident!r} declares no `backends` key. An "
                "absent key is not an empty list -- write `backends: []` to "
                "mean 'this harness reaches no backend', explicitly.")
        raw = row.get("backends")
        if raw is None:
            raise PolicyUnavailable(
                f"{path}: harness {ident!r} has a null `backends`; write an "
                "explicit empty list")
        if not isinstance(raw, list):
            raise PolicyUnavailable(
                f"{path}: harness {ident!r} has a non-list `backends`")
        names = []
        for item in raw:
            if not isinstance(item, str) or not item.strip():
                raise PolicyUnavailable(
                    f"{path}: harness {ident!r} grants a non-string or blank "
                    "backend; a name nobody can match is not a grant")
            names.append(item.strip().lower())
        if not str(row.get("reason") or "").strip():
            raise PolicyUnavailable(
                f"{path}: harness {ident!r} states no `reason`. A grant nobody "
                "can argue with is the failure this file exists to retire.")
        grants[ident] = tuple(sorted(set(names)))

    denied_raw = data.get("always_denied")
    if denied_raw is None:
        denied_raw = []
    if not isinstance(denied_raw, list):
        raise PolicyUnavailable(f"{path} has a non-list `always_denied`")
    denied = tuple(sorted({str(x).strip().lower() for x in denied_raw
                           if str(x).strip()}))
    return HarnessPolicy(grants=grants, always_denied=denied, source=str(path))
