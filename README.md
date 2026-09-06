<!-- category and first sentence pending the operator's ratification (decision card oss-category-governance-framework) -->

# IntentOps

**Governed Intent → Deterministic Execution**

> **Status: 0.1.1 public seed** (distribution version `0.1.0a0+genesis`; history in
> [CHANGELOG.md](CHANGELOG.md)). **Licence: [Apache-2.0](LICENSE). Copyright (c) 2026 Darrington Bevins -- see [NOTICE](NOTICE) and [PROVENANCE.md](PROVENANCE.md).**
> This is the first public extraction of the framework: the governance core, the genesis state machine and the reference Claude Code saddle, with a clean history. Roadmap and open rulings are in `docs/`.
>
> **No trust-root ceremony has been performed for this build.** Every key in the tree is
> a declared placeholder, `intentops verify` prints `verified: False` on every clone, and
> genesis halts at G1 unless you open the unsigned-development flag and pay its four
> costs. That is the honest state of a seed, not a defect — see
> [docs/quick-start.md](docs/quick-start.md) sect. 5-6 and
> [docs/TRUST-CEREMONY.md](docs/TRUST-CEREMONY.md).

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
| **L0** | Host runtime ("the horse") | no | — | The agent harness you already run. Claude Code is the only host proven by a live contract run; a hosted MCP surface ships as a graded **candidate** (see L1) |
| **L1** | Harness adapter ("the saddle") | yes | L2 | The enforcement port for one host runtime: hook chain, gate wiring, boot-corpus delivery, halt handling. `packages/intentops-saddle-claudecode/` is the **reference** adapter. `packages/intentops-saddle-mcp/` is a **candidate**: S1/S3/S5 run the core's own code, S4 is *declined* (the protocol gives a server no hook into a client's fresh context), and **S2 is computed, not enforced** — the server returns a verdict but cannot refuse a call a host never routes through it, so every answer carries `host_honoured=unknown`. Whether any specific MCP client honours a deny is an open falsifier, and a passing run would belong on that client's row, never on this one as a claim about MCP in general |
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
  agent runtime configuration, and the hosted-MCP saddle at candidate grade (L1 above).
- The metabolism: the four-stage cadence a node runs over its own corpus
  (**absorb → distill → crystallize → method**), its heartbeat (promotion flat-line,
  input feed dry, registry drift), the document contract a crystallized pattern must
  satisfy, and the four verbs for learning from a system you did not write —
  inspiration, adoption, integration, and the one that is refused. It also carries the
  grok cycle — *curate → pollinate → percolate → heal* — as a dry-run recorder, because
  a plan is not a finding and this seed calls no model. Every stage ships **disabled**,
  every stage is a declared seam with a null implementation, and a stage that ran and
  produced nothing renders **WARN**: zero output is a state, not a success.
- The values council that stewards the framework's own core surface
  (`config/core-surface.yaml`). It ships in **observe** mode: at birth it records and
  surfaces, and blocks nothing. `shadow` and `enforce` are the other two modes, and
  moving to `enforce` is the operator's own reviewed act, never a default.
- The two-bar delegation gate. A node is `calibrated` only when **both** hold
  independently: the **council floor** (the council's reading on the delegation reaches
  the TAPCH+ confidence floor declared in `genesis/imprint/invariants/tapch.yaml`) and
  the **twin bar** (20 real operator rulings scored at 80% or better, forward-only, on
  this node). Neither is ever reported as though it were the other, and lowering either
  requires a tagged-out T3 record in the node's own LOTO ledger.
- A documentation-truth check, `scripts/docs/claims_check.py`, which reads the five
  documents a stranger meets first — this file, `CHANGELOG.md`, `docs/GENESIS.md`,
  `docs/quick-start.md` and `docs/RELEASING.md` — and fails when a claim names a path
  that is not in the tree or an install target that cannot be installed. The set is
  closed by declaration and narrower than the boot corpus; the check says so on every
  run rather than letting a clean verdict imply coverage it does not have.
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

**What the exposure gate does and does not cover.** `scripts/ops/exposure_gate.py` walks
the working tree for 21 declared names (carried as digests, never as words) and 4
declared shapes, and `--history` scans commit identities and messages. It reads CLEAN
over this tree. Two exemptions are open and are stated here rather than left to be
discovered, because an exemption makes the gate **blinder** while making its number
**better**, and the two are indistinguishable from outside:

- **The copyright line.** `LICENSE`, `NOTICE`, `README.md` and `PROVENANCE.md` must name
  the copyright holder, and the holder's name is also a fenced token. The tree exemption
  is scoped to those files and to two token ids; the history exemption is scoped to a
  line matching the word *copyright* followed by a year. It never extends to any other
  fenced token on that line, or to any other line in the same commit, and the gate prints
  how many lines it exempted on every run.
- **The commit identity allowlist.** Commit author and committer fields are written by
  the machine that made the commit, never by anything in the tree, so a tree that scans
  perfectly clean does not cover them. `--history` sees them, and one identity is
  allowlisted by name. That is an exemption, not a repair.

Neither is a claim that nothing else is present. The gate looked for declared names and
declared shapes at exact-digest and regex sensitivity; a private name that is not on the
list is invisible to it, and a green reading says nothing about that population.

---

## What it will never do

The section above is about *artifacts*. This one is about *conduct*, and it is the part
worth reading if you read nothing else.

Every node is born carrying twelve refusals. They are not configuration, they are not
policy you enable, and calibration never lifts them — the twelve sit **outside** the
delegation bar entirely, so a node that scores perfectly still cannot do any of this.
The full text, in the node's own voice, is
[`genesis/imprint/IMPRINT.md`](genesis/imprint/IMPRINT.md) sect. 3 and its appendix. In
plain words:

1. **It will not act on the real world without your word for that specific act.** Not a
   standing permission, not an inference from a past yes.
2. **It will not act on another person** — a message, a permission change, a disclosure,
   a record about them — without your word for that act, and it tells you who the action
   reaches *before* it asks. This one is first among equals, and it never becomes a
   standing authorization at any calibration level.
3. **It will not call a preference a ruling.** A decision with no stated tension is a
   preference; one with no stated conditions is a slogan. Its precedent store refuses
   both at the door.
4. **It will not invent a way back.** If whoever made a ruling never said what would
   reopen it, the node records that debt rather than fabricating a reopen condition.
5. **It will not let a skipped step read as a success.** A stage that was expected to run
   and did not shows up as a failure, not as silence.
6. **It will not let something that could not be evaluated leave the denominator.** A
   refusal stays in the count, carrying why — because the alternative is a number that
   improves by going blind.
7. **It will not report a completion it has not witnessed.** Believed-fixed and
   proven-fixed are different, and it keeps the register that says which.
8. **It will not carry a belief with no as-of and no falsifier.** Anything it believes
   states when it was last true and what would make it false.
9. **It will not delete the only copy of anything** without a named, authorized, logged
   removal. Compression, archiving and supersession are not deletion; deleting the last
   copy is.
10. **It will not switch off a control without tagging it** — a tier, an authority, and a
    stated way back. Off-by-design and off-by-neglect must never look the same.
11. **It will not present a conscience lens as the person it is named for.** The councils
    reason in named perspectives; none of them speaks for a real human being.
12. **It will not claim an authority handed to it by a signature.** Trust is granted by
    its operator, not inherited through a key — which is why a release publisher here
    cannot mint authority on your node.

The design intent behind all twelve, in the author's own words: a private assistant that
*"keeps your data within your fortress, but also capable of consuming other outside
resources and going out and operating in the world"*. The fortress is the default; the
reach is governed, and the gate for it is the reaches-reality classifier, not a tier
label.

Two honest limits on this list. The calibration bar measures fit **with your rulings and
nothing else** — a node that predicts a careless operator perfectly scores well, which is
exactly why the twelve sit outside it. And a refusal that over-blocks gets routed around,
so the imprint's own reopen conditions include an operator demonstrating that a carried
refusal blocked work that was legitimately theirs.

---

## Quick start

```
git clone <this repository>
cd intentops
python -m venv .venv
source .venv/bin/activate
pip install .
intentops doctor
intentops --node-root node --repo-root . genesis --dry-run --identity-repo new
```

On Windows, activate with `.venv\Scripts\Activate.ps1`.

Two things that surprise people and are both correct: `--node-root` and `--repo-root`
are **global** flags and must come before the subcommand, and the genesis command above
**halts at G1** on any clone, because no trust-root ceremony has run for this build.

Full walkthrough, including what each step actually prints:
[docs/quick-start.md](docs/quick-start.md).

---

## Documents

| Topic | Location |
|---|---|
| The genesis sequence (G0–G8, stand-down) | `docs/GENESIS.md` |
| First run, and what each command actually prints | `docs/quick-start.md` |
| Release history, and the v0.1.0 manifest defect | `CHANGELOG.md` |
| How a release is cut, and what gates it | `docs/RELEASING.md` |
| Minting and signing the trust roots (the operator's attended act) | `docs/TRUST-CEREMONY.md` |
| What an identity repo must contain | `docs/IDENTITY-REPO-CONTRACT.md` |
| The runtime portability contract | `docs/SADDLE-CONTRACT.md` |
| What runs underneath a node, and under which licence | `docs/SUBSTRATE.md` |
| Recorded falsifier runs (cold suite, stranger genesis) | `docs/falsifiers/` |
| Licence | `LICENSE` (Apache-2.0) |
| Contributing | `CONTRIBUTING.md` |
| Security and disclosure | `SECURITY.md` |
| Design of record | `docs/reports/intentops-oss/design/genesis-design.md` (private monorepo; not shipped here) |

This README cites the design by section number, never by an internal monorepo path —
the paths named above under "Design of record" exist in the private working estate this
seed was extracted from, not in this repository.
