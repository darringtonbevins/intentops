"""G1 -- prove what this node carries, before it mints what it is.

PURPOSE
    Pure verification. Ten questions asked of files on disk, no network, no
    model, no credential store, no TPM. Every answer is one of the four closed
    genesis outcomes and every answer carries its reason, so a node that comes
    online degraded can NAME the faculty it is without instead of asserting
    health it never checked.

    Two fail-opens in the upstream verifier are closed here and stay closed:
    an UNSIGNED artifact is REFUSED (it never reads as valid-for-dev), and
    CRYPTO-UNAVAILABLE is a HALT with a named remedy (it never returns True).
    Those two returning ``True`` is precisely how a node comes to "verify" a
    bundle nobody signed.

    Ordering is the invariant this module exists to hold: **G1 precedes G2,
    always.** A node must not mint an identity under invariants it cannot
    prove.

WRITE MODEL
    Read-only over the repository. This module writes nothing at all; the
    caller (:mod:`intentops_core.genesis.machine`) appends the record it
    returns to ``.intentops/genesis/provenance-record.json``, which is
    append-only.

BLIND SPOTS
    - Three carriers of one fingerprint make a PARTIAL tamper visible; they do
      not make a TOTAL substitution visible. First-clone authenticity is
      trust-on-first-use unless the operator compares out of band.
    - Signature verification here is shape-and-presence only until a real root
      is minted: there is no key to verify against, and this module says
      "unsigned, therefore REFUSED" rather than pretending to check maths it
      cannot do.
    - Hash verification is delegated to ``scripts/genesis/build_manifest.py``
      by import-from-path, so there is exactly ONE definition of what a bundle
      hash is. If that script is absent this module HALTs; it never falls back
      to a second, divergent implementation.
    - It reads files. It cannot tell whether the whole repository was replaced
      before the first read.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import Halt
from . import trust_pin

__all__ = [
    "Check",
    "ProvenanceRecord",
    "crypto_available",
    "b58encode",
    "verify_provenance",
    "selftest",
]

_MANIFEST_RELPATH = Path("genesis") / "imprint" / "IMPRINT-MANIFEST.yaml"
_ROOTS_RELPATH = Path("config") / "trust-roots.yaml"
_REVOCATION_RELPATH = Path("config") / "trust-revocation.json"
_CATALOGUE_RELPATH = Path("genesis") / "imprint" / "archetypes" / "CATALOGUE.yaml"
_BUILD_MANIFEST_RELPATH = Path("scripts") / "genesis" / "build_manifest.py"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _yaml() -> Any:
    try:
        import yaml  # noqa: PLC0415 - optional dependency, checked at use
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise Halt(
            "PyYAML is required to read the trust and imprint files",
            remedy="pip install pyyaml (it is a declared dependency of this package)",
        ) from exc
    return yaml


@dataclass
class Check:
    """One question, its answer, and why."""

    id: str
    question: str
    outcome: str
    reason: str
    evidence: Dict[str, Any] = field(default_factory=dict)
    downgraded_from: Optional[str] = None
    #: True when the finding is carriers CONTRADICTING each other rather than
    #: evidence being absent. The development flag opens the second and never
    #: the first: "there is nothing to verify against yet" and "the material
    #: disagrees with itself" are different findings arriving at one gate, and
    #: a flag that cannot tell them apart is a flag that hides tampering.
    contradiction: bool = False

    def to_row(self) -> Dict[str, Any]:
        row = {
            "id": self.id,
            "question": self.question,
            "outcome": self.outcome,
            "reason": self.reason,
            "evidence": self.evidence,
        }
        if self.downgraded_from:
            row["downgraded_from"] = self.downgraded_from
        if self.contradiction:
            row["contradiction"] = True
        return row


@dataclass
class ProvenanceRecord:
    """The G1 answer sheet. ``verified`` is never True on a downgrade."""

    as_of: str
    checks: List[Check] = field(default_factory=list)
    dev_flag_open: bool = False
    crypto: bool = False

    @property
    def verified(self) -> bool:
        """True only when every check passed on its own merits.

        A check downgraded by the development flag keeps ``verified`` False
        for the life of the record: a flag can let a node proceed, and it can
        never make an unverified thing verified.
        """
        return all(c.outcome == "PASS" and c.downgraded_from is None
                   for c in self.checks) and bool(self.checks)

    @property
    def blocking(self) -> List[Check]:
        return [c for c in self.checks if c.outcome in ("REFUSE", "HALT")]

    @property
    def faculties_absent(self) -> List[str]:
        """What this node is therefore without -- named, never implied."""
        return [f"{c.id}: {c.reason}" for c in self.checks
                if c.outcome in ("REFUSE", "HALT") or c.downgraded_from]

    def to_row(self) -> Dict[str, Any]:
        return {
            "as_of": self.as_of,
            "verified": self.verified,
            "dev_flag_open": self.dev_flag_open,
            "crypto_available": self.crypto,
            "checks": [c.to_row() for c in self.checks],
            "faculties_absent": self.faculties_absent,
        }


def crypto_available() -> bool:
    """Is the Ed25519 implementation importable on this host?"""
    return importlib.util.find_spec("cryptography") is not None


# ---------------------------------------------------------------------------
# individual checks -- each returns a Check, none of them raises on a finding
# ---------------------------------------------------------------------------


def _check_roots_file(repo_root: Path) -> Tuple[Check, Optional[Dict[str, Any]]]:
    path = repo_root / _ROOTS_RELPATH
    q = "does this clone carry a readable trust-roots file with at least one root?"
    if not path.exists():
        return Check("G1.1-roots-present", q, "HALT",
                     f"absent: {_ROOTS_RELPATH.as_posix()}",
                     {"path": _ROOTS_RELPATH.as_posix()}), None
    try:
        doc = _yaml().safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - any parse failure is one finding
        # Contradiction, not absence: the file is HERE and does not say what a
        # trust-roots file says. The development flag does not open that.
        return Check("G1.1-roots-present", q, "HALT",
                     f"unparseable: {exc}", {"path": _ROOTS_RELPATH.as_posix()},
                     contradiction=True), None
    if not isinstance(doc, dict):
        return Check("G1.1-roots-present", q, "HALT",
                     "trust-roots root is not a mapping", {},
                     contradiction=True), None
    roots = doc.get("roots")
    if not roots:
        # Deliberately the OPPOSITE of an empty estate manifest: a node with no
        # assets yet is new; a node with no roots can verify nothing.
        return Check("G1.1-roots-present", q, "HALT",
                     "roots: [] -- a node with no roots can verify nothing",
                     {"roots": 0}), None
    return Check("G1.1-roots-present", q, "PASS",
                 f"{len(roots)} root(s) declared", {"roots": len(roots)}), doc


def _check_status(doc: Dict[str, Any]) -> Check:
    q = "is every declared root actually active, or still proposed?"
    proposed = [r.get("id") for r in doc.get("roots", [])
                if r.get("status") != "active"]
    if proposed:
        return Check("G1.2-root-status", q, "REFUSE",
                     "root(s) still `proposed`: no ceremony has minted them, so "
                     "nothing in this clone can be verified against a key",
                     {"proposed": proposed})
    return Check("G1.2-root-status", q, "PASS", "all roots active", {})


def _check_pin(doc: Dict[str, Any]) -> Check:
    q = "do the compiled pin and the trust-roots file agree on the release root?"
    outcome, reason = trust_pin.compare(doc.get("pinned_fingerprint"))
    return Check("G1.3-pin", q, outcome, reason,
                 {"pin_state": trust_pin.PIN_STATE,
                  "file_pinned": doc.get("pinned_fingerprint")})


def _derive_representations(pem: str) -> Tuple[str, str]:
    """(fingerprint, did) derived from a PEM public key. Raises on bad input."""
    import hashlib

    from cryptography.hazmat.primitives import serialization  # noqa: PLC0415

    key = serialization.load_pem_public_key(pem.encode("utf-8"))
    der = key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    fingerprint = "sha256:" + hashlib.sha256(der).hexdigest()
    raw = key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    did = "did:key:z" + b58encode(b"\xed\x01" + raw)
    return fingerprint, did


_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(payload: bytes) -> str:
    """base58btc, as did:key requires. Leading zero bytes map to '1'."""
    num = int.from_bytes(payload, "big")
    out = ""
    while num > 0:
        num, rem = divmod(num, 58)
        out = _B58[rem] + out
    pad = 0
    for byte in payload:
        if byte:
            break
        pad += 1
    return "1" * pad + out


def _check_representations(doc: Dict[str, Any]) -> Check:
    """Absence and disagreement are tracked SEPARATELY here, on purpose.

    A placeholder key is "nothing has been minted yet" -- the development
    flag's own case. A real key whose fingerprint or did:key does not match its
    DER SPKI bytes is a CONTRADICTION, and no flag opens it. Folding both into
    one findings list made the whole check downgradable, so tampered material
    rode through behind an unminted root.
    """
    q = "do all four representations of each key agree?"
    findings: List[str] = []
    disagreements: List[str] = []
    checked = 0
    for root in doc.get("roots", []):
        rid = root.get("id", "<unnamed>")
        pem = root.get("public_key_pem")
        fp = root.get("fingerprint")
        did = root.get("did")
        if not trust_pin.is_real_fingerprint(fp) or not isinstance(pem, str) \
                or "BEGIN PUBLIC KEY" not in (pem or ""):
            findings.append(f"{rid}: placeholder or malformed key material")
            continue
        if not crypto_available():
            # Never True. A host that cannot do the maths cannot claim the
            # answer, and saying so is the point of this branch existing.
            raise Halt(
                "the Ed25519 implementation is unavailable, so key "
                "representations cannot be checked",
                remedy="pip install cryptography, then re-run `intentops verify`",
            )
        try:
            derived_fp, derived_did = _derive_representations(pem)
        except Exception as exc:  # noqa: BLE001
            # The material claims to be a key and will not load: it disagrees
            # with its own declared shape.
            disagreements.append(f"{rid}: public_key_pem does not load ({exc})")
            continue
        checked += 1
        if derived_fp != fp:
            disagreements.append(
                f"{rid}: fingerprint disagrees with the DER SPKI bytes")
        if did and derived_did != did:
            disagreements.append(f"{rid}: did:key disagrees with the key material")
    if disagreements or findings:
        return Check("G1.4-representations", q, "HALT",
                     "; ".join(disagreements + findings), {"checked": checked},
                     contradiction=bool(disagreements))
    return Check("G1.4-representations", q, "PASS",
                 f"{checked}/{checked} roots agree across representations",
                 {"checked": checked})


def _check_revocation(repo_root: Path, cached_sequence: Optional[int]) -> Check:
    q = "is the revocation list present and monotonic?"
    path = repo_root / _REVOCATION_RELPATH
    if not path.exists():
        return Check("G1.5-revocation", q, "REFUSE",
                     f"absent: {_REVOCATION_RELPATH.as_posix()}", {})
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return Check("G1.5-revocation", q, "HALT", f"unparseable: {exc}", {})
    seq = doc.get("sequence")
    if not isinstance(seq, int):
        return Check("G1.5-revocation", q, "HALT",
                     "revocation list carries no integer `sequence`", {})
    if cached_sequence is not None and seq < cached_sequence:
        return Check("G1.5-revocation", q, "REFUSE",
                     f"sequence {seq} is lower than the cached {cached_sequence} "
                     "-- rollback refused", {"sequence": seq})
    return Check("G1.5-revocation", q, "PASS",
                 f"sequence {seq}, {len(doc.get('entries') or [])} entries",
                 {"sequence": seq})


def _load_build_manifest(repo_root: Path) -> Any:
    """Import the bundle hasher from its script path -- one definition only."""
    path = repo_root / _BUILD_MANIFEST_RELPATH
    if not path.exists():
        raise Halt(
            f"the imprint hasher is absent: {_BUILD_MANIFEST_RELPATH.as_posix()}",
            remedy="restore the file from the release, or re-clone; genesis will "
                   "not hash the bundle a second, divergent way",
        )
    spec = importlib.util.spec_from_file_location("_intentops_build_manifest", path)
    if spec is None or spec.loader is None:  # pragma: no cover - import plumbing
        raise Halt(f"cannot load {path}", remedy="check file permissions")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _check_imprint_hashes(repo_root: Path) -> Check:
    q = "are the imprint bundle's bytes the ones the manifest claims?"
    module = _load_build_manifest(repo_root)
    ok, findings = module.check(repo_root)
    if ok:
        return Check("G1.6-imprint-hashes", q, "PASS",
                     "every claimed artifact matches on disk", {})
    # CHANGED (a byte moved) and MISSING (a claimed artifact is gone) are the
    # manifest and the bundle contradicting each other. UNTRACKED / UNCLAIMED
    # are drift in what the manifest COVERS, which the development flag may
    # legitimately carry on an unbuilt clone.
    contradiction = any(f.startswith(("CHANGED", "MISSING")) for f in findings)
    return Check("G1.6-imprint-hashes", q, "HALT",
                 "; ".join(findings[:6]), {"findings": len(findings)},
                 contradiction=contradiction)


def _check_imprint_signature(repo_root: Path) -> Check:
    q = "who signed this imprint, and does the signature verify?"
    path = repo_root / _MANIFEST_RELPATH
    if not path.exists():
        return Check("G1.7-imprint-signature", q, "HALT",
                     f"absent: {_MANIFEST_RELPATH.as_posix()}", {})
    doc = _yaml().safe_load(path.read_text(encoding="utf-8")) or {}
    sigs = doc.get("signatures")
    status = doc.get("status")
    if sigs is None:
        return Check("G1.7-imprint-signature", q, "HALT",
                     "the manifest has no `signatures` key -- a field nobody "
                     "wrote is a field nobody read", {})
    if not sigs:
        return Check("G1.7-imprint-signature", q, "REFUSE",
                     "the imprint manifest is UNSIGNED. Unsigned and valid must "
                     "never be indistinguishable, so this is a refusal, not a "
                     "warning", {"status": status, "signatures": 0})
    return Check("G1.7-imprint-signature", q, "REFUSE",
                 "signatures are present but no minted release root exists to "
                 "verify them against", {"signatures": len(sigs)})


def _check_archetypes(repo_root: Path, operator_fingerprint: Optional[str]) -> Check:
    q = "is any identity archetype in the catalogue one this node may adopt?"
    path = repo_root / _CATALOGUE_RELPATH
    if not path.exists():
        return Check("G1.8-archetypes", q, "REFUSE",
                     f"absent: {_CATALOGUE_RELPATH.as_posix()}", {})
    doc = _yaml().safe_load(path.read_text(encoding="utf-8")) or {}
    entries = doc.get("entries")
    if entries is None:
        return Check("G1.8-archetypes", q, "HALT",
                     "the catalogue has no `entries` key -- refusing to guess "
                     "where archetypes live", {})
    reserved = [e for e in entries if e.get("status") == "reserved"]
    adoptable = [e.get("id") for e in reserved
                 if operator_fingerprint
                 and e.get("bound_to") == operator_fingerprint]
    return Check(
        "G1.8-archetypes", q, "PASS",
        f"{len(reserved)} reserved of {len(entries)} entries; "
        f"{len(adoptable)} adoptable by this node's operator root "
        "(a reserved archetype bound to another operator's fingerprint is "
        "REFUSED at adoption, and the node is told which and why)",
        {"entries": len(entries), "reserved": len(reserved),
         "adoptable": adoptable},
    )


# ---------------------------------------------------------------------------
# the pass
# ---------------------------------------------------------------------------


def verify_provenance(
    repo_root: Path | str,
    *,
    allow_unsigned_dev: bool = False,
    operator_fingerprint: Optional[str] = None,
    cached_revocation_sequence: Optional[int] = None,
) -> ProvenanceRecord:
    """Run every G1 check and return the record. Never writes, never exits.

    With ``allow_unsigned_dev`` the *absence-class* blocking outcomes are
    DOWNGRADED to WARN and each keeps ``downgraded_from``, so the record still
    says exactly what failed. ``verified`` stays False forever on such a record.

    **A CONTRADICTION is never downgraded.** Key material that disagrees with
    its own fingerprint or did:key, an imprint artifact whose bytes moved or
    went missing, a trust-roots file that will not parse -- those are carriers
    disagreeing with each other, not evidence that has not been minted yet. The
    flag exists for the second case. Before this distinction existed, a clone
    with tampered key material reached G7 and exited 0 with the flag set, which
    is the exact shape of a detector that goes green because it stopped looking.
    """
    repo_root = Path(repo_root)
    record = ProvenanceRecord(as_of=_now(), dev_flag_open=allow_unsigned_dev,
                              crypto=crypto_available())

    first, doc = _check_roots_file(repo_root)
    record.checks.append(first)
    if doc is not None:
        record.checks.append(_check_status(doc))
        record.checks.append(_check_pin(doc))
        try:
            record.checks.append(_check_representations(doc))
        except Halt as exc:
            record.checks.append(Check("G1.4-representations",
                                       "do all four representations agree?",
                                       "HALT", exc.render(), {}))
    record.checks.append(_check_revocation(repo_root, cached_revocation_sequence))
    for fn in (_check_imprint_hashes,):
        try:
            record.checks.append(fn(repo_root))
        except Halt as exc:
            record.checks.append(Check("G1.6-imprint-hashes",
                                       "are the imprint bytes the claimed ones?",
                                       "HALT", exc.render(), {}))
    record.checks.append(_check_imprint_signature(repo_root))
    record.checks.append(_check_archetypes(repo_root, operator_fingerprint))

    if allow_unsigned_dev:
        for check in record.checks:
            if check.outcome in ("REFUSE", "HALT") and not check.contradiction:
                check.downgraded_from = check.outcome
                check.outcome = "WARN"
    return record


# ---------------------------------------------------------------------------
# selftest -- a detector that has never fired is indistinguishable from a
# broken one
# ---------------------------------------------------------------------------


def selftest() -> Tuple[bool, str]:
    """Prove every outcome path can actually fire. Returns (ok, report)."""
    import tempfile

    failures: List[str] = []
    fired: List[str] = []

    def expect(label: str, cond: bool) -> None:
        if cond:
            fired.append(label)
        else:
            failures.append(label)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        # 1. absent roots file -> HALT
        check, doc = _check_roots_file(root)
        expect("absent-roots-halts", check.outcome == "HALT" and doc is None)

        # 2. roots: [] -> HALT (the opposite of an empty estate manifest)
        (root / "config").mkdir(parents=True, exist_ok=True)
        (root / _ROOTS_RELPATH).write_text("schema: trust-roots/v1\nroots: []\n",
                                           encoding="utf-8")
        check, doc = _check_roots_file(root)
        expect("empty-roots-halts", check.outcome == "HALT")

        # 3. unparseable -> HALT
        (root / _ROOTS_RELPATH).write_text("roots: [:\n", encoding="utf-8")
        check, _ = _check_roots_file(root)
        expect("unparseable-roots-halts", check.outcome == "HALT")

        # 4. proposed root -> REFUSE
        expect("proposed-root-refuses",
               _check_status({"roots": [{"id": "R", "status": "proposed"}]}).outcome
               == "REFUSE")
        expect("active-root-passes",
               _check_status({"roots": [{"id": "R", "status": "active"}]}).outcome
               == "PASS")

        # 5. placeholder pin -> HALT, and two placeholders never read as PASS
        expect("placeholder-pin-halts",
               _check_pin({"pinned_fingerprint": trust_pin.PLACEHOLDER_FINGERPRINT})
               .outcome == "HALT")

        # 6. placeholder key material -> HALT on representations
        expect("placeholder-representations-halt",
               _check_representations(
                   {"roots": [{"id": "R", "public_key_pem": "PLACEHOLDER-NOT-A-KEY",
                               "fingerprint": "sha256:PLACEHOLDER"}]}).outcome
               == "HALT")

        # 7. revocation: absent, malformed, rollback, clean
        expect("revocation-absent-refuses",
               _check_revocation(root, None).outcome == "REFUSE")
        (root / _REVOCATION_RELPATH).write_text('{"sequence": "x"}', encoding="utf-8")
        expect("revocation-malformed-halts",
               _check_revocation(root, None).outcome == "HALT")
        (root / _REVOCATION_RELPATH).write_text('{"sequence": 2, "entries": []}',
                                                encoding="utf-8")
        expect("revocation-rollback-refuses",
               _check_revocation(root, 5).outcome == "REFUSE")
        expect("revocation-clean-passes",
               _check_revocation(root, 1).outcome == "PASS")

        # 8. the hasher's absence HALTs rather than hashing a second way
        try:
            _load_build_manifest(root)
        except Halt:
            fired.append("missing-hasher-halts")
        else:
            failures.append("missing-hasher-halts")

        # 9. unsigned manifest REFUSES; a missing signatures KEY HALTs
        (root / "genesis" / "imprint").mkdir(parents=True, exist_ok=True)
        (root / _MANIFEST_RELPATH).write_text("signatures: []\nstatus: proposed\n",
                                              encoding="utf-8")
        expect("unsigned-imprint-refuses",
               _check_imprint_signature(root).outcome == "REFUSE")
        (root / _MANIFEST_RELPATH).write_text("status: proposed\n", encoding="utf-8")
        expect("missing-signatures-key-halts",
               _check_imprint_signature(root).outcome == "HALT")

        # 10. the dev flag downgrades and NEVER makes a record verified
        rec = ProvenanceRecord(as_of=_now(), dev_flag_open=True)
        rec.checks.append(Check("x", "q", "WARN", "r", {}, downgraded_from="HALT"))
        expect("downgrade-never-verifies", rec.verified is False)
        expect("downgrade-names-the-faculty", len(rec.faculties_absent) == 1)

        # 11. the dev flag NEVER masks a contradiction. Absence downgrades;
        #     carriers that disagree with each other stay blocking.
        absence = Check("G1.2-root-status", "q", "REFUSE", "still proposed", {})
        contra = Check("G1.4-representations", "q", "HALT",
                       "fingerprint disagrees with the DER SPKI bytes", {},
                       contradiction=True)
        rec2 = ProvenanceRecord(as_of=_now(), dev_flag_open=True)
        rec2.checks.extend([absence, contra])
        for check in rec2.checks:
            if check.outcome in ("REFUSE", "HALT") and not check.contradiction:
                check.downgraded_from = check.outcome
                check.outcome = "WARN"
        expect("dev-flag-downgrades-absence", absence.outcome == "WARN")
        expect("dev-flag-never-masks-a-contradiction",
               contra.outcome == "HALT" and [c.id for c in rec2.blocking]
               == ["G1.4-representations"])

        # 12. a real key whose fingerprint disagrees is a CONTRADICTION, and a
        #     placeholder is not -- the two must never be one finding.
        if crypto_available():
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PrivateKey,
            )

            pem = Ed25519PrivateKey.generate().public_key().public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            ).decode("ascii")
            real_fp, _real_did = _derive_representations(pem)
            bad = _check_representations({"roots": [
                {"id": "R", "public_key_pem": pem,
                 "fingerprint": "sha256:" + "0" * 64},
            ]})
            expect("real-key-mismatch-is-a-contradiction",
                   bad.outcome == "HALT" and bad.contradiction is True)
            good = _check_representations({"roots": [
                {"id": "R", "public_key_pem": pem, "fingerprint": real_fp},
            ]})
            expect("agreeing-representations-pass", good.outcome == "PASS")
        expect("placeholder-is-not-a-contradiction",
               _check_representations(
                   {"roots": [{"id": "R", "public_key_pem": "PLACEHOLDER-NOT-A-KEY",
                               "fingerprint": "sha256:PLACEHOLDER"}]}
               ).contradiction is False)

        # 13. an unparseable trust-roots file is a contradiction, an absent one
        #     is not: the file is there and does not say what it must.
        (root / "config").mkdir(parents=True, exist_ok=True)
        (root / _ROOTS_RELPATH).write_text("roots: [\n  - broken: {\n",
                                           encoding="utf-8")
        broken, _doc = _check_roots_file(root)
        expect("unparseable-roots-is-a-contradiction",
               broken.outcome == "HALT" and broken.contradiction is True)
        absent_root = Path(td) / "no-such-clone"
        missing, _doc2 = _check_roots_file(absent_root)
        expect("absent-roots-is-not-a-contradiction",
               missing.outcome == "HALT" and missing.contradiction is False)

    report = (f"provenance selftest: {len(fired)} paths fired, "
              f"{len(failures)} failed"
              + (" -- " + ", ".join(failures) if failures else ""))
    return (not failures, report)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="G1 provenance verification")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--allow-unsigned-dev", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        ok, report = selftest()
        print(report)
        return 0 if ok else 1

    record = verify_provenance(Path(args.repo_root),
                               allow_unsigned_dev=args.allow_unsigned_dev)
    print(json.dumps(record.to_row(), indent=2))
    return 0 if record.verified else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
