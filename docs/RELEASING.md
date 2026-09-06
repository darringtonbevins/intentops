# Releasing

How a release of IntentOps is cut, and what has to be true before one is.

This is a runbook, not a policy statement: every step below is a command with an
expected reading, and a step whose reading does not match is a stop, not a note. It is
written for whoever holds the repository — there is exactly one such person today, and
the steps that require a human say so rather than assuming one is watching.

**One thing here is never automated, and never will be.** The release-root ceremony
(section 6) is the operator's own attended act. It is not part of this checklist's
happy path, it does not run in CI, and no release script may invoke it. Its runbook is
[TRUST-CEREMONY.md](TRUST-CEREMONY.md).

---

## 0. What a release is allowed to claim

A release claims exactly two things: *this tree passed these gates*, and *this commit is
the one those gates ran against*. It never claims that the framework works, that a node
is aligned, or that anything is verified when `verify` prints `verified: False`.

`CHANGELOG.md` is the place those claims are written down, and it carries its own rule:
nothing moves out of *Unreleased* until a recorded run says it shipped. A defect that a
release carried is named in **that release's** entry, not quietly repaired in the next
one's.

---

## 1. Quiet the tree

A release is measured on a tree nobody is writing to. Two concurrent writers produced a
suite that read `835 passed`, then `13 failed`, then `64 failed`, then `835 passed`
again over eighteen minutes during wave 2 — none of it a real regression, all of it
modules read mid-write.

```
git status --short
```

Expect **empty**. Then confirm no peer is mid-write before you trust any number below:
uncommitted work that is not yours is a stop, and adopting it into your release commit
is its own failure. A number taken over a moving tree is not a releasable artifact, even
when it is green.

---

## 2. The gates

Every one of these must read as stated. There is no `|| true` anywhere in this section,
by design: a step allowed to fail quietly is a step nobody reads the output of.

```
python scripts/ops/exposure_gate.py
python scripts/ops/exposure_gate.py --history
python scripts/ops/trust_material_check.py
python scripts/estate/estate_manifest_check.py --selftest
python scripts/identity/identity_repo_check.py --selftest
python scripts/docs/claims_check.py
python scripts/ops/loto_check.py
```

| Gate | Required reading | Why it is here |
|---|---|---|
| exposure gate (tree) | `VERDICT: CLEAN`, `0 UNSCANNED` | No estate name or shape reachable from anything published. `0 UNSCANNED` matters as much as CLEAN: a file the gate could not read is not a file it cleared |
| exposure gate (`--history`) | `VERDICT: CLEAN`, **and every exemption line read** | Commit author and committer fields are written by the machine that made the commit, never by the tree, so a perfectly clean tree does not cover them. The scan prints its identity-allowlist and message-shape counts whether or not they changed the verdict, precisely so a CLEAN reached by widening an exemption is distinguishable from a CLEAN reached by there being nothing there. Read those counts; a new one is a finding |
| trust material | `VERDICT: CLEAN` | No private key shape, in any form, at any layer |
| estate manifests | selftest PASS | Every violation class can still fire, and the clean cases still load |
| identity repo | selftest PASS | Same, for the identity-repo contract |
| documentation claims | `VERDICT: CLEAN` | Every path a document claims exists, and every documented install target is installable. This gate exists because `pip install -e packages/intentops-core` shipped in two documents and could not work |
| tagout oracle | not BLOCKED, **or** every open T3/T4 governed with an authority and a stated way back | A safety or detection control switched off with no way back is abandoned, not parked |

Run each selftest as well as each gate. **A detector that has never fired is
indistinguishable from a broken one**, and a green reading from an instrument that cannot
go red is not evidence of anything.

---

## 3. The manifest, checked last

```
python scripts/genesis/build_manifest.py --check
```

Expect `OK: 15/15 bundle artifacts match the manifest`.

This is deliberately the **last** mechanical check before the tag, because a CHANGED
hash is a *contradiction* and a contradiction **halts genesis** — the unsigned-development
flag downgrades absence and never opens it. A stale manifest is therefore not a warning
in a log; it is a released artifact that no user can run.

That is not hypothetical here. It is precisely what `0.1.0` shipped: the manifest was
built over CRLF working-copy bytes while git stored LF, so every clone read CHANGED and
halted at G1 with no override available. The manifest is now re-derived from a
`git archive` export in test, so the two cannot diverge silently again — but run the
check anyway, and run it against an export, not your working copy.

---

## 4. The falsifiers

A release is gated on two **recorded** verdicts. Not a green suite — a written record,
in `docs/falsifiers/`, naming its environment, its pinned commit, and what it did and did
not establish.

- **F1, the cold suite** — does the full suite pass against the framework-core package
  *as shipped*, with the reference host runtime entirely absent: fresh interpreter, fresh
  virtual environment, every `CLAUDE_*` and `INTENTOPS_*` variable unset, no harness
  configuration directory, the source tree not on the import path?
- **F3, the stranger genesis** — does a caller with no prior state get an actual node
  with its own identity out of `intentops genesis`, or a pile of working parts?

Both are run against a **`git archive` export** of the pinned commit, never against the
working copy. That distinction is the entire lesson of `0.1.0`: the in-repo suite was
green because the maintainer's checkout was the one tree in the world whose bytes matched
its manifest, and F1 is the instrument that found it.

Rules that survive from the runs already recorded:

- **A FAIL is recorded as a FAIL and blocks the release.** The original FAIL records are
  kept in place beneath the later PASS re-runs, unsoftened, as lineage. A record that is
  edited into agreement with a later outcome is not a record.
- **State the environment.** An unstated environment is an unstated assumption, and a
  deviation that cannot be removed is recorded rather than argued away.
- **A transcript is literal or it is not a transcript.** One F1 block was a composite —
  the population line of one run above the hit list of another — and it read as verbatim.
  A test now checks that a quoted transcript's stated hit count matches the HIT lines it
  quotes.
- **Say what the verdict does not establish.** F1's FAIL triggered a stated reopen
  condition about the project's primary noun and was not, on its own evidence, proof of
  it. Both halves belong in the record.

---

## 5. The suite

```
PYTHONPATH=packages/intentops-core python -m pytest -q tests
```

Expect zero failures, with the pass and skip counts recorded in the build record beside
the commit they were taken at. State the count **with its denominator**; a rate without
one is not a measurement.

Then repeat it cold, as F1 does. A warm in-repo number and a cold installed-package
number are different readings and neither substitutes for the other.

---

## 6. The release-root ceremony — the operator's attended act

Until this has been performed, every key in the tree is a declared placeholder, four G1
checks refuse on every clone, and `intentops verify` prints `verified: False`. That is
the honest state of a seed and it does not block cutting a seed release — it blocks
claiming the release is *verified*.

When it is performed, it is performed by the operator, present, and never by any
automation described in this file:

- `scripts/genesis/mint_release_root.py mint` generates the root **outside** the
  repository, behind a typed arming phrase, attended-only with EOF treated as a refusal.
  It refuses any key path inside the repository and refuses to write an unencrypted root,
  and it never prints private bytes.
- It **deliberately does not edit the pin**. It prints the three-carrier edit for the
  operator to review and apply as one reviewed change. A tool that could mint authority
  and install it in the same breath is the shape of the attack the three-carrier pin
  exists to make visible.
- `... sign` signs the imprint manifest over the canonical payload defined once in the
  core, so the signer and the verifier cannot drift apart.
- No loop tick, workflow leg, subagent or CI job may run either verb.

Full preconditions, rotation, revocation and compromise handling:
[TRUST-CEREMONY.md](TRUST-CEREMONY.md).

---

## 7. Cut it

1. **Update `VERSION`.** It must be a PEP 440 version. This is not a formality: a
   non-conforming `VERSION` made metadata generation fail during the seed build, which
   meant the distribution could not be built at all and the declared `intentops` console
   script did not exist — invisible to every test, because every test ran from a source
   checkout on `PYTHONPATH`.
2. **Update `CHANGELOG.md`.** Move only what a recorded run supports out of *Unreleased*.
   Name the release's own known limitations in its own entry.
3. **Commit**, then **verify the distribution builds and the console script runs** from a
   clean virtual environment, from a working directory outside the repository:

   ```
   pip install .
   intentops --repo-root <path-to-the-clone> verify --selftest
   ```

   `--repo-root` is required from outside the repository. `scripts/` and
   `genesis/` are not package data, so without it the integrity selftest looks
   for the imprint hasher inside the virtual environment, finds nothing, and
   (before 2026-09-06) raised a traceback rather than a verdict. It now returns
   a named refusal, and the flag is what makes it pass.

4. **Tag** the release commit.
5. **Timestamp it.** Write the commit hash and tag into `release/<tag>-commit.txt` and
   submit an OpenTimestamps proof of that file to the public calendars. The proof covers
   the commit hash it names, which in turn covers the whole tree at that commit; the
   evidence commit is by construction one commit later, and that ordering is stated
   rather than hidden. Verify later with `ots upgrade` then `ots verify`, once the
   attestation has confirmed.
6. **Re-run the exposure gate over the evidence file before committing it.** The release
   evidence file is where a name leaked once already: `release/v0.1.0-commit.txt` carried
   the operator's name on three lines and turned the gate red, and it is covered today by
   a path exemption — an exemption, not a repair.
7. **Push**, and let CI speak. The three workflows answer three different questions on
   purpose, so a red build says which one failed: `ci` (does it import and do the tests
   pass), `exposure-gate` (is it safe to publish), `contract-tests` (can the gate
   actually refuse).

---

## 8. After

- Re-run `exposure_gate.py --history` against the pushed history. The tree scan does not
  cover commit metadata, and a published commit cannot be repaired by editing a file.
- Record the release in a build record: what was checked, with which instrument, and
  where a claim about the seed turned out not to be true. The build records are the
  reason this file could be written from evidence rather than from memory.
- Anything found after the tag goes in the **next** release's entry, and the shipped
  defect stays in the entry of the release that shipped it.
