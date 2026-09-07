"""The metabolism: how a node turns what it read into what it knows.

Five modules, one cycle:

* :mod:`~intentops_core.metabolism.cadence` -- the four stages
  (``absorb -> distill -> crystallize -> method``), each a declared seam with a
  ``NullStage`` at birth. A stage that ran and produced nothing renders WARN;
  there is no configuration that makes zero output green.
* :mod:`~intentops_core.metabolism.heartbeat` -- three alarms over the dated
  series: promotion flat-line, input feed dry, registry drift. The cadence
  grades one run; only the series can see the defect this exists for.
* :mod:`~intentops_core.metabolism.crystallize` -- the document contract for
  a crystallized pattern: id, pattern, graded evidence, station, and a
  ``reopens_when`` that is required rather than nullable.
* :mod:`~intentops_core.metabolism.grok` -- one dry-run cycle recorder
  (``curate -> pollinate -> percolate -> heal``), and ``forge`` as the same
  cycle at scale. A plan is never a finding.
* :mod:`~intentops_core.metabolism.assimilation` -- the four verbs for
  learning from something outside, and the hard type boundary that refuses an
  executable artifact at every one of them.

WRITE MODEL
    The heartbeat owns the one store here
    (``.intentops/metabolism/heartbeat.jsonl``, append-only JSONL under
    ``StoreLock``). The cadence file
    (``.intentops/metabolism/cadence.yaml``) is a single-writer reviewed
    config. Every other module in this package is pure: it reads its input,
    returns a value, and writes nothing.

BLIND SPOTS
    No module here calls a model, opens a socket, spawns a process, or imports
    a dotted path supplied by data. Every point where a reasoning engine would
    go is a DECLARED SEAM with a null implementation that records intent. That
    is a deliberate limit and not an unfinished one: a metabolism that could
    quietly invent its own findings would manufacture exactly the ungrounded
    output the rest of the framework exists to detect.
"""

from __future__ import annotations

from .assimilation import (
    ARTIFACT_KINDS,
    AssimilationError,
    Decision,
    Request,
    Verb,
    evaluate,
)
from .cadence import (
    Cadence,
    CadenceError,
    CadenceRun,
    NullStage,
    ON_EMPTY,
    SEAM_KINDS,
    STAGE_IDS,
    Stage,
    StageRun,
    load_cadence,
    parse_cadence,
    run_cadence,
    run_stage,
)
from .crystallize import (
    CRYST_ID_RE,
    CrystDoc,
    CrystError,
    EVIDENCE_GRADES,
    STATIONS,
    render_template,
    validate,
    validate_file,
)
from .grok import (
    PHASES,
    ForgePlan,
    GrokCycle,
    GrokError,
    PhaseRecord,
    plan_forge,
    record_cycle,
)
from .runner import (
    BIRTH_ENVELOPE,
    CALLABLES,
    Envelope,
    Plan,
    PromptTemplate,
    RunRecord,
    RunnerError,
    append_run,
    load_prompts,
    load_runs,
    plan_stage,
    run_metabolism_stage,
)
from .heartbeat import (
    Alarm,
    COUNTABLES,
    Counts,
    Reading,
    append_reading,
    check_alarms,
    collect_counts,
    load_history,
    posture,
)

__all__ = [
    # cadence
    "Cadence", "CadenceError", "CadenceRun", "NullStage", "ON_EMPTY",
    "SEAM_KINDS", "STAGE_IDS", "Stage", "StageRun", "load_cadence",
    "parse_cadence", "run_cadence", "run_stage",
    # heartbeat
    "Alarm", "COUNTABLES", "Counts", "Reading", "append_reading",
    "check_alarms", "collect_counts", "load_history", "posture",
    # crystallize
    "CRYST_ID_RE", "CrystDoc", "CrystError", "EVIDENCE_GRADES", "STATIONS",
    "render_template", "validate", "validate_file",
    # grok
    "PHASES", "ForgePlan", "GrokCycle", "GrokError", "PhaseRecord",
    "plan_forge", "record_cycle",
    # assimilation
    "ARTIFACT_KINDS", "AssimilationError", "Decision", "Request", "Verb",
    "evaluate",
    # runner -- the other half of the seam cadence.py declares
    "BIRTH_ENVELOPE", "CALLABLES", "Envelope", "Plan", "PromptTemplate",
    "RunRecord", "RunnerError", "append_run", "load_prompts", "load_runs",
    "plan_stage", "run_metabolism_stage",
]
