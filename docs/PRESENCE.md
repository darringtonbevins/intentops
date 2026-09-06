# Presence

Presence is where a node meets a person: the surfaces it can be addressed on,
the rule about who it may answer, the front door every message passes through,
and the record of every time it happened.

Design of record: `docs/reports/intentops-oss/design/genesis-design.md` sect. 7
(the design lives in the private working estate this seed was extracted from and
is cited here by section, not shipped as a path you can open in this repo).

Everything below is Code rung. **No module in `intentops_core.presence` makes a
model call, imports a vendor SDK, or opens a socket.** The model is a declared
seam whose shipped implementation returns nothing.

---

## The one invariant

> **A node answers only its bound operator. A message from anyone else is
> journaled and surfaced to the operator as an ask -- never answered
> autonomously.**

The reason is not that a reply to a stranger might be wrong. It is that **an
unsupervised reply is an outbound communication the operator never authorised**,
made in something very like their voice, on a surface where other people read
everything. That framing is what makes the rule obviously correct, and it is why
the rule is a fence on *authorship* rather than a filter on quality.

`intentops_core/presence/operator_rule.py` is that invariant as code. It answers
one question -- *may this surface answer this speaker?* -- with one of three
verdicts:

| Verdict | When |
|---|---|
| `ANSWER` | the speaker is the operator's declared identity on this channel |
| `SURFACE_TO_OPERATOR` | anyone else, **and every uncertainty** |
| `IGNORE` | the node recognised its own echo, and nothing else |

There is no path to `ANSWER` without a positive, declared identity match. An
unknown speaker, an unknown channel, a channel the operator record names no
identity for, and an operator record with no identities at all **all** resolve
to `SURFACE_TO_OPERATOR`. At birth the record is empty, so a newborn node
answers nobody until its operator tells it how they appear.

The operator record is `identity/operator/operator.yaml` in the node's identity
repository. There is **no default operator**: a missing record, an unparseable
one, or one with no root fingerprint halts. A node that invents an operator is a
node acting on a stranger's behalf, and the failure is silent, because the
invented operator answers every message perfectly happily.

## The front door

`intentops_core/presence/router.py` is the one router. Every inbound message
enters there and leaves as a `Route`: an intent id, a tier, a **handler name**,
whether taking it reaches outside the machine, and the reasons.

The order is the design:

1. **Who is speaking, before what they said.** A non-operator message is never
   classified into a handler at all -- classification is where a message
   acquires momentum, so the cheapest place to stop it is before it has any.
2. **Declared intent, then keywords, then the seam.** Bias left. A caller may
   declare an intent id and it is validated, never taken on trust; text falls to
   word-boundary keyword matching declared in the taxonomy; only what neither
   answers reaches the classifier seam.
3. **The seam ships as `NullClassifier` and returns nothing.** A node that wants
   a model installs one that satisfies the protocol. It can only ever *propose*
   a declared intent id, and the proposal passes the same escalation and ceiling
   as a keyword match.
4. **Escalation raises; the ceiling refuses.** Escalation rules can only raise a
   tier. A channel's ceiling never *lowers* one: an action above the ceiling is
   queued for the operator, because silently downgrading a tier is how an action
   walks under its own gate.

Ambiguity is not resolved by declaration order. Two matching intents produce a
disambiguation ask carrying the strictest tier of the candidates. Nothing
classified produces an ask too. `route` is a pure function and returns an
identical route every time, including the order of its reasons.

The router returns a handler **name**. It resolves nothing, imports nothing and
calls nothing, so a malformed taxonomy cannot execute anything by being loaded.

## The intent taxonomy

`config/intent-taxonomy.template.yaml` is the whole vocabulary: `<domain>.<action>`,
each declaring its tier, whether it is reversible and how, whether it reaches
outside the machine, its handler, and the literal keywords that reach it.

Nine generic domains ship -- files, code, queries, the host, version control,
data, governance, communication, planning. Domains belonging to a particular
line of work are an **estate overlay** the operator supplies, never core: a
framework that ships one operator's job description makes every other operator
delete something before they start.

`domains: {}` is valid and means "this node classifies nothing yet". A **missing
file halts** -- that is a field nobody read, and the loader cannot tell it from
packaging that dropped it. So does an undeclared key, a missing load-bearing
field, a tier outside T0-T4, a `reversible: true` action above T0 that cannot
say *how* it is reversed, an escalation rule that could lower a tier, and an
escalation matcher outside the closed vocabulary.

That last refusal has a history worth keeping: the reference implementation
wrote its escalation conditions as prose ("target contains 'prod'"), which no
code could evaluate -- so they documented an intent nothing enforced.

## Channels

`intentops_core/presence/channels.py` holds the adapter contract and exactly one
adapter, `NullChannel`, which reaches nobody. No vendor adapter ships here. The
interesting part of an adapter is not its protocol but the three facts it must
declare before the router will let it speak:

- is this surface **shared** (other people can read it) or **private**?
- what is this node's **own** identity on it, so it can recognise its echo?
- does sending on it **reach outside this machine**?

`unknown` is a real classification, not a missing one, and it resolves stricter
than `shared` everywhere it is read.

## The interaction journal

`.intentops/presence/interactions.jsonl` is the **interaction journal**: one
append-only record per inbound message -- which channel, how it is classified,
an opaque reference to who spoke, what the message was classified as, the tier,
whether the route reached outside the machine, what was decided, and what was
done about it.

**It holds no message text.** A journal that carries content inherits the
sensitivity of everything anybody ever said into it, which means it stops being
safe to read or keep, which means in practice nobody reads it. This one records
*that* something was said, with a character count and a short digest so a
specific message can still be tied to its record by whoever legitimately holds
the text. A `detail` value that is not a JSON scalar is refused, because a
nested blob is where content comes back in through the side door.

Write model: append-only JSONL under a kernel-released `StoreLock`, state as a
pure fold, no rewrites, **no unlocked fallback** -- if the lock is unavailable
the append is refused, loudly.

Its own consumer is the operator, an audit, and one check:
`python -m intentops_core.presence.journal breaches` lists every record where the
node answered somebody who is not its operator. That list must be empty, and it
is computed from the journal rather than trusted from the router -- so a router
bug leaves evidence the router did not have to volunteer.

## Probes and selftests

Every module carries `--selftest`, because a detector that has never fired is
indistinguishable from a broken one:

```
python -m intentops_core.presence.channels      --selftest
python -m intentops_core.presence.journal       --selftest
python -m intentops_core.presence.intents       --selftest
python -m intentops_core.presence.operator_rule --selftest
python -m intentops_core.presence.router        --selftest
```

Genesis probes `GEN-presence-journal`, `GEN-operator-rule` and
`GEN-intent-taxonomy` in `config/genesis-probes.yaml` ask whether a fresh
context window can still find each of these three organs.

## What this layer is not

It is **not a gate**. A route is an intention; `intentops_core.gate` is what
refuses an action. A T3 route is surfaced for approval, and nothing in this
package executes anything.

It is **not authentication**. Identity is an opaque per-channel string
comparison against what the adapter already resolved. If a channel lets one
account impersonate another, nothing here can tell.

Every module states its remaining blind spots in its own docstring. Read them
before trusting a clean run.
