"""Still True -- the belief-currency instrument.

PURPOSE. *That nothing a node holds outlives its truth: every belief it carries
knows when it was last true and what would make it false, so that when the world
moves beneath it the node feels the ground shift before it acts, not after.*

Process state decays in hours and a freshness rule covers it. Rulings, anchor
rows and briefings decay in WEEKS, and most systems have a rule for their
staleness and no sense for it. This module reads BELIEF CARRIERS and, for each,
two things beside the belief: **when it was last true** (an as-of) and **what
would make it false** (a falsifier). It then reads the falsifiers against live
state and surfaces the ones that have tripped -- as QUESTIONS, never answers.

Nothing here auto-supersedes, and nothing here fabricates a way back. A carrier
whose author stated no ``reopens_when`` is reported as owing one; inventing one
would manufacture exactly the false confidence the instrument exists to prevent.

CARRIERS (v1), each generic and configured rather than hard-coded:

* **records** -- an append-only JSONL store of dated rulings
  (``id``, ``date``, ``evidence[]``, ``instrument``, ``reopens_when``,
  ``status``). Subjects are the path-like tokens of ``evidence`` and
  ``instrument``.
* **dated carriers** -- any list of text or markdown files declared in a
  carriers config, each read in one of two declared modes:

  ``sections``   each ``## heading`` block carrying an ``[OBSERVED <date>]``
                 stamp is one belief; the as-of is the LATEST stamp in the
                 block (a row re-observed is a row re-dated). Unstamped blocks
                 are OUTSIDE the population -- see blind spot 1.
  ``document``   the whole file is one belief; the as-of is the latest
                 ``[OBSERVED <date>]`` anywhere in it, or an ``as_of:``
                 front-matter scalar.

  In both modes the subjects are the backticked file paths the text names.
  ``mode`` has NO DEFAULT: a carrier entry that omits it HALTS, because a
  carrier read in the wrong mode produces a confident wrong population.

FALSIFIERS, all deterministic, no model in the read path:

``UNDATED``        the carrier has no parseable as-of: a belief with no
                   half-life, which cannot be re-asked on time.
``EVIDENCE-MOVED`` a subject the belief rests on was COMMITTED after the
                   belief's as-of. The belief may still be true; the failure
                   this closes is carrying it past the day it stopped being
                   true with nobody asking.
``SUBJECT-GONE``   a subject the belief names no longer exists on disk.
``CONFLICT``       two active beliefs disagree. Delegated entirely to an
                   injected ``conflicts_fn`` -- this module never decides what
                   a disagreement is.

POSTURE, over an append-only ledger against a LOW-WATER MARK of open questions
(a debt metric's honest mark is its best-ever, never the previous record):

``DEGRADED``  a source could not be read. Outranks everything: an unreadable
              source stays IN the denominator and never reads as a clean bill.
``DECAYING``  open questions exceed the low-water mark by more than
              ``DECAY_TOLERANCE``: beliefs go stale faster than they are re-asked.
``ATTENTION`` questions are open and owed a human.
``CURRENT``   no open questions.

WRITE MODEL (declared at birth). Append-only JSONL at
``<node>/.intentops/still-true/ledger.jsonl``; one writer appends under a
kernel-released ``StoreLock``; state is a pure fold. Readings are never
rewritten, and a corrupt line is COUNTED (``{"corrupt": true}``) rather than
skipped, because a skipped line silently understates the low-water mark.

ROUTE, NEVER VERDICT. Exit 0 always in census mode; ``--strict`` exits 2 when
questions are open (2 = something inside the window), never 1.

BLIND SPOTS.

1. A ``sections`` block with no ``[OBSERVED]`` stamp carries beliefs with no
   as-of at all and is invisible here: this instrument sees only what is
   already dated.
2. Untracked subjects (node-local stores) have no commit history and are
   counted ``unwatchable``; modification time is a rewrite signature, not
   evidence of movement, so using it would fabricate movement.
3. Movement is COMMIT-level. A cosmetic commit trips ``EVIDENCE-MOVED``, and a
   belief that was wrong from birth never trips anything here -- that is the
   grounded-signal instruments' job, not this module's.
4. A prose ``reopens_when`` cannot be evaluated. It is carried on the question
   as the thing to re-ask, never decided.
5. Date granularity is the DAY: a subject committed the same day as the as-of
   does not count as moved.
6. Without a ``conflicts_fn`` the CONFLICT falsifier cannot fire at all. That
   absence is reported in the reading's notes, not hidden.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

try:  # pragma: no cover - import shim
    from intentops_core.store_guard import StoreLock, lock_for
except ImportError:  # pragma: no cover
    StoreLock = None  # type: ignore[assignment]
    lock_for = None  # type: ignore[assignment]

__all__ = [
    "Belief", "Question", "Reading", "CarrierSpec", "CarrierSpecError",
    "load_carrier_specs", "read_records", "read_carrier", "evaluate",
    "take_reading", "posture", "append_reading", "load_history", "render",
    "selftest", "main", "KINDS", "QUESTION_KINDS", "POSTURES",
]

KINDS = ("ruling", "carrier")
QUESTION_KINDS = ("UNDATED", "EVIDENCE-MOVED", "SUBJECT-GONE", "CONFLICT")
POSTURES = ("CURRENT", "ATTENTION", "DECAYING", "DEGRADED")
CARRIER_MODES = ("sections", "document")

# operating point: a detector states its threshold
DECAY_TOLERANCE = 3          # open questions above the low-water mark -> DECAYING
DEFAULT_LIMIT = 25           # questions rendered; the total is always stated

NO_WAY_BACK = "no way back stated"

LEDGER_RELPATH = Path(".intentops") / "still-true" / "ledger.jsonl"

_OBSERVED_STAMP = re.compile(r"\[OBSERVED\s+(\d{4}-\d{2}-\d{2})")
_FRONT_AS_OF = re.compile(r"^as_of:\s*[\"']?(\d{4}-\d{2}-\d{2})", re.MULTILINE)
# Longer alternatives FIRST (jsonl before json) or the regex clips `.jsonl` to
# `.json` and a live citation reads as gone.
_EXT = r"(?:py|yaml|yml|md|jsonl|json|toml|ps1|sh|txt|html|cfg)"
_PATH_TOKEN = re.compile(r"`([A-Za-z0-9_.][A-Za-z0-9_./\\-]*\." + _EXT + r")`")
_EVIDENCE_PATHISH = re.compile(
    r"(?<![/A-Za-z0-9_.-])[A-Za-z0-9_.][A-Za-z0-9_./\\-]*\." + _EXT
    + r"(?![A-Za-z0-9_])")
_ISO_LINE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
#: node-local stores are gitignored: absent here may simply be not-yet-born
_NODE_LOCAL_PREFIX = ".intentops/"


class CarrierSpecError(ValueError):
    """A carriers config that cannot be trusted. Nothing is read."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None


def _posix_rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


# --------------------------------------------------------------------------
# the atoms
# --------------------------------------------------------------------------


@dataclass
class Belief:
    """One belief carrier: what it is, when it was last true, what it rests on."""

    kind: str
    id: str
    as_of: Optional[str]                                   # YYYY-MM-DD or None
    subjects: List[str] = field(default_factory=list)      # watchable, repo-relative posix
    unwatchable: List[str] = field(default_factory=list)   # named but cannot be dated
    missing: List[str] = field(default_factory=list)       # named and gone from disk
    reopens_when: str = ""
    summary: str = ""


@dataclass
class Question:
    """A tripped falsifier, rendered as the question it is -- never an answer."""

    kind: str
    belief: str
    belief_kind: str
    as_of: Optional[str]
    detail: str
    reask: str = NO_WAY_BACK

    def render(self) -> str:
        when = f"as of {self.as_of}" if self.as_of else "no as-of"
        return (f"Still true? [{self.kind}] {self.belief_kind} {self.belief} "
                f"({when}): {self.detail}. Re-ask when: {self.reask}")


@dataclass
class Reading:
    as_of: str
    beliefs: int = 0
    dated: int = 0
    undated: int = 0
    watchable_subjects: int = 0
    unwatchable_subjects: int = 0
    beliefs_without_way_back: int = 0
    questions: List[Question] = field(default_factory=list)
    unreadable: List[str] = field(default_factory=list)   # sources that could not be read
    notes: List[str] = field(default_factory=list)        # instrument coverage notes
    by_kind: Dict[str, Dict[str, int]] = field(default_factory=dict)
    blind_spots: List[str] = field(default_factory=list)

    @property
    def open_questions(self) -> int:
        return len(self.questions)


@dataclass(frozen=True)
class CarrierSpec:
    """One declared belief carrier. ``mode`` has no default -- see the module
    docstring."""

    path: Path
    mode: str
    label: str = ""

    def __post_init__(self) -> None:
        if self.mode not in CARRIER_MODES:
            raise CarrierSpecError(
                f"carrier {self.path}: mode {self.mode!r} is not one of "
                f"{list(CARRIER_MODES)}")


def load_carrier_specs(config_path: Path, workspace: Path) -> List[CarrierSpec]:
    """Read a carriers config. A load-bearing field that is missing HALTS.

    Shape::

        schema: still-true-carriers/v1
        carriers:
          - path: .intentops-rules/anchors.md
            mode: sections
            label: boot anchors

    A missing ``mode`` is a hard exit, never a default: a carrier read in the
    wrong mode yields a confident wrong population, which is worse than no
    reading at all. An empty ``carriers: []`` is VALID -- a node may carry
    rulings and no prose carriers -- but the key itself must be present.
    """
    import yaml  # local import: the carriers config is optional machinery

    try:
        raw = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise CarrierSpecError(f"carriers config unreadable: {exc}") from exc
    except yaml.YAMLError as exc:
        raise CarrierSpecError(f"carriers config is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise CarrierSpecError("carriers config root must be a mapping")
    if raw.get("schema") != "still-true-carriers/v1":
        raise CarrierSpecError(
            f"carriers config schema must be 'still-true-carriers/v1', "
            f"got {raw.get('schema')!r}")
    if "carriers" not in raw:
        raise CarrierSpecError("carriers config has no 'carriers' key")
    entries = raw.get("carriers")
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        raise CarrierSpecError("'carriers' must be a list")
    specs: List[CarrierSpec] = []
    for i, e in enumerate(entries):
        where = f"carriers[{i}]"
        if not isinstance(e, dict):
            raise CarrierSpecError(f"{where} is not a mapping")
        p = e.get("path")
        if not p:
            raise CarrierSpecError(f"{where} has no 'path'")
        if "mode" not in e:
            raise CarrierSpecError(
                f"{where} ({p}) declares no 'mode' -- there is no default, "
                f"choose one of {list(CARRIER_MODES)}")
        target = Path(p)
        specs.append(CarrierSpec(
            path=target if target.is_absolute() else Path(workspace) / target,
            mode=str(e["mode"]), label=str(e.get("label") or p)))
    return specs


# --------------------------------------------------------------------------
# what version control tracks
# --------------------------------------------------------------------------


class Tracked:
    """Tracked paths as a predicate, plus a basename index.

    Records cite files by bare basename more often than by path. A basename
    resolving to exactly ONE tracked file IS that file; one resolving to several
    is AMBIGUOUS and stays unwatchable (published, never guessed); one resolving
    to none is missing.
    """

    def __init__(self, paths: Iterable[str]) -> None:
        self.paths = {p for p in paths if p}
        self.by_basename: Dict[str, List[str]] = {}
        for p in self.paths:
            self.by_basename.setdefault(p.rsplit("/", 1)[-1], []).append(p)

    def __call__(self, rel: str) -> bool:
        return rel in self.paths

    def resolve_basename(self, name: str) -> List[str]:
        return sorted(self.by_basename.get(name, []))


def git_tracked(workspace: Path) -> Tracked:
    """The tracked index over repo-relative posix paths. Raises if git cannot
    answer -- an instrument failure, never an empty index pretending to be one."""
    out = subprocess.run(["git", "ls-files", "-z"], cwd=str(workspace),
                         capture_output=True, check=True,
                         timeout=120).stdout.decode("utf-8", errors="replace")
    return Tracked(out.split("\0"))


def git_last_commit_dates(workspace: Path, paths: Sequence[str],
                          since: Optional[str] = None,
                          chunk: int = 200) -> Dict[str, str]:
    """``{repo-relative posix path: YYYY-MM-DD of its most recent commit}``.

    One ``git log --name-only`` per chunk, newest-first, so the first sighting
    of a path is its latest commit.
    """
    dates: Dict[str, str] = {}
    paths = [p for p in paths if p]
    for i in range(0, len(paths), chunk):
        batch = list(paths[i:i + chunk])
        cmd = ["git", "log", "--format=%cI", "--name-only"]
        if since:
            cmd.append(f"--since={since}")
        cmd += ["--", *batch]
        out = subprocess.run(cmd, cwd=str(workspace), capture_output=True,
                             check=True, timeout=300
                             ).stdout.decode("utf-8", errors="replace")
        current: Optional[str] = None
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            if _ISO_LINE.match(line):
                current = line[:10]
                continue
            if current and line not in dates:
                dates[line] = current
    return dates


# --------------------------------------------------------------------------
# readers
# --------------------------------------------------------------------------


def _classify_subject(token: str, workspace: Path,
                      tracked: Callable[[str], bool]) -> Tuple[str, str]:
    """Return ``(bucket, normalized)`` where bucket is
    watchable | unwatchable | missing.

    A path under the workspace that version control does not track is
    unwatchable, not missing.
    """
    tok = token.strip().replace("\\", "/")
    cand = Path(workspace) / tok
    if cand.is_file():
        rel = _posix_rel(cand, Path(workspace))
        return ("watchable" if tracked(rel) else "unwatchable"), rel
    if tok.startswith(_NODE_LOCAL_PREFIX):
        return "unwatchable", f"absent:{tok}"
    if "/" not in tok:
        resolve = getattr(tracked, "resolve_basename", None)
        if resolve is not None:
            hits = resolve(tok)
            if len(hits) == 1:
                return "watchable", hits[0]
            if len(hits) > 1:
                return "unwatchable", f"ambiguous:{tok}({len(hits)})"
    return "missing", tok


def _attach_subjects(b: Belief, tokens: Iterable[str], workspace: Path,
                     tracked: Callable[[str], bool]) -> None:
    bucket_field = {"watchable": "subjects", "unwatchable": "unwatchable",
                    "missing": "missing"}
    seen: set = set()
    for tok in tokens:
        if tok in seen:
            continue
        seen.add(tok)
        bucket, norm = _classify_subject(tok, workspace, tracked)
        getattr(b, bucket_field[bucket]).append(norm)


def read_records(store_path: Path, workspace: Path,
                 tracked: Callable[[str], bool],
                 active_statuses: Sequence[str] = ("active",)
                 ) -> Tuple[List[Belief], int, List[str]]:
    """Read an append-only JSONL ruling store as beliefs.

    Returns ``(beliefs, without_way_back, parse_failures)``. A malformed line is
    COUNTED as a parse failure and stays in the denominator -- never skipped in
    silence.

    ``status`` is DELIBERATELY defaulted to active when absent: in an
    append-only ruling store supersession is an explicit status flip, so the
    absence of a marker means not-superseded rather than unknown. ``id`` is
    load-bearing and has no default -- a record without one is a parse failure.
    """
    beliefs: List[Belief] = []
    parse_failures: List[str] = []
    no_way_back = 0
    text = Path(store_path).read_text(encoding="utf-8", errors="replace")
    for n, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            rec = json.loads(raw)
        except (ValueError, TypeError) as exc:
            parse_failures.append(f"{Path(store_path).name} line {n}: {exc}")
            continue
        if not isinstance(rec, dict):
            parse_failures.append(f"{Path(store_path).name} line {n}: not an object")
            continue
        rid = rec.get("id")
        if not rid:
            parse_failures.append(
                f"{Path(store_path).name} line {n}: record has no 'id'")
            continue
        if str(rec.get("status", "active")) not in active_statuses:
            continue
        b = Belief(kind="ruling", id=str(rid),
                   as_of=(str(rec.get("date"))[:10] if rec.get("date") else None),
                   reopens_when=str(rec.get("reopens_when") or ""),
                   summary=str(rec.get("summary") or rec.get("ordering") or ""))
        if not b.reopens_when:
            no_way_back += 1
        evidence = rec.get("evidence") or []
        if isinstance(evidence, str):
            evidence = [evidence]
        tokens: List[str] = []
        for ev in list(evidence) + [str(rec.get("instrument") or "")]:
            tokens.extend(m.group(0) for m in _EVIDENCE_PATHISH.finditer(str(ev)))
        _attach_subjects(b, tokens, workspace, tracked)
        beliefs.append(b)
    return beliefs, no_way_back, parse_failures


def read_carrier(spec: CarrierSpec, workspace: Path,
                 tracked: Callable[[str], bool]) -> List[Belief]:
    """Read one declared carrier in its declared mode."""
    text = spec.path.read_text(encoding="utf-8", errors="replace")
    rel = _posix_rel(spec.path, Path(workspace))
    beliefs: List[Belief] = []

    if spec.mode == "document":
        stamps = _OBSERVED_STAMP.findall(text)
        front = _FRONT_AS_OF.search(text)
        as_of = max(stamps) if stamps else (front.group(1) if front else None)
        b = Belief(kind="carrier", id=rel, as_of=as_of, summary=spec.label or rel)
        _attach_subjects(b, (m.group(1) for m in _PATH_TOKEN.finditer(text)),
                         workspace, tracked)
        return [b]

    # mode == "sections"
    for sec in re.split(r"^## ", text, flags=re.MULTILINE)[1:]:
        title, _, body = sec.partition("\n")
        stamps = _OBSERVED_STAMP.findall(body)
        if not stamps:
            continue  # blind spot 1: unstamped blocks are outside the population
        b = Belief(kind="carrier", id=f"{rel}#{title.strip()}",
                   as_of=max(stamps), summary=title.strip())
        _attach_subjects(b, (m.group(1) for m in _PATH_TOKEN.finditer(body)),
                         workspace, tracked)
        beliefs.append(b)
    return beliefs


# --------------------------------------------------------------------------
# the falsifier pass -- pure over its inputs
# --------------------------------------------------------------------------


def evaluate(beliefs: Iterable[Belief], last_commit: Dict[str, str],
             conflicts: Sequence[Tuple[str, str, str, str]] = ()) -> List[Question]:
    """Read every belief's falsifiers against live state; return the tripped ones.

    ONE question per belief per falsifier: a belief resting on three moved
    subjects is one thing to re-ask, not three lines to scroll past.
    """
    questions: List[Question] = []
    for b in beliefs:
        reask = b.reopens_when or NO_WAY_BACK
        as_of = _to_date(b.as_of)
        if as_of is None:
            questions.append(Question(
                "UNDATED", b.id, b.kind, b.as_of,
                "the carrier has no parseable as-of -- a belief with no "
                "half-life", reask))
            continue
        if b.missing:
            names = ", ".join(f"`{s}`" for s in b.missing)
            questions.append(Question(
                "SUBJECT-GONE", b.id, b.kind, b.as_of,
                f"names {names}, no longer on disk", reask))
        moved_bits: List[str] = []
        for sub in b.subjects:
            moved = _to_date(last_commit.get(sub))
            if moved and moved > as_of:
                moved_bits.append(f"`{sub}` committed {moved.isoformat()} "
                                  f"({(moved - as_of).days}d after)")
        if moved_bits:
            questions.append(Question(
                "EVIDENCE-MOVED", b.id, b.kind, b.as_of,
                "rests on " + "; ".join(moved_bits)
                + " -- evidence newer than the belief", reask))
    for a_id, a_text, c_id, c_text in conflicts:
        questions.append(Question(
            "CONFLICT", f"{a_id} vs {c_id}", "ruling", None,
            f"two active beliefs disagree -- '{a_text}' against '{c_text}'; "
            "the older cannot have been reopened by anything but a person",
            "an operator ruling, with lineage"))
    return questions


def take_reading(
    *,
    workspace: Path,
    records: Optional[Path] = None,
    carriers: Sequence[CarrierSpec] = (),
    conflicts_fn: Optional[Callable[[], Sequence[Tuple[str, str, str, str]]]] = None,
    last_commit_fn: Optional[Callable[[Sequence[str], Optional[str]], Dict[str, str]]] = None,
    tracked_fn: Optional[Callable[[str], bool]] = None,
) -> Reading:
    """Gather every carrier, run the falsifier pass, keep unreadable sources IN
    the count."""
    workspace = Path(workspace)
    r = Reading(as_of=_now_iso())
    r.blind_spots = [
        "unstamped section blocks are outside the population (no as-of at all)",
        "untracked subjects (node-local stores) have no commit history; "
        "modification time is a rewrite signature, not movement",
        "movement is commit-level: cosmetic commits trip; wrong-from-birth "
        "never trips (that is the grounded-signal instruments' job)",
        "a prose reopens_when is carried, never evaluated",
        "day granularity: same-day commits do not count as moved",
    ]
    beliefs: List[Belief] = []
    conflicts: List[Tuple[str, str, str, str]] = []

    try:
        tracked = tracked_fn if tracked_fn is not None else git_tracked(workspace)
    except Exception as exc:  # version control unavailable -- LOUD, not silent
        r.unreadable.append(f"git ls-files: {exc.__class__.__name__}: {exc}")
        tracked = Tracked(())

    if records is not None:
        if Path(records).is_file():
            try:
                rb, r.beliefs_without_way_back, failures = read_records(
                    Path(records), workspace, tracked)
                beliefs += rb
                r.unreadable.extend(failures)
            except Exception as exc:
                r.unreadable.append(
                    f"records {Path(records).name}: {exc.__class__.__name__}: {exc}")
        else:
            r.unreadable.append(f"records: {records} missing")

    for spec in carriers:
        if not spec.path.is_file():
            r.unreadable.append(f"carrier: {spec.path} missing")
            continue
        try:
            beliefs += read_carrier(spec, workspace, tracked)
        except Exception as exc:
            r.unreadable.append(
                f"carrier {spec.path.name}: {exc.__class__.__name__}: {exc}")

    if conflicts_fn is None:
        r.notes.append("no conflicts_fn supplied -- the CONFLICT falsifier "
                       "cannot fire in this reading (blind spot 6)")
    else:
        try:
            conflicts = list(conflicts_fn())
        except Exception as exc:
            r.unreadable.append(f"conflicts: {exc.__class__.__name__}: {exc}")

    subjects = sorted({s for b in beliefs for s in b.subjects})
    # PARSEABLE dates only. `git log --since=<garbage>` fails OPEN: it walks
    # zero commits, exits 0 and says nothing, so one unparseable as-of sorting
    # below the real dates would silently suppress every EVIDENCE-MOVED
    # question with `unreadable` still empty.
    dated_dates = [d for d in (_to_date(b.as_of) for b in beliefs) if d is not None]
    since = min(dated_dates).isoformat() if dated_dates else None
    last_commit: Dict[str, str] = {}
    if subjects:
        try:
            fn = last_commit_fn or (
                lambda ps, s: git_last_commit_dates(workspace, ps, s))
            last_commit = fn(subjects, since)
        except Exception as exc:
            r.unreadable.append(f"git log: {exc.__class__.__name__}: {exc}")

    r.beliefs = len(beliefs)
    r.dated = sum(1 for b in beliefs if _to_date(b.as_of) is not None)
    r.undated = r.beliefs - r.dated
    r.watchable_subjects = len(subjects)
    r.unwatchable_subjects = len({s for b in beliefs for s in b.unwatchable})
    for k in KINDS:
        ks = [b for b in beliefs if b.kind == k]
        dated_k = sum(1 for b in ks if _to_date(b.as_of) is not None)
        r.by_kind[k] = {"beliefs": len(ks), "dated": dated_k,
                        "undated": len(ks) - dated_k}
    r.questions = evaluate(beliefs, last_commit, conflicts)
    return r


# --------------------------------------------------------------------------
# posture -- low-water mark, never the previous record
# --------------------------------------------------------------------------


def posture(reading: Reading, history: Sequence[dict]) -> Tuple[str, List[str]]:
    notes: List[str] = []
    if reading.unreadable:
        notes.append(
            f"{len(reading.unreadable)} source(s) could not be read -- they "
            "stay in the denominator and the reading is not trustworthy: "
            + "; ".join(reading.unreadable))
        return "DEGRADED", notes
    n = reading.open_questions
    prior = [h.get("open_questions") for h in history
             if isinstance(h.get("open_questions"), int)
             and h.get("verdict") != "DEGRADED"]
    corrupt = sum(1 for h in history if h.get("corrupt"))
    if corrupt:
        notes.append(f"{corrupt} ledger line(s) unreadable -- the low-water "
                     "mark may be understated")
        if not prior:
            notes.append("readable history LOST: every prior record is "
                         "unreadable -- posture not trustworthy")
            return "DEGRADED", notes
    if prior:
        low = min(prior)
        if n > low + DECAY_TOLERANCE:
            notes.append(
                f"{n} open questions vs low-water mark {low} (+{n - low}, "
                f"tolerance {DECAY_TOLERANCE}) -- beliefs are going stale "
                "faster than they are re-asked")
            return "DECAYING", notes
        notes.append(f"{n} open questions vs low-water mark {low}")
    else:
        notes.append(f"no prior reading -- {n} open questions becomes the baseline")
    if n:
        kinds: Dict[str, int] = {}
        for q in reading.questions:
            kinds[q.kind] = kinds.get(q.kind, 0) + 1
        notes.append("owed a human: "
                     + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
        return "ATTENTION", notes
    notes.append("no open questions -- every dated belief's subjects are older "
                 "than it")
    return "CURRENT", notes


# --------------------------------------------------------------------------
# ledger
# --------------------------------------------------------------------------


def default_ledger(workspace: Path) -> Path:
    return Path(workspace) / LEDGER_RELPATH


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
            out.append({"corrupt": True})  # counted, never skipped
            continue
        out.append(rec if isinstance(rec, dict) else {"corrupt": True})
    return out


def append_reading(reading: Reading, verdict: str, path: Path) -> None:
    rec: Dict[str, Any] = {
        "as_of": reading.as_of, "verdict": verdict,
        "beliefs": reading.beliefs, "dated": reading.dated,
        "undated": reading.undated,
        "open_questions": reading.open_questions,
        "watchable_subjects": reading.watchable_subjects,
        "unwatchable_subjects": reading.unwatchable_subjects,
        "beliefs_without_way_back": reading.beliefs_without_way_back,
        "by_kind": reading.by_kind,
        "question_kinds": {k: sum(1 for q in reading.questions if q.kind == k)
                           for k in QUESTION_KINDS},
        "question_ids": [f"{q.kind}:{q.belief}" for q in reading.questions][:200],
        "unreadable": reading.unreadable,
        "notes": reading.notes,
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if StoreLock is None:
        raise RuntimeError(
            "still_true ledger append refused: StoreLock "
            "(intentops_core.store_guard) could not be imported, and appending "
            "without it risks interleaved writes")
    with StoreLock(lock_for(path) if lock_for is not None
                   else path.with_name(path.name + ".lock")):
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")


# --------------------------------------------------------------------------
# selftest -- every falsifier and every posture must be able to fire
# --------------------------------------------------------------------------


def selftest() -> Tuple[bool, str]:
    """Prove every falsifier and every posture path can fire, and that the
    non-firing cases genuinely do not fire."""
    import tempfile

    f: List[str] = []

    def check(label: str, cond: bool) -> None:
        if not cond:
            f.append(label)

    dated = Belief("ruling", "R-1", "2026-06-06", subjects=["src/a.py"],
                   reopens_when="")
    moved = {"src/a.py": "2026-08-29"}

    q = evaluate([dated], moved)
    check(f"EVIDENCE-MOVED did not fire: {[x.kind for x in q]}",
          [x.kind for x in q] == ["EVIDENCE-MOVED"])
    check("a belief with no reopens_when must say so, not invent one",
          bool(q) and q[0].reask == NO_WAY_BACK)
    check("a same-day commit must not count as moved",
          not evaluate([dated], {"src/a.py": "2026-06-06"}))
    check("an older commit must not count as moved",
          not evaluate([dated], {"src/a.py": "2026-01-01"}))

    check("UNDATED did not fire",
          [x.kind for x in evaluate([Belief("carrier", "c", None)], {})] == ["UNDATED"])
    check("SUBJECT-GONE did not fire",
          [x.kind for x in evaluate(
              [Belief("carrier", "row", "2026-09-01", missing=["src/gone.py"])],
              {})] == ["SUBJECT-GONE"])
    check("CONFLICT did not fire",
          [x.kind for x in evaluate(
              [], {}, conflicts=[("A", "x above y", "B", "y above x")])] == ["CONFLICT"])
    check("a stated reopens_when must be carried through to the question",
          evaluate([Belief("ruling", "R-2", None, reopens_when="the host changes")],
                   {})[0].reask == "the host changes")

    # -- postures ----------------------------------------------------------
    r = Reading(as_of="t")
    r.questions = evaluate([dated], moved)
    v, _n = posture(r, [])
    check(f"ATTENTION did not fire: {v}", v == "ATTENTION")
    v, _n = posture(r, [{"open_questions": 0, "verdict": "CURRENT"}] * 2)
    check("1 above a low-water of 0 must stay ATTENTION inside tolerance",
          v == "ATTENTION")
    r.questions = evaluate([dated] * (DECAY_TOLERANCE + 2), moved)
    v, _n = posture(r, [{"open_questions": 0, "verdict": "CURRENT"}])
    check(f"DECAYING did not fire: {v}", v == "DECAYING")
    r.questions = []
    v, _n = posture(r, [])
    check(f"CURRENT did not fire: {v}", v == "CURRENT")
    r.unreadable = ["records: gone"]
    v, _n = posture(r, [])
    check(f"DEGRADED did not fire: {v}", v == "DEGRADED")
    r.unreadable = []
    v, notes = posture(r, [{"open_questions": 0, "verdict": "DEGRADED"},
                           {"open_questions": 9}])
    check("a DEGRADED record must not set the low-water mark",
          "low-water mark 9" in " ".join(notes))
    v, notes = posture(r, [{"corrupt": True}, {"corrupt": True}])
    check(f"a ledger with only corrupt lines must read DEGRADED, got {v}",
          v == "DEGRADED" and any("LOST" in x for x in notes))
    v, notes = posture(r, [{"corrupt": True},
                           {"open_questions": 0, "verdict": "CURRENT"}])
    check("a corrupt line beside readable history must be NOTED, not hidden",
          v == "CURRENT" and any("unreadable" in x for x in notes))

    # -- carrier config refusals and the two carrier modes ------------------
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        cfg = ws / "carriers.yaml"

        def refuses(label: str, text: str) -> None:
            cfg.write_text(text, encoding="utf-8")
            try:
                load_carrier_specs(cfg, ws)
            except CarrierSpecError:
                return
            f.append(label)

        refuses("a carriers config with a wrong schema was accepted",
                "schema: nope\ncarriers: []\n")
        refuses("a carrier with no mode was accepted (there is no default)",
                "schema: still-true-carriers/v1\ncarriers:\n  - path: a.md\n")
        refuses("a carrier with an unknown mode was accepted",
                "schema: still-true-carriers/v1\ncarriers:\n"
                "  - path: a.md\n    mode: guess\n")
        refuses("a carriers config with no 'carriers' key was accepted",
                "schema: still-true-carriers/v1\n")
        cfg.write_text("schema: still-true-carriers/v1\ncarriers: []\n",
                       encoding="utf-8")
        try:
            check("an empty but declared carriers list must be VALID",
                  load_carrier_specs(cfg, ws) == [])
        except CarrierSpecError as exc:
            f.append(f"an empty carriers list was refused: {exc}")

        anchors = ws / "anchors.md"
        anchors.write_text(
            "# anchors\n\n"
            "## The gate\n\n[OBSERVED 2026-09-01] `src/gate.py` is the gate.\n\n"
            "## Undated row\n\nno stamp here, `src/other.py`\n",
            encoding="utf-8")
        (ws / "src").mkdir()
        (ws / "src" / "gate.py").write_text("x=1\n", encoding="utf-8")
        tracked = Tracked(["src/gate.py"])
        secs = read_carrier(CarrierSpec(anchors, "sections"), ws, tracked)
        check(f"sections mode read {len(secs)} beliefs, wanted 1 (unstamped "
              "blocks are outside the population)", len(secs) == 1)
        check("sections mode lost the section's subject",
              bool(secs) and secs[0].subjects == ["src/gate.py"])
        doc = read_carrier(CarrierSpec(anchors, "document"), ws, tracked)
        check("document mode must read the whole file as ONE belief",
              len(doc) == 1 and doc[0].as_of == "2026-09-01")

        # -- record store: parse failures stay in the denominator ----------
        store = ws / "rulings.jsonl"
        store.write_text(
            json.dumps({"id": "R-a", "date": "2026-01-01",
                        "evidence": ["src/gate.py"], "reopens_when": ""}) + "\n"
            + "{ not json\n"
            + json.dumps({"date": "2026-01-02"}) + "\n"
            + json.dumps({"id": "R-super", "date": "2026-01-03",
                          "status": "superseded"}) + "\n",
            encoding="utf-8")
        beliefs, no_back, failures = read_records(store, ws, tracked)
        check(f"read_records kept {len(beliefs)} beliefs, wanted 1 "
              "(superseded excluded)", len(beliefs) == 1)
        check(f"read_records reported {len(failures)} parse failures, wanted 2",
              len(failures) == 2)
        check("a record with no reopens_when was not counted as owing one",
              no_back == 1)

        # -- take_reading keeps an unreadable source in the denominator -----
        rd = take_reading(workspace=ws, records=ws / "absent.jsonl",
                          tracked_fn=tracked,
                          last_commit_fn=lambda ps, s: {})
        check("a missing record store did not make the reading DEGRADED",
              posture(rd, [])[0] == "DEGRADED")
        rd2 = take_reading(workspace=ws, records=store,
                           carriers=[CarrierSpec(anchors, "sections")],
                           tracked_fn=tracked,
                           last_commit_fn=lambda ps, s: {"src/gate.py": "2026-09-05"})
        kinds = {q.kind for q in rd2.questions}
        check(f"a live reading did not raise EVIDENCE-MOVED: {kinds}",
              "EVIDENCE-MOVED" in kinds)
        check("a reading with no conflicts_fn must SAY the CONFLICT falsifier "
              "cannot fire", any("CONFLICT" in n for n in rd2.notes))

    if f:
        return False, f"{len(f)} check(s) failed: " + "; ".join(f)
    return True, ("4 falsifiers and 4 postures fire; same-day and older commits "
                  "do not; a DEGRADED record never sets the low-water mark; "
                  "corrupt ledger lines are noted or DEGRADE; both carrier "
                  "modes read; 4 carriers-config refusals fire; record parse "
                  "failures stay in the denominator; a missing conflicts_fn is "
                  "reported, not hidden")


# --------------------------------------------------------------------------
# render + CLI
# --------------------------------------------------------------------------


def render(reading: Reading, verdict: str, notes: List[str],
           limit: int = DEFAULT_LIMIT) -> str:
    L: List[str] = []
    L.append(f"STILL TRUE? -- belief currency, as of {reading.as_of}")
    L.append("")
    L.append(f"  {'carrier':<10}{'beliefs':>9}{'dated':>8}{'undated':>9}")
    for k in KINDS:
        d = reading.by_kind.get(k, {})
        L.append(f"  {k:<10}{d.get('beliefs', 0):>9}{d.get('dated', 0):>8}"
                 f"{d.get('undated', 0):>9}")
    L.append("")
    L.append(f"  subjects watchable / unwatchable   "
             f"{reading.watchable_subjects} / {reading.unwatchable_subjects}")
    L.append(f"  beliefs with no way back           "
             f"{reading.beliefs_without_way_back}")
    L.append("")
    qs = reading.questions
    L.append(f"QUESTIONS OWED A HUMAN ({len(qs)}):")
    for q in qs[:limit]:
        L.append(f"  ? {q.render()}")
    if len(qs) > limit:
        L.append(f"  ... {len(qs) - limit} more (raise --limit or use --json)")
    if not qs:
        L.append("  (none)")
    L.append("")
    L.append(verdict)
    for n in notes:
        L.append(f"  - {n}")
    for n in reading.notes:
        L.append(f"  - coverage: {n}")
    L.append("  blind spots: " + "; ".join(reading.blind_spots))
    return "\n".join(L)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Still True? -- the belief-currency instrument")
    ap.add_argument("--workspace", default=".", help="node repository root")
    ap.add_argument("--records", default=str(Path(".intentops") / "wisdom"
                                            / "orderings.jsonl"),
                    help="append-only JSONL ruling store (workspace-relative)")
    ap.add_argument("--carriers", default=str(Path("config")
                                              / "still-true-carriers.yaml"),
                    help="carriers config (workspace-relative)")
    ap.add_argument("--json", action="store_true", help="machine output")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("--no-write", action="store_true",
                    help="do not append to the ledger")
    ap.add_argument("--strict", action="store_true",
                    help="exit 2 when questions are open")
    ap.add_argument("--selftest", action="store_true",
                    help="prove every falsifier and posture can fire")
    args = ap.parse_args(argv)

    if args.selftest:
        ok, msg = selftest()
        print(f"{'PASS' if ok else 'FAIL'} still_true selftest: {msg}")
        return 0 if ok else 1

    ws = Path(args.workspace).resolve()
    carriers_cfg = Path(args.carriers)
    if not carriers_cfg.is_absolute():
        carriers_cfg = ws / carriers_cfg
    specs: List[CarrierSpec] = []
    config_error: Optional[str] = None
    if carriers_cfg.is_file():
        try:
            specs = load_carrier_specs(carriers_cfg, ws)
        except CarrierSpecError as exc:
            config_error = f"carriers config: {exc}"
    else:
        config_error = f"carriers config: {carriers_cfg} missing"

    records = Path(args.records)
    if not records.is_absolute():
        records = ws / records

    reading = take_reading(workspace=ws, records=records, carriers=specs)
    if config_error:
        reading.unreadable.append(config_error)

    ledger = default_ledger(ws)
    history = load_history(ledger)
    verdict, notes = posture(reading, history)
    if not args.no_write:
        append_reading(reading, verdict, ledger)
    # Say which happened: a --no-write run must not print "becomes the
    # baseline" in the same words as a written one, or the reading cannot tell
    # a recollection from a record.
    notes = [n.replace(
        "becomes the baseline",
        "WOULD become the baseline (not written: --no-write)" if args.no_write
        else "becomes the baseline (appended to the ledger)") for n in notes]

    if args.json:
        payload: Dict[str, Any] = {"verdict": verdict, "notes": notes,
                                   **asdict(reading)}
        payload["open_questions"] = reading.open_questions
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(render(reading, verdict, notes, args.limit))

    if args.strict and reading.open_questions:
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
