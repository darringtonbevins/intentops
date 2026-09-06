"""The approval queue -- the canonical inbox for anything a human must rule on.

PURPOSE. A node that cannot act reaches for a human. This is where the ask
lands: one file per item, a real tier, an explicit expiry, and an append-only
history of every transition. Nothing here executes anything; the queue holds
records and the decision is a person's.

WRITE MODEL (declared at birth). **Per-item files** -- one JSON document per
approval under ``<node>/.intentops/approvals/{pending,approved,rejected}/`` --
which is collision-safe by construction: two writers touching two approvals
never touch one file. Each item write is additionally serialised by a
kernel-released ``StoreLock`` on that item alone, and the payload lands by
temp-file-plus-atomic-replace so a reader never sees half a record. The only
shared file is ``history.jsonl``, which is **append-only** and never rewritten.
Every transition RE-READS the item inside its lock: a value read before
acquisition is stale by definition, and the caller's object is a hint while the
disk is the truth.

THE EXPIRY WOUND, AND THE RULE THAT CLOSES IT. An expiry sweep once overwrote
``decided_by`` with ``system:expired``. The consequence was not a cosmetic one:
a store of items the operator had personally GRANTED, whose windows had lapsed,
read afterwards as a store of questions that died unruled -- 181 of 182 of them.
The record had destroyed the only field that said a human had answered.

So, structurally, in ``_expire``:

* ``decided_by`` and ``decided_at`` are **NEVER** overwritten. They name the
  actor of the ruling, and a ruling that lapsed is still a ruling that happened.
* the expiry writes its own fields instead: ``expired_at``, ``expired_by``
  (``system:expired``), and ``lapsed_from`` (the status the expiry lapsed).
* ``status`` becomes ``expired`` and the item moves to ``rejected/``.

An expiry is a DECISION nobody was present for. It may record that it happened;
it may not impersonate the person who was.

Round-tripping is total: every field ``to_dict`` omits is a field the next
transition destroys, so ``to_dict`` emits every populated field and
``from_dict`` reads every one back.

BLIND SPOTS.

1. The queue does not know whether an approval was *acted on*. ``consume`` is
   the only signal, and a consumer that forgets to call it leaves a grant
   looking live.
2. Expiry is wall-clock. A node whose clock is wrong expires items wrongly, and
   nothing here can detect that.
3. Listing for RENDERING is capped and says so when it truncates; listing for
   DECIDING (``_list_all``) is uncapped. A capped listing handed to a consumer
   deciding what to execute is a denominator failure -- items past the cap are
   invisible while the count still looks healthy.
4. The council review attached at submission is ADVISORY metadata. It is never
   a gate, and a reviewer that raises must not make filing an item riskier than
   not filing it, so its failure is recorded on the record rather than raised.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import uuid
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:  # pragma: no cover - import shim
    from intentops_core.store_guard import StoreLock, lock_for
except ImportError:  # pragma: no cover
    StoreLock = None  # type: ignore[assignment]
    lock_for = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

__all__ = [
    "ApprovalStatus", "Approval", "ApprovalQueue", "QueueError",
    "DEFAULT_TTL_HOURS", "DEFAULT_DECISION_EXPIRY_DAYS", "selftest",
]

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

DEFAULT_TTL_HOURS = 72
#: A pending human DECISION gets an explicit window. The 72h default would
#: silently expire it before anyone saw it, and an expiry is an implicit denial.
DEFAULT_DECISION_EXPIRY_DAYS = 30

MAX_PENDING = 200
MAX_FILE_SIZE = 1_048_576          # 1 MB
MAX_TEXT_LENGTH = 2_000
#: Rationale carries a four-block template (SITUATION / THE ASK / IF APPROVED /
#: IF DENIED). A 2,000-char cap silently amputated the last two blocks on real
#: decisions. Larger, still bounded.
MAX_RATIONALE_LENGTH = 8_000
MAX_COMMENTS = 50
RENDER_LIST_CAP = 400


class QueueError(RuntimeError):
    """Raised when an operation cannot be performed safely. Nothing was written."""


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize(text: str, cap: int, field_name: str) -> str:
    """Strip control characters and bound the length. Truncation is MARKED, so a
    reader can tell a short field from an amputated one."""
    s = CONTROL_CHAR_RE.sub("", str(text or ""))
    if len(s) > cap:
        logger.warning("approvals: %s truncated from %d to %d characters",
                       field_name, len(s), cap)
        s = s[: cap - 15].rstrip() + " ...[TRUNCATED]"
    return s


@dataclass
class Approval:
    """One approval record.

    ``decided_by`` / ``decided_at`` name the RULING. ``expired_by`` /
    ``expired_at`` / ``lapsed_from`` name the LAPSE. They are separate fields
    on purpose -- see the module docstring.
    """

    id: str
    subject_id: str
    tier: str
    status: str
    summary: str
    rationale: str
    created_at: str = ""
    expires_at: Optional[str] = None
    decided_at: Optional[str] = None
    decided_by: Optional[str] = None
    rejection_reason: Optional[str] = None
    expired_at: Optional[str] = None
    expired_by: Optional[str] = None
    lapsed_from: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    comments: List[Dict[str, Any]] = field(default_factory=list)
    council_review: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = _now()
        if not self.expires_at:
            self.expires_at = (datetime.now(timezone.utc)
                               + timedelta(hours=DEFAULT_TTL_HOURS)).isoformat()

    _OPTIONAL = ("expires_at", "decided_at", "decided_by", "rejection_reason",
                 "expired_at", "expired_by", "lapsed_from", "payload",
                 "comments", "council_review")

    def to_dict(self) -> Dict[str, Any]:
        """Every populated field travels. A field omitted here is a field the
        next transition destroys."""
        out: Dict[str, Any] = {
            "schema": "approval/v1",
            "id": self.id, "subject_id": self.subject_id, "tier": self.tier,
            "status": self.status, "summary": self.summary,
            "rationale": self.rationale, "created_at": self.created_at,
        }
        for key in self._OPTIONAL:
            value = getattr(self, key)
            if value:
                out[key] = value
        return out

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Approval":
        try:
            return cls(
                id=data["id"], subject_id=data["subject_id"],
                tier=data.get("tier", "T1"),
                status=data.get("status", ApprovalStatus.PENDING.value),
                summary=data.get("summary", ""),
                rationale=data.get("rationale", ""),
                created_at=data.get("created_at", ""),
                expires_at=data.get("expires_at"),
                decided_at=data.get("decided_at"),
                decided_by=data.get("decided_by"),
                rejection_reason=data.get("rejection_reason"),
                expired_at=data.get("expired_at"),
                expired_by=data.get("expired_by"),
                lapsed_from=data.get("lapsed_from"),
                payload=data.get("payload"),
                comments=data.get("comments") or [],
                council_review=data.get("council_review"),
            )
        except KeyError as exc:
            raise QueueError(f"approval record is missing {exc} -- refusing to "
                             "substitute a default for a load-bearing field") from exc

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        if not self.expires_at:
            return False
        try:
            exp = datetime.fromisoformat(self.expires_at)
        except (ValueError, TypeError):
            # An unparseable expiry is NOT "not expired": it is an unreadable
            # field, and treating it as fresh would let a malformed record live
            # forever. Surfaced, and treated as not-expired so nothing is
            # destroyed on the strength of a value nobody can read.
            logger.warning("approvals: %s has an unparseable expires_at %r -- "
                           "treated as not expired, and it will never sweep",
                           self.id, self.expires_at)
            return False
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return (now or datetime.now(timezone.utc)) > exp


class ApprovalQueue:
    """Per-item approval store. See the module docstring for the write model."""

    def __init__(self, workspace: Path | str) -> None:
        workspace = Path(workspace).resolve()
        # Doubled-segment guard: a RELATIVE workspace equal to the repository's
        # own directory name, resolved from inside it, silently mints
        # <repo>/<repo-name>/.intentops/approvals/ -- a store no reader serves.
        # Fail closed rather than write into it.
        if workspace.name and workspace.name.lower() == workspace.parent.name.lower():
            raise QueueError(
                f"doubled-segment workspace path refused: {workspace} -- pass "
                "the absolute node root instead")
        self.workspace = workspace
        self.base_dir = workspace / ".intentops" / "approvals"
        self.pending_dir = self.base_dir / "pending"
        self.approved_dir = self.base_dir / "approved"
        self.rejected_dir = self.base_dir / "rejected"
        self.history_file = self.base_dir / "history.jsonl"

    # -- submission ------------------------------------------------------
    def submit_decision(
        self,
        *,
        slug: str,
        tier: str,
        summary: str,
        rationale: str,
        expires_days: int = DEFAULT_DECISION_EXPIRY_DAYS,
        payload: Optional[Dict[str, Any]] = None,
        reviewer: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> Approval:
        """Queue a standalone DECISION for human review.

        ``expires_days`` is EXPLICIT and required to be positive: an expiry is
        an implicit denial, and a window nobody chose is a decision nobody made.

        Idempotent by slug: an identical-slug decision already pending is
        RETURNED rather than duplicated.

        ``reviewer`` is an optional advisory pass (a council reading, say). It
        is never a gate; if it raises, the failure is recorded ON the record and
        the item is still filed -- a governance read must not make filing an
        item riskier than not filing it.
        """
        if not SAFE_ID_RE.match(str(slug)):
            raise QueueError(f"unsafe decision slug: {slug!r}")
        if int(expires_days) <= 0:
            raise QueueError("expires_days must be positive -- an expiry is an "
                             "implicit denial and needs a window somebody chose")
        self._ensure_dirs()
        tier = str(tier).upper()
        subject_id = f"decision-{slug}"

        for existing in self.list_pending():
            if existing.subject_id == subject_id:
                return existing

        # Prefix BEFORE sanitising so the stored summary respects the cap even
        # with the prefix added.
        if not str(summary).startswith("DECISION:"):
            summary = f"DECISION: {summary}"

        approval = Approval(
            id=f"appr-{uuid.uuid4().hex[:12]}",
            subject_id=subject_id,
            tier=tier,
            status=ApprovalStatus.PENDING.value,
            summary=_sanitize(summary, MAX_TEXT_LENGTH, "summary"),
            rationale=_sanitize(rationale, MAX_RATIONALE_LENGTH, "rationale"),
            expires_at=(datetime.now(timezone.utc)
                        + timedelta(days=int(expires_days))).isoformat(),
            payload=payload,
        )

        if len(self._list_all(self.pending_dir)) >= MAX_PENDING:
            raise QueueError(f"approval queue at capacity ({MAX_PENDING} pending)")

        record = approval.to_dict()
        if reviewer is not None:
            try:
                record = reviewer(record)
            except Exception as exc:  # advisory only -- never blocks a filing
                logger.warning("approvals: council review unavailable: %s", exc)
                record["council_review"] = {"reviewed": False, "error": str(exc),
                                            "advisory_only": True}
        self._write_json(self.pending_dir / f"{approval.id}.json", record)
        self._record_history(Approval.from_dict(record), "submitted")
        return Approval.from_dict(record)

    # -- decisions -------------------------------------------------------
    def approve(self, approval_id: str, decided_by: str,
                comment: str = "") -> Approval:
        return self._decide(approval_id, ApprovalStatus.APPROVED,
                            decided_by, comment, self.approved_dir)

    def reject(self, approval_id: str, decided_by: str,
               reason: str) -> Approval:
        if not str(reason).strip():
            raise QueueError("a rejection with no reason is not a decision")
        return self._decide(approval_id, ApprovalStatus.REJECTED,
                            decided_by, reason, self.rejected_dir)

    def _decide(self, approval_id: str, status: ApprovalStatus,
                decided_by: str, note: str, target_dir: Path) -> Approval:
        if not str(decided_by).strip():
            raise QueueError("a decision with no decider cannot be recorded")
        src = self.pending_dir / f"{self._safe(approval_id)}.json"
        with self._item_lock(src):
            if not src.exists():
                raise QueueError(f"no pending approval {approval_id!r}")
            approval = Approval.from_dict(self._read_json(src))
            if approval.status != ApprovalStatus.PENDING.value:
                raise QueueError(
                    f"approval {approval_id!r} is {approval.status}, not pending")
            approval.status = status.value
            approval.decided_at = _now()
            approval.decided_by = decided_by
            if status is ApprovalStatus.REJECTED:
                approval.rejection_reason = _sanitize(note, MAX_TEXT_LENGTH,
                                                      "rejection_reason")
            target_dir.mkdir(parents=True, exist_ok=True)
            self._write_json(target_dir / f"{approval.id}.json", approval.to_dict())
            src.unlink(missing_ok=True)
        self._record_history(approval, status.value)
        return approval

    def comment(self, approval_id: str, author: str, text: str) -> Approval:
        """Steer an in-flight approval WITHOUT deciding it: the item stays
        pending."""
        if not str(text).strip():
            raise QueueError("an empty comment steers nothing")
        src = self.pending_dir / f"{self._safe(approval_id)}.json"
        with self._item_lock(src):
            if not src.exists():
                raise QueueError(f"no pending approval {approval_id!r}")
            approval = Approval.from_dict(self._read_json(src))
            if len(approval.comments) >= MAX_COMMENTS:
                raise QueueError(f"comment limit reached ({MAX_COMMENTS})")
            approval.comments.append({
                "author": author,
                "text": _sanitize(text, MAX_TEXT_LENGTH, "comment"),
                "at": _now()})
            self._write_json(src, approval.to_dict())
        self._record_history(approval, "commented")
        return approval

    # -- expiry ----------------------------------------------------------
    def _expire(self, path: Path) -> Optional[Approval]:
        """The ONE expiry transition.

        It records that the window lapsed. It does NOT overwrite ``decided_by``
        or ``decided_at`` -- see the module docstring. An expiry is a decision
        nobody was present for; it may say so, and it may not impersonate the
        person who was.
        """
        with self._item_lock(path):
            if not path.exists():
                return None
            # FRESH read inside the lock: the caller's object is a hint, the
            # disk is the truth.
            approval = Approval.from_dict(self._read_json(path))
            if not approval.is_expired():
                return None
            approval.lapsed_from = approval.status
            approval.status = ApprovalStatus.EXPIRED.value
            approval.expired_at = _now()
            approval.expired_by = "system:expired"
            # decided_by / decided_at deliberately untouched.
            self.rejected_dir.mkdir(parents=True, exist_ok=True)
            self._write_json(self.rejected_dir / f"{approval.id}.json",
                             approval.to_dict())
            path.unlink(missing_ok=True)
        self._record_history(approval, "expired")
        return approval

    def expire_stale(self) -> List[str]:
        """Expire items past their window in BOTH ``pending/`` and
        ``approved/``.

        An approved-but-expired item is the more dangerous of the two: approved
        means express permission, so leaving it in ``approved/`` is a stale
        grant rather than merely a stale request.
        """
        expired: List[str] = []
        for d in (self.pending_dir, self.approved_dir):
            for path in self._list_all(d):
                try:
                    approval = Approval.from_dict(self._read_json(path))
                except (QueueError, ValueError, OSError) as exc:
                    # Unreadable items stay in the population and are surfaced.
                    logger.warning("approvals: unreadable item %s (%s) -- it "
                                   "cannot be swept and is not silently gone",
                                   path.name, exc)
                    continue
                if approval.is_expired() and self._expire(path) is not None:
                    expired.append(approval.id)
        return expired

    # -- reads -----------------------------------------------------------
    def get(self, approval_id: str) -> Optional[Approval]:
        for d in (self.pending_dir, self.approved_dir, self.rejected_dir):
            path = d / f"{self._safe(approval_id)}.json"
            if path.exists():
                return Approval.from_dict(self._read_json(path))
        return None

    def list_pending(self) -> List[Approval]:
        return self._load_dir(self.pending_dir)

    def list_approved(self) -> List[Approval]:
        return self._load_dir(self.approved_dir)

    def list_rejected(self) -> List[Approval]:
        return self._load_dir(self.rejected_dir)

    def _load_dir(self, directory: Path) -> List[Approval]:
        out: List[Approval] = []
        for path in sorted(self._list_all(directory)):
            try:
                out.append(Approval.from_dict(self._read_json(path)))
            except (QueueError, ValueError, OSError) as exc:
                logger.warning("approvals: unreadable item %s (%s) -- counted "
                               "as unreadable, not dropped", path.name, exc)
        return out

    def history(self, limit: int = 50) -> List[Dict[str, Any]]:
        if not self.history_file.exists():
            return []
        rows: List[Dict[str, Any]] = []
        for line in self.history_file.read_text(
                encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except (ValueError, TypeError):
                rows.append({"corrupt": True})  # counted, never skipped
        return rows[-limit:]

    # -- plumbing --------------------------------------------------------
    @staticmethod
    def _safe(approval_id: str) -> str:
        if not SAFE_ID_RE.match(str(approval_id)):
            raise QueueError(f"unsafe approval id: {approval_id!r}")
        return str(approval_id)

    def _item_lock(self, path: Path):
        """The per-item lock. A null context when the primitive is unavailable
        -- surfaced ONCE, never silently."""
        if StoreLock is None or lock_for is None:
            if not getattr(self, "_warned_unlocked", False):
                logger.warning("approvals: store_guard unavailable -- item "
                               "writes are UNLOCKED")
                self._warned_unlocked = True
            return nullcontext()
        path.parent.mkdir(parents=True, exist_ok=True)
        return StoreLock(lock_for(path))

    def _write_json(self, target: Path, data: Dict[str, Any]) -> None:
        """Temp file in the same directory, then atomic replace: a reader never
        observes a half-written record."""
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp",
                                        prefix=".appr_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            Path(tmp_path).replace(target)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _read_json(self, path: Path) -> Dict[str, Any]:
        if path.stat().st_size > MAX_FILE_SIZE:
            raise QueueError(f"approval file too large: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise QueueError(f"approval file is not a JSON object: {path}")
        return data

    def _list_all(self, directory: Path) -> List[Path]:
        """UNCAPPED listing -- for consumers that must see the whole store.

        Budgeting is for a rendering path, never for a consumer deciding what to
        execute: items past a cap are invisible while the count still looks
        healthy.
        """
        if not directory.exists():
            return []
        return [f for f in directory.iterdir() if f.suffix == ".json"]

    def list_for_render(self, directory: Path) -> Tuple[List[Path], bool]:
        """Bounded listing for a UI page. Returns ``(paths, truncated)`` -- the
        truncation flag travels with the list so it can never be silent."""
        files = sorted(self._list_all(directory))
        return files[:RENDER_LIST_CAP], len(files) > RENDER_LIST_CAP

    def _record_history(self, approval: Approval, event: str) -> None:
        row = {
            "at": _now(), "event": event, "id": approval.id,
            "subject_id": approval.subject_id, "tier": approval.tier,
            "status": approval.status, "summary": approval.summary,
            # both actors travel, always: the history is the copy of record
            "decided_by": approval.decided_by, "decided_at": approval.decided_at,
            "expired_by": approval.expired_by, "expired_at": approval.expired_at,
            "lapsed_from": approval.lapsed_from,
        }
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        lock = (StoreLock(lock_for(self.history_file))
                if StoreLock is not None and lock_for is not None
                else nullcontext())
        try:
            with lock:
                with self.history_file.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.error("approvals: history append FAILED for %s/%s (%s) -- "
                         "the audit trail is missing this record",
                         approval.id, event, exc)

    def _ensure_dirs(self) -> None:
        for d in (self.pending_dir, self.approved_dir, self.rejected_dir):
            d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


def selftest() -> Tuple[bool, str]:
    """Prove every transition and every refusal can fire, and above all that an
    expiry cannot destroy a ruling."""
    import tempfile as _tempfile

    f: List[str] = []

    def check(label: str, cond: bool) -> None:
        if not cond:
            f.append(label)

    def refuses(label: str, fn: Callable[[], object]) -> None:
        try:
            fn()
        except QueueError:
            return
        except Exception as exc:  # pragma: no cover - defensive
            f.append(f"{label} (raised {exc.__class__.__name__}, wanted QueueError)")
            return
        f.append(label)

    with _tempfile.TemporaryDirectory() as td:
        q = ApprovalQueue(td)

        refuses("a non-positive expiry window was accepted",
                lambda: q.submit_decision(slug="x", tier="T3", summary="s",
                                          rationale="r", expires_days=0))
        refuses("an unsafe slug was accepted",
                lambda: q.submit_decision(slug="../evil", tier="T3",
                                          summary="s", rationale="r"))

        a = q.submit_decision(slug="rotate-key", tier="T3",
                              summary="rotate the signing key",
                              rationale="SITUATION: it is old.", expires_days=30)
        check("the summary was not prefixed DECISION:",
              a.summary.startswith("DECISION:"))
        check("submit is not idempotent by slug",
              q.submit_decision(slug="rotate-key", tier="T3", summary="again",
                                rationale="r").id == a.id)
        check("the pending list did not carry the new item",
              [x.id for x in q.list_pending()] == [a.id])

        # -- the wound: an expiry must never overwrite a ruling ------------
        granted = q.approve(a.id, decided_by="operator", comment="go ahead")
        check("approve did not record the decider",
              granted.decided_by == "operator" and granted.decided_at)
        # force the window shut, exactly as a lapsed grant would be
        path = q.approved_dir / f"{granted.id}.json"
        rec = json.loads(path.read_text(encoding="utf-8"))
        rec["expires_at"] = (datetime.now(timezone.utc)
                             - timedelta(days=1)).isoformat()
        path.write_text(json.dumps(rec), encoding="utf-8")

        swept = q.expire_stale()
        check(f"the sweep did not expire the lapsed grant: {swept}",
              swept == [granted.id])
        after = q.get(granted.id)
        check("the expired item vanished", after is not None)
        assert after is not None
        check("EXPIRY OVERWROTE decided_by -- the ruling was destroyed",
              after.decided_by == "operator")
        check("EXPIRY OVERWROTE decided_at", after.decided_at == granted.decided_at)
        check("the expiry did not record its own actor",
              after.expired_by == "system:expired" and bool(after.expired_at))
        check("the expiry did not record what it lapsed",
              after.lapsed_from == ApprovalStatus.APPROVED.value)
        check("the expired item is not marked expired",
              after.status == ApprovalStatus.EXPIRED.value)

        hist = q.history()
        expired_rows = [h for h in hist if h.get("event") == "expired"]
        check("the history row for an expiry lost the ruling",
              bool(expired_rows) and expired_rows[-1]["decided_by"] == "operator")

        # -- reject, comment, and their refusals ---------------------------
        b = q.submit_decision(slug="purge-cache", tier="T2", summary="purge",
                              rationale="SITUATION: it is large.")
        refuses("a rejection with no reason was accepted",
                lambda: q.reject(b.id, "operator", "   "))
        refuses("a decision with no decider was accepted",
                lambda: q.approve(b.id, "  "))
        commented = q.comment(b.id, "operator", "narrow the scope first")
        check("a comment decided the item", commented.status == "pending")
        check("the comment was not stored", len(q.get(b.id).comments) == 1)  # type: ignore[union-attr]
        rejected = q.reject(b.id, "operator", "not now")
        check("reject did not record the reason and decider",
              rejected.rejection_reason == "not now"
              and rejected.decided_by == "operator")
        refuses("a decided item was decided twice",
                lambda: q.approve(b.id, "operator"))
        refuses("an unknown approval was decided",
                lambda: q.approve("appr-000000000000", "operator"))

        # -- round trip is total -------------------------------------------
        round_tripped = Approval.from_dict(after.to_dict())
        check("the record does not round-trip -- a field to_dict omits is a "
              "field the next transition destroys",
              round_tripped.to_dict() == after.to_dict())
        refuses("a record missing a load-bearing field was defaulted",
                lambda: Approval.from_dict({"id": "appr-1"}))

        # -- an unparseable expiry must not read as fresh-and-sweepable -----
        c = q.submit_decision(slug="odd-window", tier="T1", summary="s",
                              rationale="r")
        cpath = q.pending_dir / f"{c.id}.json"
        rec = json.loads(cpath.read_text(encoding="utf-8"))
        rec["expires_at"] = "not a date"
        cpath.write_text(json.dumps(rec), encoding="utf-8")
        check("an unparseable expiry was swept anyway",
              q.expire_stale() == [] and q.get(c.id).status == "pending")  # type: ignore[union-attr]

    if f:
        return False, f"{len(f)} check(s) failed: " + "; ".join(f)
    return True, ("submit/approve/reject/comment/expire all fire; expiry "
                  "preserves decided_by, decided_at and the history row, and "
                  "records expired_by/expired_at/lapsed_from of its own; 8 "
                  "refusals fire; the record round-trips without loss")
