"""The client allowlist -- default-deny, read from the saddle registry.

PURPOSE
    The second of the two upstream safety controls that shipped DISABLED in
    the private estate's gateway (a per-harness backend allowlist, tagged out
    and never re-energised). Here it is on, and it has no off switch.

    The rule is stated once and enforced in one place: a client name that is
    not on ``allowed_clients`` cannot call anything. **An empty list refuses
    everything.** There is deliberately no value meaning "all" -- if one
    existed somebody would eventually reach for it during a debugging session
    and never take it back out, which is precisely how the upstream control
    came to be off for months with a comment where a reason should have been.

    The list lives in ``config/saddles.yaml`` beside the host row it governs,
    because an allowlist that lives somewhere nobody reads is an allowlist
    nobody maintains.

WRITE MODEL
    None -- read-only. ``config/saddles.yaml`` is a reviewed, human-authored
    registry with exactly one writer (a reviewed commit) and no runtime writer.

BLIND SPOTS
    * A client NAME is self-reported in the MCP ``initialize`` frame. It is
      not an identity and must never be read as one: the bearer token in
      ``auth.py`` is the authentication, and this list is authorisation on top
      of it. A caller holding a valid token can present any name it likes.
      Stated plainly because "allowlisted" reads like "verified" and is not.
    * Matching is exact on the lowercased name. A client that renames itself
      between versions falls off the list and is refused -- the fail-toward-
      refusal direction, and it will look like a bug the first time it happens.
    * If the registry file is absent or the row is missing, every call is
      refused. That is a HALT, not a default: a server that cannot read its own
      allowlist has no basis for letting anything through.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "HOST_ID",
    "RegistryUnavailable",
    "SaddleRow",
    "load_row",
]

#: The row in config/saddles.yaml that governs this saddle.
HOST_ID = "mcp-hosted"

_REGISTRY_RELPATH = Path("config") / "saddles.yaml"


class RegistryUnavailable(RuntimeError):
    """The saddle registry is absent, malformed, or missing this host's row."""


@dataclass(frozen=True)
class SaddleRow:
    """The governing row, with the fields this server actually acts on."""

    host_id: str
    grade: str
    allowed_clients: Tuple[str, ...]
    source: str

    @property
    def refuses_everything(self) -> bool:
        """True when the list is empty. An empty allowlist is a closed door."""
        return not self.allowed_clients

    def permits(self, client_name: Optional[str]) -> Tuple[bool, str]:
        """Returns ``(permitted, reason)``. The reason is never empty."""
        if self.refuses_everything:
            return False, (
                f"the {self.host_id} allowlist in {self.source} is empty, and an "
                "empty allowlist refuses every client by design -- add the "
                "client's name there in a reviewed change"
            )
        if not isinstance(client_name, str) or not client_name.strip():
            return False, (
                "the client did not declare a name in its initialize frame; an "
                "unnamed client cannot be matched against the allowlist"
            )
        needle = client_name.strip().lower()
        if needle in self.allowed_clients:
            return True, f"client {client_name!r} is on the {self.host_id} allowlist"
        return False, (
            f"client {client_name!r} is not on the {self.host_id} allowlist in "
            f"{self.source} ({len(self.allowed_clients)} name(s) allowed)"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "host_id": self.host_id,
            "grade": self.grade,
            "allowed_client_count": len(self.allowed_clients),
            "refuses_everything": self.refuses_everything,
            "source": self.source,
        }


def registry_path(repo_root: Optional[Path]) -> Optional[Path]:
    return (repo_root / _REGISTRY_RELPATH) if repo_root else None


def load_row(repo_root: Optional[Path], *, host_id: str = HOST_ID) -> SaddleRow:
    """Read this host's row. Raises rather than returning a permissive default."""
    if repo_root is None:
        raise RegistryUnavailable(
            "the framework checkout could not be located, so the client "
            "allowlist cannot be read; set INTENTOPS_REPO_ROOT. Every call is "
            "refused until it can be read."
        )
    path = registry_path(repo_root)
    assert path is not None  # narrowed by the guard above
    if not path.is_file():
        raise RegistryUnavailable(f"the saddle registry is missing: {path}")
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - pyyaml is a dependency
        raise RegistryUnavailable(f"pyyaml is required to read {path}: {exc}") from exc
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise RegistryUnavailable(f"{path} is present and unreadable: {exc}") from exc
    hosts = data.get("hosts") if isinstance(data, dict) else None
    if not isinstance(hosts, list):
        raise RegistryUnavailable(f"{path} declares no hosts list")
    for row in hosts:
        if isinstance(row, dict) and str(row.get("id") or "") == host_id:
            return _row_from(row, path, host_id)
    raise RegistryUnavailable(
        f"{path} carries no row with id {host_id!r}; this server refuses to run "
        "without the row that governs it"
    )


def _row_from(row: Dict[str, Any], path: Path, host_id: str) -> SaddleRow:
    # A missing load-bearing field HALTs. `allowed_clients` absent is NOT the
    # same as `allowed_clients: []`: the first is a registry nobody finished,
    # the second is a deliberate closed door, and reading the first as the
    # second would hide an unfinished change behind a control that looks armed.
    if "allowed_clients" not in row:
        raise RegistryUnavailable(
            f"the {host_id!r} row in {path} declares no `allowed_clients` key. "
            "An absent key is not an empty list -- state `allowed_clients: []` "
            "to mean 'refuse everything', explicitly."
        )
    raw = row.get("allowed_clients")
    if raw is None:
        raise RegistryUnavailable(
            f"the {host_id!r} row in {path} has a null `allowed_clients`; write "
            "an explicit empty list rather than leaving it unset"
        )
    if not isinstance(raw, list):
        raise RegistryUnavailable(
            f"the {host_id!r} row in {path} has a non-list `allowed_clients` "
            f"({type(raw).__name__}); refusing rather than coercing"
        )
    names: List[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise RegistryUnavailable(
                f"the {host_id!r} allowlist in {path} carries a non-string or "
                "blank entry; a name nobody can match is not an allowance"
            )
        names.append(item.strip().lower())
    grade = str(row.get("grade") or "")
    if not grade:
        raise RegistryUnavailable(
            f"the {host_id!r} row in {path} declares no grade; a saddle with no "
            "stated grade is a claim with no evidence attached"
        )
    return SaddleRow(
        host_id=host_id,
        grade=grade,
        allowed_clients=tuple(sorted(set(names))),
        source=str(path),
    )
