"""The closed built-in tool surface, and the dispatch both transports share.

PURPOSE
    Hold the four things the gateway can answer on its own, and the one path by
    which it answers about somebody else's backend. The surface is CLOSED and
    named here: a governance seam whose tool list can grow at runtime is a
    governance seam whose blast radius nobody can state.

      intentops.boot       the read-only boot image (imprint, rules, aliveness,
                           calibration) -- what a cold client asks for first
      intentops.status     what this gateway is, what it can read, and the one
                           thing it cannot do
      intentops.backends   the declared backends and what THIS caller may reach
      intentops.classify   the verdict for a described call, WITHOUT running it

    ONE DISPATCH, TWO TRANSPORTS. ``handle_rpc`` is the whole method
    vocabulary; ``server.py`` puts HTTP in front of it and ``mcp.py`` puts MCP
    stdio in front of it. The transports differ in how a caller presents a
    credential and a name, and in nothing else -- which is the only way "the
    same tools over both surfaces" can be a fact rather than an intention.

    EXECUTION IS A DECLARED SEAM, NOT AN OMISSION. A permitted backend call
    reaches ``BackendInvoker``. The shipped implementation is ``NullInvoker``,
    which refuses and says so. The gateway core decides WHETHER a call may
    happen; wiring a transport that makes it happen is a separate, reviewed
    act. A caller therefore never receives a fabricated success, and the seam
    is visible in ``intentops.status`` rather than discovered later.

WRITE MODEL
    None directly. Every path appends to the request ledger (append-only
    JSONL, declared in ``ledger.py``) and an ASK additionally files an
    approval (per-item files, declared in the queue). This module holds no
    state between calls -- the gateway is stateless by construction.

BLIND SPOTS
    * ``intentops.classify`` answers about a call the caller DESCRIBES. A
      caller that describes a different call than it intends to make gets a
      correct verdict about the wrong thing. It is a read, not a gate.
    * The status answer reports what the gateway could READ, not what is
      running. A declared backend with a dead process reads identically here.
    * The gateway computes refusals; it cannot refuse a call a harness never
      routes through it. That is the S2 gap, reported in ``status`` verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Protocol, Tuple

from intentops_core.gate import RealityIndicators
from intentops_saddle_mcp.node import EstateUnreadable, NodeContext, load_context

# The JSON-RPC error vocabulary is IMPORTED from the MCP saddle's protocol
# module, never re-declared: both surfaces then speak one set of codes, and a
# client that learns one learns the other. A second copy of a wire contract is
# a second thing to drift.
from intentops_saddle_mcp.protocol import (
    ERR_ALLOWLIST,
    ERR_INTERNAL,
    ERR_INVALID_PARAMS,
    ERR_METHOD_NOT_FOUND,
    ERR_STOOD_DOWN,
    ERR_UNAUTHENTICATED,
)

from . import backends as backends_mod
from . import boot as boot_mod
from . import ledger, tiers, tokens

__all__ = [
    "BUILTIN_TOOL_NAMES",
    "ERR_ALLOWLIST",
    "ERR_INTERNAL",
    "ERR_INVALID_PARAMS",
    "ERR_METHOD_NOT_FOUND",
    "ERR_STOOD_DOWN",
    "ERR_UNAUTHENTICATED",
    "EstateUnreadable",
    "BackendInvoker",
    "GatewayContext",
    "NotWired",
    "NullInvoker",
    "RpcError",
    "S2_HONESTY",
    "ToolRefused",
    "dispatch_tool",
    "handle_rpc",
    "list_tools",
    "load_gateway_context",
]

#: Returned by ``status`` verbatim. A stated limitation, not a slogan.
S2_HONESTY = "S2: harness-honoured=unknown"

BUILTIN_TOOL_NAMES: Tuple[str, ...] = (
    "intentops.boot",
    "intentops.status",
    "intentops.backends",
    "intentops.classify",
)

#: Every built-in is a READ. None of them reaches reality, none of them writes
#: anything a caller controls, and the tier says so rather than being inferred.
_BUILTIN_TIERS: Dict[str, str] = {name: "T0" for name in BUILTIN_TOOL_NAMES}


class RpcError(RuntimeError):
    """A JSON-RPC-level failure, carrying the code the transport should send."""

    def __init__(self, code: int, message: str,
                 data: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data or {}


class ToolRefused(RuntimeError):
    """A tool-level refusal: the client is meant to SEE this, not retry it."""

    def __init__(self, message: str, payload: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.payload = payload or {}


class NotWired(RuntimeError):
    """No backend invoker is wired. Never a silent success."""


class BackendInvoker(Protocol):
    """The seam between deciding a call may happen and making it happen."""

    def invoke(self, backend: backends_mod.Backend, tool: str,
               args: Mapping[str, Any]) -> Dict[str, Any]:  # pragma: no cover
        ...


class NullInvoker:
    """The shipped invoker: refuses, and says exactly why.

    This is the seam's honest default. A gateway that fabricated a result here
    would be worse than one that refuses -- the caller would act on it.
    """

    def invoke(self, backend: backends_mod.Backend, tool: str,
               args: Mapping[str, Any]) -> Dict[str, Any]:
        raise NotWired(
            f"the call to {backend.id}__{tool} was PERMITTED by the gate and "
            "was not executed: no backend invoker is wired into this gateway. "
            "The gateway core decides whether a call may happen; making it "
            "happen is a separate, reviewed wiring decision."
        )


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------


@dataclass
class GatewayContext:
    """Everything from disk one request needs, gathered once.

    Registry and policy failures are CARRIED, not raised at load: an
    unreadable policy must refuse every backend call while still letting
    ``intentops.status`` report why. A gateway that cannot start because its
    allowlist is malformed leaves the operator no instrument to diagnose it.
    """

    node: NodeContext
    registry: Optional[backends_mod.BackendRegistry] = None
    registry_error: Optional[str] = None
    policy: Optional[backends_mod.HarnessPolicy] = None
    policy_error: Optional[str] = None
    invoker: BackendInvoker = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.invoker is None:
            self.invoker = NullInvoker()

    @property
    def node_root(self) -> Path:
        return self.node.node_root

    @property
    def repo_root(self) -> Optional[Path]:
        return self.node.repo_root

    @property
    def indicators(self) -> RealityIndicators:
        return self.node.indicators


def load_gateway_context(start: Optional[str] = None,
                         *, invoker: Optional[BackendInvoker] = None) -> GatewayContext:
    """Gather the on-disk half of every decision. Raises only on an UNREADABLE estate map."""
    node = load_context(start)
    registry: Optional[backends_mod.BackendRegistry] = None
    registry_error: Optional[str] = None
    estate_root = node.repo_root or node.node_root
    try:
        registry = backends_mod.load_registry(estate_root)
    except backends_mod.RegistryUnavailable as exc:
        registry_error = str(exc)
    policy: Optional[backends_mod.HarnessPolicy] = None
    policy_error: Optional[str] = None
    try:
        policy = backends_mod.load_policy(node.repo_root)
    except backends_mod.PolicyUnavailable as exc:
        policy_error = str(exc)
    return GatewayContext(node=node, registry=registry,
                          registry_error=registry_error, policy=policy,
                          policy_error=policy_error,
                          invoker=invoker or NullInvoker())


# ---------------------------------------------------------------------------
# the tool surface
# ---------------------------------------------------------------------------


def list_tools() -> List[Dict[str, Any]]:
    """The closed built-in surface, in MCP tool-descriptor shape.

    Backend tools are deliberately ABSENT from this list even when the registry
    holds some: a tool list is a capability claim, and the gateway cannot claim
    a backend it has no invoker for. ``intentops.backends`` reports what is
    declared, which is a different and honest statement.
    """
    return [
        {
            "name": "intentops.boot",
            "title": "Boot image",
            "description": ("The read-only boot image: which imprint this node "
                            "carries, its rules corpus in full, its last "
                            "aliveness reading, and its calibration string."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "include_rule_text": {
                        "type": "boolean",
                        "description": ("Serve the full text of each rule "
                                        "(default true). False returns names "
                                        "and sizes only."),
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "intentops.status",
            "title": "Gateway status",
            "description": ("What this gateway is, what it could read, and the "
                            "one thing it cannot do."),
            "inputSchema": {"type": "object", "properties": {},
                            "additionalProperties": False},
        },
        {
            "name": "intentops.backends",
            "title": "Declared backends",
            "description": ("The declared backend population and which of them "
                            "the calling harness is granted."),
            "inputSchema": {"type": "object", "properties": {},
                            "additionalProperties": False},
        },
        {
            "name": "intentops.classify",
            "title": "Classify a call",
            "description": ("The verdict for a described call, WITHOUT running "
                            "it. A read, never a gate."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "tool": {"type": "string",
                             "description": "the tool name, namespaced as "
                                            "<backend>__<tool> for a backend tool"},
                    "arguments": {"type": "object",
                                  "description": "the arguments the call would carry"},
                },
                "required": ["tool"],
                "additionalProperties": False,
            },
        },
    ]


def dispatch_tool(gctx: GatewayContext, name: str, args: Mapping[str, Any],
                  *, harness: Optional[str]) -> Dict[str, Any]:
    """Run one built-in, or take a backend call through the gate.

    Raises ``ToolRefused`` for anything the caller is meant to SEE -- an
    unknown tool, a denied backend, a refusing verdict. Never returns a hollow
    success.
    """
    tool = str(name or "").strip()
    if tool in BUILTIN_TOOL_NAMES:
        # Recorded like every other call. A ledger that holds only backend
        # traffic answers "what reached the estate" and not "what was asked of
        # this node", and the second question is the one an audit asks first.
        payload = _dispatch_builtin(gctx, tool, args, harness=harness)
        _record(gctx, {"kind": "tool", "tool": tool, "surface": "built-in",
                       "harness": harness or "undeclared",
                       "argument_keys": ledger.safe_arg_keys(args),
                       "tier": _BUILTIN_TIERS[tool], "outcome": "OK"})
        return payload

    backend_id, backend_tool = tiers.split_tool_name(tool)
    if backend_id is None:
        raise ToolRefused(
            f"unknown tool {tool!r}. This gateway serves a CLOSED built-in "
            f"surface ({', '.join(BUILTIN_TOOL_NAMES)}) plus backend tools "
            "addressed as <backend>__<tool>. An unrecognised name is refused, "
            "never guessed at.")
    return _dispatch_backend(gctx, backend_id, backend_tool, args,
                             harness=harness, address=tool)


def _dispatch_builtin(gctx: GatewayContext, tool: str, args: Mapping[str, Any],
                      *, harness: Optional[str]) -> Dict[str, Any]:
    if tool == "intentops.boot":
        include = args.get("include_rule_text", True)
        if not isinstance(include, bool):
            # `bool("false")` is True, and the inputSchema says boolean. A
            # caller who sent the string "false" asked for LESS and would have
            # been served MORE -- coercion in the permissive direction, which
            # SIG-1 clause 3 refuses: invalid frames are NAK'd, never coerced.
            raise ToolRefused(
                "`include_rule_text` must be a JSON boolean (true/false), not "
                f"{type(include).__name__}. It is not coerced: the string "
                "\"false\" is truthy in Python, so coercing it would serve "
                "the opposite of what was asked.")
        return boot_mod.boot_image(gctx.node_root, gctx.repo_root,
                                   include_rule_text=include)
    if tool == "intentops.status":
        return _status(gctx, harness=harness)
    if tool == "intentops.backends":
        return _backends_view(gctx, harness=harness)
    if tool == "intentops.classify":
        target = str(args.get("tool") or "").strip()
        if not target:
            raise ToolRefused("`tool` is required: classify what?")
        call_args = args.get("arguments") or {}
        if not isinstance(call_args, Mapping):
            raise ToolRefused("`arguments` must be an object when present")
        # The allowlist first, exactly as `_dispatch_backend` does it: "a
        # harness with no grant has no business learning what a backend's
        # tools are tiered at" was the stated rule and this path did not
        # honour it -- an ungranted harness could read a backend's tiering
        # through the classifier. Corrected 2026-09-06. Built-ins and
        # unnamespaced names are unaffected: there is nothing to grant.
        target_backend, _ = tiers.split_tool_name(target)
        if target_backend is not None:
            if gctx.policy is None:
                raise ToolRefused(
                    gctx.policy_error or "the backend allowlist could not be "
                    "read; every backend call is refused")
            permitted, reason = gctx.policy.permits(harness, target_backend)
            if not permitted:
                _record(gctx, {"kind": "refusal", "why": "allowlist",
                               "tool": target, "surface": "classify",
                               "harness": harness or "undeclared",
                               "backend": target_backend})
                raise ToolRefused(reason, {"control": "backend-allowlist",
                                           "default": "deny"})
        verdict, detail = _verdict_for_call(gctx, target, call_args)
        payload = dict(verdict.to_dict())
        payload.update(detail)
        payload["executed"] = False
        payload["note"] = ("A classification is a READ. Nothing was run, and "
                           "an ALLOW here is not an execution.")
        return payload
    raise ToolRefused(f"unknown built-in {tool!r}")  # pragma: no cover - guarded above


def _verdict_for_call(gctx: GatewayContext, address: str,
                      args: Mapping[str, Any]):
    """Classify ``address`` and return ``(verdict, detail)``."""
    backend_id, tool = tiers.split_tool_name(address)
    detail: Dict[str, Any] = {"tool": address, "backend": backend_id}
    declared: Optional[str]
    if backend_id is None:
        declared = _BUILTIN_TIERS.get(address)
        detail["surface"] = "built-in" if declared else "unknown"
    else:
        detail["surface"] = "backend"
        backend = gctx.registry.get(backend_id) if gctx.registry else None
        if backend is None:
            declared = None
            detail["backend_declared"] = False
        else:
            declared = backend.tier_for(tool)
            detail["backend_declared"] = True
    # Show the CORE classifier the qualified address for a backend call. Its
    # outbound-communication and third-party-disclosure branches key on the
    # `mcp__` prefix, and a backend tool arrives here under its own local name
    # -- so `mail__send_message` was classified no-opinion by construction
    # while the identical call named `mcp__mail__send_message` read T3.
    # Corrected 2026-09-06. The name is shown to a classifier only; what
    # executes is unchanged, and the merge can only raise the tier.
    classifier_name = (f"mcp__{backend_id}__{tool}" if backend_id else None)
    detail["classified_as"] = classifier_name or tool
    verdict = tiers.classify_call(tool=tool, args=args, declared_tier=declared,
                                  backend=backend_id,
                                  indicators=gctx.indicators,
                                  classifier_name=classifier_name)
    return verdict, detail


def _dispatch_backend(gctx: GatewayContext, backend_id: str, tool: str,
                      args: Mapping[str, Any], *, harness: Optional[str],
                      address: str) -> Dict[str, Any]:
    # 1. the allowlist, before anything is classified. A harness with no grant
    #    has no business learning what a backend's tools are tiered at.
    if gctx.policy is None:
        raise ToolRefused(
            gctx.policy_error or "the backend allowlist could not be read; "
            "every backend call is refused")
    permitted, reason = gctx.policy.permits(harness, backend_id)
    if not permitted:
        _record(gctx, {"kind": "refusal", "why": "allowlist", "tool": address,
                       "harness": harness or "undeclared",
                       "backend": backend_id})
        raise ToolRefused(reason, {"control": "backend-allowlist",
                                   "default": "deny"})

    # 2. the registry. A granted backend that does not exist is a policy naming
    #    something the estate does not declare -- reported, never assumed.
    if gctx.registry is None:
        raise ToolRefused(
            gctx.registry_error or "the backend registry could not be read")
    backend = gctx.registry.get(backend_id)
    if backend is None:
        raise ToolRefused(
            f"backend {backend_id!r} is granted to this harness but is not "
            f"declared in {gctx.registry.source}; the gateway will not invent "
            "a backend a grant names.")

    # 3. the gate.
    verdict, detail = _verdict_for_call(gctx, address, args)
    arg_keys = ledger.safe_arg_keys(args)
    if verdict.is_refusal:
        approval_id = None
        if verdict.decision.value == "ASK":
            approval_id = tiers.file_approval(
                gctx.node_root, tool=address, verdict=verdict,
                harness=harness, arg_keys=arg_keys)
        _record(gctx, {"kind": "verdict", "tool": address,
                       "harness": harness or "undeclared",
                       "backend": backend_id, "decision": verdict.decision.value,
                       "tier": verdict.tier, "argument_keys": arg_keys,
                       "approval_id": approval_id})
        payload = dict(verdict.to_dict())
        payload.update(detail)
        payload["approval_id"] = approval_id
        payload["executed"] = False
        raise ToolRefused(verdict.render(), payload)

    # 4. permitted -- and the seam says so out loud.
    _record(gctx, {"kind": "verdict", "tool": address,
                   "harness": harness or "undeclared", "backend": backend_id,
                   "decision": verdict.decision.value, "tier": verdict.tier,
                   "argument_keys": arg_keys})
    try:
        result = gctx.invoker.invoke(backend, tool, args)
    except NotWired as exc:
        raise ToolRefused(str(exc), {"control": "invoker-seam",
                                     "permitted": True,
                                     "executed": False}) from exc
    payload = dict(verdict.to_dict())
    payload.update(detail)
    payload["executed"] = True
    payload["result"] = result
    return payload


def _status(gctx: GatewayContext, *, harness: Optional[str]) -> Dict[str, Any]:
    token_state: Dict[str, Any]
    try:
        token_state = {"minted": True, **tokens.load_record(gctx.node_root).public()}
    except tokens.AuthUnavailable as exc:
        token_state = {"minted": False, "reason": str(exc)}
    return {
        "schema": "gateway-status/v1",
        "node_root": str(gctx.node_root),
        "node_root_source": gctx.node.root_source,
        "repo_root": str(gctx.repo_root) if gctx.repo_root else None,
        "stood_down": gctx.node.halted,
        "estate_map_present": gctx.node.estate_map_present,
        "token": token_state,
        "controls": {
            "authentication": "ON -- unconditional, no anonymous identity",
            "backend_allowlist": "ON -- default-deny, no value meaning 'all'",
            "untagged_tool": f"{tiers.UNTAGGED_TIER}/deny (R-D2)",
        },
        "registry": (gctx.registry.to_dict() if gctx.registry
                     else {"status": "UNREADABLE", "error": gctx.registry_error}),
        "policy": (gctx.policy.to_dict() if gctx.policy
                   else {"status": "UNREADABLE", "error": gctx.policy_error}),
        "harness": harness or None,
        "invoker": type(gctx.invoker).__name__,
        "builtin_tools": list(BUILTIN_TOOL_NAMES),
        "limitation": S2_HONESTY,
        "limitation_detail": (
            "This gateway computes and records verdicts. It cannot refuse a "
            "call a harness never routes through it, and it has no way to "
            "observe one. That is why a harness adapter exists, and why this "
            "field is reported rather than omitted."
        ),
    }


def _backends_view(gctx: GatewayContext, *, harness: Optional[str]) -> Dict[str, Any]:
    registry = (gctx.registry.to_dict() if gctx.registry
                else {"status": "UNREADABLE", "error": gctx.registry_error})
    reachable: List[Dict[str, Any]] = []
    if gctx.registry and gctx.policy:
        for backend in sorted(gctx.registry.backends.values(), key=lambda b: b.id):
            permitted, reason = gctx.policy.permits(harness, backend.id)
            reachable.append({"backend": backend.id, "permitted": permitted,
                              "reason": reason})
    return {
        "schema": "gateway-backends/v1",
        "harness": harness or None,
        "declared": registry,
        "reachable_by_this_harness": reachable,
        "policy": (gctx.policy.to_dict() if gctx.policy
                   else {"status": "UNREADABLE", "error": gctx.policy_error}),
        "note": ("An empty declared population means this node has been given "
                 "no backends. The gateway then serves only its built-in "
                 "intentops.* tools, which is the honest birth state."),
    }


def _record(gctx: GatewayContext, event: Dict[str, Any]) -> None:
    """Append to the request ledger. A failure to record is RAISED.

    A governance surface whose audit trail has silently gone away is worse
    than one that refuses to serve, so this is never swallowed here; the
    transport owns turning it into a frame the caller can read.
    """
    ledger.append_event(gctx.node_root, event)


# ---------------------------------------------------------------------------
# the method vocabulary, shared by both transports
# ---------------------------------------------------------------------------

_METHODS: Tuple[str, ...] = ("ping", "tools/list", "tools/call")


def handle_rpc(gctx: GatewayContext, method: str, params: Mapping[str, Any],
               *, harness: Optional[str]) -> Dict[str, Any]:
    """Handle one JSON-RPC method. Returns the ``result`` payload.

    Raises ``RpcError`` for a transport-level failure and ``ToolRefused`` for a
    governance answer the client is meant to read. The distinction matters: a
    refusal is a RESULT, not a fault, and rendering it as a fault teaches
    clients to retry it.
    """
    if gctx.node.halted:
        raise RpcError(ERR_STOOD_DOWN,
                       f"this node is stood down: {gctx.node.halted}",
                       {"remedy": "remove the stand-down marker deliberately; "
                                  "a stood-down node is OFF, not broken"})
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": list_tools()}
    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str) or not name.strip():
            raise RpcError(ERR_INVALID_PARAMS, "`name` is required")
        args = params.get("arguments")
        if args is None:
            args = {}
        if not isinstance(args, Mapping):
            raise RpcError(ERR_INVALID_PARAMS, "`arguments` must be an object")
        return dispatch_tool(gctx, name, args, harness=harness)
    raise RpcError(ERR_METHOD_NOT_FOUND,
                   f"method {method!r} is not part of this gateway's subset "
                   f"({', '.join(_METHODS)})")
