# `identity/` is a POINTER, never a store

Nothing in this directory holds a node's identity, and nothing ever will. This
file exists to say where identity lives and how a node binds to it. If you find
yourself writing a ruling, a memory, a consent record, or a key into this
directory, the mistake is not the file you are about to write -- it is that you
have started treating the framework as though it were somebody in particular.

An identity repository belongs to a node. The framework belongs to everyone who
clones it. Keeping those two things in different repositories is not a policy
layered on top; it is what makes this a framework rather than one node's private
extension wearing a public name.

Contract of record: [`docs/IDENTITY-REPO-CONTRACT.md`](../docs/IDENTITY-REPO-CONTRACT.md).
Validator: [`scripts/identity/identity_repo_check.py`](../scripts/identity/identity_repo_check.py).

## How a node binds its identity repository

Three mechanisms, in this precedence order, **with no default at the bottom**:

| Rank | Mechanism | Lifetime |
|---|---|---|
| 1 | The environment variable `INTENTOPS_IDENTITY_REPO` | One process. **Never persisted.** |
| 2 | An explicit argument at genesis: a path, a URL, or a request to create a new one | Written into the node's local configuration at birth. |
| 3 | The standing binding already recorded in the node's local configuration | Used until superseded. |

**A node with nothing bound halts and says what it needs.** It does not create a
local identity to keep going. A fall-through here would answer a question nobody
decided, and the question is *whose node is this*.

The environment variable is the strongest and the shortest-lived on purpose: it
is how you point a node at a different identity for one run without changing
what it is. Persisting it would turn a one-off into a standing fact that nobody
recorded deciding.

To check a repository against the contract:

```
python scripts/identity/identity_repo_check.py --identity-repo <path>
python scripts/identity/identity_repo_check.py --selftest
```

## What a conformant repository carries at birth

Every member present, **every one empty and valid**, every one declaring how it
is written. An empty ordering store is a true statement about a node that has
ruled nothing yet; a *missing* one is a field nobody read.

The full member list is the contract's, and the validator's `REQUIRED_MEMBERS`
table is its machine-readable form -- one definition, not two that drift. In
outline: a manifest, an `identity/` area (designation, consent, epochs, the
operator record), an ordering store, a wish register, alignment-interview state,
a memory tree, a conscience area (core-change reviews, lockout/tagout, and the
ledger of every action of this node's that reached a person who is not its
operator), an estate map, a knowledge manifest of pointers, public trust
projections, and a `.gitignore` that refuses every private-key shape by name.

Two examples live under
[`tests/fixtures/`](../tests/fixtures): one conformant, one deliberately not.
Both are fictional -- there is no real node, operator, key or person in either.

## What never enters an identity repository

- **A private key of any kind.** It lives in a hardware token, a TPM, an OS
  credential store, or a passphrase-wrapped file that is not tracked. The
  refusal is checked by
  [`scripts/ops/trust_material_check.py`](../scripts/ops/trust_material_check.py),
  it is zero-tolerance, and it has no exception list -- including for tests and
  fixtures, which is why the negative fixture contains no key-shaped file and
  that case is constructed in a temporary directory instead.
- **Another operator's authority.** A ruling or grant recorded under a root that
  is not this repository's own is not deleted -- it lands marked proposed and
  escalate-only, and it never answers a live governing question until this
  node's own operator re-rules it in their own terms. A node does not inherit
  authority over itself by copying somebody's repository.
- **A name the node gave itself.** A node is born with a designation. A name is
  ratified by its operator, and the ratification is recorded beside the name.

## The first instance

The first conformant instance of this contract is Lumina's. It is private by
construction -- an identity repository holds an account of one node's conduct
and beliefs -- and it is deliberately not shipped here. What ships is the shape
it has to satisfy, which every later node satisfies in its own terms.
