# Ceremony remediation -- every way the wizard can stop, and what to do

`intentops ceremony` never fails silently and never fails with a traceback.
Every stop prints one row from the table below, with its numbered exit code,
and `--resume` continues at the step that was open. Nothing already completed
is repeated, and no key already minted is minted again.

The last column matters as much as the remedy. "Paste the error" is how a
passphrase ends up in a chat log, so every row names exactly what is safe to
hand an assistant when asking for help. **Never paste a passphrase, a private
key, a `.key` file, or the contents of your storage medium. Nothing in that
column ever requires them**, because every failure this tool can raise is
diagnosable from public fields alone.

The table is rendered from the one definition in
`packages/intentops-core/intentops_core/ceremony/wizard.py`; regenerate it with
`intentops ceremony --remedies`. A hand edit here will drift from what the
wizard actually prints, which is the failure mode a remediation document can
least afford.

## The table

| id | exit | What happened | Remedy | Safe to paste when asking for help |
|---|---:|---|---|---|
| `not-attended` | 10 | stdin is not a terminal, so a human may not be present | Run the wizard yourself in a real terminal window. It is never run from a loop tick, a workflow leg, a subagent, a container entrypoint, or a scheduled task -- if a process can trigger the ceremony, the key belongs to the process. | the exit code and this remedy id |
| `ci-set` | 11 | the CI environment variable is set | Unset CI, or move to a machine that is not a build agent. Consent from an automated job is not consent. | the exit code and this remedy id |
| `refused` | 12 | stdin ended, or the operator declined, at a prompt | Nothing was lost. Re-run with --resume to continue at the step that was open. | the exit code, the remedy id, and the step name |
| `python-too-old` | 20 | this interpreter is older than Python 3.11 | Run the ceremony under Python 3.11 or newer; the project declares that floor in pyproject.toml. | the exit code and the version line the wizard printed |
| `crypto-unavailable` | 21 | the Ed25519 implementation is not importable | pip install cryptography, then re-run. A ceremony that cannot do the maths halts; it never degrades. | the exit code and the remedy id |
| `selftest-failed` | 22 | mint_release_root.py --selftest did not pass | Do not proceed. Re-run `python scripts/genesis/mint_release_root.py --selftest` and read its report: it names which round-trip failed. A ceremony run on tooling that cannot round-trip produces a key nobody can verify. | the whole selftest report line (it carries no key material) |
| `manifest-drift` | 23 | build_manifest.py --check reports drift in the imprint bundle | Resolve the drift BEFORE signing. A signature over a stale hash block is worse than no signature. Run `python scripts/genesis/build_manifest.py --check` to see the findings; if the bundle legitimately changed, rebuild with --write, review the diff, and start the ceremony over. | the DRIFT findings (paths and hashes only) |
| `medium-inside-repo` | 30 | the chosen output path is inside a git repository | Choose a path outside every repository -- a removable volume, or a directory under your home that git does not track. A private key never enters a repository, public or private. | the exit code and the remedy id, NOT the path |
| `medium-unwritable` | 31 | the output path could not be created, or a test write failed | Check the medium is mounted, unlocked, and writable by you, then re-run with --resume. The wizard writes a throwaway probe file first precisely so this is found before a key is minted, not after. | the exit code and the OS error text |
| `passphrase-weak` | 40 | the passphrase is shorter than 12 characters | Use the passphrase you decided on before the sitting. The runbook is explicit that it is chosen beforehand, not composed at the prompt. | the exit code and the remedy id ONLY -- never the passphrase |
| `passphrase-mismatch` | 41 | the two passphrase entries did not match | Re-run with --resume and type it again. The wizard allows a limited number of attempts and then refuses, rather than looping, because an unbounded prompt is a place to guess. | the exit code and the remedy id ONLY -- never the passphrase |
| `passphrase-wrong` | 42 | the passphrase does not open the key already on the medium (resume only) | This is the passphrase you set when the root was minted earlier in this ceremony. If it is lost, the minted key is unusable: delete the medium's key files and start a fresh ceremony. Nothing in the repository has been changed yet. | the exit code and the remedy id ONLY |
| `mint-failed` | 50 | minting a key was refused by the primitive | The refusal text names the cause -- most often an output path inside a repository, or a missing passphrase. Fix it and re-run with --resume; no partial key is left behind. | the CEREMONY REFUSED line (it carries no key material) |
| `sign-failed` | 60 | signing the imprint manifest was refused | The commonest cause is that the manifest no longer carries `signatures: []` -- it is already signed, or was edited. Check `git diff genesis/imprint/IMPRINT-MANIFEST.yaml`. The wizard refuses to guess where a signature belongs. | the CEREMONY REFUSED line |
| `carrier-shape` | 70 | a carrier does not carry the placeholder text the edit replaces | One of the three carriers has been edited by hand, or is already minted. Do NOT make one carrier agree with another -- that is exactly the tamper G1.3 and G1.4 exist to catch. Restore the carriers from a clean checkout and start over. | the exit code, the remedy id, and the carrier path |
| `carrier-not-confirmed` | 71 | the operator did not type the three-carrier phrase | Nothing was written. Review the diff the wizard printed. When you are satisfied, re-run with --resume and type the phrase exactly: apply the three-carrier edit | the exit code and the remedy id |
| `verify-g17` | 80 | G1.7-imprint-signature did not read PASS against the edited tree | The acceptance test for the whole ceremony failed. Read the check's reason line: a key_id mismatch means the manifest was signed by a key the trust-roots file does not declare; a 'does NOT verify' means the manifest changed after signing. Re-run `python scripts/genesis/build_manifest.py --check` first. | the G1.7 check object from the JSON the wizard printed (id, outcome, reason, evidence -- all public) |
| `verify-genesis` | 81 | a real `genesis --dry-run` did not reach G7 without the dev flag | G1 now passes but a later phase halted. The wizard prints the phase lines; the failing one names its own remedy. This does not invalidate the ceremony -- the keys and the signature stand -- but do not announce the release until it is green. | the phase lines the wizard printed |
| `record-failed` | 90 | the ceremony record could not be written to the medium | The keys and the carrier edit stand; only the record is missing. Fix the medium and re-run with --resume, or copy docs/CEREMONY-RECORD.template.md and fill it by hand from the public fields the wizard printed. | the exit code and the OS error text |
| `state-unreadable` | 91 | --resume was asked for and no readable ceremony state was found | Run without --resume to start a fresh ceremony, or pass --out <medium> so the wizard can find the journal beside the key. A state file that half-parses is never guessed at. | the exit code and the remedy id |

## The row that is not in the table

A failure the wizard does not model prints `CEREMONY STOPPED [undeclared] exit 1`
and the full traceback. That is deliberate: inventing a numbered row for a
failure the tool has no model of would be a reassuring summary standing where an
actual cause belongs. Nothing already completed is lost -- the journal is written
after each step -- and `--resume` continues at the step that was open. The
traceback is generated from this repository's own code and carries no key
material, but read it before sending it.

## Two things no remedy can give you

**A lost passphrase is a lost key.** The private keys are written
passphrase-wrapped and the passphrase is never stored, journalled, printed, or
passed to a subprocess. If it is lost between minting and signing, the remedy
is to delete the key files from the medium and run a fresh ceremony. Nothing in
the repository has been changed at that point, so the cost is the sitting, not
the release.

**A partial three-carrier edit is not repaired by making the carriers agree.**
If `config/trust-roots.yaml`, the compiled pin and `docs/GENESIS.md` disagree,
that is precisely what G1.3 and G1.4 exist to catch. Re-derive from the key, or
restore all three from a clean checkout and start over. Editing one to match
another is the tamper, performed by hand.

## What is safe to share, in general

Everything the wizard prints is public by construction: fingerprints, `did:key`
values, key ids, authority strings, PEM **public** keys, check ids, outcomes and
reasons. The wizard has no code path that prints private key bytes -- the mint
primitive returns a record that does not contain them, and
`scripts/ops/trust_material_check.py` is the mechanical check that none of it
reaches the tree.

The ceremony record on your medium is also public-fields-only, by construction.
The one thing on that medium that is not shareable is the `.key` files
themselves.
