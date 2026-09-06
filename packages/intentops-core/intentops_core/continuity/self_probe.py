"""Self-model fidelity probe suite -- can a fresh window still find each organ?

PURPOSE. Code rung: stdlib plus YAML. No model, no network. The suite measures
whether the BOOT CORPUS -- the files whose content reaches a fresh context
window without any tool call -- can answer the canonical self-questions declared
in a probe suite (at birth, ``config/genesis-probes.yaml``).

A node proves it is alive by ANSWERING, not by asserting. This module is the
first of the birth checks: every probe names the question it asks, the literal
strings whose presence would answer it, and the source of truth those strings
must still be found in.

THE TWO FAILURE CLASSES, KEPT APART. This distinction is the whole point of the
suite and must never collapse into a single "failed" number:

``ABSENT``  a COVERAGE failure. The fact never reaches a fresh window -- the
            boot corpus lost the anchor. The organ may be perfectly healthy.
``FAIL``    a TRUTH failure: the organ the probe names has MOVED or is GONE.
``DRIFT``   a TRUTH failure: the organ is still there, but it no longer carries
            the probe string the boot corpus claims it does.
``STALE``   a TRUTH failure of the temporal kind: a freshness bound was exceeded.
``ERROR``   the probe could not be evaluated. LOUD, never silent, and it stays
            in the denominator.
``PASS``    the corpus carries the fact and every truth check still holds.

``failure_class()`` maps a status to ``"coverage"`` or ``"truth"``, and
``SuiteResult`` reports both counts separately. Reading an ABSENT as a FAIL
sends someone to repair an organ that was never broken; reading a FAIL as an
ABSENT sends them to edit a document instead of the thing it describes.

WRITE MODEL. None shared. ``main`` writes one report file per run
(create-or-replace, single writer, no fold). The suite itself is a pure read.

REFUSALS (no load-bearing field gets a default):

* a suite defining ZERO probes is a ``ValueError``. An empty population scores
  100% by construction, which is the most flattering possible reading of a
  file nobody wrote.
* an UNDECLARED boot-corpus group key is a ``ValueError``. A group nobody reads
  makes every probe over it report ABSENT for a reason that has nothing to do
  with the boot corpus.
* an ``identity_repo`` group with no identity-repo path bound is a
  ``ValueError``, not an empty read.
* a probe defining no check at all is ``ERROR``, never PASS.

BLIND SPOTS.

1. Matching is case-insensitive SUBSTRING. A short probe string can false-pass
   on an unrelated occurrence; keep probe strings distinctive.
2. The suite reads FILES. It cannot see anything a host runtime injects into a
   window at run time, so a fact carried only by an injection is invisible here
   and will read ABSENT.
3. A PASS means the corpus carries the literal string. It does not mean the
   sentence around it is true, or that a reader would understand it.
4. Freshness checks read modification time or one declared JSON field. A file
   rewritten with no content change reads fresh.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

__all__ = [
    "ProbeResult", "SuiteResult", "run_suite", "assemble_corpus",
    "failure_class", "STATUSES", "KNOWN_CORPUS_GROUPS", "selftest", "main",
]

DEFAULT_PROBES = Path("config") / "genesis-probes.yaml"
DEFAULT_REPORT = Path(".intentops") / "genesis" / "self-probe-report.json"
DEFAULT_REGISTER = Path("identity") / "wishes" / "REGISTER.yaml"

STATUSES: Tuple[str, ...] = ("PASS", "ABSENT", "FAIL", "DRIFT", "STALE", "ERROR")
#: ABSENT is a coverage failure; FAIL/DRIFT/STALE/ERROR are truth failures.
_COVERAGE_FAILURES = frozenset({"ABSENT"})
_TRUTH_FAILURES = frozenset({"FAIL", "DRIFT", "STALE", "ERROR"})
KNOWN_CORPUS_GROUPS: Tuple[str, ...] = (
    "workspace", "boot_adjacent", "home", "memory", "identity_repo")


def failure_class(status: str) -> Optional[str]:
    """``"coverage"`` | ``"truth"`` | ``None``. See the module docstring: these
    are two different findings and must never be summed."""
    if status in _COVERAGE_FAILURES:
        return "coverage"
    if status in _TRUTH_FAILURES:
        return "truth"
    return None


@dataclass
class ProbeResult:
    id: str
    category: str
    question: str
    status: str
    detail: str = ""

    @property
    def failure_class(self) -> Optional[str]:
        return failure_class(self.status)


@dataclass
class SuiteResult:
    results: List[ProbeResult]
    corpus_files: List[str]
    corpus_bytes: int
    corpus_errors: List[str]
    corpus_label: str = "files"
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def fidelity(self) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.status == "PASS") / len(self.results)

    @property
    def coverage_failures(self) -> List[ProbeResult]:
        """ABSENT: the boot corpus lost the anchor."""
        return [r for r in self.results if r.failure_class == "coverage"]

    @property
    def truth_failures(self) -> List[ProbeResult]:
        """DRIFT / STALE / ERROR: the thing the corpus describes moved."""
        return [r for r in self.results if r.failure_class == "truth"]

    @property
    def by_category(self) -> Dict[str, Dict[str, int]]:
        cats: Dict[str, Dict[str, int]] = {}
        for r in self.results:
            bucket = cats.setdefault(r.category, {"pass": 0, "total": 0})
            bucket["total"] += 1
            if r.status == "PASS":
                bucket["pass"] += 1
        return cats

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": "self-probe-report/v1",
            "corpus_source": self.corpus_label,
            "generated_at": self.generated_at,
            # the rate always travels with its denominator
            "passed": sum(1 for r in self.results if r.status == "PASS"),
            "population": len(self.results),
            "fidelity": round(self.fidelity, 4),
            "coverage_failures": len(self.coverage_failures),
            "truth_failures": len(self.truth_failures),
            "by_category": self.by_category,
            "corpus": {
                "files": self.corpus_files,
                "bytes": self.corpus_bytes,
                "approx_tokens": self.corpus_bytes // 4,
                "errors": self.corpus_errors,
            },
            "probes": [
                {"id": r.id, "category": r.category, "question": r.question,
                 "status": r.status, "failure_class": r.failure_class,
                 "detail": r.detail}
                for r in self.results
            ],
        }


# ---------------------------------------------------------------------------
# corpus assembly
# ---------------------------------------------------------------------------


def _expand_entry(base: Path, entry: str, errors: List[str]) -> List[Path]:
    """Resolve one corpus entry. Globs may match nothing; a plain path must
    exist, and a missing one is a LOUD corpus error."""
    if any(ch in entry for ch in "*?["):
        return sorted(p for p in base.glob(entry) if p.is_file())
    path = base / entry
    if not path.is_file():
        errors.append(f"corpus: configured file missing: {path}")
        return []
    return [path]


def assemble_corpus(
    workspace: Path,
    spec: Dict[str, Any],
    *,
    home: Optional[Path] = None,
    memory_dir: Optional[Path] = None,
    identity_repo: Optional[Path] = None,
) -> Tuple[Dict[str, str], List[str]]:
    """Read every boot-corpus file. Unreadable files are LOUD.

    An undeclared group key raises: a group nobody reads would make every probe
    over it report ABSENT for a reason unrelated to the boot corpus.
    """
    errors: List[str] = []
    files: List[Path] = []
    groups = spec or {}
    unknown = sorted(set(groups) - set(KNOWN_CORPUS_GROUPS))
    if unknown:
        raise ValueError(
            f"boot_corpus declares undeclared group(s) {unknown}; known groups "
            f"are {list(KNOWN_CORPUS_GROUPS)}. There is no default -- a group "
            "nobody reads makes every probe over it report ABSENT for the "
            "wrong reason")
    # Resolve the home directory LAZILY, and only when a home-group entry is
    # actually declared. ``Path.home()`` raises RuntimeError on a host with no
    # resolvable home (a stripped CI container, a service account with no
    # USERPROFILE/HOME), and a probe run that CRASHES tells the operator less
    # than one that reports the corpus error and grades the rest.
    unresolvable: set = set()
    if home is None and (groups.get("home") or []):
        try:
            home = Path.home()
        except RuntimeError:
            errors.append(
                "corpus: home directory unresolvable and a home-group entry "
                "is declared")
            unresolvable.add("home")
    bases: Dict[str, Optional[Path]] = {
        "workspace": Path(workspace),
        "boot_adjacent": Path(workspace),
        "home": home,
        "memory": Path(memory_dir) if memory_dir else None,
        "identity_repo": Path(identity_repo) if identity_repo else None,
    }
    for group in KNOWN_CORPUS_GROUPS:
        entries = groups.get(group) or []
        if not entries:
            continue
        base = bases[group]
        if base is None and group in unresolvable:
            # Already surfaced above as a LOUD corpus error. Skipping the group
            # keeps the rest of the corpus readable; the error is what grades.
            continue
        if base is None:
            raise ValueError(
                f"boot_corpus names an '{group}' group but no {group} path is "
                "bound; refusing to read an empty corpus and call it a reading")
        for entry in entries:
            files.extend(_expand_entry(base, str(entry), errors))

    corpus: Dict[str, str] = {}
    for path in files:
        try:
            corpus[str(path)] = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            errors.append(f"corpus: unreadable {path} ({exc})")
    return corpus, errors


# ---------------------------------------------------------------------------
# probe evaluation
# ---------------------------------------------------------------------------


def _in_boot(corpus_lower: str, literal: str) -> bool:
    return str(literal).lower() in corpus_lower


def _check_expect(probe: Dict[str, Any], corpus_lower: str) -> Optional[str]:
    """Return an ABSENT detail string, or None when the expectation holds."""
    expect = probe.get("expect_in_boot") or {}
    missing = [lit for lit in expect.get("all_of") or []
               if not _in_boot(corpus_lower, lit)]
    any_of = expect.get("any_of") or []
    if any_of and not any(_in_boot(corpus_lower, lit) for lit in any_of):
        missing.extend(any_of)
    if missing:
        return "not in boot corpus: " + "; ".join(repr(m) for m in missing)
    return None


def _check_freshness(probe: Dict[str, Any],
                     workspace: Path) -> Optional[Tuple[str, str]]:
    """Return ``(status, detail)`` on failure, or None when fresh."""
    fresh = probe.get("freshness") or {}
    target = Path(workspace) / fresh.get("path", "")
    max_age = float(fresh.get("max_age_hours", 0) or 0)
    if not target.is_file():
        return "ERROR", f"freshness target missing: {target}"
    try:
        if fresh.get("json_field"):
            data = json.loads(target.read_text(encoding="utf-8"))
            stamp = datetime.fromisoformat(str(data.get(fresh["json_field"], "")))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
        else:
            stamp = datetime.fromtimestamp(target.stat().st_mtime, tz=timezone.utc)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return "ERROR", f"freshness unreadable ({exc}): {target}"
    age_hours = (datetime.now(timezone.utc) - stamp).total_seconds() / 3600.0
    if age_hours > max_age:
        return "STALE", (f"age {age_hours:.1f}h exceeds max {max_age:g}h "
                         f"({target.name})")
    return None


def _check_truth(probe: Dict[str, Any],
                 workspace: Path) -> Optional[Tuple[str, str]]:
    """Return ``(status, detail)`` on a truth failure, or None when every check
    holds.

    The two truth failures are kept apart: an organ that has MOVED or is GONE is
    ``FAIL``; an organ still present that no longer carries the string the boot
    corpus claims is ``DRIFT``. ``FAIL`` wins when both occur, because a missing
    organ is the larger finding.
    """
    gone: List[str] = []
    drifted: List[str] = []
    for truth in probe.get("truth") or []:
        target = Path(workspace) / truth.get("path", "")
        if not target.is_file():
            gone.append(f"truth path missing: {truth.get('path')}")
            continue
        contains = truth.get("contains")
        if contains:
            try:
                text = target.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                gone.append(f"truth unreadable ({exc}): {truth.get('path')}")
                continue
            if str(contains).lower() not in text.lower():
                drifted.append(
                    f"truth lost probe string {contains!r}: {truth.get('path')}")
    if gone:
        return "FAIL", "; ".join(gone + drifted)
    if drifted:
        return "DRIFT", "; ".join(drifted)
    return None


def _check_register_head(probe: Dict[str, Any], workspace: Path,
                         corpus_lower: str) -> Tuple[str, str]:
    """The newest register wish's SLUG must appear in the boot corpus.

    The register is append-only, so the newest wish is the last entry. Matching
    is case-insensitive substring: a very short slug could false-pass, which is
    blind spot 1 restated here where it bites.
    """
    register = Path(workspace) / probe.get("register_path", str(DEFAULT_REGISTER))
    try:
        data = yaml.safe_load(register.read_text(encoding="utf-8")) or {}
        wishes = data.get("wishes") or []
        head_id = str(wishes[-1]["id"])
        slug = "-".join(head_id.split("-")[4:])
        if not slug:
            raise ValueError(f"head id {head_id!r} has no slug")
    except (OSError, yaml.YAMLError, ValueError, KeyError, IndexError, TypeError) as exc:
        return "ERROR", f"register head unreadable ({exc}): {register}"
    if _in_boot(corpus_lower, slug):
        return "PASS", f"newest wish {head_id} (slug {slug!r}) is in the boot corpus"
    return "ABSENT", (f"newest wish {head_id} (slug {slug!r}) never reaches a "
                      "fresh window")


def _evaluate(probe: Dict[str, Any], workspace: Path,
              corpus_lower: str) -> ProbeResult:
    pid = str(probe.get("id", "<no id>"))
    category = str(probe.get("category", "uncategorized"))
    question = str(probe.get("question", ""))

    def done(status: str, detail: str = "") -> ProbeResult:
        return ProbeResult(pid, category, question, status, detail)

    if not any(k in probe for k in ("expect_in_boot", "freshness", "dynamic")):
        return done("ERROR",
                    "probe defines no check (expect_in_boot/freshness/dynamic)")

    if probe.get("dynamic"):
        kind = probe["dynamic"]
        if kind != "register_head_slug":
            return done("ERROR", f"unknown dynamic check {kind!r}")
        status, detail = _check_register_head(probe, workspace, corpus_lower)
        if status == "PASS":
            truth = _check_truth(probe, workspace)
            if truth:
                return done(truth[0], truth[1])
        return done(status, detail)

    if "expect_in_boot" in probe:
        absent = _check_expect(probe, corpus_lower)
        if absent:
            return done("ABSENT", absent)
    if "freshness" in probe:
        failed = _check_freshness(probe, workspace)
        if failed:
            return done(failed[0], failed[1])
    truth = _check_truth(probe, workspace)
    if truth:
        return done(truth[0], truth[1])
    return done("PASS")


def run_suite(
    workspace: Path | str,
    probes_path: Path | str = DEFAULT_PROBES,
    *,
    home: Optional[Path] = None,
    memory_dir: Optional[Path] = None,
    identity_repo: Optional[Path] = None,
    corpus_override: Optional[Tuple[Dict[str, str], List[str]]] = None,
    corpus_label: str = "files",
) -> SuiteResult:
    """Run the suite.

    ``corpus_override=(corpus, errors)`` substitutes an externally assembled
    boot corpus for the file-based one; truth and freshness checks always run
    against the workspace either way.

    Raises ``ValueError`` when the suite defines zero probes -- an empty
    population scores 100% by construction, which is the most flattering
    possible reading of a file nobody wrote.
    """
    workspace = Path(workspace)
    probes_path = Path(probes_path)
    if not probes_path.is_absolute():
        probes_path = workspace / probes_path
    doc = yaml.safe_load(probes_path.read_text(encoding="utf-8")) or {}
    if not isinstance(doc, dict):
        raise ValueError(f"probe suite root must be a mapping: {probes_path}")
    probes = doc.get("probes") or []
    if not probes:
        raise ValueError(f"probe suite defines zero probes: {probes_path}")
    if corpus_override is not None:
        corpus, corpus_errors = corpus_override
    else:
        corpus, corpus_errors = assemble_corpus(
            workspace, doc.get("boot_corpus") or {},
            home=home, memory_dir=memory_dir, identity_repo=identity_repo)
    corpus_lower = "\n".join(corpus.values()).lower()
    results = [_evaluate(p, workspace, corpus_lower) for p in probes]
    return SuiteResult(
        results=results,
        corpus_files=sorted(corpus),
        corpus_bytes=sum(len(t.encode("utf-8", "replace")) for t in corpus.values()),
        corpus_errors=list(corpus_errors),
        corpus_label=corpus_label,
    )


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


def selftest() -> Tuple[bool, str]:
    """Prove every status can fire, that the two failure classes stay apart,
    and that each refusal actually refuses."""
    import tempfile

    f: List[str] = []

    def check(label: str, cond: bool) -> None:
        if not cond:
            f.append(label)

    def raises(label: str, fn) -> None:
        try:
            fn()
        except ValueError:
            return
        except Exception as exc:  # pragma: no cover - defensive
            f.append(f"{label} (raised {exc.__class__.__name__}, wanted ValueError)")
            return
        f.append(label)

    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        (ws / "config").mkdir()
        (ws / "boot").mkdir()
        (ws / "src").mkdir()
        (ws / "boot" / "anchors.md").write_text(
            "the gate lives at src/gate.py and it can refuse\n", encoding="utf-8")
        (ws / "src" / "gate.py").write_text("# the gate\nVERDICT = 'deny'\n",
                                            encoding="utf-8")
        (ws / "stale.json").write_text(
            json.dumps({"as_of": "2000-01-01T00:00:00+00:00"}), encoding="utf-8")

        def write_suite(probes: List[Dict[str, Any]],
                        corpus: Optional[Dict[str, Any]] = None) -> Path:
            p = ws / "config" / "genesis-probes.yaml"
            p.write_text(yaml.safe_dump({
                "schema": "self-probe/v1",
                "boot_corpus": corpus if corpus is not None
                else {"workspace": ["boot/anchors.md"]},
                "probes": probes,
            }, sort_keys=False), encoding="utf-8")
            return p

        # -- refusals ------------------------------------------------------
        raises("a suite defining zero probes was accepted",
               lambda: run_suite(ws, write_suite([])))
        raises("an undeclared boot-corpus group was accepted",
               lambda: run_suite(ws, write_suite(
                   [{"id": "P", "expect_in_boot": {"all_of": ["gate"]}}],
                   corpus={"memory": ["*.md"]})))
        raises("an identity_repo group with no repo bound was accepted",
               lambda: run_suite(ws, write_suite(
                   [{"id": "P", "expect_in_boot": {"all_of": ["gate"]}}],
                   corpus={"identity_repo": ["MANIFEST.yaml"]})))

        # -- every status fires --------------------------------------------
        suite = run_suite(ws, write_suite([
            {"id": "GEN-pass", "category": "gate",
             "question": "where is the gate",
             "expect_in_boot": {"all_of": ["src/gate.py"]},
             "truth": [{"path": "src/gate.py", "contains": "VERDICT"}]},
            {"id": "GEN-absent", "category": "gate",
             "question": "a fact the corpus never carries",
             "expect_in_boot": {"all_of": ["a string nobody wrote"]}},
            {"id": "GEN-drift", "category": "gate",
             "question": "the organ is there but lost the probe string",
             "expect_in_boot": {"all_of": ["src/gate.py"]},
             "truth": [{"path": "src/gate.py", "contains": "a vanished token"}]},
            {"id": "GEN-fail", "category": "gate",
             "question": "the organ the corpus names has moved or is gone",
             "expect_in_boot": {"all_of": ["src/gate.py"]},
             "truth": [{"path": "src/moved_away.py", "contains": "anything"}]},
            {"id": "GEN-stale", "category": "freshness",
             "question": "is the reading fresh",
             "freshness": {"path": "stale.json", "json_field": "as_of",
                           "max_age_hours": 1}},
            {"id": "GEN-error", "category": "gate",
             "question": "a probe that declares no check"},
        ]))
        got = {r.id: r.status for r in suite.results}
        for pid, want in (("GEN-pass", "PASS"), ("GEN-absent", "ABSENT"),
                          ("GEN-drift", "DRIFT"), ("GEN-fail", "FAIL"),
                          ("GEN-stale", "STALE"), ("GEN-error", "ERROR")):
            check(f"{want} did not fire ({pid} read {got.get(pid)})",
                  got.get(pid) == want)

        check("the coverage/truth split collapsed: wanted 1 coverage failure",
              len(suite.coverage_failures) == 1)
        check("the coverage/truth split collapsed: wanted 4 truth failures",
              len(suite.truth_failures) == 4)
        check("ABSENT must not be graded a truth failure",
              failure_class("ABSENT") == "coverage")
        check("FAIL must not be graded a coverage failure",
              failure_class("FAIL") == "truth")
        check("DRIFT must not be graded a coverage failure",
              failure_class("DRIFT") == "truth")
        check("PASS must have no failure class", failure_class("PASS") is None)
        d = suite.to_dict()
        check("the report omits the denominator beside the rate",
              d["population"] == 6 and d["passed"] == 1)

        # -- a missing corpus file is LOUD ---------------------------------
        suite2 = run_suite(ws, write_suite(
            [{"id": "GEN-x", "expect_in_boot": {"all_of": ["gate"]}}],
            corpus={"workspace": ["boot/anchors.md", "boot/never-written.md"]}))
        check("a missing corpus file was not surfaced",
              any("never-written" in e for e in suite2.corpus_errors))

    if f:
        return False, f"{len(f)} check(s) failed: " + "; ".join(f)
    return True, ("all 6 statuses fire; ABSENT (coverage) and "
                  "FAIL/DRIFT/STALE/ERROR (truth) are counted apart; 3 "
                  "refusals fire (zero probes, undeclared corpus group, "
                  "unbound identity repo); a missing corpus file is surfaced; "
                  "the rate travels with its denominator")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Self-model fidelity probe suite")
    parser.add_argument("--workspace", default=".", help="node repository root")
    parser.add_argument("--probes", default=str(DEFAULT_PROBES),
                        help="probe suite YAML (workspace-relative)")
    parser.add_argument("--identity-repo", default=None,
                        help="path to the bound identity repository")
    parser.add_argument("--memory-dir", default=None,
                        help="the host saddle's memory/context directory, if "
                             "the suite names a 'memory' corpus group")
    parser.add_argument("--json", dest="json_out", default=str(DEFAULT_REPORT))
    parser.add_argument("--strict", action="store_true",
                        help="exit 1 on any non-PASS or corpus error")
    parser.add_argument("--selftest", action="store_true",
                        help="prove every status and refusal can fire")
    args = parser.parse_args(argv)

    if args.selftest:
        ok, msg = selftest()
        print(f"{'PASS' if ok else 'FAIL'} self_probe selftest: {msg}")
        return 0 if ok else 1

    try:
        suite = run_suite(
            args.workspace, args.probes,
            memory_dir=Path(args.memory_dir) if args.memory_dir else None,
            identity_repo=Path(args.identity_repo) if args.identity_repo else None)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"SUITE ERROR: {exc}")
        return 2

    counts = {s: 0 for s in STATUSES}
    for r in suite.results:
        counts[r.status] = counts.get(r.status, 0) + 1
        marker = "OK  " if r.status == "PASS" else r.status
        line = f"[{marker:<6}] {r.id:<22} {r.question}"
        if r.status != "PASS" and r.detail:
            line += f"\n         -> {r.detail}"
        print(line)
    for err in suite.corpus_errors:
        print(f"[CORPUS] {err}")
    print(f"FIDELITY {suite.fidelity * 100:.0f}% ({counts['PASS']}/"
          f"{len(suite.results)})  corpus {len(suite.corpus_files)} files / "
          f"{suite.corpus_bytes} bytes (~{suite.corpus_bytes // 4} tokens)")
    print(f"  coverage failures (ABSENT, the boot corpus lost the anchor): "
          f"{len(suite.coverage_failures)}")
    print(f"  truth failures (DRIFT/STALE/ERROR, the organ moved): "
          f"{len(suite.truth_failures)}")
    for cat, bucket in sorted(suite.by_category.items()):
        print(f"  {cat:<11} {bucket['pass']}/{bucket['total']}")

    out = Path(args.json_out)
    if not out.is_absolute():
        out = Path(args.workspace) / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(suite.to_dict(), indent=2), encoding="utf-8")
    print(f"report: {out} (corpus={suite.corpus_label})")

    failed = len(suite.results) - counts["PASS"]
    if args.strict and (failed or suite.corpus_errors):
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
