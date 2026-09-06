# The identity-repo contract

Design of record: `docs/reports/intentops-oss/design/genesis-design.md` sect. 10.

## The rule

Every node's identity lives in its **own** repository, separate from the framework
that runs it and pointed at explicitly by that node. This repository ships the
**contract**, never a filled identity — a node never adopts another node's identity;
it grows its own under the same contract, starting empty.

The first conformant instance of this contract exists privately, is not shipped with
this codebase, and is not published here — an identity repository is, by the nature of
what it holds, private by construction, not by an oversight in packaging.

## Binding, with precedence stated

A node needs to know where its identity lives before it can do almost anything else.
Three mechanisms can supply that, in this precedence order, with no default at the
bottom:

| Rank | Mechanism | Lifetime |
|---|---|---|
| 1 | An environment variable naming the repository | One process only. Never persisted to disk. |
| 2 | Passed explicitly at genesis (a path, a URL, or a request to create a new one) | Written into the node's local configuration at birth. |
| 3 | The standing binding already recorded in the node's local configuration | Used once set, until superseded. |

**A node with nothing bound halts at the organ-creation phase (G3) and states plainly
what it needs.** It does not silently create a local one to keep going — a fall-through
that answers a question nobody actually decided is exactly the failure mode this rule
exists to prevent.

## What genesis writes into a brand-new identity repository

When a node is told to create a new identity repository, it writes a conformant
skeleton: every required member present, every one empty and valid, every one stating
how it is written (single-writer append-only journal, per-item files, or a locked
whole-file store — never an unstated write model). In outline:

- A manifest naming the contract version, the exact framework version this node was
  born under, the operator's root fingerprint, and the empty lineage list this repo
  will accumulate over time.
- An `identity/` area holding the node's designation (never a name at birth), its
  consent record (written once, at the consent gate, signed by the operator), its
  founding-conversation transcript (written once, at the founding gate), and a log of
  epochs — birth is epoch one; a stand-down is a dated event here, never a deletion.
- An empty ordering store — a place for governing rulings — with both of its structural
  refusals armed: a ruling with no stated tension is rejected as a mere preference, and
  a ruling with no stated conditions is rejected as a mere slogan. Unmatched cases
  default to escalating to the operator, never to silently permitting.
- An empty alignment-interview state: the interview's structure is bound, but zero
  questions have been answered and zero predictions have been sealed.
- An empty memory area, with its generated indexes starting empty alongside it.
- A conscience area: an empty core-change review ledger, an empty lockout/tagout
  ledger, and an empty ledger of every action of this node's that reached a person who
  is not the operator — recording who was reached, what was done, whose authorization
  covered it, and when, but never that person's own data.
- An empty estate map, filled in only once the operator answers the boundary
  questions.
- Trust-material projections that are strictly public: the operator's public root, this
  node's own public certificate, and the release fingerprint this node was born under.
  A `.gitignore` inside this area refuses every private-key-shaped pattern by name.

## What genesis reads from an identity repository, and what it refuses

Genesis reads a **closed list** from a bound identity repository: its manifest, its
identity file, its recorded operator record, its public trust projections, and its
estate map. Everything else in the repository is mounted but is deliberately **not
interpreted** at boot time — a boot sequence that starts reasoning over a stranger's
past rulings before it has even confirmed who its own operator is would be acting on
that stranger's behalf, not its own.

Genesis refuses, outright, at load time:

- An identity repository whose declared framework-version requirement is incompatible
  with the running framework.
- Any file inside the repository that is shaped like a private key.
- Any code path that would statically import from an extension area of the identity
  repository (an identity repository is data; treating it as executable code is
  exactly the class of mistake this refusal exists to prevent).
- A manifest naming a different node as already bound to this repository.
- Rulings whose recorded operator-root fingerprint does not match this node's own
  operator. Such rulings are not deleted — they are marked as proposed only, and they
  never answer a live governing question until this node's actual operator re-rules
  them in their own terms. A node never inherits another operator's authority over
  itself by copying their identity repository.

## Dependency direction

An identity repository depends on the framework; the framework never depends on, nor
references, any specific identity repository. This is not a policy layered on top — it
is definitional. A framework whose core imports from one particular identity instance
is no longer general-purpose; it has become that instance's private extension wearing
a public name.
