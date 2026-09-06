<!-- origin: .claude/rules/minimize-llm-dependency.md (upstream v7.0.0, as of 2026-09-06) -->
<!-- generalized 2026-09-06 for genesis/imprint: the invariant attributed to "the operator of node zero", specific ports and the estate-specific worked example replaced with a generic one; every rung and its reason kept -->

# Minimize LLM Dependency (invariant)

An invariant pattern for ALL architecture, planning, and runtime design:
**always minimize LLM dependency**. Prefer deterministic solutions; minimize
dependency on frontier models.

This is also the rule that keeps a node **affordable enough to keep running**,
which is the rule that keeps it alive.

## The choice ladder (bias LEFT)

```
Code  ->  Script  ->  Local embeddings  ->  Local inference  ->  Service inference
```

1. **Code** -- deterministic logic compiled into the system (reducers, state
   machines, validators, tallies, math). Same input -> same output. Zero
   marginal cost, zero drift.
2. **Script** -- deterministic automation (shell, Python, SQL, scheduled
   tasks). Auditable, replayable.
3. **Local embeddings** -- semantic lookup and classification via local
   vectors. No generation; bounded, cheap, private.
4. **Local inference** -- locally hosted models for generation or judgment that
   genuinely needs a model. Private, fixed cost, bounded quality.
5. **Service inference** -- frontier or hosted models. LAST resort at runtime:
   reserved for open-ended reasoning, synthesis, and intent understanding that
   nothing to the left can do.

## Rule

- For every capability decision, evaluate the viable rungs for
  **risk / cost / result** and pick the best ratio -- with an explicit bias to
  the left. Moving RIGHT requires a stated reason ("code cannot express this
  because ...") in the design document or decision record.
- Architecture reviews and plans must tag each component with its ladder rung.
- Runtime paths that fire frequently (ticks, renders, replication, validation,
  telemetry) must sit at Code or Script. Model calls belong at the INTENT
  boundary (deciding what to do), never in the render or replication loop.
- This composes with GIDE: the deterministic half is the execution substrate;
  the model is one organ, not the system.

## Bounded tool surfaces are a governance control, not a convenience

The same left-bias applies to what we hand an AGENT: an agent given a shell can
do essentially anything; an agent given a bounded, self-describing endpoint
surface (stateless typed tools, typed APIs) can only do what those endpoints
expose. Prefer the confined surface over raw shell or CLI access whenever the
capability can be expressed as endpoints -- the confinement is a SAFETY property
(enumerable, auditable, tier-classifiable per call), not merely tidier
engineering. This is the transport-layer expression of the T0-T4 model: "every
request judged on its own" beats "a session, once opened, is trusted."

Boundary: this guides SURFACE design; it does not retire the shell for the
node's own governed sessions, where the tool-call gates are the confinement.

## Worked example -- a live visual interface

- Scene protocol, registry validation, revision ordering, journal replay:
  **Code**.
- Physics, camera direction, affect mapping (state -> shader parameters):
  **Code** (a deterministic engine plus parameter maps -- no model in the loop).
- Choreography, projections, attention-governor thresholds: **Code/Script**.
- Semantic recall for "what should the screen show about X":
  **Local embeddings** first.
- Narrative or summary panel content, and voice conversation: **Local
  inference** where quality suffices; service inference only for the intent and
  synthesis boundary.
