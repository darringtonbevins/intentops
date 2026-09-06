<!-- origin: .claude/rules/tagout.md (upstream v7.0.0, as of 2026-09-06) -->
<!-- generalized 2026-09-06 for genesis/imprint: the directive attributed to "the operator of node zero", estate backlog ids removed, ledger path kept because the store itself ships; every rule, tier and reason kept -->

# Switch Tagging -- Lockout/Tagout for Anything Disabled

> "If you see a switch turned off, you cannot assume that it is not either on a
> circuit breaker, or switched off on purpose. We need to always implement a
> sort of tag out / tag in on things that are disabled ... everything should be
> traceable, and we can leave breadcrumbs, instructions, and warnings in the
> higher surfaces ... as long as there is a chain of custody along all
> switching."
>
> "It also means that we can know if we are fully remediated, or if we need to
> prioritize a blocking problem."
>
> -- the operator of node zero.

An open switch carries no information about WHY it is open. Off-by-design and
off-by-neglect look identical from the outside, so the next person either
re-energizes something that was protecting them, or leaves off something that
was protecting them. Both failures are silent.

## The origin incident

A lint rule that catches **undefined names** sat in a global ignore list for
five months. It *had* a comment -- "many in auto-generated stubs, will fix
incrementally" -- and that is precisely why nobody re-examined it. The tag
carried no authority, no re-energize condition, and no reference, and its
stated rationale was false (zero such stubs existed; 30 hits, not "many"). It
was swept in by alphabetical adjacency with three cosmetic rules during a bulk
style cleanup, disabling the rule in the edit-time hook AND in CI at once. The
bill: an undefined helper reached the pre-tool gate, the gate failed closed on
every shell tool, and only a human could repair it.

**An unverifiable tag is worse than no tag: it looks accounted for.**

## Comment real estate is expensive -- tag, don't narrate

The inline tag is a POINTER ONLY. One greppable token at the switch:

```
TAGOUT: LOTO-2026-03-06-LINT-UNDEFINED-NAME
```

The record lives in `.intentops/loto/LEDGER.yaml`. The deep history lives in
the commits, backlog items and knowledge stores that entry references -- it is
never duplicated into the comment. When history is added, the comment does not
grow; the ledger entry gains a reference.

Where the switch has no comment real estate at all (a disabled scheduled task,
a production flag, a vendor console toggle), the tag lives in a carrier file
under `.intentops/loto/carriers/` and the ledger points at it.

## Tiers -- by blast radius of the thing switched OFF

Not by how hard it was to disable. Higher tier, stricter tag.

| Tier | Meaning | Requirements |
|---|---|---|
| **T0** | Cosmetic; no runtime behaviour change | id, reason |
| **T1** | Local, bounded behaviour change | + reference |
| **T2** | A subsystem runs degraded | + reference |
| **T3** | A **safety or detection control is off** -- a lint rule that catches a NameError, a gate, an audit, an alarm, a disclosure control | **+ authority + reenergize_when** |
| **T4** | A destructive-capable routine is disabled **as a safety interlock** | + authority + reenergize_when; re-energizing is **always** human-gated, never auto |

T3 is the tier that bit us. A detection control switched off with no stated way
back is not parked, it is abandoned.

## Chain of custody

`chain` in the ledger is **append-only**. Every flip appends a link carrying
`action` (tagged_out / tagged_in), `at`, `by`, `authority`, `reason`,
`reenergize_when`, `evidence`. The ledger's `state` must equal the last chain
action, and the checker verifies the tag is still physically present at the
switch -- so **a switch flipped without appending a link is caught**, because
the ledger says one thing and the file says another.

## Never hand-edit the ledger -- use the sanctioned writer

The append tool is the only supported way to write the ledger. It does locked
text surgery (the file's human formatting and comments survive) and validates
the whole document BEFORE the atomic replace; a failed validation writes
nothing.

```
# mint a NEW tagout (lands tagged_out)
loto_append --action create --id LOTO-YYYY-MM-DD-SLUG \
    --tier T3 --what "..." --carrier path/to/switch.py \
    --by "..." --authority "..." --reason "..." --reenergize-when "..."

# flip an EXISTING one
loto_append --action tagged_in --id LOTO-YYYY-MM-DD-SLUG \
    --by "..." --authority "..." --reason "..."
```

Creating an entry by hand is the failure this paragraph exists to stop.
Precedent: before the writer could CREATE (only amend), every new tagout was a
session rewriting the whole ledger -- the shared-whiteboard write that
[store-write-discipline.md](./store-write-discipline.md) forbids -- and one
such hand-rolled writer truncated the ledger to 11 bytes. **The ledger has a
writer; use it.**

The tier gate is enforced there, not merely described here: `--tier T3` or `T4`
without a `--reenergize-when` is **refused**, because a safety or detection
control switched off with no stated way back is abandoned, not parked.

## The remediation oracle

The checker validates and then reports posture:

- **REMEDIATED** -- nothing safety-critical is off.
- **ATTENTION** -- T3/T4 controls are off, but every one is governed (authority
  plus a stated way back). Parked, not forgotten.
- **BLOCKED** -- either a tagout is **ungoverned** (missing authority or
  `reenergize_when` -- the origin-incident shape), or its `reenergize_status`
  is `ready`, meaning its condition is already satisfied and it should have
  been switched back on. Exits 1.

This is what makes "are we fully remediated?" a mechanical question rather than
a memory. When a control's exit condition is met, set
`reenergize_status: ready` and it becomes BLOCKING until someone actually flips
it -- so it cannot quietly stay off.

## Rules

1. **Never disable anything without a tag.** Lint rule, feature flag, scheduled
   task, test skip, `enabled=False`, kill-switch sentinel, commented-out call.
   No tag, no disable.
2. **Never re-enable a T3/T4 without reading its ledger entry first.** It may
   be an interlock holding back something destructive.
3. **Bulk suppression separates will-raise from will-look-untidy.** Justify
   real-bug rules individually. A correctness rule triaged alongside its noisy
   alphabetical neighbours is how the origin incident happened.
4. **"Will fix incrementally" is a deferral, not a reason.** It needs a tracked
   item and a `reenergize_when`, or it is not a tag.
5. **Run the oracle at wrap or close** alongside the other checkers, and any
   time the question "is this done?" comes up.
6. **A refusal that over-blocks gets routed around, and a routed-around
   refusal protects nothing.** When a tag is holding back work that is
   legitimately the operator's, that is a reason to re-rule the control, not a
   reason to leave it off untagged.

> Descriptive governance plus a Code-rung validator. The checker gates only
> where a checklist invokes it -- a tagging ledger must never wedge an
> unrelated session.
