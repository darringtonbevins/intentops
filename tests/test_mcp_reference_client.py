"""The S2 falsifier, as a test: a client that asks the gate and stops.

PURPOSE
    ``config/saddles.yaml`` grades the ``mcp-hosted`` saddle's S2 as
    ``computed_not_enforced``, and its ``open_falsifier`` says why: nobody had
    run a fenced, live test of a client HONOURING a deny returned by this
    server. These tests are that run, reduced to something that executes on
    every suite.

    The claim they support is deliberately small, and the tests are shaped to
    keep it small: ONE client, this one, in this repository, honours a deny.
    They say nothing about any third-party MCP host, and the record they back
    (``docs/falsifiers/S2-mcp-client-deny-2026-09-06.md``) says so in the same
    breath as its verdict.

    The trap being avoided is a vacuous pass. A client that never performs any
    effect honours every deny perfectly, so the deny test alone would prove
    nothing; the permitted-action test is its control, and both must hold.

WRITE MODEL
    None. Each test builds a node root and a minimal fixture repo under
    ``tmp_path``. The SERVER writes to its own ledger under that node root,
    which is thrown away with the temp directory.

BLIND SPOTS
    - One client. Nothing here observes a third-party host, and a result here
      must never be reported as one.
    - The server is spawned as a subprocess of this interpreter with an
      ``INTENTOPS_NODE_ROOT`` pointing into ``tmp_path``. That is a real
      transport over real pipes, but it is not a real deployment.
    - The verdicts are whatever the shipped gate returns for the sample tools.
      If the gate's own classification changed, these tests would follow it
      rather than catch it -- they assert the CLIENT's behaviour, not the
      gate's ruling.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

import pytest

from intentops_saddle_mcp import reference_client as refclient
from intentops_saddle_mcp.auth import mint

REPO = Path(__file__).resolve().parents[1]
RECORD = REPO / "docs" / "falsifiers" / "S2-mcp-client-deny-2026-09-06.md"


@pytest.fixture()
def wired(tmp_path: Path) -> Iterator[Tuple[str, Dict[str, str]]]:
    """A minted node, a fixture repo that admits this client, and its env."""
    node_root = tmp_path / "node"
    (node_root / ".intentops").mkdir(parents=True)
    repo_root = refclient._fixture_repo(tmp_path)
    token = mint(node_root)
    yield token, refclient._child_env(node_root, repo_root)


def _client(token: str, env: Dict[str, str], **kw: Any) -> refclient.ReferenceClient:
    return refclient.ReferenceClient(refclient.server_argv(), token=token,
                                     env=env, timeout_s=30.0, **kw)


# ---------------------------------------------------------------------------
# the falsifier itself
# ---------------------------------------------------------------------------


def test_the_client_honours_a_deny_and_never_performs_the_effect(
        wired: Tuple[str, Dict[str, str]]) -> None:
    token, env = wired
    performed: List[str] = []
    with _client(token, env) as client:
        client.initialize()
        outcome = client.act("Bash", {"command": "rm -rf /"},
                             effect=lambda: performed.append("ran"))
    assert outcome.decision != refclient.PERMITTING_DECISION, (
        "the sample action was not denied, so this proves nothing")
    assert outcome.performed is False
    assert outcome.honoured is True
    assert performed == [], "the client acted on an action it was denied"


def test_the_control_holds_a_permitted_action_really_does_run(
        wired: Tuple[str, Dict[str, str]]) -> None:
    """Without this, the test above is satisfied by a client that never acts."""
    token, env = wired
    performed: List[str] = []
    with _client(token, env) as client:
        client.initialize()
        outcome = client.act("Read", {"file_path": "README.md"},
                             effect=lambda: performed.append("ran"))
    assert outcome.decision == refclient.PERMITTING_DECISION, outcome.note
    assert outcome.performed is True
    assert performed == ["ran"]


def test_an_unreadable_verdict_is_not_permission(
        wired: Tuple[str, Dict[str, str]]) -> None:
    """Silence is not consent: no verdict means no effect."""
    import sys

    token, env = wired
    performed: List[str] = []
    dead = refclient.ReferenceClient([sys.executable, "-c", "pass"],
                                     token=token, env=env, timeout_s=5.0)
    with dead:
        outcome = dead.act("Read", {"file_path": "README.md"},
                           effect=lambda: performed.append("ran"))
    assert outcome.decision == "UNREADABLE"
    assert outcome.performed is False and performed == []


def test_a_stood_down_node_halts_and_the_client_stops(
        tmp_path: Path) -> None:
    """S5 outranks everything; the client must not act on a HALT either."""
    node_root = tmp_path / "node"
    (node_root / ".intentops").mkdir(parents=True)
    repo_root = refclient._fixture_repo(tmp_path)
    token = mint(node_root)
    (node_root / ".intentops" / "halt.marker").write_text(
        "held by the falsifier\n", encoding="utf-8")
    env = refclient._child_env(node_root, repo_root)

    performed: List[str] = []
    with _client(token, env) as client:
        try:
            client.initialize()
        except refclient.ClientError:
            # A stood-down node may refuse the handshake outright. Either way
            # the effect must not run, which is what is being asserted.
            assert performed == []
            return
        outcome = client.act("Read", {"file_path": "README.md"},
                             effect=lambda: performed.append("ran"))
    assert outcome.performed is False
    assert performed == []


# ---------------------------------------------------------------------------
# authentication and the allowlist, from the client's side of the pipe
# ---------------------------------------------------------------------------


def test_an_unlisted_client_name_is_refused_at_the_handshake(
        wired: Tuple[str, Dict[str, str]]) -> None:
    token, env = wired
    with refclient.ReferenceClient(refclient.server_argv(), token=token,
                                   client_name="somebody-else", env=env,
                                   timeout_s=30.0) as stranger:
        with pytest.raises(refclient.ClientError, match="refused the handshake"):
            stranger.initialize()


def test_a_wrong_credential_is_refused_not_downgraded(
        wired: Tuple[str, Dict[str, str]]) -> None:
    _token, env = wired
    with refclient.ReferenceClient(refclient.server_argv(),
                                   token="not-the-right-token", env=env,
                                   timeout_s=30.0) as impostor:
        with pytest.raises(refclient.ClientError):
            impostor.initialize()


def test_an_empty_credential_is_refused_before_a_process_starts() -> None:
    with pytest.raises(refclient.ClientError, match="no bearer token"):
        refclient.ReferenceClient(refclient.server_argv(), token="   ")


def test_the_reference_client_selftest_passes() -> None:
    ok, report = refclient.selftest()
    assert ok, report
    assert "0 failed" in report


# ---------------------------------------------------------------------------
# the record must claim exactly this much, and no more
# ---------------------------------------------------------------------------


def test_the_s2_record_exists_and_scopes_itself_to_this_client() -> None:
    assert RECORD.is_file(), (
        "the S2 falsifier has no recorded verdict; a falsifier that was run "
        "and not recorded is indistinguishable from one that was not run")
    text = RECORD.read_text(encoding="utf-8")
    assert len(re.findall(r"^\s*(?:#+\s*|\*\*)?VERDICT:", text, re.MULTILINE)) == 1
    lowered = text.lower()
    for phrase in ("reference client", "third-party", "candidate"):
        assert phrase in lowered, f"the record never mentions {phrase!r}"


def test_the_saddle_row_still_grades_s2_as_computed_not_enforced() -> None:
    """One reference client is not enforcement by any MCP host.

    This test exists to make an over-claim a build failure. If a future change
    promotes the row on the strength of this repository's own client, this
    fails and the promotion has to argue with the record instead of slipping
    through as a config edit.
    """
    import yaml

    data = yaml.safe_load((REPO / "config" / "saddles.yaml").read_text(
        encoding="utf-8"))
    row = next(h for h in data["hosts"] if h["id"] == "mcp-hosted")
    assert row["grade"] == "candidate"
    assert row["operations"]["S2"] == "computed_not_enforced"
    assert "third-party" in row["open_falsifier"].lower() or \
           "any specific MCP client" in row["open_falsifier"]
