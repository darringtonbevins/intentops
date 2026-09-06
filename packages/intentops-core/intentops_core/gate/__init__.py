"""The gate -- pure classification and a Verdict, with no host coupling.

Nothing in this package exits a process, writes a protocol frame, or knows what
a host runtime does with a refusal. Turning a Verdict into a host's own
vocabulary is the saddle adapter's job, and that split is what makes the gate
portable and unit-testable.
"""

from __future__ import annotations

from .classify import (
    RealityIndicators,
    classify_reaches_reality,
    command_skeleton,
    indicators_from_estate_map,
    verdict_for,
)
from .verdict import Decision, Verdict, allow, ask, block, halt, merge

__all__ = [
    "Decision",
    "RealityIndicators",
    "Verdict",
    "allow",
    "ask",
    "block",
    "classify_reaches_reality",
    "command_skeleton",
    "halt",
    "indicators_from_estate_map",
    "merge",
    "verdict_for",
]
