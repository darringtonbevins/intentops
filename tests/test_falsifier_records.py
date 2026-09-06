"""The falsifier records, under test -- a verdict must exist and be graded.

PURPOSE
    Two falsifiers gate this project's release plan: F1 (does the suite pass
    cold, against the package as shipped) and F3 (does a stranger's genesis
    produce a node). The plan's own correction record notes the failure mode
    these tests exist to prevent: a story marked done because the falsifier was
    *run*, with no recorded verdict, so a FAIL and a PASS are indistinguishable
    downstream. Tooling existing is not evidence; a graded record is.

    So these tests assert three things about each record, and deliberately not
    a fourth. They assert that the record EXISTS, that it carries exactly one
    unambiguous ``VERDICT: PASS`` / ``VERDICT: FAIL`` line, and that it NAMES
    THE ENVIRONMENT it was measured in -- because an unstated environment is an
    unstated assumption, and a green reading taken in the one environment that
    could not fail is how the line-ending defect F1 found survived to a public
    seed.

    They do not assert that either verdict is PASS. A test that demanded PASS
    would make the honest recording of a FAIL a build failure, which is exactly
    the pressure that turns a falsifier into a formality.

WRITE MODEL
    None. These tests only read. The records themselves are reviewed,
    human-and-agent-authored documents with a single writer per run (the leg
    that executed the falsifier); a re-run supersedes by adding a NEW dated
    file, never by rewriting an existing one, so the graded history is
    append-only at the directory level.

BLIND SPOTS
    - This checks the SHAPE of a record, never its truth. A record can carry a
      well-formed verdict line and a fabricated transcript, and nothing here
      would notice. The defence against that is the transcript itself being
      reproducible from the stated environment, which a reader must do.
    - It reads the newest dated record per falsifier. An older record that has
      rotted is not re-checked.
    - "Names its environment" is checked as the presence of specific stated
      facts (interpreter, working directory, harness-variable handling). A
      record could name them and name them wrongly.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FALSIFIER_DIR = REPO_ROOT / "docs" / "falsifiers"

#: id -> the filename stem each falsifier's record must use.
REQUIRED_RECORDS: Dict[str, str] = {
    "F1": "F1-cold-suite",
    "F3": "F3-stranger-genesis",
}

VERDICT_RE = re.compile(r"^\s*(?:#+\s*|\*\*)?VERDICT:\s*(PASS|FAIL)\b", re.MULTILINE)

#: Facts a record must state about where it was measured. Each entry is
#: (human name, tuple of accepted markers -- any one satisfies it).
ENVIRONMENT_FACTS = (
    ("an environment section", ("## Environment",)),
    ("the working directory", ("Working directory", "working directory", "cwd")),
    ("the interpreter", ("Interpreter", "CPython", "Python 3.")),
    ("the harness-variable handling", ("Harness variables", "harness variables")),
    ("the subject under test", ("Subject", "subject", "Subjects")),
    ("its blind spots", ("Blind spots", "blind spots")),
)


def _records_for(stem: str) -> List[Path]:
    if not FALSIFIER_DIR.is_dir():
        return []
    return sorted(FALSIFIER_DIR.glob(f"{stem}-*.md"))


def _newest_record(falsifier_id: str) -> Path:
    stem = REQUIRED_RECORDS[falsifier_id]
    found = _records_for(stem)
    if not found:
        # A load-bearing artifact that is absent HALTs; it never defaults to
        # "assume it was fine".
        pytest.fail(
            f"no record for falsifier {falsifier_id}: expected "
            f"docs/falsifiers/{stem}-<date>.md. A falsifier with no recorded "
            f"verdict has not been run, whatever the plan says."
        )
    return found[-1]


def verdicts_in(text: str) -> List[str]:
    """Every graded verdict line in a record, in order.

    Pulled out as a function so the negative tests below can prove the reader
    can actually fail -- a detector that has never fired is indistinguishable
    from a broken one.
    """
    return VERDICT_RE.findall(text)


def missing_environment_facts(text: str) -> List[str]:
    """The names of environment facts this record does not state."""
    return [
        name for name, markers in ENVIRONMENT_FACTS
        if not any(marker in text for marker in markers)
    ]


# ---------------------------------------------------------------------------
# the records themselves
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("falsifier_id", sorted(REQUIRED_RECORDS))
def test_the_record_exists(falsifier_id: str) -> None:
    record = _newest_record(falsifier_id)
    assert record.is_file()
    assert record.stat().st_size > 0, f"{record.name} is empty"


@pytest.mark.parametrize("falsifier_id", sorted(REQUIRED_RECORDS))
def test_the_record_carries_exactly_one_graded_verdict(falsifier_id: str) -> None:
    record = _newest_record(falsifier_id)
    found = verdicts_in(record.read_text(encoding="utf-8"))
    assert found, (
        f"{record.name} carries no 'VERDICT: PASS' or 'VERDICT: FAIL' line. "
        f"'was run' is not a verdict."
    )
    assert len(found) == 1, (
        f"{record.name} carries {len(found)} verdict lines ({found}). One "
        f"record grades one falsifier; two verdicts make the downstream "
        f"unlock ambiguous, which is the whole defect this file guards."
    )


@pytest.mark.parametrize("falsifier_id", sorted(REQUIRED_RECORDS))
def test_the_record_names_its_environment(falsifier_id: str) -> None:
    record = _newest_record(falsifier_id)
    absent = missing_environment_facts(record.read_text(encoding="utf-8"))
    assert not absent, (
        f"{record.name} does not state: {', '.join(absent)}. A measurement "
        f"whose environment is unstated cannot be reproduced or believed."
    )


@pytest.mark.parametrize("falsifier_id", sorted(REQUIRED_RECORDS))
def test_a_fail_verdict_states_a_reason(falsifier_id: str) -> None:
    """A bare FAIL is a mood; a FAIL with a reason is a finding."""
    record = _newest_record(falsifier_id)
    text = record.read_text(encoding="utf-8")
    if verdicts_in(text) != ["FAIL"]:
        pytest.skip(f"{record.name} did not record a FAIL")
    tail = text[text.index("VERDICT: FAIL") + len("VERDICT: FAIL"):]
    prose = "".join(ch for ch in tail[:2000] if ch.isalnum())
    assert len(prose) > 200, (
        f"{record.name} records FAIL with no stated reason after it."
    )
    assert "Reason" in tail[:2000] or "reason" in tail[:2000], (
        f"{record.name} records FAIL without naming a reason."
    )


def test_every_record_in_the_directory_is_graded() -> None:
    """No ungraded stragglers -- including falsifiers added after this file."""
    if not FALSIFIER_DIR.is_dir():
        pytest.fail("docs/falsifiers/ does not exist")
    ungraded = [
        p.name for p in sorted(FALSIFIER_DIR.glob("*.md"))
        if not p.name.startswith("README")
        and len(verdicts_in(p.read_text(encoding="utf-8"))) != 1
    ]
    assert not ungraded, f"ungraded falsifier record(s): {ungraded}"


# ---------------------------------------------------------------------------
# the readers can fail -- a green detector is evidence only if red was reachable
# ---------------------------------------------------------------------------


def test_the_verdict_reader_can_actually_fail() -> None:
    assert verdicts_in("nothing was graded here\n") == []
    assert verdicts_in("we ran it and it was fine\nVERDICT: probably ok\n") == []
    assert verdicts_in("VERDICT: PASS\n") == ["PASS"]
    assert verdicts_in("**VERDICT: FAIL**\n") == ["FAIL"]
    assert verdicts_in("## VERDICT: FAIL\n") == ["FAIL"]
    assert verdicts_in("VERDICT: PASS\nlater\nVERDICT: FAIL\n") == ["PASS", "FAIL"]


def test_the_environment_reader_can_actually_fail() -> None:
    assert missing_environment_facts("") == [name for name, _ in ENVIRONMENT_FACTS]
    bare = "## Environment\nran it on a computer\n"
    absent = missing_environment_facts(bare)
    assert "the interpreter" in absent
    assert "an environment section" not in absent


def test_a_missing_record_is_reported_not_defaulted(tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """An absent record must HALT the check, never read as a silent pass."""
    monkeypatch.setattr(__import__(__name__), "FALSIFIER_DIR", tmp_path)
    with pytest.raises(BaseException) as caught:
        _newest_record("F1")
    assert "no record for falsifier F1" in str(caught.value)


# ---------------------------------------------------------------------------
# transcript integrity -- a quoted gate run must be internally consistent
# ---------------------------------------------------------------------------
#
# Added 2026-09-06 after the wave-2 verifier found F1's exposure-gate block was
# a COMPOSITE: the population line of one run above the hit list of another,
# with two HIT lines and the true count edited out, presented as verbatim. The
# shape checks above could not see it -- they read verdict lines and section
# headings, never the transcripts. This reads the transcripts, and it is the
# cheapest mechanical grip there is on "is this quote literal": a gate that
# prints its own hit count next to its own hits cannot disagree with itself in
# a real run, so a disagreement is an edit.

FENCE_RE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)
GATE_VERDICT_RE = re.compile(r"VERDICT:\s*(EXPOSED|CLEAN)\b(?:\s*--\s*(\d+)\s*hit)?")


def transcript_inconsistencies(text: str) -> List[str]:
    """Quoted gate transcripts whose stated hit count contradicts their hits.

    Returns one human-readable string per offending block. Blocks that quote no
    gate verdict are not in the population at all -- this only grades blocks
    that make a countable claim about themselves.
    """
    problems: List[str] = []
    for n, block in enumerate(FENCE_RE.findall(text), start=1):
        found = GATE_VERDICT_RE.search(block)
        if not found:
            continue
        hits = len([ln for ln in block.splitlines() if ln.strip().startswith("HIT ")])
        stated = int(found.group(2)) if found.group(2) else 0
        if found.group(1) == "CLEAN" and hits:
            problems.append(
                f"block {n}: verdict CLEAN but {hits} HIT line(s) quoted"
            )
        elif stated != hits:
            problems.append(
                f"block {n}: verdict states {stated} hit(s), {hits} HIT "
                f"line(s) quoted between the population line and the verdict"
            )
    return problems


@pytest.mark.parametrize("falsifier_id", sorted(REQUIRED_RECORDS))
def test_quoted_gate_transcripts_are_internally_consistent(falsifier_id: str) -> None:
    record = _newest_record(falsifier_id)
    problems = transcript_inconsistencies(record.read_text(encoding="utf-8"))
    assert not problems, (
        f"{record.name} quotes a gate transcript that contradicts itself: "
        + "; ".join(problems)
        + ". A transcript is either literal or it is prose -- one run per "
          "block, or label the block abridged and stop calling it output."
    )


def test_the_transcript_reader_can_actually_fail() -> None:
    """The exact composite that shipped, and the shapes either side of it."""
    composite = (
        "```\n"
        "  population: 176 files scanned, 33847 lines read\n"
        "  HIT release/v0.1.0-commit.txt:4: token operator-given\n"
        "  VERDICT: EXPOSED -- 6 hit(s), 0 unexplained exemption(s)\n"
        "```\n"
    )
    assert transcript_inconsistencies(composite), "the composite must be caught"

    honest = (
        "```\n"
        "  population: 170 files scanned, 33645 lines read\n"
        "  HIT a:1: token x\n"
        "  HIT a:2: token x\n"
        "  VERDICT: EXPOSED -- 2 hit(s), 0 unexplained exemption(s)\n"
        "```\n"
    )
    assert transcript_inconsistencies(honest) == []

    clean = "```\n  VERDICT: CLEAN -- 0 finding(s)\n```\n"
    assert transcript_inconsistencies(clean) == []

    lying_clean = "```\n  HIT a:1: token x\n  VERDICT: CLEAN\n```\n"
    assert transcript_inconsistencies(lying_clean), "CLEAN over a HIT must fire"

    # A block quoting no gate verdict is out of the population, not a pass.
    assert transcript_inconsistencies("```\nsome shell output\n```\n") == []
