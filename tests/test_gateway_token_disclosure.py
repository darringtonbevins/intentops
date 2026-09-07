"""The gateway token is minted by a ceremony, or it is not minted.

PURPOSE
    Pin the one property the whole design turns on: this node never ends up
    holding a credential nobody was shown. Every path that could produce one
    -- a dry run, an unattended terminal, an operator who says nothing -- is
    asserted to leave the record ABSENT, and the one path that produces a
    token is asserted to have printed it exactly once.

    The negative cases matter more than the positive one here. A test suite
    that only proved "consent mints a working token" would pass just as
    happily over a build that minted silently at birth, which is the exposure
    this leg exists to close.

WRITE MODEL
    None. Every test writes under ``tmp_path``.

BLIND SPOTS
    * The TTY is SIMULATED (``attended=True`` plus an injected ``ask``). These
      tests prove the ceremony refuses when it is told it is unattended; they
      cannot prove the caller computes attendedness correctly on a real
      Windows console. ``machine._attended`` is that caller and has its own
      coverage in the genesis suite.
    * Nothing here observes a real terminal, so "printed once" means "the
      injected sink received exactly one payload".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from intentops_core.genesis import aliveness as aliveness_mod
from intentops_core.genesis import machine as machine_mod
from intentops_core.genesis import organs as organs_mod
from intentops_core.genesis import token_ceremony as ceremony
from intentops_core.trust import bearer
from intentops_gateway import cli as gateway_cli
from intentops_gateway import tokens as gateway_tokens

REPO = Path(__file__).resolve().parents[1]

#: Obviously not a credential. A realistic-looking value in a committed test
#: is how a fixture becomes a finding in a secret scanner.
PLANTED = "ceremony-test-token-not-a-real-credential"


# ---------------------------------------------------------------------------
# the primitive
# ---------------------------------------------------------------------------


def test_the_bearer_primitive_selftest_passes() -> None:
    ok, report = bearer.selftest()
    assert ok, report
    assert "0 failed" in report


def test_the_ceremony_selftest_passes() -> None:
    ok, report = ceremony.selftest()
    assert ok, report


def test_the_core_and_the_gateway_agree_on_one_record() -> None:
    """Two constants that agree until one is edited are two constants."""
    assert gateway_tokens.RECORD_RELPATH is bearer.GATEWAY_TOKEN_RELPATH
    assert gateway_tokens.SCHEMA == bearer.GATEWAY_TOKEN_SCHEMA
    organ = next(o for o in organs_mod.BIRTH_ORGANS if o.id == "gateway-token")
    assert organ.relpath == bearer.GATEWAY_TOKEN_RELPATH.as_posix()
    assert organ.present_at_birth is False


def test_the_two_authenticated_surfaces_digest_identically() -> None:
    """The gateway and the MCP saddle must never disagree about a digest."""
    from intentops_saddle_mcp import auth as saddle_auth

    assert saddle_auth.digest_of(PLANTED) == bearer.digest_of(PLANTED)


# ---------------------------------------------------------------------------
# the ceremony's refusals
# ---------------------------------------------------------------------------


def test_a_dry_run_never_mints(tmp_path: Path) -> None:
    shown: list = []
    result = ceremony.mint_disclosed(tmp_path, dry_run=True, attended=True,
                                     ask=lambda _p: "y", out=shown.append)
    assert result.minted is False
    assert not ceremony.record_path(tmp_path).exists()
    assert not shown
    assert result.certificate_line() == "gateway token: not minted (dry run)"


def test_a_non_tty_refuses_even_with_a_consenting_prompt(tmp_path: Path) -> None:
    """The load-bearing refusal: consent from an automated context is not consent."""
    shown: list = []
    result = ceremony.mint_disclosed(tmp_path, attended=False,
                                     ask=lambda _p: "yes", out=shown.append)
    assert result.minted is False
    assert not ceremony.record_path(tmp_path).exists()
    assert not shown
    assert "attended" in result.reason


def test_silence_and_refusal_are_both_a_no(tmp_path: Path) -> None:
    for answer in ("", "  ", "n", "no", "later", "Y E S"):
        result = ceremony.mint_disclosed(tmp_path, attended=True,
                                         ask=lambda _p, a=answer: a,
                                         out=lambda _m: None)
        assert result.minted is False, answer
        assert not ceremony.record_path(tmp_path).exists()


def test_an_eof_at_the_prompt_is_a_no_and_not_a_crash(tmp_path: Path) -> None:
    def ask(_prompt: str) -> str:
        raise EOFError

    result = ceremony.mint_disclosed(tmp_path, attended=True, ask=ask,
                                     out=lambda _m: None)
    assert result.minted is False
    assert not ceremony.record_path(tmp_path).exists()


def test_no_prompt_means_no_mint(tmp_path: Path) -> None:
    result = ceremony.mint_disclosed(tmp_path, attended=True, ask=None,
                                     out=lambda _m: None)
    assert result.minted is False


# ---------------------------------------------------------------------------
# the ceremony's one positive path
# ---------------------------------------------------------------------------


def test_a_simulated_tty_mint_prints_once_and_stores_a_digest(tmp_path: Path) -> None:
    shown: list = []
    prompts: list = []

    def ask(prompt: str) -> str:
        prompts.append(prompt)
        return "y"

    result = ceremony.mint_disclosed(tmp_path, attended=True, ask=ask,
                                     out=shown.append, token=PLANTED)

    assert result.minted is True
    assert len(prompts) == 1 and "[y/N]" in prompts[0]
    assert len(shown) == 1, "the plaintext must be shown exactly once"
    assert PLANTED in shown[0]
    assert "SHOWN ONCE" in shown[0]

    raw = ceremony.record_path(tmp_path).read_text(encoding="utf-8")
    assert PLANTED not in raw, "the plaintext reached the disk"
    record = json.loads(raw)
    assert record["schema"] == bearer.GATEWAY_TOKEN_SCHEMA
    assert record["algorithm"] == "sha256"
    assert record["disclosed_at"] == result.disclosed_at
    assert record["rotation"] == 1


def test_the_stored_digest_verifies_the_disclosed_token(tmp_path: Path) -> None:
    ceremony.mint_disclosed(tmp_path, attended=True, ask=lambda _p: "y",
                            out=lambda _m: None, token=PLANTED)
    assert gateway_tokens.verify(tmp_path, PLANTED) is True
    assert gateway_tokens.verify(tmp_path, PLANTED + "x") is False
    assert gateway_tokens.verify(tmp_path, None) is False
    assert gateway_tokens.load_record(tmp_path).disclosed_at is not None


def test_a_rotation_after_the_ceremony_supersedes_it(tmp_path: Path) -> None:
    ceremony.mint_disclosed(tmp_path, attended=True, ask=lambda _p: "y",
                            out=lambda _m: None, token=PLANTED)
    rotated = gateway_tokens.mint(tmp_path)
    assert gateway_tokens.verify(tmp_path, PLANTED) is False
    assert gateway_tokens.verify(tmp_path, rotated) is True
    assert gateway_tokens.load_record(tmp_path).rotation == 2


# ---------------------------------------------------------------------------
# the certificate line
# ---------------------------------------------------------------------------


def test_the_certificate_line_distinguishes_all_three_states(tmp_path: Path) -> None:
    assert ceremony.certificate_line_for(tmp_path) == "gateway token: not minted"

    # minted out of band: rotate does disclose, so drop the stamp by hand to
    # model a mint that showed nobody anything.
    gateway_tokens.mint(tmp_path, token=PLANTED)
    path = ceremony.record_path(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["disclosed_at"] is None
    line = ceremony.certificate_line_for(tmp_path)
    assert line.startswith("gateway token: minted at ")
    assert "not disclosed at genesis" in line

    ceremony.mint_disclosed(tmp_path, attended=True, ask=lambda _p: "y",
                            out=lambda _m: None, token=PLANTED)
    assert ceremony.certificate_line_for(tmp_path).startswith(
        "gateway token: minted/disclosed at ")


def test_the_birth_certificate_carries_the_line(tmp_path: Path) -> None:
    reading = aliveness_mod.Reading(as_of="2026-09-06T00:00:00Z")
    reading.gateway_token = "gateway token: not minted"
    assert "gateway token: not minted" in aliveness_mod.render_certificate(reading)

    reading.gateway_token = "gateway token: minted/disclosed at 2026-09-06T00:00:00+00:00"
    cert = aliveness_mod.render_certificate(reading)
    assert "minted/disclosed at 2026-09-06T00:00:00+00:00" in cert
    # Stated, never scored: an unminted token must not degrade the verdict.
    assert "status: healthy" not in cert


def test_take_reading_reads_the_record_off_disk(tmp_path: Path) -> None:
    node = tmp_path / "node"
    node.mkdir()
    reading = aliveness_mod.take_reading(node, repo_root=REPO, dry_run=True)
    assert reading.gateway_token == "gateway token: not minted"
    assert "gateway token: not minted" in aliveness_mod.render_certificate(reading)

    ceremony.mint_disclosed(node, attended=True, ask=lambda _p: "y",
                            out=lambda _m: None, token=PLANTED)
    reading = aliveness_mod.take_reading(node, repo_root=REPO, dry_run=True)
    assert reading.gateway_token.startswith("gateway token: minted/disclosed at ")
    assert reading.to_row()["gateway_token"] == reading.gateway_token


# ---------------------------------------------------------------------------
# G2 wiring
# ---------------------------------------------------------------------------


def test_g2_records_the_outcome_and_a_dry_run_leaves_it_absent(tmp_path: Path) -> None:
    run = machine_mod.run_genesis(
        REPO, tmp_path / "node", identity_repo="new", dry_run=True,
        allow_unsigned_dev=True, saddle="claudecode",
        isatty=lambda: False, out=lambda _m: None)
    assert not run.halted, f"{run.halt_reason} / {run.remedy}"
    g2 = next(r for r in run.results if r.state == "G2")
    assert g2.evidence["gateway_token"] == {
        "minted": False, "reason": "dry run", "disclosed_at": None,
        "rotation": None}
    assert "gateway token: not minted (dry run)" in g2.notes
    assert not ceremony.record_path(tmp_path / "node").exists()


def test_g2_calls_the_ceremony_and_never_mints_unattended(tmp_path: Path,
                                                          monkeypatch) -> None:
    """A real (non-dry) G2 on an unattended terminal mints nothing.

    The phase halts at its own storage gate long before this, which is the
    point: even if that gate were ever relaxed, the ceremony refuses on its
    own rather than trusting an upstream check.
    """
    seen: list = []

    def spy(node_root, **kwargs):
        seen.append(kwargs)
        return ceremony.mint_disclosed(node_root, **kwargs)

    monkeypatch.setattr(machine_mod.token_ceremony_mod, "mint_disclosed", spy)
    ctx = machine_mod.GenesisContext(
        repo_root=REPO, node_root=tmp_path / "node", dry_run=False,
        isatty=lambda: False, out=lambda _m: None)
    with pytest.raises(machine_mod.OperatorGateRequired):
        machine_mod.g2_keys(ctx)
    assert not ceremony.record_path(tmp_path / "node").exists()


# ---------------------------------------------------------------------------
# the CLI
# ---------------------------------------------------------------------------


def test_show_digest_is_a_registered_verb_and_rotate_survives() -> None:
    parser = gateway_cli.build_parser()
    args = parser.parse_args(["token", "show-digest"])
    assert args.token_command == "show-digest"
    args = parser.parse_args(["token", "rotate", "--yes"])
    assert args.token_command == "rotate" and args.yes is True


def test_show_digest_refuses_cleanly_when_nothing_was_minted(tmp_path: Path,
                                                             capsys) -> None:
    class _Node:
        root_source = "test"

    class _Ctx:
        node_root = tmp_path
        node = _Node()

    args = gateway_cli.argparse.Namespace(json=False)
    assert gateway_cli._cmd_token_show_digest(args, _Ctx()) == 1
    err = capsys.readouterr().err
    assert "never minted" in err and "rotate --yes" in err


def test_show_digest_prints_the_digest_and_never_the_token(tmp_path: Path,
                                                           capsys) -> None:
    class _Node:
        root_source = "test"

    class _Ctx:
        node_root = tmp_path
        node = _Node()

    ceremony.mint_disclosed(tmp_path, attended=True, ask=lambda _p: "y",
                            out=lambda _m: None, token=PLANTED)
    args = gateway_cli.argparse.Namespace(json=True)
    assert gateway_cli._cmd_token_show_digest(args, _Ctx()) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["digest"] == bearer.digest_of(PLANTED)
    assert payload["disclosed_at"]
    assert PLANTED not in captured.out and PLANTED not in captured.err


# ---------------------------------------------------------------------------
# the gate translation: "could not be asked" must not become a HALT
# ---------------------------------------------------------------------------


def test_an_unanswerable_token_gate_is_an_eof_not_a_halt(tmp_path: Path) -> None:
    """The seam that made two documents agree with each other and not the code.

    ``g2_keys``'s own comment and the ceremony's docstring both say an
    undisclosable token is DECLINED and recorded, never a halt. ``_gate``
    raises ``OperatorGateRequired``; ``mint_disclosed`` catches ``EOFError``
    and nothing else. So an attended-looking terminal that reaches EOF -- the
    Windows ``NUL`` device passes ``isatty()`` and does exactly that -- halted
    G2 over an OPTIONAL credential. The translation is what closes it.
    """
    ctx = machine_mod.GenesisContext(
        repo_root=REPO, node_root=tmp_path / "node", dry_run=False,
        isatty=lambda: False, out=lambda _m: None)
    with pytest.raises(EOFError):
        machine_mod._ask_about_the_token(ctx, "mint one?")


def test_the_ceremony_records_the_unanswerable_gate_as_its_own_phrase(
        tmp_path: Path) -> None:
    """"Declined" and "could not be asked" stay two different records."""
    node = tmp_path / "node"
    ctx = machine_mod.GenesisContext(
        repo_root=REPO, node_root=node, dry_run=False,
        isatty=lambda: False, out=lambda _m: None)

    disclosure = ceremony.mint_disclosed(
        node, dry_run=False, attended=True,
        ask=lambda prompt: machine_mod._ask_about_the_token(ctx, prompt),
        out=lambda _m: None)
    assert not disclosure.minted
    assert disclosure.reason == "the operator gate ended before an answer arrived"
    assert not ceremony.record_path(node).exists()

    declined = ceremony.mint_disclosed(
        node, dry_run=False, attended=True, ask=lambda _p: "n",
        out=lambda _m: None)
    assert declined.reason == "the operator declined at the genesis ceremony"
    assert declined.reason != disclosure.reason


def test_the_translation_is_load_bearing_not_decorative(tmp_path: Path) -> None:
    """Positive control: without it, the ceremony's contract does NOT hold.

    A detector with no proof it can fail is indistinguishable from a broken
    one, and the same is true of a translation. This asserts the raw gate
    exception really does escape ``mint_disclosed`` -- so the wrapper above is
    the thing holding the contract up, not a comment about one.
    """
    def raw_gate(_prompt: str) -> str:
        raise machine_mod.OperatorGateRequired("stdin is not attended")

    with pytest.raises(machine_mod.OperatorGateRequired):
        ceremony.mint_disclosed(
            tmp_path / "node", dry_run=False, attended=True, ask=raw_gate,
            out=lambda _m: None)
