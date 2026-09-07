"""The attended release-root ceremony, as a resumable wizard.

The runbook is ``docs/TRUST-CEREMONY.md``; the mint/sign primitives are
``scripts/genesis/mint_release_root.py``. This package is the third thing:
the walk-through that holds an operator's hand across a ten-step,
irreversible sitting, and that can be resumed when a step fails.

Nothing here weakens a single gate. The wizard is ATTENDED-ONLY by the same
test the primitives use, it never prints or copies private key material, and
it refuses to write the three carriers until the operator types the phrase.
"""

from .wizard import (  # noqa: F401
    CEREMONY_STATE_RELPATH,
    ASSURANCE_LEVELS,
    STEPS,
    CeremonyRefused,
    Console,
    Wizard,
    run,
    selftest,
)

__all__ = [
    "CEREMONY_STATE_RELPATH",
    "ASSURANCE_LEVELS",
    "STEPS",
    "CeremonyRefused",
    "Console",
    "Wizard",
    "run",
    "selftest",
]
