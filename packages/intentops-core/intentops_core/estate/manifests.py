"""Loader and contract for the six blank-estate manifests.

PURPOSE
    Read ``estate/{ESTATE-MAP,ASSETS,RESOURCES,CAPABILITIES,GRANTS,
    DEPENDENCIES}.yaml`` and either return a fully-validated ``Estate`` or HALT
    with a remedy. The contract is the design's, restated once here in code so
    there is exactly one place that decides what a manifest means.

    The two rules that govern everything below:

      * ``entries: []`` is VALID -- a new node truthfully owns nothing yet.
      * a MISSING file is a HALT -- that is a field nobody read, and the loader
        cannot tell "no venture estate" from "the packaging dropped it".

    Load-bearing fields have NO DEFAULTS. ``telemetry_source`` on a resource,
    ``max_concurrent`` on a resource, ``granted_by`` and ``expires_at`` on a
    grant, and ``health_probe`` on a dependency all HALT when missing or null,
    because a reader that substitutes a default for a missing field has taken
    that field out of the population: nothing errors, the output looks right,
    and the one field nobody read is the one nobody checks.

    Two deliberate non-HALT outcomes, both of which stay VISIBLE:

      * ``health_probe: null`` WITH an ``unprobeable_reason`` materialises as
        ``{"type": "unprobeable", "reason": <text>}``. The dependency stays in
        the denominator and grades not-ok carrying why. Dropping it instead
        would make every coverage number look better, which is why that failure
        survives review. Without the reason it is a HALT.
      * a grant whose ``granted_by`` is not this node's operator root RAISES
        NOTHING. It is not applied, and it is not silent either: a
        ``ManifestWarning`` names the entry and the foreign key id. A node
        inherits authority from the person answerable for it and from nobody
        else.

WRITE MODEL
    This module is a READER. It never writes a manifest, and genesis never adds
    a grant. The manifests themselves are single-writer stores: the operator, by
    hand or through one explicit generator, one process at a time. There is no
    concurrent programmatic writer, so no lock is claimed here; if one is ever
    added, that writer owns the locked fresh-read read-modify-write, not this
    module.

BLIND SPOTS
    - The vocabularies below are closed sets of STRINGS. This module cannot tell
      a plausible-but-wrong value ("prod-ask") from a typo; it can only tell
      declared from undeclared.
    - ``as_of`` is validated as an ISO date, never against reality. A manifest
      can be internally perfect and describe a world that moved last week; that
      is a belief-currency question and a different instrument's job.
    - ``estate`` references in ASSETS/GRANTS are checked against ESTATE-MAP ids
      only when ESTATE-MAP loaded cleanly. If it did not, those cross-references
      go unchecked and the loader says so rather than inventing a verdict.
    - Ordering inside a manifest is not meaningful to this loader; a caller that
      relies on file order is relying on something not guaranteed.
    - YAML parsing is delegated. If the YAML library is absent this HALTs with
      the remedy; it never falls back to a partial hand-rolled parse.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "ManifestError",
    "ManifestWarning",
    "LoadedManifest",
    "Estate",
    "ManifestSpec",
    "MANIFEST_SPECS",
    "MANIFEST_FILENAMES",
    "CAPABILITY_KIND_IDS",
    "ACCOUNTINGS",
    "collect_problems",
    "load_estate",
]


# --------------------------------------------------------------------------
# Failure vocabulary. There is no fourth outcome: HALT (raise), WARN (record),
# or a clean load.
# --------------------------------------------------------------------------


class ManifestError(Exception):
    """A HALT. Carries the remedy, so a caller can print what to do."""

    def __init__(
        self,
        message: str,
        remedy: str,
        *,
        path: Path | None = None,
        subject: str | None = None,
    ) -> None:
        where = f" [{path}]" if path is not None else ""
        what = f" ({subject})" if subject else ""
        super().__init__(f"HALT{where}{what}: {message}\n  remedy: {remedy}")
        self.message = message
        self.remedy = remedy
        self.path = path
        self.subject = subject


@dataclass(frozen=True)
class ManifestWarning:
    """A recorded non-fatal finding. Never a silent drop."""

    code: str
    subject: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover - formatting only
        return f"WARN {self.code}: {self.subject} -- {self.detail}"


# --------------------------------------------------------------------------
# Closed vocabularies. An undeclared value is a hard exit, never a default of
# everything-applies.
# --------------------------------------------------------------------------

ESTATE_KINDS: tuple[str, ...] = ("internal", "venture", "served", "household")
AUTONOMY_LEVELS: tuple[str, ...] = ("propose", "reversible", "prod-asks")
ASSET_KINDS: tuple[str, ...] = (
    "repo",
    "document",
    "dataset",
    "model",
    "device",
    "account",
    "domain",
    "credential-ref",
    "other",
)
RESOURCE_KINDS: tuple[str, ...] = (
    "compute",
    "model-pool",
    "storage",
    "network",
    "budget",
    "human-time",
)
DEPENDENCY_KINDS: tuple[str, ...] = (
    "service",
    "package",
    "host",
    "credential",
    "external-api",
)
CRITICALITIES: tuple[str, ...] = ("required", "degraded-ok", "optional")
TIER_CEILINGS: tuple[str, ...] = ("T0", "T1", "T2", "T3", "T4")

#: The nine declared capability kinds. Adding a tenth is an edit to
#: estate/CAPABILITIES.yaml AND to this tuple, deliberately in that order.
CAPABILITY_KIND_IDS: tuple[str, ...] = (
    "service",
    "estate-store",
    "model-domain",
    "loop",
    "organ",
    "absorption",
    "registry",
    "projection",
    "internal-module",
)

#: The three accountings a capability kind may owe.
ACCOUNTINGS: tuple[str, ...] = ("probe", "anchor", "monitored")

#: Applicability statuses. `n/a` is a CLAIM that the accounting was never owed
#: and always requires a stated reason.
ACCOUNTING_STATUSES: tuple[str, ...] = ("applies", "n/a")


# --------------------------------------------------------------------------
# Per-manifest specifications
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ManifestSpec:
    filename: str
    schema: str
    entries_key: str
    required_fields: tuple[str, ...]
    vocabularies: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: fields whose value may legitimately be ``None``
    nullable_fields: tuple[str, ...] = ()
    #: fields where ``None`` is a HALT even though the key is present
    no_default_fields: tuple[str, ...] = ()


MANIFEST_SPECS: dict[str, ManifestSpec] = {
    "ESTATE-MAP.yaml": ManifestSpec(
        filename="ESTATE-MAP.yaml",
        schema="estate-map/v1",
        entries_key="entries",
        required_fields=("id", "label", "kind", "autonomy", "keywords", "ruled_by", "as_of"),
        vocabularies={"kind": ESTATE_KINDS, "autonomy": AUTONOMY_LEVELS},
        no_default_fields=("kind", "autonomy", "ruled_by"),
    ),
    "ASSETS.yaml": ManifestSpec(
        filename="ASSETS.yaml",
        schema="estate-assets/v1",
        entries_key="entries",
        required_fields=(
            "id",
            "name",
            "kind",
            "estate",
            "owner",
            "sensitivity",
            "location",
            "as_of",
            "evidence",
        ),
        vocabularies={"kind": ASSET_KINDS},
        no_default_fields=("kind", "estate", "location"),
    ),
    "RESOURCES.yaml": ManifestSpec(
        filename="RESOURCES.yaml",
        schema="estate-resources/v1",
        entries_key="entries",
        required_fields=(
            "id",
            "kind",
            "limit_shape",
            "telemetry_source",
            "max_concurrent",
            "kill_switch",
            "data_fence",
            "as_of",
        ),
        vocabularies={"kind": RESOURCE_KINDS},
        # telemetry_source: an unreadable pool must SAY it is unmetered.
        # max_concurrent: a scheduler that invents one over-allocates silently.
        no_default_fields=("kind", "telemetry_source", "max_concurrent"),
    ),
    "CAPABILITIES.yaml": ManifestSpec(
        filename="CAPABILITIES.yaml",
        schema="estate-capabilities/v1",
        entries_key="population",
        required_fields=("id", "kind", "as_of"),
        vocabularies={"kind": CAPABILITY_KIND_IDS},
        no_default_fields=("kind",),
    ),
    "GRANTS.yaml": ManifestSpec(
        filename="GRANTS.yaml",
        schema="estate-grants/v1",
        entries_key="entries",
        required_fields=(
            "id",
            "granted_by",
            "scope",
            "tier_ceiling",
            "estate",
            "reaches_reality",
            "expires_at",
            "reopens_when",
            "revoked_at",
            "evidence",
        ),
        vocabularies={"tier_ceiling": TIER_CEILINGS},
        nullable_fields=("revoked_at",),
        # No perpetual grants, and no authority from an unknown hand.
        no_default_fields=("granted_by", "expires_at", "tier_ceiling", "reaches_reality"),
    ),
    "DEPENDENCIES.yaml": ManifestSpec(
        filename="DEPENDENCIES.yaml",
        schema="estate-dependencies/v1",
        entries_key="entries",
        required_fields=(
            "id",
            "kind",
            "depends_on",
            "health_probe",
            "criticality",
            "fallback",
            "as_of",
        ),
        vocabularies={"kind": DEPENDENCY_KINDS, "criticality": CRITICALITIES},
        # health_probe is handled specially: null + a reason becomes
        # {"type": "unprobeable", ...}; null without one is a HALT.
        nullable_fields=("health_probe",),
        no_default_fields=("kind", "criticality", "fallback"),
    ),
}

MANIFEST_FILENAMES: tuple[str, ...] = tuple(MANIFEST_SPECS)


# --------------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LoadedManifest:
    path: Path
    schema: str
    as_of: str
    entries: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class Estate:
    root: Path
    manifests: Mapping[str, LoadedManifest]
    kinds: Mapping[str, Mapping[str, Any]]
    applied_grants: tuple[Mapping[str, Any], ...]
    ignored_grants: tuple[Mapping[str, Any], ...]
    warnings: tuple[ManifestWarning, ...]

    def entries(self, filename: str) -> tuple[Mapping[str, Any], ...]:
        return self.manifests[filename].entries

    def owes(self, kind_id: str, accounting: str) -> bool:
        """True iff a capability of ``kind_id`` owes ``accounting``.

        Raises on an undeclared kind or accounting rather than answering
        ``False``: an undeclared kind is a hard exit, never a default of
        nothing-applies.
        """
        if accounting not in ACCOUNTINGS:
            raise ManifestError(
                f"unknown accounting {accounting!r}",
                f"use one of: {', '.join(ACCOUNTINGS)}",
            )
        try:
            row = self.kinds[kind_id]
        except KeyError:
            raise ManifestError(
                f"undeclared capability kind {kind_id!r}",
                "declare the kind in estate/CAPABILITIES.yaml before scoring anything of it",
            ) from None
        return bool(row["owes"][accounting]["status"] == "applies")


# --------------------------------------------------------------------------
# YAML access
# --------------------------------------------------------------------------


def _read_yaml(path: Path) -> Any:
    try:
        import yaml  # noqa: PLC0415 - deliberately late, so import of this
        # module never depends on a third-party package being installed.
    except ModuleNotFoundError as exc:  # pragma: no cover - environment shape
        raise ManifestError(
            "the YAML parser is not available, so no manifest can be read",
            "install PyYAML (pip install pyyaml); this loader never falls back "
            "to a partial hand-rolled parse, because half-parsing a manifest is "
            "worse than not reading it",
            path=path,
        ) from exc
    text = path.read_text(encoding="utf-8")
    try:
        return yaml.safe_load(text)
    except Exception as exc:  # yaml.YAMLError and anything it wraps
        raise ManifestError(
            f"the file is not valid YAML: {exc}",
            "fix the syntax; an unparseable manifest is not an empty one",
            path=path,
        ) from exc


# --------------------------------------------------------------------------
# Validation helpers -- each APPENDS problems rather than raising, so one run
# reports every violation instead of only the first.
# --------------------------------------------------------------------------


def _is_iso_date(value: Any) -> bool:
    if isinstance(value, _dt.date):
        return True
    if not isinstance(value, str):
        return False
    try:
        _dt.date.fromisoformat(value[:10])
    except ValueError:
        return False
    return True


def _check_header(
    data: Any, spec: ManifestSpec, path: Path, problems: list[ManifestError]
) -> bool:
    if not isinstance(data, Mapping):
        problems.append(
            ManifestError(
                "the manifest is not a mapping",
                f"the file must be a YAML mapping carrying schema, as_of and {spec.entries_key}",
                path=path,
            )
        )
        return False

    ok = True
    schema = data.get("schema")
    if schema is None:
        problems.append(
            ManifestError(
                "no `schema` declared",
                f"add `schema: {spec.schema}`; an undeclared schema is a field nobody read",
                path=path,
            )
        )
        ok = False
    elif schema != spec.schema:
        problems.append(
            ManifestError(
                f"unknown schema {schema!r}",
                f"this loader understands {spec.schema!r} only; a schema it does not "
                "understand is never read on a best-effort basis",
                path=path,
            )
        )
        ok = False

    as_of = data.get("as_of")
    if as_of is None:
        problems.append(
            ManifestError(
                "no `as_of` declared",
                "add `as_of: YYYY-MM-DD` -- a belief with no as-of has no half-life "
                "and cannot be re-asked on time",
                path=path,
            )
        )
        ok = False
    elif not _is_iso_date(as_of):
        problems.append(
            ManifestError(
                f"`as_of` is not an ISO date: {as_of!r}",
                "write it as YYYY-MM-DD",
                path=path,
            )
        )
        ok = False

    if spec.entries_key not in data:
        problems.append(
            ManifestError(
                f"no `{spec.entries_key}` key",
                f"add `{spec.entries_key}: []`. An empty list is valid and means the "
                "node truthfully has none yet; an absent key means nobody said",
                path=path,
            )
        )
        ok = False
    else:
        entries = data[spec.entries_key]
        if entries is None or not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
            problems.append(
                ManifestError(
                    f"`{spec.entries_key}` is not a list (got {type(entries).__name__})",
                    f"write `{spec.entries_key}: []` for none; `null` is not an empty list",
                    path=path,
                )
            )
            ok = False
    return ok


def _check_entry(
    entry: Any,
    index: int,
    spec: ManifestSpec,
    path: Path,
    problems: list[ManifestError],
) -> None:
    label = f"{spec.entries_key}[{index}]"
    if not isinstance(entry, Mapping):
        problems.append(
            ManifestError(
                f"entry is not a mapping (got {type(entry).__name__})",
                "each entry is a YAML mapping of the required fields",
                path=path,
                subject=label,
            )
        )
        return

    entry_id = entry.get("id")
    if isinstance(entry_id, str) and entry_id:
        label = f"{spec.entries_key}[{index}] id={entry_id}"

    for name in spec.required_fields:
        if name not in entry:
            problems.append(
                ManifestError(
                    f"required field `{name}` is missing",
                    f"add `{name}`. Where a field is load-bearing, missing means HALT, "
                    "not a default",
                    path=path,
                    subject=label,
                )
            )
            continue
        value = entry[name]
        if value is None and name in spec.no_default_fields:
            problems.append(
                ManifestError(
                    f"`{name}` is null and has no default",
                    _no_default_remedy(spec.filename, name),
                    path=path,
                    subject=label,
                )
            )
        elif value is None and name not in spec.nullable_fields:
            problems.append(
                ManifestError(
                    f"`{name}` is null",
                    f"give `{name}` a value; null is not an answer for this field",
                    path=path,
                    subject=label,
                )
            )

    for name, allowed in spec.vocabularies.items():
        if name not in entry:
            continue
        value = entry[name]
        if value is None:
            continue  # already reported above
        if value not in allowed:
            problems.append(
                ManifestError(
                    f"undeclared value for `{name}`: {value!r}",
                    "use one of: " + ", ".join(allowed)
                    + ". An undeclared value is a hard exit, never a default of "
                    "everything-applies",
                    path=path,
                    subject=label,
                )
            )


def _no_default_remedy(filename: str, name: str) -> str:
    remedies = {
        ("RESOURCES.yaml", "telemetry_source"): (
            "name where this resource's usage is read from. If it genuinely cannot be "
            "metered, say so in the string ('unmetered: the provider publishes no usage "
            "endpoint as of <date>') -- an unmetered pool you can read about is the "
            "opposite of one that is silently absent"
        ),
        ("RESOURCES.yaml", "max_concurrent"): (
            "declare the concurrency limit. It is declared, not measured; a scheduler "
            "that invents one over-allocates silently"
        ),
        ("GRANTS.yaml", "expires_at"): (
            "give the grant an end date. There are no perpetual grants: a grant nobody "
            "ever has to look at again accumulates authority in silence"
        ),
        ("GRANTS.yaml", "granted_by"): (
            "name the operator root key_id that granted this. A node inherits authority "
            "from the person answerable for it and from nobody else"
        ),
    }
    return remedies.get(
        (filename, name),
        f"give `{name}` a value; this field has no default because a reader that "
        "substitutes one takes the field out of the population",
    )


def _check_duplicate_ids(
    entries: Iterable[Mapping[str, Any]],
    spec: ManifestSpec,
    path: Path,
    problems: list[ManifestError],
) -> None:
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        entry_id = entry.get("id")
        if not isinstance(entry_id, str):
            continue
        if entry_id in seen:
            problems.append(
                ManifestError(
                    f"duplicate id {entry_id!r}",
                    "ids are the identity of a row; two rows with one id means one of "
                    "them is invisible to every lookup",
                    path=path,
                    subject=spec.entries_key,
                )
            )
        seen.add(entry_id)


def _check_capability_kinds(
    data: Mapping[str, Any], path: Path, problems: list[ManifestError]
) -> dict[str, Mapping[str, Any]]:
    kinds = data.get("kinds")
    if not isinstance(kinds, Mapping):
        problems.append(
            ManifestError(
                "no `kinds` block, or it is not a mapping",
                "CAPABILITIES.yaml ships the nine declared kinds; the kinds block is "
                "the applicability model and it arrives with the node",
                path=path,
            )
        )
        return {}

    for kind_id in CAPABILITY_KIND_IDS:
        if kind_id not in kinds:
            problems.append(
                ManifestError(
                    f"declared kind {kind_id!r} is missing from the kinds block",
                    "all nine kinds ship; a scorer with a missing kind rule cannot say "
                    "whether an accounting was owed",
                    path=path,
                    subject="kinds",
                )
            )

    for kind_id, row in kinds.items():
        subject = f"kinds.{kind_id}"
        if kind_id not in CAPABILITY_KIND_IDS:
            problems.append(
                ManifestError(
                    f"undeclared capability kind {kind_id!r}",
                    "the nine kinds are a closed set: " + ", ".join(CAPABILITY_KIND_IDS),
                    path=path,
                    subject=subject,
                )
            )
            continue
        if not isinstance(row, Mapping):
            problems.append(
                ManifestError(
                    "kind row is not a mapping",
                    "each kind carries `what` and `owes`",
                    path=path,
                    subject=subject,
                )
            )
            continue
        if not row.get("what"):
            problems.append(
                ManifestError(
                    "kind has no `what`",
                    "say what this kind of thing is; a kind nobody can define cannot be "
                    "assigned to honestly",
                    path=path,
                    subject=subject,
                )
            )
        owes = row.get("owes")
        if not isinstance(owes, Mapping):
            problems.append(
                ManifestError(
                    "kind has no `owes` block",
                    "declare probe/anchor/monitored, each with a status and a reason",
                    path=path,
                    subject=subject,
                )
            )
            continue
        for accounting in ACCOUNTINGS:
            cell = owes.get(accounting)
            cell_subject = f"{subject}.owes.{accounting}"
            if not isinstance(cell, Mapping):
                problems.append(
                    ManifestError(
                        f"`{accounting}` is missing or is not a mapping",
                        "each accounting is {status: applies|n/a, why: <reason>}",
                        path=path,
                        subject=cell_subject,
                    )
                )
                continue
            status = cell.get("status")
            if status not in ACCOUNTING_STATUSES:
                problems.append(
                    ManifestError(
                        f"undeclared status {status!r}",
                        "use one of: " + ", ".join(ACCOUNTING_STATUSES),
                        path=path,
                        subject=cell_subject,
                    )
                )
            why = cell.get("why")
            if not (isinstance(why, str) and why.strip()):
                problems.append(
                    ManifestError(
                        "no `why`",
                        "state the reason, INCLUDING for `n/a`. Marking an accounting "
                        "n/a is a claim that it was never owed, and an exclusion with "
                        "no stated reason is the failure this file exists to prevent",
                        path=path,
                        subject=cell_subject,
                    )
                )
        for extra in set(owes) - set(ACCOUNTINGS):
            problems.append(
                ManifestError(
                    f"undeclared accounting {extra!r}",
                    "the three accountings are a closed set: " + ", ".join(ACCOUNTINGS),
                    path=path,
                    subject=subject,
                )
            )

    return {k: v for k, v in kinds.items() if isinstance(v, Mapping)}


def _check_dependencies(
    entries: Sequence[Any],
    path: Path,
    problems: list[ManifestError],
    warnings: list[ManifestWarning],
) -> list[dict[str, Any]]:
    """Normalise health probes. A refusal stays in the denominator."""
    out: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            continue
        row = dict(entry)
        subject = f"entries[{index}] id={row.get('id')!r}"
        if "health_probe" not in row:
            continue  # already reported as a missing required field
        probe = row["health_probe"]
        if probe is None:
            reason = row.get("unprobeable_reason")
            if not (isinstance(reason, str) and reason.strip()):
                problems.append(
                    ManifestError(
                        "`health_probe` is null with no `unprobeable_reason`",
                        "either describe the probe, or set `health_probe: null` AND add "
                        "`unprobeable_reason: <why it cannot be probed>`. A refusal must "
                        "stay in the denominator carrying why; dropping it makes every "
                        "coverage number look better, which is why nobody goes looking",
                        path=path,
                        subject=subject,
                    )
                )
            else:
                row["health_probe"] = {"type": "unprobeable", "reason": reason}
                warnings.append(
                    ManifestWarning(
                        code="DEPENDENCY-UNPROBEABLE",
                        subject=str(row.get("id")),
                        detail=(
                            "materialised as {'type': 'unprobeable', ...}; it is counted, "
                            f"grades not-ok, and carries why: {reason}"
                        ),
                    )
                )
        out.append(row)
    return out


def _partition_grants(
    entries: Sequence[Any],
    operator_root_key_id: str | None,
    warnings: list[ManifestWarning],
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    """Split grants into applied and ignored. An ignored grant raises nothing."""
    applied: list[Mapping[str, Any]] = []
    ignored: list[Mapping[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        granted_by = entry.get("granted_by")
        if operator_root_key_id is None:
            ignored.append(entry)
            warnings.append(
                ManifestWarning(
                    code="GRANT-NO-OPERATOR-ROOT",
                    subject=str(entry.get("id")),
                    detail=(
                        "no operator root is bound on this node, so no grant is applied. "
                        "The entry is kept and does nothing"
                    ),
                )
            )
            continue
        if granted_by != operator_root_key_id:
            ignored.append(entry)
            warnings.append(
                ManifestWarning(
                    code="GRANT-FOREIGN-ROOT",
                    subject=str(entry.get("id")),
                    detail=(
                        f"granted_by={granted_by!r} is not this node's operator root "
                        f"({operator_root_key_id!r}); the grant raises nothing and is "
                        "never applied"
                    ),
                )
            )
            continue
        applied.append(entry)
    return applied, ignored


# --------------------------------------------------------------------------
# Public entry points
# --------------------------------------------------------------------------


def collect_problems(
    estate_dir: Path | str,
    operator_root_key_id: str | None = None,
) -> tuple[list[ManifestError], list[ManifestWarning], Estate | None]:
    """Validate an estate directory and report EVERY problem found.

    Returns ``(problems, warnings, estate_or_None)``. ``estate`` is ``None``
    whenever ``problems`` is non-empty -- a partially valid estate is never
    handed back as if it were whole.
    """
    root = Path(estate_dir)
    problems: list[ManifestError] = []
    warnings: list[ManifestWarning] = []
    manifests: dict[str, LoadedManifest] = {}
    kinds: dict[str, Mapping[str, Any]] = {}
    applied: tuple[Mapping[str, Any], ...] = ()
    ignored: tuple[Mapping[str, Any], ...] = ()

    if not root.is_dir():
        problems.append(
            ManifestError(
                f"the estate directory does not exist: {root}",
                "create estate/ with the six manifests, or point the check at the right "
                "directory",
                path=root,
            )
        )
        return problems, warnings, None

    for filename, spec in MANIFEST_SPECS.items():
        path = root / filename
        if not path.is_file():
            problems.append(
                ManifestError(
                    "the manifest file is missing",
                    "restore it. `entries: []` is a valid, honest statement about a new "
                    "node; a MISSING file is a field nobody read, and this loader cannot "
                    "tell 'none yet' from 'the packaging dropped it'",
                    path=path,
                )
            )
            continue

        try:
            data = _read_yaml(path)
        except ManifestError as exc:
            problems.append(exc)
            continue

        if not _check_header(data, spec, path, problems):
            continue

        entries = list(data[spec.entries_key])
        for index, entry in enumerate(entries):
            _check_entry(entry, index, spec, path, problems)
        _check_duplicate_ids(
            [e for e in entries if isinstance(e, Mapping)], spec, path, problems
        )

        if filename == "CAPABILITIES.yaml":
            kinds = _check_capability_kinds(data, path, problems)
        if filename == "DEPENDENCIES.yaml":
            entries = _check_dependencies(entries, path, problems, warnings)
        if filename == "GRANTS.yaml":
            applied_list, ignored_list = _partition_grants(
                entries, operator_root_key_id, warnings
            )
            applied, ignored = tuple(applied_list), tuple(ignored_list)

        manifests[filename] = LoadedManifest(
            path=path,
            schema=str(data["schema"]),
            as_of=str(data["as_of"]),
            entries=tuple(entries),
        )

    _check_estate_references(manifests, problems)

    if problems:
        return problems, warnings, None

    estate = Estate(
        root=root,
        manifests=manifests,
        kinds=kinds,
        applied_grants=applied,
        ignored_grants=ignored,
        warnings=tuple(warnings),
    )
    return problems, warnings, estate


def _check_estate_references(
    manifests: Mapping[str, LoadedManifest], problems: list[ManifestError]
) -> None:
    """Cross-check `estate:` references, but only when the map loaded cleanly."""
    estate_map = manifests.get("ESTATE-MAP.yaml")
    if estate_map is None:
        return  # unchecked, and said so in the module BLIND SPOTS
    known = {
        entry["id"]
        for entry in estate_map.entries
        if isinstance(entry, Mapping) and isinstance(entry.get("id"), str)
    }
    for filename in ("ASSETS.yaml", "GRANTS.yaml"):
        loaded = manifests.get(filename)
        if loaded is None:
            continue
        for entry in loaded.entries:
            if not isinstance(entry, Mapping):
                continue
            ref = entry.get("estate")
            if ref is None or ref in known:
                continue
            problems.append(
                ManifestError(
                    f"`estate: {ref!r}` names no entry in ESTATE-MAP.yaml",
                    "declare the estate in ESTATE-MAP.yaml, or correct the reference. "
                    "An item bound to an estate nobody declared has no autonomy rule",
                    path=loaded.path,
                    subject=str(entry.get("id")),
                )
            )


def load_estate(
    estate_dir: Path | str,
    operator_root_key_id: str | None = None,
) -> Estate:
    """Load and validate an estate, or HALT on the first problem found.

    ``entries: []`` everywhere is a clean load. A missing manifest, an unknown
    schema, an undeclared vocabulary value, a null ``telemetry_source``, a
    ``health_probe`` that is null with no reason, or a grant with no
    ``expires_at`` all raise ``ManifestError``.
    """
    problems, _warnings, estate = collect_problems(estate_dir, operator_root_key_id)
    if problems:
        raise problems[0]
    assert estate is not None  # collect_problems' contract
    return estate
