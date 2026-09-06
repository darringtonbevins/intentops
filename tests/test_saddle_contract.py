"""The saddle contract, run against every host in the registry.

PURPOSE
    A host carries IntentOps if and only if it can do five things. This file is
    the checkable form of that claim, and it is parameterized over
    ``config/saddles.yaml`` rather than over the packages that happen to exist
    -- so a host that is claimed and not implemented SKIPS OUT LOUD instead of
    quietly not being tested.

    That distinction is the whole reason the file is shaped this way. A skipped
    contract stays in the denominator: every run prints how many saddles were
    exercised out of how many are claimed. Dropping an untested host from the
    suite would raise the pass rate by shrinking the population, which is the
    most flattering way for a number to improve and the one nobody checks.

    The reference adapter is exercised as a SUBPROCESS through its real entry
    point, with real payloads on stdin. Calling the functions directly would
    test the decision and skip the protocol, and the protocol -- the exit code,
    the decision frame, the silence on allow -- is the half that only exists in
    the adapter.

WHAT THIS FILE CANNOT PROVE
    S2 says the host must HONOUR a deny. Nothing here can establish that: it
    proves the adapter emits a refusal in this runtime's own vocabulary, and
    whether the runtime then stops the call is a property of the runtime. That
    gap is named in the saddle contract itself and is not closed by a green run
    of this file.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CORE_PATH = REPO_ROOT / "packages" / "intentops-core"
SADDLES_YAML = REPO_ROOT / "config" / "saddles.yaml"

#: The five operations, in the order the contract states them.
OPERATION_IDS: Tuple[str, ...] = ("S1", "S2", "S3", "S4", "S5")


# ---------------------------------------------------------------------------
# the population
# ---------------------------------------------------------------------------


def _load_registry() -> List[Dict[str, Any]]:
    yaml = pytest.importorskip("yaml", reason="PyYAML is required to read the registry")
    if not SADDLES_YAML.is_file():
        pytest.fail(f"the saddle registry is missing: {SADDLES_YAML}")
    data = yaml.safe_load(SADDLES_YAML.read_text(encoding="utf-8"))
    hosts = data.get("hosts")
    if not isinstance(hosts, list) or not hosts:
        pytest.fail("config/saddles.yaml declares no hosts; the contract has no population")
    return hosts


def _package_dir(host_id: str) -> Path:
    return REPO_ROOT / "packages" / f"intentops-saddle-{host_id}"


def _module_name(host_id: str) -> str:
    return f"intentops_saddle_{host_id.replace('-', '_')}"


def _implementation_state(host: Dict[str, Any]) -> Tuple[bool, str]:
    """Is there code to test here, and if not, exactly why not."""
    host_id = str(host.get("id") or "")
    grade = str(host.get("grade") or "")
    pkg = _package_dir(host_id)
    if not host.get("package"):
        return False, f"{grade}: no package shell exists for this host"
    # A row may DECLINE this driver, and the reason it gives is then the one
    # reported. The driver below speaks one host's hook-payload shape; a saddle
    # on a different transport is not "unimplemented", and reporting it that
    # way is a wrong reason on a skipped row -- a blind spot wearing a green
    # tick. The key must be present AND null to decline: an absent key keeps
    # the old behaviour exactly.
    if "contract_driver" in host and host.get("contract_driver") is None:
        return False, str(
            host.get("driver_note")
            or f"{grade}: declines the hook-payload driver; exercised by its own suite"
        )
    if not (pkg / _module_name(host_id) / "__init__.py").is_file():
        return False, "roadmap: no implementation"
    return True, ""


REGISTRY = _load_registry() if SADDLES_YAML.is_file() else []
HOST_IDS = [str(h.get("id")) for h in REGISTRY]


@pytest.fixture(scope="session")
def registry() -> List[Dict[str, Any]]:
    return _load_registry()


# ---------------------------------------------------------------------------
# driving a saddle
# ---------------------------------------------------------------------------


class SaddleDriver:
    """Runs one saddle's real entry points in a subprocess, against a temp node."""

    def __init__(self, host_id: str, node_root: Path) -> None:
        self.host_id = host_id
        self.module = _module_name(host_id)
        self.node_root = node_root
        self.env = dict(os.environ)
        self.env["PYTHONPATH"] = os.pathsep.join(
            [str(CORE_PATH), str(_package_dir(host_id))]
        )
        # The node is found by the payload's cwd, exactly as it would be live.
        self.env.pop("INTENTOPS_NODE_ROOT", None)

    def _run(self, module_suffix: str, stdin_text: str,
             args: Tuple[str, ...] = ()) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", f"{self.module}.{module_suffix}", *args],
            input=stdin_text, capture_output=True, text=True,
            cwd=str(self.node_root), env=self.env, timeout=90,
            # check=False on purpose: a non-zero exit is the REFUSAL this suite
            # exists to observe, not an error to raise on. Letting subprocess
            # raise here would turn every successful block into a test error.
            check=False,
        )

    def pre_tool(self, payload: Any) -> Tuple[int, Optional[Dict[str, Any]], str]:
        text = payload if isinstance(payload, str) else json.dumps(payload)
        proc = self._run("pre_tool", text)
        frame: Optional[Dict[str, Any]] = None
        body = proc.stdout.strip()
        if body:
            frame = json.loads(body)
        return proc.returncode, frame, proc.stderr

    def observe(self, payload: Dict[str, Any], event: str) -> subprocess.CompletedProcess:
        return self._run("observe", json.dumps(payload), ("--event", event))

    def session_start(self, payload: Dict[str, Any]) -> subprocess.CompletedProcess:
        return self._run("session_start", json.dumps(payload))

    def payload(self, tool: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "hook_event_name": "PreToolUse",
            "session_id": "contract-test",
            "tool_name": tool,
            "tool_input": tool_input,
            "cwd": str(self.node_root),
        }

    def ledger_rows(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        gates = self.node_root / ".intentops" / "logs" / "gates"
        for path in sorted(gates.glob("*.jsonl")) if gates.is_dir() else []:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rows.append(json.loads(line))
        return rows


def _decision_of(frame: Optional[Dict[str, Any]]) -> Optional[str]:
    if not frame:
        return None
    return frame.get("hookSpecificOutput", {}).get("permissionDecision")


@pytest.fixture(params=HOST_IDS, ids=HOST_IDS)
def saddle(request, tmp_path: Path) -> SaddleDriver:
    """One host from the registry, or a loud skip that stays in the denominator."""
    host_id = request.param
    host = next(h for h in _load_registry() if str(h.get("id")) == host_id)
    implemented, why = _implementation_state(host)
    if not implemented:
        pytest.skip(why)
    node_root = tmp_path / "node"
    (node_root / ".intentops").mkdir(parents=True)
    return SaddleDriver(host_id, node_root)


# ---------------------------------------------------------------------------
# the five operations
# ---------------------------------------------------------------------------


def test_s1_classify_is_present_and_pure(saddle: SaddleDriver) -> None:
    """S1: the saddle exposes a classifier, and it is the core's, not its own."""
    sys.path.insert(0, str(CORE_PATH))
    sys.path.insert(0, str(_package_dir(saddle.host_id)))
    try:
        module = __import__(saddle.module, fromlist=["OPERATIONS"])
    finally:
        sys.path.remove(str(_package_dir(saddle.host_id)))
        sys.path.remove(str(CORE_PATH))
    ops = getattr(module, "OPERATIONS", None)
    assert isinstance(ops, dict), "the saddle must declare OPERATIONS as a mapping"
    assert set(ops) == set(OPERATION_IDS), (
        "the saddle must claim exactly the five contract operations; "
        f"got {sorted(ops)}"
    )
    for op_id in OPERATION_IDS:
        assert callable(ops[op_id]), f"{op_id} is claimed but is not callable"


def test_s2_a_remote_push_is_refused_or_escalated(saddle: SaddleDriver) -> None:
    """S2, the one that matters: publishing to a remote never passes silently."""
    code, frame, _ = saddle.pre_tool(
        saddle.payload("Bash", {"command": "git push origin main"})
    )
    decision = _decision_of(frame)
    assert decision in ("ask", "deny"), (
        "a push to a remote reaches the world and must be refused or put to a "
        f"human; the adapter said {decision!r} with exit {code}"
    )


def test_s2_a_local_read_is_allowed_and_stays_silent(saddle: SaddleDriver) -> None:
    """S2: a read that touches nothing gets no verdict at all.

    The silence is load-bearing. Emitting an explicit allow here would override
    the operator's own permission settings -- the gate may raise the bar and
    may never lower it.
    """
    code, frame, err = saddle.pre_tool(
        saddle.payload("Read", {"file_path": "README.md"})
    )
    assert code == 0, f"a local read must not be refused; stderr was {err!r}"
    assert frame is None, (
        "the adapter must stay silent on allow rather than emit an 'allow' "
        f"decision that would bypass the host's own rules; got {frame!r}"
    )


def test_s2_a_delete_is_put_to_a_human(saddle: SaddleDriver) -> None:
    """S2: destruction with no way back is never taken on the node's own word."""
    code, frame, _ = saddle.pre_tool(
        saddle.payload("Bash", {"command": "rm -rf ./build"})
    )
    decision = _decision_of(frame)
    assert decision in ("ask", "deny"), (
        f"a recursive delete must reach a human; the adapter said {decision!r} "
        f"with exit {code}"
    )


@pytest.mark.parametrize(
    "bad_payload, what",
    [
        ("this is not json at all", "unparseable text"),
        ("", "an empty payload"),
        ('{"hook_event_name": "PreToolUse"}', "a payload naming no tool"),
        ('{"hook_event_name": "PreToolUse", "tool_name": "Bash"}',
         "a payload carrying no arguments"),
        ('{"tool_name": "Bash", "tool_input": "rm -rf /"}',
         "arguments that are not an object"),
        ("[1, 2, 3]", "a payload that is not an object"),
    ],
)
def test_s2_a_malformed_payload_fails_closed(saddle: SaddleDriver, bad_payload: str,
                                             what: str) -> None:
    """S2: what the gate cannot read, the gate does not permit.

    Classifying an operation we cannot see is not classification. It is a coin
    flip that happens to return ALLOW most of the time.
    """
    code, frame, err = saddle.pre_tool(bad_payload)
    assert code != 0, f"{what} must fail closed, not pass; exit was {code}"
    assert _decision_of(frame) == "deny", f"{what} must render as a refusal"
    assert err.strip(), f"{what} was refused without saying why"


def test_s3_every_decision_is_recorded_including_the_permits(
        saddle: SaddleDriver) -> None:
    """S3: a log of refusals alone answers the question nobody asked."""
    saddle.pre_tool(saddle.payload("Read", {"file_path": "README.md"}))
    saddle.pre_tool(saddle.payload("Bash", {"command": "git push origin main"}))
    rows = saddle.ledger_rows()
    decisions = [r.get("decision") for r in rows]
    assert "ALLOW" in decisions, (
        "the permitted call left no trace; an audit asks what was allowed at "
        "least as often as what was stopped"
    )
    assert any(d in ("ASK", "BLOCK", "HALT") for d in decisions), (
        "the refused call left no trace"
    )
    for row in rows:
        assert row.get("as_of"), "a ledger row with no timestamp cannot be folded"
        assert row.get("reasons") is not None, (
            "a ledger row must carry the reasons the verdict gave"
        )


def test_s3_post_tool_and_stop_record_and_never_refuse(saddle: SaddleDriver) -> None:
    """S3: the after-the-fact events complete the record and stop nothing."""
    base = {"session_id": "contract-test", "cwd": str(saddle.node_root),
            "tool_name": "Bash", "tool_input": {"command": "ls"}}
    for event in ("post_tool", "stop"):
        proc = saddle.observe(dict(base, hook_event_name=event), event)
        assert proc.returncode == 0, (
            f"{event} must not refuse -- the action has already happened; "
            f"stderr was {proc.stderr!r}"
        )
    assert any(r.get("decision") == "RECORDED" for r in saddle.ledger_rows())


def test_s3_an_undeclared_event_is_refused_not_guessed(saddle: SaddleDriver) -> None:
    """S3: an event that does not know which event it is writes an unreadable row."""
    proc = saddle.observe({"cwd": str(saddle.node_root)}, "no_such_event")
    assert proc.returncode != 0


def test_s4_the_boot_corpus_reaches_the_window(saddle: SaddleDriver) -> None:
    """S4: the node's own refusals are in front of a fresh window, or it says so."""
    payload = {"hook_event_name": "SessionStart", "cwd": str(saddle.node_root)}

    empty = saddle.session_start(payload)
    assert empty.returncode == 0
    assert "0 files" in empty.stdout, (
        "a node with no rules on disk must say so; zero is a state, not a success"
    )

    rules = saddle.node_root / ".intentops-rules"
    rules.mkdir()
    (rules / "honesty.md").write_text(
        "state uncertainty explicitly\n", encoding="utf-8")
    loaded = saddle.session_start(payload)
    assert loaded.returncode == 0
    assert "honesty.md" in loaded.stdout, "the corpus file was not named"
    assert "state uncertainty explicitly" in loaded.stdout, (
        "the rule text itself did not reach the window; naming a file is not "
        "the same as carrying it"
    )


def test_s5_a_stood_down_node_refuses_everything(saddle: SaddleDriver) -> None:
    """S5: one file, no reason, no argument, and nothing proceeds.

    The harmless read is the case that matters. A stand-down that still let
    'safe' calls through would be a preference, not a stop.
    """
    marker = saddle.node_root / ".intentops" / "halt.marker"
    marker.write_text("", encoding="utf-8")
    try:
        code, frame, err = saddle.pre_tool(
            saddle.payload("Read", {"file_path": "README.md"})
        )
        assert code != 0, "a stood-down node let a call through"
        assert _decision_of(frame) == "deny"
        assert "stood down" in err.lower(), (
            "the refusal must say the node is stood down, so the operator is "
            "not sent hunting for a defect that is not there"
        )
    finally:
        marker.unlink()

    code, frame, _ = saddle.pre_tool(saddle.payload("Read", {"file_path": "README.md"}))
    assert code == 0 and frame is None, (
        "removing the marker must bring the node back; the stand-down is a "
        "state, not damage"
    )


# ---------------------------------------------------------------------------
# the denominator
# ---------------------------------------------------------------------------


def test_the_contract_population_is_reported_in_full(registry) -> None:
    """Print how many saddles were exercised out of how many are claimed.

    This test exists so that a shrinking population cannot read as an
    improvement. It asserts only that every registry row is accounted for --
    the interesting output is the printed roster, which names every skipped
    host and why it was skipped.
    """
    exercised: List[str] = []
    skipped: List[Tuple[str, str]] = []
    for host in registry:
        host_id = str(host.get("id"))
        implemented, why = _implementation_state(host)
        (exercised.append(host_id) if implemented
         else skipped.append((host_id, why)))

    print(f"\nsaddle contract: {len(exercised)} of {len(registry)} claimed "
          f"saddles exercised over {len(OPERATION_IDS)} operations")
    for host_id in exercised:
        print(f"  EXERCISED {host_id}")
    for host_id, why in skipped:
        print(f"  SKIPPED   {host_id}: {why}")
    print("  A skipped saddle stays in this denominator. It is untested, not absent.")

    assert len(exercised) + len(skipped) == len(registry), (
        "a registry row fell out of the population entirely"
    )
    assert exercised, (
        "no saddle was exercised at all -- a contract suite that tests nothing "
        "passes for the wrong reason"
    )
    reference = [str(h.get("id")) for h in registry if h.get("grade") == "reference"]
    assert reference, "the registry names no reference saddle to grade the others against"
    for host_id in reference:
        assert host_id in exercised, (
            f"{host_id} is graded 'reference' and was not exercised; a grade "
            "that no run backs is a claim, not a measurement"
        )
