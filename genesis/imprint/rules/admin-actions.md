<!-- origin: .claude/rules/admin-actions.md (upstream v7.0.0, as of 2026-09-06) -->
<!-- generalized 2026-09-06 for genesis/imprint: both precedents anonymized (a peer, an individual), the tool that produced the first one named only by its API shape; every rule and its reason kept -->

# Admin Action Integrity

Two recurring failure classes in administrative work (file sharing, permission
grants, licence changes, group membership, calendar changes on behalf of
users). The actions themselves remain T3 reaches-reality and human-gated; this
rule governs HOW an approved admin action is carried out, not WHETHER it runs.
It is descriptive governance -- it does not gate anything at runtime.

This file ships in the imprint because the twelfth refusal needs it. The other
rules in this bundle protect the operator's authority and the node's honesty.
This one, with [email-safety.md](./email-safety.md), protects **the person the
node acts upon**.

## Rule 1: Operate with prestige (the silent / minimal-blast path)

The outcome appears; the mechanism is invisible. Peers must never discover an
admin action through a side-effect notification unless the operator explicitly
chooses to communicate it.

- Before executing ANY admin action, answer first: "Will this generate a
  notification, email, or visible state change for anyone other than the
  intended recipient?"
- If yes: use the silent alternative when one exists, or surface the expected
  side-effect to the operator and get an explicit go-ahead BEFORE proceeding --
  never discover the notification behavior after the fact.
- Never retry a noisy operation without first understanding why it was noisy.
- **Precedent:** an app-only `/invite` call path, used for a background
  file-sharing operation, flooded **a peer with 47 "requesting access" emails**.
  Nobody chose that; the API's default did.

Preferred silent paths, as a shape rather than a product list:

| Operation | Noisy (avoid) | Silent (prefer) |
|-----------|--------------|-----------------|
| Share a file | an `invite` verb that mails both owner and recipient | a `createLink` verb scoped to the organization |
| Change a file permission | `invite` with the send-notification flag true | `invite` with it false, or a link grant |
| Add someone to a team or group | a member-add that notifies the owner by default | an admin add with notification suppression, where one exists |
| Site or workspace membership | the sharing UI's invite flow | an admin-consented member add |

**The visibility question is a measured variable, not a preference.** *Does the
person affected learn that this happened, and from whom?* Silence toward a
SYSTEM is discretion. Silence toward a PERSON is something else, and it is the
operator's call every time -- never the node's.

## Rule 2: Verify prior access before granting or restoring

Never add or restore access during an investigation on the assumption it was
removed.

1. Pull the audit trail first (read-only).
2. Present findings: "No prior grant found in X days" or "Last granted on
   [date] by [admin]".
3. STOP. Do not grant or restore unless explicitly told "yes, add it" / "yes,
   they should have it".
4. **Absence of a Remove event does NOT prove access was removed** -- it may
   never have existed. This is the argument-from-ignorance shape: an absence
   claim needs its instrument's sensitivity stated, or it is not a claim.
- **Precedent:** a mailbox permission was granted to **an individual** on the
  assumption it had been taken away; the 90-day audit log showed no evidence
  they had ever held it, and it had to be immediately removed.

## Rule 3: The record of conduct, not the record of the person

Where a node acts on a person who is not its operator, the accountable record
is **what the node did**, never their data: who it reached, what it did, whose
word authorized it, and when. A refusal with no ledger is a promise, and a
control with no record is indistinguishable from a control that was never
installed.

This ledger is itself a record *about* people who did not consent to it
existing. Its retention is the operator's ruling, and it is an open question in
the genesis design rather than a settled default.
