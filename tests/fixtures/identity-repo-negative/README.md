# A NON-conformant identity repository (FIXTURE)

Everything here is fictional. This tree exists so that
`scripts/identity/identity_repo_check.py` can be proven able to REJECT, which
is the only thing that makes its acceptance of the conformant sibling mean
anything: a validator with no negative fixture is a detector that has never
fired.

Eight violations are committed here on purpose, and each is commented in place:

1. `MANIFEST-FALSIFIER` -- the falsifier is blank.
2. `BINDING` -- a default binding is declared, and there is no default.
3. `NAME-UNRATIFIED` -- a name with nobody named as having ratified it.
4. `MEMBER-WRITE-MODEL` -- a member declaring a write model outside the
   closed vocabulary.
5. `MEMBER-MISSING` -- a required member absent from disk.
6. `FOREIGN-GRANT` (manifest) -- a delegated-autonomy grant from another root.
7. `FOREIGN-GRANT` (journal) -- an ACTIVE, PERMISSIVE ruling recorded under
   another operator's root.
8. `ORDERING-NO-ROOT` -- a ruling row that does not say whose authority it
   carries.

What is deliberately NOT here: a private-key-shaped file. Committing one to
prove a checker that forbids key-shaped files is the defect that checker
exists to prevent, so `KEY-SHAPE` is proven in a temporary directory by
`--selftest` instead.
