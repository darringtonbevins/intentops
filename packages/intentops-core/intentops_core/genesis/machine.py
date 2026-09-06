"""The genesis state machine -- a clone becomes a node, deterministically.

PURPOSE
    Seven phases and a calibration state. The same substrate plus the same
    operator answers produce the same journal. Three invariants hold across
    every transition:

    1. **Provenance precedes keys.** A node must not mint an identity under
       invariants it cannot prove, so G1 always precedes G2.
    2. **No phase silently degrades into the next.** Each transition appends
       ``{from, to, verdict, evidence, as_of}`` to the journal. A phase that
       cannot record its transition HALTS.
    3. **Every human gate requires a TTY.** Consent obtained from a workflow
       leg, a tick, a subagent or CI is not consent, so a non-interactive
       context is a HALT rather than a default-yes -- except under
       ``--dry-run``, which asks nothing, writes placeholders marked DRY-RUN,
       and mints no durable identity.

WRITE MODEL
    ``.intentops/genesis/journal.jsonl`` -- append-only under ``StoreLock``,
    state as a pure fold. ``--resume`` folds it and re-enters at the last
    completed state; consent and the founding conversation are never re-asked.
    Every other file this module writes is either append-only or a locked
    fresh-read read-modify-write; the phase functions say which.

BLIND SPOTS
    - Determinism is over the journal, not over the clock: timestamps and the
      minted key differ between runs by construction, and the folder ignores
      both when deciding what is complete.
    - ``--rollback G3`` removes what genesis created. It cannot distinguish a
      file the operator edited inside those trees from one genesis wrote, so
      it refuses once consent exists rather than guessing.
    - Host detection reads markers on disk. A host that carries no marker is
      reported as unbound and named; it is never guessed at, and a node with
      no bound saddle has a gate whose verdicts nothing enforces.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..store_guard import StoreLock, atomic_replace, lock_for
from . import (
    BLOCKING_OUTCOMES,
    IDENTITY_REPO_ENV,
    JOURNAL_RELPATH,
    OUTCOMES,
    STATES,
    UNSIGNED_DEV_ENV,
    UNVERIFIED_BOOT_PHRASE,
    GenesisError,
    Halt,
    OperatorGateRequired,
)
from . import aliveness as aliveness_mod
from . import organs as organs_mod
from . import provenance as provenance_mod
from . import standdown as standdown_mod

__all__ = [
    "GenesisContext",
    "PhaseResult",
    "Journal",
    "GenesisRun",
    "FOUNDING_QUESTIONS",
    "UNSIGNED_DEV_BANNER",
    "run_genesis",
    "rollback",
    "selftest",
]

FOUNDING_QUESTIONS: Tuple[str, ...] = (
    "What is this node for -- what work is it here to do?",
    "What must it never do, whatever the reason and whoever asks?",
    "Whose work does it reach besides yours, and how do they learn that it did?",
    "How will you know if it has gone wrong, and what should it do then?",
)

UNSIGNED_DEV_BANNER = (
    "############################################################\n"
    "# PROVENANCE: UNVERIFIED                                    #\n"
    "# This node is running with the unsigned-development flag   #\n"
    "# open. Nothing it carries has been proven to be what the   #\n"
    "# project published. It may not federate, may not publish a #\n"
    "# trust bundle, and its rulings may not be exported as      #\n"
    "# signed artifacts. A T3 tagout is open and the tagout      #\n"
    "# oracle reads BLOCKED, not REMEDIATED, until it is closed. #\n"
    "############################################################"
)

_DEV_TAGOUT_CARRIER = Path(".intentops") / "loto" / "carriers" \
    / "LOTO-GENESIS-UNSIGNED-DEV.md"
_DEV_TAGOUT_PROBE = "GENESIS-UNSIGNED-DEV"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# journal
# ---------------------------------------------------------------------------


class Journal:
    """Append-only transition log. State is a pure fold over it."""

    def __init__(self, node_root: Path | str) -> None:
        self.path = Path(node_root) / JOURNAL_RELPATH

    def append(self, entry: Dict[str, Any]) -> None:
        for key in ("from", "to", "verdict", "as_of"):
            if key not in entry:
                raise Halt(
                    f"a journal entry is missing {key!r}; a phase that cannot "
                    "record its transition halts",
                    remedy="this is a defect in the phase, not in the node",
                )
        if entry["verdict"] not in OUTCOMES:
            raise Halt(
                f"journal verdict {entry['verdict']!r} is outside the closed "
                f"vocabulary {list(OUTCOMES)}",
                remedy="use one of the four outcomes; there is no fifth",
            )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with StoreLock(lock_for(self.path)):
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            raise Halt(f"the genesis journal is unwritable: {exc}",
                       remedy="check permissions on .intentops/genesis/") from exc

    def fold(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: List[Dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                # A malformed line is surfaced, never dropped: a fold that
                # silently skips rows reports a state nobody reached.
                rows.append({"from": "?", "to": "?", "verdict": "HALT",
                             "as_of": "", "malformed": line[:120]})
        return rows

    def completed(self) -> List[str]:
        """States whose transition landed on a non-blocking verdict, in order."""
        done: List[str] = []
        for row in self.fold():
            state = row.get("to")
            if state in STATES and row.get("verdict") not in BLOCKING_OUTCOMES \
                    and state not in done:
                done.append(state)
        return done

    def last_state(self) -> Optional[str]:
        done = self.completed()
        return done[-1] if done else None


# ---------------------------------------------------------------------------
# context and results
# ---------------------------------------------------------------------------


@dataclass
class GenesisContext:
    repo_root: Path
    node_root: Path
    identity_repo: Optional[Path] = None
    identity_repo_arg: Optional[str] = None
    dry_run: bool = False
    allow_unsigned_dev: bool = False
    saddle: Optional[str] = None
    isatty: Callable[[], bool] = staticmethod(lambda: sys.stdin.isatty())
    out: Callable[[str], None] = staticmethod(print)
    designation: str = ""
    node_id: str = ""
    imprint_version: str = "unknown"
    operator_fingerprint: str = "unverified"
    mode: str = "unknown"


@dataclass
class PhaseResult:
    state: str
    verdict: str
    evidence: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)


@dataclass
class GenesisRun:
    final_state: str
    results: List[PhaseResult] = field(default_factory=list)
    halted: bool = False
    halt_reason: str = ""
    remedy: str = ""

    def verdicts(self) -> Dict[str, str]:
        return {r.state: r.verdict for r in self.results}


# ---------------------------------------------------------------------------
# operator gates
# ---------------------------------------------------------------------------


_GATE_REMEDY = (
    "run `intentops genesis` from a terminal you are sitting at, or use "
    "--dry-run to see the whole sequence without minting an identity"
)


def _attended(ctx: GenesisContext) -> bool:
    """Is a human plausibly at this terminal?

    ``isatty()`` alone is not enough on Windows: stdin redirected from the
    NUL device reports True, so a scheduled task, a service, or a CI job can
    walk straight past a TTY-only check and then die inside ``input()``. A
    declared CI environment is treated as unattended for the same reason -- a
    gate that a job can reach is a gate a job can answer.
    """
    if os.environ.get("CI"):
        return False
    try:
        if sys.stdin is None or sys.stdin.closed:
            return False
    except (AttributeError, ValueError):  # pragma: no cover - exotic stdin
        return False
    return bool(ctx.isatty())


def _gate(ctx: GenesisContext, state: str, prompt: str, default: str) -> str:
    """Ask the operator. Dry-run answers with a marked placeholder.

    Never defaults on a real run: a gate reached from a non-interactive
    context is a HALT, because consent from a leg is not consent.

    An EOF on stdin is the SAME refusal, not a crash. It reached one before:
    stdin redirected from the Windows NUL device passes ``isatty()``, so the
    TTY check let the run through and ``input()`` raised an uncaught
    ``EOFError`` -- which is not a ``GenesisError``, so the phase never
    journaled its HALT and the operator got a traceback instead of a verdict.
    A phase that cannot record its own transition halts (design sect. 2,
    invariant 2), and that includes this one.
    """
    if ctx.dry_run:
        return f"DRY-RUN: {default}"
    if not _attended(ctx):
        raise OperatorGateRequired(
            f"{state} needs the operator, and stdin is not an attended terminal",
            remedy=_GATE_REMEDY,
        )
    try:
        answer = input(f"[{state}] {prompt}\n> ").strip()  # noqa: S322 - operator gate
    except EOFError as exc:
        raise OperatorGateRequired(
            f"{state} needs the operator, and stdin reached EOF before "
            "an answer arrived",
            remedy=_GATE_REMEDY,
        ) from exc
    return answer or default


# ---------------------------------------------------------------------------
# G0 substrate
# ---------------------------------------------------------------------------


def _detect_saddle(ctx: GenesisContext) -> Tuple[Optional[str], str]:
    if ctx.saddle:
        return ctx.saddle, "bound by --saddle"
    env = os.environ.get("INTENTOPS_SADDLE")
    if env:
        return env, "bound by INTENTOPS_SADDLE"
    if (ctx.repo_root / ".claude" / "settings.json").exists():
        return "claudecode", "detected: the reference host's settings file is present"
    return None, ("no host marker found. A node with no bound saddle has a gate "
                  "whose verdicts nothing enforces, so this binds NONE rather "
                  "than guessing")


def g0_substrate(ctx: GenesisContext) -> PhaseResult:
    """Measure the host this node is actually on. Pure read, re-runnable."""
    evidence: Dict[str, Any] = {"as_of": _now()}
    try:
        from ..substrate.hardware_detector import detect_hardware
        from ..substrate.mode_detector import detect_mode

        hw = detect_hardware()
        cfg = detect_mode(hw)
        ctx.mode = getattr(getattr(cfg, "mode", None), "value", str(cfg))
        evidence["hardware"] = {
            k: getattr(hw, k) for k in
            ("cpu_cores", "cpu_model", "ram_gb", "gpu_vendor", "gpu_vram_gb",
             "power_source")
            if hasattr(hw, k)
        }
        evidence["mode"] = ctx.mode
    except Exception as exc:  # noqa: BLE001 - a finding, never a crash
        evidence["hardware_error"] = f"{type(exc).__name__}: {exc}"
        ctx.mode = "unknown"

    saddle, why = _detect_saddle(ctx)
    ctx.saddle = saddle
    evidence["saddle"] = saddle
    evidence["saddle_reason"] = why
    out = ctx.node_root / ".intentops" / "genesis" / "substrate.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_replace(out, json.dumps(evidence, indent=2, default=str) + "\n")

    if saddle is None:
        if not ctx.dry_run:
            raise Halt(
                "no host adapter (saddle) could be bound: " + why,
                remedy="pass --saddle <id> from config/saddles.yaml, or set "
                       "INTENTOPS_SADDLE; genesis will not fall through to "
                       "running ungated",
            )
        # A dry run exists to show the operator EVERY thing that would stop a
        # real run, not to stop at the first one.
        return PhaseResult("G0", "WARN", evidence,
                           ["would HALT on a real run: " + why])
    return PhaseResult("G0", "PASS", evidence, [])


# ---------------------------------------------------------------------------
# G1 provenance
# ---------------------------------------------------------------------------


def _render_reasons(checks: Sequence[Any]) -> str:
    """Every blocking reason, id-tagged. Never a slice of what was found."""
    return "; ".join(f"[{c.id}] {c.reason}" for c in checks)


def _contradiction_remedy(record: Any) -> str:
    """The extra sentence a contradiction earns, and nothing when there is none."""
    contradictions = [c for c in record.blocking if getattr(c, "contradiction", False)]
    if not contradictions:
        return ""
    return (
        ". NOTE: "
        + ", ".join(c.id for c in contradictions)
        + " is a CONTRADICTION -- carriers that disagree with each other -- and "
          f"{UNSIGNED_DEV_ENV} does not open it. The flag says 'there is nothing "
          "to verify against yet'; it has never meant 'the evidence disagrees "
          "and proceed anyway'. Fix the carriers or restore the clone."
    )


def _require_unverified_boot_consent(ctx: GenesisContext, record: Any) -> None:
    """The unverified-boot override is an operator act, not an environment.

    design sect. 2.2 (G1) and 11.5, trust-root-design sect. 4.3: the override
    is interactive-only and the operator types the exact phrase. An env var
    alone is a switch a scheduled job can flip, which is precisely the
    "consent from a leg is not consent" failure the gates exist to refuse.

    A dry run does not ask -- it mints nothing -- but it records that it was
    the dry run and not the operator that let the phase past.
    """
    if ctx.dry_run:
        ctx.out(f"[G1] override: DRY-RUN (the phrase {UNVERIFIED_BOOT_PHRASE!r} "
                "is not asked for, and nothing is minted)")
        return
    ctx.out(UNSIGNED_DEV_BANNER)
    if not _attended(ctx):
        raise Halt(
            f"{UNSIGNED_DEV_ENV}=1 is set, and the unverified-boot override is "
            "an operator act: it is refused from a non-interactive context"
            + (" (" + _render_reasons(record.blocking) + ")"
               if record.blocking else ""),
            remedy="run genesis from a terminal you are sitting at and type "
                   f"{UNVERIFIED_BOOT_PHRASE!r} at the prompt, or mint the "
                   "release root and sign the imprint bundle so no override is "
                   "needed",
        )
    typed = _gate(
        ctx, "G1",
        "Provenance is UNVERIFIED and the development flag is open.\n"
        f"Type exactly {UNVERIFIED_BOOT_PHRASE!r} to proceed, or anything "
        "else to halt.",
        default="",
    )
    if typed.strip().lower() != UNVERIFIED_BOOT_PHRASE:
        raise Halt(
            "the unverified-boot phrase was not typed, so G1 halts with "
            "provenance unverified",
            remedy=f"type {UNVERIFIED_BOOT_PHRASE!r} exactly, or mint and sign "
                   "so the override is unnecessary",
        )


def _write_dev_tagout(ctx: GenesisContext) -> List[str]:
    """The unsigned-development flag is expensive on purpose: carrier + T3 tagout."""
    notes: List[str] = []
    carrier = ctx.node_root / _DEV_TAGOUT_CARRIER
    carrier.parent.mkdir(parents=True, exist_ok=True)
    atomic_replace(carrier, "\n".join([
        f"# TAGOUT: {_DEV_TAGOUT_PROBE}",
        "",
        "The unsigned-development flag is OPEN on this node.",
        "",
        "- what is off: provenance verification of the imprint bundle and the",
        "  trust roots (a detection control, therefore T3).",
        f"- authority: the operator who set {UNSIGNED_DEV_ENV}=1.",
        "- way back: mint the release root, sign the imprint manifest, unset the",
        "  flag, and re-run `intentops verify` until it passes on its own merits.",
        "- while it is open: every command prints PROVENANCE: UNVERIFIED and the",
        "  tagout oracle reads BLOCKED, not REMEDIATED.",
        "",
    ]) + "\n")
    try:
        from ..loto import ledger as loto

        loto_path = ctx.node_root / loto.LEDGER_RELPATH
        if not loto_path.exists():
            organs_mod._seed_loto_ledger(loto_path)  # noqa: SLF001 - same package
        loto.create_entry(
            loto_path,
            f"LOTO-{_today()}-GENESIS-UNSIGNED-DEV",
            what="provenance verification of the imprint bundle and trust roots",
            tier="T3",
            carriers=[{"path": _DEV_TAGOUT_CARRIER.as_posix(),
                       "probe": _DEV_TAGOUT_PROBE}],
            by="operator",
            authority=f"{UNSIGNED_DEV_ENV}=1 set by the operator",
            reason="no release root has been minted, so nothing in this clone "
                   "can be verified against a key",
            reenergize_when="the release root is minted and the imprint manifest "
                            "carries a signature that verifies against it",
        )
        notes.append("T3 tagout opened for the unsigned-development flag")
    except Exception as exc:  # noqa: BLE001
        notes.append(f"tagout not recorded: {type(exc).__name__}: {exc}")
    return notes


def g1_provenance(ctx: GenesisContext) -> PhaseResult:
    """Prove what is carried. G1 precedes G2, always."""
    record = provenance_mod.verify_provenance(
        ctx.repo_root, allow_unsigned_dev=ctx.allow_unsigned_dev)
    out = ctx.node_root / ".intentops" / "genesis" / "provenance-record.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    # append-only: each run's record is kept, never overwritten
    existing: List[Any] = []
    if out.exists():
        try:
            existing = json.loads(out.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = [existing]
        except json.JSONDecodeError:
            existing = []
    existing.append(record.to_row())
    with StoreLock(lock_for(out)):
        atomic_replace(out, json.dumps(existing, indent=2) + "\n")

    try:
        import yaml

        manifest = ctx.repo_root / "genesis" / "imprint" / "IMPRINT-MANIFEST.yaml"
        if manifest.exists():
            doc = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
            ctx.imprint_version = str(doc.get("imprint_version") or "unknown")
    except Exception:  # noqa: BLE001 - the version is a label, not a gate
        ctx.imprint_version = "unknown"

    notes = list(record.faculties_absent)
    evidence = {"verified": record.verified, "checks": len(record.checks),
                "blocking": len(record.blocking),
                "imprint_version": ctx.imprint_version}

    if record.blocking:
        # Every reason, never a slice. `blocking[:3]` silently dropped findings
        # from the one line an operator reads, and a detector that reports part
        # of what it found is a detector nobody can act on.
        raise Halt(
            "G1 refused: " + _render_reasons(record.blocking),
            remedy="mint the release root and sign the imprint bundle, or set "
                   f"{UNSIGNED_DEV_ENV}=1 to proceed unverified (a T3 tagout "
                   "opens and every command says so)"
            + _contradiction_remedy(record),
        )
    if ctx.allow_unsigned_dev:
        # The consent gate prints the banner itself on a real run, so the
        # operator reads WHY before typing. A dry run is not asked, so it
        # prints the banner here instead -- once, either way.
        _require_unverified_boot_consent(ctx, record)
        if ctx.dry_run:
            ctx.out(UNSIGNED_DEV_BANNER)
        notes += _write_dev_tagout(ctx)
        return PhaseResult("G1", "WARN", evidence, notes)
    return PhaseResult("G1", "PASS", evidence, notes)


# ---------------------------------------------------------------------------
# G2 keys
# ---------------------------------------------------------------------------

def _mint_keypair() -> Tuple[str, str, bytes]:
    """(public_pem, did:key, private_der). HALTS if the maths is unavailable.

    Never returns a placeholder identity. An upstream implementation returned
    the same UUID string for both halves of a keypair when the crypto library
    was missing; that is not an identity, and a node carrying one reports
    success while being unable to sign anything.
    """
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )
    except ImportError as exc:
        raise Halt(
            "the Ed25519 implementation is unavailable, so this node cannot "
            "mint an identity",
            remedy="pip install cryptography, then re-run `intentops genesis "
                   "--resume`",
        ) from exc

    private = Ed25519PrivateKey.generate()
    public = private.public_key()
    pem = public.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    raw = public.public_bytes(encoding=serialization.Encoding.Raw,
                              format=serialization.PublicFormat.Raw)
    did = "did:key:z" + provenance_mod.b58encode(b"\xed\x01" + raw)
    der = private.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem, did, der


def _fingerprint(pem: str) -> str:
    import hashlib

    from cryptography.hazmat.primitives import serialization

    key = serialization.load_pem_public_key(pem.encode("ascii"))
    der = key.public_bytes(encoding=serialization.Encoding.DER,
                           format=serialization.PublicFormat.SubjectPublicKeyInfo)
    return "sha256:" + hashlib.sha256(der).hexdigest()


def _refuse_repo_path(path: Path, ctx: GenesisContext) -> None:
    """A private key never enters any repository. Checked, not hoped."""
    resolved = path.resolve()
    for root in (ctx.repo_root, ctx.node_root,
                 ctx.identity_repo or ctx.node_root):
        try:
            resolved.relative_to(Path(root).resolve())
        except ValueError:
            continue
        raise Halt(
            f"refusing to write a private key inside a repository: {resolved}",
            remedy="choose a storage location outside the clone and outside the "
                   "identity repository",
        )


def g2_keys(ctx: GenesisContext) -> PhaseResult:
    """Mint the node keypair and bind the operator root. Operator gate."""
    pem, did, private_der = _mint_keypair()
    ctx.node_id = did
    ctx.designation = "node-" + did[len("did:key:z"):][:8]

    storage = _gate(ctx, "G2",
                    "Where should the operator root's private key live? "
                    "(hardware token / TPM / OS credential store / "
                    "passphrase-wrapped file)",
                    "none (dry-run: no private key is persisted)")
    label = _gate(ctx, "G2", "What should this node call you in its records? "
                             "(a label, not a name it will speak to others)",
                  "operator")

    trust_dir = ctx.node_root / ".intentops" / "trust"
    trust_dir.mkdir(parents=True, exist_ok=True)
    fingerprint = _fingerprint(pem)
    ctx.operator_fingerprint = fingerprint if not ctx.dry_run else \
        "DRY-RUN: no operator root was minted"

    cert = {
        "schema": "node-cert/v1",
        "as_of": _now(),
        "designation": ctx.designation,
        "did": did,
        "public_key_pem": pem,          # PUBLIC projection only
        "fingerprint": fingerprint,
        "dry_run": ctx.dry_run,
        "note": "This directory holds PUBLIC projections only. A private key "
                "appearing here is an incident, not a bug.",
    }
    atomic_replace(trust_dir / "node-cert.json", json.dumps(cert, indent=2) + "\n")
    atomic_replace(trust_dir / "operator-root.pub.json", json.dumps({
        "schema": "operator-root/v1",
        "as_of": _now(),
        "label": label,
        "role": "operator_root",
        "status": "dry-run" if ctx.dry_run else "active",
        "fingerprint": ctx.operator_fingerprint,
        "storage": storage,
        "lifetime_years": 3,
        "signs": ["node_identity_cert", "founding_conversation",
                  "alignment_record", "core_mechanic_change"],
        "note": "R-INTENTOPS never signs an operator root and there is no "
                "cross-certification: a release publisher that could mint "
                "authority on installed nodes is one compromised key away from "
                "a fleet-wide coup.",
    }, indent=2) + "\n")

    notes = [f"designation {ctx.designation} derived from the node's did:key"]
    if ctx.dry_run:
        # The private bytes exist only in this process and are never persisted.
        del private_der
        notes.append("DRY-RUN: a keypair was minted in memory and NOT persisted; "
                     "this node cannot sign anything")
    else:
        target = Path(os.path.expanduser("~")) / ".intentops" / "keys" \
            / f"{ctx.designation}.key"
        _refuse_repo_path(target, ctx)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(private_der)
        try:
            os.chmod(target, 0o600)
        except OSError:  # pragma: no cover - platform-dependent
            notes.append("could not restrict permissions on the key file")
        notes.append(f"private key written outside every repository: {target}")
    return PhaseResult("G2", "PASS",
                       {"designation": ctx.designation, "did": did,
                        "storage": storage, "dry_run": ctx.dry_run}, notes)


# ---------------------------------------------------------------------------
# G3 organs
# ---------------------------------------------------------------------------


def g3_organs(ctx: GenesisContext) -> PhaseResult:
    """Every store empty and valid, each declaring its write model."""
    if ctx.identity_repo is None:
        raise Halt(
            "no identity repository is bound",
            remedy="re-run with --identity-repo new|<path>, or set "
                   f"{IDENTITY_REPO_ENV} for this process; genesis will not "
                   "silently create a local one",
        )
    record = organs_mod.create_organs(
        ctx.node_root,
        repo_root=ctx.repo_root,
        identity_repo=ctx.identity_repo,
        designation=ctx.designation,
        node_id=ctx.node_id,
        imprint_version=ctx.imprint_version,
        release_root_fingerprint="unverified",
        operator_fingerprint=ctx.operator_fingerprint,
        dry_run=ctx.dry_run,
    )
    import yaml

    node_cfg = ctx.node_root / ".intentops" / "config" / "node.yaml"
    node_cfg.parent.mkdir(parents=True, exist_ok=True)
    with StoreLock(lock_for(node_cfg)):
        atomic_replace(node_cfg, yaml.safe_dump({
            "schema": "node-config/v1",
            "as_of": _now(),
            "designation": ctx.designation,
            "identity_repo": str(ctx.identity_repo),
            "saddle": ctx.saddle,
            "mode": ctx.mode,
            "imprint_version": ctx.imprint_version,
            "dry_run": ctx.dry_run,
        }, default_flow_style=False, sort_keys=False))

    notes: List[str] = []
    verdict = "PASS"
    if not record["identity_repo_conformant"]:
        verdict = "WARN"
        notes += record["identity_repo_findings"][:5]
    if not record["estate_valid"]:
        verdict = "WARN"
        notes.append(f"estate: {record['estate_note']}")

    probes = aliveness_mod._check_probes(  # noqa: SLF001 - same package
        ctx.repo_root, ctx.node_root, ctx.identity_repo)
    notes.append(f"birth probe suite: {probes.detail}")
    if probes.status != "ANSWERED":
        verdict = "WARN"
    return PhaseResult("G3", verdict,
                       {"birth_entries": record["birth_entry_count"],
                        "present": record["present_at_birth"],
                        "rules": len(record["rules_bundle"]),
                        "probes": probes.counts}, notes)


# ---------------------------------------------------------------------------
# G4 consent, G5 founding, G6 alignment
# ---------------------------------------------------------------------------


def g4_consent(ctx: GenesisContext) -> PhaseResult:
    """Ask the operator before asking for anything else. Append-only."""
    import yaml

    consent = _gate(ctx, "G4",
                    "Do you consent to this node operating on your behalf, "
                    "under the refusals it carries? (yes / no)",
                    "yes")
    off_limits = _gate(ctx, "G4",
                       "What is off limits, entirely? (comma separated, or "
                       "blank for nothing yet)", "")
    path = Path(ctx.identity_repo or ctx.node_root) / "identity" / "consent.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "schema": "consent/v1",
        "as_of": _now(),
        "dry_run": ctx.dry_run,
        "events": [{
            "event": "given" if consent.lower().startswith(("y", "dry-run"))
                     else "withheld",
            "answer": consent,
            "off_limits": [s.strip() for s in off_limits.split(",") if s.strip()],
            "at": _now(),
            "signature": None if ctx.dry_run else "pending-operator-signature",
        }],
        "note": "Append-only. Withdrawing consent is a NEW recorded event, "
                "never a deletion: deleting the record of a consent destroys "
                "the evidence that it was ever asked for.",
    }
    if path.exists():
        try:
            prior = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001 - any parse failure is one finding
            # NOT swallowed. Consent is append-only, so writing over a record we
            # could not read would destroy the evidence that consent was ever
            # asked for -- which is the one thing this file exists to prevent.
            raise Halt(
                f"the existing consent record is unreadable: {exc}",
                remedy="repair or move the file yourself; genesis will not "
                       "overwrite a consent record it could not read",
            ) from exc
        doc["events"] = list(prior.get("events") or []) + doc["events"]
    with StoreLock(lock_for(path)):
        atomic_replace(path, yaml.safe_dump(doc, default_flow_style=False,
                                            sort_keys=False, allow_unicode=True))
    return PhaseResult("G4", "PASS", {"events": len(doc["events"]),
                                      "dry_run": ctx.dry_run},
                       ["consent recorded, append-only"])


def g5_founding(ctx: GenesisContext) -> PhaseResult:
    """The four founding questions, asked once, recorded verbatim."""
    answers: List[Tuple[str, str]] = []
    for question in FOUNDING_QUESTIONS:
        answers.append((question, _gate(ctx, "G5", question,
                                        "(not answered in a dry run)")))
    path = Path(ctx.identity_repo or ctx.node_root) / "identity" \
        / "founding-conversation.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Founding conversation", "",
             f"- node: `{ctx.designation}`", f"- date: {_today()}"]
    if ctx.dry_run:
        lines.append("- **DRY-RUN**: these are placeholders, not answers. A real "
                     "founding conversation is asked once, at a terminal.")
    lines.append("")
    for question, answer in answers:
        lines += [f"## {question}", "", answer, ""]
    atomic_replace(path, "\n".join(lines) + "\n")
    return PhaseResult("G5", "PASS",
                       {"questions": len(answers), "dry_run": ctx.dry_run},
                       ["recorded verbatim; supersession only, never edited"])


def g6_alignment(ctx: GenesisContext) -> PhaseResult:
    """Stage the interview by EVIDENCE AVAILABLE, never by clock."""
    import yaml

    template = ctx.repo_root / "config" / "alignment-interview.template.yaml"
    stages = [
        {"id": "S0", "name": "Consent", "available": True,
         "grade": "recorded fact"},
        {"id": "S1", "name": "Boundary", "available": True,
         "grade": "recorded fact"},
        {"id": "S2", "name": "Domain", "available": True,
         "grade": "INFERRED, never OBSERVED -- a prior that has never moved "
                  "under a real ruling is the node's own prompt reflected back"},
        {"id": "S3", "name": "Replay", "available": False,
         "requires": "at least 10 real rulings in this node's own queue history",
         "grade": "OBSERVED"},
        {"id": "S4", "name": "Commitment", "available": False,
         "requires": "at least 20 real rulings",
         "grade": "OBSERVED"},
    ]
    doc = {
        "schema": "interview-state/v1",
        "as_of": _now(),
        "dry_run": ctx.dry_run,
        "template": template.as_posix() if template.exists() else None,
        "template_present": template.exists(),
        "stages": stages,
        "calibration_bar": {"rulings": 20, "accuracy": 0.80,
                            "forward_only": True},
        "note": "At genesis there are no replays: a fresh operator has ruled "
                "nothing, so the highest-value elicitation instrument is "
                "unavailable on day one BY CONSTRUCTION. Below the bar the "
                "twin is telemetry, never authority, and the honest string is "
                "'uncalibrated: 0 of 20 rulings'.",
    }
    path = Path(ctx.identity_repo or ctx.node_root) / "twin" / "interview-state.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    with StoreLock(lock_for(path)):
        atomic_replace(path, yaml.safe_dump(doc, default_flow_style=False,
                                            sort_keys=False, allow_unicode=True))
    verdict = "PASS" if template.exists() else "WARN"
    notes = ["staged by evidence available, not by clock"]
    if not template.exists():
        notes.append(f"the interview template is absent: {template.as_posix()}")
    return PhaseResult("G6", verdict,
                       {"stages_available": sum(1 for s in stages
                                                if s["available"]),
                        "stages": len(stages)}, notes)


# ---------------------------------------------------------------------------
# G7 online
# ---------------------------------------------------------------------------


def g7_online(ctx: GenesisContext) -> PhaseResult:
    """Come online: answer the six birth questions and write the certificate."""
    reading = aliveness_mod.take_reading(
        ctx.node_root, repo_root=ctx.repo_root,
        identity_repo=ctx.identity_repo, designation=ctx.designation,
        dry_run=ctx.dry_run)
    cert = aliveness_mod.append_reading(reading, ctx.node_root)
    verdict = {"ALIVE": "PASS", "ALIVE-DEGRADED": "WARN",
               "STOOD-DOWN": "WARN", "NOT-ALIVE": "REFUSE"}[reading.verdict]
    return PhaseResult("G7", verdict,
                       {"aliveness": reading.verdict,
                        "certificate": cert.as_posix(),
                        "answered": sum(1 for a in reading.answers
                                        if a.status == "ANSWERED"),
                        "checks": len(reading.answers)},
                       reading.faculties_absent)


_PHASES: Dict[str, Callable[[GenesisContext], PhaseResult]] = {
    "G0": g0_substrate, "G1": g1_provenance, "G2": g2_keys, "G3": g3_organs,
    "G4": g4_consent, "G5": g5_founding, "G6": g6_alignment, "G7": g7_online,
}


# ---------------------------------------------------------------------------
# the driver
# ---------------------------------------------------------------------------


def _resolve_identity_repo(ctx: GenesisContext) -> Optional[Path]:
    """env > genesis binding > node config. There is no default."""
    env = os.environ.get(IDENTITY_REPO_ENV)
    if env:
        return Path(env)                      # one process; never persisted
    arg = ctx.identity_repo_arg
    if arg and arg != "new":
        return Path(arg)
    if arg == "new":
        return ctx.node_root / "identity-repo"
    cfg = ctx.node_root / ".intentops" / "config" / "node.yaml"
    if cfg.exists():
        try:
            import yaml

            doc = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
            bound = doc.get("identity_repo")
            if bound:
                return Path(bound)
        except Exception:  # noqa: BLE001
            return None
    return None


def run_genesis(
    repo_root: Path | str,
    node_root: Path | str,
    *,
    identity_repo: Optional[str] = None,
    dry_run: bool = False,
    resume: bool = False,
    saddle: Optional[str] = None,
    allow_unsigned_dev: Optional[bool] = None,
    isatty: Optional[Callable[[], bool]] = None,
    out: Callable[[str], None] = print,
) -> GenesisRun:
    """Run the machine from G0 (or from the resume point) through G7."""
    ctx = GenesisContext(
        repo_root=Path(repo_root),
        node_root=Path(node_root),
        identity_repo_arg=identity_repo,
        dry_run=dry_run,
        allow_unsigned_dev=(os.environ.get(UNSIGNED_DEV_ENV) == "1"
                            if allow_unsigned_dev is None else allow_unsigned_dev),
        saddle=saddle,
        out=out,
    )
    if isatty is not None:
        ctx.isatty = isatty  # type: ignore[assignment]
    ctx.identity_repo = _resolve_identity_repo(ctx)

    journal = Journal(ctx.node_root)
    done = journal.completed() if resume else []
    if standdown_mod.is_stood_down(ctx.node_root):
        return GenesisRun("STAND-DOWN", [], halted=False,
                          halt_reason=standdown_mod.BANNER)

    run = GenesisRun(final_state=done[-1] if done else "G0")
    previous = done[-1] if done else "-"
    for state in ("G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7"):
        if state in done:
            continue
        try:
            result = _PHASES[state](ctx)
        except GenesisError as exc:
            journal.append({"from": previous, "to": state, "verdict": "HALT",
                            "as_of": _now(), "evidence": {},
                            "reason": str(exc), "remedy": exc.remedy or ""})
            run.halted = True
            run.halt_reason = str(exc)
            run.remedy = exc.remedy or ""
            run.final_state = "HALT"
            return run
        journal.append({"from": previous, "to": state,
                        "verdict": result.verdict, "as_of": _now(),
                        "evidence": result.evidence, "notes": result.notes})
        run.results.append(result)
        previous = state
        run.final_state = state
        if result.verdict in BLOCKING_OUTCOMES:
            run.halted = True
            run.halt_reason = f"{state} returned {result.verdict}"
            return run
    return run


def rollback(node_root: Path | str,
             identity_repo: Optional[Path | str] = None,
             *, to_state: str = "G3") -> Dict[str, Any]:
    """``--rollback G3``: remove ``.intentops/`` and the unsigned skeleton.

    REFUSES once consent exists: G4 and G5 are append-only, and a rollback
    that deleted them would destroy the evidence that consent was ever asked
    for.
    """
    if to_state != "G3":
        raise Halt(f"rollback target {to_state!r} is not supported",
                   remedy="only --rollback G3 exists: G0-G3 are reversible and "
                          "G4 onward is append-only")
    node_root = Path(node_root)
    journal = Journal(node_root)
    completed = journal.completed()
    if "G4" in completed:
        raise Halt(
            "consent has been recorded, so this node is past the reversible "
            "phases",
            remedy="withdrawing consent is a NEW recorded event; use "
                   "`intentops stand-down` to switch the node off instead",
        )
    removed: List[str] = []
    for target in (node_root / ".intentops", node_root / ".intentops-rules"):
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
            removed.append(target.as_posix())
    if identity_repo:
        repo = Path(identity_repo)
        consent = repo / "identity" / "consent.yaml"
        if repo.exists() and not consent.exists():
            shutil.rmtree(repo, ignore_errors=True)
            removed.append(repo.as_posix())
    return {"rolled_back_to": "G3", "removed": removed,
            "note": "G0-G3 are reversible. G2 stays reversible only until the "
                    "operator root signs its first artifact; after that a new "
                    "root is a supersession with lineage, never a replacement."}


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


def selftest() -> Tuple[bool, str]:
    """Prove the journal refuses bad rows, the TTY gate fires, and rollback refuses."""
    import tempfile

    failures: List[str] = []
    fired: List[str] = []

    def expect(label: str, cond: bool) -> None:
        (fired if cond else failures).append(label)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        journal = Journal(root)

        for label, row in (
            ("journal-refuses-missing-field", {"from": "-", "to": "G0"}),
            ("journal-refuses-unknown-verdict",
             {"from": "-", "to": "G0", "verdict": "FINE", "as_of": _now()}),
        ):
            try:
                journal.append(row)
            except Halt:
                fired.append(label)
            else:
                failures.append(label)

        journal.append({"from": "-", "to": "G0", "verdict": "PASS",
                        "as_of": _now()})
        journal.append({"from": "G0", "to": "G1", "verdict": "HALT",
                        "as_of": _now()})
        expect("blocked-transition-is-not-completed",
               journal.completed() == ["G0"])

        # a malformed line is surfaced, never silently dropped
        with journal.path.open("a", encoding="utf-8") as fh:
            fh.write("{not json\n")
        expect("malformed-line-surfaced",
               any(r.get("malformed") for r in journal.fold()))

        # the operator gate refuses a non-TTY context rather than defaulting
        ctx = GenesisContext(repo_root=root, node_root=root,
                             isatty=lambda: False)
        try:
            _gate(ctx, "G4", "consent?", "yes")
        except OperatorGateRequired:
            fired.append("non-tty-gate-halts")
        else:
            failures.append("non-tty-gate-halts")
        ctx.dry_run = True
        expect("dry-run-marks-its-placeholders",
               _gate(ctx, "G4", "consent?", "yes").startswith("DRY-RUN"))

        # rollback refuses once consent exists
        j2 = Journal(root / "n2")
        j2.append({"from": "-", "to": "G4", "verdict": "PASS", "as_of": _now()})
        try:
            rollback(root / "n2")
        except Halt:
            fired.append("rollback-refuses-after-consent")
        else:
            failures.append("rollback-refuses-after-consent")
        expect("rollback-refuses-other-targets",
               _raises(lambda: rollback(root / "n3", to_state="G5")))

        # a stood-down node runs no phases
        standdown_mod.stand_down(root / "n4")
        run = run_genesis(root, root / "n4", dry_run=True)
        expect("stand-down-short-circuits", run.final_state == "STAND-DOWN")

    report = (f"machine selftest: {len(fired)} paths fired, {len(failures)} failed"
              + (" -- " + ", ".join(failures) if failures else ""))
    return (not failures, report)


def _raises(fn: Callable[[], Any]) -> bool:
    try:
        fn()
    except Halt:
        return True
    except Exception:  # noqa: BLE001
        return False
    return False


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="the genesis state machine")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        ok, report = selftest()
        print(report)
        return 0 if ok else 1
    parser.print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
