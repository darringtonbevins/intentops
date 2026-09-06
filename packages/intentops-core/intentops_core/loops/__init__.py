"""Portable loop scheduling: ONE charter file, one adapter per host.

PURPOSE
    Re-export the loop surface so callers write ``from intentops_core.loops
    import load_charters`` rather than reaching into a module path that may
    later grow siblings. The four members and their one job each:

      * :mod:`~intentops_core.loops.charters` -- the declaration and its
        loader. An undeclared field HALTs; every charter is born disabled.
      * :mod:`~intentops_core.loops.adapters` -- pure renderers, one per host
        kind, that refuse rather than round a cadence they cannot express.
      * :mod:`~intentops_core.loops.engine` -- ONE tick, as a dry run,
        journalled. There is no execute path in this seed.
      * :mod:`~intentops_core.loops.conformance` -- charter-versus-binding
        drift, whose headline finding is ABSENT (a charter with no binding is
        not disabled: it carries no tagout and no way back).

WRITE MODEL
    Not a store. This package writes nothing; the one writer in the subsystem
    is the engine's append-only tick journal, which declares its model there.

BLIND SPOTS
    - Re-export only. Everything that can be wrong is documented in the four
      modules; nothing is decided here.
"""

from __future__ import annotations

from .adapters import (
    ADAPTER_KINDS,
    AdapterError,
    Binding,
    HostContext,
    render,
    render_all,
    render_cron,
    render_systemd,
    render_windows_task,
)
from .charters import (
    CADENCE_KINDS,
    SCHEMA,
    TIER_CEILINGS,
    Cadence,
    Charter,
    CharterError,
    load_charters,
    parse_charters,
    undisabled,
)
from .conformance import (
    BINDINGS_SCHEMA,
    FINDING_CODES,
    POSTURES,
    BindingRecord,
    Finding,
    Report,
    check,
    load_bindings,
    parse_bindings,
)
from .engine import TICK_VERDICTS, TickRecord, last_tick, read_journal, tick

__all__ = [
    "ADAPTER_KINDS",
    "AdapterError",
    "BINDINGS_SCHEMA",
    "Binding",
    "BindingRecord",
    "CADENCE_KINDS",
    "Cadence",
    "Charter",
    "CharterError",
    "FINDING_CODES",
    "Finding",
    "HostContext",
    "POSTURES",
    "Report",
    "SCHEMA",
    "TICK_VERDICTS",
    "TIER_CEILINGS",
    "TickRecord",
    "check",
    "last_tick",
    "load_bindings",
    "load_charters",
    "parse_bindings",
    "parse_charters",
    "read_journal",
    "render",
    "render_all",
    "render_cron",
    "render_systemd",
    "render_windows_task",
    "tick",
    "undisabled",
]


def selftest() -> tuple[bool, str]:
    """Run every member's selftest. Any failure fails the whole surface."""
    from . import adapters as _adapters
    from . import charters as _charters
    from . import conformance as _conformance
    from . import engine as _engine

    reports: list[str] = []
    ok = True
    for module in (_charters, _adapters, _engine, _conformance):
        passed, report = module.selftest()
        ok = ok and passed
        reports.append(("PASS " if passed else "FAIL ") + report)
    return ok, "\n".join(reports)
