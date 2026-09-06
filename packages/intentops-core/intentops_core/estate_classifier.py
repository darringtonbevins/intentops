"""Which ESTATE does an item reach? -- the fact most rulings turn on.

PURPOSE
    One deterministic answer to "which estate does this item reach", so the
    ordering consult, the priors extractor and any twin context read the same
    fact and cannot drift apart. Autonomy follows the ESTATE, not the category,
    so this is a load-bearing input to authorization -- and therefore it must
    never guess.

    NO ESTATE IS NAMED IN THIS FILE. The estate ids, labels and keywords all
    come from ``estate/ESTATE-MAP.yaml`` (schema ``estate-map/v1``), which ships
    with ``entries: []`` and is filled by the node's operator at G6. What stays
    in code is only what is a law of governance rather than a fact about one
    operator's life:

      * the closed KIND vocabulary -- ``internal | venture | served | household``
      * the PRECEDENCE rule -- ``served > venture > household > internal``
      * ``unknown`` is OMITTED from a fact vocabulary, never defaulted

    Precedence resolves ambiguity toward the STRICTER estate on purpose. An
    item that pulls a served organisation's data into an internal store reaches
    that organisation's data; reading it as internal would be the permissive
    error, and the permissive error is the one that costs somebody else.

WRITE MODEL
    None -- this module reads a manifest and holds no store of its own. The
    manifest is written by the estate tooling, never here.

BLIND SPOTS
    * Keyword classification is approximate BY CONSTRUCTION. An item described
      without any of an estate's declared vocabulary reads ``unknown``, never
      the most permissive estate. A node whose map declares no keywords
      classifies nothing, which is the honest state of a blank estate.
    * A keyword shared by two estates is decided by precedence, not by intent.
      Overlapping vocabularies are a manifest defect this module surfaces
      (``EstateMap.keyword_collisions``) and never silently resolves.
    * An explicit ``estate`` field on an item is trusted. Nothing here verifies
      that the field is true.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "ESTATE_KINDS",
    "EstateMap",
    "EstateMapError",
    "EstateVerdict",
    "PRECEDENCE",
    "from_estate",
    "UNKNOWN",
    "load_estate_map",
]

#: The closed kind vocabulary. An undeclared kind is a hard exit, never a
#: default of everything-applies.
ESTATE_KINDS: Tuple[str, ...] = ("internal", "venture", "served", "household")

#: Strictest first. Ambiguity resolves toward the stricter estate.
PRECEDENCE: Tuple[str, ...] = ("served", "venture", "household", "internal")

UNKNOWN = "unknown"

_REQUIRED_ENTRY_FIELDS: Tuple[str, ...] = ("id", "label", "kind", "keywords")

#: Which item fields carry classifiable text. Deliberately a closed list.
_TEXT_FIELDS: Tuple[str, ...] = (
    "title", "summary", "proposal_summary", "description", "rationale",
    "action", "command", "scope", "id",
)


class EstateMapError(ValueError):
    """The estate map is not a map: a load-bearing field is missing or undeclared.

    Raised rather than defaulted. A reader that substitutes a default for a
    missing field has left that field out of the population, and the one field
    nobody read is the one nobody checks.
    """


@dataclass(frozen=True)
class EstateEntry:
    """One declared estate."""

    id: str
    label: str
    kind: str
    keywords: Tuple[str, ...]
    autonomy: str = ""
    ruled_by: str = ""
    as_of: str = ""


@dataclass(frozen=True)
class EstateVerdict:
    """Which estate an item reaches, and what decided it."""

    kind: str
    estate_id: str = ""
    matched: str = ""

    @property
    def known(self) -> bool:
        return self.kind != UNKNOWN

    def as_fact(self) -> Optional[Dict[str, str]]:
        """The fact-vocabulary form, or None when unknown.

        None means OMIT THE KEY, so no predicate can fire on an estate nobody
        observed.
        """
        return None if not self.known else {"estate": self.kind}


@dataclass
class EstateMap:
    """The loaded, validated estate map.

    ``entries: []`` is valid -- a node with no declared estates is new, and
    that is a true statement about it. A MISSING manifest, or an entry missing
    a load-bearing field, is a HALT.
    """

    entries: List[EstateEntry] = field(default_factory=list)
    as_of: str = ""
    source: str = ""

    # -- construction ------------------------------------------------------

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], *, source: str = "") -> "EstateMap":
        if not isinstance(data, Mapping):
            raise EstateMapError(
                f"estate map must be a mapping, got {type(data).__name__}"
            )
        if "entries" not in data:
            raise EstateMapError(
                "estate map has no 'entries' key. An empty list is valid and "
                "means 'no estates declared yet'; a missing key is a field "
                "nobody read, and is refused."
            )
        raw_entries = data.get("entries")
        if raw_entries is None:
            raw_entries = []
        if not isinstance(raw_entries, Sequence) or isinstance(raw_entries, (str, bytes)):
            raise EstateMapError("estate map 'entries' must be a list")

        entries: List[EstateEntry] = []
        seen: Dict[str, str] = {}
        for idx, raw in enumerate(raw_entries):
            if not isinstance(raw, Mapping):
                raise EstateMapError(f"entries[{idx}] is not a mapping")
            for fld in _REQUIRED_ENTRY_FIELDS:
                if fld not in raw or raw[fld] in (None, ""):
                    raise EstateMapError(
                        f"entries[{idx}] is missing required field {fld!r}. "
                        "Missing means HALT, never a default."
                    )
            kind = str(raw["kind"]).strip().lower()
            if kind not in ESTATE_KINDS:
                raise EstateMapError(
                    f"entries[{idx}] declares kind {raw['kind']!r}, which is not "
                    f"one of {ESTATE_KINDS}. An undeclared kind is a hard exit."
                )
            eid = str(raw["id"]).strip()
            if eid in seen:
                raise EstateMapError(f"duplicate estate id {eid!r}")
            seen[eid] = kind
            kws = raw["keywords"]
            if isinstance(kws, (str, bytes)) or not isinstance(kws, Sequence):
                raise EstateMapError(
                    f"entries[{idx}] 'keywords' must be a list of strings"
                )
            keywords = tuple(
                str(k).strip().lower() for k in kws if str(k).strip()
            )
            if not keywords:
                raise EstateMapError(
                    f"entries[{idx}] declares an empty keyword list. An estate "
                    "with no vocabulary can never be classified; declare its "
                    "words or remove the entry."
                )
            entries.append(
                EstateEntry(
                    id=eid,
                    label=str(raw["label"]),
                    kind=kind,
                    keywords=keywords,
                    autonomy=str(raw.get("autonomy", "") or ""),
                    ruled_by=str(raw.get("ruled_by", "") or ""),
                    as_of=str(raw.get("as_of", "") or ""),
                )
            )
        return cls(entries=entries, as_of=str(data.get("as_of", "") or ""), source=source)

    # -- inspection --------------------------------------------------------

    def keyword_collisions(self) -> List[Tuple[str, List[str]]]:
        """Keywords declared by more than one estate, with the ids that claim them.

        Surfaced, never resolved: an overlapping vocabulary means precedence is
        deciding something the operator thinks they decided.
        """
        owners: Dict[str, List[str]] = {}
        for e in self.entries:
            for k in e.keywords:
                owners.setdefault(k, []).append(e.id)
        return sorted((k, v) for k, v in owners.items() if len(v) > 1)

    def by_kind(self, kind: str) -> List[EstateEntry]:
        return [e for e in self.entries if e.kind == kind]

    # -- classification ----------------------------------------------------

    def classify_text(self, text: str) -> EstateVerdict:
        """Classify free text. Precedence decides when several estates match."""
        haystack = (text or "").lower()
        if not haystack.strip():
            return EstateVerdict(UNKNOWN)
        for kind in PRECEDENCE:
            for entry in self.by_kind(kind):
                for kw in entry.keywords:
                    if _keyword_hit(haystack, kw):
                        return EstateVerdict(kind, entry.id, kw)
        return EstateVerdict(UNKNOWN)

    def classify_item(self, item: Mapping[str, Any]) -> EstateVerdict:
        """Classify an approval / actuation item.

        An explicit ``estate`` field wins -- matched against declared estate ids
        first, then against the kind vocabulary. An explicit value that is
        NEITHER is a HALT: a caller naming an estate this node has never heard
        of is a question, not a default.
        """
        explicit = str(item.get("estate") or "").strip().lower()
        if explicit and explicit != UNKNOWN:
            for entry in self.entries:
                if entry.id.lower() == explicit:
                    return EstateVerdict(entry.kind, entry.id, "explicit")
            if explicit in ESTATE_KINDS:
                return EstateVerdict(explicit, "", "explicit")
            raise EstateMapError(
                f"item declares estate {explicit!r}, which is neither a declared "
                f"estate id nor one of {ESTATE_KINDS}"
            )
        text = " ".join(str(item.get(k) or "") for k in _TEXT_FIELDS)
        return self.classify_text(text)

    def fact_for(self, item: Mapping[str, Any]) -> Optional[Dict[str, str]]:
        """``{"estate": kind}`` or None. None means omit the key."""
        return self.classify_item(item).as_fact()


def _keyword_hit(haystack: str, keyword: str) -> bool:
    """Word-boundary-ish match, hyphen/underscore tolerant.

    An anchored ``\\b`` alone misses a keyword sitting inside an identifier,
    and a bare substring match hits inside unrelated words. Normalising the
    separators and then anchoring is the middle path: ``estate-map`` and
    ``estate_map`` both match the keyword ``estate map``.
    """
    norm_hay = re.sub(r"[-_/\\.]+", " ", haystack)
    norm_kw = re.sub(r"[-_/\\.]+", " ", keyword.strip())
    if not norm_kw:
        return False
    return re.search(r"(?<!\w)" + re.escape(norm_kw) + r"(?!\w)", norm_hay) is not None


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------


def load_estate_map(path: Path) -> EstateMap:
    """Load and validate ``estate/ESTATE-MAP.yaml``.

    A missing file HALTs -- an absent manifest is not an empty estate.
    """
    path = Path(path)
    if not path.exists():
        raise EstateMapError(
            f"estate map not found: {path}. 'entries: []' is a valid empty "
            "estate; a MISSING manifest is a field nobody read."
        )
    return EstateMap.from_mapping(_load_yaml_mapping(path), source=str(path))


def from_estate(estate: Any, filename: str = "ESTATE-MAP.yaml") -> EstateMap:
    """Build from an already-loaded ``estate.manifests.Estate``.

    This is the preferred path when the caller has loaded the whole estate:
    the estate loader owns manifest validation (schema, vocabularies, as-of),
    and this module owns only the classification RULES. Two loaders reading the
    same file is how two definitions of "valid" drift apart.
    """
    try:
        loaded = estate.manifests[filename]
    except (AttributeError, KeyError, TypeError) as exc:
        raise EstateMapError(
            f"{filename} is not present in the loaded estate: {exc}"
        ) from exc
    data = {"as_of": getattr(loaded, "as_of", ""),
            "entries": list(getattr(loaded, "entries", ()))}
    return EstateMap.from_mapping(data, source=str(getattr(loaded, "path", filename)))


def _load_yaml_mapping(path: Path) -> Mapping[str, Any]:
    """Minimal fallback loader.

    Requires PyYAML. Its absence RAISES rather than degrading to a partial
    hand-rolled parser: a manifest half-understood produces a classifier that
    runs and is wrong, which is the exact failure this package refuses.
    """
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise EstateMapError(
            "cannot read the estate map: PyYAML is not installed and no estate "
            "manifest loader is available. Install the manifest reader rather "
            "than parsing the file approximately."
        ) from exc
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        raise EstateMapError(f"estate map is empty: {path}")
    return data


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------

_SELFTEST_MAP: Dict[str, Any] = {
    "schema": "estate-map/v1",
    "as_of": "selftest",
    "entries": [
        {"id": "node", "label": "This node", "kind": "internal",
         "keywords": ["gate", "ordering store"]},
        {"id": "side-project", "label": "A venture", "kind": "venture",
         "keywords": ["side project", "prototype"]},
        {"id": "counterparty", "label": "A served organisation", "kind": "served",
         "keywords": ["engagement", "side project"]},
        {"id": "home", "label": "The household", "kind": "household",
         "keywords": ["home network"]},
    ],
}


def selftest() -> int:
    """Prove every classification path and every refusal can fire. 0 = pass."""
    failures: List[str] = []

    def check(label: str, cond: bool) -> None:
        if not cond:
            failures.append(label)

    em = EstateMap.from_mapping(_SELFTEST_MAP, source="selftest")

    check("internal keyword classifies", em.classify_text("edit the gate").kind == "internal")
    check("venture keyword classifies",
          em.classify_text("ship the prototype").kind == "venture")
    check("served keyword classifies",
          em.classify_text("the engagement review").kind == "served")
    check("household keyword classifies",
          em.classify_text("home network change").kind == "household")
    check("precedence: served outranks venture",
          em.classify_text("side project").kind == "served")
    check("no vocabulary reads unknown", em.classify_text("a plain sentence").kind == UNKNOWN)
    check("unknown omits the fact", em.classify_text("a plain sentence").as_fact() is None)
    check("collisions are surfaced", em.keyword_collisions() == [("side project",
                                                                 ["side-project", "counterparty"])])

    # refusals
    for label, bad in (
        ("missing entries key", {"schema": "estate-map/v1"}),
        ("undeclared kind", {"entries": [{"id": "a", "label": "A", "kind": "cloud",
                                          "keywords": ["x"]}]}),
        ("missing field", {"entries": [{"id": "a", "label": "A", "kind": "internal"}]}),
        ("empty keywords", {"entries": [{"id": "a", "label": "A", "kind": "internal",
                                         "keywords": []}]}),
        ("duplicate id", {"entries": [
            {"id": "a", "label": "A", "kind": "internal", "keywords": ["x"]},
            {"id": "a", "label": "A2", "kind": "venture", "keywords": ["y"]}]}),
    ):
        try:
            EstateMap.from_mapping(bad)
        except EstateMapError:
            pass
        else:
            failures.append(f"refusal did not fire: {label}")

    try:
        em.classify_item({"estate": "atlantis"})
    except EstateMapError:
        pass
    else:
        failures.append("refusal did not fire: undeclared explicit estate")

    print("estate_classifier selftest:")
    for f in failures:
        print(f"  FAIL: {f}")
    print(f"  {'PASS' if not failures else 'FAIL'} ({len(failures)} failure(s))")
    return 1 if failures else 0


def _main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="estate classification over the estate map")
    ap.add_argument("--selftest", action="store_true",
                    help="prove every classification path and refusal can fire")
    ap.add_argument("--map", type=Path, help="path to estate/ESTATE-MAP.yaml")
    ap.add_argument("--text", help="classify this text against --map")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.map is None:
        ap.error("--map is required (no default: a node names its own estate map)")
    em = load_estate_map(args.map)
    collisions = em.keyword_collisions()
    print(f"estate map: {len(em.entries)} entr(ies), as_of={em.as_of or 'UNSTATED'}")
    for kw, owners in collisions:
        print(f"  COLLISION: {kw!r} claimed by {', '.join(owners)}")
    if args.text:
        v = em.classify_text(args.text)
        print(f"  verdict: {v.kind} (estate={v.estate_id or '-'}, matched={v.matched or '-'})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
