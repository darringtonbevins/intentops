# The Release Root Ceremony

**Status: the ceremony has NOT been performed.** Every root in
`config/trust-roots.yaml` is `status: proposed`, the compiled pin is
`sha256:PLACEHOLDER`, and G1 halts accordingly. This document is the runbook
for the sitting that changes that, and the mechanical half is
`scripts/genesis/mint_release_root.py`.

This file exists because three carriers named it and it did not exist.
`config/trust-roots.yaml` pointed at `docs/reference/trust-root-design.md` and
`docs/reference/ceremonies/*.md`, neither of which is in this tree;
`genesis/trust_pin.py` said the pin is "edited only by the release ceremony's
tooling", and there was no tooling. A carrier that names a control nobody built
is worse than no carrier, because it stops the reader looking. (Wave-2
verifier finding, 2026-09-06.)

---

## The wizard does all of this: `intentops ceremony`

Everything below is the specification. You do not have to hold it in your head:

```
intentops --repo-root . ceremony --out <PATH OUTSIDE ANY REPOSITORY>
```

Ten screens, each stating what it is about to do, why, and what it will NOT do.
It runs the preflight, takes the passphrase without echo, mints both keys via
the primitives on this page, signs the imprint, shows you the three-carrier
diff and writes it only after you type `apply the three-carrier edit`, verifies
G1.7 in a fresh process with the development flag stripped, brings a throwaway
node up to G7, writes the ceremony record beside your key, and prints the three
commands you run afterwards. It stages; it never commits.

Every step is journalled to `.intentops/ceremony/ceremony-state.json` and to a
copy on your medium, with PUBLIC FIELDS ONLY -- no passphrase, no private key,
no derivative of one. If a step fails you get a numbered remedy from
`docs/CEREMONY-REMEDIATION.md`, and `intentops ceremony --resume` continues at
that step without re-minting a key that already exists.

**It changes none of the preconditions below.** It is attended-only by the same
test the primitives use -- non-TTY refused, `CI` refused, EOF treated as a
refusal -- and it is never run from a loop tick, a workflow leg, a subagent, a
container entrypoint, or a scheduled task.

**Two assurance levels, chosen on the first screen and RECORDED, never faked:**

| Level | What it means |
|---|---|
| `standard` | Attended, on the operator's own machine, private key encrypted on a medium the operator names. No witness required. |
| `full` | The ceremony below: offline machine, a named witness present, a clean host. |

The wizard cannot observe whether the room is offline, witnessed, or clean --
nothing can. So it records what it CAN measure beside what you STATE, and
attributes the second to you: *"network: operator states cable unplugged; tool
observed default_route_present=false, resolved_local_addresses=1"*. A record
that flattened those two into "offline: yes" would be a claim wearing a
measurement's clothes. If you choose `full` and name no witness, it records the
sitting as `standard`, because that is the honest label.

---

## What a release root is, and is not

| | |
|---|---|
| **Release root** (`R-INTENTOPS`) | The project's publishing key. Signs the release intermediate and the trust-roots file. Lives with the project's human operator, offline. |
| **Release intermediate** (`I-INTENTOPS-REL-2026`) | Signs the imprint manifest, the invariant bundle, the archetype catalogue and release artifacts. Short-lived; expiry alone revokes it for an offline clone. |
| **Operator root** (`R-OPERATOR`) | **Not minted here.** Every node mints its own at genesis phase G2 and writes it to that node's `.intentops/trust/operator-root.pub.json`. It is never listed in `config/trust-roots.yaml`. |

**There is no cross-certification, in either direction.** The release root never
signs an operator root, and an operator root never appears in the project's
trust-roots file. A release publisher that could mint authority on installed
nodes would be one compromised key away from a fleet-wide coup. Since
2026-09-06 this is enforced in code by `G1.2b-root-policy`
(`intentops_core/genesis/provenance.py`), not only stated in prose — a planted
`{role: operator_root, issued_by: R-INTENTOPS, status: active}` root used to
pass every check in the module.

---

## Preconditions — all of them, or the sitting does not happen

1. **Attended.** A human at a physical terminal. The tooling refuses a
   non-TTY stdin, refuses when `CI` is set, and treats EOF as a refusal.
2. **Never from automation.** Not from a loop tick, a workflow leg, a
   subagent, a scheduled task, a container entrypoint, or CI. This clause is
   the point of the whole document. If the ceremony can be triggered by a
   process, the key belongs to the process.
3. **Offline.** The minting machine has no network route while the key exists
   in memory or on disk unencrypted. Airplane mode is not "offline"; an
   unplugged cable is.
4. **Witnessed.** At least one second person present, named in the ceremony
   record. A ceremony with no witness is an assertion.
5. **Clean host.** A machine the operator controls, freshly booted, with no
   agent, assistant, screen recorder, clipboard manager, or remote-access tool
   running. Anything that reads the screen reads the key material.
6. **A decided storage medium** for the private key, and the passphrase chosen
   before the sitting starts, not composed at the prompt.

---

## The sitting

### 1. Mint

```
python scripts/genesis/mint_release_root.py mint --out <PATH OUTSIDE ANY REPO>
```

The tool requires the typed arming phrase `mint the release root`, prompts
twice for a passphrase without echo, and **refuses to write an unencrypted
release root**. It refuses any `--out` that resolves inside this repository.
It prints the public representations — `fingerprint`, `did`, `key_id`, the
authority string, and the PEM — and **never prints the private key**.

Record, by hand, in the ceremony record: date, location, operator, witness,
machine, network state, storage medium, and the authority string.

### 2. Apply the three-carrier edit — one reviewed change

The tool does **not** edit the repository. That is deliberate: a tool that can
rewrite the pin unattended is the attack the three-carrier design exists to
make visible. Apply all three together, in one commit, reviewed:

| Carrier | What changes |
|---|---|
| `config/trust-roots.yaml` | `pinned_fingerprint`, and the `R-INTENTOPS` record: `public_key_pem`, `fingerprint`, `did`, `key_id`, `valid_from`/`valid_until`, `ceremony.minted_at`, `ceremony.witnessed_by`, `evidence`, and `status: proposed` → `active` |
| `packages/intentops-core/intentops_core/genesis/trust_pin.py` | `ROOT_FINGERPRINT` and `PIN_STATE = "minted"` |
| `docs/GENESIS.md` | the human-readable fingerprint in the trust-root section |

A partial edit is what G1.3 (`pin`) and G1.4 (`representations`) exist to
catch. Do not "fix" a mismatch by making one carrier agree with another —
re-derive from the key.

### 3. Mint the intermediate, and sign the imprint

Repeat step 1 for `I-INTENTOPS-REL-2026` (`role: release_intermediate`,
`issued_by: R-INTENTOPS`, `signs` including `imprint_manifest`). Then:

```
python scripts/genesis/mint_release_root.py sign --key <intermediate key> --key-id <KEY_ID>
```

The signature covers **the GENERATED artifacts block of
`genesis/imprint/IMPRINT-MANIFEST.yaml`, from the BEGIN marker line through
the END marker line inclusive, newline-normalised to LF, encoded UTF-8** —
defined once, in `provenance.canonical_imprint_payload`, and imported by both
the signer and the verifier so they cannot drift. LF normalisation is
load-bearing: falsifier F1 established that this repository ships
`.gitattributes eol=lf` while the build machine's working copy held CRLF, so a
signature over raw bytes would verify on exactly one checkout in the world.

Run `python scripts/genesis/build_manifest.py --check` **before** signing. A
signature over a stale hash block is worse than no signature.

### 4. Verify from a stranger's position

From a fresh clone, on a different machine:

```
python -m intentops_core.genesis.provenance          # G1.7 must read PASS
python scripts/genesis/build_manifest.py --check
```

`G1.7-imprint-signature` reading `PASS` is the acceptance test for the whole
ceremony. Until 2026-09-06 that outcome was **unreachable by construction** —
a correctly-signed manifest and a forged all-zero signature returned the same
`REFUSE` — so a green G1.7 is now evidence rather than a formality.

### 5. Publish the fingerprint out of band

Three carriers make a **partial** tamper visible. They do not make a **total**
substitution visible, and nothing in this repository can. Publish the
authority string on the project site, in the signed git tag, and in the release
announcement, so an operator can compare a first clone against something this
repository does not control.

---

## Rotation, revocation, compromise

- **Rotation** is a new ceremony plus lineage: the superseded root keeps
  `rotation.superseded_by`, the successor keeps `supersedes`, and both stay in
  the file through `overlap_until`. Never a deletion.
- **Revocation** is an entry in `config/trust-revocation.json` with a
  monotonic `sequence`. G1.5 REFUSES a sequence lower than the one a node has
  already seen — a rollback to an older revocation list is an attack, not a
  stale file.
- **Compromise** is a T4 event: revoke, rotate, publish out of band, and write
  the incident record. Do not quietly mint a replacement.

---

## What this runbook cannot give you

It cannot make a sitting offline, witnessed, or clean — those are properties of
the room, and the tooling has no way to observe them. It cannot prove custody
of the key after step 1. And a passing `--selftest` on the ceremony tool proves
the arithmetic round-trips on throwaway keys; it says nothing about whether the
real ceremony was performed properly. The ceremony record, signed by the
witness, is the only evidence of that.
