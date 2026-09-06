"""The knowledge layer: classification, absorption, collections, embedding, coverage.

PURPOSE
    Six properties this layer exists to hold, each with a test that fails when
    it stops holding:

    1. A missing ring key resolves to QUARANTINE, never to a ring.
    2. Every closed vocabulary HALTS on an undeclared value rather than
       defaulting to everything-applies.
    3. An absorber's refusal stays in the denominator -- a run that drops its
       failures reports a better number the worse it is doing.
    4. Coverage posture moves the way it is supposed to, and DEGRADED outranks
       an improvement.
    5. A node with no embedder REFUSES rather than returning zero vectors.
    6. The Postgres adapter is import-safe and HALTS with a remedy on a
       machine with no driver.

NO LIVE SERVICES
    The embedder tests run a real HTTP server, in-process, on an ephemeral
    loopback port, and shut it down again. Everything else is temp dirs and
    in-memory stores. Nothing here needs a database, a network, or a model.
"""

from __future__ import annotations

import json
import sys
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from intentops_core.knowledge import KnowledgeHalt, WriteRefused
from intentops_core.knowledge import absorber as absorber_mod
from intentops_core.knowledge import collections as collections_mod
from intentops_core.knowledge import coverage as coverage_mod
from intentops_core.knowledge import embedder as embedder_mod
from intentops_core.knowledge import rings as rings_mod
from intentops_core.knowledge.absorber import (AbsorptionResult, FileAbsorber,
                                               advance_watermark, summarise)
from intentops_core.knowledge.collections import (CollectionRegistry,
                                                  CollectionSpec,
                                                  NullVectorStore,
                                                  PostgresVectorStore,
                                                  gate_write)
from intentops_core.knowledge.coverage import (append_reading, corpus_coverage,
                                               load_history, posture,
                                               ring_coverage)
from intentops_core.knowledge.embedder import (EmbedderUnavailable,
                                               HttpEmbedder, NullEmbedder,
                                               embedder_from_resources)
from intentops_core.knowledge.rings import (RING_META_KEY, Ring, SystemExposure,
                                            load_taxonomy, ring_from_metadata,
                                            resolve)

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# every module's own selftest is a test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module", [
    rings_mod, absorber_mod, collections_mod, embedder_mod, coverage_mod,
])
def test_every_instrument_selftest_passes(module):
    ok, report = module.selftest()
    assert ok, report
    assert "0 failed" in report


# ---------------------------------------------------------------------------
# 1. the quarantine default
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("meta", [
    None, {}, {"source_type": "anything"}, {RING_META_KEY: None},
    {RING_META_KEY: ""}, {RING_META_KEY: "R9"}, {RING_META_KEY: "ring-2"},
    {RING_META_KEY: 2}, {RING_META_KEY: ["R2"]},
])
def test_anything_but_a_valid_ring_resolves_to_quarantine(meta):
    assert ring_from_metadata(meta) is Ring.QUARANTINE


def test_a_declared_ring_survives_and_is_not_tightened_by_a_local_read():
    assert ring_from_metadata({RING_META_KEY: "R3"}) is Ring.R3
    assert resolve(Ring.R4, SystemExposure.LOCAL_ONLY) is Ring.R4


def test_content_too_sensitive_for_an_exposure_quarantines_rather_than_loosening():
    assert resolve(Ring.R0, SystemExposure.THIRD_PARTY_MODEL) is Ring.QUARANTINE
    assert resolve(Ring.R2, SystemExposure.IN_FAMILY_MODEL) is Ring.R2
    assert resolve(Ring.QUARANTINE, SystemExposure.LOCAL_ONLY) is Ring.QUARANTINE


def test_the_shipped_template_is_empty_valid_and_quarantines_everything():
    tax = load_taxonomy(REPO_ROOT / "config" / "ring-taxonomy.template.yaml")
    assert tax.mapping == {}, "the template ships estate rows"
    assert tax.default_ring is Ring.QUARANTINE
    assert tax.ring_for("anything-at-all") is Ring.QUARANTINE


# ---------------------------------------------------------------------------
# 2. closed vocabularies HALT
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("body", [
    "schema: ring-taxonomy/v1\nas_of: \"2026-01-01\"\ndefault_ring: PUBLIC\nmapping: {}\n",
    "schema: ring-taxonomy/v1\nas_of: \"2026-01-01\"\ndefault_ring: Q\n"
    "mapping:\n  a-source: TOP-SECRET\n",
    "schema: ring-taxonomy/v1\nas_of: \"2026-01-01\"\nmapping: {}\n",
    "schema: ring-taxonomy/v1\ndefault_ring: Q\nmapping: {}\n",
    "as_of: \"2026-01-01\"\ndefault_ring: Q\nmapping: {}\n",
    "mapping: [:\n",
    "- not\n- a mapping\n",
])
def test_a_taxonomy_the_loader_half_understands_halts(tmp_path, body):
    path = tmp_path / "ring-taxonomy.yaml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(KnowledgeHalt):
        load_taxonomy(path)


def test_a_missing_taxonomy_halts_rather_than_reading_as_empty(tmp_path):
    with pytest.raises(KnowledgeHalt) as exc:
        load_taxonomy(tmp_path / "absent.yaml")
    assert exc.value.remedy


def test_an_undeclared_indexing_status_halts():
    with pytest.raises(KnowledgeHalt):
        AbsorptionResult(platform="p", source="s", indexing_status="probably-ok")


def test_an_absorber_without_a_source_type_halts(tmp_path):
    class NoSourceType(FileAbsorber):
        SOURCE_TYPE = ""

    with pytest.raises(KnowledgeHalt):
        NoSourceType(tmp_path)


def test_an_absorber_declaring_an_unknown_depth_halts(tmp_path):
    class Deep(FileAbsorber):
        ECOSYSTEM_RINGS = ("artifact", "the-whole-internet")

    with pytest.raises(KnowledgeHalt):
        Deep(tmp_path)


@pytest.mark.parametrize("kwargs", [
    {"source_type": ""}, {"declared_by": ""}, {"expected_writer": ""},
    {"dims": 0}, {"dims": -1}, {"dims": "768"},
])
def test_a_collection_missing_a_load_bearing_field_halts(kwargs):
    base = dict(name="c", dims=8, source_type="s", declared_by="d",
                expected_writer="w")
    base.update(kwargs)
    with pytest.raises(KnowledgeHalt):
        CollectionSpec(**base)


def test_an_undeclared_posture_is_refused_by_the_ledger(tmp_path):
    with pytest.raises(ValueError):
        append_reading({}, "PROBABLY-FINE", tmp_path / "ledger.jsonl")


# ---------------------------------------------------------------------------
# 3. an absorber's refusal stays in the denominator
# ---------------------------------------------------------------------------


@pytest.fixture()
def corpus(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "one.md").write_text("alpha beta\n", encoding="utf-8")
    (root / "two.md").write_text("gamma\n", encoding="utf-8")
    (root / "picture.png").write_text("not text\n", encoding="utf-8")
    return root


def test_every_attempt_is_counted_including_the_ones_that_failed(tmp_path, corpus):
    absorber = FileAbsorber(tmp_path / "node")
    sources = [str(p) for p in sorted(corpus.iterdir())]
    sources.append(str(corpus / "does-not-exist.md"))
    results = [absorber.absorb(s) for s in sources]

    summary = summarise(results)
    assert summary["attempted"] == 4
    assert summary["absorbed"] + summary["refused"] == summary["attempted"]
    assert summary["refused"] == 2  # the .png and the missing file
    assert all(r["error"] for r in summary["refusals"])


def test_a_refusal_without_a_reason_is_refused():
    with pytest.raises(KnowledgeHalt):
        AbsorptionResult.refusal("p", "s", "")


def test_an_extractor_that_raises_becomes_a_result_not_a_crash(tmp_path, corpus):
    class Exploding(FileAbsorber):
        def extract_content(self, source):
            raise ValueError("the parser gave up")

    result = Exploding(tmp_path / "node").absorb(str(corpus / "one.md"))
    assert result.success is False
    assert "the parser gave up" in result.error


@pytest.mark.parametrize("indexer,expected", [
    (None, "disabled"),
    (lambda content: 0, "zero"),
    (lambda content: 4, "ok"),
])
def test_the_indexing_half_is_reported_separately(tmp_path, corpus, indexer,
                                                  expected):
    result = FileAbsorber(tmp_path / "node", indexer=indexer).absorb(
        str(corpus / "one.md"))
    assert result.success is True
    assert result.indexing_status == expected


def test_a_failing_indexer_is_loud_and_never_reads_as_absorbed(tmp_path, corpus):
    def boom(content):
        raise RuntimeError("index down")

    result = FileAbsorber(tmp_path / "node", indexer=boom).absorb(
        str(corpus / "one.md"))
    assert result.indexing_status == "failed"
    assert summarise([result])["not_searchable"] == 1


def test_an_artifact_only_absorber_says_so_rather_than_looking_complete(
        tmp_path, corpus):
    result = FileAbsorber(tmp_path / "node").absorb(str(corpus / "one.md"))
    assert result.ecosystem_rings == ("artifact",)
    assert "NOT absorbed" in result.depth_note
    assert summarise([result])["depth"] == "artifact only"


def test_a_watermark_never_walks_backwards_by_accident(tmp_path):
    node = tmp_path / "node"
    advance_watermark(node, "file", position="a", comparable=1.0)
    advance_watermark(node, "file", position="b", comparable=2.0)
    with pytest.raises(KnowledgeHalt):
        advance_watermark(node, "file", position="a", comparable=1.0)
    forced = advance_watermark(node, "file", position="a", comparable=1.0,
                               force=True)
    assert forced.position == "a"


# ---------------------------------------------------------------------------
# the write gate: classification is a write-time obligation
# ---------------------------------------------------------------------------


@pytest.fixture()
def spec():
    return CollectionSpec(name="knowledge", dims=4, source_type="local-file",
                          declared_by="the test", expected_writer="the file "
                          "absorber", sensitivity=Ring.R2)


def test_nothing_writes_to_a_collection_nothing_declared(spec):
    store = NullVectorStore(CollectionRegistry([spec]))
    with pytest.raises(KnowledgeHalt):
        store.upsert("undeclared", "1", "x", [0.0] * 4)


def test_a_row_of_the_wrong_width_is_refused_before_it_reaches_the_table(spec):
    store = NullVectorStore(CollectionRegistry([spec]))
    with pytest.raises(KnowledgeHalt):
        store.upsert("knowledge", "1", "x", [0.0] * 3)


def test_a_row_is_classified_at_write_time_not_by_a_later_backfill(spec):
    store = NullVectorStore(CollectionRegistry([spec]), mode="observe")
    row = store.upsert("knowledge", "1", "x", [0.1] * 4, {"source": "a.md"})
    assert row.metadata[RING_META_KEY] == "R2"
    assert row.metadata["ring_status"] == "stamped"


def test_a_collection_may_narrow_a_rows_ring_and_may_never_loosen_it(spec):
    strict = replace(spec, sensitivity=Ring.R1)
    narrowed, _ = gate_write({RING_META_KEY: "R4"}, strict, mode="observe")
    assert narrowed[RING_META_KEY] == "R1"
    assert narrowed["ring_status"] == "narrowed"
    kept, _ = gate_write({RING_META_KEY: "R0"}, spec, mode="observe")
    assert kept[RING_META_KEY] == "R0"


def test_an_unclassified_write_is_a_violation_not_a_silent_default(spec):
    unclassified = replace(spec, sensitivity=Ring.QUARANTINE)
    meta, violations = gate_write({"source": "x"}, unclassified, mode="observe")
    assert meta[RING_META_KEY] == "Q"
    assert any("unclassified write" in v for v in violations)


@pytest.mark.parametrize("meta,expected", [
    ({"sigma": 3, "confidence": 0.9999}, "computed"),
    ({"sigma": 5, "confidence": 0.99}, "over_claimed"),
    ({"sigma": 4}, "no_posterior"),
    ({"sigma": 2, "confidence": 0.98}, "below_floor"),
    ({}, "unscored"),
    ({"sigma": "high"}, "malformed"),
])
def test_a_confidence_claim_is_graded_against_the_posterior_beside_it(
        spec, meta, expected):
    floored = replace(spec, sigma_floor=3.0)
    annotated, violations = gate_write(dict(meta), floored, mode="observe")
    assert annotated["sigma_status"] == expected
    if expected != "computed":
        assert violations, "a graded-down claim must be loud"


@pytest.mark.parametrize("mode,expect_annotation", [
    ("observe", True), ("off", False),
])
def test_observe_annotates_and_off_leaves_no_trace(spec, mode,
                                                   expect_annotation):
    meta, _ = gate_write({"sigma": 5}, spec, mode=mode)
    assert ("sigma_status" in meta) is expect_annotation


def test_enforce_refuses_the_write_and_names_why(spec):
    with pytest.raises(WriteRefused) as exc:
        gate_write({"sigma": 5}, spec, mode="enforce")
    assert exc.value.violations


def test_a_misspelled_mode_halts_it_is_never_coerced_to_observe():
    """A typo is NAK'd, not read as a posture.

    Coercing it to ``observe`` -- the original behaviour -- meant
    ``INTENTOPS_RING_GATE=enforcce`` resolved to ``observe`` AND the coverage
    instrument then recorded ``gate_mode: observe`` in its append-only ledger,
    so a typo was indistinguishable from a deliberate posture forever after.
    """
    with pytest.raises(KnowledgeHalt) as exc:
        collections_mod.sigma_gate_mode({"INTENTOPS_SIGMA_GATE": "enfroce"})
    assert "enfroce" in str(exc.value) and exc.value.remedy
    with pytest.raises(KnowledgeHalt):
        rings_mod.ring_gate_mode({"INTENTOPS_RING_GATE": "0ff"})


def test_silence_is_observe_but_only_silence():
    assert collections_mod.sigma_gate_mode({}) == "observe"
    assert rings_mod.ring_gate_mode({}) == "observe"
    # set-to-empty is how a shell unsets a variable
    assert rings_mod.ring_gate_mode({"INTENTOPS_RING_GATE": ""}) == "observe"
    assert rings_mod.ring_gate_mode({"INTENTOPS_RING_GATE": " OFF "}) == "off"


def test_the_operator_taxonomy_is_read_by_the_write_gate():
    """The defect this closes: the taxonomy had NO consumer on the write path.

    An operator could rule a source type into R1 and every row still carried
    the collection's ring -- capture without a consumer, a store that looked
    governed while the ruling was a no-op.
    """
    spec = collections_mod.CollectionSpec(
        name="knowledge", dims=4, source_type="local-file",
        declared_by="test", expected_writer="the file absorber",
        sensitivity=rings_mod.Ring.R2)
    tax = rings_mod.RingTaxonomy(
        schema="ring-taxonomy/v1", as_of="2026-01-01",
        default_ring=rings_mod.Ring.QUARANTINE,
        mapping={"local-file": rings_mod.Ring.R1})

    meta, _ = collections_mod.gate_write({"source": "a.md"}, spec,
                                         mode="observe", taxonomy=tax)
    assert meta["ring"] == "R1" and meta["ring_status"] == "classified"

    # and it can only narrow: a taxonomy cannot loosen a collection
    loose = replace(spec, source_type="public", sensitivity=rings_mod.Ring.R2)
    tax_loose = rings_mod.RingTaxonomy(
        schema="ring-taxonomy/v1", as_of="2026-01-01",
        default_ring=rings_mod.Ring.QUARANTINE,
        mapping={"public": rings_mod.Ring.R4})
    meta, _ = collections_mod.gate_write({"source": "a.md"}, loose,
                                         mode="observe", taxonomy=tax_loose)
    assert meta["ring"] == "R2"

    # silence in the taxonomy leaves the collection's declaration standing,
    # rather than quarantining every write on a node with a blank taxonomy
    blank = rings_mod.RingTaxonomy(
        schema="ring-taxonomy/v1", as_of="2026-01-01",
        default_ring=rings_mod.Ring.QUARANTINE, mapping={})
    meta, _ = collections_mod.gate_write({"source": "a.md"}, spec,
                                         mode="observe", taxonomy=blank)
    assert meta["ring"] == "R2" and meta["ring_status"] == "stamped"

    # and the store carries its taxonomy to the gate
    store = collections_mod.NullVectorStore(
        collections_mod.CollectionRegistry([spec]), mode="observe",
        taxonomy=tax)
    row = store.upsert("knowledge", "1", "x", [0.1] * 4, {"source": "a.md"})
    assert row.metadata["ring"] == "R1"


def test_a_row_whose_metadata_is_not_a_mapping_degrades_the_collection():
    """The instrument records the collection as an error; it does not crash.

    A crash produces no Reading at all -- a refusal that left the population
    instead of one graded DEGRADED inside it.
    """

    class _BadRow:
        metadata = "not-a-mapping"

    class _BadStore:
        def collections(self):
            return ["k"]

        def rows(self, name):
            return [_BadRow()]

    reading = coverage_mod.ring_coverage(_BadStore())
    verdict, notes = coverage_mod.posture(reading, [])
    assert verdict == "DEGRADED"
    assert reading.rows == 0 and "k" not in reading.measured
    assert any("not a mapping" in e for e in reading.errors)


def test_the_coverage_cli_refuses_to_grade_a_store_nobody_wired(capsys):
    """UNMEASURED is not zero.

    The first build measured a freshly-built NullVectorStore and printed
    NOT-YET-ARMED at exit 0 on every node, populated or not.
    """
    rc = coverage_mod.main(["--node-root", "."])
    out = capsys.readouterr().out
    assert rc == 2
    assert "UNMEASURED" in out and "NOT-YET-ARMED" not in out.splitlines()[0]


# ---------------------------------------------------------------------------
# 4. coverage posture
# ---------------------------------------------------------------------------


class _Row:
    def __init__(self, metadata):
        self.metadata = metadata


class _Store:
    def __init__(self, data, broken=()):
        self.data = data
        self.broken = set(broken)

    def collections(self):
        return sorted(self.data)

    def rows(self, name):
        if name in self.broken:
            raise RuntimeError("collection unreadable")
        return [_Row(m) for m in self.data[name]]


def test_the_three_numbers_are_reported_and_never_summed():
    reading = ring_coverage(_Store({"k": [
        {RING_META_KEY: "R2"}, {RING_META_KEY: "Q"},
        {RING_META_KEY: "not-a-ring"}, {},
    ]}))
    assert (reading.rows, reading.classified, reading.key_present,
            reading.invalid) == (4, 1, 3, 1)
    assert reading.unclassified == 3


def test_a_node_at_birth_is_not_yet_armed_and_never_covered():
    reading = ring_coverage(_Store({"k": []}))
    verdict, notes = posture(reading, [])
    assert verdict == "NOT-YET-ARMED"
    assert reading.classified_pct is None
    assert notes


def test_a_clean_enforcing_store_is_covered_and_an_observing_one_is_attention():
    reading = ring_coverage(_Store({"k": [{RING_META_KEY: "R2"}]}))
    reading.gate_mode = "enforce"
    assert posture(reading, [])[0] == "COVERED"
    reading.gate_mode = "observe"
    verdict, notes = posture(reading, [])
    assert verdict == "ATTENTION"
    assert any("not enforcing" in n for n in notes)


def test_decay_is_measured_against_the_high_water_mark_not_the_last_record():
    reading = ring_coverage(_Store({"k": [{RING_META_KEY: "R2"}] * 87 + [{}] * 13}))
    reading.gate_mode = "enforce"
    walked_down = [
        {"verdict": "COVERED", "classified_pct": 90.0, "unclassified": 10,
         "measured": ["k"]},
        {"verdict": "ATTENTION", "classified_pct": 89.0, "unclassified": 11,
         "measured": ["k"]},
        {"verdict": "ATTENTION", "classified_pct": 88.0, "unclassified": 12,
         "measured": ["k"]},
    ]
    assert posture(reading, walked_down)[0] == "DECAYING"
    assert posture(reading, walked_down[-1:])[0] != "DECAYING"


def test_degraded_outranks_an_apparent_improvement():
    broken = ring_coverage(_Store({"k": [{RING_META_KEY: "R2"}]}, broken=["k"]))
    assert posture(broken, [])[0] == "DEGRADED"

    shrunk = ring_coverage(_Store({"k": [{RING_META_KEY: "R2"}]}))
    verdict, notes = posture(shrunk, [{"verdict": "ATTENTION",
                                       "classified_pct": 50.0,
                                       "unclassified": 500,
                                       "measured": ["k", "the-big-one"]}])
    assert verdict == "DEGRADED"
    assert any("the-big-one" in n for n in notes)


def test_a_corrupt_ledger_line_stays_in_the_history_and_degrades(tmp_path):
    ledger = tmp_path / "ring-coverage.jsonl"
    reading = ring_coverage(_Store({"k": [{RING_META_KEY: "R2"}]}))
    append_reading(reading.to_dict(), "COVERED", ledger)
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write("{ truncated\n")
    history = load_history(ledger)
    assert len(history) == 2 and history[-1]["verdict"] == "LOST"
    assert posture(reading, history)[0] == "DEGRADED"


def test_a_corpus_of_file_types_it_cannot_read_is_unmeasured_not_zero(tmp_path):
    root = tmp_path / "absorbed"
    (root / "text").mkdir(parents=True)
    (root / "text" / "a.md").write_text("a", encoding="utf-8")
    (root / "text" / "b.md").write_text("b", encoding="utf-8")
    (root / "binaries").mkdir()
    (root / "binaries" / "x.bin").write_text("x", encoding="utf-8")
    (root / "empty").mkdir()

    rows, errors = corpus_coverage(root, _Store({"k": [{"source": "a.md"}]}))
    states = {row.corpus: row.state for row in rows}
    assert states == {"text": "PARTIAL", "binaries": "UNMEASURED",
                      "empty": "EMPTY"}
    assert not errors
    unmeasured = next(r for r in rows if r.corpus == "binaries")
    assert "not the same claim as zero" in unmeasured.note


# ---------------------------------------------------------------------------
# 5. the embedder, against a real in-process HTTP server
# ---------------------------------------------------------------------------


class _EmbedHandler(BaseHTTPRequestHandler):
    payload = {"embeddings": [[0.5, 0.5, 0.5, 0.5]]}

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler's spelling
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        body = json.dumps(self.payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep the test output clean
        return


@pytest.fixture()
def embed_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _EmbedHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}/embed"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_the_http_embedder_talks_to_an_endpoint_and_checks_the_width(embed_server):
    embedder = HttpEmbedder(embed_server, 4, timeout=5)
    assert embedder.embed_one("hello") == [0.5, 0.5, 0.5, 0.5]

    wrong_width = HttpEmbedder(embed_server, 8, timeout=5)
    with pytest.raises(Exception) as exc:
        wrong_width.embed(["hello"])
    assert "dimensions" in str(exc.value)


def test_an_unreachable_endpoint_is_loud(tmp_path):
    # port 1 on loopback: nothing listens there, and the failure must not be
    # swallowed into an empty result
    embedder = HttpEmbedder("http://127.0.0.1:1/embed", 4, timeout=1)
    with pytest.raises(EmbedderUnavailable):
        embedder.embed(["hello"])


def test_a_node_with_no_embedder_refuses_rather_than_returning_zeros():
    null = NullEmbedder()
    assert null.dims == 0
    with pytest.raises(EmbedderUnavailable) as exc:
        null.embed(["anything"])
    assert exc.value.remedy


def test_the_shipped_blank_estate_yields_a_refusing_embedder():
    embedder = embedder_from_resources(REPO_ROOT / "estate" / "RESOURCES.yaml")
    assert isinstance(embedder, NullEmbedder)
    with pytest.raises(EmbedderUnavailable):
        embedder.embed(["anything"])


@pytest.mark.parametrize("entry", [
    {"id": "e", "kind": "storage", "endpoint": "http://127.0.0.1:1/e", "dims": 4},
    {"id": "e", "kind": "model-pool", "dims": 4},
    {"id": "e", "kind": "model-pool", "endpoint": "http://127.0.0.1:1/e"},
    {"id": "e", "kind": "model-pool", "endpoint": "http://127.0.0.1:1/e",
     "dims": "wide"},
])
def test_a_half_declared_embedding_resource_halts(entry):
    with pytest.raises(KnowledgeHalt):
        embedder_from_resources({"entries": [entry]}, resource_id="e")


# ---------------------------------------------------------------------------
# 6. the Postgres adapter, on a machine with no driver
# ---------------------------------------------------------------------------


def test_the_postgres_adapter_imports_without_a_driver():
    # the import at the top of this file already proves it; this states why
    assert PostgresVectorStore.driver_available() in (True, False)
    assert "psycopg" in PostgresVectorStore("postgresql:///x").describe()


def test_the_postgres_adapter_refuses_a_connection_string_it_was_not_given():
    with pytest.raises(KnowledgeHalt):
        PostgresVectorStore("")


def test_the_postgres_adapter_halts_with_a_remedy_when_the_driver_is_absent(
        spec, monkeypatch):
    """Simulated, never skipped.

    A ``skipif`` on driver presence would make this test silent on exactly the
    machines that have a driver -- and a check that never runs is
    indistinguishable from one that cannot fail. Binding the name to ``None``
    in ``sys.modules`` makes ``import psycopg`` raise the way it does on a
    machine that never had it, so the refusal is proven on every host.
    """
    monkeypatch.setitem(sys.modules, "psycopg", None)
    store = PostgresVectorStore("postgresql:///example",
                                CollectionRegistry([spec]))
    with pytest.raises(KnowledgeHalt) as exc:
        store.upsert("knowledge", "1", "x", [0.1] * 4)
    assert "psycopg" in exc.value.remedy
    assert "NullVectorStore" in exc.value.remedy


def test_the_postgres_adapter_runs_the_same_refusals_before_it_needs_a_driver(spec):
    store = PostgresVectorStore("postgresql:///example",
                                CollectionRegistry([spec]))
    with pytest.raises(KnowledgeHalt) as undeclared:
        store.upsert("nobody-declared-this", "1", "x", [0.1] * 4)
    assert "not declared" in str(undeclared.value)
    with pytest.raises(KnowledgeHalt) as width:
        store.upsert("knowledge", "1", "x", [0.1] * 9)
    assert "dimensions" in str(width.value)


# ---------------------------------------------------------------------------
# the layer at birth
# ---------------------------------------------------------------------------


def test_the_knowledge_organs_are_declared_at_birth_with_write_models():
    from intentops_core.genesis import organs as organs_mod

    by_id = {organ.id: organ for organ in organs_mod.BIRTH_ORGANS}
    for organ_id in ("ring-taxonomy", "ring-coverage", "corpus-coverage"):
        assert organ_id in by_id, f"{organ_id} is not a birth organ"
        organ = by_id[organ_id]
        assert organ.write_model in organs_mod.WRITE_MODELS
        assert organ.consumer, "capture without a consumer is not retention"


def test_the_absorber_writes_where_the_coverage_oracle_reads(tmp_path, corpus):
    """The two halves agree on a path, or coverage silently reads zero."""
    node = tmp_path / "node"
    absorber = FileAbsorber(node)
    result = absorber.absorb(str(corpus / "one.md"))
    assert result.success

    stored = Path(result.storage_path)
    assert stored.is_file()
    assert stored.parent.parent == node / ".intentops" / "knowledge" / "absorbed"

    rows, errors = corpus_coverage(
        node / ".intentops" / "knowledge" / "absorbed",
        _Store({"k": [{"source": stored.name}]}))
    assert not errors
    assert [r.state for r in rows] == ["COMPLETE"]
