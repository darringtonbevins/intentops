# Release evidence

`v0.1.0-commit.txt` names the release commit and tag; `v0.1.0-commit.txt.ots` is an OpenTimestamps proof of that file (sha256 7713806e...) submitted to public calendars on 2026-09-06. Verify with `ots verify v0.1.0-commit.txt.ots` once the Bitcoin attestation has confirmed (`ots upgrade` first). The proof covers the commit hash it names, which in turn covers the whole tree at that commit; this evidence commit is by construction one commit later.

`v0.4.0-commit.txt` names the release commit, tag, and the release-root authority string; `v0.4.0-commit.txt.ots` is its OpenTimestamps proof, submitted to public calendars on 2026-09-07. Same verification as above.
