# Build record — seed wave 4 (2026-09-06)

What wave 4 built, what three independent verifiers said about it **verbatim**,
what was applied, and what is still open. This is the evidence behind the
wave-4 entries in [`CHANGELOG.md`](../CHANGELOG.md); the changelog is the
summary, this is the receipt.

Two of wave 4's five build legs returned nothing (`backend-transport` and
`s2-mcp-client-falsifier`, both reported *(lost)*). Under
`dynamic-workflows.md` rule 4 a leg that returns no executed evidence is a
FAILED leg, so neither was graded as shipped; both were **re-built by the
corrector** to the specification the verifiers wrote, and both are recorded
below as corrector work rather than as leg output.

**Environment for every measurement in this document.** `C:/Workspace/intentops`,
working tree on `HEAD e43b46e` plus the wave-3 and wave-4 uncommitted changes.
CPython **3.11.15** (the project venv interpreter), Windows-10-10.0.26200-SP0.
Every run used
`PYTHONPATH="packages/intentops-core;packages/intentops-gateway;packages/intentops-saddle-mcp"`
— note the **semicolon** separator: the colon form quoted in the wave brief is a
POSIX separator and Windows CPython does not split on it, so a run using it
silently resolved only the packages already installed into the venv and
`intentops_gateway` failed to import. Nothing was committed, added or pushed.

---

## 1. What wave 4 built

| Organ | Path | State |
|---|---|---|
| Gateway bearer primitive + genesis token ceremony | `intentops_core/trust/bearer.py`, `intentops_core/genesis/token_ceremony.py` | shipped by its leg, corrected here |
| Local inference seam (provider contract + one HTTP adapter) | `intentops_core/inference/` | shipped by its leg, **defect found and corrected here** |
| Metabolism stage runner | `intentops_core/metabolism/runner.py` | shipped by its leg, corrected here |
| Selftest registry + `verify --all-selftests` | `intentops_core/selftests/registry.py` | shipped by its leg, confirmed |
| **Stdio backend transport** | `intentops_gateway/transport.py` | **built by the corrector** (leg lost) |
| **Reference MCP client + S2 falsifier record** | `intentops_saddle_mcp/reference_client.py`, `docs/falsifiers/S2-mcp-client-deny-2026-09-06.md` | **built by the corrector** (leg lost) |

New probe rows: `GEN-backend-transport`, `GEN-mcp-reference-client`
(`config/genesis-probes.yaml`), each with a matching section in
`docs/BOOT-ANCHORS.md` so the fact reaches a cold window. `DECLARED_BIRTH_ENTRIES`
is **unchanged at 25**: neither new organ instantiates an artifact in a newborn
node, and inventing a birth entry for a code module would make the count mean
two different things.

---

## 2. Verifier verdicts, verbatim

### Verdict 1 — backend transport

> **claim:** "The stdio backend transport never spawns a T3/T4-classified call,
> refuses unlisted backends, scrubs the environment to the allowlist, enforces
> timeout and output cap by refusal, and honours the kill switch at dispatch
> time."
>
> **refuted:** `false` — **cannotEvaluate:** `true`
>
> *"The artifact the claim describes does not exist. … the digest itself marks
> the `backend-transport` leg `(lost)`. So there is no invoker to drive with a
> canary env var, oversize output, sleeping backend, or T3 call, and the claim
> is UNSUPPORTED as a completion claim (dynamic-workflows.md rule 4: a leg that
> returns nothing executed is a FAILED leg). refuted=false because no defect was
> FOUND in shipped code; cannotEvaluate=true because there is nothing to run. …
> That ordering means the 'never spawns a T3/T4 call' property is satisfied
> vacuously by the seam, not by any transport. The remaining four properties …
> have no carrier at all."*

**Applied — all five corrections.**

1. *"Re-dispatch the backend-transport BUILD leg; do NOT grade this claim as
   shipped."* — **applied.** It is not graded as shipped anywhere; it is
   recorded above as corrector work, and the corrector built it rather than
   re-dispatching, being the only corrector this wave.
2. *"implement `StdioInvoker` … in a NEW module … wire it only via the
   `invoker=` parameter … so `NullInvoker` stays the default"* — **applied.**
   `transport.py` is new; `tools.py` is **unmodified**;
   `test_the_default_invoker_is_still_the_null_one` asserts the default.
3. *"re-check, at invoke time, what `_dispatch_backend` already checked … env
   … from an explicit allowlist (never `os.environ` copy); timeout and byte cap
   … by killing the child and raising a refusal that names the cap"* —
   **applied**, all five clauses. `TransportRefused` subclasses `NotWired`, so
   the dispatcher renders it with `executed: False` through the existing seam.
4. *"Ship tests/test_gateway_transport.py … Add `--selftest` to transport.py and
   a GEN probe row"* — **applied**: 20 tests (canary, oversize, timeout, T3 with
   a `Popen` spy, unlisted backend, kill switch, plus a permitted-call control
   and three broken-backend shapes), `--selftest` firing 10 paths, probe row
   `GEN-backend-transport`.
5. *"Until that lands … do not add a CHANGELOG Unreleased entry for a stdio
   transport."* — **satisfied by landing it.** The changelog entry describes the
   transport that now exists and tests that now run.

**Deviation, stated:** correction 3 asked for a kill-switch sentinel "under the
node root". The transport uses its OWN sentinel
(`.intentops/gateway/transport-disabled`) rather than the node-wide
`.intentops/halt.marker`, so that switching off *the transport* is not the same
act as standing down *the node*. The node-wide marker is honoured upstream by
`load_context`, unchanged.

### Verdict 2 — token disclosure, inference adapter, metabolism runner

> **claim:** "The gateway token is never minted from a non-TTY or in dry-run,
> its plaintext is printed once and only a digest is stored; the inference
> adapter refuses non-local endpoints by default, never calls a network in
> tests, and the metabolism runner cannot render OK on empty output."
>
> **refuted:** `true`
>
> *"TOKEN CLAUSE HOLDS. … Plaintext leaves only via out()/print … record carries
> sha256 only … Doc/behaviour mismatch, not an open path: token_ceremony.py:24-29
> promises the refusal is 'recorded rather than raised', but machine's _gate …
> converts EOF into OperatorGateRequired, which mint_disclosed … does not catch,
> so G2 HALTs."*
>
> *"INFERENCE CLAUSE REFUTED (default-open transport). classify_endpoint fences
> the DECLARED URL only; `_default_opener` … is bare urllib.request.urlopen,
> which installs ProxyHandler from HTTP_PROXY/http_proxy and follows 301/302/303.
> Probe 1 [OBSERVED]: HTTP_PROXY set -> the full POST body (the prompt) for a
> declared 127.0.0.1 pool was delivered to the proxy, the declared server
> received nothing, and the proxy's body was returned as a Completion attributed
> to pool 'p'. Probe 2 [OBSERVED]: the loopback endpoint answered 302 -> the
> adapter issued GET /elsewhere to a second host and returned that host's body as
> the completion … Probe 3 [OBSERVED]: 302 to a public name -> getaddrinfo lookup
> (off-host contact) … Also: urlopen caches its global opener at first call, so a
> proxy present then persists for the process."*
>
> *"RUNNER EMPTY->OK HOLDS … Secondary defect: runner.py:1177 declares --stage
> required=True, so `python -m intentops_core.metabolism.runner --selftest` and
> `intentops metabolism run --selftest` … both exit 2 with argparse usage."*

**Applied — all six corrections.**

1. Replace `_default_opener` with a module-level opener that neither proxies nor
   redirects — **applied**, as specified: `_OPENER = build_opener(ProxyHandler({}),
   _RefuseRedirect())`, built once, `urlopen` not used. The refusal is raised as
   `_RedirectRefused` (a `URLError` subclass) rather than an `HTTPError` so the
   adapter can report *"was not served by"* instead of a status the endpoint
   never sent.
2. Assert the served URL's host after the call — **applied and widened.** The
   check compares the served **origin (host and port)**, not the hostname alone,
   because every loopback endpoint on a machine shares one hostname and the
   hostname-only form cannot distinguish two local services; the declared-host
   comparison the correction asked for is retained inside it.
3. Two tests with two in-process servers (proxy, redirect) — **applied**, plus a
   third for a caller-supplied following opener. They live in a new file,
   `tests/test_inference_transport_fence.py`, rather than appended to
   `tests/test_inference.py`; same coverage, no edit to another leg's file.
4. Docstring corrections in `provider.py` and `openai_compat.py` — **applied**.
   Both now say the fence classifies the declared URL only, and that "no egress"
   is a property of the fence AND the transport together.
5. `--stage` no longer `required=True`, re-imposed after the selftest branch,
   with tests for `main(['--selftest']) == 0` and `main([]) == 2` — **applied**,
   plus a third test driving `intentops metabolism run --selftest` end to end.
6. Align the ceremony docstring OR wrap the ask — **applied: the wrap**, because
   `g2_keys`'s own comment already states the decline-not-halt intent, so
   aligning the docstring would have moved the code further from two documents
   instead of one. `_ask_about_the_token` is a module-level (therefore testable)
   seam that re-raises `OperatorGateRequired` as `EOFError`, so "declined" and
   "could not be asked" stay two different recorded phrases. Three tests,
   including a positive control proving the raw exception really does escape
   `mint_disclosed`.

### Verdict 3 — selftest registry and the S2 record

> **claim:** "intentops verify --all-selftests discovers and runs every
> instrument (none dropped; errors stay as ERROR), its own selftest proves a
> FAIL is reported, and the S2 record claims exactly what was proven (a
> reference client honouring a deny) and nothing about third-party hosts; the
> full suite passes and all gates read CLEAN."
>
> **refuted:** `true`
>
> *"SELFTEST HALF — CONFIRMED. … 70 instruments (58 modules + 12 scripts), 0
> discovery findings. … `run_instrument` … returns ERROR on missing frame/OSError
> and never coerces to FAIL. … Full suite: 1515 passed, 77 skipped, 0 failed …
> (the "1 FAILED" the build leg saw does not reproduce)."*
>
> *"S2 HALF — REFUTED: the record does not exist. … no S2 falsifier record, no
> reference MCP client module … and no client test. … There is NO over-claim
> anywhere … but the claim under test asserts a record that was never produced,
> so the S2 clause is false on disk."*
>
> *"Minor: CHANGELOG.md:33-37 Unreleased header still carries the wave-3
> measurement (1,327 passed) above wave-4 entries; the tree now measures 1,515."*

**Applied.**

- **S2 deliverable** — **applied.** `reference_client.py` (a client that asks
  `intentops.gate` and performs its effect only on an explicit `ALLOW`), 10 tests
  in `tests/test_mcp_reference_client.py`, `--selftest` firing 9 paths, and the
  record `docs/falsifiers/S2-mcp-client-deny-2026-09-06.md` stating environment,
  interpreter, working directory, harness-variable handling, subject, transcript,
  blind spots and **VERDICT: PASS**. `S2` is registered in
  `tests/test_falsifier_records.py::REQUIRED_RECORDS` beside F1 and F3.
- **The claim is held to its size.** `config/saddles.yaml` is **unchanged**:
  `mcp-hosted` stays `grade: candidate` with `S2: computed_not_enforced` and its
  `open_falsifier` intact, and a test
  (`test_the_saddle_row_still_grades_s2_as_computed_not_enforced`) makes a future
  promotion on the strength of our own client a build failure.
- **The stale CHANGELOG measurement** — **applied.** The Unreleased paragraph now
  carries the wave-4 numbers and says plainly what it replaced and why that shape
  was wrong.

**The corrector's own note on the verifier's correction text.** Verdict 3's
correction list is **truncated mid-sentence** in the brief this corrector
received (`"(a) docs/falsifiers/S2-mcp-client-deny-2026-09-06.md stating
environment, pinned com…"`). The visible portion was applied in full and the
truncated clause was read as *"pinned commit"*, which the record carries. Any
further clause of that correction is unread and therefore unapplied — stated
here rather than left to look complete.

---

## 3. Test totals

| Suite | Result |
|---|---|
| Full suite, after every correction | **1,558 passed, 78 skipped, 0 failed** (77.65s) |
| Verifier's pre-correction baseline | 1,515 passed, 77 skipped |
| `tests/test_gateway_transport.py` (new) | 20 passed |
| `tests/test_mcp_reference_client.py` (new) | 10 passed |
| `tests/test_inference_transport_fence.py` (new) | 3 passed |
| `tests/test_metabolism_runner.py` (3 added) | 67 passed |
| `tests/test_gateway_token_disclosure.py` (3 added) | 39 passed within the file's suite |
| `tests/test_falsifier_records.py` (S2 registered) | 9 passed, 3 skipped |

---

## 4. Gate verdicts

```
exposure_gate.py            VERDICT: CLEAN   296 files scanned, 1 skipped, 0 UNSCANNED,
                                             82,572 lines; 21 name digests, 4 shapes
exposure_gate.py --history  VERDICT: CLEAN   11 commits; 22/22 identities allowlisted
trust_material_check.py     VERDICT: CLEAN   0 findings / 312 scanned (1 unscanned: an
                                             .ots binary, reported not skipped silently)
build_manifest.py --check   OK: 15/15 bundle artifacts match the manifest
probe_coverage_check.py     37/37 satisfied, 1 exempt with a reason, 1 outside the
                            population -- every member owing a probe has one
claims_check.py             VERDICT: CLEAN   144 path claims across 5 documents
tests/test_estate_manifests.py                31 passed
verify --all-selftests      72/72 PASS, 0 FAIL, 0 ERROR, 0 TIMEOUT,
                            0 discovery findings, 25.4s   (70 before this wave)
self_probe over the shipped suite             47/47 PASS, 0 ABSENT
genesis --dry-run (temp node, unsigned-dev)   G0 PASS · G1 WARN · G2-G7 PASS,
                            final state G7; "birth probe suite: probes 47/47 PASS;
                            0 ABSENT (packaging); 0 truth-failures"
```

The genesis `G1 WARN` and the `PROVENANCE: UNVERIFIED` banner are the
unsigned-development posture, unchanged by this wave and correct: no root
ceremony has run for this build, and the run says so rather than proceeding
quietly.

---

## 5. Remaining issues

1. **The estate has no shipped stdio backend and no command table.** The
   transport exists and is tested; nothing in this repository declares a
   backend command for it, and `NullInvoker` is still what an unmodified node
   gets. That is deliberate — wiring one is an operator act — but it means the
   transport has never been exercised against a real MCP or CLI backend, only
   against a fake written by its own tests.
2. **S2 remains open for every client except ours.** The `mcp-hosted` grade is
   `candidate` and its `open_falsifier` still asks for a third-party client. The
   new record narrows the question; it does not answer it.
3. **`BLOCK` is unreachable from the shipped classifier.**
   `intentops_core.gate.verdict.block()` exists and `gate/classify.py` never
   calls it, so no run of the S2 falsifier can exercise that decision. The
   client treats every non-`ALLOW` decision identically, so the *behaviour* is
   covered and the *decision* is not. Recorded as a blind spot in the record
   rather than fixed, because inventing a BLOCK path to make a test greener is
   the wrong direction.
4. **The inference fence still cannot see below the URL.** An OS-level
   redirection of a loopback literal (a hosts file, a firewall rule) is
   invisible to both the fence and the transport, and TLS trust is whatever the
   interpreter's default context does. Both are stated in the module docstrings.
5. **A caller-supplied `opener=` takes the proxy and redirect refusals back into
   its own hands.** The served-origin check is the belt-and-braces and is
   tested, but a caller that also stubs the response object's `url`/`geturl`
   defeats it. Every test in this repository supplies an opener, so this is not
   a hypothetical shape.
6. **`prompt_cache_manager` and `intentops-gateway`'s older seam documentation
   were not revisited.** Out of scope for these verdicts; noted so the next
   wave does not read this record as a full-surface review.
7. **Nothing in this wave was committed.** The tree carries wave-3 and wave-4
   changes together; a release cut still owes a clean-export re-run of F1 and
   F3, which this wave did not perform.
