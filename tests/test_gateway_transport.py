"""The stdio backend transport, driven adversarially.

PURPOSE
    ``transport.StdioInvoker`` is the only thing in this repository that can
    turn a PERMITTED verdict into a running process, so every one of its
    refusals is load-bearing and every one is tested here by making it fire.
    The order that matters most is the one a message cannot prove: a T3 call
    must be refused by the gate BEFORE the transport is reached, which is
    asserted with a spy that fails the test if ``Popen`` is called at all.

    Two failure shapes are asserted as REFUSALS rather than as values, because
    the plausible-looking alternative is what makes them dangerous: a timeout
    that returned whatever arrived first, and an output cap that truncated,
    would both hand the caller a well-formed result over an incomplete frame.

WRITE MODEL
    None. Each test builds a node root under ``tmp_path`` and writes a fake
    backend script there. No live service is contacted; every subprocess is
    ``sys.executable`` -- this repository's own interpreter -- running a script
    the test just wrote.

BLIND SPOTS
    - These prove the transport refuses. They cannot prove a backend does
      anything sensible once it IS spawned, and nothing here evaluates a
      backend's answer beyond its JSON shape.
    - The env allowlist is tested by a canary the parent sets. A variable the
      child reads from a user profile, a registry, or a credential store is
      outside the seam and outside this test.
    - The kill switch is tested as a file that exists. A switch removed
      concurrently mid-call is not covered; the read happens once per call, at
      the start, and that is the stated contract.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from intentops_core.gate import RealityIndicators
from intentops_gateway import backends as backends_mod
from intentops_gateway import tools
from intentops_gateway import transport as transport_mod
from intentops_saddle_mcp.node import NodeContext

#: The fake backend. Behaviour is chosen by argv so one script covers every
#: shape, and it imports nothing from this project -- a backend is a stranger.
FAKE_BACKEND = '''\
import json, os, sys, time
mode = sys.argv[1]
line = sys.stdin.readline()
if mode == "answer":
    ask = json.loads(line)
    sys.stdout.write(json.dumps({"echo": ask["tool"], "args": ask["args"]}) + "\\n")
elif mode == "env":
    sys.stdout.write(json.dumps({"env": sorted(os.environ)}) + "\\n")
elif mode == "shout":
    sys.stdout.write("x" * 400000 + "\\n")
elif mode == "sleep":
    time.sleep(30)
elif mode == "garbage":
    sys.stdout.write("this is not json\\n")
elif mode == "silent":
    pass
elif mode == "die":
    sys.stderr.write("the backend fell over\\n")
    sys.exit(3)
sys.stdout.flush()
'''

DEMO = backends_mod.Backend(
    id="demo", namespace="demo", transport="stdio",
    tool_tiers={"read_it": "T0", "drop_it": "T3"}, source="<test>")


@pytest.fixture()
def node_root(tmp_path: Path) -> Path:
    root = tmp_path / "node"
    (root / ".intentops").mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture()
def backend_script(tmp_path: Path) -> Path:
    script = tmp_path / "fake_backend.py"
    script.write_text(FAKE_BACKEND, encoding="utf-8")
    return script


def registry_with(*ids: str) -> backends_mod.BackendRegistry:
    return backends_mod.BackendRegistry(
        backends={i: backends_mod.Backend(id=i, namespace=i, transport="stdio",
                                          tool_tiers={"read_it": "T0",
                                                      "drop_it": "T3"},
                                          source="<test>")
                  for i in ids},
        source="<test>")


def invoker(node_root: Path, script: Path, mode: str, **kw: Any) -> Any:
    return transport_mod.StdioInvoker(
        commands={"demo": transport_mod.StdioCommand(
            backend_id="demo",
            argv=(sys.executable, str(script), mode), **kw)},
        registry=registry_with("demo"), node_root=node_root)


class _Spy:
    """A ``Popen`` stand-in whose whole purpose is to prove it was not called."""

    def __init__(self) -> None:
        self.calls: List[Tuple[Any, ...]] = []

    def __call__(self, *a: Any, **k: Any) -> Any:
        self.calls.append(a)
        raise AssertionError(
            "a process was spawned for a call that must have been refused")


# ---------------------------------------------------------------------------
# 1. the happy path exists, so the refusals below mean something
# ---------------------------------------------------------------------------


def test_a_permitted_call_actually_reaches_the_backend(
        node_root: Path, backend_script: Path) -> None:
    result = invoker(node_root, backend_script, "answer").invoke(
        DEMO, "read_it", {"n": 1})
    assert result["backend_result"] == {"echo": "read_it", "args": {"n": 1}}
    assert result["transport"] == "stdio"
    assert isinstance(result["elapsed_ms"], int)


# ---------------------------------------------------------------------------
# 2. the environment is BUILT, never inherited
# ---------------------------------------------------------------------------


def test_the_child_never_receives_an_unlisted_variable(
        node_root: Path, backend_script: Path,
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTENTOPS_TRANSPORT_CANARY", "this-must-not-travel")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    result = invoker(node_root, backend_script, "env").invoke(DEMO, "read_it", {})
    names = set(result["backend_result"]["env"])
    assert "INTENTOPS_TRANSPORT_CANARY" not in names, (
        "the parent's environment reached the child")
    assert "HTTP_PROXY" not in names, "a proxy variable reached the child"


def test_a_command_may_widen_the_allowlist_by_name(
        node_root: Path, backend_script: Path,
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Widening is per-command, explicit, and by NAME -- never a value."""
    monkeypatch.setenv("INTENTOPS_TRANSPORT_DECLARED", "ok")
    result = invoker(node_root, backend_script, "env",
                     env_allowlist=("INTENTOPS_TRANSPORT_DECLARED",)).invoke(
        DEMO, "read_it", {})
    assert "INTENTOPS_TRANSPORT_DECLARED" in set(result["backend_result"]["env"])


# ---------------------------------------------------------------------------
# 3. the caps refuse; they never truncate and never wait
# ---------------------------------------------------------------------------


def test_an_oversize_answer_is_refused_and_never_truncated(
        node_root: Path, backend_script: Path) -> None:
    with pytest.raises(transport_mod.TransportRefused) as caught:
        invoker(node_root, backend_script, "shout",
                max_output_bytes=2000).invoke(DEMO, "read_it", {})
    message = str(caught.value)
    assert "2000 bytes" in message, "the refusal must name the cap it hit"
    assert "xxxx" not in message, "a refusal must not carry the partial output"


def test_a_slow_backend_is_refused_by_the_timeout(
        node_root: Path, backend_script: Path) -> None:
    with pytest.raises(transport_mod.TransportRefused) as caught:
        invoker(node_root, backend_script, "sleep",
                timeout_s=0.5).invoke(DEMO, "read_it", {})
    assert "did not answer within 0.5s" in str(caught.value)


@pytest.mark.parametrize("mode,marker", [
    ("garbage", "not JSON"),
    ("silent", "answered nothing"),
    ("die", "exited 3"),
])
def test_a_broken_backend_is_a_refusal_not_an_empty_result(
        node_root: Path, backend_script: Path, mode: str, marker: str) -> None:
    with pytest.raises(transport_mod.TransportRefused) as caught:
        invoker(node_root, backend_script, mode).invoke(DEMO, "read_it", {})
    assert marker in str(caught.value)


# ---------------------------------------------------------------------------
# 4. the transport's own two checks, each proven with nothing spawned
# ---------------------------------------------------------------------------


def test_an_unlisted_backend_is_refused_before_any_spawn(node_root: Path) -> None:
    spy = _Spy()
    inv = transport_mod.StdioInvoker(commands={}, registry=registry_with("demo"),
                                     node_root=node_root, popen=spy)
    with pytest.raises(transport_mod.TransportRefused) as caught:
        inv.invoke(DEMO, "read_it", {})
    assert "NO command" in str(caught.value)
    assert spy.calls == []


def test_a_backend_the_registry_does_not_carry_is_refused(
        node_root: Path, backend_script: Path) -> None:
    stranger = backends_mod.Backend(id="stranger", namespace="s",
                                    transport="stdio", tool_tiers={},
                                    source="<test>")
    with pytest.raises(transport_mod.TransportRefused) as caught:
        invoker(node_root, backend_script, "answer").invoke(
            stranger, "read_it", {})
    assert "not declared" in str(caught.value)


def test_an_unreadable_registry_spawns_nothing(node_root: Path) -> None:
    spy = _Spy()
    inv = transport_mod.StdioInvoker(
        commands={"demo": transport_mod.StdioCommand(backend_id="demo",
                                                     argv=("no-such-exe",))},
        registry=None, node_root=node_root, popen=spy)
    with pytest.raises(transport_mod.TransportRefused) as caught:
        inv.invoke(DEMO, "read_it", {})
    assert "unreadable registry" in str(caught.value)
    assert spy.calls == []


def test_the_kill_switch_is_read_at_dispatch_time(
        node_root: Path, backend_script: Path) -> None:
    """Constructed while ARMED, switched off afterwards: the call still refuses."""
    spy = _Spy()
    inv = transport_mod.StdioInvoker(
        commands={"demo": transport_mod.StdioCommand(
            backend_id="demo", argv=(sys.executable, str(backend_script),
                                     "answer"))},
        registry=registry_with("demo"), node_root=node_root, popen=spy)
    assert inv.stand_down_reason() is None

    marker = node_root / transport_mod.TRANSPORT_DISABLED_RELPATH
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("held for an incident\n", encoding="utf-8")

    with pytest.raises(transport_mod.TransportRefused) as caught:
        inv.invoke(DEMO, "read_it", {})
    assert "held for an incident" in str(caught.value)
    assert spy.calls == [], "the kill switch was consulted after the spawn"


# ---------------------------------------------------------------------------
# 5. the ORDER: the gate refuses a T3 before the transport is reached
# ---------------------------------------------------------------------------


def _ctx_with(node_root: Path, invoker_obj: Any) -> tools.GatewayContext:
    node = NodeContext(node_root=node_root, repo_root=None, root_source="test",
                       indicators=RealityIndicators(), estate_map_present=False,
                       halted=None)
    return tools.GatewayContext(
        node=node,
        registry=backends_mod.BackendRegistry(backends={"demo": DEMO},
                                              source="<test>"),
        policy=backends_mod.HarnessPolicy(grants={"known": ("demo",)},
                                          always_denied=(), source="<test>"),
        invoker=invoker_obj)


def test_a_t3_call_never_reaches_the_transport(node_root: Path) -> None:
    """The property no message can prove: nothing is spawned for a T3.

    ``_dispatch_backend`` orders allowlist, registry, gate, invoker. A refusal
    message would be identical whether the gate refused first or the transport
    refused second, so this asserts with a spy: if ``Popen`` is reached at all
    the test fails, whatever the caller was told.
    """
    spy = _Spy()
    inv = transport_mod.StdioInvoker(
        commands={"demo": transport_mod.StdioCommand(backend_id="demo",
                                                     argv=("no-such-exe",))},
        registry=registry_with("demo"), node_root=node_root, popen=spy)
    gctx = _ctx_with(node_root, inv)

    with pytest.raises(tools.ToolRefused) as caught:
        tools.dispatch_tool(gctx, "demo__drop_it", {}, harness="known")
    payload: Dict[str, Any] = getattr(caught.value, "payload", {}) or {}
    assert payload.get("executed") is False
    assert payload.get("tier") == "T3"
    assert spy.calls == [], "a T3 call reached the process transport"


def test_an_ungranted_harness_never_reaches_the_transport(node_root: Path) -> None:
    spy = _Spy()
    inv = transport_mod.StdioInvoker(
        commands={"demo": transport_mod.StdioCommand(backend_id="demo",
                                                     argv=("no-such-exe",))},
        registry=registry_with("demo"), node_root=node_root, popen=spy)
    gctx = _ctx_with(node_root, inv)
    with pytest.raises(tools.ToolRefused):
        tools.dispatch_tool(gctx, "demo__read_it", {}, harness="stranger")
    assert spy.calls == []


def test_a_permitted_t0_call_reaches_the_transport_through_the_dispatcher(
        node_root: Path, backend_script: Path) -> None:
    """The control for the two tests above: the seam is genuinely reachable."""
    inv = invoker(node_root, backend_script, "answer")
    gctx = _ctx_with(node_root, inv)
    payload = tools.dispatch_tool(gctx, "demo__read_it", {"n": 2},
                                  harness="known")
    assert payload["executed"] is True
    assert payload["result"]["backend_result"]["echo"] == "read_it"


def test_a_transport_refusal_renders_as_not_executed_through_the_dispatcher(
        node_root: Path, backend_script: Path) -> None:
    """A transport refusal must arrive as a refusal, never as a thin result."""
    inv = invoker(node_root, backend_script, "silent")
    gctx = _ctx_with(node_root, inv)
    with pytest.raises(tools.ToolRefused) as caught:
        tools.dispatch_tool(gctx, "demo__read_it", {}, harness="known")
    payload = getattr(caught.value, "payload", {}) or {}
    assert payload.get("executed") is False
    assert payload.get("control") == "invoker-seam"


# ---------------------------------------------------------------------------
# 6. the shipped default is unchanged, and the table halts on a missing field
# ---------------------------------------------------------------------------


def test_the_default_invoker_is_still_the_null_one(node_root: Path) -> None:
    """Wiring a transport must be an ACT, never something that happened."""
    gctx = tools.GatewayContext(
        node=NodeContext(node_root=node_root, repo_root=None,
                         root_source="test", indicators=RealityIndicators(),
                         estate_map_present=False, halted=None))
    assert type(gctx.invoker).__name__ == "NullInvoker"


def test_a_command_table_halts_on_a_missing_load_bearing_field(
        tmp_path: Path) -> None:
    table = tmp_path / "commands.yaml"
    table.write_text("commands:\n  - backend: demo\n", encoding="utf-8")
    with pytest.raises(ValueError, match="argv"):
        transport_mod.load_commands(table)

    table.write_text("nothing: here\n", encoding="utf-8")
    with pytest.raises(ValueError, match="commands"):
        transport_mod.load_commands(table)

    table.write_text(
        "commands:\n  - backend: demo\n    argv: ['x']\n"
        "  - backend: demo\n    argv: ['y']\n", encoding="utf-8")
    with pytest.raises(ValueError, match="twice"):
        transport_mod.load_commands(table)


def test_a_valid_command_table_round_trips(tmp_path: Path) -> None:
    table = tmp_path / "commands.yaml"
    table.write_text(
        "commands:\n"
        "  - backend: demo\n"
        "    argv: ['some-exe', '--stdio']\n"
        "    env_allowlist: ['SOME_NAME']\n"
        "    timeout_s: 5\n"
        "    max_output_bytes: 2048\n", encoding="utf-8")
    loaded = transport_mod.load_commands(table)
    assert loaded["demo"].argv == ("some-exe", "--stdio")
    assert loaded["demo"].timeout_s == 5
    assert "SOME_NAME" in loaded["demo"].names()
    assert "PATH" in loaded["demo"].names(), "the default names must survive"


def test_the_transport_selftest_passes() -> None:
    ok, report = transport_mod.selftest()
    assert ok, report
    assert "0 failed" in report
