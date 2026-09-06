"""The hosted saddle: IntentOps reachable by any MCP client, gate-first.

PURPOSE
    The other saddle in this repository rides INSIDE one host's hook chain.
    This one sits BESIDE any host that speaks the Model Context Protocol and
    offers the same governance as a set of tools: classify, gate, status,
    council, imprint_verify, and the S6 host discovery.

    That difference is the whole of its honest grade. A hook-chain saddle is
    on the critical path -- the host cannot execute a tool without passing
    through it. An MCP server is a peer the host may or may not consult.

THE FIVE OPERATIONS, AND WHERE THIS SADDLE ACTUALLY STANDS

      S1 classify     implemented -- the core classifier, unchanged, exposed
                      as ``intentops.classify``
      S2 decide       COMPUTED, NOT ENFORCED. This server returns ALLOW / ASK
                      / BLOCK / HALT for any proposed call. It cannot refuse a
                      call the host never routes through it, and no live run
                      has yet proven a host honours what it returns. Every
                      answer carries ``host_honoured: "unknown"`` and
                      ``intentops.status`` returns ``"S2: host-honoured=unknown"``.
      S3 record       implemented -- append-only JSONL, every decision
                      including the permits and the refusals at the door
      S4 boot_corpus  DECLINED on this transport. MCP has no session-start
                      hook into the client's context; a server cannot put the
                      imprint in a fresh window from here. It is named as
                      absent rather than claimed, because a node that cannot
                      read its own refusals has them in name only.
      S5 halt         implemented -- the stand-down marker is checked ahead of
                      authentication and refuses every method

    S6 discover is this package's proposal, not part of the five: a typed,
    read-only descriptor of the host, DATA only. See ``discover.py``.

    That is why ``config/saddles.yaml`` grades this row ``candidate`` and not
    ``reference``. Two of five operations are honestly short, and the row says
    which two and why.

THE TWO CONTROLS THAT SHIPPED DISABLED UPSTREAM ARE ON HERE
    Authentication (a per-node bearer token, minted at genesis, digest-only on
    disk) and a default-deny client allowlist. Neither has an off switch, and
    an unreadable control refuses everything rather than resolving a caller to
    "anonymous".

WRITE MODEL
    This module writes nothing. ``ledger.py`` (append-only JSONL, no lock, no
    read-modify-write) and ``auth.py`` (locked whole-file replace) are the two
    writers, and each declares its model in its own docstring.

BLIND SPOTS
    * Implementing an operation is not the same as the host honouring it. This
      package can compute a refusal; only the host can enforce one.
    * A client name in an ``initialize`` frame is self-reported. The allowlist
      is authorisation on top of authentication, never identification.
    * ``OPERATIONS`` below claims the five names so the contract suite can read
      a machine-readable claim rather than trusting this docstring. Two of the
      five entries return an explicit "not satisfied here" answer; they are
      present so the gap stays in the population, never so it looks filled.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

from intentops_core.gate import Verdict, classify_reaches_reality, verdict_for

from .auth import AuthUnavailable, mint, verify
from .discover import HostCapabilities, PayloadRefused, describe_host
from .ledger import LedgerUnavailable, append_event
from .node import EstateUnreadable, NodeContext, halt_reason, load_context
from .registry import HOST_ID, RegistryUnavailable, SaddleRow, load_row

__version__ = "0.1.0"

__all__ = [
    "AuthUnavailable",
    "EstateUnreadable",
    "HOST_ID",
    "HostCapabilities",
    "LedgerUnavailable",
    "NodeContext",
    "OPERATIONS",
    "PayloadRefused",
    "RegistryUnavailable",
    "SaddleRow",
    "Verdict",
    "append_event",
    "describe_host",
    "load_context",
    "load_row",
    "mint",
    "verify",
]


def _s1_classify(tool: str, tool_input: Mapping[str, Any],
                 *, indicators: Any = None) -> Optional[Any]:
    """S1: the core classifier, exposed unchanged. Pure and deterministic."""
    return classify_reaches_reality(tool, tool_input, indicators=indicators)


def _s2_decide(tool: str, tool_input: Mapping[str, Any],
               *, indicators: Any = None) -> Verdict:
    """S2: COMPUTED here, enforced by the host -- which is the open question."""
    return verdict_for(tool, tool_input, indicators=indicators)


def _s3_record(node_root: Path | str, event: Dict[str, Any]) -> Path:
    """S3: append-only, every decision, not only the refusals."""
    return append_event(node_root, event)


def _s4_boot_corpus() -> Dict[str, Any]:
    """S4: DECLINED on this transport, and named rather than faked.

    MCP gives a server no hook into the client's session start, so the imprint
    cannot be placed in a fresh window from here. Returning an empty set would
    read as "no rules to deliver"; this returns the refusal itself.
    """
    return {
        "satisfied": False,
        "reason": ("the Model Context Protocol offers a server no session-start "
                   "hook into the client's context, so this saddle cannot "
                   "deliver the imprint to a fresh window. The host must carry "
                   "S4 itself, or read the imprint through its own mechanism."),
    }


def _s5_halt(node_root: Path | str) -> Optional[str]:
    """S5: the stand-down reason, checked ahead of every other gate."""
    return halt_reason(Path(node_root))


#: The machine-readable claim the saddle contract reads. Two entries answer
#: "not satisfied here", on purpose: a gap that stays in the population is a
#: finding, and a gap papered over is a false grade.
OPERATIONS: Dict[str, Callable[..., Any]] = {
    "S1": _s1_classify,
    "S2": _s2_decide,
    "S3": _s3_record,
    "S4": _s4_boot_corpus,
    "S5": _s5_halt,
}
