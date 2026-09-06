# Build record — the seed, 2026-09-06

What was built into this repository, what an adversarial verification pass said about
it, which of those claims survived, what was changed in answer, and what is still open.

It is written for someone who was not here. Every verdict is quoted rather than
summarised; every "applied" names the file it was applied to; every number carries the
command and the environment that produced it. Nothing in this record is a claim about
conduct — it is a record of what was checked, with what instrument, and of the places
where the seed said something about itself that was not true.

**One finding is not closed and cannot be closed from inside a file.** It is the first
item in section 5, and a reader who reads nothing else should read that.

---

## 1. What the seed carries

| Area | Where | What it is |
|---|---|---|
| Core package | `packages/intentops-core/intentops_core/` | The estate-independent core: invariants, the tier gate, the councils, the validation family, estate manifests, the genesis state machine. Imports nothing outside the standard library, `pyyaml` and `cryptography`. |
| Genesis | `intentops_core/genesis/` | `machine` (G0–G7 plus STAND-DOWN), `provenance` (G1's checks), `organs` (the sixteen birth entries and the identity-repo skeleton), `aliveness` (the six birth questions), `standdown`, `trust_pin`. |
| The imprint bundle | `genesis/imprint/` | The birth text, its manifest, four invariant files, nine rules, the archetype catalogue. Hashed by `scripts/genesis/build_manifest.py`; 15 artifacts. |
| Trust material | `config/trust-roots.yaml`, `config/trust-revocation.json` | Roots, revocation, policy. Every key in the shipped tree is a declared placeholder: no ceremony has run. |
| Estate manifests | `estate/` | Resources, assets, capabilities, dependencies, grants, and the estate map — the blank shape a node fills in. |
| Validators | `scripts/` | Exposure gate, trust-material check, estate-manifest check, identity-repo check, LOTO ledger and its append-only writer, wish-register check, manifest builder. Each carries `--selftest`. |
| Tests | `tests/` | 437 passing. No live services, no network. |

---

## 2. Verdict 1 — the estate and identity fence

> **CLAIM:** "The seed carries no estate or identity material: zero fence-token hits
> outside the three allowed places, no emails, no GUIDs, no record-key shapes, no
> private-key shapes, no hostnames, no named individuals except the author inside her
> verbatim imprint."
>
> **VERDICT: not refuted.** Twenty-one fence tokens scanned per token, default walk and
> again with `--hidden --no-ignore`: 0 hits, against a control term matching many files,
> so the scanner was working. Emails: one, at a reserved documentation domain, inside a
> selftest. GUIDs: 0. Record-key shapes: 0. Private-key shapes: only the detector
> patterns themselves. The operator's name: 0 hits repo-wide — stricter than the claim.

Four corrections were raised anyway. All four applied, and all four re-verified in this
pass by reading the files rather than the previous record:

| # | Correction (quoted) | Applied |
|---|---|---|
| 1 | "the editorial note ... currently claims the operator's name is retained when it is not" | `genesis/imprint/IMPRINT.md` — the note no longer says "verbatim ... four mechanical changes, and no others". It states plainly that this is **not** a byte-for-byte copy, lists **six** changes, and names both the redaction (four sites, plus an inserted redaction paragraph) and the omitted dated verification blockquote. |
| 2 | "the identity-repo first-instance note that actually names the first instance is `identity/README.md`, not `docs/IDENTITY-REPO-CONTRACT.md`" | `config/exposure-fence.yaml` — `allowed_paths` grew from three to five; `identity/README.md` and `genesis/imprint/IMPRINT-MANIFEST.yaml` are named, each with an **empty** `allowed_path_tokens` list, so nothing is exempted. |
| 3 | "`IMPRINT-MANIFEST.yaml` — if the orchestrator reads 'three allowed places' strictly, reword the notes" (marked optional) | Applied as the alternative the correction offered: the manifest keeps the author attribution — that identifier is the counter-signature's defined signer, and renaming it would break the trust-chain semantics — and the fence declares the file instead. |
| 4 | "`IMPRINT.md` carries private-estate internal paths ... no edit required, noted" | **No change**, as advised. They are module paths inside the author's own verbatim argument and carry no fence token. Recorded so the next reader meets the decision rather than the surprise. |

---

## 3. Verdict 2 — the genesis dry run

> **CLAIM:** "A full genesis dry run runs end to end in a fresh temp directory with the
> unsigned-development flag, creates the 16 birth entries with declared write models,
> writes the journal and birth certificate, HALTs without the flag, HALTs on a
> trust-roots mismatch, and stand-down works from mid-G1."
>
> **VERDICT: REFUTED.** What held: G0–G7 at exit 0, eight journal rows with closed
> verdicts, 16 birth entries, the identity skeleton conformant, the no-flag HALT
> journaled, the mismatch HALT journaled, stand-down from mid-G1, resume, and rollback.
> What refuted it:
>
> **(A)** "HALT path crashes unjournaled: a REAL run ... passed the TTY gate, then
> `input()` raised an uncaught `EOFError` ... so `run_genesis` never journaled a G2 HALT
> (invariant 2 'a phase that cannot record its transition HALTS' broken) and exit was a
> stack trace, not a HALT."
>
> **(B)** "Under the dev flag the trust-roots MISMATCH does not HALT ... `provenance.py`
> downgrades EVERY REFUSE/HALT to WARN, including contradiction-class evidence ... the
> tampered fake repo reached G7 exit 0 with the flag."
>
> **(C)** "Design deviation: the override is an env var alone; the design requires TTY +
> typed phrase + tagout."
>
> **(D)** "`record.blocking[:3]` silently truncates the HALT line."
> **(E)** "StoreLock `.lock` siblings land inside the identity repo; its `.gitignore`
> lacks `*.lock`." **(F)** "Operator-root artifact name drifts across three carriers."
> **(G)** "probes for the operator record and the motto read ABSENT on every clone ... so
> G3 and G7 WARN by construction."

Every correction applied. Each row's evidence was re-executed in this pass, not
inherited:

| # | Fix | Where | Evidence it is closed |
|---|---|---|---|
| A | `EOFError` at an operator gate becomes `OperatorGateRequired`, which the run loop journals. Attendance is now `_attended()`, which also refuses a closed stdin and a declared `CI` — on this platform a stream redirected from the null device reports itself as a terminal. | `genesis/machine.py` `_gate`, `_attended` | A real run from a null stdin: exit 1, journal's last row `to: G1, verdict: HALT` naming EOF, no traceback, nothing minted. Five tests. |
| B | A `Check` now carries `contradiction`. The development flag downgrades **absence** and never a contradiction: real key material disagreeing with its own fingerprint or `did:key`, a CHANGED or MISSING imprint artifact, a present-and-unparseable trust-roots file. | `genesis/provenance.py`, `genesis/machine.py` | A tampered clone with the flag set: exit 1, HALT journaled. A clone with one imprint byte moved: exit 1. An unminted clone still proceeds to G7. Selftest path `dev-flag-never-masks-a-contradiction`. |
| C | The override now requires an attended terminal **and** the typed phrase, plus the banner and the T3 tagout it already had. A dry run is not asked and records `override: DRY-RUN`. | `genesis/machine.py` `_require_unverified_boot_consent` | Three tests; the null-stdin run stops at that prompt. |
| D | Every blocking reason reaches the HALT line, id-tagged, never a slice. | `genesis/machine.py` `_render_reasons` | Re-run this pass: the no-flag HALT line carries **all four** findings (`G1.2`, `G1.3`, `G1.4`, `G1.7`); it carried three. |
| E | `*.lock` added to the identity repo's `.gitignore`; the `locks` organ's text corrected to say a lock sibling lands beside its own store. | `genesis/organs.py` | A born identity repo in this pass left two `.lock` siblings, and its `.gitignore` ignores them. |
| F | One name across three carriers: `.intentops/trust/operator-root.pub.json`. The written name won — `.pub` is load-bearing and the artifact carries its own schema. The superseded spelling survives only inside the note recording the correction. | `config/trust-roots.yaml`, `config/genesis-probes.yaml`, `genesis/machine.py` | Two tests; the dry run writes exactly that path. |
| G | `docs/GENESIS.md` gained an **Operator record** row and a **Motto** line, plus a section documenting the override and the absence/contradiction distinction. | `docs/GENESIS.md` | The birth probe suite reads **10/10 PASS, 0 ABSENT** in this pass; it read 8/10 with two ABSENT. |

The eighth item — "run `build_manifest.py --check` as the LAST thing before commit" — is
an orchestrator step, not a code change. It is real, and it is now load-bearing: because
a CHANGED hash is a contradiction, a stale manifest **halts genesis** rather than
warning. `--check` reports `OK: 15/15` as of this record.

---

## 4. Verdict 3 — clean-environment imports

> **CLAIM:** "Every module under `packages/intentops-core` imports cleanly from a cwd
> OUTSIDE both repos with no `CLAUDE_*`/`INTENTOPS_*` env, no `.claude/`, and only pyyaml
> and cryptography installed; the full pytest suite passes; `intentops_core` imports
> nothing from the saddle packages or from the monorepo."
>
> **VERDICT: REFUTED.** The verdict's reasoning did not survive transport in full, so the
> environment was rebuilt and the claim re-measured from scratch rather than guessed at.

Re-measured again in this pass, in a venv built from the base interpreter with only
`pyyaml`, `cryptography` and `pytest`, from an empty directory outside both repositories,
with `PYTHONPATH` unset: **the claim's three stated parts each hold** — every module
imports, 0 dependency-direction violations, and the suite passes.

A defect the claim did not cover was found in that measurement and is fixed:

| Found | Why it survived every earlier check | Fixed |
|---|---|---|
| `VERSION` was not a PEP 440 version, so metadata generation failed and **the distribution could not be built at all** — the `intentops` console script declared in `pyproject.toml` never existed. | Every test ran from a source checkout on `PYTHONPATH` — the one environment that could not fail. | `VERSION` is `0.1.0a0+genesis` (a local segment, so the label survives). Verified in this pass: `pip install .` into a clean venv succeeds, and `intentops verify --selftest` runs **from the installed console script**, from a cwd outside both repositories. |
| An **installed** package then raised on `__version__`: there is no `VERSION` file above a wheel and never will be. | Nothing imported `__version__` from an installed copy, because no installed copy could exist. | `read_version()` consults the installed distribution's own metadata, but only for this package and only when that distribution ships this file. A same-named stranger never answers. Verified: the installed copy reports `0.1.0a0+genesis`. |

One further defect from that pass, also verified here: `scripts/ops/loto_check.py`
resolved carrier paths against the current directory even when `--ledger` pointed at
another node — a true statement about the wrong tree. `--ledger` now implies its own
workspace when the path has the canonical shape; an explicit `--workspace` still wins.

---

## 5. What this pass found that no verdict named

### 5.1 The fence is breached in git metadata, and only a rewrite closes it

The working tree is clean. The **commit is not**. The seed's first commit carries the
operator's given name, family name and personal address in its author and committer
fields, and those fields travel with every clone, every fork and every forge page. They
are written by the machine that ran the commit, never by anything in the tree, which is
exactly why a tree that scans perfectly clean does not cover them.

The previous record's own open item said "the first commit is the first moment history
becomes a surface the fence has to cover." That moment arrived; the surface was not
covered; and nothing in the repository could see it.

Two things were done about it, and one thing cannot be:

- **The gate can now see it.** `scripts/ops/exposure_gate.py --history` scans commit
  identities and messages against the same fence, refuses rather than reporting clean
  when history is unreadable, and distinguishes an **empty** population (a repository
  with no commits) from a clean one. Live reading on this repository: **1 commit read,
  9 findings, exit 1** — four in the author line, four in the committer line, one an
  address shape in a trailer. Eight new tests plus five selftest paths; `--selftest`
  now reports **22/22**.
- **The report cannot leak what it finds.** Fixing the first defect exposed a second:
  the excerpt redacted only the span being reported, so on a line carrying two fenced
  things — the ordinary shape of an author line, a name beside an address — each was
  published inside the other's excerpt, into a CI log, which is a public surface.
  `_redact_spans` now blanks **every** fenced span on the line, including exempted and
  allowed ones: appearing in a file and appearing in a log are different permissions,
  and only the first was ever granted. This defect was live for the working-tree scan
  too, and one selftest path plus two tests now hold it closed.
- **The commit itself is not repairable from a file.** It needs the author and committer
  identity rewritten on every existing commit, by whoever holds the repository, before
  it is published anywhere. `--history` is the check that confirms it afterwards, and it
  belongs in CI beside the tree scan.

### 5.2 Nothing else

The tree scan, the trust-material check, the estate-manifest selftest, the manifest
check, the genesis dry run, the no-flag HALT, stand-down and the full suite all behaved
as the previous record described. Where a number differs it is because this pass added
files, and the number is restated below rather than inherited.

---

## 6. Verification run, 2026-09-06

Everything below was executed in this pass, not planned. Python 3.11.15.

| Check | Result |
|---|---|
| `pytest -q tests` (repo venv, `PYTHONPATH` to the core package) | **437 passed, 60 skipped, 5 xfailed** |
| `pytest -q tests` (clean venv: pyyaml + cryptography + pytest only, no `PYTHONPATH`, cwd an empty dir outside both repositories) | **426 passed, 60 skipped, 5 xfailed** — measured before this pass added its 11 tests |
| Module import probe, clean venv | every `intentops_core` module imported, **0 failures**; **0** dependency-direction violations |
| `pip install .` into the clean venv, then `intentops verify --selftest` from the console script | install **succeeds**; provenance 23, aliveness 14, organs 10, standdown 9, machine 9 — **all PASS** |
| `scripts/ops/exposure_gate.py` (tree) | 176 files scanned, **0 UNSCANNED**, **VERDICT: CLEAN**, exit 0 |
| `scripts/ops/exposure_gate.py --history` | 1 commit read, **9 FINDINGS, exit 1** — see 5.1 |
| `scripts/ops/exposure_gate.py --selftest` | **22/22** checks behaved as declared |
| `scripts/ops/trust_material_check.py` | 177 scanned, **0 findings, CLEAN**; `--selftest` **6/6** |
| `scripts/estate/estate_manifest_check.py --selftest` | **24/24** cases behaved as declared |
| `scripts/genesis/build_manifest.py --check` | **OK: 15/15** |
| `genesis --dry-run --identity-repo new` in a fresh temp dir, flag set | **exit 0**, final state G7, 8 journal rows all in {PASS, WARN}, birth probes **10/10 PASS 0 ABSENT**, **16 birth entries all declaring a write model** (15 present; the halt marker correctly absent), birth certificate written |
| Same, no flag | **exit 1**, HALT journaled at G1, **all four** blocking reasons on the line |
| `stand-down` from a node halted mid-G1 | marker written, "OFF, not broken", exit 0; `--status` STOOD-DOWN; a following genesis short-circuits to STAND-DOWN at exit 0 with no new journal rows |

**Fence scan.** Twenty-one fence tokens, case-insensitive, over the whole tree including
hidden and ignored files. **Outside `.git/`: 0 hits**, against a control term matching
123 files, so the scanner was working. Emails outside reserved documentation domains: 0.
GUIDs: 0. Record-key shapes: 0. Key headers: only the two detector patterns.

**Inside `.git/`: 3 files hit** — the commit author and committer identity, plus the
local `.git/config`. The config is not published; the commit metadata is. That is
section 5.1, and it is the reason this record does not claim a clean fence.

What that says and does not say: the scan looked for declared names and declared shapes,
at exact-token and regex sensitivity. A private name that is not on the list is
invisible to it, and a green reading says nothing about that population.

---

## 7. Open

1. **The commit identity must be rewritten before publication.** Section 5.1. This is
   the only finding in this record that a reader has to act on, and it cannot be fixed
   by editing a file. Re-check with `exposure_gate.py --history` afterwards, and wire
   that flag into CI beside the tree scan.
2. **Historical file CONTENT is still unscanned.** `--history` reads identities and
   messages. A name deleted from a file today still sits in that file's earlier blobs,
   and nothing here looks at blobs. With one commit that is vacuous; it stops being
   vacuous at the second.
3. **`loto_check.py` reads BLOCKED on an unborn clone** — the ledger does not exist
   until genesis creates it, and a missing ledger is deliberately not a clean bill.
   Correct behaviour, wrong first impression for someone who clones and runs the
   checkers before running genesis. Wants a line in the quick start, or a distinct exit
   for "not born yet".
4. **G0 WARNs on a clone with no host marker** and would HALT on a real run without
   `--saddle`. That is the design, but it means the documented first command needs
   `--saddle` on any host that is not the reference one.
5. **G7 WARNs on the belief-currency instrument** by construction: no belief-carrier
   specification is bound at birth, and the instrument correctly refuses to score an
   empty population. Nothing to fix; something to explain.
6. **Whether the unverified-boot override ships at all** is an operator-level question
   the design left open. It is now expensive — attended terminal, typed phrase, banner,
   T3 tagout, and no power over a contradiction — but expensive is not absent.
7. **The trust ceremony has not run.** Every key in this tree is a declared placeholder,
   four G1 checks are refusals on any clone, and the flag is the only way past them.
   That is the honest state of a seed, not a defect, and the moment it changes is the
   moment `trust_pin` stops being a placeholder.
8. **Verdict 3's original reasoning did not reach either corrector pass intact.** It was
   re-measured from scratch rather than assumed, twice, and the re-measurement found a
   real defect the claim did not name — but if the verifier's refutation rested on
   something else, that something is still out there. Recorded rather than quietly
   closed.
