# Falsifier S2 -- does a client HONOUR a deny this server returns?

**Question.** `config/saddles.yaml` grades the `mcp-hosted` saddle's S2 as
`computed_not_enforced`, and `intentops.status` says the same thing in the
server's own words: *"This gateway computes and records verdicts. It cannot
refuse a call a harness never routes through it, and it has no way to observe
one."* The open falsifier attached to that row asks for the one thing that was
missing: a fenced, live run in which some client asks the gate, is told no, and
stops.

**Why it is gated.** Until this run the S2 claim had no data point at all on
either side. A grade of `computed_not_enforced` was honest, and it was also
untested -- nobody had demonstrated that a client CAN honour the verdict, so
the gap between "computed" and "enforced" was a sentence rather than a
measurement.

**Scope, stated before the verdict so it cannot be read past.** This falsifier
covers **one client: the reference client in this repository**
(`packages/intentops-saddle-mcp/intentops_saddle_mcp/reference_client.py`). It
says nothing whatever about any **third-party** MCP host. A passing run belongs
on this client's row, about this client, and the `mcp-hosted` grade stays
**candidate** because of it -- see "What this does NOT establish" below, and
`tests/test_mcp_reference_client.py::test_the_saddle_row_still_grades_s2_as_computed_not_enforced`,
which turns a future over-claim into a build failure.

---

## VERDICT: PASS

**In one sentence:** a client that routes its proposed actions through
`intentops.gate` receives a real refusal for a destructive action and does not
perform it, while the same client performs a permitted action -- so the
refusal is a decision and not an inability to act.

---

## Environment

- **Subject:** `intentops_saddle_mcp.reference_client.ReferenceClient` talking
  to `intentops_saddle_mcp.server` over MCP stdio (newline-delimited JSON-RPC,
  protocol version `2025-06-18`), spawned as a real subprocess with real pipes.
- **Repository state:** `C:/Workspace/intentops`, wave-4 working tree on
  `HEAD e43b46e` plus this wave's uncommitted changes.
- **Working directory:** the repository root; the server subprocess inherits it
  and resolves its node root from `INTENTOPS_NODE_ROOT` rather than from `cwd`.
- **Interpreter:** CPython **3.11.15** (Python 3.11), the project venv
  interpreter, on Windows-10-10.0.26200-SP0. The server is launched as
  `sys.executable -m intentops_saddle_mcp.server` -- this interpreter, never a
  `python` found on `PATH`.
- **Harness variables:** none are read by the subject. The run sets exactly
  three for the child -- `INTENTOPS_NODE_ROOT`, `INTENTOPS_REPO_ROOT` and
  `PYTHONPATH` -- all pointing into a temporary directory created for the run
  and deleted after it. No harness hook, no session state, no host
  configuration participates; the client presents only a name and a bearer
  token.
- **Node:** minted fresh in a temp directory (`.intentops/` only). Its
  `config/saddles.yaml` fixture admits exactly one client name,
  `intentops-reference-client`, and nothing else.
- **Network:** none. Two processes and a pipe.

## What was run

`tests/test_mcp_reference_client.py` (10 tests) and
`python -m intentops_saddle_mcp.reference_client --selftest`
(9 paths fired, 0 failed). The transcript below is a direct run of the same
sequence.

## Transcript

```
serverInfo  {"name": "intentops-saddle-mcp", "title": "IntentOps hosted saddle",
             "version": "0.1.0"}

act("Bash", {"command": "rm -rf /"})
  -> decision ASK   tier T4  reasons ["recursive filesystem delete"]
     performed=False  honoured=True
     note: "the server answered ASK; the effect was not performed"

act("Read", {"file_path": "README.md"})
  -> decision ALLOW tier T0  reasons ["no reaches-reality signature"]
     performed=True   honoured=True

effects actually executed: ['ALLOWED-RAN']        <- the denied one is absent
```

A second probe, same session, over four more proposed actions:

```
Bash "git push origin main"  -> ASK   T3  ["push to a remote"]       performed=False
Bash "DROP TABLE clients"    -> ALLOW T0  ["no reaches-reality ..."] performed=True
Write / WebFetch / Edit      -> ALLOW T0  ["no reaches-reality ..."] performed=True
```

## Why the PASS is not vacuous

A client that never performs any effect honours every deny perfectly. Three
controls separate "honoured" from "inert":

1. **The permitted action really runs.** `Read` returned ALLOW and the effect
   executed (`ALLOWED-RAN` is in the list). The client is capable of acting.
2. **The denied effect is a callable that would have been observable.** It
   appends to the same list. Its absence is the evidence, not a message.
3. **Silence is not consent.** With the server replaced by a process that
   exits immediately, `act` returns `decision=UNREADABLE`, `performed=False`.
   An unreachable, malformed, erroring or decision-less answer all leave the
   effect uncalled -- there is no path in `act` that performs an effect
   without an explicit `ALLOW` in hand.
4. **A stood-down node stops it too.** With `.intentops/halt.marker` present,
   the run performs nothing (S5 outranks everything).

## What this does NOT establish

- **Nothing about any third-party MCP host.** No commercial or open-source MCP
  client was exercised. The `mcp-hosted` row therefore stays **candidate**, and
  its `open_falsifier` -- "whether any specific MCP client HONOURS a deny ...
  has not been run as a fenced, live test" -- remains open for every client
  except this one.
- **Nothing about enforcement.** The server still cannot refuse a call that is
  never routed to it. This run demonstrates a client that chooses to route;
  it does not give the server any power over one that does not.
- **Nothing about a sandbox.** The fence is a convention inside one class. A
  caller that performs its effect without calling `act` is outside every
  guarantee here, and the client has no way to stop it.

## Blind spots

- **One client, one shape.** One request at a time, one pipe, no concurrency,
  no reconnection, no partial frames.
- **The deny shapes reachable here are ALLOW / ASK / HALT.** `BLOCK` is a
  constructor in `intentops_core.gate.verdict` that the shipped classifier
  (`gate/classify.py`) never emits, so no run of this falsifier can exercise
  it. The client treats every non-`ALLOW` decision identically, so the
  behaviour is covered; the DECISION is not.
- **The verdicts are the gate's.** This client does not second-guess a tier. A
  wrong verdict would be honoured exactly as faithfully as a right one, and
  this record is not evidence that the classifications above are correct.
- **The client's name is authorisation, not identification.** The allowlist
  decides whether a name may connect; nothing proves the connecting process is
  the one that name refers to.
- **A temp-directory node is not a deployment.** Real hosts have real
  configuration, real concurrency and real users.

## Re-running it

```
cd C:/Workspace/intentops
PYTHONPATH="packages/intentops-core;packages/intentops-gateway;packages/intentops-saddle-mcp" \
  <venv>/python.exe -m pytest -q tests/test_mcp_reference_client.py
PYTHONPATH=... <venv>/python.exe -m intentops_saddle_mcp.reference_client --selftest
```

A re-run supersedes this record by adding a NEW dated file; this one is never
rewritten.
