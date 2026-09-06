"""The gateway's bearer credential -- minted at genesis, stored as a digest.

PURPOSE
    Give the gateway an authentication that cannot be switched off. Every
    request presents this node's bearer token or it is refused with 401. There
    is no anonymous identity, no development bypass, and no environment
    variable that opens the door -- the upstream gateway had all three, in the
    form of an absent header resolving to the literal string "anonymous", and
    that is the exposure this module exists to remove.

    THE PLAINTEXT IS NEVER WRITTEN ANYWHERE. ``mint`` returns it once, to the
    operator, and persists only its SHA-256 digest. A stolen node directory
    therefore yields no usable credential, and the repository's own
    credential-material checker has nothing here to find -- which is stronger
    than storing it and remembering to fence the path.

    WHY THIS IS NOT THE MCP SADDLE'S auth.py, AND WHAT IT REUSES FROM IT.
    Two surfaces that rotate independently must not share one credential: an
    operator who revokes an MCP client should not thereby lock out every
    harness on the HTTP gateway, and vice versa. So the RECORD is this
    package's (its own path, its own schema) while the two things that must
    never diverge -- the digest function and the failure type a caller catches
    -- are IMPORTED from ``intentops_saddle_mcp.auth`` rather than copied.

    ABSENT AT BIRTH, ON PURPOSE. Genesis declares this store; it does not fill
    it. A token minted silently at birth is a credential nobody was shown,
    which is indistinguishable from no credential at all except that it looks
    armed. Until ``intentops gateway token rotate`` is run, every request is
    refused -- fail-closed, and loudly, with the remedy in the refusal.

WRITE MODEL
    Locked fresh-read read-modify-write (store-write-discipline model 2):
    ``StoreLock`` is held across the write and the payload lands via
    ``atomic_replace``. One small whole-file record, one writer (the mint
    ceremony), low contention -- per-item files would be ceremony and a journal
    would be over-built.

BLIND SPOTS
    * A bearer token is a SHARED SECRET over whatever transport carries it.
      The gateway binds loopback by default for that reason; over any network
      transport the channel MUST be encrypted by a layer beneath this one, and
      nothing here can verify that it is.
    * Digest-only storage means a lost token can only be rotated, never
      recovered. That is the intended trade.
    * ``verify`` is constant-time over the digest. The surrounding request path
      is not audited for timing, so this resists a naive comparison oracle and
      not a determined side-channel attacker.
    * Nothing here rate-limits or expires. A token is valid until it is
      rotated; there is no automatic ageing, because an expiry nobody chose is
      a decision nobody made.
"""

from __future__ import annotations

import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from intentops_core.store_guard import StoreLock, atomic_replace, lock_for

# Imported, never re-implemented: one definition of "the digest we store", and
# one exception type a caller can catch across both authenticated surfaces.
from intentops_saddle_mcp.auth import AuthUnavailable, digest_of

__all__ = [
    "AuthUnavailable",
    "GatewayToken",
    "RECORD_RELPATH",
    "SCHEMA",
    "digest_of",
    "load_record",
    "mint",
    "record_path",
    "verify",
]

#: Beside the node certificate genesis writes, under the trust directory that
#: is gitignored by the identity-repo contract.
RECORD_RELPATH = Path(".intentops") / "trust" / "gateway-auth.json"

SCHEMA = "gateway-auth/v1"

#: 32 bytes of OS entropy, URL-safe: long enough that guessing is not a threat
#: model, short enough to paste into a client configuration by hand.
_TOKEN_BYTES = 32

_REQUIRED_FIELDS = ("schema", "algorithm", "token_sha256")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class GatewayToken:
    """What is on disk: a digest and its provenance, never a credential."""

    token_sha256: str
    minted_at: str
    node_root: str
    rotation: int = 1
    algorithm: str = "sha256"
    schema: str = SCHEMA

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "algorithm": self.algorithm,
            "token_sha256": self.token_sha256,
            "minted_at": self.minted_at,
            "rotation": self.rotation,
            "node_root": self.node_root,
            "note": (
                "Digest only. The token itself was shown once at mint time and "
                "is not recoverable from this file; rotate by re-minting."
            ),
        }

    def public(self) -> Dict[str, Any]:
        """What ``status`` may show. Never the digest -- it is not a secret,
        but publishing it hands an offline guesser its oracle for free."""
        return {"minted_at": self.minted_at, "rotation": self.rotation,
                "algorithm": self.algorithm, "schema": self.schema}


def record_path(node_root: Path | str) -> Path:
    return Path(node_root) / RECORD_RELPATH


def mint(node_root: Path | str, *, token: Optional[str] = None) -> str:
    """Mint (or rotate) this node's gateway token. Returns the PLAINTEXT, once.

    ``token`` is an injection point for tests only; production callers let it
    generate. The caller shows the returned value to the operator and then
    forgets it -- writing it to a file re-introduces exactly the exposure this
    design removes.

    Rotation is a re-mint: the previous digest is REPLACED, so every holder of
    the old token is locked out at the same instant. That is the intended
    blast radius of a rotation and it is why the count is recorded.
    """
    node_root = Path(node_root)
    value = token if token is not None else secrets.token_urlsafe(_TOKEN_BYTES)
    if not value.strip():
        raise ValueError("a blank token is not a credential")
    path = record_path(node_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = 0
    try:
        previous = load_record(node_root).rotation
    except AuthUnavailable:
        previous = 0
    record = GatewayToken(
        token_sha256=digest_of(value),
        minted_at=_now(),
        node_root=str(node_root),
        rotation=previous + 1,
    )
    with StoreLock(lock_for(path)):
        atomic_replace(path, json.dumps(record.to_dict(), indent=2) + "\n")
    return value


def load_record(node_root: Path | str) -> GatewayToken:
    """Read the token record. Raises rather than returning None.

    Every failure mode is NAMED in the message -- absent, unreadable,
    malformed, wrong schema, wrong algorithm -- so an operator repairs the
    right thing instead of re-minting over a permissions problem.
    """
    path = record_path(node_root)
    if not path.is_file():
        raise AuthUnavailable(
            f"no gateway token record at {path}: this node has never minted "
            "one. Run `intentops gateway token rotate` and give the printed "
            "value to the client. Until then every request is refused."
        )
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AuthUnavailable(f"gateway token record at {path} is unreadable: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AuthUnavailable(
            f"gateway token record at {path} is malformed JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise AuthUnavailable(f"gateway token record at {path} is not a JSON object")
    # A missing load-bearing field HALTs. Substituting a default here would be
    # a reader that half-understands its own control, which is how a gate goes
    # green because it stopped looking.
    for name in _REQUIRED_FIELDS:
        if not isinstance(data.get(name), str) or not data[name].strip():
            raise AuthUnavailable(
                f"gateway token record at {path} is missing the load-bearing "
                f"field {name!r}; refusing rather than assuming a value"
            )
    if data["schema"] != SCHEMA:
        raise AuthUnavailable(
            f"gateway token record at {path} declares schema {data['schema']!r}, "
            f"and this gateway only understands {SCHEMA!r}"
        )
    if data["algorithm"] != "sha256":
        raise AuthUnavailable(
            f"gateway token record at {path} declares algorithm "
            f"{data['algorithm']!r}, which this gateway cannot verify; "
            "refusing rather than guessing"
        )
    rotation = data.get("rotation")
    return GatewayToken(
        token_sha256=data["token_sha256"],
        minted_at=str(data.get("minted_at") or ""),
        node_root=str(data.get("node_root") or ""),
        rotation=int(rotation) if isinstance(rotation, int) and rotation > 0 else 1,
    )


def verify(node_root: Path | str, presented: Optional[str]) -> bool:
    """True iff ``presented`` matches this node's minted gateway token.

    An absent, blank, or non-string presentation is False -- never an implicit
    anonymous pass. An unreadable record RAISES, so the caller refuses loudly
    rather than treating "cannot check" as "checked".
    """
    record = load_record(node_root)
    if not isinstance(presented, str) or not presented.strip():
        return False
    return hmac.compare_digest(digest_of(presented), record.token_sha256)


def bearer_from_header(value: Optional[str]) -> Optional[str]:
    """Pull the credential out of an ``Authorization`` header value.

    Accepts ``Bearer <token>`` (the documented form) and a bare token, because
    a client that sends the raw value should fail on the CREDENTIAL and not on
    a prefix, which is a far easier fault to diagnose. Returns None for
    anything empty, which the caller must treat as "no token presented".
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.lower().startswith("bearer "):
        text = text[7:].strip()
    return text or None
