"""Witness register -- completion-grounding for heal-claims.

PURPOSE. A heal-claim ("this fix works") is a citation whose target artifact is
a PRODUCTION EFFECT. Until a live run answers it the claim is OPEN-UNVERIFIED:
committed, tested, reviewed, and still unproven. The register holds that
distinction in-band instead of letting a plausible-looking fix pass for a
confirmed one.

* ``register_claim`` records what was healed, the commit that landed it, the
  INSTRUMENT that can answer it (a runnable probe or command), and the
  CONDITION reality must show. Status: ``OPEN-UNVERIFIED``.
* ``answer_claim`` records reality's answer -- ``held`` True or False -- with
  the evidence observed and the witness who was present. A claim answers ONCE;
  re-answering is refused, because a second answer is a NEW claim about a new
  run.
* ``open_claims`` is the honest debt list: everything the node believes it fixed
  that reality has not yet confirmed.

WRITE MODEL (declared at birth, per the store-write discipline). Append-only
event journal at ``<node>/.intentops/witness/register.jsonl``. One writer
appends under a kernel-released ``StoreLock``; state is a pure FOLD of events;
nothing ever rewrites the file. The lock is held across the whole
load-validate-append sequence and state is loaded INSIDE it -- a value read
before acquisition is stale by definition, and a lock that wraps only the write
lets two callers both observe OPEN-UNVERIFIED and both append an answer.

There is NO unlocked fallback. If ``StoreLock`` cannot be imported the append is
REFUSED, loudly. A silent lockless append is exactly the defect class the write
discipline exists to prevent.

ROUTE, NEVER VERDICT. The register gates nothing at runtime. An OPEN-UNVERIFIED
claim is information for humans and checklists, not a block.

BLIND SPOTS.

1. The register records that a claim was ANSWERED, not that the answer was
   competent. A shallow instrument answering "held" is indistinguishable here
   from a thorough one; the ``instrument`` and ``evidence`` strings are what a
   reader must judge.
2. It cannot notice a heal that was never registered. Coverage is exactly what
   was filed, so a low open-claim count may mean discipline or may mean nobody
   filed anything -- read it beside the number of claims registered.
3. A malformed journal line is surfaced in ``parse_failures`` and the fold
   continues; the state it would have contributed is LOST, and the count says
   so rather than the line being skipped in silence.
4. Nothing here expires a claim. An OPEN-UNVERIFIED claim stays open forever
   until reality answers it, by design: a timeout would answer a question a
   human was asked.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

try:  # pragma: no cover - import shim
    from intentops_core.store_guard import StoreLock, lock_for
except ImportError:  # pragma: no cover
    StoreLock = None  # type: ignore[assignment]
    lock_for = None  # type: ignore[assignment]

__all__ = [
    "WitnessClaim", "RegisterState", "WitnessRegister", "REGISTER_RELPATH",
    "OPEN_UNVERIFIED", "ANSWERED_TRUE", "ANSWERED_FALSE", "selftest", "main",
]

REGISTER_RELPATH = Path(".intentops") / "witness" / "register.jsonl"

OPEN_UNVERIFIED = "OPEN-UNVERIFIED"
ANSWERED_TRUE = "ANSWERED-TRUE"
ANSWERED_FALSE = "ANSWERED-FALSE"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class WitnessClaim:
    """A heal-claim and, once reality answers, its answer."""

    claim_id: str
    title: str
    healed: str            # what was healed, in one sentence
    commit: str            # the commit that landed the fix
    instrument: str        # runnable probe that can answer the claim
    condition: str         # what reality must show for the claim to hold
    registered_at: str
    registered_by: str
    status: str = OPEN_UNVERIFIED
    answered_at: Optional[str] = None
    held: Optional[bool] = None
    evidence: Optional[str] = None
    witness: Optional[str] = None

    def to_row(self) -> Dict[str, object]:
        return {
            "claim_id": self.claim_id, "title": self.title,
            "healed": self.healed, "commit": self.commit,
            "instrument": self.instrument, "condition": self.condition,
            "registered_at": self.registered_at,
            "registered_by": self.registered_by, "status": self.status,
            "answered_at": self.answered_at, "held": self.held,
            "evidence": self.evidence, "witness": self.witness,
        }


@dataclass
class RegisterState:
    """Fold of the event journal. ``parse_failures`` is loud, never dropped."""

    claims: Dict[str, WitnessClaim] = field(default_factory=dict)
    parse_failures: List[str] = field(default_factory=list)

    @property
    def open(self) -> List[WitnessClaim]:
        return [c for c in self.claims.values() if c.status == OPEN_UNVERIFIED]


class WitnessRegister:
    """Append-only witness register. See the module docstring for the write
    model, which is declared there rather than here so a reader meets it before
    the first method."""

    def __init__(self, workspace: Path | str = ".") -> None:
        self.workspace = Path(workspace)
        self.path = self.workspace / REGISTER_RELPATH
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # -- journal ---------------------------------------------------------
    def _lock_path(self) -> Path:
        if lock_for is not None:
            return lock_for(self.path)
        return self.path.with_name(self.path.name + ".lock")

    def _append_checked(self, build: Callable[[RegisterState], Tuple[dict, object]]):
        """Acquire the lock, load state FRESH inside it, then run ``build`` to
        validate the invariant and produce the event to append.

        ``build`` returns ``(event_dict, return_value)``; a ``ValueError`` it
        raises propagates before anything is written.
        """
        if StoreLock is None:
            raise RuntimeError(
                "witness register append refused: StoreLock "
                "(intentops_core.store_guard) could not be imported, and "
                "appending without it risks interleaved writes")
        with StoreLock(self._lock_path()):
            state = self.load()
            event, result = build(state)
            line = json.dumps(event, ensure_ascii=False)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return result

    def load(self) -> RegisterState:
        state = RegisterState()
        if not self.path.exists():
            return state
        for n, raw in enumerate(
                self.path.read_text(encoding="utf-8").splitlines(), start=1):
            if not raw.strip():
                continue
            try:
                ev = json.loads(raw)
            except (ValueError, TypeError) as exc:
                state.parse_failures.append(f"line {n}: {exc}")
                continue
            if not isinstance(ev, dict):
                state.parse_failures.append(f"line {n}: not a JSON object")
                continue
            kind = ev.get("event")
            if kind == "claim_registered":
                row = {k: v for k, v in ev.items() if k != "event"}
                try:
                    claim = WitnessClaim(**row)
                except TypeError as exc:
                    state.parse_failures.append(f"line {n}: bad claim row: {exc}")
                    continue
                state.claims[claim.claim_id] = claim
            elif kind == "claim_answered":
                claim = state.claims.get(str(ev.get("claim_id")))
                if claim is None:
                    state.parse_failures.append(
                        f"line {n}: answer for unknown claim "
                        f"{ev.get('claim_id')!r}")
                    continue
                claim.status = ANSWERED_TRUE if ev.get("held") else ANSWERED_FALSE
                claim.answered_at = ev.get("answered_at")
                claim.held = bool(ev.get("held"))
                claim.evidence = ev.get("evidence")
                claim.witness = ev.get("witness")
            else:
                state.parse_failures.append(f"line {n}: unknown event {kind!r}")
        return state

    # -- API -------------------------------------------------------------
    def register_claim(self, claim_id: str, title: str, healed: str,
                       commit: str, instrument: str, condition: str,
                       registered_by: str) -> WitnessClaim:
        """File a heal-claim. A claim with no instrument is refused: it is cheap
        talk, and cheap talk cannot be answered."""
        if not str(instrument).strip():
            raise ValueError(
                "a heal-claim with no instrument is cheap talk -- name the "
                "runnable probe that can answer it")
        if not str(condition).strip():
            raise ValueError(
                "a heal-claim with no condition cannot be answered -- state "
                "what reality must show")

        def build(state: RegisterState) -> Tuple[dict, WitnessClaim]:
            if claim_id in state.claims:
                raise ValueError(
                    f"claim {claim_id!r} already registered (the journal is "
                    "append-only; a new run is a new claim id)")
            claim = WitnessClaim(
                claim_id=claim_id, title=title, healed=healed, commit=commit,
                instrument=instrument, condition=condition,
                registered_at=_now(), registered_by=registered_by)
            return {"event": "claim_registered", **claim.to_row()}, claim

        return self._append_checked(build)

    def answer_claim(self, claim_id: str, held: bool, evidence: str,
                     witness: str) -> WitnessClaim:
        """Record reality's answer, once."""
        if not str(evidence).strip():
            raise ValueError("an answer without observed evidence is not an answer")

        def build(state: RegisterState) -> Tuple[dict, WitnessClaim]:
            claim = state.claims.get(claim_id)
            if claim is None:
                raise ValueError(f"unknown claim {claim_id!r}")
            if claim.status != OPEN_UNVERIFIED:
                raise ValueError(
                    f"claim {claim_id!r} already answered ({claim.status}) -- "
                    "a second answer is a new claim about a new run")
            answered_at = _now()
            claim.status = ANSWERED_TRUE if held else ANSWERED_FALSE
            claim.answered_at = answered_at
            claim.held = bool(held)
            claim.evidence = evidence
            claim.witness = witness
            return ({"event": "claim_answered", "claim_id": claim_id,
                     "answered_at": answered_at, "held": bool(held),
                     "evidence": evidence, "witness": witness}, claim)

        return self._append_checked(build)

    def open_claims(self) -> List[WitnessClaim]:
        return self.load().open


# ---------------------------------------------------------------------------
# selftest -- a detector that has never fired is indistinguishable from a
# broken one
# ---------------------------------------------------------------------------


def selftest() -> Tuple[bool, str]:
    """Prove every refusal and every state transition can actually fire."""
    import tempfile

    failures: List[str] = []

    def check(label: str, cond: bool) -> None:
        if not cond:
            failures.append(label)

    def refuses(label: str, fn: Callable[[], object]) -> None:
        try:
            fn()
        except ValueError:
            return
        except Exception as exc:  # pragma: no cover - defensive
            failures.append(f"{label} (raised {exc.__class__.__name__}, wanted ValueError)")
            return
        failures.append(label)

    if StoreLock is None:
        return False, ("StoreLock is unavailable, so every append would be "
                       "REFUSED -- the register cannot be exercised here")

    with tempfile.TemporaryDirectory() as td:
        reg = WitnessRegister(td)

        refuses("a claim with no instrument was accepted",
                lambda: reg.register_claim("W-1", "t", "h", "abc123", "  ", "c", "me"))
        refuses("a claim with no condition was accepted",
                lambda: reg.register_claim("W-1", "t", "h", "abc123", "probe", "", "me"))

        claim = reg.register_claim("W-1", "gate refuses", "wired the gate",
                                   "abc1234", "intentops gate --selftest",
                                   "a gated op is blocked and logged", "node")
        check("a fresh claim is not OPEN-UNVERIFIED", claim.status == OPEN_UNVERIFIED)
        check("open_claims did not see the new claim",
              [c.claim_id for c in reg.open_claims()] == ["W-1"])

        refuses("a duplicate claim id was accepted",
                lambda: reg.register_claim("W-1", "t", "h", "c", "i", "cond", "me"))
        refuses("an answer with no evidence was accepted",
                lambda: reg.answer_claim("W-1", True, "   ", "me"))
        refuses("an answer for an unknown claim was accepted",
                lambda: reg.answer_claim("W-404", True, "seen", "me"))

        answered = reg.answer_claim("W-1", True, "block observed in the log", "operator")
        check("ANSWERED-TRUE did not fire", answered.status == ANSWERED_TRUE)
        check("an answered claim is still open", reg.open_claims() == [])

        refuses("a second answer was accepted",
                lambda: reg.answer_claim("W-1", False, "again", "me"))

        reg.register_claim("W-2", "t2", "h2", "def5678", "probe", "cond", "node")
        false_ans = reg.answer_claim("W-2", False, "the probe did not clear", "operator")
        check("ANSWERED-FALSE did not fire", false_ans.status == ANSWERED_FALSE)

        # a malformed line must be SURFACED, never silently skipped
        with open(reg.path, "a", encoding="utf-8") as fh:
            fh.write("{not json at all\n")
            fh.write(json.dumps({"event": "who_knows"}) + "\n")
            fh.write(json.dumps({"event": "claim_answered",
                                 "claim_id": "W-999", "held": True}) + "\n")
        state = reg.load()
        check("a malformed journal line was silently skipped",
              len(state.parse_failures) == 3)
        check("the fold lost good claims because of a bad line",
              len(state.claims) == 2)

    if failures:
        return False, f"{len(failures)} check(s) failed: " + "; ".join(failures)
    return True, ("register/answer/open transitions fire; six refusals fire "
                  "(no instrument, no condition, duplicate id, no evidence, "
                  "unknown claim, second answer); three malformed journal "
                  "lines are surfaced and the fold survives them")


# ---------------------------------------------------------------------------
# CLI -- observe mode; gates nothing
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Witness register -- heal-claims awaiting reality's answer")
    ap.add_argument("--workspace", default=".", help="node repository root")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="all claims plus loud parse failures")
    sub.add_parser("open", help="claims reality has not answered yet")
    sub.add_parser("selftest", help="prove every path can fire")
    reg = sub.add_parser("register")
    for name in ("claim-id", "title", "healed", "commit", "instrument",
                 "condition", "by"):
        reg.add_argument(f"--{name}", required=True)
    ans = sub.add_parser("answer")
    ans.add_argument("--claim-id", required=True)
    ans.add_argument("--held", required=True, choices=["true", "false"])
    ans.add_argument("--evidence", required=True)
    ans.add_argument("--witness", required=True)
    args = ap.parse_args(argv)

    if args.cmd == "selftest":
        ok, msg = selftest()
        print(f"{'PASS' if ok else 'FAIL'} witness selftest: {msg}")
        return 0 if ok else 1

    r = WitnessRegister(args.workspace)
    if args.cmd == "register":
        c = r.register_claim(
            claim_id=args.claim_id, title=args.title, healed=args.healed,
            commit=args.commit, instrument=args.instrument,
            condition=args.condition, registered_by=args.by)
        print(f"registered {c.claim_id}: {c.status}")
        return 0
    if args.cmd == "answer":
        c = r.answer_claim(args.claim_id, held=(args.held == "true"),
                           evidence=args.evidence, witness=args.witness)
        print(f"answered {c.claim_id}: {c.status}")
        return 0

    state = r.load()
    rows = state.open if args.cmd == "open" else list(state.claims.values())
    for c in rows:
        print(f"[{c.status}] {c.claim_id} -- {c.title} (commit {c.commit})")
        if c.status == OPEN_UNVERIFIED:
            print(f"    instrument: {c.instrument}")
            print(f"    condition:  {c.condition}")
        else:
            print(f"    answered {c.answered_at} by {c.witness}: {c.evidence}")
    if state.parse_failures:
        print(f"WARN: {len(state.parse_failures)} journal line(s) failed to "
              "parse -- not silently dropped:", file=sys.stderr)
        for fail in state.parse_failures:
            print(f"  {fail}", file=sys.stderr)
    print(f"{len(rows)} claim(s); {len(state.open)} open")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
