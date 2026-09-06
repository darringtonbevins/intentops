"""The hosted MCP saddle: protocol, the two controls, the tools, and S6.

PURPOSE
    Drive the real modules against temporary node roots. No live service, no
    network, no key material committed: every token is generated inside a temp
    directory at test time and dies with it.

    The suite is organised around the failures that matter, not around the code
    that exists: an unauthenticated call, an unlisted client, a stood-down
    node, and an artifact offered where a descriptor was asked for. A test that
    only proves the happy path proves that the gate is present, never that it
    is armed.

BLIND SPOTS
    * These tests prove this SERVER refuses. They cannot prove a host honours
      a refusal -- that is the S2 gap, it is not observable from here, and it
      is why the registry row is graded `candidate`.
    * ``intentops.council`` and ``intentops.imprint_verify`` are exercised
      against this repository's own checkout. A different checkout with a
      different core-surface declaration would exercise different branches.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_PKG = REPO_ROOT / "packages" / "intentops-saddle-mcp"
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

from intentops_core.gate import RealityIndicators  # noqa: E402

from intentops_saddle_mcp import OPERATIONS  # noqa: E402
from intentops_saddle_mcp.auth import (  # noqa: E402
    AuthUnavailable, digest_of, load_record, mint, record_path, verify,
)
from intentops_saddle_mcp.discover import PayloadRefused, describe_host  # noqa: E402
from intentops_saddle_mcp.ledger import ledger_path  # noqa: E402
from intentops_saddle_mcp.node import NodeContext  # noqa: E402
from intentops_saddle_mcp.protocol import (  # noqa: E402
    ERR_ALLOWLIST, ERR_STOOD_DOWN, ERR_UNAUTHENTICATED, PROTOCOL_VERSION,
    Session, handle_message,
)
from intentops_saddle_mcp.registry import (  # noqa: E402
    RegistryUnavailable, SaddleRow, load_row,
)
from intentops_saddle_mcp.server import selftest  # noqa: E402
from intentops_saddle_mcp.tools import calibration_string  # noqa: E402

ALLOWED = SaddleRow(host_id="mcp-hosted", grade="candidate",
                    allowed_clients=("known-client",), source="<test>")
CLOSED = SaddleRow(host_id="mcp-hosted", grade="candidate",
                   allowed_clients=(), source="<test>")


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def node(tmp_path: Path) -> Path:
    root = tmp_path / "node"
    (root / ".intentops").mkdir(parents=True)
    return root


@pytest.fixture()
def token(node: Path) -> str:
    return mint(node)


def ctx_for(node: Path, *, repo_root: Optional[Path] = None,
            halted: Optional[str] = None) -> NodeContext:
    return NodeContext(node_root=node, repo_root=repo_root, root_source="<test>",
                       indicators=RealityIndicators(), estate_map_present=False,
                       halted=halted)


def msg(method: str, *, token: Optional[str] = None, mid: Any = 1,
        **params: Any) -> Dict[str, Any]:
    body: Dict[str, Any] = dict(params)
    if token is not None:
        body["_meta"] = {"authorization": f"Bearer {token}"}
    return {"jsonrpc": "2.0", "id": mid, "method": method, "params": body}


def handshake(node: Path, token: str, *, repo_root: Optional[Path] = None,
              row: SaddleRow = ALLOWED) -> tuple:
    session = Session()
    ctx = ctx_for(node, repo_root=repo_root)
    frame = handle_message(
        msg("initialize", token=token,
            clientInfo={"name": "known-client", "version": "1.0"},
            protocolVersion=PROTOCOL_VERSION),
        session=session, ctx=ctx, row=row)
    assert "result" in frame, frame
    return session, ctx


def call(session: Session, ctx: NodeContext, token: str, name: str,
         arguments: Optional[Dict[str, Any]] = None,
         row: SaddleRow = ALLOWED) -> Dict[str, Any]:
    frame = handle_message(
        msg("tools/call", token=token, name=name, arguments=arguments or {}),
        session=session, ctx=ctx, row=row)
    assert frame is not None
    return frame


# ---------------------------------------------------------------------------
# the protocol handshake
# ---------------------------------------------------------------------------


def test_initialize_negotiates_and_declares_only_tools(node: Path, token: str) -> None:
    session, _ = handshake(node, token)
    assert session.initialized and session.client_name == "known-client"


def test_initialize_result_shape(node: Path, token: str) -> None:
    frame = handle_message(
        msg("initialize", token=token,
            clientInfo={"name": "known-client", "version": "1.0"}),
        session=Session(), ctx=ctx_for(node), row=ALLOWED)
    result = frame["result"]
    assert result["protocolVersion"] == PROTOCOL_VERSION
    assert result["serverInfo"]["name"] == "intentops-saddle-mcp"
    # Only tools. Every capability not declared is one that cannot be asked
    # for, which is the cheapest bound on a blast radius there is.
    assert set(result["capabilities"]) == {"tools"}


def test_a_method_before_initialize_is_refused(node: Path, token: str) -> None:
    frame = handle_message(msg("tools/list", token=token),
                           session=Session(), ctx=ctx_for(node), row=ALLOWED)
    assert frame["error"]["code"] == -32004


def test_ping_and_tools_list_after_handshake(node: Path, token: str) -> None:
    session, ctx = handshake(node, token)
    assert handle_message(msg("ping", token=token), session=session, ctx=ctx,
                          row=ALLOWED)["result"] == {}
    tools = handle_message(msg("tools/list", token=token), session=session,
                           ctx=ctx, row=ALLOWED)["result"]["tools"]
    assert {t["name"] for t in tools} == {
        "intentops.classify", "intentops.gate", "intentops.status",
        "intentops.council", "intentops.imprint_verify", "intentops.discover",
    }
    for spec in tools:
        assert spec["inputSchema"]["type"] == "object"


def test_a_notification_returns_no_frame(node: Path, token: str) -> None:
    session, ctx = handshake(node, token)
    out = handle_message({"jsonrpc": "2.0", "method": "notifications/initialized",
                          "params": {"_meta": {"authorization": f"Bearer {token}"}}},
                         session=session, ctx=ctx, row=ALLOWED)
    assert out is None


def test_an_undeclared_method_is_refused_not_guessed(node: Path, token: str) -> None:
    session, ctx = handshake(node, token)
    frame = handle_message(msg("resources/list", token=token), session=session,
                           ctx=ctx, row=ALLOWED)
    assert frame["error"]["code"] == -32601


# ---------------------------------------------------------------------------
# control 1: authentication, on by default, no anonymous fall-through
# ---------------------------------------------------------------------------


def test_a_request_with_no_token_is_refused(node: Path, token: str) -> None:
    frame = handle_message({"jsonrpc": "2.0", "id": 1, "method": "ping"},
                           session=Session(), ctx=ctx_for(node), row=ALLOWED)
    assert frame["error"]["code"] == ERR_UNAUTHENTICATED
    assert frame["error"]["data"]["presented"] is False


def test_a_wrong_token_is_refused(node: Path, token: str) -> None:
    frame = handle_message(msg("ping", token="not-the-token"),
                           session=Session(), ctx=ctx_for(node), row=ALLOWED)
    assert frame["error"]["code"] == ERR_UNAUTHENTICATED


def test_an_unminted_node_refuses_everything_rather_than_resolving_anonymous(
        node: Path) -> None:
    """The upstream defect this replaces resolved an absent identity to 'anonymous'."""
    frame = handle_message(msg("initialize", token="anything",
                               clientInfo={"name": "known-client"}),
                           session=Session(), ctx=ctx_for(node), row=ALLOWED)
    assert frame["error"]["code"] == ERR_UNAUTHENTICATED
    assert "never minted" in frame["error"]["message"]


def test_authentication_is_checked_on_every_request_not_only_the_handshake(
        node: Path, token: str) -> None:
    session, ctx = handshake(node, token)
    frame = handle_message(msg("tools/list"), session=session, ctx=ctx, row=ALLOWED)
    assert frame["error"]["code"] == ERR_UNAUTHENTICATED


def test_the_plaintext_token_is_never_written_to_disk(node: Path) -> None:
    value = mint(node)
    body = record_path(node).read_text(encoding="utf-8")
    assert value not in body
    assert digest_of(value) in body
    assert load_record(node).token_sha256 == digest_of(value)
    assert verify(node, value) is True
    assert verify(node, value + "x") is False
    assert verify(node, None) is False


def test_a_malformed_auth_record_refuses_rather_than_defaults(node: Path) -> None:
    mint(node)
    path = record_path(node)
    doc = json.loads(path.read_text(encoding="utf-8"))
    del doc["token_sha256"]
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(AuthUnavailable) as exc:
        load_record(node)
    assert "load-bearing" in str(exc.value)


def test_a_foreign_schema_is_refused(node: Path) -> None:
    mint(node)
    path = record_path(node)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["schema"] = "something-else/v9"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(AuthUnavailable):
        load_record(node)


# ---------------------------------------------------------------------------
# control 2: the client allowlist, default-deny
# ---------------------------------------------------------------------------


def test_an_empty_allowlist_refuses_every_client(node: Path, token: str) -> None:
    frame = handle_message(msg("initialize", token=token,
                               clientInfo={"name": "known-client"}),
                           session=Session(), ctx=ctx_for(node), row=CLOSED)
    assert frame["error"]["code"] == ERR_ALLOWLIST
    assert "empty" in frame["error"]["message"]


def test_an_unlisted_client_is_refused_at_the_handshake(node: Path, token: str) -> None:
    frame = handle_message(msg("initialize", token=token,
                               clientInfo={"name": "stranger"}),
                           session=Session(), ctx=ctx_for(node), row=ALLOWED)
    assert frame["error"]["code"] == ERR_ALLOWLIST


def test_an_unnamed_client_is_refused(node: Path, token: str) -> None:
    frame = handle_message(msg("initialize", token=token, clientInfo={}),
                           session=Session(), ctx=ctx_for(node), row=ALLOWED)
    assert frame["error"]["code"] == ERR_ALLOWLIST


def test_an_unreadable_registry_refuses_everything(node: Path, token: str) -> None:
    frame = handle_message(msg("ping", token=token), session=Session(),
                           ctx=ctx_for(node), row=None,
                           row_error="registry could not be read")
    assert frame["error"]["code"] == ERR_ALLOWLIST


def test_the_shipped_row_is_default_deny() -> None:
    row = load_row(REPO_ROOT)
    assert row.grade == "candidate"
    assert row.refuses_everything, (
        "the shipped mcp-hosted allowlist must be empty: a seed that ships a "
        "pre-approved client is a control nobody armed"
    )
    permitted, reason = row.permits("anything")
    assert not permitted and "empty" in reason


def test_an_absent_allowed_clients_key_halts(tmp_path: Path) -> None:
    """An absent key is not an empty list -- an unfinished registry must not
    look like an armed control."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "saddles.yaml").write_text(
        "hosts:\n  - id: mcp-hosted\n    grade: candidate\n", encoding="utf-8")
    with pytest.raises(RegistryUnavailable) as exc:
        load_row(tmp_path)
    assert "not an empty list" in str(exc.value)


def test_a_null_allowed_clients_halts(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "saddles.yaml").write_text(
        "hosts:\n  - id: mcp-hosted\n    grade: candidate\n    allowed_clients:\n",
        encoding="utf-8")
    with pytest.raises(RegistryUnavailable):
        load_row(tmp_path)


def test_a_missing_repo_root_refuses_rather_than_permits() -> None:
    with pytest.raises(RegistryUnavailable) as exc:
        load_row(None)
    assert "refused" in str(exc.value)


# ---------------------------------------------------------------------------
# S5: stand-down outranks everything, including authentication
# ---------------------------------------------------------------------------


def test_a_stood_down_node_refuses_before_it_authenticates(node: Path) -> None:
    ctx = ctx_for(node, halted="operator stood this node down")
    frame = handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                           session=Session(), ctx=ctx, row=ALLOWED)
    assert frame["error"]["code"] == ERR_STOOD_DOWN


def test_the_gate_tool_returns_halt_on_a_stood_down_node(node: Path, token: str) -> None:
    session, _ = handshake(node, token)
    halted = ctx_for(node, halted="stood down")
    payload = call(session, halted, token, "intentops.gate",
                   {"tool": "Read", "tool_input": {"file_path": "x.txt"}})
    # HALT is delivered at the protocol layer before the tool is reached.
    assert payload["error"]["code"] == ERR_STOOD_DOWN


# ---------------------------------------------------------------------------
# the tools
# ---------------------------------------------------------------------------


def test_classify_is_the_cores_own_answer(node: Path, token: str) -> None:
    session, ctx = handshake(node, token)
    frame = call(session, ctx, token, "intentops.classify",
                 {"tool": "Bash", "tool_input": {"command": "git push origin main"}})
    body = frame["result"]["structuredContent"]
    assert body["operation"] == "S1"
    assert body["classified"] is True
    assert body["tier"] in ("T3", "T4")
    assert body["reason"]


def test_classify_keys_off_the_operation_never_file_content(
        node: Path, token: str) -> None:
    """A workspace write whose BODY mentions a delete is not the delete."""
    session, ctx = handshake(node, token)
    frame = call(session, ctx, token, "intentops.classify",
                 {"tool": "Write", "tool_input": {
                     "file_path": "docs/notes.md",
                     "content": "rm -rf / and git push origin main"}})
    body = frame["result"]["structuredContent"]
    assert body["tier"] == "T0" and body["classified"] is False


def test_gate_returns_a_full_verdict_and_records_it(node: Path, token: str) -> None:
    session, ctx = handshake(node, token)
    frame = call(session, ctx, token, "intentops.gate",
                 {"tool": "Bash", "tool_input": {"command": "rm -rf /var/data"}})
    body = frame["result"]["structuredContent"]
    assert body["verdict"]["decision"] in ("ASK", "BLOCK")
    assert body["verdict"]["reasons"]
    assert body["host_honoured"] == "unknown", (
        "S2 must be stated honestly on every answer: this server cannot refuse "
        "a call the host never routes through it"
    )
    lines = ledger_path(node).read_text(encoding="utf-8").strip().splitlines()
    kinds = [json.loads(x)["kind"] for x in lines]
    assert "gate" in kinds


def test_the_ledger_records_the_permits_too(node: Path, token: str) -> None:
    """A log of refusals alone answers what was stopped and nothing about what
    was allowed, and the second question is the one an audit asks."""
    session, ctx = handshake(node, token)
    call(session, ctx, token, "intentops.gate",
         {"tool": "Read", "tool_input": {"file_path": "README.md"}})
    rows = [json.loads(x) for x in
            ledger_path(node).read_text(encoding="utf-8").strip().splitlines()]
    gates = [r for r in rows if r["kind"] == "gate"]
    assert gates and gates[-1]["decision"] == "ALLOW"


def test_the_ledger_records_a_refusal_at_the_door_without_the_token(
        node: Path, token: str) -> None:
    handle_message(msg("ping", token="wrong"), session=Session(),
                   ctx=ctx_for(node), row=ALLOWED)
    rows = [json.loads(x) for x in
            ledger_path(node).read_text(encoding="utf-8").strip().splitlines()]
    refusals = [r for r in rows if r["kind"] == "refusal"]
    assert refusals and refusals[-1]["why"] == "unauthenticated"
    # A presented credential is never logged, not even truncated.
    assert all("wrong" not in json.dumps(r) for r in rows)


def test_status_carries_the_calibration_string_and_the_s2_gap(
        node: Path, token: str) -> None:
    session, ctx = handshake(node, token)
    body = call(session, ctx, token, "intentops.status")["result"]["structuredContent"]
    assert body["calibration"]["string"] == "uncalibrated: 0 of 20"
    assert body["calibration"]["calibrated"] is False
    assert body["saddle"]["s2"] == "S2: host-honoured=unknown"
    assert body["saddle"]["grade"] == "candidate"
    assert body["alive"]["verdict"] == "UNPROBEABLE"  # no repo root in this ctx


def test_calibration_counts_only_hits_and_a_partial_is_a_miss(node: Path) -> None:
    journal = node / ".intentops" / "twin" / "calibration-grades.jsonl"
    journal.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"grade": "hit"}] * 19 + [{"grade": "partial"}]
    journal.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    reading = calibration_string(node)
    assert reading["rulings_graded"] == 20 and reading["hits"] == 19
    assert reading["calibrated"] is True  # 19/20 = 95% clears the 80% bar
    rows[0] = {"grade": "miss"}


def test_calibration_below_the_bar_reads_uncalibrated(node: Path) -> None:
    journal = node / ".intentops" / "twin" / "calibration-grades.jsonl"
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text(json.dumps({"grade": "hit"}) + "\n", encoding="utf-8")
    assert calibration_string(node)["string"] == "uncalibrated: 1 of 20"


def test_status_against_the_real_checkout_reports_an_aliveness_verdict(
        node: Path, token: str) -> None:
    session, ctx = handshake(node, token, repo_root=REPO_ROOT)
    body = call(session, ctx, token, "intentops.status")["result"]["structuredContent"]
    assert body["alive"]["verdict"] in (
        "ALIVE", "ALIVE-DEGRADED", "NOT-ALIVE", "STOOD-DOWN", "UNPROBEABLE")


def test_council_reads_the_real_core_surface(node: Path, token: str) -> None:
    session, ctx = handshake(node, token, repo_root=REPO_ROOT)
    body = call(session, ctx, token, "intentops.council",
                {"tool": "Write",
                 "tool_input": {"file_path": "docs/not-a-core-surface.md"},
                 "content": "prose"})["result"]["structuredContent"]
    assert body["applies"] is False
    assert "not an approval" in body["note"]


def test_council_without_a_checkout_refuses_rather_than_says_not_applicable(
        node: Path, token: str) -> None:
    session, ctx = handshake(node, token)
    frame = call(session, ctx, token, "intentops.council", {"tool": "Write"})
    assert frame["result"]["isError"] is True
    assert "could not be located" in frame["result"]["structuredContent"]["error"]


def test_imprint_verify_without_a_checkout_is_unprobeable_never_a_pass(
        node: Path, token: str) -> None:
    session, ctx = handshake(node, token)
    body = call(session, ctx, token,
                "intentops.imprint_verify")["result"]["structuredContent"]
    assert body["outcome"] == "UNPROBEABLE" and body["verified"] is False


def test_imprint_verify_runs_against_the_real_checkout(node: Path, token: str) -> None:
    session, ctx = handshake(node, token, repo_root=REPO_ROOT)
    body = call(session, ctx, token,
                "intentops.imprint_verify")["result"]["structuredContent"]
    assert body["outcome"] in ("VERIFIED", "NOT-VERIFIED")
    assert isinstance(body["record"], dict)


def test_an_unknown_tool_is_refused_and_the_surface_does_not_grow(
        node: Path, token: str) -> None:
    session, ctx = handshake(node, token)
    frame = call(session, ctx, token, "intentops.install_skill", {})
    assert frame["result"]["isError"] is True
    assert "does not grow at runtime" in frame["result"]["structuredContent"]["error"]


def test_a_tool_error_is_a_result_not_a_transport_fault(
        node: Path, token: str) -> None:
    """A governance answer must reach the client, not be hidden as -32603."""
    session, ctx = handshake(node, token)
    frame = call(session, ctx, token, "intentops.classify", {})
    assert "result" in frame and frame["result"]["isError"] is True


# ---------------------------------------------------------------------------
# S6: descriptors in, artifacts refused
# ---------------------------------------------------------------------------


def test_discover_returns_typed_descriptors(node: Path, token: str) -> None:
    session, ctx = handshake(node, token)
    body = call(session, ctx, token, "intentops.discover", {"declared": {
        "context_ceiling_tokens": 1_000_000,
        "supports_subagents": True,
        "supports_prefix_cache": True,
        "cache_ttl_seconds": 3600,
        "max_parallel_tool_calls": 8,
        "refusal_support": "honoured",
        "cache_scope": "prefix",
    }})["result"]["structuredContent"]["descriptor"]
    assert body["kind"] == "host-capability-descriptor"
    assert body["context_ceiling_tokens"] == 1_000_000
    assert body["supports_subagents"] is True
    assert body["can_refuse_a_call"] is True
    assert body["evidence"] == "declared", (
        "a declaration is not a measurement, and the descriptor must say so"
    )
    assert body["client_name"] == "known-client"
    assert body["protocol_version"] == PROTOCOL_VERSION


@pytest.mark.parametrize("declared,why", [
    ({"skill_body": "print('hi')"}, "key names an artifact"),
    ({"prompt_template": "you are a helpful agent"}, "key names an artifact"),
    ({"tool_definition": "{}"}, "key names an artifact"),
    ({"agent_card": "x"}, "key names an artifact"),
    ({"startup_script": "echo"}, "key names an artifact"),
    ({"context_ceiling_tokens": "#!/bin/sh\necho hi"}, "shebang value"),
    ({"cache_scope": "```python\nimport os\n```"}, "code fence value"),
    ({"refusal_support": "data:text/plain;base64,aGVsbG8="}, "data url value"),
    ({"cache_scope": "x" * 200}, "oversized value"),
    ({"supports_subagents": {"nested": {"deep": True}}}, "nested structure"),
    ({"extras": ["a", "b"]}, "collection value"),
])
def test_discover_refuses_every_executable_shape(declared: Dict[str, Any],
                                                 why: str) -> None:
    with pytest.raises(PayloadRefused) as exc:
        describe_host(declared)
    assert exc.value.findings, f"refused for {why} but named no finding"


def test_discover_refuses_a_payload_through_the_tool_surface_too(
        node: Path, token: str) -> None:
    session, ctx = handshake(node, token)
    frame = call(session, ctx, token, "intentops.discover",
                 {"declared": {"skill": "#!/bin/sh\nrm -rf /"}})
    assert frame["result"]["isError"] is True
    body = frame["result"]["structuredContent"]["error"]
    assert "never things from it" in body


def test_discover_names_what_it_did_not_understand(node: Path) -> None:
    """A refusal stays in the denominator: unknown keys are reported, not dropped."""
    caps = describe_host({"some_future_flag": True, "supports_workflows": False})
    assert caps.undeclared == ("some_future_flag",)
    assert caps.supports_workflows is False


def test_an_out_of_range_int_is_unknown_never_clamped() -> None:
    """A clamped value is a measurement nobody took wearing a plausible number."""
    caps = describe_host({"context_ceiling_tokens": -5, "cache_ttl_seconds": 10**9})
    assert caps.context_ceiling_tokens is None
    assert caps.cache_ttl_seconds is None


def test_a_value_outside_a_closed_enum_is_unknown() -> None:
    caps = describe_host({"refusal_support": "sometimes"})
    assert caps.refusal_support == "unknown"
    assert caps.to_dict()["can_refuse_a_call"] is False


def test_a_bare_true_is_not_an_integer() -> None:
    caps = describe_host({"max_parallel_tool_calls": True})
    assert caps.max_parallel_tool_calls is None


# ---------------------------------------------------------------------------
# the contract claim and the selftest
# ---------------------------------------------------------------------------


def test_the_saddle_claims_exactly_the_five_operations() -> None:
    assert set(OPERATIONS) == {"S1", "S2", "S3", "S4", "S5"}
    assert all(callable(fn) for fn in OPERATIONS.values())


def test_s4_is_declined_in_the_open_never_faked() -> None:
    answer = OPERATIONS["S4"]()
    assert answer["satisfied"] is False and answer["reason"]


def test_the_selftest_fires_every_refusal_path() -> None:
    ok, report = selftest()
    assert ok, report
    assert "FAIL" not in report
