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
nothing renders as a flat line, never as green.

## The governed tool gateway

`packages/intentops-gateway/` is where a tool call from another harness meets
this node: `tokens.py` holds the bearer record (`gateway-auth`), `backends.py`
is **default-deny** over what may be reached at all, and `tiers.py` classifies
every call -- a tool nobody classified takes `UNTAGGED_TIER`, never a
permissive default. Requests land in the node's own ledger. A bounded,
self-describing endpoint surface is a SAFETY property, not merely tidier
engineering: an agent handed a shell can do anything, and an agent handed
enumerable endpoints can only do what they expose.

## Stand-down

`.intentops/halt.marker` is **absent at birth**, and its PRESENCE is the fact:
the node is switched off, with no argument from it. Read ahead of everything
else, by every tool call.

---

*This index is a pointer list, not a specification. The specifications are
`docs/GENESIS.md`, `docs/SUBSTRATE.md`, `docs/SADDLE-CONTRACT.md` and
`docs/IDENTITY-REPO-CONTRACT.md`.*
