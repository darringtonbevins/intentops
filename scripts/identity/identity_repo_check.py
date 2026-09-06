#!/usr/bin/env python
"""Validate an identity repository against the identity-repo contract.

PURPOSE
    Every node's identity lives in its OWN repository, separate from the
    framework that runs it and pointed at explicitly by that node. This script
    is the mechanical half of ``docs/IDENTITY-REPO-CONTRACT.md``: it answers
    whether a given directory is a conformant identity repository, and when it
    is not, exactly which member, which field, and what the remedy is.

    Four refusals matter more than the rest, and each has its own code:

      KEY-SHAPE      any private-key-shaped file ANYWHERE in the repository.
                     The definition is imported from
                     ``scripts/ops/trust_material_check.py`` rather than
                     restated here, so there is one definition of a key shape
                     in this repository and not two that drift.
      FOREIGN-GRANT  a ruling or grant recorded under an operator root that is
                     not this repository's own, and which is nonetheless active
                     or permissive. A node never inherits another operator's
                     authority over itself by copying their identity
                     repository. Such rulings are not deleted -- they are
                     marked proposed and escalate-only, and they never answer a
                     live governing question until this node's own operator
                     re-rules them in their own terms.
      MEMBER-*       a required member absent, undeclared, or declared with a
                     write model that is not in the closed vocabulary. An
                     unstated write model is a store nobody can reason about.
      BINDING        the binding precedence must be stated and must have no
                     default at the bottom. A node with nothing bound halts and
                     says what it needs; it does not quietly create one.

    Run it at the organ-creation phase and from checklists. It is deliberately
    NOT a blocking pre-tool hook: a contract check must never wedge an
    unrelated session.

WRITE MODEL
    Not a store. This script writes nothing outside a temporary directory it
    creates and removes during ``--selftest``.

EXIT CODES
    0  conformant (notes may still have been printed; a note is a recorded
       observation, never a silent drop, and never a failure)
    1  at least one violation, or a selftest case that failed to fire

BLIND SPOTS
    * It validates SHAPE, VOCABULARY and PROVENANCE FIELDS -- never truth. A
      repository can be perfectly conformant and describe an operator who no
      longer exists.
    * The foreign-grant check reads the ``operator_root`` field each ruling row
      declares. A row that simply omits it is reported (ORDERING-NO-ROOT) and
      not guessed, but a row that declares a fingerprint it did not earn is
      indistinguishable from an honest one here: signature verification is a
      different instrument and is not in this script.
    * ``--selftest`` proves each violation class CAN fire against a mutated
      copy of the conformant fixture. It does not prove the absence of a class
      nobody thought to construct; the case list is printed, so what is
      untested is readable rather than assumed.
    * The committed negative fixture carries NO key-shaped file, on purpose.
      Committing one to prove a checker that forbids them is the defect the
      checker exists to prevent, so that case is planted in a temp tree
      instead.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[2]
_OPS_DIR = _REPO_ROOT / "scripts" / "ops"
if str(_OPS_DIR) not in sys.path:
    sys.path.insert(0, str(_OPS_DIR))

try:
    # One definition of a private-key shape in this repository. If this import
    # fails the check HALTS rather than continuing without its most important
    # refusal: a validator that silently skips the key scan reports a clean
    # identity repository it never actually checked.
    import trust_material_check  # noqa: E402
except ImportError as _exc:  # pragma: no cover - packaging defect
    raise SystemExit(
        "HALT: the private-key shape definitions could not be imported from "
        f"{_OPS_DIR / 'trust_material_check.py'} ({_exc}). This check will not "
        "run without them -- a conformance verdict that skipped the key scan is "
        "worse than no verdict."
    )

CONTRACT_SCHEMA = "identity-repo/v1"

#: The closed write-model vocabulary. An undeclared value is a hard exit, never
#: a default of "probably fine".
WRITE_MODELS: Tuple[str, ...] = (
    "append-only-journal",
    "per-item-files",
    "locked-whole-file",
    "generated-projection",
    "pointer-only",
)

#: Members whose append-only-ness is a CONTRACT property rather than the
#: repository's own preference: these three are the record of what this node
#: ruled, what it changed in its own core, and whom it reached.
CONTRACT_JOURNALS: Tuple[str, ...] = (
    "wisdom/orderings.jsonl",
    "conscience/core-review/ledger.jsonl",
    "conscience/people/ledger.jsonl",
)

#: ``(path, kind)`` -- every member a conformant repository carries at birth,
#: each present, empty and valid. ``entries: []`` is a true statement about a
#: new node; a MISSING member is a field nobody read.
REQUIRED_MEMBERS: Tuple[Tuple[str, str], ...] = (
    ("identity/identity.yaml", "file"),
    ("identity/consent.yaml", "file"),
    ("identity/epochs.yaml", "file"),
    ("identity/operator/operator.yaml", "file"),
    ("wisdom/orderings.jsonl", "file"),
    ("wishes/REGISTER.yaml", "file"),
    ("twin", "dir"),
    ("continuity/memory", "dir"),
    ("conscience/core-review/ledger.jsonl", "file"),
    ("conscience/loto/LEDGER.yaml", "file"),
    ("conscience/people/ledger.jsonl", "file"),
    ("estate/estate-map.yaml", "file"),
    ("intelligence", "dir"),
    ("knowledge/MANIFEST.yaml", "file"),
    ("trust", "dir"),
    (".gitignore", "file"),
)

#: Written at the founding gate, not at birth. Absence is a NOTE, never a
#: violation -- a node that has not had its founding conversation yet is a
#: young node, not a broken one.
BIRTH_OPTIONAL: Tuple[str, ...] = (
    "identity/founding-conversation.md",
    "identity/founding-conversation.md.sig",
)

#: The precedence, in this exact order, with no default at the bottom.
BINDING_PRECEDENCE: Tuple[str, ...] = ("environment", "genesis-argument", "node-config")

#: Shapes a conformant repository's .gitignore refuses by name. A promise with
#: no carrier is the failure this list retires.
GITIGNORE_REQUIRED: Tuple[str, ...] = ("*.key", "*.pem", "*.pfx", "*.p12", "id_ed25519")

_MANIFEST_NAME = "MANIFEST.yaml"


# --------------------------------------------------------------------------
# findings
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    code: str
    where: str
    message: str
    remedy: str = ""

    def __str__(self) -> str:
        text = f"{self.code} {self.where} -- {self.message}"
        if self.remedy:
            text += f"  remedy: {self.remedy}"
        return text


@dataclass
class Report:
    root: Path
    problems: List[Finding] = field(default_factory=list)
    notes: List[Finding] = field(default_factory=list)
    members_declared: int = 0
    members_present: int = 0
    ordering_rows: int = 0

    @property
    def conformant(self) -> bool:
        return not self.problems

    def codes(self) -> List[str]:
        return sorted({f.code for f in self.problems})


# --------------------------------------------------------------------------
# binding precedence -- resolved here, with no default
# --------------------------------------------------------------------------


class BindingError(RuntimeError):
    """Nothing was bound. There is no default identity repository, ever."""


def resolve_binding(
    *,
    env_value: Optional[str] = None,
    genesis_argument: Optional[str] = None,
    node_config_value: Optional[str] = None,
) -> Tuple[Path, str]:
    """Return ``(path, mechanism)`` under the contract's stated precedence.

    environment > genesis argument > node config, and nothing below. The
    environment value is deliberately the strongest and is never persisted;
    the node config is the standing binding. A caller with none of the three
    gets a :class:`BindingError` naming what it needs, because a fall-through
    here would answer a question nobody decided.
    """
    for value, mechanism in (
        (env_value, "environment"),
        (genesis_argument, "genesis-argument"),
        (node_config_value, "node-config"),
    ):
        text = str(value or "").strip()
        if text:
            return Path(text), mechanism
    raise BindingError(
        "no identity repository is bound. Supply one of, in precedence order: "
        "the environment variable, an explicit genesis argument, or the "
        "standing binding in the node configuration. There is no default: a "
        "node that quietly creates its own identity has decided something "
        "nobody asked it to decide."
    )


# --------------------------------------------------------------------------
# version pins
# --------------------------------------------------------------------------

#: A clause is an operator plus a NON-EMPTY version. ">= " with nothing
#: after it is an unreadable pin, and an unreadable pin is not a
#: satisfied one.
_CLAUSE = re.compile(r"^(>=|<=|==|!=|>|<)\s*(\S+)$")


def _version_tuple(text: str) -> Tuple[Any, ...]:
    parts: List[Any] = []
    for chunk in str(text).strip().split("."):
        parts.append(int(chunk) if chunk.isdigit() else chunk)
    return tuple(parts)


def _compare(left: Tuple[Any, ...], right: Tuple[Any, ...]) -> int:
    for a, b in zip(left, right):
        if a == b:
            continue
        if isinstance(a, int) and isinstance(b, int):
            return -1 if a < b else 1
        return -1 if str(a) < str(b) else 1
    if len(left) == len(right):
        return 0
    return -1 if len(left) < len(right) else 1


def satisfies(version: str, requires: str) -> bool:
    """Does ``version`` satisfy a comma-separated requirement string?

    Deliberately small: comparison clauses over dotted versions, nothing more.
    An unparseable clause returns False rather than True -- an unreadable pin
    is not a satisfied pin.
    """
    got = _version_tuple(version)
    for raw in str(requires).split(","):
        clause = raw.strip()
        if not clause:
            continue
        match = _CLAUSE.match(clause)
        if not match:
            return False
        op, want_text = match.group(1), match.group(2).strip()
        want = _version_tuple(want_text)
        result = _compare(got, want)
        if op == ">=" and result < 0:
            return False
        if op == ">" and result <= 0:
            return False
        if op == "<=" and result > 0:
            return False
        if op == "<" and result >= 0:
            return False
        if op == "==" and result != 0:
            return False
        if op == "!=" and result == 0:
            return False
    return True


# --------------------------------------------------------------------------
# the check
# --------------------------------------------------------------------------


def _load_yaml(path: Path) -> Any:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _require(mapping: Mapping[str, Any], key: str, where: str,
             problems: List[Finding], *, allow_empty: bool = False) -> Any:
    if key not in mapping:
        problems.append(Finding(
            "MANIFEST-FIELD", where,
            f"the required field {key!r} is absent",
            f"add {key!r}; a field a reader substitutes a default for has left "
            "the population",
        ))
        return None
    value = mapping.get(key)
    if not allow_empty and (value is None or (isinstance(value, str) and not value.strip())):
        problems.append(Finding(
            "MANIFEST-FIELD", where,
            f"the required field {key!r} is present but empty",
            f"state {key!r}, or state plainly why it cannot be stated",
        ))
        return None
    return value


def check_repo(root: Path, *, framework_version: Optional[str] = None) -> Report:
    """Validate one identity repository. Pure with respect to the tree it reads."""
    report = Report(root=root)
    problems = report.problems
    notes = report.notes

    if not root.is_dir():
        problems.append(Finding(
            "REPO-MISSING", str(root), "the identity repository directory does not exist",
            "bind an existing repository, or create a conformant skeleton",
        ))
        return report

    # -- 1. the manifest ---------------------------------------------------
    manifest_path = root / _MANIFEST_NAME
    manifest: Dict[str, Any] = {}
    if not manifest_path.is_file():
        problems.append(Finding(
            "MANIFEST-MISSING", _MANIFEST_NAME,
            "an identity repository with no manifest declares nothing about "
            "itself, and a boot sequence cannot read it safely",
            f"write {_MANIFEST_NAME} with schema {CONTRACT_SCHEMA}",
        ))
    else:
        try:
            raw = _load_yaml(manifest_path)
        except Exception as exc:  # yaml errors and IO errors alike
            problems.append(Finding(
                "MANIFEST-UNPARSEABLE", _MANIFEST_NAME, f"cannot be parsed: {exc}",
                "repair the file; a manifest that half-parses is worse than none",
            ))
            raw = None
        if raw is not None and not isinstance(raw, Mapping):
            problems.append(Finding(
                "MANIFEST-UNPARSEABLE", _MANIFEST_NAME,
                "does not parse to a mapping", "the manifest is a mapping at the top level",
            ))
        elif isinstance(raw, Mapping):
            manifest = dict(raw)

    operator_root = ""
    declared: Dict[str, Dict[str, Any]] = {}
    if manifest:
        schema = str(manifest.get("schema") or "")
        if schema != CONTRACT_SCHEMA:
            problems.append(Finding(
                "MANIFEST-SCHEMA", _MANIFEST_NAME,
                f"declares schema {schema!r}; this contract is {CONTRACT_SCHEMA!r}",
                "a reader that accepts an unknown schema is guessing",
            ))
        _require(manifest, "contract_version", _MANIFEST_NAME, problems)
        _require(manifest, "as_of", _MANIFEST_NAME, problems)

        pin = _require(manifest, "intentops", _MANIFEST_NAME, problems)
        if isinstance(pin, Mapping):
            for key in ("requires", "pinned", "release_root_fingerprint", "validated_at"):
                _require(pin, key, f"{_MANIFEST_NAME}:intentops", problems)
            requires = str(pin.get("requires") or "")
            candidate = framework_version or str(pin.get("pinned") or "")
            if requires and candidate and not satisfies(candidate, requires):
                problems.append(Finding(
                    "PIN-INCOMPATIBLE", f"{_MANIFEST_NAME}:intentops",
                    f"version {candidate} does not satisfy {requires!r}",
                    "an incompatible pin halts the boot; it is never rounded to the "
                    "nearest compatible thing",
                ))

        identity = _require(manifest, "identity", _MANIFEST_NAME, problems)
        if isinstance(identity, Mapping):
            _require(identity, "designation", f"{_MANIFEST_NAME}:identity", problems)
            if str(identity.get("name") or "").strip() and not str(
                identity.get("name_ratified_by") or ""
            ).strip():
                problems.append(Finding(
                    "NAME-UNRATIFIED", f"{_MANIFEST_NAME}:identity",
                    "a name is recorded with no ratification",
                    "a node is born with a designation. A name it gave itself, with "
                    "no operator signature naming who ratified it, is not a name",
                ))

        operator = _require(manifest, "operator", _MANIFEST_NAME, problems)
        if isinstance(operator, Mapping):
            value = _require(operator, "root_fingerprint",
                             f"{_MANIFEST_NAME}:operator", problems)
            operator_root = str(value or "").strip()

        if "bound_node" not in manifest:
            problems.append(Finding(
                "MANIFEST-FIELD", _MANIFEST_NAME,
                "the required field 'bound_node' is absent",
                "declare it, empty if this repository is not yet bound. An absent "
                "key and an empty one are different claims",
            ))

        binding = manifest.get("binding")
        if not isinstance(binding, Mapping):
            problems.append(Finding(
                "BINDING", f"{_MANIFEST_NAME}:binding",
                "the binding precedence is not stated",
                f"declare precedence {list(BINDING_PRECEDENCE)} and default: null",
            ))
        else:
            order = binding.get("precedence")
            if list(order or []) != list(BINDING_PRECEDENCE):
                problems.append(Finding(
                    "BINDING", f"{_MANIFEST_NAME}:binding",
                    f"precedence is {order!r}, not {list(BINDING_PRECEDENCE)}",
                    "the order is the contract: the environment value is one process "
                    "and never persisted; the node config is the standing binding",
                ))
            if binding.get("default") not in (None, "", []):
                problems.append(Finding(
                    "BINDING", f"{_MANIFEST_NAME}:binding",
                    f"a default binding is declared ({binding.get('default')!r})",
                    "there is no default. A node with nothing bound halts and says "
                    "what it needs",
                ))

        falsifier = manifest.get("falsifier")
        reopens = ""
        if isinstance(falsifier, Mapping):
            reopens = str(falsifier.get("reopens_when") or "").strip()
        if not reopens:
            problems.append(Finding(
                "MANIFEST-FALSIFIER", f"{_MANIFEST_NAME}:falsifier",
                "no reopens_when is stated",
                "a belief carrier states what would make it false, or it is carried "
                "past the day it stopped being true with nobody asking",
            ))

        if "lineage" not in manifest:
            problems.append(Finding(
                "MANIFEST-FIELD", _MANIFEST_NAME,
                "the required field 'lineage' is absent",
                "declare it, empty if there is no pre-contract layer. Supersession "
                "keeps lineage; it never deletes",
            ))

        rows = manifest.get("members")
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            problems.append(Finding(
                "MEMBER-UNDECLARED", f"{_MANIFEST_NAME}:members",
                "no members are declared",
                "every member declares its path, its write model and its validator",
            ))
            rows = []
        for index, row in enumerate(rows):
            where = f"{_MANIFEST_NAME}:members[{index}]"
            if not isinstance(row, Mapping):
                problems.append(Finding("MEMBER-UNDECLARED", where, "is not a mapping"))
                continue
            path_value = str(row.get("path") or "").strip()
            if not path_value:
                problems.append(Finding("MEMBER-UNDECLARED", where, "declares no path"))
                continue
            model = str(row.get("write_model") or "").strip()
            if not model:
                problems.append(Finding(
                    "MEMBER-WRITE-MODEL", where,
                    f"{path_value} declares no write model",
                    f"one of {list(WRITE_MODELS)}; an unstated write model is a store "
                    "nobody can reason about",
                ))
            elif model not in WRITE_MODELS:
                problems.append(Finding(
                    "MEMBER-WRITE-MODEL", where,
                    f"{path_value} declares write model {model!r}, which is not in the "
                    "closed vocabulary",
                    f"one of {list(WRITE_MODELS)}. An undeclared value is a hard exit, "
                    "never a default",
                ))
            if not str(row.get("validator") or "").strip():
                problems.append(Finding(
                    "MEMBER-UNDECLARED", where,
                    f"{path_value} names no validator",
                    "a member with no validator is a member nobody checks",
                ))
            declared[path_value.replace("\\", "/")] = dict(row)
        report.members_declared = len(declared)

    # -- 2. required members, on disk and in the manifest -------------------
    for member, kind in REQUIRED_MEMBERS:
        target = root / member
        exists = target.is_dir() if kind == "dir" else target.is_file()
        if not exists:
            if target.exists():
                problems.append(Finding(
                    "MEMBER-KIND", member,
                    f"exists but is not a {kind}",
                    f"the contract declares {member} as a {kind}",
                ))
            else:
                problems.append(Finding(
                    "MEMBER-MISSING", member,
                    "a required member is absent",
                    "create it empty and valid. An empty store is a true statement "
                    "about a new node; a missing one is a field nobody read",
                ))
        else:
            report.members_present += 1
        if manifest and member not in declared:
            problems.append(Finding(
                "MEMBER-UNDECLARED", member,
                "is required by the contract and is not declared in the manifest",
                "declare it with its write model and validator",
            ))
    for journal in CONTRACT_JOURNALS:
        row = declared.get(journal)
        if row and str(row.get("write_model") or "") != "append-only-journal":
            problems.append(Finding(
                "MEMBER-JOURNAL", journal,
                f"declares write model {row.get('write_model')!r}",
                "this member's append-only-ness is a contract property, not a "
                "preference: it is the record of what this node ruled, changed in "
                "its own core, or did to a person",
            ))
    for optional in BIRTH_OPTIONAL:
        if not (root / optional).exists():
            notes.append(Finding(
                "NOTE-UNWRITTEN", optional,
                "not present -- written at the founding gate, not at birth",
            ))

    # -- 3. the .gitignore refusals ----------------------------------------
    gitignore = root / ".gitignore"
    if gitignore.is_file():
        text = gitignore.read_text(encoding="utf-8", errors="replace")
        missing = [p for p in GITIGNORE_REQUIRED if p not in text]
        if missing:
            problems.append(Finding(
                "GITIGNORE-PATTERNS", ".gitignore",
                f"does not refuse {', '.join(missing)}",
                "the .gitignore names the shapes it refuses, or the refusal is a "
                "promise with no carrier",
            ))

    # -- 4. private-key shapes, anywhere -----------------------------------
    findings, _scanned, unscanned = trust_material_check.scan_tree(root)
    for finding in findings:
        problems.append(Finding(
            "KEY-SHAPE", finding.path,
            f"{finding.code}: {finding.detail}",
            "a private key never lives in a repository. Move it to a token, a TPM "
            "or the OS credential store, and rotate it -- it is now disclosed",
        ))
    for rel in unscanned:
        notes.append(Finding(
            "NOTE-UNSCANNED", rel,
            "binary or oversized: not read by the key-shape scan, and counted here "
            "rather than dropped",
        ))

    # -- 5. foreign-root rulings and grants --------------------------------
    problems.extend(_check_foreign_authority(root, operator_root, report))

    return report


def _check_foreign_authority(root: Path, operator_root: str, report: Report) -> List[Finding]:
    """Rulings and grants recorded under an operator root that is not this one.

    A foreign ruling is not a violation by existing -- it is a violation by
    being ACTIVE or PERMISSIVE. The contract's own remedy is that such rulings
    land ``status: proposed`` with ``on_match: escalate`` and never answer a
    live governing question, so this check looks for exactly the two ways that
    remedy can be absent.
    """
    out: List[Finding] = []
    journal = root / "wisdom" / "orderings.jsonl"
    if journal.is_file():
        try:
            text = journal.read_text(encoding="utf-8")
        except OSError as exc:
            return [Finding("ORDERING-UNREADABLE", "wisdom/orderings.jsonl", str(exc))]
        for number, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            report.ordering_rows += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                out.append(Finding(
                    "ORDERING-UNPARSEABLE", f"wisdom/orderings.jsonl:{number}",
                    f"unreadable row: {exc}",
                    "a dropped ruling with no signal anywhere is the defect this "
                    "journal exists to prevent -- repair or supersede the row",
                ))
                continue
            if not isinstance(row, Mapping):
                out.append(Finding(
                    "ORDERING-UNPARSEABLE", f"wisdom/orderings.jsonl:{number}",
                    "row is not an object",
                ))
                continue
            if str(row.get("event") or "rule") != "rule":
                continue
            row_root = str(row.get("operator_root") or "").strip()
            identifier = str(row.get("id") or f"row {number}")
            if not row_root:
                out.append(Finding(
                    "ORDERING-NO-ROOT", f"wisdom/orderings.jsonl:{number}",
                    f"{identifier} does not say whose authority it carries",
                    "every ruling row declares operator_root. A row that omits it "
                    "cannot be checked against this node's operator, and it is "
                    "reported rather than assumed to be ours",
                ))
                continue
            if operator_root and row_root == operator_root:
                continue
            status = str(row.get("status") or "active")
            on_match = str(row.get("on_match") or "escalate")
            if status == "active" or on_match == "permit":
                out.append(Finding(
                    "FOREIGN-GRANT", f"wisdom/orderings.jsonl:{number}",
                    f"{identifier} carries the root {row_root!r}, not this "
                    f"repository's {operator_root!r}, and is status={status!r} "
                    f"on_match={on_match!r}",
                    "a foreign ruling lands status 'proposed' and on_match "
                    "'escalate' and never answers a live governing question until "
                    "this node's own operator re-rules it in their own terms",
                ))

    grants_path = root / _MANIFEST_NAME
    if grants_path.is_file():
        try:
            manifest = _load_yaml(grants_path)
        except Exception:
            manifest = None
        if isinstance(manifest, Mapping):
            grants = manifest.get("grants") or []
            if isinstance(grants, Sequence) and not isinstance(grants, (str, bytes)):
                for index, grant in enumerate(grants):
                    if not isinstance(grant, Mapping):
                        continue
                    granted_by = str(grant.get("granted_by") or "").strip()
                    if not granted_by:
                        out.append(Finding(
                            "ORDERING-NO-ROOT", f"{_MANIFEST_NAME}:grants[{index}]",
                            "a grant that names no granting root",
                            "a grant with no author binds nobody, and must not be "
                            "read as though it binds this node",
                        ))
                    elif operator_root and granted_by != operator_root:
                        out.append(Finding(
                            "FOREIGN-GRANT", f"{_MANIFEST_NAME}:grants[{index}]",
                            f"a delegated-autonomy grant from {granted_by!r}, which "
                            f"is not this repository's operator root {operator_root!r}",
                            "a grant whose granting fingerprint is not this node's "
                            "operator root raises nothing. Remove it, or re-rule it "
                            "under this operator",
                        ))
    return out


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------


def _rewrite_manifest(root: Path, mutate: Callable[[Dict[str, Any]], None]) -> None:
    import yaml

    path = root / _MANIFEST_NAME
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _m_remove_member_file(root: Path) -> None:
    (root / "conscience" / "people" / "ledger.jsonl").unlink()


def _m_bad_write_model(root: Path) -> None:
    def apply(data: Dict[str, Any]) -> None:
        data["members"][0]["write_model"] = "whatever-works"

    _rewrite_manifest(root, apply)


def _m_journal_not_append_only(root: Path) -> None:
    def apply(data: Dict[str, Any]) -> None:
        for row in data["members"]:
            if row.get("path") == "conscience/people/ledger.jsonl":
                row["write_model"] = "locked-whole-file"

    _rewrite_manifest(root, apply)


def _m_blank_falsifier(root: Path) -> None:
    def apply(data: Dict[str, Any]) -> None:
        data["falsifier"]["reopens_when"] = "  "

    _rewrite_manifest(root, apply)


def _m_default_binding(root: Path) -> None:
    def apply(data: Dict[str, Any]) -> None:
        data["binding"]["default"] = "./identity"

    _rewrite_manifest(root, apply)


def _m_unratified_name(root: Path) -> None:
    def apply(data: Dict[str, Any]) -> None:
        data["identity"]["name"] = "a name it chose for itself"

    _rewrite_manifest(root, apply)


def _m_incompatible_pin(root: Path) -> None:
    def apply(data: Dict[str, Any]) -> None:
        data["intentops"]["pinned"] = "9.9.9"

    _rewrite_manifest(root, apply)


def _m_foreign_grant(root: Path) -> None:
    def apply(data: Dict[str, Any]) -> None:
        data["grants"] = [{
            "id": "GRANT-EXAMPLE",
            "granted_by": "sha256:SOME-OTHER-OPERATOR-ROOT",
            "scope": "delegated autonomy over this node",
        }]

    _rewrite_manifest(root, apply)


def _m_foreign_ruling(root: Path) -> None:
    row = {
        "event": "rule",
        "id": "ORD-example00",
        "statement": "A fictional ruling recorded under another operator's root.",
        "operator_root": "sha256:SOME-OTHER-OPERATOR-ROOT",
        "status": "active",
        "on_match": "permit",
    }
    (root / "wisdom" / "orderings.jsonl").write_text(
        json.dumps(row) + "\n", encoding="utf-8"
    )


def _m_ruling_without_root(root: Path) -> None:
    row = {"event": "rule", "id": "ORD-example01", "statement": "No root declared."}
    (root / "wisdom" / "orderings.jsonl").write_text(
        json.dumps(row) + "\n", encoding="utf-8"
    )


def _m_unreadable_ruling(root: Path) -> None:
    (root / "wisdom" / "orderings.jsonl").write_text("{not json\n", encoding="utf-8")


def _m_private_key(root: Path) -> None:
    """Planted in a TEMP copy only. See this module's blind spots."""
    (root / "trust" / "leaked.txt").write_text(
        trust_material_check._PEM_OPEN + " " + trust_material_check._PEM_PRIVATE_TAIL
        + "\nMC4CAQAwBQYDK2Vw" + "A" * 32 + "\n",
        encoding="utf-8",
    )


def _m_gitignore_open(root: Path) -> None:
    (root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")


def _m_clean(root: Path) -> None:
    return None


SELFTEST_CASES: Tuple[Tuple[str, str, Callable[[Path], None]], ...] = (
    ("the conformant fixture as shipped", "", _m_clean),
    ("a required member deleted", "MEMBER-MISSING", _m_remove_member_file),
    ("a member with an undeclared write model", "MEMBER-WRITE-MODEL", _m_bad_write_model),
    ("a contract journal declared not-append-only", "MEMBER-JOURNAL",
     _m_journal_not_append_only),
    ("a blank falsifier", "MANIFEST-FALSIFIER", _m_blank_falsifier),
    ("a default binding declared", "BINDING", _m_default_binding),
    ("a name with no ratification", "NAME-UNRATIFIED", _m_unratified_name),
    ("an incompatible framework pin", "PIN-INCOMPATIBLE", _m_incompatible_pin),
    ("a delegated-autonomy grant from another root", "FOREIGN-GRANT", _m_foreign_grant),
    ("an active permissive ruling from another root", "FOREIGN-GRANT", _m_foreign_ruling),
    ("a ruling that names no operator root", "ORDERING-NO-ROOT", _m_ruling_without_root),
    ("an unreadable ruling row", "ORDERING-UNPARSEABLE", _m_unreadable_ruling),
    ("a private-key-shaped file anywhere in the repo", "KEY-SHAPE", _m_private_key),
    ("a .gitignore that refuses nothing", "GITIGNORE-PATTERNS", _m_gitignore_open),
)


def run_selftest(fixture: Path, negative: Optional[Path] = None) -> int:
    print(f"identity_repo_check selftest: baseline = {fixture}")
    if not (fixture / _MANIFEST_NAME).is_file():
        raise SystemExit(
            f"SELFTEST CANNOT RUN: no conformant fixture at {fixture}. A selftest "
            "with no pristine baseline proves nothing."
        )
    failures: List[str] = []
    for label, expected, mutate in SELFTEST_CASES:
        with tempfile.TemporaryDirectory(prefix="intentops-identity-selftest-") as tmp:
            work = Path(tmp) / "identity-repo"
            shutil.copytree(fixture, work)
            mutate(work)
            report = check_repo(work)
            if expected:
                ok = expected in report.codes()
                detail = "" if ok else f"expected {expected}, saw {report.codes() or 'nothing'}"
            else:
                ok = report.conformant
                detail = "" if ok else f"unexpected: {report.problems[0]}"
            print(f"  [{'FIRED ' if ok else 'MISSED'}] {label} {detail}".rstrip())
            if not ok:
                failures.append(label)

    if negative is not None and negative.is_dir():
        report = check_repo(negative)
        ok = not report.conformant
        print(f"  [{'FIRED ' if ok else 'MISSED'}] the committed negative fixture is "
              f"rejected ({', '.join(report.codes()) or 'nothing fired'})")
        if not ok:
            failures.append("the committed negative fixture is rejected")

    total = len(SELFTEST_CASES) + (1 if negative is not None and negative.is_dir() else 0)
    print(f"selftest: {total - len(failures)}/{total} cases behaved as declared")
    if failures:
        print("SELFTEST FAILED: " + ", ".join(failures))
        return 1
    print("SELFTEST PASS -- every violation class above can fire, and the "
          "conformant fixture still loads")
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

_FIXTURES = _REPO_ROOT / "tests" / "fixtures"
_CONFORMANT = _FIXTURES / "identity-repo-conformant"
_NEGATIVE = _FIXTURES / "identity-repo-negative"


def _report(report: Report) -> int:
    print(f"identity repository: {report.root}")
    for note in report.notes:
        print(f"  {note}")
    total = len(REQUIRED_MEMBERS)
    print(f"  required members present {report.members_present}/{total}; "
          f"declared in the manifest {report.members_declared}; "
          f"ruling rows read {report.ordering_rows}")
    if report.conformant:
        print("VERDICT: CONFORMANT")
        return 0
    for problem in report.problems:
        print(f"  {problem}")
    print(f"  violations {len(report.problems)}")
    print("VERDICT: NOT CONFORMANT")
    return 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="validate an identity repository against the identity-repo contract"
    )
    parser.add_argument("--identity-repo", type=Path, default=None,
                        help="the repository to check (rank 2 of the binding precedence)")
    parser.add_argument("--node-config-binding", default=None,
                        help="the standing binding from the node config (rank 3)")
    parser.add_argument("--framework-version", default=None,
                        help="the running framework version, checked against the pin")
    parser.add_argument("--selftest", action="store_true",
                        help="construct each violation in a temp tree and prove it fires")
    args = parser.parse_args(argv)

    if args.selftest:
        return run_selftest(_CONFORMANT, _NEGATIVE)

    try:
        root, mechanism = resolve_binding(
            env_value=os.environ.get("INTENTOPS_IDENTITY_REPO"),
            genesis_argument=str(args.identity_repo) if args.identity_repo else None,
            node_config_value=args.node_config_binding,
        )
    except BindingError as exc:
        print(f"HALT: {exc}")
        return 1
    print(f"binding: {mechanism}")
    return _report(check_repo(root, framework_version=args.framework_version))


if __name__ == "__main__":
    raise SystemExit(main())
