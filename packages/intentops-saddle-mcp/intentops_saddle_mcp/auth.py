"""Harness authentication -- ON by default, and it cannot be turned off here.

PURPOSE
    The private estate's own gateway resolved an absent caller identity to
    "anonymous" BY CONSTRUCTION and shipped its two safety controls disabled.
    A network-reachable governance surface with no caller identity is not a
    gate; it is a public API that returns opinions about other people's
    actions. This module is the correction, and it is the reason this saddle
    can be exposed at all: every request carries a per-node bearer token or it
    is refused.

    THE PLAINTEXT TOKEN IS NEVER WRITTEN ANYWHERE. ``mint`` returns it once,
    to the operator, and persists only its SHA-256 digest. A stolen node
    directory therefore yields no usable credential, and a checker that
    forbids credential material in a tree has nothing here to find -- which is
    stronger than storing it and remembering to fence the path.

    There is no "auth disabled" mode, no environment variable that opens it,
    and no anonymous fall-through. An unreadable or absent auth record is a
    HARD REFUSAL of every request, because the alternative -- serve while the
    control is unreadable -- is the fail-open this module exists to remove.

WRITE MODEL
    Locked fresh-read read-modify-write (store-write-discipline model 2):
    ``StoreLock`` is held across load / mutate / save and the write lands via
    ``atomic_replace``. Whole-file, single small record, low contention -- so
    per-item files would be ceremony and a journal would be over-built. The
    record is minted once per node; a re-mint is a deliberate rotation.

BLIND SPOTS
    * A bearer token is a SHARED SECRET over whatever transport the host gives
      us. On stdio that transport is a pipe between two processes on one
      machine and the token adds little; over any network transport the
      channel MUST be encrypted by the layer beneath us, and nothing here can
      check that it is. Stated because a token over cleartext is theatre.
    * Digest-only storage means a lost token cannot be recovered, only
      rotated. That is the intended trade.
    * ``verify`` is constant-time over the digest, but the surrounding request
      path is not audited for timing. This resists a naive comparison oracle,
      not a determined side-channel attacker.
    * Nothing here rate-limits. A caller may guess as fast as the transport
      allows; 256 bits of entropy is the whole defence, and a future
      network-facing deployment owes a limiter of its own.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from intentops_core.store_guard import StoreLock, atomic_replace, lock_for

__all__ = [
    "AuthRecord",
    "AuthUnavailable",
    "RECORD_RELPATH",
    "digest_of",
    "load_record",
    "mint",
    "verify",
]

#: Under the node's trust directory, beside the node certificate genesis writes.
RECORD_RELPATH = Path(".intentops") / "trust" / "mcp-saddle-auth.json"

#: 32 bytes of OS entropy, URL-safe. Long enough that guessing is not a threat
#: model, short enough to paste into a client configuration by hand.
_TOKEN_BYTES = 32

_SCHEMA = "mcp-saddle-auth/v1"


class AuthUnavailable(RuntimeError):
    """The auth record is absent, malformed, or unreadable.

    Every one of those is a refusal of the whole surface, never a downgrade to
    anonymous. The message names which of the three it was, so an operator
    repairs the right thing instead of re-minting over a permissions problem.
    """


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def digest_of(token: str) -> str:
    """SHA-256 of the presented token, hex. The only form we ever store."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuthRecord:
    """What is on disk: a digest and its provenance, never a credential."""

    token_sha256: str
    minted_at: str
    node_root: str
    algorithm: str = "sha256"
    schema: str = _SCHEMA

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "algorithm": self.algorithm,
            "token_sha256": self.token_sha256,
            "minted_at": self.minted_at,
            "node_root": self.node_root,
            "note": (
                "Digest only. The token itself was shown once at mint time and "
                "is not recoverable from this file; rotate by re-minting."
            ),
        }


def record_path(node_root: Path | str) -> Path:
    return Path(node_root) / RECORD_RELPATH


def mint(node_root: Path | str, *, token: Optional[str] = None) -> str:
    """Mint (or rotate) this node's bearer token. Returns the PLAINTEXT, once.

    ``token`` is an injection point for tests only; production callers let it
    generate. The caller is responsible for showing the returned value to the
    operator and then forgetting it -- writing it to a file re-introduces
    exactly the exposure this design removes.
    """
    node_root = Path(node_root)
    value = token if token is not None else secrets.token_urlsafe(_TOKEN_BYTES)
    if not value.strip():
        raise ValueError("a blank token is not a credential")
    path = record_path(node_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = AuthRecord(
        token_sha256=digest_of(value),
        minted_at=_now(),
        node_root=str(node_root),
    )
    with StoreLock(lock_for(path)):
        atomic_replace(path, json.dumps(record.to_dict(), indent=2) + "\n")
    return value


def load_record(node_root: Path | str) -> AuthRecord:
    """Read the auth record. Raises AuthUnavailable rather than returning None."""
    path = record_path(node_root)
    if not path.is_file():
        raise AuthUnavailable(
            f"no auth record at {path}: this node has never minted a token. "
            "Run `python -m intentops_saddle_mcp.server --mint-token` and give "
            "the printed value to the client. Until then every request is refused."
        )
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AuthUnavailable(f"auth record at {path} is unreadable: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AuthUnavailable(f"auth record at {path} is malformed JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise AuthUnavailable(f"auth record at {path} is not a JSON object")
    # A missing load-bearing field HALTs. Substituting a default here would be
    # a reader that half-understands its own control, which is how a gate goes
    # green because it stopped looking.
    for field in ("schema", "algorithm", "token_sha256"):
        if not isinstance(data.get(field), str) or not data[field].strip():
            raise AuthUnavailable(
                f"auth record at {path} is missing the load-bearing field "
                f"{field!r}; refusing rather than assuming a value"
            )
    if data["schema"] != _SCHEMA:
        raise AuthUnavailable(
            f"auth record at {path} declares schema {data['schema']!r}, "
            f"and this server only understands {_SCHEMA!r}"
        )
    if data["algorithm"] != "sha256":
        raise AuthUnavailable(
            f"auth record at {path} declares algorithm {data['algorithm']!r}, "
            "which this server cannot verify; refusing rather than guessing"
        )
    return AuthRecord(
        token_sha256=data["token_sha256"],
        minted_at=str(data.get("minted_at") or ""),
        node_root=str(data.get("node_root") or ""),
    )


def verify(node_root: Path | str, presented: Optional[str]) -> bool:
    """True iff ``presented`` matches this node's minted token.

    An absent, blank, or non-string presentation is False -- never an implicit
    anonymous pass. An unreadable record raises, so the caller refuses loudly
    rather than treating "cannot check" as "checked".
    """
    record = load_record(node_root)
    if not isinstance(presented, str) or not presented.strip():
        return False
    return hmac.compare_digest(digest_of(presented), record.token_sha256)
