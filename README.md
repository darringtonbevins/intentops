<!-- category and first sentence pending the operator's ratification (decision card oss-category-governance-framework) -->

# IntentOps

**Governed Intent → Deterministic Execution**

> **Status: 0.1.0 public seed. Licence: [Apache-2.0](LICENSE). Copyright (c) 2026 Darrington Bevins -- see [NOTICE](NOTICE) and [PROVENANCE.md](PROVENANCE.md).**
> This is the first public extraction of the framework: the governance core, the genesis state machine and the reference Claude Code saddle, with a clean history. Roadmap and open rulings are in `docs/`.

IntentOps is a governance framework for AI agents: a deterministic, tier-classified
action gate; a deliberative council and precedent store above it; and a genesis that
grows an aligned, accountable operator identity beneath it — installed into the agent
runtime you already use.

Agent frameworks get you an agent that runs. IntentOps gets you one that is allowed
to — and that knows who it answers to.

*(Category, name, and every claim above are carried by the evidence in
`docs/reports/intentops-oss/design/category-ruling.md` sect. 0 and sect. 4. This
README quotes that ruling; it does not re-argue it.)*

---

## The five-layer model

IntentOps separates *what runs* from *what is allowed to run* from *what it grows into*.
Full definitions: `docs/reports/intentops-oss/design/genesis-design.md` sect. 3.

| Layer | Name | Ships in this repo? | Depends on | What lives here |
|---|---|---|---|---|
| **L0** | Host runtime ("the horse") | no | — | The agent harness you already run — Claude Code today; a second host is a roadmap item, not yet proven (see "What never ships" below) |
| **L1** | Harness adapter ("the saddle") | yes | L2 | The enforcement port for one host runtime: hook chain, gate wiring, boot-corpus delivery, halt handling. `packages/intentops-saddle-claudecode/` is the reference adapter |
| **L2** | Framework core (**primary**) | yes | nothing above it (target — see sect. 2.3 of the genesis design for the honest correction on this) | Invariants, the T0–T4 tier ladder and reaches-reality classifier, the two councils, the ordering store, the validation family, the trust chain, the identity-repo contract, genesis itself. `packages/intentops-core/` |
| **L3** | Operative ("the program") | yes, as an entrypoint | L2 | What `intentops genesis` instantiates — a node that boots, grows, forms an identity, and aligns with its operator. Ships as the genesis driver and its documentation, never as a filled-in example |
| **L4** | Identity instance | **no** | L3, L2 | The specific, lived identity a node grows — memories, wishes, rulings, a filled alignment interview. Never shipped; private by construction. The first conformant instance of the identity-repo contract is private and is not part of this codebase (see `docs/IDENTITY-REPO-CONTRACT.md`) |

An estate overlay (the connectors, macros, and data a particular deployment adds) sits
outside this model entirely and never ships here.

---

## What ships

- The framework core: invariants, tier ladder, reaches-reality classifier, gate engine,
  the Posture Council and the Moral Compass Council, the ordering store, the validation
  family (grounded-signal / witness / still-true), the trust chain, store discipline.
- The Claude Code reference saddle (adapter), as a template you install into your own
  agent runtime configuration.
- The genesis state machine: a deterministic sequence that turns a fresh clone into a
  node with its own identity, its own trust material, and its own alignment record with
  its operator. See `docs/GENESIS.md`.
- A blank estate: manifests with `entries: []`, describing a node that has nothing yet
  rather than omitting the question. The one exception is named and exact — the three
  services of the genesis graph are declared in `estate/DEPENDENCIES.yaml`, because they
  are a dependency of the *framework*, not of anybody's estate.
- The genesis substrate: a minimal service graph (`deploy/docker-compose.genesis.yml`,
  permissively-licensed images only) and a declarative store schema for the stores that
  are not files, so "instantiate every store empty and valid" is true at the SQL level
  too. See `docs/SUBSTRATE.md`.
- The imprint: the faculties and refusals every node is born with, and none of anyone's
  conclusions.

## What never ships

- Any specific identity instance (memories, wishes, rulings, a filled alignment
  interview) — that is L4, and it is private by construction, not by omission.
- Any specific operator's or organization's estate: connectors, credentials, tenant or
  account identifiers, data, or business-specific workflow handlers.
- Private keys, in any form, at any layer.
- A skill marketplace or any mechanism for installing a third-party artifact that loads
  into an agent's context without in-repo review.
- Anything that has not passed the exposure and licence gates ahead of a public flip.

---

## Quick start

```
git clone <this repository>
cd intentops
python -m venv .venv
.venv/Scripts/activate   # or: source .venv/bin/activate
pip install -e packages/intentops-core
intentops doctor
intentops genesis --identity-repo new --dry-run
```

Full walkthrough, including what each step actually checks: `docs/quick-start.md`.

---

## Documents

| Topic | Location |
|---|---|
| The genesis sequence (G0–G8, stand-down) | `docs/GENESIS.md` |
| What an identity repo must contain | `docs/IDENTITY-REPO-CONTRACT.md` |
| The runtime portability contract | `docs/SADDLE-CONTRACT.md` |
| What runs underneath a node, and under which licence | `docs/SUBSTRATE.md` |
| Licence | `LICENSE` (Apache-2.0) |
| Contributing | `CONTRIBUTING.md` |
| Security and disclosure | `SECURITY.md` |
| Design of record | `docs/reports/intentops-oss/design/genesis-design.md` (private monorepo; not shipped here) |

This README cites the design by section number, never by an internal monorepo path —
the paths named above under "Design of record" exist in the private working estate this
seed was extracted from, not in this repository.
