"""The validation family -- four instruments, four axes of grounding.

PURPOSE. Each member answers a different question about whether something a node
believes is actually held down by anything outside the node:

* ``grounded_signals`` -- is the STATEMENT grounded at the moment it is made?
* ``witness``          -- is the COMPLETION grounded (does "fixed" mean proven)?
* ``still_true``       -- is the BELIEF still grounded across DURATION?
* ``intentops_core.continuity.self_probe`` -- is the SELF-MODEL grounded (can a
  fresh context window still find each organ)?

WRITE MODEL. This package declares none of its own; each store-bearing module
declares its own in its module docstring (``witness``: append-only event journal
under ``StoreLock``; ``still_true``: append-only reading ledger under
``StoreLock``). ``grounded_signals`` owns no store.

ROUTE, NEVER VERDICT. Every instrument here escalates for attention. None of
them rejects, blocks, or downgrades anything on its own. A cheap detector that
verdicts gets switched off at its first false positive -- and then needs a
tagout.

BLIND SPOTS. Each module publishes its own; read them before acting on a
reading. This package adds one of its own: the four axes are not exhaustive.
Nothing here asks whether a belief was ever *correct*, only whether it is
grounded, complete, current, and findable.
"""

from __future__ import annotations

__all__ = ["grounded_signals", "still_true", "witness"]
