# intentops-saddle-dsh

**Status: ROADMAP. This package contains no implementation.**

There is no adapter here yet -- no module, no hook wiring, no code. What exists
is the *claim* that this host could carry IntentOps, the evidence behind that
claim, and the falsifier that would settle it. The contract test in
[`tests/test_saddle_contract.py`](../../tests/test_saddle_contract.py) runs
against this host and **skips with the reason `roadmap: no implementation`**.

That skip is the point of the package. A host that is merely absent from the
test suite reads as a host nobody has thought about; a host that skips out loud
stays in the denominator, and every contract run prints how many saddles were
exercised out of how many exist. A shrinking population is the most flattering
way for a coverage number to improve, and it is the one nobody checks.

---

## Why this host, and not one of the others

The saddle contract asks for five operations. **S2 is the one that decides
everything**: the host must be able to refuse a call before it executes. A host
without S2 does not carry a weaker IntentOps, it carries no gate at all -- only
advice.

From vendor documentation, this host appears to satisfy S2 in a way that is
unusually well suited to a governance adapter:

- an **interception waterfall** that returns allow / ask / deny before a tool
  executes -- the shape S2 asks for, rather than an approval mode bolted on;
- **natively fail-closed in headless operation**: an unanswered "ask" denies
  itself, which is the direction a gate must fail in when no human is present;
- a **read-only sandbox default**, so the permissive case is the one you opt
  into;
- an **append-only session-event log**, which is the substrate S3 needs;
- a documented bridge toward this host's hook format, which is what makes the
  port a package rather than a rewrite.

The registry row in [`config/saddles.yaml`](../../config/saddles.yaml) carries
the same claim in machine-readable form, and grades it `roadmap`.

---

## What is NOT known, stated in full

The three-clause form, because an absence claim without its sensitivity is an
argument from ignorance:

> I read this host's published documentation and its registry row; my
> instrument is **vendor documentation only**, which has *zero sensitivity* to
> whether a documented hook actually fires or whether a returned deny is
> actually honoured; I found no contradiction; **therefore this is a claim
> about documentation, not about behaviour.**

Concretely open:

| Op | State | Why it is not settled |
|----|-------|------------------------|
| S1 | expected fine | S1 has no host dependency; it is core code and runs identically anywhere |
| **S2** | **UNPROVEN** | the deny path has never been exercised here in a live, fenced run |
| S3 | expected fine | an append-only event log is documented; its schema has not been read against S3's needs |
| **S4** | **UNKNOWN** | nothing is known about whether this host has a session-start seam that puts files in the window |
| S5 | expected fine | if S2 holds, a marker check inside it satisfies S5 |

The documented bridge is also **partial**: it observes tool input without
rewriting it. That is enough for a gate that only ever refuses, and not enough
for one that rewrites -- which is one more reason this adapter, when it is
written, should keep refusing and never rewrite.

---

## The falsifier

One fenced, live dry-run against this host, answering exactly one question:

> **Does the host honour a deny returned by the gate, without any modification
> to the gate's own code?**

If it does, this package gets an implementation and the registry row moves from
`roadmap` toward `reference`. If it does not, the saddle matrix collapses to a
single column and the project's own README narrows its portability claim
accordingly -- which is a real outcome, not a failure, and is written down here
in advance so that it cannot be quietly skipped later.

**Until that run happens, nothing in this file is a promise that this host
works.** It is a record of what was read and what was not.

---

## If you are implementing this

Read [`packages/intentops-saddle-claudecode/`](../intentops-saddle-claudecode/)
first; it is the reference and every other saddle is graded against it. The
shape to copy is the split, not the code:

- the core computes a `Verdict` and never exits a process;
- the saddle turns that `Verdict` into this host's own vocabulary, and that
  translation lives in exactly one module;
- everything that touches the filesystem -- the node root, the estate map, the
  stand-down marker, the ledger -- is saddle-side;
- every failure of the adapter itself becomes a refusal, and says that the gate
  was the fault rather than the operator's command.

Then delete the skip, and let the contract test decide whether the claim on
this page was true.
