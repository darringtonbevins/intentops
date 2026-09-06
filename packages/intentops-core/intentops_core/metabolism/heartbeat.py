"""The metabolism heartbeat: three alarms over a dated, on-disk history.

PURPOSE
    Answer, mechanically and with no model in the path, whether the metabolism
    is actually metabolising. The cadence
    (:mod:`intentops_core.metabolism.cadence`) grades a single RUN; this grades
    the SERIES, because the defect it exists for is invisible in any one run.

    The reference implementation this generalises from had two stages that
    reported ``Success`` over zero work for at least six days -- one behind an
    ImportError and an unrecognised CLI flag, one stamping its sub-checks
    without calling them. Every individual run looked fine. Only the shape of
    the series -- days of identical numbers -- said anything was wrong, and
    nothing was reading the series.

    THREE ALARMS, and each names the threshold it trades against
    (a detector that hides its operating point manufactures confidence):

      ``promotion_flatline``  the OUTPUT end is stuck: the crystallized count
                              has not moved across ``flatline_days``
                              consecutive DATES. The pipeline may be running
                              perfectly and promoting nothing.
      ``input_feed_dry``      the INPUT end is stuck: the absorbed count has not
                              moved across ``dry_days`` consecutive dates.
                              Separate from the flat-line on purpose -- a dry
                              feed and a stuck promoter need opposite repairs,
                              and a single "nothing happened" alarm would send
                              you to the wrong end of the pipe.
      ``registry_drift``      the INDEX has fallen behind the CORPUS: the
                              registry's declared highest crystallized id lags
                              the highest id actually on disk by more than
                              ``registry_lag_max``. A registry that stops being
                              written is the silent failure that leaves every
                              downstream reader looking at a frozen world.

    A reading is DEGRADED when a source could not be READ, and DEGRADED
    outranks every improvement: a source that leaves the population makes every
    count look better, which is exactly why nobody goes looking for it. An
    ABSENT directory on a newborn node is a different finding from an
    unreadable one and is recorded as such, never folded into either zero.

WRITE MODEL
    ``.intentops/metabolism/heartbeat.jsonl`` -- APPEND-ONLY JSONL under
    ``StoreLock``, state as a pure fold over the file. The append and the fold
    that computes the deltas run inside ONE lock hold, so two overlapping
    writers cannot both read history N and both write entry N+1 with the same
    deltas.

    This is a deliberate divergence from the reference implementation, which
    used a whole-file ``heartbeat.json`` read-modify-written by every caller --
    the shared-whiteboard shape that has cost that estate four separate
    data-loss incidents. An append-only journal cannot lose an earlier entry to
    a later writer at all.

    ``now`` is ALWAYS injected by the caller. No wall-clock read happens inside
    the logic, so a history replays deterministically and a test can construct
    a six-day flat-line without waiting six days.

BLIND SPOTS -- stated so a quiet heartbeat is not read as a healthy one
    * The counts are a pure function of the FILESYSTEM: files present in a
      directory. They say nothing about the quality, the freshness, or even the
      validity of what is in those files. A stage that writes empty candidates
      every night reads as perfectly healthy here.
    * ``registry_drift`` compares two NUMBERS. A registry that is current and
      wrong is invisible to it.
    * Alarms are grouped by DATE, not by run. Two runs on one date collapse to
      one dated point, so a cadence that runs hourly and stops for eight hours
      is not visible until the date rolls. That is the correct grain for a
      daily cadence and the wrong one for an hourly one; a node running hourly
      needs a different instrument, not a smaller threshold here.
    * A single dated entry can never raise a flat-line: a streak needs a prior
      point to be flat against. A brand-new node is therefore quiet, and that
      quiet is an absence of evidence, not evidence of health.
    * The alarms ROUTE, they never verdict. Nothing here blocks, rejects, or
      switches anything off.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..store_guard import StoreLock, lock_for

__all__ = [
    "Counts",
    "Reading",
    "Alarm",
    "COUNTABLES",
    "STORE_RELPATH",
    "DEFAULT_FLATLINE_DAYS",
    "DEFAULT_DRY_DAYS",
    "DEFAULT_REGISTRY_LAG_MAX",
    "POSTURES",
    "store_path",
    "collect_counts",
    "load_history",
    "append_reading",
    "check_alarms",
    "posture",
    "selftest",
    "main",
]

#: The four countables, one per cadence stage. Named here so a missing count
#: and a zero count are never the same value in the record.
COUNTABLES: Tuple[str, ...] = ("absorbed", "candidates", "crystallized",
                               "methods")

STORE_RELPATH = Path(".intentops") / "metabolism" / "heartbeat.jsonl"

#: Consecutive DATES with no movement before each alarm fires. Stated, because
#: a detector that hides its threshold is not reportable.
DEFAULT_FLATLINE_DAYS = 3
DEFAULT_DRY_DAYS = 5
#: How far the registry may lag the corpus before it counts as unwritten.
DEFAULT_REGISTRY_LAG_MAX = 10

POSTURES: Tuple[str, ...] = ("DEGRADED", "ATTENTION", "QUIET", "HEALTHY")

_CRYST_ID_RE = re.compile(r"CRYST-(\d+)")

#: Where each countable is read from, relative to the node root. A node that
#: keeps its corpus elsewhere passes explicit paths to :func:`collect_counts`;
#: nothing here guesses.
_DEFAULT_SOURCES: Dict[str, Path] = {
    "absorbed": Path(".intentops") / "metabolism" / "absorbed",
    "candidates": Path(".intentops") / "metabolism" / "candidates",
    "crystallized": Path(".intentops") / "metabolism" / "crystallized",
    "methods": Path(".intentops") / "metabolism" / "methods",
}
_DEFAULT_REGISTRY = Path(".intentops") / "metabolism" / "registry.yaml"


@dataclass
class Counts:
    """One on-disk reading: what was counted, and what could not be."""

    counts: Dict[str, int] = field(default_factory=dict)
    #: Directories that do not exist. On a newborn node this is the honest
    #: state and is NOT degradation.
    absent: List[str] = field(default_factory=list)
    #: Directories that exist and could not be read. This IS degradation.
    unreadable: List[str] = field(default_factory=list)
    registry_highest: Optional[int] = None
    corpus_highest: int = 0

    @property
    def degraded(self) -> bool:
        return bool(self.unreadable)

    def to_row(self) -> Dict[str, Any]:
        return {"counts": dict(self.counts), "absent": list(self.absent),
                "unreadable": list(self.unreadable),
                "registry_highest": self.registry_highest,
                "corpus_highest": self.corpus_highest}


@dataclass
class Alarm:
    """A routed finding. Never a verdict."""

    kind: str
    detail: str
    threshold: str

    def to_row(self) -> Dict[str, Any]:
        return {"kind": self.kind, "detail": self.detail,
                "threshold": self.threshold}


@dataclass
class Reading:
    """One appended heartbeat entry."""

    as_of: str
    date: str
    counts: Dict[str, int]
    deltas: Dict[str, int]
    absent: List[str] = field(default_factory=list)
    unreadable: List[str] = field(default_factory=list)
    registry_highest: Optional[int] = None
    corpus_highest: int = 0

    def to_row(self) -> Dict[str, Any]:
        return {"as_of": self.as_of, "date": self.date,
                "counts": dict(self.counts), "deltas": dict(self.deltas),
                "absent": list(self.absent), "unreadable": list(self.unreadable),
                "registry_highest": self.registry_highest,
                "corpus_highest": self.corpus_highest}


def store_path(node_root: Path | str) -> Path:
    return Path(node_root) / STORE_RELPATH


# ---------------------------------------------------------------------------
# counting -- a pure function of on-disk state
# ---------------------------------------------------------------------------


def _count_dir(path: Path) -> Tuple[Optional[int], Optional[str]]:
    """``(count, failure)``. Exactly one of the two is None."""
    if not path.exists():
        return None, "absent"
    if not path.is_dir():
        return None, "not-a-directory"
    try:
        return sum(1 for p in path.iterdir() if p.is_file()), None
    except OSError as exc:
        return None, f"unreadable ({exc})"


def _max_cryst_id(path: Path) -> int:
    best = 0
    try:
        names = [p.name for p in path.iterdir()]
    except OSError:
        return 0
    for name in names:
        for hit in _CRYST_ID_RE.findall(name):
            best = max(best, int(hit))
    return best


def collect_counts(node_root: Path | str,
                   sources: Optional[Mapping[str, Path]] = None,
                   registry: Optional[Path] = None) -> Counts:
    """Count what the metabolism has produced. Pure function of the filesystem.

    An ABSENT source is recorded as absent AND counted as zero -- both, not
    one. A newborn node with no corpus directory has honestly produced
    nothing, and the reading says which of the two reasons it is.
    """
    node_root = Path(node_root)
    resolved = {k: node_root / v for k, v in (sources or _DEFAULT_SOURCES).items()}
    declared_registry = Path(registry) if registry is not None else _DEFAULT_REGISTRY
    reg_path = (declared_registry if declared_registry.is_absolute()
                else node_root / declared_registry)

    out = Counts()
    for name in COUNTABLES:
        path = resolved.get(name)
        if path is None:
            out.counts[name] = 0
            out.absent.append(f"{name}: no source declared")
            continue
        count, failure = _count_dir(path)
        out.counts[name] = int(count or 0)
        if failure == "absent":
            out.absent.append(f"{name}: {path.as_posix()}")
        elif failure:
            out.unreadable.append(f"{name}: {path.as_posix()} {failure}")

    cryst_dir = resolved.get("crystallized")
    if cryst_dir is not None and cryst_dir.is_dir():
        out.corpus_highest = _max_cryst_id(cryst_dir)

    out.registry_highest = _read_registry_highest(reg_path, out)
    return out


def _read_registry_highest(path: Path, out: Counts) -> Optional[int]:
    """The registry's own declared highest id, or None with a reason recorded.

    A MISSING registry is absent (a newborn node has none). A registry that
    exists and does not parse is UNREADABLE -- never silently a zero, because a
    zero would read as "the registry is maximally behind" and fire an alarm for
    the wrong reason, or as "no drift" and fire none at all.
    """
    if not path.exists():
        out.absent.append(f"registry: {path.as_posix()}")
        return None
    try:
        import yaml  # noqa: PLC0415

        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 - the parser's error class varies
        out.unreadable.append(f"registry: {path.as_posix()} unreadable ({exc})")
        return None
    if not isinstance(doc, Mapping) or "highest_crystallized_id" not in doc:
        out.unreadable.append(
            f"registry: {path.as_posix()} declares no "
            "`highest_crystallized_id`; a missing load-bearing field is not a "
            "zero")
        return None
    try:
        return int(doc["highest_crystallized_id"])
    except (TypeError, ValueError):
        out.unreadable.append(
            f"registry: {path.as_posix()} `highest_crystallized_id` is not an "
            "integer")
        return None


# ---------------------------------------------------------------------------
# the store -- append-only JSONL, state as a pure fold
# ---------------------------------------------------------------------------


def load_history(node_root: Path | str) -> List[Dict[str, Any]]:
    """Fold the journal into the dated history. Unreadable lines are KEPT.

    A corrupt line is folded in as ``{"_corrupt": ...}`` rather than skipped:
    dropping it would shrink the denominator, and a shorter history makes every
    streak look shorter than it is.
    """
    path = store_path(node_root)
    if not path.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            rows.append({"_corrupt": line[:120], "error": str(exc)})
            continue
        rows.append(row if isinstance(row, dict) else {"_corrupt": line[:120]})
    return rows


def append_reading(node_root: Path | str, counts: Counts, now: str) -> Reading:
    """Append one dated reading, with deltas against the previous entry.

    The fold and the append happen inside ONE lock hold. A reader that folded
    outside the lock could compute its deltas against a history a concurrent
    writer was about to extend.
    """
    node_root = Path(node_root)
    path = store_path(node_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    date = str(now)[:10]

    with StoreLock(lock_for(path)):
        history = load_history(node_root)
        previous: Optional[Dict[str, Any]] = None
        for row in reversed(history):
            if "counts" in row:
                previous = row
                break
        prev_counts = dict(previous.get("counts") or {}) if previous else {}
        deltas = {name: int(counts.counts.get(name, 0))
                  - int(prev_counts.get(name, 0) or 0)
                  for name in COUNTABLES}
        reading = Reading(as_of=str(now), date=date,
                          counts=dict(counts.counts), deltas=deltas,
                          absent=list(counts.absent),
                          unreadable=list(counts.unreadable),
                          registry_highest=counts.registry_highest,
                          corpus_highest=counts.corpus_highest)
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(reading.to_row(), sort_keys=True) + "\n")
    return reading


# ---------------------------------------------------------------------------
# the three alarms
# ---------------------------------------------------------------------------


def _dated(history: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """One point per DATE, the last entry of each date, in date order."""
    by_date: Dict[str, Dict[str, Any]] = {}
    for row in history:
        if "_corrupt" in row or "date" not in row:
            continue
        by_date[str(row["date"])] = dict(row)
    return [by_date[d] for d in sorted(by_date)]


def _trailing_zero_dates(dated: Sequence[Mapping[str, Any]],
                         keys: Sequence[str]) -> int:
    """How many consecutive trailing dates moved none of ``keys``."""
    streak = 0
    for row in reversed(dated):
        deltas = row.get("deltas") or {}
        if any(int(deltas.get(k, 0) or 0) != 0 for k in keys):
            break
        streak += 1
    return streak


def check_alarms(history: Sequence[Mapping[str, Any]],
                 *,
                 flatline_days: int = DEFAULT_FLATLINE_DAYS,
                 dry_days: int = DEFAULT_DRY_DAYS,
                 registry_lag_max: int = DEFAULT_REGISTRY_LAG_MAX
                 ) -> List[Alarm]:
    """The three alarms, as a pure function of the dated history."""
    alarms: List[Alarm] = []
    dated = _dated(history)
    if not dated:
        return alarms

    promo = _trailing_zero_dates(dated, ("crystallized",))
    if promo >= flatline_days:
        alarms.append(Alarm(
            "promotion_flatline",
            f"crystallized count has not moved for {promo} consecutive dates",
            f"flatline_days={flatline_days}"))

    dry = _trailing_zero_dates(dated, ("absorbed",))
    if dry >= dry_days:
        alarms.append(Alarm(
            "input_feed_dry",
            f"absorbed count has not moved for {dry} consecutive dates",
            f"dry_days={dry_days}"))

    latest = dated[-1]
    highest = latest.get("registry_highest")
    corpus = int(latest.get("corpus_highest", 0) or 0)
    if highest is not None:
        lag = corpus - int(highest)
        if lag > registry_lag_max:
            alarms.append(Alarm(
                "registry_drift",
                f"the registry declares highest id {int(highest)} while the "
                f"corpus carries {corpus} -- a lag of {lag}",
                f"registry_lag_max={registry_lag_max}"))
    return alarms


def posture(history: Sequence[Mapping[str, Any]],
            **kwargs: Any) -> Tuple[str, List[Alarm]]:
    """``(posture, alarms)``. DEGRADED outranks every other reading.

    ``QUIET`` is its own word and is NOT ``HEALTHY``: a history too short to
    raise a streak has not passed a check, it has failed to reach one. Reading
    an absence of evidence as evidence of health is the whole defect this
    module exists for.
    """
    dated = _dated(history)
    alarms = check_alarms(history, **kwargs)
    if any(row.get("unreadable") for row in dated[-1:]):
        return "DEGRADED", alarms
    if alarms:
        return "ATTENTION", alarms
    if len(dated) < 2:
        return "QUIET", alarms
    return "HEALTHY", alarms


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


def _entry(date: str, counts: Mapping[str, int], deltas: Mapping[str, int],
           **extra: Any) -> Dict[str, Any]:
    row = {"as_of": date + "T00:00:00Z", "date": date,
           "counts": dict(counts), "deltas": dict(deltas),
           "absent": [], "unreadable": [], "registry_highest": None,
           "corpus_highest": 0}
    row.update(extra)
    return row


def selftest() -> Tuple[bool, str]:
    """Prove each alarm can fire, and that a quiet history stays quiet.

    A detector that has never fired is indistinguishable from a broken one, so
    every alarm below is CONSTRUCTED and observed, never asserted about.
    """
    import tempfile

    fired: List[str] = []
    failures: List[str] = []

    def expect(name: str, ok: bool) -> None:
        fired.append(name)
        if not ok:
            failures.append(name)

    zero = {k: 0 for k in COUNTABLES}

    # 1. a flat crystallized count fires the promotion flat-line
    flat = [_entry(f"2026-01-0{d}", {**zero, "crystallized": 4},
                   {**zero}) for d in range(1, 6)]
    kinds = {a.kind for a in check_alarms(flat)}
    expect("promotion-flatline-fires", "promotion_flatline" in kinds)

    # 2. a moving crystallized count does NOT fire it. BOTH ends move here:
    # a series where only the output moves would trip the dry-feed alarm at its
    # own longer threshold, which is the correct behaviour and the wrong
    # fixture for this check.
    moving = [_entry(f"2026-01-0{d}", {**zero, "crystallized": d,
                                       "absorbed": d},
                     {**zero, "crystallized": 1, "absorbed": 1})
              for d in range(1, 6)]
    kinds = {a.kind for a in check_alarms(moving)}
    expect("promotion-flatline-quiet-when-moving",
           "promotion_flatline" not in kinds)

    # 3. a flat absorbed count over the longer threshold fires the dry feed
    kinds = {a.kind for a in check_alarms(flat)}
    expect("input-feed-dry-fires", "input_feed_dry" in kinds)

    # 4. and does NOT fire below its own threshold, while the shorter
    #    flat-line threshold already has -- the two are independent
    short = flat[:3]
    kinds = {a.kind for a in check_alarms(short)}
    expect("dry-threshold-independent-of-flatline",
           "promotion_flatline" in kinds and "input_feed_dry" not in kinds)

    # 5. a registry behind the corpus fires drift
    drift = [_entry("2026-01-01", {**zero, "crystallized": 40},
                    {**zero, "crystallized": 1},
                    registry_highest=5, corpus_highest=99)]
    kinds = {a.kind for a in check_alarms(drift)}
    expect("registry-drift-fires", "registry_drift" in kinds)

    current = [_entry("2026-01-01", {**zero, "crystallized": 40},
                      {**zero, "crystallized": 1},
                      registry_highest=99, corpus_highest=99)]
    kinds = {a.kind for a in check_alarms(current)}
    expect("registry-drift-quiet-when-current", "registry_drift" not in kinds)

    # an ABSENT registry (None) never fires drift -- absent is not zero
    absent_reg = [_entry("2026-01-01", {**zero}, {**zero},
                         registry_highest=None, corpus_highest=99)]
    kinds = {a.kind for a in check_alarms(absent_reg)}
    expect("absent-registry-is-not-a-lag", "registry_drift" not in kinds)

    # 6. postures
    expect("empty-history-is-quiet", posture([])[0] == "QUIET")
    expect("short-history-is-quiet-not-healthy",
           posture(moving[:1])[0] == "QUIET")
    expect("moving-history-is-healthy", posture(moving)[0] == "HEALTHY")
    expect("alarms-render-attention", posture(flat)[0] == "ATTENTION")
    degraded = [dict(moving[-1], unreadable=["candidates: unreadable"])]
    expect("degraded-outranks-everything", posture(degraded)[0] == "DEGRADED")

    # 7. the store round-trips, appends, and computes deltas
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        counts = collect_counts(root)
        expect("newborn-counts-are-zero",
               all(v == 0 for v in counts.counts.values()))
        expect("newborn-sources-absent-not-unreadable",
               bool(counts.absent) and not counts.unreadable)

        r1 = append_reading(root, counts, "2026-01-01T00:00:00Z")
        expect("first-reading-has-zero-deltas",
               all(v == 0 for v in r1.deltas.values()))

        (root / ".intentops" / "metabolism" / "crystallized").mkdir(parents=True)
        (root / ".intentops" / "metabolism" / "crystallized"
         / "CRYST-007-a.md").write_text("x", encoding="utf-8")
        r2 = append_reading(root, collect_counts(root), "2026-01-02T00:00:00Z")
        expect("delta-is-computed-against-the-previous-entry",
               r2.deltas["crystallized"] == 1)
        expect("corpus-highest-is-read-from-ids", r2.corpus_highest == 7)
        expect("history-is-append-only", len(load_history(root)) == 2)

        # a corrupt line stays in the population
        with store_path(root).open("a", encoding="utf-8") as fh:
            fh.write("{not json\n")
        expect("corrupt-line-kept-in-denominator",
               len(load_history(root)) == 3
               and any("_corrupt" in r for r in load_history(root)))

        # an unparseable registry is unreadable, never a zero
        reg = root / ".intentops" / "metabolism" / "registry.yaml"
        reg.write_text("highest_crystallized_id: [:\n", encoding="utf-8")
        c = collect_counts(root)
        expect("unparseable-registry-is-degraded",
               c.degraded and c.registry_highest is None)
        reg.write_text("other: 1\n", encoding="utf-8")
        c = collect_counts(root)
        expect("registry-missing-its-field-is-degraded", c.degraded)

    report = (f"heartbeat selftest: {len(fired)} paths fired, "
              f"{len(failures)} failed"
              + (" -- " + ", ".join(failures) if failures else ""))
    return (not failures, report)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="the metabolism heartbeat: three alarms over the series")
    parser.add_argument("--node-root", default=".")
    parser.add_argument("--now", default=None,
                        help="ISO stamp for the appended reading; injected, "
                             "never read from the clock inside the logic")
    parser.add_argument("--append", action="store_true",
                        help="take a reading and append it")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        ok, report = selftest()
        print(report)
        return 0 if ok else 1

    root = Path(args.node_root)
    if args.append:
        now = args.now
        if not now:
            from datetime import datetime, timezone

            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        append_reading(root, collect_counts(root), now)

    history = load_history(root)
    verdict, alarms = posture(history)
    if args.json:
        print(json.dumps({"posture": verdict,
                          "entries": len(history),
                          "alarms": [a.to_row() for a in alarms]}, indent=2))
    else:
        print(f"metabolism heartbeat: {verdict} over {len(history)} entries")
        for alarm in alarms:
            print(f"  {alarm.kind}: {alarm.detail} [{alarm.threshold}]")
        if not alarms:
            print("  no alarms -- which over a short history means "
                  "'not enough evidence', not 'healthy'")
    return 0 if verdict in ("HEALTHY", "QUIET") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
