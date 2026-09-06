"""Continuity -- what survives a fresh context window, a restart, or a move.

PURPOSE. This package holds the instruments that ask whether the node's account
of itself still reaches whoever is about to act on it. Its first member is
``self_probe``, the self-model fidelity suite: can a fresh context window, with
no tool call, still find each organ of the node?

WRITE MODEL. None of its own. ``self_probe`` writes one create-or-replace report
file per run and owns no shared store.

BLIND SPOTS. Continuity here means *findability*, not *correctness*. A probe
that passes proves the boot corpus carries the fact; it says nothing about
whether the fact is true. That is the truth half, and ``self_probe`` keeps the
two apart deliberately (ABSENT versus FAIL).
"""

from __future__ import annotations

__all__ = ["self_probe"]
