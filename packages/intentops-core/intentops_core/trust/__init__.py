"""Trust material this node mints for itself.

PURPOSE
    One place for the primitives that turn a secret into something a node may
    keep: a bearer token, its digest, and the digest-only record that is the
    only durable form either of them ever takes. It sits in the CORE, below
    every surface that authenticates, so that the genesis ceremony can mint a
    credential for the gateway without the core ever importing the gateway --
    the dependency arrow that makes a second host a second package rather
    than an edit to the gate.

WRITE MODEL
    None at this level. :mod:`intentops_core.trust.bearer` declares its own
    (locked whole-file replace) and is the only member that writes.

BLIND SPOTS
    * This package knows nothing about transport. A bearer token is a shared
      secret over whatever carries it, and nothing here can see the channel.
"""

from __future__ import annotations

from .bearer import (  # noqa: F401
    GATEWAY_TOKEN_RELPATH,
    GATEWAY_TOKEN_SCHEMA,
    TokenUnavailable,
    digest_of,
    matches,
    mint,
    new_token,
    read_record,
    record_dict,
)

__all__ = [
    "GATEWAY_TOKEN_RELPATH",
    "GATEWAY_TOKEN_SCHEMA",
    "TokenUnavailable",
    "digest_of",
    "matches",
    "mint",
    "new_token",
    "read_record",
    "record_dict",
]
