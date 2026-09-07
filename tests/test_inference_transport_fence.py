"""The transport half of the "no egress" claim, under adversarial test.

PURPOSE
    ``provider.classify_endpoint`` reads the URL an operator DECLARED. Two
    things could still carry the request body off that URL without the fence
    ever seeing them, and neither is visible in a config file:

      * an ambient proxy variable (``HTTP_PROXY`` and friends), which
        :func:`urllib.request.urlopen` honours by construction and CACHES in a
        global opener at first use; and
      * an HTTP redirect, which the far side chooses AFTER the fence has
        classified the near side.

    Both were live in this adapter until 2026-09-06. Measured then: with
    ``HTTP_PROXY`` set, the full POST body -- the prompt -- for a declared
    loopback pool was delivered to the proxy, the declared server received
    nothing, and the proxy's reply came back attributed to the declared pool.
    A 302 produced the same outcome one hop later. These tests are the
    positive controls that the closures actually fire, because a fence with no
    test that can fail it is indistinguishable from a decorative one.

WRITE MODEL
    None. Two in-process ``ThreadingHTTPServer`` instances on ``127.0.0.1``
    port 0 (ephemeral, OS-assigned) and nothing on disk. No live service, no
    network beyond loopback, no subprocess.

BLIND SPOTS
    - These prove the SHIPPED opener refuses. A caller that passes its own
      ``opener=`` takes that refusal back into its own hands; the served-host
      check in ``complete`` is the belt-and-braces for that case and is
      exercised separately below.
    - An OS-level redirection of a loopback literal (a hosts file, a firewall
      rule) is invisible to every layer here and is stated as a blind spot in
      the module docstring rather than tested.
    - ``NO_PROXY`` handling is not exercised as a matrix. The opener carries an
      EMPTY proxy map, so no proxy variable of any name can apply; enumerating
      them would test the stdlib, not this module.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Iterator, List, Tuple

import pytest

from intentops_core.inference.openai_compat import OpenAICompatHTTPProvider
from intentops_core.inference.provider import InferenceError, parse_pool

ANSWER = {"model": "a-local-model",
          "choices": [{"message": {"role": "assistant", "content": "an answer"},
                       "finish_reason": "stop"}],
          "usage": {"prompt_tokens": 3, "completion_tokens": 2}}


def pool_entry(endpoint: str) -> Dict[str, Any]:
    return {
        "id": "local-inference",
        "kind": "model-pool",
        "limit_shape": "none-observed; bounded by local hardware",
        "telemetry_source": "the local runtime's own request log",
        "max_concurrent": 1,
        "kill_switch": "stop the local runtime process",
        "data_fence": "local_only",
        "as_of": "2026-09-06",
        "endpoint": endpoint,
        "model": "a-local-model",
        "max_tokens": 32,
        "timeout_s": 5,
        "enabled": True,
    }


class _Recorder(BaseHTTPRequestHandler):
    """Records every request it is given, and answers a fixed shape."""

    seen: List[str] = []
    mode: str = "answer"          # "answer" | "redirect"
    redirect_to: str = ""

    def _record(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        type(self).seen.append(self.command + " " + self.path)

    def _answer(self) -> None:
        raw = json.dumps(ANSWER).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:  # noqa: N802 - the stdlib names it this way
        self._record()
        if type(self).mode == "redirect":
            self.send_response(302)
            self.send_header("Location", type(self).redirect_to)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._answer()

    def do_GET(self) -> None:  # noqa: N802 - a redirect is followed as a GET
        self._record()
        self._answer()

    def log_message(self, *args: Any) -> None:
        return


def _serve() -> Tuple[str, type, ThreadingHTTPServer, threading.Thread]:
    handler = type("_Handler", (_Recorder,),
                   {"seen": [], "mode": "answer", "redirect_to": ""})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = "http://127.0.0.1:" + str(server.server_address[1])
    return url, handler, server, thread


@pytest.fixture()
def two_endpoints() -> Iterator[Tuple[Tuple[str, type], Tuple[str, type]]]:
    """A DECLARED loopback endpoint and a SECOND one nobody declared."""
    d_url, d_handler, d_server, d_thread = _serve()
    o_url, o_handler, o_server, o_thread = _serve()
    try:
        yield (d_url, d_handler), (o_url, o_handler)
    finally:
        for server, thread in ((d_server, d_thread), (o_server, o_thread)):
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


def test_an_ambient_proxy_variable_cannot_capture_a_declared_pool(
        two_endpoints: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP_PROXY set: the DECLARED server is asked, the proxy sees nothing."""
    (declared_url, declared), (proxy_url, proxy) = two_endpoints
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, proxy_url)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)

    provider = OpenAICompatHTTPProvider(
        parse_pool(pool_entry(declared_url + "/v1")))
    completion = provider.complete("hello")

    assert completion.text == "an answer"
    assert declared.seen, "the declared endpoint was never asked"
    assert proxy.seen == [], (
        "the prompt reached a host the operator never declared: "
        + repr(proxy.seen))


def test_a_redirect_is_refused_and_the_target_is_never_contacted(
        two_endpoints: Any) -> None:
    """A 3xx is a different endpoint; it is refused, not followed."""
    (declared_url, declared), (elsewhere_url, elsewhere) = two_endpoints
    declared.mode = "redirect"
    declared.redirect_to = elsewhere_url + "/elsewhere"

    provider = OpenAICompatHTTPProvider(
        parse_pool(pool_entry(declared_url + "/v1")))
    with pytest.raises(InferenceError) as caught:
        provider.complete("hello")

    assert "not served by" in str(caught.value)
    assert declared.seen, "the declared endpoint was never asked"
    assert elsewhere.seen == [], (
        "the adapter followed a redirect to an undeclared host: "
        + repr(elsewhere.seen))


def test_a_caller_supplied_opener_that_follows_is_still_caught(
        two_endpoints: Any) -> None:
    """The belt-and-braces half: a served host that is not the declared one.

    ``opener=`` is a constructor argument every test in this repository uses,
    so the shipped opener is not the only one that can run. This proves the
    post-call host comparison refuses independently of it.
    """
    (declared_url, _declared), (elsewhere_url, elsewhere) = two_endpoints
    follower = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def opener(request: Any, timeout: float) -> Any:
        # Ask the OTHER server directly -- the shape a redirect-following
        # opener would produce, without needing one.
        return follower.open(
            urllib.request.Request(elsewhere_url + "/elsewhere", method="GET"),
            timeout=timeout)

    provider = OpenAICompatHTTPProvider(
        parse_pool(pool_entry(declared_url + "/v1")), opener=opener)
    with pytest.raises(InferenceError) as caught:
        provider.complete("hello")
    assert "was served by" in str(caught.value)
    assert elsewhere.seen, "the control did not actually reach the other host"
