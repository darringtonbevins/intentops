"""One HTTP adapter, over the wire shape every local runtime already speaks.

PURPOSE
    Turn a declared :class:`~intentops_core.inference.provider.ModelPool` into
    a working :class:`~intentops_core.inference.provider.Provider` using
    nothing but :mod:`urllib.request`. The wire shape is the OpenAI-compatible
    ``POST {endpoint}/chat/completions`` that the common local runtimes serve;
    no product is named here, and no SDK is imported, because a framework that
    imports a vendor's client has chosen that vendor for every node that
    installs it.

    WHAT THIS ADAPTER REFUSES TO DO, and why each refusal is load-bearing:

    * **It will not call a disabled pool.** A pool is born ``enabled: false``.
      Calling one anyway would make the switch decorative.
    * **It will not invent a token count.** If the endpoint returns a ``usage``
      block, those numbers are carried with ``counted="reported"``. If it does
      not, an estimate is carried with ``counted="estimated"`` and the method
      written into ``counting_note``. A zero would have been worse than an
      estimate: an unmetered endpoint would then spend against a budget that
      never moved, and the budget would read healthy the whole time.
    * **It will not turn a failure into an empty answer.** A transport error, a
      non-2xx status, an unparseable body, or a response with no ``choices``
      raises. An EMPTY answer from a healthy call is returned as an empty
      :class:`Completion`, because "the model said nothing" and "the call
      failed" are different facts that a caller grades differently -- the
      first is a WARN, the second is DEGRADED.
    * **It will not retry.** One call, one answer or one loud failure. A retry
      loop hiding a failing endpoint behind an eventual success is the
      silent-failure shape wearing a resilience hat.
    * **It will not report the served model as the declared one.** They are
      recorded separately and a mismatch is written into ``finish_reason``. The
      house lesson behind that clause is a subscription harness that described
      an entire review wave as premium-model when the transcripts showed it had
      served something else: the pin was believed, never read back.

WRITE MODEL
    None. This module opens a socket and returns a value. It writes no file,
    holds nothing between calls and claims no lock. Persisting a completion is
    the caller's job, under the caller's declared store model.

BLIND SPOTS -- stated so a 200 is not read as a good answer
    * The endpoint FENCE (``provider.classify_endpoint``) reads the URL the
      operator declared, and only that. Two ways the wire could still leave
      that URL are closed HERE, in the transport, and neither is closed by the
      fence: an ambient proxy variable, and a redirect. ``_OPENER`` is built
      with an EMPTY proxy map and a redirect handler that refuses, and the
      served URL's host is compared to the declared one after the fact. What
      remains outside both: an OS-level redirection of a loopback literal (a
      hosts file, a firewall rule) is invisible from here, and TLS trust is
      whatever the interpreter's default context does.
    * A 200 is a transport fact. This adapter cannot tell a correct answer from
      a confident wrong one, and nothing here evaluates content. The one shape
      it does catch is EMPTY, which is why empty is a first-class state.
    * ``usage`` is trusted when present. A runtime reporting usage inaccurately
      is invisible from here; the only cross-check available would be a second
      tokenizer, which this seed does not ship.
    * Latency is wall clock around the call, so it includes connection setup
      and the first-call model load. A cold first call is routinely an order of
      magnitude slower than a warm one, and this number does not separate them.
    * TLS verification is whatever the interpreter's default context does. This
      module neither relaxes it nor pins anything, and an operator pointing a
      pool at an https endpoint owns that trust decision.
    * Streaming is not implemented. ``stream`` is sent as ``false`` explicitly
      rather than omitted, because a runtime defaulting to a stream would make
      the body unparseable here and the failure would read as a bad endpoint.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from .provider import (
    Completion,
    InferenceError,
    ModelPool,
    NullProvider,
    Provider,
    estimate_tokens,
    parse_pool,
)

__all__ = [
    "OpenAICompatHTTPProvider",
    "COMPLETIONS_PATH",
    "provider_for_pool",
    "select_provider",
    "selftest",
    "main",
]

#: Appended to the pool's declared base endpoint. The base is what an operator
#: reads off their runtime's own documentation (commonly ending ``/v1``); the
#: path is ours and is not configurable, because a configurable path is a
#: second wire shape nobody tested.
COMPLETIONS_PATH = "/chat/completions"

#: What the caller's opener must look like. Injected only so the refusals below
#: can be proven without standing a server up; the default is stdlib urlopen.
Opener = Callable[[urllib.request.Request, float], Any]


class OpenAICompatHTTPProvider(Provider):
    """An OpenAI-compatible chat endpoint, reached over stdlib HTTP."""

    NAME = "openai-compat-http"

    def __init__(self, pool: ModelPool, *,
                 opener: Optional[Opener] = None,
                 clock: Optional[Callable[[], float]] = None) -> None:
        if not isinstance(pool, ModelPool):
            raise InferenceError(
                "this provider requires a validated ModelPool",
                "parse the estate row with inference.provider.parse_pool "
                "first; an unvalidated dict has not been through the endpoint "
                "fence")
        super().__init__(pool)
        self._opener: Opener = opener or _default_opener
        self._clock = clock or time.perf_counter

    @property
    def url(self) -> str:
        return self.pool.endpoint + COMPLETIONS_PATH

    def complete(self, prompt: str, max_tokens: Optional[int] = None) -> Completion:
        """One request. Raises loudly; returns empty only for an empty answer."""
        pool = self.pool
        assert pool is not None  # the constructor refuses None
        if not pool.enabled:
            raise InferenceError(
                f"model pool {pool.id!r} is declared but not enabled",
                "set `enabled: true` on the pool in estate/RESOURCES.yaml. A "
                "pool is born off; switching one on is a reviewed, dated "
                "operator act, never a side effect of a call")
        if not str(prompt or "").strip():
            raise InferenceError(
                "an empty prompt was passed to a live pool",
                "an empty prompt spends a call and cannot produce a grounded "
                "answer; the caller decides not to ask, this module does not "
                "ask for nothing")

        requested = int(max_tokens) if max_tokens is not None else pool.max_tokens
        if requested <= 0:
            raise InferenceError(
                f"max_tokens={requested} is not a budget",
                "pass a positive ceiling, or None to use the pool's own")
        effective = min(requested, pool.max_tokens)
        capped = effective < requested

        body = json.dumps({
            "model": pool.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": effective,
            "stream": False,
        }).encode("utf-8")
        request = urllib.request.Request(
            self.url, data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Accept": "application/json"})

        started = self._clock()
        try:
            with self._opener(request, pool.timeout_s) as response:
                status = int(getattr(response, "status", 200) or 200)
                served_url = _served_url(response)
                raw = response.read()
        except _RedirectRefused as exc:  # a 3xx is a different endpoint
            raise InferenceError(
                f"pool {pool.id!r} was not served by {self.url}: {exc.reason}",
                "the endpoint fence classified the DECLARED url, never a "
                "target the far side names afterwards. Point the pool at the "
                "endpoint that actually serves it, in estate/RESOURCES.yaml") from exc
        except urllib.error.HTTPError as exc:  # a status, and a body worth showing
            detail = _snippet(exc)
            raise InferenceError(
                f"pool {pool.id!r} answered HTTP {exc.code} at {self.url}"
                + (f": {detail}" if detail else ""),
                "read the runtime's own log. This adapter does not retry and "
                "does not degrade a failed call to an empty answer") from exc
        except urllib.error.URLError as exc:
            raise InferenceError(
                f"pool {pool.id!r} could not be reached at {self.url} "
                f"({exc.reason})",
                "start the local runtime, or correct `endpoint` in "
                "estate/RESOURCES.yaml. Unreachable is a state, never a zero") from exc
        except (TimeoutError, OSError) as exc:  # timeout surfaces as one of these
            raise InferenceError(
                f"pool {pool.id!r} did not answer within {pool.timeout_s}s "
                f"({type(exc).__name__}: {exc})",
                "raise `timeout_s` on the pool, or ask a smaller question. A "
                "timeout is a failed call, not a quiet zero") from exc
        latency_ms = int((self._clock() - started) * 1000)

        # Belt and braces over the opener above. The opener refuses redirects,
        # so this should be unreachable; it is here because the opener is
        # REPLACEABLE (``opener=`` is a constructor argument, and every test in
        # this repository uses it), and a caller who supplies one that follows
        # redirects would otherwise silently reintroduce the defect this class
        # exists to refuse. A served host that is not the declared host is a
        # failed call, never an answer attributed to the declared pool.
        if served_url:
            served = _origin(served_url)
            declared = _origin(self.url)
            declared_host = str(pool.host or "").lower()
            host_moved = bool(served[0] and declared_host
                              and served[0] != declared_host)
            if host_moved or (served != declared):
                raise InferenceError(
                    f"pool {pool.id!r} declares {declared[0]}:{declared[1]} but "
                    f"the answer was served by {served[0]}:{served[1]} "
                    f"({served_url})",
                    "the transport reached an origin nobody declared. This "
                    "adapter refuses to attribute another endpoint's answer to "
                    "a declared pool -- a port on the same host is a different "
                    "service, and a different host is a different trust "
                    "boundary")

        if status >= 300:
            raise InferenceError(
                f"pool {pool.id!r} answered HTTP {status} at {self.url}",
                "a non-2xx status is a failed call; this adapter never reads "
                "a body it was told is an error")

        try:
            doc = json.loads(raw.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - the decoder's class varies
            raise InferenceError(
                f"pool {pool.id!r} returned a body that is not JSON "
                f"({type(exc).__name__})",
                "the endpoint is not OpenAI-compatible, or `stream` was "
                "honoured despite being sent false. An unparseable body is a "
                "failure, never an empty answer") from exc
        if not isinstance(doc, Mapping):
            raise InferenceError(
                f"pool {pool.id!r} returned JSON that is not an object",
                "expected an object carrying `choices`")

        text, finish = _first_choice(doc, pool.id)
        served = str(doc.get("model") or "").strip()
        notes: List[str] = []
        if capped:
            notes.append(f"capped to the pool ceiling {pool.max_tokens}")
        if served and served != pool.model:
            notes.append(f"served model {served!r} is not the declared "
                         f"{pool.model!r}")
        if finish:
            notes.append(f"finish_reason={finish}")

        usage = doc.get("usage")
        if isinstance(usage, Mapping) and _has_counts(usage):
            prompt_tokens = _as_int(usage.get("prompt_tokens"))
            completion_tokens = _as_int(usage.get("completion_tokens"))
            counted, note = "reported", ""
        else:
            prompt_tokens = estimate_tokens(prompt)
            completion_tokens = estimate_tokens(text)
            counted = "estimated"
            note = ("the endpoint reported no usage block; counted at four "
                    "characters to the token, which is a rule of thumb and "
                    "not a tokenizer")
            notes.append("token counts are ESTIMATED, not measured")

        return Completion(
            text=text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model=served or pool.model,
            provider=self.NAME,
            pool_id=pool.id,
            latency_ms=latency_ms,
            counted=counted,
            counting_note=note,
            finish_reason="; ".join(notes),
        )


class _RedirectRefused(urllib.error.URLError):
    """The endpoint answered a 3xx, so the served host is not the declared one.

    A redirect is not a detail of a call: it is a DIFFERENT endpoint, chosen
    by the far side, after the fence has already classified the one the
    operator declared. Following it would let a loopback declaration deliver a
    prompt to whatever the far side names next -- which is precisely the fence
    being decorative.
    """

    def __init__(self, newurl: str, code: int) -> None:
        super().__init__(
            f"the endpoint answered HTTP {code} and pointed at {newurl!r}")
        self.newurl = str(newurl)
        self.code = int(code)


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    """Turns every 3xx into a refusal instead of a second request."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: Any,
                         headers: Any, newurl: str) -> Any:  # noqa: D102
        raise _RedirectRefused(newurl, code)


#: Built ONCE, and deliberately NOT with :func:`urllib.request.urlopen`.
#:
#: ``urlopen`` installs a default opener on first use, and that opener carries
#: a :class:`~urllib.request.ProxyHandler` built from the AMBIENT environment
#: (``HTTP_PROXY``/``http_proxy``/``ALL_PROXY``). A pool declared as loopback
#: would then have its request body -- the whole prompt -- delivered to
#: whatever those variables name, the declared server would receive nothing,
#: and the answer would come back attributed to the declared pool. The fence
#: reads the URL; it cannot read an environment variable. It is also sticky:
#: the global opener is cached at first call, so a proxy present once persists
#: for the life of the process.
#:
#: An EMPTY proxy map is not "no proxy configured" -- it is "proxies refused",
#: stated in code where an operator can see it. An operator who genuinely
#: wants a proxy hop declares the proxy itself as the endpoint, which the
#: fence then classifies, which is the point.
_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), _RefuseRedirect())


def _default_opener(request: urllib.request.Request, timeout: float) -> Any:
    return _OPENER.open(request, timeout=timeout)  # noqa: S310


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin(url: str) -> Tuple[str, int]:
    """``(host, port)`` -- what actually answered, or was asked.

    The comparison is deliberately the ORIGIN and not the hostname alone. Every
    loopback endpoint on a machine shares one hostname and differs only by
    port, so a hostname-only check would read two entirely different local
    services as the same endpoint -- which is the case this adapter most needs
    to tell apart.
    """
    parts = urllib.parse.urlsplit(str(url or ""))
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:  # a malformed port is not a port
        port = None
    if port is None:
        port = _DEFAULT_PORTS.get(parts.scheme.lower(), 0)
    return host, int(port)


def _served_url(response: Any) -> str:
    """The URL that actually answered, or "" when the opener cannot say.

    An opener supplied by a caller need not implement ``geturl``; "" then
    means UNKNOWN and the host check below is skipped rather than guessed.
    """
    url = getattr(response, "url", "") or ""
    if not url:
        getter = getattr(response, "geturl", None)
        if callable(getter):
            try:
                url = getter() or ""
            except Exception:  # noqa: BLE001 - an opener that cannot say
                url = ""
    return str(url)


def _snippet(exc: urllib.error.HTTPError, limit: int = 200) -> str:
    try:
        return exc.read().decode("utf-8", "replace")[:limit]
    except Exception:  # noqa: BLE001 - a body we cannot read is not a crash
        return ""


def _has_counts(usage: Mapping[str, Any]) -> bool:
    return ("prompt_tokens" in usage) or ("completion_tokens" in usage)


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _first_choice(doc: Mapping[str, Any], pool_id: str) -> Tuple[str, str]:
    choices = doc.get("choices")
    if not isinstance(choices, list) or not choices:
        raise InferenceError(
            f"pool {pool_id!r} returned no `choices`",
            "a response with no choices is a malformed answer, not an empty "
            "one, and is refused rather than counted as zero output")
    first = choices[0]
    if not isinstance(first, Mapping):
        raise InferenceError(f"pool {pool_id!r} returned a malformed choice",
                             "expected an object carrying `message`")
    message = first.get("message")
    content: Any = ""
    if isinstance(message, Mapping):
        content = message.get("content")
    elif "text" in first:  # the older completion shape, accepted and named
        content = first.get("text")
    if content is None:
        content = ""
    if not isinstance(content, str):
        raise InferenceError(
            f"pool {pool_id!r} returned non-text content "
            f"({type(content).__name__})",
            "this adapter reads text content only; a structured content array "
            "is a different wire shape and is refused rather than flattened")
    return content, str(first.get("finish_reason") or "")


# ---------------------------------------------------------------------------
# selection -- the null provider is the default, always
# ---------------------------------------------------------------------------


def provider_for_pool(pool: Optional[ModelPool], *,
                      opener: Optional[Opener] = None) -> Provider:
    """A live provider for an ENABLED pool; the null provider otherwise.

    There is no third answer. A pool that is absent, or present and switched
    off, resolves to :class:`NullProvider` -- which contacts nothing and
    records the intent of the call, so a stage running against it renders WARN
    rather than disappearing.
    """
    if pool is None or not pool.enabled:
        return NullProvider(pool)
    return OpenAICompatHTTPProvider(pool, opener=opener)


def select_provider(pools: Any, pool_id: Optional[str] = None, *,
                    opener: Optional[Opener] = None) -> Provider:
    """Pick one declared pool by id, or the single enabled one, or the null.

    A named pool that does not exist is a HALT, never a silent fall-through to
    the null provider: an operator who named a pool asked for THAT pool, and
    quietly answering with something else is how a fence gets routed around.
    Two enabled pools with no name given is also a HALT -- this module does not
    choose between an operator's pools, and picking the first would be a policy
    nobody declared.
    """
    rows = list(pools or ())
    if pool_id:
        for pool in rows:
            if pool.id == pool_id:
                return provider_for_pool(pool, opener=opener)
        raise InferenceError(
            f"no model pool is declared with id {pool_id!r}",
            "declare it in estate/RESOURCES.yaml, or name one of: "
            + (", ".join(p.id for p in rows) or "(none declared)"))
    enabled = [p for p in rows if p.enabled]
    if not enabled:
        return NullProvider(rows[0] if rows else None)
    if len(enabled) > 1:
        raise InferenceError(
            "more than one model pool is enabled and none was named: "
            + ", ".join(p.id for p in enabled),
            "pass the pool id. Choosing for you would be a routing policy "
            "this module has no standing to declare")
    return provider_for_pool(enabled[0], opener=opener)


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


def _pool(**overrides: Any) -> ModelPool:
    entry: Dict[str, Any] = {
        "id": "local-inference", "kind": "model-pool",
        "limit_shape": "none-observed", "telemetry_source": "the runtime log",
        "max_concurrent": 1, "kill_switch": "stop the runtime",
        "data_fence": "local_only", "as_of": "2026-09-06",
        "endpoint": "http://127.0.0.1:9/v1", "model": "a-local-model",
        "max_tokens": 64, "timeout_s": 5, "enabled": True,
    }
    entry.update(overrides)
    return parse_pool(entry)


class _Body:
    """The smallest object the adapter's ``with`` block accepts."""

    def __init__(self, payload: Any, status: int = 200) -> None:
        self._raw = (payload if isinstance(payload, bytes)
                     else json.dumps(payload).encode("utf-8"))
        self.status = status

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> "_Body":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


def _answering(payload: Any, status: int = 200) -> Opener:
    def opener(request: urllib.request.Request, timeout: float) -> Any:
        return _Body(payload, status)
    return opener


def _raising(exc: BaseException) -> Opener:
    def opener(request: urllib.request.Request, timeout: float) -> Any:
        raise exc
    return opener


_GOOD = {"model": "a-local-model",
         "choices": [{"message": {"role": "assistant", "content": "an answer"},
                      "finish_reason": "stop"}],
         "usage": {"prompt_tokens": 11, "completion_tokens": 3}}


def selftest() -> Tuple[bool, str]:
    """Prove the adapter's refusals fire and its accounting stays honest."""
    failures: List[str] = []

    def refuses(label: str, opener: Opener, **kw: Any) -> None:
        try:
            OpenAICompatHTTPProvider(_pool(**kw), opener=opener).complete("ask")
        except InferenceError:
            return
        failures.append(f"accepted {label}")

    try:
        answer = OpenAICompatHTTPProvider(
            _pool(), opener=_answering(_GOOD)).complete("ask")
        if answer.text != "an answer" or not answer.is_measured:
            failures.append("a good answer did not read as measured")
        if answer.total_tokens != 14:
            failures.append("reported usage was not carried through")
    except InferenceError as exc:
        failures.append(f"refused a good answer: {exc.reason}")

    # No usage block -> estimated, labelled, and non-zero.
    try:
        no_usage = dict(_GOOD)
        no_usage.pop("usage")
        est = OpenAICompatHTTPProvider(
            _pool(), opener=_answering(no_usage)).complete("ask")
        if est.is_measured or est.counted != "estimated":
            failures.append("a missing usage block did not read as estimated")
        if est.total_tokens <= 0 or not est.counting_note:
            failures.append("an estimate came back as zero or unexplained")
    except InferenceError as exc:
        failures.append(f"refused a usage-free answer: {exc.reason}")

    # An EMPTY answer is a state, not a failure.
    try:
        empty_doc = {"model": "a-local-model",
                     "choices": [{"message": {"content": ""}}],
                     "usage": {"prompt_tokens": 4, "completion_tokens": 0}}
        empty = OpenAICompatHTTPProvider(
            _pool(), opener=_answering(empty_doc)).complete("ask")
        if not empty.empty:
            failures.append("an empty answer did not read as empty")
    except InferenceError as exc:
        failures.append(f"raised on an empty answer: {exc.reason}")

    # A served model that is not the declared one is NAMED, not hidden.
    try:
        other = dict(_GOOD, model="a-different-model")
        drift = OpenAICompatHTTPProvider(
            _pool(), opener=_answering(other)).complete("ask")
        if "not the declared" not in drift.finish_reason:
            failures.append("a served-model mismatch was not surfaced")
    except InferenceError as exc:
        failures.append(f"refused a mismatched served model: {exc.reason}")

    refuses("a disabled pool", _answering(_GOOD), enabled=False)
    refuses("a non-JSON body", _answering(b"<html>not json</html>"))
    refuses("a response with no choices", _answering({"model": "m"}))
    refuses("a 500", _answering(_GOOD, status=500))
    refuses("an unreachable endpoint",
            _raising(urllib.error.URLError("connection refused")))
    refuses("a timeout", _raising(TimeoutError("timed out")))

    try:
        OpenAICompatHTTPProvider(_pool(), opener=_answering(_GOOD)).complete("")
        failures.append("accepted an empty prompt")
    except InferenceError:
        pass

    # Selection: off pool -> null; unknown id -> HALT; two enabled -> HALT.
    if not provider_for_pool(_pool(enabled=False)).is_null:
        failures.append("a disabled pool did not resolve to the null provider")
    if not provider_for_pool(None).is_null:
        failures.append("no pool at all did not resolve to the null provider")
    try:
        select_provider([_pool()], "not-a-pool")
        failures.append("accepted a pool id nothing declares")
    except InferenceError:
        pass
    try:
        select_provider([_pool(), _pool(id="second")])
        failures.append("chose between two enabled pools unasked")
    except InferenceError:
        pass

    report = ("inference.openai_compat: a clause did not fire" if failures else
              "inference.openai_compat: every refusal fired; an empty answer "
              "stays a state, an estimate stays labelled, and no pool was "
              "chosen for the operator")
    if failures:
        report += " -- " + "; ".join(failures)
    return (not failures, report)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="intentops-inference-http",
        description="the stdlib adapter over an OpenAI-compatible endpoint")
    parser.add_argument("--selftest", action="store_true",
                        help="prove every refusal in this adapter can fire")
    args = parser.parse_args(argv)
    if not args.selftest:
        print("nothing to do: `--selftest` is the only mode that contacts "
              "nothing. A live call is made through the metabolism runner, "
              "against a pool an operator declared.", file=sys.stderr)
        return 1
    ok, report = selftest()
    print(report)
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
