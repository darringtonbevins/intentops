"""Responsiveness -- when a belief's falsifier tripped, did anyone ANSWER?

PURPOSE
    ``still_true`` NOTICES: it raises a question whenever a belief's evidence
    moved, its subject vanished, it carried no as-of, or two rulings conflict.
    Nothing measured whether those questions were then answered -- so a node
    with a perfect currency record and a 0% answer rate would have read
    healthy. This is the correction half of notice-and-correct, and it is the
    declared consumer of the ``responsiveness`` organ created at birth.

    What counts as an ANSWER is an event in the ordering journal
    (``.intentops/wisdom/orderings.jsonl``) at or after the question was first
    raised:

        supersede   the ruling was amended or revoked, with lineage
        reaffirm    the re-ask ended in CONTINUE, WITH A REASON. Before that
                    event existed, "continue" and "nobody looked" were the
                    same absence in the journal
        reopen      the re-ask STARTED -- counted separately, because in
                    progress is not answered

    A question that disappears from the latest still-true run with NO event is
    a SILENT CLOSE: continue-by-default, and the number this instrument exists
    to surface.

THE TIMIDITY PAIR
    Once an answer rate is a published number, the cheapest way to improve it
    is to have fewer contestable rulings. So the decision volume -- rulings
    recorded per ISO week -- is printed BESIDE the rate, as a pair, and the
    instrument never verdicts on the pair. There is no comparison body here, so
    a fall in volume has no reference class to be judged against.

WRITE MODEL
    ``.intentops/responsiveness/ledger.jsonl`` -- append-only under
    ``StoreLock``, state as a pure fold over the readings. Nothing is ever
    edited in place. It reads two other stores and writes to neither.

BLIND SPOTS
    - Only RULING subjects have an answer channel. A question on a briefing or
      an anchor row is answered by editing the document, which this cannot see;
      it is reported as ``no_channel``, stays in the population, and is never
      folded into the ruling shares or counted as unanswered.
    - A question raised minutes ago and not yet answered is OPEN, not a
      failure. A fresh node reads near zero responsiveness and that is a fact,
      not a siren; the posture acts on the trend against the high-water mark.
    - ``still_true`` truncates its recorded question ids at 200 per run, so a
      run with more questions hides its tail from this reader too.
    - It reads still-true RUNS. If nobody runs still_true, nothing is raised
      and nothing can be answered -- a quiet ledger here can mean a quiet
      instrument upstream, which is why the run count is printed beside
      everything else.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..store_guard import StoreLock, lock_for
from . import still_true

__all__ = [
    "LEDGER_RELPATH",
    "ORDERING_RELPATH",
    "POSTURES",
    "ANSWER_EVENTS",
    "QuestionRecord",
    "Reading",
    "read_runs",
    "read_events",
    "take_reading",
    "posture",
    "decision_volume",
    "append_reading",
    "load_history",
    "render",
    "selftest",
    "main",
]

LEDGER_RELPATH = Path(".intentops") / "responsiveness" / "ledger.jsonl"
ORDERING_RELPATH = Path(".intentops") / "wisdom" / "orderings.jsonl"

#: Closed. ``NOT-YET-ARMED`` is a real state and not a pass.
POSTURES: Tuple[str, ...] = ("NOT-YET-ARMED", "HEALTHY", "ATTENTION", "DEGRADED")

#: Journal events that END a re-ask, and the one that only starts it.
ANSWER_EVENTS: Tuple[str, ...] = ("supersede", "reaffirm")
REOPEN_EVENT = "reopen"

#: Operating point: a detector states its threshold and the error it trades.
#: A fall of more than this many POINTS below the high-water mark is ATTENTION.
FALL_TOLERANCE_POINTS = 10.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _to_date(value: Any) -> Optional[date]:
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _iso_week(value: Any) -> Optional[str]:
    d = _to_date(value)
    if d is None:
        return None
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


# --------------------------------------------------------------------------
# atoms
# --------------------------------------------------------------------------


@dataclass
class QuestionRecord:
    """One question, from when it was first raised to whatever answered it."""

    key: str                 # "<FALSIFIER>:<belief id>", as still_true records it
    falsifier: str
    subject: str
    first_raised: str        # the run's as_of
    last_raised: str
    has_channel: bool        # only ruling subjects have an answer channel
    answer: str = ""         # "" | supersede | reaffirm | reopen
    answered_at: str = ""

    @property
    def state(self) -> str:
        if not self.has_channel:
            return "no_channel"
        if self.answer in ANSWER_EVENTS:
            return "answered"
        if self.answer == REOPEN_EVENT:
            return "reopened"
        return "open" if self.still_open else "silent_close"

    still_open: bool = True


@dataclass
class Reading:
    as_of: str
    runs: int = 0
    latest_run: str = ""
    raised: int = 0
    answered_amend: int = 0
    answered_continue: int = 0
    reopened: int = 0
    open: int = 0
    silent_close: int = 0
    no_channel: int = 0
    rulings: int = 0
    decision_volume: Dict[str, int] = field(default_factory=dict)
    unreadable: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    blind_spots: List[str] = field(default_factory=list)

    @property
    def channelled(self) -> int:
        """The denominator the rates are taken over: questions with a channel."""
        return self.raised - self.no_channel

    @property
    def answered(self) -> int:
        return self.answered_amend + self.answered_continue

    @property
    def responsiveness(self) -> Optional[float]:
        """``None`` when the denominator is zero -- never 0.0, which would read
        as a measured failure rather than as nothing to measure."""
        if self.channelled <= 0:
            return None
        return self.answered / self.channelled

    @property
    def silent_close_share(self) -> Optional[float]:
        if self.channelled <= 0:
            return None
        return self.silent_close / self.channelled


# --------------------------------------------------------------------------
# readers
# --------------------------------------------------------------------------


def read_runs(ledger: Path, unreadable: List[str]) -> List[Dict[str, Any]]:
    """The still-true ledger, oldest first. A corrupt line is COUNTED."""
    ledger = Path(ledger)
    if not ledger.exists():
        unreadable.append(f"still-true ledger missing: {ledger}")
        return []
    runs: List[Dict[str, Any]] = []
    corrupt = 0
    for line in ledger.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            corrupt += 1
            continue
        if isinstance(row, dict):
            runs.append(row)
        else:
            corrupt += 1
    if corrupt:
        unreadable.append(
            f"{corrupt} corrupt line(s) in the still-true ledger -- they stay "
            "in the denominator and the reading is not trustworthy")
    return runs


def read_events(journal: Path, unreadable: List[str]
                ) -> Tuple[Dict[str, List[Tuple[str, str]]], List[Dict[str, Any]]]:
    """``({ruling id: [(event, at)]}, [rule rows])`` from the ordering journal.

    The journal is read directly rather than through the store's fold because
    the fold keeps only the CURRENT state of each ruling, and this instrument
    needs the events themselves -- when the answer happened, not what it left
    behind.
    """
    journal = Path(journal)
    events: Dict[str, List[Tuple[str, str]]] = {}
    rules: List[Dict[str, Any]] = []
    if not journal.exists():
        unreadable.append(f"ordering journal missing: {journal}")
        return events, rules
    corrupt = 0
    for line in journal.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            corrupt += 1
            continue
        if not isinstance(row, dict):
            corrupt += 1
            continue
        event = str(row.get("event") or "")
        rid = str(row.get("id") or "")
        if not rid:
            continue
        if event == "rule":
            rules.append(row)
            continue
        if event in ANSWER_EVENTS or event == REOPEN_EVENT:
            events.setdefault(rid, []).append((event, str(row.get("at") or "")))
    if corrupt:
        unreadable.append(
            f"{corrupt} corrupt line(s) in the ordering journal -- counted, "
            "never skipped")
    return events, rules


def decision_volume(rules: Sequence[Dict[str, Any]], weeks: int = 6
                    ) -> Dict[str, int]:
    """Rulings per ISO week -- the timidity pair, published, never graded."""
    counts: Dict[str, int] = {}
    for row in rules:
        week = _iso_week(row.get("date") or row.get("at"))
        if week:
            counts[week] = counts.get(week, 0) + 1
    return dict(sorted(counts.items())[-weeks:])


# --------------------------------------------------------------------------
# the reading
# --------------------------------------------------------------------------


def _subject_of(key: str) -> Tuple[str, str]:
    """``"<FALSIFIER>:<belief id>"`` -> ``(falsifier, subject)``."""
    falsifier, _, subject = key.partition(":")
    if falsifier not in still_true.QUESTION_KINDS:
        return "", key
    return falsifier, subject


def take_reading(*, node_root: Path,
                 still_true_ledger: Optional[Path] = None,
                 ordering_journal: Optional[Path] = None) -> Reading:
    """Fold every still-true run against the ordering journal.

    Sources that cannot be read stay IN the denominator: an instrument that
    quietly leaves the count makes the number better-looking, which is exactly
    why nobody goes looking.
    """
    node_root = Path(node_root)
    reading = Reading(as_of=_now())
    reading.blind_spots = [
        "only ruling subjects have an answer channel; a question on a "
        "briefing or an anchor row is no_channel, never unanswered",
        "a question raised minutes ago is OPEN, not a failure",
        "still_true records at most 200 question ids per run",
        "a quiet ledger here can mean a quiet instrument upstream -- the run "
        "count is printed for that reason",
    ]
    st_ledger = Path(still_true_ledger) if still_true_ledger else \
        node_root / still_true.LEDGER_RELPATH
    journal = Path(ordering_journal) if ordering_journal else \
        node_root / ORDERING_RELPATH

    runs = read_runs(st_ledger, reading.unreadable)
    events, rules = read_events(journal, reading.unreadable)
    reading.runs = len(runs)
    reading.rulings = len(rules)
    reading.decision_volume = decision_volume(rules)
    if not runs:
        reading.notes.append(
            "no still-true run has been recorded, so nothing has been raised "
            "and nothing could have been answered")
        return reading
    reading.latest_run = str(runs[-1].get("as_of") or "")
    latest_keys = {str(k) for k in (runs[-1].get("question_ids") or [])}

    records: Dict[str, QuestionRecord] = {}
    for run in runs:
        run_at = str(run.get("as_of") or "")
        for raw in run.get("question_ids") or []:
            key = str(raw)
            falsifier, subject = _subject_of(key)
            rec = records.get(key)
            if rec is None:
                rec = QuestionRecord(
                    key=key, falsifier=falsifier, subject=subject,
                    first_raised=run_at, last_raised=run_at,
                    has_channel=subject in events or _is_ruling_id(subject, rules))
                records[key] = rec
            rec.last_raised = run_at
            # `has_channel` can only ever become true: a subject whose journal
            # entry appears later still had a channel when it was raised.
            if not rec.has_channel:
                rec.has_channel = subject in events or _is_ruling_id(subject, rules)

    for rec in records.values():
        rec.still_open = rec.key in latest_keys
        if not rec.has_channel:
            continue
        first = rec.first_raised[:10]
        for event, at in sorted(events.get(rec.subject, ()), key=lambda e: e[1]):
            if at[:10] < first:
                continue                      # answered a question not yet asked
            if event in ANSWER_EVENTS:
                rec.answer, rec.answered_at = event, at
                break
            if event == REOPEN_EVENT and not rec.answer:
                rec.answer, rec.answered_at = event, at

    reading.raised = len(records)
    for rec in records.values():
        state = rec.state
        if state == "no_channel":
            reading.no_channel += 1
        elif state == "answered":
            if rec.answer == "supersede":
                reading.answered_amend += 1
            else:
                reading.answered_continue += 1
        elif state == "reopened":
            reading.reopened += 1
        elif state == "open":
            reading.open += 1
        else:
            reading.silent_close += 1
    return reading


def _is_ruling_id(subject: str, rules: Sequence[Dict[str, Any]]) -> bool:
    """A subject has an answer channel iff it names a ruling this store knows.

    A CONFLICT question names a PAIR (``a vs b``); it has a channel iff either
    half is a known ruling, since either can be superseded to resolve it.
    """
    known = {str(r.get("id") or "") for r in rules}
    if subject in known:
        return True
    if " vs " in subject:
        return any(half.strip() in known for half in subject.split(" vs "))
    return False


# --------------------------------------------------------------------------
# posture -- high-water mark, never the previous record
# --------------------------------------------------------------------------


def posture(reading: Reading, history: Sequence[dict]) -> Tuple[str, List[str]]:
    notes: List[str] = []
    if reading.unreadable:
        notes.append(
            f"{len(reading.unreadable)} source(s) could not be read -- they "
            "stay in the denominator: " + "; ".join(reading.unreadable))
        return "DEGRADED", notes

    if reading.channelled <= 0:
        notes.append(
            f"{reading.raised} question(s) raised, {reading.no_channel} of them "
            "on subjects with no answer channel; no rate is defined over an "
            "empty denominator, and reporting 0% would read as a measured "
            "failure rather than as nothing to measure")
        return "NOT-YET-ARMED", notes

    rate = (reading.responsiveness or 0.0) * 100.0
    marks = [float(rec.get("responsiveness_pct") or 0.0) for rec in history
             if rec.get("responsiveness_pct") is not None]
    high_water = max(marks) if marks else rate
    notes.append(
        f"responsiveness {reading.answered}/{reading.channelled} "
        f"({rate:.1f}%) against a high-water mark of {high_water:.1f}% over "
        f"{len(marks)} prior reading(s); this one becomes the baseline")
    if reading.silent_close:
        notes.append(
            f"{reading.silent_close} silent close(s): a question stopped being "
            "raised with no event in the journal -- continue-by-default")
        return "ATTENTION", notes
    if rate + FALL_TOLERANCE_POINTS < high_water:
        notes.append(
            f"responsiveness fell more than {FALL_TOLERANCE_POINTS:g} points "
            "below the high-water mark")
        return "ATTENTION", notes
    return "HEALTHY", notes


# --------------------------------------------------------------------------
# the ledger
# --------------------------------------------------------------------------


def default_ledger(node_root: Path) -> Path:
    return Path(node_root) / LEDGER_RELPATH


def load_history(path: Path) -> List[dict]:
    path = Path(path)
    if not path.exists():
        return []
    out: List[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (ValueError, TypeError):
            out.append({"corrupt": True})    # counted, never skipped
            continue
        out.append(rec if isinstance(rec, dict) else {"corrupt": True})
    return out


def append_reading(reading: Reading, verdict: str, path: Path) -> None:
    rate = reading.responsiveness
    rec: Dict[str, Any] = {
        "as_of": reading.as_of,
        "verdict": verdict,
        "runs": reading.runs,
        "latest_run": reading.latest_run,
        "raised": reading.raised,
        "channelled": reading.channelled,
        "answered_amend": reading.answered_amend,
        "answered_continue": reading.answered_continue,
        "reopened": reading.reopened,
        "open": reading.open,
        "silent_close": reading.silent_close,
        "no_channel": reading.no_channel,
        # `None`, never 0.0, when the denominator is empty -- a rate that was
        # never defined must not fold into a high-water mark as a real zero.
        "responsiveness_pct": None if rate is None else round(rate * 100.0, 2),
        "rulings": reading.rulings,
        "decision_volume": reading.decision_volume,
        "unreadable": reading.unreadable,
        "notes": reading.notes,
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with StoreLock(lock_for(path)):
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")


# --------------------------------------------------------------------------
# render
# --------------------------------------------------------------------------


def render(reading: Reading, verdict: str, notes: Sequence[str]) -> str:
    rate = reading.responsiveness
    share = reading.silent_close_share
    L = [f"RESPONSIVENESS -- did a tripped falsifier get an answer? "
         f"as of {reading.as_of}", ""]
    L.append(f"  still-true runs read              {reading.runs}"
             + (f" (latest {reading.latest_run})" if reading.latest_run else ""))
    L.append(f"  questions raised                  {reading.raised}")
    L.append(f"    with an answer channel          {reading.channelled}")
    L.append(f"    no channel (not rulings)        {reading.no_channel}")
    L.append(f"  answered by supersede             {reading.answered_amend}")
    L.append(f"  answered by reaffirm              {reading.answered_continue}")
    L.append(f"  re-ask started (reopen)           {reading.reopened}")
    L.append(f"  still open                        {reading.open}")
    L.append(f"  SILENT CLOSES                     {reading.silent_close}")
    L.append("")
    L.append("  responsiveness                    "
             + ("undefined (empty denominator)" if rate is None
                else f"{reading.answered}/{reading.channelled} = {rate * 100:.1f}%"))
    L.append("  silent-close share                "
             + ("undefined (empty denominator)" if share is None
                else f"{reading.silent_close}/{reading.channelled} = {share * 100:.1f}%"))
    L.append("")
    L.append("  PAIR (published, never a verdict): decision volume by ISO week")
    if reading.decision_volume:
        for week, count in reading.decision_volume.items():
            L.append(f"    {week}  {count}")
    else:
        L.append("    (no dated rulings recorded)")
    L.append(f"    total rulings in the journal    {reading.rulings}")
    L.append("")
    L.append(verdict)
    for note in list(notes) + list(reading.notes):
        L.append(f"  - {note}")
    L.append("  blind spots: " + "; ".join(reading.blind_spots))
    return "\n".join(L)


# --------------------------------------------------------------------------
# selftest -- every posture and every state must be able to fire
# --------------------------------------------------------------------------


def _run_row(as_of: str, keys: Sequence[str]) -> str:
    return json.dumps({"as_of": as_of, "verdict": "CURRENT",
                       "question_ids": list(keys)}) + "\n"


def selftest() -> Tuple[bool, str]:
    failures: List[str] = []

    def check(label: str, cond: bool) -> None:
        if not cond:
            failures.append(label)

    with tempfile.TemporaryDirectory() as td:
        node = Path(td)
        st = node / still_true.LEDGER_RELPATH
        jr = node / ORDERING_RELPATH
        st.parent.mkdir(parents=True, exist_ok=True)
        jr.parent.mkdir(parents=True, exist_ok=True)

        # -- 1. nothing recorded at all -> NOT-YET-ARMED, never HEALTHY -----
        st.write_text("", encoding="utf-8")
        jr.write_text("", encoding="utf-8")
        r = take_reading(node_root=node)
        v, _ = posture(r, [])
        check(f"an empty pair of stores read {v}, not NOT-YET-ARMED",
              v == "NOT-YET-ARMED")
        check("an undefined rate was reported as a number",
              r.responsiveness is None)

        # -- 2. a question with no channel stays in the population ----------
        st.write_text(_run_row("2026-09-06T00:00:00+00:00",
                               ["UNDATED:docs/brief.md"]), encoding="utf-8")
        r = take_reading(node_root=node)
        check(f"expected 1 raised, got {r.raised}", r.raised == 1)
        check(f"expected 1 no_channel, got {r.no_channel}", r.no_channel == 1)
        v, _ = posture(r, [])
        check("a population of only no-channel questions was graded",
              v == "NOT-YET-ARMED")

        # -- 3. open, then answered by supersede -> HEALTHY -----------------
        jr.write_text(json.dumps({
            "event": "rule", "id": "ORD-1", "date": "2026-09-01"}) + "\n",
            encoding="utf-8")
        st.write_text(_run_row("2026-09-06T00:00:00+00:00",
                               ["EVIDENCE-MOVED:ORD-1"]), encoding="utf-8")
        r = take_reading(node_root=node)
        check(f"expected 1 open, got open={r.open} channelled={r.channelled}",
              r.open == 1 and r.channelled == 1)
        with jr.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"event": "supersede", "id": "ORD-1",
                                 "superseded_by": "ORD-2",
                                 "at": "2026-09-07T00:00:00+00:00"}) + "\n")
        st.write_text(_run_row("2026-09-06T00:00:00+00:00",
                               ["EVIDENCE-MOVED:ORD-1"])
                      + _run_row("2026-09-08T00:00:00+00:00", []),
                      encoding="utf-8")
        r = take_reading(node_root=node)
        check(f"supersede did not answer: amend={r.answered_amend}",
              r.answered_amend == 1 and r.silent_close == 0)
        v, notes = posture(r, [])
        check(f"a fully answered reading read {v}, not HEALTHY", v == "HEALTHY")

        # -- 4. reaffirm answers; an event BEFORE the question does not -----
        jr.write_text(
            json.dumps({"event": "rule", "id": "ORD-3", "date": "2026-09-01"}) + "\n"
            + json.dumps({"event": "reaffirm", "id": "ORD-3",
                          "reason": "still holds",
                          "at": "2026-09-10T00:00:00+00:00"}) + "\n"
            + json.dumps({"event": "rule", "id": "ORD-4", "date": "2026-09-01"}) + "\n"
            + json.dumps({"event": "reaffirm", "id": "ORD-4", "reason": "old",
                          "at": "2026-09-01T00:00:00+00:00"}) + "\n",
            encoding="utf-8")
        st.write_text(_run_row("2026-09-09T00:00:00+00:00",
                               ["UNDATED:ORD-3", "UNDATED:ORD-4"])
                      + _run_row("2026-09-11T00:00:00+00:00", []),
                      encoding="utf-8")
        r = take_reading(node_root=node)
        check(f"reaffirm did not answer: continue={r.answered_continue}",
              r.answered_continue == 1)
        check(f"a pre-dated event answered a later question: "
              f"silent={r.silent_close}", r.silent_close == 1)
        v, _ = posture(r, [])
        check(f"a silent close read {v}, not ATTENTION", v == "ATTENTION")

        # -- 5. reopen is counted separately, never as an answer ------------
        jr.write_text(
            json.dumps({"event": "rule", "id": "ORD-5", "date": "2026-09-01"}) + "\n"
            + json.dumps({"event": "reopen", "id": "ORD-5", "reason": "re-ask",
                          "at": "2026-09-10T00:00:00+00:00"}) + "\n",
            encoding="utf-8")
        st.write_text(_run_row("2026-09-09T00:00:00+00:00", ["UNDATED:ORD-5"])
                      + _run_row("2026-09-11T00:00:00+00:00", []),
                      encoding="utf-8")
        r = take_reading(node_root=node)
        check(f"reopen was folded into answered: reopened={r.reopened} "
              f"answered={r.answered}", r.reopened == 1 and r.answered == 0)

        # -- 6. a CONFLICT pair resolves to a channel ----------------------
        st.write_text(_run_row("2026-09-09T00:00:00+00:00",
                               ["CONFLICT:ORD-5 vs ORD-9"]), encoding="utf-8")
        r = take_reading(node_root=node)
        check("a CONFLICT pair naming a known ruling read as no_channel",
              r.no_channel == 0 and r.channelled == 1)

        # -- 7. a corrupt line DEGRADES rather than disappearing -----------
        st.write_text(_run_row("2026-09-09T00:00:00+00:00", ["UNDATED:ORD-5"])
                      + "{not json\n", encoding="utf-8")
        r = take_reading(node_root=node)
        v, _ = posture(r, [])
        check(f"a corrupt still-true line read {v}, not DEGRADED",
              v == "DEGRADED")

        # -- 8. a fall below the high-water mark is ATTENTION ---------------
        st.write_text(_run_row("2026-09-09T00:00:00+00:00",
                               ["UNDATED:ORD-3", "UNDATED:ORD-5"])
                      + _run_row("2026-09-12T00:00:00+00:00",
                                 ["UNDATED:ORD-3", "UNDATED:ORD-5"]),
                      encoding="utf-8")
        jr.write_text(
            json.dumps({"event": "rule", "id": "ORD-3", "date": "2026-09-01"}) + "\n"
            + json.dumps({"event": "rule", "id": "ORD-5", "date": "2026-09-01"}) + "\n"
            + json.dumps({"event": "reaffirm", "id": "ORD-3", "reason": "ok",
                          "at": "2026-09-10T00:00:00+00:00"}) + "\n",
            encoding="utf-8")
        r = take_reading(node_root=node)
        v, _ = posture(r, [{"responsiveness_pct": 100.0}])
        check(f"a 50% reading against a 100% high-water read {v}, "
              "not ATTENTION", v == "ATTENTION")

        # -- 9. the ledger round-trips, and an undefined rate stays None ----
        ledger = default_ledger(node)
        empty = Reading(as_of=_now())
        append_reading(empty, "NOT-YET-ARMED", ledger)
        rows = load_history(ledger)
        check("the ledger did not round-trip", len(rows) == 1)
        check("an undefined rate was written as 0.0",
              rows[0].get("responsiveness_pct") is None)
        check("the timidity pair is absent from the record",
              "decision_volume" in rows[0])

    ok = not failures
    return ok, ("all four postures fired, supersede/reaffirm/reopen were told "
                "apart, a silent close and a pre-dated event were caught, a "
                "corrupt line degraded, and an undefined rate stayed undefined"
                if ok else "; ".join(failures))


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Responsiveness -- did a tripped falsifier get an answer?")
    ap.add_argument("--node-root", default=".")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-write", action="store_true",
                    help="do not append to the ledger")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        ok, msg = selftest()
        print(f"{'PASS' if ok else 'FAIL'} responsiveness selftest: {msg}")
        return 0 if ok else 1

    node_root = Path(args.node_root).resolve()
    reading = take_reading(node_root=node_root)
    ledger = default_ledger(node_root)
    verdict, notes = posture(reading, load_history(ledger))
    if not args.no_write:
        append_reading(reading, verdict, ledger)
    if args.json:
        payload = {"verdict": verdict, "notes": list(notes), **asdict(reading)}
        payload["responsiveness"] = reading.responsiveness
        payload["channelled"] = reading.channelled
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(render(reading, verdict, notes))
    return 0 if verdict != "DEGRADED" else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
