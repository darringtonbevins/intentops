"""S3 -- the append-only record of every decision this server made.

PURPOSE
    Model-visible means logged. Every request that reaches a tool is recorded,
    including the ones that were permitted and including the ones refused at
    the door for a bad token -- a log of refusals alone answers "what was
    stopped" and says nothing about "what was allowed", and the second question
    is the one an audit actually asks.

WRITE MODEL
    Append-only JSONL, one line per event, opened ``O_APPEND`` and written in a
    single ``write`` call per event. There is deliberately NO read-modify-write
    here, which is why it needs no lock: concurrent server processes each
    append their own line and none rewrites the file. State is a pure fold.

    The file is per-day (``.intentops/logs/mcp-saddle/YYYY-MM-DD.jsonl``) so
    rotation is a naming convention rather than a routine. A rotating deleter
    would be a knowledge-loss exit with nobody's name on it.

BLIND SPOTS
    * A presented token is NEVER logged, not even truncated -- a prefix of a
      credential is still credential material, and a log is the least fenced
      surface in the system. Only the boolean outcome is recorded.
    * The ledger records what this server DECIDED. It cannot record what the
      host then did with that decision. A host that asks for a verdict and
      ignores it leaves no trace here, which is the S2 gap this saddle states
      openly rather than papering over.
    * Timestamps come from the local clock. A clock that jumps produces an
      out-of-order journal and nothing here detects it.
    * A ledger that cannot be written raises. It is never swallowed: a
      governance surface whose audit trail is silently absent is worse than
      one that refuses to serve.
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

_LEDGER_DIR = Path(".intentops") / "logs" / "mcp-saddle"


class LedgerUnavailable(RuntimeError):
    """The ledger directory or file cannot be written."""


def ledger_path(node_root: Path | str, *, day: Optional[str] = None) -> Path:
    stamp = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return Path(node_root) / _LEDGER_DIR / f"{stamp}.jsonl"


def append_event(node_root: Path | str, event: Dict[str, Any]) -> Path:
    """Append one event. Returns the file written. Raises on failure."""
    path = ledger_path(node_root)
    row = dict(event)
    row.setdefault("at", datetime.now(timezone.utc).replace(microsecond=0).isoformat())
    row.setdefault("saddle", "mcp-hosted")
    line = json.dumps(row, ensure_ascii=False, default=str)
    if len(line.encode("utf-8")) > _MAX_EVENT_BYTES:
        row = {
            "at": row["at"],
            "saddle": row["saddle"],
            "kind": row.get("kind", "unknown"),
            "truncated": True,
            "original_bytes": len(line.encode("utf-8")),
        }
        line = json.dumps(row, ensure_ascii=False)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
        finally:
            os.close(fd)
    except OSError as exc:
        raise LedgerUnavailable(f"cannot append to {path}: {exc}") from exc
    return path
