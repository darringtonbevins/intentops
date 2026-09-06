# GENESIS — root fingerprint (human-readable carrier)

This file is **carrier #3 of 3** for the IntentOps root trust fingerprint (design:
`docs/reports/intentops-oss/design/genesis-design.md` sect. 11.2). The other two
carriers are `config/trust-roots.yaml` (the full signed record — public key, DER-SPKI
fingerprint, `did:key`, optional certificate, all four required to agree) and
`src/genesis/trust_pin.py` (the compiled constant a running node checks against).
Three carriers make a *partial* tamper visible; they do not make a *total
substitution* of this whole repository visible. Compare this file's fingerprint
against the project's published site, the signed release tag, and the release
announcement out of band before trusting a first clone — that comparison is what
closes the gap three in-repo carriers cannot close alone.

## Canonical form

```
intentops-root:v1:sha256:PLACEHOLDER-NOT-YET-MINTED-0000000000000000000000000000000000000000000000000000000000:did:key:zPLACEHOLDER-NOT-YET-MINTED
```

**This fingerprint is a PLACEHOLDER.** No root ceremony has been performed for this
public repository. The real fingerprint is minted once, offline, by whoever holds the
root signing authority (decision: `docs/reports/intentops-oss/design/genesis-design.md`
sect. 12, question Q1 — "who holds the root, on what medium, with what rotation and
revocation path" — is not this seed's to answer). Until that ceremony happens and this
file is updated with the real value in the same commit as `config/trust-roots.yaml`,
**genesis's provenance check (`G1`) must treat any node built from this seed as
unverified**, and every boot banner must say so plainly.

## Two disjoint trust domains

IntentOps deliberately separates two questions that are easy to conflate:

| | Provenance (global) | Authority (local, per node) |
|---|---|---|
| Question it answers | Is this artifact the one IntentOps actually published? | Who may authorize an action on *this* node? |
| Root | The root fingerprint above | The operator root, minted fresh at each node's genesis (`G2`) |
| A valid signature buys | Authenticity of bytes | Nothing outside that one node |

The release root never signs an operator root, and there is no cross-certification
between the two domains. A publisher key that could mint authority on every installed
node is one compromised key away from a fleet-wide takeover; keeping the domains
disjoint is what prevents that, not a policy asking people not to do it.

## Verification, fail-closed

Full detail: `config/trust-roots.yaml` and the genesis provenance step (`G1`) described
in `docs/GENESIS.md`. In outline: an absent or unparseable roots file, a fingerprint
mismatch across its four representations, a self-signature that does not verify, or a
revoked/rolled-back sequence number are all **halts**, never warnings. An expired
validity window is a **refusal**, not a halt, because a clock can be legitimately wrong
in ways a forged signature cannot be.

## The override, and its cost

A node may boot against an unverified fingerprint only via an explicit, expensive
override — typed at an interactive terminal, never from an automated context — that
tags the node as unverified in every boot banner until the ceremony above is
completed and the real fingerprint is minted. See `docs/GENESIS.md` for the full
mechanism and its costs. Whether this override exists at all in a shipped release is
itself an open, operator-level question (`genesis-design.md` sect. 12, question Q5).
