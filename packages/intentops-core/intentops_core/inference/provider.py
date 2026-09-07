"""Provider-neutral local inference: the contract, the pool, and the refusals.

PURPOSE
    A node with a metabolism eventually has to ask a model something. This
    module is the whole of what that costs in coupling: a :class:`Provider`
    ABC with ONE method, a :class:`Completion` that carries its own token
    accounting and says how that accounting was arrived at, and a
    :class:`ModelPool` parsed out of a row the OPERATOR declared in
    ``estate/RESOURCES.yaml``.

    Three things are deliberately absent, and their absence is the design:

    * **No vendor SDK, and no vendor name.** The transport is
      :mod:`urllib.request` and the wire shape is the OpenAI-compatible chat
      completion, which every local runtime worth pointing at already speaks.
      Naming a product here would put a preference into a framework.
    * **No endpoint of our own.** There is no default host, no default port and
      no fallback. A node that has been told nothing has :class:`NullProvider`,
      which is not a stub for missing code -- it is the honest statement that
      nobody has declared a pool yet, and it opens no socket ever.
    * **No egress.** :func:`classify_endpoint` REFUSES anything that is not a
      loopback or RFC1918 address, and refuses a bare hostname it cannot
      resolve without DNS. An operator who genuinely means to reach a host on
      the far side of that fence writes ``allow_remote: true`` and a
      ``allow_remote_reason``, and the reason is required -- a switch with no
      tag says nothing about why it is open.

      Read that clause narrowly, because it was once written too broadly and
      the code did less than the sentence did: this function classifies the
      URL the operator DECLARED. It cannot see an ambient proxy variable and
      it cannot see a redirect, and either would have carried the prompt off
      the declared host while the fence still read "loopback". Those two are
      refused in the TRANSPORT (``openai_compat._OPENER``: an empty proxy map,
      a redirect handler that raises, and a served-host check after the call).
      "No egress" is therefore a property of the fence AND the transport
      together, never of this function alone.

    THE DEFECT THIS EXISTS TO RETIRE. In the estate this generalises from, a
    voice pipeline shipped with local providers as the FALLBACK behind cloud
    ones, so raw microphone audio left the machine before a faster local engine
    was tried, and nothing in the config made that visible. Here the local
    fence is the default and leaving it is a declared, reasoned, per-pool act.

WRITE MODEL
    None. This module is a pure reader of ``estate/RESOURCES.yaml`` rows and a
    factory over them. It writes no file, holds no state between calls, and
    claims no lock. The row it reads is a single-writer store -- the operator,
    by hand or through one explicit generator -- and the estate loader owns
    that file's validation of the fields every resource shares. What is
    validated HERE is only what a MODEL POOL additionally owes.

BLIND SPOTS -- stated so a clean parse is not read as a safe pool
    * ``classify_endpoint`` reads the URL, and only the URL. It never resolves
      a name, so ``http://some-name:PORT`` is refused as UNRESOLVED rather than
      inspected -- fail-closed, and the reason is that resolving it would mean
      a DNS lookup from a module whose whole claim is that it contacts nothing
      until asked. A loopback LITERAL that has been redirected at the OS level
      (a hosts file, an iptables rule) still reads as loopback here.
      It also says nothing about how the request TRAVELS: a proxy variable or
      an HTTP redirect would both deliver the body somewhere this function
      never saw. Those are the transport's to refuse, and it does; a caller
      that supplies its own ``opener`` to :class:`OpenAICompatHTTPProvider`
      takes that refusal back into its own hands.
    * A private address is not a trusted address. RFC1918 says "not routable on
      the public internet"; it says nothing about who is on that segment.
      ``data_fence`` is the operator's claim about the pool, and this module
      cannot verify it -- it only refuses to let the claim be missing.
    * Token counts are the ENDPOINT's, when it reports them, and an ESTIMATE
      otherwise. The estimate is labelled ``counted="estimated"`` and carries
      its method; it is never presented as a measurement. A runtime that
      reports usage inaccurately is invisible to this module.
    * ``max_concurrent`` is read and carried but NOT enforced here. Enforcing
      it is a scheduler's job over a shared ledger; a per-process counter would
      look like a control and bound nothing across processes.
    * Nothing here rate-limits, retries, or backs off. One call, one answer or
      one loud failure. A retry policy that hid a failing endpoint behind a
      slow success would be the silent-failure shape wearing a resilience hat.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit

__all__ = [
    "InferenceError",
    "Completion",
    "Provider",
    "NullProvider",
    "ModelPool",
    "DATA_FENCES",
    "ENDPOINT_CLASSES",
    "REFUSED_ENDPOINT_CLASSES",
    "COUNTING_METHODS",
    "POOL_KIND",
    "REQUIRED_POOL_FIELDS",
    "OPTIONAL_POOL_FIELDS",
    "classify_endpoint",
    "parse_pool",
    "load_pools",
    "estimate_tokens",
    "selftest",
    "main",
]

#: The estate resource kind a model pool is declared under. A row of any other
#: kind is not ours and is skipped, never coerced.
POOL_KIND = "model-pool"

#: What may be sent to a pool. Closed vocabulary; an undeclared value is a
#: HALT, because a fence nobody can read is not a fence.
#:
#:   local_only  -- nothing leaves this host. The only fence a loopback pool
#:                  can honestly claim.
#:   in_family   -- may reach a provider already trusted with this material.
#:   third_party -- may reach a party outside that trust.
DATA_FENCES: Tuple[str, ...] = ("local_only", "in_family", "third_party")

#: How an endpoint's host reads. See the module docstring for why a name that
#: is not an IP literal is UNRESOLVED rather than looked up.
ENDPOINT_CLASSES: Tuple[str, ...] = ("loopback", "private", "public",
                                     "unresolved")

#: The two classes a pool may not use without ``allow_remote``.
REFUSED_ENDPOINT_CLASSES: Tuple[str, ...] = ("public", "unresolved")

#: How a completion's token counts were arrived at. ``reported`` is the
#: endpoint's own usage block; ``estimated`` is ours and says so; ``none`` is
#: the null provider, which counted nothing because it did nothing.
COUNTING_METHODS: Tuple[str, ...] = ("reported", "estimated", "none")

#: What a model-pool row owes ON TOP of the fields every estate resource owes
#: (id, kind, limit_shape, telemetry_source, max_concurrent, kill_switch,
#: data_fence, as_of -- validated by the estate loader, not here).
REQUIRED_POOL_FIELDS: Tuple[str, ...] = ("endpoint", "model", "max_tokens",
                                         "timeout_s", "enabled")

OPTIONAL_POOL_FIELDS: Tuple[str, ...] = ("allow_remote", "allow_remote_reason",
                                         "notes")


class InferenceError(RuntimeError):
    """A HALT with a remedy attached, in the shape the rest of genesis prints."""

    def __init__(self, reason: str, remedy: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.remedy = remedy

    def render(self) -> str:
        out = f"HALT: {self.reason}"
        if self.remedy:
            out += f"\n  remedy: {self.remedy}"
        return out


# ---------------------------------------------------------------------------
# the completion
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Completion:
    """One answer, with its cost and the provenance of that cost.

    ``counted`` is the field that keeps this honest. A value produced without
    evidence must be distinguishable in-band from a measurement, so an
    estimated token count says ``estimated`` and carries ``counting_note``
    naming the method. Nothing downstream may add a reported and an estimated
    count and call the sum measured -- :func:`Completion.is_measured` is there
    so a caller does not have to remember that.
    """

    text: str
    prompt_tokens: int
    completion_tokens: int
    model: str
    provider: str
    pool_id: str
    latency_ms: int
    counted: str = "none"
    finish_reason: str = ""
    counting_note: str = ""

    def __post_init__(self) -> None:
        if self.counted not in COUNTING_METHODS:
            raise InferenceError(
                f"undeclared counting method {self.counted!r}",
                "use one of: " + ", ".join(COUNTING_METHODS)
                + ". An undeclared value is a hard exit, never a default")
        if self.counted == "estimated" and not self.counting_note:
            raise InferenceError(
                "an estimated token count with no stated method is a number "
                "wearing a measurement's costume",
                "pass counting_note naming how the estimate was made")

    @property
    def total_tokens(self) -> int:
        return int(self.prompt_tokens) + int(self.completion_tokens)

    @property
    def is_measured(self) -> bool:
        """True only when the endpoint reported its own usage."""
        return self.counted == "reported"

    @property
    def empty(self) -> bool:
        """Zero output is a STATE. A caller that treats this as success is the bug."""
        return not self.text.strip()

    def to_row(self) -> Dict[str, Any]:
        return {
            "model": self.model, "provider": self.provider,
            "pool_id": self.pool_id,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "counted": self.counted, "counting_note": self.counting_note,
            "latency_ms": self.latency_ms,
            "finish_reason": self.finish_reason,
            "chars": len(self.text), "empty": self.empty,
        }


def estimate_tokens(text: str) -> int:
    """A deliberately crude character-based estimate.

    Four characters to a token is a rule of thumb, not a tokenizer, and it is
    wrong for every model by some margin. It exists so a runtime that reports
    no usage still contributes a NUMBER to the envelope rather than a zero --
    a zero would let an unmetered endpoint spend without bound while the
    budget read healthy. Every value it produces is labelled ``estimated``.
    """
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


# ---------------------------------------------------------------------------
# the endpoint fence
# ---------------------------------------------------------------------------


#: The address blocks that count as PRIVATE here: the three RFC1918 IPv4
#: blocks and the IPv6 unique-local range. Deliberately NARROWER than
#: ``ipaddress.is_private``, which also returns True for the documentation and
#: benchmarking ranges (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24,
#: 198.18.0.0/15) and for carrier-grade NAT space. Those are not an operator's
#: own segment, and a fence that treated them as one would pass an address
#: nobody administers. Link-local (169.254/16) is excluded for the same
#: reason: it is what a host falls back to when addressing FAILED.
_PRIVATE_NETWORKS = tuple(ipaddress.ip_network(cidr) for cidr in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7"))


def classify_endpoint(endpoint: str) -> Tuple[str, str]:
    """``(class, host)`` for a URL, without resolving anything.

    A scheme other than http/https, or a URL with no host, is a HALT rather
    than a class: it is not a remote endpoint, it is not an endpoint.
    """
    text = str(endpoint or "").strip()
    if not text:
        raise InferenceError(
            "the pool declares an empty endpoint",
            "give the base URL of an OpenAI-compatible endpoint, e.g. "
            "http://127.0.0.1:<port>/v1")
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https"):
        raise InferenceError(
            f"endpoint scheme {parts.scheme or '(none)'!r} is not http or https",
            "this adapter speaks HTTP only; a scheme it does not know is "
            "refused rather than guessed at")
    host = parts.hostname or ""
    if not host:
        raise InferenceError(
            f"the endpoint {text!r} carries no host",
            "an endpoint without a host cannot be classified, and an "
            "unclassifiable endpoint is refused")
    if host.lower() == "localhost":
        return "loopback", host
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # A NAME. Resolving it would mean a DNS lookup from a module whose
        # claim is that it contacts nothing until asked, so it is refused as
        # unresolved -- not silently treated as either local or remote.
        return "unresolved", host
    if address.is_loopback:
        return "loopback", host
    if any(address in net for net in _PRIVATE_NETWORKS):
        return "private", host
    return "public", host


# ---------------------------------------------------------------------------
# the pool
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelPool:
    """One operator-declared model pool, validated for what inference needs."""

    id: str
    endpoint: str
    model: str
    data_fence: str
    max_tokens: int
    timeout_s: float
    enabled: bool
    endpoint_class: str
    host: str
    max_concurrent: int = 1
    kill_switch: str = ""
    telemetry_source: str = ""
    allow_remote: bool = False
    allow_remote_reason: str = ""
    as_of: str = ""
    notes: str = ""

    @property
    def is_local(self) -> bool:
        return self.endpoint_class == "loopback"

    def to_row(self) -> Dict[str, Any]:
        return {
            "id": self.id, "model": self.model,
            "endpoint_class": self.endpoint_class, "host": self.host,
            "data_fence": self.data_fence, "max_tokens": self.max_tokens,
            "timeout_s": self.timeout_s, "enabled": self.enabled,
            "allow_remote": self.allow_remote,
            "max_concurrent": self.max_concurrent,
            "kill_switch": self.kill_switch,
        }


def _require(entry: Mapping[str, Any], name: str, where: str) -> Any:
    if name not in entry:
        raise InferenceError(
            f"{where} is missing required field `{name}`",
            "there is no default for it. A reader that substitutes a default "
            "for a missing field has taken that field out of the population, "
            "and the one field nobody read is the one nobody checks")
    value = entry[name]
    if value is None:
        raise InferenceError(
            f"{where} declares `{name}: null`",
            "null is not an answer for a load-bearing field")
    return value


def parse_pool(entry: Mapping[str, Any]) -> ModelPool:
    """Validate one ``kind: model-pool`` row, or HALT saying exactly why."""
    if not isinstance(entry, Mapping):
        raise InferenceError("a model-pool row is not a mapping",
                             "each entry is a YAML mapping of its fields")
    pool_id = str(entry.get("id") or "").strip()
    if not pool_id:
        raise InferenceError(
            "a model-pool row carries no `id`",
            "give every pool an id; a nameless pool cannot be selected, "
            "tagged out, or blamed")
    where = f"model pool {pool_id!r}"

    kind = str(entry.get("kind") or "")
    if kind != POOL_KIND:
        raise InferenceError(
            f"{where} declares kind {kind!r}",
            f"this reader parses `kind: {POOL_KIND}` rows only")

    undeclared = sorted(
        set(map(str, entry.keys()))
        - set(REQUIRED_POOL_FIELDS) - set(OPTIONAL_POOL_FIELDS)
        - {"id", "kind", "limit_shape", "telemetry_source", "max_concurrent",
           "kill_switch", "data_fence", "as_of"})
    if undeclared:
        raise InferenceError(
            f"{where} carries undeclared field(s) {undeclared}",
            "an undeclared field is a HALT, never ignored: a typo "
            "(`endpoint_url` for `endpoint`) would otherwise load clean and "
            "silently lose the field it was meant to set")

    endpoint = str(_require(entry, "endpoint", where))
    endpoint_class, host = classify_endpoint(endpoint)

    allow_remote = entry.get("allow_remote", False)
    if not isinstance(allow_remote, bool):
        raise InferenceError(
            f"{where} declares `allow_remote: {allow_remote!r}`, which is not "
            "a boolean",
            "`true` or `false`; a string is refused because "
            "`allow_remote: \"false\"` is truthy in most readers")
    allow_remote_reason = str(entry.get("allow_remote_reason") or "").strip()

    if endpoint_class in REFUSED_ENDPOINT_CLASSES and not allow_remote:
        detail = ("is not a loopback or RFC1918 address"
                  if endpoint_class == "public"
                  else "is a name this module will not resolve")
        raise InferenceError(
            f"{where} points at host {host!r}, which {detail} "
            f"(classified {endpoint_class})",
            "a local adapter defaults to the local fence. If you mean to "
            "reach that host, declare `allow_remote: true` AND an "
            "`allow_remote_reason` -- an open switch with no tag says nothing "
            "about why it is open")
    if allow_remote and not allow_remote_reason:
        raise InferenceError(
            f"{where} sets `allow_remote: true` with no `allow_remote_reason`",
            "state why this pool leaves the local fence. 'Will explain later' "
            "is a deferral, not a reason")

    model = str(_require(entry, "model", where)).strip()
    if not model:
        raise InferenceError(
            f"{where} declares an empty `model`",
            "name the model this pool serves; an unnamed model makes every "
            "record of a call unattributable")

    data_fence = str(_require(entry, "data_fence", where)).strip()
    if data_fence not in DATA_FENCES:
        raise InferenceError(
            f"{where} declares data_fence {data_fence!r}",
            "use one of: " + ", ".join(DATA_FENCES)
            + ". Free prose is refused HERE (the estate loader accepts it for "
              "resources generally) because a fence a caller cannot compare "
              "against is a fence nothing can enforce")
    if endpoint_class != "loopback" and data_fence == "local_only":
        raise InferenceError(
            f"{where} claims `local_only` while pointing at a {endpoint_class} "
            f"host ({host})",
            "a pool that leaves this host cannot claim nothing leaves this "
            "host. Correct the fence or correct the endpoint")

    max_tokens = _positive_int(_require(entry, "max_tokens", where),
                              f"{where} `max_tokens`")
    timeout_s = _positive_float(_require(entry, "timeout_s", where),
                                f"{where} `timeout_s`")

    enabled = _require(entry, "enabled", where)
    if not isinstance(enabled, bool):
        raise InferenceError(
            f"{where} declares `enabled: {enabled!r}`, which is not a boolean",
            "`true` or `false`. A pool is born disabled; switching one on is a "
            "reviewed, dated operator act, never a side effect of adding a row")

    max_concurrent = entry.get("max_concurrent")
    if max_concurrent is None:
        raise InferenceError(
            f"{where} declares no `max_concurrent`",
            "declared, not measured, and never defaulted: a scheduler that "
            "invents a concurrency number over-allocates silently")

    return ModelPool(
        id=pool_id,
        endpoint=endpoint.rstrip("/"),
        model=model,
        data_fence=data_fence,
        max_tokens=max_tokens,
        timeout_s=timeout_s,
        enabled=bool(enabled),
        endpoint_class=endpoint_class,
        host=host,
        max_concurrent=_positive_int(max_concurrent,
                                     f"{where} `max_concurrent`"),
        kill_switch=str(entry.get("kill_switch") or ""),
        telemetry_source=str(entry.get("telemetry_source") or ""),
        allow_remote=allow_remote,
        allow_remote_reason=allow_remote_reason,
        as_of=str(entry.get("as_of") or ""),
        notes=str(entry.get("notes") or ""),
    )


def _positive_int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InferenceError(f"{where} is {value!r}, which is not an integer",
                             "give a positive whole number")
    if value <= 0:
        raise InferenceError(f"{where} is {value}",
                             "give a positive whole number; a ceiling of zero "
                             "is a disabled pool wearing a budget")
    return int(value)


def _positive_float(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InferenceError(f"{where} is {value!r}, which is not a number",
                             "give a positive number of seconds")
    if float(value) <= 0:
        raise InferenceError(f"{where} is {value}",
                             "give a positive number of seconds; a timeout of "
                             "zero is a call that can hang forever or not at all")
    return float(value)


def load_pools(estate_dir: Path | str) -> Tuple[ModelPool, ...]:
    """Every ``kind: model-pool`` row in ``estate/RESOURCES.yaml``, validated.

    ``entries: []`` is a clean load returning no pools -- which is a NODE WITH
    NO POOLS, not an error. What is an error is a row that claims to be a pool
    and is not usable as one.
    """
    path = Path(estate_dir) / "RESOURCES.yaml"
    if not path.is_file():
        raise InferenceError(
            f"the estate resources manifest is missing: {path}",
            "restore estate/RESOURCES.yaml. An absent manifest is a field "
            "nobody read, never an empty one")
    try:
        import yaml  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise InferenceError(
            f"the YAML library is unavailable ({exc})",
            "pip install pyyaml -- this loader never falls back to a partial "
            "hand-rolled parse") from exc
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - the parser's error class varies
        raise InferenceError(f"{path} does not parse ({exc})",
                             "fix the YAML; a partial parse is refused") from exc
    if not isinstance(doc, Mapping):
        raise InferenceError(f"{path} is not a mapping",
                             "the document carries `schema:` and `entries:`")
    raw = doc.get("entries")
    if raw is None:
        raise InferenceError(
            f"{path} declares no `entries:` key",
            "an empty estate is `entries: []`; a MISSING key and an empty "
            "list are different facts and must not read the same")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise InferenceError(f"{path} `entries:` is not a list",
                             "use a YAML list, or `entries: []`")
    pools = [parse_pool(e) for e in raw
             if isinstance(e, Mapping) and e.get("kind") == POOL_KIND]
    return tuple(pools)


# ---------------------------------------------------------------------------
# the contract
# ---------------------------------------------------------------------------


class Provider(ABC):
    """One method. Everything else about a provider is somebody's policy.

    A subclass declares ``NAME`` and implements :meth:`complete`. It may not
    invent a model id, may not silently truncate below the caller's request
    without saying so in ``finish_reason``, and may not return an empty
    :class:`Completion` in place of raising -- an empty answer and a failed
    call are different facts, and a caller grades them differently.
    """

    NAME: str = ""

    def __init__(self, pool: Optional[ModelPool] = None) -> None:
        if not str(getattr(self, "NAME", "")).strip():
            raise InferenceError(
                f"{type(self).__name__} declares no NAME",
                "declare NAME. Every completion records which provider "
                "produced it, and an unattributable record is not evidence")
        self.pool = pool

    @property
    def pool_id(self) -> str:
        return self.pool.id if self.pool is not None else "(none)"

    @property
    def is_null(self) -> bool:
        """True for a provider that contacts nothing. Read before budgeting."""
        return False

    @abstractmethod
    def complete(self, prompt: str, max_tokens: Optional[int] = None) -> Completion:
        """Answer once, or raise :class:`InferenceError` loudly."""

    def describe(self) -> Dict[str, Any]:
        """What a dry-run prints. Never contacts anything."""
        row: Dict[str, Any] = {"provider": self.NAME, "is_null": self.is_null,
                               "pool_id": self.pool_id}
        if self.pool is not None:
            row.update(self.pool.to_row())
        return row


class NullProvider(Provider):
    """The default: records the INTENT of a call and produces nothing.

    This is not a placeholder for unimplemented code. It is the honest state of
    a node nobody has declared a pool for, and a call against it is RECORDED
    rather than omitted -- a stage that leaves the population is exactly how a
    metabolism comes to report green over zero.
    """

    NAME = "null"

    def __init__(self, pool: Optional[ModelPool] = None) -> None:
        super().__init__(pool)
        self.intents: List[Dict[str, Any]] = []

    @property
    def is_null(self) -> bool:
        return True

    def complete(self, prompt: str, max_tokens: Optional[int] = None) -> Completion:
        self.intents.append({"chars": len(prompt),
                             "max_tokens": max_tokens,
                             "would_ask": prompt[:200]})
        return Completion(
            text="", prompt_tokens=0, completion_tokens=0,
            model="(none declared)", provider=self.NAME, pool_id=self.pool_id,
            latency_ms=0, counted="none",
            finish_reason="null-provider: no pool is declared or enabled")


def default_provider() -> NullProvider:
    """What a node has before an operator declares anything. Always the null."""
    return NullProvider()


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


def _good_entry(**overrides: Any) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "id": "local-inference",
        "kind": POOL_KIND,
        "limit_shape": "none-observed; bounded by local hardware",
        "telemetry_source": "the local runtime's own request log",
        "max_concurrent": 1,
        "kill_switch": "stop the local runtime process",
        "data_fence": "local_only",
        "as_of": "2026-09-06",
        "endpoint": "http://127.0.0.1:9/v1",
        "model": "a-local-model",
        "max_tokens": 256,
        "timeout_s": 30,
        "enabled": False,
    }
    entry.update(overrides)
    return entry


def selftest() -> Tuple[bool, str]:
    """Prove every refusal in this module can actually fire.

    A detector that has never fired is indistinguishable from a broken one, so
    each clause below is exercised against a document that differs from a good
    one in exactly ONE way.
    """
    failures: List[str] = []

    def refuses(label: str, **overrides: Any) -> None:
        try:
            parse_pool(_good_entry(**overrides))
        except InferenceError:
            return
        failures.append(f"accepted {label}")

    try:
        pool = parse_pool(_good_entry())
        if pool.endpoint_class != "loopback" or pool.enabled:
            failures.append("a good loopback pool did not read as expected")
    except InferenceError as exc:
        failures.append(f"refused a good pool: {exc.reason}")

    refuses("a public endpoint", endpoint="http://198.51.100.7:9/v1")
    refuses("a bare hostname", endpoint="http://a-name:9/v1")
    refuses("allow_remote with no reason",
            endpoint="http://198.51.100.7:9/v1", allow_remote=True,
            data_fence="third_party")
    refuses("a free-text data fence", data_fence="local only, nothing leaves")
    refuses("enabled as a string", enabled="false")
    refuses("a zero max_tokens", max_tokens=0)
    refuses("a zero timeout", timeout_s=0)
    refuses("an undeclared field", endpoint_url="http://127.0.0.1:9/v1")
    refuses("an unknown scheme", endpoint="ftp://127.0.0.1/v1")

    # allow_remote WITH a reason is accepted -- the fence is a tag, not a wall.
    try:
        remote = parse_pool(_good_entry(
            endpoint="http://198.51.100.7:9/v1", allow_remote=True,
            data_fence="third_party",
            allow_remote_reason="the operator runs this runtime on a host "
                                "they administer, stated 2026-09-06"))
        if remote.endpoint_class != "public" or remote.is_local:
            failures.append("a tagged remote pool did not read as remote")
    except InferenceError as exc:
        failures.append(f"refused a tagged remote pool: {exc.reason}")

    # A private pool may not claim local_only.
    refuses("local_only over a private address",
            endpoint="http://10.0.0.4:9/v1")
    try:
        private = parse_pool(_good_entry(endpoint="http://10.0.0.4:9/v1",
                                         data_fence="in_family"))
        if private.endpoint_class != "private":
            failures.append("an RFC1918 endpoint did not classify private")
    except InferenceError as exc:
        failures.append(f"refused a private pool: {exc.reason}")

    # The null provider contacts nothing and counts nothing.
    null = default_provider()
    answer = null.complete("anything at all", max_tokens=16)
    if not (answer.empty and answer.total_tokens == 0
            and answer.counted == "none" and null.is_null):
        failures.append("the null provider did not read as null")
    if len(null.intents) != 1:
        failures.append("the null provider did not record the intent")

    # An estimated count with no method is refused.
    try:
        Completion(text="x", prompt_tokens=1, completion_tokens=1,
                   model="m", provider="p", pool_id="q", latency_ms=1,
                   counted="estimated")
        failures.append("accepted an estimate with no stated method")
    except InferenceError:
        pass

    report = ("inference.provider: a clause did not fire"
              if failures else
              "inference.provider: every refusal fired; the null provider "
              "contacts nothing and counts nothing")
    if failures:
        report += " -- " + "; ".join(failures)
    return (not failures, report)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="intentops-inference",
        description="declared model pools, and the local fence over them")
    parser.add_argument("--estate", default="estate",
                        help="the estate directory holding RESOURCES.yaml")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--selftest", action="store_true",
                        help="prove every refusal in this module can fire")
    args = parser.parse_args(argv)

    if args.selftest:
        ok, report = selftest()
        print(report)
        return 0 if ok else 1

    try:
        pools = load_pools(args.estate)
    except InferenceError as exc:
        print(exc.render(), file=sys.stderr)
        return 1

    rows = [p.to_row() for p in pools]
    if args.json:
        print(json.dumps({"pools": rows,
                          "enabled": sum(1 for p in pools if p.enabled)},
                         indent=2))
        return 0
    if not pools:
        print("no model pool is declared -- this node's provider is the null "
              "provider, which contacts nothing")
        return 0
    for pool in pools:
        state = "enabled" if pool.enabled else "declared, off"
        print(f"  {pool.id:<24} {state:<14} {pool.endpoint_class:<10} "
              f"fence={pool.data_fence:<12} model={pool.model}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
