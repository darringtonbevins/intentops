"""The embedding seam -- provider-neutral, and null by default.

PURPOSE
    Rung 3 of the ladder (``Code -> Script -> local embeddings -> local
    inference -> service inference``) is as far right as the seed goes, and
    even that is a SEAM rather than a dependency: an :class:`Embedder` is an
    HTTP endpoint the operator declared in their own estate, or it is
    :class:`NullEmbedder`, which refuses.

    Three rules hold this module's shape:

    1. **No vendor SDK, ever.** One ``urllib`` POST against an endpoint the
       operator names. A client library would put a provider's name in the
       framework's dependency list and its assumptions in the framework's
       code.
    2. **A null embedder REFUSES; it does not return zeros.** A zero vector is
       a value produced without evidence wearing a measurement's costume: it
       would embed, store, rank, and retrieve, and every result would be
       noise the store could not distinguish from signal.
    3. **Dimensionality is checked on the way back.** An endpoint that quietly
       changes model returns vectors of a different width, and the only place
       that is cheap to catch is here -- before the row reaches a collection
       whose declaration it silently contradicts.

WRITE MODEL
    None. This module holds no store and writes no file.

BLIND SPOTS
    - It measures shape, never quality. A endpoint returning well-formed
      nonsense of the right width passes every check here.
    - No retry, no backoff, no batching policy. Those are a caller's
      decisions, and burying them here would hide them.
    - The estate reader takes the endpoint on trust. Whether that endpoint is
      local, and therefore whether content of a given ring may cross to it, is
      the ring gate's question and is deliberately not answered here.
"""

from __future__ import annotations

import argparse
import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib import error as urlerror
from urllib import request as urlrequest

from . import KnowledgeError, KnowledgeHalt

__all__ = [
    "Embedder",
    "NullEmbedder",
    "HttpEmbedder",
    "EmbedderUnavailable",
    "embedder_from_resources",
    "selftest",
    "main",
]


class EmbedderUnavailable(KnowledgeHalt):
    """No embedder is configured, or the configured one cannot be reached."""


class Embedder(ABC):
    """A thing that turns text into vectors of a declared width."""

    #: The width every vector this embedder returns must have. Declared, not
    #: discovered: a collection is declared against this number.
    dims: int = 0

    @abstractmethod
    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        """One vector per input, in order. Raise rather than return junk."""

    @abstractmethod
    def describe(self) -> str:
        """One line naming what this is and where it goes."""

    def embed_one(self, text: str) -> List[float]:
        return self.embed([text])[0]


class NullEmbedder(Embedder):
    """The embedder a node has before its operator declares one.

    It refuses. That is the whole implementation, and it is the correct one:
    returning zeros would let a node build an index of vectors that carry no
    information, and nothing downstream could tell that index from a real
    one. A refusal is visible; a costume is not.
    """

    dims = 0

    def __init__(self, reason: str = "no embedder is declared in this estate"
                 ) -> None:
        self.reason = reason

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        raise EmbedderUnavailable(
            f"cannot embed {len(list(texts))} text(s): {self.reason}",
            "declare an embedding endpoint in estate/RESOURCES.yaml (kind: "
            "model-pool, with `endpoint` and `dims`) and pass it to "
            "embedder_from_resources. A null embedder refuses rather than "
            "returning zero vectors, because a zero vector indexes, ranks and "
            "retrieves exactly like a real one")

    def describe(self) -> str:
        return f"NullEmbedder: refuses every call ({self.reason})"


class HttpEmbedder(Embedder):
    """A POST to an endpoint the operator declared. Provider-neutral.

    Accepts either of the two payload shapes in common use --
    ``{"embeddings": [[...], ...]}`` and ``{"data": [{"embedding": [...]}]}``
    -- and HALTS on anything else rather than guessing. A reader that
    substitutes a default for a field it did not find has left that field out
    of the population; here that would mean silently embedding nothing.
    """

    def __init__(self, endpoint: str, dims: int, *, model: str = "",
                 timeout: float = 30.0,
                 opener=None) -> None:
        if not str(endpoint or "").strip():
            raise KnowledgeHalt(
                "HttpEmbedder needs an endpoint",
                "declare `endpoint` on the estate resource; there is no "
                "default, and inventing one sends content somewhere nobody "
                "chose")
        if not isinstance(dims, int) or dims <= 0:
            raise KnowledgeHalt(
                f"HttpEmbedder needs a positive `dims`, got {dims!r}",
                "declare the width this endpoint returns. It is what a "
                "collection is declared against, so missing means HALT")
        self.endpoint = endpoint
        self.dims = dims
        self.model = model
        self.timeout = timeout
        self._opener = opener or urlrequest.urlopen

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        items = [str(t) for t in texts]
        if not items:
            return []
        payload: Dict[str, Any] = {"input": items}
        if self.model:
            payload["model"] = self.model
        req = urlrequest.Request(
            self.endpoint, method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        try:
            with self._opener(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
        except (urlerror.URLError, OSError, ValueError) as exc:
            raise EmbedderUnavailable(
                f"embedding endpoint unreachable: {exc}",
                "check the endpoint is up and reachable from this process. "
                "An unreachable embedder is a loud failure on purpose: the "
                "alternative is a corpus that silently stops being "
                "searchable") from exc
        vectors = self._parse(body)
        if len(vectors) != len(items):
            raise KnowledgeError(
                f"embedding endpoint returned {len(vectors)} vectors for "
                f"{len(items)} inputs")
        for vector in vectors:
            if len(vector) != self.dims:
                raise KnowledgeError(
                    f"embedding endpoint returned {len(vector)} dimensions; "
                    f"this embedder is declared at {self.dims}. An endpoint "
                    "that changed model is the usual cause, and a collection "
                    "declared against the old width would refuse these rows "
                    "anyway")
        return vectors

    def _parse(self, body: str) -> List[List[float]]:
        try:
            doc = json.loads(body)
        except json.JSONDecodeError as exc:
            raise KnowledgeError(
                f"embedding endpoint returned non-JSON: {body[:120]!r}") from exc
        if isinstance(doc, Mapping) and isinstance(doc.get("embeddings"), list):
            raw = doc["embeddings"]
        elif isinstance(doc, Mapping) and isinstance(doc.get("data"), list):
            raw = [item.get("embedding") for item in doc["data"]
                   if isinstance(item, Mapping)]
        else:
            raise KnowledgeError(
                "embedding endpoint returned an unrecognised payload shape; "
                "expected `embeddings: [[...]]` or `data: [{embedding: "
                f"[...]}}]`, got keys {sorted(doc) if isinstance(doc, Mapping) else type(doc).__name__}")
        vectors: List[List[float]] = []
        for item in raw:
            if not isinstance(item, (list, tuple)) or not item:
                raise KnowledgeError(
                    "embedding endpoint returned an empty or non-list vector")
            vectors.append([float(x) for x in item])
        return vectors

    def describe(self) -> str:
        model = f", model {self.model}" if self.model else ""
        return f"HttpEmbedder: {self.dims}d -> {self.endpoint}{model}"


def embedder_from_resources(resources: Path | str | Mapping[str, Any], *,
                            resource_id: Optional[str] = None,
                            required: bool = False) -> Embedder:
    """Build the embedder an estate declares, or a :class:`NullEmbedder`.

    The estate's ``RESOURCES.yaml`` ships with ``entries: []`` -- a node with
    no declared embedder is the normal case at birth, and it gets a null that
    refuses, not a HALT, unless ``required=True``.

    Once an entry IS named, every field it needs is load-bearing: a resource
    of the wrong kind, a missing ``endpoint``, a missing or non-positive
    ``dims`` all HALT. A half-understood resource row produces an embedder
    that runs and writes vectors nothing can use.
    """
    import yaml

    if isinstance(resources, Mapping):
        doc: Any = resources
        where = "<mapping>"
    else:
        path = Path(resources)
        where = str(path)
        if not path.is_file():
            raise KnowledgeHalt(
                f"estate resources not found: {path}",
                "estate/RESOURCES.yaml ships with `entries: []`; a MISSING "
                "file is a halt, an empty one is a node that has nothing yet")
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = doc.get("entries") or []
    if not isinstance(entries, list):
        raise KnowledgeHalt(
            f"estate resources `entries` is not a list: {where}",
            "entries is a list; an empty estate is `entries: []`")

    if resource_id is None:
        candidates = [e for e in entries if isinstance(e, Mapping)
                      and e.get("kind") == "model-pool" and "endpoint" in e]
        if len(candidates) > 1:
            raise KnowledgeHalt(
                f"{len(candidates)} model-pool resources declare an endpoint "
                f"in {where}",
                "name which one with resource_id=...; picking one for you "
                "would be a routing decision nobody made")
        if not candidates:
            if required:
                raise KnowledgeHalt(
                    f"no embedding resource is declared in {where}",
                    "add a `kind: model-pool` entry carrying `endpoint` and "
                    "`dims`")
            return NullEmbedder(f"no model-pool resource in {where}")
        entry = candidates[0]
    else:
        matches = [e for e in entries
                   if isinstance(e, Mapping) and e.get("id") == resource_id]
        if not matches:
            raise KnowledgeHalt(
                f"estate resource {resource_id!r} is not declared in {where}",
                "declare it, or name a resource that exists. A reader that "
                "substitutes a default for a resource it could not find has "
                "left that resource out of the population")
        entry = matches[0]

    if entry.get("kind") != "model-pool":
        raise KnowledgeHalt(
            f"estate resource {entry.get('id')!r} is kind "
            f"{entry.get('kind')!r}, not model-pool",
            "an embedder is drawn from a model-pool resource; using another "
            "kind would mean spending a budget nobody allocated to this")
    endpoint = entry.get("endpoint")
    dims = entry.get("dims")
    missing = [name for name, value in (("endpoint", endpoint), ("dims", dims))
               if value in (None, "")]
    if missing:
        raise KnowledgeHalt(
            f"estate resource {entry.get('id')!r} is missing "
            f"{', '.join(missing)}",
            "an embedding resource declares `endpoint` and `dims`. Where a "
            "field is load-bearing, missing means HALT, not a default")
    try:
        dims_int = int(dims)
    except (TypeError, ValueError):
        raise KnowledgeHalt(
            f"estate resource {entry.get('id')!r} has a non-numeric `dims`: "
            f"{dims!r}", "dims is the integer width the endpoint returns") from None
    return HttpEmbedder(str(endpoint), dims_int, model=str(entry.get("model") or ""))


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


def _fake_opener(payload: Any, *, status_error: Optional[Exception] = None):
    """An opener returning a canned body, so the selftest needs no network."""
    import io
    from contextlib import contextmanager

    @contextmanager
    def opener(_req, timeout=None):  # noqa: ANN001 - test double
        if status_error is not None:
            raise status_error
        body = payload if isinstance(payload, str) else json.dumps(payload)
        yield io.BytesIO(body.encode("utf-8"))

    return opener


def selftest() -> Tuple[bool, str]:
    """Prove the null refuses, both payload shapes parse, and every HALT fires."""
    import tempfile

    failures: List[str] = []
    fired: List[str] = []

    def check(label: str, cond: bool) -> None:
        (fired if cond else failures).append(label)

    def raises(label: str, exc_type, fn) -> None:
        try:
            fn()
        except exc_type:
            fired.append(label)
        else:
            failures.append(label)

    # 1. the null refuses rather than returning zeros
    null = NullEmbedder()
    raises("null-embedder-refuses", EmbedderUnavailable,
           lambda: null.embed(["anything"]))
    check("null-embedder-declares-no-width", null.dims == 0)
    check("null-embedder-says-what-it-is", "refuses" in null.describe())

    # 2. construction HALTs on a missing endpoint or width
    raises("no-endpoint-halts", KnowledgeHalt, lambda: HttpEmbedder("", 8))
    raises("zero-dims-halts", KnowledgeHalt,
           lambda: HttpEmbedder("http://example.invalid/embed", 0))

    # 3. both payload shapes, no network
    vec = [0.1] * 4
    flat = HttpEmbedder("http://example.invalid/embed", 4,
                        opener=_fake_opener({"embeddings": [vec]}))
    check("embeddings-shape-parses", flat.embed(["a"]) == [vec])
    nested = HttpEmbedder("http://example.invalid/embed", 4,
                          opener=_fake_opener({"data": [{"embedding": vec}]}))
    check("data-shape-parses", nested.embed(["a"]) == [vec])
    check("empty-input-is-an-empty-answer", flat.embed([]) == [])

    # 4. every wrong answer is loud
    raises("unknown-payload-shape-raises", KnowledgeError,
           lambda: HttpEmbedder("http://example.invalid/e", 4,
                                opener=_fake_opener({"result": [vec]})).embed(["a"]))
    raises("non-json-raises", KnowledgeError,
           lambda: HttpEmbedder("http://example.invalid/e", 4,
                                opener=_fake_opener("not json")).embed(["a"]))
    raises("wrong-width-raises", KnowledgeError,
           lambda: HttpEmbedder("http://example.invalid/e", 8,
                                opener=_fake_opener({"embeddings": [vec]})).embed(["a"]))
    raises("count-mismatch-raises", KnowledgeError,
           lambda: HttpEmbedder("http://example.invalid/e", 4,
                                opener=_fake_opener({"embeddings": [vec]})
                                ).embed(["a", "b"]))
    raises("unreachable-endpoint-is-unavailable", EmbedderUnavailable,
           lambda: HttpEmbedder(
               "http://example.invalid/e", 4,
               opener=_fake_opener(None, status_error=urlerror.URLError("down"))
           ).embed(["a"]))

    # 5. the estate reader
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        blank = root / "RESOURCES.yaml"
        blank.write_text("schema: estate-resources/v1\nas_of: \"2026-01-01\"\n"
                         "entries: []\n", encoding="utf-8")
        check("a-blank-estate-gets-a-null-embedder",
              isinstance(embedder_from_resources(blank), NullEmbedder))
        raises("a-blank-estate-halts-when-an-embedder-is-required",
               KnowledgeHalt,
               lambda: embedder_from_resources(blank, required=True))
        raises("a-missing-resources-file-halts", KnowledgeHalt,
               lambda: embedder_from_resources(root / "nope.yaml"))

    doc = {"entries": [{"id": "local-embed", "kind": "model-pool",
                        "endpoint": "http://127.0.0.1:1/embed", "dims": 16}]}
    emb = embedder_from_resources(doc)
    check("a-declared-endpoint-builds",
          isinstance(emb, HttpEmbedder) and emb.dims == 16)
    check("named-resource-builds",
          embedder_from_resources(doc, resource_id="local-embed").dims == 16)
    raises("an-unknown-resource-id-halts", KnowledgeHalt,
           lambda: embedder_from_resources(doc, resource_id="ghost"))
    raises("a-wrong-kind-halts", KnowledgeHalt,
           lambda: embedder_from_resources(
               {"entries": [{"id": "s", "kind": "storage",
                             "endpoint": "http://127.0.0.1:1/e", "dims": 4}]},
               resource_id="s"))
    for missing in ("endpoint", "dims"):
        entry = {"id": "e", "kind": "model-pool",
                 "endpoint": "http://127.0.0.1:1/e", "dims": 4}
        entry.pop(missing)
        raises(f"missing-{missing}-halts", KnowledgeHalt,
               lambda e=entry: embedder_from_resources({"entries": [e]},
                                                       resource_id="e"))
    raises("non-numeric-dims-halts", KnowledgeHalt,
           lambda: embedder_from_resources(
               {"entries": [{"id": "e", "kind": "model-pool",
                             "endpoint": "http://127.0.0.1:1/e", "dims": "wide"}]},
               resource_id="e"))
    raises("two-candidate-endpoints-halt-rather-than-picking", KnowledgeHalt,
           lambda: embedder_from_resources({"entries": [
               {"id": "a", "kind": "model-pool", "endpoint": "http://127.0.0.1:1/a",
                "dims": 4},
               {"id": "b", "kind": "model-pool", "endpoint": "http://127.0.0.1:1/b",
                "dims": 4}]}))

    report = (f"embedder selftest: {len(fired)} paths fired, "
              f"{len(failures)} failed"
              + (" -- " + ", ".join(failures) if failures else ""))
    return (not failures, report)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="the embedding seam")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--resources", help="estate RESOURCES.yaml to read")
    args = ap.parse_args(argv)
    if args.selftest:
        ok, report = selftest()
        print(report)
        return 0 if ok else 1
    if args.resources:
        print(embedder_from_resources(args.resources).describe())
        return 0
    print(NullEmbedder().describe())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
