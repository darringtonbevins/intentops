<!-- origin: .claude/rules/no-skill-marketplace.md (upstream v7.0.0, as of 2026-09-06) -->
<!-- generalized 2026-09-06 for genesis/imprint: the ruling attributed to "the operator", estate report paths and registry file names made generic; the measured incident, its numbers and every rule kept -->

# No Skill Marketplace -- a stated non-goal, with a body count

A node has no marketplace for skills, plugins, or agents, and **will not
acquire one without an explicit ruling from its operator.** Skills are authored
in-repo under review, with the node's own curated registry as the source of
record.

Upstream, that was for a while an accident of what had not been built. This
file makes it a decision, because the pressure to add one will arrive -- a
skill marketplace is the obvious next feature for a personal AI harness, every
comparable system has one, and "we could just install that skill" is a
persuasive sentence.

## Why: the failure mode is measured, not imagined

[OBSERVED, absorbed from public incident reporting]

- Threat actors registered as marketplace developers on a large agent-skill hub
  and mass-uploaded trojanized skills disguised as crypto bots, productivity
  tools and social utilities.
- An independent security vendor disclosed it; a national CERT classified the
  family and within four days counted **1,184 malicious packages across 12
  publisher accounts -- one uploader responsible for 677**.
- The hub held roughly 10,700 cumulative skills; about 3,498 survived the
  cleanup.

**The enabling condition was not the marketplace alone.** It was permissionless
publishing into a marketplace **whose artifacts load into the agent's context
at session start**. That last clause is the whole thing: a skill file is not a
library you call, it is instructions that enter the reasoning loop before any
gate has an opinion. The platform's response -- provenance warnings, capability
consent, a force flag for arbitrary executables -- is sound and arrived after.

## The rules

1. **No marketplace, registry, or one-command install of third-party skills,
   plugins, or agent definitions.** Adding one is a ruling for the operator,
   not a design decision, and this file is the thing to cite when proposing it.
2. **A skill enters this node the way code does**: authored or reviewed in
   repo, in a commit, with an author who can be asked why. "Downloaded" is not
   a provenance.
3. **This does not forbid LEARNING from external skill ecosystems.** Reading
   them, absorbing their patterns, and porting an idea by hand are all
   encouraged. The fence is on *executing someone else's artifact*, not on
   studying it.
4. **The same test applies to anything that loads into context
   automatically** -- tool-server definitions, agent files, rule files, prompt
   templates fetched from outside. Ask: does this become instructions in a loop
   before a gate sees it? If yes, it needs in-repo provenance.
5. **If a marketplace is ever ruled in**, the minimum from the incident's
   post-mortem design: capability declaration and consent before activation,
   source and version shown at install, no arbitrary-executable sources without
   an explicit override, and an allowlist of trusted publishers. Observe-first,
   never on-by-default.

## Boundary

This is a fence on **third-party skill artifacts**, not on the node's own
skills, not on governed tool *calls* through its own gateway (those are gated
per call), and not on absorbing external knowledge into a corpus (that is a
different control with its own failure modes).

> Descriptive governance. It gates nothing at runtime -- its enforcement is
> that a proposal to add a marketplace has to argue with this file first.
