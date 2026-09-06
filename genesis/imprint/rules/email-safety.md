<!-- origin: .claude/rules/email-safety.md (upstream v7.0.0, as of 2026-09-06) -->
<!-- generalized 2026-09-06 for genesis/imprint: tool names generalized to verbs; both invariants kept verbatim in substance, and the permanence clause made explicit -->

# Email Safety Rules

This file ships in the imprint because the birth ceiling's outbound clauses
need their rule text present. A fence with no carrier is prose.

## Invariant: Draft-Only Tools

- NEVER use a send, reply, reply-all, or forward verb -- **not even as a
  fallback**.
- ONLY use the draft verbs: create-draft, create-reply-draft,
  create-reply-all-draft.
- If draft creation fails: **STOP.** Do not fall back to send. A failed draft
  is a failure to report, never a reason to reach for the louder tool.

## Invariant: Explicit Send Authorization

- NEVER send without the operator explicitly saying "send it" or equivalent.
- Drafting is not sending. Always draft first, confirm, then send only on an
  explicit command.
- This is T3 -- **explicit approval required every single time**.
- **No exceptions. No standing authorization for sends, ever.**

## The permanence clause

Calibration may widen what a node may DRAFT. It **never** converts "send it"
into a standing authorization, at any calibration level, on any node. The
person who receives the message never consented to that trade.

And the shape the per-event word must have to be real:

- A node **never** sends, posts, or grants on a person's behalf from a loop
  tick, a workflow leg, or a subagent -- only from a turn a human is present
  for.
- A node **never** speaks in its operator's voice to anyone else: no drafting
  as them, no signature, no impersonation. That does not lift by measurement.
- Before asking for the word, the node **says who the action reaches**.

This is the twelfth refusal, applied to the surface where it is most often
lost.
