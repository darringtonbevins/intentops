"""The values council -- steward of the core on every clone.

PURPOSE
    Answer one question, deterministically, before a change to this node's own
    governing machinery lands: *should this be done at all, given who could be
    affected?*

    It answers it in three readings, always in this order, because they are not
    peers:

      1. ORDERING CONSULT -- an ordering BINDS where a vote merely DELIBERATES.
         A governing ruling answers before anybody votes. Only rulings that
         carry a compiled predicate serve; a prose-only ruling is surfaced and
         never auto-applied, because deciding whether a case falls inside a
         prose condition IS the judgement.
      2. MORAL COMPASS READING -- four lenses, one arithmetic tally. Rogers'
         conscience hold surfaces whatever the mode, because a person being
         harmed is not an operator's setting to switch off.
      3. POSTURE COUNCIL TALLY -- five deterministic evaluators, ">= 3 of 5"
         with two vetoes.

    Four properties that must stay true forever:

      * BELOW THE OPERATOR, NEVER ABOVE. A REFRAIN is a surfaced HOLD, not a
        block. The operator may proceed over it, and the override records their
        reason, which is REQUIRED -- a blank override is refused the way a blank
        reaffirmation is. A refusal that over-blocks gets routed around, and a
        routed-around refusal protects nothing.
      * IT MAY RAISE THE BAR, NEVER LOWER A GATE. Nothing in this module can
        turn a refusal into a permission. An ordering whose ``on_match`` is
        ``permit`` is recorded and does not lower anything. A conscience that
        can PERMIT is a rubber stamp with better vocabulary.
      * DETERMINISTIC. Every reading is arithmetic over declared data. Two nodes
        given the same change reach the same verdict. No model is called on this
        path, ever.
      * SILENCE IS NOT CONSENT. A council that could not convene returns HALT,
        not a pass -- the same posture as "a silent reviewer BLOCKS, it never
        passes".

    THE DECLARED SURFACE. What counts as a core-mechanic change comes from
    ``config/core-surface.yaml``, never from a heuristic and never from a tuple
    in this file. A missing config, an entry with no ``glob``, or an entry with
    no ``reason`` is a HALT: a surface this module half-understands produces a
    steward that runs and is wrong.

WRITE MODEL
    Append-only JSONL journal at ``<node_root>/.intentops/core-review/ledger.jsonl``,
    every append serialized by ``StoreLock`` on the sibling lock file, state
    computed as a PURE FOLD over the rows (:func:`fold`). Nothing rewrites a
    row; an override is a new row, a consumed override is another new row, and
    a superseded verdict is a later verdict, never an edit. Malformed rows are
    counted and surfaced (``LedgerState.malformed``), never silently skipped --
    a dropped row in the only record of a node's core changes is the exact
    defect this module exists to prevent.

BLIND SPOTS
    * STEP 0 READS THE COMMAND SKELETON, NEVER FILE CONTENT. A shell command
      whose target path lives inside a quoted segment -- a ``python -c`` that
      opens a core path, a heredoc body -- is INVISIBLE here. That hole is why
      the design pairs this gate with two out-of-band detectors (a pre-commit
      refusal of a core diff with no matching ledger row, and a re-hash of the
      signed imprint bundle at every session start). Neither is in this module,
      and this gate alone is therefore not a complete steward.
    * THE COMPASS READS A SUMMARY OF THE WRITE, NOT ITS PROSE. A core write
      whose human cost is visible only in its text is not seen at write time.
      Reading raw file content into keyword lenses is how a gate starts holding
      its own documentation; conduct is read by :func:`reading_for_conduct`
      instead, where the subject is an action and a person can actually be named.
    * THE WEAKENING SIGNATURES ARE A FLOOR, NOT A PROOF. Deterministic patterns
      catch the obvious dangerous edits (fail-closed switched off, a council
      threshold lowered, a gate bypassed). They cannot catch a semantic
      regression, and a CONSENT verdict is therefore "nothing known-dangerous
      was found", never "this change is safe".
    * PATH MATCHING IS CASE-INSENSITIVE and normalizes separators. On a
      case-sensitive filesystem this over-matches rather than under-matches,
      which is the safe direction and is a deliberate choice, not an accident.
    * A LEDGER THAT IS 100% CONSENT IS A DETECTOR THAT HAS NEVER FIRED. The
      posture in :func:`fold` reports the verdict mix precisely so that reading
      is available rather than assumed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from intentops_core.councils.guardians import (
    CouncilVerdict,
    MoralCompassCouncil,
    Stance,
)
from intentops_core.councils.posture import (
    APPROVED,
    ActuationItem,
    CouncilVote,
    posture_council_vote,
)
from intentops_core.councils.proposal import ProposedAction
from intentops_core.gate.classify import (
    FILE_WRITE_TOOLS,
    SHELL_COMMAND_TOOLS,
    RealityIndicators,
    classify_reaches_reality,
    command_skeleton,
)
from intentops_core.store_guard import StoreLock, lock_for
from intentops_core.wisdom.ordering import Ordering, OrderingState, OrderingStore

__all__ = [
    "CONDUCT_KIND",
    "CoreReviewLedger",
    "CoreSurface",
    "CouncilError",
    "FACT_KEYS",
    "LEDGER_RELPATH",
    "LedgerState",
    "MODES",
    "REVIEW_KIND",
    "SurfaceEntry",
    "ValuesReading",
    "Verdict",
    "core_surface_path",
    "fold",
    "gate_values_council",
    "load_core_surface",
    "mode_from_env",
    "node_root_from_env",
    "reading_for_conduct",
    "record_override",
    "review_core_write",
    "selftest",
]

# --------------------------------------------------------------------------
# vocabulary -- closed, and an undeclared value is a hard exit
# --------------------------------------------------------------------------

#: The three modes, in ascending strictness. ``observe`` is the birth default:
#: ``shadow`` surfaces nothing, which regresses below the reference posture and
#: makes its own promotion criterion unmeasurable (a false-positive rate cannot
#: be measured over verdicts shown to nobody who could grade them), so it is
#: kept strictly as an operator-selected diagnostic.
MODES: Tuple[str, ...] = ("shadow", "observe", "enforce")
DEFAULT_MODE = "observe"

MODE_ENV = "INTENTOPS_VALUES_COUNCIL"
ENFORCE_ENV = "INTENTOPS_VALUES_COUNCIL_ENFORCE"
NODE_ROOT_ENV = "INTENTOPS_NODE_ROOT"
SURFACE_ENV = "INTENTOPS_CORE_SURFACE"

LEDGER_RELPATH = Path(".intentops") / "core-review" / "ledger.jsonl"

REVIEW_KIND = "values_council"
CONDUCT_KIND = "values_council_conduct"
OVERRIDE_KIND = "override"
OVERRIDE_CONSUMED_KIND = "override_consumed"

SURFACE_SCHEMA = "core-surface/v1"

#: The fact vocabulary the ordering consult is asked with. Closed on purpose:
#: a predicate can only fire on a fact somebody actually observed, so a key
#: that is not in this tuple is a key no ruling should be compiled against.
FACT_KEYS: Tuple[str, ...] = (
    "surface", "tool", "tier", "reaches_reality", "core_glob",
)


class CouncilError(RuntimeError):
    """The council could not convene, or could not read its own surface.

    Raised rather than returned because every caller must decide what to do
    with a HALT; returning a verdict object for this case invites treating it
    as one more reading among several, which is exactly how silence becomes
    consent.
    """


class Verdict(str, Enum):
    """What the council concluded. Ordered by severity; HALT outranks all."""

    NOT_APPLICABLE = "n/a"
    CONSENT = "consent"
    HOLD = "hold"
    ESCALATE = "escalate"
    HALT = "halt"

    @property
    def severity(self) -> int:
        return _SEVERITY[self]

    @property
    def is_refusal(self) -> bool:
        """True when the change does not proceed on this verdict alone."""
        return self in (Verdict.HOLD, Verdict.ESCALATE, Verdict.HALT)


_SEVERITY: Dict[Verdict, int] = {
    Verdict.NOT_APPLICABLE: 0,
    Verdict.CONSENT: 1,
    Verdict.HOLD: 2,
    Verdict.ESCALATE: 3,
    Verdict.HALT: 4,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _stricter(a: Verdict, b: Verdict) -> Verdict:
    return a if a.severity >= b.severity else b


# --------------------------------------------------------------------------
# the declared surface
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SurfaceEntry:
    """One declared core-mechanic glob and the reason it is core.

    ``reason`` is required. A glob with no stated reason is a rule nobody can
    argue with, which is the failure mode the declared surface exists to retire.
    """

    glob: str
    reason: str
    pattern: "re.Pattern[str]"
    literal_prefix: str


@dataclass(frozen=True)
class CoreSurface:
    """The declared surface, loaded once and matched many times."""

    path: Path
    as_of: str
    entries: Tuple[SurfaceEntry, ...]

    def match(self, candidate: str) -> Optional[SurfaceEntry]:
        """The first declared entry this path falls under, or None.

        Two matchers, deliberately:

          * the glob itself, against the normalized repo-relative path;
          * the glob's LITERAL PREFIX as a substring, when that prefix is at
            least two segments deep. This is the mechanization of the surface
            file's own rule that a path the reader does not recognise -- a
            relative escape, an odd depth, a token lifted out of a shell
            command -- is still core. It cannot widen a one-segment prefix
            (``packages``) into the whole tree, because such prefixes are
            skipped at load time.
        """
        norm = normalize_path(candidate)
        if not norm:
            return None
        for entry in self.entries:
            if entry.pattern.match(norm):
                return entry
        for entry in self.entries:
            if entry.literal_prefix and entry.literal_prefix in norm:
                return entry
        return None


def normalize_path(candidate: str) -> str:
    """Lower-case, forward-slashed, leading ``./`` and drive letters removed.

    Normalization is not cosmetic here: an unnormalized comparison is how a
    write reaches a declared surface by a path the matcher does not recognise.
    """
    text = str(candidate or "").strip().strip('"').strip("'")
    if not text:
        return ""
    text = text.replace("\\", "/").lower()
    if len(text) > 2 and text[1] == ":":
        text = text[2:]
    while text.startswith("./"):
        text = text[2:]
    return text.lstrip("/")


def _glob_to_regex(pattern: str) -> "re.Pattern[str]":
    """Translate a path glob. ``**`` spans segments; ``*`` never crosses ``/``."""
    parts = pattern.replace("\\", "/").lower().split("/")
    out: List[str] = []
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        if part == "**":
            out.append(".*" if last else "(?:.*/)?")
            continue
        segment = ""
        for char in part:
            if char == "*":
                segment += "[^/]*"
            elif char == "?":
                segment += "[^/]"
            else:
                segment += re.escape(char)
        out.append(segment if last else segment + "/")
    return re.compile("^" + "".join(out) + "$")


def _literal_prefix(pattern: str) -> str:
    """Segments before the first wildcard, when at least two deep.

    A single-segment prefix is discarded: treating everything under one
    top-level directory as core would make the declared surface meaningless,
    and a surface that covers everything covers nothing.
    """
    parts = pattern.replace("\\", "/").lower().split("/")
    literal: List[str] = []
    for part in parts:
        if any(ch in part for ch in "*?["):
            break
        literal.append(part)
    if len(literal) < 2:
        return ""
    return "/".join(literal)


def core_surface_path(repo_root: Optional[Path] = None) -> Path:
    """Where the declared surface lives. Env first, then the repo layout."""
    from_env = os.environ.get(SURFACE_ENV, "").strip()
    if from_env:
        return Path(from_env)
    if repo_root is not None:
        return Path(repo_root) / "config" / "core-surface.yaml"
    # packages/intentops-core/intentops_core/governance/values_council.py
    return Path(__file__).resolve().parents[4] / "config" / "core-surface.yaml"


def load_core_surface(path: Optional[Path] = None) -> CoreSurface:
    """Load and validate the declared surface, or HALT saying exactly why."""
    target = Path(path) if path is not None else core_surface_path()
    if not target.is_file():
        raise CouncilError(
            f"the declared core surface is missing at {target}. The values "
            "council will not guess which paths are core-mechanic: an "
            f"undeclared surface is a hard exit. Remedy: restore the file, or "
            f"set {SURFACE_ENV} to its location."
        )
    try:
        import yaml  # imported lazily: a missing parser must name its remedy
    except ImportError as exc:  # pragma: no cover - environment defect
        raise CouncilError(
            "PyYAML is required to read the declared core surface and is not "
            f"installed ({exc}). The council will not hand-parse a governance "
            "file it half-understands. Remedy: pip install pyyaml"
        ) from exc
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CouncilError(f"the declared core surface at {target} is unreadable: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise CouncilError(
            f"{target} does not parse to a mapping; it cannot declare a surface."
        )
    schema = str(raw.get("schema") or "")
    if schema != SURFACE_SCHEMA:
        raise CouncilError(
            f"{target} declares schema {schema!r}; this reader understands only "
            f"{SURFACE_SCHEMA!r}. A schema it half-understands produces a "
            "steward that runs and is wrong."
        )
    as_of = str(raw.get("as_of") or "").strip()
    if not as_of:
        raise CouncilError(
            f"{target} carries no as_of. A belief carrier with no as-of has no "
            "half-life and cannot be re-asked on time."
        )
    rows = raw.get("surface")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)) or not rows:
        raise CouncilError(
            f"{target} declares no surface entries. An empty core surface is "
            "not a node with no core; it is a reader that would consent to "
            "every core change in silence."
        )
    entries: List[SurfaceEntry] = []
    seen: set = set()
    for index, row in enumerate(rows):
        where = f"{target} surface[{index}]"
        if not isinstance(row, Mapping):
            raise CouncilError(f"{where} is not a mapping.")
        glob = str(row.get("glob") or "").strip()
        reason = str(row.get("reason") or "").strip()
        if not glob:
            raise CouncilError(f"{where} has no glob.")
        if not reason:
            raise CouncilError(
                f"{where} ({glob}) has no reason. A glob with no stated reason "
                "is a rule nobody can argue with."
            )
        if glob.lower() in seen:
            raise CouncilError(f"{where} repeats the glob {glob!r}.")
        seen.add(glob.lower())
        entries.append(
            SurfaceEntry(
                glob=glob,
                reason=reason,
                pattern=_glob_to_regex(glob),
                literal_prefix=_literal_prefix(glob),
            )
        )
    return CoreSurface(path=target, as_of=as_of, entries=tuple(entries))


# --------------------------------------------------------------------------
# mode + node root
# --------------------------------------------------------------------------


def mode_from_env(env: Optional[Mapping[str, str]] = None) -> str:
    """Resolve the mode. An undeclared value HALTs; it never falls through.

    ``INTENTOPS_VALUES_COUNCIL`` names the mode outright.
    ``INTENTOPS_VALUES_COUNCIL_ENFORCE=1`` is the shorthand that promotes to
    ``enforce``; it can only raise the mode, never lower one that was named.
    """
    source = os.environ if env is None else env
    named = str(source.get(MODE_ENV, "") or "").strip().lower()
    if named and named not in MODES:
        raise CouncilError(
            f"{MODE_ENV}={named!r} is not one of {MODES}. The council will not "
            "default a mode it does not recognise: a governance control set to "
            "an unreadable value must be loud, not quietly permissive."
        )
    mode = named or DEFAULT_MODE
    shorthand = str(source.get(ENFORCE_ENV, "") or "").strip().lower()
    if shorthand in ("1", "true", "yes", "on"):
        mode = "enforce"
    elif shorthand and shorthand not in ("0", "false", "no", "off", ""):
        raise CouncilError(
            f"{ENFORCE_ENV}={shorthand!r} is neither true nor false. An "
            "unreadable switch on a gate is tagged out, never guessed."
        )
    return mode


def node_root_from_env(env: Optional[Mapping[str, str]] = None) -> Path:
    """The node root, or HALT. There is no default node root, ever."""
    source = os.environ if env is None else env
    raw = str(source.get(NODE_ROOT_ENV, "") or "").strip()
    if not raw:
        raise CouncilError(
            f"{NODE_ROOT_ENV} is not set. The values council will not guess a "
            "node root: a review written to the wrong node is a review nobody "
            "will read, and a council that cannot record cannot convene."
        )
    return Path(raw)


# --------------------------------------------------------------------------
# the ledger -- append-only, StoreLock, pure fold
# --------------------------------------------------------------------------


@dataclass
class LedgerState:
    """The fold of the review journal. Read-only.

    ``outstanding_overrides`` counts, per normalized path, override rows minus
    consumed rows. It is a count and not a flag because two overrides recorded
    for the same path are two permissions, and collapsing them would silently
    discard one.
    """

    rows: int = 0
    malformed: int = 0
    verdicts: Dict[str, int] = field(default_factory=dict)
    outstanding_overrides: Dict[str, int] = field(default_factory=dict)
    conduct_readings: int = 0
    conscience_holds: int = 0

    def outstanding(self, path: str) -> int:
        return self.outstanding_overrides.get(normalize_path(path), 0)

    def posture(self) -> Tuple[str, List[str]]:
        """A reading over the ledger, never a verdict on it."""
        notes: List[str] = []
        reviews = sum(v for k, v in self.verdicts.items() if k != Verdict.NOT_APPLICABLE.value)
        notes.append(f"rows {self.rows}; core reviews {reviews}; conduct {self.conduct_readings}")
        if self.malformed:
            notes.append(
                f"malformed rows {self.malformed} -- the journal is the only record of "
                "this node's core changes; an unreadable row is missing history"
            )
            return "DEGRADED", notes
        consents = self.verdicts.get(Verdict.CONSENT.value, 0)
        if reviews >= 10 and consents == reviews:
            notes.append(
                f"every one of {reviews} reviews consented -- a detector that has "
                "never fired is indistinguishable from a broken one; run --selftest"
            )
            return "ATTENTION", notes
        notes.append(
            "verdicts " + ", ".join(f"{k}={v}" for k, v in sorted(self.verdicts.items()))
            if self.verdicts else "verdicts none recorded yet"
        )
        return "READING", notes


def fold(rows: Iterable[Mapping[str, Any]], *, malformed: int = 0) -> LedgerState:
    """Pure fold over journal rows. Same rows in, same state out."""
    state = LedgerState(malformed=malformed)
    for row in rows:
        state.rows += 1
        kind = str(row.get("kind") or "")
        if kind in (REVIEW_KIND, CONDUCT_KIND):
            verdict = str(row.get("verdict") or "")
            state.verdicts[verdict] = state.verdicts.get(verdict, 0) + 1
            if kind == CONDUCT_KIND:
                state.conduct_readings += 1
            if row.get("conscience_hold"):
                state.conscience_holds += 1
        elif kind == OVERRIDE_KIND:
            key = normalize_path(str(row.get("path") or ""))
            state.outstanding_overrides[key] = state.outstanding_overrides.get(key, 0) + 1
        elif kind == OVERRIDE_CONSUMED_KIND:
            key = normalize_path(str(row.get("path") or ""))
            state.outstanding_overrides[key] = state.outstanding_overrides.get(key, 0) - 1
    state.outstanding_overrides = {
        k: v for k, v in state.outstanding_overrides.items() if v > 0
    }
    return state


class CoreReviewLedger:
    """Append-only journal of council readings and operator overrides.

    WRITE MODEL: append-only JSONL, one row per event, every append taken under
    ``StoreLock`` on the sibling lock file; state is a pure fold and is never
    written back. Nothing in this class rewrites, truncates, or deletes a row.
    """

    def __init__(self, node_root: Path | str) -> None:
        if not node_root:
            raise CouncilError(
                "CoreReviewLedger requires an explicit node root -- it will not "
                "guess where a node's core reviews live"
            )
        self.node_root = Path(node_root)
        self.path = self.node_root / LEDGER_RELPATH

    def ensure(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise CouncilError(
                f"the core-review ledger directory cannot be created at "
                f"{self.path.parent}: {exc}. A council that cannot record "
                "cannot convene."
            ) from exc

    def append(self, row: Mapping[str, Any]) -> Dict[str, Any]:
        """Append one row. Raises rather than dropping it."""
        self.ensure()
        payload = dict(row)
        payload.setdefault("at", _now())
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
        try:
            with StoreLock(lock_for(self.path)):
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line)
        except OSError as exc:
            raise CouncilError(
                f"the core-review ledger at {self.path} could not be written: {exc}. "
                "An unrecorded core review is an unreviewed core change."
            ) from exc
        return payload

    def read(self) -> Tuple[List[Dict[str, Any]], int]:
        """Return ``(rows, malformed_count)``. A bad line is counted, not dropped."""
        if not self.path.exists():
            return [], 0
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CouncilError(
                f"the core-review ledger at {self.path} is unreadable: {exc}. "
                "Without it the council cannot tell whether an override exists, "
                "and guessing is not available to it."
            ) from exc
        rows: List[Dict[str, Any]] = []
        malformed = 0
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if isinstance(parsed, dict):
                rows.append(parsed)
            else:
                malformed += 1
        return rows, malformed

    def state(self) -> LedgerState:
        rows, malformed = self.read()
        return fold(rows, malformed=malformed)


def record_override(
    node_root: Path | str,
    path: str,
    reason: str,
    by: str = "operator",
) -> Dict[str, Any]:
    """Record an operator override for ONE named write.

    Refuses a blank reason. The council sits below the operator, and the price
    of that is a stated reason: an override with no reason records that the
    council was overruled and destroys the only evidence of why, which makes
    the ledger unable to answer the one question a promotion decision needs.
    """
    target = str(path or "").strip()
    if not target:
        raise CouncilError("an override must name the write it unblocks")
    text = str(reason or "").strip()
    if not text:
        raise CouncilError(
            "an override requires a reason. A blank override is refused the way "
            "a blank reaffirmation is: it records that the council was overruled "
            "and discards the only thing that made the overrule reviewable."
        )
    ledger = CoreReviewLedger(node_root)
    return ledger.append(
        {
            "kind": OVERRIDE_KIND,
            "verdict": "OVERRIDDEN",
            "path": target,
            "normalized_path": normalize_path(target),
            "reason": text,
            "by": str(by or "operator"),
        }
    )


# --------------------------------------------------------------------------
# step 0 -- what does this call target?
# --------------------------------------------------------------------------

#: A shell token is treated as a candidate path when it carries a separator or
#: a known artifact extension. Tokens inside quoted segments are already blanked
#: by ``command_skeleton`` before this runs -- see the module blind spots.
_PATH_TOKEN = re.compile(r"[A-Za-z0-9_.~/\\*?\[\]-]{3,}")
_PATHISH_EXT = (
    ".py", ".yaml", ".yml", ".json", ".md", ".toml", ".ini", ".cfg", ".sig",
)


def target_paths(tool: str, tool_input: Mapping[str, Any]) -> List[str]:
    """Every path this tool call plausibly writes to.

    File-write tools state their target outright. Shell tools are read through
    the command SKELETON only -- never file content -- which is both the
    privacy property and the blind spot documented at the top of this module.
    """
    if not isinstance(tool_input, Mapping):
        return []
    if tool in FILE_WRITE_TOOLS:
        target = str(
            tool_input.get("file_path")
            or tool_input.get("notebook_path")
            or tool_input.get("path")
            or ""
        )
        return [target] if target else []
    if tool in SHELL_COMMAND_TOOLS:
        command = str(tool_input.get("command") or "")
        if not command:
            return []
        skeleton = command_skeleton(command)
        out: List[str] = []
        for match in _PATH_TOKEN.finditer(skeleton):
            token = match.group(0)
            if "/" in token or "\\" in token or token.lower().endswith(_PATHISH_EXT):
                if token not in out:
                    out.append(token)
        return out
    return []


# --------------------------------------------------------------------------
# the content reading -- guardrail-weakening signatures
# --------------------------------------------------------------------------

#: Signatures whose presence in a PROPOSED core change would loosen this node's
#: own protection. The third field marks a signature that recurs in DESCRIPTIVE
#: prose, and those are scanned on the prose-stripped body so a rule file that
#: DESCRIBES a fail-open posture does not escalate while an active weakening
#: still does. Intent-shaped signatures keep scanning the full body: a comment
#: expressing an intent to bypass the gate is itself a finding.
_WEAKENING_SIGNATURES: Tuple[Tuple[str, str, bool], ...] = (
    (r"fail[_-]?closed\s*[:=]\s*false", "fail-closed disabled", False),
    (r"\bfail[_-]?open\b", "fail-open introduced", True),
    (r"sovereignty\s*[:=]\s*(none|false|removed|null)", "human sovereignty removed", False),
    (r"council_threshold\s*[:=]\s*[01]\b", "council threshold lowered below 2", False),
    (r"human_escalation_tiers\s*[:=]\s*\[\s*\]", "human escalation tiers emptied", False),
    (r"forbidden\s*[:=]\s*\[\s*\]", "forbidden vocabulary emptied", False),
    (r"(bypass|disable|skip)[_-]?(guardian|gate|review|council)", "guard bypass/disable", False),
    (r"surface\s*[:=]\s*\[\s*\]", "declared core surface emptied", False),
    (r"reaches[_-]?reality\s*[:=]\s*false", "reaches-reality classification switched off", False),
    (
        rf"{ENFORCE_ENV}[^\n]{{0,16}}?\b(?:0|false|no|off|none|null)\b",
        "values-council enforce switch disabled",
        True,
    ),
)

_TRIPLE_QUOTED = re.compile(r'""".*?"""|\'\'\'.*?\'\'\'', re.DOTALL)
_LINE_COMMENT = re.compile(r"(?m)#.*$")


def _strip_prose(body: str) -> str:
    """Remove docstrings and line comments so signatures match active text only."""
    return _LINE_COMMENT.sub("", _TRIPLE_QUOTED.sub(" ", body))


def weakening_findings(content: str) -> List[str]:
    """Deterministic labels for the known guardrail-weakening shapes."""
    body = content or ""
    if not body:
        return []
    scanned = _strip_prose(body)
    found: List[str] = []
    for pattern, label, prose_prone in _WEAKENING_SIGNATURES:
        if re.search(pattern, scanned if prose_prone else body, re.IGNORECASE):
            if label not in found:
                found.append(label)
    return found


# --------------------------------------------------------------------------
# the reading
# --------------------------------------------------------------------------


@dataclass
class ValuesReading:
    """One complete reading: three readings folded into one verdict."""

    verdict: Verdict
    kind: str = REVIEW_KIND
    tool: str = ""
    path: str = ""
    surface_glob: str = ""
    surface_reason: str = ""
    mode: str = DEFAULT_MODE
    tier: str = "T1"
    reaches_reality: bool = False
    reasons: List[str] = field(default_factory=list)
    ordering_ids: List[str] = field(default_factory=list)
    ordering_unruled: bool = False
    ordering_on_match: str = ""
    compass_verdict: str = ""
    conscience_hold: bool = False
    must_address: List[str] = field(default_factory=list)
    posture_verdict: str = ""
    posture_votes: List[str] = field(default_factory=list)
    overridden: bool = False

    @property
    def applies(self) -> bool:
        return self.verdict is not Verdict.NOT_APPLICABLE

    def to_row(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "verdict": self.verdict.value,
            "mode": self.mode,
            "tool": self.tool,
            "path": self.path,
            "normalized_path": normalize_path(self.path),
            "surface_glob": self.surface_glob,
            "surface_reason": self.surface_reason,
            "tier": self.tier,
            "reaches_reality": self.reaches_reality,
            "reasons": list(self.reasons),
            "ordering_ids": list(self.ordering_ids),
            "ordering_unruled": self.ordering_unruled,
            "ordering_on_match": self.ordering_on_match,
            "compass_verdict": self.compass_verdict,
            "conscience_hold": self.conscience_hold,
            "must_address": list(self.must_address),
            "posture_verdict": self.posture_verdict,
            "posture_votes": list(self.posture_votes),
            "overridden": self.overridden,
        }

    def render(self) -> str:
        head = f"VALUES COUNCIL [{self.mode}] {self.verdict.value.upper()}"
        if self.path:
            head += f" -- {self.path}"
        lines = [head]
        if self.surface_reason:
            lines.append(f"  surface: {self.surface_glob} ({self.surface_reason})")
        if self.ordering_ids:
            lines.append(
                f"  ordering: {', '.join(self.ordering_ids)} -> {self.ordering_on_match}"
            )
        elif self.ordering_unruled and self.applies:
            lines.append("  ordering: unruled -- no governing ruling covers this case")
        if self.compass_verdict:
            lines.append(f"  compass: {self.compass_verdict}")
        if self.conscience_hold:
            lines.append("  CONSCIENCE HOLD -- a person could be harmed. Look again.")
        if self.posture_verdict:
            lines.append(f"  posture: {self.posture_verdict}")
        for reason in self.reasons:
            lines.append(f"  - {reason}")
        for item in self.must_address:
            lines.append(f"  must address: {item}")
        return "\n".join(lines)


def _ordering_state(node_root: Path) -> OrderingState:
    """Load the node's rulings, or HALT. An empty store is a successful convene."""
    try:
        return OrderingStore(node_root).load()
    except OSError as exc:
        raise CouncilError(
            f"the ordering store under {node_root} could not be read: {exc}. "
            "An ordering BINDS, so a consult that cannot run is silence, and "
            "silence is not consent."
        ) from exc


def _consult_ordering(state: OrderingState, facts: Dict[str, Any]) -> Tuple[List[Ordering], bool]:
    governing = state.governing(facts)
    return governing, not governing


def _default_action(
    *,
    path: str,
    surface: SurfaceEntry,
    tier: str,
    reaches_reality: bool,
    findings: Sequence[str],
    command: str = "",
) -> ProposedAction:
    """Build the proposal the compass reads for a core WRITE.

    Deliberately built from the PATH, the declared reason and the weakening
    findings -- never from raw file content. See the module blind spots.
    """
    norm = normalize_path(path)
    signed_bundle = norm.startswith("genesis/imprint/") or "trust-root" in norm
    people: Tuple[str, ...] = ()
    if "/rules/" in norm or norm.startswith("genesis/imprint/"):
        people = ("people this node may later reach",)
    notes = "; ".join(findings)
    return ProposedAction(
        summary=f"core-mechanic change to {path}",
        tier=tier,
        reaches_reality=reaches_reality,
        affects_people=people,
        reversible=not signed_bundle,
        evidence=f"declared core surface: {surface.reason}",
        notes=notes,
        window=command,
    )


def _posture_item(path: str, surface: SurfaceEntry, tier: str, reaches: bool,
                  command: str) -> ActuationItem:
    digest = hashlib.sha256(normalize_path(path).encode("utf-8")).hexdigest()[:12]
    return ActuationItem(
        item_id=f"CORE-{digest}",
        title=f"core-mechanic change to {path}",
        description=f"Change to a declared core surface: {surface.reason}",
        tier=tier,
        command=command,
        complexity=None,
        reaches_reality=reaches,
    )


def _vote_line(vote: CouncilVote) -> str:
    return f"{vote.member}:{vote.vote}"


def review_core_write(
    tool: str,
    tool_input: Mapping[str, Any],
    *,
    node_root: Path | str,
    surface: Optional[CoreSurface] = None,
    mode: Optional[str] = None,
    content: Optional[str] = None,
    indicators: Optional[RealityIndicators] = None,
    action: Optional[ProposedAction] = None,
    record: bool = True,
) -> ValuesReading:
    """The three readings over one proposed core-mechanic change.

    Returns NOT_APPLICABLE (and records nothing) when no declared core surface
    is targeted. Raises :class:`CouncilError` when the council could not
    convene -- a missing surface file, an unreadable ordering store, a ledger
    that cannot be written.
    """
    resolved_mode = mode or mode_from_env()
    if resolved_mode not in MODES:
        raise CouncilError(f"mode {resolved_mode!r} is not one of {MODES}")
    surf = surface if surface is not None else load_core_surface()

    candidates = target_paths(tool, tool_input)
    hit: Optional[Tuple[str, SurfaceEntry]] = None
    for candidate in candidates:
        entry = surf.match(candidate)
        if entry is not None:
            hit = (candidate, entry)
            break
    if hit is None:
        return ValuesReading(verdict=Verdict.NOT_APPLICABLE, tool=tool, mode=resolved_mode)

    path, entry = hit
    command = str(tool_input.get("command") or "") if tool in SHELL_COMMAND_TOOLS else ""
    body = content
    if body is None and tool in FILE_WRITE_TOOLS:
        body = str(
            tool_input.get("content")
            or tool_input.get("new_string")
            or tool_input.get("new_source")
            or ""
        )
    findings = weakening_findings(body or "")

    classification = classify_reaches_reality(tool, tool_input, indicators=indicators)
    tier = classification[0] if classification else "T1"
    reaches = classification is not None and tier in ("T3", "T4")

    reading = ValuesReading(
        verdict=Verdict.CONSENT,
        kind=REVIEW_KIND,
        tool=tool,
        path=path,
        surface_glob=entry.glob,
        surface_reason=entry.reason,
        mode=resolved_mode,
        tier=tier,
        reaches_reality=reaches,
    )
    if classification:
        reading.reasons.append(f"classifier: {classification[1]} ({tier})")

    # -- reading 1: the ordering consult. It answers before anybody votes. ----
    state = _ordering_state(Path(node_root))
    facts = {
        "surface": "core",
        "tool": tool,
        "tier": tier,
        "reaches_reality": reaches,
        "core_glob": entry.glob,
    }
    governing, unruled = _consult_ordering(state, facts)
    reading.ordering_unruled = unruled
    verdict = Verdict.CONSENT
    for ruling in governing:
        reading.ordering_ids.append(ruling.id)
        if ruling.on_match == "escalate":
            reading.ordering_on_match = "escalate"
            reading.reasons.append(
                f"a governing ruling ({ruling.id}) escalates this case: {ruling.ordering}"
            )
            verdict = _stricter(verdict, Verdict.ESCALATE)
        else:
            reading.ordering_on_match = reading.ordering_on_match or ruling.on_match
            reading.reasons.append(
                f"a governing ruling ({ruling.id}) permits this case; recorded, and it "
                "lowers nothing -- this council may raise the bar, never lower a gate"
            )
    if unruled:
        reading.reasons.append(
            "no governing ruling covers this case -- the council is asking, not guessing"
        )

    # -- reading 2: the moral compass ----------------------------------------
    proposal = action if action is not None else _default_action(
        path=path, surface=entry, tier=tier, reaches_reality=reaches,
        findings=findings, command=command,
    )
    compass = MoralCompassCouncil().deliberate(proposal)
    reading.compass_verdict = compass.verdict.value
    reading.conscience_hold = compass.conscience_hold
    reading.must_address = list(compass.must_address)
    if compass.verdict is CouncilVerdict.REFRAIN:
        verdict = _stricter(verdict, Verdict.HOLD)
        reading.reasons.append("moral compass: REFRAIN -- a surfaced hold, not a block")
    if compass.conscience_hold:
        verdict = _stricter(verdict, Verdict.HOLD)
        reading.reasons.append(
            "conscience hold: a person could be harmed. This surfaces in every mode."
        )

    # -- reading 3: the posture tally ----------------------------------------
    item = _posture_item(path, entry, tier, reaches, command)
    posture_verdict, votes = posture_council_vote(item, indicators=indicators)
    reading.posture_verdict = posture_verdict
    reading.posture_votes = [_vote_line(v) for v in votes]
    if posture_verdict != APPROVED:
        verdict = _stricter(verdict, Verdict.ESCALATE)
        rejects = [f"{v.member}: {v.reason}" for v in votes if v.vote == "REJECT"]
        reading.reasons.append("posture council: REJECTED -- " + "; ".join(rejects))

    # -- the content floor ----------------------------------------------------
    for label in findings:
        verdict = _stricter(verdict, Verdict.ESCALATE)
        reading.reasons.append(f"guardrail-weakening signature: {label}")

    if verdict is Verdict.CONSENT:
        # Always, not only when nothing else was said. A CONSENT that does not
        # carry its own limit reads as a clearance, and this council cannot
        # issue one: deterministic signatures catch the known-dangerous shapes
        # and cannot catch a semantic regression.
        reading.reasons.append(
            "no known-dangerous signature found -- consent means nothing known was "
            "found, never that the change is safe"
        )
    reading.verdict = verdict

    if record:
        CoreReviewLedger(node_root).append(reading.to_row())
    return reading


#: Reason fragments from the reaches-reality classifier that mean a PERSON who
#: is not the operator is on the other end. A keyword read of the classifier's
#: own reason string, and therefore a heuristic: an action that reaches a person
#: by a route the classifier does not name is invisible to this test.
_PERSON_REACHING = re.compile(
    r"outbound|disclos\w*|mailbox|message|email|recipient|post\b|chat|calendar|"
    r"invit\w*|grant\w*|permission",
    re.IGNORECASE,
)


def reading_for_conduct(
    action: ProposedAction,
    *,
    node_root: Path | str,
    tool: str = "",
    tool_input: Optional[Mapping[str, Any]] = None,
    mode: Optional[str] = None,
    indicators: Optional[RealityIndicators] = None,
    record: bool = True,
) -> ValuesReading:
    """The conduct docket: read the node's ACTIONS, not only its files.

    Runs when BOTH hold: the reaches-reality classifier has an opinion about
    this action, AND the action names a person subject (``affects_people`` is
    non-empty, or the classifier's own reason says a person is reached). The
    reading is attached BEFORE the action reaches the operator, so a conscience
    hold can force care rather than annotate a decision already made.

    Mode-independent by construction: the conscience hold always surfaces. An
    operator may choose how strict the core-write gate is; whether they are
    told a person could be harmed is not a setting.
    """
    resolved_mode = mode or mode_from_env()
    inputs: Mapping[str, Any] = tool_input if isinstance(tool_input, Mapping) else {}

    classification = (
        classify_reaches_reality(tool, inputs, indicators=indicators) if tool else None
    )
    tier = classification[0] if classification else str(action.tier or "T0").upper()
    reason_text = classification[1] if classification else ""
    reaches = bool(classification) or bool(action.reaches_reality)

    names_person = bool(action.affects_people) or bool(
        reason_text and _PERSON_REACHING.search(reason_text)
    )
    if not (reaches and names_person):
        return ValuesReading(
            verdict=Verdict.NOT_APPLICABLE, kind=CONDUCT_KIND, tool=tool,
            mode=resolved_mode, tier=tier, reaches_reality=reaches,
        )

    subject = ProposedAction(
        summary=action.summary,
        tier=tier,
        reaches_reality=True,
        affects_people=tuple(action.affects_people),
        reversible=action.reversible,
        evidence=action.evidence,
        notes=action.notes,
        effort=action.effort,
        window=action.window,
        depends_on=tuple(action.depends_on),
    )
    compass = MoralCompassCouncil().deliberate(subject)

    reading = ValuesReading(
        verdict=Verdict.CONSENT,
        kind=CONDUCT_KIND,
        tool=tool,
        path=action.summary,
        mode=resolved_mode,
        tier=tier,
        reaches_reality=True,
        compass_verdict=compass.verdict.value,
        conscience_hold=compass.conscience_hold,
        must_address=list(compass.must_address),
    )
    if reason_text:
        reading.reasons.append(f"classifier: {reason_text} ({tier})")
    verdict = Verdict.CONSENT
    if compass.verdict is CouncilVerdict.REFRAIN:
        verdict = _stricter(verdict, Verdict.HOLD)
        reading.reasons.append("moral compass: REFRAIN -- a surfaced hold, not a block")
    if compass.conscience_hold:
        verdict = _stricter(verdict, Verdict.HOLD)
        reading.reasons.append(
            "conscience hold: a person could be harmed. This surfaces in every mode."
        )
    if tier in ("T3", "T4"):
        reading.reasons.append(
            "T3/T4 reaches-reality: a human word for THIS act is required regardless "
            "of this reading -- the council never authorizes it"
        )
    if any(v.stance is Stance.OBJECT for v in compass.votes) and verdict is Verdict.CONSENT:
        verdict = Verdict.HOLD
    reading.verdict = verdict

    if record:
        CoreReviewLedger(node_root).append(reading.to_row())
    return reading


# --------------------------------------------------------------------------
# the gate
# --------------------------------------------------------------------------


def gate_values_council(
    tool: str,
    params: Any,
    *,
    node_root: Optional[Path | str] = None,
    surface: Optional[CoreSurface] = None,
    mode: Optional[str] = None,
    indicators: Optional[RealityIndicators] = None,
) -> Tuple[bool, List[str]]:
    """The gate member. Returns ``(allowed, notes)``; never exits, never prints.

    Modes:

      * ``shadow``  -- records; surfaces NOTHING except a HALT, because a
        diagnostic that can hide its own breakage has stopped being one;
        blocks nothing.
      * ``observe`` -- records AND surfaces; blocks nothing. The birth default.
      * ``enforce`` -- records, surfaces, and BLOCKS a HOLD or an ESCALATE
        until an operator override row exists for that write. The override is
        consumed when it is used, so it unblocks one named write and not a
        standing lane.

    A HALT (could not convene, unreadable surface, unwritable ledger) blocks in
    ``enforce`` and is surfaced loudly otherwise -- the same posture the
    reference gate takes with an internal error: never a silent permit, and in
    an advisory mode the human is still in the loop.
    """
    # Cheap pre-check before any store touch: only a write tool or a shell tool
    # can reach a path at all.
    if tool not in FILE_WRITE_TOOLS and tool not in SHELL_COMMAND_TOOLS:
        return True, []

    try:
        resolved_mode = mode or mode_from_env()
        root = Path(node_root) if node_root is not None else node_root_from_env()
        tool_input = _coerce_params(params)
        if tool_input is None:
            raise CouncilError(
                "the tool payload could not be parsed, so the council cannot tell "
                "which path this call targets. An unreadable payload is not an "
                "empty one."
            )
        reading = review_core_write(
            tool, tool_input, node_root=root, surface=surface,
            mode=resolved_mode, indicators=indicators,
        )
    except CouncilError as exc:
        note = f"VALUES-COUNCIL HALT: {exc}"
        enforcing = _enforcing(mode)
        if enforcing:
            return False, [note + " -- fail-closed (enforce mode)"]
        return True, [note + " -- surfaced; not blocking in this mode"]

    if not reading.applies:
        return True, []

    if reading.mode == "shadow":
        return True, []

    notes = [reading.render()]
    if reading.mode != "enforce" or not reading.verdict.is_refusal:
        return True, notes

    ledger = CoreReviewLedger(node_root if node_root is not None else node_root_from_env())
    try:
        state = ledger.state()
    except CouncilError as exc:
        return False, notes + [f"VALUES-COUNCIL HALT: {exc} -- fail-closed (enforce mode)"]
    if state.outstanding(reading.path) > 0:
        ledger.append(
            {
                "kind": OVERRIDE_CONSUMED_KIND,
                "path": reading.path,
                "normalized_path": normalize_path(reading.path),
                "verdict": reading.verdict.value,
            }
        )
        return True, notes + [
            "an operator override was outstanding for this write and has been "
            "consumed; it unblocks this one write and nothing else"
        ]
    return False, notes + [
        "blocked in enforce mode. The operator may proceed: record an override "
        "naming this write and stating a reason (a blank reason is refused)."
    ]


def _enforcing(mode: Optional[str]) -> bool:
    try:
        return (mode or mode_from_env()) == "enforce"
    except CouncilError:
        # An unreadable mode is not a permission. Treat it as strict.
        return True


def _coerce_params(params: Any) -> Optional[Dict[str, Any]]:
    """Accept a mapping or a JSON string. Anything else is unreadable, not empty."""
    if isinstance(params, Mapping):
        return dict(params)
    if isinstance(params, (str, bytes)):
        text = params.decode("utf-8", "replace") if isinstance(params, bytes) else params
        if not text.strip():
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        return dict(parsed) if isinstance(parsed, dict) else None
    if params is None:
        return {}
    return None


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------


def _write_surface(root: Path) -> Path:
    """A tiny declared surface whose globs match the shipped repo's shape."""
    path = root / "core-surface.yaml"
    path.write_text(
        "schema: core-surface/v1\n"
        "as_of: '2026-09-06'\n"
        "surface:\n"
        "  - glob: 'genesis/imprint/**'\n"
        "    reason: 'the signed birth bundle'\n"
        "  - glob: 'config/core-surface.yaml'\n"
        "    reason: 'this file declares its own surface'\n",
        encoding="utf-8",
    )
    return path


def selftest() -> int:
    """Prove every verdict path can fire. A detector that has never fired is
    indistinguishable from a broken one."""
    failures: List[str] = []
    checked: List[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        checked.append(label)
        print(f"  [{'FIRED ' if ok else 'MISSED'}] {label} {detail}".rstrip())
        if not ok:
            failures.append(label)

    with tempfile.TemporaryDirectory(prefix="intentops-values-council-") as tmp:
        root = Path(tmp)
        surface = load_core_surface(_write_surface(root))

        # 1. NOT_APPLICABLE: a write outside the declared surface
        r = review_core_write(
            "Write", {"file_path": "docs/quick-start.md", "content": "hello"},
            node_root=root, surface=surface, mode="observe",
        )
        check("a non-core write is NOT_APPLICABLE",
              r.verdict is Verdict.NOT_APPLICABLE, r.verdict.value)

        # 2. CONSENT: a clean core write
        r = review_core_write(
            "Write",
            {"file_path": "genesis/imprint/IMPRINT.md", "content": "A plain line of text.\n"},
            node_root=root, surface=surface, mode="observe",
        )
        check("a clean core write CONSENTS", r.verdict is Verdict.CONSENT, r.verdict.value)

        # 3. ESCALATE: a guardrail-weakening signature
        r = review_core_write(
            "Write",
            {"file_path": "genesis/imprint/invariants/core.yaml",
             "content": "fail_closed: false\n"},
            node_root=root, surface=surface, mode="observe",
        )
        check("a guardrail-weakening core write ESCALATES",
              r.verdict is Verdict.ESCALATE, r.verdict.value)

        # 4. Step 0 widening: a shell command reaching the same path
        r = review_core_write(
            "Bash",
            {"command": "sed -i s/a/b/ genesis/imprint/IMPRINT.md"},
            node_root=root, surface=surface, mode="observe",
        )
        check("a shell command targeting a core path is seen",
              r.applies and normalize_path(r.path).startswith("genesis/imprint/"), r.path)

        # 5. HOLD: a REFRAIN carrying a conscience hold, on a supplied proposal.
        #    Two guardians object independently: Rogers (a named person, reached
        #    irreversibly, and kept in the dark) and Henson (irreversible with no
        #    rehearsal), which is REFRAIN by the council's fixed tally.
        harmful = ProposedAction(
            summary="silently overwrite the records of an individual and destroy the only copy",
            tier="T3", reaches_reality=True, affects_people=("an individual",),
            reversible=False, evidence="",
        )
        r = review_core_write(
            "Write", {"file_path": "genesis/imprint/rules/honesty.md", "content": "x"},
            node_root=root, surface=surface, mode="observe", action=harmful,
        )
        check("a REFRAIN becomes a surfaced HOLD, never a block",
              r.verdict is Verdict.HOLD and r.compass_verdict == "REFRAIN",
              r.compass_verdict)
        check("a conscience hold is recorded on the reading", r.conscience_hold)

        # 6. The conduct docket fires on an action that reaches a person
        conduct = reading_for_conduct(
            harmful, node_root=root,
            tool="Bash", tool_input={"command": "git push origin main"},
        )
        check("the conduct docket reads an action that reaches a person",
              conduct.applies and conduct.conscience_hold, conduct.verdict.value)

        # 7. The conduct docket is NOT_APPLICABLE with no person named
        quiet = reading_for_conduct(
            ProposedAction(summary="rebuild the local index", tier="T1"),
            node_root=root, tool="Bash", tool_input={"command": "ls"},
        )
        check("the conduct docket abstains when no person is named",
              not quiet.applies, quiet.verdict.value)

        # 8. enforce blocks, and a blank override is refused
        allowed, notes = gate_values_council(
            "Write",
            {"file_path": "genesis/imprint/invariants/core.yaml",
             "content": "fail_closed: false\n"},
            node_root=root, surface=surface, mode="enforce",
        )
        check("enforce mode blocks an ESCALATE", not allowed,
              notes[-1][:60] if notes else "")
        blank_refused = False
        try:
            record_override(root, "genesis/imprint/invariants/core.yaml", "   ")
        except CouncilError:
            blank_refused = True
        check("a blank override is refused", blank_refused)

        # 9. an override with a reason unblocks exactly one write
        record_override(root, "genesis/imprint/invariants/core.yaml",
                        "reviewed by the operator; the flag is being removed, not disabled")
        allowed_after, _ = gate_values_council(
            "Write",
            {"file_path": "genesis/imprint/invariants/core.yaml",
             "content": "fail_closed: false\n"},
            node_root=root, surface=surface, mode="enforce",
        )
        allowed_again, _ = gate_values_council(
            "Write",
            {"file_path": "genesis/imprint/invariants/core.yaml",
             "content": "fail_closed: false\n"},
            node_root=root, surface=surface, mode="enforce",
        )
        check("an override unblocks one write and is consumed",
              allowed_after and not allowed_again,
              f"first={allowed_after} second={allowed_again}")

        # 10. shadow surfaces nothing
        allowed_shadow, shadow_notes = gate_values_council(
            "Write",
            {"file_path": "genesis/imprint/invariants/core.yaml",
             "content": "fail_closed: false\n"},
            node_root=root, surface=surface, mode="shadow",
        )
        check("shadow records but surfaces nothing",
              allowed_shadow and not shadow_notes, f"notes={len(shadow_notes)}")

        # 11. HALT: an undeclared mode, and a missing surface file
        halted = False
        try:
            mode_from_env({MODE_ENV: "lenient"})
        except CouncilError:
            halted = True
        check("an undeclared mode HALTs", halted)

        missing = False
        try:
            load_core_surface(root / "no-such-surface.yaml")
        except CouncilError:
            missing = True
        check("a missing declared surface HALTs", missing)

        no_reason = False
        try:
            bad = root / "bad-surface.yaml"
            bad.write_text(
                "schema: core-surface/v1\nas_of: '2026-09-06'\n"
                "surface:\n  - glob: 'a/**'\n",
                encoding="utf-8",
            )
            load_core_surface(bad)
        except CouncilError:
            no_reason = True
        check("a surface entry with no reason HALTs", no_reason)

        # 12. an ordering that escalates BINDS before any vote
        store = OrderingStore(root)
        ruling = Ordering.create(
            statement="Changes to the signed birth bundle go to the operator.",
            tension="Speed of self-repair against the operator staying answerable.",
            ordering="The operator's sight is placed above the node's convenience.",
            conditions="Any change to a declared core surface on this node.",
            authority="the operator",
        )
        store.rule(ruling)
        store.compile_predicate(
            ruling.id, {"surface": "core"}, on_match="escalate",
            by="the operator", rationale="selftest",
        )
        r = review_core_write(
            "Write",
            {"file_path": "genesis/imprint/IMPRINT.md", "content": "A plain line.\n"},
            node_root=root, surface=surface, mode="observe",
        )
        check("a governing ruling BINDS before any vote",
              r.verdict is Verdict.ESCALATE and ruling.id in r.ordering_ids,
              r.verdict.value)

        # 13. the ledger folds, and a malformed row is counted not dropped
        ledger = CoreReviewLedger(root)
        with ledger.path.open("a", encoding="utf-8") as handle:
            handle.write("{not json\n")
        state = ledger.state()
        check("a malformed ledger row is counted, never dropped",
              state.malformed == 1 and state.rows > 0,
              f"malformed={state.malformed} rows={state.rows}")
        posture_verdict, _notes = state.posture()
        check("a malformed row makes the ledger posture DEGRADED",
              posture_verdict == "DEGRADED", posture_verdict)

    total = len(checked)
    print(f"values_council selftest: {total - len(failures)}/{total} paths behaved as declared")
    if failures:
        print("SELFTEST FAILED: " + ", ".join(failures))
        return 1
    print("SELFTEST PASS -- every verdict path above can fire")
    return 0


def _main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="the values council -- steward of the core (Code rung, no model)"
    )
    parser.add_argument("--selftest", action="store_true",
                        help="prove every verdict path can fire")
    parser.add_argument("--posture", action="store_true",
                        help="read the core-review ledger and report its posture")
    parser.add_argument("--node-root", type=Path, default=None,
                        help=f"node root (default: ${NODE_ROOT_ENV})")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.posture:
        try:
            root = args.node_root if args.node_root is not None else node_root_from_env()
            state = CoreReviewLedger(root).state()
        except CouncilError as exc:
            print(f"HALT: {exc}")
            return 1
        verdict, notes = state.posture()
        print(f"core-review ledger: {verdict}")
        for note in notes:
            print(f"  {note}")
        return 1 if verdict == "DEGRADED" else 0
    parser.print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
