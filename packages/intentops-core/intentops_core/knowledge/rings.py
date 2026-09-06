"""Knowledge rings -- the sensitivity classification, and the quarantine default.

PURPOSE
    Every piece of knowledge a node holds carries one RING (R0 innermost to R4
    public, or Q for quarantine), and every consumer of that knowledge carries
    a SYSTEM EXPOSURE describing what kind of system is about to see it.
    :func:`resolve` combines them into the ring that actually governs the
    exchange: the stricter of "how sensitive is this content" and "how much
    exposure will this channel tolerate" always wins.

    The load-bearing property, and the reason this module is short:

        A MISSING RING KEY RESOLVES TO QUARANTINE, NEVER TO A RING.

    Missing and quarantined are the same answer at the gate. That is why the
    coverage oracle (:mod:`coverage`) reports key-presence and classification
    as two numbers and never sums them -- stamping every unringed row with a
    literal ``Q`` would take key-presence to 100% while changing nothing
    anybody can retrieve.

    The MECHANISM here is estate-independent and ships. The MAPPING -- which
    source types are which ring -- is estate, and ships EMPTY
    (``config/ring-taxonomy.template.yaml``): a taxonomy with no rows
    classifies nothing, so a fresh node quarantines everything until its
    operator rules otherwise. That is the safe direction, and it is the one
    genuine advantage a new node has over a migration: a blank estate can run
    the gate in ``enforce`` from birth.

WRITE MODEL
    None here. The node's own taxonomy copy
    (``.intentops/knowledge/ring-taxonomy.yaml``, created at birth) is
    single-writer-ceremony: the operator edits it, one process at a time, and
    this module only ever reads it.

BLIND SPOTS
    - Classification is by DECLARED ``source_type``. This module never reads
      content. A source type declared into the wrong ring is invisible here.
    - The taxonomy is a flat map. There is no inheritance, no glob, and no
      precedence rule, on purpose: every one of those is a place where a
      reader and a writer can disagree about which row won.
    - ``resolve`` governs a HANDOFF. It says nothing about what a consumer
      does after it has the content, and it is not a redaction engine.
    - The gate mode is read per call from the environment. A process that
      cached a mode at import would keep enforcing after an operator switched
      it off; nothing here caches, but a caller that does re-introduces that.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from . import GATE_MODES, KnowledgeHalt, gate_mode

__all__ = [
    "Ring",
    "SystemExposure",
    "CallerContext",
    "RING_META_KEY",
    "EXPOSURE_CEILING",
    "RingTaxonomy",
    "ring_from_metadata",
    "resolve",
    "allowed",
    "stricter",
    "load_taxonomy",
    "blank_taxonomy_document",
    "ring_gate_mode",
    "RING_GATE_ENV",
    "selftest",
    "main",
]

#: The metadata key a row carries its ring under. One key, one spelling.
RING_META_KEY = "ring"

#: Environment variable resolving the ring gate's mode (observe/enforce/off).
RING_GATE_ENV = "INTENTOPS_RING_GATE"


class Ring(str, Enum):
    """Sensitivity ring, innermost to outermost, plus quarantine.

    R0  the node's own self-model and anything credentials-adjacent
    R1  sensitive operational knowledge: scoped, strategic, pre-decision
    R2  internal, non-sensitive operational knowledge
    R3  broadly shareable internal content
    R4  public, or already-public-safe
    Q   QUARANTINE -- unclassified or external-origin, pending a ruling.
        Treated at least as strictly as R0 at every gate.
    """

    R0 = "R0"
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"
    R4 = "R4"
    QUARANTINE = "Q"


class SystemExposure(str, Enum):
    """What kind of system is about to consume the content."""

    LOCAL_ONLY = "LOCAL_ONLY"
    IN_FAMILY_MODEL = "IN_FAMILY_MODEL"
    THIRD_PARTY_MODEL = "THIRD_PARTY_MODEL"
    ADVERSARIAL = "ADVERSARIAL"


@dataclass(frozen=True)
class CallerContext:
    """Who is asking. ``inner_voice`` is what gates the innermost rings."""

    inner_voice: bool = False
    surface: str = "unknown"


#: Restrictiveness order, MOST restrictive first. Quarantine outranks every
#: named ring, which is what makes "missing" the safe answer.
_ORDER: Tuple[Ring, ...] = (
    Ring.QUARANTINE, Ring.R0, Ring.R1, Ring.R2, Ring.R3, Ring.R4,
)
_RANK: Dict[Ring, int] = {ring: i for i, ring in enumerate(_ORDER)}

#: The LEAST restrictive ring each exposure is cleared for. Read a row as a
#: clearance threshold, never as a value to blend with the content's own ring:
#: a plain ``min()`` would "tighten" public R4 content to R0 merely for being
#: read locally, which is backwards.
EXPOSURE_CEILING: Dict[SystemExposure, Ring] = {
    SystemExposure.LOCAL_ONLY: Ring.R0,
    SystemExposure.IN_FAMILY_MODEL: Ring.R2,
    SystemExposure.THIRD_PARTY_MODEL: Ring.R3,
    SystemExposure.ADVERSARIAL: Ring.R4,
}

#: Rings that require an inner-voice caller. Quarantine is here because
#: unclassified content is treated as if it were the innermost ring.
_INNER_VOICE_ONLY = frozenset({Ring.R0, Ring.R1, Ring.QUARANTINE})


def stricter(left: Ring, right: Ring) -> Ring:
    """The more restrictive of two rings."""
    return left if _RANK[left] <= _RANK[right] else right


def ring_from_metadata(meta: Optional[Mapping[str, Any]]) -> Ring:
    """Extract a ring from a metadata mapping. Missing or invalid -> QUARANTINE.

    Fail-closed by construction. A row with no ring key, a ring key of
    ``None``, a misspelling, or a value of the wrong type is quarantined --
    never silently promoted to a middle ring on the theory that most content
    is ordinary.
    """
    if not meta:
        return Ring.QUARANTINE
    raw = meta.get(RING_META_KEY)
    if raw is None:
        return Ring.QUARANTINE
    if isinstance(raw, Ring):
        return raw
    try:
        return Ring(str(raw).strip())
    except ValueError:
        return Ring.QUARANTINE


def resolve(ring: Ring, exposure: SystemExposure) -> Ring:
    """The ring that governs this handoff, or QUARANTINE when it may not happen.

    Content ring ``C`` is cleared for exposure ``E`` iff ``C`` is no stricter
    than ``E``'s ceiling; then ``C`` passes through unchanged. Otherwise the
    content is more sensitive than ``E`` may ever see and the answer is
    QUARANTINE -- a hard, visible refusal, never a quietly loosened value.
    """
    ceiling = EXPOSURE_CEILING[exposure]
    if _RANK[ring] >= _RANK[ceiling]:
        return ring
    return Ring.QUARANTINE


def allowed(ring: Ring, ctx: CallerContext) -> bool:
    """The cheaper caller-context check, ahead of any exposure arithmetic."""
    if ring in _INNER_VOICE_ONLY:
        return bool(ctx.inner_voice)
    return True


def ring_gate_mode(environ: Optional[Mapping[str, str]] = None) -> str:
    """observe (default) | enforce | off, resolved per call.

    Raises :class:`KnowledgeHalt` when the variable is set to something
    outside that vocabulary; see :func:`intentops_core.knowledge.gate_mode`
    for why a typo is not coerced.
    """
    return gate_mode(RING_GATE_ENV, environ)


# ---------------------------------------------------------------------------
# the taxonomy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RingTaxonomy:
    """A loaded, validated source_type -> ring map.

    ``default_ring`` is what an UNLISTED source type resolves to. It is
    declared in the document rather than assumed, and a document declaring
    anything but ``Q`` is loud about it in :meth:`describe` -- a default that
    is not quarantine is a policy decision, not a formatting choice.
    """

    schema: str
    as_of: str
    default_ring: Ring
    mapping: Dict[str, Ring]
    source_path: Optional[str] = None

    def ring_for(self, source_type: Optional[str]) -> Ring:
        """Ring for a source type. Unlisted, blank, or None -> the default."""
        if not source_type:
            return self.default_ring
        return self.mapping.get(str(source_type).strip(), self.default_ring)

    def declared_ring_for(self, source_type: Optional[str]) -> Optional[Ring]:
        """The EXPLICIT ring for a source type, or ``None`` when unlisted.

        :meth:`ring_for` folds "unlisted" into ``default_ring``, which is
        right at a READ gate -- where the only safe answer for content nobody
        ruled on is the default -- and wrong at a WRITE gate, which must be
        able to tell *the operator ruled on this source type* from *the
        operator has said nothing*. Those two resolve differently against a
        collection's own declared sensitivity: an explicit ruling participates
        in the strictest-wins merge, while silence leaves the collection's
        declaration standing rather than quarantining every row on a node
        whose taxonomy is still blank.
        """
        if not source_type:
            return None
        return self.mapping.get(str(source_type).strip())

    def describe(self) -> str:
        note = "" if self.default_ring is Ring.QUARANTINE else (
            f"  WARNING: default_ring is {self.default_ring.value}, not Q -- "
            "unlisted content is NOT quarantined by this taxonomy")
        return (f"ring taxonomy: {len(self.mapping)} declared source types, "
                f"default {self.default_ring.value}" + (f"\n{note}" if note else ""))


def _halt(message: str, remedy: str) -> "KnowledgeHalt":
    return KnowledgeHalt(message, remedy)


def load_taxonomy(path: Path | str) -> RingTaxonomy:
    """Load and validate a ring taxonomy. Every failure is a HALT.

    HALTS on: a missing file; unparseable YAML; a missing ``schema``,
    ``as_of``, ``default_ring`` or ``mapping`` key; a mapping that is not a
    mapping; a ring value outside the closed vocabulary; a duplicate source
    type; a blank source type. There is no default for any of these, because a
    taxonomy the loader half-understands produces a classification that runs
    and is wrong.
    """
    import yaml

    p = Path(path)
    if not p.is_file():
        raise _halt(
            f"ring taxonomy not found: {p}",
            "genesis writes one at birth from "
            "config/ring-taxonomy.template.yaml; restore it, or point the "
            "loader at the node's own copy")
    try:
        doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - any parse failure is one halt
        raise _halt(f"ring taxonomy is unparseable: {p} ({exc})",
                    "fix the YAML; an unreadable taxonomy classifies nothing "
                    "and must not be treated as an empty one") from exc
    if not isinstance(doc, Mapping):
        raise _halt(f"ring taxonomy is not a mapping: {p}",
                    "the document is a mapping with schema, as_of, "
                    "default_ring and mapping keys")

    for key in ("schema", "as_of", "default_ring", "mapping"):
        if key not in doc:
            raise _halt(f"ring taxonomy is missing `{key}`: {p}",
                        f"add `{key}`. Where a field is load-bearing, missing "
                        "means HALT, not a default")
        if doc[key] is None and key != "mapping":
            raise _halt(f"ring taxonomy `{key}` is null: {p}",
                        f"give `{key}` a value; null is not an answer here")

    try:
        default_ring = Ring(str(doc["default_ring"]).strip())
    except ValueError:
        raise _halt(
            f"undeclared ring {doc['default_ring']!r} for `default_ring`: {p}",
            "use one of: " + ", ".join(r.value for r in Ring)
            + ". An undeclared value is a hard exit, never a default of "
              "everything-applies") from None

    raw_map = doc["mapping"]
    if raw_map is None:
        raw_map = {}
    if not isinstance(raw_map, Mapping):
        raise _halt(
            f"ring taxonomy `mapping` is a {type(raw_map).__name__}, not a "
            f"mapping: {p}",
            "mapping is `source_type: ring` pairs; an empty taxonomy is "
            "`mapping: {}`, which is a true statement about a new node")

    mapping: Dict[str, Ring] = {}
    for raw_key, raw_value in raw_map.items():
        source_type = str(raw_key).strip()
        if not source_type:
            raise _halt(f"blank source type in ring taxonomy: {p}",
                        "every row names a source type; a blank key can never "
                        "be matched and hides a real row")
        if source_type in mapping:
            raise _halt(f"duplicate source type {source_type!r} in {p}",
                        "one row per source type; a duplicate means two "
                        "authors disagreed and only one row is being read")
        try:
            mapping[source_type] = Ring(str(raw_value).strip())
        except ValueError:
            raise _halt(
                f"undeclared ring {raw_value!r} for source type "
                f"{source_type!r}: {p}",
                "use one of: " + ", ".join(r.value for r in Ring)
                + ". An undeclared value is a hard exit") from None

    return RingTaxonomy(schema=str(doc["schema"]), as_of=str(doc["as_of"]),
                        default_ring=default_ring, mapping=mapping,
                        source_path=str(p))


def blank_taxonomy_document(as_of: str) -> str:
    """An empty-and-valid taxonomy, generated from this module's vocabulary.

    Used at birth when the shipped template cannot be found. Generated rather
    than embedded so a ring added to :class:`Ring` cannot leave a stale
    document behind describing a ladder that no longer exists.
    """
    ladder = "\n".join(f"#   {r.value:<2} -- {desc}" for r, desc in (
        (Ring.R0, "innermost: the node's own self-model, credentials-adjacent"),
        (Ring.R1, "sensitive operational knowledge"),
        (Ring.R2, "internal, non-sensitive operational knowledge"),
        (Ring.R3, "broadly shareable internal content"),
        (Ring.R4, "public, or already-public-safe"),
        (Ring.QUARANTINE, "unclassified or external-origin, pending a ruling"),
    ))
    return (
        "# The node's own ring taxonomy. GENERATED EMPTY AT BIRTH.\n"
        "#\n"
        "# WRITE MODEL: single-writer-ceremony -- the operator edits this file\n"
        "# by hand, one process at a time. Nothing appends to it.\n"
        "#\n"
        "# A source type that is not listed below resolves to `default_ring`,\n"
        "# which is Q: unclassified content is quarantined, never promoted.\n"
        "#\n" + ladder + "\n"
        "#\n"
        "# EXAMPLE ROW (fictional):  public-docs: R4\n"
        "\n"
        "schema: ring-taxonomy/v1\n"
        f"as_of: \"{as_of}\"\n"
        "default_ring: Q\n"
        "mapping: {}\n"
    )


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


def selftest() -> Tuple[bool, str]:
    """Prove the quarantine default, the ceiling staircase, and every HALT."""
    import tempfile

    failures: List[str] = []
    fired: List[str] = []

    def check(label: str, cond: bool) -> None:
        (fired if cond else failures).append(label)

    # 1. the quarantine default, four ways
    check("no-metadata-quarantines", ring_from_metadata(None) is Ring.QUARANTINE)
    check("empty-metadata-quarantines", ring_from_metadata({}) is Ring.QUARANTINE)
    check("missing-key-quarantines",
          ring_from_metadata({"source_type": "x"}) is Ring.QUARANTINE)
    check("null-ring-quarantines",
          ring_from_metadata({RING_META_KEY: None}) is Ring.QUARANTINE)
    check("invalid-ring-quarantines",
          ring_from_metadata({RING_META_KEY: "R9"}) is Ring.QUARANTINE)
    check("valid-ring-survives",
          ring_from_metadata({RING_META_KEY: "R2"}) is Ring.R2)

    # 2. the staircase: each exposure unlocks exactly one more ring
    expected = {
        (Ring.QUARANTINE, SystemExposure.LOCAL_ONLY): Ring.QUARANTINE,
        (Ring.R0, SystemExposure.LOCAL_ONLY): Ring.R0,
        (Ring.R0, SystemExposure.IN_FAMILY_MODEL): Ring.QUARANTINE,
        (Ring.R2, SystemExposure.IN_FAMILY_MODEL): Ring.R2,
        (Ring.R2, SystemExposure.THIRD_PARTY_MODEL): Ring.QUARANTINE,
        (Ring.R4, SystemExposure.ADVERSARIAL): Ring.R4,
        (Ring.R3, SystemExposure.ADVERSARIAL): Ring.QUARANTINE,
    }
    check("exposure-staircase",
          all(resolve(r, e) is want for (r, e), want in expected.items()))
    check("public-content-is-not-tightened-by-a-local-read",
          resolve(Ring.R4, SystemExposure.LOCAL_ONLY) is Ring.R4)

    # 3. inner-voice gating
    outer, inner = CallerContext(False), CallerContext(True)
    check("quarantine-needs-inner-voice", not allowed(Ring.QUARANTINE, outer))
    check("r0-needs-inner-voice", not allowed(Ring.R0, outer))
    check("r2-is-open-to-outer-voice", allowed(Ring.R2, outer))
    check("inner-voice-sees-r0", allowed(Ring.R0, inner))

    # 4. the loader HALTs, one refusal at a time
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        def halts(label: str, text: Optional[str]) -> None:
            p = root / f"{label}.yaml"
            if text is not None:
                p.write_text(text, encoding="utf-8")
            try:
                load_taxonomy(p)
            except KnowledgeHalt:
                fired.append(f"halt-{label}")
            else:
                failures.append(f"halt-{label}")

        halts("missing-file", None)
        halts("unparseable", "mapping: [:\n")
        halts("not-a-mapping", "- a\n- b\n")
        halts("no-default-ring",
              "schema: ring-taxonomy/v1\nas_of: \"2026-01-01\"\nmapping: {}\n")
        halts("no-mapping-key",
              "schema: ring-taxonomy/v1\nas_of: \"2026-01-01\"\ndefault_ring: Q\n")
        halts("undeclared-default",
              "schema: ring-taxonomy/v1\nas_of: \"2026-01-01\"\n"
              "default_ring: R7\nmapping: {}\n")
        halts("undeclared-ring-value",
              "schema: ring-taxonomy/v1\nas_of: \"2026-01-01\"\n"
              "default_ring: Q\nmapping:\n  a-source: PUBLIC\n")
        halts("mapping-is-a-list",
              "schema: ring-taxonomy/v1\nas_of: \"2026-01-01\"\n"
              "default_ring: Q\nmapping:\n  - a-source\n")

        # 5. an empty-and-valid taxonomy loads and quarantines everything
        blank = root / "blank.yaml"
        blank.write_text(blank_taxonomy_document("2026-01-01"), encoding="utf-8")
        tax = load_taxonomy(blank)
        check("blank-taxonomy-loads", tax.mapping == {})
        check("blank-taxonomy-quarantines-everything",
              tax.ring_for("anything") is Ring.QUARANTINE
              and tax.ring_for(None) is Ring.QUARANTINE)

        # 6. a populated taxonomy classifies only what it declares
        pop = root / "pop.yaml"
        pop.write_text("schema: ring-taxonomy/v1\nas_of: \"2026-01-01\"\n"
                       "default_ring: Q\nmapping:\n  public-docs: R4\n",
                       encoding="utf-8")
        tax = load_taxonomy(pop)
        check("declared-source-type-classified",
              tax.ring_for("public-docs") is Ring.R4)
        check("undeclared-source-type-quarantined",
              tax.ring_for("operator-notes") is Ring.QUARANTINE)

    # 7. the mode never falls open, and a typo is never coerced
    try:
        ring_gate_mode({RING_GATE_ENV: "OFFF"})
    except KnowledgeHalt:
        fired.append("unknown-mode-halts-it-is-not-coerced")
    else:
        failures.append("unknown-mode-halts-it-is-not-coerced")
    check("mode-default-is-observe", ring_gate_mode({}) == "observe")
    check("empty-mode-is-observe", ring_gate_mode({RING_GATE_ENV: ""}) == "observe")
    check("enforce-is-readable", ring_gate_mode({RING_GATE_ENV: "enforce"})
          == "enforce")
    check("modes-are-closed", set(GATE_MODES) == {"observe", "enforce", "off"})

    report = (f"rings selftest: {len(fired)} paths fired, {len(failures)} failed"
              + (" -- " + ", ".join(failures) if failures else ""))
    return (not failures, report)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="knowledge rings")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--taxonomy", help="load and describe a taxonomy file")
    args = ap.parse_args(argv)
    if args.selftest:
        ok, report = selftest()
        print(report)
        return 0 if ok else 1
    if args.taxonomy:
        tax = load_taxonomy(args.taxonomy)
        print(tax.describe())
        return 0
    print(json.dumps({
        "rings": [r.value for r in Ring],
        "exposures": [e.value for e in SystemExposure],
        "ceilings": {e.value: r.value for e, r in EXPOSURE_CEILING.items()},
        "missing_key_resolves_to": Ring.QUARANTINE.value,
        "gate_mode": ring_gate_mode(),
    }, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
