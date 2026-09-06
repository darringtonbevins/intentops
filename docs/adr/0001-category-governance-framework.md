# ADR-0001: IntentOps is a governance framework

**Status: PROPOSED** — pending the operator's ratification (decision card
`oss-category-governance-framework`). Nothing in this repository, its packaging, or
its public description should be treated as final on this question until that card is
resolved.

## Context

Three candidate primary categories were argued for what this project's public artifact
*is*: an orchestration-style **agent framework**, an enforcement **adapter** for one
specific host agent runtime (internally called "the saddle"), and an **operative** —
the grown, identity-bearing thing genesis produces (internally called "the program").
Full argumentation and the evidence behind each: `docs/reports/intentops-oss/design/category-ruling.md`.

The three are not actually competing descriptions of one thing; they are three
different layers of the same system (see the five-layer model in the root `README.md`
and `genesis-design.md` sect. 3), and the question this ADR settles is only which of
the three is the *primary noun* — the one a newcomer reads in the first sentence of the
README.

## Decision

**IntentOps is a governance framework for AI agents**, with the enforcement adapter as
its per-host layer and the grown operative as what it instantiates at genesis — neither
of the other two candidates is the primary noun.

Supporting evidence, briefly:

- The package metadata already calls it a framework, twice, independent of this
  ruling.
- The great majority of the codebase's internal imports already use the framework's
  own namespace, not a host-specific one.
- The project's own ruled internal vocabulary already uses the word "framework" for
  this exact thing inside an unrelated, already-ruled definition — so naming it
  anything else would create the very kind of one-concept-two-names collision that
  vocabulary is meant to prevent.
- The enforcement-adapter reading is correct about what is *demonstrated today* (one
  host runtime, proven), but a removal test — "does the agent keep running with the
  adapter removed" — only distinguishes this project from being *the runtime itself*,
  which nobody argued it was. That test does not distinguish a framework from an
  adapter.
- The grown-operative reading is correct about what a fully-run genesis is *supposed to
  produce*, but as of this ADR, the composed end-to-end genesis sequence has not been
  demonstrated on a stranger's machine — calling the repository "the operative" today
  would describe a capability that has not yet been proven to exist.

## Consequences

- The README's first sentence, tagline, and short description follow this ruling
  directly (see `category-ruling.md` sect. 4 for the exact, cited wording).
- Work is owed to make the framework core *actually* self-contained: the package that
  claims to be the framework core must physically contain the code it claims, the
  importable namespace must be able to reach every module the framework claims as its
  own, and the one-way dependency direction (adapter and operative depend on the core;
  the core depends on neither) must be checked by an automated import-graph test, not
  asserted in prose.

## This decision reopens if

- A cold, from-scratch test suite — run with the reference host runtime entirely
  absent — fails against the framework-core package as shipped.
- A second host runtime is fenced and dry-run tested and cannot carry the gate without
  rewriting it (in which case the enforcement-adapter reading was the more honest
  primary all along, and the README's closing clause narrows accordingly).
- A stranger's genesis run, on unfamiliar hardware, produces a pile of working parts
  rather than an actual node with its own identity.
- Legal counsel returns an adverse finding on using "IntentOps" as a mark in commerce
  (a separate question from this ADR, but one whose adverse outcome would force a
  rename that makes revisiting this ADR's wording worthwhile at the same time).
