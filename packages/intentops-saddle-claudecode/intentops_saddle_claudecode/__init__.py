"""The reference saddle: IntentOps carried by one specific host runtime.

PURPOSE
    A saddle is the impure half of the gate. ``intentops_core.gate`` computes a
    Verdict and knows nothing about any host; this package turns that Verdict
    into the one thing this host understands -- an exit code and a decision
    frame on stdout -- and does the filesystem work the core deliberately does
    not do.

    It exists as a separate package for one reason: so that supporting a second
    host is writing a second package, not editing the gate. A gate with a host
    baked into it can only ever live in that host, and can only be tested by
    running it.

THE FIVE OPERATIONS
    A host carries IntentOps if and only if it can do five things. This package
    implements all five for its host, and ``OPERATIONS`` below is the
    machine-readable claim that it does -- the contract test reads it rather
    than trusting this docstring.

      S1 classify     what tier is this, and does it reach the world
      S2 decide       return allow / ask / deny, and have the host HONOUR deny
      S3 record       append the decision, every decision, not only refusals
      S4 boot_corpus  put the node's own rules in a fresh window
      S5 halt         refuse everything while the stand-down marker exists

    S2 is the only hard one. Without it there is no gate, only advice.

WRITE MODEL
    This module writes nothing. ``ledger.py`` is the sole writer in the package
    and declares its own model (append-only JSONL, single-write appends, no
    read-modify-write, therefore no lock).

BLIND SPOTS
    * Implementing the five operations is not the same as the host honouring
      them. This package can return a refusal; only the host can enforce one.
      The contract test drives the real entry point in a subprocess for exactly
      that reason, and even that proves the ADAPTER's half, not the runtime's.
    * The adapter never emits an "allow" decision, only silence on allow. That
      is deliberate (see ``pre_tool``) and it means this gate can raise the
      host's bar and can never lower it -- including in cases where a human
      might have wanted it to.
    * Everything here keys off what the host says is about to happen. A host
      that misreports a tool call defeats the adapter entirely.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

from intentops_core.gate import Verdict, block, halt, verdict_for

from .ledger import LedgerUnavailable, append_event
from .node import EstateUnreadable, NodeContext, boot_corpus, halt_reason, load_context
from .payload import HookEvent, MalformedPayload, parse_event, parse_pre_tool

__all__ = [
    "EstateUnreadable",
    "HookEvent",
    "LedgerUnavailable",
    "MalformedPayload",
    "NodeContext",
    "OPERATIONS",
    "SADDLE_ID",
    "boot_corpus",
    "classify",
    "decide",
    "halted",
    "load_context",
    "parse_event",
    "parse_pre_tool",
    "record",
]

#: Must match the row id in ``config/saddles.yaml``. The contract test joins on
#: this, so a package that renames itself without updating the registry is
#: caught rather than quietly unexercised.
SADDLE_ID = "claudecode"


# -- S1 ---------------------------------------------------------------------


def classify(tool: str, tool_input: Mapping[str, Any],
             *, context: Optional[NodeContext] = None) -> Verdict:
    """Classify one operation. Pure delegation to the core; no host knowledge."""
    indicators = context.indicators if context is not None else None
    return verdict_for(tool, tool_input, indicators=indicators)


# -- S2 ---------------------------------------------------------------------


def decide(event: HookEvent, context: NodeContext) -> Verdict:
    """Decide about one operation, stand-down first.

    The stand-down is read BEFORE anything else -- ahead of the classifier,
    ahead of any council, ahead of any allowlist. A stood-down node does not
    get to reason about whether this particular call might be fine, because
    the whole point of a one-command stop is that it needs no cooperation from
    the thing being stopped.
    """
    stood_down = halt_reason(context.root)
    if stood_down is not None:
        return halt(stood_down)
    return classify(event.tool_name, event.tool_input, context=context)


# -- S3 ---------------------------------------------------------------------


def record(root: Path, event: Dict[str, Any]) -> Path:
    """Append one decision to the node's gate ledger. Raises if it cannot."""
    return append_event(root, event)


# -- S5 ---------------------------------------------------------------------


def halted(root: Path) -> Optional[str]:
    """True-ish when the node is stood down; the value is the reason, if any."""
    return halt_reason(root)


#: The claim, in a form a test can read. Each entry maps an operation id to the
#: callable that implements it, so "implemented" is checkable rather than
#: asserted in prose.
OPERATIONS: Dict[str, Callable[..., Any]] = {
    "S1": classify,
    "S2": decide,
    "S3": record,
    "S4": boot_corpus,
    "S5": halted,
}


def refusal_for_internal_error(reason: str) -> Verdict:
    """The verdict this adapter returns when IT is broken, rather than the call.

    Fail closed, and say which half failed. An adapter that cannot read its own
    node, parse its own payload, or write its own ledger has no basis for
    permitting anything -- but the operator needs to know the refusal was the
    gate's own fault and not a finding about their command.
    """
    return block("T0", f"the gate could not evaluate this call: {reason}",
                 metadata={"fault": "adapter"})


def summary_of(verdict: Verdict, event: HookEvent, context: NodeContext) -> Dict[str, Any]:
    """The ledger row for one decision. Records the ALLOWs too."""
    return {
        "saddle": SADDLE_ID,
        "hook_event": event.event_name,
        "session_id": event.session_id,
        "tool": event.tool_name,
        "decision": verdict.decision.value,
        "tier": verdict.tier,
        "reaches_reality": verdict.reaches_reality,
        "reasons": list(verdict.reasons),
        "node_root": str(context.root),
        "root_provenance": context.root_provenance,
        "estate_map": context.estate_map_state,
    }
