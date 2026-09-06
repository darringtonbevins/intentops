"""The gateway: authentication, the allowlist, the ladder, and the boot image.

PURPOSE
    Drive the real modules against a REAL socket on a free port and against
    temporary node roots. No live service, no network beyond loopback, no key
    material committed: every token is generated inside a temp directory at
    test time and dies with it.

    The suite is organised around the failures that matter, not around the code
    that exists. Three of them are the controls the reference implementation
    shipped disabled -- an unauthenticated call, an ungranted backend, and a
    tool nobody classified -- because a suite that only proves the happy path
    proves the gate is present, never that it is armed.

BLIND SPOTS
    * These tests prove this GATEWAY refuses. They cannot prove a harness
      routes its calls through it, which is the stated S2 gap.
    * The HTTP tests bind loopback on port 0. They exercise the transport, not
      any deployment's network posture.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
for _pkg in ("intentops-saddle-mcp", "intentops-gateway"):
    _path = REPO_ROOT / "packages" / _pkg
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from intentops_core.gate import RealityIndicators  # noqa: E402
from intentops_core.gate.verdict import Decision  # noqa: E402
from intentops_saddle_mcp.node import NodeContext  # noqa: E402
from intentops_saddle_mcp.protocol import (  # noqa: E402
    PROTOCOL_VERSION,
    Session,
)
from intentops_saddle_mcp import protocol as saddle_protocol  # noqa: E402

from intentops_gateway import backends as backends_mod  # noqa: E402
from intentops_gateway import boot as boot_mod  # noqa: E402
from intentops_gateway import ledger, mcp, server, tiers, tokens, tools  # noqa: E402


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _node(tmp_path: Path, *, halted: Optional[str] = None,
          repo_root: Optional[Path] = None) -> NodeContext:
    root = tmp_path / "node"
    (root / ".intentops").mkdir(parents=True, exist_ok=True)
    return NodeContext(node_root=root, repo_root=repo_root,
                       root_source="test", indicators=RealityIndicators(),
                       estate_map_present=False, halted=halted)


DEMO = backends_mod.Backend(
    id="demo", namespace="demo", transport="stdio",
    tool_tiers={"read_it": "T0", "drop_it": "T3"}, source="<test>")


def _ctx(tmp_path: Path, *, grants: Optional[Dict[str, Tuple[str, ...]]] = None,
         backends: Optional[Dict[str, backends_mod.Backend]] = None,
         halted: Optional[str] = None,
         repo_root: Optional[Path] = None) -> tools.GatewayContext:
    return tools.GatewayContext(
        node=_node(tmp_path, halted=halted, repo_root=repo_root),
        registry=backends_mod.BackendRegistry(backends=backends or {},
                                              source="<test>"),
        policy=backends_mod.HarnessPolicy(grants=grants or {},
                                          always_denied=(), source="<test>"))


@pytest.fixture()
def live(tmp_path: Path):
    """A running gateway on a free loopback port, with one granted harness."""
    import io

    gctx = _ctx(tmp_path, grants={"known": ("demo",)}, backends={"demo": DEMO})
    token = tokens.mint(gctx.node_root)
    with server.GatewayServer(gctx, host=server.DEFAULT_BINDING, port=0,
                              diag=io.StringIO()) as running:
        yield running, token, gctx


def _post(url: str, body: Dict[str, Any],
          headers: Optional[Dict[str, str]] = None) -> Tuple[int, Dict[str, Any]]:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# 1. authentication -- unconditional, and there is no anonymous
# ---------------------------------------------------------------------------


def test_no_token_is_401_and_never_anonymous(live) -> None:
    running, _token, _gctx = live
    status, frame = _post(running.url, {"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert status == 401
    assert frame["error"]["code"] == tools.ERR_UNAUTHENTICATED
    assert "anonymous" in frame["error"]["message"]
    assert frame["error"]["data"] == {"presented": False}


def test_a_wrong_token_is_401(live) -> None:
    running, _token, _gctx = live
    status, frame = _post(running.url, {"jsonrpc": "2.0", "id": 2, "method": "ping"},
                          {"Authorization": "Bearer not-the-token"})
    assert status == 401
    assert frame["error"]["data"] == {"presented": True}


def test_the_right_token_reaches_ping(live) -> None:
    running, token, _gctx = live
    status, frame = _post(running.url, {"jsonrpc": "2.0", "id": 3, "method": "ping"},
                          {"Authorization": f"Bearer {token}"})
    assert status == 200 and frame["result"] == {}


def test_an_unminted_node_refuses_every_request(tmp_path: Path) -> None:
    """Absent auth material is a HARD refusal, never a downgrade to open."""
    import io

    gctx = _ctx(tmp_path)
    with server.GatewayServer(gctx, host=server.DEFAULT_BINDING, port=0,
                              diag=io.StringIO()) as running:
        status, frame = _post(running.url,
                              {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                              {"Authorization": "Bearer anything"})
    assert status == 401
    assert "never minted" in frame["error"]["message"]


def test_the_token_is_stored_as_a_digest_and_never_in_plaintext(tmp_path: Path) -> None:
    root = tmp_path / "node"
    root.mkdir()
    value = tokens.mint(root)
    raw = tokens.record_path(root).read_text(encoding="utf-8")
    assert value not in raw, "the plaintext token reached disk"
    assert tokens.digest_of(value) in raw
    assert tokens.verify(root, value) is True
    assert tokens.verify(root, value + "x") is False
    assert tokens.verify(root, None) is False
    assert tokens.verify(root, "") is False


def test_rotation_locks_out_the_previous_holder(tmp_path: Path) -> None:
    root = tmp_path / "node"
    root.mkdir()
    first = tokens.mint(root)
    second = tokens.mint(root)
    assert first != second
    assert tokens.verify(root, first) is False
    assert tokens.verify(root, second) is True
    assert tokens.load_record(root).rotation == 2


@pytest.mark.parametrize("body,fragment", [
    ('{"schema": "gateway-auth/v1", "algorithm": "sha256"}', "token_sha256"),
    ('{"schema": "other/v1", "algorithm": "sha256", "token_sha256": "a"}', "schema"),
    ('{"schema": "gateway-auth/v1", "algorithm": "md5", "token_sha256": "a"}',
     "algorithm"),
    ("not json at all", "malformed"),
])
def test_a_malformed_token_record_halts_rather_than_defaulting(
        tmp_path: Path, body: str, fragment: str) -> None:
    """A missing load-bearing field HALTs. A default here is a gate going green
    because it stopped looking."""
    root = tmp_path / "node"
    path = tokens.record_path(root)
    path.parent.mkdir(parents=True)
    path.write_text(body, encoding="utf-8")
    with pytest.raises(tokens.AuthUnavailable) as excinfo:
        tokens.verify(root, "anything")
    assert fragment in str(excinfo.value)


# ---------------------------------------------------------------------------
# 2. the backend allowlist -- default-deny, no value meaning "all"
# ---------------------------------------------------------------------------


def test_an_ungranted_harness_reaches_no_backend(live) -> None:
    running, token, _gctx = live
    status, frame = _post(running.url, {
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "demo__read_it"}},
        {"Authorization": f"Bearer {token}", "X-Harness-Id": "stranger"})
    assert status == 200
    result = frame["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["control"] == "backend-allowlist"
    assert "no row" in result["structuredContent"]["error"]


def test_an_undeclared_harness_reaches_no_backend(live) -> None:
    """No header at all is not an identity, and it is certainly not a grant."""
    running, token, _gctx = live
    _status, frame = _post(running.url, {
        "jsonrpc": "2.0", "id": 5, "method": "tools/call",
        "params": {"name": "demo__read_it"}},
        {"Authorization": f"Bearer {token}"})
    assert frame["result"]["isError"] is True
    assert "declared no harness id" in frame["result"]["structuredContent"]["error"]


def test_a_granted_harness_reaches_the_gate_and_stops_at_the_seam(live) -> None:
    running, token, _gctx = live
    _status, frame = _post(running.url, {
        "jsonrpc": "2.0", "id": 6, "method": "tools/call",
        "params": {"name": "demo__read_it"}},
        {"Authorization": f"Bearer {token}", "X-Harness-Id": "known"})
    result = frame["result"]
    # PERMITTED by the gate, and honestly NOT executed: the invoker is a
    # declared seam and the shipped one refuses rather than fabricating.
    assert result["isError"] is True
    assert result["structuredContent"]["control"] == "invoker-seam"
    assert result["structuredContent"]["permitted"] is True
    assert result["structuredContent"]["executed"] is False


def test_an_empty_grant_list_is_a_closed_door(tmp_path: Path) -> None:
    policy = backends_mod.HarnessPolicy(grants={"known": ()}, always_denied=(),
                                        source="<test>")
    permitted, reason = policy.permits("known", "demo")
    assert permitted is False
    assert "deliberate closed door" in reason


def test_always_denied_outranks_a_grant(tmp_path: Path) -> None:
    policy = backends_mod.HarnessPolicy(grants={"known": ("demo",)},
                                        always_denied=("demo",), source="<test>")
    permitted, reason = policy.permits("known", "demo")
    assert permitted is False and "always-denied" in reason


def test_an_absent_backends_key_halts_and_an_empty_list_does_not(tmp_path: Path) -> None:
    """An absent key is a policy nobody finished; an empty list is a decision."""
    cfg = tmp_path / "config"
    cfg.mkdir()
    path = cfg / "gateway-policy.yaml"
    path.write_text(
        "schema: gateway-policy/v1\nharnesses:\n  - id: known\n"
        "    reason: 'test'\n", encoding="utf-8")
    with pytest.raises(backends_mod.PolicyUnavailable) as excinfo:
        backends_mod.load_policy(tmp_path)
    assert "absent key is not an empty list" in str(excinfo.value)

    path.write_text(
        "schema: gateway-policy/v1\nharnesses:\n  - id: known\n"
        "    backends: []\n    reason: 'registered, granted nothing'\n",
        encoding="utf-8")
    policy = backends_mod.load_policy(tmp_path)
    assert policy.grants == {"known": ()}


def test_a_grant_with_no_reason_is_refused(tmp_path: Path) -> None:
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "gateway-policy.yaml").write_text(
        "schema: gateway-policy/v1\nharnesses:\n  - id: known\n"
        "    backends: [demo]\n", encoding="utf-8")
    with pytest.raises(backends_mod.PolicyUnavailable) as excinfo:
        backends_mod.load_policy(tmp_path)
    assert "reason" in str(excinfo.value)


def test_an_unreadable_policy_refuses_every_backend(tmp_path: Path) -> None:
    gctx = tools.GatewayContext(
        node=_node(tmp_path),
        registry=backends_mod.BackendRegistry(backends={"demo": DEMO},
                                              source="<test>"),
        policy=None, policy_error="the policy could not be read")
    with pytest.raises(tools.ToolRefused) as excinfo:
        tools.dispatch_tool(gctx, "demo__read_it", {}, harness="known")
    assert "could not be read" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 3. the ladder -- untagged is T4/deny, T3 is an ASK with a record
# ---------------------------------------------------------------------------


def test_an_untagged_tool_is_t4_and_blocked_never_t2() -> None:
    """The public default-deny ruling. A tool nobody classified is unbounded."""
    verdict = tiers.classify_call(tool="mystery", args={}, declared_tier=None,
                                  backend="demo")
    assert verdict.tier == "T4"
    assert verdict.decision is Decision.BLOCK
    assert verdict.tier != "T2"
    assert "no declared tier" in verdict.reasons[0]


def test_an_undeclared_tool_on_a_granted_backend_is_refused(live) -> None:
    running, token, _gctx = live
    _status, frame = _post(running.url, {
        "jsonrpc": "2.0", "id": 7, "method": "tools/call",
        "params": {"name": "demo__never_declared"}},
        {"Authorization": f"Bearer {token}", "X-Harness-Id": "known"})
    content = frame["result"]["structuredContent"]
    assert frame["result"]["isError"] is True
    assert content["tier"] == "T4"
    assert content["decision"] == "BLOCK"
    assert content["executed"] is False


def test_a_t3_call_asks_and_files_an_approval(live) -> None:
    running, token, gctx = live
    _status, frame = _post(running.url, {
        "jsonrpc": "2.0", "id": 8, "method": "tools/call",
        "params": {"name": "demo__drop_it", "arguments": {"target": "x"}}},
        {"Authorization": f"Bearer {token}", "X-Harness-Id": "known"})
    content = frame["result"]["structuredContent"]
    assert content["decision"] == "ASK"
    assert content["tier"] == "T3"
    assert content["executed"] is False
    assert content["approval_id"], "a T3 ask with no record is an ask nobody sees"

    pending = list((gctx.node_root / ".intentops" / "approvals" / "pending")
                   .glob("*.json"))
    assert len(pending) == 1
    record = json.loads(pending[0].read_text(encoding="utf-8"))
    assert record["tier"] == "T3"
    # Argument KEYS travel; argument VALUES never do.
    assert record["payload"]["argument_keys"] == ["target"]
    assert "x" not in json.dumps(record["payload"])


def test_arguments_can_raise_a_declared_tier_but_never_lower_it() -> None:
    """Name-based tiering is blind to what a call carries. A T2-named tool
    carrying a destructive shell command must not ride its declared tier."""
    raised = tiers.classify_call(
        tool="Bash", args={"command": "rm -rf /var/data"}, declared_tier="T2",
        backend="demo")
    assert raised.tier == "T4"
    assert raised.metadata["elevated_by_arguments"] is True

    kept = tiers.classify_call(tool="read_it", args={}, declared_tier="T2",
                               backend="demo")
    assert kept.tier == "T2" and kept.decision is Decision.ALLOW
    assert kept.metadata["elevated_by_arguments"] is False


def test_a_shell_by_any_other_name_is_still_a_shell() -> None:
    """The 2026-09-06 finding, as a test.

    The arguments reading used to fire only for tools named exactly ``Bash``,
    ``PowerShell`` or ``Shell`` (case-sensitive). A backend tool called
    ``shell`` -- which is what a backend's own local name looks like once the
    namespace is stripped -- carried a force-push at ALLOW T1, while the
    identical arguments under ``Bash`` read ASK T4.
    """
    for name in ("shell", "run_command", "SHELL", "exec_command"):
        pushed = tiers.classify_call(
            tool=name, args={"command": "git push --force origin main"},
            declared_tier="T1", backend="demo")
        assert pushed.tier in ("T3", "T4"), name
        assert pushed.decision is not Decision.ALLOW, name
        assert pushed.metadata["elevated_by_arguments"] is True, name

        wiped = tiers.classify_call(
            tool=name, args={"command": "rm -rf /var/data"},
            declared_tier="T1", backend="demo")
        assert wiped.tier == "T4", name

    # a call carrying no command is untouched by any of it
    quiet = tiers.classify_call(tool="run_command", args={"query": "select 1"},
                                declared_tier="T1", backend="demo")
    assert quiet.tier == "T1" and quiet.decision is Decision.ALLOW


def test_classify_does_not_leak_a_backend_to_an_ungranted_harness(
        tmp_path: Path) -> None:
    """The stated rule, now honoured on this path too.

    ``_dispatch_backend`` checks the allowlist before anything is classified,
    "because a harness with no grant has no business learning what a
    backend's tools are tiered at". ``intentops.classify`` did not, so an
    ungranted harness could read a backend's tiering through it.
    """
    gctx = _ctx(tmp_path, grants={"known": ("demo",)},
                backends={"demo": DEMO})
    with pytest.raises(tools.ToolRefused):
        tools.dispatch_tool(gctx, "intentops.classify",
                            {"tool": "demo__drop_it"}, harness="nobody")

    # the granted harness still gets its answer
    ruled = tools.dispatch_tool(gctx, "intentops.classify",
                                {"tool": "demo__drop_it"}, harness="known")
    assert ruled["tier"] == "T3" and ruled["executed"] is False

    # and a built-in needs no grant: there is nothing to grant
    builtin = tools.dispatch_tool(gctx, "intentops.classify",
                                  {"tool": "intentops.status"},
                                  harness="nobody")
    assert builtin["executed"] is False


def test_include_rule_text_must_be_a_boolean(tmp_path: Path) -> None:
    """`bool("false")` is True: coercion here serves the opposite of the ask."""
    gctx = _ctx(tmp_path, grants={"known": ("demo",)},
                backends={"demo": DEMO}, repo_root=Path.cwd())
    with pytest.raises(tools.ToolRefused) as excinfo:
        tools.dispatch_tool(gctx, "intentops.boot",
                            {"include_rule_text": "false"}, harness="known")
    assert "boolean" in str(excinfo.value)


def test_a_backend_send_verb_reaches_the_outbound_branch() -> None:
    """A backend tool is an MCP tool and must be classified as one.

    ``_verdict_for_call`` hands the core classifier the qualified address
    ``mcp__<backend>__<tool>``. Without it the outbound-communication branch,
    which keys on that prefix, was unreachable for every backend call -- so
    ``mail__send_message`` was no-opinion by construction.
    """
    bare = tiers.classify_call(tool="send_message",
                               args={"to": "someone", "body": "hi"},
                               declared_tier="T0", backend="mail")
    assert bare.tier == "T0"  # no opinion on a bare, unqualified name

    qualified = tiers.classify_call(
        tool="send_message", args={"to": "someone", "body": "hi"},
        declared_tier="T0", backend="mail",
        classifier_name="mcp__mail__send_message")
    assert qualified.tier == "T3"
    assert qualified.decision is not Decision.ALLOW
    assert qualified.metadata["elevated_by_arguments"] is True


def test_a_registry_tool_with_a_bogus_tier_halts(tmp_path: Path) -> None:
    """A typo must not quietly become the untagged default."""
    estate = tmp_path / "estate"
    estate.mkdir()
    (estate / "CAPABILITIES.yaml").write_text(
        "schema: estate-capabilities/v1\npopulation:\n"
        "  - id: demo\n    kind: service\n    as_of: '2026-09-06'\n"
        "    gateway_backend:\n      namespace: demo\n      transport: stdio\n"
        "      tools:\n        - name: read_it\n          tier: T9\n",
        encoding="utf-8")
    with pytest.raises(backends_mod.RegistryUnavailable) as excinfo:
        backends_mod.load_registry(tmp_path)
    assert "not one of" in str(excinfo.value)


def test_split_tool_name_never_invents_a_default_backend() -> None:
    assert tiers.split_tool_name("demo__read_it") == ("demo", "read_it")
    assert tiers.split_tool_name("intentops.status") == (None, "intentops.status")
    # A single underscore is common INSIDE a tool name and must not split.
    assert tiers.split_tool_name("read_it") == (None, "read_it")


# ---------------------------------------------------------------------------
# 4. no backend is reachable at birth
# ---------------------------------------------------------------------------


def test_the_shipped_estate_declares_no_backends() -> None:
    registry = backends_mod.load_registry(REPO_ROOT)
    assert registry.is_empty, (
        "the seed shipped a backend. A newborn node must have none: the "
        "population is GROWN, and a hand-listed one decays toward maximum "
        "disagreement with reality.")


def test_the_shipped_policy_grants_nothing_to_anybody() -> None:
    policy = backends_mod.load_policy(REPO_ROOT)
    assert policy.grants == {}
    permitted, reason = policy.permits("any-harness", "any-backend")
    assert permitted is False and "default-deny" in reason


def test_a_newborn_gateway_serves_only_the_builtin_surface(tmp_path: Path) -> None:
    gctx = tools.GatewayContext(
        node=_node(tmp_path, repo_root=REPO_ROOT),
        registry=backends_mod.load_registry(REPO_ROOT),
        policy=backends_mod.load_policy(REPO_ROOT))
    names = {t["name"] for t in tools.list_tools()}
    assert names == set(tools.BUILTIN_TOOL_NAMES)
    with pytest.raises(tools.ToolRefused):
        tools.dispatch_tool(gctx, "anything__at_all", {}, harness="known")


# ---------------------------------------------------------------------------
# 5. the built-in surface
# ---------------------------------------------------------------------------


def test_every_builtin_is_reachable_over_http(live) -> None:
    running, token, _gctx = live
    auth = {"Authorization": f"Bearer {token}", "X-Harness-Id": "known"}
    _status, frame = _post(running.url,
                           {"jsonrpc": "2.0", "id": 9, "method": "tools/list"},
                           auth)
    assert {t["name"] for t in frame["result"]["tools"]} == set(
        tools.BUILTIN_TOOL_NAMES)
    for name in tools.BUILTIN_TOOL_NAMES:
        args = {"tool": "demo__read_it"} if name == "intentops.classify" else {}
        _status, frame = _post(running.url, {
            "jsonrpc": "2.0", "id": 10, "method": "tools/call",
            "params": {"name": name, "arguments": args}}, auth)
        assert "result" in frame, f"{name} did not answer"
        assert frame["result"].get("isError") is not True, f"{name} errored"


def test_classify_is_a_read_and_runs_nothing(live) -> None:
    running, token, _gctx = live
    _status, frame = _post(running.url, {
        "jsonrpc": "2.0", "id": 11, "method": "tools/call",
        "params": {"name": "intentops.classify",
                   "arguments": {"tool": "demo__drop_it"}}},
        {"Authorization": f"Bearer {token}", "X-Harness-Id": "known"})
    payload = frame["result"]
    assert payload["decision"] == "ASK" and payload["tier"] == "T3"
    assert payload["executed"] is False


def test_status_states_the_gap_it_cannot_close(live) -> None:
    running, token, _gctx = live
    _status, frame = _post(running.url, {
        "jsonrpc": "2.0", "id": 12, "method": "tools/call",
        "params": {"name": "intentops.status"}},
        {"Authorization": f"Bearer {token}", "X-Harness-Id": "known"})
    payload = frame["result"]
    assert payload["limitation"] == tools.S2_HONESTY
    assert payload["controls"]["untagged_tool"].startswith("T4")
    assert payload["token"]["minted"] is True
    assert "token_sha256" not in json.dumps(payload)


def test_an_unknown_tool_is_refused_never_guessed(live) -> None:
    running, token, _gctx = live
    _status, frame = _post(running.url, {
        "jsonrpc": "2.0", "id": 13, "method": "tools/call",
        "params": {"name": "intentops.not_a_tool"}},
        {"Authorization": f"Bearer {token}", "X-Harness-Id": "known"})
    assert frame["result"]["isError"] is True
    assert "CLOSED" in frame["result"]["structuredContent"]["error"]


def test_there_is_no_get_surface(live) -> None:
    running, _token, _gctx = live
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(running.url, timeout=10)
    assert excinfo.value.code == 405


# ---------------------------------------------------------------------------
# 6. the boot image
# ---------------------------------------------------------------------------


def test_the_boot_image_carries_all_four_parts(tmp_path: Path) -> None:
    node = tmp_path / "node"
    (node / ".intentops-rules").mkdir(parents=True)
    (node / ".intentops-rules" / "honesty.md").write_text(
        "# Honesty\nA refusal must stay in the denominator.\n", encoding="utf-8")
    image = boot_mod.boot_image(node, REPO_ROOT)

    assert image["schema"] == "gateway-boot/v1"
    assert image["read_only"] is True
    # imprint: which one, and whether it is signed -- as a FACT, not a pass
    assert image["imprint"]["status"] == "PRESENT"
    assert image["imprint"]["imprint_version"]
    assert image["imprint"]["signed"] is (image["imprint"]["signatures"] > 0)
    # rules: the text, in full
    assert image["rules"]["count"] == 1
    assert "denominator" in image["rules"]["entries"][0]["text"]
    # aliveness: absence is DATA, never an empty pass
    assert image["aliveness"]["status"] == "NEVER-TAKEN"
    # calibration: the honest string, below the bar on a fresh node
    assert image["calibration"]["calibrated"] is False
    assert image["calibration"]["rulings_required"] == 20


def test_an_absent_rules_corpus_reads_as_absent_not_as_no_refusals(
        tmp_path: Path) -> None:
    node = tmp_path / "node"
    node.mkdir()
    image = boot_mod.boot_image(node, REPO_ROOT)
    assert image["rules"]["status"] == "ABSENT"
    assert image["rules"]["count"] == 0
    assert "empty set of refusals" in image["rules"]["reason"]


def test_the_boot_image_writes_nothing(tmp_path: Path) -> None:
    node = tmp_path / "node"
    (node / ".intentops-rules").mkdir(parents=True)
    (node / ".intentops-rules" / "r.md").write_text("x\n", encoding="utf-8")
    before = sorted(p.relative_to(node).as_posix() for p in node.rglob("*"))
    boot_mod.boot_image(node, REPO_ROOT)
    boot_mod.boot_image(node, REPO_ROOT)
    after = sorted(p.relative_to(node).as_posix() for p in node.rglob("*"))
    assert before == after


def test_the_calibration_string_has_one_definition() -> None:
    """Delegated, not recomputed: two surfaces disagreeing about whether a node
    is calibrated would leave an operator no way to tell which was lying."""
    source = (REPO_ROOT / "packages" / "intentops-gateway" / "intentops_gateway"
              / "boot.py").read_text(encoding="utf-8")
    assert "from intentops_saddle_mcp.tools import" in source
    assert "calibration_string" in source


# ---------------------------------------------------------------------------
# 7. stand-down outranks everything, including authentication
# ---------------------------------------------------------------------------


def test_a_stood_down_node_refuses_ahead_of_the_method(tmp_path: Path) -> None:
    gctx = _ctx(tmp_path, halted="the operator switched it off")
    with pytest.raises(tools.RpcError) as excinfo:
        tools.handle_rpc(gctx, "tools/list", {}, harness="known")
    assert excinfo.value.code == tools.ERR_STOOD_DOWN


def test_stand_down_outranks_authentication_on_the_mcp_surface(tmp_path: Path) -> None:
    gctx = _ctx(tmp_path, halted="off")
    frame = mcp.handle_message({"jsonrpc": "2.0", "id": 1, "method": "ping"},
                               session=Session(), gctx=gctx)
    # No token was presented, and the answer is STOOD-DOWN rather than
    # UNAUTHENTICATED: a stood-down node does not authenticate anybody.
    assert frame["error"]["code"] == saddle_protocol.ERR_STOOD_DOWN


# ---------------------------------------------------------------------------
# 8. the MCP surface is the same surface
# ---------------------------------------------------------------------------


def test_the_mcp_surface_reuses_the_saddle_protocol_contract() -> None:
    """Imported, not forked. If these ever diverge, one is a stale copy."""
    assert mcp.PROTOCOL_VERSION is saddle_protocol.PROTOCOL_VERSION
    assert mcp.Session is saddle_protocol.Session
    assert tools.ERR_UNAUTHENTICATED == saddle_protocol.ERR_UNAUTHENTICATED
    assert tools.ERR_STOOD_DOWN == saddle_protocol.ERR_STOOD_DOWN
    source = (REPO_ROOT / "packages" / "intentops-gateway" / "intentops_gateway"
              / "mcp.py").read_text(encoding="utf-8")
    assert "from intentops_saddle_mcp.protocol import" in source


def test_the_mcp_surface_serves_the_same_tools(tmp_path: Path) -> None:
    gctx = _ctx(tmp_path, grants={"known": ("demo",)}, backends={"demo": DEMO})
    token = tokens.mint(gctx.node_root)
    meta = {"_meta": {"authorization": f"Bearer {token}"}}
    session = Session()

    frame = mcp.handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": dict(meta, clientInfo={"name": "known", "version": "1"})},
        session=session, gctx=gctx)
    assert frame["result"]["protocolVersion"] == PROTOCOL_VERSION

    frame = mcp.handle_message(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": dict(meta)},
        session=session, gctx=gctx)
    assert {t["name"] for t in frame["result"]["tools"]} == set(
        tools.BUILTIN_TOOL_NAMES)
    assert mcp.tool_surface_matches_http() is True


def test_the_mcp_stdio_loop_runs_end_to_end(tmp_path: Path) -> None:
    """The transport LOOP, not just the handler.

    A handler that is tested while its loop never is leaves the one line that
    actually reaches a client unexercised -- and a transport that has never
    been driven is indistinguishable from a broken one.
    """
    import io

    gctx = _ctx(tmp_path, grants={"known": ("demo",)}, backends={"demo": DEMO})
    token = tokens.mint(gctx.node_root)
    meta = {"_meta": {"authorization": f"Bearer {token}"}}
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": dict(meta, clientInfo={"name": "known", "version": "1"})},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": dict(meta, name="intentops.status")},
    ]
    stdin = io.StringIO("".join(json.dumps(m) + "\n" for m in lines)
                        + "not json\n")
    stdout, stderr = io.StringIO(), io.StringIO()
    assert mcp.run_stdio(stdin, stdout, stderr, gctx=gctx) == 0

    frames = [json.loads(line) for line in stdout.getvalue().splitlines() if line]
    # initialize, tools/call, and the parse error -- the notification returns
    # no frame, which is the specification's own rule.
    assert len(frames) == 3
    assert frames[0]["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert frames[1]["result"]["structuredContent"]["limitation"] == tools.S2_HONESTY
    assert frames[2]["error"]["code"] == saddle_protocol.ERR_PARSE


def test_the_mcp_surface_refuses_an_unauthenticated_call(tmp_path: Path) -> None:
    gctx = _ctx(tmp_path)
    tokens.mint(gctx.node_root)
    frame = mcp.handle_message({"jsonrpc": "2.0", "id": 1, "method": "ping"},
                               session=Session(), gctx=gctx)
    assert frame["error"]["code"] == saddle_protocol.ERR_UNAUTHENTICATED


def test_the_mcp_surface_uses_the_gateways_own_credential(tmp_path: Path) -> None:
    """Two surfaces that rotate independently must not share one secret."""
    from intentops_saddle_mcp import auth as saddle_auth

    gctx = _ctx(tmp_path)
    saddle_token = saddle_auth.mint(gctx.node_root)
    gateway_token = tokens.mint(gctx.node_root)
    assert saddle_token != gateway_token
    assert tokens.verify(gctx.node_root, saddle_token) is False
    assert saddle_auth.verify(gctx.node_root, gateway_token) is False


# ---------------------------------------------------------------------------
# 9. the ledger
# ---------------------------------------------------------------------------


def test_every_request_lands_in_the_ledger_allowed_and_refused(live) -> None:
    running, token, gctx = live
    auth = {"Authorization": f"Bearer {token}", "X-Harness-Id": "known"}
    _post(running.url, {"jsonrpc": "2.0", "id": 1, "method": "ping"})  # refused
    _post(running.url, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": "intentops.status"}}, auth)
    _post(running.url, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                        "params": {"name": "demo__never_declared"}}, auth)
    kinds = [row.get("kind") for row in ledger.read_events(gctx.node_root)]
    assert "refusal" in kinds, "a door-level refusal left no trace"
    assert "tool" in kinds, "an allowed call left no trace"
    assert "verdict" in kinds, "a gated call left no trace"


def test_the_ledger_never_carries_a_credential_or_an_argument_value(live) -> None:
    running, token, gctx = live
    _post(running.url, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "demo__drop_it",
                                   "arguments": {"secret_field": "swordfish"}}},
          {"Authorization": f"Bearer {token}", "X-Harness-Id": "known"})
    raw = ledger.ledger_path(gctx.node_root).read_text(encoding="utf-8")
    assert token not in raw
    assert "swordfish" not in raw, "an argument VALUE reached the ledger"
    assert "secret_field" in raw, "the argument KEYS should travel"


def test_the_ledger_refuses_a_credential_shaped_field(tmp_path: Path) -> None:
    node = tmp_path / "node"
    node.mkdir()
    with pytest.raises(ledger.LedgerUnavailable):
        ledger.append_event(node, {"kind": "tool", "token": "anything"})


def test_a_malformed_ledger_line_stays_in_the_denominator(tmp_path: Path) -> None:
    """A reader that silently skips a bad line removes it from the count, and
    the number then looks better for exactly the wrong reason."""
    node = tmp_path / "node"
    ledger.append_event(node, {"kind": "tool"})
    with ledger.ledger_path(node).open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    rows = list(ledger.read_events(node))
    assert len(rows) == 2
    assert rows[1]["kind"] == "malformed"


def test_safe_arg_keys_returns_keys_only() -> None:
    assert ledger.safe_arg_keys({"b": 1, "a": "secret"}) == ["a", "b"]
    assert ledger.safe_arg_keys(None) == []


# ---------------------------------------------------------------------------
# 10. layering and the selftest
# ---------------------------------------------------------------------------


def test_the_core_imports_no_gateway() -> None:
    """The same arrow test_dependency_direction.py makes for saddles.

    Written here because that file's pattern matches `intentops_saddle*` only,
    and a gateway import inside the core would pass it while welding the gate
    to one transport.
    """
    core = REPO_ROOT / "packages" / "intentops-core"
    offenders = []
    for path in core.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith(("import intentops_gateway",
                                    "from intentops_gateway")):
                offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{number}")
    assert not offenders, (
        "the core imports the gateway, which welds the gate to one transport: "
        + ", ".join(offenders))


def test_the_core_package_declares_no_gateway_dependency() -> None:
    """The same rule at the packaging layer, where it breaks more quietly.

    Both manifests are checked and a missing one is SKIPPED rather than
    asserted on: the core is currently published from the root manifest and has
    no pyproject of its own, and a test that demanded a file the layout does
    not use would fail for a reason that has nothing to do with the arrow.
    """
    checked = 0
    for manifest in (REPO_ROOT / "pyproject.toml",
                     REPO_ROOT / "packages" / "intentops-core" / "pyproject.toml"):
        if not manifest.is_file():
            continue
        checked += 1
        for line in manifest.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert "intentops-gateway" not in stripped, (
                f"{manifest.name} declares a dependency on the gateway: "
                f"{stripped!r}. Installing the core must never pull a "
                "transport in with it.")
    assert checked, "no manifest was found; the packaging check is vacuous"


def test_the_gateway_selftest_fires_every_refusal_path() -> None:
    ok, report = server.selftest()
    assert ok, report
    assert report.count("ok  ") >= 5


def test_the_port_default_comes_from_the_registry() -> None:
    port, source = server.resolve_port(REPO_ROOT)
    assert port == server.DEFAULT_PORT
    assert source.endswith("ports.yaml")


def test_an_unreadable_port_registry_falls_back_and_says_so(tmp_path: Path) -> None:
    port, source = server.resolve_port(tmp_path)
    assert port == server.DEFAULT_PORT
    assert "built-in default" in source, (
        "a surprising port must be traceable to the rule that produced it")


def test_the_gateway_organs_are_declared_at_birth() -> None:
    from intentops_core.genesis.organs import BIRTH_ORGANS

    by_id = {organ.id: organ for organ in BIRTH_ORGANS}
    requests = by_id["gateway-requests"]
    assert requests.write_model == "append-only-jsonl"
    assert requests.relpath == ledger.LEDGER_RELPATH.as_posix()
    token_organ = by_id["gateway-token"]
    assert token_organ.present_at_birth is False, (
        "a token minted silently at birth is a credential nobody was shown")
    assert token_organ.relpath == tokens.RECORD_RELPATH.as_posix()
