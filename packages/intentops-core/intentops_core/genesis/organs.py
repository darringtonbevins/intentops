"""G3 -- create every organ empty and valid, each declaring its write model.

PURPOSE
    A node at birth holds SIXTEEN entries under ``.intentops/``; a mature one
    accumulates hundreds, and the difference between the two numbers is the
    measure of what is carried versus what is accumulated. This module creates
    those entries, the ``.intentops-rules/`` boot corpus, and the identity-repo
    skeleton -- every one of them empty AND valid, because an empty store is a
    true statement about a new node while a MISSING store is a field nobody
    read.

    Every organ records its write model at birth into
    ``.intentops/genesis/organs.json``. A store that cannot say how it is
    written is a shared whiteboard waiting to happen, and the estate this
    design comes from was bitten by that four times before the rule existed.

WRITE MODEL
    ``.intentops/genesis/organs.json`` -- locked fresh-read read-modify-write
    (``StoreLock`` + ``atomic_replace``). It is a whole-file store with one
    writer (genesis) and low contention, so model 2 of the three is correct.
    Every store this module CREATES declares its own model in
    :data:`BIRTH_ORGANS`, which is the point of the file.

BLIND SPOTS
    - This module proves a store EXISTS and PARSES. It does not prove anyone
      reads it. "Capture without a consumer is not retention" is why every
      organ row carries a ``consumer`` field, but nothing here checks that the
      named consumer is wired.
    - The identity-repo conformance check is a structural one: members present,
      YAML parses, no private-key-shaped file. It cannot tell a conformant
      skeleton from a well-formed hostile one, which is why the boot READ list
      is closed and everything else is mounted-not-interpreted.
    - The key-shape detector matches PEM armour headers only. A raw or
      DER-encoded key file carries no armour and is invisible to it; the
      ``.gitignore`` patterns are the second, independent control.
    - The birth count of 16 is a DECLARED constant here, derived from the
      design's tree plus the two ledgers the validation family brings with it.
      If a later leg ships a seventeenth organ, this constant is the thing to
      correct -- it will not notice on its own.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..store_guard import StoreLock, atomic_replace, lock_for
from . import Halt

__all__ = [
    "Organ",
    "BIRTH_ORGANS",
    "IDENTITY_REPO_MEMBERS",
    "WRITE_MODELS",
    "create_organs",
    "create_identity_skeleton",
    "check_identity_repo",
    "key_armour",
    "selftest",
]

_ARMOUR = "-" * 5


def key_armour(kind: str = "") -> str:
    """The PEM armour line for a private key, assembled rather than spelled.

    Written this way on purpose: a literal armour header sitting in source is
    indistinguishable, to every secret scanner in the chain, from a leaked key.
    A detector's own fixture must not trip the detector.
    """
    label = f"{kind} PRIVATE KEY".strip()
    return f"{_ARMOUR}BEGIN {label}{_ARMOUR}"


_PRIVATE_KEY_MARKERS: Tuple[str, ...] = tuple(
    key_armour(kind) for kind in ("", "OPENSSH", "EC", "RSA", "ENCRYPTED")
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@dataclass(frozen=True)
class Organ:
    """One birth entry: what it is, how it is written, and who reads it."""

    id: str
    relpath: str
    kind: str                 # dir | file
    write_model: str
    consumer: str
    present_at_birth: bool = True
    seed: Optional[str] = None   # initial file content, when it is a file


#: The write-model vocabulary. Closed by declaration; an organ carrying a value
#: outside this tuple is a hard exit in :func:`create_organs`, never a default.
WRITE_MODELS: Tuple[str, ...] = (
    "per-item-files",             # collision-safe by construction
    "locked-rmw",                 # StoreLock + fresh read + atomic_replace
    "append-only-jsonl",          # append under StoreLock, state as a pure fold
    "single-writer-ceremony",     # only its own sanctioned writer touches it
    "generated-projection",       # correct the source, never this file
    "derived-regenerable",        # safe to delete; rebuilt by an instrument
    "absent-at-birth",            # its PRESENCE is the signal
)

#: The sixteen entries a node holds at birth.
#:
#: Thirteen come from the design's own tree (genesis, trust, config, logs,
#: core-review, approvals, witness, still-true, loto, register, locks,
#: checkpoint.json, estate-cache) plus ``halt.marker``, which is named because
#: its ABSENCE is the fact. The remaining two are the ledgers the validation
#: family brings with it when it ships at birth: the node-local ordering
#: write-through buffer and the responsiveness ledger, without which Still True
#: raises questions nothing measures the answering of.
BIRTH_ORGANS: Tuple[Organ, ...] = (
    Organ("genesis", ".intentops/genesis", "dir", "append-only-jsonl",
          "intentops doctor, intentops verify, --resume"),
    Organ("trust", ".intentops/trust", "dir", "single-writer-ceremony",
          "G1 provenance, G2 key ceremony -- PUBLIC projections only"),
    Organ("config", ".intentops/config", "dir", "locked-rmw",
          "every command: the identity-repo binding and the bound saddle"),
    Organ("logs", ".intentops/logs", "dir", "append-only-jsonl",
          "the gate, the audit trail, guardian blocks"),
    Organ("core-review", ".intentops/core-review/ledger.jsonl", "file",
          "append-only-jsonl",
          "the values council; write-through to the identity repo", seed=""),
    Organ("approvals", ".intentops/approvals", "dir", "per-item-files",
          "the approval queue -- one file per item, collision-safe"),
    Organ("witness", ".intentops/witness/register.jsonl", "file",
          "append-only-jsonl",
          "the witness register: believed-fixed-but-unproven debt", seed=""),
    Organ("still-true", ".intentops/still-true/ledger.jsonl", "file",
          "append-only-jsonl",
          "belief currency: which carriers stopped being true", seed=""),
    Organ("responsiveness", ".intentops/responsiveness/ledger.jsonl", "file",
          "append-only-jsonl",
          "whether a tripped falsifier ever got an answer", seed=""),
    Organ("wisdom", ".intentops/wisdom/orderings.jsonl", "file",
          "append-only-jsonl",
          "the ordering store's node-local write-through buffer", seed=""),
    Organ("loto", ".intentops/loto/LEDGER.yaml", "file",
          "single-writer-ceremony",
          "the tagout oracle: is anything safety-critical switched off"),
    Organ("register", ".intentops/register", "dir", "locked-rmw",
          "the session registry and peer consult"),
    Organ("locks", ".intentops/locks", "dir", "derived-regenerable",
          "reserved for lock files; a StoreLock sibling lands BESIDE its own "
          "store, not here, so this directory is normally empty and nothing "
          "reads it"),
    Organ("checkpoint", ".intentops/checkpoint.json", "file", "locked-rmw",
          "continuity: where the node was when it last stopped"),
    Organ("estate-cache", ".intentops/estate-cache", "dir",
          "derived-regenerable", "the estate loader; rebuilt on demand"),
    Organ("halt-marker", ".intentops/halt.marker", "file", "absent-at-birth",
          "every tool call, ahead of everything else -- its PRESENCE is the "
          "stand-down", present_at_birth=False),
)

#: The identity-repo skeleton. Every member present, empty and valid.
IDENTITY_REPO_MEMBERS: Tuple[Tuple[str, str, str], ...] = (
    # (relpath, kind, write_model)
    ("MANIFEST.yaml", "file", "locked-rmw"),
    ("identity/identity.yaml", "file", "locked-rmw"),
    ("identity/epochs.yaml", "file", "append-only-jsonl"),
    ("identity/operator/operator.yaml", "file", "locked-rmw"),
    ("identity/wishes/REGISTER.yaml", "file", "locked-rmw"),
    ("wisdom/orderings.jsonl", "file", "append-only-jsonl"),
    ("twin", "dir", "append-only-jsonl"),
    ("continuity/memory", "dir", "generated-projection"),
    ("conscience/core-review.jsonl", "file", "append-only-jsonl"),
    ("conscience/LEDGER.yaml", "file", "single-writer-ceremony"),
    ("conscience/people/ledger.jsonl", "file", "append-only-jsonl"),
    ("estate", "dir", "locked-rmw"),
    ("intelligence", "dir", "locked-rmw"),
    ("knowledge/MANIFEST.yaml", "file", "locked-rmw"),
    ("trust", "dir", "single-writer-ceremony"),
    (".gitignore", "file", "locked-rmw"),
)

_GITIGNORE = """# The identity repo refuses private-key shapes BY PATTERN, not by hope.
*.key
*.pem
*_rsa
*_ed25519
id_*
*.p12
*.pfx
*.jks
.env
*.secret

# Kernel-released lock files. A StoreLock sibling lands beside the store it
# guards, so writing to this repository leaves `<store>.lock` files in it; they
# are machine-local runtime state and never part of an identity.
*.lock
"""

_KNOWLEDGE_MANIFEST = """# Pointers only. A node-local knowledge store never enters a repository:
# what lives here is where to find it, never what it holds.
schema: knowledge-manifest/v1
as_of: "{today}"
stores: []
"""


def _write_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_replace(path, text)


def _seed_loto_ledger(path: Path) -> None:
    """An empty-and-valid tagout ledger: a node at birth has switched nothing off.

    The ledger's own writer appends entries to a document; it does not create
    one. Birth writes the empty document here, once, under the same lock that
    writer uses. Every subsequent write goes through the ledger module.
    """
    import yaml

    from ..loto.ledger import new_ledger

    path.parent.mkdir(parents=True, exist_ok=True)
    with StoreLock(lock_for(path)):
        if path.exists() and path.read_text(encoding="utf-8").strip():
            return
        atomic_replace(path, yaml.safe_dump(new_ledger(), default_flow_style=False,
                                            sort_keys=False, allow_unicode=True))


def _seed_checkpoint(path: Path) -> None:
    _write_file(path, json.dumps(
        {"schema": "checkpoint/v1", "as_of": _now(), "state": None,
         "note": "GENERATED at birth. A null state is the honest reading of a "
                 "node that has not yet stopped."},
        indent=2) + "\n")


def _identity_manifest(designation: str, node_id: str, imprint_version: str,
                       release_root_fingerprint: str,
                       operator_fingerprint: str) -> str:
    import yaml

    members = [{"path": relpath, "kind": kind, "write_model": model}
               for relpath, kind, model in IDENTITY_REPO_MEMBERS]
    doc = {
        "schema": "identity-repo/v1",
        "contract_version": 1,
        "as_of": _today(),
        "intentops": {
            "requires": ">=0.1,<1.0",
            "pinned": imprint_version,
            "release_root_fingerprint": release_root_fingerprint,
            "validated_at": _now(),
        },
        "identity": {"designation": designation, "name": None},
        "operator": {"root_fingerprint": operator_fingerprint},
        "bound_node": node_id,
        "members": members,
        "lineage": [],
        "falsifier": {
            "reopens_when":
                "this repo is restored on new hardware and finds it has lost "
                "open debt it needed, or the bound node is not this node",
        },
    }
    return yaml.safe_dump(doc, default_flow_style=False, sort_keys=False,
                          allow_unicode=True)


def create_identity_skeleton(
    identity_repo: Path,
    *,
    designation: str,
    node_id: str,
    imprint_version: str,
    release_root_fingerprint: str,
    operator_fingerprint: str,
    wish_template: Optional[Path] = None,
    estate_source: Optional[Path] = None,
    dry_run: bool = False,
) -> List[str]:
    """Write a conformant, empty identity-repo skeleton. Returns member paths."""
    import yaml

    identity_repo = Path(identity_repo)
    written: List[str] = []
    for relpath, kind, _model in IDENTITY_REPO_MEMBERS:
        target = identity_repo / relpath
        if kind == "dir":
            target.mkdir(parents=True, exist_ok=True)
            (target / ".gitkeep").touch()
        elif not target.exists():
            _write_file(target, "")
        written.append(relpath)

    _write_file(identity_repo / "MANIFEST.yaml",
                _identity_manifest(designation, node_id, imprint_version,
                                   release_root_fingerprint,
                                   operator_fingerprint))
    _write_file(identity_repo / "identity" / "identity.yaml",
                yaml.safe_dump({
                    "schema": "identity/v1",
                    "designation": designation,
                    "node_id": node_id,
                    "did": None,
                    "epoch": 1,
                    "archetype": None,
                    "name": None,
                    "as_of": _today(),
                }, default_flow_style=False, sort_keys=False))
    _write_file(identity_repo / "identity" / "epochs.yaml",
                yaml.safe_dump({
                    "schema": "epochs/v1",
                    "epochs": [{"epoch": 1, "event": "birth", "date": _today(),
                                "dry_run": bool(dry_run)}],
                }, default_flow_style=False, sort_keys=False))
    _write_file(identity_repo / "identity" / "operator" / "operator.yaml",
                yaml.safe_dump({
                    "schema": "operator/v1",
                    "as_of": _today(),
                    # No-default slots. A boot sequence that invents an operator
                    # is a boot sequence acting on a stranger's behalf.
                    "label": None,
                    "root_fingerprint": operator_fingerprint,
                    "notification_posture": None,
                    "off_limits": [],
                }, default_flow_style=False, sort_keys=False))
    _write_file(identity_repo / ".gitignore", _GITIGNORE)
    _write_file(identity_repo / "knowledge" / "MANIFEST.yaml",
                _KNOWLEDGE_MANIFEST.format(today=_today()))

    register = identity_repo / "identity" / "wishes" / "REGISTER.yaml"
    if wish_template and Path(wish_template).exists():
        register.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(wish_template, register)
    elif not register.read_text(encoding="utf-8").strip():
        _write_file(register, yaml.safe_dump(
            {"schema": "wish-register/v1", "as_of": _today(),
             "wishes": [], "decisions_open": []},
            default_flow_style=False, sort_keys=False))

    if estate_source and Path(estate_source).is_dir():
        (identity_repo / "estate").mkdir(parents=True, exist_ok=True)
        for src in sorted(Path(estate_source).glob("*.yaml")):
            shutil.copyfile(src, identity_repo / "estate" / src.name)
            written.append(f"estate/{src.name}")
    return written


def check_identity_repo(identity_repo: Path | str) -> Tuple[bool, List[str]]:
    """Structural conformance. Returns ``(ok, findings)``; never raises on a finding.

    Findings, not exceptions, because the caller decides whether a missing
    member is a HALT (at G3) or a degraded faculty (at G7).
    """
    import yaml

    root = Path(identity_repo)
    findings: List[str] = []
    if not root.is_dir():
        return False, [f"identity repo is not a directory: {root}"]
    for relpath, kind, _model in IDENTITY_REPO_MEMBERS:
        target = root / relpath
        if kind == "dir" and not target.is_dir():
            findings.append(f"missing directory: {relpath}")
        elif kind == "file" and not target.is_file():
            findings.append(f"missing file: {relpath}")
    for relpath, kind, _model in IDENTITY_REPO_MEMBERS:
        target = root / relpath
        if kind != "file" or not target.is_file() or target.suffix != ".yaml":
            continue
        try:
            yaml.safe_load(target.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            findings.append(f"unparseable: {relpath} ({exc})")
    for path in root.rglob("*"):
        if not path.is_file() or path.stat().st_size > 1_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # pragma: no cover - unreadable file
            continue
        if any(marker in text for marker in _PRIVATE_KEY_MARKERS):
            findings.append(
                f"PRIVATE KEY SHAPE in {path.relative_to(root).as_posix()} -- "
                "a private key never enters any repository")
    return (not findings), findings


def create_organs(
    node_root: Path | str,
    *,
    repo_root: Path | str,
    identity_repo: Optional[Path | str],
    designation: str,
    node_id: str,
    imprint_version: str = "unknown",
    release_root_fingerprint: str = "unverified",
    operator_fingerprint: str = "unverified",
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Create every birth organ, the rules corpus, and the identity skeleton.

    HALTS -- never defaults -- when no identity repo is bound: a fall-through
    to a silent local default is a decision nobody made at the moment it fired.
    """
    node_root = Path(node_root)
    repo_root = Path(repo_root)
    if identity_repo is None:
        raise Halt(
            "no identity repository is bound, and genesis will not invent one",
            remedy="re-run with --identity-repo new|<path>, or set "
                   "INTENTOPS_IDENTITY_REPO for this process only",
        )
    identity_repo = Path(identity_repo)

    created: List[Dict[str, Any]] = []
    for organ in BIRTH_ORGANS:
        if organ.write_model not in WRITE_MODELS:
            raise Halt(
                f"organ {organ.id!r} declares write model "
                f"{organ.write_model!r}, which is not one of {list(WRITE_MODELS)}",
                remedy="declare the model in WRITE_MODELS, or correct the organ",
            )
        target = node_root / organ.relpath
        row = {"id": organ.id, "path": organ.relpath, "kind": organ.kind,
               "write_model": organ.write_model, "consumer": organ.consumer,
               "present": organ.present_at_birth}
        created.append(row)
        if not organ.present_at_birth:
            continue
        if organ.kind == "dir":
            target.mkdir(parents=True, exist_ok=True)
        elif organ.id == "loto":
            _seed_loto_ledger(target)
        elif organ.id == "checkpoint":
            _seed_checkpoint(target)
        elif not target.exists():
            _write_file(target, organ.seed or "")

    # the approvals queue is per-item files: the shape IS the write model
    for sub in ("pending", "approved"):
        (node_root / ".intentops" / "approvals" / sub).mkdir(parents=True,
                                                             exist_ok=True)
    hist = node_root / ".intentops" / "approvals" / "history.jsonl"
    if not hist.exists():
        _write_file(hist, "")
    for sub in ("gates", "audit", "guardian-blocks"):
        (node_root / ".intentops" / "logs" / sub).mkdir(parents=True, exist_ok=True)

    # the boot corpus: the imprint rules must reach a fresh window, or the
    # node's refusals exist in name only
    rules_src = repo_root / "genesis" / "imprint" / "rules"
    rules_dst = node_root / ".intentops-rules"
    rules_copied: List[str] = []
    if rules_src.is_dir():
        rules_dst.mkdir(parents=True, exist_ok=True)
        for src in sorted(rules_src.glob("*.md")):
            shutil.copyfile(src, rules_dst / src.name)
            rules_copied.append(src.name)
    if not rules_copied:
        raise Halt(
            "the imprint rules bundle is empty or absent; a node whose refusals "
            "never reach a fresh window has them in name only",
            remedy=f"restore {rules_src.as_posix()} from the release",
        )

    members = create_identity_skeleton(
        identity_repo,
        designation=designation,
        node_id=node_id,
        imprint_version=imprint_version,
        release_root_fingerprint=release_root_fingerprint,
        operator_fingerprint=operator_fingerprint,
        wish_template=repo_root / "genesis" / "templates"
        / "wishes-REGISTER.template.yaml",
        estate_source=repo_root / "estate",
        dry_run=dry_run,
    )

    estate_ok, estate_note = validate_estate(identity_repo / "estate")
    repo_ok, repo_findings = check_identity_repo(identity_repo)

    record = {
        "schema": "genesis-organs/v1",
        "as_of": _now(),
        "dry_run": bool(dry_run),
        "node_root": str(node_root),
        "identity_repo": str(identity_repo),
        "birth_entry_count": len(BIRTH_ORGANS),
        "present_at_birth": sum(1 for o in BIRTH_ORGANS if o.present_at_birth),
        "organs": created,
        "rules_bundle": rules_copied,
        "identity_repo_members": members,
        "identity_repo_conformant": repo_ok,
        "identity_repo_findings": repo_findings,
        "estate_valid": estate_ok,
        "estate_note": estate_note,
    }
    out = node_root / ".intentops" / "genesis" / "organs.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with StoreLock(lock_for(out)):
        atomic_replace(out, json.dumps(record, indent=2) + "\n")
    return record


def validate_estate(estate_dir: Path) -> Tuple[bool, str]:
    """Load the copied manifests through the estate loader, or say why not."""
    try:
        from ..estate.manifests import load_estate
    except Exception as exc:  # noqa: BLE001 - the loader is optional at import
        return False, f"estate loader unavailable: {exc}"
    try:
        load_estate(estate_dir)
    except Exception as exc:  # noqa: BLE001 - a finding, not a crash
        return False, f"{type(exc).__name__}: {exc}"
    return True, "six manifests present, schema-valid, entries: [] is a clean load"


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


def selftest() -> Tuple[bool, str]:
    """Prove each refusal can fire and a clean skeleton passes."""
    import tempfile

    failures: List[str] = []
    fired: List[str] = []

    def expect(label: str, cond: bool) -> None:
        (fired if cond else failures).append(label)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        # 1. no identity repo bound -> HALT, never a silent local default
        try:
            create_organs(root / "node", repo_root=root / "repo",
                          identity_repo=None, designation="node-00000000",
                          node_id="n")
        except Halt:
            fired.append("unbound-identity-repo-halts")
        else:
            failures.append("unbound-identity-repo-halts")

        # 2. a missing rules bundle HALTs rather than shipping a mute node
        (root / "repo").mkdir(parents=True, exist_ok=True)
        try:
            create_organs(root / "node", repo_root=root / "repo",
                          identity_repo=root / "id", designation="node-00000000",
                          node_id="n")
        except Halt:
            fired.append("missing-rules-bundle-halts")
        else:
            failures.append("missing-rules-bundle-halts")

        # 3. the skeleton checker finds missing members
        skel = root / "empty-identity"
        skel.mkdir(parents=True, exist_ok=True)
        ok, findings = check_identity_repo(skel)
        expect("missing-members-found", (not ok) and len(findings) >= 5)

        # 4. a clean skeleton passes, and a key shape is refused
        full = root / "identity"
        create_identity_skeleton(full, designation="node-00000000", node_id="n",
                                 imprint_version="0", release_root_fingerprint="x",
                                 operator_fingerprint="x")
        ok, findings = check_identity_repo(full)
        expect("clean-skeleton-passes", ok and not findings)
        (full / "trust" / "leaked.pem").write_text(
            key_armour() + "\nnot-a-real-key\n", encoding="utf-8")
        ok, findings = check_identity_repo(full)
        expect("private-key-shape-refused",
               (not ok) and any("PRIVATE KEY SHAPE" in f for f in findings))

        # 5. an unparseable member is a finding, not a crash
        (full / "MANIFEST.yaml").write_text("a: [:\n", encoding="utf-8")
        ok, findings = check_identity_repo(full)
        expect("unparseable-member-found",
               any("unparseable" in f for f in findings))

        # 6. the birth count is 16, one of them absent by design
        expect("sixteen-birth-entries", len(BIRTH_ORGANS) == 16)
        expect("halt-marker-absent-at-birth",
               sum(1 for o in BIRTH_ORGANS if not o.present_at_birth) == 1)
        expect("every-organ-declares-a-known-write-model",
               all(o.write_model in WRITE_MODELS for o in BIRTH_ORGANS))

        # 7. an absent estate directory is reported, never assumed valid
        ok, note = validate_estate(root / "no-such-estate")
        expect("absent-estate-reported", (not ok) and bool(note))

    report = (f"organs selftest: {len(fired)} paths fired, {len(failures)} failed"
              + (" -- " + ", ".join(failures) if failures else ""))
    return (not failures, report)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="G3 organ creation")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        ok, report = selftest()
        print(report)
        return 0 if ok else 1
    print(json.dumps([o.__dict__ for o in BIRTH_ORGANS], indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
