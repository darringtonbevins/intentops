"""Per-call tier classification: two independent readings, strictest wins.

PURPOSE
    Decide what one tool call is, and refuse to guess when nobody has said.

    THREE RULES, IN ORDER, AND THE ORDER IS LOAD-BEARING.

    1. **An UNTAGGED tool is T4 and BLOCKED.** Not T2, which is what the
       reference gateway did -- an unclassified tool there rode the backend's
       default tier and was auto-approved. A tool nobody classified is a tool
       nobody bounded, and the honest verdict on an unbounded operation is a
       refusal, not an ask. This is the public default-deny ruling (R-D2).

       BLOCK, not ASK, is the deliberate half. An ASK asks a human to rule on
       an operation NOBODY CAN DESCRIBE -- the operator would be approving a
       name. The remedy is to classify the tool in the estate manifest, which
       is a reviewed act, and then the call goes through the ordinary ladder.

    2. **A DECLARED tier is the floor, never the answer.** Name-based tiering
       is blind to what a call carries in its ARGUMENTS. The reference estate
       learned this the expensive way: a proxy tool whose name classified T2
       carried the real verb in its arguments and auto-approved a
       reaches-reality deletion. So the core's ``reaches_reality`` classifier
       runs over the call as well, and the two readings are MERGED with the
       strictest winning. A classifier that can only raise the tier cannot
       lower a declared one.

    3. **T3/T4 is an ASK, and an ASK is never an execution.** The gateway
       files an approval record and returns the verdict. It does not execute
       and then report; there is no path here from ASK to a call.

WRITE MODEL
    None in this module -- ``classify_call`` is pure and returns a ``Verdict``.
    Filing the approval record is a separate, explicit call
    (``file_approval``), so a caller that only wants to KNOW the tier never
    writes anything. The queue it writes to declares its own model (per-item
    files).

BLIND SPOTS
    * **The arguments reading has a stated operating point, and it is
      narrower than "operation shapes".** The core classifier reads exactly
      three things:

      - a non-empty ``command`` field, on ANY tool name (money, irreversible
        delete, remote push, prod change, shell-dispatched mail). Corrected
        2026-09-06: this used to fire only for tools named exactly ``Bash``,
        ``PowerShell`` or ``Shell``, case-sensitive, so a backend tool called
        ``shell`` carrying ``git push --force`` read ALLOW T1 while the
        identical arguments under ``Bash`` read ASK T4;
      - a ``file_path``/``notebook_path`` naming a credential or key file,
        and that IS still name-gated to the file-write tools;
      - a name prefixed ``mcp__`` for the outbound-communication and
        third-party-disclosure branches. Backend calls reach those branches
        only because ``tools._verdict_for_call`` hands the classifier the
        qualified address ``mcp__<backend>__<tool>``; a caller of
        :func:`classify_call` that does not pass ``classifier_name`` gets no
        opinion from those two branches.

      Anything else -- a destructive verb travelling in a field none of the
      above reads, a backend whose semantics live in its own protocol -- is
      invisible to rule 2, and the DECLARED tier is then the only bound. That
      is why rule 1 exists.
    * ``Verdict`` carries reasons, not proof. Nothing here verifies that a
      declared tier is the right one; it verifies that somebody declared it.
    * A merged verdict states both readings' reasons and cannot tell you they
      disagreed for incompatible causes -- it keeps both so a human can see it.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional, Tuple

from intentops_core.gate import (
    RealityIndicators,
    Verdict,
    allow,
    ask,
    block,
    merge,
    verdict_for,
)
from intentops_core.gate.verdict import TIERS

__all__ = [
    "HUMAN_GATED_TIERS",
    "UNTAGGED_TIER",
    "classify_call",
    "file_approval",
    "split_tool_name",
]

#: What an unclassified tool is worth. Highest tier, and a refusal.
UNTAGGED_TIER = "T4"

#: The tiers that require a human word for THIS act.
HUMAN_GATED_TIERS: Tuple[str, ...] = ("T3", "T4")

#: ``<namespace>__<tool>``, the address form the gateway serves backend tools
#: under. Two underscores because a single one is common inside tool names and
#: splitting on it would silently reattribute a tool to the wrong backend.
_ADDRESS = re.compile(r"^(?P<backend>[a-z0-9][a-z0-9._-]*)__(?P<tool>.+)$",
                      re.IGNORECASE)

_SLUG_UNSAFE = re.compile(r"[^a-z0-9-]+")


def split_tool_name(name: str) -> Tuple[Optional[str], str]:
    """``('backend', 'tool')`` for a namespaced call, ``(None, name)`` otherwise.

    A name with no namespace is NOT attributed to a default backend. There is
    no default backend; an unnamespaced name is either a built-in or an error,
    and both of those are the caller's to resolve.
    """
    text = str(name or "").strip()
    match = _ADDRESS.match(text)
    if not match:
        return None, text
    return match.group("backend").lower(), match.group("tool")


def classify_call(
    *,
    tool: str,
    args: Optional[Mapping[str, Any]] = None,
    declared_tier: Optional[str],
    backend: Optional[str] = None,
    indicators: Optional[RealityIndicators] = None,
    classifier_name: Optional[str] = None,
) -> Verdict:
    """The gateway's verdict for one call. Pure; writes nothing.

    ``declared_tier`` is what the registry says, or None for an undeclared
    tool. None is the interesting case and it is rule 1 above.

    ``classifier_name`` is the name shown to the CORE classifier for the
    arguments reading, when that differs from the name shown to a human. The
    core's outbound-communication and third-party-disclosure branches key on
    the ``mcp__`` prefix -- the address form an MCP-served tool has in the
    reference host. A gateway backend tool IS one, but arrives here under its
    backend-local name (``send_message``, not ``mcp__mail__send_message``), so
    without this those branches were unreachable by construction. It is a name
    shown to a classifier, never a change to what executes, and the merge
    still only ever raises the tier.
    """
    arguments: Mapping[str, Any] = args if isinstance(args, Mapping) else {}
    where = f"{backend}__{tool}" if backend else str(tool)

    if declared_tier is None:
        return block(
            UNTAGGED_TIER,
            (f"{where!r} carries no declared tier, so it is classified "
             f"{UNTAGGED_TIER} and refused. An unclassified tool is an "
             "unbounded one; declare its tier in the estate's capability "
             "population and the call goes through the ordinary ladder."),
            reaches_reality=True,
            metadata={"rule": "R-D2 default-deny", "declared": False},
        )

    tier = str(declared_tier).strip().upper()
    declared: Verdict
    if tier in HUMAN_GATED_TIERS:
        declared = ask(tier, f"{where!r} is declared {tier}: a human word is "
                             "required for this act",
                       reaches_reality=True,
                       metadata={"source": "declared", "declared": True})
    else:
        declared = allow(tier, reasons=(f"{where!r} is declared {tier}",),
                         metadata={"source": "declared", "declared": True})

    # The second, independent reading: what do the ARGUMENTS say? A declared
    # tier can only be raised by it, never lowered -- merge takes the strictest.
    from_args = verdict_for(classifier_name or tool, arguments,
                            indicators=indicators)
    verdict = merge([declared, from_args])
    meta = dict(verdict.metadata)
    meta.update({"declared_tier": tier,
                 "argument_tier": from_args.tier,
                 # Ranked by the ladder's own order, never by string
                 # comparison: "T10" would sort below "T2" and the comparison
                 # would go quietly wrong the day a sixth tier is added.
                 "elevated_by_arguments":
                     TIERS.index(from_args.tier) > TIERS.index(tier),
                 "declared": True})
    return Verdict(decision=verdict.decision, tier=verdict.tier,
                   reaches_reality=verdict.reaches_reality,
                   reasons=verdict.reasons, metadata=meta)


def file_approval(node_root, *, tool: str, verdict: Verdict,
                  harness: Optional[str], arg_keys) -> Optional[str]:
    """File the ASK as an approval record. Returns its id, or None.

    Called ONLY for a verdict that asks. The record carries the tool, the
    tier, the reasons and the argument KEYS -- never the argument values,
    which are the most likely carrier of material that does not belong in a
    queue nobody fenced (``ledger.py`` states the same rule).

    A failure to file is RAISED, never swallowed: an ASK whose record did not
    land is an ask nobody will ever see, which is indistinguishable from an
    allow.
    """
    from intentops_core.approvals.queue import ApprovalQueue

    if not verdict.is_refusal:
        return None
    slug = _SLUG_UNSAFE.sub("-", f"gateway-{tool}".lower()).strip("-")[:60]
    queue = ApprovalQueue(node_root)
    approval = queue.submit_decision(
        slug=slug or "gateway-call",
        tier=verdict.tier,
        summary=f"Gateway call {tool!r} classified {verdict.tier}",
        rationale=(
            "SITUATION: a harness asked the gateway to run a tool that "
            f"classifies {verdict.tier}.\n"
            f"  tool: {tool}\n"
            f"  harness (self-declared): {harness or 'undeclared'}\n"
            f"  argument keys: {', '.join(arg_keys) or '(none)'}\n"
            "THE ASK: may this call proceed?\n"
            "IF APPROVED: nothing runs on this record alone -- the gateway "
            "does not execute an approved ask retroactively; the caller must "
            "make the call again.\n"
            "IF DENIED / IGNORED: the call did not happen. The gateway "
            "already refused it; this record is the account of the refusal.\n"
            "REASONS THE GATE GAVE:\n"
            + "\n".join(f"  - {r}" for r in verdict.reasons)
        ),
        expires_days=30,
        payload={"tool": tool, "tier": verdict.tier,
                 "harness": harness or None,
                 "argument_keys": list(arg_keys),
                 "reaches_reality": verdict.reaches_reality},
    )
    return approval.id
