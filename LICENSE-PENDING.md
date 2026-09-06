# Licence status: PENDING — no `LICENSE` file ships until this is ruled

**This repository carries no `LICENSE` file, deliberately, and must not be made public
until it does.** A repository with no licence is "all rights reserved" by default; that
is the correct default state for something not yet flipped public, and it is also why a
public flip without a ruled licence in place is a hard stop, not an oversight to fix
later.

## Where the upstream code stands today

The private monorepo this seed is extracted from ships its own `intentops` package
under **BUSL-1.1** (Business Source License 1.1) — a source-available licence, not an
open-source one, with a stated future conversion date to a permissive licence. Some
adjacent packages in that monorepo carry `LicenseRef-Proprietary` instead, which is
inconsistent with a public-facing core and is one of the reasons this decision is
pending rather than settled by inertia.

## The recommendation on record

The licensing research behind this seed recommends **Apache-2.0** for the framework
core, for reasons that generalise beyond any one project:

- An explicit patent grant and a patent-retaliation clause, which BUSL-1.1 and plain
  MIT do not provide — material for a framework whose value is partly in *how* it
  gates actions, not only in its source text.
- Compatibility with the dependency graph already in use: the large majority of
  scanned dependencies are already MIT/Apache-2.0/BSD family, and several are
  themselves Apache-2.0 (a licence family the ecosystem already assumes).
- A clean read for adopters: BUSL-style "source-available, converts later" licences
  ask a prospective contributor to reason about a future date; Apache-2.0 asks nothing
  extra.
- Room for a narrower licence on individual, young, optional **capability** packages
  (never the core) if a commercial or time-boxed licence is wanted for something
  specific — Apache-2.0 for the framework core does not force every future package
  into the same choice.

This recommendation is not a ruling. It is the input to one.

## What is pending, named as decision cards

| Card | Question | Tier |
|---|---|---|
| `oss-licence-apache-2-core` | Licence the framework core Apache-2.0 (recommended), or something else | T3 — his, the licensor's |
| `oss-contributions-dco-vs-cla` | DCO, a CLA, or a DCO+CLA hybrid for external contributions, and who administers it | T3 |
| — | Whether any young, optional capability package ships under a different (e.g. time-boxed FSL) licence instead of the core's | T3, per package, at the time that package is proposed for release |

## The rule this file exists to state plainly

**Do not add a `LICENSE` file to this repository except as the direct result of one of
the rulings above, and do not make this repository public before a `LICENSE` file
exists.** A licence choice made by default — by copying a template, by an automated
tool, or by omission read as permission — is exactly the failure mode this file is
here to prevent. See `CONTRIBUTING.md` for how contributions are handled in the
meantime (provisionally, under DCO, pending the card above).
