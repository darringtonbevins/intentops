# Falsifier F3 -- stranger genesis

**Question.** Does a caller with no prior state -- no session history, no
harness variables, no harness configuration, nothing this project's authors
carry in their heads -- get *an actual node with its own identity* out of
`intentops genesis`, or a pile of working parts?

**Why it is gated.** `docs/adr/0001-category-governance-framework.md` names this
run as a reopen condition: *"A stranger's genesis run, on unfamiliar hardware,
produces a pile of working parts rather than an actual node with its own
identity."* The release plan additionally blocks the trust-root ceremony on a
recorded **PASS** (PMO-PLAN Correction C2).

---

## VERDICT: FAIL

A stranger running the published artifact cannot reach genesis at all.

**Reason, in one sentence:** the imprint bundle a fresh clone checks out does
not match the byte lengths and digests its own manifest records, because
`.gitattributes` normalises the working tree to LF while the manifest was built
from a CRLF checkout, so `G1.6-imprint-hashes` halts on a *contradiction* --
the one verdict class `INTENTOPS_GENESIS_UNSIGNED_DEV=1` is documented never to
open, leaving a stranger with no override and no workaround.

Against a **fresh-clone-equivalent tree** -- `git archive HEAD`, byte-identical
to what any clone or source download produces, because `.gitattributes` declares
`* text=auto eol=lf` -- genesis halts at G1 before minting anything:

```
HALT at HALT: G1 refused: [G1.6-imprint-hashes]
  CHANGED genesis/imprint/IMPRINT.md -- manifest 72589d709d1d/56635B, disk 002d17bb42bb/55679B
  ... (three more)
  NOTE: G1.6-imprint-hashes is a CONTRADICTION -- carriers that disagree with each
  other -- and INTENTOPS_GENESIS_UNSIGNED_DEV does not open it.
```

The unsigned-development flag was open and correctly refused to open it: the
override downgrades *absence*, never *contradiction*. There is therefore no
flag, no argument, and no documented workaround available to a stranger. Root
cause and hard-key verification: `F1-cold-suite-2026-09-06.md`, "Root cause".

**The honest reading of the whole run, though, is narrower and more useful than
that headline alone**, and both halves belong in the record:

- Against a tree whose bytes match its own manifest, the sequence works
  end to end and produces a genuine node -- 54 artifacts, its own key, its own
  designation derived from its own `did:key`, an identity repository, a gate
  that refuses, and a stand-down that works (Part 2 below).
- The blocker is one packaging defect between a manifest and its bundle. It is
  not an architectural failure of the sequence, and the sequence's refusals all
  behaved as designed -- including the refusal that produced this FAIL.

A FAIL is a FAIL. It is recorded as one, it blocks the trust-root story, and the
re-plan route applies. What it is evidence *of* is stated in F1's "What this
verdict does and does not imply".

---

## Environment

| Property | Value |
|---|---|
| Working directory | `C:/tmp/intentops-falsifiers-20260906/f3` and `.../f1head` -- outside both the public repo and the private estate it was extracted from |
| Caller | no prior state: fresh directory, `0` entries before the run |
| Harness variables | **0** -- every `CLAUDE_*` / `INTENTOPS_*` variable unset before launch (29 present in the parent shell); `PYTHONPATH`, `PYTHONHOME`, `PYTHONSTARTUP` unset |
| `HOME` / `USERPROFILE` | redirected into the temp tree; no harness configuration directory inside it |
| Interpreter | CPython **3.11.15**, base interpreter, fresh virtual environment per run |
| Install | `pip install <tree>` -- the root `pyproject.toml`; **the console script was used**, never `python -m` against the source tree |
| Installed version | `intentops 0.1.0a0+genesis`; `intentops.exe` present |
| Subjects | (A) the installed distribution alone, no clone; (B) this machine's working clone; (C) same, flag closed; (D) `git archive HEAD` export at `b9bb5516` -- the fresh-clone equivalent |
| Runs | the driver was executed **three times end to end**, each after deleting its tree. Behaviour identical; the node's designation differed every time (`node-6MkkpMeJ`, `node-6Mkvp5hA`, `node-6MkvMZSo`) -- fresh key material per run, as designed. Not a fluke. |

**Stated deviation.** A harness configuration directory exists at the root of the
drive (`C:\.claude`, 149 bytes) and is on the ancestor path of any directory on
that volume. It was not removed; nothing in this run is the reference host
runtime, and the CLI does not read it. Recorded rather than argued away.

---

## Part 1 -- the installed distribution alone, no clone

Transcript (banner elided after its first appearance; it printed on **every**
command, as documented):

```
$ intentops --node-root nodeA doctor
    ############################################################
    # PROVENANCE: UNVERIFIED                                    #
    # This node is running with the unsigned-development flag   #
    # open. Nothing it carries has been proven to be what the   #
    # project published. It may not federate, may not publish a #
    # trust bundle, and its rulings may not be exported as      #
    # signed artifacts. A T3 tagout is open and the tagout      #
    # oracle reads BLOCKED, not REMEDIATED, until it is closed. #
    ############################################################
    verdict: **NOT-ALIVE**   questions answered: 2/6
    | 1 | can a fresh window find each organ? | self_probe | UNPROBEABLE | no probe suite at <venv>/config/genesis-probes.yaml |
    | 3 | is anything safety-critical switched off? | loto_check | FAILED | no tagout ledger at .intentops/loto/LEDGER.yaml |
    | 6 | manifests and identity repo conformant? | ... | FAILED | no identity repository is bound |
    [exit 1]

$ intentops --node-root nodeA genesis --dry-run --identity-repo new
    HALT at HALT: the imprint rules bundle is empty or absent; a node whose
    refusals never reach a fresh window has them in name only
      remedy: restore <venv>/genesis/imprint/rules from the release
    G0  WARN  ... no host marker found ... binds NONE rather than guessing
    G1  WARN  G1.1-roots-present: absent: config/trust-roots.yaml; G1.5-revocation: absent
    G2  PASS  designation node-6MkkpMeJ derived from the node's did:key;
              DRY-RUN: a keypair was minted in memory and NOT persisted
    [exit 1]

$ intentops --node-root nodeA verify
    WARN  G1.1-roots-present   ... [downgraded from HALT]
    WARN  G1.5-revocation      ... [downgraded from REFUSE]
    WARN  G1.6-imprint-hashes  the imprint hasher is absent [downgraded from HALT]
    WARN  G1.7-imprint-signature ... [downgraded from HALT]
    WARN  G1.8-archetypes      ... [downgraded from REFUSE]
    verified: False
    [exit 1]
```

**Reading.** `pip install intentops` alone cannot run genesis: `config/`,
`genesis/`, `estate/` and `scripts/` are not package data, and `--repo-root`
defaults to the *installed package's* fourth parent, which in a virtual
environment is the venv directory. This is consistent with the documented quick
start (`git clone` first, then install), so it is not a contradiction of the
docs -- but the remedy line points at a path inside the venv, which will read to
a stranger as a corrupted install rather than a wrong invocation. **Not a
failure of this falsifier; an open issue on the error text.** The behaviour
itself is correct: it halted loudly with a named remedy rather than proceeding.

---

## Part 2 -- a stranger with a byte-consistent tree, flag open

`--repo-root` pointed at this machine's working clone (whose imprint bytes match
the manifest -- see the blind spot at the end).

```
$ intentops --node-root nodeB --repo-root <clone> genesis --dry-run --identity-repo new
    G0  WARN  would HALT on a real run: no host marker found. A node with no bound
              saddle has a gate whose verdicts nothing enforces, so this binds NONE
    G1  WARN  G1.2-root-status: root(s) still `proposed`; G1.3-pin: the compiled
              release-root pin is a placeholder
    G2  PASS  designation node-6MkvMZSo derived from the node's did:key;
              DRY-RUN: a keypair was minted in memory and NOT persisted
    G3  PASS  birth probe suite: probes 10/10 PASS; 0 ABSENT (packaging); 0 truth-failures
    G4  PASS  consent recorded, append-only
    G5  PASS  recorded verbatim; supersession only, never edited
    G6  PASS  staged by evidence available, not by clock
    G7  WARN  check 2 (still_true): no belief-carrier specification is bound at birth
    final state: G7
    [exit 0]

$ intentops ... verify
    PASS G1.1-roots-present: 2 root(s) declared
    WARN G1.2-root-status  [downgraded from REFUSE]
    WARN G1.3-pin          [downgraded from HALT]
    WARN G1.4-representations [downgraded from HALT]
    PASS G1.5-revocation: sequence 0, 0 entries
    PASS G1.6-imprint-hashes: every claimed artifact matches on disk
    WARN G1.7-imprint-signature: the imprint manifest is UNSIGNED [downgraded from REFUSE]
    PASS G1.8-archetypes: 0 reserved of 1 entries
    verified: False
    [exit 1]

$ intentops ... doctor --birth
    - designation: `node-6Mkvp5hA`
    - verdict: **ALIVE-DEGRADED**
    - questions answered: 5/6
    | 1 | can a fresh window find each organ? | self_probe | ANSWERED | probes 10/10 PASS; 0 ABSENT |
    | 2 | is every belief I carry still current? | still_true | UNPROBEABLE | no belief-carrier
          specification is bound at birth. Running the instrument over an empty population would
          score a perfect reading by construction, so this stays in the denominator as unprobeable
          rather than reporting a clean sweep of nothing |
    | 3 | is anything safety-critical switched off? | loto_check | ANSWERED | posture ATTENTION
          over 1 tagout(s); controls currently OFF: 1 of 1 tagged |
    | 4 | what do I believe is fixed but have not proven? | witness | ANSWERED | 1 open claim(s),
          W-CLAIM-2026-09-06-BIRTH OPEN-UNVERIFIED -- a birth claim that answered itself would
          not be evidence |
    | 5 | can my gate actually refuse? | intentops gate --selftest | ANSWERED | a reaches-reality
          operation classified T3 and did not pass; a read passed; the refusal is in
          logs/guardian-blocks/ |
    | 6 | manifests and identity repo conformant, and can the validators reject? | ANSWERED |
          six manifests present, schema-valid, entries: [] is a clean load; identity repo
          conformant; both validators rejected their negative fixture (16 findings) |
    [exit 0]

$ intentops ... stand-down
    STOOD DOWN. This node is OFF, not broken. Nothing bad happens to you for using this.
    No reason was asked for and none was recorded. To bring it back, delete the marker
    file yourself -- there is no command that does it.
    [exit 0]

$ intentops ... doctor
    - verdict: **STOOD-DOWN**   (all six rows unchanged)
    [exit 0]
```

**Birth artifacts** -- 54 files under the node root. The load-bearing ones:

`.intentops/config/node.yaml`
```yaml
schema: node-config/v1
as_of: '2026-09-06T20:14:06Z'
designation: node-6MkvMZSo
identity_repo: identity-repo
saddle: null
mode: minimal
imprint_version: 0.1.0-genesis
dry_run: true
```

`.intentops/trust/node-cert.json` (public projection; the PEM body is elided
here, and the key belonged to a discarded dry run)
```json
{ "schema": "node-cert/v1", "designation": "node-6MkvMZSo",
  "did": "did:key:z6MkvMZSo8Wjg8Y2XkuZ9tYmyk7piJ98wbkiyWsm1or8fS2z",
  "public_key_pem": "-----BEGIN PUBLIC KEY----- <elided> -----END PUBLIC KEY-----",
  "fingerprint": "sha256:f9418cf0...768d44", "dry_run": true,
  "note": "This directory holds PUBLIC projections only. A private key appearing
           here is an incident, not a bug." }
```

`.intentops/trust/operator-root.pub.json` -- every operator field carries a
literal `DRY-RUN:` prefix (`"fingerprint": "DRY-RUN: no operator root was
minted"`), so a dry-run record cannot later be mistaken for a real one. It also
carries the reason there is no cross-certification: *"a release publisher that
could mint authority on installed nodes is one compromised key away from a
fleet-wide coup."*

`.intentops/genesis/aliveness.jsonl` -- append-only, one row per reading, each
carrying `verdict`, `answered`, `checks`, `faculties_absent`, and the per-check
instrument, status, detail and counts. Two rows after this run.

Also present: the append-only `journal.jsonl` (one `{from,to,verdict,evidence,as_of}`
row per transition), `provenance-record.json`, `organs.json`, `substrate.json`,
a `LOTO` ledger carrying the `LOTO-GENESIS-UNSIGNED-DEV` tagout and its carrier
file, a `guardian-blocks/2026-09-06.jsonl` holding the gate's own refusal, nine
`.intentops-rules/*.md` carried out of the imprint, and a 22-file
`identity-repo/` with its estate manifests, consent, epochs, founding
conversation and wish register.

**Reading.** This is a node, not a pile of parts: it has its own key, a
designation derived from that key and from nothing inherited, an identity
repository, stores that declare their write model, a gate that demonstrably
refused a T3 operation, an honest `ALIVE-DEGRADED` self-report that *names the
one faculty it lacks and why*, and an off switch that works and says so kindly.

---

## Part 3 -- the same stranger, unsigned-development flag CLOSED

```
INTENTOPS_GENESIS_UNSIGNED_DEV: [<unset>]

$ intentops --node-root nodeC --repo-root <clone> genesis --dry-run --identity-repo new
    HALT at HALT: G1 refused:
      [G1.2-root-status] root(s) still `proposed`: no ceremony has minted them, so
        nothing in this clone can be verified against a key
      [G1.3-pin] the compiled release-root pin is a placeholder
      [G1.4-representations] R-INTENTOPS: placeholder or malformed key material;
        I-INTENTOPS-REL-2026: placeholder or malformed key material
      [G1.7-imprint-signature] the imprint manifest is UNSIGNED. Unsigned and valid
        must never be indistinguishable, so this is a refusal, not a warning
      remedy: mint the release root and sign the imprint bundle, or set
        INTENTOPS_GENESIS_UNSIGNED_DEV=1 to proceed unverified (a T3 tagout opens
        and every command says so)
    G0  WARN  ... no host marker found ...
    [exit 1]

$ intentops ... verify
    PASS   G1.1-roots-present: 2 root(s) declared
    REFUSE G1.2-root-status
    HALT   G1.3-pin
    HALT   G1.4-representations
    PASS   G1.5-revocation
    PASS   G1.6-imprint-hashes: every claimed artifact matches on disk
    REFUSE G1.7-imprint-signature
    PASS   G1.8-archetypes
    verified: False
    [exit 1]
```

Node C's entire footprint afterwards -- five files, nothing minted:

```
.intentops/genesis/journal.jsonl
.intentops/genesis/journal.jsonl.lock
.intentops/genesis/provenance-record.json
.intentops/genesis/provenance-record.json.lock
.intentops/genesis/substrate.json
```

**Reading.** The HALT is exactly as documented: no banner (the flag is closed,
so there is nothing to warn about), every blocking reason printed in full and
id-tagged, no truncation, a remedy that names both the real fix and the override
and states the override's price. It journalled its own refusal before stopping.
Correct, and the strongest single result in this record.

---

## Part 4 -- the graded run: a stranger with a *fresh clone*

Same stranger, same installed console script, `--repo-root` pointed at the
`git archive HEAD` export -- byte-identical to what any user's clone or source
download produces. Unsigned-development flag **open**.

```
$ intentops --node-root nodeFRESH --repo-root <fresh-clone-equivalent> \
      genesis --dry-run --identity-repo new
    ############################################################
    # PROVENANCE: UNVERIFIED ...                                #
    ############################################################

    HALT at HALT: G1 refused: [G1.6-imprint-hashes]
      CHANGED genesis/imprint/IMPRINT.md               -- manifest 72589d709d1d/56635B, disk 002d17bb42bb/55679B
      CHANGED genesis/imprint/invariants/gide.yaml     -- manifest fc2e6090983a/8690B,  disk 1a9f22f4d21a/8471B
      CHANGED genesis/imprint/invariants/tapch.yaml    -- manifest 72b3c9064c65/3340B,  disk 2cc788682883/3248B
      CHANGED genesis/imprint/invariants/vocabulary.yaml -- manifest 83873c62154e/11465B, disk da0161b37dd7/11187B
      remedy: mint the release root and sign the imprint bundle, or set
        INTENTOPS_GENESIS_UNSIGNED_DEV=1 to proceed unverified. NOTE:
        G1.6-imprint-hashes is a CONTRADICTION -- carriers that disagree with each
        other -- and INTENTOPS_GENESIS_UNSIGNED_DEV does not open it. The flag says
        'there is nothing to verify against yet'; it has never meant 'the evidence
        disagrees and proceed anyway'. Fix the carriers or restore the clone.
    G0  WARN  ... no host marker found ...
    [exit 1]
```

The flag was set and the phase refused it anyway, correctly. `git ls-files --eol`
proves the export is what a clone gets (`i/lf`, `attr/text=auto eol=lf`) and that
this machine's working copy (`w/crlf`) is the outlier -- full verification in
`F1-cold-suite-2026-09-06.md`.

**This is the graded reading, and it is a FAIL.** Part 2 shows what the sequence
does when its bytes agree with its manifest; Part 4 shows that no user of the
published artifact reaches that state.

---

## Consequence

- The trust-root ceremony story stays **blocked**: its dependency is a recorded
  PASS, and this is a recorded FAIL.
- The `Falsifier FAIL re-plan` story (PMO-PLAN Correction C2) is the route: a
  decision card owed to the operator before further public-release work.
- The repair is small and testable -- rebuild `IMPRINT-MANIFEST.yaml` from the
  committed (LF) bytes, and add a test that re-derives the manifest from a
  `git archive` export so the two can never diverge again. Once that lands, this
  falsifier should be re-run; Part 2 is direct evidence it will then pass.
- Not fixed by this leg: this record is a reading, not a repair.

---

## Blind spots of this reading

- **Part 2 ran against a non-reproducible tree.** This machine's working copy
  predates the repository's own `.gitattributes` and is the only checkout whose
  imprint bytes match its manifest. Part 2 is therefore evidence about the
  *sequence*, not about what a user receives. It is reported as such and is not
  the graded run.
- **Dry run only.** Every run used `--dry-run`, so no private key was persisted,
  no real consent was taken, and G4/G5/G6 recorded placeholders marked
  `DRY-RUN`. A real run requires an attended terminal by design and cannot be
  driven from an automated context -- which is itself the correct behaviour, and
  means an unattended falsifier can never exercise the human gates. What was
  *not* tested here: the real ceremony, real consent, real founding answers, and
  key storage on any medium.
- **G0 bound no saddle** (`saddle: null`, `mode: minimal`) because the temp
  directories carry no host marker. So the gate's verdicts were produced but
  nothing enforced them; the node said so plainly and would have halted on a
  real run. The saddle-bound path is untested here.
- Genesis was never run to **G8**; the calibration bar requires twenty scored
  operator rulings and no such history exists at birth by construction.
- Three runs on **one** machine and one operating system. "Unfamiliar hardware"
  in ADR-0001's sense -- a different machine, a different OS -- is not covered
  by this record. On the evidence of Part 4 the line-ending defect makes a
  non-Windows run strictly worse, never better.

---

*Run 2026-09-06, three full executions. Driver retained outside the repository;
every command in this record is reproducible from the Environment table above.*
