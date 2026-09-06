"""The pre-tool hook -- the ONE place in IntentOps that refuses a live call.

PURPOSE
    This is the impure end of the whole system. Everything upstream of it
    returns values: the classifier returns a tier, the gate returns a Verdict,
    the councils return readings. Nothing upstream exits a process or writes a
    protocol frame, and that is what makes all of it testable and portable.

    Here, and nowhere else in this package, a Verdict becomes this host's own
    vocabulary: a decision object on stdout and a non-zero exit status. If you
    are looking for where IntentOps actually stops something, it is this file.

THE THREE RULES OF THIS FILE
    1. It fails CLOSED. A payload it cannot parse, a node it cannot read, a
       ledger it cannot append to -- each is a refusal, never a pass. The
       reason names the gate as the fault so the operator is not sent hunting
       through their own command.
    2. It never emits an ALLOW decision. On a clean verdict it stays silent and
       exits zero, which leaves the host's own permission rules to run. An
       adapter that returned "allow" would be auto-approving calls the operator
       had asked to be prompted about: this gate may raise the bar and may
       never lower it.
    3. It records BEFORE it emits. A decision that reached the host but not the
       ledger is an action nobody can answer for, so an unwritable ledger turns
       into a refusal rather than a quiet permit.

WRITE MODEL
    Delegated. The only write is the ledger append in ``ledger.py``, which
    declares its own model there (append-only JSONL, single-write appends, no
    read-modify-write, no lock).

BLIND SPOTS
    * Both refusal channels are used at once -- the decision frame AND exit 2 --
      because a host that understands only one still refuses. If a future host
      version treated a non-zero exit as a hook CRASH and ran the tool anyway,
      this file would be advisory and would not know it. That is a property of
      the host, and it is why the saddle contract makes "the host honours deny"
      a requirement on the runtime rather than a function in this package.
    * Timeouts are the host's. If the runtime kills this process before it
      decides, no verdict is emitted at all, and what happens next is the
      host's default rather than ours.
    * The gate sees one call at a time. A sequence of individually innocuous
      calls that together reach the world is not something this file can see.

CLI
    python -m intentops_saddle_claudecode.pre_tool          # reads stdin
    python -m intentops_saddle_claudecode.pre_tool --selftest
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional, Sequence

from intentops_core.gate import Decision, Verdict

from . import (
    SADDLE_ID,
    EstateUnreadable,
    LedgerUnavailable,
    MalformedPayload,
    decide,
    load_context,
    parse_pre_tool,
    record,
    refusal_for_internal_error,
    summary_of,
)

__all__ = ["EXIT_BLOCK", "EXIT_OK", "decision_frame", "main", "selftest"]

#: The host's own vocabulary. Zero lets the call continue to whatever the host
#: would normally do; two is this runtime's "the hook refused, and the reason
#: on stderr goes back to the model".
EXIT_OK = 0
EXIT_BLOCK = 2

_HOST_EVENT = "PreToolUse"


def decision_frame(verdict: Verdict) -> Optional[Dict[str, Any]]:
    """Render a Verdict as this host's decision object, or None for silence.

    ALLOW deliberately returns None. See rule 2 in the module docstring: an
    explicit "allow" here would override the operator's own permission
    settings, which is the one direction this gate must never move.
    """
    if verdict.decision is Decision.ALLOW:
        return None
    permission = "ask" if verdict.decision is Decision.ASK else "deny"
    return {
        "hookSpecificOutput": {
            "hookEventName": _HOST_EVENT,
            "permissionDecision": permission,
            "permissionDecisionReason": verdict.render(),
        }
    }


def _emit(verdict: Verdict, *, out=None, err=None) -> int:
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr
    frame = decision_frame(verdict)
    if frame is not None:
        print(json.dumps(frame, ensure_ascii=True), file=out)
    if verdict.decision in (Decision.BLOCK, Decision.HALT):
        print(f"IntentOps [{SADDLE_ID}] {verdict.render()}", file=err)
        return EXIT_BLOCK
    return EXIT_OK


def _apply_values_council(verdict: Verdict, event, context) -> Verdict:
    """Run step 0 and fold its answer into the verdict. Never raises.

    Three postures, and none of them is silence:

      * the council permits -> the verdict is returned unchanged, with the
        council's notes attached so the ledger records that step 0 ran;
      * the council refuses and its mode BLOCKS -> the verdict is escalated to
        BLOCK, carrying the council's own reasons;
      * the council itself breaks -> the call is escalated to ASK with the
        failure named. It is deliberately NOT a permit: an advisory member
        that vanishes on error is how a step becomes decorative.

    The council never RELAXES a verdict. A BLOCK stays a BLOCK whatever step 0
    thinks, because the tier decision above is the floor and this is a second
    opinion stacked on top of it, not a replacement for it.
    """
    if verdict.decision in (Decision.BLOCK, Decision.HALT):
        return verdict
    try:
        from intentops_core.governance import gate_values_council
    except Exception as exc:  # noqa: BLE001 - an absent council is a finding
        return _escalate(verdict, "the values council could not be imported, so "
                                  f"step 0 did not run: {type(exc).__name__}: {exc}")
    try:
        allowed, notes = gate_values_council(
            event.tool_name, event.tool_input, node_root=context.root,
            indicators=context.indicators,
        )
    except Exception as exc:  # noqa: BLE001 - same reason as decide()'s catch
        return _escalate(verdict, "the values council raised while reading this "
                                  f"call: {type(exc).__name__}: {exc}")
    if allowed:
        if notes:
            verdict.metadata.setdefault("values_council", list(notes))
        return verdict
    return Verdict(
        decision=Decision.BLOCK,
        tier=verdict.tier,
        reaches_reality=verdict.reaches_reality,
        reasons=tuple(verdict.reasons) + tuple(notes or
                      ("the values council refused this core-surface write",)),
        metadata={**verdict.metadata, "values_council": list(notes)},
    )


def _escalate(verdict: Verdict, reason: str) -> Verdict:
    """Raise a permit to ASK, naming why. A broken member is never a permit."""
    return Verdict(decision=Decision.ASK, tier=verdict.tier,
                   reaches_reality=verdict.reaches_reality,
                   reasons=tuple(verdict.reasons) + (reason,),
                   metadata=dict(verdict.metadata))


def run(stdin_text: str, *, out=None, err=None) -> int:
    """The whole hook, as a function, so it can be exercised without a process."""
    try:
        event = parse_pre_tool(stdin_text)
    except MalformedPayload as exc:
        return _emit(refusal_for_internal_error(str(exc)), out=out, err=err)

    try:
        context = load_context(event.cwd)
    except EstateUnreadable as exc:
        return _emit(refusal_for_internal_error(str(exc)), out=out, err=err)

    try:
        verdict = decide(event, context)
    # noqa: BLE001 is deliberate and load-bearing. Catching broadly here is the
    # whole point: ANY unexpected failure inside the gate -- including one no
    # author anticipated -- must become a refusal. A narrower except would let
    # an unforeseen exception escape, and an escaping exception in a pre-tool
    # hook is a permit with a stack trace attached.
    except Exception as exc:  # noqa: BLE001
        return _emit(refusal_for_internal_error(f"{type(exc).__name__}: {exc}"),
                     out=out, err=err)

    # Step 0 -- the values council, AFTER the tier decision and BEFORE the
    # verdict is emitted. Wave-2 verifier finding: the council, its core
    # surface, its shell classifier and its review ledger were all built and
    # tested, and NO host hook ever called `gate_values_council`, so step 0 was
    # unreachable in the one place it could act. A gate nothing invokes has
    # never once fired, and is indistinguishable from a gate that cannot.
    verdict = _apply_values_council(verdict, event, context)

    try:
        record(context.root, summary_of(verdict, event, context))
    except LedgerUnavailable as exc:
        return _emit(
            refusal_for_internal_error(
                f"the decision could not be recorded, so it will not be acted on: {exc}"
            ),
            out=out, err=err,
        )

    return _emit(verdict, out=out, err=err)


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


def selftest() -> int:
    """Prove the adapter can refuse, can permit, and fails closed when broken.

    A gate that has never been seen to refuse is indistinguishable from a gate
    that cannot, so each path gets a case that makes it fire. This runs against
    a throwaway node so it never touches a real ledger.
    """
    import io
    import tempfile
    from pathlib import Path

    failures: List[str] = []
    checks = 0

    def check(label: str, ok: bool) -> None:
        nonlocal checks
        checks += 1
        print(f"  [{'ok' if ok else 'FAIL'}] {label}")
        if not ok:
            failures.append(label)

    def run_payload(payload: Any, cwd: Path) -> tuple:
        text = payload if isinstance(payload, str) else json.dumps(payload)
        out, err = io.StringIO(), io.StringIO()
        code = run(text, out=out, err=err)
        return code, out.getvalue(), err.getvalue()

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".intentops").mkdir()

        def payload(tool: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
            return {"hook_event_name": _HOST_EVENT, "tool_name": tool,
                    "tool_input": tool_input, "cwd": str(root)}

        code, out, _ = run_payload(payload("Read", {"file_path": "README.md"}), root)
        check("a local read is not refused and emits no decision",
              code == EXIT_OK and out.strip() == "")

        code, out, _ = run_payload(payload("Bash", {"command": "git push origin main"}), root)
        check("a remote push is refused or escalated",
              code != EXIT_OK or "permissionDecision" in out)

        code, out, _ = run_payload(payload("Bash", {"command": "rm -rf ./build"}), root)
        check("a recursive delete is refused or escalated",
              code != EXIT_OK or "permissionDecision" in out)

        code, _, err = run_payload("this is not json", root)
        check("a malformed payload fails closed", code == EXIT_BLOCK and "gate" in err)

        code, _, err = run_payload({"hook_event_name": _HOST_EVENT, "tool_name": "Bash"}, root)
        check("a payload missing its arguments fails closed", code == EXIT_BLOCK)

        ledger_dir = root / ".intentops" / "logs" / "gates"
        check("every decision reached the ledger",
              ledger_dir.is_dir() and any(ledger_dir.glob("*.jsonl")))

        (root / ".intentops" / "halt.marker").write_text("", encoding="utf-8")
        code, out, err = run_payload(payload("Read", {"file_path": "README.md"}), root)
        check("a stood-down node refuses even a harmless read",
              code == EXIT_BLOCK and "deny" in out)
        check("the stand-down needs no reason to be effective", "stood down" in err.lower())

    print(f"\n{checks - len(failures)}/{checks} selftest checks behaved as declared")
    if failures:
        print("SELFTEST FAIL: " + "; ".join(failures))
        return 1
    print("SELFTEST PASS")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="IntentOps pre-tool gate for this host runtime.")
    parser.add_argument("--selftest", action="store_true",
                        help="prove the gate can refuse, then exit")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.selftest:
        return selftest()
    return run(sys.stdin.read())


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
