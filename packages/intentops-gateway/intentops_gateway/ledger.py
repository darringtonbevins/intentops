"""The append-only record of every gateway request, refusals included.

PURPOSE
    Model-visible means logged. Every request that reaches the gateway is
    recorded -- the ones refused at the door for a missing token, the ones
    refused by the backend allowlist, the ones that produced a verdict, and the
    ones that were served. A log of refusals alone answers "what was stopped"
    and says nothing about "what was allowed", and the second question is the
    one an audit actually asks.

WRITE MODEL
    Append-only JSONL (store-write-discipline model 3's write half), one line
    per event, under a kernel-released ``StoreLock`` held across the append.

    Two writers are in scope and they need different things. Across PROCESSES
    an ``O_APPEND`` write of a single short line is already atomic on both
    platforms we support; the lock is what makes the guarantee hold for a line
    that outgrows a pipe buffer, and it is cheap. Within ONE process the
    gateway is threaded, so a threading lock is taken FIRST -- ``StoreLock``
    is a byte-range lock on a sibling file and a second thread would otherwise
    spin against its own process for the full timeout.

    State is a pure fold: nothing here ever rewrites or truncates the file, so
    there is no read-modify-write and no last-write-wins window.

BLIND SPOTS
    * A presented token is NEVER logged, not even truncated -- a prefix of a
      credential is still credential material, and a log is the least fenced
      surface in the system. Only the boolean outcome is recorded.
    * Tool ARGUMENTS are not logged. They are the most likely carrier of
      estate or client material, and a gateway audit trail that quietly
      accumulates them is a data store nobody declared. Argument KEYS are
      recorded, which is enough to reconstruct what was called without
      recording what it was called with.
    * The ledger records what the GATEWAY decided. It cannot record what a
      harness then did, or what it did without asking. That is the stated S2
      gap, not a defect of this file.
    * Timestamps come from the local clock. A clock that jumps produces an
      out-of-order journal and nothing here detects it.
    * A ledger that cannot be written RAISES. It is never swallowed: a
      governance surface whose audit trail is silently absent is worse than
      one that refuses to serve.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional

from intentops_core.store_guard import StoreLock, lock_for

__all__ = [
    "LEDGER_RELPATH",
    "LedgerUnavailable",
    "append_event",
    "ledger_path",
    "read_events",
    "safe_arg_keys",
]

LEDGER_RELPATH = Path(".intentops") / "logs" / "gateway-requests.jsonl"

#: Events larger than this are replaced by a truncation stub rather than
#: written whole, so one enormous payload cannot threaten an append.
_MAX_EVENT_BYTES = 16_000

#: Intra-process serialisation. See the WRITE MODEL note above.
_THREAD_LOCK = threading.Lock()


class LedgerUnavailable(RuntimeError):
    """The ledger file cannot be written. Raised, never swallowed."""


def ledger_path(node_root: Path | str) -> Path:
    return Path(node_root) / LEDGER_RELPATH


def safe_arg_keys(args: Optional[Mapping[str, Any]]) -> List[str]:
    """The KEYS of a tool's arguments, sorted. Never the values.

    Exists as a named function rather than an inline comprehension so that the
    rule -- keys travel, values never do -- has one place to be read and one
    place to be changed, and so a test can point at it.
    """
    if not isinstance(args, Mapping):
        return []
    return sorted(str(k) for k in args.keys())


def append_event(node_root: Path | str, event: Dict[str, Any]) -> Path:
    """Append one event. Returns the file written. Raises on failure."""
    path = ledger_path(node_root)
    row = dict(event)
    row.setdefault("at", datetime.now(timezone.utc).replace(microsecond=0).isoformat())
    row.setdefault("surface", "gateway")
    if "token" in row or "authorization" in row:
        # Defence in depth: no caller should ever pass one, and if one does the
        # ledger is not where it lands.
        raise LedgerUnavailable(
            "a credential-shaped field reached the gateway ledger; the append "
            "is refused rather than written -- fix the caller"
        )
    line = json.dumps(row, ensure_ascii=False, default=str)
    if len(line.encode("utf-8")) > _MAX_EVENT_BYTES:
        row = {
            "at": row["at"],
            "surface": row["surface"],
            "kind": row.get("kind", "unknown"),
            "truncated": True,
            "original_bytes": len(line.encode("utf-8")),
        }
        line = json.dumps(row, ensure_ascii=False)
    payload = (line + "\n").encode("utf-8")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _THREAD_LOCK:
            with StoreLock(lock_for(path)):
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                try:
                    os.write(fd, payload)
                finally:
                    os.close(fd)
    except OSError as exc:
        raise LedgerUnavailable(f"cannot append to {path}: {exc}") from exc
    return path


def read_events(node_root: Path | str) -> Iterator[Dict[str, Any]]:
    """Fold the ledger. A malformed line is YIELDED as a marked row.

    A reader that silently skips an unparseable line removes it from the
    denominator, and the resulting count looks better for exactly the wrong
    reason.
    """
    path = ledger_path(node_root)
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8") as fh:
        for number, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as exc:
                yield {"kind": "malformed", "line": number, "error": str(exc)}
                continue
            if not isinstance(row, dict):
                yield {"kind": "malformed", "line": number,
                       "error": "line is not a JSON object"}
                continue
            yield row
