# intentops-saddle-claudecode

The **reference saddle**: the adapter that lets one specific host runtime carry
IntentOps.

A saddle is the impure half of the gate. `intentops_core.gate` decides and
returns a `Verdict` -- it never exits a process, never writes a protocol frame,
and knows nothing about any runtime. This package turns that `Verdict` into the
one thing this host understands, and does the filesystem work the core
deliberately refuses to do.

That split exists so that supporting a second host is **writing a second
package**, not editing the gate. A gate with a host baked into it can only live
in that host, and can only be tested by running it.

---

## The five operations

A host carries IntentOps if and only if it can do five things. This package
implements all five, and `OPERATIONS` in `__init__.py` is the machine-readable
form of that claim -- the contract test reads it rather than trusting this page.

| Op | Name | What this package does | What breaks without it |
|----|------|------------------------|------------------------|
| **S1** | `classify` | delegates to the core classifier; adds nothing | nothing -- S1 is pure and runs identically everywhere |
| **S2** | `decide` | returns allow / ask / deny, and renders deny as this host's refusal | **there is no gate, only advice** |
| **S3** | `record` | appends every decision, including the permits | no audit trail, and no ledger a council or an operator can read |
| **S4** | `boot_corpus` | prints the node's rules into a fresh window at session start | a node that cannot read its own refusals has them in name only |
| **S5** | `halt` | refuses everything while the stand-down marker exists | no one-command stop |

**S2 is the only hard one, and it is only half ours.** This package can return a
refusal; only the host can enforce one. That is why the saddle contract states
S2 as a requirement on the *runtime* and not as a function in a package -- and
why "the tests pass" is not the same claim as "this host honours a deny".

---

## Install into an existing project

```bash
pip install -e packages/intentops-core -e packages/intentops-saddle-claudecode
```

Then merge the `hooks` block from [`settings.template.json`](settings.template.json)
into the project's settings file (or copy the whole file if the project has
none), and prove the gate can actually refuse:

```bash
python -m intentops_saddle_claudecode.pre_tool --selftest
python -m intentops_saddle_claudecode.observe --selftest
python -m intentops_saddle_claudecode.session_start --selftest
```

**Do not skip that last step.** A hook that is wired but broken is
indistinguishable from a hook that is wired and permissive: both let everything
through, quietly. The selftests exist so that "the gate can refuse" is something
you have watched happen rather than something you assume.

### Where the node is

The adapter has to find the node it is riding. It resolves the root in this
order, and the answer it used travels into every ledger row so a later reader
can see which step answered:

1. `INTENTOPS_NODE_ROOT` -- an explicit override, never persisted
2. the nearest ancestor holding `.intentops/`
3. the nearest ancestor holding `.git/`
4. the working directory

Step 4 is what lets a freshly cloned node run genesis at all. Without it the
gate would refuse every call including the one that creates the state it is
looking for, which is a deadlock dressed as rigour.

### Where things are written

Only one file is ever written: `.intentops/logs/gates/<date>.jsonl`, append-only,
one single-write append per event, no read-modify-write and therefore no lock.
Nothing else in this package writes anything.

---

## Three properties worth knowing before you trust it

**It fails closed.** A payload it cannot parse, an estate map it cannot read, a
ledger it cannot append to -- each is a refusal, never a pass. The reason names
the gate as the fault, so an operator is not sent hunting through their own
command for a problem that was ours.

**It never emits an "allow".** On a clean verdict it stays silent and exits
zero, leaving the host's own permission rules to run. An adapter that returned
an explicit allow would be auto-approving calls the operator had asked to be
prompted about. **This gate may raise the bar and may never lower it** -- which
also means it will not get you out of a prompt you configured for yourself.

**It records before it emits.** A decision that reached the host but never
reached the ledger is an action nobody can answer for later, so an unwritable
ledger becomes a refusal rather than a quiet permit.

---

## What this package cannot tell you

Stated plainly, because a gate that hides its coverage manufactures confidence
over a population it never saw:

- **It sees one call at a time.** A sequence of individually innocuous calls
  that together reach the world is invisible to it.
- **It believes the host.** Everything keys off what the runtime says is about
  to happen. A runtime that misreports a tool call defeats the adapter entirely,
  and no amount of schema checking would notice.
- **Classification reads the command skeleton, never file content.** That is
  deliberate -- the opposite error, reading data as an act, fires constantly and
  teaches people to route around the gate -- but it means a command assembled at
  runtime, decoded from a variable, or hidden in quotes is not seen. This raises
  the cost of an accident. It is not a defence against a determined evasion and
  must never be described as one.
- **S4 reports what it printed, not what the window received.** Whether the host
  kept the text is outside anything this package can observe.
- **Both refusal channels are used at once** -- the decision frame and a non-zero
  exit -- because a host that understands only one still refuses. If a future
  host version treated a non-zero exit as a hook crash and ran the tool anyway,
  this adapter would be advisory and would not know it.

---

## Uninstalling, and stopping

Removing the `hooks` block removes the gate. That is a deliberate act and it
leaves no trace in the ledger, which is worth saying out loud: **the ledger
records what the gate decided, never what the host did with it.**

If what you want is to stop the node rather than to remove the gate, that is a
different thing and it takes one command:

```bash
intentops stand-down
```

It writes `.intentops/halt.marker`, and this adapter refuses every call while
that file exists -- ahead of the classifier, ahead of everything. No reason is
required or recorded. A stood-down node is **off, not broken**, and nothing bad
happens to you for using it.
