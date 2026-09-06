"""The alignment interview -- loader, stage gate, and answer journal.

PURPOSE
    A node cannot inherit alignment from anywhere but its own operator's own
    rulings on its own machine. What generalises is the SHAPE of the interview
    that elicits them, and -- more importantly -- the loader's REFUSALS, which
    are the instrument rather than the prose describing them.

    This module loads ``config/alignment-interview.template.yaml`` (schema
    ``alignment-interview/v1``), gates its sittings by EVIDENCE AVAILABLE
    rather than by clock, renders replay questions with their options shuffled
    by a seeded RNG the caller supplies, and journals answers into the bound
    identity repository.

THE COLD-START PROBLEM, STATED
    At genesis there are no replays. A fresh operator has ruled nothing, so
    the single highest-value elicitation instrument is unavailable on day one
    BY CONSTRUCTION. S0/S1/S2 open immediately; S3 opens at 10 real operator
    rulings in this node's own approval history and S4 at 20. S2's output is
    graded ``[INFERRED]`` and may NEVER be cited as evidence of alignment: a
    prior that has never moved under a real ruling is the node's own prompt
    reflected back (the movement test).

WRITE MODEL (declared at birth, per the store-write discipline)
    Append-only event journal at
    ``<identity-repo>/identity/alignment/answers.jsonl``. One writer appends
    under a kernel-released ``StoreLock``; the load-validate-append sequence
    runs INSIDE the lock, and state is a pure FOLD of the events. Nothing ever
    rewrites the file. There is NO unlocked fallback -- if ``StoreLock`` cannot
    be imported the append is REFUSED, loudly.

    The template itself is a reviewed, human-authored config with exactly one
    writer (a reviewed commit). This module never writes it.

BLIND SPOTS -- stated so a green load is not read as a clean bill of health
    * The loader validates SHAPE, never fitness. A perfectly-shaped question
      that elicits nothing useful passes every refusal here.
    * ``operator_rulings`` counts rulings whose actor is not in the node's own
      declared non-operator actor sets. An operator who rules under an
      identifier the node was never told about is counted as the operator, and
      a node actor nobody declared is counted as the operator too. The policy
      must be supplied; there is no default, because defaulting would inflate
      the denominator in the direction of calibration.
    * A ``[INFERRED]`` grade marks provenance, not quality. It says the answer
      came from a hypothetical, not that the answer is wrong.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import yaml

from ..store_guard import StoreLock, lock_for

__all__ = [
    "AnswerJournal",
    "Interview",
    "InterviewError",
    "Module",
    "Question",
    "RenderedQuestion",
    "STAGE_THRESHOLDS",
    "Stage",
    "grade_for_sitting",
    "load_interview",
    "operator_rulings",
    "render_question",
    "reveal_prior_ruling",
    "stage_plan",
]

SCHEMA = "alignment-interview/v1"
JOURNAL_RELPATH = Path("identity") / "alignment" / "answers.jsonl"

#: Sittings open by EVIDENCE AVAILABLE, never by clock. The numbers are the
#: design's own table (genesis-design sect. 7.2): S0-S2 immediately, S3 at 10
#: real rulings, S4 at 20 -- and 20 is the calibration bar itself, which
#: ``load_interview`` asserts against the template rather than trusting.
STAGE_THRESHOLDS: Dict[str, int] = {"S0": 0, "S1": 0, "S2": 0, "S3": 10,
                                    "S4": 20}

#: The grade each sitting's output carries. S2 is [INFERRED] by construction:
#: it may shape a prediction and may never be cited as evidence of alignment.
STAGE_GRADES: Dict[str, str] = {
    "S0": "recorded fact",
    "S1": "recorded fact",
    "S2": "[INFERRED]",
    "S3": "[OBSERVED]",
    "S4": "[OBSERVED]",
}

PROVENANCE = frozenset({"durable", "situational"})
MAX_OPTIONS = 4

#: History events that name a RULING. An expiry is a lapse, not a ruling, and
#: counting it would credit the operator with a decision nobody made.
RULING_EVENTS = frozenset({"approved", "rejected"})


class InterviewError(RuntimeError):
    """A refusal. Every one of these is a shape the loader will not load."""


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Question:
    id: str
    kind: str
    tier: str
    sitting: str
    prompt: str
    rationale: str
    variables: Tuple[str, ...] = ()
    technique: str = ""
    provenance: str = "durable"
    options: Tuple[str, ...] = ()
    recommended: Optional[int] = None
    replay: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class Module:
    id: str
    title: str
    sitting: str
    why: str
    questions: Tuple[Question, ...]


@dataclass(frozen=True)
class Interview:
    path: Path
    kinds: Tuple[str, ...]
    tiers: Tuple[str, ...]
    sittings: Tuple[str, ...]
    variables: Tuple[str, ...]
    variables_floor: Dict[str, Any]
    calibration_bar: Dict[str, Any]
    modules: Tuple[Module, ...]

    def questions(self) -> List[Question]:
        return [q for m in self.modules for q in m.questions]

    def for_sitting(self, sitting: str) -> List[Question]:
        return [q for q in self.questions() if q.sitting == sitting]


@dataclass(frozen=True)
class Stage:
    id: str
    available: bool
    requires: str
    grade: str
    rulings_needed: int


@dataclass(frozen=True)
class RenderedQuestion:
    """A question as the operator sees it -- and what they must NOT see yet."""

    question_id: str
    kind: str
    prompt: str
    options: Tuple[str, ...]
    order: Tuple[int, ...]
    shuffled: bool
    recommended: Optional[int] = None
    prior_ruling_visible: bool = False


# ---------------------------------------------------------------------------
# loader -- the refusals ARE the instrument
# ---------------------------------------------------------------------------


def _require(doc: Mapping[str, Any], key: str, kind: type) -> Any:
    """A load-bearing field missing means HALT, never a default.

    A reader that substitutes a default for a missing field has left that
    field out of the population: nothing errors, the output looks right, and
    the one field nobody read is the one nobody checks.
    """
    if key not in doc:
        raise InterviewError(
            f"the interview declares no {key!r} -- refusing to substitute a "
            "default for a load-bearing field")
    value = doc[key]
    if not isinstance(value, kind):
        raise InterviewError(
            f"{key!r} is {type(value).__name__}, expected {kind.__name__}")
    if not value:
        raise InterviewError(f"{key!r} is empty; a closed vocabulary with no "
                             "members admits nothing and refuses nothing")
    return value


def load_interview(path: Path | str) -> Interview:
    """Load and VALIDATE the interview. Raises -- never warns, never skips."""
    path = Path(path)
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise InterviewError(f"the interview is unparseable: {exc}") from exc
    if not isinstance(doc, dict):
        raise InterviewError(
            f"interview root is {type(doc).__name__}, expected a mapping")
    if doc.get("schema") != SCHEMA:
        raise InterviewError(
            f"schema is {doc.get('schema')!r}, expected {SCHEMA!r}")

    kinds = tuple(_require(doc, "kinds", list))
    tiers = tuple(_require(doc, "tiers", list))
    sittings_raw = _require(doc, "sittings", list)
    sittings = tuple(str(s.get("id")) for s in sittings_raw
                     if isinstance(s, dict))
    if len(sittings) != len(sittings_raw) or not all(sittings):
        raise InterviewError("every sitting needs an id")
    variables_map = _require(doc, "variables", dict)
    variables = tuple(variables_map)
    floor = doc.get("variables_floor") or {}
    if not isinstance(floor, dict):
        raise InterviewError("variables_floor must be a mapping")
    bar = _require(doc, "calibration_bar", dict)

    # A floor entry that names a variable the file does not declare is a
    # dropped axis wearing a floor's clothes.
    for name in floor.get("cannot_remove") or ():
        if name not in variables_map:
            raise InterviewError(
                f"variables_floor names {name!r}, which the variables map does "
                "not declare -- a floor over a missing axis protects nothing")

    # A template whose bar disagrees with its own stage gate is a
    # contradiction, and the two numbers would silently drift apart.
    minimum = bar.get("minimum_rulings")
    if minimum != STAGE_THRESHOLDS["S4"]:
        raise InterviewError(
            f"calibration_bar.minimum_rulings is {minimum!r} but the S4 stage "
            f"gate opens at {STAGE_THRESHOLDS['S4']}; the bar and the gate "
            "must be the same number")

    seen: Dict[str, str] = {}

    def claim(identifier: Any, what: str) -> str:
        if not identifier or not isinstance(identifier, str):
            raise InterviewError(f"a {what} has no id")
        if identifier in seen:
            raise InterviewError(
                f"duplicate id {identifier!r} ({what} collides with "
                f"{seen[identifier]})")
        seen[identifier] = what
        return identifier

    modules: List[Module] = []
    for raw_module in _require(doc, "modules", list):
        if not isinstance(raw_module, dict):
            raise InterviewError("every module must be a mapping")
        module_id = claim(raw_module.get("id"), "module")
        module_sitting = str(raw_module.get("sitting", ""))
        if module_sitting not in sittings:
            raise InterviewError(
                f"module {module_id!r} names sitting {module_sitting!r}, "
                f"which is not declared")
        questions: List[Question] = []
        for raw in raw_module.get("questions") or ():
            questions.append(_load_question(raw, claim, kinds, tiers, sittings,
                                            variables_map))
        modules.append(Module(id=module_id,
                              title=str(raw_module.get("title", "")),
                              sitting=module_sitting,
                              why=str(raw_module.get("why", "")),
                              questions=tuple(questions)))

    return Interview(path=path, kinds=kinds, tiers=tiers, sittings=sittings,
                     variables=variables, variables_floor=floor,
                     calibration_bar=dict(bar), modules=tuple(modules))


def _load_question(raw: Any, claim, kinds: Sequence[str],
                   tiers: Sequence[str], sittings: Sequence[str],
                   variables_map: Mapping[str, Any]) -> Question:
    if not isinstance(raw, dict):
        raise InterviewError("every question must be a mapping")
    qid = claim(raw.get("id"), "question")

    kind = str(raw.get("kind", ""))
    if kind not in kinds:
        raise InterviewError(f"question {qid!r}: kind {kind!r} is not one of "
                             f"{list(kinds)}")
    tier = str(raw.get("tier", ""))
    if tier not in tiers:
        raise InterviewError(f"question {qid!r}: tier {tier!r} is not one of "
                             f"{list(tiers)}")
    sitting = str(raw.get("sitting", ""))
    if sitting not in sittings:
        raise InterviewError(f"question {qid!r}: sitting {sitting!r} is not "
                             "declared")

    for field_name in ("prompt", "rationale"):
        if not str(raw.get(field_name) or "").strip():
            raise InterviewError(f"question {qid!r} has no {field_name}")

    provenance = str(raw.get("provenance", ""))
    if provenance not in PROVENANCE:
        raise InterviewError(
            f"question {qid!r}: provenance {provenance!r} is not one of "
            f"{sorted(PROVENANCE)}")

    variables = tuple(raw.get("variables") or ())
    for name in variables:
        if name not in variables_map:
            raise InterviewError(
                f"question {qid!r} names variable {name!r}, which the "
                "variables map does not declare")

    options = tuple(raw.get("options") or ())
    if len(options) > MAX_OPTIONS:
        raise InterviewError(
            f"question {qid!r} carries {len(options)} options; the maximum is "
            f"{MAX_OPTIONS}")

    recommended = raw.get("recommended")
    if recommended is not None:
        if not isinstance(recommended, int) or isinstance(recommended, bool):
            raise InterviewError(
                f"question {qid!r}: recommended must be an option index")
        if not 0 <= recommended < len(options):
            raise InterviewError(
                f"question {qid!r}: recommended index {recommended} points "
                f"outside its {len(options)} options")
        # A consent instrument whose standing recommendation is the widest
        # scope is not a consent instrument.
        if kind == "consent":
            raise InterviewError(
                f"question {qid!r} is a consent question carrying a "
                "recommendation; a consent instrument with a standing "
                "recommendation is not one")
        # A replay's recommendation would point at the prior ruling before it
        # is revealed, which is the whole thing the replay withholds.
        if kind == "replay":
            raise InterviewError(
                f"question {qid!r} is a replay carrying a recommendation; the "
                "prior ruling is revealed only after the answer")

    replay = raw.get("replay")
    if kind == "replay":
        if not isinstance(replay, dict):
            raise InterviewError(
                f"replay question {qid!r} carries no replay block")
        if "approval_id" not in replay:
            raise InterviewError(
                f"replay question {qid!r} names no approval id to replay")
        if replay.get("shuffle_options") is not True:
            raise InterviewError(
                f"replay question {qid!r} is not marked for shuffle; an "
                "unshuffled replay whose first option is always the prior "
                "ruling measures nothing")
        if replay.get("reveal_prior_ruling") != "after_answer":
            raise InterviewError(
                f"replay question {qid!r} must reveal the prior ruling only "
                "after the answer")

    return Question(id=qid, kind=kind, tier=tier, sitting=sitting,
                    prompt=str(raw["prompt"]).strip(),
                    rationale=str(raw["rationale"]).strip(),
                    variables=variables, technique=str(raw.get("technique", "")),
                    provenance=provenance, options=options,
                    recommended=recommended,
                    replay=dict(replay) if isinstance(replay, dict) else None)


# ---------------------------------------------------------------------------
# stage gate -- by evidence available, never by clock
# ---------------------------------------------------------------------------


def stage_plan(rulings: int = 0) -> List[Stage]:
    """Which sittings are open, given this node's own ruling count.

    Pure and template-free on purpose: a defect in the template must not be
    able to strand genesis, and the thresholds are the design's numbers, not
    the template's prose.
    """
    if rulings < 0:
        raise InterviewError("a negative ruling count is not a count")
    plan: List[Stage] = []
    for stage_id, needed in STAGE_THRESHOLDS.items():
        plan.append(Stage(
            id=stage_id,
            available=rulings >= needed,
            requires=("immediately" if needed == 0
                      else f"at least {needed} real rulings by this operator "
                           "in this node's own history"),
            grade=STAGE_GRADES[stage_id],
            rulings_needed=needed))
    return plan


def grade_for_sitting(sitting: str) -> str:
    """The grade a sitting's answers carry. Derived, never passed in."""
    try:
        return STAGE_GRADES[sitting]
    except KeyError:
        raise InterviewError(
            f"sitting {sitting!r} has no declared grade; refusing to grade an "
            "answer from a sitting nobody declared") from None


@dataclass(frozen=True)
class ActorPolicy:
    """Who is NOT the operator, declared by the node rather than guessed.

    There is no default. Defaulting would count the node's own decisions and
    its delegated lane's toward the operator's total -- inflating the
    denominator in the direction of calibration, which is the dangerous
    direction to be wrong in.
    """

    node_actors: frozenset = field(default_factory=frozenset)
    lane_actors: frozenset = field(default_factory=frozenset)

    def is_operator(self, actor: Optional[str]) -> bool:
        if not actor or not str(actor).strip():
            return False  # a ruling naming nobody is not the operator's
        name = str(actor).strip().lower()
        return (name not in {a.lower() for a in self.node_actors}
                and name not in {a.lower() for a in self.lane_actors})


def operator_rulings(history: Sequence[Mapping[str, Any]],
                     policy: ActorPolicy) -> int:
    """Count REAL rulings by this operator in this node's own history.

    Counts each approval id once, on a ruling event only. An expiry is a
    lapse, not a ruling -- crediting it would count a decision nobody made.
    """
    if policy is None:
        raise InterviewError(
            "an actor policy is required: without one, the node's own "
            "decisions would be counted as the operator's")
    seen: set = set()
    for row in history:
        if not isinstance(row, Mapping) or row.get("corrupt"):
            continue
        if row.get("event") not in RULING_EVENTS:
            continue
        if not policy.is_operator(row.get("decided_by")):
            continue
        identifier = row.get("id")
        if identifier:
            seen.add(identifier)
    return len(seen)


# ---------------------------------------------------------------------------
# rendering -- the shuffle is not decoration
# ---------------------------------------------------------------------------


def render_question(question: Question,
                    rng: Optional[random.Random] = None) -> RenderedQuestion:
    """Render one question. Replay options are shuffled by the caller's RNG.

    The RNG is passed in, never created here: a shuffle nobody can reproduce
    cannot be audited, and a replay rendered with no shuffler at all measures
    nothing.
    """
    if question.kind != "replay":
        return RenderedQuestion(
            question_id=question.id, kind=question.kind,
            prompt=question.prompt, options=question.options,
            order=tuple(range(len(question.options))), shuffled=False,
            recommended=question.recommended, prior_ruling_visible=False)

    if rng is None:
        raise InterviewError(
            f"replay question {question.id!r} rendered with no seeded RNG; an "
            "unshuffled replay whose first option is always the prior ruling "
            "measures nothing")
    order = list(range(len(question.options)))
    rng.shuffle(order)
    return RenderedQuestion(
        question_id=question.id, kind=question.kind, prompt=question.prompt,
        options=tuple(question.options[i] for i in order),
        order=tuple(order), shuffled=True, recommended=None,
        prior_ruling_visible=False)


def reveal_prior_ruling(question: Question, *, answered: bool) -> Dict[str, Any]:
    """Reveal the replayed approval -- only after the operator has answered."""
    if question.kind != "replay":
        raise InterviewError(
            f"question {question.id!r} is not a replay; there is no prior "
            "ruling to reveal")
    if not answered:
        raise InterviewError(
            f"refusing to reveal the prior ruling for {question.id!r} before "
            "the operator has answered")
    replay = question.replay or {}
    return {"approval_id": replay.get("approval_id"),
            "revealed": "after_answer"}


# ---------------------------------------------------------------------------
# the answer journal
# ---------------------------------------------------------------------------


class AnswerJournal:
    """Append-only journal of interview answers in the bound identity repo."""

    def __init__(self, identity_repo: Path | str) -> None:
        self.identity_repo = Path(identity_repo)
        self.path = self.identity_repo / JOURNAL_RELPATH

    def _append(self, row: Dict[str, Any]) -> Dict[str, Any]:
        if StoreLock is None or lock_for is None:  # pragma: no cover
            raise InterviewError(
                "answer append refused: StoreLock (intentops_core.store_guard) "
                "is unavailable, and a silent lockless append is the "
                "shared-whiteboard defect")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with StoreLock(lock_for(self.path)):
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    def record(self, question: Question, answer: Any, *,
               rulings: int, answered_by: str,
               at: str, dry_run: bool = False) -> Dict[str, Any]:
        """Record one answer, GRADED BY ITS SITTING and never by the caller."""
        if not str(answered_by or "").strip():
            raise InterviewError(
                "an answer must name who gave it; an unattributed answer is "
                "not an operator's answer")
        plan = {s.id: s for s in stage_plan(rulings)}
        stage = plan.get(question.sitting)
        if stage is None:
            raise InterviewError(
                f"question {question.id!r} names sitting {question.sitting!r}, "
                "which has no stage gate")
        if not stage.available:
            raise InterviewError(
                f"sitting {question.sitting} is not open: it needs "
                f"{stage.rulings_needed} real rulings and this node has "
                f"{rulings}")
        row = {
            "schema": "alignment-answer/v1",
            "at": at,
            "question_id": question.id,
            "sitting": question.sitting,
            "kind": question.kind,
            "tier": question.tier,
            "variables": list(question.variables),
            "provenance": question.provenance,
            "answer": answer,
            "answered_by": answered_by,
            "grade": grade_for_sitting(question.sitting),
            "rulings_at_answer": rulings,
            "dry_run": bool(dry_run),
        }
        return self._append(row)

    def load(self) -> List[Dict[str, Any]]:
        """State is a pure fold. An unreadable line is COUNTED, never skipped."""
        if not self.path.exists():
            return []
        rows: List[Dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8",
                                        errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except (ValueError, TypeError):
                rows.append({"corrupt": True})
        return rows
