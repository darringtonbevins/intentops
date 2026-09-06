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
-- Schema version: 0001-genesis


CREATE EXTENSION IF NOT EXISTS vector;

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

-- ----------------------------------------------------------------------
-- schema_migrations
-- PURPOSE:          which versions of this schema have been applied to this database, in order, with the digest of the SQL that was applied
-- WRITE MODEL:      migration-ceremony
-- EXPECTED WRITER:  the migration runner (`psql -f`, or whatever applies deploy/schema/*.sql); nothing else may write it
-- CONSUMER:         `intentops substrate init`, which refuses to render a plan against a database whose applied version it cannot read
-- NOTE:             A migration table that is itself append-only would refuse its own corrections, so this one is not; the digest column is the control instead.
-- ----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_migrations (
    -- the migration id, e.g. the value of SCHEMA_VERSION
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now(),
    description text NOT NULL,
    -- digest of the rendered SQL -- a version that moved without its digest moving is a hand edit, and this column is how that becomes visible
    sql_sha256 text NOT NULL
);

COMMENT ON TABLE schema_migrations IS
    'write model: migration-ceremony. expected writer: the migration runner (`psql -f`, or whatever applies deploy/schema/*.sql); nothing else may write it. consumer: `intentops substrate init`, which refuses to render a plan against a database whose applied version it cannot read.';

-- ----------------------------------------------------------------------
-- collections
-- PURPOSE:          the registry of vector collections -- one row per collection, declaring its dimensionality, who fills it, and what claims it
-- WRITE MODEL:      transactional-upsert
-- EXPECTED WRITER:  the collection manager, on an explicit declare call; never implicitly on first write
-- CONSUMER:         the dims trigger on `embeddings`, and any retrieval path that needs to know a collection's shape
-- ----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS collections (
    name text PRIMARY KEY,
    -- declared per collection, which is why one generic embeddings table can serve them all
    dims integer NOT NULL CHECK (dims > 0),
    -- stable and undated -- a dated campaign label is what quarantined half a corpus in the estate this came from
    source_type text NOT NULL,
    -- the manifest or module that claims this collection. A collection nothing claims is refused, not created
    declared_by text NOT NULL,
    -- at birth every collection is empty; this is how an unpopulated store is told from a never-written one
    expected_writer text NOT NULL,
    -- missing and unclassified resolve identically, so absence is never read as 'safe'
    sensitivity text NOT NULL DEFAULT 'unclassified',
    declared_at timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE collections IS
    'write model: transactional-upsert. expected writer: the collection manager, on an explicit declare call; never implicitly on first write. consumer: the dims trigger on `embeddings`, and any retrieval path that needs to know a collection''s shape.';

CREATE INDEX IF NOT EXISTS collections_source_type_idx ON collections (source_type);

-- ----------------------------------------------------------------------
-- embeddings
-- PURPOSE:          the whole vector store: one generic table, every collection, dimensionality enforced against the collection's declaration
-- WRITE MODEL:      transactional-upsert
-- EXPECTED WRITER:  the embedding writer, one collection at a time
-- CONSUMER:         semantic retrieval (rung 3 of the ladder: local embeddings before any model call)
-- NOTE:             Sixty-three tables became five because a collection is DATA, not DDL. Adding a collection is an INSERT into `collections`.
-- ----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS embeddings (
    -- content-hash id, so a re-embed of unchanged content is an upsert rather than a duplicate
    id text PRIMARY KEY,
    collection text NOT NULL REFERENCES collections (name) ON DELETE RESTRICT,
    content text NOT NULL,
    -- UNDIMENSIONED on purpose -- see the module's blind spots: the dimensionality is the collection's declaration, and an undimensioned column cannot carry a vector index
    embedding vector NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE embeddings IS
    'write model: transactional-upsert. expected writer: the embedding writer, one collection at a time. consumer: semantic retrieval (rung 3 of the ladder: local embeddings before any model call).';

CREATE INDEX IF NOT EXISTS embeddings_collection_idx ON embeddings (collection);
CREATE INDEX IF NOT EXISTS embeddings_metadata_idx ON embeddings USING gin (metadata);

DROP TRIGGER IF EXISTS embeddings_dims_declared ON embeddings;
CREATE TRIGGER embeddings_dims_declared
    BEFORE INSERT OR UPDATE ON embeddings
    FOR EACH ROW EXECUTE FUNCTION intentops_enforce_embedding_dims();

-- ----------------------------------------------------------------------
-- interaction_journal
-- PURPOSE:          every gate-visible decision, in order -- the S3 `record` operation of the saddle contract, at the service tier
-- WRITE MODEL:      append-only
-- EXPECTED WRITER:  the bound saddle, on every gate verdict
-- CONSUMER:         the audit trail, the responsiveness check, any question of the form 'what did this node actually do'
-- ----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS interaction_journal (
    seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at timestamptz NOT NULL DEFAULT now(),
    session_id text NOT NULL,
    -- which surface produced the call -- a saddle id, never a person
    actor text NOT NULL,
    tool text NOT NULL,
    tier text NOT NULL CHECK (tier IN ('T0','T1','T2','T3','T4')),
    -- the discriminator that is actually enforced on every call; no default, because 'we did not classify it' and 'it does not reach reality' are different findings
    reaches_reality boolean NOT NULL,
    verdict text NOT NULL CHECK (verdict IN ('allow','ask','deny','halt')),
    -- what permitted it. A blank string is refused by the check below: an action with no named authority is not a record
    authority text NOT NULL,
    -- a digest, never the payload -- the journal records that a call happened and under whose word, not its contents
    payload_digest text NOT NULL,
    CONSTRAINT interaction_journal_authority_not_blank CHECK (length(btrim(authority)) > 0)
);

COMMENT ON TABLE interaction_journal IS
    'write model: append-only. expected writer: the bound saddle, on every gate verdict. consumer: the audit trail, the responsiveness check, any question of the form ''what did this node actually do''.';

CREATE INDEX IF NOT EXISTS interaction_journal_at_idx ON interaction_journal (at);
CREATE INDEX IF NOT EXISTS interaction_journal_session_idx ON interaction_journal (session_id, seq);

DROP TRIGGER IF EXISTS interaction_journal_append_only ON interaction_journal;
CREATE TRIGGER interaction_journal_append_only
    BEFORE UPDATE OR DELETE ON interaction_journal
    FOR EACH ROW EXECUTE FUNCTION intentops_refuse_mutation();

-- ----------------------------------------------------------------------
-- approvals_index
-- PURPOSE:          a readable index over the approval queue, whose write model of record is per-item FILES under .intentops/approvals
-- WRITE MODEL:      generated-projection
-- EXPECTED WRITER:  the approvals indexer, rebuilding from the files; correct a file, never a row here
-- CONSUMER:         anything that needs to ask the queue a question in SQL
-- ----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS approvals_index (
    approval_id text PRIMARY KEY,
    state text NOT NULL CHECK (state IN ('pending','approved','denied','expired')),
    tier text NOT NULL CHECK (tier IN ('T0','T1','T2','T3','T4')),
    summary text NOT NULL,
    created_at timestamptz NOT NULL,
    -- no perpetual item; an absent expiry is a decision nobody made
    expires_at timestamptz NOT NULL,
    decided_at timestamptz,
    -- WHO RULED. Expiry must never write here -- see the table's own comment: an expiry sweep that overwrote this field destroyed the record of real grants and made lapsed approvals read as never-ruled
    decided_by text,
    -- expiry has its OWN column, so a lapse can never be mistaken for a ruling and a ruling can never be erased by a lapse
    expired_at timestamptz,
    -- the per-item file this row projects; the source of record
    source_path text NOT NULL,
    indexed_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT approvals_index_decision_is_attributed CHECK ((decided_at IS NULL) = (decided_by IS NULL)),
    CONSTRAINT approvals_index_expiry_is_not_a_ruling CHECK (NOT (state = 'expired' AND decided_by IS NOT NULL AND expired_at IS NULL))
);

COMMENT ON TABLE approvals_index IS
    'write model: generated-projection. expected writer: the approvals indexer, rebuilding from the files; correct a file, never a row here. consumer: anything that needs to ask the queue a question in SQL.';

CREATE INDEX IF NOT EXISTS approvals_index_state_idx ON approvals_index (state, expires_at);

-- ----------------------------------------------------------------------
-- ordering_journal_mirror
-- PURPOSE:          a queryable mirror of the identity repository's ordering journal -- the rulings that GOVERN, with both structural refusals armed
-- WRITE MODEL:      append-only
-- EXPECTED WRITER:  the ordering mirror, following the identity repo's own append-only journal; the identity repo is the source of record and this is downstream of it
-- CONSUMER:         the ordering consult, which runs BEFORE the council: an ordering binds, a vote deliberates
-- ----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ordering_journal_mirror (
    seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ordering_id text NOT NULL,
    -- a supersession is a status flip plus lineage, never a delete
    event text NOT NULL CHECK (event IN ('record','supersede','reaffirm','compile')),
    ruled_at timestamptz NOT NULL,
    ruled_by text NOT NULL,
    statement text NOT NULL,
    -- REFUSED IF BLANK: a ruling with no tension is a preference
    tension text NOT NULL,
    -- REFUSED IF BLANK: a ruling with no conditions is a slogan
    conditions text NOT NULL,
    -- NULLABLE ON PURPOSE. Most carried rulings have none, and fabricating a way back would manufacture exactly the false confidence this store exists to prevent. Absence is debt, surfaced, never invented
    reopens_when text,
    -- only a compiled predicate can answer a case mechanically; an uncompiled ruling still binds a human reader
    predicate text,
    -- defaulting to permit would make compiling a ruling dangerous
    on_match text NOT NULL DEFAULT 'escalate' CHECK (on_match IN ('permit','escalate')),
    superseded_by text,
    -- digest of the journal line this row mirrors, so drift between the mirror and its source is detectable
    source_digest text NOT NULL,
    CONSTRAINT ordering_mirror_tension_not_blank CHECK (length(btrim(tension)) > 0),
    CONSTRAINT ordering_mirror_conditions_not_blank CHECK (length(btrim(conditions)) > 0)
);

COMMENT ON TABLE ordering_journal_mirror IS
    'write model: append-only. expected writer: the ordering mirror, following the identity repo''s own append-only journal; the identity repo is the source of record and this is downstream of it. consumer: the ordering consult, which runs BEFORE the council: an ordering binds, a vote deliberates.';

CREATE INDEX IF NOT EXISTS ordering_mirror_ordering_idx ON ordering_journal_mirror (ordering_id, seq);

DROP TRIGGER IF EXISTS ordering_journal_mirror_append_only ON ordering_journal_mirror;
CREATE TRIGGER ordering_journal_mirror_append_only
    BEFORE UPDATE OR DELETE ON ordering_journal_mirror
    FOR EACH ROW EXECUTE FUNCTION intentops_refuse_mutation();

-- ----------------------------------------------------------------------
-- aliveness_log
-- PURPOSE:          the birth questions, asked again over time -- whether this node is actually alive and under its own rules, answered by instrument
-- WRITE MODEL:      append-only
-- EXPECTED WRITER:  `intentops doctor`, on every run
-- CONSUMER:         the come-online report, and anyone asking whether a reading improved or the population shrank
-- ----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS aliveness_log (
    seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at timestamptz NOT NULL DEFAULT now(),
    question_id text NOT NULL,
    -- ABSENT is a context-coverage failure and FAIL is a truth failure; UNPROBEABLE keeps a refusal in the denominator rather than letting it leave the population
    verdict text NOT NULL CHECK (verdict IN ('PASS','FAIL','ABSENT','UNPROBEABLE')),
    -- what was actually run. An answer with no instrument is an assertion, and this column is why it cannot be recorded as one
    instrument text NOT NULL,
    evidence text NOT NULL,
    -- the denominator beside the reading, always -- a rate without its population is not a measurement
    population integer NOT NULL CHECK (population >= 0)
);

COMMENT ON TABLE aliveness_log IS
    'write model: append-only. expected writer: `intentops doctor`, on every run. consumer: the come-online report, and anyone asking whether a reading improved or the population shrank.';

CREATE INDEX IF NOT EXISTS aliveness_log_question_idx ON aliveness_log (question_id, at);

DROP TRIGGER IF EXISTS aliveness_log_append_only ON aliveness_log;
CREATE TRIGGER aliveness_log_append_only
    BEFORE UPDATE OR DELETE ON aliveness_log
    FOR EACH ROW EXECUTE FUNCTION intentops_refuse_mutation();

-- ----------------------------------------------------------------------
-- Record that this version was applied. ON CONFLICT DO NOTHING is
-- what makes re-applying this file a no-op rather than an error.
-- ----------------------------------------------------------------------
INSERT INTO schema_migrations (version, description, sql_sha256)
VALUES ('0001-genesis',
        'genesis service-tier schema: the five stores a node is born with',
        'rendered-by-schema.py')
ON CONFLICT (version) DO NOTHING;
