"""The Ordering record and its store -- the wisdom subsystem's substrate.

PURPOSE
    Hold a node's RULINGS UNDER CONDITIONS: what its authority decided, which
    two goods conflicted, and the scope in which the decision holds. Two things
    are refused at the door because their absence turns a ruling into something
    else:

      * no ``tension``    -> it is a PREFERENCE, not a ruling
      * no ``conditions`` -> it is a SLOGAN: the conclusion was recorded and
                             the wisdom discarded

    A store that accepts either is worse than no store: it looks governed and
    is not.

    ``reopens_when`` is deliberately NOT refused. A corpus of rulings
    transcribed from an authority's own words will not carry one, and
    fabricating a way-back for a ruling whose author never stated one
    manufactures exactly the false confidence this module exists to prevent. It
    is surfaced as debt by :func:`posture` instead, and paid by asking.

    SERVING IS PREDICATE-GATED, and that is the honest boundary. Deciding
    whether a case falls inside a prose-written condition IS the particularist
    judgement, and this module does not pretend to make it. A prose-only ruling
    is stored, listed and surfaced to a human; it is never auto-served as
    governing. Only ``predicate``-carrying rulings answer
    :meth:`OrderingState.governing`.

    ``on_match`` defaults to ``escalate``. Defaulting to ``permit`` would make
    compiling a ruling into a predicate a DANGEROUS act, which would guarantee
    nobody ever compiles one.

WRITE MODEL
    Append-only journal + StoreLock; state is a pure fold of events. Nothing
    rewrites the file, so a superseded ruling is retained forever --
    supersession is a status flip plus lineage, never a deletion.
    Store: ``<node_root>/.intentops/wisdom/orderings.jsonl``.

BLIND SPOTS
    * The predicate language is exact conjunctive equality -- no operators, no
      eval, no DSL. It is deliberately too small to hide judgement inside, and
      therefore too small to express most conditions. Most rulings will stay
      prose, and that is the honest state, not a gap to close by widening the
      language.
    * ``conflicts()`` sees only rulings that carry predicates. Two prose
      rulings that contradict each other are invisible here.
    * A corrupt journal line is skipped with a WARNING. The ruling it carried
      is lost from the fold until someone reads the log.
    * No LLM anywhere in the read path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from intentops_core.store_guard import StoreLock, lock_for

logger = logging.getLogger(__name__)

__all__ = [
    "ON_MATCH",
    "STATIONS",
    "STATUSES",
    "Ordering",
    "OrderingState",
    "OrderingStore",
    "ValidationError",
    "posture",
    "selftest",
]

STATIONS: Tuple[str, ...] = ("described", "advisory", "operative")
#: What a MATCHING ruling means for the case. See the module docstring on why
#: the default is fail-safe.
ON_MATCH: Tuple[str, ...] = ("permit", "escalate", "reject")
STATUSES: Tuple[str, ...] = ("active", "superseded", "reopened")

_NODE_ROOT_ENV = "INTENTOPS_NODE_ROOT"


class ValidationError(ValueError):
    """A record that is not a ruling."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def node_root_from_env() -> Path:
    """Resolve the node root from the environment, or HALT.

    There is no default. A store that silently picks a directory writes a
    node's rulings somewhere nobody looks.
    """
    raw = os.environ.get(_NODE_ROOT_ENV, "").strip()
    if not raw:
        raise ValidationError(
            f"{_NODE_ROOT_ENV} is not set. The ordering store will not guess a "
            "node root: a ruling written to the wrong node is a ruling nobody "
            "will read."
        )
    return Path(raw)


# --------------------------------------------------------------------------
# the atom
# --------------------------------------------------------------------------


@dataclass
class Ordering:
    """A ruling under conditions.

    Every field earns its place by naming something whose absence turns the
    record into a different, weaker thing.
    """

    id: str
    statement: str
    tension: str
    ordering: str
    conditions: str
    authority: str
    reopens_when: str = ""
    predicate: Dict[str, Any] = field(default_factory=dict)
    on_match: str = "escalate"
    evidence: List[str] = field(default_factory=list)
    station: str = "described"
    instrument: str = ""
    status: str = "active"
    supersedes: str = ""
    superseded_by: str = ""
    date: str = ""
    at: str = ""
    compiled_by: str = ""
    compile_rationale: str = ""
    # A re-ask that ends in CONTINUE is recorded WITH its reason, so "continue,
    # for this reason" and "nobody looked" are different rows in the journal.
    # Without it, no instrument can tell them apart.
    reaffirmed_at: str = ""
    reaffirm_reason: str = ""

    # -- construction ------------------------------------------------------

    @classmethod
    def create(cls, statement: str, tension: str, ordering: str, conditions: str,
               authority: str, **kw: Any) -> "Ordering":
        statement = (statement or "").strip()
        if not statement:
            raise ValidationError("statement is required")
        if not (tension or "").strip():
            raise ValidationError(
                "tension is required: a ruling with no tension is a PREFERENCE"
            )
        if not (ordering or "").strip():
            raise ValidationError(
                "ordering is required: which good was placed above which"
            )
        if not (conditions or "").strip():
            raise ValidationError(
                "conditions are required: a ruling that omits its conditions has "
                "recorded the conclusion and discarded the wisdom -- that is the "
                "SLOGAN decay, refused at the door"
            )
        if not (authority or "").strip():
            raise ValidationError(
                "authority is required: a ruling with no author binds nobody"
            )
        station = kw.pop("station", "described")
        if station not in STATIONS:
            raise ValidationError(f"station must be one of {STATIONS}, got {station!r}")
        on_match = kw.pop("on_match", "escalate")
        if on_match not in ON_MATCH:
            raise ValidationError(f"on_match must be one of {ON_MATCH}, got {on_match!r}")
        return cls(
            id=cls.mint_id(statement),
            statement=statement,
            tension=tension.strip(),
            ordering=ordering.strip(),
            conditions=conditions.strip(),
            authority=authority.strip(),
            station=station,
            on_match=on_match,
            at=kw.pop("at", "") or _now(),
            **kw,
        )

    @staticmethod
    def mint_id(statement: str) -> str:
        digest = hashlib.sha256(statement.strip().encode("utf-8")).hexdigest()[:12]
        return f"ORD-{digest}"

    # -- the honest gaps ---------------------------------------------------

    def incomplete(self) -> List[str]:
        """Fields whose absence keeps this ruling from being fully governed.

        Deliberately not raised at construction: a transcribed corpus lacks
        them, and inventing them would be fabrication. Surfaced by
        :func:`posture` instead.
        """
        gaps: List[str] = []
        if not self.reopens_when.strip():
            gaps.append("reopens_when")
        if not self.predicate:
            gaps.append("predicate")
        if not self.evidence:
            gaps.append("evidence")
        return gaps

    # -- serving -----------------------------------------------------------

    def matches(self, facts: Dict[str, Any]) -> bool:
        """True iff every predicate key is present in ``facts`` and equal.

        Exact conjunctive match. A ruling with NO predicate never matches --
        prose conditions are human-judged by construction, and silence here is
        the honest answer.
        """
        if not self.predicate:
            return False
        return all(k in facts and facts[k] == v for k, v in self.predicate.items())

    def to_row(self, event: str = "rule") -> Dict[str, Any]:
        return {
            "event": event,
            "id": self.id,
            "statement": self.statement,
            "tension": self.tension,
            "ordering": self.ordering,
            "conditions": self.conditions,
            "predicate": self.predicate,
            "on_match": self.on_match,
            "authority": self.authority,
            "reopens_when": self.reopens_when,
            "evidence": list(self.evidence),
            "station": self.station,
            "instrument": self.instrument,
            "status": self.status,
            "supersedes": self.supersedes,
            "superseded_by": self.superseded_by,
            "date": self.date,
            "compiled_by": self.compiled_by,
            "compile_rationale": self.compile_rationale,
            "reaffirmed_at": self.reaffirmed_at,
            "reaffirm_reason": self.reaffirm_reason,
            "at": self.at or _now(),
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "Ordering":
        return cls(
            id=row.get("id", ""),
            statement=row.get("statement", ""),
            tension=row.get("tension", ""),
            ordering=row.get("ordering", ""),
            conditions=row.get("conditions", ""),
            authority=row.get("authority", ""),
            reopens_when=row.get("reopens_when", "") or "",
            predicate=row.get("predicate") or {},
            on_match=row.get("on_match", "escalate") or "escalate",
            evidence=list(row.get("evidence") or []),
            station=row.get("station", "described") or "described",
            instrument=row.get("instrument", "") or "",
            status=row.get("status", "active") or "active",
            supersedes=row.get("supersedes", "") or "",
            superseded_by=row.get("superseded_by", "") or "",
            date=row.get("date", "") or "",
            compiled_by=row.get("compiled_by", "") or "",
            compile_rationale=row.get("compile_rationale", "") or "",
            reaffirmed_at=row.get("reaffirmed_at", "") or "",
            reaffirm_reason=row.get("reaffirm_reason", "") or "",
            at=row.get("at", "") or "",
        )


# --------------------------------------------------------------------------
# folded state
# --------------------------------------------------------------------------


@dataclass
class OrderingState:
    """The fold of the event journal. Read-only."""

    records: Dict[str, Ordering] = field(default_factory=dict)

    def all(self) -> List[Ordering]:
        return list(self.records.values())

    def by_id(self, ordering_id: str) -> Optional[Ordering]:
        return self.records.get(ordering_id)

    def active(self) -> List[Ordering]:
        return [o for o in self.records.values() if o.status == "active"]

    def incomplete(self) -> List[Ordering]:
        return [o for o in self.active() if o.incomplete()]

    # -- serve -------------------------------------------------------------

    def governing(self, facts: Dict[str, Any]) -> List[Ordering]:
        """Active rulings whose predicate matches this case.

        Predicate-carrying rulings only -- see the module docstring on why
        prose conditions are never auto-served.
        """
        return [o for o in self.active() if o.matches(facts)]

    def unruled(self, facts: Dict[str, Any]) -> bool:
        """True iff no active ruling's predicate covers this case.

        The flag worth acting on: the node asking for a ruling rather than
        guessing one.
        """
        return not self.governing(facts)

    def conflicts(self) -> List[Tuple[Ordering, Ordering]]:
        """Pairs of active rulings whose predicates overlap with DIFFERENT orderings.

        Overlap alone is fine -- rulings compose. Overlap with divergent
        orderings is a case the node would answer two ways, which is a question
        for the authority, not for the caller.
        """
        out: List[Tuple[Ordering, Ordering]] = []
        with_pred = [o for o in self.active() if o.predicate]
        for i, a in enumerate(with_pred):
            for b in with_pred[i + 1:]:
                if _predicates_overlap(a.predicate, b.predicate) and a.ordering != b.ordering:
                    out.append((a, b))
        return out


def _predicates_overlap(p: Dict[str, Any], q: Dict[str, Any]) -> bool:
    """True iff some case could satisfy both -- no shared key disagrees."""
    shared = set(p) & set(q)
    if not shared:
        return False
    return all(p[k] == q[k] for k in shared)


# --------------------------------------------------------------------------
# the store
# --------------------------------------------------------------------------


class OrderingStore:
    """Append-only journal of ruling events, guarded by StoreLock.

    The node root is a required parameter. There is no workspace default: a
    store that guesses its own location is a store nobody can audit.
    """

    def __init__(self, node_root: Path | str) -> None:
        if not node_root:
            raise ValidationError(
                "OrderingStore requires an explicit node root -- it will not "
                "guess where a node's rulings live"
            )
        self.node_root = Path(node_root)
        self.path = self.node_root / ".intentops" / "wisdom" / "orderings.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls) -> "OrderingStore":
        return cls(node_root_from_env())

    # -- write -------------------------------------------------------------

    def _append(self, rows: Iterable[Dict[str, Any]]) -> None:
        payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
        if not payload:
            return
        with StoreLock(lock_for(self.path)):
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(payload)

    def rule(self, ordering: Ordering) -> Ordering:
        """Record a ruling. If it supersedes another, the lineage is appended too."""
        rows = [ordering.to_row("rule")]
        if ordering.supersedes:
            rows.append({
                "event": "supersede",
                "id": ordering.supersedes,
                "superseded_by": ordering.id,
                "at": _now(),
            })
        self._append(rows)
        return ordering

    def compile_predicate(self, ordering_id: str, predicate: Dict[str, Any],
                          on_match: str, by: str, rationale: str) -> None:
        """Give a ruling a machine-checkable predicate.

        Compiling is an INTERPRETIVE act -- someone decided that this prose
        condition means these facts -- so it is recorded as its own event with
        an author and a rationale, never folded silently into the ruling. A
        WRONG predicate is worse than none: it makes a ruling fire on cases its
        author never contemplated.
        """
        if on_match not in ON_MATCH:
            raise ValidationError(f"on_match must be one of {ON_MATCH}, got {on_match!r}")
        if not predicate:
            raise ValidationError("a compile event must carry a non-empty predicate")
        if not (by or "").strip():
            raise ValidationError("compiling is attributable: `by` is required")
        if not (rationale or "").strip():
            raise ValidationError(
                "rationale is required: state why this prose condition means "
                "these facts, or the predicate is an unverifiable tag"
            )
        self._append([{
            "event": "compile",
            "id": ordering_id,
            "predicate": predicate,
            "on_match": on_match,
            "by": by,
            "rationale": rationale,
            "at": _now(),
        }])

    def reopen(self, ordering_id: str, reason: str, by: str) -> None:
        """Mark a ruling reopened -- its ``reopens_when`` fired."""
        self._append([{
            "event": "reopen",
            "id": ordering_id,
            "reason": reason,
            "by": by,
            "at": _now(),
        }])

    def reaffirm(self, ordering_id: str, reason: str, by: str) -> None:
        """Record that a re-ask ended in CONTINUE, with the reason.

        Refuses a blank reason (a bare "continue" is indistinguishable from
        nobody looking) and refuses to reaffirm a superseded ruling (its
        successor is the thing to reaffirm).
        """
        if not (reason or "").strip():
            raise ValidationError(
                "reaffirm needs a reason: a bare 'continue' is indistinguishable "
                "from nobody looking"
            )
        rec = self.load().by_id(ordering_id)
        if rec is None:
            raise ValidationError(f"no such ruling: {ordering_id}")
        if rec.status == "superseded":
            raise ValidationError(
                f"{ordering_id} is superseded by {rec.superseded_by}; "
                "reaffirm the successor"
            )
        self._append([{
            "event": "reaffirm",
            "id": ordering_id,
            "reason": reason.strip(),
            "by": by,
            "at": _now(),
        }])

    # -- read --------------------------------------------------------------

    def load(self) -> OrderingState:
        state = OrderingState()
        if not self.path.exists():
            return state
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                # A corrupt line never silently changes a verdict -- and it must
                # not be SILENT either: the journal is the sole write model, so
                # a dropped ruling with no signal anywhere is the exact defect
                # class this package refuses.
                logger.warning(
                    "[wisdom.ordering] unreadable line in %s: %s", self.path.name, exc
                )
                continue
            event = row.get("event", "rule")
            rid = row.get("id", "")
            if not rid:
                continue
            if event == "rule":
                state.records[rid] = Ordering.from_row(row)
            elif event == "supersede":
                rec = state.records.get(rid)
                if rec:
                    rec.status = "superseded"
                    rec.superseded_by = row.get("superseded_by", "")
            elif event == "compile":
                rec = state.records.get(rid)
                if rec:
                    rec.predicate = row.get("predicate") or {}
                    rec.on_match = row.get("on_match", "escalate") or "escalate"
                    rec.compiled_by = row.get("by", "") or ""
                    rec.compile_rationale = row.get("rationale", "") or ""
            elif event == "reopen":
                rec = state.records.get(rid)
                if rec:
                    rec.status = "reopened"
            elif event == "reaffirm":
                rec = state.records.get(rid)
                if rec and rec.status != "superseded":
                    rec.status = "active"
                    rec.reaffirmed_at = row.get("at", "") or ""
                    rec.reaffirm_reason = row.get("reason", "") or ""
        return state


# --------------------------------------------------------------------------
# the oracle
# --------------------------------------------------------------------------


def posture(state: OrderingState) -> Tuple[str, List[str]]:
    """ALIGNED / ATTENTION / BLOCKED over the ordering corpus.

    * **BLOCKED**   -- active rulings CONFLICT: the node would answer one case
      two ways, which no caller can resolve.
    * **ATTENTION** -- rulings are incomplete (no way back, no predicate, no
      evidence). Governed, but not fully governed.
    * **ALIGNED**   -- every active ruling carries its way back and its predicate.

    Every count is printed with its denominator: a rate without its population
    is not a measurement.
    """
    lines: List[str] = []
    active = state.active()
    conflicts = state.conflicts()
    incomplete = state.incomplete()

    lines.append(f"orderings: {len(active)} active, {len(state.all())} total")

    if conflicts:
        lines.append(f"CONFLICT: {len(conflicts)} pair(s) of active rulings disagree")
        for a, b in conflicts[:10]:
            lines.append(f"  {a.id} '{a.ordering}' vs {b.id} '{b.ordering}'")

    if incomplete:
        by_gap: Dict[str, int] = {}
        for o in incomplete:
            for gap in o.incomplete():
                by_gap[gap] = by_gap.get(gap, 0) + 1
        gaps = ", ".join(f"{k}={v}" for k, v in sorted(by_gap.items()))
        lines.append(f"incomplete: {len(incomplete)} of {len(active)} active -- {gaps}")

    bound = [o for o in active if o.predicate and o.on_match != "escalate"]
    served = len([o for o in active if o.predicate])
    lines.append(
        f"servable: {served}/{len(active)} carry a predicate "
        "(prose-only rulings are surfaced to a human, never auto-served); "
        f"{len(bound)} bind autonomy"
    )

    if conflicts:
        return "BLOCKED", lines
    if incomplete:
        return "ATTENTION", lines
    return "ALIGNED", lines


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------


def selftest() -> int:
    """Prove both refusals fire, prose never serves, and every posture path is reachable."""
    failures: List[str] = []

    def refuses(label: str, **kw: Any) -> None:
        base: Dict[str, Any] = {
            "statement": "s", "tension": "a vs b", "ordering": "a over b",
            "conditions": "when c", "authority": "operator",
        }
        base.update(kw)
        try:
            Ordering.create(**base)
        except ValidationError:
            return
        failures.append(f"refusal did not fire: {label}")

    refuses("missing tension", tension="")
    refuses("missing conditions", conditions="")
    refuses("missing statement", statement="")
    refuses("missing ordering", ordering="")
    refuses("missing authority", authority="")
    refuses("unknown station", station="operational")
    refuses("unknown on_match", on_match="allow")

    prose = Ordering.create("prose ruling", "a vs b", "a over b", "when c", "operator")
    if prose.matches({"anything": True}):
        failures.append("a prose-only ruling served itself as governing")
    if prose.on_match != "escalate":
        failures.append("on_match did not default to escalate")

    compiled = Ordering.create("compiled ruling", "a vs b", "a over b", "when c",
                               "operator", predicate={"estate": "served"},
                               reopens_when="the audience widens",
                               evidence=["selftest"])
    if not compiled.matches({"estate": "served", "tier": "T3"}):
        failures.append("a compiled predicate failed to match its own facts")
    if compiled.matches({"estate": "internal"}):
        failures.append("a compiled predicate matched the wrong facts")

    # posture paths
    aligned = OrderingState(records={compiled.id: compiled})
    if posture(aligned)[0] != "ALIGNED":
        failures.append("ALIGNED posture unreachable")
    attention = OrderingState(records={prose.id: prose})
    if posture(attention)[0] != "ATTENTION":
        failures.append("ATTENTION posture unreachable")
    rival = Ordering.create("rival ruling", "a vs b", "b over a", "when c",
                            "operator", predicate={"estate": "served"},
                            reopens_when="x", evidence=["selftest"])
    blocked = OrderingState(records={compiled.id: compiled, rival.id: rival})
    if posture(blocked)[0] != "BLOCKED":
        failures.append("BLOCKED posture unreachable -- the conflict detector is dead")

    print("wisdom.ordering selftest:")
    for f in failures:
        print(f"  FAIL: {f}")
    print(f"  {'PASS' if not failures else 'FAIL'} ({len(failures)} failure(s))")
    return 1 if failures else 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="the ordering store -- rulings under conditions")
    ap.add_argument("--node-root", type=Path,
                    help=f"node root (default: ${_NODE_ROOT_ENV})")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("selftest", help="prove the refusals and posture paths fire")
    sub.add_parser("check", help="posture over the ordering corpus")

    p_list = sub.add_parser("list", help="list active rulings")
    p_list.add_argument("--incomplete", action="store_true",
                        help="only rulings missing a way back / predicate / evidence")
    p_list.add_argument("--limit", type=int, default=30)

    p_show = sub.add_parser("show", help="show one ruling")
    p_show.add_argument("id")

    p_gov = sub.add_parser("governing", help="which rulings govern a case")
    p_gov.add_argument("facts", help='JSON object, e.g. {"estate":"served"}')

    p_re = sub.add_parser("reaffirm", help="record that a re-ask ended in CONTINUE")
    p_re.add_argument("id")
    p_re.add_argument("--reason", required=True)
    p_re.add_argument("--by", required=True)

    args = ap.parse_args(argv)

    if args.cmd == "selftest":
        return selftest()

    store = OrderingStore(args.node_root) if args.node_root else OrderingStore.from_env()

    if args.cmd == "reaffirm":
        store.reaffirm(args.id, reason=args.reason, by=args.by)
        print(f"reaffirmed {args.id}")
        return 0

    state = store.load()

    if args.cmd == "check":
        verdict, lines = posture(state)
        print(f"[{verdict}] ordering corpus")
        for line in lines:
            print(f"  {line}")
        return 1 if verdict == "BLOCKED" else 0

    if args.cmd == "list":
        rows = state.incomplete() if args.incomplete else state.active()
        for o in rows[: args.limit]:
            gaps = ",".join(o.incomplete()) or "-"
            print(f"{o.id}  [{o.station}] gaps={gaps}")
            print(f"    {o.statement}")
            print(f"    tension: {o.tension}")
            print(f"    ordering: {o.ordering}")
        print(f"\n{len(rows)} ruling(s); showing {min(len(rows), args.limit)}")
        return 0

    if args.cmd == "show":
        rec = state.by_id(args.id)
        if not rec:
            print(f"no such ruling: {args.id}")
            return 1
        print(json.dumps(rec.to_row(), indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "governing":
        facts = json.loads(args.facts)
        hits = state.governing(facts)
        if not hits:
            print("UNRULED -- no active ruling's predicate covers this case.")
            print("That is a question for the authority, not a default.")
            return 0
        for o in hits:
            print(f"{o.id}: {o.ordering} (on_match={o.on_match})")
            print(f"    {o.statement}")
        return 0

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
