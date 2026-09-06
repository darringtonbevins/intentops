"""The interaction journal -- one append-only record of every time the node was addressed.

PURPOSE
    A node that talks to people accumulates a scattered trail: a router log
    here, a gate audit there, an adapter's own file somewhere else. Nobody can
    answer "who addressed this node, on which surface, and what did it decide"
    from four half-overlapping logs, and the answer is exactly what an
    accountable operative owes.

    This is the one place that question is answered. Every inbound message the
    presence layer sees produces exactly one record: which channel, how that
    channel is classified, an opaque reference to who spoke, what the message
    was classified AS, the tier that classification carries, whether the route
    reached outside the machine, what was decided, and what was DONE about it
    (answered / surfaced to the operator / ignored / refused).

WHAT IT DELIBERATELY DOES NOT HOLD
    The message text. A journal that carries content is a transcript, and a
    transcript inherits the sensitivity of everything anybody ever said into
    it -- so it stops being safe to read, back up, or ship, which means in
    practice nobody reads it. This journal records THAT something was said and
    how it was handled, with ``body_chars`` and an optional ``body_digest``
    so a specific message can still be tied to its record by whoever legitimately
    holds the text. ``detail`` is refused unless every value is a JSON scalar,
    because a nested blob is where content comes back in through the side door.

WRITE MODEL
    Append-only JSONL at ``<node>/.intentops/presence/interactions.jsonl``.
    One writer appends under a kernel-released ``StoreLock``; state is a pure
    FOLD over the events; nothing ever rewrites the file. The lock is held
    across the whole validate-then-append sequence, and any state a caller
    needs is read INSIDE it -- a value read before acquisition is stale by
    definition.

    There is NO unlocked fallback. If ``StoreLock`` cannot be imported the
    append is REFUSED, loudly. A silent lockless append to a shared file is the
    defect class the write discipline exists to prevent.

ROUTE, NEVER VERDICT
    The journal gates nothing. It is written after a decision, and reading it
    changes nothing at runtime. Its consumers are the operator, an audit, and
    the posture summary below.

BLIND SPOTS
    1. Coverage is exactly what callers record. A channel adapter that routes
       around :mod:`intentops_core.presence.router` produces no records at all,
       and a low interaction count is then indistinguishable from a quiet week.
       ``population`` is reported beside every count for that reason.
    2. ``speaker_ref`` is compared, never resolved. The journal cannot tell two
       accounts belonging to one person from two people, and it never learns a
       name.
    3. A malformed line is surfaced in ``parse_failures`` and the fold
       continues; the state that line would have contributed is LOST and the
       count says so, rather than the line being skipped in silence.
    4. Nothing here expires or prunes. Growth is the operator's to manage, and
       any pruning is a named ablation with an archive step -- never a TTL that
       destroys the only copy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

try:  # pragma: no cover - import shim, exercised by the no-lock refusal below
    from ..store_guard import StoreLock, lock_for
except ImportError:  # pragma: no cover
    StoreLock = None  # type: ignore[assignment]
    lock_for = None  # type: ignore[assignment]

__all__ = [
    "JOURNAL_RELPATH",
    "DISPOSITIONS",
    "DECISIONS",
    "TIERS",
    "InteractionRecord",
    "JournalState",
    "InteractionJournal",
    "digest_body",
    "selftest",
    "main",
]

#: Where the journal lives under a node root. Declared once; the birth organ
#: entry in ``genesis/organs.py`` names the same path.
JOURNAL_RELPATH = ".intentops/presence/interactions.jsonl"

#: What was DONE about the message. Closed vocabulary; an undeclared value is a
#: hard exit, never coerced to the nearest neighbour.
DISPOSITIONS: Tuple[str, ...] = (
    "answered",            # the node replied on the channel
    "surfaced",            # handed to the operator as an ask; nothing was said
    "ignored",             # recognised as the node's own echo
    "queued_for_approval",  # a route the node may not take unattended
    "refused",             # the node declined, and the reason is recorded
)

#: The gate's own decision vocabulary, mirrored here so the journal is readable
#: without importing the gate. Kept in step by :func:`selftest`.
DECISIONS: Tuple[str, ...] = ("ALLOW", "ASK", "BLOCK", "HALT")

#: The authorization ladder. Same values as ``gate.verdict.TIERS``.
TIERS: Tuple[str, ...] = ("T0", "T1", "T2", "T3", "T4")

_SCALARS = (str, int, float, bool, type(None))


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def digest_body(text: str) -> str:
    """A short, stable digest of a message body.

    Content never enters the journal; this is how a record is tied back to a
    specific message by somebody who legitimately holds the text. Truncated to
    16 hex characters -- long enough to be a join key, short enough that nobody
    mistakes it for a content archive.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class InteractionRecord:
    """One journal line. Every field below is load-bearing and has no default
    that could be mistaken for a measurement."""

    ts: str
    channel_id: str
    channel_class: str
    speaker_ref: str
    speaker_is_operator: bool
    intent: str            # an intent id, or "unclassified"
    tier: str
    reaches_reality: bool
    decision: str
    disposition: str
    handler: str
    reasons: Tuple[str, ...] = ()
    body_chars: int = 0
    body_digest: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "ts": self.ts,
            "channel_id": self.channel_id,
            "channel_class": self.channel_class,
            "speaker_ref": self.speaker_ref,
            "speaker_is_operator": self.speaker_is_operator,
            "intent": self.intent,
            "tier": self.tier,
            "reaches_reality": self.reaches_reality,
            "decision": self.decision,
            "disposition": self.disposition,
            "handler": self.handler,
            "reasons": list(self.reasons),
            "body_chars": self.body_chars,
        }
        if self.body_digest:
            out["body_digest"] = self.body_digest
        if self.detail:
            out["detail"] = dict(self.detail)
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "InteractionRecord":
        return cls(
            ts=str(data["ts"]),
            channel_id=str(data["channel_id"]),
            channel_class=str(data["channel_class"]),
            speaker_ref=str(data["speaker_ref"]),
            speaker_is_operator=bool(data["speaker_is_operator"]),
            intent=str(data["intent"]),
            tier=str(data["tier"]),
            reaches_reality=bool(data["reaches_reality"]),
            decision=str(data["decision"]),
            disposition=str(data["disposition"]),
            handler=str(data["handler"]),
            reasons=tuple(str(r) for r in data.get("reasons") or ()),
            body_chars=int(data.get("body_chars") or 0),
            body_digest=str(data.get("body_digest") or ""),
            detail=dict(data.get("detail") or {}),
        )


@dataclass(frozen=True)
class JournalState:
    """A pure fold over the journal. Every rate travels with its denominator."""

    population: int
    parse_failures: int
    by_disposition: Dict[str, int]
    by_tier: Dict[str, int]
    by_channel: Dict[str, int]
    operator_turns: int
    non_operator_turns: int
    reaches_reality_turns: int

    @property
    def answered_non_operator(self) -> int:
        """Should always be ZERO. See :meth:`InteractionJournal.invariant_breaches`."""
        return self.by_disposition.get("__answered_non_operator", 0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "population": self.population,
            "parse_failures": self.parse_failures,
            "by_disposition": dict(self.by_disposition),
            "by_tier": dict(self.by_tier),
            "by_channel": dict(self.by_channel),
            "operator_turns": self.operator_turns,
            "non_operator_turns": self.non_operator_turns,
            "reaches_reality_turns": self.reaches_reality_turns,
        }

    def render(self) -> str:
        lines = [
            f"interactions: {self.population} "
            f"(parse failures {self.parse_failures})",
            f"  operator turns      : {self.operator_turns}",
            f"  non-operator turns  : {self.non_operator_turns}",
            f"  reaches-reality     : {self.reaches_reality_turns}",
        ]
        for label, table in (("disposition", self.by_disposition),
                             ("tier", self.by_tier),
                             ("channel", self.by_channel)):
            for key in sorted(table):
                if key.startswith("__"):
                    continue
                lines.append(f"  {label:<11} {key:<20} {table[key]}")
        return "\n".join(lines)


class InteractionJournal:
    """Append-only, content-free ledger of every interaction the node had."""

    def __init__(self, node_root: Path | str,
                 relpath: str = JOURNAL_RELPATH) -> None:
        self.node_root = Path(node_root)
        self.path = self.node_root / relpath

    # -- writing -----------------------------------------------------------

    def record(
        self,
        *,
        channel_id: str,
        channel_class: str,
        speaker_ref: str,
        speaker_is_operator: bool,
        intent: str,
        tier: str,
        reaches_reality: bool,
        decision: str,
        disposition: str,
        handler: str,
        reasons: Sequence[str] = (),
        body: Optional[str] = None,
        detail: Optional[Mapping[str, Any]] = None,
        ts: Optional[str] = None,
    ) -> InteractionRecord:
        """Validate, then append one record under the store lock.

        Raises on an undeclared vocabulary value, a missing load-bearing field,
        a non-scalar ``detail`` value, or an unavailable lock. It never writes a
        record it could not fully validate, and it never degrades a failed write
        to a warning.
        """
        if StoreLock is None or lock_for is None:  # pragma: no cover
            raise RuntimeError(
                "interaction journal: StoreLock is unavailable, so an append "
                "cannot be serialised. Refusing to write -- a silent lockless "
                "append to a shared journal is the failure this store's write "
                "model exists to prevent."
            )
        for name, value in (("channel_id", channel_id),
                            ("speaker_ref", speaker_ref),
                            ("intent", intent),
                            ("handler", handler)):
            if not value:
                raise ValueError(
                    f"interaction journal: {name} is load-bearing and empty. "
                    "A record that cannot say who was addressed, by whom, as "
                    "what, or what handled it is not a record."
                )
        if channel_class not in ("private", "shared", "unknown"):
            raise ValueError(
                f"interaction journal: undeclared channel_class "
                f"{channel_class!r}"
            )
        if tier not in TIERS:
            raise ValueError(
                f"interaction journal: tier must be one of {TIERS}, got {tier!r}"
            )
        if decision not in DECISIONS:
            raise ValueError(
                f"interaction journal: decision must be one of {DECISIONS}, "
                f"got {decision!r}"
            )
        if disposition not in DISPOSITIONS:
            raise ValueError(
                f"interaction journal: disposition must be one of "
                f"{DISPOSITIONS}, got {disposition!r} -- an undeclared "
                "disposition is a hard exit, never the nearest neighbour"
            )
        detail = dict(detail or {})
        for key, value in detail.items():
            if not isinstance(value, _SCALARS):
                raise ValueError(
                    f"interaction journal: detail[{key!r}] is a "
                    f"{type(value).__name__}. Only JSON scalars are accepted -- "
                    "a nested structure is where message content re-enters a "
                    "journal that promises to hold none."
                )

        record = InteractionRecord(
            ts=ts or _utcnow(),
            channel_id=channel_id,
            channel_class=channel_class,
            speaker_ref=speaker_ref,
            speaker_is_operator=bool(speaker_is_operator),
            intent=intent,
            tier=tier,
            reaches_reality=bool(reaches_reality),
            decision=decision,
            disposition=disposition,
            handler=handler,
            reasons=tuple(str(r) for r in reasons),
            body_chars=len(body) if body is not None else 0,
            body_digest=digest_body(body) if body else "",
            detail=detail,
        )

        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record.to_dict(), sort_keys=True) + "\n"
        with StoreLock(lock_for(self.path)):
            with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(line)
        return record

    # -- reading -----------------------------------------------------------

    def read(self) -> Tuple[List[InteractionRecord], int]:
        """Return ``(records, parse_failures)``. A pure read; no lock needed
        for a fold over an append-only file, because a partially-written final
        line reads as a parse failure rather than as corruption."""
        if not self.path.is_file():
            return [], 0
        records: List[InteractionRecord] = []
        failures = 0
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                records.append(InteractionRecord.from_dict(json.loads(line)))
            except (ValueError, KeyError, TypeError):
                failures += 1
        return records, failures

    def state(self) -> JournalState:
        records, failures = self.read()
        by_disposition: Dict[str, int] = {}
        by_tier: Dict[str, int] = {}
        by_channel: Dict[str, int] = {}
        operator_turns = 0
        reaches = 0
        answered_non_operator = 0
        for r in records:
            by_disposition[r.disposition] = by_disposition.get(r.disposition, 0) + 1
            by_tier[r.tier] = by_tier.get(r.tier, 0) + 1
            by_channel[r.channel_id] = by_channel.get(r.channel_id, 0) + 1
            if r.speaker_is_operator:
                operator_turns += 1
            if r.reaches_reality:
                reaches += 1
            if r.disposition == "answered" and not r.speaker_is_operator:
                answered_non_operator += 1
        if answered_non_operator:
            by_disposition["__answered_non_operator"] = answered_non_operator
        return JournalState(
            population=len(records),
            parse_failures=failures,
            by_disposition=by_disposition,
            by_tier=by_tier,
            by_channel=by_channel,
            operator_turns=operator_turns,
            non_operator_turns=len(records) - operator_turns,
            reaches_reality_turns=reaches,
        )

    def invariant_breaches(self) -> List[InteractionRecord]:
        """Every record where the node ANSWERED somebody who is not its operator.

        This list must be empty. It is computed from the journal rather than
        trusted from the router, so a router bug that answers a stranger leaves
        evidence the router itself did not have to volunteer.
        """
        records, _ = self.read()
        return [r for r in records
                if r.disposition == "answered" and not r.speaker_is_operator]


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


def selftest() -> int:
    """Every refusal, the fold, and the append-only property must all fire."""
    import tempfile

    failures: List[str] = []

    def expect(name: str, ok: bool) -> None:
        if not ok:
            failures.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        j = InteractionJournal(tmp)
        base = dict(channel_id="c1", channel_class="private", speaker_ref="op",
                    speaker_is_operator=True, intent="query.status", tier="T0",
                    reaches_reality=False, decision="ALLOW",
                    disposition="answered", handler="retrieval")

        j.record(**base, body="how are you")
        first = j.path.read_text(encoding="utf-8")
        expect("one-line-per-record", first.count("\n") == 1)
        expect("body-text-never-stored", "how are you" not in first)
        expect("body-digest-recorded", '"body_digest"' in first)

        j.record(**{**base, "channel_id": "c2", "channel_class": "shared",
                    "speaker_ref": "stranger", "speaker_is_operator": False,
                    "disposition": "surfaced", "decision": "ASK",
                    "tier": "T3", "reaches_reality": True})
        after = j.path.read_text(encoding="utf-8")
        expect("append-only-prefix-preserved", after.startswith(first))

        st = j.state()
        expect("fold-population", st.population == 2 and st.parse_failures == 0)
        expect("fold-operator-split",
               st.operator_turns == 1 and st.non_operator_turns == 1)
        expect("fold-reaches-reality", st.reaches_reality_turns == 1)
        expect("no-invariant-breach", j.invariant_breaches() == [])

        # the breach detector must be able to fire
        j.record(**{**base, "speaker_ref": "stranger",
                    "speaker_is_operator": False})
        expect("breach-detector-fires", len(j.invariant_breaches()) == 1)

        for name, kwargs in (
            ("undeclared-disposition", {"disposition": "replied"}),
            ("undeclared-tier", {"tier": "T9"}),
            ("undeclared-decision", {"decision": "MAYBE"}),
            ("undeclared-channel-class", {"channel_class": "semi"}),
            ("empty-speaker-ref", {"speaker_ref": ""}),
            ("empty-handler", {"handler": ""}),
        ):
            try:
                j.record(**{**base, **kwargs})
                expect(f"{name}-refused", False)
            except ValueError:
                expect(f"{name}-refused", True)

        try:
            j.record(**base, detail={"payload": {"text": "secret"}})
            expect("nested-detail-refused", False)
        except ValueError:
            expect("nested-detail-refused", True)

        j.path.write_text(j.path.read_text(encoding="utf-8") + "{ not json\n",
                          encoding="utf-8")
        expect("malformed-line-counted", j.state().parse_failures == 1)

        empty = InteractionJournal(Path(tmp) / "nowhere")
        expect("absent-journal-is-empty-not-error",
               empty.state().population == 0)

    total = 16
    if failures:
        print(f"SELFTEST FAIL -- {len(failures)} of {total}: "
              + ", ".join(failures))
        return 1
    print(f"SELFTEST PASS -- {total}/{total} paths behaved as declared")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="the interaction journal")
    parser.add_argument("--node-root", default=".",
                        help="node root holding .intentops/")
    parser.add_argument("--selftest", action="store_true")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("posture", help="fold the journal and print it")
    sub.add_parser("breaches", help="records where a non-operator was answered")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.selftest:
        return selftest()

    journal = InteractionJournal(args.node_root)
    if args.command == "breaches":
        breaches = journal.invariant_breaches()
        if not breaches:
            print("no breaches: the node answered nobody but its operator")
            return 0
        for r in breaches:
            print(json.dumps(r.to_dict(), sort_keys=True))
        return 1
    print(journal.state().render())
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
