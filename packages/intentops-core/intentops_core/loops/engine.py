"""The loop engine: one tick of one charter, recorded as INTENT and nothing else.

PURPOSE
    Answer, for a single charter, the question a scheduler asks every time it
    fires: *may this loop run right now, and on what terms?* The answer is
    written to an append-only journal and returned. It is never acted on here.

    THERE IS NO EXECUTE PATH IN THIS SEED, AND THAT IS THE DESIGN. ``tick()``
    takes no ``execute=True``. It calls no model, opens no socket, spawns no
    process and reads no file outside the node root. A seed that shipped an
    unattended actor would ship it before any operator had reviewed a single
    charter -- so what ships is the part that decides, journals its decision,
    and stops. An adopting project wires an actor to the ``DRY-RUN`` verdict
    deliberately, as a reviewed act, with the charter's ``tier_ceiling`` and
    ``requires_evidence`` already in hand from the record this module wrote.

    FOUR REFUSALS, CHECKED IN THIS ORDER, AND THE ORDER MATTERS
      1. The node-wide HALT marker. Checked first, ahead of everything, so a
         stood-down node cannot be talked into a tick by any charter state.
      2. The charter's own kill switch file. Present means refuse, whatever
         ``enabled`` says -- the sentinel is the fast, local way to stop one
         loop without editing a reviewed config file.
      3. ``enabled: false``. Every charter is born here.
      4. A ``manual`` cadence with no explicit ``manual_run`` flag -- a manual
         charter that a scheduler fired is itself a finding, because nothing
         should have been scheduling it.

    A REFUSAL IS RECORDED, NEVER SILENT. Every outcome lands in the journal
    with its reason. A loop that has been off for three weeks and a loop that
    was never wired look identical if refusals are not written down, and the
    second is a much worse state than the first.

WRITE MODEL
    Append-only JSONL at ``<node_root>/.intentops/loops/ticks.jsonl``, one
    record per tick, written under a :class:`~intentops_core.store_guard.
    StoreLock` on the sibling lock file. State is a PURE FOLD over the journal
    -- nothing rewrites a line, nothing truncates the file, and a reader that
    wants "the last tick of charter X" folds forward rather than trusting a
    cached pointer. Two schedulers firing the same charter at once therefore
    produce two honest records rather than one lost one.

BLIND SPOTS
    * The journal records what the engine DECIDED. It is not evidence that a
      loop did anything, because in this seed no loop does anything.
    * ``now`` defaults to the wall clock. Two nodes disagreeing about the time
      produce records that sort wrongly against each other, and nothing here
      detects that.
    * Budget share is COPIED into the record, never enforced. Nothing in this
      module can see what a tick would have spent; enforcement belongs to
      whatever an adopting project wires to the DRY-RUN verdict.
    * The kill-switch check is a file-existence probe at one instant. A switch
      created one millisecond after the probe is not seen by that tick.
    * A journal on a full or read-only disk raises. That is deliberate: a tick
      whose decision could not be recorded did not happen, and swallowing the
      write error would produce an unattended loop with no record at all.
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

from ..store_guard import StoreLock, lock_for
from .charters import Charter, CharterError

__all__ = [
    "TickRecord",
    "TICK_VERDICTS",
    "JOURNAL_RELPATH",
    "HALT_MARKER_RELPATH",
    "RECORD_SCHEMA",
    "tick",
    "journal_path",
    "read_journal",
    "last_tick",
    "selftest",
]

#: Where the journal lives, relative to the node root.
JOURNAL_RELPATH = Path(".intentops") / "loops" / "ticks.jsonl"

#: The node-wide stand-down marker, checked ahead of every other gate.
HALT_MARKER_RELPATH = Path(".intentops") / "halt.marker"

RECORD_SCHEMA = "loop-tick/v1"

#: Closed vocabulary. There is no third outcome and no ``EXECUTED``.
TICK_VERDICTS: Tuple[str, ...] = ("DRY-RUN", "REFUSED")


@dataclass(frozen=True)
class TickRecord:
    """One decision about one charter at one instant."""

    charter_id: str
    verdict: str
    reason: str
    at: str
    tier_ceiling: str
    budget_share: float
    kill_switch: str
    requires_evidence: Tuple[str, ...] = ()
    cadence: str = ""
    mode: str = "dry-run"
    schema: str = RECORD_SCHEMA

    def to_row(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "at": self.at,
            "charter_id": self.charter_id,
            "verdict": self.verdict,
            "reason": self.reason,
            "mode": self.mode,
            "tier_ceiling": self.tier_ceiling,
            "budget_share": self.budget_share,
            "kill_switch": self.kill_switch,
            "cadence": self.cadence,
            "requires_evidence": list(self.requires_evidence),
        }

    @property
    def ran(self) -> bool:
        """True when the tick was permitted -- in this seed, permitted to
        record an intent and nothing more."""
        return self.verdict == "DRY-RUN"


def journal_path(node_root: Path | str) -> Path:
    return Path(node_root) / JOURNAL_RELPATH


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append(record: TickRecord, node_root: Path | str) -> Path:
    """Append one line under the store lock. Raises rather than swallowing."""
    path = journal_path(node_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record.to_row(), sort_keys=True) + "\n"
    with StoreLock(lock_for(path)):
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
    return path


def _decide(charter: Charter, node_root: Path | str, *,
            manual_run: bool) -> Tuple[str, str]:
    """The whole decision, in refusal order. Pure: probes files, nothing else."""
    root = Path(node_root)
    if (root / HALT_MARKER_RELPATH).exists():
        return "REFUSED", ("the node is stood down: "
                           f"{HALT_MARKER_RELPATH.as_posix()} exists. Removing "
                           "it is the operator's own manual act")
    switch = charter.kill_switch_path(root)
    if switch.exists():
        return "REFUSED", (f"the kill switch {charter.kill_switch} exists; "
                           "delete it to let this charter tick again")
    if not charter.enabled:
        return "REFUSED", ("the charter is disabled. Every charter is born "
                           "disabled; switching one on is a reviewed, dated "
                           "operator act")
    if not charter.cadence.scheduled and not manual_run:
        return "REFUSED", ("the cadence is manual and this tick did not "
                           "declare manual_run -- a manual charter that a "
                           "scheduler fired is a finding: nothing should have "
                           "been scheduling it")
    return "DRY-RUN", ("intent recorded; this seed's engine calls no model and "
                       "takes no action. Wiring an actor to this verdict is a "
                       "reviewed act")


def tick(charter: Charter, node_root: Path | str, *,
         now: str | None = None, manual_run: bool = False,
         record: bool = True) -> TickRecord:
    """Run ONE tick of ONE charter as a dry run and journal the decision.

    ``record=False`` returns the decision without writing -- for a caller that
    wants to preview a verdict. It is not a quiet mode: the default is to
    write, because a tick nobody recorded is a tick nobody can audit.
    """
    if not isinstance(charter, Charter):
        raise CharterError(
            f"tick() was handed a {type(charter).__name__}, not a Charter",
            "load charters through intentops_core.loops.charters.load_charters "
            "so every field has been validated before an unattended loop reads "
            "it")
    verdict, reason = _decide(charter, node_root, manual_run=manual_run)
    result = TickRecord(
        charter_id=charter.id,
        verdict=verdict,
        reason=reason,
        at=now or _now(),
        tier_ceiling=charter.tier_ceiling,
        budget_share=charter.budget_share,
        kill_switch=charter.kill_switch,
        requires_evidence=charter.requires_evidence,
        cadence=charter.cadence.describe(),
    )
    if record:
        _append(result, node_root)
    return result


def read_journal(node_root: Path | str) -> List[Dict[str, Any]]:
    """Fold the journal. An unreadable LINE stays in the population as a row
    marked unreadable -- dropping it would make every count look better."""
    path = journal_path(node_root)
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for number, raw in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            rows.append({"schema": RECORD_SCHEMA, "unreadable": True,
                         "line": number, "reason": str(exc)})
            continue
        if not isinstance(row, Mapping):
            rows.append({"schema": RECORD_SCHEMA, "unreadable": True,
                         "line": number, "reason": "not a JSON object"})
            continue
        rows.append(dict(row))
    return rows


def last_tick(node_root: Path | str, charter_id: str) -> Dict[str, Any] | None:
    """The most recent record for one charter, by fold order (never a cached
    pointer -- a pointer is a second writer)."""
    found = None
    for row in read_journal(node_root):
        if row.get("charter_id") == charter_id:
            found = row
    return found


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------


def selftest() -> Tuple[bool, str]:
    """Prove every refusal fires, that a permitted tick records, and that the
    journal is append-only in practice and not just in prose."""
    import tempfile

    from .charters import SCHEMA, parse_charters

    failures: List[str] = []

    def check(label: str, cond: bool) -> None:
        if not cond:
            failures.append(label)

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

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        enabled = make()

        first = tick(enabled, root, now="2026-09-06T00:00:00Z")
        check(f"an enabled charter did not read DRY-RUN (got {first.verdict})",
              first.verdict == "DRY-RUN" and first.ran)

        off = tick(make(enabled=False), root, now="2026-09-06T00:01:00Z")
        check("a disabled charter was not refused", off.verdict == "REFUSED")
        check("the disabled refusal did not name the reason",
              "disabled" in off.reason)

        switch = enabled.kill_switch_path(root)
        switch.parent.mkdir(parents=True, exist_ok=True)
        switch.write_text("off", encoding="utf-8")
        killed = tick(enabled, root, now="2026-09-06T00:02:00Z")
        check("the kill switch did not refuse an enabled charter",
              killed.verdict == "REFUSED" and "kill switch" in killed.reason)
        switch.unlink()

        marker = root / HALT_MARKER_RELPATH
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("stood down", encoding="utf-8")
        halted = tick(enabled, root, now="2026-09-06T00:03:00Z")
        check("the node-wide halt marker did not refuse",
              halted.verdict == "REFUSED" and "stood down" in halted.reason)
        marker.unlink()

        manual = make(cadence={"kind": "manual"})
        scheduled_manual = tick(manual, root, now="2026-09-06T00:04:00Z")
        check("a scheduler-fired manual charter was not refused",
              scheduled_manual.verdict == "REFUSED")
        asked_manual = tick(manual, root, now="2026-09-06T00:05:00Z",
                            manual_run=True)
        check("an explicit manual run was refused",
              asked_manual.verdict == "DRY-RUN")

        rows = read_journal(root)
        check(f"the journal did not keep all 6 records (got {len(rows)})",
              len(rows) == 6)
        check("the fold lost the last record for the charter",
              (last_tick(root, "loop-example") or {}).get("at")
              == "2026-09-06T00:05:00Z")

        with journal_path(root).open("a", encoding="utf-8") as handle:
            handle.write("{not json}\n")
        rows = read_journal(root)
        check("an unreadable line left the population instead of staying in it",
              len(rows) == 7 and rows[-1].get("unreadable") is True)

        preview = tick(enabled, root, now="2026-09-06T00:06:00Z", record=False)
        check("record=False still wrote to the journal",
              preview.verdict == "DRY-RUN" and len(read_journal(root)) == 7)

        try:
            tick("loop-example", root)  # type: ignore[arg-type]
            failures.append("tick() accepted a bare string as a charter")
        except CharterError:
            pass

    if failures:
        return False, "engine: " + "; ".join(failures)
    return True, ("engine: 4 refusals fire in order, a permitted tick records, "
                  "the journal folds and keeps unreadable lines in the count")
