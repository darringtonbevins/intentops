"""The selftest registry -- one verb that runs EVERY instrument's selftest.

PURPOSE
    The house rule is *every detector ships ``--selftest``*, because a
    detector that has never fired is indistinguishable from a broken one. The
    tree obeys it: dozens of instruments carry one. Nothing ran them all.

    ``intentops verify --selftest`` enumerates the SEVEN genesis instruments
    and nothing else, and the CI job step listed TEN by hand -- a hand list
    that goes stale the moment an eleventh is born, silently, in the
    direction that looks green. That is the denominator failure, not the
    silent-failure one: the instruments did not fail, they left the count.
    This module is the counter.

    Two populations, DERIVED rather than listed:

    1. **Modules** under every package in ``packages/*/`` that expose a
       module-level ``selftest`` callable. Run in a subprocess through
       :mod:`intentops_core.selftests._child`, which prints a delimited
       verdict frame.
    2. **Scripts** under ``scripts/`` whose source advertises ``--selftest``.
       Run in a subprocess as ``python <script> --selftest``.

    Every instrument is reported as ``name | STATUS | duration``, and every
    instrument stays in the DENOMINATOR:

    ==========  ==============================================================
    ``PASS``    the instrument answered, and its answer was good
    ``FAIL``    the instrument answered, and its answer was bad
    ``ERROR``   the instrument could not answer -- import failure, a raise, a
                required argument, or no verdict frame at all
    ``TIMEOUT`` the instrument did not finish inside its bound
    ==========  ==============================================================

    ERROR is never dropped and never coerced to FAIL. "This detector is
    broken" and "this detector found something" are different findings, and a
    registry that merged them would let a decayed instrument hide inside the
    failure count -- which is the exact shape it exists to catch.

    **Headless by construction.** Every child gets ``stdin`` closed. A
    selftest that waits for a terminal reads EOF and reports rather than
    hanging the run: a selftest that needs a TTY cannot run in CI, so it is a
    defect, not a skip.

WRITE MODEL
    None. This module reads the tree, spawns subprocesses and prints. It
    writes no store, keeps no ledger and has no high-water mark -- the
    posture instruments that carry one measure a series over time; this
    measures a tree at an instant, and a ledger here would be a second
    unread store rather than a consumer for the first.

BLIND SPOTS
    - **A module that exposes ``--selftest`` only through its argument parser,
      with no module-level ``selftest`` callable, is invisible to the module
      sweep.** Rather than let that be a silent gap, discovery reports it as
      a FINDING (``Finding.kind == "unreachable"``) and the run exits
      non-zero -- unless the module is declared in :data:`AGGREGATE_CLIS`
      with a reason, which the two aggregate command surfaces are.
    - It proves a selftest RUNS and what it returns. It does not prove the
      selftest asserts anything useful: an instrument whose ``selftest()``
      is ``return True, "ok"`` passes here and proves nothing. That is the
      same floor ``probe_coverage_check`` publishes about probe rows.
    - Discovery reads module TEXT through ``ast``. A ``selftest`` bound
      dynamically (assigned, injected, or re-exported through ``__init__``
      without a definition) is not seen. The direction is the safe one --
      it shrinks the population rather than the pass rate -- but it is a
      floor, not a census.
    - A subprocess per instrument means the run is bounded by process start
      cost, not by the instruments. Nothing here is parallel: two selftests
      planting fixtures in the same repository at the same instant would
      produce a false FAIL, and a false red is more expensive than a slow
      green.
    - ``build/`` directories inside a package are skipped. They hold stale
      copies of shipped modules, and grading a copy would inflate the
      population with instruments nobody runs.

Usage::

    python -m intentops_core.selftests.registry
    python -m intentops_core.selftests.registry --list
    python -m intentops_core.selftests.registry --json
    python -m intentops_core.selftests.registry --only knowledge
    python -m intentops_core.selftests.registry --selftest

    intentops verify --all-selftests
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ._child import FRAME

__all__ = [
    "AGGREGATE_CLIS",
    "DEFAULT_TIMEOUT_S",
    "ERROR",
    "FAIL",
    "Finding",
    "Instrument",
    "PASS",
    "Report",
    "Result",
    "STATUSES",
    "TIMEOUT",
    "build_parser",
    "discover",
    "main",
    "package_roots",
    "repo_root",
    "run_all",
    "run_instrument",
    "selftest",
]

PASS = "PASS"
FAIL = "FAIL"
ERROR = "ERROR"
TIMEOUT = "TIMEOUT"

#: The closed status vocabulary. A result carrying anything else is a bug in
#: this module, not a new kind of answer.
STATUSES: Tuple[str, ...] = (PASS, FAIL, ERROR, TIMEOUT)

#: Per-instrument wall-clock bound. Generous, because a few instruments hash
#: the whole tree, and a timeout that fires on a slow machine turns a green
#: run red for a reason that has nothing to do with the instrument.
DEFAULT_TIMEOUT_S: float = 180.0

#: Modules that advertise ``--selftest`` on an argument parser and own no
#: ``selftest()`` callable, WITH THE REASON that is not a gap.
#:
#: An exemption carries a reason, always. "Will do it later" is a deferral,
#: not a reason -- an unverifiable exemption is worse than none, because it
#: looks accounted for. Anything advertising ``--selftest`` that is not in
#: here and owns no callable is reported as a finding and exits 1.
AGGREGATE_CLIS: Dict[str, str] = {
    "intentops_core.cli": (
        "An aggregate command surface, not an instrument. Its "
        "`verify --selftest` and `gate --selftest` delegate to the genesis "
        "modules and to gate.classify, every one of which is discovered here "
        "in its own right -- so running it would double-count them, not "
        "cover anything new."),
    "intentops_core.loops.cli": (
        "The loops command surface. Its `--selftest` delegates to "
        "intentops_core.loops.{charters,conformance,engine,adapters}, all "
        "four of which are discovered here in their own right."),
}

#: Directory names never walked when looking for instruments.
_SKIP_DIRS = frozenset({"__pycache__", "build", "dist", ".git", ".venv",
                        "node_modules", ".mypy_cache", ".pytest_cache",
                        ".eggs"})


# ---------------------------------------------------------------------------
# the population
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Instrument:
    """One thing that claims it can prove it still fires."""

    name: str            # dotted module path, or a repo-relative script path
    kind: str            # "module" | "script"
    source: str          # repo-relative path to the file that declares it

    def to_row(self) -> Dict[str, str]:
        return {"name": self.name, "kind": self.kind, "source": self.source}


@dataclass(frozen=True)
class Finding:
    """Something discovery noticed that is not an instrument result."""

    kind: str            # "unreachable" | "unparseable"
    subject: str
    detail: str

    def to_row(self) -> Dict[str, str]:
        return {"kind": self.kind, "subject": self.subject,
                "detail": self.detail}


def repo_root() -> Path:
    """The repository this package was imported from.

    ``<repo>/packages/intentops-core/intentops_core/selftests/registry.py``
    -- four parents up from the file, so the answer moves with the file
    rather than being a constant somebody has to remember to correct.
    """
    return Path(__file__).resolve().parents[4]


def package_roots(root: Optional[Path] = None) -> List[Path]:
    """Every ``packages/<dist>/`` that actually contains an importable package.

    Derived from the tree, never listed: a saddle that is documentation only
    contributes nothing and needs no entry anywhere to say so.
    """
    root = Path(root or repo_root())
    found: List[Path] = []
    packages_dir = root / "packages"
    if not packages_dir.is_dir():
        return found
    for dist in sorted(p for p in packages_dir.iterdir() if p.is_dir()):
        for child in sorted(dist.iterdir()):
            if not child.is_dir() or child.name in _SKIP_DIRS:
                continue
            if (child / "__init__.py").is_file():
                found.append(dist)
                break
    return found


def _walk_python(base: Path) -> Iterable[Path]:
    for path in sorted(base.rglob("*.py")):
        if any(part in _SKIP_DIRS for part in path.relative_to(base).parts):
            continue
        yield path


def _module_facts(path: Path) -> Tuple[bool, bool, Optional[str]]:
    """``(has module-level selftest, advertises --selftest, parse error)``."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return False, False, f"unreadable: {exc}"
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        return False, "--selftest" in text, f"unparseable: {exc}"
    has_callable = any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "selftest"
        for node in tree.body)
    return has_callable, "--selftest" in text, None


def _dotted(path: Path, dist: Path) -> str:
    rel = path.relative_to(dist).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def discover(root: Optional[Path] = None
             ) -> Tuple[List[Instrument], List[Finding]]:
    """``([instruments], [findings])`` over the whole tree.

    Pure over its inputs and side-effect free, so the registry's own selftest
    can point it at a planted tree and get a real answer.
    """
    root = Path(root or repo_root())
    instruments: List[Instrument] = []
    findings: List[Finding] = []

    for dist in package_roots(root):
        for path in _walk_python(dist):
            dotted = _dotted(path, dist)
            # A private module is implementation, not an instrument. The
            # check is per SEGMENT, not on the head: `pkg._child` is as
            # private as `_pkg`, and reading only the head is how this
            # module's own subprocess harness -- whose docstring names
            # ``--selftest`` -- got reported as an unreachable instrument.
            if not dotted or any(part.startswith("_")
                                 for part in dotted.split(".")):
                continue
            has_callable, advertises, err = _module_facts(path)
            rel = path.relative_to(root).as_posix()
            if err:
                findings.append(Finding("unparseable", rel, err))
            if has_callable:
                instruments.append(Instrument(dotted, "module", rel))
            elif advertises and dotted not in AGGREGATE_CLIS:
                findings.append(Finding(
                    "unreachable", dotted,
                    "advertises --selftest but exposes no module-level "
                    "selftest() callable, so the registry cannot run it. "
                    "Add the callable, or declare it in AGGREGATE_CLIS with "
                    "a reason."))

    scripts_dir = root / "scripts"
    if scripts_dir.is_dir():
        for path in _walk_python(scripts_dir):
            rel = path.relative_to(root).as_posix()
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                findings.append(Finding("unparseable", rel, f"unreadable: {exc}"))
                continue
            if "--selftest" in text:
                instruments.append(Instrument(rel, "script", rel))

    instruments.sort(key=lambda i: (i.kind, i.name))
    findings.sort(key=lambda f: (f.kind, f.subject))
    return instruments, findings


# ---------------------------------------------------------------------------
# running one
# ---------------------------------------------------------------------------


@dataclass
class Result:
    instrument: Instrument
    status: str
    duration_s: float
    report: str = ""
    returncode: Optional[int] = None

    @property
    def ok(self) -> bool:
        return self.status == PASS

    def to_row(self) -> Dict[str, object]:
        row: Dict[str, object] = self.instrument.to_row()
        row.update(status=self.status,
                   duration_s=round(self.duration_s, 3),
                   returncode=self.returncode,
                   report=self.report)
        return row


#: The package root this registry itself was imported from. The child
#: harness lives inside it, so it is on every child's path REGARDLESS of
#: which tree is being swept -- otherwise pointing ``--root`` at another
#: repository (which is exactly what the registry's own selftest does)
#: produces a child that cannot import the thing that runs it, and every
#: instrument reads ERROR for a reason that has nothing to do with it.
_OWN_PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def _child_env(root: Path) -> Dict[str, str]:
    env = dict(os.environ)
    roots = [str(p) for p in package_roots(root)]
    if str(_OWN_PACKAGE_ROOT) not in roots:
        roots.append(str(_OWN_PACKAGE_ROOT))
    existing = env.get("PYTHONPATH", "")
    if existing:
        roots.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(roots)
    # Deterministic, decodable output from a child on any host encoding.
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    # A child that thinks it is on a terminal may try to prompt. Nothing here
    # can answer it, so say so up front rather than discovering it at the
    # timeout.
    env["INTENTOPS_NONINTERACTIVE"] = "1"
    return env


def _last_line(text: str, limit: int = 400) -> str:
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if line:
            return line[:limit]
    return ""


def _parse_frame(stdout: str) -> Optional[Dict[str, object]]:
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if line.startswith(FRAME):
            try:
                payload = json.loads(line[len(FRAME):].strip())
            except (ValueError, TypeError):
                return None
            return payload if isinstance(payload, dict) else None
    return None


def run_instrument(instrument: Instrument,
                   root: Optional[Path] = None,
                   timeout: float = DEFAULT_TIMEOUT_S,
                   python: Optional[str] = None) -> Result:
    """Run one instrument's selftest in a subprocess and grade the answer."""
    root = Path(root or repo_root())
    exe = python or sys.executable
    if instrument.kind == "module":
        argv = [exe, "-m", "intentops_core.selftests._child", instrument.name]
    else:
        argv = [exe, str(root / instrument.name), "--selftest"]

    started = time.monotonic()
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            argv, cwd=str(root), env=_child_env(root),
            stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return Result(instrument, TIMEOUT, time.monotonic() - started,
                      report=f"no answer within {timeout:g}s. A selftest that "
                             f"cannot finish headless is a defect, not a skip.")
    except OSError as exc:
        return Result(instrument, ERROR, time.monotonic() - started,
                      report=f"could not start: {exc}")
    duration = time.monotonic() - started

    if instrument.kind == "module":
        payload = _parse_frame(proc.stdout)
        if payload is None:
            detail = _last_line(proc.stderr) or _last_line(proc.stdout)
            return Result(instrument, ERROR, duration,
                          report="no verdict frame on stdout"
                                 + (f" -- {detail}" if detail else ""),
                          returncode=proc.returncode)
        ok = payload.get("ok")
        report = str(payload.get("report") or "")
        if ok is None:
            return Result(instrument, ERROR, duration,
                          report=report or "the child returned no verdict",
                          returncode=proc.returncode)
        return Result(instrument, PASS if ok else FAIL, duration,
                      report=report, returncode=proc.returncode)

    # A script's --selftest convention is 0 pass / 1 fail. Any other code is
    # the script telling us something the convention has no word for, and
    # that is ERROR rather than a guess.
    if proc.returncode == 0:
        return Result(instrument, PASS, duration,
                      report=_last_line(proc.stdout), returncode=0)
    if proc.returncode == 1:
        return Result(instrument, FAIL, duration,
                      report=_last_line(proc.stdout) or _last_line(proc.stderr),
                      returncode=1)
    return Result(instrument, ERROR, duration,
                  report=f"exit {proc.returncode}: "
                         + (_last_line(proc.stderr) or _last_line(proc.stdout)
                            or "no output"),
                  returncode=proc.returncode)


# ---------------------------------------------------------------------------
# running all of them
# ---------------------------------------------------------------------------


@dataclass
class Report:
    results: List[Result] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)
    duration_s: float = 0.0

    def count(self, status: str) -> int:
        return len([r for r in self.results if r.status == status])

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def ok(self) -> bool:
        """Green iff every instrument passed and discovery found no gap.

        An ERROR is not forgiven for being an ERROR. An instrument that
        cannot run is exactly as red as one that ran and failed -- the
        registry's whole reason for existing is that the first kind is the
        one nobody notices.
        """
        return (self.total > 0
                and self.count(PASS) == self.total
                and not self.findings)

    @property
    def summary(self) -> str:
        return (f"{self.count(PASS)}/{self.total} PASS, "
                f"{self.count(FAIL)} FAIL, {self.count(ERROR)} ERROR, "
                f"{self.count(TIMEOUT)} TIMEOUT, "
                f"{len(self.findings)} discovery finding(s), "
                f"{self.duration_s:.1f}s")

    def to_row(self) -> Dict[str, object]:
        return {"summary": self.summary,
                "total": self.total,
                "counts": {s: self.count(s) for s in STATUSES},
                "duration_s": round(self.duration_s, 3),
                "ok": self.ok,
                "results": [r.to_row() for r in self.results],
                "findings": [f.to_row() for f in self.findings]}

    def render(self) -> str:
        width = max([len(r.instrument.name) for r in self.results] + [4])
        lines = [f"{'instrument'.ljust(width)}  status   duration",
                 f"{'-' * width}  -------  --------"]
        for result in self.results:
            lines.append(f"{result.instrument.name.ljust(width)}  "
                         f"{result.status.ljust(7)}  "
                         f"{result.duration_s:7.2f}s")
        bad = [r for r in self.results if r.status != PASS]
        if bad:
            lines.append("")
            lines.append("NOT PASSING -- every one stays in the denominator:")
            for result in bad:
                lines.append(f"  {result.status} {result.instrument.name}")
                if result.report:
                    lines.append(f"      {result.report.splitlines()[0][:200]}")
        if self.findings:
            lines.append("")
            lines.append("DISCOVERY FINDINGS:")
            for finding in self.findings:
                lines.append(f"  {finding.kind}: {finding.subject}")
                lines.append(f"      {finding.detail}")
        lines.append("")
        lines.append(self.summary)
        return "\n".join(lines)


def run_all(root: Optional[Path] = None,
            timeout: float = DEFAULT_TIMEOUT_S,
            only: Optional[str] = None,
            python: Optional[str] = None,
            instruments: Optional[Sequence[Instrument]] = None,
            findings: Optional[Sequence[Finding]] = None,
            on_result=None) -> Report:
    """Run every discovered instrument, in order, one at a time."""
    root = Path(root or repo_root())
    if instruments is None:
        instruments, discovered = discover(root)
        findings = discovered if findings is None else findings
    selected = [i for i in instruments
                if not only or only.lower() in i.name.lower()]
    report = Report(findings=list(findings or []))
    started = time.monotonic()
    for instrument in selected:
        result = run_instrument(instrument, root=root, timeout=timeout,
                                python=python)
        report.results.append(result)
        if on_result is not None:
            on_result(result)
    report.duration_s = time.monotonic() - started
    return report


# ---------------------------------------------------------------------------
# the registry's own selftest
# ---------------------------------------------------------------------------


_PLANTS: Dict[str, str] = {
    "good": "def selftest():\n    return True, 'planted: passes'\n",
    "bad": "def selftest():\n    return False, 'planted: fails'\n",
    "raiser": "def selftest():\n    raise RuntimeError('planted: raises')\n",
    "silent": "def selftest():\n    return None\n",
    "needs_arg": "def selftest(root):\n    return True, root\n",
    "slow": ("import time\n\n\n"
             "def selftest():\n    time.sleep(30)\n    return True, 'never'\n"),
    "tty": ("import sys\n\n\n"
            "def selftest():\n"
            "    line = sys.stdin.readline()\n"
            "    if not line:\n"
            "        raise RuntimeError('planted: needed a TTY')\n"
            "    return True, 'read a line'\n"),
    "advertises_only": ("import argparse\n\n\n"
                        "def main():\n"
                        "    ap = argparse.ArgumentParser()\n"
                        "    ap.add_argument('--selftest')\n"),
}

_SCRIPT_PLANTS: Dict[str, str] = {
    "script_good": ("import sys\n"
                    "if '--selftest' in sys.argv:\n"
                    "    print('planted script: passes')\n"
                    "    raise SystemExit(0)\n"),
    "script_bad": ("import sys\n"
                   "if '--selftest' in sys.argv:\n"
                   "    print('planted script: fails')\n"
                   "    raise SystemExit(1)\n"),
    "script_odd": ("import sys\n"
                   "if '--selftest' in sys.argv:\n"
                   "    print('planted script: neither')\n"
                   "    raise SystemExit(7)\n"),
}


def _plant(root: Path) -> None:
    pkg = root / "packages" / "planted" / "planted_pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    for name, body in _PLANTS.items():
        (pkg / f"{name}.py").write_text(body, encoding="utf-8")
    # A stale copy under build/ must NOT enter the population.
    stale = root / "packages" / "planted" / "build" / "lib" / "planted_pkg"
    stale.mkdir(parents=True, exist_ok=True)
    (stale / "__init__.py").write_text("", encoding="utf-8")
    (stale / "good.py").write_text(_PLANTS["good"], encoding="utf-8")
    # A documentation-only distribution contributes nothing.
    (root / "packages" / "docs-only").mkdir(parents=True, exist_ok=True)
    (root / "packages" / "docs-only" / "README.md").write_text(
        "no code here\n", encoding="utf-8")
    scripts = root / "scripts" / "planted"
    scripts.mkdir(parents=True, exist_ok=True)
    for name, body in _SCRIPT_PLANTS.items():
        (scripts / f"{name}.py").write_text(body, encoding="utf-8")


def selftest() -> Tuple[bool, str]:
    """Plant instruments of every shape and prove each status can fire.

    A registry whose own detector has never fired is exactly the thing it
    exists to catch, so this plants a passing instrument, a FAILING one, one
    that raises, one that returns nothing, one that demands an argument, one
    that never finishes, one that waits for a terminal, and a module that
    advertises ``--selftest`` without owning a callable -- then proves the
    registry reports each as declared and, above all, that the erroring ones
    stay in the denominator.
    """
    import tempfile

    failures: List[str] = []
    fired: List[str] = []

    def expect(label: str, cond: bool) -> None:
        (fired if cond else failures).append(label)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _plant(root)
        instruments, findings = discover(root)
        names = {i.name for i in instruments}

        expect("modules-discovered",
               {"planted_pkg.good", "planted_pkg.bad", "planted_pkg.raiser",
                "planted_pkg.silent", "planted_pkg.needs_arg"} <= names)
        expect("scripts-discovered",
               {"scripts/planted/script_good.py",
                "scripts/planted/script_bad.py"} <= names)
        expect("stale-build-copy-excluded",
               not any(".build." in n or "/build/" in n for n in names))
        expect("advertises-without-callable-is-a-finding",
               any(f.kind == "unreachable"
                   and f.subject == "planted_pkg.advertises_only"
                   for f in findings))
        expect("advertising-module-is-not-an-instrument",
               "planted_pkg.advertises_only" not in names)

        # The planted tree is not an installable package for the child, so
        # point the child's import path at it explicitly by running from the
        # planted root -- package_roots() derives it.
        chosen = [i for i in instruments
                  if i.name in {"planted_pkg.good", "planted_pkg.bad",
                                "planted_pkg.raiser", "planted_pkg.silent",
                                "planted_pkg.needs_arg",
                                "scripts/planted/script_good.py",
                                "scripts/planted/script_bad.py",
                                "scripts/planted/script_odd.py"}]
        report = run_all(root=root, timeout=90.0, instruments=chosen,
                         findings=findings)
        status = {r.instrument.name: r.status for r in report.results}

        expect("a-passing-instrument-reports-PASS",
               status.get("planted_pkg.good") == PASS)
        expect("a-failing-instrument-reports-FAIL",
               status.get("planted_pkg.bad") == FAIL)
        expect("a-raising-instrument-reports-ERROR",
               status.get("planted_pkg.raiser") == ERROR)
        expect("a-verdictless-instrument-reports-ERROR",
               status.get("planted_pkg.silent") == ERROR)
        expect("a-required-argument-reports-ERROR",
               status.get("planted_pkg.needs_arg") == ERROR)
        expect("a-passing-script-reports-PASS",
               status.get("scripts/planted/script_good.py") == PASS)
        expect("a-failing-script-reports-FAIL",
               status.get("scripts/planted/script_bad.py") == FAIL)
        expect("an-off-convention-exit-reports-ERROR",
               status.get("scripts/planted/script_odd.py") == ERROR)
        expect("errors-stay-in-the-denominator",
               report.total == len(chosen))
        expect("a-planted-failure-turns-the-report-red", not report.ok)
        expect("every-status-is-in-the-closed-vocabulary",
               all(r.status in STATUSES for r in report.results))

        # TIMEOUT and the TTY defect, on their own short bound so proving
        # them costs seconds rather than the full timeout.
        slow = [i for i in instruments if i.name == "planted_pkg.slow"]
        timed = run_all(root=root, timeout=2.0, instruments=slow, findings=[])
        expect("a-wedged-instrument-reports-TIMEOUT",
               [r.status for r in timed.results] == [TIMEOUT])

        tty = [i for i in instruments if i.name == "planted_pkg.tty"]
        headless = run_all(root=root, timeout=20.0, instruments=tty,
                           findings=[])
        expect("a-selftest-that-wants-a-TTY-reports-rather-than-hangs",
               [r.status for r in headless.results] == [ERROR])

        # A clean tree with no findings and only passes is green.
        green = run_all(root=root, timeout=90.0,
                        instruments=[i for i in instruments
                                     if i.name == "planted_pkg.good"],
                        findings=[])
        expect("a-clean-run-is-green", green.ok)

    report_line = (f"selftests.registry selftest: {len(fired)} paths fired, "
                   f"{len(failures)} failed"
                   + (" -- " + ", ".join(failures) if failures else ""))
    return (not failures, report_line)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="intentops-selftests",
        description="Run every instrument's selftest, and report each as "
                    "name | status | duration. An instrument that cannot run "
                    "stays in the denominator as ERROR.")
    ap.add_argument("--root", default=None,
                    help="the repository to sweep (default: this one)")
    ap.add_argument("--only", default=None,
                    help="run only instruments whose name contains this")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S,
                    help=f"per-instrument seconds (default {DEFAULT_TIMEOUT_S:g})")
    ap.add_argument("--list", action="store_true",
                    help="print the discovered population and exit")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true",
                    help="prove this registry reports a planted failure")
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.selftest:
        ok, report = selftest()
        print(report)
        return 0 if ok else 1

    root = Path(args.root) if args.root else repo_root()
    instruments, findings = discover(root)

    if args.list:
        selected = [i for i in instruments
                    if not args.only or args.only.lower() in i.name.lower()]
        if args.json:
            print(json.dumps({"instruments": [i.to_row() for i in selected],
                              "findings": [f.to_row() for f in findings]},
                             indent=2))
        else:
            for instrument in selected:
                print(f"{instrument.kind:7} {instrument.name}")
            print(f"\n{len(selected)} instrument(s), "
                  f"{len(findings)} discovery finding(s)")
        return 0 if not findings else 1

    def _tick(result: Result) -> None:
        if not args.json:
            print(f"{result.status:7} {result.duration_s:6.2f}s  "
                  f"{result.instrument.name}", flush=True)

    report = run_all(root=root, timeout=args.timeout, only=args.only,
                     instruments=instruments, findings=findings,
                     on_result=_tick)
    if args.json:
        print(json.dumps(report.to_row(), indent=2))
    else:
        print()
        print(report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
