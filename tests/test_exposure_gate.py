"""The exposure gate, under test -- including the gate's own scan of this repo.

PURPOSE
    The gate is the mechanical half of the extraction seam. These tests do two
    different jobs and it is worth being clear which is which:

      * the unit tests prove the INSTRUMENT works -- it fires on a planted hit,
        it clears an explained exemption, it refuses a bare one, and it keeps
        an unscannable file in the denominator;
      * ``test_this_repository_is_clean`` is the LIVE reading. It can go red
        because someone committed something, which is the whole point.

    Keeping both in one file is deliberate: a green live reading means nothing
    unless the instrument that produced it was shown to be capable of red, and
    a reader who sees only one of the two has half the evidence.

BLIND SPOTS
    * These tests inherit every blind spot the gate publishes in its own module
      docstring, and add none of their own. Chiefly: the fence finds only what
      it declares, and it reads the working tree rather than git history.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE_PATH = REPO_ROOT / "scripts" / "ops" / "exposure_gate.py"
FENCE_PATH = REPO_ROOT / "config" / "exposure-fence.yaml"


def _load_gate():
    spec = importlib.util.spec_from_file_location("exposure_gate", GATE_PATH)
    assert spec and spec.loader, f"could not load {GATE_PATH}"
    module = importlib.util.module_from_spec(spec)
    sys.modules["exposure_gate"] = module
    spec.loader.exec_module(module)
    return module


gate = _load_gate()


@pytest.fixture(scope="module")
def fence():
    return gate.load_fence(FENCE_PATH)


# ---------------------------------------------------------------------------
# the instrument
# ---------------------------------------------------------------------------


def test_the_selftest_passes() -> None:
    """Every verdict path the gate can take is shown to fire."""
    assert gate.selftest() == 0


def test_the_shipped_fence_loads_and_is_not_empty(fence) -> None:
    assert fence.tokens, "an empty denylist makes every scan green by construction"
    assert fence.patterns, "the shape patterns are the half a rename cannot evade"
    assert fence.allowed_paths, (
        "the fence declares no allowed paths; the files that legitimately carry "
        "a name would be unfixable findings"
    )


def test_a_fence_missing_a_load_bearing_field_halts(tmp_path: Path) -> None:
    """A config half-read produces a scan that runs and is wrong."""
    bad = tmp_path / "fence.yaml"
    bad.write_text("schema: exposure-fence/v1\nas_of: 'today'\n", encoding="utf-8")
    with pytest.raises(gate.FenceError):
        gate.load_fence(bad)


def test_an_unknown_schema_is_a_hard_exit(tmp_path: Path) -> None:
    bad = tmp_path / "fence.yaml"
    bad.write_text("schema: exposure-fence/v99\nas_of: 'today'\n", encoding="utf-8")
    with pytest.raises(gate.FenceError):
        gate.load_fence(bad)


def test_an_undeclared_match_mode_is_a_hard_exit(tmp_path: Path) -> None:
    """An undeclared value is never a default of everything-applies."""
    bad = tmp_path / "fence.yaml"
    bad.write_text(
        "schema: exposure-fence/v1\n"
        "as_of: 'today'\n"
        "tokens:\n"
        "  - id: x\n"
        f"    sha256: \"{'a' * 64}\"\n"
        "    length: 6\n"
        "    match: fuzzy\n",
        encoding="utf-8",
    )
    with pytest.raises(gate.FenceError, match="not declared"):
        gate.load_fence(bad)


def test_a_finding_never_echoes_the_matched_text(fence) -> None:
    """The report goes into a public build log; it must not carry the word back."""
    planted = "qzjvxwtprm"
    synthetic = gate._synthetic_fence(planted)
    result = gate.scan_text(f"a line about {planted} here", synthetic, path="a.md")
    assert result.findings
    for finding in result.findings:
        assert planted not in finding.excerpt
        assert planted not in finding.render()


def test_an_unexplained_exemption_is_itself_a_finding(fence) -> None:
    """Off-by-design and off-by-neglect must not look identical."""
    planted = "qzjvxwtprm"
    synthetic = gate._synthetic_fence(planted)
    bare = gate.scan_text(f"{planted}  # exposure-gate: allow", synthetic, path="a.md")
    assert bare.bad_markers and not bare.clean

    explained = gate.scan_text(
        f"{planted}  # exposure-gate: allow the reserved first instance",
        synthetic, path="a.md",
    )
    assert explained.clean


def test_a_path_exemption_covers_only_the_tokens_it_names(fence) -> None:
    """An allowed path is not a hole in the fence.

    This is the property that matters most in this file, because getting it
    wrong is invisible: a whole-file exemption over the repository's most
    sensitive prose renders as a CLEAN scan. The gate would go green because it
    stopped looking, not because there was nothing to find -- and a green scan
    is exactly what nobody investigates.
    """
    planted = "qzjvxwtprm"
    synthetic = gate._synthetic_fence(planted)
    exempt = synthetic.allowed_path_tokens["docs/exempt.md"]

    named = gate.scan_text(f"about {planted}", synthetic, path="docs/exempt.md",
                           exempt_token_ids=exempt)
    assert not named.findings, "the token the path is allowed to carry was reported"

    other = gate.scan_text(f"about {planted}", synthetic, path="docs/exempt.md",
                           exempt_token_ids=("a-different-token",))
    assert other.findings, (
        "an exemption for one token silenced a different one; a path exemption "
        "must name the tokens it covers and cover nothing else"
    )

    shapes = gate.scan_text(gate._KEY_HEADER_SAMPLE, synthetic,
                            path="docs/exempt.md", exempt_token_ids=exempt)
    assert shapes.findings, "an allowed path must never be exempt from the shape patterns"


def test_the_shipped_fence_exempts_no_token_anywhere(fence) -> None:
    """Today every allowed path carries an empty exemption list, and that is a
    claim worth pinning: the three allowed paths exist for a name that is not
    on the denylist, so no exemption is owed. If one is ever added, this test
    fails and somebody has to justify it in the diff."""
    for path, ids in fence.allowed_path_tokens.items():
        assert ids == (), (
            f"{path} now exempts {list(ids)}. A path exemption is a declared "
            "blind spot: state in the commit which name it covers and why that "
            "name legitimately appears there."
        )


def test_an_exemption_for_an_undeclared_path_is_a_hard_exit(tmp_path: Path) -> None:
    """An exemption for a path the fence does not declare is one nobody reviewed."""
    bad = tmp_path / "fence.yaml"
    bad.write_text(
        "schema: exposure-fence/v1\n"
        "as_of: 'today'\n"
        "tokens:\n"
        "  - {id: x, sha256: \"" + "a" * 64 + "\", length: 6, match: substring}\n"
        "patterns:\n"
        "  - {id: p, regex: 'zzz', catches: c, trades: t}\n"
        "allowed_email_domains: []\n"
        "allowed_paths: [docs/ok.md]\n"
        "allowed_path_tokens: {docs/elsewhere.md: [x]}\n"
        "allowed_line_markers: ['exposure-gate: allow']\n"
        "skip_dirs: []\n"
        "skip_extensions: []\n"
        "max_file_bytes: 1000\n",
        encoding="utf-8",
    )
    with pytest.raises(gate.FenceError, match="not in allowed_paths"):
        gate.load_fence(bad)


def test_an_unscannable_file_stays_in_the_denominator(tmp_path: Path) -> None:
    """A refusal that leaves the population is how an instrument flatters itself."""
    synthetic = gate._synthetic_fence("qzjvxwtprm")
    (tmp_path / "big.md").write_text("x" * (synthetic.max_file_bytes + 1),
                                     encoding="utf-8")
    result = gate.scan_tree(tmp_path, synthetic)
    assert [rel for rel, _ in result.files_unscanned] == ["big.md"]
    assert result.files_scanned == 0


def test_the_report_states_what_a_clean_run_does_not_mean(fence, tmp_path: Path) -> None:
    """A green verdict that does not publish its sensitivity manufactures trust."""
    (tmp_path / "ok.md").write_text("ordinary prose\n", encoding="utf-8")
    result = gate.scan_tree(tmp_path, fence)
    report = gate.render_report(result, fence, tmp_path)
    assert "VERDICT: CLEAN" in report
    assert "does and does not say" in report
    assert "git history" in report, (
        "the clean report must name the population it did not read"
    )
    assert "files scanned" in report, "a count without its denominator is not a measurement"


# ---------------------------------------------------------------------------
# the live reading
# ---------------------------------------------------------------------------


def test_this_repository_is_clean(fence) -> None:
    """The live reading. Red here means something was committed, not that a
    test is flaky -- read the finding, do not silence it."""
    result = gate.scan_tree(REPO_ROOT, fence)
    print("\n" + gate.render_report(result, fence, REPO_ROOT))
    assert result.files_scanned > 0, (
        "the walk read no files at all; a green verdict over an empty "
        "population is not a finding"
    )
    assert result.clean, (
        f"{len(result.findings)} fenced hit(s) and "
        f"{len(result.bad_markers)} unexplained exemption(s) are present. "
        "Each is a real finding: the estate this project was extracted from "
        "must not be reachable from anything published here."
    )
