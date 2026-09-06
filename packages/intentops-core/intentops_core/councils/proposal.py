"""The thing a council looks at. Shared by both councils.

PURPOSE
    One small, host-independent description of a proposed action, so the values
    council and the action council read the same object and cannot disagree
    about what was proposed.

WRITE MODEL
    None -- a value object, never persisted by this module.

BLIND SPOTS
    * Fields are optional so a caller can ask for a reading with whatever it
      actually knows. An unstated field is treated as UNKNOWN, and several
      council members are built to notice exactly that ("it touches the world
      but names no one who bears the cost"). Silence is a signal here, never a
      default of "fine".
    * ``text()`` is a flattening for keyword lenses. A proposal whose hazard
      lives only in an attachment, a URL, or a caller's head is invisible to it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

__all__ = ["ProposedAction"]


@dataclass
class ProposedAction:
    """What the councils are asked to look at.

    Deliberately small. The councils read intent and blast radius, not
    implementation.
    """

    summary: str
    tier: str = "T0"
    reaches_reality: bool = False
    affects_people: Sequence[str] = field(default_factory=tuple)
    reversible: bool = True
    evidence: str = ""
    notes: str = ""
    # Action-council context. Unknown by default, and the silence is itself a signal.
    effort: str = ""
    window: str = ""
    depends_on: Sequence[str] = field(default_factory=tuple)

    def text(self) -> str:
        return " ".join(
            [
                self.summary,
                self.evidence,
                self.notes,
                self.effort,
                self.window,
                " ".join(self.affects_people),
                " ".join(self.depends_on),
            ]
        ).lower()
