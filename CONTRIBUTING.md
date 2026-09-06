# Contributing to IntentOps

This project is a governance framework. That has consequences for how contributions
are handled, beyond the usual "open a pull request": a change to the framework core is
a change to the thing that decides whether *other* actions are allowed to run, so it
gets a higher bar than a typical library change.

## Provisional: contributions are certified by DCO, not a CLA

Pending ruling (decision card `oss-contributions-dco-vs-cla`), the **provisional
default is the Developer Certificate of Origin (DCO)**: sign off every commit
(`git commit -s`) to certify that you wrote the contribution, or otherwise have the
right to submit it, under the project's licence. No separate signed agreement is
required under this provisional default.

This is provisional, not settled, because a project intending to relicense later (a
BUSL-style source-available core moving to a permissive licence on a fixed date, or an
open-core model with a paid tier) commonly wants a Contributor License Agreement (CLA)
instead, or a DCO+CLA hybrid, because a CLA gives the licensor room to relicense
without renegotiating with every past contributor individually. That decision has not
been made. If it changes, this file changes in the same commit as the ruling, and it
changes for future contributions only — nobody's past DCO sign-off is invalidated
retroactively.

## No skill or plugin marketplace

IntentOps does not accept contributions in the form of a marketplace listing, a
downloadable skill package, or any artifact meant to be installed by a user without
review. Every capability that ships is authored or reviewed in this repository, in a
commit, with an author who can be asked why. This is a stated position, not an
oversight — a marketplace whose artifacts load into an agent's context before any gate
sees them is a documented and exploited attack surface in comparable ecosystems. See
`SECURITY.md`.

## Review before commit

A change to anything on the **declared core surface** — the invariants, the tier
ladder, the reaches-reality classifier, the gate/hook chain, either council's own code,
the ordering store, the validation family, or the imprint bundle — is reviewed before
it lands, never after. A silent or missing review blocks a core-surface change; it
never counts as a pass. This mirrors how the framework treats its own values council
(`docs/GENESIS.md` sect. on the values council): a steward that could not convene is a
halt, not an approval.

Non-core changes (documentation, an isolated capability package, test coverage) follow
ordinary pull-request review.

### Install the pre-commit audit

The values-council gate reads a tool CALL, so it cannot see a path assembled at
runtime, an edit made in an editor outside the agent, or a `git apply` whose targets
live inside the patch. All of those still reach the commit, which is what the audit
below checks:

```
python scripts/hooks/pre-commit-core-surface.py --install
```

It refuses a commit that stages a path under `config/core-surface.yaml` with no
matching row in `.intentops/core-review/ledger.jsonl`. The council sits below you, so
the refusal names its own override:

```
python scripts/hooks/pre-commit-core-surface.py --override     --reason "why this core change goes in without a review"
```

A blank reason is refused. Run `--selftest` to prove the refusal and the acceptance
both fire; run `--print-hook` if your repository already has a `pre-commit` hook and
you want to add the line by hand. The audit cannot see `git commit --no-verify`; the
session-start re-hash below is what catches what it misses.

### The imprint is re-hashed at every session start

`genesis/imprint/**` and the node's own `.intentops-rules/` copy are re-hashed against
`IMPRINT-MANIFEST.yaml` at every session start, not only at birth
(`python -m intentops_core.genesis.integrity --session-start`, wired in the reference
saddle's `settings.template.json`; `intentops verify --imprint` is the same check on
demand). Drift HALTS and names the drifted files. If you change a bundle file
deliberately, rebuild the manifest:

```
python scripts/genesis/build_manifest.py --write
```

Rebuilding the manifest silences the instrument; it does not review the edit. The
review is the section above.

## Test-first

- New behaviour ships with a failing test first, then the change that makes it pass.
- Any detector or validator you add or touch must ship a `--selftest` (or equivalent)
  that proves it can actually fire — a detector that has never fired is
  indistinguishable from a broken one, and this project treats that as a real defect
  class, not a nicety.
- Tests run under `tests/` with `pytest`, with no dependency on a live external
  service. A test that requires a running database, a real network endpoint, or a
  specific machine's hardware does not belong in this suite.

## Style

- Python 3.11+, type hints on new code.
- Stdlib-first. A new third-party dependency is a deliberate choice, argued in the
  pull request, not a default.
- Every store-shaped module (anything that reads and writes its own persisted state)
  states its write model in its module docstring: what writes it, how concurrent
  writers are handled, and what a missing or corrupt file means.
- No silent failures: a missing load-bearing field halts rather than falling back to a
  default nobody chose. If you are tempted to write `.get(key, some_default)` for a
  field the code cannot function correctly without, that is the shape this project
  asks you not to introduce.

## Where to start

`docs/quick-start.md` gets a working checkout running. `docs/GENESIS.md` and
`docs/IDENTITY-REPO-CONTRACT.md` explain the two ideas — the boot sequence and the
identity split — that most other design choices here follow from.
