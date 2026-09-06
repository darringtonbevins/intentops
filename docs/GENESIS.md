# Genesis

Genesis is the deterministic sequence that turns a fresh clone into a *node*: it
proves what it carries before it mints what it is, it creates its own stores empty and
valid, it asks its operator for consent before it asks for anything else, and it comes
online able to *answer* — with instruments, not assertions — whether it is actually
alive and under its own rules.

Design of record: `docs/reports/intentops-oss/design/genesis-design.md` sect. 2 (this
repository ships the sequence and the driver; the design document itself lives in the
private working estate this seed was extracted from and is cited here by section, not
shipped as a path you can open in this repo).

Genesis is deterministic: the same substrate plus the same operator answers produce the
same journal. Every state transition is appended to an append-only journal before the
next state begins; a phase that cannot record its own transition halts rather than
silently continuing.

## The states

There are eight normal states plus one interrupt state reachable from any of them.

```
                    +---------------------- STAND-DOWN (reachable from ANY state)
                    |
  G0 SUBSTRATE --> G1 PROVENANCE --> G2 KEYS --> G3 ORGANS --> G4 CONSENT
                    |                                                |
                    +-> HALT (fail-closed)                           v
                                                    G5 FOUNDING --> G6 ALIGNMENT --> G7 ONLINE
                                                                                       |
                                                                        (bar met) --> G8 CALIBRATED
```

| # | State | What it does | Operator gate | Reversible? |
|---|---|---|---|---|
| **G0** | Substrate | Detects the actual hardware and mode of the host, rather than trusting a policy file's assumption about it. | None. | Yes — pure read, re-runnable. |
| **G1** | Provenance | Verifies the trust roots, the invariant bundle, the imprint manifest, and the archetype catalogue. Ten checks, no network, all pure functions. | Only if you invoke the override (see below). | Yes — verification is pure; its record is append-only. |
| **G2** | Keys | Mints this node's own Ed25519 keypair and its operator root. **This build implements exactly one storage medium: a passphrase-wrapped file** (PKCS8, prompted without echo, written under your home directory, `0600` where the platform allows). Hardware token, TPM, and OS credential store are named at the gate and **halt** — the node refuses to record a protection it did not apply. The private key never leaves the machine and never enters any repository, and `operator-root.pub.json` records both your answer (`storage`) and what was actually done (`storage_applied`). | **Yes.** You choose the storage medium and type the passphrase. | Freely, until the operator root signs its first artifact; after that, only by a superseding root with recorded lineage — never a silent replacement. |
| **G3** | Organs | Instantiates every store empty and valid, each one declaring how it is written; materialises the blank estate manifests; binds the identity repository; runs the birth probe suite. | **Yes, if no identity repository is bound** — genesis halts and states plainly what it needs. It never silently creates a local one. | Yes — a rollback to this point removes local runtime state and any unsigned identity-repo skeleton. |
| **G4** | Consent | Prints the first-day text; records what you consent to and what is off-limits. | **Yes.** Requires an interactive terminal; refuses to run from an automated context. | No — append-only. Withdrawing consent later is a new recorded event, never a deletion of the fact it was once given. |
| **G5** | Founding | Asks the founding questions once, and records the answers verbatim. | **Yes.** Requires you present at the terminal. | No — superseded only, never edited or deleted. |
| **G6** | Alignment | Runs the staged alignment interview (see below); elicits and rules the estate map; records priors, explicitly labelled as priors, not as evidence. | **Yes, per sitting.** | Partially — answers are append-only; a corrected answer supersedes the earlier one rather than overwriting it. |
| **G7** | Online | Steady state under the birth ceiling (see below); the come-online report can be regenerated on demand. | None. | n/a |
| **G8** | Calibrated | Reached only once the forward-only scorer has graded twenty or more of this operator's real rulings, on this node, at eighty percent or better. | Reaching the bar is measured, not asked; *using* whatever it unlocks is still the operator's separate choice to enable. | n/a — a later fall below the bar re-imposes the ceiling automatically. |

## The unverified-boot override, and what it does not open

Before a release root is minted there is nothing for G1 to verify against, and a node
that could never be built in that state could never be built at all. The override for
that case is deliberately expensive, and all four costs are paid together:

1. **`INTENTOPS_GENESIS_UNSIGNED_DEV=1`** must be set.
2. **An attended terminal.** A closed or redirected stdin, a declared CI environment,
   and — on this platform specifically — stdin redirected from the null device, which
   reports itself as a terminal, are all refused. Consent from an automated job is
   not consent.
3. **The exact phrase `boot unverified`, typed at the prompt.** No environment
   variable substitutes for typing it. A dry run does not ask, because a dry run
   mints nothing; it records that the dry run, and not the operator, let the phase
   past.
4. **A T3 tagout and a banner on every command**, until the roots are minted and the
   bundle is signed.

What the override has never meant is *"the evidence disagrees, proceed anyway."*
Absence and contradiction are different findings that arrive at the same gate:

| Finding | Class | Under the override |
|---|---|---|
| A root is still `proposed`; no ceremony has minted it | absence | Downgraded to a warning, named in the record |
| The imprint manifest is unsigned | absence | Downgraded, named |
| The revocation list is absent | absence | Downgraded, named |
| Key material whose fingerprint or `did:key` disagrees with its own bytes | **contradiction** | **HALT. The override does not open it.** |
| An imprint artifact whose bytes moved, or that the manifest claims and the bundle does not carry | **contradiction** | **HALT** |
| A trust-roots file present and unparseable | **contradiction** | **HALT** |

Every blocking reason is printed, id-tagged, with no truncation; the full record is
appended to `.intentops/genesis/provenance-record.json`. A record produced under the
override is never marked verified, at any later point, for the life of that record.

## What a node carries out of genesis

Two facts a fresh node must be able to answer about itself from its boot corpus
alone, with no tool call. The birth probe suite asks for both, and until 2026-09-06
neither had a carrier here — so `GEN-operator` and `GEN-motto` read ABSENT on every
clone by construction, which is a packaging failure, not a missing organ.

| Fact | Where it lands | What it says at birth |
|---|---|---|
| **Operator record** | `.intentops/trust/operator-root.pub.json` — schema `operator-root/v1`, written at G2 | Who this node is answerable to, as a fingerprint and a label its operator chose. Never a name inherited from anywhere else. That directory holds public projections only; a private key appearing in it is an incident, not a bug. |
| **Motto** | `genesis/imprint/invariants/core.yaml`, carried verbatim into the imprint's I9 bundle | **Be well. Do good. Bring light.** The stated guiding light, and the compression of the ethics the imprint spends nine bundles arguing for. |

Neither is a claim about conduct. The operator record says whose word rules here; the
motto says what the node is aiming at. Whether it hit is the calibration bar's
question, and the honest answer before that bar is a count, not an adjective.

## What only the operator can answer

Genesis asks a human five times, and plumbing the rest of the time: the root ceremony
(G2), the identity-repository binding when none is supplied (G3), consent (G4), the
founding conversation (G5), and each sitting of the alignment interview (G6). A
proposed name and, if the unverified-boot override is ever used, that override are
both out-of-band operator acts as well. Every one of these requires an interactive
terminal; none of them will proceed from a script, a scheduled job, or an automated
agent turn acting on your behalf.

## The alignment interview, honestly staged

At genesis there are no prior rulings to replay, because there is no operator history
yet — that is not a flaw to work around, it is the honest starting condition. The
interview is staged by what evidence actually exists, never by a clock:

| Stage | Available when | What it produces |
|---|---|---|
| **S0 Consent** | Immediately | Permission and fences, recorded as fact. |
| **S1 Boundary** | Immediately | The node's own estate vocabulary and notification posture. |
| **S2 Domain** | Immediately | Priors from explicitly hypothetical cases — labelled as inferred, never as observed, and never citable later as evidence of actual alignment. |
| **S3 Replay** | Once at least ten real rulings exist in this node's own history | The operator's own past decisions replayed with shuffled options and no recommendation, the prior ruling revealed only after the answer. This is the first durable evidence. |
| **S4 Commitment** | Once at least twenty real rulings exist | Three real pending decisions ruled through as actual conduct, with the model's prediction sealed *before* the ruling lands — the first scored grades. |

## The birth ceiling

Some restrictions never lift, at any calibration level, on any node — a floor, not a
ceiling that calibration raises. Among them: no action that reaches the real world
without an in-session human word for that specific act; no outbound message or grant
made on a person's behalf from an unattended loop, a background task, or a subagent —
only from a turn a human is actually present for; never speaking in the operator's
voice to a third party; never deleting anything without a named, reversible reason;
and never claiming to be "aligned" — the honest self-description before the bar is
reached is a plain count, such as "twelve of twenty rulings scored, sixty-seven
percent," not a claim of trustworthiness.

A small number of things *do* lift at G8, and only by measurement: citing the model of
the operator as an input to a decision (never as authority before that point), running
a delegated-autonomy routine that ships disabled until then, compiling a ruling into
something that can decide future cases automatically, and proposing changes to the
framework's own core under the values council's review rather than only as the
operator's direct edit. A node never lifts any of these about itself; it can only
propose, and the operator disposes.

## Stand-down

`intentops stand-down` is one command. No reason field, no confirmation prompt, and no
argument the node can make against it. It works at every state, including partway
through provenance verification, and it does not require the node's cooperation — it
is a file, not a request, and the gate checks for that file's presence ahead of every
other check it makes. A stood-down node is off, not broken: nothing reports a defect,
and the exit code says so. There is deliberately no `stand-up` command — removing the
stand-down file is the operator's own manual act.

## Reversibility, in one place

The early states (through organ creation) are fully reversible. Consent, the founding
conversation, and every alignment record are append-only by design — not because
mistakes cannot be corrected, but because deleting the record that something was once
asked and once agreed to destroys the only evidence that it happened at all. A
correction is a new recorded event, never a vanished old one.
