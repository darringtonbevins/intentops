#!/usr/bin/env python3
"""The exposure gate -- prove this repository carries none of the private estate.

PURPOSE
    IntentOps was extracted from a private working estate. The extraction is
    only trustworthy if the seam is checked by a machine: a reviewer's
    attention is not an instrument, and "we were careful" is not a finding.
    This script reads ``config/exposure-fence.yaml`` and walks the tree,
    looking for two different kinds of thing:

      * NAMES -- carried as SHA-256 digests, because a plaintext denylist would
        put every fenced word into the very repository the fence exists to keep
        them out of. See that file's header for why this is not secrecy.
      * SHAPES -- an address, an identifier, a composite record key, a key
        header. A regex for an address is not an address, so these ship in the
        open.

    It is deliberately free of any external search binary. A pure-Python walk
    runs identically on a maintainer's laptop, in CI, and on a stranger's
    fresh clone, which is the only way its verdict means the same thing in all
    three places.

WRITE MODEL
    None. This is a reader: it opens files, and it writes nothing anywhere. The
    only output is its report on stdout and its exit code. A gate that mutates
    the thing it is grading cannot be trusted about it.

BLIND SPOTS -- published, per the rule that a detector which hides its coverage
manufactures confidence over a population it never saw
    * It finds only what the fence declares. A private name nobody thought of
      is invisible, and a green run says nothing at all about that population.
      The honest form of a green result is the three-clause one, which the
      report prints verbatim rather than leaving to the reader.
    * The default scan reads the WORKING TREE only. ``--history`` scans commit
      IDENTITIES and MESSAGES as well, which is where the first exposure of a
      published repository normally lives: an author line is written by the
      committer's local config, never by anything in the tree, so a tree that
      scans clean can still publish a private name the moment it is pushed.
      Neither mode reads historical file CONTENT -- a name deleted from a file
      today still sits in that file's earlier blobs, and nothing here looks.
    * Binary files are skipped (by extension, and by a null-byte probe). A
      token inside an archive or an image is not seen.
    * Files above ``max_file_bytes`` are reported UNSCANNED and stay in the
      denominator. They are never dropped from the population -- a refusal
      that leaves the count is how an instrument flatters itself.
    * Short tokens match whole words only, so a short token embedded in a
      longer proper noun is missed; long tokens match as substrings, so some
      ordinary English words are flagged. Both trades are stated in the fence
      config and are deliberate.
    * Decoding uses ``errors="replace"``. A token in an unusual encoding may be
      mangled past recognition before it is ever compared.
    * ``--history`` exempts the identities and addresses declared under the
      config's ``history_allowed_identities`` block -- the repository's own
      publishing account and its co-author trailer, never a blanket pass on
      names or emails in general. The exemption is scoped to the specific
      author/committer line an identity appears on; the same word in a
      subject or body is still a finding. Every run prints how many identity
      checks it made and how many it exempted, so a widened allowlist is
      visible in the report rather than only in the config diff.

EXIT CODES
    0  clean -- no hit outside an allowed path or an explained marker
    1  at least one hit, or a fence/config failure (fail closed, never quiet)
    2  usage error

CLI
    python scripts/ops/exposure_gate.py                 # scan the repository
    python scripts/ops/exposure_gate.py --root PATH     # scan somewhere else
    python scripts/ops/exposure_gate.py --history       # also scan commit identities
    python scripts/ops/exposure_gate.py --selftest      # prove it can fire
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "Fence",
    "Finding",
    "HistoryUnavailable",
    "ScanResult",
    "load_fence",
    "render_history_report",
    "render_report",
    "scan_history",
    "scan_text",
    "scan_tree",
    "selftest",
]

_WORD = re.compile(r"[a-z0-9]+")
_MATCH_MODES = ("word", "substring")

#: Assembled rather than written out: the literal header is itself a string
#: that credential scanners refuse to let through, and a source file that
#: cannot be saved is not a working instrument.
_KEY_HEADER_SAMPLE = ("-" * 5) + "BEGIN OPENSSH PRIVATE KEY" + ("-" * 5)

#: Same reasoning, for the history-identity selftest fixtures below: a
#: literal address at a domain this file itself does not declare reserved
#: would be a finding when the gate scans its own source -- these two exist
#: only to prove the history-scope exact-digest and domain exemptions fire.
_SYNTHETIC_EXACT_EMAIL = "exact" + "@" + "allowed.example"
_SYNTHETIC_DOMAIN_EMAIL = "person" + "@" + "reserved.example"


class FenceError(RuntimeError):
    """The fence itself is unusable. Always fatal -- never a degraded scan."""


# ---------------------------------------------------------------------------
# the fence
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TokenRule:
    """One denied name, as a digest. The word itself is never in this process."""

    id: str
    sha256: str
    length: int
    match: str


@dataclass(frozen=True)
class PatternRule:
    """One denied shape. Ships in the open -- a regex is not the thing it finds."""

    id: str
    regex: "re.Pattern[str]"
    catches: str
    trades: str


@dataclass(frozen=True)
class Fence:
    """The loaded fence. Every field is load-bearing; none has a default."""

    tokens: Tuple[TokenRule, ...]
    patterns: Tuple[PatternRule, ...]
    allowed_email_domains: Tuple[str, ...]
    allowed_paths: Tuple[str, ...]
    #: path -> the token ids that path may carry. An absent path and an empty
    #: list mean the same thing: no token is exempt there. There is deliberately
    #: no value meaning "every token", because a whole-file exemption is a blind
    #: spot that renders as a clean scan.
    allowed_path_tokens: Dict[str, Tuple[str, ...]]
    allowed_line_markers: Tuple[str, ...]
    skip_dirs: Tuple[str, ...]
    skip_extensions: Tuple[str, ...]
    max_file_bytes: int
    #: HISTORY-SCOPE ONLY (see config header). name-digest -> token ids that
    #: name legitimately triggers, exempted ONLY on the specific author or
    #: committer line that name appears on -- never on the subject or body.
    history_allowed_name_tokens: Dict[str, Tuple[str, ...]]
    #: HISTORY-SCOPE ONLY. Exact digests of addresses exempt from the
    #: email-shaped pattern anywhere in a commit (identity line, subject, or
    #: body).
    history_allowed_email_digests: Tuple[str, ...]
    #: HISTORY-SCOPE ONLY. Domain suffixes exempt from the email-shaped
    #: pattern anywhere in a commit -- broader than the exact digests above.
    history_allowed_email_domains: Tuple[str, ...]

    # -- derived lookups ---------------------------------------------------

    @property
    def word_digests(self) -> Dict[str, str]:
        return {t.sha256: t.id for t in self.tokens if t.match == "word"}

    @property
    def substring_digests(self) -> Dict[int, Dict[str, str]]:
        by_len: Dict[int, Dict[str, str]] = {}
        for t in self.tokens:
            if t.match == "substring":
                by_len.setdefault(t.length, {})[t.sha256] = t.id
        return by_len


def _require(data: Mapping[str, Any], key: str, where: str) -> Any:
    """Read a load-bearing field, or HALT.

    There is no default here on purpose. A fence that half-understands its own
    configuration produces a scan that runs and is wrong, and the one field
    nobody read is the one nobody checks.
    """
    if key not in data:
        raise FenceError(
            f"{where}: required field {key!r} is missing. This field is "
            "load-bearing; the gate refuses to substitute a default for it, "
            "because a fence with an invented field is a fence nobody can "
            "reason about."
        )
    value = data[key]
    if value is None:
        raise FenceError(f"{where}: field {key!r} is null. Null is not a value here.")
    return value


def load_fence(path: Path) -> Fence:
    """Load and validate ``config/exposure-fence.yaml``. Raises on anything odd."""
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise FenceError(
            "PyYAML is required to read the exposure fence and is not "
            "installed. Remedy: pip install pyyaml. The gate will not "
            "hand-parse the config -- a half-read fence is worse than none."
        ) from exc

    if not path.is_file():
        raise FenceError(f"fence config not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise FenceError(f"{path}: top level must be a mapping")

    schema = _require(raw, "schema", str(path))
    if schema != "exposure-fence/v1":
        raise FenceError(
            f"{path}: unknown schema {schema!r}. An unrecognised schema is a "
            "hard exit, never a best-effort read."
        )
    _require(raw, "as_of", str(path))

    tokens: List[TokenRule] = []
    for i, entry in enumerate(_require(raw, "tokens", str(path))):
        where = f"{path}: tokens[{i}]"
        if not isinstance(entry, dict):
            raise FenceError(f"{where}: must be a mapping")
        mode = str(_require(entry, "match", where))
        if mode not in _MATCH_MODES:
            raise FenceError(
                f"{where}: match mode {mode!r} is not declared. Declared modes "
                f"are {_MATCH_MODES}; an undeclared mode is a hard exit."
            )
        digest = str(_require(entry, "sha256", where)).strip().lower()
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise FenceError(f"{where}: sha256 is not a 64-character hex digest")
        length = int(_require(entry, "length", where))
        if length < 1:
            raise FenceError(f"{where}: length must be positive")
        tokens.append(TokenRule(str(_require(entry, "id", where)), digest, length, mode))
    if not tokens:
        raise FenceError(
            f"{path}: the token list is empty. An empty denylist would make "
            "every scan green by construction, which is the failure this gate "
            "exists to prevent."
        )

    patterns: List[PatternRule] = []
    for i, entry in enumerate(_require(raw, "patterns", str(path))):
        where = f"{path}: patterns[{i}]"
        if not isinstance(entry, dict):
            raise FenceError(f"{where}: must be a mapping")
        try:
            compiled = re.compile(str(_require(entry, "regex", where)))
        except re.error as exc:
            raise FenceError(f"{where}: regex does not compile: {exc}") from exc
        patterns.append(
            PatternRule(
                str(_require(entry, "id", where)),
                compiled,
                str(_require(entry, "catches", where)),
                str(_require(entry, "trades", where)),
            )
        )
    if not patterns:
        raise FenceError(f"{path}: at least one shape pattern must be declared")

    markers = tuple(str(m) for m in _require(raw, "allowed_line_markers", str(path)))
    if not markers:
        raise FenceError(f"{path}: at least one allowed-line marker must be declared")

    allowed_paths = tuple(str(p) for p in _require(raw, "allowed_paths", str(path)))
    known_paths = {p.strip("/").lower() for p in allowed_paths}
    known_tokens = {t.id for t in tokens}
    raw_pairs = _require(raw, "allowed_path_tokens", str(path))
    if not isinstance(raw_pairs, dict):
        raise FenceError(f"{path}: allowed_path_tokens must be a mapping")
    pairs: Dict[str, Tuple[str, ...]] = {}
    for key, ids in raw_pairs.items():
        norm = str(key).strip("/").lower()
        if norm not in known_paths:
            raise FenceError(
                f"{path}: allowed_path_tokens names {key!r}, which is not in "
                "allowed_paths. An exemption for a path the fence does not "
                "declare is an exemption nobody reviewed."
            )
        if ids is None:
            raise FenceError(
                f"{path}: allowed_path_tokens[{key!r}] is null. Write an empty "
                "list to mean 'no token is exempt here'; null is not a value."
            )
        if not isinstance(ids, list):
            raise FenceError(
                f"{path}: allowed_path_tokens[{key!r}] must be a list of token "
                "ids. There is no value meaning 'every token' -- a whole-file "
                "exemption is a blind spot that renders as a clean scan."
            )
        for token_id in ids:
            if str(token_id) not in known_tokens:
                raise FenceError(
                    f"{path}: allowed_path_tokens[{key!r}] exempts unknown "
                    f"token id {token_id!r}"
                )
        pairs[norm] = tuple(str(t) for t in ids)

    history_block = _require(raw, "history_allowed_identities", str(path))
    if not isinstance(history_block, dict):
        raise FenceError(f"{path}: history_allowed_identities must be a mapping")
    history_entries = _require(
        history_block, "identities", f"{path}: history_allowed_identities"
    )
    if not isinstance(history_entries, list):
        raise FenceError(f"{path}: history_allowed_identities.identities must be a list")
    history_name_tokens: Dict[str, Tuple[str, ...]] = {}
    history_email_digests: List[str] = []
    seen_history_ids: set = set()
    _HISTORY_KINDS = ("name", "email")
    for i, entry in enumerate(history_entries):
        where = f"{path}: history_allowed_identities.identities[{i}]"
        if not isinstance(entry, dict):
            raise FenceError(f"{where}: must be a mapping")
        hid = str(_require(entry, "id", where))
        if hid in seen_history_ids:
            raise FenceError(f"{where}: duplicate identity id {hid!r}")
        seen_history_ids.add(hid)
        kind = str(_require(entry, "kind", where))
        if kind not in _HISTORY_KINDS:
            raise FenceError(
                f"{where}: kind {kind!r} is not declared. Declared kinds are "
                f"{_HISTORY_KINDS}; an undeclared kind is a hard exit."
            )
        digest = str(_require(entry, "sha256", where)).strip().lower()
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise FenceError(f"{where}: sha256 is not a 64-character hex digest")
        if kind == "name":
            exempts_raw = entry.get("exempts")
            if not isinstance(exempts_raw, list) or not exempts_raw:
                raise FenceError(
                    f"{where}: a 'name' identity must declare a non-empty "
                    "'exempts' list of token ids -- a name exemption with "
                    "nothing to exempt does nothing and is not a valid entry."
                )
            for token_id in exempts_raw:
                if str(token_id) not in known_tokens:
                    raise FenceError(
                        f"{where}: exempts unknown token id {token_id!r}"
                    )
            history_name_tokens[digest] = tuple(str(t) for t in exempts_raw)
        else:
            if entry.get("exempts"):
                raise FenceError(
                    f"{where}: an 'email' identity does not exempt tokens; "
                    "remove 'exempts' or declare kind: name."
                )
            history_email_digests.append(digest)
    history_domains_raw = _require(
        history_block, "allowed_email_domains", f"{path}: history_allowed_identities"
    )
    if not isinstance(history_domains_raw, list):
        raise FenceError(
            f"{path}: history_allowed_identities.allowed_email_domains must be a list"
        )

    return Fence(
        tokens=tuple(tokens),
        patterns=tuple(patterns),
        allowed_email_domains=tuple(
            str(d).lower() for d in _require(raw, "allowed_email_domains", str(path))
        ),
        allowed_paths=allowed_paths,
        allowed_path_tokens=pairs,
        allowed_line_markers=markers,
        skip_dirs=tuple(str(d) for d in _require(raw, "skip_dirs", str(path))),
        skip_extensions=tuple(str(e).lower() for e in _require(raw, "skip_extensions", str(path))),
        max_file_bytes=int(_require(raw, "max_file_bytes", str(path))),
        history_allowed_name_tokens=history_name_tokens,
        history_allowed_email_digests=tuple(history_email_digests),
        history_allowed_email_domains=tuple(str(d).lower() for d in history_domains_raw),
    )


# ---------------------------------------------------------------------------
# findings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One hit. Carries the RULE id and never the matched text.

    Printing the match would write the fenced word into the CI log, which is a
    public surface. The redacted excerpt is enough to find the line.
    """

    path: str
    line_no: int
    rule_kind: str  # "token" | "pattern" | "marker"
    rule_id: str
    excerpt: str

    def render(self) -> str:
        return f"{self.path}:{self.line_no}: {self.rule_kind} {self.rule_id} | {self.excerpt}"


@dataclass
class ScanResult:
    """The population and what was found in it. Rates are never printed alone."""

    findings: List[Finding] = field(default_factory=list)
    files_scanned: int = 0
    files_unscanned: List[Tuple[str, str]] = field(default_factory=list)
    files_skipped: int = 0
    lines_read: int = 0
    marker_lines: int = 0
    bad_markers: List[Finding] = field(default_factory=list)
    #: HISTORY-SCOPE counters. Always 0 for a tree scan. Reported loudly
    #: rather than folded silently into "clean" -- an exemption changes what
    #: a CLEAN verdict means, and the report says so every time, never only
    #: when something is found (no-silent-failures.md rule 1: a refusal, and
    #: an exemption is a kind of refusal-to-look, must stay countable).
    identities_checked: int = 0
    identities_exempted: int = 0
    emails_exempted: int = 0

    @property
    def clean(self) -> bool:
        return not self.findings and not self.bad_markers


def _redact_spans(
    line: str,
    spans: Sequence[Tuple[int, int, str]],
    focus: Tuple[int, int],
) -> str:
    """Excerpt one hit with EVERY fenced span on the line redacted.

    Redacting only the hit being reported is not enough. A line that carries
    two fenced things -- a name beside an address, which is the ordinary shape
    of a commit author line -- would then publish each one inside the other's
    excerpt, into a CI log, which is a public surface. So every span any rule
    matched on this line is blanked, including spans that were exempted or
    allowed: appearing in a FILE and appearing in a LOG are different
    permissions, and only the first was ever granted.
    """
    body = line.rstrip("\n")
    merged: List[Tuple[int, int, str]] = []
    for start, end, rule in sorted(spans, key=lambda s: (s[0], -s[1])):
        if merged and start < merged[-1][1]:
            prev_start, prev_end, prev_rule = merged[-1]
            if end > prev_end:
                merged[-1] = (prev_start, end, prev_rule)
            continue
        merged.append((start, end, rule))

    parts: List[str] = []
    cursor = 0
    focus_start: Optional[int] = None
    focus_end: Optional[int] = None
    for start, end, rule in merged:
        parts.append(body[cursor:start])
        placeholder = f"[REDACTED:{rule}]"
        if focus_start is None and start <= focus[0] < end:
            focus_start = sum(len(p) for p in parts)
            focus_end = focus_start + len(placeholder)
        parts.append(placeholder)
        cursor = end
    parts.append(body[cursor:])
    rebuilt = "".join(parts)

    if focus_start is None or focus_end is None:
        focus_start, focus_end = 0, min(len(rebuilt), 24)
    lo = max(0, focus_start - 24)
    hi = min(len(rebuilt), focus_end + 24)
    return rebuilt[lo:hi].strip()


def _marker_on(line: str, fence: Fence) -> Optional[Tuple[str, bool]]:
    """Return (marker, has_reason) when the line carries an allow marker."""
    low = line.lower()
    for marker in fence.allowed_line_markers:
        idx = low.find(marker.lower())
        if idx >= 0:
            tail = line[idx + len(marker):].strip(" \t-:#*/")
            return marker, len(tail) >= 3
    return None


def scan_text(
    text: str,
    fence: Fence,
    *,
    path: str,
    exempt_token_ids: Sequence[str] = (),
    result: Optional[ScanResult] = None,
    extra_allowed_email_domains: Sequence[str] = (),
    extra_allowed_email_digests: Sequence[str] = (),
    line_offset: int = 0,
) -> ScanResult:
    """Scan one document. Pure over its arguments; opens nothing.

    ``exempt_token_ids`` is a set of specific token ids this document may
    legitimately carry -- never a blanket "skip the names here". The shape
    patterns are never exempt anywhere, for anyone -- EXCEPT the email-shaped
    pattern, which two more parameters can clear: ``extra_allowed_email_domains``
    (a domain suffix) and ``extra_allowed_email_digests`` (an exact address
    digest). Both are HISTORY-SCOPE constructs (``scan_history`` is their only
    caller with non-empty values); the tree walk never passes them, so
    ``scan_tree`` is unaffected by their existence.

    ``line_offset`` shifts reported line numbers by a constant -- used by
    ``scan_history`` to scan a commit's author line, committer line, and
    subject/body as three separate calls (each needing its own exemption
    scope) while the reported line numbers still read 1, 2, 3... as if the
    whole commit had been scanned in one pass.
    """
    res = result if result is not None else ScanResult()
    exempt = frozenset(exempt_token_ids)
    extra_domains = tuple(extra_allowed_email_domains)
    extra_digests = frozenset(extra_allowed_email_digests)
    word_digests = fence.word_digests
    substring_digests = fence.substring_digests

    for line_no, line in enumerate(text.splitlines(), start=1 + line_offset):
        res.lines_read += 1

        marker = _marker_on(line, fence)
        if marker is not None:
            res.marker_lines += 1
            if not marker[1]:
                # A bare marker is the untagged-switch failure: off-by-design
                # and off-by-neglect look identical from outside.
                res.bad_markers.append(
                    Finding(path, line_no, "marker", "unexplained-exemption",
                            "allow marker carries no reason")
                )
            continue

        # Two passes over the line. The first finds every fenced span,
        # whether or not it will be reported; the second renders the ones
        # that are, with ALL of them blanked. One pass cannot do this: the
        # excerpt for the first hit has to know about a hit found later.
        spans: List[Tuple[int, int, str]] = []
        reported: List[Tuple[int, int, str, str]] = []  # start, end, kind, rule

        for pat in fence.patterns:
            for m in pat.regex.finditer(line):
                spans.append((m.start(), m.end(), pat.id))
                if pat.id == "email-shaped":
                    address = m.group(0).strip().lower()
                    domain = address.rsplit("@", 1)[-1].rstrip(".")
                    if any(domain == d or domain.endswith("." + d)
                           for d in fence.allowed_email_domains):
                        continue
                    if extra_domains and any(
                        domain == d or domain.endswith("." + d) for d in extra_domains
                    ):
                        res.emails_exempted += 1
                        continue
                    if extra_digests:
                        addr_digest = hashlib.sha256(address.encode("utf-8")).hexdigest()
                        if addr_digest in extra_digests:
                            res.emails_exempted += 1
                            continue
                reported.append((m.start(), m.end(), "pattern", pat.id))

        low = line.lower()
        for wm in _WORD.finditer(low):
            word = wm.group(0)
            digest = hashlib.sha256(word.encode("ascii")).hexdigest()
            hit_id = word_digests.get(digest)
            if hit_id is not None:
                # A whole-word match is decided here either way: reported, or
                # exempt and dropped. Falling through to the substring pass
                # would re-find the same word under a second rule and report an
                # exemption the fence had just granted.
                spans.append((wm.start(), wm.end(), hit_id))
                if hit_id not in exempt:
                    reported.append((wm.start(), wm.end(), "token", hit_id))
                continue
            for length, table in substring_digests.items():
                if len(word) < length:
                    continue
                for off in range(len(word) - length + 1):
                    piece = word[off:off + length]
                    pd = hashlib.sha256(piece.encode("ascii")).hexdigest()
                    sub_id = table.get(pd)
                    if sub_id is None:
                        continue
                    start = wm.start() + off
                    spans.append((start, start + length, sub_id))
                    if sub_id not in exempt:
                        reported.append((start, start + length, "token", sub_id))

        for start, end, kind, rule_id in reported:
            res.findings.append(
                Finding(path, line_no, kind, rule_id,
                        _redact_spans(line, spans, (start, end)))
            )
    return res


# ---------------------------------------------------------------------------
# the walk
# ---------------------------------------------------------------------------


def _skip_dir(rel_posix: str, name: str, fence: Fence) -> bool:
    return name in fence.skip_dirs or rel_posix in fence.skip_dirs


def _iter_files(root: Path, fence: Fence) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        rel_dir = here.relative_to(root).as_posix()
        dirnames[:] = sorted(
            d for d in dirnames
            if not _skip_dir((f"{rel_dir}/{d}" if rel_dir != "." else d), d, fence)
        )
        for name in sorted(filenames):
            yield here / name


def scan_tree(root: Path, fence: Fence) -> ScanResult:
    """Walk ``root`` and scan every readable text file under it."""
    res = ScanResult()
    for file_path in _iter_files(root, fence):
        rel = file_path.relative_to(root).as_posix()
        if file_path.suffix.lower() in fence.skip_extensions:
            res.files_skipped += 1
            continue
        try:
            size = file_path.stat().st_size
        except OSError as exc:
            res.files_unscanned.append((rel, f"stat failed: {exc}"))
            continue
        if size > fence.max_file_bytes:
            res.files_unscanned.append((rel, f"above max_file_bytes ({size} bytes)"))
            continue
        try:
            raw = file_path.read_bytes()
        except OSError as exc:
            res.files_unscanned.append((rel, f"read failed: {exc}"))
            continue
        if b"\x00" in raw:
            res.files_skipped += 1
            continue
        res.files_scanned += 1
        scan_text(
            raw.decode("utf-8", errors="replace"),
            fence,
            path=rel,
            exempt_token_ids=fence.allowed_path_tokens.get(rel.lower(), ()),
            result=res,
        )
    return res


# ---------------------------------------------------------------------------
# history
#
# The working tree is not the whole published surface. A commit carries an
# author and a committer -- a name and an address taken from whoever ran the
# command, not from anything in the tree -- and those travel with every clone
# and every forge page. A repository whose files are spotless can still
# publish a private name on its very first push, and until this existed
# nothing here could see that.
# ---------------------------------------------------------------------------


class HistoryUnavailable(RuntimeError):
    """Raised when the history could not be read at all.

    Deliberately not a quiet zero: an unreadable history that graded as clean
    would be the denominator failure this project keeps finding elsewhere --
    the population shrinks and the number gets better.
    """


_GIT_FIELD = "\x1f"
_GIT_RECORD = "\x1e"
_GIT_FORMAT = _GIT_FIELD.join(
    ["%H", "%an", "%ae", "%cn", "%ce", "%s", "%b"]) + _GIT_RECORD

#: Substrings git uses when a repository exists but carries no commits yet.
#: That is an EMPTY population, which is a different thing from an unreadable
#: one, and the two must not collapse into the same verdict.
_EMPTY_HISTORY_SIGNS = ("does not have any commits",
                        "unknown revision",
                        "bad default revision")


def _run_git(root: Path, args: Sequence[str]) -> Tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    except OSError as exc:  # git absent from PATH
        raise HistoryUnavailable(f"git could not be run: {exc}") from exc
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _name_digest(name: str) -> str:
    """Digest a display name the same way the fence's identity entries do."""
    return hashlib.sha256(name.strip().lower().encode("utf-8")).hexdigest()


def scan_history(
    root: Path,
    fence: Fence,
    *,
    limit: Optional[int] = None,
    log_text: Optional[str] = None,
) -> Tuple[ScanResult, int]:
    """Scan commit identities and messages. Returns ``(result, commits)``.

    ``log_text`` injects a log instead of running git, which is how the
    selftest proves this can fire without needing a repository to exist.

    History gets NO path exemptions. ``allowed_paths`` names places in the
    working tree where a reserved name may legitimately appear; an author
    line is not one of them, and inheriting that exemption here would let the
    one surface this function exists to watch exempt itself.

    ``fence.history_allowed_identities`` (loaded from the config's
    ``history_allowed_identities`` block) is scoped even tighter than that:
    the author and committer lines are scanned SEPARATELY from the subject
    and body, each with only the exemption its own identity earns. A name
    that clears the author line is still a finding if the same word turns up
    in the commit message -- the allowlist exempts a KNOWN IDENTITY, not a
    word, and the subject/body were never that identity's line to begin with.
    """
    if log_text is None:
        code, out, err = _run_git(root, ["rev-parse", "--git-dir"])
        if code != 0:
            raise HistoryUnavailable(
                f"not a git repository (or git refused): {err.strip() or code}")
        args = ["log", "--all", f"--format={_GIT_FORMAT}"]
        if limit is not None:
            args.append(f"-n{int(limit)}")
        code, out, err = _run_git(root, args)
        if code != 0:
            low = err.lower()
            if any(sign in low for sign in _EMPTY_HISTORY_SIGNS):
                return ScanResult(), 0
            raise HistoryUnavailable(f"git log failed: {err.strip() or code}")
        log_text = out

    res = ScanResult()
    commits = 0
    for record in log_text.split(_GIT_RECORD):
        record = record.strip("\n")
        if not record.strip():
            continue
        fields = record.split(_GIT_FIELD)
        sha = (fields[0] or "?").strip()
        _, an, ae, cn, ce, subject, body = (list(fields) + [""] * 7)[:7]
        commits += 1
        res.files_scanned += 1
        commit_path = f"git:{sha[:12]}"

        for role_label, name, email, offset in (
            ("author", an, ae, 0),
            ("committer", cn, ce, 1),
        ):
            res.identities_checked += 1
            exempt_tokens = fence.history_allowed_name_tokens.get(_name_digest(name), ())
            if exempt_tokens:
                res.identities_exempted += 1
            scan_text(
                f"{role_label}: {name} <{email}>\n", fence, path=commit_path,
                exempt_token_ids=exempt_tokens,
                extra_allowed_email_domains=fence.history_allowed_email_domains,
                extra_allowed_email_digests=fence.history_allowed_email_digests,
                line_offset=offset, result=res,
            )

        rest_lines = [f"subject: {subject}"]
        if body.strip():
            rest_lines.extend(body.splitlines())
        # No exempt_token_ids here: the subject/body is nobody's identity
        # line, so a fenced word there is still a finding even for an
        # allowlisted identity. The email exemption stays -- the co-author
        # trailer's address lives in the body, not on an identity line.
        scan_text(
            "\n".join(rest_lines) + "\n", fence, path=commit_path,
            extra_allowed_email_domains=fence.history_allowed_email_domains,
            extra_allowed_email_digests=fence.history_allowed_email_digests,
            line_offset=2, result=res,
        )
    return res, commits


def render_history_report(res: ScanResult, commits: int, root: Path) -> str:
    lines = [f"exposure gate over the history of {root}",
             f"  population: {commits} commits read "
             f"(identities and messages; never historical file content)",
             f"  history allowlist: {res.identities_checked} identity checks "
             f"(author + committer), {res.identities_exempted} matched an "
             f"allowlisted identity, {res.emails_exempted} address(es) "
             f"cleared by the history-scope email allowlist -- never silent, "
             f"printed whether or not it changed the verdict"]
    if res.findings or res.bad_markers:
        lines.append(f"  FINDINGS: {len(res.findings)}")
        for finding in res.findings + res.bad_markers:
            lines.append("    " + finding.render())
        lines.append("  A commit's author and committer come from the machine "
                     "that made it, so this is corrected by rewriting the "
                     "identity on those commits -- never by editing a file.")
    elif commits == 0:
        lines.append("  VERDICT: EMPTY -- this repository has no commits yet. "
                     "That is an empty population, not a clean bill; the first "
                     "commit is the first thing to re-scan.")
    else:
        lines.append("  VERDICT: CLEAN")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------


def render_report(res: ScanResult, fence: Fence, root: Path) -> str:
    lines: List[str] = []
    lines.append(f"exposure gate over {root}")
    lines.append(
        f"  population: {res.files_scanned} files scanned, "
        f"{res.files_skipped} skipped (binary or listed extension), "
        f"{len(res.files_unscanned)} UNSCANNED, {res.lines_read} lines read"
    )
    lines.append(
        f"  fence: {len(fence.tokens)} name digests, {len(fence.patterns)} shapes, "
        f"{len(fence.allowed_paths)} allowed paths, {res.marker_lines} marker lines"
    )
    for rel, why in res.files_unscanned:
        lines.append(f"  UNSCANNED {rel}: {why}")
    for f in res.bad_markers:
        lines.append(f"  BAD MARKER {f.render()}")
    for f in res.findings:
        lines.append(f"  HIT {f.render()}")
    if res.clean:
        lines.append("  VERDICT: CLEAN")
        lines.append(
            "  What this does and does not say: I looked for "
            f"{len(fence.tokens)} declared names and {len(fence.patterns)} "
            "declared shapes; my sensitivity is exact-digest over extracted "
            "words and regex over lines, on the working tree only; I found "
            "none; therefore none of THOSE is present above THAT sensitivity. "
            "It is not a statement about anything the fence does not declare, "
            "nor about git history."
        )
    else:
        lines.append(
            f"  VERDICT: EXPOSED -- {len(res.findings)} hit(s), "
            f"{len(res.bad_markers)} unexplained exemption(s)"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


def _synthetic_fence(planted: str) -> Fence:
    """A fence built around a token invented at runtime.

    The selftest never plants a REAL fenced name: that would write one into a
    temp tree and, on a bad day, into a log. It proves the MECHANISM fires,
    which is a different claim from proving the denylist is complete -- and
    the difference is stated rather than glossed.
    """
    digest = hashlib.sha256(planted.encode("ascii")).hexdigest()
    return Fence(
        tokens=(TokenRule("planted-substring", digest, len(planted), "substring"),
                TokenRule("planted-word", digest, len(planted), "word")),
        patterns=(
            PatternRule("email-shaped",
                        re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
                        "an address", "example domains are allowed"),
            PatternRule("key-header",
                        re.compile(r"-{5}BEGIN [A-Z ]*PRIVATE KEY-{5}"),
                        "key material", "nothing"),
        ),
        allowed_email_domains=("example.com",),
        allowed_paths=("docs/exempt.md",),
        allowed_path_tokens={"docs/exempt.md": ("planted-word", "planted-substring")},
        allowed_line_markers=("exposure-gate: allow",),
        skip_dirs=(".git",),
        skip_extensions=(".png",),
        max_file_bytes=64,
        # A name and two addresses invented for the selftest, unrelated to
        # ``planted`` -- proving the identity mechanism fires is a different
        # claim from proving the planted-token mechanism fires, and mixing
        # them would let one mask the other going untested.
        history_allowed_name_tokens={
            _name_digest("Permitted Person"): ("planted-word", "planted-substring"),
        },
        history_allowed_email_digests=(
            hashlib.sha256(_SYNTHETIC_EXACT_EMAIL.encode("utf-8")).hexdigest(),
        ),
        history_allowed_email_domains=("reserved.example",),
    )


def selftest() -> int:
    """Prove every verdict path can fire.

    A detector that has never fired is indistinguishable from a broken one, so
    each of the ways this gate can say something -- found, cleared, refused,
    unscanned -- gets a case that makes it say it.
    """
    planted = "zqxwvutsrp"
    fence = _synthetic_fence(planted)
    failures: List[str] = []
    checks = 0

    def check(label: str, ok: bool) -> None:
        nonlocal checks
        checks += 1
        print(f"  [{'ok' if ok else 'FAIL'}] {label}")
        if not ok:
            failures.append(label)

    # 1-3: the three ways a hit is found.
    r = scan_text(f"a line naming {planted} in prose", fence, path="a.md")
    check("a planted name is found as a whole word", len(r.findings) >= 1)
    r = scan_text(f"prefix{planted}suffix", fence, path="a.md")
    check("a planted name is found inside a longer word",
          any(f.rule_id == "planted-substring" for f in r.findings))
    # Assembled, not written out: a literal address in this file would be a
    # finding when the gate scans its own source, and an instrument that
    # cannot pass its own scan teaches people to exempt it.
    sample_address = "someone" + "@" + "somewhere.test"
    r = scan_text(f"write to {sample_address} today", fence, path="a.md")
    check("an address shape is found", any(f.rule_id == "email-shaped" for f in r.findings))

    # 4: the redaction promise -- the log must not carry the word back.
    r = scan_text(f"a line naming {planted} in prose", fence, path="a.md")
    check("a finding never echoes the matched text",
          bool(r.findings) and all(planted not in f.excerpt for f in r.findings))

    # 4b: and never echoes a DIFFERENT fenced thing on the same line. This is
    # the ordinary shape of a commit author line -- a name beside an address --
    # and redacting only the reported span published each one inside the
    # other's excerpt.
    crowded = f"author: {planted} Person <{sample_address}>"
    r = scan_text(crowded, fence, path="a.md")
    check("a line with two fenced things leaks neither into the other's excerpt",
          len(r.findings) >= 2
          and all(planted not in f.excerpt and sample_address not in f.excerpt
                  for f in r.findings))

    # 5-6: the two ways a hit is legitimately cleared.
    r = scan_text(f"{planted}  # exposure-gate: allow this is the reserved instance",
                  fence, path="a.md")
    check("an explained marker clears the line", r.clean)
    r = scan_text(f"a line naming {planted}", fence, path="docs/exempt.md",
                  exempt_token_ids=fence.allowed_path_tokens["docs/exempt.md"])
    check("a path exempted for THAT token does not report it", not r.findings)

    r = scan_text(f"a line naming {planted}", fence, path="docs/exempt.md",
                  exempt_token_ids=("some-other-token",))
    check("a path exemption covers only the tokens it names", bool(r.findings))

    # 7: and the one way it is not.
    r = scan_text(f"{planted}  # exposure-gate: allow", fence, path="a.md")
    check("a bare marker with no reason is itself a finding", bool(r.bad_markers))

    # 8: an allowed path is NOT exempt from shapes.
    r = scan_text(_KEY_HEADER_SAMPLE, fence, path="docs/exempt.md",
                  exempt_token_ids=fence.allowed_path_tokens["docs/exempt.md"])
    check("an allowed path is still checked for key material",
          any(f.rule_id == "key-header" for f in r.findings))

    # 9: a reserved documentation domain is not a correspondent.
    r = scan_text("mail nobody@example.com about it", fence, path="a.md")
    check("a reserved example domain is not a finding", r.clean)

    # 10: a clean line is clean.
    check("a clean line produces no finding",
          scan_text("nothing to see", fence, path="a.md").clean)

    # 11-14: the walk -- population accounting, and a refusal that stays in it.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "clean.md").write_text("ordinary prose\n", encoding="utf-8")
        (root / "dirty.md").write_text(f"names {planted}\n", encoding="utf-8")
        (root / "big.md").write_text("x" * (fence.max_file_bytes + 1), encoding="utf-8")
        (root / "shot.png").write_bytes(b"\x89PNG binary")
        (root / ".git").mkdir()
        (root / ".git" / "hidden.md").write_text(f"names {planted}\n", encoding="utf-8")
        walked = scan_tree(root, fence)
        check("the walk finds the planted file",
              any(f.path == "dirty.md" for f in walked.findings))
        check("an oversized file is UNSCANNED and stays in the denominator",
              any(rel == "big.md" for rel, _ in walked.files_unscanned))
        check("a skipped directory is not walked",
              not any(f.path.startswith(".git/") for f in walked.findings))
        check("skipped and scanned are counted separately",
              walked.files_scanned == 2 and walked.files_skipped == 1)

    # 15-19: the history surface. A commit's author line is written by the
    # machine that made it, so it is the one place a spotless tree can still
    # publish a private name.
    def _log(*records: Sequence[str]) -> str:
        return _GIT_RECORD.join(_GIT_FIELD.join(r) for r in records) + _GIT_RECORD

    planted_log = _log(("abc123def456", f"A {planted} Person", "a@example.com",
                        "A Person", "a@example.com", "a subject", ""))
    hist, count = scan_history(Path("."), fence, log_text=planted_log)
    check("a planted name in an author line is found",
          count == 1 and any(f.path.startswith("git:") for f in hist.findings))
    check("a history finding never echoes the matched text",
          bool(hist.findings) and all(planted not in f.excerpt for f in hist.findings))

    addressed = _log(("abc123def456", "A Person", "someone" + "@" + "somewhere.test",
                      "A Person", "someone" + "@" + "somewhere.test", "s", ""))
    hist, _ = scan_history(Path("."), fence, log_text=addressed)
    check("an address outside the reserved domains is found in an identity",
          any(f.rule_id == "email-shaped" for f in hist.findings))

    clean_log = _log(("abc123def456", "A Person", "nobody@example.com",
                      "A Person", "nobody@example.com", "ordinary subject", ""))
    hist, count = scan_history(Path("."), fence, log_text=clean_log)
    check("an ordinary commit record is clean", hist.clean and count == 1)

    with tempfile.TemporaryDirectory() as tmp:
        try:
            scan_history(Path(tmp), fence)
            fired = False
        except HistoryUnavailable:
            fired = True
        check("an unreadable history refuses rather than reporting clean", fired)

    # 20-25: the history identity allowlist. An allowlisted NAME exempts only
    # its own declared tokens, only on its own identity line; an allowlisted
    # ADDRESS (by exact digest or by domain) clears the email-shaped pattern
    # anywhere in the commit, because a co-author trailer lives in the body,
    # not on an identity line.
    own_name_log = _log(("abc123def456", "Permitted Person", f"{planted}@example.com",
                        "Permitted Person", f"{planted}@example.com", "ordinary subject", ""))
    hist, count = scan_history(Path("."), fence, log_text=own_name_log)
    check("an allowlisted identity's own declared token is exempted on its identity line",
          hist.clean and count == 1 and hist.identities_exempted == 2)

    body_leak_log = _log(("abc123def456", "Permitted Person", "safe@example.com",
                         "Permitted Person", "safe@example.com", "a subject",
                         f"mentions {planted} in the body"))
    hist, _ = scan_history(Path("."), fence, log_text=body_leak_log)
    check("the same allowlisted identity's message body is still scanned for its own token",
          any(f.rule_id in ("planted-word", "planted-substring") for f in hist.findings))

    domain_log = _log(("abc123def456", "A Person", _SYNTHETIC_DOMAIN_EMAIL,
                       "A Person", _SYNTHETIC_DOMAIN_EMAIL, "subject",
                       f"contact {_SYNTHETIC_DOMAIN_EMAIL} about it"))
    hist, _ = scan_history(Path("."), fence, log_text=domain_log)
    check("a history-scope allowed email domain clears the email-shaped pattern anywhere in the commit",
          hist.clean and hist.emails_exempted >= 1)

    exact_log = _log(("abc123def456", "A Person", _SYNTHETIC_EXACT_EMAIL,
                      "A Person", _SYNTHETIC_EXACT_EMAIL, "subject", ""))
    hist, _ = scan_history(Path("."), fence, log_text=exact_log)
    check("a history-scope allowed exact email digest clears the email-shaped pattern",
          hist.clean and hist.emails_exempted >= 1)

    hist, count = scan_history(Path("."), fence, log_text=clean_log)
    check("identity checks are counted for every commit whether or not any identity matches",
          hist.identities_checked == 2 and hist.identities_exempted == 0)

    rendered = render_history_report(hist, count, Path("."))
    check("the history report states identity-check and exemption counts even when nothing fired",
          "history allowlist:" in rendered)

    # 26: an unusable fence is fatal, never a quiet clean run.
    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "fence.yaml"
        bad.write_text("schema: exposure-fence/v1\nas_of: 'x'\n", encoding="utf-8")
        try:
            load_fence(bad)
            fired = False
        except FenceError:
            fired = True
        check("a fence missing a load-bearing field halts", fired)

    print(f"\n{checks - len(failures)}/{checks} selftest checks behaved as declared")
    if failures:
        print("SELFTEST FAIL: " + "; ".join(failures))
        return 1
    print("SELFTEST PASS")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _default_root() -> Path:
    return Path(__file__).resolve().parents[2]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan the tree for fenced names and shapes.")
    parser.add_argument("--root", type=Path, default=None,
                        help="tree to scan (default: the repository this script lives in)")
    parser.add_argument("--fence", type=Path, default=None,
                        help="fence config (default: <root>/config/exposure-fence.yaml)")
    parser.add_argument("--history", action="store_true",
                        help="also scan commit identities and messages")
    parser.add_argument("--selftest", action="store_true",
                        help="prove the gate can fire, then exit")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.selftest:
        return selftest()

    root = (args.root or _default_root()).resolve()
    fence_path = args.fence or (root / "config" / "exposure-fence.yaml")
    try:
        fence = load_fence(fence_path)
    except FenceError as exc:
        print(f"exposure gate: FENCE UNUSABLE -- {exc}", file=sys.stderr)
        return 1

    res = scan_tree(root, fence)
    print(render_report(res, fence, root))
    ok = res.clean

    if args.history:
        print()
        try:
            hist, commits = scan_history(root, fence)
        except HistoryUnavailable as exc:
            # Fail closed and loud. An unreadable history is not an empty one.
            print(f"exposure gate: HISTORY UNSCANNED -- {exc}", file=sys.stderr)
            ok = False
        else:
            print(render_history_report(hist, commits, root))
            ok = ok and hist.clean

    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
