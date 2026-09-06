"""The exposure gate's history surface, and the excerpt it prints.

PURPOSE
    Two defects found on 2026-09-06, the day this repository got its first
    commit, and one regression test each.

      * The gate read the WORKING TREE only. A commit's author and committer
        lines are written by the machine that made the commit, never by
        anything in the tree, so a repository that scans perfectly clean can
        publish a private name on its first push and nothing here would
        notice. The gate's own module docstring declared this blind spot; the
        moment history existed, the blind spot had something in it.
      * The excerpt redacted only the span being reported. On a line carrying
        two fenced things -- which is exactly the shape of ``author: A Name
        <an@address>`` -- each was published inside the other's excerpt, into
        a CI log, which is a public surface.

    A live reading over this repository's own history is deliberately NOT
    asserted clean here. Whether the commits in this tree carry an operator's
    identity is a fact about who ran ``git commit``; the instrument's job is
    to say so, and a test that demanded silence would only teach the next
    person to switch it off.

BLIND SPOTS
    * These inherit every blind spot the gate publishes, and add one: history
      scanning reads identities and MESSAGES, never historical file content.
      A name deleted from a file today still sits in that file's earlier
      blobs and nothing here looks at blobs.
    * The synthetic logs below are built by hand rather than by git, so they
      prove the parser and the redaction, not git's output format. The live
      test below is the one that proves the two agree.
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE_PATH = REPO_ROOT / "scripts" / "ops" / "exposure_gate.py"


def _load_gate():
    spec = importlib.util.spec_from_file_location("exposure_gate_hist", GATE_PATH)
    assert spec and spec.loader, f"could not load {GATE_PATH}"
    module = importlib.util.module_from_spec(spec)
    sys.modules["exposure_gate_hist"] = module
    spec.loader.exec_module(module)
    return module


gate = _load_gate()

#: A token invented here, never a real fenced name: planting a real one would
#: write it into this file, which is the thing the fence exists to prevent.
PLANTED = "qzjvbxmrkw"
ADDRESS = "someone" + "@" + "nowhere.test"

#: A SECOND invented token, distinct from PLANTED, used only to prove the
#: message-shape exemption is scoped to its OWN declared token ids -- never
#: to every fenced thing that happens to sit on a matching line.
OTHER_PLANTED = "vqfnzhtklb"

#: A minimal, otherwise-valid fence document -- every load-bearing block
#: `load_fence` requires, with one substitutable slot for the block under
#: test. Used only to prove a bad `history_allowed_message_shapes` entry
#: halts loading; never exercised against a real corpus.
_MINIMAL_FENCE_YAML = """\
schema: exposure-fence/v1
as_of: "2026-09-06"
tokens:
  - id: t1
    sha256: "{zero_digest}"
    length: 6
    match: word
patterns:
  - id: p1
    regex: "x"
    catches: "x"
    trades: "x"
allowed_email_domains: []
allowed_line_markers:
  - "exposure-gate: allow"    # declaring the marker here, not exempting a real hit
allowed_paths: []
allowed_path_tokens: {{}}
skip_dirs: []
skip_extensions: []
max_file_bytes: 4000000
history_allowed_identities:
  identities: []
  allowed_email_domains: []
{message_shapes}
"""


def _fence():
    return gate._synthetic_fence(PLANTED)


def _fence_with_extra_token():
    """The standard synthetic fence, plus a second token the copyright-line
    shape does NOT declare as exempt."""
    fence = _fence()
    extra_digest = hashlib.sha256(OTHER_PLANTED.encode("ascii")).hexdigest()
    extra = gate.TokenRule("other-planted", extra_digest, len(OTHER_PLANTED), "word")
    return dataclasses.replace(fence, tokens=fence.tokens + (extra,))


def _log(*records):
    return gate._GIT_RECORD.join(
        gate._GIT_FIELD.join(r) for r in records) + gate._GIT_RECORD


# ---------------------------------------------------------------------------
# the instrument can fire
# ---------------------------------------------------------------------------


def test_a_name_in_an_author_line_is_a_finding():
    result, commits = gate.scan_history(
        REPO_ROOT, _fence(),
        log_text=_log(("a1b2c3d4e5f6", f"{PLANTED} Person", "ok@example.com",
                       "Someone Else", "ok@example.com", "a subject", "")))
    assert commits == 1
    assert [f for f in result.findings if f.rule_id == "planted-word"], (
        "an author line carrying a fenced name must be a finding")
    assert all(f.path.startswith("git:") for f in result.findings)


def test_a_name_in_a_commit_message_body_is_a_finding():
    result, _ = gate.scan_history(
        REPO_ROOT, _fence(),
        log_text=_log(("a1b2c3d4e5f6", "A Person", "ok@example.com",
                       "A Person", "ok@example.com", "tidy up",
                       f"reported by {PLANTED} last week")))
    assert result.findings, "a commit body is part of the published surface"
    assert any(f.line_no >= 4 for f in result.findings), (
        "the body should report on its own line, not the subject's")


def test_an_ordinary_commit_is_clean_and_still_counted():
    result, commits = gate.scan_history(
        REPO_ROOT, _fence(),
        log_text=_log(("a1b2c3d4e5f6", "A Person", "nobody@example.com",
                       "A Person", "nobody@example.com", "ordinary", "")))
    assert result.clean
    assert commits == 1, "a clean commit stays in the denominator"


# ---------------------------------------------------------------------------
# the history identity allowlist -- a public repository's own publishing
# account is not a leak, but the exemption is scoped to exactly the identity
# and line it was granted for, never to the word or the commit as a whole.
# ---------------------------------------------------------------------------


def test_an_allowlisted_identity_produces_no_finding_on_its_own_line():
    """The fixture identity is ``Permitted Person``, declared in the gate's
    own ``_synthetic_fence`` -- never a real name, for the same reason the
    planted token is never a real fenced word."""
    result, commits = gate.scan_history(
        REPO_ROOT, _fence(),
        log_text=_log(("a1b2c3d4e5f6", "Permitted Person", f"{PLANTED}@example.com",
                       "Permitted Person", f"{PLANTED}@example.com", "ordinary", "")))
    assert commits == 1
    assert result.clean, (
        "an identity the fence explicitly allowlists must not be a finding "
        "on its own author/committer line"
    )
    assert result.identities_exempted == 2, (
        "both the author and committer role matched the allowlisted identity"
    )


def test_a_personal_looking_email_outside_the_allowlist_is_still_a_finding():
    """Only the two declared addresses (and their domains) are exempt.

    Everything else -- a personal address, a firm address, anything not on
    this narrow list -- is still a hard finding, exactly as it is in the
    working tree scan.
    """
    result, _ = gate.scan_history(
        REPO_ROOT, _fence(),
        log_text=_log(("a1b2c3d4e5f6", "A Person", ADDRESS,
                       "A Person", ADDRESS, "subject", "")))
    assert any(f.rule_id == "email-shaped" for f in result.findings), (
        "an address that matches no allowlist entry must still be a finding"
    )


def test_a_fenced_token_in_the_message_body_is_still_a_finding_for_an_allowlisted_identity():
    """The allowlist exempts an IDENTITY LINE, not a commit.

    An allowlisted author/committer earns nothing for the subject or body --
    those are not that identity's line, and a fenced word there is exactly
    as much a finding as it would be for anyone else.
    """
    result, _ = gate.scan_history(
        REPO_ROOT, _fence(),
        log_text=_log(("a1b2c3d4e5f6", "Permitted Person", "safe@example.com",
                       "Permitted Person", "safe@example.com", "subject",
                       f"this body still names {PLANTED}")))
    assert any(f.rule_id in ("planted-word", "planted-substring")
               for f in result.findings), (
        "a fenced token in the message body must be a finding even when the "
        "author/committer identity is allowlisted"
    )


def test_the_exemption_counts_are_reported_never_silently():
    """A run always states how many identities it checked and exempted --
    whether or not anything was found -- so a widened allowlist is visible
    in the report, not only in the config diff."""
    result, commits = gate.scan_history(
        REPO_ROOT, _fence(),
        log_text=_log(("a1b2c3d4e5f6", "Permitted Person", f"{PLANTED}@example.com",
                       "Permitted Person", f"{PLANTED}@example.com", "subject", "")))
    assert result.identities_checked == 2
    assert result.identities_exempted == 2
    rendered = gate.render_history_report(result, commits, REPO_ROOT)
    assert "history allowlist:" in rendered
    assert "2 identity checks" in rendered
    assert "2 matched an allowlisted identity" in rendered


# ---------------------------------------------------------------------------
# the history message-shape allowlist -- a copyright line quoting the
# repository's own LICENSE is not a leak, but the exemption is scoped to
# exactly the tokens the shape declares, and only on the line that matches --
# never to every fenced thing that happens to sit on that line, and never to
# the same tokens elsewhere in the message.
# ---------------------------------------------------------------------------


def test_a_copyright_line_carrying_the_declared_tokens_produces_no_finding():
    result, commits = gate.scan_history(
        REPO_ROOT, _fence(),
        log_text=_log(("a1b2c3d4e5f6", "A Person", "safe@example.com",
                       "A Person", "safe@example.com", "release",
                       f"Copyright 2026 {PLANTED} Person")))
    assert commits == 1
    assert result.clean, (
        "a copyright-shaped line carrying the shape's declared tokens must "
        "be exempted, not a finding"
    )
    assert result.message_lines_exempted == 1


def test_the_same_tokens_on_a_non_copyright_body_line_are_still_a_finding():
    result, _ = gate.scan_history(
        REPO_ROOT, _fence(),
        log_text=_log(("a1b2c3d4e5f6", "A Person", "safe@example.com",
                       "A Person", "safe@example.com", "release",
                       f"see {PLANTED} for the maintainer")))
    assert any(f.rule_id in ("planted-word", "planted-substring")
               for f in result.findings), (
        "the shape's tokens are exempt only on a line that matches the "
        "shape -- the same tokens elsewhere in the message are unaffected"
    )
    assert result.message_lines_exempted == 0


def test_a_copyright_line_carrying_a_different_fenced_token_is_still_a_finding():
    """The exemption is scoped to the SHAPE's declared token ids, never to
    every fenced thing that happens to sit on a matching line."""
    fence = _fence_with_extra_token()
    result, _ = gate.scan_history(
        REPO_ROOT, fence,
        log_text=_log(("a1b2c3d4e5f6", "A Person", "safe@example.com",
                       "A Person", "safe@example.com", "release",
                       f"Copyright 2026 {OTHER_PLANTED}")))
    assert any(f.rule_id == "other-planted" for f in result.findings), (
        "a copyright-shaped line still reports a fenced token the shape "
        "does not declare as exempt"
    )
    assert result.message_lines_exempted == 1, (
        "the line still matched the copyright shape and incremented the "
        "counter; only its declared tokens were cleared"
    )


def test_a_midsentence_lowercase_copyright_mention_clears_while_an_estate_token_on_the_line_still_finds():
    """The real live finding this shape was widened for: commit 23e20a2
    carries its copyright notice mid-sentence, lowercase, preceded by other
    prose on the same line -- not on a line of its own. The shape must clear
    its own declared tokens there, while a wholly different fenced token
    sharing that same line is still a hard finding."""
    fence = _fence_with_extra_token()
    result, _ = gate.scan_history(
        REPO_ROOT, fence,
        log_text=_log(("a1b2c3d4e5f6", "A Person", "safe@example.com",
                       "A Person", "safe@example.com", "release",
                       f"Apache License 2.0, copyright 2026 {PLANTED} Person, "
                       f"also touches the {OTHER_PLANTED} estate")))
    assert not any(f.rule_id in ("planted-word", "planted-substring")
                   for f in result.findings), (
        "a mid-sentence, lowercase copyright mention must still be "
        "recognised as the shape and clear its declared tokens"
    )
    assert any(f.rule_id == "other-planted" for f in result.findings), (
        "a different fenced token on the same copyright-shaped line is "
        "still a finding -- the exemption never extends past its own "
        "declared tokens"
    )
    assert result.message_lines_exempted == 1


def test_a_message_shape_with_an_unparseable_pattern_halts_load_fence(tmp_path):
    bad = tmp_path / "fence.yaml"
    bad.write_text(
        _MINIMAL_FENCE_YAML.format(
            zero_digest="0" * 64,
            message_shapes=(
                "history_allowed_message_shapes:\n"
                "  - id: broken\n"
                "    pattern: '('\n"
                "    exempt_token_ids: [t1]\n"
            ),
        ),
        encoding="utf-8",
    )
    with pytest.raises(gate.FenceError):
        gate.load_fence(bad)


def test_a_message_shape_with_a_valid_pattern_loads_cleanly(tmp_path):
    """The negative case above is only meaningful beside a positive one --
    proving the minimal document loads when the pattern DOES compile."""
    good = tmp_path / "fence.yaml"
    pattern_line = (
        "    pattern: '^" + r"\s*Copyright " + r"\d{4}" + r"\b" + "'\n"
    )
    good.write_text(
        _MINIMAL_FENCE_YAML.format(
            zero_digest="0" * 64,
            message_shapes=(
                "history_allowed_message_shapes:\n"
                "  - id: fine\n"
                + pattern_line
                + "    exempt_token_ids: [t1]\n"
            ),
        ),
        encoding="utf-8",
    )
    fence = gate.load_fence(good)
    assert [s.id for s in fence.history_allowed_message_shapes] == ["fine"]


# ---------------------------------------------------------------------------
# and refuses rather than flattering itself
# ---------------------------------------------------------------------------


def test_an_unreadable_history_refuses(tmp_path):
    with pytest.raises(gate.HistoryUnavailable):
        gate.scan_history(tmp_path, _fence())


def test_an_empty_repository_is_an_empty_population_not_a_clean_bill(tmp_path):
    # An absent `git` raises FileNotFoundError before the returncode check
    # below can fire, so the PATH lookup has to come first.
    if shutil.which("git") is None:
        pytest.skip("git is not on PATH")
    if subprocess.run(["git", "init", "-q", str(tmp_path)],
                      capture_output=True).returncode != 0:
        pytest.skip("git is not available on this machine")
    result, commits = gate.scan_history(tmp_path, _fence())
    assert commits == 0 and result.clean
    rendered = gate.render_history_report(result, commits, tmp_path)
    assert "EMPTY" in rendered and "not a clean bill" in rendered, (
        "zero commits must not render as a clean verdict")


def test_history_never_inherits_a_working_tree_path_exemption():
    """``allowed_paths`` names places in the TREE. An author line is not one."""
    fence = _fence()
    assert "docs/exempt.md" in fence.allowed_path_tokens
    result, _ = gate.scan_history(
        REPO_ROOT, fence,
        log_text=_log(("a1b2c3d4e5f6", f"{PLANTED} Person", "ok@example.com",
                       "A Person", "ok@example.com", "docs/exempt.md", "")))
    assert result.findings, (
        "no commit is an allowed path; the exemption must not reach here")


# ---------------------------------------------------------------------------
# the excerpt promise
# ---------------------------------------------------------------------------


def test_an_excerpt_redacts_every_fenced_span_on_the_line():
    line = f"author: {PLANTED} Person <{ADDRESS}>"
    result = gate.scan_text(line, _fence(), path="a.md")
    assert len(result.findings) >= 2, (
        "this line carries a fenced name AND an address shape")
    for finding in result.findings:
        assert PLANTED not in finding.excerpt
        assert ADDRESS not in finding.excerpt
    assert any("[REDACTED:" in f.excerpt for f in result.findings)


def test_the_excerpt_still_shows_enough_context_to_find_the_line():
    line = f"author: {PLANTED} Person <{ADDRESS}>"
    result = gate.scan_text(line, _fence(), path="a.md")
    assert any("author" in f.excerpt for f in result.findings), (
        "a redaction that removes the whole line is not an excerpt")


def test_a_history_finding_never_echoes_an_identity():
    result, _ = gate.scan_history(
        REPO_ROOT, _fence(),
        log_text=_log(("a1b2c3d4e5f6", f"{PLANTED} Person", ADDRESS,
                       f"{PLANTED} Person", ADDRESS, "subject", "")))
    assert result.findings
    for finding in result.findings:
        assert PLANTED not in finding.excerpt
        assert ADDRESS not in finding.excerpt


# ---------------------------------------------------------------------------
# the live reading -- reported, never asserted silent
# ---------------------------------------------------------------------------


def test_the_gate_can_read_this_repositorys_own_history():
    """The parser and git agree, whatever the verdict turns out to be."""
    fence = gate.load_fence(REPO_ROOT / "config" / "exposure-fence.yaml")
    try:
        result, commits = gate.scan_history(REPO_ROOT, fence)
    except gate.HistoryUnavailable:
        pytest.skip("no readable git history here")
    assert commits >= 0
    for finding in result.findings:
        assert finding.path.startswith("git:"), finding.render()
        # Whatever it found, the report must carry a redaction rather than the
        # thing itself. This is the property that makes it safe to print the
        # findings in CI at all.
        assert "[REDACTED:" in finding.excerpt, finding.render()


def test_the_selftest_covers_the_history_paths():
    assert gate.selftest() == 0
