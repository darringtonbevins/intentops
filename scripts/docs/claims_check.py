#!/usr/bin/env python3
"""Do the documents a stranger reads first name paths that actually exist?

PURPOSE
    A Code-rung truth check over the three documents that reach a reader before
    any code does -- ``README.md``, ``docs/GENESIS.md`` and
    ``docs/quick-start.md``. It answers exactly one question: **when one of
    those files claims something ships, is built, or is carried, and names a
    path while doing so, is that path in the tree?**

    It exists because the two defects this repository has actually shipped in
    its documentation were both of this shape, and neither was catchable by any
    other instrument here:

    * ``pip install -e packages/intentops-core`` appeared in README.md and in
      docs/quick-start.md as the documented install step. There is no
      ``pyproject.toml`` at that path and never was, so the one command every
      stranger runs could not work. The test suite passed throughout, because
      the suite installs nothing.
    * The falsifier F3 record notes a remedy line pointing at a path inside the
      caller's virtual environment -- a real path that reads to a stranger as a
      corrupted install.

    A green reading here means the *paths* are real. It is not a statement
    about whether the prose around them is true; see BLIND SPOTS.

WRITE MODEL
    None. This is a pure read: it opens the declared documents, resolves
    candidate paths against the repository root, and writes nothing anywhere.
    ``--selftest`` builds its fixtures in a temporary directory and removes
    them.

OPERATING POINT (SIG-1 clause 7 -- a detector that hides its threshold
manufactures confidence)
    * Population: the documents in :data:`DOCUMENTS`, EVERY line of each.
      There is deliberately no claim-marker gate. One was tried first --
      check a line only if it carried a word like "ships" -- and measured: it
      let **nine of twelve** real path claims through, including four broken
      module pointers on adjacent table rows, because whether a row happens to
      contain the word "reads" is not correlated with whether its path is real.
      In three curated documents, every path named is a claim.
    * Candidates are backtick spans and markdown link targets; inside a fenced
      code block, every whitespace-separated token, because a command shown to
      a reader is a claim that the command runs and its arguments are not
      backticked.
    * A candidate is PATH-SHAPED iff it contains ``/`` or ends in one of
      :data:`PATH_SUFFIXES`, and carries no space, glob, angle bracket or URL
      scheme. Angle brackets are the placeholder convention: write
      ``<n>/<n>``, never ``N/N``, or the placeholder reads as a path.
    * RESOLUTION: a candidate resolves relative to **its own document's
      directory first**, then to the repository root. That is how a reader's
      markdown viewer resolves a link, and resolving only from the root
      reported three correct links in ``docs/`` as broken.
    * A path-shaped candidate under one of :data:`EXEMPT_PREFIXES`, or shaped
      like a schema id (:data:`SCHEMA_ID_RE`, e.g. ``operator-root/v1``),
      leaves the population; the counts are printed on every run.
    * :data:`CLAIM_MARKERS` no longer gates anything. It LABELS: a finding on
      a line carrying one of those words is an explicit claim, and one that is
      not is a reference. Both are findings; the label is for the reader.
    * SECOND CLASS -- an INSTALL TARGET: a code-block line matching
      :data:`INSTALL_RE` whose path argument names a directory must find a
      packaging file (:data:`PACKAGING_FILES`) in it. Existence alone is not
      enough here, which is the whole reason this class exists: the directory
      in the defect above *did* exist.

REFUSALS (no load-bearing field gets a default)
    * A declared document that is missing or unreadable is a HALT (exit 2),
      never a skip. A checker that quietly drops the file it was pointed at
      reports a better number for having gone blind.
    * :data:`DOCUMENTS`, :data:`CLAIM_MARKERS` and :data:`PATH_SUFFIXES` may
      not be empty; an empty population scores a perfect reading by
      construction.

BLIND SPOTS (published, per grounded-signal rule 4)
    * Prose only. A claim with no path in it -- "the councils ship" -- is
      invisible here, and most false claims are of that kind.
    * Existence only. A path that exists but does not contain what the sentence
      says it contains passes. This finds broken pointers, not lies.
    * Three documents. Nothing else in ``docs/`` is read, and the build records
      and falsifier records are deliberately out of population: they are
      historical transcripts, and a path that has since moved is correct
      *there*. **The boot corpus declared in ``config/genesis-probes.yaml`` is
      wider than this population** and has grown past it; adding a document
      here is a reviewed change, and until one is made, a claim in a
      boot-corpus document outside these three is unchecked.
    * Existence, never correctness of the reference: a path that resolves from
      the document's own directory when the author meant the repository root
      (or the reverse) passes, because both resolve.
    * The exemptions are a real blind spot and are counted, not hidden: a path
      under ``.intentops/`` is created at genesis and is genuinely absent from
      a fresh clone, so it cannot be checked from here at all.

Exit codes: 0 CLEAN, 1 FINDINGS, 2 HALT.
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, NamedTuple, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The documents a stranger meets before any code. Closed by declaration.
DOCUMENTS: Tuple[str, ...] = (
    "README.md",
    "docs/GENESIS.md",
    "docs/quick-start.md",
    # Added 2026-09-06: the README says this check reads five documents, and it
    # read three. A release procedure and a changelog name paths like any other
    # claim, and a dead path in either is exactly as misleading.
    "CHANGELOG.md",
    "docs/RELEASING.md",
)

#: Words that make a finding an explicit CLAIM rather than a reference. These
#: LABEL a finding; they no longer gate whether a line is read. Lowercase,
#: matched case-insensitively as a substring.
CLAIM_MARKERS: Tuple[str, ...] = (
    "ships",
    "shipped",
    "built",
    "carries",
    "carried",
    "installs",
    "runs",
    "lives",
    "reads",
    "writes",
)

#: A candidate with no ``/`` still counts as a path if it ends in one of
#: these. Keeps ``README.md`` in and ``intentops.gate`` out.
PATH_SUFFIXES: Tuple[str, ...] = (
    ".md", ".py", ".yaml", ".yml", ".json", ".toml", ".txt", ".sql",
    ".cfg", ".ini", ".lock", ".ots",
)

#: Paths that do not exist in a fresh clone BY DESIGN, with the reason. Each
#: entry removes candidates from the population; the count is printed every
#: run, because an exemption makes this checker blinder and the number better
#: at the same time.
EXEMPT_PREFIXES: Tuple[Tuple[str, str], ...] = (
    (".intentops/",
     "created by genesis at G3; absent from every fresh clone by design"),
    (".venv/",
     "created by the reader at install time; never in the tree"),
    ("identity-repo/",
     "the L4 identity instance; never ships (README, five-layer model)"),
    ("docs/reports/intentops-oss/",
     "the design of record, in the private monorepo this seed was extracted "
     "from; cited by section, never shipped"),
    ("~/",
     "the reader's own home directory"),
)

#: The schema-id class, reported beside the path exemptions so it is visible
#: rather than silently dropped.
_SCHEMA_ID = "<schema-id>"

#: A documented install command. The path argument must be installable, not
#: merely present.
INSTALL_RE = re.compile(r"\bpip\s+install\b(?P<rest>.*)$")

#: What makes a directory installable by pip.
PACKAGING_FILES: Tuple[str, ...] = ("pyproject.toml", "setup.py", "setup.cfg")

#: A schema identifier -- ``operator-root/v1``, ``self-probe/v1`` -- is
#: slash-shaped and is not a path. Narrow on purpose: a trailing ``/vN`` only.
SCHEMA_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/v\d+$")

_BACKTICK = re.compile(r"`([^`\n]+)`")
_MDLINK = re.compile(r"\[[^\]\n]*\]\(([^)\s]+)\)")
_URL_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://|^mailto:", re.IGNORECASE)
_FENCE = re.compile(r"^\s*(```|~~~)")


class Finding(NamedTuple):
    """One claimed path that is not in the tree, or not installable."""

    document: str
    line_no: int
    path: str
    excerpt: str
    kind: str = "missing-path"

    def render(self) -> str:
        what = ("claimed path does not exist" if self.kind == "missing-path"
                else "documented install target carries no packaging file "
                     "(pyproject.toml / setup.py / setup.cfg), so the command "
                     "as written cannot succeed")
        return (f"  FINDING {self.document}:{self.line_no}: {what} -- "
                f"{self.path}\n           | {self.excerpt}")


class Report(NamedTuple):
    findings: Tuple[Finding, ...]
    documents_read: int
    lines_read: int
    claim_lines: int
    checked: int
    exempted: Dict[str, int]

    @property
    def clean(self) -> bool:
        return not self.findings


class ClaimsHalt(RuntimeError):
    """A load-bearing input is missing. Never downgraded to a skip."""


# ---------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------


def is_claim_line(line: str, markers: Sequence[str] = CLAIM_MARKERS) -> bool:
    """True iff the line asserts that something exists."""
    low = line.lower()
    return any(m in low for m in markers)


def _path_shaped(candidate: str) -> bool:
    if not candidate or candidate != candidate.strip():
        return False
    if any(ch in candidate for ch in (" ", "\t", "*", "<", ">", "|", '"')):
        return False
    if _URL_SCHEME.search(candidate):
        return False
    if candidate.startswith("#"):
        return False
    if "/" in candidate:
        return True
    return candidate.endswith(PATH_SUFFIXES)


def candidate_paths(line: str, in_code_block: bool = False) -> List[str]:
    """Every path-shaped token on one line, in order, de-duplicated.

    Inside a fenced code block the whole line is the candidate source: a
    command shown to a reader is a claim that the command runs, and its
    arguments are not backticked.
    """
    raw: List[str] = []
    if in_code_block:
        raw.extend(line.replace("'", " ").replace('"', " ").split())
    else:
        raw.extend(_BACKTICK.findall(line))
        raw.extend(_MDLINK.findall(line))
    out: List[str] = []
    for cand in raw:
        cand = cand.strip().rstrip(".,;:").split("#", 1)[0]
        # `VAR=value` in a shown command is an environment assignment, not a
        # path claim. The VALUE may still be one, so it is checked and the
        # assignment prefix is not -- narrower than dropping the token.
        if "=" in cand and not cand.startswith("="):
            head, _, tail = cand.partition("=")
            if head and head.replace("_", "").isalnum() and head.isupper():
                cand = tail
        if _path_shaped(cand) and cand not in out:
            out.append(cand)
    return out


def install_targets(line: str, repo_root: Path) -> List[str]:
    """Local-path arguments of a documented ``pip install``.

    A bare token with no slash -- ``pkg`` -- is a local target iff it names a
    directory in the tree, and a PyPI distribution name otherwise. Deciding
    that on shape alone would have missed the bare-dirname form entirely,
    which is the same class as the defect this check exists for.
    """
    match = INSTALL_RE.search(line)
    if not match:
        return []
    out: List[str] = []
    for token in match.group("rest").split():
        if token.startswith("-"):
            continue
        token = token.strip().strip("'\"").rstrip(".,;:")
        if not token:
            continue
        local = token in (".", "..") or _path_shaped(token)
        if not local:
            candidate = (repo_root / token)
            local = candidate.is_dir()
        if local and token not in out:
            out.append(token)
    return out


def exemption_for(path: str) -> str:
    """The declared prefix a path is exempt under, or the empty string."""
    for prefix, _reason in EXEMPT_PREFIXES:
        if path.startswith(prefix):
            return prefix
    return ""


def path_exists(repo_root: Path, path: str, document: str = "") -> bool:
    """Does the claimed path resolve inside the tree?

    Resolution order is the reader's: the DOCUMENT's own directory first (that
    is what a markdown viewer does with a relative link), then the repository
    root. Resolving only from the root reported three correct links in
    ``docs/`` as broken.

    A path escaping the repository root is reported as missing rather than
    followed: this checker never reads outside the tree it is measuring.
    """
    cleaned = path.rstrip("/")
    bases = []
    if document:
        bases.append((repo_root / document).parent)
    bases.append(repo_root)

    root = repo_root.resolve()
    for base in bases:
        target = (base / cleaned).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            continue
        if target.exists():
            return True
    return False


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------


def check(repo_root: Path,
          documents: Iterable[str] = DOCUMENTS) -> Report:
    """Read every declared document and grade its path claims."""
    docs = tuple(documents)
    if not docs:
        raise ClaimsHalt("no documents declared; an empty population scores "
                         "a perfect reading by construction")
    if not CLAIM_MARKERS or not PATH_SUFFIXES:
        raise ClaimsHalt("CLAIM_MARKERS and PATH_SUFFIXES may not be empty")

    findings: List[Finding] = []
    exempted: Dict[str, int] = {p: 0 for p, _ in EXEMPT_PREFIXES}
    exempted[_SCHEMA_ID] = 0
    lines_read = claim_lines = checked = 0

    for rel in docs:
        target = repo_root / rel
        if not target.is_file():
            raise ClaimsHalt(
                f"declared document {rel} is missing at {target}. This is a "
                f"HALT, not a skip: a checker that drops the file it was "
                f"pointed at reports a better number for having gone blind")
        try:
            text = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ClaimsHalt(f"declared document {rel} is unreadable: "
                             f"{exc}") from exc

        in_block = False
        for line_no, line in enumerate(text.splitlines(), start=1):
            lines_read += 1
            if _FENCE.match(line):
                in_block = not in_block
                continue
            if is_claim_line(line):
                claim_lines += 1

            excerpt = line.strip()
            if len(excerpt) > 110:
                excerpt = excerpt[:107] + "..."

            for cand in candidate_paths(line, in_code_block=in_block):
                if SCHEMA_ID_RE.match(cand):
                    exempted[_SCHEMA_ID] += 1
                    continue
                prefix = exemption_for(cand)
                if prefix:
                    exempted[prefix] += 1
                    continue
                checked += 1
                if not path_exists(repo_root, cand, rel):
                    findings.append(Finding(rel, line_no, cand, excerpt))

            if in_block:
                for target in install_targets(line, repo_root):
                    if exemption_for(target):
                        continue
                    resolved = (repo_root / target).resolve()
                    checked += 1
                    if not resolved.is_dir():
                        continue  # a file or a PyPI name: not this class
                    if not any((resolved / name).is_file()
                               for name in PACKAGING_FILES):
                        findings.append(Finding(rel, line_no, target, excerpt,
                                                kind="uninstallable-target"))

    return Report(tuple(findings), len(docs), lines_read, claim_lines,
                  checked, exempted)


def render(report: Report) -> str:
    """The whole reading, including what it did NOT look at."""
    out: List[str] = []
    out.append(
        f"  population: {report.documents_read} documents, "
        f"{report.lines_read} lines read, {report.checked} path claims "
        f"checked ({report.claim_lines} lines carry an explicit claim word)")
    total_exempt = sum(report.exempted.values())
    out.append(
        f"  exemptions: {total_exempt} candidate(s) left the population "
        f"across {len(EXEMPT_PREFIXES)} declared prefixes plus schema ids")
    for prefix, reason in EXEMPT_PREFIXES:
        out.append(f"    {report.exempted[prefix]:>3}  {prefix:<28} {reason}")
    out.append(f"    {report.exempted[_SCHEMA_ID]:>3}  {_SCHEMA_ID:<28} "
               f"a schema identifier such as operator-root/v1 is "
               f"slash-shaped and is not a path")
    for finding in report.findings:
        out.append(finding.render())
    if report.clean:
        out.append("  VERDICT: CLEAN -- every checked path claim resolves in "
                   "the tree, and every documented install target is "
                   "installable")
    else:
        missing = sum(1 for f in report.findings if f.kind == "missing-path")
        bad_install = len(report.findings) - missing
        out.append(f"  VERDICT: FINDINGS -- {len(report.findings)} "
                   f"({missing} missing path, {bad_install} uninstallable "
                   f"install target)")
    out.append(
        f"  What this does and does not say: I read every line of "
        f"{report.documents_read} documents and checked every path-shaped "
        "token for EXISTENCE (resolved from the document's own directory, "
        "then from the repository root) plus every pip-install target for a "
        "packaging file. It says nothing about whether the sentence around a "
        "path is true, nothing about a claim that names no path, and nothing "
        "about any document outside the declared set -- and the boot corpus "
        "is wider than that set.")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# selftest -- a detector that has never fired is indistinguishable from a
# broken one, so every path below must actually fire.
# ---------------------------------------------------------------------------


def selftest() -> Tuple[bool, str]:
    """Prove each class can fire, in a temporary tree."""
    results: List[Tuple[str, bool]] = []

    def expect(label: str, cond: bool) -> None:
        results.append((label, bool(cond)))

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        docs = root / "docs"
        docs.mkdir()
        (root / "real.md").write_text("real\n", encoding="utf-8")

        # 1. a claim naming a path that exists is not a finding
        (root / "A.md").write_text(
            "This ships `real.md` today.\n", encoding="utf-8")
        rep = check(root, ["A.md"])
        expect("an existing claimed path is clean", rep.clean and rep.checked == 1)

        # 2. a claim naming a path that does not exist IS a finding
        (root / "B.md").write_text(
            "This ships `packages/nope/pyproject.toml` today.\n",
            encoding="utf-8")
        rep = check(root, ["B.md"])
        expect("a missing claimed path fires",
               len(rep.findings) == 1
               and rep.findings[0].path == "packages/nope/pyproject.toml")

        # 3. a path on a line with NO claim word is still checked. The
        #    marker gate this replaces let nine of twelve real claims through.
        (root / "C.md").write_text(
            "Someday we might add `packages/nope/pyproject.toml`.\n",
            encoding="utf-8")
        rep = check(root, ["C.md"])
        expect("a path with no claim word is still checked",
               len(rep.findings) == 1 and rep.claim_lines == 0
               and rep.checked == 1)

        # 4. an exempt prefix leaves the population and is COUNTED
        (root / "D.md").write_text(
            "Genesis writes `.intentops/genesis/journal.jsonl` at birth.\n",
            encoding="utf-8")
        rep = check(root, ["D.md"])
        expect("an exempt path leaves the population and is counted",
               rep.clean and rep.checked == 0
               and rep.exempted[".intentops/"] == 1)

        # 5. a markdown link target is checked too
        (root / "E.md").write_text(
            "The design ships in [the plan](docs/nope.md).\n", encoding="utf-8")
        rep = check(root, ["E.md"])
        expect("a markdown link target fires",
               len(rep.findings) == 1 and rep.findings[0].path == "docs/nope.md")

        # 6. a URL is never treated as a path
        (root / "F.md").write_text(
            "This ships at [home](https://example.invalid/x.md).\n",
            encoding="utf-8")
        rep = check(root, ["F.md"])
        expect("a URL is not a path claim", rep.clean and rep.checked == 0)

        # 7. a dotted identifier is not a path
        (root / "G.md").write_text(
            "The gate ships as `intentops.gate` and returns a verdict.\n",
            encoding="utf-8")
        rep = check(root, ["G.md"])
        expect("a dotted identifier is not a path claim",
               rep.clean and rep.checked == 0)

        # 8. a path escaping the root reads as missing, and is not followed
        (root / "H.md").write_text(
            "This ships `../outside/secret.md` today.\n", encoding="utf-8")
        rep = check(root, ["H.md"])
        expect("an escaping path is a finding, never followed",
               len(rep.findings) == 1)

        # 9. a MISSING declared document HALTs, never skips
        halted = False
        try:
            check(root, ["docs/absent.md"])
        except ClaimsHalt:
            halted = True
        expect("a missing declared document halts", halted)

        # 10. an EMPTY document list halts
        halted = False
        try:
            check(root, [])
        except ClaimsHalt:
            halted = True
        expect("an empty population halts", halted)

        # 11. a directory claim resolves
        (root / "pkg").mkdir()
        (root / "I.md").write_text(
            "The adapter ships in `pkg/`.\n", encoding="utf-8")
        rep = check(root, ["I.md"])
        expect("a directory claim resolves", rep.clean and rep.checked == 1)

        # 12. a command inside a fenced block is a claim, backticks or not
        (root / "J.md").write_text(
            "Steps:\n\n```\ncat docs/nope.md\n```\n", encoding="utf-8")
        rep = check(root, ["J.md"])
        expect("a code-block command is a claim",
               len(rep.findings) == 1 and rep.findings[0].path == "docs/nope.md")

        # 13. THE SHIPPED DEFECT: an install target that exists but carries no
        #     packaging file. Existence alone would have passed this.
        (root / "K.md").write_text(
            "Install:\n\n```\npip install -e pkg\n```\n", encoding="utf-8")
        rep = check(root, ["K.md"])
        expect("an existing-but-uninstallable target fires",
               len(rep.findings) == 1
               and rep.findings[0].kind == "uninstallable-target")

        # 14. the same target, once it carries a pyproject, is clean
        (root / "pkg" / "pyproject.toml").write_text("[project]\n",
                                                     encoding="utf-8")
        rep = check(root, ["K.md"])
        expect("an installable target is clean", rep.clean)

        # 15. a PyPI name is not an install-target finding
        (root / "L.md").write_text(
            "Install:\n\n```\npip install pytest\n```\n", encoding="utf-8")
        rep = check(root, ["L.md"])
        expect("a PyPI name is not a path claim", rep.clean)

        # 16. the fence CLOSES: an unbackticked path in prose is not tokenised.
        #     If the fence leaked, docs/nope.md below would fire.
        (root / "M.md").write_text(
            "```\ncat real.md\n```\n\nSomeday docs/nope.md may exist.\n",
            encoding="utf-8")
        rep = check(root, ["M.md"])
        expect("a closed fence returns to prose rules", rep.clean)

        # 17. a link resolves relative to ITS OWN DOCUMENT first. Resolving
        #     only from the root reported three correct docs/ links as broken.
        (docs / "sib.md").write_text("sibling\n", encoding="utf-8")
        (docs / "N.md").write_text(
            "See [the sibling](sib.md).\n", encoding="utf-8")
        rep = check(root, ["docs/N.md"])
        expect("a link resolves from its own document's directory",
               rep.clean and rep.checked == 1)

        # 18. ...and the repository root is still tried as a fallback
        (docs / "O.md").write_text(
            "See [the root file](real.md).\n", encoding="utf-8")
        rep = check(root, ["docs/O.md"])
        expect("the repository root is still a fallback base", rep.clean)

        # 19. a schema id is slash-shaped and is not a path
        (root / "P.md").write_text(
            "It carries schema `operator-root/v1`.\n", encoding="utf-8")
        rep = check(root, ["P.md"])
        expect("a schema id leaves the population and is counted",
               rep.clean and rep.checked == 0
               and rep.exempted[_SCHEMA_ID] == 1)

        # 20. an angle-bracket placeholder is never read as a path
        (root / "Q.md").write_text(
            "```\nprobes <n>/<n> PASS\n```\n", encoding="utf-8")
        rep = check(root, ["Q.md"])
        expect("an angle-bracket placeholder is not a path",
               rep.clean and rep.checked == 0)

    ok = all(passed for _label, passed in results)
    lines = [f"  {'PASS' if p else 'FAIL'}  {label}" for label, p in results]
    lines.append(f"claims_check selftest: {sum(p for _l, p in results)}"
                 f"/{len(results)} paths behaved as declared")
    return ok, "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="claims_check.py",
        description="Do README / GENESIS / quick-start name paths that exist?")
    parser.add_argument("--repo-root", default=str(REPO_ROOT),
                        help="the tree to read (default: this repository)")
    parser.add_argument("--selftest", action="store_true",
                        help="prove every finding class can fire, then exit")
    args = parser.parse_args(argv)

    if args.selftest:
        ok, text = selftest()
        print(text)
        return 0 if ok else 1

    root = Path(args.repo_root).resolve()
    print(f"documentation claims check over {root}")
    try:
        report = check(root)
    except ClaimsHalt as exc:
        print(f"  HALT: {exc}")
        return 2
    print(render(report))
    return 0 if report.clean else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
