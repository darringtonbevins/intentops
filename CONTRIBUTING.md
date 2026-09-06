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
