"""Tests for the inference package: the fence, the adapter, and the accounting.

The shape below is the house one: take a good pool row as the baseline, break
exactly ONE thing, and assert the loader halts for THAT reason rather than by
accident. A test that only proved "something went wrong" would pass against a
loader that halts on everything.

NO LIVE SERVICE. The one test that exercises real HTTP stands an in-process
``http.server`` on ``127.0.0.1`` port 0 (an ephemeral port the OS picks), talks
to it over the loopback interface, and shuts it down in the fixture. Nothing
here resolves a name, reaches a registry, or contacts anything outside this
process's own socket.
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "packages" / "intentops-core"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from intentops_core.inference import openai_compat as http_mod  # noqa: E402
from intentops_core.inference import provider as provider_mod  # noqa: E402
from intentops_core.inference.openai_compat import (  # noqa: E402
    OpenAICompatHTTPProvider,
    provider_for_pool,
    select_provider,
)
from intentops_core.inference.provider import (  # noqa: E402
    Completion,
    DATA_FENCES,
    InferenceError,
    NullProvider,
    classify_endpoint,
    default_provider,
    estimate_tokens,
    load_pools,
    parse_pool,
)


# ---------------------------------------------------------------------------
# fixtures and helpers
# ---------------------------------------------------------------------------


def good_entry(**overrides: Any) -> Dict[str, Any]:
    """A model-pool row that parses. Every test breaks exactly one field."""
    entry: Dict[str, Any] = {
        "id": "local-inference",
        "kind": "model-pool",
        "limit_shape": "none-observed; bounded by local hardware",
        "telemetry_source": "the local runtime's own request log",
        "max_concurrent": 1,
        "kill_switch": "stop the local runtime process",
        "data_fence": "local_only",
        "as_of": "2026-09-06",
        "endpoint": "http://127.0.0.1:9/v1",
        "model": "a-local-model",
        "max_tokens": 128,
        "timeout_s": 5,
        "enabled": False,
    }
    entry.update(overrides)
    return entry


ANSWER = {"model": "a-local-model",
          "choices": [{"message": {"role": "assistant", "content": "an answer"},
                       "finish_reason": "stop"}],
          "usage": {"prompt_tokens": 20, "completion_tokens": 5}}


class _Handler(BaseHTTPRequestHandler):
    """Answers one shape, records what it was asked. Loopback only."""

    payload: Dict[str, Any] = ANSWER
    status: int = 200
    seen: List[Dict[str, Any]] = []

    def do_POST(self) -> None:  # noqa: N802 - the stdlib's own naming
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        try:
            type(self).seen.append(json.loads(body))
        except Exception:  # noqa: BLE001 - a malformed ask is still recorded
            type(self).seen.append({"unparsed": body.decode("utf-8", "replace")})
        raw = json.dumps(type(self).payload).encode("utf-8")
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args: Any) -> None:  # keep the test output clean
        return


@pytest.fixture()
def fake_endpoint() -> Iterator[Tuple[str, type]]:
    """An in-process OpenAI-compatible endpoint on an ephemeral loopback port."""
    _Handler.payload = dict(ANSWER)
    _Handler.status = 200
    _Handler.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1", _Handler
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def refusing_opener(*_a: Any, **_k: Any) -> Any:
    """Any use of this is a test failure: the code under test must not call."""
    raise AssertionError("the code under test opened a socket when it must not")


# ---------------------------------------------------------------------------
# the endpoint fence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url,expected", [
    ("http://127.0.0.1:9/v1", "loopback"),
    ("http://localhost:9/v1", "loopback"),
    ("http://[::1]:9/v1", "loopback"),
    ("http://10.0.0.4:9/v1", "private"),
    ("http://172.16.0.4:9/v1", "private"),
    ("http://192.168.1.4:9/v1", "private"),
    ("http://198.51.100.7:9/v1", "public"),
    ("http://8.8.8.8/v1", "public"),
    ("http://a-runtime-host:9/v1", "unresolved"),
])
def test_classify_endpoint_reads_the_url_and_never_resolves(url: str,
                                                            expected: str) -> None:
    assert classify_endpoint(url)[0] == expected


def test_documentation_ranges_are_public_not_private() -> None:
    """Narrower than ``ipaddress.is_private`` on purpose.

    Python calls 198.51.100.0/24 private because it is not globally reachable.
    It is not an operator's own segment, and a fence that treated it as one
    would pass an address nobody administers.
    """
    import ipaddress

    assert ipaddress.ip_address("198.51.100.7").is_private is True
    assert classify_endpoint("http://198.51.100.7:9/v1")[0] == "public"


def test_a_public_endpoint_is_refused() -> None:
    with pytest.raises(InferenceError) as exc:
        parse_pool(good_entry(endpoint="http://198.51.100.7:9/v1",
                              data_fence="third_party"))
    assert "allow_remote" in exc.value.remedy


def test_a_bare_hostname_is_refused_unresolved_not_looked_up() -> None:
    with pytest.raises(InferenceError) as exc:
        parse_pool(good_entry(endpoint="http://a-runtime-host:9/v1",
                              data_fence="in_family"))
    assert "unresolved" in exc.value.reason


def test_allow_remote_needs_a_stated_reason() -> None:
    with pytest.raises(InferenceError) as exc:
        parse_pool(good_entry(endpoint="http://198.51.100.7:9/v1",
                              data_fence="third_party", allow_remote=True))
    assert "reason" in exc.value.reason or "reason" in exc.value.remedy


def test_allow_remote_with_a_reason_is_accepted_and_stays_marked() -> None:
    pool = parse_pool(good_entry(
        endpoint="http://198.51.100.7:9/v1", data_fence="third_party",
        allow_remote=True,
        allow_remote_reason="the operator administers that host, 2026-09-06"))
    assert pool.endpoint_class == "public"
    assert pool.allow_remote is True
    assert pool.is_local is False


def test_a_non_loopback_pool_may_not_claim_local_only() -> None:
    with pytest.raises(InferenceError) as exc:
        parse_pool(good_entry(endpoint="http://10.0.0.4:9/v1",
                              data_fence="local_only"))
    assert "local_only" in exc.value.reason


@pytest.mark.parametrize("bad", ["ftp://127.0.0.1/v1", "127.0.0.1:9", ""])
def test_an_endpoint_that_is_not_an_http_url_is_refused(bad: str) -> None:
    with pytest.raises(InferenceError):
        classify_endpoint(bad)


# ---------------------------------------------------------------------------
# the pool row: a missing load-bearing field HALTs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["endpoint", "model", "max_tokens",
                                   "timeout_s", "enabled"])
def test_a_missing_load_bearing_field_halts(field: str) -> None:
    entry = good_entry()
    entry.pop(field)
    with pytest.raises(InferenceError) as exc:
        parse_pool(entry)
    assert field in exc.value.reason


@pytest.mark.parametrize("field", ["endpoint", "model", "max_tokens",
                                   "timeout_s", "enabled", "max_concurrent"])
def test_a_null_load_bearing_field_halts(field: str) -> None:
    with pytest.raises(InferenceError):
        parse_pool(good_entry(**{field: None}))


def test_an_undeclared_field_halts_rather_than_being_ignored() -> None:
    with pytest.raises(InferenceError) as exc:
        parse_pool(good_entry(endpoint_url="http://127.0.0.1:9/v1"))
    assert "endpoint_url" in exc.value.reason


def test_enabled_as_a_string_is_refused_because_false_is_truthy() -> None:
    with pytest.raises(InferenceError) as exc:
        parse_pool(good_entry(enabled="false"))
    assert "boolean" in exc.value.reason


def test_a_free_text_data_fence_is_refused_here() -> None:
    with pytest.raises(InferenceError) as exc:
        parse_pool(good_entry(data_fence="local only, nothing leaves"))
    for name in DATA_FENCES:
        assert name in exc.value.remedy


@pytest.mark.parametrize("value", [0, -1, "128", 12.5, True])
def test_a_max_tokens_that_is_not_a_positive_integer_is_refused(value: Any) -> None:
    with pytest.raises(InferenceError):
        parse_pool(good_entry(max_tokens=value))


def test_a_pool_of_another_kind_is_not_parsed_as_one() -> None:
    with pytest.raises(InferenceError):
        parse_pool(good_entry(kind="compute"))


# ---------------------------------------------------------------------------
# loading from an estate
# ---------------------------------------------------------------------------


def _write_estate(root: Path, entries: List[Dict[str, Any]]) -> Path:
    estate = root / "estate"
    estate.mkdir(parents=True, exist_ok=True)
    (estate / "RESOURCES.yaml").write_text(
        yaml.safe_dump({"schema": "estate-resources/v1",
                        "as_of": "2026-09-06", "entries": entries},
                       default_flow_style=False, sort_keys=False),
        encoding="utf-8")
    return estate


def test_the_shipped_estate_declares_no_pool_and_that_is_a_clean_load() -> None:
    """A node with no pool is a NODE WITH NO POOL, not an error."""
    assert load_pools(REPO_ROOT / "estate") == ()


def test_rows_of_other_kinds_are_skipped_not_coerced(tmp_path: Path) -> None:
    estate = _write_estate(tmp_path, [
        {"id": "a-budget", "kind": "budget", "limit_shape": "monthly",
         "telemetry_source": "the ledger", "max_concurrent": 1,
         "kill_switch": "stop drawing", "data_fence": "n/a",
         "as_of": "2026-09-06"},
        good_entry(),
    ])
    pools = load_pools(estate)
    assert [p.id for p in pools] == ["local-inference"]


def test_a_missing_entries_key_is_not_the_same_fact_as_an_empty_list(
        tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    estate.mkdir()
    (estate / "RESOURCES.yaml").write_text(
        "schema: estate-resources/v1\nas_of: \"2026-09-06\"\n", encoding="utf-8")
    with pytest.raises(InferenceError) as exc:
        load_pools(estate)
    assert "entries" in exc.value.reason


def test_a_missing_manifest_halts(tmp_path: Path) -> None:
    with pytest.raises(InferenceError):
        load_pools(tmp_path / "nowhere")


# ---------------------------------------------------------------------------
# the null provider is the default, everywhere
# ---------------------------------------------------------------------------


def test_the_default_provider_is_null_and_contacts_nothing() -> None:
    null = default_provider()
    assert isinstance(null, NullProvider)
    assert null.is_null is True
    answer = null.complete("anything", max_tokens=16)
    assert answer.empty and answer.total_tokens == 0
    assert answer.counted == "none"
    assert "no pool" in answer.finish_reason


def test_the_null_provider_records_the_intent_rather_than_omitting_it() -> None:
    null = default_provider()
    null.complete("a question")
    assert len(null.intents) == 1
    assert null.intents[0]["chars"] == len("a question")


def test_a_declared_but_disabled_pool_resolves_to_the_null_provider() -> None:
    assert provider_for_pool(parse_pool(good_entry(enabled=False))).is_null


def test_no_pool_at_all_resolves_to_the_null_provider() -> None:
    assert provider_for_pool(None).is_null


def test_an_enabled_pool_resolves_to_the_http_provider() -> None:
    live = provider_for_pool(parse_pool(good_entry(enabled=True)))
    assert isinstance(live, OpenAICompatHTTPProvider)
    assert live.is_null is False


def test_selecting_a_pool_nobody_declared_halts_rather_than_falling_back() -> None:
    pools = [parse_pool(good_entry(enabled=True))]
    with pytest.raises(InferenceError) as exc:
        select_provider(pools, "not-a-pool")
    assert "not-a-pool" in exc.value.reason


def test_two_enabled_pools_with_no_name_halts_rather_than_choosing() -> None:
    pools = [parse_pool(good_entry(enabled=True)),
             parse_pool(good_entry(id="second", enabled=True))]
    with pytest.raises(InferenceError):
        select_provider(pools)


def test_no_enabled_pool_selects_the_null_provider() -> None:
    assert select_provider([parse_pool(good_entry(enabled=False))]).is_null


# ---------------------------------------------------------------------------
# the round trip, against an in-process endpoint
# ---------------------------------------------------------------------------


def test_a_fake_local_endpoint_round_trips(fake_endpoint: Tuple[str, type]) -> None:
    url, handler = fake_endpoint
    pool = parse_pool(good_entry(endpoint=url, enabled=True))
    answer = OpenAICompatHTTPProvider(pool).complete("what is the claim?",
                                                    max_tokens=32)
    assert answer.text == "an answer"
    assert answer.model == "a-local-model"
    assert answer.pool_id == "local-inference"
    assert answer.latency_ms >= 0
    asked = handler.seen[-1]
    assert asked["model"] == "a-local-model"
    assert asked["messages"][0]["content"] == "what is the claim?"
    assert asked["stream"] is False
    assert asked["max_tokens"] == 32


def test_the_request_is_capped_at_the_pool_ceiling(
        fake_endpoint: Tuple[str, type]) -> None:
    url, handler = fake_endpoint
    pool = parse_pool(good_entry(endpoint=url, enabled=True, max_tokens=16))
    answer = OpenAICompatHTTPProvider(pool).complete("ask", max_tokens=9_999)
    assert handler.seen[-1]["max_tokens"] == 16
    assert "capped" in answer.finish_reason


def test_reported_usage_is_carried_and_reads_as_measured(
        fake_endpoint: Tuple[str, type]) -> None:
    url, _ = fake_endpoint
    pool = parse_pool(good_entry(endpoint=url, enabled=True))
    answer = OpenAICompatHTTPProvider(pool).complete("ask")
    assert (answer.prompt_tokens, answer.completion_tokens) == (20, 5)
    assert answer.total_tokens == 25
    assert answer.is_measured is True
    assert answer.counted == "reported"


def test_a_missing_usage_block_is_estimated_labelled_and_never_zero(
        fake_endpoint: Tuple[str, type]) -> None:
    url, handler = fake_endpoint
    handler.payload = {k: v for k, v in ANSWER.items() if k != "usage"}
    pool = parse_pool(good_entry(endpoint=url, enabled=True))
    answer = OpenAICompatHTTPProvider(pool).complete("a longer question here")
    assert answer.counted == "estimated"
    assert answer.is_measured is False
    assert answer.total_tokens > 0, (
        "a zero would let an unmetered endpoint spend against a budget that "
        "never moved")
    assert answer.counting_note
    assert "ESTIMATED" in answer.finish_reason


def test_an_empty_answer_is_a_state_not_a_failure(
        fake_endpoint: Tuple[str, type]) -> None:
    url, handler = fake_endpoint
    handler.payload = {"model": "a-local-model",
                       "choices": [{"message": {"content": ""}}],
                       "usage": {"prompt_tokens": 4, "completion_tokens": 0}}
    pool = parse_pool(good_entry(endpoint=url, enabled=True))
    answer = OpenAICompatHTTPProvider(pool).complete("ask")
    assert answer.empty is True
    assert answer.prompt_tokens == 4


def test_a_served_model_that_is_not_the_declared_one_is_named(
        fake_endpoint: Tuple[str, type]) -> None:
    url, handler = fake_endpoint
    handler.payload = dict(ANSWER, model="something-else")
    pool = parse_pool(good_entry(endpoint=url, enabled=True))
    answer = OpenAICompatHTTPProvider(pool).complete("ask")
    assert "not the declared" in answer.finish_reason
    assert answer.model == "something-else"


def test_a_500_is_a_failed_call_not_an_empty_answer(
        fake_endpoint: Tuple[str, type]) -> None:
    url, handler = fake_endpoint
    handler.status = 500
    pool = parse_pool(good_entry(endpoint=url, enabled=True))
    with pytest.raises(InferenceError) as exc:
        OpenAICompatHTTPProvider(pool).complete("ask")
    assert "500" in exc.value.reason


def test_a_non_json_body_is_a_failed_call(fake_endpoint: Tuple[str, type]) -> None:
    url, handler = fake_endpoint
    handler.payload = "<html>not json</html>"  # type: ignore[assignment]
    pool = parse_pool(good_entry(endpoint=url, enabled=True))
    # A JSON string body IS valid JSON but is not an object -- refused either way.
    with pytest.raises(InferenceError):
        OpenAICompatHTTPProvider(pool).complete("ask")


def test_a_response_with_no_choices_is_refused(
        fake_endpoint: Tuple[str, type]) -> None:
    url, handler = fake_endpoint
    handler.payload = {"model": "a-local-model"}
    pool = parse_pool(good_entry(endpoint=url, enabled=True))
    with pytest.raises(InferenceError) as exc:
        OpenAICompatHTTPProvider(pool).complete("ask")
    assert "choices" in exc.value.reason


def test_an_unreachable_endpoint_is_a_loud_failure() -> None:
    """Port 9 (discard) on loopback: nothing here listens, and no name resolves."""
    pool = parse_pool(good_entry(endpoint="http://127.0.0.1:9/v1", enabled=True,
                                 timeout_s=2))
    with pytest.raises(InferenceError) as exc:
        OpenAICompatHTTPProvider(pool).complete("ask")
    assert "local-inference" in exc.value.reason


def test_a_disabled_pool_refuses_to_be_called_directly(
        fake_endpoint: Tuple[str, type]) -> None:
    url, _ = fake_endpoint
    pool = parse_pool(good_entry(endpoint=url, enabled=False))
    with pytest.raises(InferenceError) as exc:
        OpenAICompatHTTPProvider(pool).complete("ask")
    assert "not enabled" in exc.value.reason


def test_an_empty_prompt_is_refused_before_a_socket_opens() -> None:
    pool = parse_pool(good_entry(enabled=True))
    provider = OpenAICompatHTTPProvider(pool, opener=refusing_opener)
    with pytest.raises(InferenceError) as exc:
        provider.complete("   ")
    assert "empty prompt" in exc.value.reason


def test_an_unvalidated_dict_is_refused_as_a_pool() -> None:
    with pytest.raises(InferenceError) as exc:
        OpenAICompatHTTPProvider(good_entry())  # type: ignore[arg-type]
    assert "ModelPool" in exc.value.reason


# ---------------------------------------------------------------------------
# accounting honesty
# ---------------------------------------------------------------------------


def test_an_estimated_count_with_no_stated_method_is_refused() -> None:
    with pytest.raises(InferenceError):
        Completion(text="x", prompt_tokens=1, completion_tokens=1, model="m",
                   provider="p", pool_id="q", latency_ms=1, counted="estimated")


def test_an_undeclared_counting_method_is_refused() -> None:
    with pytest.raises(InferenceError):
        Completion(text="x", prompt_tokens=1, completion_tokens=1, model="m",
                   provider="p", pool_id="q", latency_ms=1, counted="guessed")


def test_estimate_tokens_is_monotonic_and_never_zero_for_real_text() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("a") == 1
    assert estimate_tokens("a" * 400) > estimate_tokens("a" * 40)


# ---------------------------------------------------------------------------
# the detectors can fire
# ---------------------------------------------------------------------------


def test_the_provider_selftest_passes() -> None:
    ok, report = provider_mod.selftest()
    assert ok, report


def test_the_adapter_selftest_passes() -> None:
    ok, report = http_mod.selftest()
    assert ok, report


def test_the_selftests_can_actually_fail() -> None:
    """A detector that has never fired is indistinguishable from a broken one."""
    original = provider_mod.classify_endpoint
    try:
        provider_mod.classify_endpoint = lambda _e: ("loopback", "x")  # type: ignore[assignment]
        ok, report = provider_mod.selftest()
        assert not ok, "the selftest passed with the endpoint fence disabled"
        assert "accepted" in report
    finally:
        provider_mod.classify_endpoint = original  # type: ignore[assignment]


def test_no_vendor_sdk_is_imported_anywhere_in_the_package() -> None:
    """The coupling claim, checked mechanically rather than asserted in prose."""
    package = PACKAGE_ROOT / "intentops_core" / "inference"
    forbidden = ("import openai", "from openai", "import anthropic",
                 "from anthropic", "import httpx", "import requests",
                 "from langchain", "import litellm")
    for path in sorted(package.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{path.name} imports {token!r}"


def test_no_endpoint_literal_is_built_into_the_package() -> None:
    """There is no default host and no default port. A pool is declared or absent."""
    package = PACKAGE_ROOT / "intentops_core" / "inference"
    for path in sorted(package.glob("*.py")):
        for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1):
            if "http://" not in line and "https://" not in line:
                continue
            # Loopback and the documentation ranges appear in selftest
            # fixtures and in refusal remedies; a PLACEHOLDER host appears in
            # prose. Anything else on a line carrying a URL would be a default
            # endpoint, which this package does not have.
            allowed = ("#", "*", '"""', "127.0.0.1", "localhost", "10.0.0.4",
                       "198.51.100", "a-name", "some-name", "<port>",
                       "ftp://")
            assert any(token in line for token in allowed), (
                f"{path.name}:{number} carries a live endpoint literal")
