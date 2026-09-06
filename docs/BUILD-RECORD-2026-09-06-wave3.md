# Build record — seed wave 3 (2026-09-06)

Third and last build wave of the public seed, on top of
[BUILD-RECORD-2026-09-06.md](BUILD-RECORD-2026-09-06.md) (the seed) and
[BUILD-RECORD-2026-09-06-wave2.md](BUILD-RECORD-2026-09-06-wave2.md) (the first
correction pass).

Same discipline as wave 2, and it is the point of these documents: every
adversarial verdict is reproduced with its `claim | refuted | applied` triple.
A correction that was **not** applied says so and says why — an unapplied
correction that goes unrecorded is exactly the defect
[`no-silent-failures`](../.claude/rules) rule 1 exists to prevent, and a build
record that only lists wins is a roadmap wearing a receipt's clothes.

**One corrector ran this wave.** Everything below was applied by it, in this
tree, and re-measured after.

---

## 1. What wave 3 built

| Surface | Where | Shape |
|---|---|---|
| **Knowledge layer** | `packages/intentops-core/intentops_core/knowledge/` (`rings`, `absorber`, `collections`, `embedder`, `coverage`) | Ring classification with quarantine as the missing-key answer; the absorber contract and its watermark; the collection registry over one embeddings table and the write gate (ring + sigma); a provider-neutral embedder that refuses rather than inventing a vector; ring and corpus coverage with high-water-mark posture. Templates: `config/ring-taxonomy.template.yaml`. |
| **Metabolism** | `packages/intentops-core/intentops_core/metabolism/` | The four-stage cadence (absorb → distill → crystallize → method), the heartbeat, the crystallization document contract, the assimilation verbs, and the grok cycle as a dry-run recorder. Every stage ships **disabled**; every stage is a seam with a null implementation. Template: `config/metabolism-cadence.template.yaml`. |
| **Gateway core** | `packages/intentops-gateway/` | The governed tool surface: bearer-token auth on every request, a default-deny per-harness backend allowlist, per-call tier classification with untagged → T4/BLOCK, T3/T4 as an ASK that files an approval and never executes, an append-only request ledger, and the four built-in reads. Policy: `config/gateway-policy.yaml`. |
| **Presence organs** | `packages/intentops-core/intentops_core/presence/` | Channels, the intent taxonomy, the operator rule, the router and the interaction journal. Doc: [`docs/PRESENCE.md`](PRESENCE.md). Template: `config/intent-taxonomy.template.yaml`. |
| **Belief carriers** | `intentops_core/validation/belief_carriers.py`, `validation/responsiveness.py` | The specification the currency instrument reads, and the correction half of notice-and-correct. Template: `config/belief-carriers.template.yaml`. |
| **Probe coverage** | `scripts/genesis/probe_coverage_check.py` | Every organ a node has must be findable by a fresh window. Fails on a planted unprobed organ; carries `--selftest`. |
| **Boot anchors** | [`docs/BOOT-ANCHORS.md`](BOOT-ANCHORS.md) | The pointer index a cold window carries. |

---

## 2. Verifier verdicts

### V1 — the gateway core

> **Claim.** "The gateway core serves nothing without a bearer token, refuses
> unlisted backends by default, classifies every call by tier with untagged
> tools denied and T3/T4 never executed, and never resolves an identity to
> anonymous."
>
> **Refuted: NO.** 56 of 60 driven checks passed against a real
> `GatewayServer` on a loopback port with a spy invoker, a three-backend
> registry and a two-harness policy.

The verifier drove the live server rather than reading it. Confirmed: an
unminted node, a missing token, a wrong token, a blank `Bearer`, a token
prefix, a wrong scheme, a token in `_meta`, in the query string or in a cookie
all 401; `X-Harness-Id` alone never authenticates; no `os.environ` read exists
anywhere in the package, so there is no environment-variable bypass; the MCP
surface refuses `initialize` and `tools/call` without a token and rotating the
token kills an initialized session's credential; unlisted, unknown-harness,
empty-grant, missing-header, always-denied and granted-but-undeclared backends
are all refused with `default: deny`; an absent `backends` key HALTs; untagged
tools BLOCK at T4 with the invoker never called; declared T3/T4 ASK, file an
approval and never execute; the boot image carries neither the token nor its
digest; and the string `anonymous` appears nowhere in ledger output.

**The weakness found** did not refute the claim but **overstated `tiers.py`
rule 2**: the second, argument-based reading was NAME-GATED. The shell classes
fired only for tools named exactly `Bash`, `PowerShell` or `Shell`
(case-sensitive), and the outbound/disclosure branches only for names prefixed
`mcp__` — while `tools._verdict_for_call` passed the **bare backend-local**
name. Observed: declared-T1 `listed__shell` carrying
`git push --force origin main` → **ALLOW T1 and executed** via the spy invoker;
the identical call named `Bash` → ASK T4. The declared tier is the floor by
design and the shipped invoker is Null, so this was never a default-open path
— but the docstring promised a reading the code did not perform.

| # | Correction | Applied |
|---|---|---|
| 1 | Feed the core classifier a name it actually reads for backend calls | **YES** — `tiers.classify_call` takes `classifier_name`; `tools._verdict_for_call` passes `mcp__<backend>__<tool>` and records it as `classified_as` in the detail. A name shown to a classifier only; nothing about execution changes and the merge can still only raise a tier. |
| 2 | Fire the shell classes on a non-empty `command` regardless of tool name | **YES** — `classify.classify_reaches_reality` now reads the field, matching what `classify_outbound_comms` already did. `SHELL_COMMAND_TOOLS` survives as documentation of the reference host's names and is no longer the gate. The file-write classes stay name-gated on purpose, and the constant now says why: `file_path` appears innocently on unrelated tools far more readily than `command` does. |
| 3 | State the real operating point in BLIND SPOTS and in the package README | **YES** — both now enumerate the three things the arguments reading actually reads, and say that everything else leaves the declared tier as the only bound. |
| 4 | Add the negative test | **YES** — `test_a_shell_by_any_other_name_is_still_a_shell` (four names × two commands, plus a no-command control) and `test_a_backend_send_verb_reaches_the_outbound_branch`. Both fail against the pre-correction code. |
| 5 | `intentops.classify` must honour the backend grant | **YES** — the allowlist check now runs before a backend address is classified, refusing and recording exactly as `_dispatch_backend` does. Built-ins and unnamespaced names are unaffected: there is nothing to grant. Test: `test_classify_does_not_leak_a_backend_to_an_ungranted_harness`. |
| 6 | `bool(include_rule_text)` treats the string `"false"` as true | **YES** — a non-boolean is now refused by name. Coercion here served the *opposite* of the ask. Test: `test_include_rule_text_must_be_a_boolean`. |

Two observations the verifier made and did not raise as corrections are
recorded here rather than dropped: HTTP checks auth before stand-down while
MCP checks stand-down before auth (both refuse; ordering only), and that
ordering is left as-is.

### V2 — the knowledge layer

> **Claim.** "The knowledge layer resolves a missing ring key to quarantine,
> refuses undeclared vocabulary, keeps refusals in the denominator of the
> coverage instruments, and carries no estate source types or absorbed data."
>
> **Refuted: YES**, on two of four clauses.

Confirmed and unchanged: `ring_from_metadata` resolves `None`, `{}`, a missing
key, a `None` value and an unmappable value to QUARANTINE, and `gate_write`
never loosens; `load_taxonomy` HALTs on eight classes; a read error grades
DEGRADED first and a corrupt ledger line becomes a LOST record; and there is
no estate residue — exposure gate CLEAN over 274 files, trust material CLEAN
over 284, and the only `source_type` literals in the package are `local-file`
and test placeholders.

**Defect A — the ring taxonomy had no consumer on the write path.**
`gate_write` ringed a row from the collection's declared sensitivity alone.
Nothing called `RingTaxonomy.ring_for` outside `rings.py` and its own tests, so
an operator who filled `.intentops/knowledge/ring-taxonomy.yaml` changed
*nothing* — capture without a consumer, and a false organ description in
`genesis/organs.py`, which named readers that did not read.

**Defect B — a typo'd gate mode was coerced, then recorded as a measurement.**
`INTENTOPS_RING_GATE=enforcce` resolved to `observe` with no in-band signal,
and `ring_coverage` then wrote `gate_mode: observe` into the append-only
ledger — a typo made permanently indistinguishable from a deliberate posture.

**Defect C — the coverage CLI graded a store nobody wired.**
`python -m intentops_core.knowledge.coverage` measured a freshly-built
`NullVectorStore` and printed `NOT-YET-ARMED … nothing to classify` at exit 0
on **every** node, populated or not: the instrument's own blindness rendered
as a clean posture.

**Defect D — a non-mapping `row.metadata` crashed the instrument** out of
`ring_coverage` with no `Reading` at all, rather than grading the collection
DEGRADED inside the population.

| # | Correction | Applied |
|---|---|---|
| A | Make `gate_write` read the operator's taxonomy | **YES**, with one deliberate narrowing of the suggested formula. `gate_write` takes `taxonomy=`; `NullVectorStore` and `PostgresVectorStore` take it at construction and pass it. Precedence is strictest-wins across an **explicit** taxonomy row, the collection's declared sensitivity and the row's carried ring, so a ruling can only narrow; new `RING_STATUSES` member `classified`. **Taxonomy SILENCE is not a quarantine verdict**: the verifier's `stricter(taxonomy.ring_for(...), spec.sensitivity)` would have quarantined every write on a node whose taxonomy is still blank — which is every new node — so `RingTaxonomy.declared_ring_for` was added to tell *the operator ruled* from *the operator said nothing*, and silence leaves the collection's declaration standing. Five selftest paths and `test_the_operator_taxonomy_is_read_by_the_write_gate` cover it, including that a taxonomy cannot loosen a collection. `genesis/organs.py` and the template now name the real reader and say the coverage oracle does **not** read it. |
| B | Refuse an undeclared gate mode instead of coercing it | **YES** — `gate_mode` HALTs on a present-but-undeclared value with a remedy; unset, and set-to-empty (how a shell unsets), stay `observe`. The two selftest paths that asserted the old coercion now assert the halt. **One thing the correction did not anticipate:** the halt would have taken the coverage instrument down with it, so `coverage._gate_mode` catches it, records `the ring gate mode is unreadable` as an error and reads `gate_mode: "unreadable"` — which forces DEGRADED. The finding stays in the population; it does not become a crash. |
| C | Refuse to grade an unwired store | **YES** — `--dsn` added; without it the CLI prints `UNMEASURED: no store is wired to this CLI` naming why a `NullVectorStore` built there would read NOT-YET-ARMED on every node, and exits **2**. |
| D | Grade a bad row at collection granularity | **YES** — the counting loop moved inside the per-collection guard and a non-`Mapping` metadata raises a named `TypeError` caught there; the collection is recorded as an error and leaves `measured`, and posture reads DEGRADED. |

### V3 — probe coverage and genesis

> **Claim.** "Every organ the seed declares has a probe row, the probe-coverage
> check fails on a planted unprobed organ, and a fresh genesis `--dry-run`
> reads G7 PASS with an honest belief-carrier count."
>
> **Refuted: NO**, and executed rather than read: 34/34 owed members satisfied
> with one exemption carrying its reason and one member reported outside the
> population; a planted gap (`gateway-token` removed from `GEN-gateway`'s
> covers) produced `MISSING A PROBE ROW (1)` at exit 1; and a temp-dir genesis
> reached G7 PASS.

No corrections. **This verdict was truncated in transmission** at the
aliveness line; nothing was applied from it, and nothing in the applied set
depends on its tail.

---

## 3. Test totals, after every correction

Full suite: **1,327 passed, 77 skipped** (`pytest -q tests/`, 49s).
Wave-2 baseline was 962; the four skipped-block groups are the live-service
tests that stay skipped by design.

**The cold-run claim, stated with its conditions.** The suite **passes cold
with `git` on `PATH` and a resolvable home directory**; those two host
prerequisites are now declared in `CONTRIBUTING.md` and `docs/quick-start.md`
rather than assumed. Never read this as "passes with the environment
stripped" unqualified — that phrasing claims a population the run never
covered.

- **`git` absent:** measured 2026-09-06 by running the two git-dependent
  files with `git` off `PATH` — **45 passed, 3 skipped**, no collection
  error. Before this pass those three raised `FileNotFoundError`, because
  `subprocess.run` raises before any returncode check can fall back.
- **Home unresolvable:** `assemble_corpus` no longer calls `Path.home()`
  unless a home-group entry is declared, and a `RuntimeError` becomes the
  named corpus error `home directory unresolvable and a home-group entry is
  declared` rather than a crashed probe run.
- **`git archive` export (no `.git`):** **961 passed, 78 skipped** at `HEAD`
  — the wave-2 baseline, because this wave's work is uncommitted. The extra
  skip over the working-tree run is the export's missing repository.

| Suite | Tests |
|---|---|
| `tests/test_knowledge.py` | 84 (79 before this pass; 5 added or rewritten) |
| `tests/test_metabolism.py` | 117 |
| `tests/test_gateway.py` | 58 (54 before; 4 added) |
| `tests/test_presence.py` | 44 |
| `tests/test_belief_carriers.py` | 40 |
| `tests/test_docs_claims.py` | 20 |

Module selftests, all re-run after the corrections:

```
knowledge.rings            29 paths fired, 0 failed
knowledge.collections      51 paths fired, 0 failed
knowledge.coverage         29 paths fired, 0 failed
knowledge.absorber         22 paths fired, 0 failed
knowledge.embedder         24 paths fired, 0 failed
metabolism.heartbeat       21 paths fired, 0 failed
metabolism.cadence         23 paths fired, 0 failed
presence.router            16/16 paths behaved as declared
gate.classify              PASS (0 failure(s))
validation.belief_carriers PASS
validation.responsiveness  PASS
intentops_gateway selftest 5/5 ok, rc=0
```

---

## 4. Gate verdicts

Every gate re-run after the last correction, in this tree:

```
exposure gate (tree)      VERDICT: CLEAN
                          274 files scanned, 1 skipped, 0 UNSCANNED, 72,854 lines
                          21 name digests, 4 shapes, 10 allowed paths, 9 marker lines
exposure gate (--history) VERDICT: CLEAN
                          9 commits; 18/18 identity checks matched an allowlisted
                          identity; 1 message line matched the copyright shape
trust material            VERDICT: CLEAN   findings 0 / 284 scanned
                          1 UNSCANNED: release/v0.1.0-commit.txt.ots (binary)
manifest check            OK: 15/15 bundle artifacts match the manifest
probe coverage            every member owing a probe has one
                          1 exempt (locks) with its reason; 1 outside the
                          population (estate); 0 orphan covers
probe coverage --selftest PASS (planted unprobed organ caught)
estate manifest           VERDICT: CONFORMANT   6/6 manifests, 0 warnings
estate manifest --selftest SELFTEST PASS   24/24 cases behaved as declared
claims check              VERDICT: CLEAN
genesis --dry-run         G0 WARN, G1 WARN, G2-G7 PASS, final state G7, rc=0
  (temp node, INTENTOPS_GENESIS_UNSIGNED_DEV=1)
  G3: belief carriers bound: 2 sources; probes 42/42 PASS, 0 ABSENT,
      0 truth-failures
```

The two WARNs are the honest state of an unborn clone and are unchanged from
wave 2: G0 finds no host marker, and G1 finds roots still `proposed` because
no trust-root ceremony has run for this build. Both are documented in
[`docs/quick-start.md`](quick-start.md) and
[`docs/TRUST-CEREMONY.md`](TRUST-CEREMONY.md).

---

## 5. Remaining issues

Open, and stated here rather than carried silently:

1. **The taxonomy is load-bearing only when a caller wires it.** `gate_write`
   reads it, but no shipped code path constructs a store with
   `taxonomy=load_taxonomy(...)` — a node's own instrument must pass it. The
   template says so in the section headed *WHO READS IT, EXACTLY*, and the
   organ description no longer overstates it. Closing it properly means
   deciding where a node loads its taxonomy, which is a wiring decision, not a
   correction.
2. **`coverage --dsn` is untested against a live database.** The CLI now
   refuses to grade an unwired store, and the wired path constructs a
   `PostgresVectorStore` whose driver is an operator install. The seed has no
   live-service tests by rule, so that branch is exercised only to its HALT.
3. **The gateway's arguments reading is still narrow, by construction.** It
   reads a `command` field, credential-shaped file paths, and `mcp__`-prefixed
   outbound verbs. A backend whose destructive verb travels in a field none of
   those touch is invisible to it, and the declared tier is the only bound —
   which is why untagged is a refusal. The operating point is now stated in
   three places instead of overstated in one.
4. **`intentops.classify` reveals a verdict for an ungranted backend** — fixed
   — but a *granted* harness can still classify a tool that does not exist on
   that backend and receive the untagged T4/BLOCK answer. That is the correct
   answer to the question asked and is not treated as a leak.
5. **V3's verdict was truncated in transmission.** Its checks were reproduced
   independently in section 4 (probe coverage, its selftest, and a real
   genesis dry run), so nothing rests on the missing tail — but the record
   should say that the tail was never read rather than imply a complete
   verdict was received.
6. **Wave 3 was verified by three legs and corrected by one.** The corrections
   above were applied and re-measured, not independently re-reviewed. A second
   adversarial pass over this diff has not run.
