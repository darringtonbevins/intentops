"""S3 at the host's seam -- the events that record but never refuse.

PURPOSE
    Two of this host's hooks carry no refusal channel at all: the one that
    fires after a tool has already run, and the one that fires when a session
    ends. Nothing can be stopped from here, and pretending otherwise would be
    theatre. What they can do is complete the record, which is the whole of S3:
    the gate said something before the call, and this says what became of it.

    They are one module because they are one act with two labels. ``--event``
    is REQUIRED and has no default: an event that does not know which event it
    is writes a row nobody can interpret, and a default would pick for it.

WRITE MODEL
    Delegated to ``ledger.py`` -- append-only JSONL, one single-write append
    per event, no read-modify-write, therefore no lock.

BLIND SPOTS
    * The after-the-fact row is written by the same process the host chose to
      run. If the host never fires the hook -- because the session crashed, or
      the tool timed out -- there is simply no row, and nothing here can tell
      the difference between "did not happen" and "was not recorded".
    * A ledger failure here cannot be turned into a refusal, because the action
      has already happened. It exits non-zero instead, which this host surfaces
      as a visible non-blocking error. That is the loudest honest option: the
      failure is seen, and nothing is falsely claimed to have been prevented.

CLI
    python -m intentops_saddle_claudecode.observe --event post_tool
    python -m intentops_saddle_claudecode.observe --event stop
    python -m intentops_saddle_claudecode.observe --selftest
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional, Sequence

from . import SADDLE_ID, LedgerUnavailable, MalformedPayload, parse_event, record
from .node import resolve_root

__all__ = ["EVENTS", "main", "run", "selftest"]

#: Closed vocabulary. An undeclared event is a hard exit, never a default of
#: "record it as something".
EVENTS: Dict[str, str] = {
    "post_tool": "PostToolUse",
    "stop": "Stop",
}


def run(stdin_text: str, event_key: str, *, err=None) -> int:
    """Record one non-gating event. Returns 0 when recorded, 1 when not."""
    err = err if err is not None else sys.stderr
    if event_key not in EVENTS:
        print(f"IntentOps [{SADDLE_ID}] unknown event {event_key!r}; "
              f"declared events are {sorted(EVENTS)}", file=err)
        return 1

    expected = EVENTS[event_key]
    try:
        event = parse_event(stdin_text, expected=expected)
    except MalformedPayload as exc:
        print(f"IntentOps [{SADDLE_ID}] {expected}: unrecordable payload -- {exc}",
              file=err)
        return 1

    root, provenance = resolve_root(event.cwd)
    row: Dict[str, Any] = {
        "saddle": SADDLE_ID,
        "hook_event": event.event_name,
        "session_id": event.session_id,
        "tool": event.tool_name,
        "decision": "RECORDED",
        "node_root": str(root),
        "root_provenance": provenance,
    }
    if isinstance(event.raw, dict) and "tool_response" in event.raw:
        response = event.raw["tool_response"]
        row["tool_error"] = bool(
            isinstance(response, dict) and response.get("error") is not None
        )

    try:
        record(root, row)
    except LedgerUnavailable as exc:
        # Loud, and honest about what it does not mean: the action already
        # happened, so this is a lost record, not a prevented act.
        print(f"IntentOps [{SADDLE_ID}] {expected}: the record was LOST -- {exc}. "
              "The operation itself already ran; nothing was prevented.", file=err)
        return 1
    return 0


def selftest() -> int:
    """Prove both events record, and that the failure path is loud."""
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

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".intentops").mkdir()
        base = {"session_id": "s1", "cwd": str(root)}

        for key, name in EVENTS.items():
            err = io.StringIO()
            payload = dict(base, hook_event_name=name, tool_name="Bash",
                           tool_input={"command": "ls"})
            code = run(json.dumps(payload), key, err=err)
            check(f"{key} records and exits clean", code == 0 and err.getvalue() == "")

        ledger = list((root / ".intentops" / "logs" / "gates").glob("*.jsonl"))
        rows = ledger[0].read_text(encoding="utf-8").strip().splitlines() if ledger else []
        check("both events reached one append-only ledger", len(rows) == len(EVENTS))

        err = io.StringIO()
        check("an undeclared event is refused, not recorded as something",
              run("{}", "no_such_event", err=err) == 1 and "unknown event" in err.getvalue())

        err = io.StringIO()
        check("a malformed payload is loud and non-zero",
              run("not json", "stop", err=err) == 1 and "unrecordable" in err.getvalue())

    print(f"\n{checks - len(failures)}/{checks} selftest checks behaved as declared")
    if failures:
        print("SELFTEST FAIL: " + "; ".join(failures))
        return 1
    print("SELFTEST PASS")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Record a non-gating hook event for this host runtime.")
    parser.add_argument("--event", choices=sorted(EVENTS),
                        help="which hook fired (required; there is no default)")
    parser.add_argument("--selftest", action="store_true",
                        help="prove both events record, then exit")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.selftest:
        return selftest()
    if not args.event:
        parser.error("--event is required: an event that does not know which "
                     "event it is writes a row nobody can interpret")
    return run(sys.stdin.read(), args.event)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
