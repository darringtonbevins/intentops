# The substrate

What has to be running underneath a node, what is optional, and what every
image on the list is licensed under.

Genesis phase G3 says a node instantiates *every store empty and valid*. Most
of those stores are files, and `intentops_core/genesis/organs.py` creates them.
Two are not: the vector store and the journals live in a database, and a state
machine cannot create a table in a server that is not running. This document,
`deploy/docker-compose.genesis.yml` and `deploy/schema/genesis.sql` are the
part of G3 that is not a file tree.

Everything here connects to nothing when you only *ask* about it:

```
intentops substrate init --dry-run
```

renders the service plan and the store schema and starts nothing. That is the
only mode implemented, on purpose — see "Applying the schema" below.

---

## What runs at genesis

Three services, two of them required. Nothing else is a floor.

| Service | Image | Posture | Registered port | Licence |
|---|---|---|---|---|
| `postgres` | `pgvector/pgvector:pg16` | **required** | `vector_store` (loopback) | PostgreSQL Licence, for both the server and the pgvector extension. OSI-approved, permissive. |
| `valkey` | `valkey/valkey:8-alpine` | **required**, degraded-ok | `cache` (loopback) | BSD 3-Clause. OSI-approved, permissive. |
| `ollama` | `ollama/ollama:0.12` | **optional**, behind the `local-inference` profile | `local_inference` (loopback) | MIT. OSI-approved, permissive. |

The **models** an inference server downloads carry their own licences, which
are not covered by the row above and are the operator's decision. Nothing is
pre-pulled.

Ports are registered in `config/ports.yaml` and bound to `127.0.0.1`. A
published port that is not in that registry, or that binds anywhere else, is a
finding from `intentops substrate init` — the registry is the one place a port
changes.

Image tags are **series** tags supplied through variables with defaults, never
`latest`: a graph that changes under you is not a floor. Those defaults are
declared from convention and were **not** resolved against a registry from
here — this seed has no network in its tests. A wrong default fails loudly at
`docker pull`; it never silently becomes a different image.

`INTENTOPS_POSTGRES_PASSWORD` has **no default**, and compose refuses to start
without it. There is no credential value anywhere in this repository.

## What is deliberately absent

| Absent | Why |
|---|---|
| A graph store | A genesis node has no graph organ. The one the private estate this seed came from runs is under a source-available licence, and a public clone must not inherit an obligation its operator never chose. Retrieval at genesis is the vector table and SQL. |
| A source-available cache | Valkey, under BSD 3-Clause, rather than the fork whose terms changed. The framework uses queue and cache primitives that Valkey covers directly. |
| An identity provider | Built-in JWT is the default; an IdP is an opt-in upgrade. A stranger reading a port registry should not provision a service the node has never used. |
| Any estate service | A capability app, another project's API, a firm's connector — those are somebody's deployment, never the framework's floor. `service_graph.py` refuses them by name. |
| The node's own host processes | The gateway, the API and the loop scheduler are started by the node, not by compose. Compose owns the things that hold state between processings. |

## The store schema

`deploy/schema/genesis.sql` is a **generated projection**. Its source of record
is `packages/intentops-core/intentops_core/substrate/schema.py`; correct the
declaration and re-render, and `intentops substrate init --check` catches a
hand edit.

Seven tables, five stores. Each states its **write model**, its **expected
writer** and its **consumer**, in the SQL file *and* as a `COMMENT ON TABLE`
that survives into a database somebody inspects without this repo in front of
them.

| Table | Write model | What it is |
|---|---|---|
| `schema_migrations` | migration-ceremony | Which versions were applied, with the digest of the SQL that was applied. |
| `collections` | transactional-upsert | The registry of vector collections: dimensionality, source type, who declared it, and who is expected to fill it. |
| `embeddings` | transactional-upsert | **The whole vector store.** One generic table for every collection. |
| `interaction_journal` | append-only | Every gate-visible decision — the saddle contract's `record` operation at the service tier. |
| `approvals_index` | generated-projection | A readable index over the approval queue, whose write model of record is per-item *files*. |
| `ordering_journal_mirror` | append-only | A queryable mirror of the identity repository's ordering journal. |
| `aliveness_log` | append-only | The birth questions, asked again over time, with the instrument that answered and the population it was answered over. |

### One embeddings table, not sixty-three

A collection is **data**, not DDL. Adding one is an `INSERT` into
`collections` declaring its `dims`; a trigger then refuses any embedding whose
dimensionality disagrees with that declaration, and refuses a write to a
collection nothing declared at all.

The estate this seed was extracted from had sixty-three vector tables spread
across three modules, seven of which read exactly zero rows while being
declared capability stores. At genesis *every* table is empty, so an
unpopulated store and a never-written store are indistinguishable — which is
why `collections.expected_writer` is `NOT NULL`. Naming the writer at
declaration time is what makes the difference detectable later.

**The cost, stated:** `embeddings.embedding` is an *undimensioned* `vector`
column. pgvector will store any dimensionality there and **cannot build an
HNSW or ivfflat index on it** — an index needs a fixed dimension. A genesis
node therefore has exact search and no vector index. Adding one is a
per-collection migration against a dimensioned partition. That is owed work,
named here rather than left to be discovered.

### Three refusals armed in SQL

- **Append-only means append-only.** `interaction_journal`,
  `ordering_journal_mirror` and `aliveness_log` carry a trigger that raises on
  `UPDATE` and `DELETE`. It is a *defect control*, not a security boundary — a
  superuser can drop a trigger — and it exists so ordinary code cannot quietly
  rewrite a record of conduct.
- **A ruling with no tension is a preference; a ruling with no conditions is a
  slogan.** Both are `NOT NULL` and non-blank on the ordering mirror.
  `reopens_when` is deliberately **nullable**: most carried rulings have none,
  and fabricating a way back would manufacture exactly the false confidence
  the store exists to prevent. `on_match` defaults to `escalate`, because
  defaulting to `permit` would make compiling a ruling dangerous.
- **An expiry is not a ruling.** `approvals_index` gives expiry its own column,
  and a check constraint keeps `decided_by` attributable. An expiry sweep that
  overwrote that field once destroyed the record of real grants and made
  lapsed approvals read as never-ruled; the schema now makes that shape
  impossible rather than discouraged.

### Applying the schema

```
psql -f deploy/schema/genesis.sql
```

Or simply bring the graph up: `deploy/schema/` is mounted read-only into the
database image's own initialisation directory, so a first `up` on an empty
volume applies it. It is idempotent — every statement is `IF NOT EXISTS`,
`CREATE OR REPLACE`, or a `DROP`-then-`CREATE` trigger pair — so re-applying
it changes nothing.

`intentops substrate init` will **not** apply it. This seed ships no database
driver (stdlib, `pyyaml`, `cryptography`), so a verb that claimed to apply the
schema would be claiming something it cannot verify. It prints the command.

## Bringing it up

```
export INTENTOPS_POSTGRES_PASSWORD=...            # no default, by design
docker compose -f deploy/docker-compose.genesis.yml up -d
docker compose -f deploy/docker-compose.genesis.yml --profile local-inference up -d
```

Then, before believing any of it:

```
intentops substrate init --dry-run     # the plan, and whether it is clean
intentops substrate init --selftest    # prove the checks can actually fire
intentops doctor                       # the six birth questions
```

## What this cannot tell you

- `substrate init` reads files. **A healthcheck being declared is not a
  healthcheck passing**, and nothing here has contacted a service. The
  dependency entries in `estate/DEPENDENCIES.yaml` name the real probes; running
  them is the node's job, not this document's.
- The refused-image list matches by **name**. An estate service re-tagged under
  a different name is invisible to it. The list is a floor; the reviewer is the
  ceiling.
- The licence column above was written by a human who checked. No code in this
  repository verifies a licence, and none pretends to.
- The compose file is read as YAML, not through `docker compose config`, so an
  error only the compose implementation's own schema knows about would pass
  here. Variable expansion is resolved for the two forms this file uses; a
  third form is reported unresolved rather than quietly accepted.
