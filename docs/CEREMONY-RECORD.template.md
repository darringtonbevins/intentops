# Release-root ceremony record (fill by hand, during the sitting)

Copy this file to a location OUTSIDE any repository before the sitting, fill it during the
sitting, and keep it with the private key's storage medium. Only the PUBLIC fields marked
(public) are later copied into `config/trust-roots.yaml`; nothing else leaves the record.

| Field | Value |
|---|---|
| Date and time (local, with zone) | |
| Location | |
| Operator (the person minting) | |
| Witness (a second person, present) | |
| Machine (make/model; freshly booted; no agent, assistant, screen recorder, clipboard manager or remote-access tool running) | |
| Network state (cable unplugged / radio off; how you checked) | |
| Storage medium for the private key (decided BEFORE the sitting) | |
| Passphrase custody (where the passphrase lives; never the passphrase itself) | |
| Tool version (git commit of this repository the tool ran from) | |
| Selftest result before minting (`mint_release_root.py --selftest`) | |

## Root: R-INTENTOPS (public fields)

| Field (public) | Value as printed by the tool |
|---|---|
| `key_id` | |
| `fingerprint` (sha256 over DER SPKI) | |
| `did` (did:key) | |
| authority string | |
| `valid_from` / `valid_until` | |

## Intermediate: I-INTENTOPS-REL-2026 (public fields)

| Field (public) | Value as printed by the tool |
|---|---|
| `key_id` | |
| `fingerprint` | |
| `did` | |
| `issued_by` | R-INTENTOPS |
| `signs` | trust-roots, revocation list, imprint_manifest, archetype catalogue, release tags |

## Signing

| Field | Value |
|---|---|
| `build_manifest.py --check` result BEFORE signing | |
| Manifest signed with (key_id) | |
| Signature file written to | |

## Attestation

We, the operator and the witness named above, attest that the release root was minted
attended, offline, on the machine named above, with the private key written only to the
storage medium named above in passphrase-wrapped form, and that no private key material was
printed, copied, or transmitted.

Operator signature: ______________________  Witness signature: ______________________

## After the sitting (back online)

1. The three-carrier edit, one reviewed commit: `config/trust-roots.yaml` (pin + R-INTENTOPS record, `status: active`), `packages/intentops-core/intentops_core/genesis/trust_pin.py` (`ROOT_FINGERPRINT`, `PIN_STATE = "minted"`), `docs/GENESIS.md` (the fingerprint).
2. From a fresh clone on a different machine: `python -m intentops_core.genesis.provenance` must read `G1.7 PASS`; `build_manifest.py --check` OK.
3. Publish the authority string out of band: the signed tag, the release notes, the project page.
