# A conformant identity repository (FIXTURE)

Everything here is fictional. There is no real node, no real operator, no real
key, and no real person in this tree. It exists so that
`scripts/identity/identity_repo_check.py` has a pristine baseline to mutate,
and so that its negative sibling has something to be a negative of.

The first real instance of this contract is private by construction and is not
shipped with this codebase.

One thing this fixture deliberately does NOT contain: a private-key-shaped
file. The key-shape refusal is proven in a temporary directory by
`--selftest`, because committing a key-shaped file to prove a checker that
forbids key-shaped files is the defect that checker exists to prevent.
