"""The five governance tools this server exposes, and the S6 discovery.

PURPOSE
    Turn the core's pure Verdict into an answer an MCP client can read, for
    exactly six named operations and no more. The tool surface is deliberately
    closed: a governance server whose tool list can grow at runtime is a
    governance server whose blast radius nobody can state.

      intentops.classify        S1 -- tier and reaches-reality, nothing else
      intentops.gate            S1+S2+S3+S5 -- the full verdict for one call
      intentops.status          aliveness, the calibration string, the S2 gap
      intentops.council         the values council over a core-surface change
      intentops.imprint_verify  the provenance chain: are the bytes the claimed ones
      intentops.discover        S6 -- typed host descriptors, DATA only

WRITE MODEL
    None here directly. ``intentops.gate`` and ``intentops.council`` cause
    appends through ``ledger.py`` (append-only JSONL) and the core council's
    own ledger; each declares its own model. This module holds no state.

BLIND SPOTS
    * **S2 is not satisfiable from inside this process.** This server can
      compute a refusal; only the host can enforce one, and a host that never
      routes a call through us cannot be refused by us. Every ``gate`` answer
      therefore carries ``host_honoured: "unknown"``, and ``status`` returns
      ``"S2: host-honoured=unknown"``. That is the honest grade, and it is why
      the registry row is ``candidate`` and not ``reference``.
    * The calibration count is folded from this node's own grade journal. An
      absent journal reads as zero, which is correct for a fresh node and
      indistinguishable from a journal that was deleted.
    * ``imprint_verify`` needs the framework checkout. Where it cannot be
      located the answer is UNPROBEABLE and stays in the population -- never a
      pass by absence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from intentops_core.gate import RealityIndicators, classify_reaches_reality, verdict_for

from . import ledger
from .discover import PayloadRefused, describe_host
from .node import NodeContext

__all__ = [
    "CALIBRATION_BAR_RULINGS",
    "S2_HONESTY",
    "TOOL_SPECS",
    "ToolError",
    "calibration_string",
    "dispatch",
]

#: The bar, from the imprint: 20 real rulings by this operator scored >= 80% by
#: a forward-only scorer, on THIS node. Below it the twin is telemetry, never
#: authority, and the honest string says so out loud.
CALIBRATION_BAR_RULINGS = 20
CALIBRATION_BAR_ACCURACY = 0.80

#: Returned by ``status`` verbatim. It is a stated limitation, not a slogan.
S2_HONESTY = "S2: host-honoured=unknown"

_GRADES_RELPATH = Path(".intentops") / "twin" / "calibration-grades.jsonl"


class ToolError(RuntimeError):
    """A tool could not answer. Raised, never returned as a hollow success."""


# ---------------------------------------------------------------------------
# calibration
# ---------------------------------------------------------------------------


def _fold_grades(node_root: Path) -> Tuple[int, int, int]:
    """Pure fold over the append-only grade journal: (total, hits, malformed)."""
    path = node_root / _GRADES_RELPATH
    if not path.is_file():
        return 0, 0, 0
    total = hits = malformed = 0
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ToolError(f"the calibration journal at {path} is unreadable: {exc}") from exc
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if not isinstance(row, dict):
            malformed += 1
            continue
        total += 1
        if str(row.get("grade") or "").strip().lower() == "hit":
            hits += 1
    return total, hits, malformed


def calibration_string(node_root: Path) -> Dict[str, Any]:
    """The honest calibration statement, with the numbers that produced it.

    A partial grade counts as a miss. That is the fail-toward-uncalibrated
    direction and it is deliberate: inventing a fractional score for a
    judgement nobody scored is exactly the manufactured precision the bar
    exists to prevent.
    """
    total, hits, malformed = _fold_grades(node_root)
    accuracy = (hits / total) if total else 0.0
    calibrated = total >= CALIBRATION_BAR_RULINGS and accuracy >= CALIBRATION_BAR_ACCURACY
    text = (f"calibrated: {hits} of {total} rulings at {accuracy:.0%}"
            if calibrated
            else f"uncalibrated: {total} of {CALIBRATION_BAR_RULINGS}")
    return {
        "string": text,
        "calibrated": calibrated,
        "rulings_graded": total,
        "rulings_required": CALIBRATION_BAR_RULINGS,
        "hits": hits,
        "accuracy": round(accuracy, 4),
        "accuracy_required": CALIBRATION_BAR_ACCURACY,
        "malformed_rows": malformed,
        "note": ("Below the bar this node is telemetry, never authority. A "
                 "partial grade counts as a miss by design."),
    }


# ---------------------------------------------------------------------------
# the tools
# ---------------------------------------------------------------------------


def _tool_and_input(args: Mapping[str, Any]) -> Tuple[str, Dict[str, Any]]:
    tool = args.get("tool")
    if not isinstance(tool, str) or not tool.strip():
        raise ToolError("`tool` is required and must be a non-empty string")
    raw = args.get("tool_input", {})
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ToolError("`tool_input` must be an object when present")
    return tool.strip(), dict(raw)


def tool_classify(ctx: NodeContext, args: Mapping[str, Any]) -> Dict[str, Any]:
    """S1. Pure, deterministic, keyed off the operation -- never file content."""
    tool, tool_input = _tool_and_input(args)
    hit = classify_reaches_reality(tool, tool_input, indicators=ctx.indicators)
    verdict = verdict_for(tool, tool_input, indicators=ctx.indicators)
    return {
        "operation": "S1",
        "tool": tool,
        "tier": hit[0] if hit else "T0",
        "reaches_reality": bool(hit and hit[0] in ("T3", "T4")),
        "reason": hit[1] if hit else "no reaches-reality signature",
        "classified": hit is not None,
        "verdict": verdict.to_dict(),
        "estate_map_present": ctx.estate_map_present,
        "note": ("A None classification means this classifier has no opinion, "
                 "never that the operation is safe."),
    }


def tool_gate(ctx: NodeContext, args: Mapping[str, Any]) -> Dict[str, Any]:
    """S5 then S1/S2, recorded by S3. HALT outranks everything, including ALLOW."""
    tool, tool_input = _tool_and_input(args)
    if ctx.halted:
        payload = {
            "operation": "S2",
            "tool": tool,
            "verdict": {
                "decision": "HALT",
                "tier": "T0",
                "reaches_reality": False,
                "reasons": [ctx.halted],
                "metadata": {},
            },
            "host_honoured": "unknown",
            "note": ("This node is stood down. A stood-down node is OFF, not "
                     "broken, and nothing proceeds at all."),
        }
        ledger.append_event(ctx.node_root, {"kind": "gate", "tool": tool,
                                            "decision": "HALT"})
        return payload
    verdict = verdict_for(tool, tool_input, indicators=ctx.indicators)
    ledger.append_event(ctx.node_root, {
        "kind": "gate",
        "tool": tool,
        "decision": verdict.decision.value,
        "tier": verdict.tier,
        "reaches_reality": verdict.reaches_reality,
        "reasons": list(verdict.reasons),
    })
    return {
        "operation": "S2",
        "tool": tool,
        "verdict": verdict.to_dict(),
        "render": verdict.render(),
        "host_honoured": "unknown",
        "note": ("This server computed a verdict. Whether the host HONOURS a "
                 "refusal is not observable from here -- see " + S2_HONESTY),
    }


def tool_status(ctx: NodeContext, args: Mapping[str, Any]) -> Dict[str, Any]:
    """Aliveness by ANSWERING, plus the calibration string and the S2 gap."""
    aliveness: Dict[str, Any]
    if ctx.repo_root is None:
        aliveness = {
            "verdict": "UNPROBEABLE",
            "reason": ("the framework checkout could not be located, so the "
                       "aliveness probes cannot run; set INTENTOPS_REPO_ROOT"),
        }
    else:
        try:
            from intentops_core.genesis.aliveness import take_reading
            reading = take_reading(ctx.node_root, repo_root=ctx.repo_root)
            aliveness = {
                "verdict": reading.verdict,
                "answered": sum(1 for a in reading.answers if a.status == "ANSWERED"),
                "checks": len(reading.answers),
                "faculties_absent": reading.faculties_absent,
            }
        except Exception as exc:  # a probe that cannot run stays in the population
            aliveness = {"verdict": "UNPROBEABLE", "reason": f"{type(exc).__name__}: {exc}"}
    return {
        "operation": "status",
        "alive": aliveness,
        "calibration": calibration_string(ctx.node_root),
        "stood_down": bool(ctx.halted),
        "stand_down_reason": ctx.halted,
        "saddle": {
            "id": "mcp-hosted",
            "grade": "candidate",
            "s2": S2_HONESTY,
            "s2_explained": (
                "This server cannot refuse a call the host never routes through "
                "it. It computes verdicts on request; enforcement belongs to the "
                "host, and no run has yet proven this host honours a deny."
            ),
            "auth": "required (per-node bearer token)",
            "client_allowlist": "required (default-deny; an empty list refuses all)",
        },
        "node_root": str(ctx.node_root),
        "node_root_source": ctx.root_source,
    }


def tool_council(ctx: NodeContext, args: Mapping[str, Any]) -> Dict[str, Any]:
    """The values council over a proposed change to a declared core surface."""
    tool, tool_input = _tool_and_input(args)
    content = args.get("content")
    if content is not None and not isinstance(content, str):
        raise ToolError("`content` must be a string when present")
    if ctx.repo_root is None:
        raise ToolError(
            "the framework checkout could not be located, so the core-surface "
            "declaration cannot be read; the council cannot convene and this is "
            "reported rather than answered as NOT_APPLICABLE"
        )
    try:
        from intentops_core.governance.values_council import (
            core_surface_path, load_core_surface, review_core_write,
        )
        surface = load_core_surface(core_surface_path(ctx.repo_root))
        reading = review_core_write(
            tool, tool_input,
            node_root=ctx.node_root,
            surface=surface,
            content=content,
            indicators=ctx.indicators,
        )
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"the council could not convene: {type(exc).__name__}: {exc}") from exc
    return {
        "operation": "council",
        "applies": reading.applies,
        "reading": reading.to_row(),
        "render": reading.render(),
        "note": ("NOT_APPLICABLE means no declared core surface was targeted -- "
                 "it is not an approval."),
    }


def tool_imprint_verify(ctx: NodeContext, args: Mapping[str, Any]) -> Dict[str, Any]:
    """Are the imprint bytes the claimed ones, and does the trust chain agree?"""
    if ctx.repo_root is None:
        return {
            "operation": "imprint_verify",
            "verified": False,
            "outcome": "UNPROBEABLE",
            "reason": ("the framework checkout could not be located, so the "
                       "imprint cannot be verified; set INTENTOPS_REPO_ROOT. "
                       "This is a refusal, never a pass by absence."),
        }
    try:
        from intentops_core.genesis.provenance import verify_provenance
        record = verify_provenance(ctx.repo_root)
    except Exception as exc:
        raise ToolError(f"provenance verification failed to run: "
                        f"{type(exc).__name__}: {exc}") from exc
    return {
        "operation": "imprint_verify",
        "verified": record.verified,
        "outcome": "VERIFIED" if record.verified else "NOT-VERIFIED",
        "blocking": [c.to_row() for c in record.blocking],
        "faculties_absent": record.faculties_absent,
        "record": record.to_row(),
    }


def tool_discover(ctx: NodeContext, args: Mapping[str, Any]) -> Dict[str, Any]:
    """S6. Typed descriptors of the host. DATA only -- never an artifact."""
    declared = args.get("declared", {})
    if declared is None:
        declared = {}
    if not isinstance(declared, Mapping):
        raise ToolError("`declared` must be an object of capability facts when present")
    try:
        caps = describe_host(
            declared,
            client_name=str(args.get("client_name") or ""),
            client_version=str(args.get("client_version") or ""),
            protocol_version=str(args.get("protocol_version") or ""),
        )
    except PayloadRefused as exc:
        raise ToolError(
            "S6 refuses this declaration: it carries an artifact shape, and this "
            "operation accepts facts about the host, never things from it. "
            + "; ".join(exc.findings)
        ) from exc
    return {"operation": "S6", "descriptor": caps.to_dict()}


#: The closed tool surface. Name -> (description, JSON schema, handler).
TOOL_SPECS: Dict[str, Dict[str, Any]] = {
    "intentops.classify": {
        "description": ("S1: return the tier and whether a proposed tool call "
                        "reaches outside the machine. Pure and deterministic."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool": {"type": "string", "description": "the host tool name"},
                "tool_input": {"type": "object", "description": "the proposed tool input"},
            },
            "required": ["tool"],
        },
        "handler": tool_classify,
    },
    "intentops.gate": {
        "description": ("Full verdict for a proposed tool call: ALLOW, ASK, "
                        "BLOCK or HALT, with reasons. Recorded to the ledger."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool": {"type": "string"},
                "tool_input": {"type": "object"},
            },
            "required": ["tool"],
        },
        "handler": tool_gate,
    },
    "intentops.status": {
        "description": ("Aliveness reading, the calibration string, and the "
                        "stated S2 limitation of this saddle."),
        "inputSchema": {"type": "object", "properties": {}},
        "handler": tool_status,
    },
    "intentops.council": {
        "description": ("Values-council reading for a proposed change to a "
                        "declared core surface."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool": {"type": "string"},
                "tool_input": {"type": "object"},
                "content": {"type": "string", "description": "the proposed new body"},
            },
            "required": ["tool"],
        },
        "handler": tool_council,
    },
    "intentops.imprint_verify": {
        "description": ("Verify the imprint bytes and the trust chain of the "
                        "framework checkout this server is riding."),
        "inputSchema": {"type": "object", "properties": {}},
        "handler": tool_imprint_verify,
    },
    "intentops.discover": {
        "description": ("S6: typed capability descriptors of the host. Returns "
                        "DATA about the host and refuses artifacts from it."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "declared": {
                    "type": "object",
                    "description": ("capability facts: booleans, bounded "
                                    "integers, and closed-enum strings only"),
                },
            },
        },
        "handler": tool_discover,
    },
}


def list_tools() -> List[Dict[str, Any]]:
    """The MCP ``tools/list`` payload -- the closed surface, in a stable order."""
    return [
        {"name": name, "description": spec["description"],
         "inputSchema": spec["inputSchema"]}
        for name, spec in TOOL_SPECS.items()
    ]


def dispatch(name: str, ctx: NodeContext, args: Mapping[str, Any]) -> Dict[str, Any]:
    """Call one tool by name. An unknown name is refused, never guessed."""
    spec = TOOL_SPECS.get(name)
    if spec is None:
        raise ToolError(
            f"unknown tool {name!r}; this server exposes exactly "
            f"{', '.join(sorted(TOOL_SPECS))} and its surface does not grow at runtime"
        )
    handler: Callable[[NodeContext, Mapping[str, Any]], Dict[str, Any]] = spec["handler"]
    return handler(ctx, args)
