<!-- origin: .claude/rules/knowledge-retention.md (upstream v7.0.0, as of 2026-09-06) -->
<!-- generalized 2026-09-06 for genesis/imprint: the ruling attributed to "the operator of node zero", estate store names and script paths made generic; every rule and its reason kept -->

# Knowledge Retention -- loss is a deliberate act, never a side effect

> "Let us not lose any knowledge or wisdom unless we are specifically ablating
> or obliterating."
> -- the operator of node zero, ruling the day the upstream system was found
> quietly shedding its own capital.

Four losses, none of them chosen: session transcripts auto-erased at 30 days
while the provider kept its own copy for years; thousands of generated insights
with no reader; a nightly distillation stage that had never once executed
behind months of "Success" stamps; and a crash absorbed by a guard baseline with
no write-up. That is the defect this rule retires: **knowledge may leave a node
only through a door with a name on it.**

## The three sanctioned exits

| Exit | Meaning | Requirements |
|---|---|---|
| **Ablation** | Targeted, deliberate removal (a store cut, a corpus trimmed, rows swept) | Named in the act: what, why, whose authority, what would restore it. Logged (commit message, tagout entry, or the owning ledger). Keyed on the AUTHORITATIVE key ([honesty.md](./honesty.md) hard-key rule) with the would-be-deleted set quantified first |
| **Obliteration** | Permanent destruction with no restore path (confidential scrubs, credential purges) | The operator's explicit ruling, **every time**. T4-shaped regardless of tier label |
| **Supersession** | Replaced with lineage | The old carrier stays reachable (a status flip plus `superseded_by`), never deleted |

Everything else is **retain, compress, archive, demote, or park**. Compression
is not loss (an index entry trimmed while the topic file keeps the body; old
transcripts zipped; hot demoted to warm). Deleting the only copy is the line.

**Parking CODE: the `.shelved` pair.** A prototype that was built, proven, and
then not shipped is kept as `<name>.shelved` **in place**, beside a sibling
`.md` note in the SAME directory that (1) names the file, (2) carries the
**measurement** that decided it, and (3) states the condition under which it is
worth **reopening**. The suffix is the tag; the note is the ledger entry.
Deleting the prototype loses the measurement that killed it, which is exactly
the knowledge the next person needs before trying it again; leaving it with no
note is [tagout.md](./tagout.md)'s untagged-switch shape. Enforced by a
zero-tolerance checker with a `--selftest` that proves it fires. Dead code under
any other suffix is not tagged and is not seen.

## Rules

1. **No auto-deleter without an archive step or a tagout.** A TTL prune,
   rotation, or cleanup routine that destroys the only copy must either archive
   first (compress to a cold store, verified before the source is removed) or
   carry a tagout-registered acceptance naming why loss is tolerable and when
   to revisit. A retention limit alone is not consent to lose.
2. **Accumulating records are merged into, never overwritten.** A whole-file
   write that replaces a record which accumulates across runs (a day's stage
   log, a journal, a ledger) is the
   [store-write-discipline.md](./store-write-discipline.md) defect wearing a
   retention face. Observed: a single-stage forced run would have replaced a
   whole day's run record.
3. **Capture without a consumer is not retention.** A store nobody reads decays
   invisibly. Every knowledge store names a consuming surface at birth, or its
   next review asks why it exists.
4. **Agent-introduced deleters land DISABLED.** Enabling loss is a reviewed,
   human-adjacent act, never a side effect of a delegated leg. Precedent: a leg
   in a workflow that was described as a read-only investigation silently
   re-enabled a destructive daily deletion routine; only stale in-memory daemon
   code prevented execution.
5. **Provider-side copies are not our retention.** That a vendor keeps a
   transcript for years does not satisfy this rule; the copy we can mine is the
   one on our own disk.
6. **The audit is mechanical, not a memory.** A retention-loss inventory
   classifies every deletion site as ALIGNED / SILENT-LOSS / DISABLED /
   NEEDS-TAGOUT; a SILENT-LOSS site is a finding to close, not a note. Re-sweep
   when a new deleter is born.

Aligned machinery to cite rather than rebuild: demote-only memory cascades with
a protected top rank, an append-only tagout ledger, an append-only wish
register, zip-before-unlink pruners, and any export that is rebuildable from
its source store.

> Descriptive governance -- guides behavior; enforcement lives in the owning
> stores' write models and in the audit above, never a runtime gate here.
