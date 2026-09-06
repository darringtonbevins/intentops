"""Tests for the genesis substrate: the service graph and the store schema.

No live services, no network, no container, no database. Every test either
reads a shipped file or builds a deliberately broken one in a temp directory.

The shape of the negative tests is the one the rest of this suite uses: take
the shipped artifact as the baseline, break exactly ONE thing, and assert the
check fires for THAT reason. A test that only proved "something went wrong"
would pass against a checker that refuses everything.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Mapping

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "packages" / "intentops-core"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from intentops_core.substrate import schema as schema_mod  # noqa: E402
from intentops_core.substrate import service_graph as graph_mod  # noqa: E402

COMPOSE = REPO_ROOT / "deploy" / "docker-compose.genesis.yml"
SQL = REPO_ROOT / "deploy" / "schema" / "genesis.sql"
PORTS = REPO_ROOT / "config" / "ports.yaml"


def _compose() -> Mapping[str, Any]:
    return graph_mod.load_compose(COMPOSE)


def _healthy_service() -> Dict[str, Any]:
    return {"image": "example/thing:1.0",
            "healthcheck": {"test": ["CMD", "true"]},
            "ports": ["127.0.0.1:8002:5432"]}


def _fake_repo(tmp_path: Path, services: Mapping[str, Any]) -> Path:
    (tmp_path / "deploy").mkdir(exist_ok=True)
    (tmp_path / "config").mkdir(exist_ok=True)
    graph_mod.compose_path(tmp_path).write_text(
        yaml.safe_dump({"services": dict(services)}), encoding="utf-8")
    (tmp_path / "config" / "ports.yaml").write_text(
        yaml.safe_dump({"services": {"vector_store": {"port": 8002}}}),
        encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# The schema: declaration, rendering, idempotence
# ---------------------------------------------------------------------------


def test_schema_selftest_passes() -> None:
    ok, report = schema_mod.selftest()
    assert ok, report


def test_every_table_declares_a_write_model_a_writer_and_a_consumer() -> None:
    """The whole point of the declaration: no unstated store."""
    for table in schema_mod.TABLES:
        assert table.write_model in schema_mod.SQL_WRITE_MODELS, table.name
        assert table.expected_writer.strip(), table.name
        assert table.consumer.strip(), table.name


def test_the_write_model_survives_into_the_database() -> None:
    """A reader inspecting the database, without this repo, still learns it."""
    sql = schema_mod.render_sql()
    for table in schema_mod.TABLES:
        assert f"COMMENT ON TABLE {table.name} IS" in sql
        assert f"write model: {table.write_model}" in sql


def test_render_is_deterministic() -> None:
    assert schema_mod.render_sql() == schema_mod.render_sql()


def test_shipped_sql_matches_its_declaration() -> None:
    """genesis.sql is a GENERATED projection; a hand edit is a finding."""
    ok, report = schema_mod.check_rendered(REPO_ROOT)
    assert ok, report


def test_a_hand_edit_to_the_sql_is_caught(tmp_path: Path) -> None:
    """Positive control for the check above -- prove it can go red."""
    (tmp_path / "deploy" / "schema").mkdir(parents=True)
    schema_mod.rendered_path(tmp_path).write_text(
        schema_mod.render_sql() + "\n-- a hand edit\n", encoding="utf-8")
    ok, report = schema_mod.check_rendered(tmp_path)
    assert not ok
    assert "drifted" in report


def test_a_missing_sql_file_is_reported_not_assumed_fine(tmp_path: Path) -> None:
    ok, report = schema_mod.check_rendered(tmp_path)
    assert not ok and "MISSING" in report


def test_every_statement_is_idempotent_shaped() -> None:
    """The file may be applied any number of times to the same database."""
    sql = schema_mod.render_sql()
    assert sql.count("CREATE TABLE ") == sql.count("CREATE TABLE IF NOT EXISTS ")
    assert sql.count("CREATE INDEX ") == sql.count("CREATE INDEX IF NOT EXISTS ")
    assert sql.count("CREATE EXTENSION ") == sql.count(
        "CREATE EXTENSION IF NOT EXISTS ")
    assert sql.count("CREATE FUNCTION ") == 0
    # every trigger is preceded by its own DROP TRIGGER IF EXISTS
    assert sql.count("CREATE TRIGGER ") == sql.count("DROP TRIGGER IF EXISTS ")
    assert "ON CONFLICT (version) DO NOTHING" in sql


def test_append_only_tables_arm_the_refusal() -> None:
    """A declared model that nothing enforces is prose."""
    sql = schema_mod.render_sql()
    append_only = [t for t in schema_mod.TABLES if t.append_only]
    assert append_only, "no append-only table -- the check would pass vacuously"
    for table in append_only:
        assert f"CREATE TRIGGER {table.name}_append_only" in sql
        assert f"BEFORE UPDATE OR DELETE ON {table.name}" in sql


def test_the_ordering_mirror_refuses_a_preference_and_a_slogan() -> None:
    sql = schema_mod.render_sql()
    assert "ordering_mirror_tension_not_blank" in sql
    assert "ordering_mirror_conditions_not_blank" in sql
    # reopens_when is nullable ON PURPOSE: never fabricate a way back
    mirror = {t.name: t for t in schema_mod.TABLES}["ordering_journal_mirror"]
    reopens = {c.name: c for c in mirror.columns}["reopens_when"]
    assert "NOT NULL" not in reopens.constraints
    on_match = {c.name: c for c in mirror.columns}["on_match"]
    assert "DEFAULT 'escalate'" in on_match.constraints


def test_an_expiry_can_never_be_recorded_as_a_ruling() -> None:
    """The 2026-09 lesson, in a constraint rather than a comment."""
    approvals = {t.name: t for t in schema_mod.TABLES}["approvals_index"]
    names = {c.name for c in approvals.columns}
    assert {"decided_by", "decided_at", "expired_at"} <= names
    assert "approvals_index_decision_is_attributed" in schema_mod.render_sql()


def test_the_vector_store_is_one_table_with_dims_declared_per_collection() -> None:
    names = {t.name for t in schema_mod.TABLES}
    assert "embeddings" in names and "collections" in names
    vector_tables = [n for n in names if n.startswith("vec_")]
    assert not vector_tables, f"a table per collection has crept back: {vector_tables}"
    collections = {t.name: t for t in schema_mod.TABLES}["collections"]
    dims = {c.name: c for c in collections.columns}["dims"]
    assert "NOT NULL" in dims.constraints and "dims > 0" in dims.constraints
    sql = schema_mod.render_sql()
    assert "intentops_enforce_embedding_dims" in sql
    assert "embeddings_dims_declared" in sql


def test_a_collection_names_who_is_expected_to_fill_it() -> None:
    """At birth every table is empty; this is what makes debt detectable."""
    collections = {t.name: t for t in schema_mod.TABLES}["collections"]
    expected = {c.name: c for c in collections.columns}["expected_writer"]
    assert "NOT NULL" in expected.constraints


@pytest.mark.parametrize("bad,reason", [
    ({"write_model": "invented"}, "undeclared write model"),
    ({"expected_writer": "  "}, "no expected writer"),
    ({"consumer": ""}, "no consumer"),
])
def test_the_declaration_halts_on_an_unstated_table(bad: Dict[str, Any],
                                                    reason: str) -> None:
    fields: Dict[str, Any] = {
        "name": "t", "purpose": "p", "write_model": "transactional-upsert",
        "expected_writer": "w", "consumer": "c",
        "columns": (schema_mod.Column("a", "text"),)}
    fields.update(bad)
    with pytest.raises(schema_mod.SchemaError):
        schema_mod.validate_tables([schema_mod.Table(**fields)])


# ---------------------------------------------------------------------------
# The service graph
# ---------------------------------------------------------------------------


def test_service_graph_selftest_passes() -> None:
    ok, report = graph_mod.selftest(REPO_ROOT)
    assert ok, report


def test_the_shipped_graph_is_clean() -> None:
    ok, findings, _ = graph_mod.check_graph(REPO_ROOT)
    assert ok, "; ".join(str(f) for f in findings)


def test_the_compose_file_parses_and_declares_the_expected_services() -> None:
    services = _compose()["services"]
    assert set(services) == {"postgres", "valkey", "ollama"}


def test_every_service_declares_a_healthcheck_with_a_test() -> None:
    for name, svc in _compose()["services"].items():
        health = svc.get("healthcheck")
        assert isinstance(health, dict), f"{name} declares no healthcheck"
        assert health.get("test"), f"{name} declares a healthcheck with no test"
        assert health.get("disable") is not True, f"{name} disables its healthcheck"


def test_every_published_port_is_registered_and_loopback() -> None:
    registry = graph_mod.load_registered_ports(REPO_ROOT)
    published = graph_mod.published_ports(_compose())
    assert published, "no published port -- this test would pass vacuously"
    for port in published:
        assert port.host_ip == "127.0.0.1", port
        assert port.host_port in registry, (
            f"{port.service} publishes {port.host_port}, which config/ports.yaml "
            "does not register")


def test_no_refused_image_is_in_the_graph() -> None:
    text = COMPOSE.read_text(encoding="utf-8").lower()
    images = [str(svc.get("image", "")).lower()
              for svc in _compose()["services"].values()]
    assert images, "no images -- vacuous"
    for refused, _reason in graph_mod.REFUSED_IMAGES:
        assert not any(refused in image for image in images), refused
    # and no refused name reaches the file by any other route -- a commented
    # -out service or an override default would pass the loop above
    for refused, _reason in graph_mod.REFUSED_IMAGES:
        assert refused not in text, f"{refused!r} appears in the compose file"


def test_no_floating_image_tag() -> None:
    for name, svc in _compose()["services"].items():
        image, _ = graph_mod.expand(str(svc["image"]))
        assert ":" in image.rsplit("/", 1)[-1], f"{name} carries no tag"
        assert not image.endswith(":latest"), f"{name} floats on latest"


def test_the_database_password_has_no_default_anywhere() -> None:
    """A load-bearing credential with a convenient default is how one leaks."""
    postgres = _compose()["services"]["postgres"]
    password = str(postgres["environment"]["POSTGRES_PASSWORD"])
    assert ":?" in password, "the password expansion must be the required form"
    assert ":-" not in password, "the password must have no default"


def test_local_inference_is_optional_and_behind_a_profile() -> None:
    services = _compose()["services"]
    assert services["ollama"].get("profiles") == ["local-inference"]
    for required in ("postgres", "valkey"):
        assert not services[required].get("profiles"), (
            f"{required} is the floor and must not sit behind a profile")


def test_every_volume_is_named() -> None:
    volumes = _compose().get("volumes") or {}
    assert volumes
    for key, row in volumes.items():
        assert key and (row or {}).get("name"), key


@pytest.mark.parametrize("check,services", [
    ("refused-image", {"a": {**_healthy_service(), "image": "falkordb/falkordb:1.0"}}),
    ("healthcheck-declared", {"a": {"image": "example/thing:1.0"}}),
    ("port-registered", {"a": {**_healthy_service(), "ports": ["127.0.0.1:9999:5432"]}}),
    ("port-loopback", {"a": {**_healthy_service(), "ports": ["0.0.0.0:8002:5432"]}}),
    ("image-tag-pinned", {"a": {**_healthy_service(), "image": "example/thing:latest"}}),
    ("image-declared", {"a": {"healthcheck": {"test": ["CMD", "true"]}}}),
])
def test_each_graph_check_can_actually_fire(tmp_path: Path, check: str,
                                            services: Dict[str, Any]) -> None:
    """A detector that has never fired is indistinguishable from a broken one."""
    root = _fake_repo(tmp_path, services)
    ok, findings, _ = graph_mod.check_graph(root)
    assert not ok
    assert any(f.check == check for f in findings), [str(f) for f in findings]


def test_a_clean_synthetic_graph_produces_no_finding(tmp_path: Path) -> None:
    """The control for the parametrised test above: it is not always red."""
    root = _fake_repo(tmp_path, {"a": _healthy_service()})
    ok, findings, _ = graph_mod.check_graph(root)
    assert ok, [str(f) for f in findings]


def test_a_missing_compose_file_halts_rather_than_reading_clean(tmp_path: Path) -> None:
    with pytest.raises(graph_mod.GraphError):
        graph_mod.check_graph(tmp_path)


def test_a_required_expansion_is_never_invented() -> None:
    text, resolved = graph_mod.expand("${SOMETHING:?you must set this}")
    assert not resolved and "${SOMETHING" in text


# ---------------------------------------------------------------------------
# The dependency manifest and the documentation
# ---------------------------------------------------------------------------


def test_the_service_dependencies_declare_a_real_probe() -> None:
    """`health_probe` never null for a dependency that IS probeable."""
    data = yaml.safe_load(
        (REPO_ROOT / "estate" / "DEPENDENCIES.yaml").read_text(encoding="utf-8"))
    entries = {row["id"]: row for row in data["entries"]}
    for dep in ("dep-genesis-postgres", "dep-genesis-cache",
                "dep-genesis-local-inference"):
        row = entries[dep]
        assert row["kind"] == "service"
        assert row["health_probe"], f"{dep} declares no probe"
        assert row["health_probe"] is not None
        assert "unprobeable_reason" not in row
        assert row["fallback"].strip(), f"{dep} states no fallback"


def test_every_compose_service_has_a_dependency_entry() -> None:
    """A service running with nothing declaring it is a store off the books."""
    data = yaml.safe_load(
        (REPO_ROOT / "estate" / "DEPENDENCIES.yaml").read_text(encoding="utf-8"))
    probes = " ".join(str(row["health_probe"]) for row in data["entries"])
    registry = graph_mod.load_registered_ports(REPO_ROOT)
    for port in graph_mod.published_ports(_compose()):
        registered_name = registry[port.host_port]["name"]
        assert registered_name in probes, (
            f"{port.service} publishes the {registered_name} port and no "
            "dependency entry names it")


def test_substrate_doc_names_a_licence_for_every_image() -> None:
    doc = (REPO_ROOT / "docs" / "SUBSTRATE.md").read_text(encoding="utf-8")
    for svc in _compose()["services"].values():
        image, _ = graph_mod.expand(str(svc["image"]))
        repository = image.split(":")[0]
        assert repository in doc, f"{repository} carries no line in SUBSTRATE.md"
    for licence in ("PostgreSQL Licence", "BSD 3-Clause", "MIT"):
        assert licence in doc


# ---------------------------------------------------------------------------
# The CLI verb
# ---------------------------------------------------------------------------


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "intentops_core.cli", "--repo-root",
         str(REPO_ROOT), *args],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
        env={**__import__("os").environ,
             "PYTHONPATH": str(PACKAGE_ROOT)}, check=False)


def test_substrate_init_dry_run_renders_and_connects_to_nothing() -> None:
    result = _cli("substrate", "init", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "GENESIS SERVICE PLAN" in result.stdout
    assert "NOT APPLIED" in result.stdout
    assert "psql -f deploy/schema/genesis.sql" in result.stdout
    for name in ("postgres", "valkey", "ollama"):
        assert name in result.stdout


def test_substrate_init_without_dry_run_applies_nothing_and_says_so() -> None:
    result = _cli("substrate", "init")
    assert result.returncode == 2
    assert "nothing was applied" in result.stderr


def test_substrate_init_check_and_selftest_pass() -> None:
    assert _cli("substrate", "init", "--check").returncode == 0
    assert _cli("substrate", "init", "--selftest").returncode == 0


def test_substrate_init_sql_prints_the_rendered_schema() -> None:
    result = _cli("substrate", "init", "--sql")
    assert result.returncode == 0
    assert result.stdout.replace("\r\n", "\n") == schema_mod.render_sql()
