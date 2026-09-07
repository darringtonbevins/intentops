"""The metabolism runner: one enabled stage, through its declared seam, once.

PURPOSE
    ``cadence.py`` declares four stages and grades what they produced. Until
    now nothing could BE that seam: every stage shipped ``seam: "null"``, the
    only runner in the package was :class:`~.cadence.NullStage`, and a node's
    cadence could therefore only SKIP or WARN. This module is the other half --
    four workers, one per stage, and the machinery that runs exactly ONE of
    them under a declared resource envelope and records what happened.

    THE FOUR WORKERS

      ``absorb``       :class:`~intentops_core.knowledge.absorber.FileAbsorber`
                       over a directory the OPERATOR declared. There is no
                       default source; a node with no declared directory
                       absorbs nothing and says so.
      ``distill``      one :class:`~intentops_core.inference.provider.Provider`
                       call per absorbed document, using a prompt shipped in
                       ``config/metabolism-prompts.yaml``. The default provider
                       is the NULL provider, which contacts nothing -- so on a
                       node nobody has declared a pool for, this stage runs,
                       produces nothing, and renders WARN.
      ``crystallize``  the CRYST validator over the distill output. A candidate
                       that does not validate is NOT crystallized and NOT
                       discarded: it stays in the attempted population with the
                       validator's findings attached.
      ``method``       records only. It writes a method record naming each
                       crystallized pattern at station ``described`` and does
                       nothing else -- promoting a pattern is a reviewed act,
                       never a side effect of a cadence tick.

    THE SEAM IS A CLOSED REGISTRY, NOT AN IMPORT. ``cadence.py`` refuses to
    resolve a dotted path on purpose, so that a cadence file cannot become an
    execution vector. This module keeps that property: a ``callable`` seam is
    matched against :data:`CALLABLES` by exact string, and anything else is a
    HALT. Nothing here calls :func:`importlib.import_module` on operator text.

    A ``null`` seam is REFUSED for a live run rather than quietly worked
    around. A stage whose file says it reaches nothing, that in fact called a
    model, would make the declaration a lie -- and the declaration is the only
    thing a reviewer reads.

WRITE MODEL
    Three writers, each declared:

    * ``.intentops/metabolism/runs.jsonl`` -- APPEND-ONLY JSONL under
      ``StoreLock``, state as a pure fold. One record per stage run, including
      the runs that produced nothing, because a run that leaves the population
      is exactly how a metabolism comes to report green over zero. The method
      stage's records ride inside its run record rather than in a store of
      their own: a second ledger nothing reads would be capture without a
      consumer.
    * ``.intentops/metabolism/candidates/`` -- PER-ITEM FILES, one per
      candidate, named by the absorbed document's content id. Collision-safe by
      construction; re-distilling unchanged input overwrites the same path
      rather than accumulating duplicates.
    * ``.intentops/metabolism/crystallized/`` -- PER-ITEM FILES, one per
      validated pattern, named by its CRYST id.

    The absorb stage writes through the absorber's own per-item store. Nothing
    here writes the cadence file, the heartbeat, or the prompts.

BLIND SPOTS -- stated so a green run is not read as a good one
    * A candidate's grade is ALWAYS ``INFERRED``. A generator is not an
      instrument, so nothing it returns is an observation, whatever the answer
      claims about itself. This is a floor on the grade, not a judgement of the
      content -- an ``INFERRED`` candidate may still be wrong.
    * The answer is parsed by a plain string scan for two labelled lines. A
      model that answers correctly in a different shape reads as INCOMPLETE.
      That direction is deliberate: repairing an off-shape answer would put a
      claim into the population that nobody made.
    * The envelope bounds TOKENS and ITEMS per run, and pauses between items.
      It does not bound wall clock, memory, or the host's other work. It is a
      citizenship posture over a shared machine, not a scheduler.
    * The token spend it counts is the provider's, which is measured only when
      the endpoint reported usage (see the inference package). Against an
      unmetered endpoint the envelope is enforced on ESTIMATES, and the record
      says which.
    * The method stage derives no station from code. It records ``described``
      for everything and says that it did not look; a station claimed without a
      literal reference would be a promotion nobody earned.
    * ``--dry-run`` reads the filesystem to count what WOULD be processed. It
      opens no socket and writes nothing, but a plan is not a run and its item
      counts are a snapshot.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from ..inference.openai_compat import select_provider
from ..inference.provider import (
    Completion,
    InferenceError,
    ModelPool,
    NullProvider,
    Provider,
    estimate_tokens,
    load_pools,
)
from ..knowledge.absorber import FileAbsorber
from ..store_guard import StoreLock, lock_for
from .cadence import Cadence, CadenceError, Stage, StageRun, load_cadence, run_stage
from .crystallize import CRYST_ID_RE, render_template, validate

__all__ = [
    "RunnerError",
    "Envelope",
    "BIRTH_ENVELOPE",
    "PromptTemplate",
    "Plan",
    "RunRecord",
    "CALLABLES",
    "RUNS_RELPATH",
    "CANDIDATES_RELDIR",
    "CRYSTALLIZED_RELDIR",
    "PROMPTS_RELPATH",
    "MAX_SOURCE_CHARS",
    "AbsorbWorker",
    "DistillWorker",
    "CrystallizeWorker",
    "MethodWorker",
    "load_prompts",
    "resolve_worker_kind",
    "build_worker",
    "plan_stage",
    "run_metabolism_stage",
    "append_run",
    "load_runs",
    "selftest",
    "main",
]

SCHEMA = "metabolism-run/v1"

#: The append-only run ledger. A birth organ, so an empty ledger and a ledger
#: nothing ever wrote to are the same readable fact rather than one being an
#: absent file.
RUNS_RELPATH = Path(".intentops") / "metabolism" / "runs.jsonl"

#: Per-item stores the runner creates on first use.
CANDIDATES_RELDIR = Path(".intentops") / "metabolism" / "candidates"
CRYSTALLIZED_RELDIR = Path(".intentops") / "metabolism" / "crystallized"

#: Where the absorber puts what it absorbed. Read, never written, from here.
ABSORBED_RELDIR = Path(".intentops") / "knowledge" / "absorbed" / "file"

#: The shipped prompt file, relative to the repository root.
PROMPTS_RELPATH = Path("config") / "metabolism-prompts.yaml"

#: The exact strings a cadence's ``callable:`` may carry for this runner. A
#: closed registry, matched by string equality. Nothing is imported from
#: operator text.
CALLABLES: Dict[str, str] = {
    "absorb": "intentops_core.metabolism.runner:absorb",
    "distill": "intentops_core.metabolism.runner:distill",
    "crystallize": "intentops_core.metabolism.runner:crystallize",
    "method": "intentops_core.metabolism.runner:method",
}

#: How much of one document is sent to a model. A cap, not a chunker: a
#: document longer than this is sent truncated AND the record says so, because
#: a silently-shortened input produces a candidate about a document nobody
#: read. Chunking is owed work, named rather than pretended.
MAX_SOURCE_CHARS = 8000

#: The lines the distill answer is parsed for, in order.
_PATTERN_LABEL = "PATTERN:"
_REOPENS_LABEL = "REOPENS_WHEN:"

#: The answer a model gives when the document carries no falsifiable claim.
#: It is a VALID answer and is recorded as one.
_NO_CLAIM = "NO_CLAIM"

#: The only grade a candidate may carry. See BLIND SPOTS.
CANDIDATE_GRADE = "INFERRED"


class RunnerError(RuntimeError):
    """A HALT with a remedy attached, in the shape the rest of genesis prints."""

    def __init__(self, reason: str, remedy: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.remedy = remedy

    def render(self) -> str:
        out = f"HALT: {self.reason}"
        if self.remedy:
            out += f"\n  remedy: {self.remedy}"
        return out


# ---------------------------------------------------------------------------
# the envelope
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Envelope:
    """What one run of one stage may spend on a machine somebody else uses.

    Every field is REQUIRED at construction. There is a shipped default,
    :data:`BIRTH_ENVELOPE`, and it is PRINTED in every plan and written into
    every run record -- a default that is visible is a decision, a default
    nobody can see is the defect.
    """

    max_tokens: int
    max_items: int
    duty_pause_s: float

    def __post_init__(self) -> None:
        for name in ("max_tokens", "max_items"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise RunnerError(
                    f"envelope `{name}` is {value!r}",
                    "give a positive whole number; a ceiling of zero is a "
                    "disabled stage wearing a budget")
        if isinstance(self.duty_pause_s, bool) or not isinstance(
                self.duty_pause_s, (int, float)) or self.duty_pause_s < 0:
            raise RunnerError(
                f"envelope `duty_pause_s` is {self.duty_pause_s!r}",
                "give zero or a positive number of seconds")

    def to_row(self) -> Dict[str, Any]:
        return {"max_tokens": self.max_tokens, "max_items": self.max_items,
                "duty_pause_s": self.duty_pause_s}


#: The shipped posture. Small on purpose: a metabolism is background work on a
#: machine whose foreground belongs to a person, and the right failure for
#: background work is "did less than it could have", never "took the machine".
BIRTH_ENVELOPE = Envelope(max_tokens=4096, max_items=25, duty_pause_s=0.25)


# ---------------------------------------------------------------------------
# the prompts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PromptTemplate:
    """One reviewed prompt, with the placeholders it promised to carry."""

    id: str
    purpose: str
    max_tokens: int
    required_placeholders: Tuple[str, ...]
    template: str

    def render(self, **values: str) -> str:
        """Substitute the DECLARED placeholders only, by literal replacement.

        ``str.format`` is deliberately not used: a prompt is prose and prose
        contains braces, and a template that raised on an unrelated ``{`` would
        fail at the moment of the call rather than at review time.
        """
        missing = [name for name in self.required_placeholders
                   if name not in values]
        if missing:
            raise RunnerError(
                f"prompt {self.id!r} was rendered without {missing}",
                "every declared placeholder is supplied at the call site")
        text = self.template
        for name in self.required_placeholders:
            text = text.replace("{" + name + "}", str(values[name]))
        return text


def load_prompts(path: Path | str) -> Dict[str, PromptTemplate]:
    """Read ``config/metabolism-prompts.yaml``, or HALT saying exactly why."""
    path = Path(path)
    if not path.is_file():
        raise RunnerError(
            f"the prompt file is missing: {path}",
            "restore config/metabolism-prompts.yaml. The distill prompt is a "
            "reviewed governance surface, not a string built at runtime, so "
            "an absent file is a HALT rather than a built-in fallback")
    try:
        import yaml  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise RunnerError(f"the YAML library is unavailable ({exc})",
                          "pip install pyyaml") from exc
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - the parser's error class varies
        raise RunnerError(f"{path} does not parse ({exc})",
                          "fix the YAML; a partial parse is refused") from exc
    if not isinstance(doc, Mapping):
        raise RunnerError(f"{path} is not a mapping",
                          "the document carries `schema:` and `prompts:`")
    schema = str(doc.get("schema") or "")
    if schema != "metabolism-prompts/v1":
        raise RunnerError(
            f"unknown prompt schema {schema!r}",
            "this reader implements metabolism-prompts/v1 only; a schema it "
            "does not know is refused rather than read optimistically")
    raw = doc.get("prompts")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise RunnerError(f"{path} `prompts:` is missing or is not a list",
                          "declare at least the `distill` prompt")

    out: Dict[str, PromptTemplate] = {}
    for index, item in enumerate(raw):
        where = f"prompt #{index + 1}"
        if not isinstance(item, Mapping):
            raise RunnerError(f"{where} is not a mapping",
                              "each prompt is a mapping of its fields")
        for name in ("id", "purpose", "max_tokens", "required_placeholders",
                     "template"):
            if name not in item or item[name] is None:
                raise RunnerError(
                    f"{where} is missing required field `{name}`",
                    "there is no default for it; a reader that fills in a "
                    "blank has taken that field out of the population")
        prompt_id = str(item["id"]).strip()
        max_tokens = item["max_tokens"]
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) \
                or max_tokens <= 0:
            raise RunnerError(f"{where} declares max_tokens {max_tokens!r}",
                              "give a positive whole number")
        placeholders = item["required_placeholders"]
        if not isinstance(placeholders, Sequence) or isinstance(
                placeholders, (str, bytes)) or not placeholders:
            raise RunnerError(
                f"{where} declares no `required_placeholders`",
                "list every placeholder the template must carry; a template "
                "missing one is a HALT at load, never a KeyError at the "
                "moment of the call")
        template = str(item["template"])
        names = tuple(str(p) for p in placeholders)
        absent = [n for n in names if ("{" + n + "}") not in template]
        if absent:
            raise RunnerError(
                f"{where} promises placeholder(s) {absent} its template does "
                "not carry",
                "the declaration and the text disagree; correct one of them. "
                "A promised placeholder that is never substituted silently "
                "sends the model a prompt with a hole in it")
        if prompt_id in out:
            raise RunnerError(
                f"two prompts share the id {prompt_id!r}",
                "one id, one prompt; two would make every record of a call "
                "ambiguous about which text produced it")
        out[prompt_id] = PromptTemplate(
            id=prompt_id, purpose=str(item["purpose"]).strip(),
            max_tokens=int(max_tokens),
            required_placeholders=names, template=template)
    if "distill" not in out:
        raise RunnerError(
            "no `distill` prompt is declared",
            "the distill stage is the one stage that asks a model anything; "
            "without its prompt the stage cannot run and will not improvise")
    return out


# ---------------------------------------------------------------------------
# the workers
# ---------------------------------------------------------------------------


@dataclass
class _Spend:
    """What a run actually cost, and how much of that was measured.

    THREE buckets, not two. A call the endpoint reported usage for is
    MEASURED; a call we counted ourselves is ESTIMATED; a call against the null
    provider is UNCOUNTED, because nothing was spent and nothing was guessed.
    Folding the third into the second would have made a node that contacted
    nothing report "partly estimated" spend -- an estimate is a number about
    something that happened, and nothing happened.
    """

    tokens: int = 0
    calls: int = 0
    measured_calls: int = 0
    estimated_calls: int = 0
    uncounted_calls: int = 0
    stopped_on_envelope: bool = False

    def add(self, completion: Completion) -> None:
        self.tokens += completion.total_tokens
        self.calls += 1
        if completion.counted == "reported":
            self.measured_calls += 1
        elif completion.counted == "estimated":
            self.estimated_calls += 1
        else:
            self.uncounted_calls += 1

    @property
    def accounting(self) -> str:
        if not self.calls or self.uncounted_calls == self.calls:
            return "none"
        if self.estimated_calls or self.uncounted_calls:
            return "partly estimated"
        return "measured"

    def to_row(self) -> Dict[str, Any]:
        return {"tokens": self.tokens, "calls": self.calls,
                "measured_calls": self.measured_calls,
                "estimated_calls": self.estimated_calls,
                "uncounted_calls": self.uncounted_calls,
                "stopped_on_envelope": self.stopped_on_envelope,
                "accounting": self.accounting}


class _Worker:
    """Base: a callable a cadence stage can be run through, plus a record."""

    countable: str = ""

    def __init__(self, node_root: Path | str, envelope: Envelope,
                 sleep: Optional[Callable[[float], None]] = None) -> None:
        self.node_root = Path(node_root)
        self.envelope = envelope
        self._sleep = sleep or time.sleep
        self.detail: Dict[str, Any] = {}
        self.spend = _Spend()
        self.notes: List[str] = []

    def __call__(self, stage: Stage) -> Dict[str, int]:  # pragma: no cover - ABC
        raise NotImplementedError

    def pause(self) -> None:
        if self.envelope.duty_pause_s:
            self._sleep(self.envelope.duty_pause_s)

    def plan(self) -> Dict[str, Any]:
        """What this worker WOULD do. Reads the filesystem, contacts nothing."""
        return {}


class AbsorbWorker(_Worker):
    """Absorb text files from ONE directory the operator declared."""

    countable = "absorbed"

    def __init__(self, node_root: Path | str, envelope: Envelope, *,
                 source_dir: Optional[Path | str] = None,
                 sleep: Optional[Callable[[float], None]] = None) -> None:
        super().__init__(node_root, envelope, sleep)
        if not source_dir:
            raise RunnerError(
                "the absorb stage has no declared source directory",
                "pass --source-dir. A new node has NO sources, and that is the "
                "honest starting condition rather than a gap to fill with a "
                "default source list")
        self.source_dir = Path(source_dir)

    def _files(self) -> List[Path]:
        if not self.source_dir.is_dir():
            raise RunnerError(
                f"the declared source directory does not exist: {self.source_dir}",
                "an unreadable input is DEGRADED, never a quiet zero: a source "
                "that leaves the population makes every count look better")
        return sorted(p for p in self.source_dir.iterdir()
                      if p.is_file()
                      and p.suffix.lower() in FileAbsorber.SUFFIXES)

    def plan(self) -> Dict[str, Any]:
        try:
            found = len(self._files())
            unreadable = ""
        except RunnerError as exc:
            found, unreadable = 0, exc.reason
        return {"source_dir": str(self.source_dir), "files_found": found,
                "would_absorb": min(found, self.envelope.max_items),
                "writes_to": str(self.node_root / ABSORBED_RELDIR),
                "unreadable": unreadable}

    def __call__(self, stage: Stage) -> Dict[str, int]:
        files = self._files()
        absorber = FileAbsorber(self.node_root)
        absorbed, refused = 0, []
        for path in files[:self.envelope.max_items]:
            result = absorber.absorb(str(path))
            if result.success:
                absorbed += 1
            else:
                refused.append({"source": path.name, "reason": result.error})
            self.pause()
        if len(files) > self.envelope.max_items:
            self.notes.append(
                f"the envelope stopped this run at {self.envelope.max_items} "
                f"of {len(files)} files; the rest are not lost, they are not "
                "yet read")
        self.detail = {"source_dir": str(self.source_dir),
                       "files_found": len(files), "absorbed": absorbed,
                       "refused": refused}
        return {self.countable: absorbed}


class DistillWorker(_Worker):
    """One provider call per absorbed document, under the token envelope."""

    countable = "candidates"

    def __init__(self, node_root: Path | str, envelope: Envelope, *,
                 provider: Provider, prompt: PromptTemplate,
                 sleep: Optional[Callable[[float], None]] = None) -> None:
        super().__init__(node_root, envelope, sleep)
        self.provider = provider
        self.prompt = prompt
        self.out_dir = self.node_root / CANDIDATES_RELDIR

    def _sources(self) -> List[Path]:
        source_dir = self.node_root / ABSORBED_RELDIR
        if not source_dir.is_dir():
            return []
        return sorted(p for p in source_dir.iterdir()
                      if p.is_file() and p.suffix.lower() == ".md")

    def plan(self) -> Dict[str, Any]:
        found = len(self._sources())
        ceiling = min(self.prompt.max_tokens,
                      self.provider.pool.max_tokens
                      if isinstance(getattr(self.provider, "pool", None), ModelPool)
                      else self.prompt.max_tokens)
        return {"absorbed_documents": found,
                "would_call": min(found, self.envelope.max_items),
                "prompt": self.prompt.id,
                "answer_ceiling_tokens": ceiling,
                "provider": self.provider.describe(),
                "writes_to": str(self.out_dir),
                "contacts_network": not self.provider.is_null}

    def __call__(self, stage: Stage) -> Dict[str, int]:
        sources = self._sources()
        complete, incomplete, no_claim, failed = 0, [], 0, []
        self.out_dir.mkdir(parents=True, exist_ok=True)
        for path in sources[:self.envelope.max_items]:
            body = _document_body(path.read_text(encoding="utf-8"))
            truncated = len(body) > MAX_SOURCE_CHARS
            prompt_text = self.prompt.render(source=path.name,
                                             text=body[:MAX_SOURCE_CHARS])
            reserve = estimate_tokens(prompt_text) + self.prompt.max_tokens
            if self.spend.tokens + reserve > self.envelope.max_tokens:
                self.spend.stopped_on_envelope = True
                self.notes.append(
                    f"the {self.envelope.max_tokens}-token envelope stopped "
                    f"this run before {path.name}; what was not distilled is "
                    "not lost, it is not yet asked")
                break
            try:
                answer = self.provider.complete(prompt_text,
                                                max_tokens=self.prompt.max_tokens)
            except InferenceError as exc:
                failed.append({"source": path.name, "reason": exc.reason})
                self.pause()
                continue
            self.spend.add(answer)
            verdict = _parse_candidate(answer.text)
            if verdict["state"] == "complete":
                _write_candidate(self.out_dir, path.stem, verdict, answer,
                                 source=path.name, truncated=truncated)
                complete += 1
            elif verdict["state"] == "no-claim":
                no_claim += 1
            else:
                incomplete.append({"source": path.name,
                                   "reason": verdict["reason"]})
            self.pause()

        if failed:
            # A failed CALL is not an empty answer. It is named here and it
            # leaves the stage DEGRADED via the raise below when every call
            # failed -- a run whose calls all failed must never read as WARN.
            self.notes.append(f"{len(failed)} call(s) failed against the pool")
        self.detail = {"absorbed_documents": len(sources),
                       "candidates": complete, "no_claim": no_claim,
                       "incomplete": incomplete, "failed_calls": failed,
                       "provider": self.provider.describe(),
                       "prompt": self.prompt.id,
                       "grade_floor": CANDIDATE_GRADE}
        if failed and complete == 0 and no_claim == 0 and not incomplete:
            raise RunnerError(
                f"every distill call failed ({failed[0]['reason']})",
                "the pool is unreachable or refusing. A run whose every call "
                "failed is DEGRADED, never a quiet zero")
        return {self.countable: complete}


class CrystallizeWorker(_Worker):
    """The CRYST validator over the candidates. Nothing invalid is written."""

    countable = "crystallized"

    def __init__(self, node_root: Path | str, envelope: Envelope,
                 sleep: Optional[Callable[[float], None]] = None) -> None:
        super().__init__(node_root, envelope, sleep)
        self.in_dir = self.node_root / CANDIDATES_RELDIR
        self.out_dir = self.node_root / CRYSTALLIZED_RELDIR

    def _candidates(self) -> List[Path]:
        if not self.in_dir.is_dir():
            return []
        return sorted(p for p in self.in_dir.iterdir()
                      if p.is_file() and p.suffix == ".json")

    def _existing_ids(self) -> List[str]:
        if not self.out_dir.is_dir():
            return []
        return sorted(p.stem for p in self.out_dir.iterdir()
                      if p.is_file() and CRYST_ID_RE.match(p.stem))

    def plan(self) -> Dict[str, Any]:
        return {"candidates": len(self._candidates()),
                "would_crystallize": min(len(self._candidates()),
                                         self.envelope.max_items),
                "existing_patterns": len(self._existing_ids()),
                "writes_to": str(self.out_dir),
                "contacts_network": False}

    def __call__(self, stage: Stage) -> Dict[str, int]:
        candidates = self._candidates()
        known = list(self._existing_ids())
        next_number = _next_cryst_number(known)
        written, refused, unreadable = 0, [], []
        self.out_dir.mkdir(parents=True, exist_ok=True)
        for path in candidates[:self.envelope.max_items]:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001 - a bad file is DEGRADED
                unreadable.append({"candidate": path.name,
                                   "reason": f"{type(exc).__name__}: {exc}"})
                continue
            doc_id = f"CRYST-{next_number:03d}"
            text = render_template(
                doc_id,
                pattern=str(record.get("pattern") or ""),
                station="described",
                reopens_when=str(record.get("reopens_when") or ""),
                evidence=[{"grade": CANDIDATE_GRADE,
                           "claim": str(record.get("pattern") or ""),
                           "citation": str(record.get("source") or path.name)}],
                title=str(record.get("source") or path.stem),
                as_of=str(record.get("as_of") or ""))
            doc, findings = validate(text, known_ids=known)
            if doc is None:
                refused.append({"candidate": path.name, "findings": findings})
                continue
            (self.out_dir / f"{doc_id}.md").write_text(text, encoding="utf-8")
            known.append(doc_id)
            next_number += 1
            written += 1
        if unreadable:
            raise RunnerError(
                f"{len(unreadable)} candidate file(s) could not be read "
                f"({unreadable[0]['reason']})",
                "an unreadable input is DEGRADED and outranks any count; a "
                "source that leaves the population makes every number look "
                "better")
        self.detail = {"candidates": len(candidates), "crystallized": written,
                       "refused": refused, "grade": CANDIDATE_GRADE,
                       "station": "described"}
        if refused:
            self.notes.append(
                f"{len(refused)} candidate(s) did not validate and were NOT "
                "crystallized; they remain in the candidate store with the "
                "validator's findings recorded")
        return {self.countable: written}


class MethodWorker(_Worker):
    """Records only. One record per crystallized pattern, station ``described``."""

    countable = "methods"

    def __init__(self, node_root: Path | str, envelope: Envelope,
                 sleep: Optional[Callable[[float], None]] = None) -> None:
        super().__init__(node_root, envelope, sleep)
        self.in_dir = self.node_root / CRYSTALLIZED_RELDIR

    def _patterns(self) -> List[Path]:
        if not self.in_dir.is_dir():
            return []
        return sorted(p for p in self.in_dir.iterdir()
                      if p.is_file() and CRYST_ID_RE.match(p.stem))

    def plan(self) -> Dict[str, Any]:
        found = len(self._patterns())
        return {"crystallized_patterns": found,
                "would_record": min(found, self.envelope.max_items),
                "writes_to": str(self.node_root / RUNS_RELPATH),
                "station_derivation": "not attempted -- see BLIND SPOTS",
                "contacts_network": False}

    def __call__(self, stage: Stage) -> Dict[str, int]:
        patterns = self._patterns()[:self.envelope.max_items]
        records = [{"pattern_id": p.stem, "station": "described",
                    "station_basis": "recorded, never derived: no reference "
                                     "scan was run, and a station claimed "
                                     "without a literal reference is a "
                                     "promotion nobody earned"}
                   for p in patterns]
        self.detail = {"crystallized_patterns": len(patterns),
                       "records": records}
        return {self.countable: len(records)}


# ---------------------------------------------------------------------------
# candidate parsing and storage
# ---------------------------------------------------------------------------


def _document_body(text: str) -> str:
    """Strip the absorber's own header block, when one is present.

    BLIND SPOT: the split is on the absorber's literal separator. A document
    whose own body contains that separator earlier than the header's would be
    cut short; that costs content, never correctness of provenance.
    """
    marker = "\n---\n\n"
    head, sep, rest = text.partition(marker)
    return rest if sep and head.startswith("# ") else text


def _parse_candidate(answer: str) -> Dict[str, str]:
    """Deterministic two-line scan. No model in the read path."""
    stripped = (answer or "").strip()
    if not stripped:
        return {"state": "incomplete", "reason": "the answer was empty",
                "pattern": "", "reopens_when": ""}
    if stripped.upper().startswith(_NO_CLAIM):
        return {"state": "no-claim",
                "reason": "the document carries no falsifiable claim",
                "pattern": "", "reopens_when": ""}
    pattern, reopens = "", ""
    for line in stripped.splitlines():
        bare = line.strip()
        if not pattern and bare.upper().startswith(_PATTERN_LABEL):
            pattern = bare[len(_PATTERN_LABEL):].strip()
        elif not reopens and bare.upper().startswith(_REOPENS_LABEL):
            reopens = bare[len(_REOPENS_LABEL):].strip()
    if pattern and reopens:
        return {"state": "complete", "reason": "", "pattern": pattern,
                "reopens_when": reopens}
    missing = [name for name, value in ((_PATTERN_LABEL, pattern),
                                        (_REOPENS_LABEL, reopens)) if not value]
    return {"state": "incomplete",
            "reason": "the answer carried no " + " or ".join(missing)
                      + " line; it is kept and counted, never repaired",
            "pattern": pattern, "reopens_when": reopens}


def _write_candidate(out_dir: Path, stem: str, verdict: Mapping[str, str],
                     answer: Completion, *, source: str,
                     truncated: bool) -> Path:
    """One candidate, one file, named by the absorbed document's id."""
    path = out_dir / f"{stem}.json"
    path.write_text(json.dumps({
        "schema": "metabolism-candidate/v1",
        "source": source,
        "pattern": verdict["pattern"],
        "reopens_when": verdict["reopens_when"],
        "grade": CANDIDATE_GRADE,
        "grade_note": "a generator is not an instrument; nothing it returns "
                      "is an observation, whatever the answer claims",
        "source_truncated": truncated,
        "completion": answer.to_row(),
    }, indent=2) + "\n", encoding="utf-8")
    return path


def _next_cryst_number(known: Sequence[str]) -> int:
    highest = 0
    for doc_id in known:
        try:
            highest = max(highest, int(str(doc_id).split("-", 1)[1]))
        except (IndexError, ValueError):
            continue
    return highest + 1


# ---------------------------------------------------------------------------
# the seam
# ---------------------------------------------------------------------------


def resolve_worker_kind(stage: Stage) -> str:
    """Which worker a stage's declared seam names, or a HALT saying why not."""
    if stage.seam == "null":
        raise RunnerError(
            f"stage {stage.id!r} declares `seam: \"null\"`, which reaches "
            "nothing",
            "a null stage is RUN through cadence.NullStage and renders WARN. "
            "To give it a worker, set `seam: callable` and "
            f"`callable: {CALLABLES[stage.id]}` in the node's cadence -- a "
            "reviewed, dated operator act. Running a worker behind a `null` "
            "declaration would make the declaration a lie")
    if stage.seam != "callable":
        raise RunnerError(
            f"stage {stage.id!r} declares seam {stage.seam!r}",
            "this runner implements the `callable` seam. A `command` seam is "
            "the host saddle's to run, and its exit code is checked there")
    declared = str(stage.callable or "")
    expected = CALLABLES[stage.id]
    if declared != expected:
        raise RunnerError(
            f"stage {stage.id!r} declares callable {declared!r}",
            f"this runner answers to {expected!r} and matches by exact string. "
            "Nothing here imports a dotted path out of a cadence file, so a "
            "cadence cannot become an execution vector")
    return stage.id


def build_worker(kind: str, node_root: Path | str, envelope: Envelope, *,
                 provider: Optional[Provider] = None,
                 prompt: Optional[PromptTemplate] = None,
                 source_dir: Optional[Path | str] = None,
                 sleep: Optional[Callable[[float], None]] = None) -> _Worker:
    """Construct the worker for one stage. Contacts nothing; reads nothing."""
    if kind == "absorb":
        return AbsorbWorker(node_root, envelope, source_dir=source_dir,
                            sleep=sleep)
    if kind == "distill":
        if prompt is None:
            raise RunnerError("the distill worker was built with no prompt",
                              "load config/metabolism-prompts.yaml first")
        return DistillWorker(node_root, envelope,
                             provider=provider or NullProvider(),
                             prompt=prompt, sleep=sleep)
    if kind == "crystallize":
        return CrystallizeWorker(node_root, envelope, sleep=sleep)
    if kind == "method":
        return MethodWorker(node_root, envelope, sleep=sleep)
    raise RunnerError(f"unknown worker kind {kind!r}",
                      "one of: " + ", ".join(CALLABLES))


# ---------------------------------------------------------------------------
# the plan, the run, and the ledger
# ---------------------------------------------------------------------------


@dataclass
class Plan:
    """What a run WOULD do. Opens no socket and writes nothing."""

    stage_id: str
    enabled: bool
    seam: str
    envelope: Dict[str, Any]
    worker: Dict[str, Any] = field(default_factory=dict)
    blockers: List[str] = field(default_factory=list)

    @property
    def runnable(self) -> bool:
        return self.enabled and not self.blockers

    def to_row(self) -> Dict[str, Any]:
        return {"stage": self.stage_id, "enabled": self.enabled,
                "seam": self.seam, "envelope": self.envelope,
                "worker": self.worker, "blockers": list(self.blockers),
                "runnable": self.runnable}

    def render(self) -> str:
        lines = [f"metabolism run --stage {self.stage_id} --dry-run",
                 f"  enabled           {self.enabled}",
                 f"  seam              {self.seam}",
                 "  envelope          "
                 + f"max_tokens={self.envelope['max_tokens']} "
                 + f"max_items={self.envelope['max_items']} "
                 + f"duty_pause_s={self.envelope['duty_pause_s']}"]
        for key in sorted(self.worker):
            value = self.worker[key]
            if isinstance(value, Mapping):
                value = ", ".join(f"{k}={v}" for k, v in sorted(value.items()))
            lines.append(f"  {key:<17} {value}")
        for blocker in self.blockers:
            lines.append(f"  BLOCKED           {blocker}")
        lines.append("  nothing was contacted and nothing was written: a plan "
                     "is not a run")
        return "\n".join(lines)


@dataclass
class RunRecord:
    """One stage run, as it lands in the append-only ledger."""

    stage_id: str
    at: str
    verdict: str
    produced: Dict[str, int]
    seam: str
    envelope: Dict[str, Any]
    spend: Dict[str, Any]
    detail: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    unreadable: List[str] = field(default_factory=list)

    def to_row(self) -> Dict[str, Any]:
        return {"schema": SCHEMA, "stage": self.stage_id, "at": self.at,
                "verdict": self.verdict, "produced": dict(self.produced),
                "total": sum(self.produced.values()), "seam": self.seam,
                "envelope": dict(self.envelope), "spend": dict(self.spend),
                "detail": dict(self.detail), "notes": list(self.notes),
                "unreadable": list(self.unreadable)}

    def render(self) -> str:
        counts = ", ".join(f"{k}={v}" for k, v in sorted(self.produced.items()))
        head = (f"metabolism {self.stage_id}: {self.verdict}  "
                f"{counts or '-'}  tokens={self.spend.get('tokens', 0)} "
                f"({self.spend.get('accounting', 'none')})")
        return "\n".join([head] + [f"  {n}" for n in self.notes]
                         + [f"  unreadable: {u}" for u in self.unreadable])


def append_run(node_root: Path | str, record: RunRecord) -> Path:
    """Append one run to the ledger. WRITE MODEL: append-only under StoreLock."""
    path = Path(node_root) / RUNS_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record.to_row(), sort_keys=True) + "\n"
    with StoreLock(lock_for(path)):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
    return path


def load_runs(node_root: Path | str) -> List[Dict[str, Any]]:
    """The ledger, as a pure fold. An unreadable line is NAMED, never skipped."""
    path = Path(node_root) / RUNS_RELPATH
    if not path.is_file():
        return []
    out: List[Dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except Exception as exc:  # noqa: BLE001 - a bad line stays visible
            out.append({"schema": SCHEMA, "unreadable_line": number,
                        "reason": f"{type(exc).__name__}: {exc}"})
    return out


def plan_stage(cadence: Cadence, stage_id: str, *,
               node_root: Path | str,
               envelope: Envelope = BIRTH_ENVELOPE,
               provider: Optional[Provider] = None,
               prompt: Optional[PromptTemplate] = None,
               source_dir: Optional[Path | str] = None) -> Plan:
    """Render the plan for one stage. Contacts nothing, writes nothing."""
    stage = _stage(cadence, stage_id)
    blockers: List[str] = []
    worker_view: Dict[str, Any] = {}
    if not stage.enabled:
        blockers.append(
            f"stage {stage.id!r} is disabled; switching one on is a reviewed, "
            "dated operator act")
    try:
        kind = resolve_worker_kind(stage)
        worker = build_worker(kind, node_root, envelope, provider=provider,
                              prompt=prompt, source_dir=source_dir)
        worker_view = worker.plan()
    except RunnerError as exc:
        blockers.append(exc.reason)
    return Plan(stage_id=stage.id, enabled=stage.enabled, seam=stage.seam,
                envelope=envelope.to_row(), worker=worker_view,
                blockers=blockers)


def run_metabolism_stage(cadence: Cadence, stage_id: str, *,
                         node_root: Path | str, now: str,
                         envelope: Envelope = BIRTH_ENVELOPE,
                         provider: Optional[Provider] = None,
                         prompt: Optional[PromptTemplate] = None,
                         source_dir: Optional[Path | str] = None,
                         sleep: Optional[Callable[[float], None]] = None,
                         append: bool = True) -> RunRecord:
    """Run ONE stage through its declared seam and record what happened.

    Grading is delegated to :func:`cadence.run_stage` -- the same fold that
    grades a null stage grades this one, so a worker cannot invent a verdict
    vocabulary of its own, and in particular cannot render zero output green.
    """
    stage = _stage(cadence, stage_id)
    if not stage.enabled:
        result = run_stage(stage, None)
        record = RunRecord(stage.id, now, result.verdict, result.produced,
                           stage.seam, envelope.to_row(), _Spend().to_row(),
                           detail={}, notes=list(result.notes))
        if append:
            append_run(node_root, record)
        return record

    kind = resolve_worker_kind(stage)
    worker = build_worker(kind, node_root, envelope, provider=provider,
                          prompt=prompt, source_dir=source_dir, sleep=sleep)
    result: StageRun = run_stage(stage, worker)
    record = RunRecord(
        stage_id=stage.id, at=now, verdict=result.verdict,
        produced=result.produced, seam=stage.seam,
        envelope=envelope.to_row(), spend=worker.spend.to_row(),
        detail=worker.detail,
        notes=list(worker.notes) + list(result.notes),
        unreadable=list(result.unreadable))
    if append:
        append_run(node_root, record)
    return record


def _stage(cadence: Cadence, stage_id: str) -> Stage:
    try:
        return cadence.stage(stage_id)
    except KeyError:
        raise RunnerError(f"the cadence declares no stage {stage_id!r}",
                          "one of: " + ", ".join(CALLABLES)) from None


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


def _cadence_doc(stage_id: str, **overrides: Any) -> Dict[str, Any]:
    stages = []
    for sid in ("absorb", "distill", "crystallize", "method"):
        row: Dict[str, Any] = {
            "id": sid, "purpose": f"the {sid} stage", "seam": "null",
            "enabled": False, "produces": [_COUNTABLE[sid]],
            "on_empty": "warn", "tier_ceiling": "T1"}
        if sid == stage_id:
            row.update({"seam": "callable", "enabled": True,
                        "callable": CALLABLES[sid]})
            row.update(overrides)
        stages.append(row)
    return {"schema": "metabolism-cadence/v1", "as_of": "2026-09-06",
            "stages": stages}


_COUNTABLE = {"absorb": "absorbed", "distill": "candidates",
              "crystallize": "crystallized", "method": "methods"}


def selftest() -> Tuple[bool, str]:
    """Prove the runner's refusals fire and its verdicts cannot be gamed.

    Everything below runs in a temporary directory against the null provider
    and a hand-built prompt. No socket is opened and no clock is read for a
    decision.
    """
    import tempfile  # noqa: PLC0415 - selftest-only
    from .cadence import parse_cadence  # noqa: PLC0415 - avoids a cycle at import

    failures: List[str] = []
    prompt = PromptTemplate(
        id="distill", purpose="selftest", max_tokens=32,
        required_placeholders=("source", "text"),
        template="{source}\n{text}\n")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        sources = root / "sources"
        sources.mkdir()
        (sources / "one.md").write_text("a document", encoding="utf-8")

        # A NULL seam is refused rather than worked around.
        null_cadence = parse_cadence(_cadence_doc("absorb", seam="null",
                                                  callable=None))
        try:
            resolve_worker_kind(null_cadence.stage("absorb"))
            failures.append("ran a worker behind a null seam")
        except RunnerError:
            pass

        # A callable string this runner does not answer to is refused.
        try:
            resolve_worker_kind(parse_cadence(_cadence_doc(
                "absorb", callable="os:system")).stage("absorb"))
            failures.append("accepted a callable this runner does not declare")
        except RunnerError:
            pass

        # A disabled stage SKIPS and is still recorded.
        off = parse_cadence(_cadence_doc("absorb", enabled=False,
                                         seam="null", callable=None))
        rec = run_metabolism_stage(off, "absorb", node_root=root,
                                   now="2026-09-06T00:00:00Z",
                                   sleep=lambda _s: None)
        if rec.verdict != "SKIPPED":
            failures.append("a disabled stage did not read as SKIPPED")

        # Absorb over a real directory produces work; the ledger grows.
        on = parse_cadence(_cadence_doc("absorb"))
        rec = run_metabolism_stage(on, "absorb", node_root=root,
                                   now="2026-09-06T00:01:00Z",
                                   source_dir=sources, sleep=lambda _s: None)
        if rec.verdict != "OK" or rec.produced.get("absorbed") != 1:
            failures.append(f"absorb did not read as OK ({rec.verdict})")

        # Distill against the NULL provider: it runs, produces nothing, WARNs.
        dist = parse_cadence(_cadence_doc("distill"))
        rec = run_metabolism_stage(dist, "distill", node_root=root,
                                   now="2026-09-06T00:02:00Z",
                                   provider=NullProvider(), prompt=prompt,
                                   sleep=lambda _s: None)
        if rec.verdict != "WARN":
            failures.append(f"the null provider did not WARN ({rec.verdict})")
        if rec.spend["tokens"] != 0:
            failures.append("the null provider spent tokens")

        # An unreadable source directory is DEGRADED, never a quiet zero.
        rec = run_metabolism_stage(on, "absorb", node_root=root,
                                   now="2026-09-06T00:03:00Z",
                                   source_dir=root / "nowhere",
                                   sleep=lambda _s: None)
        if rec.verdict != "DEGRADED":
            failures.append("a missing source directory did not read DEGRADED")

        if len(load_runs(root)) != 4:
            failures.append("the run ledger did not record every run")

    # Envelope construction refuses a zero ceiling.
    for bad in ({"max_tokens": 0}, {"max_items": 0}, {"duty_pause_s": -1}):
        kwargs = {"max_tokens": 10, "max_items": 1, "duty_pause_s": 0.0}
        kwargs.update(bad)
        try:
            Envelope(**kwargs)  # type: ignore[arg-type]
            failures.append(f"accepted envelope {bad}")
        except RunnerError:
            pass

    # The answer parser: complete, no-claim, and incomplete are three states.
    if _parse_candidate("PATTERN: a\nREOPENS_WHEN: b")["state"] != "complete":
        failures.append("a complete answer did not parse")
    if _parse_candidate("NO_CLAIM")["state"] != "no-claim":
        failures.append("NO_CLAIM did not parse as a valid empty answer")
    if _parse_candidate("PATTERN: a")["state"] != "incomplete":
        failures.append("a half answer was not incomplete")
    if _parse_candidate("")["state"] != "incomplete":
        failures.append("an empty answer was not incomplete")

    report = ("metabolism.runner: a clause did not fire" if failures else
              "metabolism.runner: the null seam is refused, a disabled stage "
              "skips, the null provider WARNs at zero, an unreadable input is "
              "DEGRADED, and every run is in the ledger")
    if failures:
        report += " -- " + "; ".join(failures)
    return (not failures, report)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_provider(estate_dir: Optional[str],
                      pool_id: Optional[str]) -> Provider:
    """The null provider unless an operator declared AND enabled a pool."""
    if not estate_dir:
        return NullProvider()
    pools = load_pools(estate_dir)
    if not pools:
        return NullProvider()
    return select_provider(pools, pool_id)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="intentops metabolism run",
        description="run ONE enabled stage through its declared seam")
    # NOT `required=True`. `--selftest` is a whole-module verb that names no
    # stage, and argparse enforces `required` BEFORE any code in this
    # function runs -- so a required `--stage` made
    # `runner.py --selftest` exit 2 with a usage line, and made
    # `intentops metabolism run --selftest` (which delegates here) do the
    # same. A detector that cannot be invoked is indistinguishable from a
    # broken one. The requirement is re-imposed below, after the selftest
    # branch, so a run with neither still HALTs rather than defaulting.
    parser.add_argument("--stage", default=None, choices=sorted(CALLABLES))
    parser.add_argument("--node-root", default=".")
    parser.add_argument("--cadence", default=None,
                        help="the cadence file (default: "
                             "<node-root>/.intentops/metabolism/cadence.yaml)")
    parser.add_argument("--prompts", default=None,
                        help=f"the prompt file (default: {PROMPTS_RELPATH})")
    parser.add_argument("--estate", default=None,
                        help="the estate directory holding RESOURCES.yaml; "
                             "omitted means the null provider, which contacts "
                             "nothing")
    parser.add_argument("--pool", default=None, help="which declared pool to use")
    parser.add_argument("--source-dir", default=None,
                        help="the operator-declared absorb directory")
    parser.add_argument("--max-tokens", type=int, default=BIRTH_ENVELOPE.max_tokens)
    parser.add_argument("--max-items", type=int, default=BIRTH_ENVELOPE.max_items)
    parser.add_argument("--duty-pause", type=float,
                        default=BIRTH_ENVELOPE.duty_pause_s)
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan; contact nothing, write nothing")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        ok, report = selftest()
        print(report)
        return 0 if ok else 1

    if not args.stage:
        parser.error("--stage is required unless --selftest is given")

    node_root = Path(args.node_root)
    cadence_path = Path(args.cadence) if args.cadence else (
        node_root / ".intentops" / "metabolism" / "cadence.yaml")
    try:
        envelope = Envelope(max_tokens=args.max_tokens,
                            max_items=args.max_items,
                            duty_pause_s=args.duty_pause)
        cadence = load_cadence(cadence_path)
        prompt = None
        if args.stage in ("distill", "method"):
            prompts = load_prompts(Path(args.prompts) if args.prompts
                                   else PROMPTS_RELPATH)
            prompt = prompts.get(args.stage) or prompts["distill"]
        provider = (NullProvider() if args.dry_run and not args.estate
                    else _resolve_provider(args.estate, args.pool))
    except (RunnerError, CadenceError, InferenceError) as exc:
        print(exc.render(), file=sys.stderr)
        return 1

    if args.dry_run:
        plan = plan_stage(cadence, args.stage, node_root=node_root,
                          envelope=envelope, provider=provider, prompt=prompt,
                          source_dir=args.source_dir)
        print(json.dumps(plan.to_row(), indent=2) if args.json
              else plan.render())
        return 0 if plan.runnable else 1

    try:
        record = run_metabolism_stage(
            cadence, args.stage, node_root=node_root, now=_now(),
            envelope=envelope, provider=provider, prompt=prompt,
            source_dir=args.source_dir)
    except (RunnerError, InferenceError) as exc:
        print(exc.render(), file=sys.stderr)
        return 1
    print(json.dumps(record.to_row(), indent=2) if args.json else record.render())
    # A WARN or DEGRADED run exits non-zero: those are the readings a checklist
    # must not step over.
    return 0 if record.verdict in ("OK", "SKIPPED") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
