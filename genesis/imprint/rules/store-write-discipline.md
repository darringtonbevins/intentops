<!-- origin: .claude/rules/store-write-discipline.md (upstream v7.0.0, as of 2026-09-06) -->
<!-- generalized 2026-09-06 for genesis/imprint: precedents anonymized to store shapes and roles, estate module and report paths made generic; every rule and its reason kept -->

# Store Write Discipline -- no shared whiteboards

The upstream system's most-recurrent data-loss class: a store file written by
more than one process or session, where each writer reads the whole file,
mutates its copy, and rewrites the whole file. Two overlapping writers =
last-write-wins; one edit dies silently. Bitten four times before this rule
existed: 269 duplicate rows in a financial-import store; a memory index
clobbered mid-edit (88 -> 76 lines while two sessions edited it); a concurrent
hard reset wiping four in-flight slices; and a scheduler that held its
boot-time snapshot for HOURS and wrote it back whole -- predicted in analysis,
then observed live the same day.

## The rule: every multi-writer store declares one of three write models

1. **Per-item files** -- one file per record (an approvals queue is the shape).
   Collision-safe by construction. Prefer this for new stores when records are
   independent.
2. **Locked fresh-read RMW** -- hold the store lock across the
   load -> mutate -> save pair, read INSIDE the lock (a value read before
   acquisition is stale by definition), and replace atomically. Right for
   low-contention whole-file stores (todo lists, ledgers, state files).
3. **Journal + single reconciler** -- an append-only journal, with one
   OS-locked reconciler applying entries with fresh-read RMW. Required when
   writers cannot block or contention is real. This is the graduation path for
   a model-2 store that develops lock pressure.

**Never** load-early-write-late: any code path that loads a shared store, holds
the value across other work, then writes it back whole, is the defect --
however rare the collision window looks.

## Requirements

- **Consult the peer roster before shared-state writes.** In any
  possibly-concurrent session, before editing a shared store or a file another
  session may hold, ask the session registry who is at work now and whether a
  live peer's holdings overlap the paths you intend to write. A hit is a ROUTE
  signal (worktree / defer / journal / re-read), never a block; the locks and
  journals below remain the write-time enforcement. This is sight
  (proprioception), and it composes with -- never replaces -- the write models.
  **The consult fails by being skipped, not by being wrong.** Observed twice in
  one session: (a) a new archiver was built at one path when a better one had
  shipped at another three hours earlier -- already wired into the nightly
  checklist, which NAMED the correct path; (b) a peer's redesigned report file
  was overwritten as though it were this session's stale copy. Both recovered
  clean by luck, not by design. So the consult is **pre-write, not
  post-mortem**, and it extends to BUILDING: before authoring a new organ, glob
  the likely directories and grep the cadence checklists -- a name in a
  checklist means it should exist, and a file at that path means it already
  does.
- **"Unattributed dirty" is ambiguous, and peer-gone does NOT mean
  safe-to-write.** The consult reports overlap in two shapes: a LIVE peer
  holding a path, and a path merely dirty in the shared tree with no live
  claimant. Both hit in one session, with opposite correct answers. A config
  file came back unattributed-dirty and it was that session's own edit --
  proceed. Later a backlog file came back the same way after its peer had
  exited, leaving 162 uncommitted lines behind -- do NOT proceed. Resolve WHOSE
  the dirt is before deciding; the signal alone does not tell you. Two hazards
  survive the author's exit: a whole-file store write can entangle their work
  with yours, and committing the path at all sweeps their uncommitted lines
  into your commit under your message. Orphaned uncommitted work is also
  exposed to any concurrent hard reset -- surface it to a human rather than
  adopting it, because committing work you cannot vouch for is its own failure.
- A NEW store touched by more than one writer **names its model in the owning
  module's docstring at birth**. No model named = a review finding.
- Long-lived processes (daemons) re-read shared state at each work-cycle start,
  and a failed re-read is LOUD on the cycle result
  ([no-silent-failures.md](./no-silent-failures.md)), never a
  log-line-and-proceed.
- **Kernel-released locks only** (an advisory lock held on an open handle):
  the OS releases on process death, so there is no stale-lock reclaim, ever.
  PID files and command-line preflights are liveness probes, not locks.
- **A store lock takes a lock path, never the store path.** Precedent: a
  hand-rolled writer passed a 183 KB ledger itself to the lock primitive, which
  truncates the path it is handed expecting a sibling `.lock`, and cut the
  ledger to 11 bytes. It was recovered from version control; a gitignored store
  would have been gone.
- Accepted stores (a latest-pointer, a per-item queue, a
  single-writer-by-schedule store) carry their **revisit trigger** in the
  inventory -- acceptance is a ruling with a condition, not a shrug.

> Descriptive governance plus a Code-rung primitive. The store-lock module
> enforces where it is adopted; this rule makes adoption the default for
> anything new.
