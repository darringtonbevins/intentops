"""Grounded-signal detectors -- cheap, stateless instruments for the
grounded-vs-ungrounded axis.

PURPOSE. The useful discriminator for machine-produced content is not
imagined-versus-perceived but GROUNDED-versus-UNGROUNDED: was the output
constrained by anything outside the generator? Faulty recall, guessing and
invention share the property that matters, and that property is detectable
BELOW the reasoning tier -- variance where evidence is absent, zero movement
under evidence, causally orphaned citations, absence claims missing their
sensitivity clause. None of these needs a model, a network, or introspection.

WRITE MODEL. None. Every detector here is a pure function over values: it takes
inputs, returns a frozen dataclass, touches no store and calls no model. The
sweep CLI is the only thing that writes, and it writes a fresh timestamped JSONL
file per run under ``<node>/.intentops/hallucination/`` -- create-once files, no
shared whiteboard, so there is no multi-writer store to declare.

DESIGN CONTRACT.

* ADDITIVE ONLY. Whatever verdict layer a node already has stays in place
  unchanged; these instruments run beside it, never instead of it.
* ROUTE, NEVER VERDICT. A flag means "escalate for attention". It never rejects,
  blocks, or downgrades on its own.
* STATELESS AND PURE. Code rung: stdlib only, no model call. Telemetry is the
  caller's job.
* STATED OPERATING POINTS. Every threshold below is named as a constant with the
  error it trades against.

THE SIGNALS.

===========================  =================================================
``spread()``                 wide answer distribution over ONE question = the
                             BENIGN ungrounded class (it announces itself)
``control_probe()``          near-zero movement between with-evidence and
                             without-evidence arms = the answer was manufactured
                             from the prior. Catches SHARP fabrications that
                             ``spread`` structurally cannot see
``no_evidence_by_construction()``  the context never contained the claim's
                             terms, so treatment == control by construction:
                             ungroundedness decidable before any answer
``round_number_signature()`` measurement yields ragged values; uniformly round
                             values are prior-shaped
``causal_edges()``           a real artifact has edges (version-control ancestry,
                             inbound references); a fabricated citation floats
``AbsenceClaim``             negative space admits only the three-clause form:
                             looked / instrument sensitivity / found nothing
===========================  =================================================

BLIND SPOTS (this module's, beyond each result's own ``blind_spots`` tuple):

1. Every detector is a ROUTER. A clean sweep is not evidence that content is
   grounded -- only that these six cheap tells did not fire.
2. ``causal_edges`` needs a working ``git`` binary in the workspace. Where git
   is unavailable it returns ``GIT_UNAVAILABLE`` -- an instrument failure that
   stays in the denominator, never a pass.
3. Nothing here reads semantics. Term overlap, distribution distance and digit
   shape are all surface features; a fluent fabrication that reuses the
   context's vocabulary passes every one of them.
4. The ``--selftest`` cannot exercise the version-control branches of
   ``causal_edges`` (``EDGES_PRESENT`` / ``ORPHAN``) without a repository, so it
   proves only the ``REF_MISSING`` and ``INSTRUMENT_ERROR`` paths of that
   detector. Stated rather than hidden.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePath
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "SpreadResult", "ControlProbeResult", "ConstructionResult",
    "RoundNumbersResult", "CausalEdgesResult", "AbsenceClaim",
    "AbsenceValidation", "spread", "control_probe",
    "no_evidence_by_construction", "round_number_signature", "causal_edges",
    "validate_absence_claim", "sweep_citations", "selftest", "main",
]

# --- operating points: every detector names its threshold and the error it
# --- trades against -------------------------------------------------------
SPREAD_MIN_SAMPLES = 5      # below this, spread is noise -> INSUFFICIENT_SAMPLES
WIDE_MODAL_SHARE = 0.5      # modal answer under half the mass = wide (flag);
                            # trades toward false negatives on 2-way splits
CONTROL_MIN_SAMPLES = 3     # per arm; below -> INSUFFICIENT_SAMPLES
TV_PRIOR_ONLY = 0.2         # total variation below this = evidence moved
                            # nothing. TV chosen over KL: bounded in [0, 1],
                            # no infinities on disjoint support
ROUND_MIN_VALUES = 3        # fewer values -> INSUFFICIENT_SAMPLES
ROUND_FLAG_FRACTION = 0.8   # >= 80% suspiciously-round values flags
ROUND_MAGNITUDE_FLOOR = 10  # values below this are EXEMPT (see _is_round)
MIN_TERM_LEN = 4            # claim tokens shorter than this carry no signal

DEFAULT_SWEEP_ROOTS: Tuple[str, ...] = ("docs", "genesis", ".intentops-rules")

_STOPWORDS = frozenset({
    "this", "that", "these", "those", "with", "from", "have", "been", "were",
    "will", "would", "could", "should", "there", "their", "about", "which",
    "when", "what", "where", "does", "only", "very", "into", "over", "under",
    "than", "then", "them", "they", "your", "some", "such", "also", "each",
    "because", "while", "after", "before", "between",
})


# ---------------------------------------------------------------------------
# spread -- the variance tell
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpreadResult:
    flag: bool                 # route-for-attention; NEVER a verdict
    status: str                # OK | INSUFFICIENT_SAMPLES
    n: int
    distinct: int
    modal_answer: str
    modal_share: float
    entropy_bits: float
    blind_spots: Tuple[str, ...] = field(default=(
        "sharp fabrication (low variance, zero grounding) is invisible to "
        "spread -- use control_probe or causal_edges for that class",
        "normalization is whitespace/case only; paraphrases of one answer "
        "count as distinct",
    ))
    detail: str = ""


def _norm_answer(s: str) -> str:
    return " ".join(str(s).split()).lower()


def spread(answers: Iterable[str]) -> SpreadResult:
    """Distribution over resampled answers to ONE question.

    A wide spread over an unpinned question is the benign ungrounded class: the
    answer was drawn, not read, and the drawing announces itself.
    """
    normed = [_norm_answer(a) for a in answers if str(a).strip()]
    n = len(normed)
    if n < SPREAD_MIN_SAMPLES:
        return SpreadResult(False, "INSUFFICIENT_SAMPLES", n, len(set(normed)),
                            "", 0.0, 0.0,
                            detail=f"need >= {SPREAD_MIN_SAMPLES} samples, got {n}")
    counts = Counter(normed)
    modal_answer, modal_count = counts.most_common(1)[0]
    modal_share = modal_count / n
    entropy = -sum((c / n) * math.log2(c / n) for c in counts.values())
    return SpreadResult(
        flag=modal_share < WIDE_MODAL_SHARE,
        status="OK", n=n, distinct=len(counts), modal_answer=modal_answer,
        modal_share=modal_share, entropy_bits=entropy,
        detail=f"modal {modal_share:.2f} vs wide-threshold {WIDE_MODAL_SHARE}")


# ---------------------------------------------------------------------------
# control_probe -- movement under evidence
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ControlProbeResult:
    flag: bool
    status: str                # PRIOR_ONLY | EVIDENCE_MOVED | INSUFFICIENT_SAMPLES
    tv_distance: float         # total variation in [0, 1]
    n_with: int
    n_without: int
    blind_spots: Tuple[str, ...] = field(default=(
        "measures whether evidence moved the answer, not whether it moved it "
        "in the RIGHT direction",
        "both arms must hold the question fixed; a drifting prompt confounds "
        "the delta",
    ))
    detail: str = ""


def control_probe(with_evidence: Iterable[str],
                  without_evidence: Iterable[str]) -> ControlProbeResult:
    """The grounded/ungrounded test that needs no introspection.

    Same question, evidence present versus withheld. Near-zero movement means
    the answer was manufactured from the prior -- catching the SHARP fabrication
    (confident, low-variance, zero grounding) that ``spread`` cannot see.
    """
    p_raw = [_norm_answer(a) for a in with_evidence if str(a).strip()]
    q_raw = [_norm_answer(a) for a in without_evidence if str(a).strip()]
    if len(p_raw) < CONTROL_MIN_SAMPLES or len(q_raw) < CONTROL_MIN_SAMPLES:
        return ControlProbeResult(
            False, "INSUFFICIENT_SAMPLES", 0.0, len(p_raw), len(q_raw),
            detail=f"need >= {CONTROL_MIN_SAMPLES} per arm, "
                   f"got {len(p_raw)}/{len(q_raw)}")
    p, q = Counter(p_raw), Counter(q_raw)
    support = set(p) | set(q)
    tv = 0.5 * sum(abs(p[a] / len(p_raw) - q[a] / len(q_raw)) for a in support)
    prior_only = tv < TV_PRIOR_ONLY
    return ControlProbeResult(
        flag=prior_only,
        status="PRIOR_ONLY" if prior_only else "EVIDENCE_MOVED",
        tv_distance=tv, n_with=len(p_raw), n_without=len(q_raw),
        detail=f"TV {tv:.3f} vs prior-only threshold {TV_PRIOR_ONLY}")


# ---------------------------------------------------------------------------
# no_evidence_by_construction -- decidable from the setup
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConstructionResult:
    flag: bool
    status: str  # UNGROUNDED_BY_CONSTRUCTION | CONTEXT_TERMS_PRESENT | INSTRUMENT_ERROR
    terms_checked: Tuple[str, ...]
    terms_found: Tuple[str, ...]
    blind_spots: Tuple[str, ...] = field(default=(
        "term presence does not prove grounding -- the claim may still ignore "
        "or contradict the context (run control_probe for movement)",
        "substring matching: 'white' matches 'whitewash', '50' matches '150'; "
        "coarse by design",
        "semantic evidence carried in synonyms or structure is invisible to "
        "term overlap",
    ))
    detail: str = ""


def no_evidence_by_construction(claim: str, context: str) -> ConstructionResult:
    """When the context never held the claim's content terms, the
    with-evidence and without-evidence conditions are the SAME condition.

    The answer could not have been grounded here, whatever it says -- knowable
    before a single answer is sampled.
    """
    if not isinstance(claim, str) or not isinstance(context, str):
        # str(None) == "none" would satisfy term matching for exactly the
        # absence-claim vocabulary this suite polices. Non-str input is an
        # instrument error, never coerced.
        return ConstructionResult(
            False, "INSTRUMENT_ERROR", (), (),
            detail="claim and context must be str (got "
                   f"{type(claim).__name__}/{type(context).__name__})")
    # Pure-digit tokens are kept at any length -- short numeric literals are
    # often the highest-signal term in a factual claim.
    terms = tuple(sorted({
        t for t in re.findall(r"[a-z0-9]+", claim.lower())
        if (len(t) >= MIN_TERM_LEN or t.isdigit()) and t not in _STOPWORDS}))
    if not terms:
        return ConstructionResult(
            False, "INSTRUMENT_ERROR", (), (),
            detail="no content terms extractable from claim")
    ctx = context.lower()
    found = tuple(t for t in terms if t in ctx)
    if not found:
        return ConstructionResult(
            True, "UNGROUNDED_BY_CONSTRUCTION", terms, (),
            detail=f"0/{len(terms)} claim terms present in context")
    return ConstructionResult(
        False, "CONTEXT_TERMS_PRESENT", terms, found,
        detail=f"{len(found)}/{len(terms)} claim terms present "
               "(presence is not proof of grounding)")


# ---------------------------------------------------------------------------
# round_number_signature -- prior-shaped values
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoundNumbersResult:
    flag: bool
    status: str                # OK | INSUFFICIENT_SAMPLES | INSTRUMENT_ERROR
    n: int
    round_fraction: float
    blind_spots: Tuple[str, ...] = field(default=(
        "some domains legitimately produce round values (prices, quotas, "
        "config limits) -- the flag routes for a look, never rejects",
        "a fabricator emitting ragged digits passes untouched",
        "MAGNITUDE FLOOR: values below 10 are EXEMPT, so 0.05 / 0.3 never "
        "count as round however prior-shaped they are. The trade buys immunity "
        "to the exact-small-count false positive (a truthful '3 sources, 5 "
        "checks' would otherwise flag) at the cost of missing sub-10 "
        "fabrications. On a corpus mixing magnitudes, exempt sub-10 values "
        "DILUTE the fraction -- partition by magnitude when that matters",
        "a fabricator quoting genuinely round published figures is "
        "indistinguishable from the source material it quotes",
    ))
    detail: str = ""


def _is_round(v: float) -> bool:
    """<= 2 significant digits at magnitude >= ``ROUND_MAGNITUDE_FLOOR``.

    OPERATING POINT. The magnitude floor is a DELIBERATE exemption, not an
    oversight of scale. Roundness-as-tell is a claim about reporting precision,
    and significant digits stop carrying that signal for exact cardinalities: a
    truthful "3 sources, 5 checks, 0 errors" is infinitely precise yet reads
    <= 2 significant digits, so a scale-free rule would flag honest measured
    evidence. The floor trades that false positive away and pays for it by
    missing sub-10 fabrications.

    Known inconsistency, deliberately left: ``v == 0`` returns True although 0
    is below the floor. Reconcile only as part of an evidence-gated detection
    change, never as a drive-by.
    """
    if v == 0:
        return True
    a = abs(float(v))
    if a < ROUND_MAGNITUDE_FLOOR:
        return False
    s = f"{a:.10g}"
    mantissa = s.split("e")[0].split("E")[0] if ("e" in s or "E" in s) else s
    digits = mantissa.replace(".", "").lstrip("0").rstrip("0")
    return len(digits) <= 2


def round_number_signature(values: Iterable[float]) -> RoundNumbersResult:
    """Uniformly round values are prior-shaped -- a measurement costume."""
    try:
        vals = [float(v) for v in values]
    except (TypeError, ValueError) as exc:
        # Malformed input degrades LOUDLY, never crashes and never passes.
        return RoundNumbersResult(False, "INSTRUMENT_ERROR", 0, 0.0,
                                  detail=f"non-numeric input: {exc}")
    n = len(vals)
    if n < ROUND_MIN_VALUES:
        return RoundNumbersResult(False, "INSUFFICIENT_SAMPLES", n, 0.0,
                                  detail=f"need >= {ROUND_MIN_VALUES} values, got {n}")
    frac = sum(1 for v in vals if _is_round(v)) / n
    return RoundNumbersResult(
        flag=frac >= ROUND_FLAG_FRACTION, status="OK", n=n, round_fraction=frac,
        detail=f"round fraction {frac:.2f} vs threshold {ROUND_FLAG_FRACTION}")


# ---------------------------------------------------------------------------
# causal_edges -- a real artifact has ancestry and inbound references
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CausalEdgesResult:
    flag: bool
    status: str    # EDGES_PRESENT | ORPHAN | REF_MISSING | GIT_UNAVAILABLE | INSTRUMENT_ERROR
    ancestry: bool             # some commit introduced or touched it
    edges_in: int              # other files that name it
    blind_spots: Tuple[str, ...] = field(default=(
        "a legitimately brand-new uncommitted file reads ORPHAN until its "
        "first commit -- route, never verdict",
        "inbound-edge search is by basename over tracked *.md/*.py/*.yaml/"
        "*.yml only; other file types, renames and prose aliases are invisible",
        "edges prove the ARTIFACT is real, not that the claim ABOUT it is true",
    ))
    detail: str = ""


def _git(args: Sequence[str], cwd: Path, timeout: int) -> Optional[str]:
    """Run git. ``None`` means the INSTRUMENT failed (a loud GIT_UNAVAILABLE at
    the caller, never a silent pass). Exit 1 from ``git grep`` is a real
    no-match, not a failure."""
    try:
        proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode not in (0, 1):
        return None
    return proc.stdout


def causal_edges(ref: str, workspace: Path, timeout: int = 15) -> CausalEdgesResult:
    """A fact cannot exist without having had an effect on its environment.

    For a cited repository artifact those effects are edges: version-control
    ancestry (something produced it) and inbound references (something depends
    on it). A fabricated citation is causally orphaned.
    """
    if not isinstance(ref, str) or not ref.strip():
        return CausalEdgesResult(False, "INSTRUMENT_ERROR", False, 0,
                                 detail="ref must be a non-empty str")
    raw = ref.strip()
    m = re.match(r"^(?P<path>.+?)(?::\d+(?:-\d+)?)?$", raw)
    path_part = m.group("path") if m else raw
    p = Path(path_part)
    if not p.is_absolute():
        p = Path(workspace) / p
    try:
        resolved = p.resolve()
        ws = Path(workspace).resolve()
    except OSError as exc:
        return CausalEdgesResult(False, "INSTRUMENT_ERROR", False, 0,
                                 detail=f"unresolvable: {exc}")
    if resolved != ws and ws not in resolved.parents:
        return CausalEdgesResult(False, "INSTRUMENT_ERROR", False, 0,
                                 detail=f"ref escapes workspace: {resolved}")
    if not resolved.is_file():
        return CausalEdgesResult(True, "REF_MISSING", False, 0,
                                 detail=f"cited file not found: {resolved}")

    rel = resolved.relative_to(ws).as_posix()
    log_out = _git(["log", "--oneline", "-1", "--", rel], ws, timeout)
    grep_out = _git(["grep", "-l", "-F", PurePath(rel).name,
                     "--", "*.md", "*.py", "*.yaml", "*.yml"], ws, timeout)
    if log_out is None or grep_out is None:
        return CausalEdgesResult(False, "GIT_UNAVAILABLE", False, 0,
                                 detail="git probe failed -- instrument "
                                        "degraded, not a pass")
    ancestry = bool(log_out.strip())
    referents = [ln for ln in grep_out.splitlines()
                 if ln.strip() and ln.strip() != rel]
    edges_in = len(referents)
    if ancestry or edges_in >= 1:
        return CausalEdgesResult(False, "EDGES_PRESENT", ancestry, edges_in,
                                 detail=f"ancestry={ancestry}, edges_in={edges_in}")
    return CausalEdgesResult(True, "ORPHAN", False, 0,
                             detail="exists on disk with no ancestry and no "
                                    "inbound references")


# ---------------------------------------------------------------------------
# AbsenceClaim -- three clauses or nothing
# ---------------------------------------------------------------------------

_PLACEHOLDER_SENSITIVITY = frozenset({"", "n/a", "na", "unknown", "none", "tbd", "-"})


@dataclass(frozen=True)
class AbsenceClaim:
    """The only admissible negative-space form.

    *I looked for X; my instrument would have caught X at this sensitivity; I
    found nothing; therefore X is absent above that sensitivity.* Drop the
    middle clause and "zero detections" over a population the instrument never
    covered reads as safety.
    """

    looked_for: str
    instrument: str
    sensitivity: str           # coverage the instrument can actually see
    found_nothing: bool
    coverage_complete: Optional[bool] = None


@dataclass(frozen=True)
class AbsenceValidation:
    ok: bool
    errors: List[str]

    def __bool__(self) -> bool:
        return self.ok


def validate_absence_claim(claim: AbsenceClaim) -> AbsenceValidation:
    errors: List[str] = []
    if not isinstance(claim.looked_for, str) or not claim.looked_for.strip():
        errors.append("looked_for is empty or not a string")
    if not isinstance(claim.instrument, str) or not claim.instrument.strip():
        errors.append("instrument is empty or not a string")
    if (not isinstance(claim.sensitivity, str)
            or claim.sensitivity.strip().lower() in _PLACEHOLDER_SENSITIVITY):
        errors.append("sensitivity is empty or a placeholder -- an absence "
                      "claim without instrument sensitivity is an argument "
                      "from ignorance")
    if claim.found_nothing is not True:
        errors.append("found_nothing is not True -- a positive finding is not "
                      "an absence claim")
    return AbsenceValidation(not errors, errors)


# ---------------------------------------------------------------------------
# sweep_citations -- observe-mode corpus instrument
# ---------------------------------------------------------------------------

# Conservative, prefix-anchored extraction: bounded recall, near-zero false
# extraction. Anything this regex misses is a stated blind spot, not a pass.
_CITE_RE = re.compile(
    r"(?<![\w/\\.-])"
    r"((?:src|scripts|docs|config|tests|packages|genesis|estate|identity)"
    r"/[\w./-]+\.(?:py|md|ya?ml|json|toml|ps1|sh))"
    r"(?::(\d+))?")

_EXCLUDE_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv",
                 "dist", "build", ".mypy_cache", ".pytest_cache"}

# A citation authored specifically to SAY a path does not exist must not
# accumulate as a false absence on every sweep forever. Two conservative,
# LINE-scoped tells admit the exclusion; anything else still counts as a real
# absence. Excluded rows are tallied separately and written out with their own
# causal_status -- never dropped.
_NEGATIVE_SPACE_MARKER_RE = re.compile(
    r"does\s+not\s+exist"
    r"|doesn'?t\s+exist"
    r"|did\s+not\s+exist"
    r"|never\s+(?:existed|created)"
    r"|was\s+(?:never|not)\s+created"
    r"|no\s+longer\s+exists?"
    r"|not\s+(?:yet\s+)?created"
    r"|negative[- ]space",
    re.IGNORECASE)

NEGATIVE_SPACE_BLIND_SPOTS: Tuple[str, ...] = (
    "line granularity: the strikethrough pair or marker phrase must share a "
    "LINE with the citation -- a marker one line away is invisible and the "
    "citation still counts as ABSENT",
    "fixed marker vocabulary -- novel phrasing for the same intent is not "
    "recognized and will still count as a real absence",
    "strikethrough detection is markdown ~~...~~ only; HTML del/s tags and "
    "prose descriptions are invisible",
    "a citation sharing a line with an unrelated marker phrase is classified "
    "together -- coarse by design, and only ever widens the excluded set",
    "the negative-space CLAIM itself is not verified: a path marked "
    "does-not-exist that has since come to exist is reported separately as "
    "NEGATIVE_SPACE_STALE, never silently accepted",
)


def _in_strikethrough(line: str, start: int, end: int) -> bool:
    """True when ``line[start:end]`` sits inside a markdown ``~~...~~`` pair on
    this line -- the conventional way an author marks a cited path as
    intentionally nonexistent."""
    open_idx = line.rfind("~~", 0, start)
    if open_idx == -1:
        return False
    if line.find("~~", end) == -1:
        return False
    # Reject if an unrelated pair closes BEFORE the citation starts -- that
    # opener does not actually wrap this match.
    return line.find("~~", open_idx + 2, start) == -1


def _iter_md(root: Path) -> Iterable[Path]:
    """Yield ``*.md`` under ``root``, PRUNING excluded directories during the
    walk -- never enumerating a dependency tree even to discard it."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _EXCLUDE_DIRS)
        for fn in sorted(filenames):
            if fn.endswith(".md"):
                yield Path(dirpath) / fn


def sweep_citations(roots: Iterable[Path], workspace: Path,
                    limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Walk ``*.md`` under ``roots``, extract repo-path citations, check each
    against the filesystem. Pure read: returns rows, writes nothing.

    Extraction is LINE-scoped (never whole-text) so the negative-space
    convention can be checked in the same pass.
    """
    workspace = Path(workspace)
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    for root in roots:
        root = Path(root)
        if not root.exists():
            continue
        for md in _iter_md(root):
            try:
                text = md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                rows.append({"file": str(md), "ref": "", "path": "",
                             "exists": False, "error": "unreadable"})
                continue
            for line in text.splitlines():
                marker_hit = bool(_NEGATIVE_SPACE_MARKER_RE.search(line))
                for m in _CITE_RE.finditer(line):
                    ref = m.group(1) + (f":{m.group(2)}" if m.group(2) else "")
                    key = (str(md), ref)
                    if key in seen:
                        continue
                    seen.add(key)
                    target = workspace / m.group(1)
                    struck = _in_strikethrough(line, m.start(), m.end())
                    if struck and marker_hit:
                        reason = "strikethrough+marker"
                    elif struck:
                        reason = "strikethrough"
                    elif marker_hit:
                        reason = "marker"
                    else:
                        reason = ""
                    rows.append({"file": str(md), "ref": ref,
                                 "path": str(target), "exists": target.is_file(),
                                 "negative_space": bool(struck or marker_hit),
                                 "negative_space_reason": reason})
                    if limit and len(rows) >= limit:
                        return rows
    return rows


# ---------------------------------------------------------------------------
# selftest -- a detector that has never fired is indistinguishable from a
# broken one
# ---------------------------------------------------------------------------


def _check(label: str, cond: bool, failures: List[str]) -> None:
    if not cond:
        failures.append(label)


def selftest(workspace: Optional[Path] = None) -> Tuple[bool, str]:
    """Prove every signal can fire, in both directions, plus its refusals.

    Returns ``(ok, message)``. Version-control-dependent branches of
    ``causal_edges`` are NOT exercised (module blind spot 4); the message says
    which paths were proven so the coverage is never overstated.
    """
    import tempfile

    f: List[str] = []

    # -- spread ------------------------------------------------------------
    wide = spread(["white", "cream", "grey", "black", "beige", "tan"])
    _check("spread did not flag a wide distribution", wide.flag and wide.status == "OK", f)
    narrow = spread(["white"] * 5 + ["cream"])
    _check("spread flagged a narrow distribution", not narrow.flag, f)
    _check("spread INSUFFICIENT_SAMPLES did not fire",
           spread(["a", "b"]).status == "INSUFFICIENT_SAMPLES", f)

    # -- control_probe -----------------------------------------------------
    frozen = control_probe(["white"] * 5, ["white"] * 5)
    _check("control_probe PRIOR_ONLY did not fire",
           frozen.flag and frozen.status == "PRIOR_ONLY", f)
    moved = control_probe(["navy"] * 5, ["white"] * 5)
    _check("control_probe EVIDENCE_MOVED did not fire",
           (not moved.flag) and moved.status == "EVIDENCE_MOVED", f)
    _check("control_probe INSUFFICIENT_SAMPLES did not fire",
           control_probe(["a"], ["b"]).status == "INSUFFICIENT_SAMPLES", f)

    # -- no_evidence_by_construction ---------------------------------------
    ungrounded = no_evidence_by_construction(
        "the background was cream", "a numbered list of unrelated ports")
    _check("UNGROUNDED_BY_CONSTRUCTION did not fire",
           ungrounded.flag and ungrounded.status == "UNGROUNDED_BY_CONSTRUCTION", f)
    present = no_evidence_by_construction(
        "the background was cream", "the background of the plate was cream")
    _check("CONTEXT_TERMS_PRESENT did not fire",
           (not present.flag) and present.status == "CONTEXT_TERMS_PRESENT", f)
    _check("non-str input was coerced instead of refused",
           no_evidence_by_construction(None, "x").status == "INSTRUMENT_ERROR", f)  # type: ignore[arg-type]
    _check("a claim with no content terms was not refused",
           no_evidence_by_construction("of the", "x").status == "INSTRUMENT_ERROR", f)

    # -- round_number_signature --------------------------------------------
    _check("round_number_signature did not flag uniformly round values",
           round_number_signature([1000, 2000, 500000, 30]).flag, f)
    _check("round_number_signature flagged ragged values",
           not round_number_signature([1037, 2149, 487213, 33]).flag, f)
    _check("the magnitude floor is not holding (exact small counts must not flag)",
           not round_number_signature([3, 5, 0, 7]).flag, f)
    _check("round INSUFFICIENT_SAMPLES did not fire",
           round_number_signature([10, 20]).status == "INSUFFICIENT_SAMPLES", f)
    _check("round INSTRUMENT_ERROR did not fire",
           round_number_signature(["x", "y", "z"]).status == "INSTRUMENT_ERROR", f)  # type: ignore[list-item]

    # -- causal_edges (non-git branches only; see blind spot 4) -------------
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        _check("causal_edges REF_MISSING did not fire",
               causal_edges("docs/never-written.md", ws).status == "REF_MISSING", f)
        _check("causal_edges did not refuse an empty ref",
               causal_edges("", ws).status == "INSTRUMENT_ERROR", f)
        _check("causal_edges did not refuse a ref escaping the workspace",
               causal_edges("../../etc/passwd", ws).status == "INSTRUMENT_ERROR", f)

    # -- AbsenceClaim ------------------------------------------------------
    good = AbsenceClaim("a private key shape", "a regex over every tracked file",
                        "PEM and OpenSSH headers, tracked files only", True)
    _check("a well-formed three-clause absence claim was rejected",
           bool(validate_absence_claim(good)), f)
    bad = AbsenceClaim("a private key shape", "a regex", "unknown", True)
    _check("an absence claim with a placeholder sensitivity was accepted",
           not validate_absence_claim(bad).ok, f)
    _check("an absence claim that found something was accepted",
           not validate_absence_claim(
               AbsenceClaim("x", "y", "full tree", False)).ok, f)

    # -- sweep_citations ---------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        (ws / "docs").mkdir(parents=True)
        (ws / "src").mkdir(parents=True)
        (ws / "src" / "real.py").write_text("x = 1\n", encoding="utf-8")
        (ws / "docs" / "a.md").write_text(
            "see src/real.py:12 for the loader\n"
            "the shim at ~~`src/never.py`~~ was never created\n"
            "and src/gone.py is cited plainly\n", encoding="utf-8")
        rows = sweep_citations([ws / "docs"], ws)
        by_ref = {r["ref"]: r for r in rows}
        _check("sweep missed a present citation",
               by_ref.get("src/real.py:12", {}).get("exists") is True, f)
        _check("sweep missed a plainly-absent citation",
               by_ref.get("src/gone.py", {}).get("exists") is False, f)
        _check("sweep did not exclude a struck-through negative-space citation",
               by_ref.get("src/never.py", {}).get("negative_space") is True, f)
        _check("a plainly-absent citation was wrongly marked negative space",
               by_ref.get("src/gone.py", {}).get("negative_space") is False, f)

    if f:
        return False, f"{len(f)} check(s) failed: " + "; ".join(f)
    return True, ("6 signals fire in both directions; every refusal path "
                  "(INSUFFICIENT_SAMPLES, INSTRUMENT_ERROR, placeholder "
                  "sensitivity, workspace escape) fires; the negative-space "
                  "convention excludes without hiding. NOT proven here: "
                  "causal_edges EDGES_PRESENT/ORPHAN/GIT_UNAVAILABLE, which "
                  "need a repository (module blind spot 4)")


# ---------------------------------------------------------------------------
# CLI -- observe mode; routes findings, never blocks
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None,
         edge_probe: Optional[Callable[[str, Path], CausalEdgesResult]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Observe-mode grounded-signal sweep: extract repo-path "
                    "citations from *.md and check existence plus causal "
                    "edges. Routes findings; never blocks (exit 0 unless "
                    "--strict).")
    ap.add_argument("--workspace", default=".", help="node repository root")
    ap.add_argument("--roots", nargs="*", default=list(DEFAULT_SWEEP_ROOTS))
    ap.add_argument("--edges-cap", type=int, default=50,
                    help="max DISTINCT PRESENT refs to orphan-probe (capped "
                         "LOUDLY, never silently)")
    ap.add_argument("--json-out", default="")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 when missing refs found (default: observe)")
    ap.add_argument("--selftest", action="store_true",
                    help="prove every signal can fire")
    args = ap.parse_args(argv)

    if args.selftest:
        ok, msg = selftest()
        print(f"{'PASS' if ok else 'FAIL'} grounded_signals selftest: {msg}")
        return 0 if ok else 1

    ws = Path(args.workspace).resolve()
    roots = [ws / r if not Path(r).is_absolute() else Path(r) for r in args.roots]
    rows = sweep_citations(roots, workspace=ws)
    not_present = [r for r in rows if not r.get("exists")]
    missing = [r for r in not_present if not r.get("negative_space")]
    negative_space = [r for r in not_present if r.get("negative_space")]
    stale_negative = [r for r in rows if r.get("exists") and r.get("negative_space")]

    # A missing ref needs no ancestry probe -- causal_edges would only restate
    # REF_MISSING. Spend the probe budget where ORPHAN means something.
    for r in missing:
        r["causal_status"] = "ABSENT_FROM_DISK"
    for r in negative_space:
        r["causal_status"] = "NEGATIVE_SPACE_EXCLUDED"
    for r in stale_negative:
        r["causal_status"] = "NEGATIVE_SPACE_STALE"

    present_refs: List[str] = []
    seen_refs: set = set()
    for r in rows:
        if r.get("exists") and r["ref"] not in seen_refs:
            seen_refs.add(r["ref"])
            present_refs.append(r["ref"])
    probe = edge_probe or (lambda ref, w: causal_edges(ref, w))
    probe_set = present_refs[:args.edges_cap]
    statuses = {ref: probe(ref, ws).status for ref in probe_set}
    for r in rows:
        if r["ref"] in statuses and r.get("causal_status") != "NEGATIVE_SPACE_STALE":
            r["causal_status"] = statuses[r["ref"]]
    if len(present_refs) > args.edges_cap:
        print(f"WARN orphan probe capped at {args.edges_cap} of "
              f"{len(present_refs)} distinct present refs (--edges-cap to raise)")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.json_out) if args.json_out else (
        ws / ".intentops" / "hallucination" / f"grounded-sweep-{stamp}.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=True) + "\n")
        if negative_space:
            fh.write(json.dumps({
                "_summary": "negative_space_blind_spots",
                "blind_spots": list(NEGATIVE_SPACE_BLIND_SPOTS),
            }, ensure_ascii=True) + "\n")

    orphans = sorted(ref for ref, st in statuses.items() if st == "ORPHAN")
    print(f"grounded-sweep: {len(rows)} citations checked, {len(missing)} "
          f"missing, {len(negative_space)} negative-space excluded, "
          f"{len(probe_set)} present refs orphan-probed ({len(orphans)} "
          f"ORPHAN) -> {out}")
    for ref in orphans[:10]:
        print(f"  ORPHAN  {ref} (on disk; no ancestry, no inbound references)")
    for r in missing[:20]:
        print(f"  MISSING {r['ref']}  cited in {r['file']}")
    if len(missing) > 20:
        print(f"  ... and {len(missing) - 20} more (see JSONL)")
    if stale_negative:
        print(f"WARN {len(stale_negative)} negative-space citation(s) now "
              "EXIST on disk -- stale claim, see NEGATIVE_SPACE_STALE rows")
    if args.strict and missing:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
