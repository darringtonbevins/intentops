"""S6 discover -- learn the SHAPE of the host, never take its ARTIFACTS.

PURPOSE
    A node that knows its host has a million-token context, subagents, a
    prefix cache with a TTL, and parallel tool calls can ROUTE differently.
    That is the whole of the assimilation the memetic program asks for, and
    the whole of what is safe: the node learns a fact and changes a decision.

    The fence is a TYPE BOUNDARY, not a policy, and it is the no-skill-
    marketplace rule expressed in code:

      permitted   a capability DESCRIPTOR -- booleans, integers, and values
                  from closed enumerations. Data about the host.
      forbidden   an ARTIFACT -- a skill file, a prompt template, a tool
                  definition, an agent card, a script, any executable shape.

    A marketplace's artifacts load into an agent's context before any gate has
    an opinion; one such marketplace shipped 1,184 malicious packages across
    12 publisher accounts. So the refusal here is not "we do not execute it" --
    it is "we do not accept it into the record at all". Nothing this module
    returns has ever been a string the host authored: every declared field is
    coerced into a bool, a bounded int, or a member of a closed enum, and
    anything else is REFUSED and named, never dropped quietly.

WRITE MODEL
    None -- pure functions over the declaration the client sent. The caller
    decides whether to persist the descriptor; this module never writes.

BLIND SPOTS
    * The host DECLARES these capabilities; nothing here measures them. A host
      that overstates its context ceiling is believed. The descriptor therefore
      carries ``evidence: "declared"`` on every field, and a routing decision
      that treats a declaration as a measurement is the caller's error, not a
      gap this module can close.
    * Refusal is by SHAPE: a suspicious key name, a value long enough to be a
      payload, or a nested structure. A tiny artifact hidden inside an integer
      field is not expressible, which is the point; an artifact smuggled as a
      short enum value would be indistinguishable from an honest one, and the
      closed enums are what bound that.
    * These descriptors NEVER reach the gate. Routing consumes them; the tier
      ladder, the reaches-reality classifier and the councils do not, so a
      lying host cannot widen its own permissions by lying here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

__all__ = [
    "EXECUTABLE_KEY_PATTERN",
    "HostCapabilities",
    "PayloadRefused",
    "describe_host",
]


class PayloadRefused(ValueError):
    """The host offered something that is not a descriptor.

    Raised, never swallowed: a silently-dropped payload is a fence that looks
    armed and is not, and the caller must be able to tell an honest host that
    declared nothing from a host that tried to hand us an artifact.
    """

    def __init__(self, findings: List[str]) -> None:
        self.findings = list(findings)
        super().__init__("; ".join(findings) if findings else "payload refused")


#: Key names that name an ARTIFACT rather than a fact about the host. Matched
#: as substrings on the lowercased key, deliberately broad: the error this
#: trades against is refusing an honestly-named field, which costs a rename in
#: a reviewed change. The opposite error costs an artifact in the context.
EXECUTABLE_KEY_PATTERN = re.compile(
    r"skill|plugin|prompt|template|instruction|script|command|code|payload"
    r"|agent[_-]?card|tool[_-]?def|handler|exec|eval|hook|macro|snippet",
    re.IGNORECASE,
)

#: Value shapes that are an artifact wearing a value's clothes.
_EXECUTABLE_VALUE_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("shebang", re.compile(r"^#!")),
    ("markup or code block", re.compile(r"```|<\s*script\b|<\s*\?php", re.IGNORECASE)),
    ("import or definition", re.compile(r"\b(?:import|require|def|function|class)\s+\w")),
    ("data url", re.compile(r"^data:[\w/.+-]+;base64,", re.IGNORECASE)),
    ("shell pipeline", re.compile(r"[;&|]{1,2}\s*(?:rm|curl|wget|sh|bash|powershell)\b",
                                  re.IGNORECASE)),
)

#: A declared string value longer than this is a payload, not a fact. Every
#: legitimate value below is an enum member of at most a couple of words.
_MAX_VALUE_CHARS = 64

#: Closed enumerations. A value outside its enum is UNKNOWN, never the value.
_ENUMS: Dict[str, Tuple[str, ...]] = {
    "refusal_support": ("honoured", "advisory", "none", "unknown"),
    "cache_scope": ("prefix", "none", "unknown"),
}

#: Integer fields and their sane bounds. Out of range is UNKNOWN, never clamped
#: -- a clamped value is a measurement nobody took wearing a plausible number.
_INTS: Dict[str, Tuple[int, int]] = {
    "context_ceiling_tokens": (1, 100_000_000),
    "cache_ttl_seconds": (0, 86_400),
    "max_parallel_tool_calls": (1, 4_096),
}

#: Boolean fields.
_BOOLS: Tuple[str, ...] = (
    "supports_subagents",
    "supports_workflows",
    "supports_parallel_tool_calls",
    "supports_session_start_hook",
    "supports_prefix_cache",
)

_UNKNOWN = "unknown"


@dataclass(frozen=True)
class HostCapabilities:
    """A typed, enumerable record of what the host says it offers.

    Every field is a bool, a bounded int, or a closed-enum string. There is no
    free-text field and no place to put one, which is the fence made structural
    rather than procedural.
    """

    client_name: str = ""
    client_version: str = ""
    protocol_version: str = ""
    context_ceiling_tokens: Optional[int] = None
    cache_ttl_seconds: Optional[int] = None
    max_parallel_tool_calls: Optional[int] = None
    supports_subagents: Optional[bool] = None
    supports_workflows: Optional[bool] = None
    supports_parallel_tool_calls: Optional[bool] = None
    supports_session_start_hook: Optional[bool] = None
    supports_prefix_cache: Optional[bool] = None
    refusal_support: str = _UNKNOWN
    cache_scope: str = _UNKNOWN
    #: Fields the host declared that this module could not type. Named, never
    #: dropped: a refusal stays in the denominator.
    undeclared: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": "host-capability-descriptor",
            "evidence": "declared",
            "evidence_note": (
                "The host declared these; nothing here measured them. Route on "
                "them; never treat a declaration as a measurement."
            ),
            "client_name": self.client_name,
            "client_version": self.client_version,
            "protocol_version": self.protocol_version,
            "context_ceiling_tokens": self.context_ceiling_tokens,
            "cache_ttl_seconds": self.cache_ttl_seconds,
            "max_parallel_tool_calls": self.max_parallel_tool_calls,
            "supports_subagents": self.supports_subagents,
            "supports_workflows": self.supports_workflows,
            "supports_parallel_tool_calls": self.supports_parallel_tool_calls,
            "supports_session_start_hook": self.supports_session_start_hook,
            "supports_prefix_cache": self.supports_prefix_cache,
            "refusal_support": self.refusal_support,
            "cache_scope": self.cache_scope,
            "can_refuse_a_call": self.refusal_support == "honoured",
            "unrecognised_fields": list(self.undeclared),
            "fence": (
                "S6 returns DATA about the host and never an artifact from it. "
                "Nothing in this record is executed, stored as code, or loaded "
                "into a context as instructions."
            ),
        }


def _scan_for_artifacts(declared: Mapping[str, Any], *, prefix: str = "") -> List[str]:
    """Every reason this declaration is not a descriptor. Empty means clean."""
    findings: List[str] = []
    for key, value in declared.items():
        name = f"{prefix}{key}"
        if not isinstance(key, str):
            findings.append(f"{name!r}: a non-string key is not a capability name")
            continue
        if EXECUTABLE_KEY_PATTERN.search(key):
            findings.append(
                f"{name!r}: names an artifact (skill, prompt, script, tool "
                "definition or handler), and S6 accepts facts about the host, "
                "never things from it"
            )
        if isinstance(value, Mapping):
            # A descriptor is FLAT by construction. Nesting is where an
            # artifact hides, and a structure is the shape of a document, not
            # the shape of a fact. Recurse anyway, so the refusal names
            # everything wrong rather than only the outermost thing.
            findings.append(
                f"{name!r}: a nested object value is a payload shape; a "
                "capability descriptor is flat -- a bool, a bounded integer, "
                "or an enum member per field"
            )
            findings.extend(_scan_for_artifacts(value, prefix=f"{name}."))
            continue
        if isinstance(value, (list, tuple, set)):
            findings.append(
                f"{name!r}: a collection value is a payload shape; a capability "
                "descriptor carries a bool, a bounded integer, or an enum member"
            )
            continue
        if isinstance(value, str):
            if len(value) > _MAX_VALUE_CHARS:
                findings.append(
                    f"{name!r}: value is {len(value)} characters, over the "
                    f"{_MAX_VALUE_CHARS}-character descriptor limit -- that is a "
                    "payload, not a fact"
                )
            for label, pattern in _EXECUTABLE_VALUE_PATTERNS:
                if pattern.search(value):
                    findings.append(f"{name!r}: value carries an executable shape ({label})")
    return findings


def _typed_bool(value: Any) -> Optional[bool]:
    return value if isinstance(value, bool) else None


def _typed_int(value: Any, bounds: Tuple[int, int]) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    low, high = bounds
    return value if low <= value <= high else None


def _typed_enum(value: Any, allowed: Tuple[str, ...]) -> str:
    if isinstance(value, str) and value.strip().lower() in allowed:
        return value.strip().lower()
    return _UNKNOWN


def _short(value: Any) -> str:
    """A short, non-executable identity string, or empty. Never free text."""
    if not isinstance(value, str):
        return ""
    trimmed = value.strip()
    if not trimmed or len(trimmed) > _MAX_VALUE_CHARS:
        return ""
    return trimmed if re.fullmatch(r"[\w .:+-]{1,64}", trimmed) else ""


def describe_host(
    declared: Optional[Mapping[str, Any]],
    *,
    client_name: str = "",
    client_version: str = "",
    protocol_version: str = "",
) -> HostCapabilities:
    """Coerce a host declaration into a descriptor, or refuse it outright.

    Raises :class:`PayloadRefused` when the declaration carries an artifact
    shape. Unknown-but-harmless keys are recorded in ``undeclared`` so the
    caller can see what was not understood rather than believing everything was.
    """
    body: Mapping[str, Any] = declared if isinstance(declared, Mapping) else {}
    findings = _scan_for_artifacts(body)
    if findings:
        raise PayloadRefused(findings)

    known = set(_BOOLS) | set(_INTS) | set(_ENUMS)
    undeclared = tuple(sorted(str(k) for k in body if str(k) not in known))

    return HostCapabilities(
        client_name=_short(client_name),
        client_version=_short(client_version),
        protocol_version=_short(protocol_version),
        context_ceiling_tokens=_typed_int(
            body.get("context_ceiling_tokens"), _INTS["context_ceiling_tokens"]),
        cache_ttl_seconds=_typed_int(
            body.get("cache_ttl_seconds"), _INTS["cache_ttl_seconds"]),
        max_parallel_tool_calls=_typed_int(
            body.get("max_parallel_tool_calls"), _INTS["max_parallel_tool_calls"]),
        supports_subagents=_typed_bool(body.get("supports_subagents")),
        supports_workflows=_typed_bool(body.get("supports_workflows")),
        supports_parallel_tool_calls=_typed_bool(body.get("supports_parallel_tool_calls")),
        supports_session_start_hook=_typed_bool(body.get("supports_session_start_hook")),
        supports_prefix_cache=_typed_bool(body.get("supports_prefix_cache")),
        refusal_support=_typed_enum(body.get("refusal_support"), _ENUMS["refusal_support"]),
        cache_scope=_typed_enum(body.get("cache_scope"), _ENUMS["cache_scope"]),
        undeclared=undeclared,
    )
