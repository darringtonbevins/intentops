"""The genesis package -- the deterministic bootstrap of a fresh node.

PURPOSE
    Turn a clone on disk into a node: prove what it carries before it mints
    what it is (G1 precedes G2, always), create its organs empty-and-valid,
    ask its operator for consent before it asks for anything else, and come
    online able to ANSWER -- with instruments, not assertions -- whether it is
    alive and under its own rules.

    This module holds only the vocabulary the whole package shares: the state
    names, the closed outcome vocabulary, the exception types, and the paths
    every phase writes to. The phases themselves live in :mod:`machine`; the
    verification in :mod:`provenance`; the store creation in :mod:`organs`;
    the come-online reading in :mod:`aliveness`; the off switch in
    :mod:`standdown`.

WRITE MODEL
    None. This module creates no store and writes no file. Every store the
    package creates declares its own write model at birth, recorded in
    ``.intentops/genesis/organs.json`` by :mod:`organs`.

BLIND SPOTS
    - The state list is a vocabulary, not a schedule: nothing here checks that
      a caller runs the phases in order. :class:`~.machine.Journal` is what
      enforces ordering, and only for runs that go through it.
    - ``OUTCOMES`` is closed by declaration, not by a type system. A caller
      that invents a fifth outcome string gets no error from this module; the
      journal validator in :mod:`machine` is what refuses it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

__all__ = [
    "STATES",
    "STATE_PURPOSE",
    "TERMINAL_STATES",
    "OUTCOMES",
    "BLOCKING_OUTCOMES",
    "GENESIS_DIR",
    "JOURNAL_RELPATH",
    "HALT_MARKER_RELPATH",
    "UNSIGNED_DEV_ENV",
    "UNVERIFIED_BOOT_PHRASE",
    "UNVERIFIED_ENV",
    "IDENTITY_REPO_ENV",
    "GenesisError",
    "Halt",
    "Refuse",
    "OperatorGateRequired",
]

#: The eight normal states, in order. Deterministic: the same substrate plus
#: the same operator answers produce the same journal.
STATES: Tuple[str, ...] = ("G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8")

STATE_PURPOSE = {
    "G0": "SUBSTRATE -- measure the host this node is actually on",
    "G1": "PROVENANCE -- prove what is carried, before minting what it is",
    "G2": "KEYS -- mint the node keypair and bind the operator root",
    "G3": "ORGANS -- create every store empty and valid, then probe",
    "G4": "CONSENT -- ask the operator before asking for anything else",
    "G5": "FOUNDING -- the four founding questions, asked once",
    "G6": "ALIGNMENT -- the staged interview, by evidence available",
    "G7": "ONLINE -- steady state under the birth ceiling",
    "G8": "CALIBRATED -- the ceiling lifts only by measurement",
}

#: Reachable from any state. A stand-down leaves a node that is OFF, never one
#: that is broken; HALT is the fail-closed stop.
TERMINAL_STATES: Tuple[str, ...] = ("STAND-DOWN", "HALT")

#: The closed outcome vocabulary. There is no fifth outcome. ``PASS`` is the
#: absence of a finding; the other three are findings of decreasing severity
#: read upward: WARN continues and records, REFUSE denies one artifact and the
#: node names the faculty it is therefore without, HALT stops the run.
OUTCOMES: Tuple[str, ...] = ("PASS", "WARN", "REFUSE", "HALT")

#: Outcomes that stop a phase from being recorded as completed.
BLOCKING_OUTCOMES: Tuple[str, ...] = ("REFUSE", "HALT")

GENESIS_DIR = Path(".intentops") / "genesis"
JOURNAL_RELPATH = GENESIS_DIR / "journal.jsonl"
HALT_MARKER_RELPATH = Path(".intentops") / "halt.marker"

#: Set to ``1`` to proceed past an unsigned or proposed imprint -- that is, past
#: the case where there is *nothing to verify against yet*. Deliberately
#: expensive: an attended terminal, a typed phrase, a loud banner on every
#: command, and a T3 tagout carrier.
#:
#: It has never meant "the evidence disagrees, proceed anyway". A CONTRADICTION
#: -- key material that does not match its own fingerprint, an imprint byte that
#: moved, a trust-roots file that will not parse -- is not opened by this flag,
#: because the two cases are different findings that happen to arrive at the
#: same gate.
UNSIGNED_DEV_ENV = "INTENTOPS_GENESIS_UNSIGNED_DEV"

#: The exact phrase the operator types at the G1 override prompt. Lowercased on
#: comparison; nothing else is accepted, and no environment variable substitutes
#: for typing it (design sect. 2.2 G1 / 11.5, trust-root-design sect. 4.3).
UNVERIFIED_BOOT_PHRASE = "boot unverified"

#: The trust-roots file's own declared override name, carried here so a reader
#: meets both spellings in one place. It is NOT a second door: this package
#: reads :data:`UNSIGNED_DEV_ENV` only, and says so.
UNVERIFIED_ENV = "INTENTOPS_GENESIS_UNVERIFIED"

#: Highest-precedence identity-repo binding. One process; never persisted.
IDENTITY_REPO_ENV = "INTENTOPS_IDENTITY_REPO"


class GenesisError(RuntimeError):
    """Base for every refusal this package raises."""

    def __init__(self, message: str, *, remedy: Optional[str] = None) -> None:
        super().__init__(message)
        self.remedy = remedy

    def render(self) -> str:
        if self.remedy:
            return f"{self.args[0]}\n  remedy: {self.remedy}"
        return str(self.args[0])


class Halt(GenesisError):
    """Fail-closed stop. The run does not continue and nothing is assumed.

    A Halt always carries a remedy: a stop with no stated way forward is the
    tagout lesson wearing a boot sequence's clothes.
    """


class Refuse(GenesisError):
    """One artifact is denied. The node continues and names what it lost."""


class OperatorGateRequired(Halt):
    """A gate only the operator can pass was reached without a TTY.

    Raised rather than defaulted: consent obtained from a workflow leg, a
    tick, a subagent or CI is not consent.
    """
