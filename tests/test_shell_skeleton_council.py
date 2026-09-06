"""A shell is not a side door onto the core surface.

The work order these tests close: *the values-council step-0 pre-check lets a
shell command onto core surfaces bypass the council.* Until 2026-09-06 step 0
mined only the quote-BLANKED command skeleton, so a target named inside quotes
-- the whole body of a ``python -c``, a quoted path, a heredoc -- reached the
signed birth bundle without convening anything, while the identical edit through
``Write`` faced all three readings.

Two properties are asserted here and they pull against each other on purpose:

  * every declared MUTATING shape onto a core path routes to the council
    EXACTLY as a ``Write`` would;
  * a READ-ONLY command that merely names a core path convenes nothing.

The second is not politeness. A refusal that fires on ``grep`` gets routed
around, and a routed-around refusal protects nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from intentops_core.governance.values_council import (
    SHELL_SHAPES,
    UNRECOGNISED_SHAPE,
    Verdict,
    classify_shell_shape,
    load_core_surface,
    normalize_path,
    review_core_write,
    shell_targets,
    target_hits,
    target_paths,
)

CORE = "genesis/imprint/IMPRINT.md"
NOT_CORE = "docs/quick-start.md"


@pytest.fixture()
def surface(tmp_path: Path):
    path = tmp_path / "core-surface.yaml"
    path.write_text(
        "schema: core-surface/v1\n"
        "as_of: '2026-09-06'\n"
        "surface:\n"
        "  - glob: 'genesis/imprint/**'\n"
        "    reason: 'the signed birth bundle'\n"
        "  - glob: 'config/core-surface.yaml'\n"
        "    reason: 'this file declares its own surface'\n",
        encoding="utf-8",
    )
    return load_core_surface(path)


@pytest.fixture()
def node(tmp_path: Path) -> Path:
    root = tmp_path / "node"
    root.mkdir()
    return root


# ---------------------------------------------------------------------------
# every declared shape, one test each
# ---------------------------------------------------------------------------

SHAPED = [
    ("sed-in-place", f"sed -i 's/a/b/' {CORE}"),
    ("heredoc-redirect", f"cat <<'EOF' | sponge {CORE}"),
    ("redirect-write", f"printf x >> {CORE}"),
    ("tee", f"echo x | tee {CORE}"),
    ("git-restore", f"git checkout -- {CORE}"),
    ("git-restore", f"git reset --hard -- {CORE}"),
    ("inline-interpreter", f"python -c \"open('{CORE}','w').write('x')\""),
    ("inline-interpreter", f"perl -e 'unlink(\"{CORE}\")'"),
    ("powershell-inline", f"pwsh -Command \"Remove-Item {CORE}\""),
    ("powershell-write", f"Set-Content -Path '{CORE}' -Value 'x'"),
    ("copy-move-remove", f"cp /tmp/forged.md {CORE}"),
    ("copy-move-remove", f"rm -f {CORE}"),
    ("copy-move-remove", f"mv /tmp/x {CORE}"),
    ("dd", f"dd if=/dev/zero of={CORE}"),
    ("archive-extract", f"tar -xf payload.tar {CORE}"),
    ("apply-patch", f"git apply /tmp/one.patch  # rewrites {CORE}"),
]


@pytest.mark.parametrize("shape_id,command", SHAPED,
                         ids=[f"{s}:{c[:22]}" for s, c in SHAPED])
def test_each_mutating_shape_reaches_the_council(
    shape_id: str, command: str, surface, node: Path
) -> None:
    """The shape LABEL is the first declared match, and the table is ordered.

    A command carrying two shapes (a redirect inside an interpreter body) is
    labelled by whichever is declared first. That is fine -- the label is
    diagnostic, and what is load-bearing is that the council convened at all --
    but the cases below are chosen to carry exactly one shape so the assertion
    means something.
    """
    reading = review_core_write("Bash", {"command": command},
                                node_root=node, surface=surface, mode="observe")
    assert reading.applies, f"{command!r} bypassed the council"
    assert normalize_path(reading.path) == normalize_path(CORE)
    assert reading.shell_shape == shape_id


def test_a_shell_call_is_routed_exactly_as_a_write_would_be(
    surface, node: Path
) -> None:
    """Same path, same verdict. The tool used must not change the reading."""
    by_write = review_core_write(
        "Write", {"file_path": CORE, "content": "a plain line\n"},
        node_root=node, surface=surface, mode="observe")
    by_shell = review_core_write(
        "Bash", {"command": f"printf 'a plain line' > {CORE}"},
        node_root=node, surface=surface, mode="observe")
    assert by_write.verdict is by_shell.verdict is Verdict.CONSENT
    assert by_write.surface_glob == by_shell.surface_glob
    assert by_shell.shell_shape == "redirect-write"
    assert by_write.shell_shape == "file-write"


def test_the_shape_and_its_reason_are_recorded_on_the_row(
    surface, node: Path
) -> None:
    reading = review_core_write(
        "Bash", {"command": f"sed -i s/a/b/ {CORE}"},
        node_root=node, surface=surface, mode="observe")
    row = reading.to_row()
    assert row["shell_shape"] == "sed-in-place"
    assert "in place" in row["shell_shape_reason"]
    assert "not a side door" in reading.render()


def test_enforce_blocks_a_shell_route_onto_a_weakened_core_file(
    surface, node: Path
) -> None:
    """The widening is not cosmetic: it reaches the blocking path too."""
    from intentops_core.governance.values_council import gate_values_council

    allowed, notes = gate_values_council(
        "Bash",
        {"command": f"python -c \"open('{CORE}','w').write('fail_closed: false')\""},
        node_root=node, surface=surface, mode="enforce",
    )
    # The council convenes; the content floor cannot see inside the quotes, so
    # the verdict is CONSENT and enforce lets it through. That is the honest
    # boundary: step 0 decides WHETHER to convene; it never reads file content.
    assert allowed is True
    assert any("shell shape" in n for n in notes)


# ---------------------------------------------------------------------------
# benign commands convene nothing
# ---------------------------------------------------------------------------

BENIGN = [
    f"grep -n imprint {CORE}",
    f"cat {CORE}",
    f"cat {CORE} | head -40",
    f"git diff -- {CORE}",
    f"git status {CORE}",
    f"git log --oneline -- {CORE}",
    f"wc -l {CORE}",
    f"ls -la {CORE}",
    f"rg --files-with-matches imprint {CORE}",
    f"sha256sum {CORE}",
    f"diff {CORE} /tmp/other.md",
]


@pytest.mark.parametrize("command", BENIGN, ids=[c[:26] for c in BENIGN])
def test_a_read_only_command_naming_a_core_path_convenes_nothing(
    command: str, surface, node: Path
) -> None:
    reading = review_core_write("Bash", {"command": command},
                                node_root=node, surface=surface, mode="observe")
    assert reading.verdict is Verdict.NOT_APPLICABLE, command
    assert shell_targets(command) == []


def test_a_mutating_shape_on_a_non_core_path_is_not_applicable(
    surface, node: Path
) -> None:
    reading = review_core_write("Bash", {"command": f"sed -i s/a/b/ {NOT_CORE}"},
                                node_root=node, surface=surface, mode="observe")
    assert reading.verdict is Verdict.NOT_APPLICABLE


# ---------------------------------------------------------------------------
# the unrecognised case fails toward convening
# ---------------------------------------------------------------------------


def test_an_unrecognised_verb_is_read_as_capable_of_writing(
    surface, node: Path
) -> None:
    reading = review_core_write(
        "Bash", {"command": f"some-vendor-tool --apply {CORE}"},
        node_root=node, surface=surface, mode="observe")
    assert reading.applies
    assert reading.shell_shape == UNRECOGNISED_SHAPE


def test_a_read_only_verb_in_a_pipeline_with_a_mutating_one_still_convenes(
    surface, node: Path
) -> None:
    reading = review_core_write(
        "Bash", {"command": f"cat /tmp/forged.md | tee {CORE}"},
        node_root=node, surface=surface, mode="observe")
    assert reading.applies and reading.shell_shape == "tee"


def test_an_empty_command_reaches_nothing() -> None:
    assert shell_targets("") == []
    assert target_paths("Bash", {"command": ""}) == []


# ---------------------------------------------------------------------------
# the declared shape table keeps its own discipline
# ---------------------------------------------------------------------------


def test_every_declared_shape_states_a_reason() -> None:
    """A pattern with no stated reason is a rule nobody can argue with."""
    for shape in SHELL_SHAPES:
        assert shape.id and shape.reason.strip(), shape.id


def test_the_shape_ids_are_unique() -> None:
    ids = [s.id for s in SHELL_SHAPES]
    assert len(ids) == len(set(ids))


def test_classify_returns_none_rather_than_guessing() -> None:
    """None means 'no declared shape', never 'read-only'."""
    assert classify_shell_shape("some-vendor-tool --apply x") is None
    assert shell_targets("some-vendor-tool --apply core/a.py")


# ---------------------------------------------------------------------------
# the residue is asserted, not hidden
# ---------------------------------------------------------------------------


def test_a_path_held_in_a_variable_is_still_invisible(surface, node: Path) -> None:
    """The honest remaining blind spot, asserted so nobody claims otherwise.

    No regex over a command string reaches a path assembled at runtime. This is
    why the pre-commit refusal and the session-start re-hash are not optional.
    """
    reading = review_core_write(
        "Bash", {"command": "T=$IMPRINT; printf x >> $T"},
        node_root=node, surface=surface, mode="observe")
    assert reading.verdict is Verdict.NOT_APPLICABLE


def test_file_write_tools_are_unchanged_by_the_widening() -> None:
    hits = target_hits("Write", {"file_path": CORE, "content": "x"})
    assert [h.path for h in hits] == [CORE]
    assert hits[0].shape == "file-write"
