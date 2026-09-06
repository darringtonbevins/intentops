# config/

Every file here is either a **closed-vocabulary declaration** (a rule
nothing may fall through by default) or a **placeholder for an act only the
operator may perform** (minting a root, running a real alignment interview).
None of them contain, or may ever come to contain, a private key, a real
signature, a client identifier, or an operator's real ruling history -- see
each file's own header for its write model.

| File | Purpose | HALTs on absence? |
|---|---|---|
| `trust-roots.yaml` | The embedded PKI authority strings a clone checks its own birth bundle against (invariants, imprint, releases). Ships with `status: proposed` and obvious placeholder key material until the operator runs the root-minting ceremony. | **Yes.** G1 (offline genesis verification) HALTs if this file is missing, unparseable, or its `roots` list is empty -- there is no default of "run unverified" other than the explicit, interactive, LOTO-tagged override described in the file's own header. |
| `trust-revocation.json` | The signed, monotonic revocation list. Ships at `sequence: 0` with no entries and no signature, because nothing has been revoked and no root exists yet to sign one. | **No**, by itself -- a clone that has never fetched a revocation list treats absence as "nothing revoked yet". A file present with a **lower** sequence number than the cached one is refused (rollback protection), which is a REFUSE, not a HALT. |
| `core-surface.yaml` | The declared list of path globs that make a write a **core-mechanic change** -- one requiring the values-council gate rather than ordinary review. | **Yes**, for the values-council gate itself: an undeclared surface is a hard exit, never a default of "probably not core". A missing file means the gate cannot tell what it is protecting and must refuse to proceed rather than guess. |
| `capability-kinds.yaml` | The taxonomy of capability KINDS (service, estate-store, organ, registry, …) and what each kind owes in probe / boot-anchor / liveness accounting. | **No** for ordinary operation -- a project without this file simply has no capability-membership audit yet. It becomes load-bearing only once a project wires a membership-audit script against it, at which point a missing or malformed kind is that script's own HALT, per its own rules. |
| `alignment-interview.template.yaml` | The STRUCTURE of the interview a node uses to elicit its own operator's judgement -- sittings, question kinds, the judgement-variable floor, the loader's refusals, the calibration bar. Ships with example/placeholder questions only; no real ruling, no household module, no names. | **No** on its own -- a node that has not yet run any alignment interview is simply uncalibrated, which is a stated, honest state (`uncalibrated: N of 20 rulings, X%`), never a silent default of "aligned". The interview LOADER halts on any malformed question per the refusals documented inside the file. |
| `genesis-probes.yaml` | The small, general probe suite a newborn node runs against its own boot corpus to answer "can a fresh window find each organ" (self-probe/v1 schema). | **No** for boot itself -- a missing probe suite means the aliveness check (`intentops doctor --birth`) cannot run check 1 and reports that check ABSENT, which is itself an honest, printed finding, not a crash. |
| `saddles.yaml` | The host-adapter registry: which runtimes (Claude Code, dsh, Codex CLI, OpenClaw, a bare API loop) implement the five saddle operations (S1 classify, S2 decide, S3 record, S4 boot_corpus, S5 halt), and at what evidence grade. | **Yes**, for `intentops genesis` at G0: an unrecognised host binds no saddle and HALTs, naming what it needs -- never a fall-through to "run ungated". |
| `ports.yaml` | The small, generic public subset of default ports (gateway, api, embed, rerank, local inference, vector store). | **No** -- these are convenience defaults, not safety-critical. A project overrides them freely; nothing in the gate chain depends on a specific port number. |

## The one rule that spans all of them

**An undeclared value is a hard exit, never a default of "probably fine".**
Every closed vocabulary in this directory (`role`, `status`, `signs`,
`kind`, `tier`, `provenance`, `grade`, …) exists so that an unrecognised
value is caught and refused rather than silently coerced into the nearest
guess. This is the same discipline stated once, generally, in
`no-silent-failures.md` rule 6, and it is why every file above states its
own HALT behavior explicitly instead of leaving it to be inferred from the
code that reads it.
