"""Lockout/tagout -- nothing is switched off without a tag.

PURPOSE. An open switch carries no information about WHY it is open.
Off-by-design and off-by-neglect look identical from the outside, so the next
person either re-energises something that was protecting them or leaves off
something that was protecting them. Both failures are silent. This package is
the chain of custody that makes "are we fully remediated?" a mechanical question
rather than a memory.

WRITE MODEL. Declared in ``ledger``: locked fresh-read read-modify-write with an
atomic replace. The ledger has ONE sanctioned writer and is never hand-edited.

BLIND SPOTS. Declared in ``ledger``. The shortest of them: the ledger can only
see switches somebody tagged.
"""

from __future__ import annotations

__all__ = ["ledger"]
