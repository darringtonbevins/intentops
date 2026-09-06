"""The approval council -- five lenses that read an item and vote.

PURPOSE. An actuation council inspects an ACTION a daemon is about to take: it
pattern-matches hazardous operation signatures, reads a complexity integer,
counts consecutive failures. Approvals carry none of those -- they are prose
asking a human a question. Run over a real queue, an actuation-shaped council
returned APPROVED on every single item, with the same reasons every time. A body
that cannot say no is worse than no body, because it looks like scrutiny.

This is the approval-shaped counterpart: five independent members, a majority of
three, one lens each, ABSTAIN where the evidence for a lens is absent rather
than guessing.

===========  ==================================================================
RESERVED     does this touch a surface the operator reserved -- tenancy, spend,
             user accounts, production? Those never auto-execute.
REVERSIBLE   is there a stated way back, or is this one-way?
EVIDENCE     does it carry claims a third party could check, or assertion only?
CLARITY      can the operator actually rule on it -- one ask, real branches?
DECAY        is it rotting -- stale, expiring, or already overtaken?
===========  ==================================================================

WRITE MODEL. None. Pure and deterministic: no I/O, no clock, no model call. The
same item and tier always produce the same votes, so two nodes reach the same
reading.

ADVISORY. This votes; it does not gate. Nothing here approves, rejects, or
executes anything, and its ESCALATE means "the operator's", not "no".

THREE LENSES ARE DISPOSITIVE -- their ESCALATE cannot be outvoted:

``reserved``    a majority must not be able to vote away "this reaches a
                surface the operator reserved".
``reversible``  nor "this cannot be undone".
``evidence``    fires ONLY when the item's own text admits more unverified
                claims than checkable ones. A majority saying "well, it reads
                fine" is exactly how a confident falsehood gets ratified.

``clarity`` and ``decay`` stay outvotable: they are quality-of-writing signals,
and a well-evidenced reversible item should not be blocked on prose alone.

TWO LESSONS BUILT IN, BOTH PAID FOR.

1. **Classify the CALL, not the prose, wherever a call exists.** A reserved lens
   that regexed prose returned APPROVE on a proposal to write into a production
   tenant because its pattern happened not to carry that word -- and rewriting
   the same request in better prose flipped the whole body to APPROVED without
   changing the action at all. The fix is not a bigger regex.
2. **A template check cannot work over a guaranteed template.** Lenses that
   scored conformance to a shape the item generator already guarantees could
   only ever say yes. ``clarity`` and ``evidence`` here test SUBSTANCE --
   branches that actually differ, an ask that is actually singular, and claims
   with a checkable referent -- never section headings.

BLIND SPOTS.

1. The prose fallback names SURFACES, not risk words. A surface described in
   vocabulary this list does not carry is invisible, and every negative finding
   says so rather than asserting "no reserved surface".
2. Nothing here reads the item's truth. ``evidence`` counts whether claims are
   CHECKABLE, not whether they check out.
3. ``decay`` reads the item's own language. An item that is stale and does not
   say so passes it; the queue's expiry sweep is the mechanical half.
4. The reserved-surface vocabulary is generic by design. A node whose estate has
   its own systems of record must extend ``surfaces`` at call time, and until it
   does the lens cannot see them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Pattern, Tuple

__all__ = [
    "CouncilVote", "SurfaceFinding", "QualityFinding", "PROSE_SURFACES",
    "PROSE_BLIND_SPOTS", "DISPOSITIVE", "classify_item_surface",
    "assess_evidence", "assess_rulability", "council_vote", "selftest",
]

VOTES = ("APPROVE", "ESCALATE", "REJECT", "ABSTAIN")
VERDICTS = ("APPROVED", "REJECTED", "ESCALATE")

#: Members whose ESCALATE cannot be outvoted. See the module docstring.
DISPOSITIVE = frozenset({"reserved", "reversible", "evidence"})

_TEXT_FIELDS = ("title", "proposal_summary", "summary", "description",
                "rationale", "notes", "scope")


@dataclass(frozen=True)
class CouncilVote:
    member: str
    vote: str
    reason: str


@dataclass(frozen=True)
class SurfaceFinding:
    """One reserved-surface classification, carrying HOW it was reached.

    ``evidence`` is the load-bearing field. A finding from the CALL is a fact
    about the operation; a finding from PROSE is a reading of a description, and
    a NEGATIVE finding from prose is weaker still.
    """

    reserved: bool
    evidence: str          # "call" | "prose" | "none"
    reason: str
    matched: Tuple[str, ...] = ()
    blind_spots: Tuple[str, ...] = ()


@dataclass(frozen=True)
class QualityFinding:
    verdict: str           # one of VOTES
    reason: str
    detail: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# reserved surfaces
# ---------------------------------------------------------------------------

#: Prose fallback vocabulary. Deliberately names SURFACES -- systems and objects
#: that reach reality -- rather than generic risk words. A lens that fires on
#: everything is exactly as useless as one that fires on nothing, and the second
#: failure is the one being repaired here; do not "fix" it into the first.
PROSE_SURFACES: Pattern[str] = re.compile(
    r"\b("
    # tenancy, identity, money. NOTE the qualifiers: a bare `directory` matched
    # "cache directory" and made the dispositive lens fire on a local cleanup,
    # which is how a lens that fires on everything becomes as useless as one
    # that fires on nothing.
    r"tenant|directory service|identity provider|subscription|spend|cost|"
    r"budget|invoice|licen[cs]e|"
    r"billing|payment|user account|identit(y|ies)|credential|secret|token|"
    r"key ?vault|private key|"
    # communication surfaces -- the class a narrow pattern lets through
    r"mailbox|inbox|calendar|meeting invite|attendee|chat|channel|"
    r"outbound (message|email|post)|"
    # infrastructure
    r"certificate|tls|dns|firewall|vpn|backup|restore|snapshot|"
    r"scheduled task|group policy|virtual machine|\bvm\b|storage account|"
    r"container|cluster|"
    # data of record
    r"database|\bsql\b|production|\bprod\b|client data|customer data|"
    r"personal data"
    r")\b",
    re.I)

PROSE_BLIND_SPOTS: Tuple[str, ...] = (
    "a surface named only in the tool call and never in the prose",
    "a surface described in words this vocabulary does not carry",
    "a surface reached indirectly by a script the item merely names",
)

#: Tool-name fragments that reach reality wherever they appear in a call.
_CALL_RESERVED_TOOLS = (
    "mail", "message", "post", "send", "calendar", "invite", "grant", "revoke",
    "delete", "deploy", "publish", "billing", "payment", "user", "group",
    "role", "secret", "credential", "certificate", "database", "sql",
)


def classify_item_surface(
    item: Mapping[str, Any],
    surfaces: Optional[Pattern[str]] = None,
) -> SurfaceFinding:
    """Does this item reach a reserved surface, and how do we know?

    Order matters: if the item carries a tool call, classify the CALL. Only if
    it does not, fall back to prose -- and SAY SO, because a prose miss is a
    blind spot rather than a clean bill of health.
    """
    surfaces = surfaces or PROSE_SURFACES

    call = item.get("tool_call") or item.get("call")
    if isinstance(call, Mapping):
        tool = str(call.get("tool") or call.get("name") or "").lower()
        target = " ".join(str(v) for v in (call.get("params")
                                           or call.get("arguments")
                                           or {}).values()).lower()
        hits = tuple(sorted({t for t in _CALL_RESERVED_TOOLS
                             if t in tool or t in target}))
        if hits:
            return SurfaceFinding(
                True, "call",
                f"the tool call {tool!r} reaches a reserved surface "
                f"({', '.join(hits)}) -- reserved surfaces never auto-execute",
                hits)
        return SurfaceFinding(
            False, "call",
            f"the tool call {tool!r} names no reserved surface; classified "
            "from the CALL, which is evidence about the operation rather than "
            "a reading of its description",
            (), ())

    text = " ".join(str(item.get(k) or "") for k in _TEXT_FIELDS)
    hits = tuple(sorted({m.group(0).lower() for m in surfaces.finditer(text)}))
    if hits:
        return SurfaceFinding(
            True, "prose",
            f"the item's text names reserved surface(s): {', '.join(hits[:6])}",
            hits, PROSE_BLIND_SPOTS)
    return SurfaceFinding(
        False, "prose",
        "no reserved surface found IN THE PROSE by the surface vocabulary; "
        "the item carries no tool call, so this is a weak negative -- see "
        "blind spots",
        (), PROSE_BLIND_SPOTS)


# ---------------------------------------------------------------------------
# evidence and rulability
# ---------------------------------------------------------------------------

# Markers that a claim can actually be checked by someone who was not there.
# Deliberately EXCLUDES bare dates and bare clock times: both are formatting
# rather than evidence, and counting them made an evidence lens say yes to
# everything.
_CHECKABLE = re.compile(
    r"\[(OBSERVED|INFERRED|HYPOTHESIS)[^\]]*\]"
    r"|\b[\w./-]+\.(py|md|ya?ml|json|toml|ps1|sh|sql|ts|js):\d+"
    r"|\bcommits?\s+[0-9a-f]{7,40}\b"
    r"|\bappr-[0-9a-f]{6,}\b"
    r"|\bexit code \d+\b|\bexit \d+\b"
    r"|\bverified by\b|\bmeasured\b|\blive check\b|\bre-?read live\b",
    re.I)

_ADMITTED_GAP = re.compile(
    r"\bunverified\b|\bassum(e|ed|ption)\b|\bunknown\b"
    r"|\bnot (yet )?(measured|checked|verified)\b|\buntested\b|\bno evidence\b",
    re.I)

# An agent asserting that somebody else authorised this. Textually identical
# whether true or fabricated, so it must be checkable or flagged.
_AUTHORITY_CLAIM = re.compile(
    r"\b(the )?(operator|principal|owner|admin)\s+"
    r"(has\s+)?(asked|instructed|told|requested|directed|authoris?zed)\b"
    r"|\bas (instructed|directed|requested|authoris?zed)\b"
    r"|\bi was (asked|told|instructed)\b",
    re.I)

# What turns a claim of authority into a checkable one. Deliberately NOT an
# evidence grade: an [OBSERVED] tag anywhere would let a thoroughly evidenced
# item launder an uncited instruction, which is the one thing this stops. A
# citation must point at the INSTRUCTION.
_AUTHORITY_CITED = re.compile(
    r"\bappr-[0-9a-f]{6,}\b|\btranscript\b|\bsession [0-9a-f]{6,}\b"
    r"|\bthis turn\b|\bin session\b",
    re.I)

_ONE_WAY = re.compile(
    r"\b(cannot be (recalled|undone|unsent|reversed)|irreversible|"
    r"permanent(ly)?|delete[ds]? forever|unrecoverable|no way back|"
    r"sent (mail|message)s? cannot)\b", re.I)

_REVERSIBLE = re.compile(
    r"\b(revers(e|ible|ed)|undo|undone|restor(e|able)|revert|roll ?back|"
    r"recoverable|removable|re-?enable|way back)\b", re.I)

_DECAY = re.compile(
    r"\b(stale|expired?|expiring|overtaken|moot|superseded|no longer|"
    r"\d+\s*(days?|weeks?|months?)\s*(old|ago|stale))\b", re.I)

# canonical rule: ONE decision per item, never bundled asks. Stems, so an ask
# that says "rotating" is the same action as one that says "rotate"; matching
# bare forms only let a bundled ask through.
_ACTION_VERB = re.compile(
    r"\b(delet|remov|send|creat|grant|revok|rotat|mov|merg|purg|disabl|enabl"
    r"|restor|refund|credit|schedul|migrat|deploy|publish|install)"
    r"(e|es|ed|ing|s)?\b", re.I)

_NEXT_HEADING = r"(?=^\s*[A-Z][A-Z /]{3,}[^:\n]{0,40}:|\Z)"
_ASK_BLOCK = re.compile(r"^\s*THE ASK\s*:(?P<body>.*?)" + _NEXT_HEADING,
                        re.I | re.M | re.S)
_APPROVED_BLOCK = re.compile(r"\bIF APPROVED\b[^:\n]{0,40}:(?P<body>.*?)"
                             + _NEXT_HEADING, re.I | re.M | re.S)
_DENIED_BLOCK = re.compile(r"\bIF (?:DENIED|IGNORED)\b[^:\n]{0,40}:(?P<body>.*?)"
                           + _NEXT_HEADING, re.I | re.M | re.S)


def _text(item: Mapping[str, Any]) -> str:
    return " ".join(str(item.get(k) or "") for k in _TEXT_FIELDS)


def assess_evidence(item: Mapping[str, Any]) -> QualityFinding:
    """Does the ask rest on anything a third party could check?"""
    t = _text(item)
    checkable = sorted({m.group(0)[:40] for m in _CHECKABLE.finditer(t)})
    gaps = sorted({m.group(0).lower() for m in _ADMITTED_GAP.finditer(t)})

    if _AUTHORITY_CLAIM.search(t) and not _AUTHORITY_CITED.search(t):
        return QualityFinding(
            "ESCALATE",
            "the item asserts someone else's instruction and cites nothing "
            "that points at the instruction -- an uncited claim of authority "
            "is textually identical whether true or fabricated",
            {"checkable": checkable, "gaps": gaps})

    if not checkable and not gaps:
        return QualityFinding(
            "ABSTAIN",
            "no checkable claim and no admitted gap -- nothing to weigh, "
            "abstaining rather than reading absence as sufficiency",
            {"checkable": [], "gaps": []})
    if len(gaps) > len(checkable):
        return QualityFinding(
            "ESCALATE",
            f"the item admits more unverified claims ({len(gaps)}) than "
            f"checkable ones ({len(checkable)}) -- proceeding on something "
            "nobody checked",
            {"checkable": checkable, "gaps": gaps})
    return QualityFinding(
        "APPROVE",
        f"{len(checkable)} checkable claim(s) against {len(gaps)} admitted gap(s)",
        {"checkable": checkable, "gaps": gaps})


def assess_rulability(item: Mapping[str, Any]) -> QualityFinding:
    """Can a human actually rule on this?

    Three signals, ALL required and each structural: a singular ask, a stated
    consequence of approving, and a stated consequence of denying. Requiring two
    of three over a guaranteed template is how a lens comes to say yes to
    everything.
    """
    t = " ".join(str(item.get(k) or "") for k in ("proposal_summary", "summary",
                                                  "rationale", "description"))
    ask = _ASK_BLOCK.search(t)
    approved = _APPROVED_BLOCK.search(t)
    denied = _DENIED_BLOCK.search(t)
    missing = [name for name, m in (("an ask", ask),
                                    ("what happens if approved", approved),
                                    ("what happens if denied", denied))
               if m is None or not m.group("body").strip()]
    if missing:
        return QualityFinding(
            "ESCALATE",
            "not rulable as written -- missing " + ", ".join(missing),
            {"missing": missing})

    a_body = approved.group("body").strip().lower()
    d_body = denied.group("body").strip().lower()
    if a_body == d_body:
        return QualityFinding(
            "ESCALATE",
            "the approve and deny branches say the same thing -- a choice "
            "whose outcomes do not differ is not a decision",
            {"missing": []})

    verbs = {m.group(1).lower() for m in _ACTION_VERB.finditer(ask.group("body"))}
    if len(verbs) > 1:
        return QualityFinding(
            "ESCALATE",
            f"the ask bundles {len(verbs)} distinct actions "
            f"({', '.join(sorted(verbs))}) -- one decision per item",
            {"verbs": sorted(verbs)})
    return QualityFinding(
        "APPROVE",
        "a singular ask with branches that actually differ",
        {"verbs": sorted(verbs)})


# ---------------------------------------------------------------------------
# the five members
# ---------------------------------------------------------------------------


def _vote_reserved(item: Mapping[str, Any], tier: str,
                   surfaces: Optional[Pattern[str]]) -> CouncilVote:
    finding = classify_item_surface(item, surfaces)
    return CouncilVote("reserved",
                       "ESCALATE" if finding.reserved else "APPROVE",
                       finding.reason)


def _vote_reversible(item: Mapping[str, Any], tier: str,
                     surfaces: Optional[Pattern[str]]) -> CouncilVote:
    t = _text(item)
    one_way = bool(_ONE_WAY.search(t))
    back = bool(_REVERSIBLE.search(t))
    if one_way and not back:
        return CouncilVote("reversible", "ESCALATE",
                           "one-way language with no stated way back -- "
                           "irreversible acts are the operator's")
    if one_way and back:
        return CouncilVote("reversible", "APPROVE",
                           "an irreversible step is named AND a way back is "
                           "stated -- decoupled correctly")
    if back:
        return CouncilVote("reversible", "APPROVE", "a way back is stated")
    return CouncilVote("reversible", "ABSTAIN",
                       "reversibility is not stated either way -- abstaining "
                       "rather than assuming it")


def _vote_evidence(item: Mapping[str, Any], tier: str,
                   surfaces: Optional[Pattern[str]]) -> CouncilVote:
    f = assess_evidence(item)
    return CouncilVote("evidence", f.verdict, f.reason)


def _vote_clarity(item: Mapping[str, Any], tier: str,
                  surfaces: Optional[Pattern[str]]) -> CouncilVote:
    f = assess_rulability(item)
    return CouncilVote("clarity", f.verdict, f.reason)


def _vote_decay(item: Mapping[str, Any], tier: str,
                surfaces: Optional[Pattern[str]]) -> CouncilVote:
    hits = sorted({m.group(0).lower() for m in _DECAY.finditer(_text(item))})
    if hits:
        return CouncilVote(
            "decay", "ESCALATE",
            f"age or staleness signals present ({', '.join(hits[:4])}) -- "
            "re-verify the facts before ruling on them")
    return CouncilVote("decay", "APPROVE", "no staleness signal in the item text")


_MEMBERS = (_vote_reserved, _vote_reversible, _vote_evidence, _vote_clarity,
            _vote_decay)


def council_vote(item: Mapping[str, Any], tier: str,
                 surfaces: Optional[Pattern[str]] = None
                 ) -> Tuple[str, List[CouncilVote]]:
    """Five approval-shaped members; a majority of three decides.

    Verdicts: ``APPROVED`` / ``REJECTED`` / ``ESCALATE``. ESCALATE means "the
    operator's", not "no" -- the common and correct answer for a queue whose
    whole purpose is items that reach a human.
    """
    votes = [m(item, tier, surfaces) for m in _MEMBERS]
    approve = sum(1 for v in votes if v.vote == "APPROVE")
    reject = sum(1 for v in votes if v.vote == "REJECT")

    # Fail closed on the dispositive lenses BEFORE counting: a majority must
    # not be able to vote away a reserved surface or an unreversible act.
    for v in votes:
        if v.member in DISPOSITIVE and v.vote == "ESCALATE":
            return "ESCALATE", votes

    if reject >= 3:
        return "REJECTED", votes
    if approve >= 3:
        return "APPROVED", votes
    return "ESCALATE", votes  # no majority anywhere: the operator's, by default


# ---------------------------------------------------------------------------
# selftest -- a body that cannot say no is worse than no body
# ---------------------------------------------------------------------------

_GOOD_RATIONALE = (
    "SITUATION: a local cache directory has grown past its bound.\n"
    "THE ASK: approve removing the cache directory.\n"
    "IF APPROVED: the directory is removed and rebuilt on next run; "
    "reversible, since it is regenerated.\n"
    "IF DENIED: the directory keeps growing and the disk warning stays.\n"
    "Verified by exit 0 on the size probe; see scripts/cache_probe.py:41.\n")


def selftest() -> Tuple[bool, str]:
    """Prove every lens can fire in both directions and that a dispositive
    ESCALATE cannot be outvoted."""
    f: List[str] = []

    def check(label: str, cond: bool) -> None:
        if not cond:
            f.append(label)

    def votes_of(item: Mapping[str, Any], tier: str = "T1"
                 ) -> Tuple[str, Dict[str, str]]:
        verdict, vs = council_vote(item, tier)
        return verdict, {v.member: v.vote for v in vs}

    clean = {"proposal_summary": "DECISION: remove the local cache directory",
             "rationale": _GOOD_RATIONALE}
    verdict, by = votes_of(clean)
    check(f"a clean, evidenced, reversible, singular item did not read "
          f"APPROVED (got {verdict} / {by})", verdict == "APPROVED")

    # -- reserved ----------------------------------------------------------
    prose_reserved = dict(clean)
    prose_reserved["rationale"] = _GOOD_RATIONALE + "This touches production.\n"
    verdict, by = votes_of(prose_reserved)
    check("the reserved lens did not fire on a production surface in prose",
          by.get("reserved") == "ESCALATE" and verdict == "ESCALATE")

    call_reserved = {"proposal_summary": "DECISION: file a note",
                     "rationale": _GOOD_RATIONALE,
                     "tool_call": {"tool": "calendar_create_event",
                                   "params": {"subject": "sync"}}}
    verdict, by = votes_of(call_reserved)
    check("the reserved lens did not classify the CALL when one was present",
          by.get("reserved") == "ESCALATE")
    finding = classify_item_surface(call_reserved)
    check("a call-based finding must be graded evidence 'call'",
          finding.evidence == "call")
    finding = classify_item_surface(clean)
    check("a prose negative must publish its blind spots",
          finding.evidence == "prose" and bool(finding.blind_spots))

    # rewriting the SAME action in better prose must not change the verdict
    reworded = dict(call_reserved)
    reworded["rationale"] = _GOOD_RATIONALE + "Beautifully argued throughout.\n"
    v2, _ = votes_of(reworded)
    check("rewording the prose changed the verdict on an unchanged call",
          v2 == "ESCALATE")

    # -- reversible --------------------------------------------------------
    one_way = {"proposal_summary": "DECISION: purge the archive",
               "rationale": "SITUATION: an archive is large.\n"
                            "THE ASK: approve purging it.\n"
                            "IF APPROVED: it is deleted forever and is "
                            "unrecoverable.\n"
                            "IF DENIED: it stays and the disk stays full.\n"
                            "Verified by measured size on disk.\n"}
    verdict, by = votes_of(one_way)
    check("the reversible lens did not fire on one-way language with no way back",
          by.get("reversible") == "ESCALATE" and verdict == "ESCALATE")
    silent = {"proposal_summary": "DECISION: adjust a display label",
              "rationale": "SITUATION: a label reads oddly.\n"
                           "THE ASK: approve changing the label.\n"
                           "IF APPROVED: the label changes.\n"
                           "IF DENIED: it stays as it is.\n"}
    _v, by = votes_of(silent)
    check("the reversible lens must ABSTAIN when reversibility is unstated",
          by.get("reversible") == "ABSTAIN")

    # -- evidence ----------------------------------------------------------
    check("an uncited claim of authority did not escalate",
          assess_evidence({"rationale": "the operator has asked me to do this"}
                          ).verdict == "ESCALATE")
    check("a CITED claim of authority must not escalate on that ground",
          assess_evidence({"rationale": "the operator asked in this turn; "
                                        "see appr-0123456789ab, verified by "
                                        "exit 0"}).verdict != "ESCALATE")
    check("the evidence lens must ABSTAIN on an item with nothing to weigh",
          assess_evidence({"rationale": "we should probably do this"}
                          ).verdict == "ABSTAIN")
    check("a bare date must not count as a checkable claim",
          assess_evidence({"rationale": "on 2026-01-01 at 09:00 we noticed it"}
                          ).verdict == "ABSTAIN")
    check("more admitted gaps than checkable claims did not escalate",
          assess_evidence({"rationale": "unverified and untested and unknown; "
                                        "measured once"}).verdict == "ESCALATE")

    # -- clarity -----------------------------------------------------------
    check("a missing deny branch did not escalate",
          assess_rulability({"rationale": "THE ASK: approve it.\n"
                                          "IF APPROVED: it happens.\n"}
                            ).verdict == "ESCALATE")
    check("identical approve and deny branches did not escalate",
          assess_rulability({"rationale": "THE ASK: approve it.\n"
                                          "IF APPROVED: nothing changes.\n"
                                          "IF DENIED: nothing changes.\n"}
                            ).verdict == "ESCALATE")
    check("a bundled ask did not escalate",
          assess_rulability({"rationale": "THE ASK: approve rotating the "
                                          "credential and deploying the build.\n"
                                          "IF APPROVED: both happen.\n"
                                          "IF DENIED: neither happens.\n"}
                            ).verdict == "ESCALATE")

    # -- decay -------------------------------------------------------------
    stale = dict(clean)
    stale["notes"] = "this item is 40 days old and the finding may be stale"
    verdict, by = votes_of(stale)
    check("the DECAY lens did not fire on a stale item",
          by.get("decay") == "ESCALATE")
    check("decay is outvotable: a stale but otherwise clean item must not "
          f"become ESCALATE on decay alone (got {verdict})",
          verdict == "APPROVED")

    # -- dispositive lenses cannot be outvoted ------------------------------
    verdict, by = votes_of(one_way)
    approvals = sum(1 for v in by.values() if v == "APPROVE")
    check(f"a dispositive ESCALATE was outvoted by {approvals} approvals",
          verdict == "ESCALATE")

    # -- determinism -------------------------------------------------------
    check("the council is not deterministic",
          council_vote(clean, "T1")[1] == council_vote(clean, "T1")[1])

    if f:
        return False, f"{len(f)} check(s) failed: " + "; ".join(f)
    return True, ("all 5 lenses fire in both directions; DECAY fires on a stale "
                  "item and stays outvotable; the 3 dispositive lenses cannot "
                  "be outvoted; a call is classified as a call and rewording "
                  "prose does not move the verdict; a prose negative publishes "
                  "its blind spots; the tally is deterministic")
