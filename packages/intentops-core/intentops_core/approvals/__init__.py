"""Approvals -- the queue a human rules on, and the body that reads it first.

PURPOSE. Two modules, deliberately separate:

* ``queue``   the per-item approval store: submit, approve, reject, comment,
              expire. It owns the record.
* ``council`` five approval-shaped lenses that read an item and vote. Advisory:
              it votes, it never approves, rejects, or executes anything.

WRITE MODEL. Declared per module. ``queue`` uses per-item files (collision-safe
by construction) plus an append-only history journal; ``council`` owns no store
and is a pure function over an item mapping.

BLIND SPOTS. The council reads the SUBMISSION. Where an item carries a tool call
the reserved lens classifies the call, which is evidence; where it does not, the
lens falls back to prose, and a prose negative is a weak negative rather than a
clean bill of health. Rewriting a request in better words must never change what
the action reaches.
"""

from __future__ import annotations

__all__ = ["council", "queue"]
