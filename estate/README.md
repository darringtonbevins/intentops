# The blank estate

Six manifests. Together they are the node's answer to *what is here, what can it
spend, what can it do, what has it been allowed, and what must be alive for any
of that to work.*

| File | Schema | Answers |
|---|---|---|
| `ESTATE-MAP.yaml` | `estate-map/v1` | which estates exist here, and the autonomy gradient over them |
| `ASSETS.yaml` | `estate-assets/v1` | what the estate **has** |
| `RESOURCES.yaml` | `estate-resources/v1` | what the estate can **spend** |
| `CAPABILITIES.yaml` | `estate-capabilities/v1` | what the node can **do**, and what each capability **owes** |
| `GRANTS.yaml` | `estate-grants/v1` | what authority has been granted, by whom, until when |
| `DEPENDENCIES.yaml` | `estate-dependencies/v1` | what must be alive for a capability to work |

Every manifest carries `schema`, `as_of` and its entries block. Every enumerated
field is a **closed vocabulary**: an undeclared value is a hard exit, never a
default of everything-applies.

---

## The one rule: `entries: []` is valid; a MISSING file is a HALT

An estate with no assets yet is a **true statement about a new node**. Nothing is
wrong; nobody has told it about anything. So a manifest whose entries list is
empty loads cleanly and the node comes online.

A **missing manifest is a field nobody read**. The loader has no way to tell
"this operator has no venture estate" from "somebody deleted the file" or "the
packaging dropped it", and the two have opposite consequences. Where a field is
load-bearing, missing means **HALT**, not default. So:

```
estate/ASSETS.yaml with entries: []      -> loads, node comes online
estate/ASSETS.yaml absent                -> HALT, with the remedy printed
```

The same discipline runs down into the entries themselves. `telemetry_source`
on a resource and `health_probe` on a dependency have **no defaults**: a
registry that half-understands its rows produces a control plane that runs and
is wrong. A dependency that cannot be probed is materialised as
`{"type": "unprobeable", "reason": ...}` and stays in the denominator, rather
than being dropped -- because a thing that leaves the population makes every
coverage number look *better*, which is exactly why nobody goes looking for it.

## This is deliberately the opposite of the trust roots

`config/trust-roots.yaml` inverts it: there, `roots: []` is a **HALT**.

The asymmetry is the point, and it is not an inconsistency.

- **A node with no assets is new.** Emptiness is its honest starting state and
  it can still tell the truth about itself.
- **A node with no trust roots can verify nothing.** Emptiness there is not a
  starting state, it is a blindness: every signature check would pass
  vacuously, and an unsigned artifact and a valid one would become
  indistinguishable.

So: emptiness is *expected* where it describes a world not yet grown, and
*forbidden* where it would silently disable a check.

## What genesis will and will not write here

Genesis materialises these six files at **G3 (ORGANS)** if they are absent, and
validates them whatever their origin. It writes `ESTATE-MAP.yaml` entries only
at **G6 (ALIGNMENT)**, from the operator's own answers.

**Genesis never creates a grant.** `GRANTS.yaml` stays `entries: []` until a
human puts something in it, and a grant whose `granted_by` is not this node's
operator root raises nothing: it is recorded as a WARN and is never applied. A
node inherits authority from the person answerable for it and from nobody else.

## Validating

```
python scripts/estate/estate_manifest_check.py                 # check ./estate
python scripts/estate/estate_manifest_check.py --selftest      # prove the check can fire
```

The selftest constructs each violation in a temporary tree and fails if any of
them passes validation. A detector that has never fired is indistinguishable
from a broken one.

The check runs at G3 and from checklists. It is **never a blocking pre-tool
hook** -- a contract check must not be able to wedge an unrelated session.
