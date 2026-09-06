# intentops-gateway

The governed tool gateway: **one JSON-RPC seam every tool call passes through
before it reaches a backend.**

Two transports, one surface — JSON-RPC over HTTP (loopback by default) and the
same tools over MCP stdio. Both call the same dispatch, so a tool that exists on
one exists on the other by construction rather than by discipline.

## The three controls, and none of them has an off switch

| Control | Behaviour | Why it is written this way |
|---|---|---|
| **Authentication** | Every request carries this node's bearer token or it is **401**. | The reference implementation resolved an absent identity header to the literal string `anonymous` *by construction*, and shipped enforcement behind a flag nobody flipped. There is no anonymous identity here, no localhost bypass, and no environment variable that opens one. |
| **Backend allowlist** | Default-deny, per harness, from `config/gateway-policy.yaml`. An unreadable policy refuses **everything**. | Same incident, second control. There is deliberately no value meaning "all" — if one existed somebody would reach for it while debugging and never take it back out. |
| **Untagged tool → T4/deny** | A tool with no declared tier classifies **T4** and is **BLOCKED**. | Upstream, an unclassified tool rode its backend's default tier and was auto-approved. A tool nobody classified is a tool nobody bounded. |

A `BLOCK`, not an `ASK`, is the deliberate half of the third row: an ASK would
put an operator in front of an operation *nobody can describe*. The remedy is to
classify the tool in the estate manifest — a reviewed act — after which the call
goes through the ordinary ladder.

**A declared tier is the floor, never the answer**, because name-based tiering
is blind to what a call carries in its arguments. The core's reaches-reality
classifier reads the call as a second, independent opinion and the strictest
wins — it can only ever raise a tier, never lower a declared one. Its operating
point is narrow and worth stating exactly, because "it reads the arguments"
promises more than it delivers:

- a non-empty `command` field, on **any** tool name — money, irreversible
  delete, remote push, prod change, mail dispatched from a shell. (Until
  2026-09-06 this fired only for tools literally named `Bash`, `PowerShell` or
  `Shell`, so a backend tool called `shell` carried a force-push at its
  declared tier.)
- a `file_path` naming a credential or key file, on the file-write tool names;
- the outbound-communication and third-party-disclosure branches, which key on
  the `mcp__` prefix — backend calls reach them because the gateway hands the
  classifier the qualified address `mcp__<backend>__<tool>`.

Anything outside those three is invisible to this reading, and the declared
tier is then the only bound. That is exactly why the untagged-tool row above is
a refusal rather than a default.

## What it serves at birth

A newborn node has an empty capability population, so the gateway has **no
backends** and serves exactly four built-in reads:

```
intentops.boot       the read-only boot image: imprint, rules, aliveness, calibration
intentops.status     what this gateway is, what it could read, what it cannot do
intentops.backends   the declared backends and which of them THIS caller may reach
intentops.classify   the verdict for a described call, without running it
```

Backend tools are addressed `<backend>__<tool>`. Whether a permitted call is
then *executed* is a declared seam (`BackendInvoker`); the shipped
implementation is `NullInvoker`, which refuses and says so. The gateway core
decides **whether** a call may happen; making it happen is a separate, reviewed
wiring decision, and a caller never receives a fabricated success.

## Commands

```
intentops-gateway status              # what it would be, without starting it
intentops-gateway token rotate --yes  # mint the bearer token, printed ONCE to stdout
intentops-gateway serve               # HTTP, loopback, port from config/ports.yaml
intentops-gateway mcp                 # the same tools over MCP stdio
intentops-gateway selftest            # prove the refusal paths can actually fire
```

The token is printed to **stdout and nowhere else**; every explanation goes to
stderr. Only its SHA-256 digest is stored, under `.intentops/trust/`, which is
never in a repository. A lost token can be rotated, never recovered.

## Layering

`intentops_gateway` → `intentops_saddle_mcp` → `intentops_core`. The arrow never
points back: `tests/test_dependency_direction.py` and `tests/test_gateway.py`
both check it rather than trusting it. The MCP wire contract (protocol revision,
JSON-RPC error codes, session shape, the `_meta.authorization` token location) is
**imported** from the saddle, never copied — a second copy of a wire contract is
a second thing to drift.

This is why the CLI is `intentops-gateway` and not a subcommand of `intentops`:
welding it into the core's parser would invert the arrow, and doing it by a lazy
import would invert it *invisibly*, since the direction check reads import
statements and says so in its own blind-spot list.

## What it cannot do

The gateway computes and records verdicts. **It cannot refuse a call a harness
never routes through it**, and it has no way to observe one. That is reported by
`intentops.status` as `S2: harness-honoured=unknown` rather than omitted. Closing
that gap is a harness adapter's job, not this package's.
