"""Parsing the host's hook payload -- and refusing the ones we do not understand.

PURPOSE
    Every host speaks its own dialect at the gate. This module is where this
    host's dialect stops: it turns the JSON object the runtime writes to stdin
    into a small typed value, and it raises rather than guessing.

    The refusals here are the load-bearing part. A payload we cannot parse is
    a payload whose tool and arguments we do not know, and classifying an
    operation we cannot see is not classification -- it is a coin flip that
    happens to return ALLOW most of the time.

WRITE MODEL
    None -- pure parsing over a string.

BLIND SPOTS
    * ``tool_input`` is required and never defaulted to an empty object. If a
      future host version omits it for argument-less tools, this adapter must
      be CORRECTED rather than made permissive, because the same permissive
      branch would swallow a genuinely malformed payload carrying a push.
    * Extra fields are ignored, so a host that starts carrying a field the
      classifier ought to see (a target account, a sandbox flag) is invisible
      here until someone reads the host's release notes.
    * Parsing says nothing about truth. A host that lies about which tool is
      about to run defeats this adapter completely, and no amount of schema
      checking would notice.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

__all__ = ["HookEvent", "MalformedPayload", "parse_pre_tool", "parse_event"]


class MalformedPayload(ValueError):
    """The payload could not be understood. Always fail closed on this."""


@dataclass(frozen=True)
class HookEvent:
    """One hook invocation, as much of it as this adapter needs."""

    event_name: str
    tool_name: str
    tool_input: Mapping[str, Any]
    cwd: Optional[str] = None
    session_id: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None


def _load(text: str) -> Dict[str, Any]:
    if not text or not text.strip():
        raise MalformedPayload(
            "the hook payload was empty. An empty payload names no tool and no "
            "arguments; there is nothing here to classify."
        )
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MalformedPayload(f"the hook payload is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise MalformedPayload(
            f"the hook payload must be a JSON object, got {type(data).__name__}"
        )
    return data


def parse_pre_tool(text: str) -> HookEvent:
    """Parse a pre-tool payload, or raise.

    ``tool_name`` and ``tool_input`` are both required. Neither has a default,
    and the reason is the same for both: they are the entire operation. A
    missing one is not a small gap to paper over, it is the whole question.
    """
    data = _load(text)
    tool = data.get("tool_name")
    if not isinstance(tool, str) or not tool.strip():
        raise MalformedPayload(
            "the payload carries no usable 'tool_name'. This field is "
            "load-bearing -- without it there is no operation to classify, and "
            "substituting a default would classify something that was never asked."
        )
    if "tool_input" not in data:
        raise MalformedPayload(
            "the payload carries no 'tool_input'. This adapter refuses to "
            "default it to an empty object: the arguments ARE the operation, "
            "and an empty stand-in would read a push as a no-op."
        )
    tool_input = data["tool_input"]
    if not isinstance(tool_input, dict):
        raise MalformedPayload(
            f"'tool_input' must be a JSON object, got {type(tool_input).__name__}"
        )
    return HookEvent(
        event_name=str(data.get("hook_event_name") or "PreToolUse"),
        tool_name=tool.strip(),
        tool_input=tool_input,
        cwd=data.get("cwd") if isinstance(data.get("cwd"), str) else None,
        session_id=(data.get("session_id")
                    if isinstance(data.get("session_id"), str) else None),
        raw=data,
    )


def parse_event(text: str, *, expected: str) -> HookEvent:
    """Parse a non-gating payload (post-tool, stop, session start).

    These events carry no refusal, so a missing tool name is recorded as
    ``"(none)"`` rather than raised -- the honest reading of a session-level
    event that names no tool, not a default standing in for a missing one.
    Malformed JSON still raises: a record we cannot parse is not a record.
    """
    data = _load(text)
    tool = data.get("tool_name")
    tool_input = data.get("tool_input")
    return HookEvent(
        event_name=str(data.get("hook_event_name") or expected),
        tool_name=tool.strip() if isinstance(tool, str) and tool.strip() else "(none)",
        tool_input=tool_input if isinstance(tool_input, dict) else {},
        cwd=data.get("cwd") if isinstance(data.get("cwd"), str) else None,
        session_id=(data.get("session_id")
                    if isinstance(data.get("session_id"), str) else None),
        raw=data,
    )
