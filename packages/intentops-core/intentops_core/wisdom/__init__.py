"""The wisdom subsystem -- recorded rulings under conditions.

An ordering BINDS; a vote DELIBERATES. This package holds the substrate the
councils consult before anybody votes.
"""

from __future__ import annotations

from .ordering import (
    ON_MATCH,
    STATIONS,
    STATUSES,
    Ordering,
    OrderingState,
    OrderingStore,
    ValidationError,
    posture,
)

__all__ = [
    "ON_MATCH",
    "STATIONS",
    "STATUSES",
    "Ordering",
    "OrderingState",
    "OrderingStore",
    "ValidationError",
    "posture",
]
