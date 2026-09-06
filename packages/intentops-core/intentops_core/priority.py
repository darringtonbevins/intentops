"""The Priority Invariant (IPI-1) -- deterministic UIES -> Priority arithmetic.

PURPOSE
    Rank any set of competing work items (approvals, backlog, sprints) by a
    total, reproducible function. Judgement assigns the four anchored
    sub-scores (Urgency, Importance, Ease, Synergy); Priority itself is
    computed HERE -- same scores in, same Priority out, forever. That split is
    the ladder in action: judgement at the intent boundary, arithmetic in Code.
    No model ever emits the Priority number.

    Model (grounded in WSJF / Cost-of-Delay-over-Duration, CD3):

        CoD (cost-of-delay proxy) = Urgency + Importance + Synergy   # 3..30
        JobSize (duration proxy)  = 11 - Ease                        # 1..10
        Priority P                = CoD / JobSize                    # 0.30..30.00

    Ease is a DIVISOR, not a co-equal addend, so two items of equal value rank
    by effort and genuine quick wins float up. Synergy sits in the NUMERATOR
    because dependency leverage is a value component, not an effort component.

WRITE MODEL
    None -- this module is pure arithmetic and holds no store.

BLIND SPOTS
    * Priority orders ATTENTION; it never decides. A high Priority authorises
      nothing: gated actions stay gated whatever this returns.
    * The four sub-scores are judgement. Garbage in, deterministic garbage out.
      The invariant guarantees reproducibility, not calibration.
    * ``is_critical`` is deliberately narrow. A list where the majority is
      critical has no critical in it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Tuple

__all__ = [
    "INVARIANT_ID",
    "SCALE_MAX",
    "SCALE_MIN",
    "ScoredItem",
    "UIES",
    "cost_of_delay",
    "is_critical",
    "is_epic_candidate",
    "job_size",
    "priority",
    "rank",
    "rank_key",
]

INVARIANT_ID = "IPI-1"
SCALE_MIN = 1
SCALE_MAX = 10
_DIMS: Tuple[str, ...] = ("urgency", "importance", "ease", "synergy")


@dataclass(frozen=True)
class UIES:
    """The four anchored sub-scores. Integers in [1, 10].

    An out-of-range or non-integer score raises rather than clamping: a
    silently clamped score is a value produced without evidence wearing a
    measurement's costume.
    """

    urgency: int
    importance: int
    ease: int
    synergy: int

    def __post_init__(self) -> None:
        for name in _DIMS:
            v = getattr(self, name)
            if not isinstance(v, int) or isinstance(v, bool):
                raise TypeError(f"UIES.{name} must be int, got {type(v).__name__}")
            if not (SCALE_MIN <= v <= SCALE_MAX):
                raise ValueError(
                    f"UIES.{name}={v} out of range [{SCALE_MIN},{SCALE_MAX}]"
                )


def cost_of_delay(s: UIES) -> int:
    """WSJF numerator: Urgency + Importance + Synergy. Range 3..30."""
    return s.urgency + s.importance + s.synergy


def job_size(s: UIES) -> int:
    """Duration proxy: 11 - Ease. Range 1..10 (easier work => smaller size)."""
    return 11 - s.ease


def priority(s: UIES) -> float:
    """Deterministic Priority = CoD / JobSize, rounded to 2dp. 0.30..30.00."""
    return round(cost_of_delay(s) / job_size(s), 2)


def is_critical(s: UIES, *, security_blocker: bool = False) -> bool:
    """Deterministic CRITICAL flag -- "jump the queue, delay is dangerous".

    Critical iff ANY of:
      * Urgency >= 8 AND Importance >= 8, or
      * Importance == 10 AND Urgency >= 7, or
      * security_blocker AND Urgency >= 8 AND Importance >= 7.

    A broad "touches security" flag alone does NOT make an item critical.
    """
    if s.urgency >= 8 and s.importance >= 8:
        return True
    if s.importance == 10 and s.urgency >= 7:
        return True
    if security_blocker and s.urgency >= 8 and s.importance >= 7:
        return True
    return False


def is_epic_candidate(s: UIES, *, epic_shaped: bool) -> bool:
    """Deterministic epic candidacy: epic-shaped work at Importance>=7, Synergy>=7.

    Candidacy is about whether the underlying WORK is a program/multi-slice
    build, not about whether the approval is a decision. A one-shot go/no-go
    scores ``epic_shaped=False`` and is excluded even at high priority.
    """
    return epic_shaped and s.importance >= 7 and s.synergy >= 7


@dataclass
class ScoredItem:
    """An item carrying its UIES scores and the derived deterministic fields."""

    id: str
    uies: UIES
    security_blocker: bool = False
    is_decision: bool = False
    epic_shaped: bool = False
    created_at: str = ""
    justification: str = ""

    @property
    def priority(self) -> float:
        return priority(self.uies)

    @property
    def critical(self) -> bool:
        return is_critical(self.uies, security_blocker=self.security_blocker)

    @property
    def epic_candidate(self) -> bool:
        return is_epic_candidate(self.uies, epic_shaped=self.epic_shaped)

    def as_scores_dict(self) -> Dict[str, Any]:
        """The ``priority`` block persisted onto an item."""
        return {
            "urgency": self.uies.urgency,
            "importance": self.uies.importance,
            "ease": self.uies.ease,
            "synergy": self.uies.synergy,
            "cost_of_delay": cost_of_delay(self.uies),
            "job_size": job_size(self.uies),
            "priority": self.priority,
            "critical": self.critical,
            "epic_candidate": self.epic_candidate,
            "security_blocker": self.security_blocker,
            "is_decision": self.is_decision,
            "justification": self.justification,
            "invariant": INVARIANT_ID,
        }


def rank_key(item: ScoredItem) -> tuple:
    """Total, deterministic ordering key (ascending sort => Priority order).

    Descending Priority, then critical-first, then Urgency, then Importance,
    then OLDER created_at first (FIFO fairness), then id (a final tie-break so
    the order is a total function with zero ambiguity).
    """
    return (
        -item.priority,
        0 if item.critical else 1,
        -item.uies.urgency,
        -item.uies.importance,
        item.created_at or "9999",  # missing timestamp sorts last among ties
        item.id,
    )


def rank(items: Iterable[ScoredItem]) -> List[ScoredItem]:
    """Return items ordered by the invariant (highest Priority first)."""
    return sorted(items, key=rank_key)
