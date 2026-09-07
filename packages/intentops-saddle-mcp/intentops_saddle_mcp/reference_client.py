"""A reference MCP client that ASKS BEFORE IT ACTS, and honours the answer.

PURPOSE
    Close, for exactly one client, the S2 gap this saddle states in its own
    ``intentops.status``: *the server computes a verdict and cannot refuse a
    call the host never routes through it.* That sentence stays true of MCP in
    general and of every third-party host. What was missing was a single
    honest data point -- a client that DOES route through it and DOES stop --
    so that "computed, not enforced" could be measured against something
    rather than only asserted.

    THE ONLY INTERESTING METHOD IS :meth:`ReferenceClient.act`. Everything
    else here is plumbing. ``act`` takes a proposed action, a description of
    it, and a callable that would perform it, and it calls that callable ONLY
    on an explicit ``ALLOW``. Every other outcome -- ``BLOCK``, ``ASK``,
    ``HALT``, a tool error, a transport failure, a malformed frame, a missing
    ``decision`` field -- leaves the effect uncalled and is recorded as
    honoured. A client that acted on anything it could not read would be a
    client that treats silence as permission, which is the failure this whole
    project is organised around.

    WHAT THIS IS NOT. It is not an MCP client library, it is not a host, and
    it proves NOTHING about any other client. A passing run belongs on THIS
    client's row, about THIS client. That fence is stated in the falsifier
    record it exists to produce (``docs/falsifiers/S2-mcp-client-deny-*.md``)
    and in ``config/saddles.yaml``, whose ``mcp-hosted`` grade is unchanged by
    it.

    The credential rides in the request's ``_meta``, never in a tool argument,
    for the reason the protocol module already gives: an argument ends up in
    tool logs, transcripts and the server's own ledger.

WRITE MODEL
    None. This module spawns a server, exchanges JSON lines with it, and
    returns values. It writes no file and holds nothing between runs. The
    SERVER it talks to appends to its own ledger under that module's declared
    append-only model, and an ``effect`` supplied by a caller writes whatever
    the caller's own write model says -- which is the caller's to declare.

BLIND SPOTS -- stated so a PASS is not read as more than it is
    * One client. This says nothing about any other MCP client, and a result
      here must never be reported as a fact about MCP hosts generally.
    * It proves the client asks and stops. It cannot prove the client has no
      OTHER path to the same effect; the fence here is a convention inside one
      class, not a sandbox, and a caller that performs its effect without
      calling :meth:`act` is outside every guarantee on this page.
    * The verdict is the server's. This client does not second-guess a tier or
      re-derive a decision; a wrong verdict is honoured exactly as faithfully
      as a right one.
    * The transport is one process, one pipe, one request at a time. Nothing
      here is concurrent and nothing here reconnects.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import subprocess  # noqa: S404 - launching the server IS this module
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "CLIENT_NAME",
    "CLIENT_VERSION",
    "ClientError",
    "Outcome",
    "ReferenceClient",
    "main",
    "selftest",
]

#: The name this client presents at ``initialize``. It is AUTHORISATION, never
#: identification -- the server's ``allowed_clients`` list decides whether a
#: name may connect at all, and this client has no way to prove it is the one
#: that name refers to.
CLIENT_NAME = "intentops-reference-client"
CLIENT_VERSION = "0.1.0"

PROTOCOL_VERSION = "2025-06-18"

#: The one decision that permits an effect. Everything else stops.
PERMITTING_DECISION = "ALLOW"


class ClientError(RuntimeError):
    """The client could not obtain a verdict. NEVER a reason to proceed."""


@dataclass(frozen=True)
class Outcome:
    """What the client asked, what it was told, and what it then did."""

    tool: str
    decision: str
    tier: str = ""
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    performed: bool = False
    effect_result: Any = None
    #: True whenever the client's behaviour matched the verdict it received --
    #: which includes every case where no verdict could be read at all.
    honoured: bool = True
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"tool": self.tool, "decision": self.decision, "tier": self.tier,
                "reasons": list(self.reasons), "performed": self.performed,
                "honoured": self.honoured, "note": self.note}


def _reader(stream: Any, sink: "queue.Queue[Optional[str]]") -> None:
    try:
        for line in stream:
            sink.put(line)
    except (OSError, ValueError):
        pass
    finally:
        sink.put(None)


class ReferenceClient:
    """Speaks MCP stdio to one IntentOps saddle server, and obeys it."""

    def __init__(self, argv: Sequence[str], *, token: str,
                 client_name: str = CLIENT_NAME,
                 env: Optional[Mapping[str, str]] = None,
                 cwd: Optional[str] = None,
                 timeout_s: float = 20.0) -> None:
        if not argv:
            raise ClientError("a client with no server command can ask nobody")
        if not str(token or "").strip():
            raise ClientError(
                "no bearer token was given. This server has no anonymous "
                "identity, so a client with no credential is refused rather "
                "than downgraded")
        self._argv = list(argv)
        self._token = str(token).strip()
        self._client_name = client_name
        self._env = dict(env) if env is not None else dict(os.environ)
        self._cwd = cwd
        self._timeout_s = float(timeout_s)
        self._proc: Optional[Any] = None
        self._lines: "queue.Queue[Optional[str]]" = queue.Queue()
        self._next_id = 0
        self.server_info: Dict[str, Any] = {}

    # -- lifecycle -----------------------------------------------------------

    def __enter__(self) -> "ReferenceClient":
        self.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def start(self) -> None:
        self._proc = subprocess.Popen(  # noqa: S603 - the caller names the argv
            self._argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=self._env, cwd=self._cwd,
            text=True, encoding="utf-8", bufsize=1)
        threading.Thread(target=_reader, args=(self._proc.stdout, self._lines),
                         daemon=True).start()

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 - a server that will not leave is killed
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    # -- the wire ------------------------------------------------------------

    def _send(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if self._proc is None or self._proc.stdin is None:
            raise ClientError("the client is not connected to a server")
        self._next_id += 1
        body = dict(params)
        # The credential rides in `_meta`, per the protocol module's own rule.
        body["_meta"] = {"authorization": f"Bearer {self._token}"}
        frame = {"jsonrpc": "2.0", "id": self._next_id, "method": method,
                 "params": body}
        try:
            self._proc.stdin.write(json.dumps(frame) + "\n")
            self._proc.stdin.flush()
        except OSError as exc:
            raise ClientError(f"the server closed the pipe: {exc}") from exc
        try:
            line = self._lines.get(timeout=self._timeout_s)
        except queue.Empty as exc:
            raise ClientError(
                f"the server did not answer {method!r} within "
                f"{self._timeout_s}s") from exc
        if line is None:
            raise ClientError(f"the server ended without answering {method!r}")
        try:
            answer = json.loads(line)
        except Exception as exc:  # noqa: BLE001
            raise ClientError(
                f"the server answered {method!r} with something that is not "
                f"JSON ({type(exc).__name__})") from exc
        if not isinstance(answer, Mapping):
            raise ClientError(f"the server answered {method!r} with a "
                              f"{type(answer).__name__}, not an object")
        return dict(answer)

    def initialize(self) -> Dict[str, Any]:
        answer = self._send("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "clientInfo": {"name": self._client_name, "version": CLIENT_VERSION},
            "capabilities": {},
        })
        if "error" in answer:
            error = answer["error"] or {}
            raise ClientError(
                f"the server refused the handshake: {error.get('message')} "
                f"(code {error.get('code')})")
        result = dict(answer.get("result") or {})
        self.server_info = dict(result.get("serverInfo") or {})
        return result

    def call_tool(self, name: str,
                  arguments: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        answer = self._send("tools/call", {"name": name,
                                           "arguments": dict(arguments or {})})
        if "error" in answer:
            error = answer["error"] or {}
            raise ClientError(f"{name}: {error.get('message')} "
                              f"(code {error.get('code')})")
        result = answer.get("result")
        if not isinstance(result, Mapping):
            raise ClientError(f"{name}: the server returned no result object")
        if result.get("isError"):
            structured = result.get("structuredContent") or {}
            raise ClientError(f"{name}: {structured.get('error') or 'refused'}")
        structured = result.get("structuredContent")
        if not isinstance(structured, Mapping):
            raise ClientError(f"{name}: the server returned no structured content")
        return dict(structured)

    # -- the whole point -----------------------------------------------------

    def act(self, tool: str, tool_input: Optional[Mapping[str, Any]] = None, *,
            effect: Optional[Callable[[], Any]] = None) -> Outcome:
        """Ask the gate, then perform ``effect`` ONLY on an explicit ALLOW.

        Every path that is not an ALLOW leaves ``effect`` uncalled. That
        includes the paths where nothing could be read at all: an unreachable
        server, a malformed frame, a tool error, a verdict with no
        ``decision``. A client that treated any of those as permission would
        be treating silence as consent.
        """
        try:
            payload = self.call_tool("intentops.gate",
                                     {"tool": tool,
                                      "tool_input": dict(tool_input or {})})
        except ClientError as exc:
            return Outcome(tool=tool, decision="UNREADABLE", performed=False,
                           honoured=True,
                           note=f"no verdict could be read, so nothing was "
                                f"done: {exc}")

        verdict = payload.get("verdict")
        if not isinstance(verdict, Mapping):
            return Outcome(tool=tool, decision="UNREADABLE", performed=False,
                           honoured=True,
                           note="the answer carried no verdict object")
        decision = str(verdict.get("decision") or "").strip().upper()
        tier = str(verdict.get("tier") or "")
        reasons = tuple(str(r) for r in (verdict.get("reasons") or []))
        if not decision:
            return Outcome(tool=tool, decision="UNREADABLE", tier=tier,
                           reasons=reasons, performed=False, honoured=True,
                           note="the verdict carried no decision field")

        if decision != PERMITTING_DECISION:
            return Outcome(tool=tool, decision=decision, tier=tier,
                           reasons=reasons, performed=False, honoured=True,
                           note=f"the server answered {decision}; the effect "
                                "was not performed")

        if effect is None:
            return Outcome(tool=tool, decision=decision, tier=tier,
                           reasons=reasons, performed=False, honoured=True,
                           note="permitted, and the caller supplied no effect")
        return Outcome(tool=tool, decision=decision, tier=tier, reasons=reasons,
                       performed=True, effect_result=effect(), honoured=True,
                       note="permitted, and performed")


# ---------------------------------------------------------------------------
# selftest -- a fenced, live run against a real server subprocess
# ---------------------------------------------------------------------------


def server_argv(python: Optional[str] = None) -> List[str]:
    """The command that starts the saddle server, using THIS interpreter."""
    return [python or sys.executable, "-m", "intentops_saddle_mcp.server"]


def _fixture_repo(root: Path) -> Path:
    """A minimal repo root whose saddles row admits this client, and only it."""
    repo = root / "repo"
    (repo / "config").mkdir(parents=True, exist_ok=True)
    # `_is_repo` requires BOTH markers; a fixture missing one is not a repo and
    # the server refuses every call rather than guessing, which is correct.
    (repo / "genesis" / "imprint").mkdir(parents=True, exist_ok=True)
    (repo / "config" / "saddles.yaml").write_text(
        "hosts:\n"
        "  - id: mcp-hosted\n"
        "    grade: candidate\n"
        f"    allowed_clients: ['{CLIENT_NAME}']\n",
        encoding="utf-8")
    return repo


def _child_env(node_root: Path, repo_root: Path) -> Dict[str, str]:
    env = dict(os.environ)
    env["INTENTOPS_NODE_ROOT"] = str(node_root)
    env["INTENTOPS_REPO_ROOT"] = str(repo_root)
    # The server imports its own package; the child needs to find it the same
    # way this process did.
    existing = env.get("PYTHONPATH", "")
    here = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = here + (os.pathsep + existing if existing else "")
    return env


def selftest() -> Tuple[bool, str]:
    """Prove the client asks, honours a deny, and never acts on silence."""
    from .auth import mint  # local import: the selftest is the only user

    fired: List[str] = []
    failures: List[str] = []

    def expect(label: str, cond: bool) -> None:
        fired.append(label)
        if not cond:
            failures.append(label)

    root = Path(tempfile.mkdtemp(prefix="intentops-refclient-selftest-"))
    try:
        node_root = root / "node"
        (node_root / ".intentops").mkdir(parents=True, exist_ok=True)
        repo_root = _fixture_repo(root)
        token = mint(node_root)
        env = _child_env(node_root, repo_root)
        effects: List[str] = []

        with ReferenceClient(server_argv(), token=token, env=env) as client:
            result = client.initialize()
            expect("the-handshake-succeeds", bool(result.get("serverInfo")))

            # 1. a denied action: the effect must not run
            denied = client.act("Bash", {"command": "rm -rf /"},
                                effect=lambda: effects.append("denied-ran"))
            expect("a-deny-is-not-allow", denied.decision != PERMITTING_DECISION)
            expect("a-denied-effect-never-ran",
                   (not denied.performed) and "denied-ran" not in effects)
            expect("a-deny-is-recorded-as-honoured", denied.honoured)

            # 2. a permitted action: the effect DOES run, or the test above
            #    proves nothing (a client that never acts honours everything)
            allowed = client.act("Read", {"file_path": "README.md"},
                                 effect=lambda: effects.append("allowed-ran"))
            expect("a-permitted-effect-does-run",
                   allowed.performed and "allowed-ran" in effects)

        # 3. an unreachable server is not permission
        with ReferenceClient([sys.executable, "-c", "pass"], token=token,
                             env=env, timeout_s=5) as dead:
            outcome = dead.act("Bash", {"command": "echo"},
                               effect=lambda: effects.append("dead-ran"))
        expect("an-unreadable-verdict-is-not-permission",
               (not outcome.performed) and outcome.decision == "UNREADABLE"
               and "dead-ran" not in effects)

        # 4. an unlisted client name is refused at the handshake
        with ReferenceClient(server_argv(), token=token,
                             client_name="somebody-else", env=env) as stranger:
            try:
                stranger.initialize()
                expect("an-unlisted-client-is-refused", False)
            except ClientError as exc:
                expect("an-unlisted-client-is-refused",
                       "refused the handshake" in str(exc))

        # 5. a wrong credential is refused, not downgraded to anonymous
        with ReferenceClient(server_argv(), token="not-the-right-token",
                             env=env) as impostor:
            try:
                impostor.initialize()
                expect("a-wrong-credential-is-refused", False)
            except ClientError:
                expect("a-wrong-credential-is-refused", True)

        # 6. no credential at all is refused before a process is even started
        try:
            ReferenceClient(server_argv(), token="  ")
            expect("an-empty-credential-is-refused-at-construction", False)
        except ClientError:
            expect("an-empty-credential-is-refused-at-construction", True)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    report = (f"saddle_mcp.reference_client selftest: {len(fired)} paths fired, "
              f"{len(failures)} failed"
              + (" -- " + ", ".join(failures) if failures else ""))
    return (not failures, report)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="intentops-mcp-reference-client",
        description=("a reference MCP client that asks the gate before it "
                     "acts, and honours the answer"))
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        ok, report = selftest()
        print(report)
        return 0 if ok else 1
    parser.error("give --selftest; this client is a demonstration, not a tool")
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
