"""S3 -- the append-only record of every decision the gate made.

PURPOSE
    Model-visible means logged. This is the operation that turns a gate into
    something an operator, a council, or a later reader can audit: not only the
    refusals, but every decision, including the ones that let an action
    through. A log of refusals alone tells you what was stopped and nothing at
    all about what was permitted, and the second question is the one an audit
    actually asks.

WRITE MODEL
    Append-only JSONL, one line per event, opened ``O_APPEND`` and written in a
    single ``write`` call per event. There is deliberately NO read-modify-write
    anywhere in this module, which is why it needs no lock: concurrent hook
    processes each append their own line and none of them ever rewrites the
    file. State is a pure fold over the lines.

    The file is per-day (``.intentops/logs/gates/YYYY-MM-DD.jsonl``) so that
    rotation is a naming convention rather than a routine -- a rotating deleter
    would be a knowledge-loss exit with nobody's name on it.

BLIND SPOTS
    * Single-write append is atomic in practice for lines under the platform's
      pipe buffer and is what every other append-only store in this project
      relies on. A line longer than that could in principle interleave under
      heavy concurrency; the writer caps event size to keep well inside it.
    * The ledger records what the gate DECIDED. It does not and cannot record
      what the host then did with that decision. A host that ignores a refusal
      leaves no trace here, which is why S2 is a contract on the host and not
      merely a function in this package.
    * Timestamps come from the local clock. A clock that jumps produces a
      journal that is out of order, and nothing here detects it.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

__all__ = ["LedgerUnavailable", "append_event", "ledger_path"]

#: Events larger than this are truncated rather than written whole, so a single
#: enormous tool input cannot threaten the atomicity of an append.
_MAX_EVENT_BYTES = 16000


class LedgerUnavailable(RuntimeError):
    """The decision could not be recorded.

    Callers must treat this as fatal to the operation, not as a warning. An
    action taken without a record is an action nobody can answer for later, and
    the whole point of S3 is that there is no such action.
    """


def ledger_path(root: Path, *, day: Optional[str] = None) -> Path:
    stamp = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return root / ".intentops" / "logs" / "gates" / f"{stamp}.jsonl"


def append_event(root: Path, event: Dict[str, Any]) -> Path:
    """Append one event. Raises ``LedgerUnavailable`` if it cannot.

    The caller decides what an unrecordable decision means. In this adapter it
    means refusal: the gate would rather stop an action than take it off the
    record.
    """
    path = ledger_path(root)
    payload = dict(event)
    payload.setdefault("as_of", datetime.now(timezone.utc).isoformat())
    try:
        line = json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str)
    except (TypeError, ValueError) as exc:
        raise LedgerUnavailable(f"event is not serialisable: {exc}") from exc

    if len(line) > _MAX_EVENT_BYTES:
        trimmed = {k: v for k, v in payload.items() if k != "tool_input"}
        trimmed["tool_input"] = "[TRUNCATED: event exceeded the single-append size cap]"
        line = json.dumps(trimmed, ensure_ascii=True, sort_keys=True, default=str)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
        finally:
            os.close(fd)
    except OSError as exc:
        raise LedgerUnavailable(f"could not append to {path}: {exc}") from exc
    return path
