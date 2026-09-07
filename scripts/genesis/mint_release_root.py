#!/usr/bin/env python3
"""Mint the project's release root, and sign the imprint manifest with it.

PURPOSE
    The ceremony tooling that ``config/trust-roots.yaml`` and
    ``genesis/trust_pin.py`` have both claimed exists since the seed. It did
    not. Until 2026-09-06 the trust-pin docstring said the pin is "edited only
    by the release ceremony's tooling", the trust-roots header said "the
    ceremony tooling flips it, together with the real key material, in one
    signed, reviewed change", and no such tool was in the tree -- so the only
    way to mint a root was by hand, which is precisely the shape of the attack
    the three-carrier pin exists to make visible. A carrier that names a
    control nobody built is worse than no carrier: it stops people looking.

    Two verbs, deliberately separate:

    ``mint``
        Generate an Ed25519 release root OUTSIDE the repository, print its
        public representations, and emit the exact three-carrier edit the
        operator must review and apply. It never edits the repository itself.
    ``sign``
        Sign ``genesis/imprint/IMPRINT-MANIFEST.yaml`` with a minted key, over
        the canonical payload defined ONCE in
        :func:`intentops_core.genesis.provenance.canonical_imprint_payload`.

    Both are ATTENDED-ONLY. Neither may be run from a loop tick, a workflow
    leg, a subagent, or CI -- see docs/TRUST-CEREMONY.md, which this tool is
    the mechanical half of.

WRITE MODEL
    Single-writer, attended, outside the repository. ``mint`` writes exactly
    one file: the private key, at a path the operator names, refused if it
    resolves inside any repository. ``sign`` performs a locked read-modify-
    write of the imprint manifest's ``signatures`` list via ``atomic_replace``,
    reading inside the lock. Nothing here writes ``config/trust-roots.yaml`` or
    the compiled pin: those two edits are the operator's own reviewed change,
    because a tool that can rewrite the pin unattended is the coup the pin
    exists to prevent.

BLIND SPOTS
    - It cannot make a ceremony witnessed, offline, or performed on clean
      hardware. Those are properties of the sitting, not of this script, and
      the runbook is where they live.
    - It proves nothing about the key's custody after it writes it. A key on a
      networked laptop and a key on an air-gapped machine are identical bytes.
    - ``--selftest`` exercises the mint/sign/verify round trip on throwaway
      keys in a temporary directory. A passing selftest says the arithmetic
      works; it says nothing about whether the real ceremony was run properly.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import os
import re
import sys
from pathlib import Path
from typing import Any, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
_PKG = REPO_ROOT / "packages" / "intentops-core"
if str(_PKG) not in sys.path:  # pragma: no cover - import plumbing
    sys.path.insert(0, str(_PKG))

from intentops_core.genesis import provenance as provenance_mod  # noqa: E402
from intentops_core.genesis import trust_pin  # noqa: E402
from intentops_core.store_guard import StoreLock, atomic_replace  # noqa: E402

MANIFEST_RELPATH = Path("genesis") / "imprint" / "IMPRINT-MANIFEST.yaml"
ROOTS_RELPATH = Path("config") / "trust-roots.yaml"

ARMING_PHRASE = "mint the release root"

#: Every carrier that must move together when a root is minted. Named here so
#: the tool can PRINT the whole edit; it deliberately applies none of them.
CARRIERS = (
    "config/trust-roots.yaml -- the full root record and pinned_fingerprint",
    "packages/intentops-core/intentops_core/genesis/trust_pin.py -- "
    "ROOT_FINGERPRINT and PIN_STATE",
    "docs/GENESIS.md -- the human-readable fingerprint",
)


class CeremonyRefused(RuntimeError):
    """The ceremony did not proceed, and says why. Never a silent return."""


# ---------------------------------------------------------------------------
# attendance -- consent from a job is not consent
# ---------------------------------------------------------------------------


def attended() -> bool:
    """Is a human plausibly at this terminal?

    Same shape as the genesis machine's gate, and for the same reason: on
    Windows a stdin redirected from the NUL device reports ``isatty()`` True,
    so a TTY-only check lets a scheduled task straight through.
    """
    if os.environ.get("CI"):
        return False
    try:
        if sys.stdin is None or sys.stdin.closed:
            return False
        return bool(sys.stdin.isatty())
    except (AttributeError, ValueError):  # pragma: no cover - exotic stdin
        return False


def require_arming(expected: str = ARMING_PHRASE) -> None:
    """Refuse unless a human types the exact phrase. EOF is a refusal."""
    if not attended():
        raise CeremonyRefused(
            "the root ceremony is attended-only, and stdin is not a terminal. "
            "It is never run from a loop tick, a workflow leg, a subagent, or "
            "CI -- see docs/TRUST-CEREMONY.md."
        )
    try:
        typed = input(f"Type exactly: {expected}\n> ").strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise CeremonyRefused("stdin ended before the arming phrase") from exc
    if typed != expected:
        raise CeremonyRefused("the arming phrase was not typed exactly")


def refuse_repo_path(path: Path) -> None:
    """A private key never enters any repository. Checked, not hoped.

    Deliberately the same rule the genesis machine applies to the OPERATOR
    root; a release root is strictly more dangerous, so it does not get a
    weaker check.
    """
    resolved = path.resolve()
    try:
        resolved.relative_to(REPO_ROOT.resolve())
    except ValueError:
        return
    raise CeremonyRefused(
        f"refusing to write a private key inside the repository: {resolved}"
    )


# ---------------------------------------------------------------------------
# the maths
# ---------------------------------------------------------------------------


def _crypto() -> Any:
    try:
        from cryptography.hazmat.primitives import serialization  # noqa: PLC0415
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: PLC0415
            Ed25519PrivateKey,
        )
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise CeremonyRefused(
            "the Ed25519 implementation is not importable; a ceremony that "
            "cannot do the maths must halt, never degrade"
        ) from exc
    return serialization, Ed25519PrivateKey


def representations(public_pem: str) -> Tuple[str, str]:
    """(fingerprint, did:key) for a PEM public key, derived one way only."""
    serialization, _ = _crypto()
    key = serialization.load_pem_public_key(public_pem.encode("ascii"))
    der = key.public_bytes(encoding=serialization.Encoding.DER,
                           format=serialization.PublicFormat.SubjectPublicKeyInfo)
    raw = key.public_bytes(encoding=serialization.Encoding.Raw,
                           format=serialization.PublicFormat.Raw)
    fingerprint = "sha256:" + hashlib.sha256(der).hexdigest()
    did = "did:key:z" + provenance_mod.b58encode(b"\xed\x01" + raw)
    return fingerprint, did


def key_id(fingerprint: str) -> str:
    """The 16-character key id used in signature entries."""
    return fingerprint.split(":", 1)[1][:16].upper()


def mint(out: Path, passphrase: Optional[str]) -> dict:
    """Generate a release root and write the private key to ``out``.

    Returns the PUBLIC record only. The private bytes are written once and
    never returned, printed, or logged.
    """
    serialization, Ed25519PrivateKey = _crypto()
    refuse_repo_path(out)
    private = Ed25519PrivateKey.generate()
    public_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    if passphrase:
        algorithm: Any = serialization.BestAvailableEncryption(
            passphrase.encode("utf-8"))
    else:
        raise CeremonyRefused(
            "a release root is never written unencrypted. Supply a passphrase."
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(private.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=algorithm,
    ))
    try:
        os.chmod(out, 0o600)
    except OSError:  # pragma: no cover - platform-dependent
        pass
    fingerprint, did = representations(public_pem)
    return {
        "public_key_pem": public_pem,
        "fingerprint": fingerprint,
        "did": did,
        "key_id": key_id(fingerprint),
        "authority_string": trust_pin.authority_string(fingerprint, did),
        "private_key_path": str(out),
    }


def _signing_lock_path(repo_root: Path) -> Path:
    """Where the imprint signing lock lives -- OUTSIDE the audited bundle.

    Corrected 2026-09-06. This lock used to be
    ``genesis/imprint/IMPRINT-MANIFEST.lock``: a sibling of the store, which is
    the house convention and is wrong exactly here. ``genesis/imprint`` is the
    HASH-AUDITED bundle, so a lock file dropped into it becomes an artifact the
    manifest does not claim -- and ``StoreLock`` leaves its file behind by
    design, because the kernel releases the lock, not the unlink.

    The consequence was ceremony-shaped rather than cosmetic: signing the
    manifest was the LAST step with a gate after it, and the file it left made
    G1.6 report ``UNTRACKED``/``UNCLAIMED`` on every boot from then on. Genesis
    halted at G1 with a bundle-drift finding whose actual cause was the signer,
    which reads as tampering rather than as litter.

    ``.intentops/`` is the node's own runtime directory and is gitignored, so
    the lock is where every other piece of runtime state lives and where no
    integrity check counts it.
    """
    return repo_root / ".intentops" / "genesis" / "imprint-manifest.lock"


def sign_manifest(repo_root: Path, private_key_path: Path,
                  passphrase: Optional[str], signer_key_id: str) -> str:
    """Sign the imprint manifest. Returns the hex signature.

    The payload comes from :func:`canonical_imprint_payload` -- imported, never
    reimplemented -- so the signer and the verifier can never drift apart.
    """
    serialization, _ = _crypto()
    manifest_path = repo_root / MANIFEST_RELPATH
    with StoreLock(_signing_lock_path(repo_root)):
        text = manifest_path.read_text(encoding="utf-8")   # read INSIDE the lock
        payload = provenance_mod.canonical_imprint_payload(text)
        private = serialization.load_der_private_key(
            private_key_path.read_bytes(),
            password=passphrase.encode("utf-8") if passphrase else None,
        )
        signature = private.sign(payload).hex()
        new_text = _with_signature(text, signer_key_id, signature)
        atomic_replace(manifest_path, new_text)
    return signature


def _with_signature(text: str, signer_key_id: str, signature: str) -> str:
    """Replace the top-level ``signatures: []`` KEY. HALTs if it is absent.

    Deliberately textual: the manifest's GENERATED block is byte-sensitive and
    a YAML round-trip would rewrite it, invalidating the very signature being
    added.

    The match is anchored to a WHOLE LINE at column zero, and is not the first
    occurrence of the substring. Corrected 2026-09-06: the shipped manifest
    explains its own empty list, in backticks, in a header comment on line 7 --
    so a substring replace rewrote the COMMENT and produced a manifest that
    would not parse, from the one step of the ceremony with no later gate to
    catch it. G1.7 then reported `unparseable`, which reads as a tampered
    bundle rather than as a signer that corrupted what it signed. Anchoring is
    not tidiness here.
    """
    pattern = re.compile(r"^signatures:[ \t]*\[[ \t]*\][ \t]*$", re.MULTILINE)
    found = pattern.findall(text)
    if not found:
        raise CeremonyRefused(
            "the manifest does not carry a top-level `signatures: []` key; "
            "refusing to guess where a signature belongs in an already-signed "
            "or edited file"
        )
    if len(found) > 1:
        raise CeremonyRefused(
            f"the manifest carries {len(found)} top-level `signatures: []` "
            "keys; refusing to choose one"
        )
    block = (f"signatures:\n  - key_id: {signer_key_id}\n"
             f"    algorithm: ed25519\n"
             f"    scope: \"{provenance_mod.IMPRINT_SIGNING_SCOPE}\"\n"
             f"    signature: \"{signature}\"")
    return pattern.sub(lambda _m: block, text, count=1)


# ---------------------------------------------------------------------------
# selftest -- a ceremony tool that has never round-tripped is a claim
# ---------------------------------------------------------------------------


def selftest() -> Tuple[bool, str]:
    """Mint, sign, and verify on throwaway keys in a temp dir."""
    import tempfile

    fired: List[str] = []
    failures: List[str] = []

    def expect(label: str, cond: bool) -> None:
        (fired if cond else failures).append(label)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # 1. a key inside the repository is refused
        try:
            refuse_repo_path(REPO_ROOT / "config" / "leaked.key")
        except CeremonyRefused:
            fired.append("key-inside-the-repo-is-refused")
        else:
            failures.append("key-inside-the-repo-is-refused")

        # 2. an unencrypted release root is refused
        try:
            mint(tmp / "bare.key", None)
        except CeremonyRefused:
            fired.append("unencrypted-release-root-is-refused")
        else:
            failures.append("unencrypted-release-root-is-refused")

        # 3. mint round-trips into agreeing representations
        record = mint(tmp / "root.key", "correct horse battery staple")
        expect("mint-writes-a-key", (tmp / "root.key").is_file())
        expect("mint-derives-a-real-fingerprint",
               trust_pin.is_real_fingerprint(record["fingerprint"]))
        expect("mint-never-returns-private-bytes",
               "private_key_pem" not in record and "der" not in record)

        # 4. sign -> the verifier PASSES over the same canonical payload
        repo = tmp / "clone"
        (repo / MANIFEST_RELPATH.parent).mkdir(parents=True)
        (repo / MANIFEST_RELPATH).write_text(
            "status: released\nsignatures: []\n"
            "# >>> BEGIN GENERATED artifacts\nartifacts:\n"
            "  - path: 'x'\n    sha256: '" + "a" * 64 + "'\n    bytes: 1\n"
            "# <<< END GENERATED artifacts\n", encoding="utf-8")
        sign_manifest(repo, tmp / "root.key", "correct horse battery staple",
                      record["key_id"])
        # The signer must not litter the hash-audited bundle: a lock file left
        # in genesis/imprint makes G1.6 report drift on every later boot, and
        # names the bundle rather than the signer as the cause.
        expect("signing-leaves-no-file-in-the-audited-bundle",
               sorted(p.name for p in (repo / MANIFEST_RELPATH).parent.iterdir())
               == [MANIFEST_RELPATH.name])
        doc = {"roots": [{"id": "R", "role": "release_root", "status": "active",
                          "key_id": record["key_id"],
                          "public_key_pem": record["public_key_pem"],
                          "signs": ["imprint_manifest"]}]}
        saved = trust_pin.PIN_STATE, trust_pin.ROOT_FINGERPRINT
        try:
            trust_pin.PIN_STATE = "minted"
            trust_pin.ROOT_FINGERPRINT = record["fingerprint"]
            check = provenance_mod._check_imprint_signature(repo, doc)
            expect("signed-manifest-verifies", check.outcome == "PASS")
            # 5. and a tampered payload does NOT
            tampered = (repo / MANIFEST_RELPATH).read_text(encoding="utf-8")
            (repo / MANIFEST_RELPATH).write_text(
                tampered.replace("bytes: 1", "bytes: 2"), encoding="utf-8")
            bad = provenance_mod._check_imprint_signature(repo, doc)
            expect("tampered-payload-is-a-contradiction",
                   bad.outcome == "HALT" and bad.contradiction is True)
        finally:
            trust_pin.PIN_STATE, trust_pin.ROOT_FINGERPRINT = saved

        # 6. signing a file with no `signatures: []` is refused, not guessed
        (repo / MANIFEST_RELPATH).write_text("status: x\n", encoding="utf-8")
        try:
            _with_signature("status: x\n", "K", "00")
        except CeremonyRefused:
            fired.append("missing-signatures-slot-is-refused")
        else:
            failures.append("missing-signatures-slot-is-refused")

        # 7. a COMMENT that mentions the marker is never mistaken for the key.
        # This defect had shipped: the real manifest explains its empty list in
        # backticks on line 7, and a substring replace rewrote that comment.
        commented = ("# `signatures: []` below is the honest state of an "
                     "unsigned artifact\nstatus: released\nsignatures: []\n")
        signed = _with_signature(commented, "K", "00")
        expect("a-comment-naming-the-marker-is-not-the-key",
               signed.splitlines()[0] == commented.splitlines()[0]
               and "  - key_id: K" in signed)

        # 8. two real slots is an ambiguity, refused rather than chosen
        try:
            _with_signature("signatures: []\nsignatures: []\n", "K", "00")
        except CeremonyRefused:
            fired.append("two-signature-slots-is-refused")
        else:
            failures.append("two-signature-slots-is-refused")

    report = (f"mint_release_root selftest: {len(fired)} paths fired, "
              f"{len(failures)} failed"
              + (" -- " + ", ".join(failures) if failures else ""))
    return (not failures, report)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("verb", nargs="?", choices=["mint", "sign"],
                        help="mint a release root, or sign the imprint manifest")
    parser.add_argument("--out", type=Path,
                        help="where to write the private key (OUTSIDE the repo)")
    parser.add_argument("--key", type=Path,
                        help="the minted private key, for `sign`")
    parser.add_argument("--key-id", help="the signing key's id, for `sign`")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        ok, report = selftest()
        print(report)
        return 0 if ok else 1
    if not args.verb:
        parser.print_help()
        return 2

    try:
        require_arming()
        if args.verb == "mint":
            if not args.out:
                raise CeremonyRefused("--out is required and must be outside the repo")
            passphrase = getpass.getpass("passphrase for the release root: ")
            if passphrase != getpass.getpass("again: "):
                raise CeremonyRefused("the two passphrases do not match")
            record = mint(args.out, passphrase)
            print("\nRelease root minted. PUBLIC material follows; the private "
                  "key is at the path below and is never printed.\n")
            for key in ("fingerprint", "did", "key_id", "authority_string",
                        "private_key_path"):
                print(f"  {key}: {record[key]}")
            print("\n  public_key_pem:\n" + record["public_key_pem"])
            print("Now apply ONE reviewed change across all three carriers:")
            for carrier in CARRIERS:
                print(f"  - {carrier}")
            print("\nThis tool does not edit them. See docs/TRUST-CEREMONY.md.")
        else:
            if not (args.key and args.key_id):
                raise CeremonyRefused("--key and --key-id are required for sign")
            passphrase = getpass.getpass("passphrase for the release root: ")
            sig = sign_manifest(REPO_ROOT, args.key, passphrase, args.key_id)
            print(f"imprint manifest signed by {args.key_id}: {sig[:16]}...")
    except CeremonyRefused as exc:
        print(f"CEREMONY REFUSED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
