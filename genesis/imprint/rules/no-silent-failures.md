<!-- origin: .claude/rules/no-silent-failures.md (upstream v7.0.0, as of 2026-09-06) -->
<!-- generalized 2026-09-06 for genesis/imprint: precedents anonymized to roles and subsystem shapes, estate names/paths/tool names removed; every rule and its reason kept -->

# No Silent Failures + Single-Writer Preflight

## No Silent Failures

> "I really do not like the idea of anything failing silently, to be honest."
> -- the operator of node zero, on the day this rule was made.

- Never let a caught exception degrade to a bare `logger.warning` +
  continue-as-success. If a step that was EXPECTED to run is skipped or failed,
  the result must carry an explicit visible signal (a status field plus a
  surfaced message), and overall success must reflect the partial failure.
- Distinguish INTENTIONAL skips (the operator passed a flag, a feature is
  disabled) -- those may be quiet -- from UNEXPECTED failures (a store,
  embedder, credential or port unavailable) -- those must be loud.
- In command output, show per-step status. A "succeeded" summary over a skipped
  step IS the bug. Precedent: an absorb pipeline reported `success=True` while
  **0 chunks** reached the vector store.
- Zero output is a state, not a success: a stage that produces nothing must
  render WARN or a flat line, never a green [OK].
- Applies across pipelines: ingest and index, secret propagation, data sync,
  outbound comms, fan-outs, scheduled cadences.

## The sibling failure: leaving the population, not failing in it

The rule above catches a step that RAN and failed quietly. Its sibling is worse
and is not covered by it: **the thing never enters the count at all.** A silent
failure leaves a suspicious zero. A denominator failure leaves a *better-looking
number* -- which is why it survives review, and why nobody goes looking.

Seven instances surfaced in a single session across unrelated subsystems, which
is what makes it a class rather than a bug:

| Instrument | How it left the population |
|---|---|
| dependency health probes | `health: null` -> type `none` -> the entry was skipped by the loader; absent from probed / passed / failed / findings alike |
| container inventory | a collector rewrite dropped enricher-detected health -> 8 of 38 containers invisible to every run |
| an approval queue | items read as "expired and unruled"; expiry is an **implicit denial**, and the queue looked healthy *because* they left it. One layer down: the expiry sweep OVERWROTE the `decided_by` field, so the record lost the ruling and the reading was built on the destroyed field -- the append-only history had kept it |
| an ordering consult | a `TypeError` swallowed into an `ordering_error` field, which the completeness check read as "no ordering present" -- the only deliberating body, discarded |
| backlog completion | `completed_at` written on 49 of 470 terminal items; throughput is unmeasurable, so nobody can say whether we are gaining ground |
| package probes | a fix real in the code and inert under the scheduled task -- passing in the one environment that could not fail |
| our own reporting | "467 -> 82 failures" where 81% of the drop was **deleting entries**, not fixing probes |

### Rules

1. **A refusal must stay in the denominator.** When something cannot be
   evaluated, emit a value the consumer counts and grades -- never `null`,
   never absence. Emit `{"type": "unprobeable", "reason": ...}`, which grades
   `ok=False` carrying WHY, rather than vanishing.
2. **Ask what the failure would look like.** Before trusting an instrument:
   *if this were broken right now, would anything change in its output?* If the
   answer is "the number would look slightly better", it cannot be trusted.
3. **A detector that has never fired is indistinguishable from a broken one.**
   Ship a test that proves it CAN fire (`test_the_guard_can_actually_fail`, a
   `--selftest` that constructs each violation). A green detector is evidence
   only when a red one was reachable.
4. **When a metric improves, check the denominator before celebrating.** Pass
   rates rise for two reasons and only one is repair. State the population size
   beside the rate, always -- a rate without its denominator is not a
   measurement.
5. **An expiry, a default, and a fall-through are all decisions.** Whoever set
   the timeout decided; nobody chose it at the moment it fired. If a default
   answers a question a human was asked, it is an implicit ruling and must be
   surfaced before it lands, not after.
6. **A reader that substitutes a default for a missing field has left the
   field out of the population.** The failure is silent AND plausible: nothing
   errors, the output looks right, and the one field that was never read is the
   one nobody checks. Where a field is load-bearing for fidelity (a model
   shape, a sensitivity class, a tier, a port, a threshold), missing means
   HALT, not default. "An undeclared kind is a hard exit" and "a missing
   sensitivity key resolves to quarantine, never to a class" are the house
   instances; a `.get(key, sensible_value)` on such a field is this defect.

Composes with the signal-integrity clause that a value produced without
evidence must be distinguishable in-band from a measurement, with the clause
that every detector states its operating point, and with the grounded-signal
rule that every instrument publishes its blind spots.

## Single-Writer Preflight

Two concurrent writers on one resource created 269 duplicate transactions in a
financial-import store (a stale PID was misread, so two pushers ran), and on a
later day a second session's tree-sweeping commit absorbed an in-flight
session's uncommitted edits.

- Before (re)launching a writer process: verify ZERO instances are already
  running **by COMMAND LINE**, never by PID file alone -- PID files go stale.
  Kill, confirm zero, then launch ONE.
- Before working in a tree that another session or daemon commits to: isolate
  in a separate worktree and merge back, or keep uncommitted windows to
  minutes. Two writers never share one working tree.
- A writer that emits to a shared store (a ledger, a vector collection, a
  journal, an approvals queue) must be idempotent or de-duplicated by
  distinctive IDs; if it is neither, the single-writer check is the only
  guard -- treat launching a second copy as T3-adjacent, not routine.

See [store-write-discipline.md](./store-write-discipline.md) for the three
sanctioned write models.
