"""The absorber contract -- and the refusal that stays in the denominator.

PURPOSE
    An absorber turns something outside the node into something inside it. The
    contract here is small on purpose; what it is careful about is the two
    ways absorption lies:

    1. **Silent partial success.** A source that was fetched, stored, and
       never indexed reports "absorbed" while returning nothing to a search.
       So :class:`AbsorptionResult` carries an explicit ``indexing_status``
       from a closed vocabulary, and ``zero`` is a distinct state from ``ok``:
       zero output is a state, not a success.
    2. **Leaving the population.** A source that could not be read at all is
       the one most likely to vanish from the count -- and a run that drops
       its refusals reports a better number than the one that records them.
       So :meth:`AbsorptionResult.refusal` produces a REAL result, and
       :func:`summarise` refuses to publish a total that does not equal the
       number of sources attempted.

    The third thing it is careful about is **depth**. An absorber that reads
    only the artifact -- and not the ecosystem around it: the sources it drew
    on, the criticism, the retellings, the people who carried it forward --
    has absorbed shallowly. That is not a defect to be fixed by a bigger
    model; it is a POLICY that must be declared. ``ECOSYSTEM_RINGS`` is that
    declaration, and an absorber that declares only the artifact says so in
    every result it returns rather than appearing complete.

WRITE MODEL
    Two stores, both per-absorber, both under the node root:

    * the content store (``<node>/.intentops/knowledge/absorbed/<platform>/``)
      -- per-item files, collision-safe by construction: one file per absorbed
      item, named by content id.
    * the watermark (``<node>/.intentops/knowledge/watermarks/<platform>.json``)
      -- locked fresh-read RMW (``StoreLock`` + ``atomic_replace``), because
      two absorbers on one platform would otherwise both read the old mark and
      both write past each other.

BLIND SPOTS
    - The watermark is a POSITION, not a proof. It says where an absorber got
      to, not that everything before it landed; that is what
      :mod:`coverage`'s corpus check is for, and the two are deliberately
      separate questions.
    - ``indexing_status`` is reported by the indexer the caller passes in. An
      indexer that lies is not detectable here.
    - The ABC has no network code and no parser. A real absorber's fetch and
      its parse are where most failures live, and neither is inherited.
    - ``ECOSYSTEM_RINGS`` is a declaration, not an audit. Nothing checks that
      an absorber claiming the ecosystem ring actually reaches one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import (Any, Callable, Dict, Iterable, List, Optional,
                    Sequence, Tuple)

from ..store_guard import StoreLock, atomic_replace, lock_for
from . import KnowledgeHalt

__all__ = [
    "INDEXING_STATUSES",
    "ECOSYSTEM_RINGS",
    "ExtractedContent",
    "AbsorptionResult",
    "Watermark",
    "read_watermark",
    "advance_watermark",
    "Absorber",
    "FileAbsorber",
    "summarise",
    "selftest",
    "main",
]

#: What happened to the INDEX half of an absorption. Closed by declaration.
#:
#: ``ok``          content reached the index
#: ``zero``        the indexer ran and produced nothing -- a state, not a success
#: ``disabled``    intentionally skipped (no indexer was supplied): quiet is fine
#: ``unavailable`` the indexer could not be reached: loud
#: ``failed``      the indexer ran and errored: loud
#: ``not-attempted`` extraction never succeeded, so indexing was never reached
INDEXING_STATUSES: Tuple[str, ...] = (
    "ok", "zero", "disabled", "unavailable", "failed", "not-attempted",
)

#: How deep an absorber declares it goes. Closed by declaration; an absorber
#: naming anything else HALTs at construction rather than silently absorbing
#: less than it claims.
#:
#: ``artifact``  ring 1: the work itself
#: ``ecosystem`` ring 2: what surrounds it -- sources, scholarship, criticism,
#:               retellings, lectures, the people who carried it forward
ECOSYSTEM_RINGS: Tuple[str, ...] = ("artifact", "ecosystem")

_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class ExtractedContent:
    """What an absorber got out of one source."""

    content_id: str
    title: str = ""
    text: str = ""
    source: str = ""
    segments: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    extracted_at: str = field(default_factory=_now)

    @property
    def word_count(self) -> int:
        return len(self.text.split())


@dataclass
class AbsorptionResult:
    """The outcome of absorbing ONE source, refusals included.

    A refusal is a result. It carries ``success=False`` and a reason, and it
    is counted by :func:`summarise` exactly like a success -- because an
    instrument whose failures leave the population reports a better number
    the worse it is doing.
    """

    platform: str
    source: str
    content_id: str = ""
    storage_path: str = ""
    word_count: int = 0
    indexed_chunks: int = 0
    indexing_status: str = "not-attempted"
    ecosystem_rings: Tuple[str, ...] = ("artifact",)
    success: bool = True
    error: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    absorbed_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        if self.indexing_status not in INDEXING_STATUSES:
            raise KnowledgeHalt(
                f"undeclared indexing status {self.indexing_status!r}",
                "use one of: " + ", ".join(INDEXING_STATUSES)
                + ". An undeclared value is a hard exit, never a default")

    @classmethod
    def refusal(cls, platform: str, source: str, error: str,
                **kw: Any) -> "AbsorptionResult":
        """A source that could not be absorbed -- still a result, still counted."""
        if not error:
            raise KnowledgeHalt(
                "a refusal with no reason is indistinguishable from a success",
                "pass the reason the source could not be absorbed")
        return cls(platform=platform, source=source, success=False, error=error,
                   indexing_status=kw.pop("indexing_status", "not-attempted"),
                   **kw)

    @property
    def depth_note(self) -> str:
        """What this result did NOT reach, stated so it cannot look complete."""
        if "ecosystem" in self.ecosystem_rings:
            return "artifact and ecosystem"
        return ("artifact only -- the ecosystem around this source "
                "(its sources, criticism, retellings, heirs) was NOT absorbed")

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["ecosystem_rings"] = list(self.ecosystem_rings)
        d["depth_note"] = self.depth_note
        return d


# ---------------------------------------------------------------------------
# watermarks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Watermark:
    """Where an absorber got to on one platform.

    ``position`` is an opaque, absorber-defined string ordered by
    ``comparable`` -- a timestamp, a sequence number, a cursor. The store
    keeps both so a reader that does not know the platform's ordering can
    still tell whether a proposed advance goes backwards.
    """

    platform: str
    position: str
    comparable: float
    updated_at: str
    runs: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _watermark_path(node_root: Path | str, platform: str) -> Path:
    safe = _SAFE_ID.sub("-", str(platform)).strip("-") or "unnamed"
    return (Path(node_root) / ".intentops" / "knowledge" / "watermarks"
            / f"{safe}.json")


def read_watermark(node_root: Path | str, platform: str) -> Optional[Watermark]:
    """The stored mark, or None when this platform has never been absorbed.

    A CORRUPT mark is a HALT, never a None: silently treating an unreadable
    watermark as "never absorbed" re-absorbs a whole corpus, and treating it
    as "up to date" skips one.
    """
    path = _watermark_path(node_root, platform)
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return Watermark(platform=str(doc["platform"]),
                         position=str(doc["position"]),
                         comparable=float(doc["comparable"]),
                         updated_at=str(doc["updated_at"]),
                         runs=int(doc.get("runs", 0)))
    except Exception as exc:  # noqa: BLE001 - any unreadable mark is one halt
        raise KnowledgeHalt(
            f"watermark for {platform!r} is unreadable: {path} ({exc})",
            "repair or delete it deliberately. An unreadable watermark read "
            "as 'never absorbed' re-absorbs the corpus; read as 'current' it "
            "skips one") from exc


def advance_watermark(node_root: Path | str, platform: str, *, position: str,
                      comparable: float, force: bool = False) -> Watermark:
    """Move the mark forward under a lock. Backwards requires ``force``.

    WRITE MODEL: locked fresh-read RMW. The current mark is read INSIDE the
    lock, because a value read before acquisition is stale by definition.
    """
    path = _watermark_path(node_root, platform)
    path.parent.mkdir(parents=True, exist_ok=True)
    with StoreLock(lock_for(path)):
        current: Optional[Watermark] = None
        if path.is_file():
            doc = json.loads(path.read_text(encoding="utf-8"))
            current = Watermark(platform=str(doc["platform"]),
                                position=str(doc["position"]),
                                comparable=float(doc["comparable"]),
                                updated_at=str(doc["updated_at"]),
                                runs=int(doc.get("runs", 0)))
        if current and comparable < current.comparable and not force:
            raise KnowledgeHalt(
                f"watermark for {platform!r} would move BACKWARDS "
                f"({current.comparable} -> {comparable})",
                "pass force=True and say why. A watermark that walks back "
                "silently re-absorbs, and one that walks forward on a failed "
                "run silently skips")
        mark = Watermark(platform=str(platform), position=str(position),
                         comparable=float(comparable), updated_at=_now(),
                         runs=(current.runs + 1) if current else 1)
        atomic_replace(path, json.dumps(mark.to_dict(), indent=2) + "\n")
    return mark


# ---------------------------------------------------------------------------
# the contract
# ---------------------------------------------------------------------------


class Absorber(ABC):
    """Template method: match -> extract -> store -> index -> record.

    A subclass declares ``PLATFORM``, ``COLLECTION``, ``SOURCE_TYPE`` and
    ``ECOSYSTEM_RINGS``, and implements :meth:`extract_id`,
    :meth:`extract_content` and :meth:`source_patterns`. Everything the base
    class does is the part that is easy to get subtly wrong: the closed
    vocabularies, the refusal-as-result, and the indexing status.

    ``SOURCE_TYPE`` is required and has no default. It is what the ring
    taxonomy classifies on, so an absorber that does not declare one writes
    rows nothing can classify -- the write-time obligation, refused at
    construction rather than discovered at coverage time.
    """

    PLATFORM: str = ""
    COLLECTION: str = ""
    SOURCE_TYPE: str = ""
    ECOSYSTEM_RINGS: Tuple[str, ...] = ("artifact",)

    def __init__(self, node_root: Path | str,
                 indexer: Optional[Callable[[ExtractedContent], int]] = None) -> None:
        for name in ("PLATFORM", "COLLECTION", "SOURCE_TYPE"):
            if not str(getattr(self, name, "")).strip():
                raise KnowledgeHalt(
                    f"{type(self).__name__} declares no {name}",
                    f"declare {name}. It is load-bearing: without it the rows "
                    "this absorber writes cannot be classified, and missing "
                    "means HALT, not a default")
        unknown = [r for r in self.ECOSYSTEM_RINGS if r not in ECOSYSTEM_RINGS]
        if unknown or not self.ECOSYSTEM_RINGS:
            raise KnowledgeHalt(
                f"{type(self).__name__} declares ecosystem rings "
                f"{list(self.ECOSYSTEM_RINGS)}",
                "use one or more of: " + ", ".join(ECOSYSTEM_RINGS)
                + ". An absorber that declares none is claiming a depth it "
                  "cannot state")
        self.node_root = Path(node_root)
        self.indexer = indexer
        self.store_dir = (self.node_root / ".intentops" / "knowledge"
                          / "absorbed" / self.PLATFORM)

    # -- subclass surface --------------------------------------------------

    @abstractmethod
    def extract_id(self, source: str) -> Optional[str]:
        """A stable id for this source, or None when it is not ours."""

    @abstractmethod
    def extract_content(self, source: str) -> ExtractedContent:
        """Fetch and parse. Raise to refuse; the template records the refusal."""

    @abstractmethod
    def source_patterns(self) -> Sequence[str]:
        """Regexes this absorber recognises, for :meth:`matches`."""

    def matches(self, source: str) -> bool:
        return any(re.search(p, str(source)) for p in self.source_patterns())

    # -- the template ------------------------------------------------------

    def absorb(self, source: str) -> AbsorptionResult:
        """Absorb one source. Never raises for a source-level failure."""
        content_id = None
        try:
            content_id = self.extract_id(source)
        except Exception as exc:  # noqa: BLE001 - a refusal, not a crash
            return AbsorptionResult.refusal(
                self.PLATFORM, source, f"id extraction failed: {exc}",
                ecosystem_rings=tuple(self.ECOSYSTEM_RINGS))
        if not content_id:
            return AbsorptionResult.refusal(
                self.PLATFORM, source, "not a source this absorber recognises",
                ecosystem_rings=tuple(self.ECOSYSTEM_RINGS))

        try:
            content = self.extract_content(source)
        except Exception as exc:  # noqa: BLE001 - a refusal, not a crash
            return AbsorptionResult.refusal(
                self.PLATFORM, source, f"{type(exc).__name__}: {exc}",
                content_id=content_id,
                ecosystem_rings=tuple(self.ECOSYSTEM_RINGS))

        storage_path = self._store(content)

        indexed, status = 0, "disabled"
        if self.indexer is not None:
            try:
                indexed = int(self.indexer(content))
                status = "ok" if indexed > 0 else "zero"
            except Exception as exc:  # noqa: BLE001 - loud, never silent
                indexed, status = 0, "failed"
                content.metadata["indexing_error"] = f"{type(exc).__name__}: {exc}"

        return AbsorptionResult(
            platform=self.PLATFORM,
            source=source,
            content_id=content.content_id,
            storage_path=str(storage_path),
            word_count=content.word_count,
            indexed_chunks=indexed,
            indexing_status=status,
            ecosystem_rings=tuple(self.ECOSYSTEM_RINGS),
            success=True,
            metadata={"source_type": self.SOURCE_TYPE,
                      "collection": self.COLLECTION,
                      **content.metadata},
        )

    def _store(self, content: ExtractedContent) -> Path:
        """Per-item file, named by content id. Collision-safe by construction."""
        self.store_dir.mkdir(parents=True, exist_ok=True)
        safe = _SAFE_ID.sub("-", content.content_id).strip("-") or "unnamed"
        path = self.store_dir / f"{safe}.md"
        header = (f"# {content.title or content.content_id}\n\n"
                  f"source: {content.source}\n"
                  f"source_type: {self.SOURCE_TYPE}\n"
                  f"extracted_at: {content.extracted_at}\n\n---\n\n")
        atomic_replace(path, header + content.text)
        return path


class FileAbsorber(Absorber):
    """Reference implementation: absorb text files from a directory.

    It exists to be READ. It is the smallest absorber that still honours the
    whole contract -- a declared source type, a closed depth declaration, a
    refusal that is a result, and an id that is a content hash so re-absorbing
    unchanged content is an upsert rather than a duplicate.

    It declares ``artifact`` only, and says so: a file on disk has an
    ecosystem, and this absorber does not go and find it.
    """

    PLATFORM = "file"
    COLLECTION = "knowledge"
    SOURCE_TYPE = "local-file"
    ECOSYSTEM_RINGS = ("artifact",)
    SUFFIXES = (".md", ".txt", ".rst")

    def source_patterns(self) -> Sequence[str]:
        return [r".*"]

    def extract_id(self, source: str) -> Optional[str]:
        path = Path(source)
        if path.suffix.lower() not in self.SUFFIXES:
            return None
        if not path.is_file():
            return None
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        return f"{path.stem}-{digest}"

    def extract_content(self, source: str) -> ExtractedContent:
        path = Path(source)
        text = path.read_text(encoding="utf-8")
        return ExtractedContent(
            content_id=self.extract_id(source) or path.stem,
            title=path.stem,
            text=text,
            source=path.name,
            metadata={"suffix": path.suffix.lower(), "bytes": len(text)},
        )


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------


def summarise(results: Iterable[AbsorptionResult]) -> Dict[str, Any]:
    """Counts that must add up, and a depth claim that cannot overstate itself.

    ``attempted`` equals ``absorbed + refused`` by construction, and the
    function raises if it ever does not: a summary whose parts do not sum to
    its whole is how a refusal leaves the population.
    """
    rows = list(results)
    absorbed = [r for r in rows if r.success]
    refused = [r for r in rows if not r.success]
    by_index: Dict[str, int] = {s: 0 for s in INDEXING_STATUSES}
    for r in rows:
        by_index[r.indexing_status] = by_index.get(r.indexing_status, 0) + 1
    if len(absorbed) + len(refused) != len(rows):  # pragma: no cover - guard
        raise KnowledgeHalt(
            "absorption summary does not account for every attempt",
            "every result is either a success or a refusal; there is no third "
            "state and no result may be dropped")
    reached_ecosystem = [r for r in absorbed if "ecosystem" in r.ecosystem_rings]
    return {
        "attempted": len(rows),
        "absorbed": len(absorbed),
        "refused": len(refused),
        "refusals": [{"source": r.source, "error": r.error} for r in refused],
        "indexing": by_index,
        "not_searchable": by_index["zero"] + by_index["failed"]
                          + by_index["unavailable"] + by_index["not-attempted"],
        "ecosystem_reached": len(reached_ecosystem),
        "depth": ("artifact and ecosystem" if reached_ecosystem
                  and len(reached_ecosystem) == len(absorbed)
                  else "artifact only" if not reached_ecosystem
                  else "mixed"),
    }


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


def selftest() -> Tuple[bool, str]:
    """Prove every refusal fires and no refusal leaves the count."""
    import tempfile

    failures: List[str] = []
    fired: List[str] = []

    def check(label: str, cond: bool) -> None:
        (fired if cond else failures).append(label)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        corpus = root / "corpus"
        corpus.mkdir()
        (corpus / "a.md").write_text("alpha beta gamma\n", encoding="utf-8")
        (corpus / "b.txt").write_text("delta\n", encoding="utf-8")
        (corpus / "c.bin").write_text("not ours\n", encoding="utf-8")

        # 1. undeclared vocabulary HALTs
        try:
            AbsorptionResult(platform="p", source="s", indexing_status="fine")
        except KnowledgeHalt:
            fired.append("undeclared-indexing-status-halts")
        else:
            failures.append("undeclared-indexing-status-halts")

        class NoSourceType(FileAbsorber):
            SOURCE_TYPE = ""

        try:
            NoSourceType(root)
        except KnowledgeHalt:
            fired.append("absorber-without-source-type-halts")
        else:
            failures.append("absorber-without-source-type-halts")

        class BadDepth(FileAbsorber):
            ECOSYSTEM_RINGS = ("everything",)

        try:
            BadDepth(root)
        except KnowledgeHalt:
            fired.append("undeclared-ecosystem-ring-halts")
        else:
            failures.append("undeclared-ecosystem-ring-halts")

        try:
            AbsorptionResult.refusal("p", "s", "")
        except KnowledgeHalt:
            fired.append("reasonless-refusal-halts")
        else:
            failures.append("reasonless-refusal-halts")

        # 2. a refusal is a result and stays in the denominator
        absorber = FileAbsorber(root)
        results = [absorber.absorb(str(p)) for p in sorted(corpus.iterdir())]
        results.append(absorber.absorb(str(corpus / "missing.md")))
        summary = summarise(results)
        check("every-attempt-counted",
              summary["attempted"] == 4
              and summary["absorbed"] + summary["refused"] == 4)
        check("unrecognised-and-missing-both-refused", summary["refused"] == 2)
        check("refusals-carry-reasons",
              all(r["error"] for r in summary["refusals"]))
        check("depth-is-stated-not-assumed", summary["depth"] == "artifact only")
        check("artifact-only-result-says-so",
              all("NOT absorbed" in r.depth_note for r in results if r.success))

        # 3. indexing status: disabled vs zero vs failed vs ok
        check("no-indexer-is-disabled-not-ok",
              all(r.indexing_status == "disabled" for r in results if r.success))
        zero = FileAbsorber(root, indexer=lambda c: 0).absorb(str(corpus / "a.md"))
        check("zero-chunks-is-zero-not-ok", zero.indexing_status == "zero")

        def boom(_c: ExtractedContent) -> int:
            raise RuntimeError("indexer down")

        failed = FileAbsorber(root, indexer=boom).absorb(str(corpus / "a.md"))
        check("indexer-failure-is-loud",
              failed.indexing_status == "failed" and failed.indexed_chunks == 0)
        good = FileAbsorber(root, indexer=lambda c: 3).absorb(str(corpus / "a.md"))
        check("indexed-is-ok",
              good.indexing_status == "ok" and good.indexed_chunks == 3)
        check("stored-file-exists", Path(good.storage_path).is_file())
        check("content-id-is-content-addressed",
              good.content_id == absorber.extract_id(str(corpus / "a.md")))

        # 4. an extractor that raises is a refusal, not a crash
        class Exploding(FileAbsorber):
            def extract_content(self, source: str) -> ExtractedContent:
                raise ValueError("parse failed")

        r = Exploding(root).absorb(str(corpus / "a.md"))
        check("extraction-failure-is-a-refusal",
              (not r.success) and "parse failed" in r.error)

        # 5. watermarks: forward is fine, backward needs force, corrupt HALTs
        m1 = advance_watermark(root, "file", position="p1", comparable=1.0)
        check("watermark-first-run", m1.runs == 1)
        m2 = advance_watermark(root, "file", position="p2", comparable=2.0)
        check("watermark-advances", m2.comparable == 2.0 and m2.runs == 2)
        try:
            advance_watermark(root, "file", position="p0", comparable=0.5)
        except KnowledgeHalt:
            fired.append("backward-watermark-halts")
        else:
            failures.append("backward-watermark-halts")
        forced = advance_watermark(root, "file", position="p0", comparable=0.5,
                                   force=True)
        check("forced-rewind-allowed", forced.comparable == 0.5)
        check("unabsorbed-platform-has-no-mark",
              read_watermark(root, "never-seen") is None)
        _watermark_path(root, "file").write_text("{", encoding="utf-8")
        try:
            read_watermark(root, "file")
        except KnowledgeHalt:
            fired.append("corrupt-watermark-halts")
        else:
            failures.append("corrupt-watermark-halts")

    report = (f"absorber selftest: {len(fired)} paths fired, "
              f"{len(failures)} failed"
              + (" -- " + ", ".join(failures) if failures else ""))
    return (not failures, report)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="the absorber contract")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        ok, report = selftest()
        print(report)
        return 0 if ok else 1
    print(json.dumps({"indexing_statuses": list(INDEXING_STATUSES),
                      "ecosystem_rings": list(ECOSYSTEM_RINGS)}, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
