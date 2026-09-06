"""Substrate -- what host is this node actually running on?

PURPOSE. The first phase of genesis measures the machine it is ON rather than
trusting a policy overlay that describes the machine somebody expected. Two
members: ``hardware_detector`` (read-only detection, stdlib only, safe fallback
on every path) and ``mode_detector`` (a pure function from a hardware profile to
an operating mode).

WRITE MODEL. None. Both members are pure reads; the caller owns the profile it
writes to ``.intentops/genesis/substrate.json``.

BLIND SPOTS. Detection reports what an unprivileged process can see. Inside a
container it can read the HOST's core count rather than the cgroup's quota, and
a detection that fails returns its declared fallback rather than raising -- so
a zero is "not detected", never "not present". Read the per-function notes.
"""

from __future__ import annotations

__all__ = ["hardware_detector", "mode_detector"]
