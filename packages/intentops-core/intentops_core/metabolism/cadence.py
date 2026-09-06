"""The metabolism cadence: four declared stages, and a seam under each one.

PURPOSE
    Read a cadence file (``config/metabolism-cadence.template.yaml``, or a
    node's own copy at ``.intentops/metabolism/cadence.yaml``) and either
    return fully-validated :class:`Stage` objects or HALT with a remedy; then
    run them through a declared SEAM, recording what each stage actually
    produced.

    The four stages are fixed and ordered -- ``absorb -> distill ->
    crystallize -> method``. They are not configuration: a cadence missing one
    is indistinguishable from a cadence whose packaging dropped one, and the
    two have opposite consequences, so a missing stage is a HALT.

    THE DEFECT THIS MODULE EXISTS TO RETIRE. The reference implementation this
    generalises from ran a nightly cadence that reported ``Success`` over ZERO
    work for at least six days: one stage could not import its own entry point
    and exited on an unrecognised flag, and a second stamped its sub-checks as
    done without calling any of them. Both were green throughout. So the
    verdict vocabulary here has no way to say "ran, produced nothing, fine":

      * ``OK``       ran, and moved at least one of its declared countables
      * ``WARN``     ran, and moved nothing. Zero output is a STATE, not a
                     success. There is no configuration that turns this green
      * ``DEGRADED`` could not READ one of its own inputs. Outranks WARN and
                     outranks OK, because a source that leaves the population
                     makes every number look BETTER
      * ``HALT``     ran, produced nothing, and declared ``on_empty: halt``
      * ``SKIPPED``  disabled. An INTENTIONAL skip is allowed to be quiet; an
                     unexpected failure is not. That distinction is the whole
                     of the no-silent-failures rule and it is the only reason
                     ``SKIPPED`` exists as a separate word from ``WARN``

    THE SEAM. ``seam: "null"`` is the birth state and resolves to
    :class:`NullStage`, which RECORDS THE INTENT of the stage and produces
    nothing. That is not a placeholder for "unimplemented" -- it is the honest
    statement that this node has not been told how to absorb anything yet, and
    it is recorded as a run rather than omitted. ``command`` and ``callable``
    seams are DECLARED here and executed by the caller: this module never
    spawns a process and never imports a dotted path, so a cadence file cannot
    become an execution vector.

WRITE MODEL
    This module is a READER and a PURE RUNNER. It never writes a cadence file
    and never writes a ledger. :func:`run_cadence` returns a
    :class:`CadenceRun`; persisting one is
    :mod:`intentops_core.metabolism.heartbeat`'s job, and that store declares
    its own model (append-only JSONL under ``StoreLock``).

    The cadence FILE is a single-writer store: the operator, by hand or through
    one explicit generator, one process at a time. No lock is claimed here.

BLIND SPOTS -- stated so a clean load is not read as a good cadence
    * Every closed vocabulary below is a set of STRINGS. This module can tell
      declared from undeclared. It cannot tell a plausible-but-wrong value
      (``tier_ceiling: T1`` on a stage that actually writes to production)
      from a correct one. Only a reviewer reads intent.
    * ``purpose`` and ``produces`` are checked for presence and non-emptiness,
      never for quality. "does the thing" satisfies this loader.
    * A countable is an INTEGER a runner reports. This module does not verify
      that the integer describes anything real; a runner that returns
      ``{"absorbed": 7}`` having absorbed nothing passes here. The
      cross-check on that lives in the heartbeat's on-disk counts, which are a
      pure function of the filesystem rather than of a runner's own claim.
    * Stage ORDER is validated; stage TIMING is not. Nothing here reads a
      clock, a timezone, or a scheduler. Whether the cadence is bound to
      anything is the loop subsystem's question, not this one's.
    * A ``command`` seam's command string is never parsed, classified, or run.
      It is opaque text to this module. The tier gate sees it when a host
      actually runs it, and not before.
    * YAML parsing is delegated. If the YAML library is absent this HALTs with
      the remedy; it never falls back to a partial hand-rolled parse.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "CadenceError",
    "Stage",
    "Cadence",
    "StageRun",
    "CadenceRun",
    "NullStage",
    "STAGE_IDS",
    "SEAM_KINDS",
    "ON_EMPTY",
    "TIER_CEILINGS",
    "VERDICTS",
    "REQUIRED_FIELDS",
    "OPTIONAL_FIELDS",
    "SCHEMA",
    "load_cadence",
    "parse_cadence",
    "run_stage",
    "run_cadence",
    "selftest",
    "main",
]

SCHEMA = "metabolism-cadence/v1"

#: The four stages, in order. Fixed by declaration, not by configuration.
STAGE_IDS: Tuple[str, ...] = ("absorb", "distill", "crystallize", "method")

#: How a stage's work is reached. ``null`` is the birth state.
SEAM_KINDS: Tuple[str, ...] = ("null", "command", "callable")

#: What a stage does when it produced nothing. ``ok`` is DELIBERATELY ABSENT --
#: there is no configuration in this framework that renders zero output green.
ON_EMPTY: Tuple[str, ...] = ("warn", "halt")

#: Same ceiling, and the same reason, as a loop charter: T3 and T4 reach
#: outside the machine and are human-gated, and a stage firing with nobody
#: present cannot hold a human gate open.
TIER_CEILINGS: Tuple[str, ...] = ("T0", "T1", "T2")

#: The run vocabulary. See the module docstring for why SKIPPED and WARN are
#: two words and not one.
VERDICTS: Tuple[str, ...] = ("OK", "WARN", "DEGRADED", "HALT", "SKIPPED")

#: Worst-first, for folding a run's stages into one posture.
_SEVERITY: Tuple[str, ...] = ("HALT", "DEGRADED", "WARN", "SKIPPED", "OK")

REQUIRED_FIELDS: Tuple[str, ...] = (
    "id", "purpose", "seam", "enabled", "produces", "on_empty", "tier_ceiling",
)
OPTIONAL_FIELDS: Tuple[str, ...] = (
    "notes", "tagout", "timeout_minutes", "command", "callable",
)


class CadenceError(RuntimeError):
    """A HALT with a remedy attached, in the shape genesis prints."""

    def __init__(self, reason: str, remedy: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.remedy = remedy

    def render(self) -> str:
        out = f"HALT: {self.reason}"
        if self.remedy:
            out += f"\n  remedy: {self.remedy}"
        return out


@dataclass(frozen=True)
class Stage:
    """One declared stage. Every field below was present in the file."""

    id: str
    purpose: str
    seam: str
    enabled: bool
    produces: Tuple[str, ...]
    on_empty: str
    tier_ceiling: str
    notes: str = ""
    tagout: Optional[str] = None
    timeout_minutes: Optional[int] = None
    command: Optional[str] = None
    callable: Optional[str] = None

    def to_row(self) -> Dict[str, Any]:
        return {
            "id": self.id, "seam": self.seam, "enabled": self.enabled,
            "produces": list(self.produces), "on_empty": self.on_empty,
            "tier_ceiling": self.tier_ceiling,
            "tagout": self.tagout,
        }


@dataclass(frozen=True)
class Cadence:
    """A whole cadence file, validated."""

    schema: str
    as_of: str
    stages: Tuple[Stage, ...]
    source: Optional[str] = None

    def stage(self, stage_id: str) -> Stage:
        for st in self.stages:
            if st.id == stage_id:
                return st
        raise KeyError(stage_id)

    @property
    def enabled_stages(self) -> Tuple[Stage, ...]:
        return tuple(s for s in self.stages if s.enabled)


@dataclass
class StageRun:
    """What one stage actually did, and how that reads."""

    stage_id: str
    seam: str
    ran: bool
    produced: Dict[str, int] = field(default_factory=dict)
    unreadable: List[str] = field(default_factory=list)
    verdict: str = "SKIPPED"
    notes: List[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(int(v) for v in self.produced.values())

    def to_row(self) -> Dict[str, Any]:
        return {
            "stage": self.stage_id, "seam": self.seam, "ran": self.ran,
            "produced": dict(self.produced), "total": self.total,
            "unreadable": list(self.unreadable), "verdict": self.verdict,
            "notes": list(self.notes),
        }


@dataclass
class CadenceRun:
    """A whole pass over the cadence."""

    stages: List[StageRun] = field(default_factory=list)
    posture: str = "SKIPPED"

    @property
    def produced(self) -> Dict[str, int]:
        totals: Dict[str, int] = {}
        for run in self.stages:
            for key, value in run.produced.items():
                totals[key] = totals.get(key, 0) + int(value)
        return totals

    def to_row(self) -> Dict[str, Any]:
        return {"posture": self.posture,
                "produced": self.produced,
                "stages": [s.to_row() for s in self.stages]}

    def render(self) -> str:
        lines = [f"metabolism cadence: {self.posture}"]
        for run in self.stages:
            counts = ", ".join(f"{k}={v}" for k, v in sorted(run.produced.items()))
            lines.append(f"  {run.stage_id:<12} {run.verdict:<9} "
                         f"seam={run.seam:<8} {counts or '-'}"
                         + (("  " + "; ".join(run.notes)) if run.notes else ""))
        return "\n".join(lines)


class NullStage:
    """The birth seam: records the INTENT of a stage, produces nothing.

    This is not a stub standing in for missing code. It is the honest state of
    a node nobody has told how to absorb anything yet, and it is RECORDED as a
    run rather than omitted -- because a stage that leaves the population is
    exactly how a metabolism comes to report green over zero.
    """

    def __init__(self) -> None:
        self.intents: List[Dict[str, Any]] = []

    def __call__(self, stage: Stage) -> Dict[str, int]:
        self.intents.append({
            "stage": stage.id,
            "purpose": stage.purpose,
            "would_produce": list(stage.produces),
            "seam": stage.seam,
        })
        # Zero for every declared countable -- named, never absent. A missing
        # key and a zero must not look the same to the heartbeat.
        return {name: 0 for name in stage.produces}


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------


def _require_yaml() -> Any:
    try:
        import yaml  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise CadenceError(
            f"the YAML library is unavailable ({exc}), so the cadence cannot "
            "be read",
            remedy="pip install pyyaml -- this loader never falls back to a "
                   "partial hand-rolled parse",
        ) from exc
    return yaml


def load_cadence(path: Path | str, *, birth: bool = False) -> Cadence:
    """Read and validate a cadence file.

    A MISSING FILE is a HALT, not an empty cadence: the loader cannot tell
    "this node has no metabolism" from "the packaging dropped the file", and
    only one of those is survivable.
    """
    path = Path(path)
    if not path.is_file():
        raise CadenceError(
            f"the cadence file is missing: {path}",
            remedy="copy config/metabolism-cadence.template.yaml into place; "
                   "an absent cadence is a field nobody read, never an empty "
                   "one",
        )
    yaml = _require_yaml()
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - the parser's own error class varies
        raise CadenceError(f"the cadence file does not parse: {path} ({exc})",
                           remedy="fix the YAML; a partial parse is refused") from exc
    return parse_cadence(doc, source=str(path), birth=birth)


def parse_cadence(doc: Any, *, source: Optional[str] = None,
                  birth: bool = False) -> Cadence:
    """Validate a loaded document. Every refusal below is load-bearing."""
    if not isinstance(doc, Mapping):
        raise CadenceError("the cadence file is not a mapping",
                           remedy=f"the document must carry `schema: {SCHEMA}`")
    schema = str(doc.get("schema") or "")
    if schema != SCHEMA:
        raise CadenceError(
            f"unknown cadence schema {schema!r}",
            remedy=f"this reader implements {SCHEMA} only; a schema it does "
                   "not know is refused rather than read optimistically")
    as_of = str(doc.get("as_of") or "")
    if not as_of:
        raise CadenceError(
            "the cadence declares no `as_of`",
            remedy="a belief carrier states when it was last true; add "
                   "`as_of: \"YYYY-MM-DD\"`")

    raw_stages = doc.get("stages")
    if not isinstance(raw_stages, Sequence) or isinstance(raw_stages, (str, bytes)):
        raise CadenceError(
            "`stages:` is missing or is not a list",
            remedy="all four stages are required, in order: "
                   + " -> ".join(STAGE_IDS))

    stages: List[Stage] = []
    seen: List[str] = []
    for index, raw in enumerate(raw_stages):
        stages.append(_parse_stage(raw, index, birth=birth))
        seen.append(stages[-1].id)

    if tuple(seen) != STAGE_IDS:
        raise CadenceError(
            f"the cadence declares stages {seen!r}",
            remedy="exactly the four stages, in order: "
                   + " -> ".join(STAGE_IDS)
                   + ". A missing stage cannot be told from a dropped one, so "
                     "neither is defaulted")

    return Cadence(schema=schema, as_of=as_of, stages=tuple(stages),
                   source=source)


def _parse_stage(raw: Any, index: int, *, birth: bool) -> Stage:
    where = f"stage #{index + 1}"
    if not isinstance(raw, Mapping):
        raise CadenceError(f"{where} is not a mapping",
                           remedy="each stage is a mapping of the required fields")

    keys = set(str(k) for k in raw.keys())
    known = set(REQUIRED_FIELDS) | set(OPTIONAL_FIELDS)
    undeclared = sorted(keys - known)
    if undeclared:
        raise CadenceError(
            f"{where} carries undeclared field(s) {undeclared}",
            remedy="an undeclared field is a HALT, never ignored: a typo "
                   "(`tier-ceiling` for `tier_ceiling`) would otherwise load "
                   "clean and silently lose the field it was meant to set")
    missing = [f for f in REQUIRED_FIELDS if f not in keys]
    if missing:
        raise CadenceError(
            f"{where} is missing required field(s) {missing}",
            remedy="there is no default for any of them; a reader that fills "
                   "in a blank has taken that field out of the population")

    stage_id = str(raw["id"])
    if stage_id not in STAGE_IDS:
        raise CadenceError(f"{where} declares unknown id {stage_id!r}",
                           remedy="one of " + ", ".join(STAGE_IDS))
    if stage_id != STAGE_IDS[index]:
        raise CadenceError(
            f"{where} is {stage_id!r}, expected {STAGE_IDS[index]!r}",
            remedy="the four stages are ordered by declaration: "
                   + " -> ".join(STAGE_IDS))

    purpose = str(raw["purpose"] or "").strip()
    if not purpose:
        raise CadenceError(f"{where} declares an empty `purpose`",
                           remedy="one sentence a stranger can act on")

    seam = str(raw["seam"])
    if seam not in SEAM_KINDS:
        raise CadenceError(f"{where} declares unknown seam {seam!r}",
                           remedy="one of " + ", ".join(SEAM_KINDS))

    enabled = raw["enabled"]
    if not isinstance(enabled, bool):
        raise CadenceError(
            f"{where} declares `enabled: {enabled!r}`, which is not a boolean",
            remedy="`true` or `false`; a string is refused because "
                   "`enabled: \"false\"` is truthy in most readers")
    if birth and enabled:
        raise CadenceError(
            f"{where} is enabled at birth",
            remedy="every stage is born disabled. Switching one on is a "
                   "reviewed, dated operator act, never a side effect of "
                   "adding a row")

    produces = raw["produces"]
    if (not isinstance(produces, Sequence) or isinstance(produces, (str, bytes))
            or not produces):
        raise CadenceError(
            f"{where} declares no `produces` countables",
            remedy="a non-empty list. A stage with no declared countable "
                   "cannot be told apart from a stage that did nothing")
    countables = tuple(str(p) for p in produces)
    if any(not c.strip() for c in countables):
        raise CadenceError(f"{where} declares a blank countable name",
                           remedy="every countable is a name something counts")

    on_empty = str(raw["on_empty"])
    if on_empty not in ON_EMPTY:
        raise CadenceError(
            f"{where} declares `on_empty: {on_empty!r}`",
            remedy="one of " + ", ".join(ON_EMPTY)
                   + ". `ok` is deliberately not in this vocabulary: zero "
                     "output is a state, not a success")

    ceiling = str(raw["tier_ceiling"])
    if ceiling not in TIER_CEILINGS:
        raise CadenceError(
            f"{where} declares `tier_ceiling: {ceiling!r}`",
            remedy="one of " + ", ".join(TIER_CEILINGS)
                   + ". T3 and T4 reach outside the machine and are "
                     "human-gated; a stage firing with nobody present cannot "
                     "hold a human gate open")

    timeout = raw.get("timeout_minutes")
    if timeout is not None:
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            raise CadenceError(
                f"{where} declares `timeout_minutes: {timeout!r}`",
                remedy="a positive integer, or omit the field")

    return Stage(
        id=stage_id, purpose=purpose, seam=seam, enabled=enabled,
        produces=countables, on_empty=on_empty, tier_ceiling=ceiling,
        notes=str(raw.get("notes") or ""),
        tagout=(str(raw["tagout"]) if raw.get("tagout") else None),
        timeout_minutes=timeout,
        command=(str(raw["command"]) if raw.get("command") else None),
        callable=(str(raw["callable"]) if raw.get("callable") else None),
    )


# ---------------------------------------------------------------------------
# running
# ---------------------------------------------------------------------------

#: A runner is called with the stage and returns a mapping of countable -> int.
#: It may raise; a raise is DEGRADED, never a quiet zero.
Runner = Callable[[Stage], Mapping[str, int]]


def run_stage(stage: Stage, runner: Optional[Runner] = None) -> StageRun:
    """Run one stage through its seam and grade the result.

    ``runner`` defaults to :class:`NullStage`. A ``command`` or ``callable``
    seam with no runner supplied is DEGRADED, not OK: this module refuses to
    spawn a process or import a dotted path, and a declared seam nobody wired
    is a finding rather than a silent no-op.
    """
    if not stage.enabled:
        return StageRun(stage.id, stage.seam, ran=False, verdict="SKIPPED",
                        produced={name: 0 for name in stage.produces},
                        notes=["disabled -- an intentional skip is allowed to "
                               "be quiet"])

    if runner is None:
        if stage.seam == "null":
            runner = NullStage()
        else:
            return StageRun(
                stage.id, stage.seam, ran=False, verdict="DEGRADED",
                produced={name: 0 for name in stage.produces},
                unreadable=[f"seam:{stage.seam}"],
                notes=[f"seam {stage.seam!r} is declared and no runner was "
                       "supplied; this module never spawns a process nor "
                       "imports a dotted path"])

    try:
        raw = runner(stage)
    except Exception as exc:  # noqa: BLE001 - a runner may raise anything
        return StageRun(stage.id, stage.seam, ran=True, verdict="DEGRADED",
                        produced={name: 0 for name in stage.produces},
                        unreadable=[f"{type(exc).__name__}: {exc}"],
                        notes=["the stage could not complete; DEGRADED "
                               "outranks a zero, because an unreadable input "
                               "makes every count look better"])

    produced: Dict[str, int] = {name: 0 for name in stage.produces}
    notes: List[str] = []
    undeclared = sorted(set(map(str, raw.keys())) - set(stage.produces))
    if undeclared:
        notes.append("runner reported undeclared countable(s) "
                     + ", ".join(undeclared) + " -- recorded, not counted")
    for name in stage.produces:
        if name not in raw:
            notes.append(f"runner reported no value for declared countable "
                         f"{name!r}; recorded as 0")
            continue
        try:
            produced[name] = int(raw[name])
        except (TypeError, ValueError):
            return StageRun(stage.id, stage.seam, ran=True, verdict="DEGRADED",
                            produced=produced,
                            unreadable=[f"{name}={raw[name]!r} is not an integer"],
                            notes=notes)

    total = sum(produced.values())
    if total > 0:
        return StageRun(stage.id, stage.seam, ran=True, produced=produced,
                        verdict="OK", notes=notes)

    verdict = "HALT" if stage.on_empty == "halt" else "WARN"
    notes.append("ran and produced nothing -- zero output is a state, not a "
                 "success")
    return StageRun(stage.id, stage.seam, ran=True, produced=produced,
                    verdict=verdict, notes=notes)


def run_cadence(cadence: Cadence,
                runners: Optional[Mapping[str, Runner]] = None) -> CadenceRun:
    """Run every stage in declared order and fold the verdicts into a posture.

    The fold is WORST-WINS: ``HALT`` outranks ``DEGRADED`` outranks ``WARN``
    outranks ``SKIPPED`` outranks ``OK``. A run that halts stops there, and the
    stages after it are recorded as SKIPPED with the reason, never omitted.
    """
    runners = dict(runners or {})
    run = CadenceRun()
    halted = False
    for stage in cadence.stages:
        if halted:
            run.stages.append(StageRun(
                stage.id, stage.seam, ran=False, verdict="SKIPPED",
                produced={name: 0 for name in stage.produces},
                notes=["not reached: an earlier stage halted"]))
            continue
        result = run_stage(stage, runners.get(stage.id))
        run.stages.append(result)
        if result.verdict == "HALT":
            halted = True

    verdicts = [s.verdict for s in run.stages]
    for candidate in _SEVERITY:
        if candidate in verdicts:
            run.posture = candidate
            break
    return run


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


def _birth_doc() -> Dict[str, Any]:
    return {
        "schema": SCHEMA,
        "as_of": "2026-09-06",
        "stages": [
            {"id": sid, "purpose": f"the {sid} stage", "seam": "null",
             "enabled": False, "produces": [sid + "ed"], "on_empty": "warn",
             "tier_ceiling": "T1"}
            for sid in STAGE_IDS
        ],
    }


def selftest() -> Tuple[bool, str]:
    """Prove every refusal and every verdict can actually fire.

    A detector that has never fired is indistinguishable from a broken one, so
    each branch below is constructed rather than asserted about.
    """
    fired: List[str] = []
    failures: List[str] = []

    def expect(name: str, ok: bool) -> None:
        fired.append(name)
        if not ok:
            failures.append(name)

    def halts(name: str, doc: Any, *, birth: bool = False) -> None:
        try:
            parse_cadence(doc, birth=birth)
        except CadenceError:
            expect(name, True)
        else:
            expect(name, False)

    good = _birth_doc()
    try:
        cadence = parse_cadence(good, birth=True)
        expect("birth-cadence-loads", len(cadence.stages) == 4)
        expect("every-stage-disabled-at-birth", not cadence.enabled_stages)
    except CadenceError:
        expect("birth-cadence-loads", False)
        expect("every-stage-disabled-at-birth", False)

    import copy

    doc = copy.deepcopy(good)
    doc["stages"][0]["enabled"] = True
    halts("enabled-at-birth-halts", doc, birth=True)

    doc = copy.deepcopy(good)
    doc["stages"][0]["tier_ceiling"] = "T3"
    halts("t3-ceiling-halts", doc)

    doc = copy.deepcopy(good)
    doc["stages"][0]["on_empty"] = "ok"
    halts("on-empty-ok-halts", doc)

    doc = copy.deepcopy(good)
    doc["stages"][0]["produces"] = []
    halts("no-countable-halts", doc)

    doc = copy.deepcopy(good)
    del doc["stages"][2]
    halts("missing-stage-halts", doc)

    doc = copy.deepcopy(good)
    doc["stages"][0], doc["stages"][1] = doc["stages"][1], doc["stages"][0]
    halts("reordered-stages-halt", doc)

    doc = copy.deepcopy(good)
    doc["stages"][0]["tier-ceiling"] = "T1"
    halts("undeclared-field-halts", doc)

    doc = copy.deepcopy(good)
    del doc["stages"][0]["on_empty"]
    halts("missing-field-halts", doc)

    doc = copy.deepcopy(good)
    doc["schema"] = "metabolism-cadence/v99"
    halts("unknown-schema-halts", doc)

    doc = copy.deepcopy(good)
    doc["stages"][0]["seam"] = "shell"
    halts("undeclared-seam-halts", doc)

    # verdicts
    enabled = copy.deepcopy(good)
    for st in enabled["stages"]:
        st["enabled"] = True
    live = parse_cadence(enabled)

    run = run_cadence(live)
    expect("null-seam-runs-and-warns",
           all(s.verdict == "WARN" for s in run.stages))
    expect("zero-output-never-ok", run.posture == "WARN")
    expect("no-ok-verdict-on-zero", "OK" not in {s.verdict for s in run.stages}
           )

    run = run_cadence(live, {"absorb": lambda st: {"absorbed": 3}})
    expect("nonzero-output-is-ok", run.stages[0].verdict == "OK")

    def boom(_st: Stage) -> Mapping[str, int]:
        raise OSError("the corpus directory is unreadable")

    run = run_cadence(live, {"absorb": boom})
    expect("unreadable-input-is-degraded", run.stages[0].verdict == "DEGRADED")
    expect("degraded-outranks-warn", run.posture == "DEGRADED")

    halting = copy.deepcopy(enabled)
    halting["stages"][0]["on_empty"] = "halt"
    run = run_cadence(parse_cadence(halting))
    expect("on-empty-halt-fires", run.stages[0].verdict == "HALT")
    expect("stages-after-halt-recorded-not-omitted", len(run.stages) == 4)

    birth_run = run_cadence(parse_cadence(good, birth=True))
    expect("disabled-cadence-is-skipped",
           all(s.verdict == "SKIPPED" for s in birth_run.stages))

    # a declared non-null seam with no runner is a finding, not a no-op
    seamed = copy.deepcopy(enabled)
    seamed["stages"][0]["seam"] = "command"
    seamed["stages"][0]["command"] = "python -m nothing"
    run = run_cadence(parse_cadence(seamed))
    expect("unwired-seam-is-degraded", run.stages[0].verdict == "DEGRADED")

    # the NullStage records intent rather than producing nothing invisibly
    null = NullStage()
    run_cadence(live, {sid: null for sid in STAGE_IDS})
    expect("nullstage-records-intent", len(null.intents) == 4)

    report = (f"cadence selftest: {len(fired)} paths fired, "
              f"{len(failures)} failed"
              + (" -- " + ", ".join(failures) if failures else ""))
    return (not failures, report)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="the metabolism cadence: load, validate, dry-run")
    parser.add_argument("--cadence", default=None,
                        help="path to a cadence file")
    parser.add_argument("--birth", action="store_true",
                        help="load under birth rules (every stage must be "
                             "disabled)")
    parser.add_argument("--run", action="store_true",
                        help="run the cadence through its declared seams; with "
                             "no runners wired that is the NullStage")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        ok, report = selftest()
        print(report)
        return 0 if ok else 1

    if not args.cadence:
        parser.error("--cadence is required (or use --selftest)")
    try:
        cadence = load_cadence(args.cadence, birth=args.birth)
    except CadenceError as exc:
        print(exc.render(), file=sys.stderr)
        return 1

    if not args.run:
        rows = [s.to_row() for s in cadence.stages]
        print(json.dumps({"schema": cadence.schema, "as_of": cadence.as_of,
                          "stages": rows}, indent=2) if args.json
              else "\n".join(f"{r['id']:<12} enabled={str(r['enabled']):<5} "
                             f"seam={r['seam']:<8} ceiling={r['tier_ceiling']}"
                             for r in rows))
        return 0

    run = run_cadence(cadence)
    print(json.dumps(run.to_row(), indent=2) if args.json else run.render())
    return 0 if run.posture in ("OK", "SKIPPED") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
