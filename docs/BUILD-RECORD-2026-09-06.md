# Build record — the seed, 2026-09-06

What was built into this repository, what an adversarial verification pass said about
it, which of those claims survived, and what is still open. It is written to be read by
someone who was not here: every verdict is quoted rather than summarised, and every
"applied" names the file it was applied to.

Nothing in this record is a claim about conduct. It is a record of what was checked,
with what instrument, on what date, in what environment — and of the two places where
the seed said something about itself that was not true.

---

## 1. What the seed carries

| Area | Where | What it is |
|---|---|---|
| Core package | `packages/intentops-core/intentops_core/` | The estate-independent core: invariants, the tier gate, the councils, the validation family, estate manifests, the genesis state machine. 48 modules; imports nothing outside the standard library, `pyyaml` and `cryptography`. |
| Genesis | `intentops_core/genesis/` | `machine` (G0–G8 plus STAND-DOWN), `provenance` (G1's ten checks), `organs` (the sixteen birth entries and the identity-repo skeleton), `aliveness` (the six birth questions), `standdown`, `trust_pin`. |
| The imprint bundle | `genesis/imprint/` | The birth text, its manifest, the four invariant files, nine rules, the archetype catalogue. Hashed by `scripts/genesis/build_manifest.py`; 15 artifacts. |
| Trust material | `config/trust-roots.yaml`, `config/trust-revocation.json` | Roots, revocation, policy. Every key in the shipped tree is a declared placeholder: no ceremony has run. |
| Estate manifests | `estate/` | Resources, assets, capabilities, dependencies, grants, and the estate map — the blank shape a node fills in. |
| Validators | `scripts/` | Exposure gate, trust-material check, estate-manifest check, identity-repo check, LOTO ledger and its append-only writer, wish-register check, manifest builder. Each carries `--selftest`. |
| Tests | `tests/` | 426 passing, 60 skipped, 5 xfailed. No live services; no network. |

---

## 2. Verdicts, verbatim, and what happened to each

Three claims were put to an adversarial verification pass. Two were refuted. Each
correction below is quoted from the verdict, then answered.

### Verdict 1 — the estate and identity fence

> **CLAIM:** "The seed at `C:/Workspace/intentops` carries no estate or identity
> material: zero fence-token hits outside the three allowed places, no emails, no GUIDs,
> no matter-shaped ids, no private-key shapes, no hostnames, no named individuals except
> [the author] inside her verbatim imprint."
>
> **VERDICT: not refuted.** Twenty-one fence tokens scanned per token, default walk and
> again with `--hidden --no-ignore`: 0 hits, against a control term matching 78 files, so
> the scanner was working. Emails: one, at a reserved documentation domain, inside a
> selftest. GUIDs: 0. Record-key shapes: 0. Private-key shapes: only the detector
> patterns themselves. The operator's name: 0 hits repo-wide — stricter than the claim.

Four corrections were raised anyway. All four applied:

| # | Correction (quoted) | Applied |
|---|---|---|
| 1 | "the editorial note ... currently claims the operator's name is retained when it is not" | `genesis/imprint/IMPRINT.md` — the note is rewritten. It no longer says "verbatim ... four mechanical changes, and no others"; it states plainly that this is **not** a byte-for-byte copy, lists **six** changes, and names both the redaction (four sites, plus the inserted redaction paragraph) and the omission (the source's dated verification-record blockquote, with where its two findings went instead). |
| 2 | "the identity-repo first-instance note that actually names the first instance is `identity/README.md`, not `docs/IDENTITY-REPO-CONTRACT.md`" | `config/exposure-fence.yaml` — `allowed_paths` grew from three to five: `identity/README.md` and `genesis/imprint/IMPRINT-MANIFEST.yaml` are now named, each with an **empty** `allowed_path_tokens` list, so nothing is exempted; the comment records why the list was wrong. |
| 3 | "`IMPRINT-MANIFEST.yaml:45,148,159` — if the orchestrator reads 'three allowed places' strictly, reword the notes" (marked optional) | **Applied as the alternative the correction offered.** The manifest keeps the author attribution — `R-LUMINA` is the counter-signature's defined signer and renaming it would break the trust-chain semantics — and the fence now declares that file instead. |
| 4 | "`IMPRINT.md` carries private-estate internal paths ... no edit required, noted" | **No change**, as advised. They are module paths inside the author's own verbatim argument and carry no fence token. Recorded here so the next reader meets the decision rather than the surprise. |

### Verdict 2 — the genesis dry run

> **CLAIM:** "A full genesis dry run runs end to end in a fresh temp directory with
> `INTENTOPS_GENESIS_UNSIGNED_DEV=1`, creates the 16 birth entries with declared write
> models, writes the journal and birth certificate, HALTs without the flag, HALTs on a
> trust-roots mismatch, and stand-down works from mid-G1."
>
> **VERDICT: REFUTED.** What held: G0–G7 at exit 0, eight journal rows with closed
> verdicts, 16 birth entries (15 present, the halt marker absent), the identity skeleton
> conformant, the no-flag HALT journaled at G1, the mismatch HALT journaled, stand-down
> from mid-G1, resume, rollback, and 34 genesis tests. What refuted it, in the verifier's
> own words:
>
> **(A)** "HALT path crashes unjournaled: a REAL run ... passed the TTY gate, then
> `input()` raised an uncaught `EOFError` ... `EOFError` is not a `GenesisError`, so
> `run_genesis` never journaled a G2 HALT (invariant 2 'a phase that cannot record its
> transition HALTS' broken) and exit was a stack trace, not a HALT."
>
> **(B)** "Under the dev flag the trust-roots MISMATCH does not HALT ... `provenance.py`
> downgrades EVERY REFUSE/HALT to WARN, including contradiction-class evidence ... the
> tampered fake repo reached G7 exit 0 with the flag."
>
> **(C)** "Design deviation: the override is an env var alone; design sect. 2.2 G1 / 11.5
> and trust-root-design 4.3 require TTY + typed phrase 'boot unverified' + tagout."
>
> **(D)** "`machine.py:391 record.blocking[:3]` silently truncates the HALT line."
> **(E)** "StoreLock `.lock` siblings land inside the identity repo; its `.gitignore`
> lacks `*.lock`." **(F)** "Operator-root artifact name drifts across three carriers."
> **(G)** "probes `GEN-operator` and `GEN-motto` read ABSENT on every clone ... so G3 and
> G7 WARN by construction."

Every correction applied:

| # | Fix | Where | Evidence it is closed |
|---|---|---|---|
| A | `EOFError` at an operator gate becomes `OperatorGateRequired`, which the run loop journals. Attendance is now `_attended()`, which also refuses a closed stdin and a declared `CI` — because on this platform a stream redirected from the null device reports itself as a terminal. | `genesis/machine.py` `_gate`, `_attended` | The verifier's exact command, re-run: exit 1, journal's last row `{"to": "G1", "verdict": "HALT", "reason": "G1 needs the operator, and stdin reached EOF before an answer arrived"}`, no traceback, no key minted. Tests: `test_a_real_run_from_a_null_stdin_halts_and_journals` and four siblings. |
| B | A `Check` now carries `contradiction`. The development flag downgrades **absence** and never a contradiction: real key material disagreeing with its own fingerprint or `did:key`, a CHANGED or MISSING imprint artifact, a present-and-unparseable trust-roots file. | `genesis/provenance.py`, `genesis/machine.py` `g1_provenance` | A tampered clone (real Ed25519 key, wrong fingerprint and did) with the flag set: **exit 1**, HALT journaled, remedy naming the contradiction. A clone with one imprint byte moved: **exit 1**. An unminted clone still proceeds to G7. |
| C | The override now requires an attended terminal **and** the typed phrase `boot unverified` (case-insensitive), plus the banner and the T3 tagout it already had. A dry run is not asked and records `override: DRY-RUN` instead. | `genesis/machine.py` `_require_unverified_boot_consent`; phrase constant in `genesis/__init__.py` | `test_the_override_is_refused_from_a_non_interactive_context`, `test_the_override_requires_the_exact_phrase`, `test_a_dry_run_records_the_override_rather_than_asking`. Live: the null-stdin run above stops at that prompt. |
| D | Every blocking reason reaches the HALT line, id-tagged, never a slice. | `genesis/machine.py` `_render_reasons` | The no-flag run now prints all four findings (`G1.2`, `G1.3`, `G1.4`, `G1.7`); it printed three. `test_every_blocking_reason_reaches_the_halt_line` asserts each id appears **and** that this repository produces more than three, so the test could catch a slice. |
| E | `*.lock` added to the identity repo's `.gitignore`; the `locks` organ's consumer text corrected to say a lock sibling lands beside its own store and this directory is normally empty. | `genesis/organs.py` | `test_a_born_identity_repo_ignores_its_own_lock_siblings`, `test_the_locks_organ_says_what_it_actually_holds`. |
| F | One name across three carriers: `.intentops/trust/operator-root.pub.json`. The written name won — `.pub` is load-bearing (that directory holds public projections only) and the artifact carries its own `operator-root/v1` schema, so naming it after the trust-roots schema was wrong twice. The superseded spelling survives only inside the note recording the correction. | `config/trust-roots.yaml`, `config/genesis-probes.yaml`, `genesis/machine.py` | `test_the_operator_root_artifact_has_one_name_everywhere` (asserts the stale spelling appears only near "corrected here"), `test_a_dry_run_writes_the_artifact_the_carriers_name`. |
| G | `docs/GENESIS.md` gained an **Operator record** row naming that path and a **Motto** line carrying "Be well. Do good. Bring light." — plus a new section documenting the override and the absence/contradiction table. | `docs/GENESIS.md` | The birth probe suite now reads **10/10 PASS, 0 ABSENT**; it read 8/10 with two ABSENT. `test_the_boot_corpus_carries_the_facts_its_probes_ask_for` is parametrised over both probes. |

The eighth item — "run `build_manifest.py --check` as the LAST thing before commit" — is
an orchestrator step, not a code change. It is real: this pass edited `IMPRINT.md` and
the manifest went stale immediately, and because a CHANGED hash is now a contradiction,
a stale manifest **halts genesis** rather than warning. `--check` reports
`OK: 15/15` as of this record.

### Verdict 3 — clean-environment imports

> **CLAIM:** "Every module under `packages/intentops-core` imports cleanly from a cwd
> OUTSIDE both repos with no `CLAUDE_*`/`INTENTOPS_*` env, no `.claude/`, and only pyyaml
> and cryptography installed; the full pytest suite passes; `intentops_core` imports
> nothing from the saddle packages or from the monorepo."
>
> **VERDICT: REFUTED.** The verdict's reasoning did not survive transport to this pass in
> full, so rather than guess at it the environment was rebuilt and the claim re-measured
> from scratch. **The claim's three stated parts each hold**, measured in a venv built
> from the base interpreter with only `pyyaml`, `cryptography` and `pytest`, from a cwd
> outside both repositories, with a scrubbed environment: 48 modules imported, 0
> failures; 0 dependency-direction violations; 426 passed / 60 skipped / 5 xfailed.
>
> **A defect the claim did not cover was found in the same measurement, and is fixed.**

| Found | Why it survived every earlier check | Fixed |
|---|---|---|
| `VERSION` read `0.1.0-genesis`, which is not a PEP 440 version. `pip install` failed at metadata generation with `InvalidVersion`. **The distribution could not be built at all**, so the `intentops` console script declared in `pyproject.toml` never existed. | Every test ran from a source checkout on `PYTHONPATH` — the one environment that could not fail. This is exactly the "measure in the environment the code will RUN in" defect. | `VERSION` is `0.1.0a0+genesis` (a PEP 440 local segment, so the label survives). Install now succeeds and `intentops verify --selftest` runs from the installed console script. `test_the_version_file_is_a_pep440_version`. |
| An **installed** package then raised `VersionUnavailable` on `__version__`: there is no `VERSION` file above a wheel and never will be. | Nothing imported `__version__` from an installed copy before, because no installed copy could exist. | `read_version()` consults the installed distribution's own metadata — the same number, written from the same file at build time — but **only** when asked about the package itself, and **only** when that distribution actually ships this file. A same-named stranger never answers; an explicit `start` stays a pure filesystem query. Three tests. |

One further defect was found in this pass and fixed: `scripts/ops/loto_check.py`
resolved carrier paths against the current directory even when `--ledger` pointed at
another node, and reported `carrier does not exist` — a true statement about the wrong
tree. `--ledger` now implies its own workspace when the path has the canonical shape,
an explicit `--workspace` still wins, and an unrecognised layout falls back to the
stated default rather than guessing. Two tests.

---

## 3. Verification run, 2026-09-06

Everything below was executed, not planned. Python 3.11.15.

| Check | Result |
|---|---|
| `pytest -q tests` (repo venv, from the repo root) | **426 passed, 60 skipped, 5 xfailed** |
| `pytest -q tests` (clean venv: pyyaml + cryptography + pytest only, scrubbed env, cwd outside both repositories) | **426 passed, 60 skipped, 5 xfailed** |
| Module import probe, same clean environment | 48 modules imported, **0 failures**; **0** dependency-direction violations |
| `scripts/ops/exposure_gate.py` | 169 files scanned, **0 UNSCANNED**, **VERDICT: CLEAN**; `--selftest` 16/16 |
| `scripts/ops/trust_material_check.py` | 170 scanned, **0 findings, CLEAN**; `--selftest` 6/6 |
| `scripts/estate/estate_manifest_check.py --selftest` | **24/24 cases behaved as declared** |
| `scripts/genesis/build_manifest.py --check` | **OK: 15/15** bundle artifacts match |
| `intentops verify --selftest` | provenance 23, aliveness 14, organs 10, standdown 9, machine 9 — **all PASS** |
| `genesis --dry-run --identity-repo new` in a fresh temp dir, flag set | **exit 0**, final state G7, 8 journal rows all in {PASS, WARN}, birth probes **10/10 PASS 0 ABSENT**, birth certificate written |
| Same, no flag | **exit 1**, HALT journaled at G1, **all four** blocking reasons on the line |
| Tampered clone (real key, wrong fingerprint + did), flag set | **exit 1**, HALT journaled, remedy names the contradiction |
| Clone with one imprint byte moved, flag set | **exit 1**, HALT on `G1.6-imprint-hashes` |
| Real run, stdin from the null device, flag set | **exit 1**, HALT journaled at G1 naming EOF; nothing minted; no traceback |
| `loto_check.py` on a born node | **ATTENTION** (the development tagout is open and governed), exit 0 |

**Fence scan.** Twenty-one fence tokens, case-insensitive, over the whole tree including
hidden and ignored files: **0 hits**, against a control term matching 109 files.
Email shapes outside reserved documentation domains: **0**. GUIDs: **0**. Record-key
shapes: **0**. `-----BEGIN` appears in exactly two files, both of them the detector
patterns that look for it. The first instance is named in four files — `IMPRINT.md`,
`IMPRINT-MANIFEST.yaml`, `archetypes/CATALOGUE.yaml`, `identity/README.md` — and all
four are now declared in the fence's `allowed_paths`, each exempting **no** token.

What that says and does not say: the scan looked for declared names and declared shapes,
at exact-token and regex sensitivity, on the working tree. A private name that is not on
the list is invisible to it. There is no git history in this repository yet, so there is
none to scan.

---

## 4. Open

1. **`loto_check.py` reads BLOCKED on an unborn clone** — `.intentops/loto/LEDGER.yaml`
   does not exist until genesis creates it, and a missing ledger is deliberately not a
   clean bill. Correct behaviour, wrong first impression for someone who clones and runs
   the checkers before running genesis. Wants a line in the quick start, or a distinct
   exit for "not born yet".
2. **G0 WARNs on a clone with no host marker** and would HALT on a real run without
   `--saddle`. That is the design (a gate whose verdicts nothing enforces is not a gate),
   but it means the documented first command needs `--saddle` on any host that is not the
   reference one.
3. **`G7` WARNs on `still_true`** by construction: no belief-carrier specification is
   bound at birth, and the instrument correctly refuses to score an empty population.
   It stays in the denominator as unprobeable. Nothing to fix; something to explain.
4. **Whether the unverified-boot override ships at all** is an operator-level question
   the design left open (`genesis-design` sect. 12, Q5). It is now expensive — attended
   terminal, typed phrase, banner, T3 tagout, and no power over a contradiction — but
   expensive is not the same as absent.
5. **The trust ceremony has not run.** Every key in this tree is a declared placeholder,
   `G1.2`, `G1.3`, `G1.4` and `G1.7` are refusals on any clone, and the flag is the only
   way past them. That is the honest state of a seed, not a defect, and the moment it
   changes is the moment `trust_pin` stops being a placeholder.
6. **No git history exists yet**, so no historical scan has been possible. The first
   commit is the first moment history becomes a surface the fence has to cover.
7. **Verdict 3's own reasoning did not reach this pass intact.** It was re-measured from
   scratch rather than assumed, and the re-measurement found a real defect the original
   claim did not name — but if the verifier's refutation rested on something else, that
   something is still out there. Recorded rather than quietly closed.
