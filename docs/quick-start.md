# Quick start

This walks a fresh clone to a `doctor` reading and a dry run of genesis, and tells you
what each command actually prints today — including the parts that look like failures
and are not.

It is written against a recorded run rather than an intention. Every command below is
one that the stranger-genesis falsifier ran end to end from a fresh clone, with no prior
state and no harness variables:
[docs/falsifiers/F3-stranger-genesis-2026-09-06.md](falsifiers/F3-stranger-genesis-2026-09-06.md).
Where output is quoted here, that record is where it came from.

**Read this before you start:** on a clone where no trust-root ceremony has run — which
is every clone of this seed — genesis **halts**, `verify` prints `verified: False`, and
the tagout oracle reads BLOCKED. All three are correct. Sections 5 and 6 say why.

**Prerequisites.** Python, plus two things the test suite needs and this page assumes:
**`git` on `PATH`** — several tests build a throwaway repository, and skip by name where
it is absent — and a **resolvable home directory** (`USERPROFILE` on Windows, `HOME`
elsewhere), which the boot-corpus reader consults. With neither present the CLI still
runs; the suite reports skips and a named corpus error rather than a pass.

---

## 1. Clone

```
git clone <this repository>
cd intentops
```

## 2. Create and activate a virtual environment

```
python -m venv .venv
```

Windows (PowerShell):

```
.venv\Scripts\Activate.ps1
```

macOS / Linux:

```
source .venv/bin/activate
```

Python 3.11 or newer is required. There is no CUDA or GPU dependency anywhere in the
framework core — if a future capability package wants one, it will say so explicitly in
its own `pyproject.toml`, not inherit it silently from the core.

## 3. Install

From the **repository root**:

```
pip install .
```

That installs the `intentops` console command and its two dependencies (`pyyaml`, and
`cryptography` for Ed25519 key material — never for anything network-facing).

Two things about this step, both learned the hard way:

- **The distribution is the repository root**, not the package directory. Until
  2026-09-06 this page and the README both said
  `pip install -e packages/intentops-core`; there is no packaging file at that path, so
  the one command every stranger runs could not work. It is now checked mechanically by
  `scripts/docs/claims_check.py`.
- **Installing the distribution alone is not enough to run genesis.** `config/`,
  `genesis/`, `estate/` and `scripts/` are not package data, so an install with no clone
  beside it has no imprint bundle to verify and no probe suite to run. It halts and says
  so — but the remedy line it prints names a path inside your virtual environment, which
  reads like a corrupted install and is not. Keep the clone.

## 4. Run the doctor

```
intentops doctor
```

A read-only reading of the six birth questions. On a fresh, unborn clone it answers
**2 of 6** and exits non-zero, because there is nothing born yet to ask: no probe suite
resolves from the install root, no tagout ledger exists, and no identity repository is
bound. That is the honest shape of a clone, not a fault.

## 5. Dry-run genesis

`--repo-root` and `--node-root` are **global** flags: they belong before the subcommand,
never after it. `intentops genesis --repo-root ...` is rejected by the argument parser
with `unrecognized arguments`. That is an invocation error, not a tool defect — the F3
record notes it happening on a first attempt so nobody else has to rediscover it.

```
intentops --node-root node --repo-root . genesis --dry-run --identity-repo new
```

On a clone where no ceremony has run, this **halts at G1** and mints nothing:

```
HALT at HALT: G1 refused: [G1.2-root-status] root(s) still `proposed` ...
  [G1.3-pin] the compiled release-root pin is a placeholder ...
  [G1.4-representations] ... placeholder or malformed key material
  [G1.7-imprint-signature] the imprint manifest is UNSIGNED. Unsigned and valid
    must never be indistinguishable, so this is a refusal, not a warning
  remedy: mint the release root and sign the imprint bundle, or set
    INTENTOPS_GENESIS_UNSIGNED_DEV=1 to proceed unverified (a T3 tagout opens
    and every command says so)
```

Every blocking reason is printed in full and id-tagged; nothing is truncated. The node
journals its own refusal before stopping, and its whole footprint afterwards is five
files under `.intentops/genesis/` — no keys, no identity.

### Seeing the sequence anyway, with the price stated

Set the flag the remedy names. A banner then prints on **every** command, until the
roots are minted and the bundle is signed:

```
############################################################
# PROVENANCE: UNVERIFIED                                    #
# This node is running with the unsigned-development flag   #
# open. Nothing it carries has been proven to be what the   #
# project published. It may not federate, may not publish a #
# trust bundle, and its rulings may not be exported as      #
# signed artifacts. A T3 tagout is open and the tagout      #
# oracle reads BLOCKED, not REMEDIATED, until it is closed. #
############################################################
```

and the sequence runs to G7:

```
G0  WARN    would HALT on a real run: no host marker found. A node with no bound
            saddle has a gate whose verdicts nothing enforces, so this binds NONE
            rather than guessing
G1  WARN    G1.2-root-status ...; G1.3-pin ...
G2  PASS    designation node-6Mk... derived from the node's did:key; DRY-RUN: a
            keypair was minted in memory and NOT persisted
G3  PASS    belief carriers bound: <n> source(s); birth probe suite: probes <n>/<n> PASS;
            0 ABSENT (packaging); 0 truth-failures
G4  PASS    consent recorded, append-only
G5  PASS    recorded verbatim; supersession only, never edited
G6  PASS    staged by evidence available, not by clock
G7  PASS

final state: G7
```

Four things there are worth reading rather than skipping:

- **What the flag does *not* open.** It downgrades **absence** — a root nobody has
  minted, a manifest nobody has signed — and never a **contradiction**. Key material
  disagreeing with its own fingerprint, or an imprint artifact whose bytes moved, halts
  with the flag set. That refusal produced the original F3 FAIL, and it was correct both
  times.
- **`G0 WARN` and `--saddle`.** A directory with no host marker binds **no** saddle
  rather than guessing, and genesis says a real run would halt there. `--saddle <id>`
  binds one explicitly; the ids are the rows in
  [config/saddles.yaml](../config/saddles.yaml). F3 did not exercise that flag, so this
  paragraph describes the CLI, not a recorded run.
- **`G3` reports its denominator.** A rate never travels without one, and `0 ABSENT`
  distinguishes a *coverage* failure (the boot corpus lost the anchor) from a *truth*
  failure (the organ moved). They must never be read as the same finding.
- **`G7` and the belief-currency instrument.** When belief carriers are bound at birth,
  G7 passes. When none are, it **WARNs** — which is what the F3 record shows, and it is
  by construction rather than a fault: the currency instrument refuses to score an empty
  population, because that would return a perfect reading of nothing. It stays in the
  denominator as *unprobeable* instead. Either line is correct; which one you see depends
  on whether your bundle declares carriers.

The dry run mints no key, takes no real consent, and marks every operator field
`DRY-RUN:` so a dry record can never later be mistaken for a real one.

## 6. Verify, and what `verified: False` means

```
intentops --node-root node --repo-root . verify
```

`--repo-root` is load-bearing here. Without it the repo root defaults to the install
location, and from an installed distribution every G1 artifact reads `absent`
(`config/trust-roots.yaml`, `genesis/imprint/IMPRINT-MANIFEST.yaml` and the rest) — a
reading about a directory that was never the clone, not a reading about your node.

```
PASS    G1.1-roots-present: 2 root(s) declared
REFUSE  G1.2-root-status: root(s) still `proposed` ...
PASS    G1.2b-root-policy: 2 root(s) carry legal roles, issuers and signing scopes
HALT    G1.3-pin: the compiled release-root pin is a placeholder ...
HALT    G1.4-representations: ... placeholder or malformed key material
PASS    G1.5-revocation: sequence 0, 0 entries
PASS    G1.6-imprint-hashes: every claimed artifact matches on disk
REFUSE  G1.7-imprint-signature: the imprint manifest is UNSIGNED ...
PASS    G1.8-archetypes: 0 reserved of 1 entries

verified: False
```

**`verified: False` is the honest string, not a defect.** No release root has been
minted for this build, so there is nothing for the signature checks to verify against.
The correct reading is *"this node has not been proven to be what the project
published"* — true of every clone until the ceremony in
[docs/TRUST-CEREMONY.md](TRUST-CEREMONY.md) has been performed and its output published.
`G1.6-imprint-hashes` passing is the genuinely reassuring part: the bundle on your disk
is byte-for-byte what the manifest claims.

For the same reason, the tagout oracle on an unborn clone reads **BLOCKED**:
`.intentops/loto/LEDGER.yaml` does not exist until genesis creates it, and a missing
ledger is deliberately not a clean bill of health.

## 7. What a node holds at birth

After the dry run, `intentops --node-root node --repo-root . doctor --birth` writes a
birth certificate. A dry run measured on 2026-09-06 left **72 files** under the node
root; the number is incidental and moves with the bundle. The load-bearing count is the
**24 birth entries** (`DECLARED_BIRTH_ENTRIES` in
`packages/intentops-core/intentops_core/genesis/organs.py`) — every store a
node holds on day one, each declaring how it is written, from one of seven closed write
models (per-item files, locked read-modify-write, append-only JSONL, single-writer
ceremony, generated projection, derived-regenerable, absent-at-birth). One of them,
`.intentops/halt.marker`, carries its fact by its **absence**.

What the certificate answers depends on the flag above, and both readings are honest.
**As shown without `--repo-root`** — from an installed distribution — it answers **five
of six** and names the one it cannot: `self_probe`, because no probe suite resolves from
the install root, so the verdict is **ALIVE-DEGRADED**. **With `--repo-root .`** it
answers **6/6** and reads **ALIVE**. A node proves it is alive by answering, with an
instrument named on every row — never by asserting.

## 8. Stand-down

```
intentops --node-root node stand-down
```

```
STOOD DOWN. This node is OFF, not broken. Nothing bad happens to you for using this.
No reason was asked for and none was recorded. To bring it back, delete the marker
file yourself -- there is no command that does it.
```

It works from any state, including partway through provenance verification, and it does
not require the node's cooperation. There is deliberately no `stand-up` command: undoing
it is your own manual act. A following `doctor` reports **STOOD-DOWN**, every row
unchanged.

## 9. What "done" looks like at this stage

- `intentops doctor` answers what it can and names what is missing on a fresh clone.
- `intentops --node-root node --repo-root . genesis --dry-run --identity-repo new`
  **halts at G1** with four named reasons and mints nothing.
- With the unsigned-development flag it reaches `final state: G7`, prints the banner on
  every command, and writes nothing outside the node root you named.
- `intentops --node-root node --repo-root . verify` prints `verified: False`, and
  `G1.6-imprint-hashes` passes. Drop `--repo-root` from an installed distribution and
  every G1 artifact reads `absent` instead — a different reading of a different
  directory.
- `stand-down` works and says so kindly.

If any of those does not hold, that is a bug in this seed, not something to work around
locally.

## 10. Running genesis for real

Everything above is `--dry-run`. A real run mints this node's own keypair, prompts for a
passphrase without echo, takes your consent, records a founding conversation, and binds
an identity repository. Read [docs/GENESIS.md](GENESIS.md) first. Every state that
matters gates on you being present at an interactive terminal: a closed or redirected
stdin, a declared CI environment, and stdin redirected from the null device are all
refused. Consent from an automated job is not consent, and genesis is deliberately not
something you back into by trying flags.
