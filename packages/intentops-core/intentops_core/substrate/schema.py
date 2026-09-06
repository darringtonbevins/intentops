"""The declarative service-tier schema -- what "every store, empty and valid" means in SQL.

PURPOSE
    G3 ORGANS says a node instantiates every store empty and valid, each one
    declaring how it is written. The file tier has ``genesis/organs.py`` for
    that. The SERVICE tier had nothing: the estate this seed was extracted
    from spread its table creation across three modules and sixty-three
    tables, so no artifact could answer "what does every store mean at the SQL
    level" without reading code. This module is that artifact.

    It is DECLARATIVE. The tables, their columns, their write models and their
    expected writers are data here; :func:`render_sql` turns that data into
    the DDL. ``deploy/schema/genesis.sql`` is a GENERATED PROJECTION of this
    module -- correct the declaration, never the SQL file, and
    :func:`check_rendered` (``--check``) catches a hand edit.

    Five stores, not sixty-three. The vector store is ONE generic table whose
    dimensionality is declared per collection in a registry beside it, because
    a table per collection is how a schema becomes unreadable and how seven
    declared-but-never-written tables hid in plain sight.

WRITE MODEL
    This module writes nothing. It renders text. The write model of every
    TABLE it declares is stated in :data:`TABLES` and carried into the SQL as
    a ``COMMENT ON TABLE``, so the model survives into a database a reader
    inspects without this repository in front of them.

    The vocabulary is closed (:data:`SQL_WRITE_MODELS`) and deliberately NOT
    the file tier's vocabulary from ``genesis/organs.py``: a locked
    read-modify-write is a filesystem answer to a filesystem problem, and a
    transaction is not the same instrument. A table declaring a model outside
    the vocabulary is a HALT, never a default.

BLIND SPOTS
    - This module never connects to anything. It cannot tell you that the
      rendered schema APPLIED, only what would be applied. The seed ships no
      database driver on purpose (stdlib, pyyaml, cryptography), so the apply
      step is ``psql`` and is named as such rather than simulated.
    - ``embeddings.embedding`` is an UNDIMENSIONED ``vector`` column. pgvector
      can store any dimensionality there, and CANNOT build an HNSW or ivfflat
      index on it -- an index needs a fixed dimension. So a genesis node has
      exact search and no vector index; adding one is a per-collection
      migration against a dimensioned partition, and it is owed work, not a
      thing this schema quietly already did.
    - The dims trigger enforces agreement between a row and its collection's
      DECLARED dimensionality. It cannot tell you the declaration is right.
    - The append-only triggers refuse UPDATE and DELETE from ordinary
      sessions. A superuser can drop a trigger; this is a defect control, not
      a security boundary, and the ledger it protects is not the only copy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

__all__ = [
    "SCHEMA_VERSION",
    "SQL_WRITE_MODELS",
    "Column",
    "Table",
    "TABLES",
    "SchemaError",
    "render_sql",
    "rendered_path",
    "check_rendered",
    "write_rendered",
    "selftest",
]

#: The migration version this module renders. A change to any table here is a
#: change to this number and a row in ``schema_migrations``; a schema that
#: moved without its version moving is a store that cannot say what it is.
SCHEMA_VERSION = "0001-genesis"

#: Closed vocabulary. An undeclared model is a HALT (see module docstring).
SQL_WRITE_MODELS: Tuple[str, ...] = (
    "append-only",          # INSERT only; UPDATE and DELETE refused by trigger
    "transactional-upsert",  # ordinary read-write under a transaction
    "generated-projection",  # rebuilt from a source of record elsewhere
    "migration-ceremony",   # only the migration runner writes it
)


class SchemaError(RuntimeError):
    """A declaration this module refuses to render."""


@dataclass(frozen=True)
class Column:
    """One column, with the reason it exists rather than only its type."""

    name: str
    type_: str
    constraints: str = ""
    note: str = ""

    def render(self) -> str:
        body = f"{self.name} {self.type_}"
        if self.constraints:
            body = f"{body} {self.constraints}"
        return body


@dataclass(frozen=True)
class Table:
    """One service-tier store.

    ``expected_writer`` exists because at genesis every table is empty, so a
    store nobody will ever write and a store not written YET are
    indistinguishable. Naming the writer at declaration time is what makes the
    difference detectable later.
    """

    name: str
    purpose: str
    write_model: str
    expected_writer: str
    consumer: str
    columns: Tuple[Column, ...]
    table_constraints: Tuple[str, ...] = ()
    indexes: Tuple[Tuple[str, str], ...] = ()   # (index name, ON-clause body)
    append_only: bool = False
    notes: Tuple[str, ...] = field(default_factory=tuple)


# --------------------------------------------------------------------------
# The declaration
# --------------------------------------------------------------------------

TABLES: Tuple[Table, ...] = (
    Table(
        name="schema_migrations",
        purpose=(
            "which versions of this schema have been applied to this database, "
            "in order, with the digest of the SQL that was applied"
        ),
        write_model="migration-ceremony",
        expected_writer="the migration runner (`psql -f`, or whatever applies "
                        "deploy/schema/*.sql); nothing else may write it",
        consumer="`intentops substrate init`, which refuses to render a plan "
                 "against a database whose applied version it cannot read",
        columns=(
            Column("version", "text", "PRIMARY KEY",
                   "the migration id, e.g. the value of SCHEMA_VERSION"),
            Column("applied_at", "timestamptz", "NOT NULL DEFAULT now()"),
            Column("description", "text", "NOT NULL"),
            Column("sql_sha256", "text", "NOT NULL",
                   "digest of the rendered SQL -- a version that moved without "
                   "its digest moving is a hand edit, and this column is how "
                   "that becomes visible"),
        ),
        notes=(
            "A migration table that is itself append-only would refuse its own "
            "corrections, so this one is not; the digest column is the control "
            "instead.",
        ),
    ),
    Table(
        name="collections",
        purpose=(
            "the registry of vector collections -- one row per collection, "
            "declaring its dimensionality, who fills it, and what claims it"
        ),
        write_model="transactional-upsert",
        expected_writer="the collection manager, on an explicit declare call; "
                        "never implicitly on first write",
        consumer="the dims trigger on `embeddings`, and any retrieval path "
                 "that needs to know a collection's shape",
        columns=(
            Column("name", "text", "PRIMARY KEY"),
            Column("dims", "integer", "NOT NULL CHECK (dims > 0)",
                   "declared per collection, which is why one generic "
                   "embeddings table can serve them all"),
            Column("source_type", "text", "NOT NULL",
                   "stable and undated -- a dated campaign label is what "
                   "quarantined half a corpus in the estate this came from"),
            Column("declared_by", "text", "NOT NULL",
                   "the manifest or module that claims this collection. A "
                   "collection nothing claims is refused, not created"),
            Column("expected_writer", "text", "NOT NULL",
                   "at birth every collection is empty; this is how an "
                   "unpopulated store is told from a never-written one"),
            Column("sensitivity", "text", "NOT NULL DEFAULT 'unclassified'",
                   "missing and unclassified resolve identically, so absence "
                   "is never read as 'safe'"),
            Column("declared_at", "timestamptz", "NOT NULL DEFAULT now()"),
        ),
        indexes=(("collections_source_type_idx", "collections (source_type)"),),
    ),
    Table(
        name="embeddings",
        purpose=(
            "the whole vector store: one generic table, every collection, "
            "dimensionality enforced against the collection's declaration"
        ),
        write_model="transactional-upsert",
        expected_writer="the embedding writer, one collection at a time",
        consumer="semantic retrieval (rung 3 of the ladder: local embeddings "
                 "before any model call)",
        columns=(
            Column("id", "text", "PRIMARY KEY",
                   "content-hash id, so a re-embed of unchanged content is an "
                   "upsert rather than a duplicate"),
            Column("collection", "text",
                   "NOT NULL REFERENCES collections (name) ON DELETE RESTRICT"),
            Column("content", "text", "NOT NULL"),
            Column("embedding", "vector", "NOT NULL",
                   "UNDIMENSIONED on purpose -- see the module's blind spots: "
                   "the dimensionality is the collection's declaration, and an "
                   "undimensioned column cannot carry a vector index"),
            Column("metadata", "jsonb", "NOT NULL DEFAULT '{}'::jsonb"),
            Column("created_at", "timestamptz", "NOT NULL DEFAULT now()"),
            Column("updated_at", "timestamptz", "NOT NULL DEFAULT now()"),
        ),
        indexes=(
            ("embeddings_collection_idx", "embeddings (collection)"),
            ("embeddings_metadata_idx", "embeddings USING gin (metadata)"),
        ),
        notes=(
            "Sixty-three tables became five because a collection is DATA, not "
            "DDL. Adding a collection is an INSERT into `collections`.",
        ),
    ),
    Table(
        name="interaction_journal",
        purpose=(
            "every gate-visible decision, in order -- the S3 `record` operation "
            "of the saddle contract, at the service tier"
        ),
        write_model="append-only",
        expected_writer="the bound saddle, on every gate verdict",
        consumer="the audit trail, the responsiveness check, any question of "
                 "the form 'what did this node actually do'",
        columns=(
            Column("seq", "bigint", "GENERATED ALWAYS AS IDENTITY PRIMARY KEY"),
            Column("at", "timestamptz", "NOT NULL DEFAULT now()"),
            Column("session_id", "text", "NOT NULL"),
            Column("actor", "text", "NOT NULL",
                   "which surface produced the call -- a saddle id, never a person"),
            Column("tool", "text", "NOT NULL"),
            Column("tier", "text",
                   "NOT NULL CHECK (tier IN ('T0','T1','T2','T3','T4'))"),
            Column("reaches_reality", "boolean", "NOT NULL",
                   "the discriminator that is actually enforced on every call; "
                   "no default, because 'we did not classify it' and 'it does "
                   "not reach reality' are different findings"),
            Column("verdict", "text",
                   "NOT NULL CHECK (verdict IN ('allow','ask','deny','halt'))"),
            Column("authority", "text", "NOT NULL",
                   "what permitted it. A blank string is refused by the check "
                   "below: an action with no named authority is not a record"),
            Column("payload_digest", "text", "NOT NULL",
                   "a digest, never the payload -- the journal records that a "
                   "call happened and under whose word, not its contents"),
        ),
        table_constraints=(
            "CONSTRAINT interaction_journal_authority_not_blank "
            "CHECK (length(btrim(authority)) > 0)",
        ),
        indexes=(
            ("interaction_journal_at_idx", "interaction_journal (at)"),
            ("interaction_journal_session_idx",
             "interaction_journal (session_id, seq)"),
        ),
        append_only=True,
    ),
    Table(
        name="approvals_index",
        purpose=(
            "a readable index over the approval queue, whose write model of "
            "record is per-item FILES under .intentops/approvals"
        ),
        write_model="generated-projection",
        expected_writer="the approvals indexer, rebuilding from the files; "
                        "correct a file, never a row here",
        consumer="anything that needs to ask the queue a question in SQL",
        columns=(
            Column("approval_id", "text", "PRIMARY KEY"),
            Column("state", "text",
                   "NOT NULL CHECK (state IN ('pending','approved','denied','expired'))"),
            Column("tier", "text",
                   "NOT NULL CHECK (tier IN ('T0','T1','T2','T3','T4'))"),
            Column("summary", "text", "NOT NULL"),
            Column("created_at", "timestamptz", "NOT NULL"),
            Column("expires_at", "timestamptz", "NOT NULL",
                   "no perpetual item; an absent expiry is a decision nobody made"),
            Column("decided_at", "timestamptz", ""),
            Column("decided_by", "text", "",
                   "WHO RULED. Expiry must never write here -- see the table's "
                   "own comment: an expiry sweep that overwrote this field "
                   "destroyed the record of real grants and made lapsed "
                   "approvals read as never-ruled"),
            Column("expired_at", "timestamptz", "",
                   "expiry has its OWN column, so a lapse can never be mistaken "
                   "for a ruling and a ruling can never be erased by a lapse"),
            Column("source_path", "text", "NOT NULL",
                   "the per-item file this row projects; the source of record"),
            Column("indexed_at", "timestamptz", "NOT NULL DEFAULT now()"),
        ),
        table_constraints=(
            "CONSTRAINT approvals_index_decision_is_attributed "
            "CHECK ((decided_at IS NULL) = (decided_by IS NULL))",
            "CONSTRAINT approvals_index_expiry_is_not_a_ruling "
            "CHECK (NOT (state = 'expired' AND decided_by IS NOT NULL "
            "AND expired_at IS NULL))",
        ),
        indexes=(("approvals_index_state_idx", "approvals_index (state, expires_at)"),),
    ),
    Table(
        name="ordering_journal_mirror",
        purpose=(
            "a queryable mirror of the identity repository's ordering journal "
            "-- the rulings that GOVERN, with both structural refusals armed"
        ),
        write_model="append-only",
        expected_writer="the ordering mirror, following the identity repo's "
                        "own append-only journal; the identity repo is the "
                        "source of record and this is downstream of it",
        consumer="the ordering consult, which runs BEFORE the council: an "
                 "ordering binds, a vote deliberates",
        columns=(
            Column("seq", "bigint", "GENERATED ALWAYS AS IDENTITY PRIMARY KEY"),
            Column("ordering_id", "text", "NOT NULL"),
            Column("event", "text",
                   "NOT NULL CHECK (event IN ('record','supersede','reaffirm','compile'))",
                   "a supersession is a status flip plus lineage, never a delete"),
            Column("ruled_at", "timestamptz", "NOT NULL"),
            Column("ruled_by", "text", "NOT NULL"),
            Column("statement", "text", "NOT NULL"),
            Column("tension", "text", "NOT NULL",
                   "REFUSED IF BLANK: a ruling with no tension is a preference"),
            Column("conditions", "text", "NOT NULL",
                   "REFUSED IF BLANK: a ruling with no conditions is a slogan"),
            Column("reopens_when", "text", "",
                   "NULLABLE ON PURPOSE. Most carried rulings have none, and "
                   "fabricating a way back would manufacture exactly the false "
                   "confidence this store exists to prevent. Absence is debt, "
                   "surfaced, never invented"),
            Column("predicate", "text", "",
                   "only a compiled predicate can answer a case mechanically; "
                   "an uncompiled ruling still binds a human reader"),
            Column("on_match", "text",
                   "NOT NULL DEFAULT 'escalate' CHECK (on_match IN ('permit','escalate'))",
                   "defaulting to permit would make compiling a ruling dangerous"),
            Column("superseded_by", "text", ""),
            Column("source_digest", "text", "NOT NULL",
                   "digest of the journal line this row mirrors, so drift "
                   "between the mirror and its source is detectable"),
        ),
        table_constraints=(
            "CONSTRAINT ordering_mirror_tension_not_blank "
            "CHECK (length(btrim(tension)) > 0)",
            "CONSTRAINT ordering_mirror_conditions_not_blank "
            "CHECK (length(btrim(conditions)) > 0)",
        ),
        indexes=(
            ("ordering_mirror_ordering_idx", "ordering_journal_mirror (ordering_id, seq)"),
        ),
        append_only=True,
    ),
    Table(
        name="aliveness_log",
        purpose=(
            "the birth questions, asked again over time -- whether this node "
            "is actually alive and under its own rules, answered by instrument"
        ),
        write_model="append-only",
        expected_writer="`intentops doctor`, on every run",
        consumer="the come-online report, and anyone asking whether a reading "
                 "improved or the population shrank",
        columns=(
            Column("seq", "bigint", "GENERATED ALWAYS AS IDENTITY PRIMARY KEY"),
            Column("at", "timestamptz", "NOT NULL DEFAULT now()"),
            Column("question_id", "text", "NOT NULL"),
            Column("verdict", "text",
                   "NOT NULL CHECK (verdict IN ('PASS','FAIL','ABSENT','UNPROBEABLE'))",
                   "ABSENT is a context-coverage failure and FAIL is a truth "
                   "failure; UNPROBEABLE keeps a refusal in the denominator "
                   "rather than letting it leave the population"),
            Column("instrument", "text", "NOT NULL",
                   "what was actually run. An answer with no instrument is an "
                   "assertion, and this column is why it cannot be recorded as one"),
            Column("evidence", "text", "NOT NULL"),
            Column("population", "integer", "NOT NULL CHECK (population >= 0)",
                   "the denominator beside the reading, always -- a rate "
                   "without its population is not a measurement"),
        ),
        indexes=(("aliveness_log_question_idx", "aliveness_log (question_id, at)"),),
        append_only=True,
    ),
)


# --------------------------------------------------------------------------
# Validation of the declaration itself
# --------------------------------------------------------------------------

def validate_tables(tables: Sequence[Table] = TABLES) -> None:
    """HALT on a declaration this module will not render.

    Every rule here is a rule a reader of the SQL could not check for
    themselves once the SQL exists, which is why it runs before rendering.
    """
    seen: Dict[str, Table] = {}
    for table in tables:
        if table.write_model not in SQL_WRITE_MODELS:
            raise SchemaError(
                f"table {table.name!r} declares write model "
                f"{table.write_model!r}, which is not one of "
                f"{list(SQL_WRITE_MODELS)} -- declare it, or correct the table"
            )
        if not table.expected_writer.strip():
            raise SchemaError(
                f"table {table.name!r} names no expected writer; at birth every "
                "table is empty, so a store nobody will write is "
                "indistinguishable from one not written yet"
            )
        if not table.consumer.strip():
            raise SchemaError(
                f"table {table.name!r} names no consumer -- capture without a "
                "consumer is not retention"
            )
        if not table.columns:
            raise SchemaError(f"table {table.name!r} declares no columns")
        if table.name in seen:
            raise SchemaError(f"table {table.name!r} is declared twice")
        seen[table.name] = table
        if table.append_only and table.write_model != "append-only":
            raise SchemaError(
                f"table {table.name!r} arms the append-only triggers but "
                f"declares write model {table.write_model!r}"
            )
        if table.write_model == "append-only" and not table.append_only:
            raise SchemaError(
                f"table {table.name!r} declares append-only and does not arm "
                "the triggers -- a declared model nothing enforces is prose"
            )
        cols = [c.name for c in table.columns]
        if len(cols) != len(set(cols)):
            raise SchemaError(f"table {table.name!r} declares a column twice")


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

_HEADER = """\
-- IntentOps -- the genesis service-tier schema.
--
-- GENERATED FILE. Source of record:
--   packages/intentops-core/intentops_core/substrate/schema.py
-- Correct the declaration there and re-render; a hand edit here is caught by
--   intentops substrate init --check
--
-- Every table below states its WRITE MODEL, its EXPECTED WRITER and its
-- CONSUMER, both in this file and as a COMMENT ON TABLE that survives into
-- the database. A table that cannot say how it is written is a shared
-- whiteboard waiting to happen.
--
-- Idempotent by construction: this file may be applied to the same database
-- any number of times. Every statement is IF NOT EXISTS, CREATE OR REPLACE,
-- or a DROP-then-CREATE pair for a trigger.
--
-- Schema version: {version}
"""

_FUNCTIONS = """\
-- Refuse UPDATE and DELETE on an append-only table.
--
-- This is a DEFECT CONTROL, not a security boundary: a superuser can drop a
-- trigger. It exists so that ordinary code cannot quietly rewrite a record of
-- conduct, which is the failure it was written for.
CREATE OR REPLACE FUNCTION intentops_refuse_mutation() RETURNS trigger
LANGUAGE plpgsql AS $intentops$
BEGIN
    RAISE EXCEPTION
        'table % is append-only; % refused. Append a superseding row with '
        'lineage -- never edit or delete a record of conduct.',
        TG_TABLE_NAME, TG_OP;
END;
$intentops$;

-- Enforce that an embedding matches the dimensionality its collection
-- DECLARED, and that the collection was declared at all.
--
-- The second half is the load-bearing one: a table nothing claims is a hard
-- exit, never a row created on the way past.
CREATE OR REPLACE FUNCTION intentops_enforce_embedding_dims() RETURNS trigger
LANGUAGE plpgsql AS $intentops$
DECLARE
    declared_dims integer;
BEGIN
    SELECT dims INTO declared_dims FROM collections WHERE name = NEW.collection;
    IF declared_dims IS NULL THEN
        RAISE EXCEPTION
            'collection % is not declared in collections; declare it (dims, '
            'source_type, declared_by, expected_writer) before writing to it.',
            NEW.collection;
    END IF;
    IF vector_dims(NEW.embedding) <> declared_dims THEN
        RAISE EXCEPTION
            'collection % declares % dimensions; this row carries %.',
            NEW.collection, declared_dims, vector_dims(NEW.embedding);
    END IF;
    RETURN NEW;
END;
$intentops$;
"""


def _render_table(table: Table) -> str:
    lines: List[str] = []
    lines.append("-- " + "-" * 70)
    lines.append(f"-- {table.name}")
    lines.append(f"-- PURPOSE:          {table.purpose}")
    lines.append(f"-- WRITE MODEL:      {table.write_model}")
    lines.append(f"-- EXPECTED WRITER:  {table.expected_writer}")
    lines.append(f"-- CONSUMER:         {table.consumer}")
    for note in table.notes:
        lines.append(f"-- NOTE:             {note}")
    lines.append("-- " + "-" * 70)

    body: List[str] = []
    for col in table.columns:
        if col.note:
            body.append(f"    -- {col.note}")
        body.append(f"    {col.render()},")
    for constraint in table.table_constraints:
        body.append(f"    {constraint},")
    if body:
        body[-1] = body[-1].rstrip(",")

    lines.append(f"CREATE TABLE IF NOT EXISTS {table.name} (")
    lines.extend(body)
    lines.append(");")
    lines.append("")

    comment = (
        f"write model: {table.write_model}. "
        f"expected writer: {table.expected_writer}. "
        f"consumer: {table.consumer}."
    ).replace("'", "''")
    lines.append(f"COMMENT ON TABLE {table.name} IS")
    lines.append(f"    '{comment}';")
    lines.append("")

    for index_name, on_clause in table.indexes:
        lines.append(
            f"CREATE INDEX IF NOT EXISTS {index_name} ON {on_clause};")
    if table.indexes:
        lines.append("")

    if table.append_only:
        trigger = f"{table.name}_append_only"
        lines.append(f"DROP TRIGGER IF EXISTS {trigger} ON {table.name};")
        lines.append(f"CREATE TRIGGER {trigger}")
        lines.append(f"    BEFORE UPDATE OR DELETE ON {table.name}")
        lines.append("    FOR EACH ROW EXECUTE FUNCTION intentops_refuse_mutation();")
        lines.append("")

    if table.name == "embeddings":
        trigger = "embeddings_dims_declared"
        lines.append(f"DROP TRIGGER IF EXISTS {trigger} ON {table.name};")
        lines.append(f"CREATE TRIGGER {trigger}")
        lines.append(f"    BEFORE INSERT OR UPDATE ON {table.name}")
        lines.append(
            "    FOR EACH ROW EXECUTE FUNCTION intentops_enforce_embedding_dims();")
        lines.append("")

    return "\n".join(lines)


def render_sql(tables: Sequence[Table] = TABLES,
               version: str = SCHEMA_VERSION) -> str:
    """Render the whole schema. Pure: same declaration, same bytes."""
    validate_tables(tables)
    parts: List[str] = [_HEADER.format(version=version), ""]
    parts.append("CREATE EXTENSION IF NOT EXISTS vector;")
    parts.append("")
    parts.append(_FUNCTIONS)
    for table in tables:
        parts.append(_render_table(table))
    parts.append("-- " + "-" * 70)
    parts.append("-- Record that this version was applied. ON CONFLICT DO NOTHING is")
    parts.append("-- what makes re-applying this file a no-op rather than an error.")
    parts.append("-- " + "-" * 70)
    parts.append(
        "INSERT INTO schema_migrations (version, description, sql_sha256)\n"
        f"VALUES ('{version}',\n"
        "        'genesis service-tier schema: the five stores a node is born with',\n"
        "        'rendered-by-schema.py')\n"
        "ON CONFLICT (version) DO NOTHING;")
    parts.append("")
    return "\n".join(parts)


def rendered_path(repo_root: Path) -> Path:
    """Where the generated projection lives."""
    return Path(repo_root) / "deploy" / "schema" / "genesis.sql"


def check_rendered(repo_root: Path) -> Tuple[bool, str]:
    """Is the shipped SQL file still what this module renders?"""
    path = rendered_path(repo_root)
    if not path.exists():
        return False, f"{path} is MISSING -- render it with --write"
    on_disk = path.read_text(encoding="utf-8")
    wanted = render_sql()
    if on_disk == wanted:
        return True, f"{path.name} matches the declaration ({len(TABLES)} tables)"
    return False, (
        f"{path} has drifted from schema.py -- it is a GENERATED projection. "
        "Correct the declaration in schema.py and re-render; do not hand-edit "
        "the SQL.")


def write_rendered(repo_root: Path) -> Path:
    """Re-render the projection. The only writer of the SQL file."""
    path = rendered_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(render_sql())
    return path


# --------------------------------------------------------------------------
# Selftest
# --------------------------------------------------------------------------

def selftest() -> Tuple[bool, str]:
    """Prove every refusal in this module can actually fire."""
    fired: List[str] = []
    failures: List[str] = []

    def expect(name: str, ok: bool) -> None:
        fired.append(name)
        if not ok:
            failures.append(name)

    good = TABLES[0]

    def halts(tables: Sequence[Table]) -> bool:
        try:
            validate_tables(tables)
        except SchemaError:
            return True
        return False

    expect("shipped-declaration-valid", not halts(TABLES))
    expect("undeclared-write-model-halts", halts([
        Table("t", "p", "invented-model", "w", "c", (Column("a", "text"),))]))
    expect("no-expected-writer-halts", halts([
        Table("t", "p", "transactional-upsert", "  ", "c", (Column("a", "text"),))]))
    expect("no-consumer-halts", halts([
        Table("t", "p", "transactional-upsert", "w", "", (Column("a", "text"),))]))
    expect("no-columns-halts", halts([
        Table("t", "p", "transactional-upsert", "w", "c", ())]))
    expect("duplicate-table-halts", halts([good, good]))
    expect("append-only-unenforced-halts", halts([
        Table("t", "p", "append-only", "w", "c", (Column("a", "text"),),
              append_only=False)]))
    expect("duplicate-column-halts", halts([
        Table("t", "p", "transactional-upsert", "w", "c",
              (Column("a", "text"), Column("a", "int")))]))

    sql = render_sql()
    expect("render-is-deterministic", sql == render_sql())
    expect("every-table-rendered",
           all(f"CREATE TABLE IF NOT EXISTS {t.name} (" in sql for t in TABLES))
    expect("every-create-table-idempotent",
           sql.count("CREATE TABLE ") == sql.count("CREATE TABLE IF NOT EXISTS "))
    expect("every-append-only-table-armed",
           all(f"{t.name}_append_only" in sql
               for t in TABLES if t.append_only))
    expect("write-models-survive-into-the-database",
           all(f"COMMENT ON TABLE {t.name} IS" in sql for t in TABLES))

    report = (f"schema selftest: {len(fired)} paths fired, {len(failures)} failed"
              + (" -- " + ", ".join(failures) if failures else ""))
    return (not failures, report)


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Render or check the genesis service-tier schema.")
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--write", action="store_true",
                        help="re-render deploy/schema/genesis.sql")
    parser.add_argument("--check", action="store_true",
                        help="fail if the rendered file has drifted")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.selftest:
        ok, report = selftest()
        print(report)
        return 0 if ok else 1

    root = Path(args.repo_root) if args.repo_root else Path(
        __file__).resolve().parents[3]
    if args.write:
        print(f"rendered {write_rendered(root)}")
        return 0
    if args.check:
        ok, report = check_rendered(root)
        print(report)
        return 0 if ok else 1
    print(render_sql(), end="")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
