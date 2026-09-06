"""Substrate -- what host is this node actually running on?

PURPOSE. The first phase of genesis measures the machine it is ON rather than
trusting a policy overlay that describes the machine somebody expected, and the
node then needs a place for the stores that are not files. Four members:
``hardware_detector`` (read-only detection, stdlib only, safe fallback on every
path), ``mode_detector`` (a pure function from a hardware profile to an
operating mode), ``schema`` (the declarative service-tier store schema, and the
source of record for the generated ``deploy/schema/genesis.sql``) and
``service_graph`` (reads ``deploy/docker-compose.genesis.yml`` and refuses what
it must never contain).

WRITE MODEL. None at import. The two detectors are pure reads; the caller owns
the profile it writes to ``.intentops/genesis/substrate.json``. ``schema`` has
one writer of one generated file (``write_rendered``), stated there;
``service_graph`` writes nothing at all.

BLIND SPOTS. Detection reports what an unprivileged process can see. Inside a
container it can read the HOST's core count rather than the cgroup's quota, and
a detection that fails returns its declared fallback rather than raising -- so
a zero is "not detected", never "not present". Read the per-function notes.
"""

from __future__ import annotations

__all__ = ["hardware_detector", "mode_detector", "schema", "service_graph"]
