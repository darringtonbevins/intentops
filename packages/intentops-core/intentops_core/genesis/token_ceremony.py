"""The gateway token, minted at genesis and DISCLOSED, or not minted at all.

PURPOSE
    Close the one gap between "a node is born with a gateway" and "a node is
    born with a gateway anyone can reach": the credential. Until 2026-09-06
    the only mint was ``intentops-gateway token rotate --yes``, run out of
    band, some time after birth -- so a node came online with an
    authenticating surface and no authentication, and the operator's first
    real act was a step nobody had told them about.

    The fix is NOT to mint one quietly at birth. A token minted silently is a
    credential nobody was shown, which looks armed and is not; it is worse
    than none, because the gateway's refusal at least says what to do. The fix
    is a CEREMONY: an attended terminal, a question asked out loud, the
    plaintext shown ONCE with a banner, and a record on disk that says a human
    was shown it and when.

    THREE OUTCOMES, ALL RECORDED. Minted-and-disclosed, not minted because the
    operator declined, and not minted because nothing could disclose it (a dry
    run, or a terminal nobody is sitting at). All three land in the phase
    result and on the birth certificate, because "no token" and "a token
    nobody saw" must never look the same from the outside.

    NEVER FROM A NON-TTY. A ceremony that a scheduled task can complete is not
    a ceremony. This module refuses to mint when it cannot disclose, and the
    refusal is recorded rather than raised: an optional credential must never
    be able to strand a genesis run that would otherwise succeed. The gateway
    already fails closed without a token, so declining costs an out-of-band
    rotation and nothing else.

    THAT CONTRACT BINDS THE CALLER TOO, and it did not always hold. ``ask`` is
    supplied by the genesis machine, whose gate helper raises a HALT when the
    operator cannot be asked. This module catches ``EOFError`` and
    ``KeyboardInterrupt``; it cannot catch a caller's own exception class
    without importing it, so a caller that raises anything else turns an
    optional credential back into a halt -- which is what happened until
    2026-09-06, invisibly, because the passphrase gate on the same stdin
    reached EOF one step earlier. A caller whose prompt cannot be answered must
    surface that as ``EOFError``; the machine's ``_ask_about_the_token``
    wrapper is where that translation lives, and it keeps "declined" and "could
    not be asked" as two different recorded phrases rather than one.

    THE ARROW. The mint primitive is :mod:`intentops_core.trust.bearer`, in
    the core, so genesis never imports the gateway. The gateway package
    consumes the same primitive and the same path constant, so there is one
    record and not two that agree until one is edited.

WRITE MODEL
    Delegated. The record is written by ``intentops_core.trust.bearer.mint``
    (locked whole-file replace, ``StoreLock`` + ``atomic_replace``). Nothing
    here writes anything else, and the plaintext is written nowhere at all.

BLIND SPOTS
    * ``disclosed_at`` records that this node PRINTED the value to an attended
      terminal at a stated moment. It cannot know that anyone read the screen,
      and it cannot know where a redirected stdout sent the banner -- stdin
      being a terminal does not make stdout one. An operator who pipes the run
      to a file and looks away has a disclosed token they never saw, and only
      a rotation fixes that.
    * A declined mint leaves no trace outside the phase result and the
      certificate. There is no journal of the asking, because the journal of
      the asking would be the genesis journal, which already records G2.
    * Nothing here checks the terminal is private. Shoulder-surfing, screen
      recording and scrollback are outside what any code at this layer sees.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from intentops_core.trust import bearer

__all__ = [
    "TokenDisclosure",
    "certificate_line_for",
    "mint_disclosed",
    "record_path",
    "selftest",
]

#: What the operator is asked. One question, defaulting to NO: a ceremony that
#: proceeds when the operator says nothing is a ceremony that assumed consent.
PROMPT = ("Mint this node's gateway bearer token now and show it to you once? "
          "The value is printed to THIS terminal and stored only as a digest; "
          "if you decline, the gateway refuses every request until you run "
          "`intentops-gateway token rotate --yes`. [y/N]")

_BANNER_TOP = "=" * 72
_ACCEPTED = ("y", "yes")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class TokenDisclosure:
    """What happened at the ceremony. Never the credential."""

    minted: bool
    reason: str
    disclosed_at: Optional[str] = None
    rotation: Optional[int] = None

    def certificate_line(self) -> str:
        """The line the birth certificate carries, verbatim."""
        if self.minted and self.disclosed_at:
            return f"gateway token: minted/disclosed at {self.disclosed_at}"
        return f"gateway token: not minted ({self.reason})"

    def to_evidence(self) -> Dict[str, Any]:
        return {"minted": self.minted, "reason": self.reason,
                "disclosed_at": self.disclosed_at, "rotation": self.rotation}


def record_path(node_root: Path | str) -> Path:
    return Path(node_root) / bearer.GATEWAY_TOKEN_RELPATH


def _render_banner(value: str, node_root: Path, rotation: int) -> str:
    return "\n".join([
        "",
        _BANNER_TOP,
        "  GATEWAY BEARER TOKEN -- SHOWN ONCE, STORED ONLY AS A DIGEST",
        _BANNER_TOP,
        f"  node     : {node_root}",
        f"  rotation : {rotation}",
        "",
        f"  {value}",
        "",
        "  Copy it now. This node cannot show it to you again; it kept the",
        "  digest and nothing else. If you lose it, rotate:",
        "      intentops-gateway token rotate --yes",
        "  Anyone holding this value can reach this node's gateway, so treat",
        "  it as you would a password and do not paste it into a chat.",
        _BANNER_TOP,
        "",
    ])


def mint_disclosed(
    node_root: Path | str,
    *,
    dry_run: bool = False,
    attended: bool = False,
    ask: Optional[Callable[[str], str]] = None,
    out: Callable[[str], None] = print,
    token: Optional[str] = None,
) -> TokenDisclosure:
    """Run the ceremony. Mints ONLY when it can disclose, and says so either way.

    ``ask`` is the operator prompt (returns the typed answer); ``token`` is a
    test injection point. Neither has a production default that could answer
    for a human: with no ``ask``, nothing is minted.
    """
    node_root = Path(node_root)

    if dry_run:
        # A dry run mints nothing and shows nothing, so it may not record a
        # disclosure. This is the phrase the certificate carries.
        return TokenDisclosure(False, "dry run")
    if not attended:
        return TokenDisclosure(
            False,
            "no attended terminal: a credential nobody can be shown is not "
            "minted here")
    if ask is None:
        return TokenDisclosure(
            False, "no operator prompt was available to ask the question")

    try:
        answer = ask(PROMPT)
    except (EOFError, KeyboardInterrupt):
        # The same refusal, not a crash. Reaching EOF at a consent prompt is
        # the operator not answering, and an unanswered question is a NO.
        return TokenDisclosure(False, "the operator gate ended before an answer arrived")

    if str(answer).strip().lower() not in _ACCEPTED:
        return TokenDisclosure(False, "the operator declined at the genesis ceremony")

    path = record_path(node_root)
    rotation = 1
    try:
        previous = bearer.read_record(path, schema=bearer.GATEWAY_TOKEN_SCHEMA)
        rotation = int(previous.get("rotation") or 0) + 1
    except bearer.TokenUnavailable:
        rotation = 1

    disclosed_at = _now()
    value, record = bearer.mint(
        path,
        schema=bearer.GATEWAY_TOKEN_SCHEMA,
        node_root=str(node_root),
        rotation=rotation,
        token=token,
        disclosed_at=disclosed_at,
    )
    # Printed exactly once, here, and never returned to a caller that might
    # journal it. The plaintext leaves this function only through the screen.
    out(_render_banner(value, node_root, int(record["rotation"])))
    del value
    return TokenDisclosure(True, "minted and disclosed at the genesis ceremony",
                           disclosed_at=disclosed_at,
                           rotation=int(record["rotation"]))


def certificate_line_for(node_root: Path | str) -> str:
    """Read the record and say, in one line, what this node's token state is.

    Three states, three sentences, because a token rotated out of band is not
    the same fact as one disclosed at birth and neither is the same as none.
    """
    path = record_path(node_root)
    try:
        record = bearer.read_record(path, schema=bearer.GATEWAY_TOKEN_SCHEMA)
    except bearer.TokenUnavailable:
        return "gateway token: not minted"
    disclosed = record.get("disclosed_at")
    if isinstance(disclosed, str) and disclosed.strip():
        return f"gateway token: minted/disclosed at {disclosed}"
    minted_at = str(record.get("minted_at") or "an unrecorded time")
    return (f"gateway token: minted at {minted_at}, not disclosed at genesis "
            "(rotated out of band)")


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


def selftest() -> Tuple[bool, str]:
    """Prove the ceremony can refuse, and that the refusals are distinguishable."""
    import tempfile

    fired: list = []
    failed: list = []

    def expect(name: str, condition: bool) -> None:
        (fired if condition else failed).append(name)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        shown: list = []

        dry = mint_disclosed(root, dry_run=True, attended=True,
                             ask=lambda _p: "y", out=shown.append)
        expect("dry-run-mints-nothing",
               not dry.minted and not record_path(root).exists())
        expect("dry-run-says-so-on-the-certificate",
               dry.certificate_line() == "gateway token: not minted (dry run)")

        unattended = mint_disclosed(root, attended=False, ask=lambda _p: "y",
                                    out=shown.append)
        expect("non-tty-refuses",
               not unattended.minted and not record_path(root).exists())

        declined = mint_disclosed(root, attended=True, ask=lambda _p: "",
                                  out=shown.append)
        expect("silence-is-a-no",
               not declined.minted and not record_path(root).exists())

        expect("nothing-was-shown-by-any-refusal", not shown)

        planted = "selftest-token-not-a-real-credential"
        minted = mint_disclosed(root, attended=True, ask=lambda _p: "y",
                                out=shown.append, token=planted)
        expect("consent-mints", minted.minted and record_path(root).exists())
        expect("the-token-was-shown-exactly-once", len(shown) == 1)
        expect("the-banner-warns", "SHOWN ONCE" in (shown[0] if shown else ""))
        expect("the-banner-carries-the-value", planted in (shown[0] if shown else ""))
        expect("the-plaintext-is-not-on-disk",
               planted not in record_path(root).read_text(encoding="utf-8"))
        expect("certificate-records-the-disclosure",
               minted.certificate_line().startswith(
                   "gateway token: minted/disclosed at "))
        expect("the-reader-agrees-with-the-ceremony",
               certificate_line_for(root) == minted.certificate_line())

    ok = not failed
    lines = [f"token-ceremony selftest: {len(fired)} paths fired, "
             f"{len(failed)} failed"]
    lines += [f"  fired  {name}" for name in fired]
    lines += [f"  FAILED {name}" for name in failed]
    return ok, "\n".join(lines)


def main(argv=None) -> int:  # pragma: no cover - thin CLI
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m intentops_core.genesis.token_ceremony",
        description="the disclosed gateway-token mint performed at genesis G2")
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
