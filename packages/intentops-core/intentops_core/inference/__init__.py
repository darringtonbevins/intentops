"""Inference: a contract, a local HTTP adapter, and a default that calls nothing.

PURPOSE
    The whole coupling between IntentOps and a model lives behind this package:
    one :class:`~.provider.Provider` ABC, one stdlib HTTP adapter over the
    OpenAI-compatible chat shape, and :class:`~.provider.NullProvider` as the
    default a node is born with. No vendor SDK is imported anywhere in it, and
    no endpoint is built in -- a pool is a row the operator declared in
    ``estate/RESOURCES.yaml``, or there is no pool.

WRITE MODEL
    None, anywhere in this package. Both modules are readers and callers.
    Persisting a completion belongs to the caller, under the caller's own
    declared store model -- for the metabolism that is
    ``intentops_core.metabolism.runner`` and its append-only run ledger.

BLIND SPOTS
    Each module publishes its own; the two that a reader of this file should
    carry away are that the endpoint fence reads the URL and never resolves a
    name (fail-closed), and that a token count is measured only when the
    endpoint reported it -- otherwise it is an estimate and says so.
"""

from __future__ import annotations

from .openai_compat import (
    COMPLETIONS_PATH,
    OpenAICompatHTTPProvider,
    provider_for_pool,
    select_provider,
)
from .provider import (
    COUNTING_METHODS,
    DATA_FENCES,
    ENDPOINT_CLASSES,
    POOL_KIND,
    REFUSED_ENDPOINT_CLASSES,
    Completion,
    InferenceError,
    ModelPool,
    NullProvider,
    Provider,
    classify_endpoint,
    default_provider,
    estimate_tokens,
    load_pools,
    parse_pool,
)

__all__ = [
    "COMPLETIONS_PATH", "COUNTING_METHODS", "Completion", "DATA_FENCES",
    "ENDPOINT_CLASSES", "InferenceError", "ModelPool", "NullProvider",
    "OpenAICompatHTTPProvider", "POOL_KIND", "Provider",
    "REFUSED_ENDPOINT_CLASSES", "classify_endpoint", "default_provider",
    "estimate_tokens", "load_pools", "parse_pool", "provider_for_pool",
    "select_provider",
]
