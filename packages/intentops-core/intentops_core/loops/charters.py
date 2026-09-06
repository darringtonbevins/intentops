"""The loop charter: one declaration per unattended cadence, and its loader.

PURPOSE
    Read ``config/loop-charters.template.yaml`` (or an operator's own copy of
    it) and either return fully-validated :class:`Charter` objects or HALT with
    a remedy. One charter file feeds every host adapter, so this module is the
    single place that decides what a charter MEANS -- a second opinion living
    in a cron generator is how two hosts come to disagree about the same loop.

    Four refusals are load-bearing and are the reason this file exists rather
    than a ``yaml.safe_load`` at each call site:

      * An UNDECLARED FIELD is a HALT. A charter carrying ``budget-share``
        instead of ``budget_share`` would otherwise load clean, render clean,
        and spend an unbounded share forever -- the typo is invisible because
        the missing field was silently defaulted somewhere downstream.
      * A MISSING LOAD-BEARING FIELD is a HALT, never a default. There is no
        sensible default for "how much of the budget may this unattended loop
        spend" or "how do I switch it off".
      * ``enabled: true`` at BIRTH is a HALT. Every charter is born disabled;
        switching one on is a reviewed, dated operator act, never a side effect
        of adding a row.
      * A ``tier_ceiling`` above T2 is a HALT. T3 and T4 reach outside the
        machine and are human-gated; a tick that fires with nobody present
        cannot hold a human gate open. A loop that needs a T3 action files it
        for approval and stops, which is a T2 charter whose output is a
        proposal.

    ``entries: []`` is VALID and is the shipped state. A MISSING FILE is a
    HALT: that is a field nobody read, and the loader cannot tell "this node
    runs no loops" from "the packaging dropped the file".

WRITE MODEL
    This module is a READER. It never writes a charter file. The charter file
    itself is a single-writer store: the operator, by hand or through one
    explicit generator, one process at a time. There is no concurrent
    programmatic writer, so no lock is claimed here; a future writer owns the
    locked fresh-read read-modify-write, not this module.

BLIND SPOTS -- stated so a clean load is not read as a good charter
    * Every closed vocabulary below is a set of STRINGS. This module can tell
      declared from undeclared; it cannot tell a plausible-but-wrong value
      (``T1`` on a loop that actually writes to production) from a correct one.
      Only a reviewer reads intent.
    * ``purpose`` and ``requires_evidence`` are checked for presence and
      non-emptiness, never for quality. "does the thing" satisfies this loader.
    * The budget-share sum is checked over ENABLED charters only. A file full
      of disabled charters summing to 40.0 loads clean, because none of them
      can spend anything; the sum becomes load-bearing the moment one is
      switched on, and is re-checked then.
    * Cadence is validated for SHAPE, never against a clock or a timezone.
      ``at: "03:15"`` is local time on whatever host renders it; two hosts in
      two zones fire at two different instants and this loader cannot see that.
    * Nothing here reads the host. Whether a charter is actually bound to
      anything is :mod:`intentops_core.loops.conformance`'s question.
    * YAML parsing is delegated. If the YAML library is absent this HALTs with
      the remedy; it never falls back to a partial hand-rolled parse.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List, Mapping, Sequence, Tuple

__all__ = [
    "CharterError",
    "Charter",
    "Cadence",
    "CADENCE_KINDS",
    "TIER_CEILINGS",
    "WEEKDAYS",
    "REQUIRED_FIELDS",
    "OPTIONAL_FIELDS",
    "SCHEMA",
    "load_charters",
    "parse_charters",
    "undisabled",
    "selftest",
]

#: The one schema string this loader understands. A different one HALTs rather
#: than being read optimistically as "close enough".
SCHEMA = "loop-charters/v1"

#: Closed vocabularies. An undeclared value is a hard exit, never a default of
#: everything-applies.
CADENCE_KINDS: Tuple[str, ...] = ("interval", "daily", "weekly", "manual")
WEEKDAYS: Tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

#: T3/T4 are ABSENT here on purpose, not by oversight -- see the module
#: docstring. Raising this cap is a decision, not a configuration value.
TIER_CEILINGS: Tuple[str, ...] = ("T0", "T1", "T2")

REQUIRED_FIELDS: Tuple[str, ...] = (
    "id",
    "purpose",
    "cadence",
    "tier_ceiling",
    "enabled",
    "budget_share",
    "kill_switch",
    "requires_evidence",
)
OPTIONAL_FIELDS: Tuple[str, ...] = ("notes", "tagout")

_CADENCE_REQUIRED = {
    "interval": ("every",),
    "daily": ("at",),
    "weekly": ("at", "weekday"),
    "manual": (),
}
_CADENCE_ALLOWED = {
    "interval": {"kind", "every"},
    "daily": {"kind", "at"},
    "weekly": {"kind", "at", "weekday"},
    "manual": {"kind"},
}

_ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_EVERY_RE = re.compile(r"^(\d+)([mhd])$")
_AT_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class CharterError(Exception):
    """A HALT. Carries the remedy, so a caller can print what to do."""

    def __init__(self, message: str, remedy: str, *,
                 path: Path | None = None, subject: str | None = None) -> None:
        where = f" [{path}]" if path is not None else ""
        what = f" ({subject})" if subject else ""
        super().__init__(f"HALT{where}{what}: {message}\n  remedy: {remedy}")
        self.message = message
        self.remedy = remedy
        self.path = path
        self.subject = subject


@dataclass(frozen=True)
class Cadence:
    """When a loop fires, in the one portable vocabulary every adapter reads."""

    kind: str
    every: str | None = None
    at: str | None = None
    weekday: str | None = None

    @property
    def scheduled(self) -> bool:
        """False only for ``manual`` -- the one cadence for which having no
        host binding is the correct state rather than drift."""
        return self.kind != "manual"

    def interval_parts(self) -> Tuple[int, str]:
        """``("15m")`` -> ``(15, "m")``. Raises for a non-interval cadence."""
        if self.kind != "interval" or self.every is None:
            raise CharterError(
                f"interval_parts() called on a {self.kind} cadence",
                "call it only when cadence.kind == 'interval'")
        match = _EVERY_RE.match(self.every)
        assert match is not None  # validated at load
        return int(match.group(1)), match.group(2)

    def at_parts(self) -> Tuple[int, int]:
        """``"03:15"`` -> ``(3, 15)``. Raises when the cadence carries no time."""
        if self.at is None:
            raise CharterError(
                f"at_parts() called on a {self.kind} cadence with no time",
                "call it only for a daily or weekly cadence")
        hh, mm = self.at.split(":")
        return int(hh), int(mm)

    def describe(self) -> str:
        if self.kind == "interval":
            return f"every {self.every}"
        if self.kind == "daily":
            return f"daily at {self.at}"
        if self.kind == "weekly":
            return f"{self.weekday} at {self.at}"
        return "manual (no schedule)"


@dataclass(frozen=True)
class Charter:
    """One unattended cadence, fully declared. Frozen: adapters render it,
    nobody mutates it."""

    id: str
    purpose: str
    cadence: Cadence
    tier_ceiling: str
    enabled: bool
    budget_share: float
    kill_switch: str
    requires_evidence: Tuple[str, ...]
    notes: str | None = None
    tagout: str | None = None

    def kill_switch_path(self, node_root: Path | str) -> Path:
        """Resolve the (relative, by contract) kill switch against a node."""
        return Path(node_root) / self.kill_switch


# --------------------------------------------------------------------------
# validation helpers -- each raises with a remedy, never returns a default
# --------------------------------------------------------------------------


def _halt(message: str, remedy: str, path: Path | None, subject: str | None
          ) -> "CharterError":
    return CharterError(message, remedy, path=path, subject=subject)


def _require_mapping(value: Any, what: str, path: Path | None,
                     subject: str | None) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _halt(f"{what} is {type(value).__name__}, not a mapping",
                    f"write {what} as a YAML mapping", path, subject)
    return value


def _parse_cadence(raw: Any, path: Path | None, cid: str) -> Cadence:
    block = _require_mapping(raw, "cadence", path, cid)
    kind = block.get("kind")
    if kind is None:
        raise _halt("cadence.kind is missing",
                    "declare one of: " + " | ".join(CADENCE_KINDS), path, cid)
    if kind not in CADENCE_KINDS:
        raise _halt(f"cadence.kind {kind!r} is undeclared",
                    "use one of: " + " | ".join(CADENCE_KINDS), path, cid)
    allowed = _CADENCE_ALLOWED[kind]
    undeclared = sorted(set(block) - allowed)
    if undeclared:
        raise _halt(
            f"cadence carries field(s) {', '.join(undeclared)} that a "
            f"{kind} cadence does not declare",
            f"a {kind} cadence declares: {', '.join(sorted(allowed))}",
            path, cid)
    for field_name in _CADENCE_REQUIRED[kind]:
        if block.get(field_name) in (None, ""):
            raise _halt(f"cadence.{field_name} is missing on a {kind} cadence",
                        f"a {kind} cadence requires {field_name}", path, cid)

    every = at = weekday = None
    if kind == "interval":
        every = str(block["every"])
        match = _EVERY_RE.match(every)
        if match is None or int(match.group(1)) < 1:
            raise _halt(f"cadence.every {every!r} is not <N>m | <N>h | <N>d "
                        "with N >= 1",
                        "write an interval such as '15m', '2h' or '1d'",
                        path, cid)
    if kind in ("daily", "weekly"):
        at = str(block["at"])
        if _AT_RE.match(at) is None:
            raise _halt(f"cadence.at {at!r} is not HH:MM 24-hour local time",
                        "write a time such as '03:15'", path, cid)
    if kind == "weekly":
        weekday = str(block["weekday"]).lower()
        if weekday not in WEEKDAYS:
            raise _halt(f"cadence.weekday {weekday!r} is undeclared",
                        "use one of: " + " | ".join(WEEKDAYS), path, cid)
    return Cadence(kind=kind, every=every, at=at, weekday=weekday)


def _parse_entry(raw: Any, index: int, path: Path | None, *,
                 birth: bool) -> Charter:
    entry = _require_mapping(raw, f"entries[{index}]", path, None)
    cid = entry.get("id")
    subject = str(cid) if cid else f"entries[{index}]"

    undeclared = sorted(set(entry) - set(REQUIRED_FIELDS) - set(OPTIONAL_FIELDS))
    if undeclared:
        raise _halt(
            "undeclared field(s): " + ", ".join(undeclared),
            "remove them, or declare them in REQUIRED_FIELDS / OPTIONAL_FIELDS "
            "in intentops_core/loops/charters.py -- a field this loader does "
            "not know is a field nothing validates",
            path, subject)
    for field_name in REQUIRED_FIELDS:
        if field_name not in entry or entry[field_name] is None:
            raise _halt(
                f"required field {field_name!r} is missing or null",
                "state it explicitly; there is no default for a load-bearing "
                "field on an unattended loop", path, subject)

    if not isinstance(cid, str) or _ID_RE.match(cid) is None:
        raise _halt(f"id {cid!r} is not kebab-case",
                    "use lowercase words joined by single hyphens, e.g. "
                    "'loop-doc-drift'", path, subject)

    purpose = entry["purpose"]
    if not isinstance(purpose, str) or not purpose.strip():
        raise _halt("purpose is empty",
                    "write one sentence a stranger can act on", path, subject)

    cadence = _parse_cadence(entry["cadence"], path, cid)

    ceiling = entry["tier_ceiling"]
    if ceiling not in TIER_CEILINGS:
        raise _halt(
            f"tier_ceiling {ceiling!r} is undeclared for a loop charter",
            "use T0, T1 or T2. T3 and T4 reach outside the machine and are "
            "human-gated; a tick that fires with nobody present cannot hold a "
            "human gate open. A loop that needs a T3 action files it for "
            "approval and stops -- that is a T2 charter", path, subject)

    enabled = entry["enabled"]
    if not isinstance(enabled, bool):
        raise _halt(f"enabled is {type(enabled).__name__}, not a boolean",
                    "write true or false", path, subject)
    if birth and enabled:
        raise _halt(
            "enabled: true at birth",
            "every charter is born disabled; switching one on is a reviewed, "
            "dated operator act, never a side effect of adding a row",
            path, subject)

    share = entry["budget_share"]
    if isinstance(share, bool) or not isinstance(share, (int, float)):
        raise _halt(f"budget_share is {type(share).__name__}, not a number",
                    "write a fraction such as 0.10", path, subject)
    share = float(share)
    if not (0.0 < share <= 1.0):
        raise _halt(f"budget_share {share} is outside (0, 1]",
                    "write the fraction of the node's loop budget this charter "
                    "may spend", path, subject)

    kill = entry["kill_switch"]
    if not isinstance(kill, str) or not kill.strip():
        raise _halt("kill_switch is empty",
                    "name a relative sentinel path; a loop with no way to "
                    "switch it off is not governed", path, subject)
    # Absoluteness is asked in a host-independent way on purpose: a POSIX
    # rooted path reads as RELATIVE to pathlib on Windows and vice versa, so a
    # single Path.is_absolute() check passes the very charter it should refuse
    # on the other half of the hosts this file exists to span.
    if (kill.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", kill)
            or Path(kill).is_absolute()):
        raise _halt(f"kill_switch {kill!r} is an absolute path",
                    "make it relative to the node root; an absolute path is "
                    "machine-specific and does not survive the move this file "
                    "exists to make possible", path, subject)
    if ".." in re.split(r"[\\/]", kill):
        raise _halt(f"kill_switch {kill!r} climbs out of the node root",
                    "keep the sentinel inside the node; a switch a node cannot "
                    "reach after a move is a switch that silently stops "
                    "working", path, subject)

    evidence = entry["requires_evidence"]
    if isinstance(evidence, str) or not isinstance(evidence, Sequence):
        raise _halt("requires_evidence is not a list",
                    "write a YAML list of what this loop must be able to show",
                    path, subject)
    items = [str(e).strip() for e in evidence]
    if not items or any(not e for e in items):
        raise _halt("requires_evidence is empty or holds a blank item",
                    "name at least one thing this loop must show; a loop with "
                    "no evidence requirement produces claims, not work",
                    path, subject)

    for optional in ("notes", "tagout"):
        value = entry.get(optional)
        if value is not None and not isinstance(value, str):
            raise _halt(f"{optional} is {type(value).__name__}, not a string",
                        f"write {optional} as text or omit it", path, subject)

    return Charter(
        id=cid,
        purpose=purpose.strip(),
        cadence=cadence,
        tier_ceiling=ceiling,
        enabled=enabled,
        budget_share=share,
        kill_switch=kill,
        requires_evidence=tuple(items),
        notes=entry.get("notes"),
        tagout=entry.get("tagout"),
    )


# --------------------------------------------------------------------------
# the loader
# --------------------------------------------------------------------------


def parse_charters(document: Any, *, path: Path | None = None,
                   birth: bool = False) -> List[Charter]:
    """Validate an already-parsed charter document. HALTs; never warns."""
    doc = _require_mapping(document, "the charter document", path, None)

    schema = doc.get("schema")
    if schema != SCHEMA:
        raise _halt(f"schema is {schema!r}, not {SCHEMA!r}",
                    f"this loader reads {SCHEMA} only; a different schema is a "
                    "different contract and is not read optimistically",
                    path, None)
    as_of = doc.get("as_of")
    if as_of in (None, ""):
        raise _halt("as_of is missing",
                    "date the file; an undated declaration has no half-life "
                    "and cannot be re-asked on time", path, None)
    try:
        _dt.date.fromisoformat(str(as_of))
    except ValueError as exc:
        raise _halt(f"as_of {as_of!r} is not an ISO date: {exc}",
                    "write as_of as YYYY-MM-DD", path, None) from exc

    if "entries" not in doc:
        raise _halt("entries is missing",
                    "write `entries: []` for a node that runs no loops -- "
                    "an empty list is the honest state; an absent key is a "
                    "question nobody answered", path, None)
    entries = doc["entries"]
    if entries is None:
        entries = []
    if isinstance(entries, Mapping) or not isinstance(entries, Sequence):
        raise _halt(f"entries is {type(entries).__name__}, not a list",
                    "write entries as a YAML list", path, None)

    charters = [_parse_entry(raw, i, path, birth=birth)
                for i, raw in enumerate(entries)]

    seen: dict[str, int] = {}
    for i, charter in enumerate(charters):
        if charter.id in seen:
            raise _halt(
                f"duplicate charter id {charter.id!r} "
                f"(entries[{seen[charter.id]}] and entries[{i}])",
                "ids are how a binding names its charter; two rows with one id "
                "make every drift finding ambiguous", path, charter.id)
        seen[charter.id] = i

    spent = sum(c.budget_share for c in charters if c.enabled)
    if spent > 1.0 + 1e-9:
        raise _halt(
            f"enabled charters claim {spent:.3f} of the loop budget",
            "reduce budget_share until the enabled entries sum to at most 1.0; "
            "a budget nobody can honour is a budget that will be exceeded "
            "silently", path, None)
    return charters


def load_charters(path: Path | str, *, birth: bool = False) -> List[Charter]:
    """Read and validate a charter file. A MISSING FILE is a HALT.

    ``birth=True`` additionally refuses any ``enabled: true`` charter -- use it
    when loading the SHIPPED template, where an enabled row is a packaging
    defect rather than an operator's decision.
    """
    p = Path(path)
    if not p.exists():
        raise _halt("the charter file does not exist",
                    "create it from config/loop-charters.template.yaml; a "
                    "missing file cannot be told apart from 'this node runs no "
                    "loops', and those are different facts", p, None)
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise _halt(f"the YAML library is unavailable: {exc}",
                    "pip install pyyaml -- this loader never falls back to a "
                    "partial hand-rolled parse", p, None) from exc
    try:
        document = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - any parse failure is a HALT
        raise _halt(f"the charter file is unparseable: {exc}",
                    "fix the YAML; a charter file that half-parses produces a "
                    "loop fleet nobody declared", p, None) from exc
    return parse_charters(document, path=p, birth=birth)


def undisabled(charters: Iterable[Charter]) -> List[str]:
    """Ids of charters that are switched ON. Never a verdict -- an enabled
    charter is legitimate after review; this is the list a reviewer reads."""
    return [c.id for c in charters if c.enabled]


# --------------------------------------------------------------------------
# selftest -- a loader that has never refused is indistinguishable from one
# that cannot
# --------------------------------------------------------------------------

_GOOD_ENTRY: dict[str, Any] = {
    "id": "loop-example",
    "purpose": "Compare two things and file each mismatch as one finding.",
    "cadence": {"kind": "interval", "every": "30m"},
    "tier_ceiling": "T1",
    "enabled": False,
    "budget_share": 0.1,
    "kill_switch": ".intentops/loops/example.disabled",
    "requires_evidence": ["the file and line of each claimed mismatch"],
}


def _doc(*entries: Mapping[str, Any]) -> dict[str, Any]:
    return {"schema": SCHEMA, "as_of": "2026-09-06",
            "entries": [dict(e) for e in entries]}


def selftest() -> Tuple[bool, str]:
    """Prove the loader accepts a good file and refuses each named defect."""
    failures: List[str] = []

    def refuses(label: str, document: Any, *, birth: bool = False) -> None:
        try:
            parse_charters(document, birth=birth)
        except CharterError:
            return
        failures.append(label)

    try:
        empty = parse_charters(_doc())
        if empty != []:
            failures.append("an empty charter file did not load as no charters")
        good = parse_charters(_doc(_GOOD_ENTRY))
        if len(good) != 1 or good[0].cadence.interval_parts() != (30, "m"):
            failures.append("a good charter did not round-trip")
    except CharterError as exc:  # pragma: no cover - would be a real defect
        failures.append(f"a good charter file was refused: {exc}")

    refuses("an undeclared field was accepted",
            _doc({**_GOOD_ENTRY, "budget-share": 0.2}))
    refuses("a missing kill_switch was accepted",
            _doc({k: v for k, v in _GOOD_ENTRY.items() if k != "kill_switch"}))
    refuses("a POSIX-absolute kill_switch was accepted",
            _doc({**_GOOD_ENTRY, "kill_switch": "/var/run/example.disabled"}))
    refuses("a drive-absolute kill_switch was accepted",
            _doc({**_GOOD_ENTRY, "kill_switch": "C:/node/example.disabled"}))
    refuses("a kill_switch climbing out of the node root was accepted",
            _doc({**_GOOD_ENTRY, "kill_switch": "../example.disabled"}))
    refuses("a T3 tier_ceiling was accepted",
            _doc({**_GOOD_ENTRY, "tier_ceiling": "T3"}))
    refuses("enabled: true was accepted at birth",
            _doc({**_GOOD_ENTRY, "enabled": True}), birth=True)
    refuses("an empty requires_evidence was accepted",
            _doc({**_GOOD_ENTRY, "requires_evidence": []}))
    refuses("a budget_share of 0 was accepted",
            _doc({**_GOOD_ENTRY, "budget_share": 0}))
    refuses("over-subscribed enabled charters were accepted",
            _doc({**_GOOD_ENTRY, "enabled": True, "budget_share": 0.7},
                 {**_GOOD_ENTRY, "id": "loop-other", "enabled": True,
                  "budget_share": 0.7}))
    refuses("a duplicate id was accepted", _doc(_GOOD_ENTRY, _GOOD_ENTRY))
    refuses("an unknown schema was accepted",
            {**_doc(), "schema": "loop-charters/v2"})
    refuses("a missing entries key was accepted",
            {"schema": SCHEMA, "as_of": "2026-09-06"})
    refuses("an interval field on a daily cadence was accepted",
            _doc({**_GOOD_ENTRY,
                  "cadence": {"kind": "daily", "at": "03:15", "every": "5m"}}))
    refuses("a malformed time was accepted",
            _doc({**_GOOD_ENTRY, "cadence": {"kind": "daily", "at": "25:00"}}))

    if failures:
        return False, "charters: " + "; ".join(failures)
    return True, "charters: a good file loads; 15 named defects are refused"
