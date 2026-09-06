# Security

IntentOps is a governance and action-gating framework. A defect here can mean an
action that should have been refused was allowed, or that trust material was handled
incorrectly — both are treated as security issues, not ordinary bugs.

## Reporting a vulnerability

**Private disclosure path: placeholder.** This repository is pre-release and has not
yet flipped public; a maintained private-disclosure address (or a GitHub Security
Advisory flow) will be published here before the public flip, as part of the same gate
that requires a `LICENSE` file to exist first (see `LICENSE-PENDING.md`). Until then,
do not open a public issue for a suspected vulnerability — hold it for the disclosure
channel once it exists.

Please include, where you can: the affected module, the tier or gate you believe is
bypassed, reproduction steps that do not require any private or third-party data, and
your assessment of impact (does the defect allow an action to bypass the gate, or only
mis-report one).

## Known, tracked fail-open defects

Two defects are tracked here deliberately, rather than fixed silently and forgotten,
because a fail-open in a trust-verification path is exactly the kind of thing that
must never be quietly patched without a record that it existed:

1. **An unsigned invariant bundle is treated as valid.** The bundle-verification path
   currently returns a pass ("valid for dev/test") when a bundle carries no signature
   at all, rather than refusing it. An unsigned bundle and a correctly-signed one are
   indistinguishable to the caller today.
2. **A crypto-unavailable environment is treated as valid.** The same verification
   path degrades to a pass when the cryptography library or a required primitive is
   unavailable at runtime, rather than halting. A host that cannot verify anything
   currently reports the same result as a host that verified successfully.

Both must close — refuse (never pass) on an unsigned bundle, and halt (never pass) on
a crypto-unavailable host — before this framework ships genesis to anyone outside this
seed's own authors. Tracking them here, before the fix, is itself the point: a fixed
defect with no record it ever existed teaches nothing about why the fix matters.

## Trust-material scanning

This repository is scanned before every push for private-key-shaped content —
PEM-format private keys, raw Ed25519 seed material, and any file matching a
private-key extension or header — as a zero-tolerance check, not a warning. A hit
blocks the push; it does not get triaged as "probably a test fixture." See
`docs/GENESIS.md` for where key material is generated at genesis and why it is
designed to never enter a repository in the first place (a node's private key is
minted locally, at genesis time, into local secure storage the operator chooses — a
hardware token, a TPM, the OS credential store, or a passphrase-wrapped file — and is
never written to any file this scanner would need to catch).

## No private keys, ever

No private key — a node's, an operator's, or a root's — may appear in this repository
in any form, including as a "temporary" or "example" value, an environment-variable
default, a fixture, or test data. A key that looks fake is still a defect if the
scanner cannot tell the difference; use an explicitly and obviously invalid value
(wrong length, wrong encoding) in any test that needs a key-shaped input, and say so in
a comment.

## Supported versions

Pre-release: there is one line of development, and it is the only one that receives
fixes. A versioned support policy will be published once a first tagged release
exists.
