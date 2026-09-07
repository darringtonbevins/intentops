"""The compiled release-root pin -- carrier #2 of three.

PURPOSE
    The release root's fingerprint is carried three ways so that a partial
    tamper is visible: the full record in ``config/trust-roots.yaml``, THIS
    compiled constant, and the human-readable copy in ``docs/GENESIS.md``.
    G1 compares them; a disagreement is a HALT.

    The honest limit, and it is stated in the boot banner in these words:
    three carriers make a PARTIAL tamper visible. They do not make a TOTAL
    substitution visible. First-clone authenticity is trust-on-first-use
    unless the operator compares the fingerprint out of band against the
    project site, the signed git tag and the release announcement.

WRITE MODEL
    Not a store. This file is source, edited by the operator as ONE reviewed
    change alongside ``config/trust-roots.yaml`` and ``docs/GENESIS.md``,
    immediately after the ceremony mints the key (``docs/TRUST-CEREMONY.md``
    step 2). ``scripts/genesis/mint_release_root.py`` deliberately does NOT
    write this file: a tool that can rewrite the pin unattended is the coup
    the three carriers exist to make visible. A pin edited by hand ON ITS OWN,
    without the other two carriers, is that same attack -- which is what G1.3
    and G1.4 catch. (Corrected 2026-09-06: this docstring previously said the
    pin is "edited only by the release ceremony's tooling", and no such
    tooling existed anywhere in the tree.)

BLIND SPOTS
    - A placeholder pin is not a weak pin, it is NO pin. Every function here
      says so rather than letting ``"sha256:PLACEHOLDER"`` compare equal to
      itself and read as agreement -- that would be the exact fail-open this
      package exists to close (an unsigned bundle and a valid one must never
      be indistinguishable).
    - This module verifies nothing about a key. It carries a string and can
      say whether that string is a real fingerprint shape. Verification is
      :mod:`intentops_core.genesis.provenance`.
"""

from __future__ import annotations

import re
from typing import Tuple

__all__ = [
    "ROOT_FINGERPRINT",
    "PIN_STATE",
    "PLACEHOLDER_FINGERPRINT",
    "FINGERPRINT_RE",
    "is_real_fingerprint",
    "pin_is_minted",
    "authority_string",
    "compare",
]

#: The literal placeholder the shipped trust-roots file carries until the
#: ceremony runs. Named, so no code has to spell it inline and no reader has
#: to guess whether a value is real.
PLACEHOLDER_FINGERPRINT = "sha256:PLACEHOLDER"

#: The compiled pin. Until the root ceremony runs -- a witnessed, offline,
#: irreversible act only the project's human operator may perform -- this is
#: deliberately the placeholder, and :func:`pin_is_minted` returns False.
ROOT_FINGERPRINT: str = "sha256:4a9c193315845493c57354235f9655fd2e8f6b3b125da131bede5c4874071a1c"

#: ``placeholder`` | ``minted``. A third value is not permitted: a pin is
#: either the published one or it is not one at all.
PIN_STATE: str = "minted"

#: sha256 over the DER SPKI bytes, never over the PEM text.
FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def is_real_fingerprint(value: object) -> bool:
    """True only for a well-formed ``sha256:<64 lowercase hex>`` string.

    A placeholder, an empty string, ``None``, and an upper-case digest all
    return False. This is deliberately strict: the one thing a fingerprint
    check must never do is accept something that merely looks fingerprint-ish.
    """
    return isinstance(value, str) and bool(FINGERPRINT_RE.match(value))


def pin_is_minted() -> bool:
    """True iff a real release root has been compiled into this build."""
    return PIN_STATE == "minted" and is_real_fingerprint(ROOT_FINGERPRINT)


def authority_string(fingerprint: str, did: str) -> str:
    """The canonical one-line form, for banners and eyeball comparison."""
    return f"intentops-root:v1:{fingerprint}:{did}"


def compare(file_fingerprint: object) -> Tuple[str, str]:
    """Compare a trust-roots file's pinned fingerprint against this pin.

    Returns ``(outcome, reason)`` where outcome is one of the closed genesis
    outcomes. Two placeholders comparing equal is reported as **HALT**, not
    PASS: agreement between two values that are both "no key" is not evidence
    of anything, and reading it as a pass is how an unminted build would come
    to describe itself as verified.
    """
    if not pin_is_minted():
        return (
            "HALT",
            "the compiled release-root pin is a placeholder: no root ceremony "
            "has run for this build, so there is nothing to verify against",
        )
    if not is_real_fingerprint(file_fingerprint):
        return (
            "HALT",
            "config/trust-roots.yaml carries no real pinned_fingerprint "
            f"(found {file_fingerprint!r})",
        )
    if file_fingerprint != ROOT_FINGERPRINT:
        return (
            "HALT",
            "pin mismatch between the compiled constant and "
            "config/trust-roots.yaml -- compare both against the project site, "
            "the signed tag and the release announcement before proceeding",
        )
    return ("PASS", "compiled pin agrees with the trust-roots file")
