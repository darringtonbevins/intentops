# Build record — seed wave 2, correction pass (2026-09-06)

Companion to [BUILD-RECORD-2026-09-06.md](BUILD-RECORD-2026-09-06.md), which
records the seed itself. This one records **wave 2**: what the build legs
added, what the adversarial verifiers refuted, and what the corrector applied.

Every verdict below is reproduced with its `claim | refuted | applied` triple.
A verdict that was refuted and NOT applied says so, and says why — an
unapplied correction that goes unrecorded is the defect
`no-silent-failures` rule 1 exists to prevent.

---

## 1. Verifier verdicts

### V1 — the falsifier records

> **Claim.** "F1 and F3 were actually executed in the stated clean
> environments, the records carry literal transcripts and honest PASS/FAIL
> verdicts, and a FAIL (if any) names its consequence."
>
> **Refuted: YES**, on one clause of four.

The verifier re-executed F1 from the pinned commit in a clean environment
outside both repositories, reproduced the graded result to the file
(19 failed / 421 passed / 61 skipped / 5 xfailed, resolving to the recorded
18/422 once `git` was on PATH), reproduced the G1.6 CHANGED lines
byte-for-byte, and re-ran F3 Part 4 to the same HALT. It confirmed both
records carry exactly one `VERDICT: FAIL`, name the consequence, and are not
softened.

**The one defect:** F1's exposure-gate block was a **composite** presented as
verbatim — the population line of the post-install run (176 files, 33,847
lines) above the hit list of the pre-install run, with two `PKG-INFO` hits and
the true count edited out. No single run ever produced "176 scanned … 6
hit(s)". Two smaller number errors rode along: "Tracked files exported: 176"
(171 tracked) and "18 of 445 collected" (506 collected, 445 ran).

| # | Correction | Applied |
|---|---|---|
| 1 | `F1:50` — tracked files 176 → 171, with the 176 explained as the post-install scan population | **Applied.** Verified: `git ls-tree -r --name-only b9bb5516 \| wc -l` → 171 |
| 2 | `F1:190-199` — replace the composite with the literal output of ONE run | **Applied.** The block is now the pre-install run over the exported HEAD tree, **reproduced at correction time**: 170 scanned / 33,645 lines / 6 hits. A dated correction note above it states what the block used to be |
| 3 | `F1:227` — say precisely what was removed and from where | **Applied.** The egg-info directory was removed from the throwaway export; the two `PKG-INFO` lines were removed from the transcript. Both are now stated, and the post-install reading is a separate, labelled block |
| 4 | `F1:20` — "18 of 445 collected" → 506 collected, 445 ran, 61 skipped | **Applied** |
| 5 | `tests/test_falsifier_records.py` — a test that a transcript's stated hit count matches its quoted HIT lines | **Applied.** `transcript_inconsistencies()` + `test_quoted_gate_transcripts_are_internally_consistent`, with a negative test that feeds it the exact composite that shipped |

One honest note on correction 3's second block. The 176/33,842 reading was
reproduced by generating the egg-info with setuptools rather than by a full
`pip install`, so its **line total differs by 5** from the original run's
33,847 (`SOURCES.txt` content differs between the two paths). File population,
hit list and verdict are identical. The record says this in the record, rather
than copying the earlier number into a block labelled as measured here — which
would have been the same defect one layer down.

### V2 — the trust tooling

> **Claim.** "The trust tooling never writes a private key into the repo or
> prints one, refuses cross-certification and disagreeing representations,
> verifies real signatures in G1 without the dev flag, and the ceremony runbook
> keeps minting as the operator's attended act."
>
> **Refuted: YES**, on three clauses of four, plus two further defects found.

The verifier drove a real (non-dry-run) G2 mint in a temp directory with `HOME`
redirected and confirmed what **holds**: the private key lands outside every
clone, notes print only the path, the trust directory holds public projections
only, a `HOME` inside the node root halts, a tampered imprint byte stays a
blocking contradiction under the dev flag, and the G2 mint is attended-gated.

| # | Correction | Applied |
|---|---|---|
| 1 | G1.7 must actually **verify** signatures when the pin is minted; forged ≠ valid | **Applied.** `_check_imprint_signature` now resolves `key_id` → active root, loads the PEM, and verifies. PASS on success, HALT `contradiction=True` on a bad signature or an unknown key id, REFUSE only for the genuinely-unminted cases. Payload defined once in `canonical_imprint_payload()` |
| 2 | A `_check_root_policy` that refuses cross-certification and enforces the declared vocabularies | **Applied** as `G1.2b-root-policy`, between G1.2 and G1.3. HALTs (contradiction) on `role: operator_root` in the project file, an undeclared `role` or `signs` value, a non-self-issued release root, a role that names no issuer, and duplicate ids or fingerprints |
| 3 | Unparseable manifest / catalogue must HALT, not raise `ParserError` | **Applied** at both sites, matching the roots-file precedent; both graded as contradictions |
| 4 | Honour the storage answer or stop offering it | **Applied.** `_resolve_storage` HALTs on hardware token / TPM / OS credential store ("not implemented in this build") and on any undeclared answer; `passphrase-wrapped file` prompts via `getpass` and wraps the PKCS8 DER with `BestAvailableEncryption`. `operator-root.pub.json` now records `storage` (the answer) **and** `storage_applied` (what was done). `docs/GENESIS.md` G2 row rewritten |
| 5 | `docs/TRUST-CEREMONY.md` — the runbook | **Applied.** Preconditions (attended, offline, witnessed, clean host), the mint, the one-reviewed-change three-carrier edit, signing, stranger verification, out-of-band publication, rotation/revocation/compromise, and an explicit "never from a loop tick, workflow leg, subagent, or CI" clause. The five dangling `docs/reference/...` pointers in `config/trust-roots.yaml` now point here |
| 6 | `scripts/genesis/mint_release_root.py` — the ceremony tooling the carriers claimed existed | **Applied.** Two verbs (`mint`, `sign`), typed arming phrase, attended-only with EOF-is-refusal, refuses any key path inside the repository, refuses an unencrypted release root, never prints private bytes, and **deliberately does not edit the pin** — it prints the three-carrier edit for the operator to review. `--selftest`: 8/8 |

`trust_pin.py`'s WRITE MODEL docstring is corrected in the same pass: it said
the pin is "edited only by the release ceremony's tooling", and no such tooling
existed anywhere in the tree.

Two of the verifier's own observations are worth keeping as findings rather
than fixes. Before correction 1, G1.7 had **never once been able to return
PASS on any clone** — a gate that has never said yes is indistinguishable from
a broken one, which is why this is a correctness fix and not a hardening.
Before correction 2, the cross-certification refusal existed only as a prose
`note` string written into a JSON artifact at G2, so a planted
`{role: operator_root, issued_by: R-INTENTOPS, status: active}` root passed
every check in the module with three carriers agreeing.

### V3 — the values council, imprint integrity, and the pre-commit hook

> **Claim.** "The values-council step-0 pre-check now routes shell commands
> that target core-surface paths, the imprint is re-hashed at session start
> with HALT on drift, and the pre-commit hook refuses an unledgered
> core-surface commit."
>
> **Refuted: UNKNOWN. Corrections NOT DELIVERED.**

**This verdict reached the corrector truncated**, mid-sentence in its reasoning
("Integrity: in a temp node, a tampered `.intentops-rules/honesty.md` …"), and
carried **no corrections array**. Its WHAT HOLDS section survived — all 8
mutating shapes routed, the 4 benign commands produced no targets, quoted /
uppercase / backslash / `./`-prefixed / `xargs rm` / `ln -sf` / `Set-Content` /
`perl -pi` / `sed --in-place` / `git checkout HEAD~1 --` all routed, the
shipped tests read 116 passed / 5 xfailed, and all three `--selftest`s passed —
but any refuted clauses and their corrections were lost in transit.

**Nothing has been applied for V3**, and nothing about it should be read as
verified. The corrector re-ran what it could reach, which is only the green
half:

```
values_council selftest: 31/31 paths behaved as declared
integrity      selftest: 17/17 paths behaved as declared
pre-commit-core-surface selftest: 9/9 paths behaved as declared
```

A passing selftest is not a verifier verdict. The corrections were requested
back from the orchestrator; until they arrive this is **open**, and it is the
first item in section 4.

---

## 2. Test totals

Full suite, from the repository root, on the project interpreter
(CPython 3.11.15), `PYTHONPATH=packages/intentops-core`:

```
835 passed, 75 skipped, 5 xfailed in 37.62s      [13:52, corrections applied, tree quiet]
```

Baseline before this correction pass: 803 passed / 75 skipped / 5 xfailed.
The 32 added tests are `tests/test_trust_ceremony.py` (V2) and three in
`tests/test_falsifier_records.py` (V1 correction 5).

**The full-suite number then became unstable, and it was not this pass that
made it so.** A peer build leg was writing `values_council.py`,
`pre-commit-core-surface.py` and the saddle-contract surface concurrently.
Four consecutive runs over eighteen minutes:

| Time | Result | `values_council.py` mtime |
|---|---|---|
| 13:52 | 835 passed / 75 skipped / 5 xfailed | — |
| 14:12 | **13 failed** / 822 passed (`NameError`) | 14:12:30 |
| 14:13 | **64 failed** / 771 passed | 14:13:06 |
| 14:13 | 835 passed / 75 skipped / **5 xpassed** | 14:13:06 |
| 14:15 | **4 failed** / 831 passed (saddle contract) | still moving |
| 14:17 | 835 passed / 75 skipped / 5 xpassed, **0 failed** | settled |

The `NameError` is the `live-gate-edits.md` shape exactly: a module read
mid-write between the edit that adds a call and the edit that adds its
definition. **None of these failures is in a surface this pass touched**, and
the corrector deliberately did not repair a peer's in-flight file. Measured
with the four peer-owned test files excluded, the tree is clean:

```
683 passed                                       [14:15, peer surfaces excluded]
157 passed                                       [14:15, this pass's own surfaces:
                                                  trust_ceremony, falsifier_records,
                                                  genesis, genesis_corrections,
                                                  trust_material_check, exposure_gate]
```

Note also that the 14:13 run reported **5 xpassed** where every earlier run
reported 5 xfailed — the peer's change made five expected-failures pass, so
those `xfail` markers are now stale. That belongs to the peer's leg, not this
one, but an xpass is a signal and is recorded here rather than smoothed over.

Environment stated because an unstated environment is an unstated assumption:
these are **warm** in-repo measurements, not cold ones. Falsifier F1's verdict
on the cold suite is `FAIL` and is unchanged by this pass — the line-ending
contradiction it found is a `.gitattributes` defect, not a test defect, and no
correction here touches it.

---

## 3. Gate verdict lines

Every gate, run at the end of the pass, verbatim:

```
exposure gate (tree)      VERDICT: CLEAN
                          population: 217 files scanned, 1 skipped, 0 UNSCANNED, 48705 lines read
                          fence: 21 name digests, 4 shapes, 10 allowed paths, 8 marker lines
exposure gate (history)   FINDINGS: 2   (exit 0 -- see section 4)
                          population: 4 commits read; 8 identity checks, 8 allowlisted
trust material            VERDICT: CLEAN   (findings 0 / 220 scanned; 1 UNSCANNED: release/v0.1.0-commit.txt.ots)
build_manifest --check    OK: 15/15 bundle artifacts match the manifest   (exit 0)
build_manifest --selftest selftest: 9/9 paths fired as specified
estate_manifest_check     SELFTEST PASS -- 24/24 cases behaved as declared
trust_material_check      SELFTEST PASS -- 6/6 cases behaved as declared
exposure_gate --selftest  SELFTEST PASS -- 28/28 checks behaved as declared
provenance --selftest     provenance selftest: 34 paths fired, 0 failed
mint_release_root         mint_release_root selftest: 8 paths fired, 0 failed
values_council            SELFTEST PASS -- 31/31 paths behaved as declared
integrity                 SELFTEST PASS -- 17/17 paths behaved as declared
pre-commit-core-surface   SELFTEST PASS -- 9/9 paths behaved as declared
```

`provenance --selftest` fired 23 paths before this pass and 34 after: the
eleven new ones are the root-policy classes, the two unparseable classes, and
the five signature classes — including `valid-signature-passes`, which was
unreachable by construction until today.

### Genesis dry-run, in a temp directory outside both repositories

```
$ intentops --node-root C:/tmp/genesis-dryrun-w2e/node genesis --dry-run --identity-repo new
  (INTENTOPS_GENESIS_UNSIGNED_DEV=1)

  PROVENANCE: UNVERIFIED banner printed
G0  WARN    would HALT on a real run: no host marker found...
G1  WARN    G1.2-root-status: root(s) still `proposed`...; G1.3-pin: the compiled
            release-root pin is a placeholder...
G2  PASS    designation node-6MkhCr8v derived from the node's did:key; DRY-RUN: a
            keypair was minted in memory and NOT persisted
G3  PASS    birth probe suite: probes 10/10 PASS; 0 ABSENT; 0 truth-failures
G4  PASS    consent recorded, append-only
G5  PASS    recorded verbatim; supersession only, never edited
G6  PASS    staged by evidence available, not by clock
G7  WARN    check 2 (still_true): no belief-carrier specification is bound at birth...
final state: G7                                                          EXIT=0
```

Run a second time **without** `--identity-repo`, the machine halts as designed:
`HALT at HALT: no identity repository is bound` — it never silently creates a
local one. `G1.2b-root-policy` does not appear in either summary because it
**passed**; `test_the_policy_check_runs_in_the_real_pass` asserts it is in the
record rather than trusting the summary's silence.

### Falsifier verdict lines

```
docs/falsifiers/F1-cold-suite-2026-09-06.md:18:## VERDICT: FAIL
docs/falsifiers/F3-stranger-genesis-2026-09-06.md:16:## VERDICT: FAIL
```

Both remain FAIL. The corrections above repaired how F1 *reports*; they did not
touch what it *found*, and the record explicitly resists being read that way.

---

## 4. Remaining issues

1. **V3's corrections were never delivered** (section 1). The values-council
   pre-check, the session-start imprint re-hash, and the pre-commit hook are
   **unverified** in this pass. Requested from the orchestrator; do not treat
   the three passing selftests as a verdict.
2. **The history exposure gate reads 2 FINDINGS**, in the *commit message* of
   `23e20a2c41fa` — the copyright line naming the operator, which the fence
   exempts in `LICENSE` / `README.md` / `NOTICE` / `PROVENANCE.md` but not in
   commit prose. Pre-existing from wave 1, not introduced here, and **not the
   corrector's to fix**: correcting it means rewriting a message on a published
   public commit. Note the gate exits 0 on this, so it will not stop a build —
   which is itself worth a ruling.
3. **The `*.egg-info/PKG-INFO` false-positive class is still open.** Any
   contributor who runs `pip install .` locally turns the repository's own
   exposure gate red (measured: 176/33,842/8 hits). Two ways to close it, both
   owing a stated population cost: a build-artifact exclusion, or an
   `allowed_path_tokens` entry.
4. **`release/v0.1.0-commit.txt` is exempted, not repaired.** F1 recorded this
   as measured; the working tree now carries an `allowed_paths` entry, which
   makes the gate BLINDER over that file rather than removing the name from it.
   The fence's own comment block still reads "4 token-checks removed from the
   population"; it is 10 across 5 files.
5. **The three unimplemented storage media are now honest, not implemented.**
   A hardware-token or TPM custodian for the operator root remains real work;
   G2 currently halts on all three by name rather than pretending.
6. **The ceremony itself has not been performed**, and `docs/TRUST-CEREMONY.md`
   says so in its first line. `G1.7-imprint-signature` can now return PASS, but
   no clone will see it until a root is minted, the imprint is signed, and the
   three carriers move in one reviewed change.
7. **The values-council selftest batch exceeded a 120-second shell timeout**
   once during this pass and completed in the background at exit 0. Not
   investigated; noted so nobody re-discovers it as a hang.
8. **The tree had two live writers** while this pass ran (section 2). The
   orchestrator must re-run the full suite over a quiet tree before committing
   — the last number in this record was taken while a peer's surface was still
   moving, and 4 saddle-contract failures were outstanding at 14:15 in code
   this pass never touched. Five `xfail` markers now `xpass` and want
   re-grading by whoever owns them.

---

*Nothing in this pass was committed. The corrector does not commit; the
orchestrator reviews and commits.*


---

## Addendum — second corrector's verification run (2026-09-06, ~14:25)

**Two correctors ran this pass concurrently.** This record was authored by one
of them; this section is appended by the other rather than overwriting it,
because a shared store with two writers and a whole-file rewrite is the
last-write-wins defect `store-write-discipline` exists to retire — and one of
the two accounts of a correction pass would simply have vanished.

The two passes converged on the same tree: every correction named above is
present on disk, and the distinctive carriers of both were checked by name
(`canonical_imprint_payload`, `G1.2b-root-policy`, `_resolve_storage`,
`_read_passphrase`, `_apply_values_council`, `_ledger_root`, `--no-renames`)
plus the four new files (`docs/TRUST-CEREMONY.md`,
`scripts/genesis/mint_release_root.py`, `tests/test_trust_ceremony.py`,
`tests/test_bypass_closures.py`). Nothing was found missing.

### The clean-tree run this record asked for

Item 8 of the remaining issues above asks the orchestrator to re-run the suite
over a quiet tree, noting four saddle-contract failures outstanding at 14:15.
**That run was taken at ~14:25 and is clean.** The five `xfail` markers it
mentions were the surface-coverage markers in `tests/test_values_council.py`;
the wave-2 builder had left them addressed to the corrector, predicting they
would XPASS "the moment the declared surface matches the tree". They did, and
the marker has been **removed** — a non-strict `xfail` left in place after it
passes lets the surface rot back to dead globs and report the same green.

```
867 passed, 75 skipped, 0 failed, 0 xfailed, 0 xpassed
```

### Gate verdict lines, same run

```
exposure gate (tree)      population: 219 files scanned, 1 skipped, 0 UNSCANNED, 49561 lines read
                          VERDICT: CLEAN
exposure gate (history)   population: 5 commits read
                          history allowlist: 10 identity checks, 10 matched, 15 addresses cleared
                          FINDINGS: 2   (exit 0 — pre-existing, see below)
trust material check      findings 0 / 222 scanned files
                          VERDICT: CLEAN
build manifest --check    OK: 15/15 bundle artifacts match the manifest
estate manifest selftest  SELFTEST PASS -- every violation class above can fire, and the two clean cases still load
identity repo selftest    SELFTEST PASS -- every violation class above can fire, and the conformant fixture still loads
genesis --dry-run         final state: G7   (fresh temp node root, --identity-repo new, EXIT 0)
falsifier F1              ## VERDICT: FAIL
falsifier F3              ## VERDICT: FAIL
```

### Selftests, same run

```
provenance        34 paths fired, 0 failed      (23 before this pass — 11 new)
integrity         17/17 paths behaved as declared
values_council    31/31 paths behaved as declared
pre-commit hook    9/9 paths behaved as declared
mint ceremony      8 paths fired, 0 failed      (new organ)
exposure gate     28/28 checks behaved as declared
trust material     6/6 cases behaved as declared
build manifest     9/9 paths fired as specified
saddle pre_tool    8/8 checks behaved as declared
```

### Two notes this addendum adds to the remaining issues

- **The 2 history findings are the operator's name in the commit-message body
  of `23e20a2c41fa`** (the v0.1.0 release commit). The fence exempts that name
  in `LICENSE` / `README.md` / `NOTICE` / `PROVENANCE.md`, and a commit message
  is none of those. It is **not correctable by editing a file** — it needs a
  history rewrite on a published repository or a history-scope allowlist entry,
  which is an orchestrator/human call. The gate exits 0 today.
- **One correction was declined with a reason.** Making the saddle importable by
  adding it to pytest's `pythonpath` would break
  `tests/test_dependency_direction.py`, which asserts the core package declares
  no saddle dependency and reads the pyproject text to do it. The new tests load
  the saddle **by path** instead: the dependency-direction invariant outranks
  test convenience.

*Neither corrector committed anything.*
