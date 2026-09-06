"""The lockout/tagout ledger -- the chain of custody for anything disabled.

PURPOSE. Every switch a node turns off carries a TAG: a greppable id at the
switch itself, and an entry here recording what was disabled, at what blast
radius, on whose authority, for what reason, and -- for anything safety- or
detection-shaped -- the condition under which it comes back on. The tag at the
switch is a POINTER; the record lives here; the deep history lives in the
commits and items this entry references and is never copied into the comment.

TIERS, BY BLAST RADIUS OF THE THING SWITCHED OFF -- never by how hard it was to
disable:

====  ==========================================================  ==============
T0    cosmetic; no runtime behaviour change                       id, reason
T1    local, bounded behaviour change                             + reference
T2    a subsystem runs degraded                                   + reference
T3    a SAFETY or DETECTION control is off                        + authority
                                                                  + reenergize_when
T4    a destructive-capable routine is disabled as an interlock   + authority
                                                                  + reenergize_when;
                                                                  re-energising is
                                                                  ALWAYS human-gated
====  ==========================================================  ==============

T3 is the tier that bites. A lint rule that catches undefined names sat in a
global ignore list for five months because its tag carried a comment and no
authority, no way back, and no reference -- and its stated rationale was false.
An unverifiable tag is WORSE than no tag: it looks accounted for. So
``create_entry`` and ``append_chain_link`` REFUSE a T3 or T4 tagout with no
``reenergize_when``. A safety control switched off with no stated way back is
abandoned, not parked, and the refusal is enforced here rather than described
somewhere.

THE POSTURE ORACLE.

``REMEDIATED``  nothing safety-critical is off.
``ATTENTION``   T3/T4 controls are off, but every one is GOVERNED -- an
                authority and a stated way back. Parked, not forgotten.
``BLOCKED``     either a tagout is UNGOVERNED (missing authority or way back),
                or its ``reenergize_status`` is ``ready``, meaning its
                condition is already satisfied and it should have been switched
                back on. When a control's exit condition is met, marking it
                ``ready`` makes it BLOCKING until somebody actually flips it --
                so it cannot quietly stay off.

An EMPTY ledger is VALID and reads REMEDIATED. A node at birth has switched
nothing off, and that is a true statement about a new node -- deliberately
unlike a trust-root list, where empty means "can verify nothing" and halts.

WRITE MODEL (declared at birth). Locked fresh-read read-modify-write: the whole
document is loaded INSIDE a kernel-released ``StoreLock``, mutated, and written
back by atomic replace. The ledger is MACHINE-WRITTEN ONLY -- these functions
are the sanctioned path, and a YAML round-trip does not preserve hand-authored
comments or formatting. Hand-editing is the failure this module exists to
retire: an ad-hoc writer once handed the ledger itself to a lock primitive that
truncates the path it is given, and a sixty-entry ledger became eleven bytes.

The ``chain`` of each entry is APPEND-ONLY, and an entry's ``state`` must equal
its last chain action -- so a switch flipped without appending a link is caught,
because the ledger says one thing and the file says another.

BLIND SPOTS.

1. The ledger sees only switches somebody tagged. A silent disable is invisible
   here by construction, and no posture verdict is evidence that nothing else
   is off.
2. Carrier verification is textual: the probe string must appear in the carrier
   file. A probe that also appears in an unrelated comment reads as present.
3. A SENTINEL carrier -- a file whose PRESENCE is the off-state -- has nothing
   to find once correctly tagged in. Those are exempted when the entry state is
   ``tagged_in``; a code-comment carrier is not, because that file persists
   whatever the state.
4. ``reenergize_status`` is set by a human judgement about whether a condition
   is met. Nothing here evaluates the condition itself.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import yaml

try:  # pragma: no cover - import shim
    from intentops_core.store_guard import StoreLock, atomic_replace, lock_for
except ImportError:  # pragma: no cover
    atomic_replace = None  # type: ignore[assignment]
    lock_for = None  # type: ignore[assignment]
    StoreLock = None  # type: ignore[assignment]

__all__ = [
    "LedgerError", "SCHEMA", "TIERS", "SAFETY_TIERS", "STATES", "ACTIONS",
    "REENERGIZE_STATES", "LEDGER_RELPATH", "new_ledger", "load_ledger",
    "create_entry", "append_chain_link", "set_reenergize_status",
    "check_schema", "check_carriers", "check_tier_rules", "posture",
    "selftest",
]

SCHEMA = "loto-ledger/1"
LEDGER_RELPATH = Path(".intentops") / "loto" / "LEDGER.yaml"

TIERS: Tuple[str, ...] = ("T0", "T1", "T2", "T3", "T4")
SAFETY_TIERS = frozenset({"T3", "T4"})
STATES: Tuple[str, ...] = ("tagged_out", "tagged_in")
ACTIONS: Tuple[str, ...] = ("tagged_out", "tagged_in")
REENERGIZE_STATES: Tuple[str, ...] = ("pending", "ready", "done")

LINK_FIELDS = ("action", "at", "by", "authority", "reason", "reenergize_when",
               "evidence")
ID_RE = re.compile(r"^LOTO-\d{4}-\d{2}-\d{2}-[A-Z0-9][A-Z0-9-]*$")

#: Placeholders that are NOT an authority and NOT a way back.
_NULL_MARKERS = frozenset({"", "-", "n/a", "na", "none", "null", "tbd",
                           "unknown", "?", "todo"})
#: Openers that JUSTIFY an absence rather than being one ("no longer needed
#: because ...") -- flagging those is how detectors get switched off.
_JUSTIFYING_OPENERS = ("no longer needed", "not applicable because",
                       "none required because")
_NEGATING_FIRST_WORDS = frozenset({"no", "none", "not", "nobody", "never",
                                   "unrecorded", "unknown", "missing"})
_NEGATING_PHRASES = ("not recorded", "no authority", "nobody recorded",
                     "never stated", "carried forward", "unattributed")

ABSENCE_DETECTOR_BLIND_SPOTS: Tuple[str, ...] = (
    "an absence phrased without a negating token reads as an authority",
    "a negation that is neither the first word nor a listed phrase is invisible",
    "non-English text",
)


class LedgerError(RuntimeError):
    """The operation cannot be performed safely. NOTHING was written."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _self_negates(value: Any) -> bool:
    """True when an AUTHORITY's own text says it is not one."""
    v = str(value or "").strip().lower()
    if not v:
        return False
    v = re.sub(r"^[^a-z0-9]+", "", v)
    if v.startswith(_JUSTIFYING_OPENERS):
        return False
    first = re.split(r"[^a-z]+", v, maxsplit=1)[0]
    if first in _NEGATING_FIRST_WORDS:
        return True
    return any(phrase in v for phrase in _NEGATING_PHRASES)


def _is_null(value: Any) -> bool:
    return str(value or "").strip().lower() in _NULL_MARKERS


def _authority_is_null(value: Any) -> bool:
    return _is_null(value) or _self_negates(value)


# ---------------------------------------------------------------------------
# document
# ---------------------------------------------------------------------------


def new_ledger() -> Dict[str, Any]:
    """An empty-and-VALID ledger: a node at birth has switched nothing off."""
    return {"schema": SCHEMA, "updated": _now(), "entries": []}


def load_ledger(path: Path) -> Dict[str, Any]:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(doc, dict):
        raise LedgerError(f"ledger root is {type(doc).__name__}, expected a mapping")
    return doc


def _entries(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    entries = doc.get("entries")
    if entries is None:
        raise LedgerError("ledger has no 'entries' key -- refusing to guess "
                          "where entries live")
    if not isinstance(entries, list):
        raise LedgerError("ledger 'entries' must be a list")
    return entries


def _write(path: Path, doc: Dict[str, Any]) -> None:
    doc["updated"] = _now()
    text = yaml.safe_dump(doc, default_flow_style=False, sort_keys=False,
                          allow_unicode=True, width=100)
    if atomic_replace is None:  # pragma: no cover - import shim
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text, encoding="utf-8", newline="\n")
        return
    atomic_replace(Path(path), text)


def _locked(path: Path):
    if StoreLock is None or lock_for is None:  # pragma: no cover - import shim
        raise LedgerError(
            "ledger write refused: StoreLock (intentops_core.store_guard) "
            "could not be imported, and rewriting a whole-file store without "
            "it is the shared-whiteboard defect")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return StoreLock(lock_for(Path(path)), timeout_s=15.0)


# ---------------------------------------------------------------------------
# writers -- the sanctioned path
# ---------------------------------------------------------------------------


def create_entry(ledger_path: Path, loto_id: str, *, what: str, tier: str,
                 carriers: Sequence[Dict[str, str]], by: str, authority: str,
                 reason: str, reenergize_when: Optional[str] = None,
                 evidence: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Create a NEW tagout entry, already tagged out, atomically.

    Refuses, writing nothing: a missing required field; no carrier (a tagout
    whose tag is not physically present at the switch cannot be verified); a
    duplicate id; a malformed id; an unknown tier; and -- the one that matters
    most -- a T3 or T4 with no ``reenergize_when``.
    """
    for name, value in (("what", what), ("tier", tier), ("by", by),
                        ("authority", authority), ("reason", reason)):
        if not str(value or "").strip():
            raise LedgerError(f"field {name!r} is required")
    if str(tier) not in TIERS:
        raise LedgerError(f"tier {tier!r} is not one of {list(TIERS)}")
    if not ID_RE.match(str(loto_id)):
        raise LedgerError(f"id must match LOTO-YYYY-MM-DD-SLUG, got {loto_id!r}")
    if not carriers:
        raise LedgerError(
            "at least one carrier is required -- a tagout whose tag is not "
            "physically present at the switch cannot be verified")
    for c in carriers:
        if not c.get("path") or not c.get("probe"):
            raise LedgerError("every carrier needs both 'path' and 'probe'")
    if tier in SAFETY_TIERS and _is_null(reenergize_when):
        raise LedgerError(
            f"tier {tier} requires reenergize_when: a safety or detection "
            "control switched off with no stated way back is abandoned, not "
            "parked")

    link: Dict[str, Any] = {"action": "tagged_out", "at": _now(), "by": by,
                            "authority": authority, "reason": reason}
    if reenergize_when:
        link["reenergize_when"] = reenergize_when
    if evidence:
        link["evidence"] = list(evidence)

    entry = {"id": str(loto_id), "what": what, "tier": str(tier),
             "state": "tagged_out", "carriers": [dict(c) for c in carriers],
             "chain": [link]}

    with _locked(ledger_path):
        doc = load_ledger(ledger_path)      # fresh read INSIDE the lock
        entries = _entries(doc)
        if any(e.get("id") == loto_id for e in entries):
            raise LedgerError(f"entry already exists: {loto_id}")
        entries.append(entry)
        _write(ledger_path, doc)
        return {"entries": len(entries), "state": "tagged_out"}


def append_chain_link(ledger_path: Path, loto_id: str,
                      link: Dict[str, Any]) -> Dict[str, Any]:
    """Append one chain link to an existing entry and flip its state, atomically.

    The chain is append-only and the entry's ``state`` follows its last action,
    so the two can never disagree through this path.
    """
    action = link.get("action")
    if action not in ACTIONS:
        raise LedgerError(f"action must be one of {list(ACTIONS)}, got {action!r}")
    for req in ("by", "authority", "reason"):
        if not str(link.get(req) or "").strip():
            raise LedgerError(f"link field {req!r} is required")
    link = {k: v for k, v in link.items() if k in LINK_FIELDS and v is not None}
    link.setdefault("at", _now())

    with _locked(ledger_path):
        doc = load_ledger(ledger_path)
        entries = _entries(doc)
        entry = next((e for e in entries if e.get("id") == loto_id), None)
        if entry is None:
            raise LedgerError(f"entry not found: {loto_id}")
        tier = str(entry.get("tier"))
        if action == "tagged_out" and tier in SAFETY_TIERS \
                and _is_null(link.get("reenergize_when")):
            raise LedgerError(
                f"tier {tier} requires reenergize_when on a tagged_out link")
        chain = entry.setdefault("chain", [])
        if not isinstance(chain, list):
            raise LedgerError(f"{loto_id}: 'chain' is not a list")
        chain.append(link)
        entry["state"] = action
        if action == "tagged_in":
            entry.pop("reenergize_status", None)
        _write(ledger_path, doc)
        return {"chain_length": len(chain), "state": action}


def set_reenergize_status(ledger_path: Path, loto_id: str, status: str, *,
                          by: str, authority: str, reason: str) -> Dict[str, Any]:
    """Record that a tagout's exit condition is (or is not) satisfied.

    This does NOT flip the switch. Setting ``ready`` makes the posture BLOCKED
    until somebody actually re-energises, so a control whose reason to be off
    has expired cannot quietly stay off.
    """
    if status not in REENERGIZE_STATES:
        raise LedgerError(f"reenergize_status must be one of "
                          f"{list(REENERGIZE_STATES)}, got {status!r}")
    for name, value in (("by", by), ("authority", authority), ("reason", reason)):
        if not str(value or "").strip():
            raise LedgerError(f"field {name!r} is required")
    with _locked(ledger_path):
        doc = load_ledger(ledger_path)
        entry = next((e for e in _entries(doc) if e.get("id") == loto_id), None)
        if entry is None:
            raise LedgerError(f"entry not found: {loto_id}")
        if entry.get("state") != "tagged_out":
            raise LedgerError(
                f"{loto_id} is {entry.get('state')!r}: a reenergize_status is "
                "only meaningful while the control is OFF")
        entry["reenergize_status"] = status
        entry.setdefault("status_history", []).append(
            {"at": _now(), "status": status, "by": by, "authority": authority,
             "reason": reason})
        _write(ledger_path, doc)
        return {"reenergize_status": status, "state": entry["state"]}


# ---------------------------------------------------------------------------
# validators
# ---------------------------------------------------------------------------


def check_schema(ledger: Dict[str, Any]) -> List[str]:
    """Structural conformance. An EMPTY entries list is VALID."""
    out: List[str] = []
    if ledger.get("schema") != SCHEMA:
        out.append(f"schema: expected {SCHEMA!r}, got {ledger.get('schema')!r}")
    entries = ledger.get("entries")
    if entries is None:
        out.append("schema: missing 'entries' key (an empty list is valid; a "
                   "missing key is a field nobody read)")
        return out
    if not isinstance(entries, list):
        out.append("schema: 'entries' must be a list")
        return out

    seen: set = set()
    for i, e in enumerate(entries):
        where = e.get("id") or f"entry[{i}]"
        for key in ("id", "what", "tier", "state", "carriers", "chain"):
            if not e.get(key):
                out.append(f"{where}: missing required key {key!r}")
        eid = e.get("id", "")
        if eid and not ID_RE.match(str(eid)):
            out.append(f"{where}: id must match LOTO-YYYY-MM-DD-SLUG")
        if eid in seen:
            out.append(f"{where}: duplicate id")
        seen.add(eid)
        if e.get("tier") not in TIERS:
            out.append(f"{where}: tier {e.get('tier')!r} not in {list(TIERS)}")
        if e.get("state") not in STATES:
            out.append(f"{where}: state {e.get('state')!r} not in {list(STATES)}")
        rs = e.get("reenergize_status")
        if rs is not None and rs not in REENERGIZE_STATES:
            out.append(f"{where}: reenergize_status {rs!r} not in "
                       f"{list(REENERGIZE_STATES)}")
        chain = e.get("chain") or []
        if isinstance(chain, list) and chain:
            for link in chain:
                if link.get("action") not in ACTIONS:
                    out.append(f"{where}: chain action {link.get('action')!r} "
                               f"not in {list(ACTIONS)}")
                if not link.get("at"):
                    out.append(f"{where}: a chain link has no 'at' date")
            last = chain[-1].get("action")
            if last in ACTIONS and last != e.get("state"):
                out.append(f"{where}: state is {e.get('state')!r} but the last "
                           f"chain action is {last!r} -- the switch was flipped "
                           "without appending a link, or the reverse")
    return out


def check_carriers(ledger: Dict[str, Any], workspace: Path) -> List[str]:
    """The tag must be physically present at the switch."""
    out: List[str] = []
    workspace = Path(workspace)
    for e in ledger.get("entries") or []:
        eid = e.get("id", "?")
        for c in e.get("carriers") or []:
            rel = c.get("path")
            probe = c.get("probe")
            if not rel or not probe:
                out.append(f"{eid}: carrier needs both 'path' and 'probe'")
                continue
            p = workspace / rel
            if not p.is_file():
                # A SENTINEL carrier is the switch itself: its PRESENCE is the
                # off-state, so a correctly tagged-in entry has no file to
                # find. Demanding one makes every release fail forever, and a
                # checker that fails permanently stops being read.
                sentinel_like = str(rel).replace("\\", "/").startswith(".intentops/")
                if sentinel_like and str(e.get("state", "")).strip() == "tagged_in":
                    continue
                out.append(f"{eid}: carrier does not exist: {rel}")
                continue
            try:
                text = p.read_text(encoding="utf-8-sig", errors="replace")
            except OSError as exc:
                out.append(f"{eid}: carrier unreadable: {rel} ({exc})")
                continue
            if str(probe) not in text:
                out.append(
                    f"{eid}: probe {probe!r} NOT FOUND in {rel} -- the tag is "
                    "not at the switch, or the switch was flipped without "
                    "updating the ledger")
    return out


def check_tier_rules(ledger: Dict[str, Any]) -> List[str]:
    """A LIVE T3/T4 tagout must name its authority AND its way back."""
    out: List[str] = []
    for e in ledger.get("entries") or []:
        if e.get("state") != "tagged_out" or e.get("tier") not in SAFETY_TIERS:
            continue
        eid = e.get("id", "?")
        last_out = None
        for link in e.get("chain") or []:
            if link.get("action") == "tagged_out":
                last_out = link
        if last_out is None:
            out.append(f"{eid}: state is tagged_out but there is no tagged_out "
                       "chain link")
            continue
        if _authority_is_null(last_out.get("authority")):
            out.append(f"{eid} ({e.get('tier')}): tagged_out with NO AUTHORITY "
                       "recorded")
        if _is_null(last_out.get("reenergize_when")):
            out.append(f"{eid} ({e.get('tier')}): tagged_out with NO "
                       "reenergize_when -- an unbounded tagout is how a "
                       "detection control survives five months switched off")
    return out


def posture(ledger: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Are we fully remediated, or is something blocking?"""
    lines: List[str] = []
    ungoverned = check_tier_rules(ledger)
    entries = ledger.get("entries") or []
    off = [e for e in entries if e.get("state") == "tagged_out"]

    by_tier: Dict[str, List[Dict[str, Any]]] = {}
    for e in off:
        by_tier.setdefault(str(e.get("tier", "?")), []).append(e)

    # the count always travels with its denominator
    lines.append(f"controls currently OFF: {len(off)} of {len(entries)} tagged")
    for tier in sorted(by_tier, reverse=True):
        names = ", ".join(str(e.get("id", "?")) for e in by_tier[tier])
        lines.append(f"  {tier} x{len(by_tier[tier])}  {names}")

    blocking: List[str] = []
    for e in [x for x in off if x.get("reenergize_status") == "ready"]:
        blocking.append(f"READY TO RE-ENERGIZE (act on it now): {e.get('id')} "
                        f"-- {e.get('what')}")
    for msg in ungoverned:
        blocking.append(f"UNGOVERNED TAGOUT: {msg}")

    if blocking:
        verdict = "BLOCKED"
    elif any(e.get("tier") in SAFETY_TIERS for e in off):
        verdict = "ATTENTION"
    else:
        verdict = "REMEDIATED"

    if verdict == "ATTENTION":
        lines.append("")
        lines.append("Safety or detection controls are OFF, but every one is "
                     "governed (an authority and a stated way back). Parked, "
                     "not forgotten:")
        for e in off:
            if e.get("tier") in SAFETY_TIERS:
                lines.append(f"  {e.get('id')} ({e.get('tier')}) -- {e.get('what')}")
    return verdict, lines + ([""] + blocking if blocking else [])


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


def selftest() -> Tuple[bool, str]:
    """Prove every refusal and every posture can fire."""
    import tempfile

    f: List[str] = []

    def check(label: str, cond: bool) -> None:
        if not cond:
            f.append(label)

    def refuses(label: str, fn) -> None:
        try:
            fn()
        except LedgerError:
            return
        except Exception as exc:  # pragma: no cover - defensive
            f.append(f"{label} (raised {exc.__class__.__name__}, wanted LedgerError)")
            return
        f.append(label)

    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        path = ws / LEDGER_RELPATH
        path.parent.mkdir(parents=True, exist_ok=True)
        _write(path, new_ledger())

        check("an empty ledger is not schema-valid",
              check_schema(load_ledger(path)) == [])
        check("an empty ledger does not read REMEDIATED",
              posture(load_ledger(path))[0] == "REMEDIATED")

        switch = ws / "src" / "watcher.py"
        switch.parent.mkdir(parents=True, exist_ok=True)
        switch.write_text("# TAGOUT: LOTO-2026-09-06-WATCHER-OFF\nENABLED = False\n",
                          encoding="utf-8")
        carriers = [{"path": "src/watcher.py",
                     "probe": "LOTO-2026-09-06-WATCHER-OFF"}]

        # -- refusals -------------------------------------------------------
        refuses("a T3 with no reenergize_when was accepted -- the whole point",
                lambda: create_entry(path, "LOTO-2026-09-06-WATCHER-OFF",
                                     what="the watcher", tier="T3",
                                     carriers=carriers, by="node",
                                     authority="the operator", reason="noisy"))
        refuses("a T4 with a placeholder reenergize_when was accepted",
                lambda: create_entry(path, "LOTO-2026-09-06-WATCHER-OFF",
                                     what="the watcher", tier="T4",
                                     carriers=carriers, by="node",
                                     authority="the operator", reason="noisy",
                                     reenergize_when="TBD"))
        refuses("a tagout with no carrier was accepted",
                lambda: create_entry(path, "LOTO-2026-09-06-WATCHER-OFF",
                                     what="the watcher", tier="T1",
                                     carriers=[], by="node",
                                     authority="the operator", reason="noisy"))
        refuses("a malformed id was accepted",
                lambda: create_entry(path, "not-an-id", what="x", tier="T1",
                                     carriers=carriers, by="node",
                                     authority="a", reason="b"))
        refuses("an unknown tier was accepted",
                lambda: create_entry(path, "LOTO-2026-09-06-WATCHER-OFF",
                                     what="x", tier="T9", carriers=carriers,
                                     by="node", authority="a", reason="b"))
        refuses("a missing required field was accepted",
                lambda: create_entry(path, "LOTO-2026-09-06-WATCHER-OFF",
                                     what="x", tier="T1", carriers=carriers,
                                     by="", authority="a", reason="b"))

        # -- a governed T3 lands and reads ATTENTION -------------------------
        create_entry(path, "LOTO-2026-09-06-WATCHER-OFF", what="the watcher",
                     tier="T3", carriers=carriers, by="node",
                     authority="the operator",
                     reason="it fired on every write during the migration",
                     reenergize_when="the migration completes")
        led = load_ledger(path)
        check(f"schema check failed on a good entry: {check_schema(led)}",
              check_schema(led) == [])
        check(f"carrier check failed on a present tag: {check_carriers(led, ws)}",
              check_carriers(led, ws) == [])
        v, _lines = posture(led)
        check(f"a governed T3 tagout did not read ATTENTION: {v}", v == "ATTENTION")

        refuses("a duplicate id was accepted",
                lambda: create_entry(path, "LOTO-2026-09-06-WATCHER-OFF",
                                     what="x", tier="T1", carriers=carriers,
                                     by="node", authority="a", reason="b",
                                     reenergize_when="never"))

        # -- reenergize_status ready must BLOCK ------------------------------
        set_reenergize_status(path, "LOTO-2026-09-06-WATCHER-OFF", "ready",
                              by="node", authority="the operator",
                              reason="the migration completed")
        v, lines = posture(load_ledger(path))
        check(f"a ready-to-re-energize control did not BLOCK: {v}",
              v == "BLOCKED" and any("READY TO RE-ENERGIZE" in x for x in lines))
        refuses("an unknown reenergize_status was accepted",
                lambda: set_reenergize_status(path,
                                              "LOTO-2026-09-06-WATCHER-OFF",
                                              "maybe", by="n", authority="a",
                                              reason="r"))

        # -- tagging back in clears it --------------------------------------
        append_chain_link(path, "LOTO-2026-09-06-WATCHER-OFF",
                          {"action": "tagged_in", "by": "node",
                           "authority": "the operator",
                           "reason": "the migration completed"})
        led = load_ledger(path)
        check("tagging in did not flip the state",
              led["entries"][0]["state"] == "tagged_in")
        check("tagging in did not clear reenergize_status",
              "reenergize_status" not in led["entries"][0])
        check("the chain is not append-only (a link went missing)",
              len(led["entries"][0]["chain"]) == 2)
        v, _lines = posture(led)
        check(f"a fully tagged-in ledger did not read REMEDIATED: {v}",
              v == "REMEDIATED")

        refuses("a chain link with no authority was accepted",
                lambda: append_chain_link(path, "LOTO-2026-09-06-WATCHER-OFF",
                                          {"action": "tagged_out", "by": "n",
                                           "authority": "", "reason": "r"}))
        refuses("an unknown chain action was accepted",
                lambda: append_chain_link(path, "LOTO-2026-09-06-WATCHER-OFF",
                                          {"action": "wiggled", "by": "n",
                                           "authority": "a", "reason": "r"}))

        # -- UNGOVERNED detection (the hand-authored shape) ------------------
        bad = {"schema": SCHEMA, "updated": _now(), "entries": [{
            "id": "LOTO-2026-09-06-LINT-OFF", "what": "a lint rule",
            "tier": "T3", "state": "tagged_out",
            "carriers": [{"path": "src/watcher.py",
                          "probe": "LOTO-2026-09-06-WATCHER-OFF"}],
            "chain": [{"action": "tagged_out", "at": _now(), "by": "someone",
                       "authority": "none recorded",
                       "reason": "will fix incrementally"}]}]}
        findings = check_tier_rules(bad)
        check("a self-negating authority was accepted as an authority",
              any("NO AUTHORITY" in x for x in findings))
        check("a T3 with no way back was not flagged",
              any("reenergize_when" in x for x in findings))
        check("an ungoverned tagout did not BLOCK", posture(bad)[0] == "BLOCKED")

        justified = {"schema": SCHEMA, "updated": _now(), "entries": [{
            "id": "LOTO-2026-09-06-LINT-OFF", "what": "a lint rule",
            "tier": "T3", "state": "tagged_out", "carriers": [],
            "chain": [{"action": "tagged_out", "at": _now(), "by": "someone",
                       "authority": "no longer needed because the operator "
                                    "ruled it on 2026-09-01",
                       "reason": "r", "reenergize_when": "the rule returns"}]}]}
        check("a JUSTIFYING opener was misread as an absent authority",
              not any("NO AUTHORITY" in x for x in check_tier_rules(justified)))

        # -- schema catches a state/chain disagreement ----------------------
        drifted = {"schema": SCHEMA, "updated": _now(), "entries": [{
            "id": "LOTO-2026-09-06-DRIFT", "what": "x", "tier": "T1",
            "state": "tagged_in",
            "carriers": [{"path": "a", "probe": "b"}],
            "chain": [{"action": "tagged_out", "at": _now()}]}]}
        check("a state that disagrees with the last chain action was accepted",
              any("last chain action" in x for x in check_schema(drifted)))

        # -- a probe that is not at the switch ------------------------------
        moved = {"schema": SCHEMA, "updated": _now(), "entries": [{
            "id": "LOTO-2026-09-06-MOVED", "what": "x", "tier": "T1",
            "state": "tagged_out",
            "carriers": [{"path": "src/watcher.py", "probe": "NOT-PRESENT"}],
            "chain": [{"action": "tagged_out", "at": _now()}]}]}
        check("a probe missing from its carrier was not flagged",
              any("NOT FOUND" in x for x in check_carriers(moved, ws)))

    if f:
        return False, f"{len(f)} check(s) failed: " + "; ".join(f)
    return True, ("all 3 postures fire (REMEDIATED / ATTENTION / BLOCKED, the "
                  "last from both an ungoverned tagout and a ready one); 10 "
                  "refusals fire, including a T3 and a T4 with no way back; "
                  "the chain stays append-only and state follows it; a "
                  "self-negating authority is caught while a justifying one is "
                  "not; a probe missing from its carrier is caught")
