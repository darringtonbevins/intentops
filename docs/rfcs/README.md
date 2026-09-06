# RFCs

This directory is a placeholder for the RFCs the framework's design corpus names as
candidates to port into this public repository, on the phasing the extraction plan
lays out (design of record: `docs/reports/intentops-oss/design/category-ruling.md` and
the extraction manifest referenced there).

## Status: nothing is ported yet

**No RFC has been ported into this repository as of this seed.** Porting an RFC here
means more than copying a file: each one needs the same review the rest of the
extraction manifest applies — checking it for anything specific to a particular
operator, organization, or deployment, and giving it the same reconciliation-note
treatment (stating plainly where the RFC's original claims have since been superseded
or narrowed) that the framework's own canon requires of itself. That review has not
been done for any RFC yet, so none ship here yet, even in draft form.

## What is scheduled to port, in phase P3

The framework's phased extraction plan schedules RFC porting as a **P3** activity —
after the framework core, the reference saddle, and genesis itself are real and
tested, not before. The rationale: an RFC is a design record about the framework;
porting design records ahead of the thing they describe risks shipping documentation
that describes code that does not yet exist in this repository, which is exactly the
kind of gap this project's own review discipline exists to catch elsewhere.

When RFC porting begins, each ported RFC lands here with:

- Its own reconciliation note, stating what has changed since it was written.
- Confirmation it carries no operator-, organization-, or deployment-specific content.
- A note on which parts of it, if any, are aspirational (describe intended design) as
  opposed to observed (describe what the shipped code actually does) — the same
  evidence-grading discipline the rest of this project's documentation follows.

Until then, this file is the honest statement that the directory is empty on purpose,
not by an omission nobody noticed.
