"""The crystallized-pattern document contract: template, and a validator.

PURPOSE
    A crystallized pattern (a "CRYST document") is the metabolism's OUTPUT: the
    thing a node has learned, written down so a stranger -- or a fresh context
    window -- can act on it. This module owns what one must contain, and
    refuses one that does not.

    FIVE REQUIRED FIELDS, and every one of them is a refusal of a specific
    failure that has already happened somewhere:

      ``id``           ``CRYST-<n>``, unique and stable. A pattern with no id
                       cannot be cited, superseded, or counted, so it cannot
                       be retired either.
      ``pattern``      the claim itself, in one statement. Not a topic, not a
                       category -- a sentence that could be wrong.
      ``evidence``     a non-empty list, EACH entry GRADED ``OBSERVED`` /
                       ``INFERRED`` / ``HYPOTHESIS`` and carrying a citation.
                       An ungraded causal claim is the fabrication class this
                       whole framework is built against: internal consistency
                       is not corroboration, and arithmetic can be flawless
                       over invented constants.
      ``station``      ``described`` | ``referenced`` | ``operative``. Where
                       this pattern has actually GOT to. A pattern is a
                       compound with a trajectory, and a corpus that records
                       only the claim cannot tell a rule that governs code from
                       a rule that has only ever been prose. Station is a LOWER
                       BOUND by construction (see BLIND SPOTS).
      ``reopens_when`` what would make this false. REQUIRED, non-blank.

    WHY ``reopens_when`` IS REQUIRED HERE AND NULLABLE IN THE ORDERING STORE,
    stated because the asymmetry looks like an inconsistency and is not. An
    ordering-store ruling is TRANSCRIBED from a human's words; requiring a way
    back there would make the transcriber invent one, which manufactures
    exactly the false confidence that store exists to prevent. A CRYST document
    is AUTHORED by the node from its own evidence. The author is present, the
    author is the one making the claim, and an author who cannot say what would
    falsify their own pattern has not finished thinking. So: never fabricate a
    falsifier for someone else's ruling; always state one for your own claim.

WRITE MODEL
    None of its own. This module RENDERS text and VALIDATES text. Where a
    document lands, and under which write model, is the caller's declaration --
    the metabolism's own corpus directory is per-item files (one document, one
    file, collision-safe by construction), which is why this module never needs
    a lock.

BLIND SPOTS -- stated so a valid document is not read as a true one
    * This validator checks SHAPE. It cannot tell a true pattern from a
      confident false one, an OBSERVED grade from an honest one, or a real
      citation from a plausible string. Every field is checked for presence,
      vocabulary and non-emptiness; none is checked for correctness.
    * ``station`` is SELF-DECLARED here. The instrument that could contradict
      it -- a sweep for literal references to the id across a tree -- is not in
      this module, and when it exists it will be a LOWER BOUND anyway: a
      pattern re-implemented without naming its source is invisible to a
      literal search, so ``described`` may understate and ``operative`` can
      only ever be a claim.
    * A citation is any non-blank string. It is not resolved, fetched, or
      checked for existence. A citation pointing at a file that does not exist
      passes here.
    * The front matter is parsed as YAML. A document whose body contradicts its
      own front matter is invisible: nothing here reads the prose.
    * ``id`` uniqueness is checked only across a set the caller passes in.
      This module holds no registry and cannot know what else exists.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "CrystError",
    "CrystDoc",
    "REQUIRED_FIELDS",
    "OPTIONAL_FIELDS",
    "STATIONS",
    "EVIDENCE_GRADES",
    "CRYST_ID_RE",
    "SCHEMA",
    "render_template",
    "parse_document",
    "validate",
    "validate_file",
    "selftest",
    "main",
]

SCHEMA = "cryst/v1"

CRYST_ID_RE = re.compile(r"^CRYST-\d{3,}$")

#: How far a pattern has actually got. Closed vocabulary.
STATIONS: Tuple[str, ...] = ("described", "referenced", "operative")

#: The grade every piece of evidence carries. Closed vocabulary; an ungraded
#: claim is refused rather than defaulted to the weakest grade, because a
#: default would put an unexamined claim into the population wearing a label
#: nobody chose.
EVIDENCE_GRADES: Tuple[str, ...] = ("OBSERVED", "INFERRED", "HYPOTHESIS")

REQUIRED_FIELDS: Tuple[str, ...] = ("id", "pattern", "evidence", "station",
                                    "reopens_when")
OPTIONAL_FIELDS: Tuple[str, ...] = ("schema", "title", "as_of", "supersedes",
                                    "superseded_by", "related", "notes",
                                    "tagout")

_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


class CrystError(RuntimeError):
    """A refusal with a remedy attached."""

    def __init__(self, reason: str, remedy: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.remedy = remedy

    def render(self) -> str:
        out = f"REFUSED: {self.reason}"
        if self.remedy:
            out += f"\n  remedy: {self.remedy}"
        return out


@dataclass(frozen=True)
class CrystDoc:
    """A validated crystallized pattern."""

    id: str
    pattern: str
    evidence: Tuple[Dict[str, str], ...]
    station: str
    reopens_when: str
    title: str = ""
    as_of: str = ""
    body: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def grades(self) -> Tuple[str, ...]:
        return tuple(e["grade"] for e in self.evidence)

    @property
    def strongest_grade(self) -> str:
        for grade in EVIDENCE_GRADES:
            if grade in self.grades:
                return grade
        return "HYPOTHESIS"

    def to_row(self) -> Dict[str, Any]:
        return {"id": self.id, "pattern": self.pattern,
                "station": self.station, "reopens_when": self.reopens_when,
                "evidence": [dict(e) for e in self.evidence],
                "strongest_grade": self.strongest_grade,
                "title": self.title, "as_of": self.as_of}


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def render_template(doc_id: str,
                    *,
                    pattern: str = "",
                    station: str = "described",
                    reopens_when: str = "",
                    evidence: Optional[Sequence[Mapping[str, str]]] = None,
                    title: str = "",
                    as_of: str = "") -> str:
    """Render a CRYST document. With no arguments it is a BLANK template.

    The blank template is deliberately INVALID: every required field is present
    as an empty placeholder, so a reader can see what is owed, and
    :func:`validate` refuses it until the placeholders are filled. A template
    that validated while empty would let a node crystallize nothing and count
    it.
    """
    lines: List[str] = [
        "---",
        f"schema: {SCHEMA}",
        f"id: {doc_id}",
    ]
    if title:
        lines.append(f"title: {json.dumps(title)}")
    if as_of:
        lines.append(f"as_of: {json.dumps(as_of)}")
    lines.append("# The claim itself, in one statement that could be wrong.")
    lines.append(f"pattern: {json.dumps(pattern)}")
    lines.append("# Where this pattern has actually got to: "
                 + " | ".join(STATIONS))
    lines.append(f"station: {station}")
    lines.append("# What would make this false. Required, non-blank -- an "
                 "author who")
    lines.append("# cannot say what would falsify their own claim has not "
                 "finished thinking.")
    lines.append(f"reopens_when: {json.dumps(reopens_when)}")
    lines.append("# Each entry GRADED " + " | ".join(EVIDENCE_GRADES)
                 + ", each carrying a citation.")
    # An explicitly EMPTY list renders as empty -- it is a real, refusable
    # state and must not be quietly replaced by a blank placeholder entry.
    items = (list(evidence) if evidence is not None
             else [{"grade": "", "claim": "", "citation": ""}])
    lines.append("evidence:" + ("" if items else " []"))
    for item in items:
        lines.append(f"  - grade: {item.get('grade', '')}")
        lines.append(f"    claim: {json.dumps(item.get('claim', ''))}")
        lines.append(f"    citation: {json.dumps(item.get('citation', ''))}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {doc_id}" + (f": {title}" if title else ""))
    lines.append("")
    lines.append(pattern or "_State the pattern here, in prose, once._")
    lines.append("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# parsing and validation
# ---------------------------------------------------------------------------


def parse_document(text: str) -> Tuple[Dict[str, Any], str]:
    """``(front_matter, body)``. A document with no front matter is refused."""
    match = _FRONT_MATTER.match(text)
    if not match:
        raise CrystError(
            "the document carries no `---` front matter",
            remedy="a CRYST document is front matter plus prose; the machine "
                   "reads the front matter and a human reads the prose")
    try:
        import yaml  # noqa: PLC0415

        front = yaml.safe_load(match.group(1))
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise CrystError(f"the YAML library is unavailable ({exc})",
                         remedy="pip install pyyaml") from exc
    except Exception as exc:  # noqa: BLE001 - the parser's error class varies
        raise CrystError(f"the front matter does not parse ({exc})",
                         remedy="fix the YAML; a partial parse is refused") from exc
    if not isinstance(front, Mapping):
        raise CrystError("the front matter is not a mapping",
                         remedy="it must be a YAML mapping of the required fields")
    return dict(front), match.group(2)


def validate(text: str,
             known_ids: Optional[Iterable[str]] = None
             ) -> Tuple[Optional[CrystDoc], List[str]]:
    """``(doc, findings)``. ``doc`` is None whenever findings is non-empty.

    EVERY finding is collected, not just the first: an author fixing one
    refusal at a time round-trips five times, and each round trip is a chance
    to give up and write the document somewhere this validator cannot see.
    """
    findings: List[str] = []
    try:
        front, body = parse_document(text)
    except CrystError as exc:
        return None, [exc.reason]

    unknown = sorted(set(map(str, front.keys()))
                     - set(REQUIRED_FIELDS) - set(OPTIONAL_FIELDS))
    if unknown:
        findings.append(
            f"undeclared front-matter field(s) {unknown}: an undeclared field "
            "is refused, never ignored -- a typo would otherwise load clean "
            "and silently lose the field it was meant to set")

    for name in REQUIRED_FIELDS:
        if name not in front:
            findings.append(
                f"missing required field {name!r}: there is no default for it")

    doc_id = str(front.get("id") or "")
    if "id" in front and not CRYST_ID_RE.match(doc_id):
        findings.append(f"id {doc_id!r} is not of the form CRYST-<n>")
    if known_ids is not None and doc_id and doc_id in set(known_ids):
        findings.append(
            f"id {doc_id!r} already exists: two documents under one id make "
            "every citation of it ambiguous")

    pattern = str(front.get("pattern") or "").strip()
    if "pattern" in front and not pattern:
        findings.append(
            "`pattern` is blank: the claim itself is the document, and a "
            "pattern that cannot be stated cannot be wrong, cited, or retired")

    station = str(front.get("station") or "")
    if "station" in front and station not in STATIONS:
        findings.append(
            f"station {station!r} is not one of {list(STATIONS)}: an "
            "undeclared station is a hard exit, never a default of "
            "'probably described'")

    reopens = str(front.get("reopens_when") or "").strip()
    if "reopens_when" in front and not reopens:
        findings.append(
            "`reopens_when` is blank: a pattern with no stated falsifier has "
            "no half-life and cannot be re-asked on time. It is REQUIRED here "
            "-- unlike an ordering-store ruling, which is transcribed from "
            "someone else's words -- because the author of a CRYST document is "
            "the one making the claim")

    evidence = front.get("evidence")
    parsed_evidence: List[Dict[str, str]] = []
    if "evidence" in front:
        if (not isinstance(evidence, Sequence)
                or isinstance(evidence, (str, bytes)) or not evidence):
            findings.append(
                "`evidence` is empty or is not a list: a pattern with no "
                "evidence is a preference")
        else:
            for index, item in enumerate(evidence):
                where = f"evidence[{index}]"
                if not isinstance(item, Mapping):
                    findings.append(f"{where} is not a mapping")
                    continue
                grade = str(item.get("grade") or "")
                claim = str(item.get("claim") or "").strip()
                citation = str(item.get("citation") or "").strip()
                if grade in EVIDENCE_GRADES and claim and citation:
                    parsed_evidence.append({"grade": grade, "claim": claim,
                                            "citation": citation})
                if grade not in EVIDENCE_GRADES:
                    findings.append(
                        f"{where} carries grade {grade!r}, not one of "
                        f"{list(EVIDENCE_GRADES)}: an ungraded causal claim is "
                        "refused rather than defaulted, because a default "
                        "would put an unexamined claim into the population "
                        "wearing a label nobody chose")
                if not claim:
                    findings.append(f"{where} states no claim")
                if not citation:
                    findings.append(
                        f"{where} carries no citation: an artifact with no "
                        "ancestry floats, and a claim with no source is the "
                        "shape a fabricated one takes")

    if findings:
        return None, findings

    return CrystDoc(
        id=doc_id, pattern=pattern, evidence=tuple(parsed_evidence),
        station=station, reopens_when=reopens,
        title=str(front.get("title") or ""),
        as_of=str(front.get("as_of") or ""),
        body=body,
        extra={k: v for k, v in front.items()
               if k not in REQUIRED_FIELDS and k not in ("title", "as_of")},
    ), []


def validate_file(path: Path | str,
                  known_ids: Optional[Iterable[str]] = None
                  ) -> Tuple[Optional[CrystDoc], List[str]]:
    path = Path(path)
    if not path.is_file():
        return None, [f"no such document: {path}"]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        # UNREADABLE is a finding, never an absence: a document that leaves the
        # population makes every validity rate look better.
        return None, [f"unreadable ({exc}): {path}"]
    return validate(text, known_ids=known_ids)


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


_GOOD_EVIDENCE = [{"grade": "OBSERVED",
                   "claim": "the stage reported success over zero output",
                   "citation": "the cadence run record for that date"}]


def _good_text(**overrides: Any) -> str:
    kwargs: Dict[str, Any] = {
        "pattern": "A stage that ran and produced nothing renders WARN.",
        "station": "operative",
        "reopens_when": "a stage is found rendering OK at zero output",
        "evidence": _GOOD_EVIDENCE,
        "title": "Zero output is a state",
        "as_of": "2026-09-06",
    }
    kwargs.update(overrides)
    return render_template("CRYST-001", **kwargs)


def selftest() -> Tuple[bool, str]:
    """Prove the validator accepts a good document and refuses each defect."""
    fired: List[str] = []
    failures: List[str] = []

    def expect(name: str, ok: bool) -> None:
        fired.append(name)
        if not ok:
            failures.append(name)

    doc, findings = validate(_good_text())
    expect("a-complete-document-validates", doc is not None and not findings)
    if doc is not None:
        expect("strongest-grade-is-read", doc.strongest_grade == "OBSERVED")

    # the BLANK template is deliberately invalid
    _, findings = validate(render_template("CRYST-002"))
    expect("blank-template-is-refused", bool(findings))

    def refuses(name: str, text: str, needle: str) -> None:
        _, found = validate(text)
        expect(name, any(needle in f for f in found))

    refuses("missing-reopens-when-refused",
            _good_text(reopens_when=""), "reopens_when")
    refuses("blank-pattern-refused", _good_text(pattern=""), "pattern")
    refuses("unknown-station-refused",
            _good_text(station="crystallised"), "station")
    refuses("empty-evidence-refused", _good_text(evidence=[]), "evidence")
    refuses("ungraded-evidence-refused",
            _good_text(evidence=[{"grade": "", "claim": "c",
                                  "citation": "s"}]), "grade")
    refuses("uncited-evidence-refused",
            _good_text(evidence=[{"grade": "OBSERVED", "claim": "c",
                                  "citation": ""}]), "citation")
    refuses("no-front-matter-refused", "# just prose\n", "front matter")

    _, found = validate(render_template("CRYST-1", pattern="p",
                                        station="described",
                                        reopens_when="r",
                                        evidence=_GOOD_EVIDENCE))
    expect("short-id-refused", any("CRYST-<n>" in f for f in found))

    _, found = validate(_good_text(), known_ids={"CRYST-001"})
    expect("duplicate-id-refused", any("already exists" in f for f in found))

    # a document missing a required field entirely, not merely blank
    text = _good_text().replace("station: operative\n", "")
    _, found = validate(text)
    expect("absent-required-field-refused",
           any("missing required field 'station'" in f for f in found))

    # an undeclared field is refused rather than ignored
    text = _good_text().replace("station: operative",
                                "station: operative\nstations: operative")
    _, found = validate(text)
    expect("undeclared-field-refused", any("undeclared" in f for f in found))

    # every finding is collected, not just the first
    _, found = validate(_good_text(pattern="", reopens_when="",
                                   station="nope"))
    expect("all-findings-collected", len(found) >= 3)

    report = (f"crystallize selftest: {len(fired)} paths fired, "
              f"{len(failures)} failed"
              + (" -- " + ", ".join(failures) if failures else ""))
    return (not failures, report)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="the crystallized-pattern document contract")
    parser.add_argument("--template", metavar="CRYST-ID", default=None,
                        help="render a blank template (deliberately invalid "
                             "until its placeholders are filled)")
    parser.add_argument("--validate", metavar="PATH", default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        ok, report = selftest()
        print(report)
        return 0 if ok else 1
    if args.template:
        print(render_template(args.template), end="")
        return 0
    if args.validate:
        doc, findings = validate_file(args.validate)
        if args.json:
            print(json.dumps({"valid": doc is not None,
                              "doc": doc.to_row() if doc else None,
                              "findings": findings}, indent=2))
        elif doc is not None:
            print(f"{doc.id}: valid, station={doc.station}, "
                  f"strongest evidence grade {doc.strongest_grade}")
        else:
            print(f"{args.validate}: REFUSED", file=sys.stderr)
            for finding in findings:
                print(f"  - {finding}", file=sys.stderr)
        return 0 if doc is not None else 1
    parser.print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
