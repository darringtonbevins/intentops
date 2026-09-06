"""Alignment with the operator: the interview that elicits it, the scorer that
measures it, and the two-bar gate that refuses to call it done.

Design of record: ``docs/reports/intentops-oss/design/genesis-design.md``
sect. 7, and the genesis imprint sect. 8 and 10.

Nothing in this package claims alignment. The honest string below the bar is
``uncalibrated: N of 20 rulings, X%`` -- a system that describes itself as
aligned is asserting the one thing it cannot verify about itself.
"""

from __future__ import annotations

__all__ = ["calibration", "interview"]
