# The hosted saddle — IntentOps over MCP

An MCP stdio server that offers the framework's governance to **any client that
speaks the Model Context Protocol**: the tier ladder and reaches-reality
classifier, the full gate verdict, the values council, the imprint/trust check,
an aliveness-and-calibration status, and a host-capability discovery.

Registry row: `mcp-hosted` in [`config/saddles.yaml`](../../config/saddles.yaml),
graded **`candidate`** — not `reference`. The rest of this document is why.

---

## The honest limitation, first: S2

**This server cannot refuse a call the host never routes through it.**

The reference saddle rides inside one host's hook chain: the host physically
cannot execute a tool without passing through it, and a deny is a non-zero exit
the host obeys. This saddle is a *peer the host may consult*. It computes
`ALLOW` / `ASK` / `BLOCK` / `HALT` correctly and completely, and then it hands
that verdict to a client which is free to ignore it.

So every gate answer carries `"host_honoured": "unknown"`, and
`intentops.status` returns the string **`S2: host-honoured=unknown`** verbatim.
That is the honest grade until a fenced, live run proves a specific client
honours a deny — at which point the claim belongs on that client's own row,
about *that client*, not on this one about MCP in general.

`S4 boot_corpus` is likewise **declined, not claimed**: MCP gives a server no
hook into the client's session start, so the imprint cannot reach a fresh
window from here. `OPERATIONS["S4"]` returns the refusal and its reason rather
than an empty set, because an empty set reads as "nothing to deliver".

| Op | Here |
|---|---|
| S1 `classify` | implemented — the core classifier, unchanged |
| S2 `decide` | **computed, not enforced** — see above |
| S3 `record` | implemented — append-only JSONL, permits and refusals alike |
| S4 `boot_corpus` | **declined** — no session-start hook exists on this transport |
| S5 `halt` | implemented — checked ahead of authentication, refuses every method |
| S6 `discover` | this package's proposal: typed host descriptors, data only |

---

## The two controls that shipped disabled upstream are ON here

The private gateway this design is extracted from resolved an absent caller
identity to `"anonymous"` by construction, and shipped both of its safety
controls tagged out. A network-reachable governance surface in that state is
not a gate; it is a public API that returns opinions about other people's
actions. Both are on here, and neither has an off switch.

**1. Authentication — a per-node bearer token.**

```
python -m intentops_saddle_mcp.server --mint-token
```

The plaintext is printed **once**, on stdout (guidance goes to stderr, so the
token pipes cleanly), and only its SHA-256 digest is written to
`.intentops/trust/mcp-saddle-auth.json`. A
stolen node directory yields no usable credential, and a lost token is rotated,
never recovered. Every request must carry it:

```json
{"jsonrpc":"2.0","id":1,"method":"tools/list",
 "params":{"_meta":{"authorization":"Bearer <token>"}}}
```

It rides in `_meta` — which MCP reserves on every request for exactly this kind
of transport-independent metadata — rather than in a tool argument, because a
credential in an argument ends up in tool logs, transcripts, and this server's
own ledger. An absent or unreadable auth record refuses **every** request; there
is no anonymous identity to fall through to.

**2. A client allowlist — default-deny.**

`config/saddles.yaml`, row `mcp-hosted`, key `allowed_clients`. **An empty list
refuses everything.** There is deliberately no value meaning "all": if one
existed somebody would reach for it during a debugging session and never take it
back out, which is precisely how the upstream control came to be off for months
with a comment where a reason should have been. An absent key is a halt, not an
empty list — an unfinished registry must not look like an armed control.

The name comes from the client's own `initialize` frame, so it is
**authorisation on top of authentication, never identification**. A caller
holding a valid token may present any name it likes.

Order is load-bearing: **stand-down → authentication → allowlist → method.**

---

## The tools — a closed set of six

| Tool | What it answers |
|---|---|
| `intentops.classify` | S1: the tier, and whether this reaches outside the machine |
| `intentops.gate` | the full verdict for a proposed call, recorded to the ledger |
| `intentops.status` | aliveness, the calibration string, the S2 gap |
| `intentops.council` | the values council over a change to a declared core surface |
| `intentops.imprint_verify` | are the imprint bytes the claimed ones, and does the trust chain agree |
| `intentops.discover` | S6: typed capability descriptors of the host |

The surface does not grow at runtime. A governance server whose tool list can
change is one whose blast radius nobody can state.

`intentops.status` reports calibration as **`uncalibrated: N of 20`** until this
node has 20 real rulings by its operator graded at 80% or better by a
forward-only scorer. Below the bar the node is telemetry, never authority. A
partial grade counts as a miss — the fail-toward-uncalibrated direction, chosen
deliberately over inventing a fractional score nobody assigned.

---

## S6 `discover` — assimilate the shape, never the artifact

A node that knows its host has a million-token context, subagents, a prefix
cache with a TTL and parallel tool calls can **route differently**. That is the
whole of the assimilation worth having, and the whole of what is safe.

The fence is a **type boundary, not a policy**:

| Permitted | Forbidden |
|---|---|
| a capability **descriptor** — booleans, bounded integers, closed-enum strings | an **artifact** — a skill file, prompt template, tool definition, agent card, script, any executable shape |
| the node **routes differently** because the host has a feature | the node **executes** something the host supplied |

`describe_host()` coerces every field into a bool, a bounded int, or an enum
member, and **raises** on an artifact shape — a suspicious key name, a value
long enough to be a payload, a nested structure, a shebang, a code fence, a data
URL. Refusals are named, never dropped quietly. Unrecognised-but-harmless keys
are reported in `unrecognised_fields` so the caller can see what was *not*
understood instead of believing everything was.

Every descriptor carries `evidence: "declared"`. The host **says** it has a 1M
context; nothing here measured it. And these descriptors never reach the gate —
routing consumes them, the tier ladder and the councils do not, so a lying host
cannot widen its own permissions by lying here.

This is the no-skill-marketplace fence expressed in code rather than in prose.

---

## Running it

```
pip install -e packages/intentops-core
pip install -e packages/intentops-saddle-mcp
python -m intentops_saddle_mcp.server --mint-token     # once, per node
python -m intentops_saddle_mcp.server --selftest       # prove the refusals fire
python -m intentops_saddle_mcp.server                  # stdio server
```

Transport is newline-delimited JSON-RPC on stdin/stdout. **Nothing but protocol
frames ever goes to stdout**; diagnostics go to stderr. Protocol revision
`2025-06-18`: `initialize` with capability negotiation, `tools/list`,
`tools/call`, `ping`, and `notifications/initialized`. No resources, prompts,
sampling, roots, or completion — each would be a surface with its own blast
radius, and this server declares only what it can defend.

Two roots, never conflated: `INTENTOPS_NODE_ROOT` (runtime state, trust
material, ledger) and `INTENTOPS_REPO_ROOT` (the framework checkout carrying
`config/` and `genesis/imprint/`). Where the repo root cannot be found, the
allowlist cannot be read and **every call is refused**; `imprint_verify` returns
`UNPROBEABLE` and stays in the population rather than passing by absence.

---

## Blind spots

- A bearer token over stdio is two processes sharing a pipe on one machine. Over
  any network transport the channel **must** be encrypted by a layer beneath
  this one, and nothing here can verify that it is.
- Nothing here rate-limits. 256 bits of entropy is the whole defence against
  guessing; a network-facing deployment owes a limiter of its own.
- `--selftest` proves the refusal paths *can* fire. It cannot prove a host
  honours a refusal, which is the S2 gap and is not observable from here.
- The ledger records what this server **decided**. A host that asks and then
  ignores the answer leaves no trace in it.
