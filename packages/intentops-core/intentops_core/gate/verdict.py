"""The Verdict -- what the gate returns, and the only thing it returns.

PURPOSE
    Split the gate into a pure half and an impure half. This module is the
    seam: a ``Verdict`` is a value, and computing one has no side effects, no
    process exit, no stdout protocol, and no knowledge of any host runtime. The
    per-host adapter (the "saddle") is what turns a Verdict into whatever its
    host understands -- an exit code, a JSON frame, an exception.

    That split is what makes the gate portable. A gate that calls
    ``sys.exit(2)`` in its decision logic can only ever live in the one host
    that reads exit code 2, and cannot be unit-tested without a subprocess.

    Four decisions, strictly ordered by severity:

      ALLOW   the operation proceeds; nothing is owed
      ASK     a human word is required for THIS act before it proceeds
      BLOCK   the gate refuses this operation
      HALT    the node is stood down; nothing proceeds at all

    HALT is not a worse BLOCK. BLOCK is about the operation; HALT is about the
    node, and it outranks everything including an ALLOW, because a stood-down
    node is OFF, not broken.

WRITE MODEL
    None -- a frozen value object. The caller records it wherever it is
    accountable.

BLIND SPOTS
    * A Verdict carries reasons, not proof. Nothing here verifies that the
      reasons are true; they are the classifier's account of itself.
    * ``merge`` takes the STRICTEST decision. It cannot detect that two
      classifiers disagreed for incompatible reasons -- it only keeps both
      reasons so a human can see that they did.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Tuple

__all__ = ["Decision", "TIERS", "Verdict", "allow", "ask", "block", "halt", "merge"]

TIERS: Tuple[str, ...] = ("T0", "T1", "T2", "T3", "T4")
_TIER_ORDER: Dict[str, int] = {t: i for i, t in enumerate(TIERS)}


class Decision(str, Enum):
    """What the gate decided. Ordered by severity; HALT outranks all."""

    ALLOW = "ALLOW"
    ASK = "ASK"
    BLOCK = "BLOCK"
    HALT = "HALT"

    @property
    def severity(self) -> int:
        return _SEVERITY[self]

    @property
    def is_refusal(self) -> bool:
        """True when the operation does not proceed on this verdict alone."""
        return self in (Decision.ASK, Decision.BLOCK, Decision.HALT)


_SEVERITY: Dict[Decision, int] = {
    Decision.ALLOW: 0,
    Decision.ASK: 1,
    Decision.BLOCK: 2,
    Decision.HALT: 3,
}


@dataclass(frozen=True)
class Verdict:
    """The gate's answer about one operation.

    ``reasons`` is never empty for a refusal: a refusal with no stated reason
    is indistinguishable from a bug, and the operator has nothing to act on.
    """

    decision: Decision
    tier: str = "T0"
    reaches_reality: bool = False
    reasons: Tuple[str, ...] = ()
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.tier not in TIERS:
            raise ValueError(f"tier must be one of {TIERS}, got {self.tier!r}")
        if self.decision.is_refusal and not self.reasons:
            raise ValueError(
                f"a {self.decision.value} verdict must state a reason -- a refusal "
                "with no reason leaves the operator nothing to act on"
            )

    # -- reading -----------------------------------------------------------

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW

    @property
    def is_refusal(self) -> bool:
        return self.decision.is_refusal

    def render(self) -> str:
        head = (f"{self.decision.value} [{self.tier}]"
                f"{' reaches-reality' if self.reaches_reality else ''}")
        if not self.reasons:
            return head
        return head + "\n" + "\n".join(f"  - {r}" for r in self.reasons)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision.value,
            "tier": self.tier,
            "reaches_reality": self.reaches_reality,
            "reasons": list(self.reasons),
            "metadata": dict(self.metadata),
        }


# -- constructors ----------------------------------------------------------


def allow(tier: str = "T0", *, reasons: Iterable[str] = (),
          metadata: Optional[Dict[str, Any]] = None) -> Verdict:
    return Verdict(Decision.ALLOW, tier=tier, reasons=tuple(reasons),
                   metadata=dict(metadata or {}))


def ask(tier: str, reason: str, *, reaches_reality: bool = True,
        metadata: Optional[Dict[str, Any]] = None) -> Verdict:
    """A human word is required for THIS act. Never a standing authorization."""
    return Verdict(Decision.ASK, tier=tier, reaches_reality=reaches_reality,
                   reasons=(reason,), metadata=dict(metadata or {}))


def block(tier: str, reason: str, *, reaches_reality: bool = False,
          metadata: Optional[Dict[str, Any]] = None) -> Verdict:
    return Verdict(Decision.BLOCK, tier=tier, reaches_reality=reaches_reality,
                   reasons=(reason,), metadata=dict(metadata or {}))


def halt(reason: str, *, metadata: Optional[Dict[str, Any]] = None) -> Verdict:
    """The node is stood down. Nothing proceeds, and nothing is broken."""
    return Verdict(Decision.HALT, tier="T0", reasons=(reason,),
                   metadata=dict(metadata or {}))


def merge(verdicts: Iterable[Verdict]) -> Verdict:
    """Combine verdicts: the STRICTEST decision and the HIGHEST tier win.

    Reasons from every contributing verdict are kept, in order, deduplicated.
    Merging an empty sequence raises -- an empty population is not an ALLOW,
    it is a caller bug, and silently returning ALLOW would be exactly the
    fall-through this package refuses.
    """
    items = list(verdicts)
    if not items:
        raise ValueError(
            "merge() of no verdicts: an empty population is not permission. "
            "The caller must decide what zero classifiers means."
        )
    strictest = max(items, key=lambda v: v.decision.severity)
    tier = max((v.tier for v in items), key=lambda t: _TIER_ORDER[t])
    reaches = any(v.reaches_reality for v in items)
    reasons: List[str] = []
    for v in items:
        for r in v.reasons:
            if r not in reasons:
                reasons.append(r)
    metadata: Dict[str, Any] = {}
    for v in items:
        metadata.update(v.metadata)
    return Verdict(strictest.decision, tier=tier, reaches_reality=reaches,
                   reasons=tuple(reasons), metadata=metadata)
