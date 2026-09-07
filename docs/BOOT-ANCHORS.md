# Boot anchors -- the organ index a fresh window must carry

A node's cold context window carries a small, fixed set of files. Everything
else it owns is reachable only if something in that set names it. This file is
that something: one row per organ, carrying **where it lives** and **what it is
for**, and nothing else.

It is a POINTER INDEX, never a second copy of the prose. When an organ moves,
correct the row; when an organ is born, add one -- in the same session, beside
its probe row in `config/genesis-probes.yaml`. That pairing is mechanical:
`scripts/genesis/probe_coverage_check.py` exits 1 when an organ has no probe,
and `intentops_core.continuity.self_probe` reports ABSENT when a probe's fact
does not reach a fresh window from the boot corpus. The two failures are
different and must never be summed: **ABSENT is a packaging failure** (the
corpus lost the anchor, the organ may be perfectly healthy) and **FAIL is a
truth failure** (the organ moved or is gone).

This file is itself part of the boot corpus, declared in
`config/genesis-probes.yaml` under `boot_corpus.workspace`. That list must
never shrink below what the suite's own probes depend on.

---

## Genesis -- how a clone becomes a node

`intentops_core/genesis/machine.py` runs G0-G7 and stops at the first HALT.
`intentops_core/genesis/organs.py` declares **BIRTH_ORGANS**: every entry a node
holds at birth, each with its **write model** (per-item-files, locked-rmw,
append-only-jsonl, single-writer-ceremony, generated-projection,
derived-regenerable, absent-at-birth) and the consumer that reads it. A store
that cannot say how it is written is a shared whiteboard waiting to happen.
`.intentops/config/node.yaml` is the node's own binding: which identity
repository, which saddle. `.intentops/checkpoint.json` is where the node was
when it last stopped; `.intentops/estate-cache` and `.intentops/register` are
rebuilt and locked-rmw respectively. `.intentops/locks` is reserved and
normally empty -- a `StoreLock` sibling lands beside its own store, not there.
The birth reading is `intentops_core/genesis/aliveness.py`: six questions, each
answered by a named instrument, every rate printed beside its denominator, and
no `OK` anywhere in the verdict vocabulary.

## Trust -- provenance, the release root, and the saddle token

`intentops_core/genesis/trust_pin.py` carries the compiled **release-root** pin
and `config/trust-roots.yaml` the declared roots; `.intentops/trust/` holds
PUBLIC projections only and never a private key. The operator record written at
G2 (`.intentops/trust/operator-root.pub.json`) is the honest answer to *who is
this node answerable to* -- never a name inherited from anywhere else.
`intentops_core/genesis/integrity.py` re-hashes the node's own copy of the
rules, `.intentops-rules/`, against the imprint bundle named in
`genesis/imprint/IMPRINT-MANIFEST.yaml`, so a node can tell whether what it is
reading is what the project published. The MCP saddle's bearer record is
`.intentops/trust/mcp-saddle-auth.json` (`intentops_saddle_mcp/auth.py`).

## The gate and the audit trail

`intentops_core/gate/classify.py` holds the tier ladder and the
**reaches_reality** classifier that outranks the tier label. Refusals and
guardian blocks land under `.intentops/logs/` -- a detector that has never
fired is indistinguishable from a broken one, so the birth reading exercises
the gate and keeps the refusal.

## The two councils, the ordering store, and core review

`intentops_core/councils/` is the Posture Council; `intentops_core/wisdom/
ordering.py` is the ordering store, whose atom is a ruling under conditions and
which refuses a record carrying no `tension` and no `conditions`. An ordering
BINDS where a vote DELIBERATES, so it is consulted ahead of a council and is
never a member of one. `reopens_when` is surfaced as debt rather than
fabricated. `intentops_core/governance/values_council.py` writes the
**core-review** ledger (`.intentops/core-review/ledger.jsonl`), the record of
what this node was asked to change about itself.

## Approvals

`intentops_core/approvals/` is the queue: one file per item under
`.intentops/approvals/`, collision-safe by construction. An expiry is a
decision -- whoever set the timeout decided, and nobody chose it at the moment
it fired.

## The validation family -- four axes of grounding

| Instrument | Module | The axis it grounds |
|---|---|---|
| grounded signals | `intentops_core/validation/grounded_signals.py` | the STATEMENT, when it is made |
| witness | `intentops_core/validation/witness.py` | the COMPLETION -- claims stay OPEN-UNVERIFIED until a live run answers them (`.intentops/witness/register.jsonl`) |
| self probe | `intentops_core/continuity/self_probe.py` | the SELF-MODEL -- can a fresh window still find each organ (PASS / ABSENT / FAIL / DRIFT / STALE / ERROR) |
| still true | `intentops_core/validation/still_true.py` | the BELIEF, across DURATION (`.intentops/still-true/ledger.jsonl`) |

**Belief carriers** are what Still True reads: `config/belief-carriers.template.yaml`
(schema `belief-carriers/v1`), bound by G3 to `.intentops/config/belief-carriers.yaml`
and loaded by `intentops_core/validation/belief_carriers.py`. Four kinds --
ruling, anchor, briefing, memory-topic -- each with its as-of extractor, and
four falsifiers: **EVIDENCE-MOVED, SUBJECT-GONE, UNDATED, CONFLICT**. A source
may only declare a falsifier its reader can actually fire; a falsifier that
cannot fire is worse than none, because it looks accounted for.

**Responsiveness** is the correction half: `intentops_core/validation/
responsiveness.py` reads still-true runs against the ordering journal and
counts what was answered by supersede, what by reaffirm, what merely reopened,
and what became a **silent close** -- a question that stopped being raised with
no event at all. Ledger `.intentops/responsiveness/ledger.jsonl`. It publishes
decision volume beside the answer rate as a PAIR and never verdicts on the
pair, because once an answer rate is a published number the cheapest way to
improve it is to have fewer contestable rulings.

## Tagouts -- is anything safety-critical switched off?

`intentops_core/loto/ledger.py` over `.intentops/loto/LEDGER.yaml`. An open
switch carries no information about why it is open, so every disable is tagged
with an authority and a **reenergize** condition. Posture is REMEDIATED /
ATTENTION / BLOCKED, and BLOCKED is correct and loud while the
unsigned-development tagout is open.

## Routing, loops, alignment, substrate

`intentops_core/routing/policy.py` reads `config/routing-policy.yaml`
(`routing-policy/v1`): which class of work goes to which tier of model, with a
NullProvider seam where a model call would be. `intentops_core/loops/` reads
`config/loop-charters.template.yaml` (`loop-charters/v1`) -- a budget share is
summed over ENABLED charters only. `intentops_core/alignment/` holds the
staged interview (`config/alignment-interview.template.yaml`) and the
calibration bar; its journals live in the identity repository under
`identity/alignment/`, never in the node. `intentops_core/substrate/schema.py`
is the declarative service-tier schema -- `deploy/schema/genesis.sql` is its
GENERATED projection, and `schema_migrations` records which versions were
applied.

## Presence, knowledge, metabolism

`intentops_core/presence/` carries the interaction journal
(`.intentops/presence/interactions.jsonl`, which holds what happened and never
what was said), the operator rule that decides whether this node answers at all
-- **it answers only its bound operator; everyone else is surfaced as an ask** --
and the ONE router (`presence/router.py`) that classifies every inbound message
against `config/intent-taxonomy.template.yaml` before any handler is named, with
the model as a declared seam that ships returning nothing. Full text:
`docs/PRESENCE.md`. `intentops_core/knowledge/` holds the
collections, the embedder seam and the sensitivity **rings**; a node-local
knowledge store never enters a repository, so `knowledge/MANIFEST.yaml` in the
identity repo carries pointers only. `intentops_core/metabolism/` is the
cadence: heartbeat, assimilation, crystallisation -- and a stage that produces
nothing renders as a flat line, never as green. `metabolism/runner.py` is the
other half of that seam: four workers, one per stage, run ONE stage at a time
under a declared token/item envelope, with every run -- including the ones that
produced nothing -- appended to `.intentops/metabolism/runs.jsonl`. It resolves
a cadence's `callable:` against a CLOSED registry by exact string and imports
nothing from operator text, and it REFUSES to run a worker behind a `seam:
"null"` declaration, because the declaration is what a reviewer reads. The
distill prompt is shipped and reviewed in `config/metabolism-prompts.yaml`, and
a candidate is graded `INFERRED` always: a generator is not an instrument.

## Inference -- what a model call costs in coupling

`intentops_core/inference/` is the whole of it. `provider.py` holds the
`Provider` ABC (one method), the `Completion` that says whether its token count
was REPORTED by the endpoint or ESTIMATED by us, and `NullProvider` -- **the
default, which contacts nothing**. `openai_compat.py` is one stdlib-`urllib`
adapter over the OpenAI-compatible chat shape; no vendor SDK is imported
anywhere. A pool is a `kind: model-pool` row the operator declared in
`estate/RESOURCES.yaml` (endpoint, model, data_fence, max_tokens, timeout_s,
enabled) and there is no built-in endpoint. The fence: an endpoint must be
LOOPBACK or RFC1918 or it is REFUSED, a bare hostname is refused unresolved
rather than looked up, and leaving that fence needs `allow_remote: true` AND a
stated `allow_remote_reason`. A pool is born `enabled: false`.

## The governed tool gateway

`packages/intentops-gateway/` is where a tool call from another harness meets
this node: `tokens.py` holds the bearer record (`gateway-auth`), `backends.py`
is **default-deny** over what may be reached at all, and `tiers.py` classifies
every call -- a tool nobody classified takes `UNTAGGED_TIER`, never a
permissive default. Requests land in the node's own ledger. A bounded,
self-describing endpoint surface is a SAFETY property, not merely tidier
engineering: an agent handed a shell can do anything, and an agent handed
enumerable endpoints can only do what they expose.

The **gateway token** is minted by a ceremony, never in silence.
`intentops_core/trust/bearer.py` is the mint primitive -- in the CORE, so
genesis can write the gateway's record without importing the gateway --
and `intentops_core/genesis/token_ceremony.py` is the ceremony G2 runs: an
attended terminal, one question, the plaintext printed ONCE under a banner,
and only the SHA-256 digest plus a `disclosed_at` stamp kept. It never mints
from a non-TTY and never in a dry run, because a credential nobody was shown
looks armed and is not. All three outcomes -- **minted/disclosed at a stated
time**, minted out of band, or **not minted** -- are stated on the birth
certificate as a fact, never scored as a check: an unminted token is a
fail-closed gateway, which is a posture. `intentops-gateway token
show-digest` reports what is on disk without ever recovering the value;
`token rotate --yes` replaces it and locks out every existing holder.

## The selftest registry -- one verb that runs every instrument

`packages/intentops-core/intentops_core/selftests/registry.py` is the counter
for the house rule *every detector ships `--selftest`*. It **derives** the
population -- every module under `packages/*/` owning a `selftest()` callable,
plus every script under `scripts/` advertising `--selftest` -- runs each in a
subprocess with a timeout and stdin closed, and reports
`name | PASS/FAIL/ERROR/TIMEOUT | duration`. Run it with
`intentops verify --all-selftests`, or
`python -m intentops_core.selftests.registry`.

**An instrument that cannot answer stays in the DENOMINATOR as ERROR.** That
is the whole reason it exists: `verify --selftest` named seven genesis
instruments by hand and the CI step named ten, and a hand list goes stale in
the direction that looks green. ERROR is never merged into FAIL either --
"this detector is broken" and "this detector found something" are different
findings, and merging them lets a decayed instrument hide inside the failure
count. A module that advertises `--selftest` without owning a callable is
reported as a discovery FINDING unless it is declared in `AGGREGATE_CLIS`
with a reason.

## The backend transport (how a permitted call is actually made)

`packages/intentops-gateway/intentops_gateway/transport.py` is the ONE way a
permitted backend call becomes a running process. The shipped default is still
`NullInvoker`, which refuses; `StdioInvoker` reaches a node only through the
`invoker=` argument of `tools.load_gateway_context`, so wiring it is an ACT and
never something that happened. It re-checks the registry and its own command
table at spawn time, reads the kill switch
`.intentops/gateway/transport-disabled` on EVERY call rather than caching it,
builds the child's environment from an allowlist of NAMES instead of copying
`os.environ`, and enforces the timeout and the output cap **by refusing** --
never by returning what arrived first, because a truncated frame that reads as
a result is the silent-failure shape. A T3 or T4 call never reaches it at all:
`tools._dispatch_backend` refuses at the gate first, which is asserted with a
spy rather than with a message.

## The reference MCP client (the S2 data point)

`packages/intentops-saddle-mcp/intentops_saddle_mcp/reference_client.py` is a
client that asks `intentops.gate` before it acts and performs its effect only
on an explicit `ALLOW`. Everything else -- `ASK`, `HALT`, a tool error, an
unreachable server, a verdict with no decision -- leaves the effect uncalled,
because a client that treats silence as permission is the failure this project
is organised around. Its recorded run is
`docs/falsifiers/S2-mcp-client-deny-2026-09-06.md` (**VERDICT: PASS**), and its
scope is one client: the `mcp-hosted` saddle's grade stays **candidate** and
its S2 stays `computed_not_enforced`, because nothing here observes a
third-party host.

## Stand-down

`.intentops/halt.marker` is **absent at birth**, and its PRESENCE is the fact:
the node is switched off, with no argument from it. Read ahead of everything
else, by every tool call.

---

*This index is a pointer list, not a specification. The specifications are
`docs/GENESIS.md`, `docs/SUBSTRATE.md`, `docs/SADDLE-CONTRACT.md` and
`docs/IDENTITY-REPO-CONTRACT.md`.*
