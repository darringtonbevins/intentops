# Quick start

This walks a fresh clone to a running `intentops doctor` and a dry-run of genesis. It
does not walk you through a real genesis run — that mints identity and trust material,
and doing it for real is covered in `docs/GENESIS.md`, not here.

## 1. Clone

```
git clone <this repository>
cd intentops
```

## 2. Create and activate a virtual environment

```
python -m venv .venv
```

Windows (PowerShell):

```
.venv\Scripts\Activate.ps1
```

macOS / Linux:

```
source .venv/bin/activate
```

Python 3.11 or newer is required. There is no CUDA or GPU dependency anywhere in the
framework core — if a future capability package wants one, it will say so explicitly
in its own `pyproject.toml`, not inherit it silently from the core.

## 3. Install the framework core

```
pip install -e packages/intentops-core
```

This installs the `intentops` console command and its stdlib-first dependencies
(`pyyaml`, `cryptography` — used for Ed25519 key material, never for anything
network-facing).

## 4. Run the doctor

```
intentops doctor
```

This is a read-only check. It answers questions like: is a genesis identity bound yet
(it will not be, on a fresh clone), do the trust-root carriers agree with each other,
is the imprint bundle present and does its hash match its manifest. Nothing here writes
anything to disk.

## 5. Dry-run genesis

```
intentops genesis --identity-repo new --dry-run
```

`--dry-run` walks the genesis state machine's read-only phases (substrate detection,
provenance verification) and reports what a real run *would* do at each subsequent
phase, without minting any keys, writing any identity material, or asking you the
consent and founding-conversation questions that a real run requires you to be present
for. This is the safe way to see the shape of genesis before deciding to run it for
real.

To actually grow an identity — which asks for your consent, records a founding
conversation, and mints trust material that (by design) never leaves your machine and
never enters a repository — read `docs/GENESIS.md` first. Genesis is deliberately not
something you back into by trying flags; every state that matters gates on you being
present at an interactive terminal.

## What "done" looks like at this stage

- `intentops doctor` runs and exits cleanly (or names, in plain language, what is
  missing on a fresh clone — a missing genesis binding is expected here, not an error).
- `intentops genesis --identity-repo new --dry-run` prints the phase sequence and does
  not write to `.intentops/` or to any identity repository.

If either of those does not hold, that is a bug in this seed, not something to work
around locally.
