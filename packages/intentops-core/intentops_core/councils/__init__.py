"""The councils -- a values reading and a posture tally over one proposal.

Conscience outranks appetite: the values council can stop the posture council;
the posture council can never overrule a values REFRAIN. Neither authorizes a
gated action.
"""

from __future__ import annotations

from .charter import POWERS, RESTRICTIONS, VIRTUES, Virtue, render_charter
from .guardians import (
    GUARDIANS,
    CouncilReading,
    CouncilVerdict,
    Guardian,
    GuardianVote,
    MoralCompassCouncil,
    Stance,
)
from .posture import APPROVED, REJECTED, ActuationItem, CouncilVote, posture_council_vote
from .proposal import ProposedAction

__all__ = [
    "APPROVED",
    "ActuationItem",
    "CouncilReading",
    "CouncilVerdict",
    "CouncilVote",
    "GUARDIANS",
    "Guardian",
    "GuardianVote",
    "MoralCompassCouncil",
    "POWERS",
    "ProposedAction",
    "REJECTED",
    "RESTRICTIONS",
    "Stance",
    "VIRTUES",
    "Virtue",
    "posture_council_vote",
    "render_charter",
]
