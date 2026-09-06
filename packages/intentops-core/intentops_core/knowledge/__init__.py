"""The knowledge layer at birth -- classification, absorption, collections,
embedding, and the coverage instruments that say whether any of it is true.

PURPOSE
    A node is born able to CLASSIFY, ABSORB, DECLARE and MEASURE knowledge,
    and carrying none. Every store here ships empty and valid: an empty
    taxonomy is a true statement about a new node, while a missing taxonomy is
    a field nobody read.

    The layer is five organs and one refusal:

    * :mod:`rings`       -- the sensitivity classification. A missing key
                            resolves to QUARANTINE, never to a ring.
    * :mod:`absorber`    -- the absorber contract, its no-silent-failures
                            result, and the watermark that stops an absorber
                            re-reading or skipping a source.
    * :mod:`collections` -- the collection registry over the one embeddings
                            table, and the write gate (ring + sigma) that
                            makes classification a WRITE-TIME obligation
                            rather than a backfill project.
    * :mod:`embedder`    -- a provider-neutral client: an endpoint declared in
                            the estate, or a null that refuses rather than
                            inventing a vector.
    * :mod:`coverage`    -- ring coverage and corpus coverage, with
                            high-water-mark posture and DEGRADED outranking
                            improvement.

    The refusal: nothing in this package calls a model. Rung 3 of the ladder
    (local embeddings) is as far right as it goes, and even that is a declared
    seam with a null implementation.

WRITE MODEL
    None. This module holds vocabulary and exception types only; it creates no
    store. Every store in the package declares its own model in its own
    docstring and at birth in ``genesis/organs.py``.

BLIND SPOTS
    - The layer measures what is IN a store. It cannot tell a store nobody
      writes from a store not written YET; that is what
      ``CollectionSpec.expected_writer`` exists for, and nothing here checks
      that the named writer is wired.
    - Classification here is by declared ``source_type``, not by reading
      content. A sensitivity detector over raw text is a different organ and
      is deliberately absent from a seed that ships no patterns of anybody's.
"""

from __future__ import annotations

__all__ = [
    "KnowledgeError",
    "KnowledgeHalt",
    "WriteRefused",
    "GATE_MODES",
    "gate_mode",
]

#: The three gate modes, shared by the ring gate and the sigma gate. Closed by
#: declaration: an unrecognised value is REFUSED, never coerced. A coerced
#: mode is written into the coverage ledger as though it had been measured,
#: and a misspelled ``enforce`` then reads exactly like a deliberate
#: ``observe`` -- forever, because the ledger is append-only.
GATE_MODES = ("observe", "enforce", "off")


class KnowledgeError(Exception):
    """Base for every refusal in the knowledge layer."""


class KnowledgeHalt(KnowledgeError):
    """A load-bearing field is missing, or a driver is absent -- HALT.

    Carries a ``remedy`` because a halt with no way forward is an outage
    wearing a policy costume.
    """

    def __init__(self, message: str, remedy: str = "") -> None:
        super().__init__(message)
        self.remedy = remedy

    def __str__(self) -> str:  # pragma: no cover - formatting only
        base = super().__str__()
        return f"{base}\n  remedy: {self.remedy}" if self.remedy else base


class WriteRefused(KnowledgeError):
    """The write gate refused a write in ``enforce`` mode."""

    def __init__(self, message: str, violations=None) -> None:
        super().__init__(message)
        self.violations = list(violations or [])


def gate_mode(env_var: str, environ=None) -> str:
    """Resolve a gate mode. Absent -> ``observe``; present-but-undeclared HALTS.

    UNSET -- or set to the empty string, which is how a shell unsets a
    variable -- means the operator has said nothing, and the safe reading of
    silence is ``observe``. A value that is PRESENT and outside
    :data:`GATE_MODES` is a different thing: somebody tried to say something
    and this layer could not read it.

    Coercing that to ``observe`` was the original behaviour and it is a silent
    failure of the sibling kind. ``INTENTOPS_RING_GATE=enforcce`` resolved to
    ``observe``; the coverage instrument then recorded ``gate_mode: observe``
    in its append-only ledger, and the typo became indistinguishable from a
    deliberate posture. SIG-1 clause 3 -- invalid frames are NAK'd, never
    coerced -- and clause 2: a value produced without evidence must not look
    like a measurement.
    """
    import os

    source = os.environ if environ is None else environ
    if env_var not in source:
        return "observe"
    raw = str(source.get(env_var) or "").strip().lower()
    if not raw:
        return "observe"
    if raw not in GATE_MODES:
        raise KnowledgeHalt(
            f"{env_var}={raw!r} is not a declared gate mode "
            f"({', '.join(GATE_MODES)})",
            f"set {env_var} to one of {', '.join(GATE_MODES)}, or unset it to "
            "leave the gate in its default observe posture. It is not "
            "coerced: a misspelled mode resolving to observe would be written "
            "into the coverage ledger as if it had been chosen.")
    return raw
