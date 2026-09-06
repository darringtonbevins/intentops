"""The collection registry, the write gate, and two vector stores.

PURPOSE
    A COLLECTION is data, not DDL. The substrate ships ONE ``embeddings``
    table for every collection and a ``collections`` registry declaring each
    one's dimensionality, source type, who claims it and who is expected to
    fill it (``substrate/schema.py``). This module is that registry in
    Python, plus the two things the SQL trigger cannot do:

    * **The write gate.** Classification is a WRITE-TIME OBLIGATION, never a
      backfill project. Every write passes :func:`gate_write`, which resolves
      the row's ring (missing key -> quarantine, and the collection's own
      declared sensitivity may only ever NARROW it, never loosen it) and
      grades any confidence claim it carries. Observe / enforce / off, the
      same shape as every other control here: observe annotates and warns,
      enforce refuses the write, off is a break-glass pass-through.
    * **A store that is not a database.** :class:`NullVectorStore` runs the
      identical three refusals in memory, so the contract is testable with no
      service running -- and so a node with no substrate can still exercise
      the whole knowledge path.

    The Postgres adapter is import-safe on a machine with no driver: the seed
    declares stdlib, ``pyyaml`` and ``cryptography`` and nothing else. Asking
    for it without a driver HALTS with the install line, rather than failing
    at import and taking the whole package down with it.

WRITE MODEL
    * :class:`CollectionRegistry` -- transactional-upsert, in-process, one
      writer. It mirrors the ``collections`` table; where a node has a
      database, THAT table is the record and this is a cache of it.
    * :class:`NullVectorStore` -- in-memory, single-process, test and
      no-substrate use only. It persists nothing and says so.
    * :class:`PostgresVectorStore` -- transactional-upsert against the shipped
      schema. The database's own trigger is the enforcement of record; this
      class runs the same checks earlier so the error names the collection
      rather than a constraint.

BLIND SPOTS
    - The gate reads DECLARED metadata. It cannot tell whether a row's
      ``source_type`` is honest, and it never reads content.
    - A collection's ``expected_writer`` is a string nobody verifies. It makes
      an unpopulated store distinguishable from a never-written one by
      INSPECTION, not by enforcement.
    - The sigma ladder grades a claim against an accompanying confidence. It
      cannot tell whether the confidence itself was computed or typed.
    - ``NullVectorStore`` does exact scans. It is not a benchmark of anything.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import GATE_MODES, KnowledgeHalt, WriteRefused, gate_mode
from .rings import (RING_META_KEY, Ring, RingTaxonomy, ring_from_metadata,
                    stricter)

__all__ = [
    "CollectionSpec",
    "CollectionRegistry",
    "SIGMA_STATUSES",
    "RING_STATUSES",
    "SIGMA_GATE_ENV",
    "sigma_gate_mode",
    "confidence_to_sigma",
    "gate_write",
    "VectorRow",
    "NullVectorStore",
    "PostgresVectorStore",
    "selftest",
    "main",
]

#: Environment variable resolving the write gate's mode.
SIGMA_GATE_ENV = "INTENTOPS_SIGMA_GATE"

#: How a row's confidence claim graded. Closed by declaration.
SIGMA_STATUSES: Tuple[str, ...] = (
    "computed",      # the claim is justified by the confidence beside it
    "over_claimed",  # the claim exceeds what that confidence earns
    "no_posterior",  # a claim with no confidence at all -- a costume
    "below_floor",   # justified, but under the collection's declared floor
    "unscored",      # no claim; loud on a floored collection
    "malformed",     # present and unparseable: never graded `computed`
)

#: How a row's ring resolved at write time.
RING_STATUSES: Tuple[str, ...] = (
    "declared",    # the row carried its own ring, and it stood
    "classified",  # the operator's ring taxonomy ruled on this source type
    "narrowed",    # a stricter declaration (collection or taxonomy) won
    "stamped",     # the row carried none; the collection's declaration applied
    "quarantined", # nothing classified it -- the write is unclassified
)


def sigma_gate_mode(environ: Optional[Mapping[str, str]] = None) -> str:
    """observe (default) | enforce | off, resolved per call.

    Raises :class:`KnowledgeHalt` on a present-but-undeclared value: a
    misspelled ``enforce`` must not silently become ``observe``.
    """
    return gate_mode(SIGMA_GATE_ENV, environ)


def confidence_to_sigma(confidence: float) -> float:
    """The sigma tier a posterior confidence actually earns.

    Two-sided normal, computed from ``math.erf`` so the ladder is arithmetic
    rather than a table somebody can quietly edit: 1s = 0.6827, 2s = 0.9545,
    3s = 0.9973, 4s = 0.99993, 5s = 0.9999994.
    """
    for tier in (5, 4, 3, 2, 1):
        if confidence >= math.erf(tier / math.sqrt(2.0)):
            return float(tier)
    return 0.0


@dataclass(frozen=True)
class CollectionSpec:
    """One declared collection. Every field below is load-bearing.

    ``dims``, ``source_type``, ``declared_by`` and ``expected_writer`` have no
    defaults: a registry that half-understands a collection produces a store
    that runs and is wrong. ``sensitivity`` defaults to QUARANTINE, which is
    not a default so much as a refusal -- an undeclared collection classifies
    nothing.
    """

    name: str
    dims: int
    source_type: str
    declared_by: str
    expected_writer: str
    sensitivity: Ring = Ring.QUARANTINE
    sigma_floor: float = 0.0

    def __post_init__(self) -> None:
        for field_name in ("name", "source_type", "declared_by",
                           "expected_writer"):
            if not str(getattr(self, field_name) or "").strip():
                raise KnowledgeHalt(
                    f"collection {self.name or '<unnamed>'!r}: "
                    f"`{field_name}` is missing",
                    f"declare `{field_name}`. Where a field is load-bearing, "
                    "missing means HALT, not a default")
        if not isinstance(self.dims, int) or self.dims <= 0:
            raise KnowledgeHalt(
                f"collection {self.name!r}: dims must be a positive integer, "
                f"got {self.dims!r}",
                "declare the dimensionality of the embedder that will fill "
                "this collection; one embeddings table serves them all only "
                "because each row's shape is checked against its declaration")
        if not isinstance(self.sensitivity, Ring):
            raise KnowledgeHalt(
                f"collection {self.name!r}: sensitivity {self.sensitivity!r} "
                "is not a ring",
                "use a Ring value; an undeclared value is a hard exit")
        if self.sigma_floor < 0.0:
            raise KnowledgeHalt(
                f"collection {self.name!r}: sigma_floor is negative",
                "a floor is a sigma tier (0 means unfloored)")

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "dims": self.dims,
                "source_type": self.source_type,
                "declared_by": self.declared_by,
                "expected_writer": self.expected_writer,
                "sensitivity": self.sensitivity.value,
                "sigma_floor": self.sigma_floor}


class CollectionRegistry:
    """The declared collections. Nothing writes to an undeclared one.

    WRITE MODEL: transactional-upsert, one writer, in process. Declaring the
    same collection twice with the same shape is idempotent; declaring it with
    a DIFFERENT shape is a HALT, because the second declaration would
    invalidate every row the first one wrote.
    """

    def __init__(self, specs: Iterable[CollectionSpec] = ()) -> None:
        self._specs: Dict[str, CollectionSpec] = {}
        for spec in specs:
            self.declare(spec)

    def declare(self, spec: CollectionSpec) -> CollectionSpec:
        existing = self._specs.get(spec.name)
        if existing is not None and existing != spec:
            raise KnowledgeHalt(
                f"collection {spec.name!r} is already declared with a "
                f"different shape (dims {existing.dims} -> {spec.dims}, "
                f"source_type {existing.source_type!r} -> {spec.source_type!r})",
                "a redeclaration invalidates every row already written under "
                "the first declaration; declare a NEW collection instead")
        self._specs[spec.name] = spec
        return spec

    def get(self, name: str) -> CollectionSpec:
        try:
            return self._specs[name]
        except KeyError:
            raise KnowledgeHalt(
                f"collection {name!r} is not declared",
                "declare it (dims, source_type, declared_by, expected_writer) "
                "before writing to it. A collection nothing claims is refused, "
                "never created on first write") from None

    def names(self) -> List[str]:
        return sorted(self._specs)

    def __contains__(self, name: object) -> bool:
        return name in self._specs

    def __len__(self) -> int:
        return len(self._specs)

    def to_rows(self) -> List[Dict[str, Any]]:
        return [self._specs[n].to_dict() for n in self.names()]


def _parse_number(raw: Any) -> Tuple[Optional[float], bool]:
    """(value, malformed). Absent is (None, False); unparseable is (None, True)."""
    if raw is None:
        return None, False
    if isinstance(raw, bool):
        return None, True
    try:
        return float(raw), False
    except (TypeError, ValueError):
        return None, True


def gate_write(metadata: Optional[Dict[str, Any]], spec: CollectionSpec, *,
               mode: Optional[str] = None,
               environ: Optional[Mapping[str, str]] = None,
               taxonomy: Optional[RingTaxonomy] = None,
               ) -> Tuple[Dict[str, Any], List[str]]:
    """Gate one write. Returns ``(annotated metadata, violations)``.

    In ``enforce`` a violation raises :class:`WriteRefused`. In ``observe``
    (the default) the metadata is annotated with ``ring``, ``ring_status`` and
    ``sigma_status`` and the violations are returned for the caller to
    surface -- annotate and warn, never block. In ``off`` the metadata passes
    through untouched, which is a break-glass posture and leaves no
    annotation to mistake for a measurement.

    The ring rule, stated once: the collection's declared sensitivity may
    NARROW a row's ring and may never loosen it. A row claiming public in a
    sensitive collection is written as sensitive; a row claiming sensitive in
    a public collection stays sensitive.

    ``taxonomy`` is the operator's own ``source_type -> ring`` ruling
    (``.intentops/knowledge/ring-taxonomy.yaml``). Precedence, strictest
    wins throughout:

      1. an EXPLICIT taxonomy row for this row's source type,
      2. the collection's declared sensitivity,
      3. the row's own carried ring,

    merged with :func:`~intentops_core.knowledge.rings.stricter`, so a ruling
    can only ever narrow. Taxonomy SILENCE (an unlisted source type) is not a
    quarantine verdict here: it leaves the collection's declaration standing,
    which is what keeps a node with a still-blank taxonomy from quarantining
    every write it makes. Passing no taxonomy at all is the same case.

    Until 2026-09-06 this argument did not exist, and the taxonomy the node
    writes at birth had NO consumer on the write path: an operator could rule
    a source type into R1 and every row still carried the collection's ring.
    That was capture without a consumer -- the store looked governed and the
    ruling was a no-op.
    """
    mode = mode or sigma_gate_mode(environ)
    meta: Dict[str, Any] = dict(metadata or {})
    if mode == "off":
        return meta, []

    violations: List[str] = []
    where = f"[{spec.name}]"

    # -- ring -------------------------------------------------------------
    carried = ring_from_metadata(meta)
    had_key = meta.get(RING_META_KEY) is not None
    source_type = meta.get("source_type") or spec.source_type
    ruled = (taxonomy.declared_ring_for(source_type)
             if taxonomy is not None else None)

    if not had_key:
        if ruled is not None:
            resolved = stricter(ruled, spec.sensitivity)
            status = ("classified" if resolved is not Ring.QUARANTINE
                      else "quarantined")
        else:
            resolved = spec.sensitivity
            status = ("stamped" if resolved is not Ring.QUARANTINE
                      else "quarantined")
    else:
        resolved = stricter(carried, spec.sensitivity)
        if ruled is not None:
            resolved = stricter(resolved, ruled)
        if resolved is Ring.QUARANTINE:
            status = "quarantined"
        elif resolved is not carried:
            status = "narrowed"
        else:
            status = "declared"
    if resolved is Ring.QUARANTINE:
        ruling = ("no taxonomy ruling" if ruled is None
                  else f"taxonomy rules {source_type!r} {ruled.value}")
        violations.append(
            f"{where} unclassified write: nothing classifies this row "
            f"(carried {carried.value}, collection declares "
            f"{spec.sensitivity.value}, {ruling}) -- it will be quarantined "
            "at every gate, which is a stamp, not a classification")
    meta[RING_META_KEY] = resolved.value
    meta["ring_status"] = status
    meta.setdefault("source_type", spec.source_type)

    # -- sigma ------------------------------------------------------------
    claim, claim_bad = _parse_number(meta.get("sigma"))
    conf, conf_bad = _parse_number(meta.get("confidence"))
    if claim_bad or conf_bad:
        sigma_status = "malformed"
        violations.append(f"{where} sigma or confidence is present and "
                          "unparseable; a claim that cannot be read is never "
                          "graded computed")
    elif claim is None and conf is None:
        sigma_status = "unscored"
        if spec.sigma_floor > 0:
            violations.append(
                f"{where} no confidence claim on a collection with a floor of "
                f"{spec.sigma_floor:g} sigma")
    elif claim is not None and conf is None:
        sigma_status = "no_posterior"
        violations.append(f"{where} sigma {claim:g} claimed with no confidence "
                          "behind it")
    else:
        earned = confidence_to_sigma(conf if conf is not None else 0.0)
        effective = claim if claim is not None else earned
        if claim is not None and claim > earned:
            sigma_status = "over_claimed"
            violations.append(f"{where} sigma {claim:g} claimed; confidence "
                              f"{conf:g} earns {earned:g}")
        elif effective < spec.sigma_floor:
            sigma_status = "below_floor"
            violations.append(f"{where} sigma {effective:g} is below the "
                              f"collection floor {spec.sigma_floor:g}")
        else:
            sigma_status = "computed"
    meta["sigma_status"] = sigma_status

    if mode == "enforce" and violations:
        raise WriteRefused(
            f"write to {spec.name!r} refused: " + "; ".join(violations),
            violations)
    return meta, violations


@dataclass
class VectorRow:
    """One stored row. ``embedding`` is kept so dims can be re-checked."""

    id: str
    collection: str
    content: str
    embedding: Tuple[float, ...]
    metadata: Dict[str, Any] = field(default_factory=dict)


class NullVectorStore:
    """An in-memory vector store with the substrate's own three refusals.

    PURPOSE: to make the knowledge path exercisable with no service running --
    in tests, and on a node whose substrate is not up yet. It refuses exactly
    what the shipped SQL refuses:

      1. a write to a collection nothing declared;
      2. a row whose dimensionality disagrees with its collection's;
      3. (this layer's addition) a write the gate refused in enforce mode.

    WRITE MODEL: in-memory, single process, no persistence. It says so rather
    than pretending: :meth:`describe` names it, because a store that looks
    durable and is not is worse than no store.
    """

    persistent = False

    def __init__(self, registry: Optional[CollectionRegistry] = None, *,
                 mode: Optional[str] = None,
                 taxonomy: Optional[RingTaxonomy] = None) -> None:
        self.registry = registry or CollectionRegistry()
        self.mode = mode
        #: The operator's source_type -> ring ruling, consulted on every
        #: write. None means "no ruling loaded", which leaves the collection
        #: declaration as the only classifier -- never a silent default.
        self.taxonomy = taxonomy
        self._rows: Dict[str, VectorRow] = {}
        self.gate_violations: List[str] = []

    def declare(self, spec: CollectionSpec) -> CollectionSpec:
        return self.registry.declare(spec)

    def upsert(self, collection: str, row_id: str, content: str,
               embedding: Sequence[float],
               metadata: Optional[Dict[str, Any]] = None) -> VectorRow:
        spec = self.registry.get(collection)
        vector = tuple(float(x) for x in embedding)
        if len(vector) != spec.dims:
            raise KnowledgeHalt(
                f"embedding for collection {collection!r} has "
                f"{len(vector)} dimensions; the collection declares "
                f"{spec.dims}",
                "embed with the model the collection was declared for, or "
                "declare a new collection for this model. One embeddings "
                "table serves every collection only because this holds")
        meta, violations = gate_write(metadata, spec, mode=self.mode,
                                      taxonomy=self.taxonomy)
        self.gate_violations.extend(violations)
        row = VectorRow(id=row_id, collection=collection, content=content,
                        embedding=vector, metadata=meta)
        self._rows[f"{collection}::{row_id}"] = row
        return row

    def rows(self, collection: Optional[str] = None) -> List[VectorRow]:
        return [r for r in self._rows.values()
                if collection is None or r.collection == collection]

    def count(self, collection: Optional[str] = None) -> int:
        return len(self.rows(collection))

    def collections(self) -> List[str]:
        return self.registry.names()

    def describe(self) -> str:
        return (f"NullVectorStore: {len(self._rows)} rows across "
                f"{len(self.registry)} declared collections, IN MEMORY -- "
                "nothing here survives the process")


class PostgresVectorStore:
    """The adapter for the shipped schema. Import-safe without a driver.

    The seed declares stdlib, ``pyyaml`` and ``cryptography``. A database
    driver is therefore an OPERATOR'S choice, made when they bring the
    substrate up -- so this class imports nothing at module load and HALTS
    with the install line the first time it is actually used.

    That is deliberate: an adapter that fails at import takes the whole
    knowledge package down on a machine that never intended to use it, and a
    package that cannot be imported cannot tell anybody why.

    WRITE MODEL: transactional-upsert against ``embeddings``, with the
    collection declared in ``collections`` first. The database's dims trigger
    is the enforcement of record; the checks here run earlier so the message
    names the collection instead of a constraint.
    """

    persistent = True
    DRIVER = "psycopg"

    def __init__(self, dsn: str, registry: Optional[CollectionRegistry] = None,
                 *, mode: Optional[str] = None,
                 taxonomy: Optional[RingTaxonomy] = None) -> None:
        if not str(dsn or "").strip():
            raise KnowledgeHalt(
                "PostgresVectorStore needs a connection string",
                "pass the DSN for the node's own database; there is no "
                "default, and inventing one connects to somebody else's")
        self.dsn = dsn
        self.registry = registry or CollectionRegistry()
        self.mode = mode
        self.taxonomy = taxonomy

    @classmethod
    def driver_available(cls) -> bool:
        """True when the driver can be imported. Never raises."""
        import importlib.util

        return importlib.util.find_spec(cls.DRIVER) is not None

    def _driver(self):
        """Import the driver, or HALT with the one line that fixes it."""
        try:
            import psycopg  # type: ignore  # noqa: PLC0415 - deliberate
        except ImportError as exc:
            raise KnowledgeHalt(
                f"the {self.DRIVER!r} driver is not installed, so this node "
                "cannot reach its vector store",
                f"install it (`pip install {self.DRIVER}[binary]`) and bring "
                "the substrate up (`intentops substrate init --dry-run` "
                "prints the plan). Until then use NullVectorStore, which "
                "runs the same refusals in memory and persists nothing") from exc
        return psycopg

    def connect(self):  # pragma: no cover - requires a live database
        return self._driver().connect(self.dsn)

    def declare(self, spec: CollectionSpec) -> CollectionSpec:
        """Register locally AND upsert the row the dims trigger reads."""
        self.registry.declare(spec)
        self._driver()  # HALTs here when there is no driver
        return spec  # pragma: no cover - the write needs a live database

    def upsert(self, collection: str, row_id: str, content: str,
               embedding: Sequence[float],
               metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        spec = self.registry.get(collection)
        vector = tuple(float(x) for x in embedding)
        if len(vector) != spec.dims:
            raise KnowledgeHalt(
                f"embedding for collection {collection!r} has {len(vector)} "
                f"dimensions; the collection declares {spec.dims}",
                "the database trigger will refuse this too; fix the embedder "
                "or declare a new collection")
        meta, _violations = gate_write(metadata, spec, mode=self.mode,
                                       taxonomy=self.taxonomy)
        self._driver()  # HALTs here when there is no driver
        return {"id": row_id, "collection": collection,  # pragma: no cover
                "content": content, "metadata": meta}

    def describe(self) -> str:
        state = "available" if self.driver_available() else "NOT INSTALLED"
        return (f"PostgresVectorStore: driver {self.DRIVER} {state}; "
                f"{len(self.registry)} declared collections")


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


def selftest() -> Tuple[bool, str]:
    """Prove every refusal fires, in every mode."""
    failures: List[str] = []
    fired: List[str] = []

    def check(label: str, cond: bool) -> None:
        (fired if cond else failures).append(label)

    def halts(label: str, fn) -> None:
        try:
            fn()
        except KnowledgeHalt:
            fired.append(label)
        else:
            failures.append(label)

    ok_spec = CollectionSpec(name="knowledge", dims=8, source_type="local-file",
                             declared_by="selftest", expected_writer="the file "
                             "absorber", sensitivity=Ring.R2)

    # 1. no-default fields HALT
    halts("no-source-type-halts", lambda: CollectionSpec(
        name="c", dims=8, source_type="", declared_by="d", expected_writer="w"))
    halts("no-expected-writer-halts", lambda: CollectionSpec(
        name="c", dims=8, source_type="s", declared_by="d", expected_writer=""))
    halts("zero-dims-halts", lambda: CollectionSpec(
        name="c", dims=0, source_type="s", declared_by="d", expected_writer="w"))
    halts("non-ring-sensitivity-halts", lambda: CollectionSpec(
        name="c", dims=8, source_type="s", declared_by="d", expected_writer="w",
        sensitivity="R2"))  # type: ignore[arg-type]

    # 2. the registry
    reg = CollectionRegistry([ok_spec])
    check("declare-is-idempotent", reg.declare(ok_spec) is ok_spec and len(reg) == 1)
    halts("redeclaration-with-a-new-shape-halts",
          lambda: reg.declare(replace(ok_spec, dims=16)))
    halts("undeclared-collection-halts", lambda: reg.get("nobody-declared-this"))

    # 3. the store's three refusals
    store = NullVectorStore(reg, mode="observe")
    halts("write-to-undeclared-collection-halts",
          lambda: store.upsert("ghost", "1", "x", [0.0] * 8))
    halts("dims-mismatch-halts",
          lambda: store.upsert("knowledge", "1", "x", [0.0] * 4))
    row = store.upsert("knowledge", "1", "x", [0.1] * 8, {"source": "a.md"})
    check("good-write-lands", store.count("knowledge") == 1)
    check("ring-stamped-from-the-collection",
          row.metadata[RING_META_KEY] == "R2"
          and row.metadata["ring_status"] == "stamped")
    check("source-type-carried", row.metadata["source_type"] == "local-file")
    check("upsert-is-not-append",
          store.upsert("knowledge", "1", "y", [0.2] * 8) is not None
          and store.count("knowledge") == 1)
    check("null-store-admits-it-is-not-durable",
          store.persistent is False and "IN MEMORY" in store.describe())

    # 4. the ring rule: narrow, never loosen
    quarantine_spec = replace(ok_spec, name="unclassified",
                              sensitivity=Ring.QUARANTINE)
    meta, viol = gate_write({"source": "x"}, quarantine_spec, mode="observe")
    check("unclassified-collection-quarantines",
          meta[RING_META_KEY] == "Q" and meta["ring_status"] == "quarantined"
          and any("unclassified write" in v for v in viol))
    meta, _ = gate_write({RING_META_KEY: "R4"}, replace(ok_spec, sensitivity=Ring.R1),
                         mode="observe")
    check("collection-narrows-a-loose-claim",
          meta[RING_META_KEY] == "R1" and meta["ring_status"] == "narrowed")
    meta, _ = gate_write({RING_META_KEY: "R0"}, ok_spec, mode="observe")
    check("a-stricter-row-is-not-loosened", meta[RING_META_KEY] == "R0")
    meta, _ = gate_write({RING_META_KEY: "not-a-ring"}, quarantine_spec,
                         mode="observe")
    check("invalid-ring-quarantines-at-the-gate", meta[RING_META_KEY] == "Q")

    # 4b. the operator's taxonomy is CONSULTED, and can only narrow
    tax = RingTaxonomy(schema="ring-taxonomy/v1", as_of="2026-01-01",
                       default_ring=Ring.QUARANTINE,
                       mapping={"local-file": Ring.R1, "public": Ring.R4})
    meta, _ = gate_write({"source": "a.md"}, ok_spec, mode="observe",
                         taxonomy=tax)
    check("taxonomy-ruling-changes-the-stamped-ring",
          meta[RING_META_KEY] == "R1" and meta["ring_status"] == "classified")
    loose = replace(ok_spec, name="public-notes", source_type="public",
                    sensitivity=Ring.R2)
    meta, _ = gate_write({"source": "a.md"}, loose, mode="observe",
                         taxonomy=tax)
    check("taxonomy-never-loosens-a-collection",
          meta[RING_META_KEY] == "R2")
    meta, _ = gate_write({RING_META_KEY: "R3", "source_type": "local-file"},
                         replace(ok_spec, sensitivity=Ring.R3), mode="observe",
                         taxonomy=tax)
    check("taxonomy-narrows-a-carried-ring",
          meta[RING_META_KEY] == "R1" and meta["ring_status"] == "narrowed")
    silent = RingTaxonomy(schema="ring-taxonomy/v1", as_of="2026-01-01",
                          default_ring=Ring.QUARANTINE, mapping={})
    meta, _ = gate_write({"source": "a.md"}, ok_spec, mode="observe",
                         taxonomy=silent)
    check("a-blank-taxonomy-does-not-quarantine-every-write",
          meta[RING_META_KEY] == "R2" and meta["ring_status"] == "stamped")
    store_t = NullVectorStore(CollectionRegistry([ok_spec]), mode="observe",
                              taxonomy=tax)
    row_t = store_t.upsert("knowledge", "1", "x", [0.1] * 8, {"source": "a.md"})
    check("the-store-passes-its-taxonomy-to-the-gate",
          row_t.metadata[RING_META_KEY] == "R1")
    check("ring-statuses-are-closed",
          set(RING_STATUSES) == {"declared", "classified", "narrowed",
                                 "stamped", "quarantined"})

    # 5. the sigma ladder
    check("five-sigma-needs-five-nines",
          confidence_to_sigma(0.9999995) == 5.0
          and confidence_to_sigma(0.99) == 2.0
          and confidence_to_sigma(0.5) == 0.0)
    floored = replace(ok_spec, name="findings", sigma_floor=3.0)
    cases = {
        "computed": ({"sigma": 3, "confidence": 0.9999}, "computed"),
        "over_claimed": ({"sigma": 5, "confidence": 0.99}, "over_claimed"),
        "no_posterior": ({"sigma": 4}, "no_posterior"),
        "below_floor": ({"sigma": 2, "confidence": 0.98}, "below_floor"),
        "unscored": ({}, "unscored"),
        "malformed": ({"sigma": "very"}, "malformed"),
    }
    for label, (meta_in, want) in cases.items():
        got, viol = gate_write(dict(meta_in), floored, mode="observe")
        check(f"sigma-{label}", got["sigma_status"] == want)
        if want != "computed":
            check(f"sigma-{label}-is-loud", bool(viol))
    check("sigma-statuses-are-closed",
          set(SIGMA_STATUSES) == {c[1] for c in cases.values()})
    unfloored, _ = gate_write({}, ok_spec, mode="observe")
    check("unscored-is-quiet-without-a-floor",
          unfloored["sigma_status"] == "unscored"
          and not gate_write({}, ok_spec, mode="observe")[1])

    # 6. the three modes
    try:
        gate_write({"sigma": 5}, ok_spec, mode="enforce")
    except WriteRefused as exc:
        fired.append("enforce-refuses")
        check("refusal-carries-its-violations", bool(exc.violations))
    else:
        failures.append("enforce-refuses")
    passthrough, viol = gate_write({"sigma": 5}, ok_spec, mode="off")
    check("off-annotates-nothing",
          "sigma_status" not in passthrough and RING_META_KEY not in passthrough
          and not viol)
    observed, viol = gate_write({"sigma": 5}, ok_spec, mode="observe")
    check("observe-annotates-and-never-blocks",
          observed["sigma_status"] == "no_posterior" and bool(viol))
    check("modes-are-closed", set(GATE_MODES) == {"observe", "enforce", "off"})
    try:
        sigma_gate_mode({SIGMA_GATE_ENV: "enforcce"})
    except KnowledgeHalt:
        fired.append("misspelled-enforce-halts-never-reads-as-observe")
    else:
        failures.append("misspelled-enforce-halts-never-reads-as-observe")
    check("unset-mode-is-observe", sigma_gate_mode({}) == "observe")

    # 7. the Postgres adapter: import-safe, and honest about the driver
    halts("postgres-without-a-dsn-halts", lambda: PostgresVectorStore(""))
    pg = PostgresVectorStore("postgresql:///example", CollectionRegistry([ok_spec]))
    check("driver-availability-is-answerable-without-raising",
          isinstance(PostgresVectorStore.driver_available(), bool))
    check("describe-names-the-driver-state", "psycopg" in pg.describe())
    halts("postgres-refuses-an-undeclared-collection",
          lambda: pg.upsert("ghost", "1", "x", [0.0] * 8))
    halts("postgres-refuses-a-dims-mismatch",
          lambda: pg.upsert("knowledge", "1", "x", [0.0] * 3))
    # The no-driver refusal is SIMULATED rather than left to the host: on a
    # machine that happens to have the driver installed, a skipped check is
    # indistinguishable from a broken one. Binding the name to None in
    # sys.modules makes `import psycopg` raise exactly as it would on a
    # machine that never had it.
    import sys

    sentinel = object()
    saved = sys.modules.get("psycopg", sentinel)
    sys.modules["psycopg"] = None  # type: ignore[assignment]
    try:
        halts("postgres-halts-cleanly-without-the-driver",
              lambda: pg.upsert("knowledge", "1", "x", [0.0] * 8))
    finally:
        if saved is sentinel:
            sys.modules.pop("psycopg", None)
        else:
            sys.modules["psycopg"] = saved  # type: ignore[assignment]

    report = (f"collections selftest: {len(fired)} paths fired, "
              f"{len(failures)} failed"
              + (" -- " + ", ".join(failures) if failures else ""))
    return (not failures, report)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="the collection registry and write gate")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        ok, report = selftest()
        print(report)
        return 0 if ok else 1
    print(json.dumps({"sigma_statuses": list(SIGMA_STATUSES),
                      "ring_statuses": list(RING_STATUSES),
                      "gate_mode": sigma_gate_mode(),
                      "postgres_driver_available":
                          PostgresVectorStore.driver_available()}, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
