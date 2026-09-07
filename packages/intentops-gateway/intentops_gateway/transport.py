"""The stdio transport: the one way a PERMITTED backend call actually happens.

PURPOSE
    ``tools.NullInvoker`` is the shipped default and refuses, which is the
    honest state of a gateway that decides but does not execute. This module is
    the OTHER implementation of the same seam -- an invoker that spawns a
    declared backend as a child process, hands it one JSON line on stdin, and
    reads one JSON line back. Wiring it in is a separate, reviewed act: it
    reaches the caller only through the ``invoker=`` argument of
    ``tools.load_gateway_context``, so ``NullInvoker`` stays what an unmodified
    node gets (live-gate-edits.md rule 3 -- additive elsewhere, never an edit
    to the gate body).

    IT RE-CHECKS WHAT THE DISPATCHER ALREADY CHECKED, ON PURPOSE. ``tools.
    _dispatch_backend`` tests the allowlist, then the registry, then the gate,
    and only then calls this. Every one of those could be bypassed by a caller
    who constructs an invoker and calls ``invoke`` directly -- a test, a
    future transport, a well-meaning script. A control that is correct only
    because of its caller is not a control, so the two checks a transport can
    make on its own it makes again here, at spawn time:

      * the backend is still IN THE REGISTRY (a grant naming something the
        estate no longer declares spawns nothing);
      * the backend has a DECLARED COMMAND in this invoker's own table (an
        unlisted backend is refused here even if a policy permits it); and
      * the node is not STOOD DOWN, read from disk on every call rather than
        cached at construction, because a kill switch consulted once at
        startup is a kill switch that does not work.

    WHAT IT REFUSES, RATHER THAN TRUNCATES. A timeout and an output cap are
    both enforced by killing the child and RAISING, never by returning what
    arrived first. A truncated answer that reads as a successful result is the
    silent-failure shape: the caller acts on half a frame and nothing says so.
    Both refusals name the cap they hit, so an operator can tell "the backend
    is slow" from "the backend is shouting".

    THE ENVIRONMENT IS BUILT, NOT INHERITED. The child gets an env assembled
    from an explicit allowlist of NAMES, never a copy of ``os.environ``. A
    gateway that forwards its whole environment hands every child every
    credential, proxy setting and path the parent happens to be carrying, and
    nothing in the call would show it.

WRITE MODEL
    None. This module spawns a process and returns a value. It writes no file,
    holds no state between calls and claims no lock. The request ledger is
    appended by ``tools._record`` around the call, under that module's declared
    append-only model. The command table it reads is operator-authored and
    single-writer (a human, or one explicit generator); this module only reads
    it, and a missing load-bearing field HALTs rather than defaulting.

BLIND SPOTS -- stated so a returned frame is not read as a good answer
    * This transports; it does not evaluate. A backend that returns a
      well-formed JSON object full of nonsense is indistinguishable here from
      one that worked.
    * The env allowlist bounds what is PASSED. It cannot bound what the child
      reads from disk, from a user profile, or from a credential store, and a
      child that spawns its own children is outside every bound here.
    * ``cwd`` and ``argv`` are the operator's. Nothing validates that the
      command named is the backend it claims to be; the fence is that a command
      must be declared at all, not that the declaration is honest.
    * The output cap bounds MEMORY and the timeout bounds WALL CLOCK. Neither
      bounds what the child does to the machine in between -- CPU, disk, or its
      own outbound sockets are outside this seam entirely.
    * One request, one response, one process. There is no session, no
      keep-alive and no reuse, which costs a process launch per call and is the
      trade this makes for having nothing to leak between calls.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess  # noqa: S404 - spawning a declared backend IS this module
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from . import backends as backends_mod
from .tools import NotWired

__all__ = [
    "DEFAULT_ENV_ALLOWLIST",
    "DEFAULT_MAX_OUTPUT_BYTES",
    "DEFAULT_TIMEOUT_S",
    "TRANSPORT_DISABLED_RELPATH",
    "StdioCommand",
    "StdioInvoker",
    "TransportRefused",
    "load_commands",
    "main",
    "selftest",
]


class TransportRefused(NotWired):
    """A refusal from the transport itself. NEVER a partial result.

    It subclasses :class:`~intentops_gateway.tools.NotWired` deliberately:
    ``_dispatch_backend`` already turns that class into a ``ToolRefused``
    carrying ``executed: False``, so a transport refusal reaches the caller
    through the same door as "no invoker is wired" and can never be mistaken
    for an answer.
    """


#: Read at DISPATCH time, on every call. A sentinel file under the node root,
#: beside the stand-down marker the whole node already honours.
TRANSPORT_DISABLED_RELPATH = Path(".intentops") / "gateway" / "transport-disabled"

#: Wall clock for one call, in seconds. A backend slower than this is a failed
#: call, not a slow one -- the caller gets a refusal naming the cap.
DEFAULT_TIMEOUT_S: float = 30.0

#: Bytes of stdout one call may produce. Past this the child is killed and the
#: call is refused; nothing truncated is ever returned as a result.
DEFAULT_MAX_OUTPUT_BYTES: int = 1_048_576

#: Bytes of stderr kept for the refusal message. Diagnostics, never a result.
_STDERR_SNIPPET_BYTES: int = 2_000

#: The env NAMES a child may receive unless its command declares more. Values
#: come from this process; names come from here. It is deliberately the
#: minimum a process needs to start on the platforms this runs on -- no
#: proxy variables, no credentials, no ``PYTHONPATH``, nothing a parent
#: session happens to be carrying.
DEFAULT_ENV_ALLOWLIST: Tuple[str, ...] = (
    "COMSPEC",
    "HOME",
    "LANG",
    "LC_ALL",
    "PATH",
    "PATHEXT",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "WINDIR",
)


@dataclass(frozen=True)
class StdioCommand:
    """One backend's declared command line. Every field is load-bearing."""

    backend_id: str
    argv: Tuple[str, ...]
    cwd: Optional[str] = None
    #: EXTRA env names, on top of :data:`DEFAULT_ENV_ALLOWLIST`. Names only.
    env_allowlist: Tuple[str, ...] = field(default_factory=tuple)
    timeout_s: float = DEFAULT_TIMEOUT_S
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES

    def __post_init__(self) -> None:
        if not self.backend_id:
            raise ValueError("a command with no backend id can never be found")
        if not self.argv:
            raise ValueError(
                f"backend {self.backend_id!r} declares an empty argv; a "
                "command nobody can run is not a declaration")
        if self.timeout_s <= 0:
            raise ValueError(
                f"backend {self.backend_id!r} declares timeout_s="
                f"{self.timeout_s}, which is not a bound")
        if self.max_output_bytes <= 0:
            raise ValueError(
                f"backend {self.backend_id!r} declares max_output_bytes="
                f"{self.max_output_bytes}, which is not a bound")

    def names(self) -> Tuple[str, ...]:
        """Every env NAME this command's child may receive."""
        return tuple(dict.fromkeys(DEFAULT_ENV_ALLOWLIST + tuple(self.env_allowlist)))


def _require(row: Mapping[str, Any], key: str, where: str) -> Any:
    """A load-bearing field that is absent HALTs; it never defaults."""
    if key not in row:
        raise ValueError(
            f"{where} declares no `{key}`. A missing load-bearing field is a "
            "halt, not a default -- a transport that half-understands its own "
            "command table spawns something nobody declared.")
    return row[key]


def load_commands(path: Path | str) -> Dict[str, StdioCommand]:
    """Read an operator-authored command table. The CALLER names the path.

    No location is assumed and none is defaulted: a module that invented a
    path would be declaring where an operator's process table lives on their
    behalf. Shape::

        commands:
          - backend: some-backend
            argv: ["/path/to/exe", "--stdio"]
            cwd: "/optional/working/directory"     # optional
            env_allowlist: ["SOME_NAME"]           # optional, NAMES only
            timeout_s: 30                          # optional
            max_output_bytes: 1048576              # optional
    """
    source = Path(path)
    if not source.is_file():
        raise ValueError(f"{source} is missing; refusing rather than assuming "
                         "an empty command table")
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - pyyaml is a dependency
        raise ValueError(f"pyyaml is required to read {source}: {exc}") from exc
    doc = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(doc, Mapping):
        raise ValueError(f"{source} is not a mapping")
    rows = _require(doc, "commands", str(source))
    if not isinstance(rows, list):
        raise ValueError(f"{source}: `commands` must be a list, not "
                         f"{type(rows).__name__}")
    out: Dict[str, StdioCommand] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError(f"{source}: a command entry is not a mapping")
        ident = str(_require(row, "backend", str(source))).strip().lower()
        argv = _require(row, "argv", f"{source}: backend {ident!r}")
        if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
            raise ValueError(f"{source}: backend {ident!r} has an `argv` that "
                             "is not a list of strings")
        if ident in out:
            raise ValueError(f"{source} declares backend {ident!r} twice; a "
                             "name collision is reported, never resolved")
        extra = row.get("env_allowlist") or []
        if not isinstance(extra, list) or not all(isinstance(n, str) for n in extra):
            raise ValueError(f"{source}: backend {ident!r} has an "
                             "`env_allowlist` that is not a list of names")
        out[ident] = StdioCommand(
            backend_id=ident,
            argv=tuple(argv),
            cwd=(str(row["cwd"]) if row.get("cwd") else None),
            env_allowlist=tuple(extra),
            timeout_s=float(row.get("timeout_s", DEFAULT_TIMEOUT_S)),
            max_output_bytes=int(row.get("max_output_bytes",
                                         DEFAULT_MAX_OUTPUT_BYTES)),
        )
    return out


def _capped_reader(stream: Any, cap: int, sink: List[bytes],
                   overflow: threading.Event) -> None:
    """Read until EOF or until ``cap`` is exceeded, then stop. Never blocks forever."""
    total = 0
    try:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                return
            total += len(chunk)
            if total > cap:
                overflow.set()
                return
            sink.append(chunk)
    except (OSError, ValueError):  # the pipe closed under us; EOF by another name
        return


class StdioInvoker:
    """A ``BackendInvoker`` that spawns a declared backend for exactly one call.

    Construct it with the command table, the registry it must agree with, and
    the node root whose kill switch it must honour. ``popen`` is injectable so
    a test can prove a spawn did NOT happen -- a refusal that is only asserted
    by its message is not asserted at all.
    """

    def __init__(self, *,
                 commands: Mapping[str, StdioCommand],
                 registry: Optional[backends_mod.BackendRegistry],
                 node_root: Path | str,
                 popen: Optional[Callable[..., Any]] = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._commands = {str(k).strip().lower(): v for k, v in commands.items()}
        for ident, command in self._commands.items():
            if command.backend_id != ident:
                raise ValueError(
                    f"the command table keys {ident!r} to a command declaring "
                    f"backend {command.backend_id!r}; a table that disagrees "
                    "with itself would spawn the wrong process")
        self._registry = registry
        self._node_root = Path(node_root)
        self._popen = popen or subprocess.Popen
        self._clock = clock

    # -- the checks, each answerable on its own -----------------------------

    def stand_down_reason(self) -> Optional[str]:
        """The kill switch, READ FROM DISK on every call. Never cached."""
        marker = self._node_root / TRANSPORT_DISABLED_RELPATH
        if not marker.exists():
            return None
        try:
            body = marker.read_text(encoding="utf-8").strip()
        except OSError as exc:  # an unreadable switch is still a switch
            return f"the transport kill switch is present and unreadable: {exc}"
        return body or "the transport kill switch is present"

    def env_for(self, command: StdioCommand) -> Dict[str, str]:
        """The child's whole environment, assembled from names, never copied."""
        allowed = command.names()
        return {name: os.environ[name] for name in allowed if name in os.environ}

    # -- the seam ------------------------------------------------------------

    def invoke(self, backend: backends_mod.Backend, tool: str,
               args: Mapping[str, Any]) -> Dict[str, Any]:
        ident = str(getattr(backend, "id", "") or "").strip().lower()

        halted = self.stand_down_reason()
        if halted:
            raise TransportRefused(
                f"the backend transport is switched off: {halted}. Nothing was "
                f"spawned for {ident}__{tool}.")

        if self._registry is None:
            raise TransportRefused(
                "the backend registry could not be read, so this transport "
                "cannot confirm that any backend is still declared; nothing "
                "is spawned on an unreadable registry")
        declared = self._registry.get(ident)
        if declared is None:
            raise TransportRefused(
                f"backend {ident!r} is not declared in {self._registry.source}; "
                "the transport will not spawn a process for a backend the "
                "estate does not carry")

        command = self._commands.get(ident)
        if command is None:
            raise TransportRefused(
                f"backend {ident!r} is declared and has NO command in this "
                "transport's table; an unlisted backend is refused here even "
                "when a policy permits it. Declare its command, in a reviewed "
                "change, or leave it unreachable.")

        frame = json.dumps({"tool": str(tool), "args": dict(args or {})},
                           sort_keys=True) + "\n"
        return self._run(command, frame, address=f"{ident}__{tool}")

    # -- the spawn -----------------------------------------------------------

    def _run(self, command: StdioCommand, frame: str, *,
             address: str) -> Dict[str, Any]:
        started = self._clock()
        try:
            proc = self._popen(
                list(command.argv),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=command.cwd,
                env=self.env_for(command),
                close_fds=True,
            )
        except OSError as exc:
            raise TransportRefused(
                f"{address}: the declared command could not be started "
                f"({type(exc).__name__}: {exc}). A backend that will not start "
                "is a failed call, never an empty result") from exc

        out_chunks: List[bytes] = []
        err_chunks: List[bytes] = []
        overflow = threading.Event()
        readers = [
            threading.Thread(target=_capped_reader,
                             args=(proc.stdout, command.max_output_bytes,
                                   out_chunks, overflow),
                             daemon=True),
            threading.Thread(target=_capped_reader,
                             args=(proc.stderr, _STDERR_SNIPPET_BYTES,
                                   err_chunks, threading.Event()),
                             daemon=True),
        ]
        for reader in readers:
            reader.start()

        try:
            if proc.stdin is not None:
                proc.stdin.write(frame.encode("utf-8"))
                proc.stdin.flush()
                proc.stdin.close()
        except OSError:
            # A child that closed stdin before we finished writing is a child
            # that answered or died; both are decided by what comes back.
            pass

        readers[0].join(command.timeout_s)
        timed_out = readers[0].is_alive()
        if timed_out or overflow.is_set():
            self._kill(proc)
            for reader in readers:
                reader.join(1.0)
            if overflow.is_set():
                raise TransportRefused(
                    f"{address}: the backend produced more than "
                    f"{command.max_output_bytes} bytes on stdout and was "
                    "killed. Nothing partial is returned -- a truncated frame "
                    "that read as a result is exactly the failure this cap "
                    "exists to prevent.")
            raise TransportRefused(
                f"{address}: the backend did not answer within "
                f"{command.timeout_s}s and was killed. A timeout is a failed "
                "call, never a quiet empty answer.")

        try:
            code = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._kill(proc)
            code = proc.poll()
        readers[1].join(1.0)
        elapsed_ms = int((self._clock() - started) * 1000)
        stderr = b"".join(err_chunks).decode("utf-8", "replace").strip()

        if code not in (0, None):
            raise TransportRefused(
                f"{address}: the backend exited {code}"
                + (f" ({stderr[:400]})" if stderr else "")
                + ". A non-zero exit is a failed call.")

        raw = b"".join(out_chunks).decode("utf-8", "replace").strip()
        if not raw:
            raise TransportRefused(
                f"{address}: the backend answered nothing on stdout"
                + (f" ({stderr[:400]})" if stderr else "")
                + ". Zero output is a state, never a success.")
        line = raw.splitlines()[0]
        try:
            doc = json.loads(line)
        except Exception as exc:  # noqa: BLE001 - the decoder's class varies
            raise TransportRefused(
                f"{address}: the backend answered something that is not JSON "
                f"({type(exc).__name__}). This transport speaks one JSON "
                "object per line and will not guess at anything else.") from exc
        if not isinstance(doc, dict):
            raise TransportRefused(
                f"{address}: the backend answered JSON that is not an object "
                f"({type(doc).__name__}); a result must be an object.")
        return {"backend_result": doc, "elapsed_ms": elapsed_ms,
                "transport": "stdio"}

    @staticmethod
    def _kill(proc: Any) -> None:
        for step in (proc.kill, proc.terminate):
            try:
                step()
                return
            except Exception:  # noqa: BLE001 - a dead child is already killed
                continue


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------

#: A backend that answers, shouts, sleeps or reports its env, chosen by argv.
#: Written to a temp directory by the selftest; never shipped as a file.
_FAKE_BACKEND = '''\
import json, os, sys, time
mode = sys.argv[1]
line = sys.stdin.readline()
if mode == "answer":
    ask = json.loads(line)
    sys.stdout.write(json.dumps({"echo": ask["tool"]}) + "\\n")
elif mode == "env":
    sys.stdout.write(json.dumps({"env": sorted(os.environ)}) + "\\n")
elif mode == "shout":
    sys.stdout.write("x" * 200000 + "\\n")
elif mode == "sleep":
    time.sleep(30)
sys.stdout.flush()
'''


def _fake_registry(ids: Sequence[str]) -> backends_mod.BackendRegistry:
    return backends_mod.BackendRegistry(
        backends={i: backends_mod.Backend(id=i, namespace=i, transport="stdio",
                                          tool_tiers={"read": "T0"},
                                          source="selftest")
                  for i in ids},
        source="selftest")


def selftest() -> Tuple[bool, str]:
    """Prove every refusal path CAN fire, and that a good call still works."""
    fired: List[str] = []
    failures: List[str] = []

    def expect(label: str, cond: bool) -> None:
        fired.append(label)
        if not cond:
            failures.append(label)

    root = Path(tempfile.mkdtemp(prefix="intentops-transport-selftest-"))
    try:
        script = root / "fake_backend.py"
        script.write_text(_FAKE_BACKEND, encoding="utf-8")
        registry = _fake_registry(["good", "listed-only"])
        backend = registry.get("good")

        def invoker_for(mode: str, **kw: Any) -> StdioInvoker:
            return StdioInvoker(
                commands={"good": StdioCommand(
                    backend_id="good",
                    argv=(sys.executable, str(script), mode),
                    **kw)},
                registry=registry, node_root=root)

        # 1. a permitted call actually runs
        result = invoker_for("answer").invoke(backend, "read", {"a": 1})
        expect("a-good-call-returns-the-backends-object",
               result["backend_result"] == {"echo": "read"})

        # 2. the environment is BUILT, not inherited
        os.environ["INTENTOPS_TRANSPORT_SELFTEST_CANARY"] = "present"
        try:
            seen = invoker_for("env").invoke(backend, "read", {})
            names = set(seen["backend_result"]["env"])
        finally:
            os.environ.pop("INTENTOPS_TRANSPORT_SELFTEST_CANARY", None)
        expect("the-child-never-sees-an-unlisted-variable",
               "INTENTOPS_TRANSPORT_SELFTEST_CANARY" not in names)

        # 3. the output cap refuses rather than truncating
        try:
            invoker_for("shout", max_output_bytes=1000).invoke(backend, "read", {})
            expect("an-oversize-answer-is-refused", False)
        except TransportRefused as exc:
            expect("an-oversize-answer-is-refused", "1000 bytes" in str(exc))

        # 4. the timeout refuses rather than waiting
        try:
            invoker_for("sleep", timeout_s=0.5).invoke(backend, "read", {})
            expect("a-slow-backend-is-refused", False)
        except TransportRefused as exc:
            expect("a-slow-backend-is-refused", "did not answer within" in str(exc))

        # 5. an unlisted backend is refused HERE, with nothing spawned
        spawned: List[Any] = []

        def spy(*a: Any, **k: Any) -> Any:
            spawned.append(a)
            raise AssertionError("a process was spawned for a refused call")

        unlisted = StdioInvoker(commands={}, registry=registry,
                                node_root=root, popen=spy)
        try:
            unlisted.invoke(backend, "read", {})
            expect("an-unlisted-backend-is-refused", False)
        except TransportRefused as exc:
            expect("an-unlisted-backend-is-refused",
                   "NO command" in str(exc) and not spawned)

        # 6. a backend the registry does not carry is refused
        stranger = backends_mod.Backend(id="stranger", namespace="s",
                                        transport="stdio", tool_tiers={},
                                        source="selftest")
        try:
            invoker_for("answer").invoke(stranger, "read", {})
            expect("an-undeclared-backend-is-refused", False)
        except TransportRefused as exc:
            expect("an-undeclared-backend-is-refused", "not declared" in str(exc))

        # 7. the kill switch, read at dispatch time
        marker = root / TRANSPORT_DISABLED_RELPATH
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("switched off by the selftest\n", encoding="utf-8")
        killed = StdioInvoker(commands={"good": StdioCommand(
            backend_id="good", argv=(sys.executable, str(script), "answer"))},
            registry=registry, node_root=root, popen=spy)
        try:
            killed.invoke(backend, "read", {})
            expect("the-kill-switch-refuses-before-any-spawn", False)
        except TransportRefused as exc:
            expect("the-kill-switch-refuses-before-any-spawn",
                   "switched off" in str(exc) and not spawned)
        marker.unlink()

        # 8. a refusal is a NotWired, so the dispatcher renders executed=False
        expect("a-transport-refusal-is-a-notwired",
               issubclass(TransportRefused, NotWired))

        # 9. a command table that disagrees with itself is refused at build
        try:
            StdioInvoker(commands={"a": StdioCommand(backend_id="b",
                                                     argv=("x",))},
                         registry=registry, node_root=root)
            expect("a-self-contradicting-table-is-refused", False)
        except ValueError:
            expect("a-self-contradicting-table-is-refused", True)

        # 10. a missing load-bearing field HALTs rather than defaulting
        table = root / "commands.yaml"
        table.write_text("commands:\n  - backend: good\n", encoding="utf-8")
        try:
            load_commands(table)
            expect("a-command-with-no-argv-halts", False)
        except ValueError as exc:
            expect("a-command-with-no-argv-halts", "argv" in str(exc))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    report = (f"gateway.transport selftest: {len(fired)} paths fired, "
              f"{len(failures)} failed"
              + (" -- " + ", ".join(failures) if failures else ""))
    return (not failures, report)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="intentops-gateway-transport",
        description="the stdio backend transport (a seam, wired by the operator)")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--commands", default=None,
                        help="validate an operator command table and print it")
    args = parser.parse_args(argv)
    if args.selftest:
        ok, report = selftest()
        print(report)
        return 0 if ok else 1
    if args.commands:
        table = load_commands(args.commands)
        print(json.dumps({k: {"argv": list(v.argv), "cwd": v.cwd,
                              "env_names": list(v.names()),
                              "timeout_s": v.timeout_s,
                              "max_output_bytes": v.max_output_bytes}
                          for k, v in sorted(table.items())}, indent=2))
        return 0
    parser.error("give --selftest or --commands")
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
