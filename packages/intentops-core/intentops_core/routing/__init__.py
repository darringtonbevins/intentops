"""Routing -- a policy that decides, never a client that calls.

PURPOSE
    The seam between "which shape of work is this, and whose material is it"
    and "which pool serves it". Everything in this subpackage is pure: it
    reads a reviewed config file and returns a value. No socket is opened, no
    provider SDK is imported, and no model id is named anywhere in it.

WRITE MODEL
    None. Nothing here holds a store.

BLIND SPOTS
    See ``policy`` -- the fence trusts a pool's declared provider class, and
    the estate ring is an input this package does not verify.
"""

from __future__ import annotations

from .policy import (
    Assignment,
    DEFAULT_POLICY_PATH,
    FENCE_PERMITTED,
    FENCE_REFUSED,
    FENCE_UNMET,
    Policy,
    PolicyError,
    Pool,
    SCHEMA,
    load_policy,
    parse_policy,
    render,
    resolve,
    selftest,
)

__all__ = [
    "Assignment",
    "DEFAULT_POLICY_PATH",
    "FENCE_PERMITTED",
    "FENCE_REFUSED",
    "FENCE_UNMET",
    "Policy",
    "PolicyError",
    "Pool",
    "SCHEMA",
    "load_policy",
    "parse_policy",
    "render",
    "resolve",
    "selftest",
]
