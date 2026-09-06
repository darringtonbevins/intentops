# The saddle contract

Design of record: `docs/reports/intentops-oss/design/genesis-design.md` sect. 8.

"The saddle" is this framework's name for its **enforcement adapter** — the layer that
ports the framework's gate onto one specific host agent runtime (the "horse", in the
same design's terminology — a term this document does not reuse for the adapter
itself, to avoid the exact ambiguity that name caused in earlier internal drafts).
`packages/intentops-saddle-claudecode/` is the reference adapter, for Claude Code.

## Five operations

A host runtime carries IntentOps if, and only if, it can perform five operations. This
is the entire portability question, reduced to something checkable rather than argued.

| Op | Name | Contract |
|---|---|---|
| **S1** | `classify` | Given a tool and its input, return a tier and whether the action reaches outside the machine — pure, deterministic, keyed off the shape of the command or its target, **never off file content** (so that a self-scoped workspace write is never accidentally bricked by its own content). |
| **S2** | `decide` | Given a classified action, return `allow`, `ask`, or `deny` — and the host must actually **honour a deny before the action executes**. This is the one hard requirement; without it there is no gate, only advice nobody has to take. |
| **S3** | `record` | Append every gate-visible decision to a log. If it was visible to the model, it is logged. |
| **S4** | `boot_corpus` | Deliver the imprint's rules to the agent's context at session start, so a fresh window can actually find its own refusals rather than having them exist only in name. |
| **S5** | `halt` | Refuse every tool call while the stand-down marker exists, checked ahead of every other gate. |

## The matrix

| Host | S1 | S2 | S3 | S4 | S5 | Grade |
|---|---|---|---|---|---|---|
| **Claude Code** | yes | yes | yes | yes | yes | **Reference** — the adapter this repository ships and the only row proven by a live run, not by documentation alone. |
| A second host runtime | — | — | — | — | — | **Roadmap.** Whether a second host can carry the gate without rewriting it has not been proven by a live run as of this seed. Any row added to this table for a host that has not been fenced and dry-run is a claim about that host's *documentation*, not about its behaviour — say so plainly if you add one. |

## The seam, in code

The gate itself (`intentops.gate`) returns a plain verdict value and never exits the
process, writes to standard output, or otherwise reaches outside itself. Each saddle
package owns the *impure* half for its host: how a deny is actually enforced (an exit
code, a returned error, a blocked call — whatever that host's own mechanism is), how
its own configuration format is written, and how the host's boot sequence is told to
call `boot_corpus`. `config/saddles.yaml` registers the known adapters; genesis detects
the host it is running on and binds exactly one adapter — and **binds none rather than
guessing** when the host is unrecognised, halting with a plain statement of what it
needs rather than falling through to running ungated.

## Why this seam, and not a rewrite per host

Every host-specific detail — exit codes, a settings file's exact shape, how that
host's hook chain is wired — lives entirely inside its own saddle package. The
decision logic itself (the tier ladder, the reaches-reality classifier, the councils,
the ordering store) is written once, tested once, and never duplicated per host. A bug
found and fixed in the gate is fixed for every host at once; a bug found in one
saddle's exit-code handling cannot silently become a bug in the gate itself, because
the gate never sees that host's exit codes at all.
