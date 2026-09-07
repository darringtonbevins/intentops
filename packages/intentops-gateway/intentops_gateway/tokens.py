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

    MINTED AT BIRTH ONLY IF DISCLOSED (2026-09-06). Genesis declares this
    store and, at G2, OFFERS to fill it: an attended terminal, a question
    asked out loud, the plaintext shown once with a banner, and
    ``disclosed_at`` recorded here. A dry run, an unattended terminal, or an
    operator who declines all leave it ABSENT -- because a token minted
    silently is a credential nobody was shown, which is indistinguishable
    from no credential except that it looks armed. Absent, every request is
    refused -- fail-closed, and loudly, with the remedy in the refusal.

    THE RECORD IS ``.intentops/trust/gateway-auth.json`` (schema
    ``gateway-auth/v1``), a digest and its provenance, never a credential.

    THE MINT PRIMITIVE LIVES IN THE CORE, and so does the path constant.
    Genesis writes this record and genesis may not import the gateway, so
    ``intentops_core.trust.bearer`` owns the mint, the digest, the schema and
    the relpath, and this module re-exports them. One record, one writer's
    definition of it, and no pair of constants that agree until one is
    edited.

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

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

# Imported, never re-implemented: the mint, the digest, the record shape and
# the path all have exactly one definition, and it sits in the core so that
# genesis can perform the ceremony without importing this package.
from intentops_core.trust import bearer

# One exception type a caller can catch across both authenticated surfaces.
from intentops_saddle_mcp.auth import AuthUnavailable

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
#: is gitignored by the identity-repo contract. Declared in the core.
RECORD_RELPATH = bearer.GATEWAY_TOKEN_RELPATH

SCHEMA = bearer.GATEWAY_TOKEN_SCHEMA

#: Re-exported so every existing caller keeps working and there is still only
#: one implementation of "the digest we store".
digest_of = bearer.digest_of

_REQUIRED_FIELDS = ("schema", "algorithm", "token_sha256")


@dataclass(frozen=True)
class GatewayToken:
    """What is on disk: a digest and its provenance, never a credential."""

    token_sha256: str
    minted_at: str
    node_root: str
    rotation: int = 1
    algorithm: str = "sha256"
    schema: str = SCHEMA
    #: When a human was SHOWN this credential, or None. None is not "never" --
    #: it is "no disclosure was recorded", which is what a token rotated out
    #: of band looks like, and the two must not be collapsed into one word.
    disclosed_at: Optional[str] = None

    def public(self) -> Dict[str, Any]:
        """What ``status`` may show. Never the digest -- it is not a secret,
        but publishing it hands an offline guesser its oracle for free."""
        return {"minted_at": self.minted_at, "rotation": self.rotation,
                "algorithm": self.algorithm, "schema": self.schema,
                "disclosed_at": self.disclosed_at}


def record_path(node_root: Path | str) -> Path:
    return Path(node_root) / RECORD_RELPATH


def mint(node_root: Path | str, *, token: Optional[str] = None,
         disclosed_at: Optional[str] = None) -> str:
    """Mint (or rotate) this node's gateway token. Returns the PLAINTEXT, once.

    ``token`` is an injection point for tests only; production callers let it
    generate. The caller shows the returned value to the operator and then
    forgets it -- writing it to a file re-introduces exactly the exposure this
    design removes.

    ``disclosed_at`` records that a human was shown the value at that moment.
    ``rotate`` sets it, because it prints the token to the operator; anything
    minting without showing must leave it None rather than write a stamp
    nobody earned.

    Rotation is a re-mint: the previous digest is REPLACED, so every holder of
    the old token is locked out at the same instant. That is the intended
    blast radius of a rotation and it is why the count is recorded.

    The write itself is ``intentops_core.trust.bearer.mint`` -- one locked
    whole-file replace, one record shape, shared with the genesis ceremony.
    """
    node_root = Path(node_root)
    try:
        previous = load_record(node_root).rotation
    except AuthUnavailable:
        previous = 0
    value, _record = bearer.mint(
        record_path(node_root),
        schema=SCHEMA,
        node_root=str(node_root),
        rotation=previous + 1,
        token=token,
        disclosed_at=disclosed_at,
    )
    return value


def load_record(node_root: Path | str) -> GatewayToken:
    """Read the token record. Raises rather than returning None.

    Every failure mode is NAMED in the message -- absent, unreadable,
    malformed, wrong schema, wrong algorithm -- so an operator repairs the
    right thing instead of re-minting over a permissions problem.

    The validation itself -- absent, unreadable, malformed, wrong schema,
    wrong algorithm, and a missing load-bearing field that HALTs rather than
    defaulting -- is ``bearer.read_record``, shared with genesis. Only the
    absent case is re-worded here, because only this package knows the command
    that fixes it.
    """
    path = record_path(node_root)
    if not path.is_file():
        raise AuthUnavailable(
            f"no gateway token record at {path}: this node has never minted "
            "one. Run `intentops-gateway token rotate --yes` and give the "
            "printed value to the client. Until then every request is refused."
        )
    try:
        data = bearer.read_record(path, schema=SCHEMA)
    except bearer.TokenUnavailable as exc:
        raise AuthUnavailable(f"gateway token record: {exc}") from exc
    rotation = data.get("rotation")
    disclosed = data.get("disclosed_at")
    return GatewayToken(
        token_sha256=data["token_sha256"],
        minted_at=str(data.get("minted_at") or ""),
        node_root=str(data.get("node_root") or ""),
        rotation=int(rotation) if isinstance(rotation, int) and rotation > 0 else 1,
        disclosed_at=disclosed if isinstance(disclosed, str) and disclosed.strip()
        else None,
    )


def verify(node_root: Path | str, presented: Optional[str]) -> bool:
    """True iff ``presented`` matches this node's minted gateway token.

    An absent, blank, or non-string presentation is False -- never an implicit
    anonymous pass. An unreadable record RAISES, so the caller refuses loudly
    rather than treating "cannot check" as "checked".
    """
    record = load_record(node_root)
    return bearer.matches(presented, record.token_sha256)


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
