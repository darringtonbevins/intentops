"""Charter-versus-binding conformance: does the host run what the charter says?

PURPOSE
    A charter file is a declaration. A host binding -- a crontab line, an
    installed timer, a registered scheduled task -- is what actually fires.
    Nothing keeps the two in step by itself, and the gap is silent in both
    directions: a charter nobody bound never runs and nobody notices, and a
    binding nobody chartered runs unattended with no declared ceiling, budget
    or kill switch. This module compares them and names each gap.

    THE FINDING THIS EXISTS FOR IS ``ABSENT``. A charter with no binding is
    ABSENT, **not disabled**. Disabled is a governed state: something says so,
    somebody dated it, and there is a stated way back. Absent is the same
    picture with none of that -- no tagout, no reason, no condition under which
    it returns. The two look identical from the outside (the loop is not
    running) and only one of them is a decision. A charter that is off with a
    ``tagout`` id reads ``PARKED`` and is ATTENTION; the same charter with no
    tagout reads ``ABSENT`` and is DRIFTED.

    THE DANGEROUS DIRECTION IS ``UNCHARTERED-RUN``: the charter says off and
    the host says on. That is an unattended loop with no authority behind it,
    and it is graded DRIFTED whatever else is clean.

    AN UNREADABLE BINDINGS SOURCE IS ``DEGRADED``, NEVER CONFORMANT. A check
    that cannot see the host must say so and grade worst, because "I found no
    drift" and "I could not look" render identically as a green line and only
    one of them is a reading.

WRITE MODEL
    None of its own. This module READS a charter file and a bindings file and
    returns a report; it writes no store and installs nothing. A project that
    wants a posture ledger appends this report itself, under its own declared
    model.

BLIND SPOTS
    * The bindings file is a DECLARATION of what the host has, produced by
      exporting the host's schedule. This module does not talk to cron, systemd
      or a task scheduler, so a bindings file that is out of date reads clean.
      Its ``as_of`` is the only guard, and it is a claim, not a measurement.
    * Cadence drift is compared against what THIS repository's adapter renders
      today. An adapter change makes every installed binding read as drift,
      which is correct but arrives all at once.
    * Cadence drift is checked only when a :class:`~intentops_core.loops.
      adapters.HostContext` is supplied. Without one the report says
      ``cadence_checked: false`` in so many words rather than quietly counting
      those charters as clean.
    * Nothing here reads whether a bound loop ever FIRED. A perfectly
      conformant pair where the daemon is dead reads CONFORMANT.
    * A binding for a host kind other than the one being checked is not
      evidence about this host, and is skipped -- named in the report's
      ``skipped`` count, never silently dropped.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from .adapters import ADAPTER_KINDS, AdapterError, HostContext, render
from .charters import Charter, CharterError

__all__ = [
    "BindingRecord",
    "Finding",
    "Report",
    "BINDINGS_SCHEMA",
    "FINDING_CODES",
    "POSTURES",
    "load_bindings",
    "parse_bindings",
    "check",
    "selftest",
]

BINDINGS_SCHEMA = "loop-bindings/v1"

#: Every finding code, with the posture it drives. There is no other outcome.
FINDING_CODES: Dict[str, str] = {
    "OK": "CONFORMANT",
    "MANUAL-UNBOUND": "CONFORMANT",
    "PARKED": "ATTENTION",
    "BINDING-PARKED": "ATTENTION",
    "ABSENT": "DRIFTED",
    "ORPHAN": "DRIFTED",
    "UNCHARTERED-RUN": "DRIFTED",
    "BINDING-OFF": "DRIFTED",
    "MANUAL-BOUND": "DRIFTED",
    "CADENCE-DRIFT": "DRIFTED",
    "UNRENDERABLE": "DRIFTED",
    "UNREADABLE": "DEGRADED",
}

#: Worst-first. DEGRADED outranks everything: an unreadable source cannot be
#: read as an improvement.
POSTURES: Tuple[str, ...] = ("DEGRADED", "DRIFTED", "ATTENTION", "CONFORMANT")

_BINDING_REQUIRED: Tuple[str, ...] = ("charter_id", "kind", "enabled")
_BINDING_OPTIONAL: Tuple[str, ...] = ("cadence", "tagout", "notes")


@dataclass(frozen=True)
class BindingRecord:
    """What the host says it has, for one charter, on one host kind."""

    charter_id: str
    kind: str
    enabled: bool
    cadence: str | None = None
    tagout: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class Finding:
    """One comparison outcome. Always carries a subject and a remedy."""

    code: str
    subject: str
    detail: str
    remedy: str

    @property
    def posture(self) -> str:
        return FINDING_CODES[self.code]

    def __str__(self) -> str:
        return f"{self.code:16} {self.subject}: {self.detail}"


@dataclass(frozen=True)
class Report:
    """The whole reading, with its denominator beside its verdict."""

    posture: str
    findings: Tuple[Finding, ...]
    host_kind: str
    charters_seen: int
    bindings_seen: int
    bindings_skipped: int = 0
    cadence_checked: bool = True
    counts: Dict[str, int] = field(default_factory=dict)

    @property
    def exit_code(self) -> int:
        """0 when the posture is governed; 1 when it is drifted or unread."""
        return 0 if self.posture in ("CONFORMANT", "ATTENTION") else 1

    def render(self) -> str:
        cadence_line = ("yes" if self.cadence_checked else
                        "NO -- no host context was supplied, so cadence "
                        "drift is UNCHECKED, not clean")
        lines = [
            f"loops check -- host {self.host_kind}",
            f"  charters: {self.charters_seen}   bindings: "
            f"{self.bindings_seen} (skipped, other host: "
            f"{self.bindings_skipped})",
            f"  cadence compared: {cadence_line}",
        ]
        for finding in self.findings:
            lines.append(f"  {finding}")
            lines.append(f"      remedy: {finding.remedy}")
        counted = ", ".join(f"{k} {v}" for k, v in sorted(self.counts.items()))
        lines.append(f"  counts: {counted or 'none'}")
        lines.append(f"  posture: {self.posture}")
        return "\n".join(lines)


def _halt(message: str, remedy: str, path: Path | None = None,
          subject: str | None = None) -> CharterError:
    return CharterError(message, remedy, path=path, subject=subject)


# --------------------------------------------------------------------------
# the bindings file
# --------------------------------------------------------------------------


def parse_bindings(document: Any, *, path: Path | None = None
                   ) -> Tuple[str, List[BindingRecord]]:
    """Validate an already-parsed bindings document. Returns (host, records)."""
    if not isinstance(document, Mapping):
        raise _halt(f"the bindings document is {type(document).__name__}, "
                    "not a mapping", "write it as a YAML mapping", path)
    if document.get("schema") != BINDINGS_SCHEMA:
        raise _halt(f"schema is {document.get('schema')!r}, not "
                    f"{BINDINGS_SCHEMA!r}",
                    f"this reader reads {BINDINGS_SCHEMA} only", path)
    as_of = document.get("as_of")
    if as_of in (None, ""):
        raise _halt("as_of is missing",
                    "date the export; an undated claim about a host has no "
                    "half-life", path)
    try:
        _dt.date.fromisoformat(str(as_of))
    except ValueError as exc:
        raise _halt(f"as_of {as_of!r} is not an ISO date: {exc}",
                    "write as_of as YYYY-MM-DD", path) from exc
    host = document.get("host")
    if host not in ADAPTER_KINDS:
        raise _halt(f"host {host!r} is undeclared",
                    "use one of: " + " | ".join(ADAPTER_KINDS), path)
    entries = document.get("entries")
    if "entries" not in document:
        raise _halt("entries is missing",
                    "write `entries: []` for a host that schedules nothing; an "
                    "absent key is a question nobody answered", path)
    if entries is None:
        entries = []
    if isinstance(entries, Mapping) or not isinstance(entries, Sequence):
        raise _halt(f"entries is {type(entries).__name__}, not a list",
                    "write entries as a YAML list", path)

    records: List[BindingRecord] = []
    for index, raw in enumerate(entries):
        subject = f"entries[{index}]"
        if not isinstance(raw, Mapping):
            raise _halt(f"{subject} is not a mapping",
                        "write each binding as a YAML mapping", path, subject)
        undeclared = sorted(set(raw) - set(_BINDING_REQUIRED)
                            - set(_BINDING_OPTIONAL))
        if undeclared:
            raise _halt("undeclared field(s): " + ", ".join(undeclared),
                        "a field this reader does not know is a field nothing "
                        "validates", path, subject)
        for name in _BINDING_REQUIRED:
            if name not in raw or raw[name] is None:
                raise _halt(f"required field {name!r} is missing or null",
                            "state it explicitly; there is no default",
                            path, subject)
        if raw["kind"] not in ADAPTER_KINDS:
            raise _halt(f"kind {raw['kind']!r} is undeclared",
                        "use one of: " + " | ".join(ADAPTER_KINDS),
                        path, subject)
        if not isinstance(raw["enabled"], bool):
            raise _halt("enabled is not a boolean", "write true or false",
                        path, subject)
        records.append(BindingRecord(
            charter_id=str(raw["charter_id"]),
            kind=str(raw["kind"]),
            enabled=bool(raw["enabled"]),
            cadence=(None if raw.get("cadence") is None
                     else str(raw["cadence"])),
            tagout=(None if raw.get("tagout") is None
                    else str(raw["tagout"])),
            notes=(None if raw.get("notes") is None else str(raw["notes"]))))
    return str(host), records


def load_bindings(path: Path | str) -> Tuple[str, List[BindingRecord]]:
    """Read a bindings export. A MISSING FILE is a HALT, not an empty host."""
    p = Path(path)
    if not p.exists():
        raise _halt("the bindings file does not exist",
                    "export the host's schedule to it; a missing file cannot "
                    "be told apart from 'this host schedules nothing', and "
                    "those are different facts", p)
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise _halt(f"the YAML library is unavailable: {exc}",
                    "pip install pyyaml", p) from exc
    try:
        document = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise _halt(f"the bindings file is unparseable: {exc}",
                    "fix the YAML; a half-parsed export is a host nobody "
                    "actually read", p) from exc
    return parse_bindings(document, path=p)


# --------------------------------------------------------------------------
# the comparison
# --------------------------------------------------------------------------


def _cadence_finding(charter: Charter, binding: BindingRecord,
                     host_kind: str, context: HostContext) -> Finding | None:
    try:
        rendered = render(host_kind, charter, context)
    except AdapterError as exc:
        return Finding(
            "UNRENDERABLE", charter.id,
            f"{host_kind} cannot express this charter: {exc.message}",
            "give the charter a cadence this host expresses, or bind it on a "
            "host that can. It stays in the denominator either way")
    if binding.cadence is None:
        return None
    if binding.cadence.strip() != rendered.cadence:
        return Finding(
            "CADENCE-DRIFT", charter.id,
            f"the host says {binding.cadence.strip()!r}; the charter renders "
            f"{rendered.cadence!r}",
            "re-render from the charter and re-install. A hand-edited "
            "schedule is the drift this check exists to find")
    return None


def check(charters: Iterable[Charter],
          bindings: Iterable[BindingRecord],
          *,
          host_kind: str,
          context: HostContext | None = None,
          unreadable: Sequence[str] = ()) -> Report:
    """Compare declared charters against a host's bindings.

    ``unreadable`` carries sources the caller could not read at all. Each one
    becomes a DEGRADED finding and stays in the denominator, because a check
    that could not look must never render as a check that found nothing.
    """
    if host_kind not in ADAPTER_KINDS:
        raise _halt(f"host_kind {host_kind!r} is undeclared",
                    "use one of: " + " | ".join(ADAPTER_KINDS))

    charter_list = list(charters)
    all_bindings = list(bindings)
    for_host = [b for b in all_bindings if b.kind == host_kind]
    skipped = len(all_bindings) - len(for_host)

    by_id: Dict[str, BindingRecord] = {}
    findings: List[Finding] = []
    for binding in for_host:
        if binding.charter_id in by_id:
            findings.append(Finding(
                "ORPHAN", binding.charter_id,
                "the host carries two bindings with this charter id",
                "remove the duplicate; two bindings for one charter make "
                "every later finding about it ambiguous"))
            continue
        by_id[binding.charter_id] = binding

    declared = {c.id for c in charter_list}
    for binding in for_host:
        if binding.charter_id not in declared:
            findings.append(Finding(
                "ORPHAN", binding.charter_id,
                f"the host schedules {binding.charter_id!r} and no charter "
                "declares it",
                "write the charter, or remove the binding. An unattended loop "
                "with no charter has no ceiling, no budget and no kill switch"))

    for charter in charter_list:
        binding = by_id.get(charter.id)
        if binding is None:
            if not charter.cadence.scheduled:
                findings.append(Finding(
                    "MANUAL-UNBOUND", charter.id,
                    "a manual charter with no binding -- the correct state",
                    "none"))
            elif charter.tagout and not charter.enabled:
                findings.append(Finding(
                    "PARKED", charter.id,
                    f"off, with a stated way back ({charter.tagout})",
                    "re-read the tagout when its condition is met; a parked "
                    "loop that is quietly never re-energised is the next "
                    "ABSENT"))
            else:
                findings.append(Finding(
                    "ABSENT", charter.id,
                    "the charter declares a schedule and this host has no "
                    "binding for it. ABSENT is not disabled: it carries no "
                    "tagout, no reason and no way back",
                    f"render and install it (`intentops loops render "
                    f"{charter.id} --host {host_kind}`), or switch it off "
                    "deliberately by adding a tagout id and enabled: false"))
            continue

        if not charter.enabled and binding.enabled:
            findings.append(Finding(
                "UNCHARTERED-RUN", charter.id,
                "the charter is disabled and the host runs it anyway",
                "disable the binding on the host, or enable the charter "
                "deliberately. This is the dangerous direction: an unattended "
                "loop the charter has not authorised"))
            continue
        if charter.enabled and not binding.enabled:
            code = "BINDING-PARKED" if binding.tagout else "BINDING-OFF"
            note = (f"with a stated way back ({binding.tagout})"
                    if binding.tagout else "with no tagout and no way back")
            findings.append(Finding(
                code, charter.id,
                f"the charter is enabled and the host binding is off, {note}",
                "re-enable the binding, or record a tagout naming who switched "
                "it off, why, and what re-energises it"))
            continue
        if not charter.cadence.scheduled:
            findings.append(Finding(
                "MANUAL-BOUND", charter.id,
                "a manual charter is bound to a schedule -- something is "
                "firing a loop that declares no cadence",
                "remove the binding, or give the charter a real cadence"))
            continue
        if context is not None:
            drift = _cadence_finding(charter, binding, host_kind, context)
            if drift is not None:
                findings.append(drift)
                continue
        findings.append(Finding("OK", charter.id,
                                f"{charter.cadence.describe()}, bound and "
                                "enabled on both sides", "none"))

    for source in unreadable:
        findings.append(Finding(
            "UNREADABLE", source,
            "this source could not be read, so nothing about it was checked",
            "fix or replace the source. It stays in the denominator: 'I found "
            "no drift' and 'I could not look' must never render alike"))

    counts: Dict[str, int] = {}
    for finding in findings:
        counts[finding.code] = counts.get(finding.code, 0) + 1
    posture = "CONFORMANT"
    for candidate in POSTURES:  # worst-first: the first hit wins
        if any(f.posture == candidate for f in findings):
            posture = candidate
            break
    return Report(posture=posture, findings=tuple(findings),
                  host_kind=host_kind, charters_seen=len(charter_list),
                  bindings_seen=len(for_host), bindings_skipped=skipped,
                  cadence_checked=context is not None, counts=counts)


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------


def selftest() -> Tuple[bool, str]:
    """Prove every finding code can fire and that the posture ordering holds.

    A detector that has never fired is indistinguishable from a broken one, and
    this one has eleven ways to fire.
    """
    from .charters import SCHEMA, parse_charters

    failures: List[str] = []
    context = HostContext(node_root="/srv/node",
                          tick_command="intentops loops tick",
                          windows_wrapper="scripts/launch-hidden.vbs")

    def make(**over: Any) -> Charter:
        entry: Dict[str, Any] = {
            "id": "loop-example",
            "purpose": "Compare two things and file each mismatch.",
            "cadence": {"kind": "interval", "every": "30m"},
            "tier_ceiling": "T1",
            "enabled": True,
            "budget_share": 0.1,
            "kill_switch": ".intentops/loops/example.disabled",
            "requires_evidence": ["the file and line of each mismatch"],
        }
        entry.update(over)
        return parse_charters({"schema": SCHEMA, "as_of": "2026-09-06",
                               "entries": [entry]})[0]

    def bind(**over: Any) -> BindingRecord:
        base: Dict[str, Any] = {"charter_id": "loop-example", "kind": "cron",
                                "enabled": True, "cadence": "*/30 * * * *"}
        base.update(over)
        return BindingRecord(**base)

    def codes(report: Report) -> set[str]:
        return {f.code for f in report.findings}

    def expect(label: str, report: Report, code: str, posture: str) -> None:
        if code not in codes(report):
            failures.append(f"{label}: {code} did not fire "
                            f"(got {sorted(codes(report))})")
        elif report.posture != posture:
            failures.append(f"{label}: posture read {report.posture}, "
                            f"expected {posture}")

    expect("a matching pair", check([make()], [bind()], host_kind="cron",
                                    context=context), "OK", "CONFORMANT")
    expect("a charter with no binding",
           check([make()], [], host_kind="cron", context=context),
           "ABSENT", "DRIFTED")
    expect("a parked charter",
           check([make(enabled=False, tagout="LOTO-2026-09-06-EXAMPLE")], [],
                 host_kind="cron", context=context), "PARKED", "ATTENTION")
    expect("a binding with no charter",
           check([], [bind(charter_id="loop-nobody")], host_kind="cron",
                 context=context), "ORPHAN", "DRIFTED")
    expect("a disabled charter the host runs",
           check([make(enabled=False)], [bind()], host_kind="cron",
                 context=context), "UNCHARTERED-RUN", "DRIFTED")
    expect("an enabled charter the host has switched off",
           check([make()], [bind(enabled=False)], host_kind="cron",
                 context=context), "BINDING-OFF", "DRIFTED")
    expect("a binding switched off WITH a tagout",
           check([make()], [bind(enabled=False, tagout="LOTO-2026-09-06-X")],
                 host_kind="cron", context=context),
           "BINDING-PARKED", "ATTENTION")
    expect("a manual charter with no binding",
           check([make(cadence={"kind": "manual"})], [], host_kind="cron",
                 context=context), "MANUAL-UNBOUND", "CONFORMANT")
    expect("a manual charter that is bound anyway",
           check([make(cadence={"kind": "manual"})], [bind(cadence=None)],
                 host_kind="cron", context=context), "MANUAL-BOUND", "DRIFTED")
    expect("a hand-edited schedule",
           check([make()], [bind(cadence="*/5 * * * *")], host_kind="cron",
                 context=context), "CADENCE-DRIFT", "DRIFTED")
    expect("a cadence this host cannot express",
           check([make(cadence={"kind": "interval", "every": "7m"})],
                 [bind(cadence="*/7 * * * *")], host_kind="cron",
                 context=context), "UNRENDERABLE", "DRIFTED")
    expect("an unreadable source",
           check([make()], [bind()], host_kind="cron", context=context,
                 unreadable=["the host export"]), "UNREADABLE", "DEGRADED")

    clean = check([make()], [bind()], host_kind="cron", context=None)
    if clean.cadence_checked:
        failures.append("a check with no host context claimed it compared "
                        "cadence")
    if "cadence drift is UNCHECKED" not in clean.render():
        failures.append("the report did not say cadence went unchecked")

    other_host = check([make()], [bind(kind="systemd")], host_kind="cron",
                       context=context)
    if other_host.bindings_skipped != 1:
        failures.append("a binding for another host was not counted as skipped")

    degraded = check([make()], [], host_kind="cron", context=context,
                     unreadable=["x"])
    if degraded.posture != "DEGRADED":
        failures.append("DEGRADED did not outrank DRIFTED")
    if degraded.exit_code != 1 or check([make(cadence={"kind": "manual"})], [],
                                        host_kind="cron").exit_code != 0:
        failures.append("exit codes do not follow the posture")

    try:
        parse_bindings({"schema": BINDINGS_SCHEMA, "as_of": "2026-09-06",
                        "host": "cron",
                        "entries": [{"charter_id": "a", "kind": "cron",
                                     "enabled": True, "cadance": "typo"}]})
        failures.append("the bindings reader accepted an undeclared field")
    except CharterError:
        pass
    try:
        parse_bindings({"schema": BINDINGS_SCHEMA, "as_of": "2026-09-06",
                        "host": "launchd", "entries": []})
        failures.append("the bindings reader accepted an undeclared host")
    except CharterError:
        pass

    if failures:
        return False, "conformance: " + "; ".join(failures)
    return True, ("conformance: 12 finding codes fire, DEGRADED outranks, "
                  "unchecked cadence is stated not assumed")
