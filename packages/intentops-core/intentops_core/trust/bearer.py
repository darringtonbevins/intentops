"""Bearer tokens: mint once, disclose once, store the digest and nothing else.

PURPOSE
    The generic half of every bearer credential this node holds. It mints the
    value, digests it, verifies a presentation against a stored digest, and
    writes the digest-only record. It is deliberately ignorant of WHICH
    surface the credential is for: the caller supplies the path and the
    schema, so two surfaces that must rotate independently get two records
    and one implementation.

    WHY IT LIVES IN THE CORE. Genesis mints the gateway's token during G2, and
    genesis is core. If the mint lived in the gateway package the core would
    have to import it, which inverts the dependency arrow the whole package
    split exists to protect -- and inverts it INVISIBLY if done by a lazy
    import inside a phase. So the primitive is here and the gateway consumes
    it, which is the arrow pointing the way it already points everywhere else.

    THE PLAINTEXT IS NEVER WRITTEN ANYWHERE. :func:`mint` returns it once, to
    its caller, and persists only the SHA-256 digest. A caller that writes the
    returned value to a file re-introduces exactly the exposure this design
    removes; the ceremony that consumes it prints it to an attended terminal
    and forgets it.

    DISCLOSURE IS RECORDED, NOT THE SECRET. ``disclosed_at`` says a human was
    shown this credential at a stated moment. A token minted with no
    disclosure is a credential nobody was shown, which looks armed and is not
    -- so the field exists in order that the difference be legible on disk,
    and never as a place to keep the value.

WRITE MODEL
    Locked fresh-read read-modify-write (store-write-discipline model 2):
    ``StoreLock`` is held across the write and the payload lands through
    ``atomic_replace``. One small whole-file record with one writer (a mint
    ceremony) and effectively no contention -- per-item files would be
    ceremony for a single row, and a journal would be over-built for a value
    whose entire history is "the current one".

    Rotation is a REPLACEMENT, not an append: the previous digest is gone, so
    every holder of the old token is locked out at the same instant. That is
    the intended blast radius, and the reason ``rotation`` is counted.

BLIND SPOTS
    * A bearer token is a shared secret over whatever transport carries it.
      Nothing here can see or verify that transport.
    * Digest-only storage means a lost token can only be rotated, never
      recovered. That is the intended trade.
    * :func:`matches` is constant-time over the digest. The surrounding
      request path is not audited for timing, so this resists a naive
      comparison oracle and not a determined side-channel attacker.
    * Nothing here expires anything. A token is valid until it is rotated,
      because an expiry nobody chose is a decision nobody made.
    * The record's ``disclosed_at`` is this node's own claim that a human was
      shown the value. It is evidence of a ceremony having run, not proof
      that anyone read the screen.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from intentops_core.store_guard import StoreLock, atomic_replace, lock_for

__all__ = [
    "GATEWAY_TOKEN_RELPATH",
    "GATEWAY_TOKEN_SCHEMA",
    "TOKEN_BYTES",
    "TokenUnavailable",
    "digest_of",
    "matches",
    "mint",
    "new_token",
    "read_record",
    "record_dict",
    "selftest",
]


class TokenUnavailable(RuntimeError):
    """The record is absent, malformed, or unreadable.

    Every one of those is a refusal of the whole surface, never a downgrade
    to anonymous. The message names which of the three it was, so an operator
    repairs the right thing instead of re-minting over a permissions problem.
    """


#: The gateway's bearer record, named HERE because genesis writes it and
#: genesis may not import the gateway. The gateway re-exports this constant
#: rather than declaring its own, so there is one path and not two that agree
#: until the day one of them is edited.
GATEWAY_TOKEN_RELPATH = Path(".intentops") / "trust" / "gateway-auth.json"
GATEWAY_TOKEN_SCHEMA = "gateway-auth/v1"

#: 32 bytes of OS entropy, URL-safe: long enough that guessing is not a threat
#: model, short enough to paste into a client configuration by hand.
TOKEN_BYTES = 32

#: Load-bearing. A reader that substitutes a default for any of these has left
#: the field out of the population: nothing errors, the record looks right,
#: and the one field nobody read is the one the whole check turns on.
_REQUIRED_FIELDS = ("schema", "algorithm", "token_sha256")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def digest_of(token: str) -> str:
    """SHA-256 of the presented token, hex. The only form ever stored."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token(*, nbytes: int = TOKEN_BYTES) -> str:
    """A fresh credential from OS entropy. Never seeded, never derived."""
    if nbytes < 16:
        raise ValueError(
            "a bearer token below 16 bytes of entropy is a password with "
            "extra steps; refusing rather than minting a weak credential")
    return secrets.token_urlsafe(nbytes)


def matches(presented: Optional[str], stored_digest: str) -> bool:
    """True iff ``presented`` digests to ``stored_digest``.

    An absent, blank, or non-string presentation is False -- never an implicit
    anonymous pass. The comparison is constant-time.
    """
    if not isinstance(presented, str) or not presented.strip():
        return False
    if not isinstance(stored_digest, str) or not stored_digest.strip():
        return False
    return hmac.compare_digest(digest_of(presented), stored_digest)


def record_dict(*, schema: str, token: str, node_root: str, rotation: int,
                minted_at: Optional[str] = None,
                disclosed_at: Optional[str] = None) -> Dict[str, Any]:
    """The canonical on-disk shape. One definition, so no two writers drift."""
    return {
        "schema": schema,
        "algorithm": "sha256",
        "token_sha256": digest_of(token),
        "minted_at": minted_at or _now(),
        "rotation": int(rotation),
        "node_root": node_root,
        # None, not "never": absence of a disclosure is a fact about this
        # record, and writing a word there would make it look answered.
        "disclosed_at": disclosed_at,
        "note": ("Digest only. The token itself was shown once at mint time "
                 "and is not recoverable from this file; rotate by "
                 "re-minting."),
    }


def mint(path: Path | str, *, schema: str, node_root: str, rotation: int,
         token: Optional[str] = None,
         disclosed_at: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
    """Mint a credential into ``path``. Returns ``(plaintext, record)``.

    ``token`` is an injection point for tests only; production callers let it
    generate. The caller shows the returned plaintext to its operator and then
    forgets it.
    """
    path = Path(path)
    value = token if token is not None else new_token()
    if not isinstance(value, str) or not value.strip():
        raise ValueError("a blank token is not a credential")
    record = record_dict(schema=schema, token=value, node_root=node_root,
                         rotation=rotation, disclosed_at=disclosed_at)
    path.parent.mkdir(parents=True, exist_ok=True)
    with StoreLock(lock_for(path)):
        atomic_replace(path, json.dumps(record, indent=2) + "\n")
    return value, record


def read_record(path: Path | str, *, schema: str) -> Dict[str, Any]:
    """Read and validate a bearer record. Raises rather than returning None.

    Every failure mode is NAMED -- absent, unreadable, malformed, wrong
    schema, wrong algorithm, missing load-bearing field -- because "cannot
    check" must never be reported as "checked".
    """
    path = Path(path)
    if not path.is_file():
        raise TokenUnavailable(
            f"no bearer record at {path}: nothing has ever minted one here")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TokenUnavailable(f"bearer record at {path} is unreadable: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TokenUnavailable(
            f"bearer record at {path} is malformed JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise TokenUnavailable(f"bearer record at {path} is not a JSON object")
    for name in _REQUIRED_FIELDS:
        if not isinstance(data.get(name), str) or not data[name].strip():
            raise TokenUnavailable(
                f"bearer record at {path} is missing the load-bearing field "
                f"{name!r}; refusing rather than assuming a value")
    if data["schema"] != schema:
        raise TokenUnavailable(
            f"bearer record at {path} declares schema {data['schema']!r}, "
            f"and this reader only understands {schema!r}")
    if data["algorithm"] != "sha256":
        raise TokenUnavailable(
            f"bearer record at {path} declares algorithm "
            f"{data['algorithm']!r}, which this reader cannot verify; "
            "refusing rather than guessing")
    return data


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


def selftest() -> Tuple[bool, str]:
    """Prove every refusal path can actually fire.

    A detector that has never fired is indistinguishable from a broken one,
    and a credential check that has never refused is the same claim.
    """
    import tempfile

    fired: list = []
    failed: list = []

    def expect(name: str, condition: bool) -> None:
        (fired if condition else failed).append(name)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        path = root / GATEWAY_TOKEN_RELPATH

        expect("absent-record-refuses", _raises(
            lambda: read_record(path, schema=GATEWAY_TOKEN_SCHEMA)))

        value, record = mint(path, schema=GATEWAY_TOKEN_SCHEMA,
                             node_root=str(root), rotation=1)
        expect("plaintext-is-not-on-disk",
               value not in path.read_text(encoding="utf-8"))
        expect("digest-verifies-the-token", matches(value, record["token_sha256"]))
        expect("a-near-miss-does-not-verify",
               not matches(value + "x", record["token_sha256"]))
        expect("absent-presentation-does-not-verify",
               not matches(None, record["token_sha256"]))
        expect("blank-presentation-does-not-verify",
               not matches("   ", record["token_sha256"]))
        expect("undisclosed-mint-records-none", record["disclosed_at"] is None)

        stamp = _now()
        value2, record2 = mint(path, schema=GATEWAY_TOKEN_SCHEMA,
                               node_root=str(root), rotation=2,
                               disclosed_at=stamp)
        expect("rotation-locks-out-the-old-token",
               not matches(value, record2["token_sha256"]))
        expect("rotation-admits-the-new-token", matches(value2, record2["token_sha256"]))
        expect("disclosure-is-recorded", record2["disclosed_at"] == stamp)

        path.write_text("{not json", encoding="utf-8")
        expect("malformed-record-refuses", _raises(
            lambda: read_record(path, schema=GATEWAY_TOKEN_SCHEMA)))

        path.write_text(json.dumps({"schema": GATEWAY_TOKEN_SCHEMA,
                                    "algorithm": "sha256"}), encoding="utf-8")
        expect("missing-load-bearing-field-halts", _raises(
            lambda: read_record(path, schema=GATEWAY_TOKEN_SCHEMA)))

        mint(path, schema="other/v1", node_root=str(root), rotation=1)
        expect("wrong-schema-refuses", _raises(
            lambda: read_record(path, schema=GATEWAY_TOKEN_SCHEMA)))

        expect("a-weak-token-is-refused", _raises(lambda: new_token(nbytes=8)))

    ok = not failed
    lines = [f"bearer selftest: {len(fired)} paths fired, {len(failed)} failed"]
    lines += [f"  fired  {name}" for name in fired]
    lines += [f"  FAILED {name}" for name in failed]
    return ok, "\n".join(lines)


def _raises(fn) -> bool:
    try:
        fn()
    except Exception:
        return True
    return False


def main(argv=None) -> int:  # pragma: no cover - thin CLI
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m intentops_core.trust.bearer",
        description="the bearer-token primitive: mint, digest, verify")
    parser.add_argument("--selftest", action="store_true",
                        help="prove every refusal path can actually fire")
    args = parser.parse_args(argv)
    if not args.selftest:
        parser.print_help()
        return 1
    ok, report = selftest()
    print(report)
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
