"""Coverage -- is the classification still complete, and is the corpus searchable?

PURPOSE
    Two questions nothing else in the node asks, and both of them have been
    answered wrong by silence before:

    * **Ring coverage.** A one-time backfill stamped a corpus, reported
      "untagged = 0", and nothing watched it afterwards. Months later
      classification had fallen to a quarter of the corpus while KEY PRESENCE
      -- the flattering proxy -- still read two thirds. So this module reports
      THREE numbers and never sums them: ``key_present`` (a ring key exists),
      ``classified`` (the value resolves ABOVE quarantine -- the headline),
      and ``invalid`` (a key is present and unmappable: covered under
      key-presence, quarantined at the gate, pure illusion).
    * **Corpus coverage.** Gathering and embedding are separate steps, so a
      corpus can sit complete on disk and mostly absent from the index while
      the gatherer reports success and search returns plausible hits from the
      fraction that landed. The unit is the FILE, never the chunk: chunk
      counts vary with document length, so a chunk ratio cannot tell "half the
      corpus is missing" from "the documents are short".

POSTURE, NOT A VERDICT
    ``COVERED / ATTENTION / DECAYING / DEGRADED / NOT-YET-ARMED``, over an
    append-only ledger, against a HIGH-WATER MARK rather than the previous
    record. A previous-record baseline re-grants the full threshold budget
    every run, so a small nightly fall is invisible forever and the
    instrument gets blinder the more often it runs.

    **DEGRADED outranks improvement.** The day a large collection errors or
    leaves the measured set, its unclassified rows leave the totals and
    coverage "improves". Any read error, or any collection that was measured
    and no longer is, forces DEGRADED and suppresses the improvement reading.

    **NOT-YET-ARMED is not COVERED.** A node at birth has zero rows. Zero of
    zero is not a hundred percent; it is a question nobody has asked yet.

WRITE MODEL
    Append-only JSONL under ``StoreLock``, state as a pure fold, one logical
    writer, readers never rewrite:
    ``.intentops/knowledge/ring-coverage.jsonl`` and
    ``.intentops/knowledge/corpus-coverage.jsonl``. Both are created empty at
    birth (``genesis/organs.py``), because an empty ledger is a true statement
    about a node that has not measured yet and a missing one is a field
    nobody read.

BLIND SPOTS
    - Ring coverage reads what a store reports. A store that omits rows from
      its own iteration is invisible here, which is why any read error is
      DEGRADED rather than a smaller denominator.
    - Corpus coverage joins on a file's BASENAME as recorded in the row's
      ``source`` metadata. A gatherer that stores a different basename than it
      wrote reads as 0%; none in this seed does.
    - Corpus coverage measures PRESENCE, not freshness. A file embedded from
      old content counts as covered. Staleness is a content-digest question
      and is deliberately a different instrument.
    - A directory holding only file types this module cannot enumerate is
      reported UNMEASURED, never 0%. Reading an instrument's blindness as an
      empty corpus is how a re-absorb gets triggered over nothing.
    - Nothing here re-stamps a row or reclassifies anything. Changing a
      classification is a reviewed act, never a side effect of measuring one.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import (Any, Dict, List, Mapping, Optional, Sequence,
                    Tuple)

from . import KnowledgeHalt
from ..store_guard import StoreLock, lock_for
from .rings import (RING_GATE_ENV, RING_META_KEY, Ring,
                    ring_from_metadata, ring_gate_mode)

__all__ = [
    "POSTURES",
    "CORPUS_STATES",
    "DROP_PP",
    "GROW_ROWS",
    "Reading",
    "CorpusRow",
    "ring_coverage",
    "corpus_coverage",
    "posture",
    "load_history",
    "append_reading",
    "default_ring_ledger",
    "default_corpus_ledger",
    "render",
    "selftest",
    "main",
]

#: Closed vocabulary. DEGRADED outranks every other reading.
POSTURES: Tuple[str, ...] = (
    "COVERED", "ATTENTION", "DECAYING", "DEGRADED", "NOT-YET-ARMED",
)

#: Per-corpus states. UNMEASURED is not zero, and EMPTY is not a failure.
CORPUS_STATES: Tuple[str, ...] = (
    "COMPLETE", "PARTIAL", "EXCESS", "EMPTY", "UNMEASURED",
)

#: A fall of this many percentage points below the HIGH-WATER MARK is DECAYING.
DROP_PP = 2.0
#: This many more unclassified rows than the low-water mark is DECAYING.
GROW_ROWS = 100

#: What corpus coverage can enumerate on disk. Anything else is UNMEASURED.
MEASURABLE_SUFFIXES: Tuple[str, ...] = (".md", ".txt", ".rst")

#: A corpus at or above this share of files embedded reads COMPLETE.
COMPLETE_THRESHOLD = 0.99


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# ring coverage
# ---------------------------------------------------------------------------


@dataclass
class Reading:
    """One ring-coverage measurement. Three numbers, never summed."""

    as_of: str
    rows: int
    key_present: int
    classified: int
    invalid: int
    gate_mode: str
    measured: List[str] = field(default_factory=list)
    per_collection: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def unclassified(self) -> int:
        return self.rows - self.classified

    @property
    def classified_pct(self) -> Optional[float]:
        return None if not self.rows else 100.0 * self.classified / self.rows

    @property
    def key_present_pct(self) -> Optional[float]:
        return None if not self.rows else 100.0 * self.key_present / self.rows

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["unclassified"] = self.unclassified
        d["classified_pct"] = self.classified_pct
        d["key_present_pct"] = self.key_present_pct
        return d


def _gate_mode(errors: List[str]) -> str:
    """The ring gate's mode, or ``"unreadable"`` recorded as an error.

    :func:`ring_gate_mode` HALTS on a present-but-undeclared value, which is
    right for a WRITE gate and wrong for an instrument: a misconfigured
    environment variable must not stop the estate from being measured. It is
    carried as an error instead, which forces DEGRADED -- the finding stays
    in the population rather than taking the whole Reading down with it.
    """
    try:
        return ring_gate_mode()
    except KnowledgeHalt as exc:
        errors.append(f"the ring gate mode is unreadable: {exc}")
        return "unreadable"


def ring_coverage(store: Any, *, now: Optional[str] = None) -> Reading:
    """Measure ring coverage over a store exposing ``collections()``/``rows()``.

    Every per-collection read is guarded: a collection that raises is recorded
    as an ERROR and its rows leave the totals for this run -- which is right
    per run, and is exactly why :func:`posture` forces DEGRADED rather than
    reading the smaller denominator as an improvement.
    """
    errors: List[str] = []
    measured: List[str] = []
    per_collection: List[Dict[str, Any]] = []
    totals = {"rows": 0, "key_present": 0, "classified": 0, "invalid": 0}

    mode = _gate_mode(errors)
    try:
        names = list(store.collections())
    except Exception as exc:  # noqa: BLE001 - a finding, not a crash
        return Reading(as_of=now or _now(), rows=0, key_present=0, classified=0,
                       invalid=0, gate_mode=mode,
                       errors=errors + [f"cannot list collections: "
                                        f"{type(exc).__name__}: {exc}"])

    for name in sorted(names):
        # The COUNTING runs inside the guard too, not just the read. A row
        # whose metadata is not a mapping used to raise out of this function
        # and take the whole Reading with it -- an instrument that crashes
        # produces no record at all, which is a refusal that left the
        # population rather than one graded DEGRADED inside it.
        try:
            rows = list(store.rows(name))
            counts = {"rows": len(rows), "key_present": 0, "classified": 0,
                      "invalid": 0, "quarantined": 0}
            for row in rows:
                meta = getattr(row, "metadata", None)
                if meta is None:
                    meta = {}
                if not isinstance(meta, Mapping):
                    raise TypeError(
                        f"row metadata is {type(meta).__name__}, not a "
                        "mapping; this collection cannot be classified")
                raw = meta.get(RING_META_KEY)
                has_key = raw is not None
                resolved = ring_from_metadata(meta)
                if has_key:
                    counts["key_present"] += 1
                    if (resolved is Ring.QUARANTINE
                            and str(raw).strip() != Ring.QUARANTINE.value):
                        # a key is present and unmappable: covered under the
                        # proxy, quarantined at the gate
                        counts["invalid"] += 1
                if resolved is Ring.QUARANTINE:
                    counts["quarantined"] += 1
                else:
                    counts["classified"] += 1
        except Exception as exc:  # noqa: BLE001 - a finding, not a crash
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            continue
        measured.append(name)
        per_collection.append({"collection": name, **counts})
        for key in totals:
            totals[key] += counts[key]

    return Reading(as_of=now or _now(), gate_mode=mode,
                   measured=measured, per_collection=per_collection,
                   errors=errors, **totals)


def posture(reading: Reading, history: Sequence[Mapping[str, Any]],
            ) -> Tuple[str, List[str]]:
    """COVERED / ATTENTION / DECAYING / DEGRADED / NOT-YET-ARMED, and why.

    Order matters and is the whole design: DEGRADED is decided FIRST, because
    every trend below it is unreadable once the measured set has moved.
    """
    notes: List[str] = []
    clean = [h for h in history if h.get("verdict") != "DEGRADED"]

    if reading.errors:
        notes.extend(reading.errors)
        return "DEGRADED", notes
    if any(h.get("verdict") == "LOST" for h in history):
        notes.append("the ledger holds unreadable records; the history is torn")
        return "DEGRADED", notes
    if clean:
        previous = set(clean[-1].get("measured") or [])
        gone = sorted(previous - set(reading.measured))
        if gone:
            notes.append(
                "collections left the measured set since the last reading: "
                + ", ".join(gone)
                + " -- their rows left the totals, so any improvement here is "
                  "unreadable")
            return "DEGRADED", notes

    if reading.rows == 0:
        notes.append("no rows: a node that has not absorbed anything has "
                     "nothing to classify, and zero of zero is not complete")
        return "NOT-YET-ARMED", notes

    pct = reading.classified_pct or 0.0
    marks = [h["classified_pct"] for h in clean
             if isinstance(h.get("classified_pct"), (int, float))]
    unclassified_marks = [h["unclassified"] for h in clean
                          if isinstance(h.get("unclassified"), int)]
    high_water = max(marks) if marks else None
    low_water = min(unclassified_marks) if unclassified_marks else None

    if high_water is not None and pct <= high_water - DROP_PP:
        notes.append(f"classified {pct:.1f}% is {high_water - pct:.1f}pp below "
                     f"the high-water mark of {high_water:.1f}%")
        return "DECAYING", notes
    if low_water is not None and reading.unclassified >= low_water + GROW_ROWS:
        notes.append(f"unclassified rows grew to {reading.unclassified} from a "
                     f"low-water mark of {low_water}")
        return "DECAYING", notes

    if reading.invalid:
        notes.append(f"{reading.invalid} rows carry an unmappable ring value: "
                     "counted as covered by key presence, quarantined at the "
                     "gate")
    if reading.unclassified:
        notes.append(f"{reading.unclassified} of {reading.rows} rows are "
                     "unclassified (missing key and literal Q resolve "
                     "identically at the gate)")
    if reading.gate_mode != "enforce":
        notes.append(f"the ring gate is in {reading.gate_mode!r}: while it is "
                     "not enforcing, classification protects nothing at the "
                     "gate")
    return ("ATTENTION" if notes else "COVERED"), notes


# ---------------------------------------------------------------------------
# corpus coverage
# ---------------------------------------------------------------------------


@dataclass
class CorpusRow:
    """One corpus directory: files on disk against distinct files indexed."""

    corpus: str
    files: int
    embedded: int
    state: str
    note: str = ""

    @property
    def pct(self) -> Optional[float]:
        return None if not self.files else 100.0 * self.embedded / self.files

    def line(self) -> str:
        pct = "n/a" if self.pct is None else f"{self.pct:5.1f}%"
        return (f"  {self.state:<10} {pct}  {self.corpus} "
                f"({self.embedded}/{self.files} files)"
                + (f" -- {self.note}" if self.note else ""))


def _indexed_sources(store: Any, collection: Optional[str]) -> Tuple[set, List[str]]:
    """Distinct ``metadata['source']`` values present in the store."""
    sources: set = set()
    errors: List[str] = []
    try:
        names = ([collection] if collection is not None
                 else list(store.collections()))
    except Exception as exc:  # noqa: BLE001
        return sources, [f"cannot list collections: {type(exc).__name__}: {exc}"]
    for name in names:
        try:
            for row in store.rows(name):
                meta = getattr(row, "metadata", None) or {}
                value = meta.get("source")
                if value:
                    sources.add(str(value))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
    return sources, errors


def corpus_coverage(corpus_root: Path | str, store: Any, *,
                    collection: Optional[str] = None,
                    ) -> Tuple[List[CorpusRow], List[str]]:
    """Per-corpus-directory coverage. Returns ``(rows, errors)``.

    UNMEASURED is a real answer: a directory holding only file types this
    module cannot enumerate is reported as such, never as zero. A glob that
    cannot see a corpus must not report it as missing.
    """
    root = Path(corpus_root)
    indexed, errors = _indexed_sources(store, collection)
    rows: List[CorpusRow] = []
    if not root.is_dir():
        return rows, errors + [f"corpus root is not a directory: {root}"]

    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        all_files = [p for p in directory.rglob("*") if p.is_file()]
        measurable = [p for p in all_files
                      if p.suffix.lower() in MEASURABLE_SUFFIXES]
        if not all_files:
            rows.append(CorpusRow(directory.name, 0, 0, "EMPTY",
                                  "no files gathered yet"))
            continue
        if not measurable:
            rows.append(CorpusRow(
                directory.name, 0, 0, "UNMEASURED",
                f"{len(all_files)} files, none of them "
                + "/".join(MEASURABLE_SUFFIXES)
                + " -- this instrument cannot enumerate them, which is not "
                  "the same claim as zero"))
            continue
        present = sum(1 for p in measurable if p.name in indexed)
        if present > len(measurable):  # pragma: no cover - defensive
            state = "EXCESS"
        elif present >= len(measurable) * COMPLETE_THRESHOLD:
            state = "COMPLETE"
        else:
            state = "PARTIAL"
        rows.append(CorpusRow(directory.name, len(measurable), present, state,
                              "" if state != "PARTIAL" else
                              "gathered but not searchable"))
    return rows, errors


# ---------------------------------------------------------------------------
# the ledgers
# ---------------------------------------------------------------------------


def default_ring_ledger(node_root: Path | str) -> Path:
    return Path(node_root) / ".intentops" / "knowledge" / "ring-coverage.jsonl"


def default_corpus_ledger(node_root: Path | str) -> Path:
    return Path(node_root) / ".intentops" / "knowledge" / "corpus-coverage.jsonl"


def load_history(path: Path | str) -> List[Dict[str, Any]]:
    """Read the ledger as a pure fold. An unreadable line becomes a LOST record.

    A corrupt line is never skipped: it enters the history as
    ``{"verdict": "LOST"}``, which :func:`posture` reads as DEGRADED. Skipping
    it would quietly shrink the history and make the trend look cleaner than
    the store is.
    """
    p = Path(path)
    if not p.is_file():
        return []
    records: List[Dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            doc = json.loads(line)
        except json.JSONDecodeError:
            records.append({"verdict": "LOST", "note": "unreadable ledger line"})
            continue
        records.append(doc if isinstance(doc, dict)
                       else {"verdict": "LOST", "note": "record is not a mapping"})
    return records


def append_reading(record: Mapping[str, Any], verdict: str,
                   path: Path | str) -> Dict[str, Any]:
    """Append one graded record under the store lock. Append-only, always."""
    if verdict not in POSTURES:
        raise ValueError(f"undeclared posture {verdict!r}; use one of "
                         + ", ".join(POSTURES))
    doc = {**dict(record), "verdict": verdict}
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with StoreLock(lock_for(p)):
        with p.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(doc, sort_keys=True) + "\n")
    return doc


def render(reading: Reading, verdict: str, notes: Sequence[str],
           corpus_rows: Sequence[CorpusRow] = ()) -> str:
    """The three numbers, side by side, never summed."""
    pct = reading.classified_pct
    key = reading.key_present_pct
    head = (f"ring coverage: {verdict} -- classified "
            f"{'n/a' if pct is None else f'{pct:.1f}%'} "
            f"({reading.classified}/{reading.rows}), key present "
            f"{'n/a' if key is None else f'{key:.1f}%'} "
            f"({reading.key_present}), invalid {reading.invalid}; "
            f"gate {reading.gate_mode}")
    lines = [head]
    lines.extend(f"  - {n}" for n in notes)
    if corpus_rows:
        lines.append("corpus coverage:")
        lines.extend(row.line() for row in corpus_rows)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


class _FakeRow:
    def __init__(self, metadata: Dict[str, Any]) -> None:
        self.metadata = metadata


class _FakeStore:
    """A row source with a switchable fault, so DEGRADED can be made to fire."""

    def __init__(self, data: Dict[str, List[Dict[str, Any]]],
                 broken: Sequence[str] = ()) -> None:
        self.data = data
        self.broken = set(broken)

    def collections(self) -> List[str]:
        return sorted(self.data)

    def rows(self, name: str) -> List[_FakeRow]:
        if name in self.broken:
            raise RuntimeError("collection unreadable")
        return [_FakeRow(m) for m in self.data[name]]


def selftest() -> Tuple[bool, str]:
    """Prove every posture path fires, including the ones that must outrank."""
    import tempfile

    failures: List[str] = []
    fired: List[str] = []

    def check(label: str, cond: bool) -> None:
        (fired if cond else failures).append(label)

    # 1. the three numbers, and the invalid row that inflates the proxy
    store = _FakeStore({"knowledge": [
        {RING_META_KEY: "R2"},          # classified, key present
        {RING_META_KEY: "Q"},           # key present, NOT classified
        {RING_META_KEY: "R9"},          # key present, invalid, NOT classified
        {"source_type": "x"},           # no key at all
    ]})
    r = ring_coverage(store)
    check("rows-counted", r.rows == 4)
    check("classified-is-the-headline", r.classified == 1)
    check("key-presence-is-the-flattering-proxy", r.key_present == 3)
    check("invalid-counted-separately", r.invalid == 1)
    check("three-numbers-are-never-summed",
          r.classified + r.key_present + r.invalid != r.rows)
    check("unclassified-is-the-complement", r.unclassified == 3)

    # 2. a node at birth is NOT-YET-ARMED, not COVERED
    empty = ring_coverage(_FakeStore({"knowledge": []}))
    v, notes = posture(empty, [])
    check("zero-rows-is-not-yet-armed", v == "NOT-YET-ARMED" and bool(notes))
    check("zero-of-zero-has-no-percentage", empty.classified_pct is None)

    # 3. fully classified and enforcing reads COVERED; observing reads ATTENTION
    full = ring_coverage(_FakeStore({"k": [{RING_META_KEY: "R2"}]}))
    full.gate_mode = "enforce"
    v, notes = posture(full, [])
    check("clean-and-enforcing-is-covered", v == "COVERED" and not notes)
    full.gate_mode = "observe"
    v, notes = posture(full, [])
    check("not-enforcing-is-attention",
          v == "ATTENTION" and any("not enforcing" in n for n in notes))

    # 4. DECAYING against the HIGH-WATER MARK, not the previous record
    history = [{"verdict": "COVERED", "classified_pct": 90.0, "unclassified": 10,
                "measured": ["k"]},
               {"verdict": "ATTENTION", "classified_pct": 89.0,
                "unclassified": 11, "measured": ["k"]}]
    now = ring_coverage(_FakeStore({"k": [{RING_META_KEY: "R2"}] * 87
                                    + [{}] * 13}))
    now.gate_mode = "enforce"
    v, notes = posture(now, history)
    check("a-walk-down-from-the-high-water-mark-is-decaying",
          v == "DECAYING" and any("high-water" in n for n in notes))
    v, _ = posture(now, [{"verdict": "COVERED", "classified_pct": 88.0,
                          "unclassified": 12, "measured": ["k"]}])
    check("a-small-fall-from-the-previous-record-alone-is-not-decaying",
          v != "DECAYING")

    # 5. DEGRADED outranks improvement, three ways
    broken = ring_coverage(_FakeStore({"k": [{RING_META_KEY: "R2"}]},
                                      broken=["k"]))
    v, notes = posture(broken, [])
    check("a-read-error-is-degraded", v == "DEGRADED" and bool(notes))
    shrunk = ring_coverage(_FakeStore({"k": [{RING_META_KEY: "R2"}]}))
    v, notes = posture(shrunk, [{"verdict": "ATTENTION", "classified_pct": 50.0,
                                 "unclassified": 500, "measured": ["k", "big"]}])
    check("a-collection-leaving-the-set-is-degraded-not-an-improvement",
          v == "DEGRADED" and any("left the measured set" in n for n in notes))
    v, _ = posture(shrunk, [{"verdict": "LOST"}])
    check("an-unreadable-ledger-line-is-degraded", v == "DEGRADED")

    class _BadMetaRow:
        metadata = "not-a-mapping"

    class _BadMetaStore:
        def collections(self):
            return ["k"]

        def rows(self, name):
            return [_BadMetaRow()]

    bad = ring_coverage(_BadMetaStore())
    v, notes = posture(bad, [])
    check("non-mapping-metadata-is-degraded-not-a-crash",
          v == "DEGRADED" and bad.rows == 0
          and any("not a mapping" in e for e in bad.errors))
    unreadable: List[str] = []
    import os as _os
    _prev = _os.environ.get(RING_GATE_ENV)
    _os.environ[RING_GATE_ENV] = "enforcce"
    try:
        mode = _gate_mode(unreadable)
        typo = ring_coverage(_FakeStore({"k": [{RING_META_KEY: "R2"}]}))
    finally:
        if _prev is None:
            _os.environ.pop(RING_GATE_ENV, None)
        else:
            _os.environ[RING_GATE_ENV] = _prev
    check("a-typo-in-the-gate-mode-is-an-error-not-a-silent-observe",
          mode == "unreadable" and bool(unreadable)
          and typo.gate_mode == "unreadable"
          and posture(typo, [])[0] == "DEGRADED")

    # 6. the ledger: append-only, corrupt lines stay in the population
    with tempfile.TemporaryDirectory() as td:
        ledger = Path(td) / "ring-coverage.jsonl"
        append_reading(now.to_dict(), "DECAYING", ledger)
        append_reading(full.to_dict(), "ATTENTION", ledger)
        check("ledger-appends", len(load_history(ledger)) == 2)
        with ledger.open("a", encoding="utf-8") as fh:
            fh.write("{not json\n")
        hist = load_history(ledger)
        check("a-corrupt-line-becomes-a-LOST-record-not-a-gap",
              len(hist) == 3 and hist[-1]["verdict"] == "LOST")
        try:
            append_reading({}, "FINE", ledger)
        except ValueError:
            fired.append("undeclared-posture-refused")
        else:
            failures.append("undeclared-posture-refused")

        # 7. corpus coverage: COMPLETE / PARTIAL / EMPTY / UNMEASURED
        root = Path(td) / "absorbed"
        (root / "docs").mkdir(parents=True)
        (root / "docs" / "a.md").write_text("a", encoding="utf-8")
        (root / "docs" / "b.md").write_text("b", encoding="utf-8")
        (root / "binaries").mkdir()
        (root / "binaries" / "x.bin").write_text("x", encoding="utf-8")
        (root / "nothing").mkdir()
        (root / "whole").mkdir()
        (root / "whole" / "c.md").write_text("c", encoding="utf-8")
        store2 = _FakeStore({"k": [{"source": "a.md"}, {"source": "c.md"}]})
        rows, errors = corpus_coverage(root, store2)
        states = {row.corpus: row.state for row in rows}
        check("half-a-corpus-is-partial", states["docs"] == "PARTIAL")
        check("a-whole-corpus-is-complete", states["whole"] == "COMPLETE")
        check("an-empty-directory-is-empty", states["nothing"] == "EMPTY")
        check("unenumerable-files-are-unmeasured-not-zero",
              states["binaries"] == "UNMEASURED")
        check("unmeasured-carries-its-reason",
              any("not the same claim as zero" in row.note for row in rows))
        check("corpus-states-are-closed",
              set(states.values()) <= set(CORPUS_STATES) and not errors)
        missing_rows, missing_errors = corpus_coverage(root / "ghost", store2)
        check("a-missing-corpus-root-is-an-error-not-an-empty-reading",
              not missing_rows and bool(missing_errors))

    check("postures-are-closed", set(POSTURES) == {
        "COVERED", "ATTENTION", "DECAYING", "DEGRADED", "NOT-YET-ARMED"})
    check("render-shows-all-three-numbers",
          "classified" in render(now, "DECAYING", notes)
          and "key present" in render(now, "DECAYING", notes)
          and "invalid" in render(now, "DECAYING", notes))

    report = (f"coverage selftest: {len(fired)} paths fired, "
              f"{len(failures)} failed"
              + (" -- " + ", ".join(failures) if failures else ""))
    return (not failures, report)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="ring coverage and corpus coverage")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--node-root", default=".",
                    help="the node root holding .intentops/")
    ap.add_argument("--dsn", default="",
                    help="connection string for the node's own vector store. "
                         "Required: without it there is no store to measure "
                         "and the command reports UNMEASURED rather than "
                         "grading an empty one it built itself")
    ap.add_argument("--json", action="store_true", help="emit the raw reading")
    args = ap.parse_args(argv)
    if args.selftest:
        ok, report = selftest()
        print(report)
        return 0 if ok else 1

    # A store must be WIRED before anything is measured. The first build
    # measured a freshly-constructed NullVectorStore here and printed
    # "NOT-YET-ARMED -- a node that has not absorbed anything has nothing to
    # classify", exit 0, on EVERY node including a fully populated one: the
    # instrument's own blindness rendered as a clean posture, which is the
    # denominator failure this whole module exists to refuse. UNMEASURED is
    # not zero.
    if not args.dsn:
        print("\n".join((
            "UNMEASURED: no store is wired to this CLI.",
            "  This command measures the store you point it at, and you "
            "pointed it at nothing.",
            "  A NullVectorStore built here would read NOT-YET-ARMED on "
            "every node, populated or not,",
            "  which is the instrument's blindness wearing a posture's "
            "clothes.",
            "  remedy: pass --dsn <connection string> for this node's own "
            "database, or call",
            "  ring_coverage(store) from the node's own instrument, with the "
            "store it actually writes to.",
        )))
        return 2

    from .collections import PostgresVectorStore

    try:
        store: Any = PostgresVectorStore(args.dsn)
        reading = ring_coverage(store)
    except KnowledgeHalt as exc:
        print(f"UNMEASURED: {exc}")
        return 2
    verdict, notes = posture(reading, load_history(
        default_ring_ledger(args.node_root)))
    if args.json:
        print(json.dumps({**reading.to_dict(), "verdict": verdict,
                          "notes": notes}, indent=2))
    else:
        print(render(reading, verdict, notes))
    return 0 if verdict in ("COVERED", "NOT-YET-ARMED") else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
