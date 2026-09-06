"""Belief carriers -- the specification a node BINDS at birth, so that the
belief-currency instrument has a population from day one.

PURPOSE
    ``still_true`` can read belief carriers. Until this module existed, nothing
    told a newborn node *which* carriers are its own, so birth check 2 answered
    ``UNPROBEABLE``: "no belief-carrier specification is bound at birth". That
    is an honest reading of an absence, and an absence is what this module
    closes.

    A binding is a DECLARATION, not a discovery. It names, per source: the
    carrier KIND, where the source lives, which base it is relative to, whether
    that base is under version control, and WHICH FALSIFIERS the reader is
    expected to be able to fire over it. Every one of those fields is
    load-bearing and none has a default -- a binding that half-understands its
    own sources produces a reading that runs and is wrong.

    The template ships bound to the two carriers every node has at birth: its
    own ordering store and its own founding conversation. The population is
    therefore never empty by construction, which matters because an empty
    population scores a perfect reading -- the most flattering possible answer
    to a question nobody asked.

THE FOUR KINDS (v1), each with its as-of extractor:

    ruling        an append-only JSONL ruling store; as-of is each record's
                  ``date`` field
    anchor        ``## heading`` blocks in a document, each carrying an
                  ``[OBSERVED <date>]`` stamp; as-of is the latest stamp in
                  the block
    briefing      a whole document as one belief; as-of is a front-matter
                  ``as_of:`` scalar or the latest ``[OBSERVED <date>]`` in it
    memory-topic  a directory of topic files whose FILENAME carries the date
                  (``*_YYYY-MM-DD.<ext>``); as-of is that date

THE FOUR FALSIFIERS are ``still_true``'s: EVIDENCE-MOVED, SUBJECT-GONE,
UNDATED, CONFLICT. A source declares the ones it expects, and the loader
REFUSES a declaration the reader could not honour -- notably EVIDENCE-MOVED
over a source whose base is not under version control, because movement is
read from commit history and nothing else. A falsifier that cannot fire is
worse than no falsifier: it looks accounted for.

WRITE MODEL
    The template (``config/belief-carriers.template.yaml``) is reviewed,
    human-authored config -- not a store. The node's bound copy
    (``.intentops/config/belief-carriers.yaml``) is written ONCE by genesis G3
    under locked fresh-read read-modify-write (``StoreLock`` +
    ``atomic_replace``), which is the write model its containing organ
    (``.intentops/config``) declares at birth. Readings go to ``still_true``'s
    own append-only ledger; this module writes no ledger of its own.

BLIND SPOTS
    - A binding says which carriers a node MEANT to watch. It cannot know about
      a carrier nobody declared, so coverage is a declaration and never a
      census. The instrument reports the bound count; it never claims it is all.
    - ``version_control: none`` means EVIDENCE-MOVED cannot fire over that
      source at all. Subjects it names are classified unwatchable rather than
      moved, and the reading says so in a note. This is the honest reading for
      a freshly-created identity repository, which is not a git repository
      until somebody makes it one.
    - The memory-topic reader dates a belief from its FILENAME. A file renamed
      is re-dated and a file rewritten is not, which is the opposite of what
      modification time would say and is why the filename is used.
    - CONFLICT is delegated whole to the ordering store's own ``conflicts()``,
      which sees only rulings carrying predicates. Two prose rulings that
      disagree are invisible to it, here as there.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import still_true

__all__ = [
    "SCHEMA",
    "KINDS",
    "BASES",
    "VERSION_CONTROL",
    "AS_OF_EXTRACTORS",
    "EVALUABLE_FALSIFIERS",
    "BINDING_RELPATH",
    "TEMPLATE_RELPATH",
    "BeliefCarrierError",
    "Source",
    "Binding",
    "load_binding",
    "bind_template",
    "resolve_source",
    "read_memory_topics",
    "take_bound_reading",
    "selftest",
    "main",
]

SCHEMA = "belief-carriers/v1"

#: Closed vocabulary. An undeclared kind is a hard exit, never a default.
KINDS: Tuple[str, ...] = ("ruling", "anchor", "briefing", "memory-topic")

#: Which root a source's ``path`` is relative to. There is no default: a
#: binding that guesses its base reads the wrong node's beliefs.
BASES: Tuple[str, ...] = ("node", "identity-repo", "repo")

#: Whether the base is under version control. Load-bearing, because
#: EVIDENCE-MOVED is read from commit history and from nothing else.
VERSION_CONTROL: Tuple[str, ...] = ("git", "none")

#: The as-of extractor each kind uses. Declared here so the template can state
#: it and a reader can check the template against the code rather than against
#: a comment.
AS_OF_EXTRACTORS: Dict[str, str] = {
    "ruling": "record-field:date",
    "anchor": "observed-stamp",
    "briefing": "document-stamp",
    "memory-topic": "filename-date",
}

#: What the reader can actually fire, per kind. A source declaring anything
#: outside its kind's set is REFUSED -- see the module docstring.
EVALUABLE_FALSIFIERS: Dict[str, frozenset] = {
    "ruling": frozenset({"EVIDENCE-MOVED", "SUBJECT-GONE", "UNDATED", "CONFLICT"}),
    "anchor": frozenset({"EVIDENCE-MOVED", "SUBJECT-GONE", "UNDATED"}),
    "briefing": frozenset({"EVIDENCE-MOVED", "SUBJECT-GONE", "UNDATED"}),
    # No git history is read for a topic directory, so movement is not
    # observable there even when the base IS version controlled.
    "memory-topic": frozenset({"SUBJECT-GONE", "UNDATED"}),
}

#: Where genesis writes the node's own bound copy, and where the template lives.
BINDING_RELPATH = Path(".intentops") / "config" / "belief-carriers.yaml"
TEMPLATE_RELPATH = Path("config") / "belief-carriers.template.yaml"

#: ``<stem>_YYYY-MM-DD.<ext>`` or ``<stem>-YYYY-MM-DD.<ext>``.
_FILENAME_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")

_CARRIER_MODE_FOR_KIND = {"anchor": "sections", "briefing": "document"}


class BeliefCarrierError(ValueError):
    """A binding that cannot be trusted. Nothing is read."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# the declaration
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Source:
    """One declared belief-carrier source. No field has a default that could
    make an unread declaration look like a read one."""

    id: str
    kind: str
    path: str
    base: str
    version_control: str
    falsifiers: Tuple[str, ...]
    label: str = ""

    @property
    def mode(self) -> Optional[str]:
        """The ``still_true`` carrier mode, for the two text kinds."""
        return _CARRIER_MODE_FOR_KIND.get(self.kind)


@dataclass
class Binding:
    """A loaded, validated belief-carrier specification."""

    schema: str
    as_of: str
    sources: List[Source] = field(default_factory=list)
    path: Optional[Path] = None
    bound_at: str = ""

    @property
    def kinds_bound(self) -> List[str]:
        return sorted({s.kind for s in self.sources})

    @property
    def kinds_unbound(self) -> List[str]:
        return [k for k in KINDS if k not in self.kinds_bound]


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise BeliefCarrierError(message)


def load_binding(path: Path) -> Binding:
    """Read a belief-carriers document. Any missing load-bearing field HALTs."""
    import yaml

    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise BeliefCarrierError(f"belief-carriers binding unreadable: {exc}") from exc
    except yaml.YAMLError as exc:
        raise BeliefCarrierError(f"belief-carriers binding is not valid YAML: {exc}") from exc

    _require(isinstance(raw, dict), "belief-carriers root must be a mapping")
    _require(raw.get("schema") == SCHEMA,
             f"belief-carriers schema must be {SCHEMA!r}, got {raw.get('schema')!r}")
    as_of = str(raw.get("as_of") or "").strip()
    _require(bool(as_of),
             "belief-carriers has no `as_of`. A specification about belief "
             "currency that does not say when it was last true is the defect "
             "it exists to prevent")

    declared_kinds = raw.get("kinds")
    _require(isinstance(declared_kinds, dict) and bool(declared_kinds),
             "belief-carriers has no `kinds` block; the carrier vocabulary is "
             "declared, never inferred from the sources present")
    unknown_kinds = sorted(set(declared_kinds) - set(KINDS))
    _require(not unknown_kinds,
             f"belief-carriers declares undeclared kind(s) {unknown_kinds}; "
             f"known kinds are {list(KINDS)}")
    for kind, spec in declared_kinds.items():
        _require(isinstance(spec, dict), f"kinds.{kind} must be a mapping")
        got = str(spec.get("as_of") or "")
        want = AS_OF_EXTRACTORS[kind]
        _require(got == want,
                 f"kinds.{kind}.as_of is {got!r}; this reader extracts "
                 f"{want!r}. A declared extractor that is not the one running "
                 f"is a comment wearing a schema")

    _require("sources" in raw, "belief-carriers has no `sources` key")
    entries = raw.get("sources")
    _require(isinstance(entries, list), "`sources` must be a list")
    _require(bool(entries),
             "`sources` is empty. A bound-but-empty population scores a "
             "perfect reading by construction; bind at least the node's own "
             "ordering store")

    sources: List[Source] = []
    seen_ids: set = set()
    for i, entry in enumerate(entries):
        where = f"sources[{i}]"
        _require(isinstance(entry, dict), f"{where} must be a mapping")
        sid = str(entry.get("id") or "").strip()
        _require(bool(sid), f"{where} has no `id`")
        _require(sid not in seen_ids, f"{where}: duplicate source id {sid!r}")
        seen_ids.add(sid)

        kind = entry.get("kind")
        _require(kind in KINDS,
                 f"{where} ({sid}): kind {kind!r} is not one of {list(KINDS)}")
        _require(kind in declared_kinds,
                 f"{where} ({sid}): kind {kind!r} is not declared in `kinds`")

        spath = str(entry.get("path") or "").strip()
        _require(bool(spath), f"{where} ({sid}) has no `path`")

        base = entry.get("base")
        _require(base in BASES,
                 f"{where} ({sid}): base {base!r} is not one of {list(BASES)}; "
                 "there is no default")

        vc = entry.get("version_control")
        _require(vc in VERSION_CONTROL,
                 f"{where} ({sid}): version_control {vc!r} is not one of "
                 f"{list(VERSION_CONTROL)}; there is no default, because "
                 "EVIDENCE-MOVED is read from commit history and nothing else")

        falsifiers = entry.get("falsifiers")
        _require(isinstance(falsifiers, list) and bool(falsifiers),
                 f"{where} ({sid}) declares no `falsifiers`. A belief carrier "
                 "with no falsifier is a belief with no way back")
        fset = [str(f) for f in falsifiers]
        unknown = sorted(set(fset) - set(still_true.QUESTION_KINDS))
        _require(not unknown,
                 f"{where} ({sid}): unknown falsifier(s) {unknown}; known are "
                 f"{list(still_true.QUESTION_KINDS)}")
        unevaluable = sorted(set(fset) - EVALUABLE_FALSIFIERS[kind])
        _require(not unevaluable,
                 f"{where} ({sid}): falsifier(s) {unevaluable} cannot be "
                 f"evaluated for a {kind!r} carrier. A falsifier that cannot "
                 "fire is worse than none -- it looks accounted for")
        if "EVIDENCE-MOVED" in fset and vc != "git":
            raise BeliefCarrierError(
                f"{where} ({sid}) declares EVIDENCE-MOVED but its base is not "
                "under version control. Movement is read from commit history; "
                "declaring it here would be a falsifier that can never fire")

        sources.append(Source(id=sid, kind=kind, path=spath, base=base,
                              version_control=vc, falsifiers=tuple(fset),
                              label=str(entry.get("label") or "")))

    return Binding(schema=SCHEMA, as_of=as_of, sources=sources, path=path,
                   bound_at=str(raw.get("bound_at") or ""))


def bind_template(template: Path, dest: Path, *, bound_at: Optional[str] = None,
                  writer: Optional[Callable[[Path, str], None]] = None) -> Binding:
    """Copy the reviewed template to the node's own bound copy, and stamp it.

    The template is VALIDATED before it is written, so a node never carries a
    binding its own loader would refuse. The stamp records when this node bound
    it; ``as_of`` stays the template's, because that is when the specification
    was last reviewed and this copy did not review it again.
    """
    import yaml

    template = Path(template)
    binding = load_binding(template)          # refuses before anything is written
    raw = yaml.safe_load(template.read_text(encoding="utf-8"))
    raw["bound_at"] = bound_at or _now()
    raw["bound_from"] = template.name
    text = yaml.safe_dump(raw, default_flow_style=False, sort_keys=False,
                          allow_unicode=True)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if writer is not None:
        writer(dest, text)
    else:
        from ..store_guard import StoreLock, atomic_replace, lock_for

        with StoreLock(lock_for(dest)):
            atomic_replace(dest, text)
    binding.path = dest
    binding.bound_at = raw["bound_at"]
    return binding


def resolve_source(source: Source, *, node_root: Path,
                   identity_repo: Optional[Path],
                   repo_root: Optional[Path]) -> Tuple[Optional[Path], Optional[Path]]:
    """Return ``(absolute path, workspace)`` for a source, or ``(None, None)``.

    The workspace is the base the source's subjects are resolved against; it is
    returned beside the path because a subject named in an identity-repo
    document means a path in the identity repo, not in the node root.
    """
    bases: Dict[str, Optional[Path]] = {
        "node": Path(node_root),
        "identity-repo": Path(identity_repo) if identity_repo else None,
        "repo": Path(repo_root) if repo_root else None,
    }
    base = bases[source.base]
    if base is None:
        return None, None
    return base / source.path, base


# --------------------------------------------------------------------------
# the one reader this module adds
# --------------------------------------------------------------------------


def read_memory_topics(directory: Path, workspace: Path,
                       tracked: Callable[[str], bool],
                       *, suffixes: Sequence[str] = (".md", ".txt"),
                       ) -> List[still_true.Belief]:
    """One belief per topic file, dated from its FILENAME.

    A topic directory has no front matter and no commit history worth reading,
    so the filename is the only honest as-of available. A file whose name
    carries no date is UNDATED -- it stays in the population and says so,
    rather than leaving the count and making the reading look better.
    """
    directory = Path(directory)
    beliefs: List[still_true.Belief] = []
    if not directory.is_dir():
        return beliefs
    for path in sorted(p for p in directory.iterdir()
                       if p.is_file() and p.suffix.lower() in suffixes):
        stamps = _FILENAME_DATE.findall(path.name)
        as_of = max(stamps) if stamps else None
        rel = still_true._posix_rel(path, Path(workspace))  # noqa: SLF001
        belief = still_true.Belief(kind="carrier", id=rel, as_of=as_of,
                                   summary=path.stem)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        still_true._attach_subjects(  # noqa: SLF001
            belief, (m.group(1) for m in still_true._PATH_TOKEN.finditer(text)),
            workspace, tracked)
        beliefs.append(belief)
    return beliefs


# --------------------------------------------------------------------------
# the bound reading
# --------------------------------------------------------------------------


def _tracked_for(workspace: Path, version_control: str,
                 unreadable: List[str]) -> Callable[[str], bool]:
    if version_control == "none":
        return still_true.Tracked(())
    try:
        return still_true.git_tracked(workspace)
    except Exception as exc:  # noqa: BLE001 - LOUD, never silent
        unreadable.append(f"git ls-files ({workspace}): "
                          f"{exc.__class__.__name__}: {exc}")
        return still_true.Tracked(())


def _ordering_conflicts(node_root: Path) -> Sequence[Tuple[str, str, str, str]]:
    """CONFLICT, delegated whole to the ordering store's own detector."""
    from ..wisdom.ordering import OrderingStore

    state = OrderingStore(node_root).load()
    return [(a.id, a.ordering, b.id, b.ordering) for a, b in state.conflicts()]


def take_bound_reading(
    binding: Binding, *, node_root: Path,
    identity_repo: Optional[Path] = None,
    repo_root: Optional[Path] = None,
    conflicts_fn: Optional[Callable[[], Sequence[Tuple[str, str, str, str]]]] = None,
    last_commit_fn: Optional[Callable[[Path, Sequence[str], Optional[str]], Dict[str, str]]] = None,
) -> still_true.Reading:
    """Read every bound source and run ``still_true``'s falsifier pass over it.

    The falsifier pass is not reimplemented here -- ``still_true.evaluate`` is
    the verdict layer and stays the only one. What this function adds is the
    DECLARED population: which sources, under which base, with which falsifiers.
    """
    reading = still_true.Reading(as_of=still_true._now_iso())  # noqa: SLF001
    reading.blind_spots = [
        "the population is DECLARED, never discovered -- a carrier nobody "
        "bound is invisible here",
        "a source declared version_control: none cannot fire EVIDENCE-MOVED; "
        "its subjects are unwatchable, not unmoved",
        "memory-topic beliefs are dated from the FILENAME: a rename re-dates, "
        "a rewrite does not",
        "CONFLICT sees only predicate-carrying rulings; two prose rulings that "
        "disagree are invisible",
    ]
    beliefs: List[still_true.Belief] = []
    conflicts: List[Tuple[str, str, str, str]] = []
    # subject -> the workspace its commit history would be read against
    subject_ws: Dict[str, Path] = {}
    wants_conflict = any("CONFLICT" in s.falsifiers for s in binding.sources)

    for source in binding.sources:
        path, workspace = resolve_source(source, node_root=node_root,
                                         identity_repo=identity_repo,
                                         repo_root=repo_root)
        if path is None or workspace is None:
            reading.unreadable.append(
                f"source {source.id}: base {source.base!r} is not bound")
            continue
        tracked = _tracked_for(workspace, source.version_control,
                               reading.unreadable)
        try:
            if source.kind == "ruling":
                if not path.is_file():
                    reading.unreadable.append(
                        f"source {source.id}: records missing at {path}")
                    continue
                got, no_way_back, failures = still_true.read_records(
                    path, workspace, tracked)
                reading.beliefs_without_way_back += no_way_back
                reading.unreadable.extend(failures)
            elif source.kind == "memory-topic":
                if not path.is_dir():
                    reading.unreadable.append(
                        f"source {source.id}: topic directory missing at {path}")
                    continue
                got = read_memory_topics(path, workspace, tracked)
            else:
                if not path.is_file():
                    reading.unreadable.append(
                        f"source {source.id}: carrier missing at {path}")
                    continue
                spec = still_true.CarrierSpec(path, source.mode or "document",
                                              label=source.label)
                got = still_true.read_carrier(spec, workspace, tracked)
        except Exception as exc:  # noqa: BLE001
            reading.unreadable.append(
                f"source {source.id}: {exc.__class__.__name__}: {exc}")
            continue
        for belief in got:
            for subject in belief.subjects:
                subject_ws.setdefault(subject, workspace)
        beliefs.extend(got)
        reading.notes.append(
            f"{source.id}: {len(got)} belief(s), kind {source.kind}, "
            f"falsifiers {'+'.join(source.falsifiers)}, "
            f"version_control {source.version_control}")

    if wants_conflict:
        fn = conflicts_fn or (lambda: _ordering_conflicts(node_root))
        try:
            conflicts = list(fn())
        except Exception as exc:  # noqa: BLE001
            reading.unreadable.append(
                f"conflicts: {exc.__class__.__name__}: {exc}")
    else:
        reading.notes.append(
            "no bound source declares CONFLICT -- that falsifier cannot fire "
            "in this reading, by declaration and not by accident")

    # EVIDENCE-MOVED, per workspace, over the subjects that source declared it.
    dated = [d for d in (still_true._to_date(b.as_of) for b in beliefs)  # noqa: SLF001
             if d is not None]
    since = min(dated).isoformat() if dated else None
    last_commit: Dict[str, str] = {}
    by_ws: Dict[Path, List[str]] = {}
    for subject, workspace in subject_ws.items():
        by_ws.setdefault(workspace, []).append(subject)
    for workspace, subjects in by_ws.items():
        try:
            fn = last_commit_fn or still_true.git_last_commit_dates
            last_commit.update(fn(workspace, sorted(subjects), since))
        except Exception as exc:  # noqa: BLE001
            reading.unreadable.append(
                f"git log ({workspace}): {exc.__class__.__name__}: {exc}")

    reading.beliefs = len(beliefs)
    reading.dated = sum(1 for b in beliefs
                        if still_true._to_date(b.as_of) is not None)  # noqa: SLF001
    reading.undated = reading.beliefs - reading.dated
    reading.watchable_subjects = len({s for b in beliefs for s in b.subjects})
    reading.unwatchable_subjects = len({s for b in beliefs for s in b.unwatchable})
    for kind in still_true.KINDS:
        group = [b for b in beliefs if b.kind == kind]
        dated_k = sum(1 for b in group
                      if still_true._to_date(b.as_of) is not None)  # noqa: SLF001
        reading.by_kind[kind] = {"beliefs": len(group), "dated": dated_k,
                                 "undated": len(group) - dated_k}
    reading.questions = still_true.evaluate(beliefs, last_commit, conflicts)
    if binding.kinds_unbound:
        reading.notes.append(
            "kinds declared but not bound at this node: "
            + ", ".join(binding.kinds_unbound)
            + " -- absent from the population by declaration")
    return reading


def summary_line(reading: still_true.Reading) -> str:
    """The honest one-liner: every count beside its denominator."""
    return (f"{reading.beliefs} carriers, {reading.dated} dated, "
            f"{reading.open_questions} open questions")


# --------------------------------------------------------------------------
# selftest -- every refusal and every reader path must be able to fire
# --------------------------------------------------------------------------


_MIN_KINDS = "\n".join(
    [f"  {kind}:\n    as_of: {AS_OF_EXTRACTORS[kind]}" for kind in KINDS])


def _doc(sources: str, *, schema: str = SCHEMA, as_of: str = "2026-09-06",
         kinds: Optional[str] = None) -> str:
    body = f"schema: {schema}\nas_of: \"{as_of}\"\nkinds:\n"
    body += (kinds if kinds is not None else _MIN_KINDS) + "\n"
    body += sources
    return body


def selftest() -> Tuple[bool, str]:
    """Prove every refusal fires, every reader runs, and the falsifiers land."""
    failures: List[str] = []

    def check(label: str, cond: bool) -> None:
        if not cond:
            failures.append(label)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = root / "belief-carriers.yaml"

        def refuses(label: str, text: str) -> None:
            cfg.write_text(text, encoding="utf-8")
            try:
                load_binding(cfg)
            except BeliefCarrierError:
                return
            failures.append(label)

        good_sources = ("sources:\n"
                        "  - id: orderings\n"
                        "    kind: ruling\n"
                        "    path: .intentops/wisdom/orderings.jsonl\n"
                        "    base: node\n"
                        "    version_control: none\n"
                        "    falsifiers: [UNDATED, SUBJECT-GONE, CONFLICT]\n")

        refuses("a wrong schema was accepted",
                _doc(good_sources, schema="belief-carriers/v0"))
        refuses("a binding with no as_of was accepted", _doc(good_sources, as_of=""))
        refuses("a binding with no kinds block was accepted",
                f"schema: {SCHEMA}\nas_of: \"2026-09-06\"\n" + good_sources)
        refuses("an undeclared kind was accepted",
                _doc(good_sources, kinds="  omen:\n    as_of: entrails"))
        refuses("a wrong as-of extractor was accepted",
                _doc(good_sources, kinds="  ruling:\n    as_of: mtime"))
        refuses("a binding with no sources key was accepted",
                _doc("", kinds=_MIN_KINDS).rstrip() + "\n")
        refuses("an EMPTY sources list was accepted", _doc("sources: []\n"))
        refuses("a source with no base was accepted",
                _doc("sources:\n  - id: a\n    kind: ruling\n    path: x.jsonl\n"
                     "    version_control: none\n    falsifiers: [UNDATED]\n"))
        refuses("a source with no version_control was accepted",
                _doc("sources:\n  - id: a\n    kind: ruling\n    path: x.jsonl\n"
                     "    base: node\n    falsifiers: [UNDATED]\n"))
        refuses("a source with no falsifiers was accepted",
                _doc("sources:\n  - id: a\n    kind: ruling\n    path: x.jsonl\n"
                     "    base: node\n    version_control: none\n"))
        refuses("an unknown falsifier was accepted",
                _doc("sources:\n  - id: a\n    kind: ruling\n    path: x.jsonl\n"
                     "    base: node\n    version_control: none\n"
                     "    falsifiers: [VIBES]\n"))
        refuses("CONFLICT on a briefing carrier was accepted",
                _doc("sources:\n  - id: a\n    kind: briefing\n    path: x.md\n"
                     "    base: node\n    version_control: none\n"
                     "    falsifiers: [CONFLICT]\n"))
        refuses("EVIDENCE-MOVED without version control was accepted",
                _doc("sources:\n  - id: a\n    kind: briefing\n    path: x.md\n"
                     "    base: node\n    version_control: none\n"
                     "    falsifiers: [EVIDENCE-MOVED]\n"))
        refuses("a duplicate source id was accepted",
                _doc("sources:\n"
                     "  - id: a\n    kind: briefing\n    path: x.md\n"
                     "    base: node\n    version_control: none\n"
                     "    falsifiers: [UNDATED]\n"
                     "  - id: a\n    kind: briefing\n    path: y.md\n"
                     "    base: node\n    version_control: none\n"
                     "    falsifiers: [UNDATED]\n"))

        # -- a good binding loads, and its two text readers run --------------
        node = root / "node"
        (node / ".intentops" / "wisdom").mkdir(parents=True)
        (node / ".intentops" / "wisdom" / "orderings.jsonl").write_text(
            "", encoding="utf-8")
        (node / "docs").mkdir(parents=True)
        (node / "docs" / "brief.md").write_text(
            "as_of: 2026-09-06\n\n# A briefing\n\nnothing yet.\n",
            encoding="utf-8")
        (node / "docs" / "anchors.md").write_text(
            "# Anchors\n\n## An organ\n\n[OBSERVED 2026-09-06] it is here.\n",
            encoding="utf-8")
        topics = node / "topics"
        topics.mkdir()
        (topics / "a_topic_2026-09-01.md").write_text("dated\n", encoding="utf-8")
        (topics / "undated_topic.md").write_text("undated\n", encoding="utf-8")

        cfg.write_text(_doc(
            "sources:\n"
            "  - id: orderings\n    kind: ruling\n"
            "    path: .intentops/wisdom/orderings.jsonl\n"
            "    base: node\n    version_control: none\n"
            "    falsifiers: [UNDATED, SUBJECT-GONE, CONFLICT]\n"
            "  - id: brief\n    kind: briefing\n    path: docs/brief.md\n"
            "    base: node\n    version_control: none\n"
            "    falsifiers: [UNDATED, SUBJECT-GONE]\n"
            "  - id: anchors\n    kind: anchor\n    path: docs/anchors.md\n"
            "    base: node\n    version_control: none\n"
            "    falsifiers: [UNDATED, SUBJECT-GONE]\n"
            "  - id: topics\n    kind: memory-topic\n    path: topics\n"
            "    base: node\n    version_control: none\n"
            "    falsifiers: [UNDATED, SUBJECT-GONE]\n"), encoding="utf-8")
        try:
            binding = load_binding(cfg)
            reading = take_bound_reading(binding, node_root=node,
                                         conflicts_fn=lambda: ())
            check("the four bound sources did not all read",
                  len(binding.sources) == 4)
            # 1 briefing + 1 anchor section + 2 topics; the ruling store is empty
            check(f"expected 4 beliefs, read {reading.beliefs}",
                  reading.beliefs == 4)
            check(f"expected 3 dated, read {reading.dated}", reading.dated == 3)
            kinds = {q.kind for q in reading.questions}
            check("the UNDATED falsifier did not fire on the undated topic",
                  "UNDATED" in kinds)
            check(f"unreadable sources on a clean tree: {reading.unreadable}",
                  not reading.unreadable)
            check("no kinds should be unbound in this fixture",
                  binding.kinds_unbound == [])
        except Exception as exc:  # noqa: BLE001
            failures.append(f"the good binding raised: {exc!r}")

        # -- SUBJECT-GONE fires, and a missing source stays in the count -----
        (node / "docs" / "gone.md").write_text(
            "as_of: 2026-09-06\n\nrests on `no/such/file.py`.\n", encoding="utf-8")
        cfg.write_text(_doc(
            "sources:\n"
            "  - id: gone\n    kind: briefing\n    path: docs/gone.md\n"
            "    base: node\n    version_control: none\n"
            "    falsifiers: [UNDATED, SUBJECT-GONE]\n"
            "  - id: absent\n    kind: briefing\n    path: docs/absent.md\n"
            "    base: node\n    version_control: none\n"
            "    falsifiers: [UNDATED, SUBJECT-GONE]\n"), encoding="utf-8")
        try:
            reading = take_bound_reading(load_binding(cfg), node_root=node)
            check("SUBJECT-GONE did not fire on a named missing file",
                  any(q.kind == "SUBJECT-GONE" for q in reading.questions))
            check("an absent source left the denominator instead of being "
                  "reported unreadable", len(reading.unreadable) == 1)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"the SUBJECT-GONE fixture raised: {exc!r}")

        # -- an unbound base is reported, never silently skipped -------------
        cfg.write_text(_doc(
            "sources:\n  - id: idr\n    kind: briefing\n    path: x.md\n"
            "    base: identity-repo\n    version_control: none\n"
            "    falsifiers: [UNDATED]\n"), encoding="utf-8")
        try:
            reading = take_bound_reading(load_binding(cfg), node_root=node,
                                         identity_repo=None)
            check("an unbound identity-repo base was skipped silently",
                  any("not bound" in u for u in reading.unreadable))
        except Exception as exc:  # noqa: BLE001
            failures.append(f"the unbound-base fixture raised: {exc!r}")

        # -- bind_template validates BEFORE it writes ------------------------
        bad = root / "bad-template.yaml"
        bad.write_text(_doc("sources: []\n"), encoding="utf-8")
        dest = root / "out" / "belief-carriers.yaml"
        try:
            bind_template(bad, dest)
            failures.append("bind_template wrote an invalid template")
        except BeliefCarrierError:
            check("bind_template wrote a file it had refused", not dest.exists())

    ok = not failures
    return ok, ("every refusal fired, all four readers ran, UNDATED and "
                "SUBJECT-GONE landed, an unbound base and an absent source "
                "stayed in the count, and bind_template refused before writing"
                if ok else "; ".join(failures))


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Belief carriers -- the bound belief-currency population")
    ap.add_argument("--node-root", default=".", help="the node root")
    ap.add_argument("--identity-repo", default=None)
    ap.add_argument("--repo-root", default=None)
    ap.add_argument("--binding", default=None,
                    help=f"binding path (default: <node-root>/{BINDING_RELPATH.as_posix()})")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        ok, msg = selftest()
        print(f"{'PASS' if ok else 'FAIL'} belief_carriers selftest: {msg}")
        return 0 if ok else 1

    node_root = Path(args.node_root).resolve()
    binding_path = Path(args.binding) if args.binding else node_root / BINDING_RELPATH
    if not binding_path.is_file():
        print(f"no belief-carrier binding at {binding_path}")
        return 2
    try:
        binding = load_binding(binding_path)
    except BeliefCarrierError as exc:
        print(f"REFUSED: {exc}")
        return 2
    reading = take_bound_reading(
        binding, node_root=node_root,
        identity_repo=Path(args.identity_repo) if args.identity_repo else None,
        repo_root=Path(args.repo_root) if args.repo_root else None)
    if args.json:
        print(json.dumps({"binding": binding_path.as_posix(),
                          "sources": [s.id for s in binding.sources],
                          "summary": summary_line(reading),
                          "unreadable": reading.unreadable,
                          "notes": reading.notes,
                          "questions": [q.render() for q in reading.questions]},
                         indent=2))
    else:
        print(f"BELIEF CARRIERS -- {binding_path.as_posix()}")
        print(f"  {summary_line(reading)}")
        for note in reading.notes:
            print(f"  - {note}")
        for entry in reading.unreadable:
            print(f"  ! unreadable: {entry}")
        for question in reading.questions:
            print(f"  ? {question.render()}")
    return 0 if not reading.unreadable else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
