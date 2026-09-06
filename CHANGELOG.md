# Changelog

Notable changes to IntentOps. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions are
[semantic](https://semver.org/) with the caveat that everything below `1.0.0` is a seed
and its surfaces may move.

Two rules govern what may appear here, and they are the reason this file is worth
reading rather than skimming:

- **Nothing is listed as done until a recorded run says so.** Work in progress lives
  under *Unreleased* and is marked as such. A changelog that describes intentions is a
  roadmap wearing a receipt's clothes.
- **Defects are stated plainly, in the release that carried them.** The `0.1.0` entry
  below names a defect that made genesis unreachable for every user of that artifact. It
  is not softened, and it is not moved into the entry that fixed it.

The evidence behind every entry: [`docs/BUILD-RECORD-2026-09-06.md`](docs/BUILD-RECORD-2026-09-06.md),
[`docs/BUILD-RECORD-2026-09-06-wave2.md`](docs/BUILD-RECORD-2026-09-06-wave2.md),
[`docs/BUILD-RECORD-2026-09-06-wave3.md`](docs/BUILD-RECORD-2026-09-06-wave3.md), and the
recorded falsifier runs in [`docs/falsifiers/`](docs/falsifiers/).

---

## [Unreleased]

Seed wave 3 is **built, verified, corrected and unreleased.** The record that backs
every entry below is [`docs/BUILD-RECORD-2026-09-06-wave3.md`](docs/BUILD-RECORD-2026-09-06-wave3.md),
which carries the verifier verdicts verbatim with their `claim | refuted | applied`
triples, the test totals and the gate lines. Unreleased means *not tagged*: nothing here
has been through a release cut.

Measured after the last correction, in this tree: **1,327 tests passing, 77 skipped**
(962 at the wave-2 tag). Gates: exposure tree and history CLEAN, trust material CLEAN
(0/284), manifest 15/15, probe coverage complete, estate manifests 6/6 CONFORMANT,
claims check CLEAN, and a real `genesis --dry-run` in a temp node reaching **G7 PASS**
with `probes 42/42 PASS, 0 ABSENT`.

### Added — wave 3

- **Knowledge layer** (`packages/intentops-core/intentops_core/knowledge/`) — ring
  classification where a missing key resolves to QUARANTINE and never to a ring; the
  absorber contract and its watermark; the collection registry over one embeddings table
  with a write gate that makes classification a write-time obligation rather than a
  backfill project; a provider-neutral embedder that refuses rather than inventing a
  vector; ring and corpus coverage with high-water-mark posture where **DEGRADED
  outranks improvement**. Ships with an empty taxonomy
  (`config/ring-taxonomy.template.yaml`), which is a true statement about a new node.
  Nothing in the package calls a model.
- **Metabolism** (`packages/intentops-core/intentops_core/metabolism/`) — the four-stage cadence
  (absorb → distill → crystallize → method), its heartbeat, the crystallization document
  contract, the four assimilation verbs, and the grok cycle as a dry-run recorder. Every
  stage ships **disabled** and is a declared seam with a null implementation; a stage
  that ran and produced nothing renders WARN.
- **Gateway core** (`packages/intentops-gateway/`) — the governed tool surface. Bearer
  token on every request or 401, with no anonymous identity, no localhost bypass and no
  environment variable that opens one; a default-deny per-harness backend allowlist with
  no value meaning "all"; per-call tier classification where an **untagged tool is T4 and
  BLOCKED**; T3/T4 as an ASK that files an approval record and never executes. Serves
  four built-in reads at birth, because a newborn node has no backends.
- **Presence organs** (`packages/intentops-core/intentops_core/presence/`) — channels, the intent taxonomy, the
  operator rule, the router and the interaction journal. See
  [`docs/PRESENCE.md`](docs/PRESENCE.md).
- **Belief carriers**
  (`packages/intentops-core/intentops_core/validation/belief_carriers.py`) and
  **responsiveness**
  (`packages/intentops-core/intentops_core/validation/responsiveness.py`) — the specification the currency
  instrument reads, and the correction half of notice-and-correct: a tripped falsifier is
  answered by supersede, by reaffirm-with-a-reason, or it stays open, and a question that
  simply stops being raised is a **silent close** that gets counted.
- `scripts/genesis/probe_coverage_check.py` — every organ a node has must be reachable by
  a fresh window. Reports exemptions with their reasons and members outside the
  population rather than filtering them; fails on a planted unprobed organ; `--selftest`.
- [`docs/BOOT-ANCHORS.md`](docs/BOOT-ANCHORS.md) — the pointer index a cold window
  carries.

### Fixed — wave 3 corrections, from the adversarial pass

- **The ring taxonomy had no consumer on the write path.** `gate_write` ringed rows from
  the collection's declared sensitivity alone; nothing read the taxonomy a node writes at
  birth, so an operator could rule a source type into a stricter ring and *nothing
  changed* — and `packages/intentops-core/intentops_core/genesis/organs.py` named two
  readers that did not read. The write gate
  now consults it: an explicit taxonomy row joins the strictest-wins merge with the
  collection's sensitivity and the row's own ring, so a ruling can only narrow. Taxonomy
  **silence** deliberately does not quarantine: an unlisted source type leaves the
  collection's declaration standing, or every write on every new node would quarantine.
- **A misspelled gate mode was coerced and then recorded as a measurement.**
  `INTENTOPS_RING_GATE=enforcce` resolved to `observe`, and the coverage instrument wrote
  `gate_mode: observe` into its append-only ledger — making a typo permanently
  indistinguishable from a chosen posture. A present-but-undeclared value now HALTs with
  a remedy; unset and set-to-empty still mean `observe`. The coverage instrument catches
  that halt, records it as an error and reads `unreadable`, which forces DEGRADED: the
  finding stays in the population instead of becoming a crash.
- **The coverage CLI graded a store nobody wired.** It measured a freshly-built
  in-memory store and printed `NOT-YET-ARMED … nothing to classify` at exit 0 on every
  node, populated or not — the instrument's own blindness wearing a posture's clothes. It
  now requires `--dsn` and otherwise prints `UNMEASURED` and exits 2.
- **A row whose metadata was not a mapping crashed the coverage instrument**, producing
  no reading at all. The collection is now recorded as an error and grades DEGRADED.
- **The gateway's argument-based tier reading was gated on tool NAMES.** The shell
  classes fired only for tools named exactly `Bash`, `PowerShell` or `Shell`
  (case-sensitive), so a backend tool called `shell` carrying `git push --force` read
  ALLOW T1 while the identical arguments under `Bash` read ASK T4; and the
  outbound-communication branch, which keys on the `mcp__` prefix, was unreachable for
  every backend call because the gateway passed the bare backend-local name. The shell
  classes now fire on any call carrying a non-empty `command`, and backend calls are
  shown the qualified address `mcp__<backend>__<tool>`. The declared tier was always the
  floor and the shipped invoker is Null, so this was never a default-open path — but the
  docstring promised a reading the code did not perform, and it now states its exact
  operating point instead.
- **`intentops.classify` answered for a backend the calling harness was not granted**,
  against its own stated rule that a harness with no grant has no business learning what
  a backend's tools are tiered at. The allowlist now runs first on that path too.
- **`include_rule_text: "false"` served the opposite of the ask** — `bool("false")` is
  true. A non-boolean is refused by name rather than coerced.

> **What is still open** is listed in section 5 of the wave-3 build record, and the two
> worth knowing here: no shipped code path yet *wires* a taxonomy into a store (the write
> gate reads one when a caller passes it, and the template says exactly who reads it), and
> the corrections above were applied and re-measured by one corrector, not independently
> re-reviewed.

**Shipped in this section, and already in the tree:**

### Added
- `CHANGELOG.md` — this file.
- [`docs/RELEASING.md`](docs/RELEASING.md) — how a release is cut: the falsifiers that
  gate it, the gates that must read clean, the manifest check, the tag, the timestamp
  proof, and the statement that the release-root ceremony is the operator's attended act
  and is not part of any automated release path.
- `scripts/docs/claims_check.py` — a Code-rung documentation-truth check. Every path
  named in `README.md`, `docs/GENESIS.md` or `docs/quick-start.md` must exist, and every
  documented `pip install` target must be installable. Paths resolve the way a reader's
  markdown viewer resolves them: from the document's own directory first, then the
  repository root. Carries `--selftest` (20 paths). Probe row `GEN-doc-claims` in
  `config/genesis-probes.yaml`; tests in `tests/test_docs_claims.py`. Its first live run
  over the tree found **six** broken claims, all fixed in this section.
- `README.md` — a **What it will never do** section: the twelve refusals every node is
  born carrying, in plain words, with the note that calibration never lifts them.

### Fixed
- **The documented install command could not work.** `README.md` and
  `docs/quick-start.md` both said `pip install -e packages/intentops-core`; there is no
  packaging file at that path, and the distribution is the repository root. Verified by
  running it: *"does not appear to be a Python project: neither 'setup.py' nor
  'pyproject.toml' found"*. Both documents now say `pip install .`, and
  `scripts/docs/claims_check.py` fails on any recurrence — existence alone would not have
  caught it, since the directory does exist.
- `docs/quick-start.md` rewritten against the recorded stranger run
  (`docs/falsifiers/F3-stranger-genesis-2026-09-06.md`) rather than against intent. It
  now states the global position of `--repo-root`, the G1 HALT a clone actually gets, the
  unsigned-development banner and what that flag does *not* open, what `verified: False`
  means before the ceremony, why the tagout oracle reads BLOCKED on an unborn clone, the
  16 birth entries, and stand-down.
- `docs/GENESIS.md`: the state count said "eight normal states" over a nine-row table.
  The driver runs G0–G7; **G8 is a status the calibration gate reports, not a phase the
  driver executes**, and the document now says so.
- `docs/GENESIS.md`: the G8 row described the twin bar alone. The shipped gate is
  **two bars** — the council floor *and* the twin bar, independently — and a new section
  states both, the scorer's refusals, the tagout required to lower either, and what the
  bar does not measure.
- `README.md`: the five-layer table called a second host a roadmap item. The hosted-MCP
  saddle ships at **candidate** grade with **S2 computed, not enforced**
  (`host_honoured=unknown`) and S4 declined; the row now says so, and says that a passing
  deny test would belong on a specific client's row, never on MCP in general.
- `README.md` and `docs/GENESIS.md`: the values council ships in **observe** mode at
  birth — it records and surfaces and blocks nothing. Neither document said which mode
  shipped.
- `README.md`: the exposure gate's two open exemptions — the copyright line and the
  commit-identity allowlist — are now stated in the README rather than left in the fence
  file's comments, with the reason an exemption makes the gate blinder and its number
  better at the same time.
- **Five broken path pointers in `docs/GENESIS.md`**, found by the new check on its
  first live run: four module paths written package-relative
  (a bare `intentops_core` prefix, with no `packages/intentops-core/` ahead of it)
  rather than repo-relative, so a reader could not follow any of them, and one bare
  filename where the artifact's real location is
  `.intentops/trust/operator-root.pub.json`. All five are now full paths.
  Four of the five sat on adjacent table rows and would have been missed by the
  claim-marker gate the checker was first built with — which is why that gate was
  removed rather than widened.

---

## [0.1.1] — 2026-09-06

The correction release. `0.1.0` shipped an artifact no stranger could run; this one is
the smallest set of changes that makes the published tree reach genesis, plus the wave-2
build. Evidence: `docs/BUILD-RECORD-2026-09-06-wave2.md`.

### Fixed
- **The imprint manifest did not describe the bundle any clone receives.** This is the
  `0.1.0` defect, stated in full in that entry below. `.gitattributes` now marks the
  imprint bundle `-text`, the bundle is normalised to LF on disk, and
  `genesis/imprint/IMPRINT-MANIFEST.yaml` was rebuilt over those exact bytes. The two can
  no longer diverge silently: the manifest is re-derived from a `git archive` export in
  test.
- Three test-environment defects that made the cold suite fail on a published export
  while passing in the maintainer's working copy: the exposure fence now skips build
  artifacts (an in-tree `pip install` writes an egg-info `PKG-INFO` carrying the README
  copyright line), the core-glob test walks the tree when there is no `.git`, and the
  anchored-key test locates the scanner by file path.
- `G1.7-imprint-signature` now **verifies** a signature rather than only noting its
  absence: it resolves the signer's key id to an active root, loads the key, and checks.
  Before this it had never once been able to return PASS on any clone — a gate that has
  never said yes is indistinguishable from a broken one.
- The cross-certification refusal existed only as prose written into a JSON artifact, so
  a planted operator root passed every check with three carriers agreeing. It is now
  `G1.2b-root-policy`, a contradiction-class check.
- An unparseable imprint manifest or archetype catalogue now HALTs instead of raising a
  parser error.
- G2 honours the storage medium you choose or refuses it by name: hardware token, TPM and
  OS credential store **halt** as not implemented in this build, rather than recording a
  protection that was not applied. `.intentops/trust/operator-root.pub.json` records
  both the answer
  (`storage`) and what was actually done (`storage_applied`).
- A scoped copyright-line exemption in the history scan (owner-name tokens only, on a
  line carrying the word *copyright* and a year). The history scan reads clean.

### Added
- `docs/TRUST-CEREMONY.md` — the runbook for minting the release root and signing the
  imprint bundle: preconditions, the mint, the one reviewed three-carrier edit, stranger
  verification, publication, rotation, revocation, compromise, and an explicit clause
  that it never runs from a loop tick, a workflow leg, a subagent, or CI.
- `scripts/genesis/mint_release_root.py` — the ceremony tooling that two carriers had
  claimed existed since the seed and which was not in the tree. Two verbs (`mint`,
  `sign`), a typed arming phrase, attended-only with EOF treated as refusal, refuses any
  key path inside the repository, refuses an unencrypted release root, never prints
  private bytes, and deliberately does **not** edit the pin — it prints the three-carrier
  edit for the operator to review.
- Alignment stages and the two-bar calibration gate; the genesis substrate (a
  permissively-licensed compose profile and a declarative store schema); the hosted MCP
  saddle with per-node bearer auth and a default-deny client allowlist, both **on** with
  no off switch; host-capability discovery (proposed operation `S6`); provider-neutral
  routing policy; a portable loop scheduler; imprint integrity re-hashing; and the
  core-surface pre-commit hook.
- `docs/falsifiers/` — the recorded F1 (cold suite) and F3 (stranger genesis) runs, with
  the original FAIL records kept unsoftened as lineage beneath the re-run PASS verdicts.

### Changed
- Both falsifiers were re-run on a clean export of the corrected tree and **PASS**:
  F1 cold suite `957 passed, 76 skipped`; F3 reaches `final state: G7` under the
  unsigned-development flag and HALTs at G1 without it — the designed behaviour until the
  ceremony signs the bundle.

### Known limitations at this release
- The trust ceremony has **not** been performed. Every key in the tree is a declared
  placeholder, four G1 checks refuse on any clone, and `verify` prints `verified: False`.
- The history exposure scan reads CLEAN, but by **exemption, not repair**. The `0.1.0`
  release commit message names the copyright holder in its body, and a commit message is
  none of the four files where that name is allowed. Rewriting a message on a published
  commit was rejected as the more damaging option; the scan instead exempts the declared
  owner tokens on a line matching the word *copyright* followed by a year, on that line
  alone, and prints how many lines it exempted on every run. The name is still in the
  history.
- Three key-storage media (hardware token, TPM, OS credential store) are named and halt
  rather than being implemented.
- `release/v0.1.0-commit.txt` is covered by a path exemption in the fence. That is an
  exemption, not a repair.

---

## [0.1.0] — 2026-09-06

The first public extraction: the governance core, the genesis state machine, the
reference Claude Code saddle, the imprint bundle, the blank estate manifests, and the
validator family — with a clean history. Evidence:
`docs/BUILD-RECORD-2026-09-06.md`.

### The defect this release shipped

**Genesis was unreachable for every user of this artifact.** Stated plainly because a
release note that omits it would be the fabrication the framework's own rules forbid.

`.gitattributes` declares `* text=auto eol=lf`, so every clone on every platform checks
the imprint bundle out with LF line endings. `genesis/imprint/IMPRINT-MANIFEST.yaml` had
been built from a working copy that predated that file and still held CRLF, and the
manifest builder hashes raw bytes with no newline normalisation. The manifest therefore
recorded the digests and byte lengths of a checkout **no clone would ever produce**.

The arithmetic, checked rather than assumed: `genesis/imprint/IMPRINT.md` was 56,635 bytes in the
manifest and 55,679 in the export — a delta of 956, against a file of exactly 956 lines.
One byte per line. Git's own answer was decisive: `git ls-files --eol` reported the
index copy as LF and the attribute as `text=auto eol=lf` (LF on every platform), while
the working copy alone was CRLF -- the maintainer's checkout was the outlier.

The consequence was total, not partial. `G1.6-imprint-hashes` read CHANGED, which is a
**contradiction** — carriers that disagree with each other — and the
`INTENTOPS_GENESIS_UNSIGNED_DEV` override is documented never to open a contradiction.
So there was no flag, no argument and no documented workaround.

Two honest readings sit beside that, and both belong here:

- The refusal itself was **correct**. The override downgrades absence and never
  contradiction, and it refused with the flag open, exactly as designed. What failed was
  packaging, not the sequence.
- **The suite passed in the only environment that could not fail.** The in-repo tests
  were green throughout, because the maintainer's checkout was the one tree whose bytes
  matched. That is the "measure in the environment the code will run in" failure,
  reproduced exactly.

Recorded in `docs/falsifiers/F1-cold-suite-2026-09-06.md` (root cause, hard-key
verification) and `docs/falsifiers/F3-stranger-genesis-2026-09-06.md` Part 4 (the live
halt from a fresh-clone-equivalent export). Both carried `VERDICT: FAIL` at the time.
Fixed in `0.1.1`.

### Added
- The framework core: invariants, the T0–T4 tier ladder and the reaches-reality
  classifier that outranks it, the gate engine, the Posture Council and the Moral Compass
  Council, the ordering store, the validation family (grounded-signal, witness,
  still-true), estate manifests, store-write discipline.
- The genesis state machine: G0–G7 plus stand-down, sixteen birth entries each declaring
  its write model, the identity-repo skeleton, the birth probe suite, and the six birth
  questions.
- The imprint bundle: the birth text, four invariant files, nine rules and the archetype
  catalogue — fifteen hashed artifacts.
- The Claude Code reference saddle, and the five-operation saddle contract.
- The validators, each with `--selftest`: exposure gate (tree and history), trust-material
  check, estate-manifest check, identity-repo check, LOTO ledger and its append-only
  writer, wish-register check, imprint manifest builder.
- Apache-2.0 licensing, `NOTICE`, `PROVENANCE.md`, and an OpenTimestamps proof of the
  release commit (`release/`).

### Fixed during the build, before the tag
- The unverified-boot override was an environment variable alone. It now requires an
  attended terminal **and** a typed phrase, plus the banner and the T3 tagout it already
  had, and a dry run records that the dry run — not the operator — let the phase past.
- The development flag downgraded *every* refusal, including contradiction-class evidence:
  a tampered clone reached G7 at exit 0. Absence and contradiction are now different
  classes and only absence is downgraded.
- An `EOFError` at an operator gate produced an unjournalled stack trace instead of a
  HALT, breaking the invariant that a phase which cannot record its transition halts.
- The HALT line truncated its reasons to the first three. Every blocking reason now
  reaches it, id-tagged.
- `VERSION` was not a PEP 440 version, so metadata generation failed and **the
  distribution could not be built at all** — the `intentops` console script declared in
  `pyproject.toml` never existed. Every test had run from a source checkout on
  `PYTHONPATH`: the one environment that could not fail.
- Two birth probes (`GEN-operator`, `GEN-motto`) read ABSENT on every clone because the
  boot corpus carried neither fact — a packaging failure, not a missing organ.
- The exposure gate's excerpt redacted only the span it was reporting, so on a line
  carrying two fenced things each was published inside the other's excerpt, into a CI log.
  Every fenced span on a reported line is now blanked.

### Known limitations at this release
- The commit identity in the first commit carried the author's personal fields. The
  history scan (`exposure_gate.py --history`) exists to see exactly this; the repair is a
  history rewrite by whoever holds the repository, and it cannot be done from a file.
- Historical file *content* is unscanned: `--history` reads identities and messages, not
  blobs.
- `scripts/ops/loto_check.py` reads BLOCKED on an unborn clone, because the ledger does not exist
  until genesis creates it. Correct behaviour, misleading first impression — now
  explained in `docs/quick-start.md`.
- G0 warns on a clone with no host marker and would halt on a real run without
  `--saddle`.

---

*Version links are deliberately omitted: a forge URL for this repository embeds the
copyright holder's account name, which is a fenced token everywhere except `LICENSE`,
`NOTICE`, `README.md` and `PROVENANCE.md`. Compare `v0.1.0...v0.1.1` on the forge, or
read `git log v0.1.0..v0.1.1` locally.*
