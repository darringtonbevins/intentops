"""``intentops ceremony`` -- the attended, resumable release-root wizard.

PURPOSE
    ``docs/TRUST-CEREMONY.md`` is correct and it is a lot to hold in a head at
    once: ten steps, three carriers that must move together, two keys, one
    signature, and an acceptance test that fails silently if any of it is done
    in the wrong order. The operator said so plainly -- *"I certainly am not
    going to remember all of that, and if something goes wrong, I'll need your
    help and remediation."* A runbook nobody can execute under pressure is a
    control that exists on paper.

    So this is the walk-through. It states, at every screen, what it is about
    to do, why the runbook says so, and what it will NOT do. Every step writes
    a journal entry, so a failure at step seven does not cost steps one to six
    and does not re-mint a key that already exists. Every failure prints a
    NUMBERED remedy from ``docs/CEREMONY-REMEDIATION.md`` and exits non-zero.

    It reuses the primitives rather than forking them:
    ``scripts/genesis/mint_release_root.py`` mints and signs,
    ``scripts/genesis/build_manifest.py`` checks the bundle, and
    ``intentops_core.genesis.provenance`` verifies. Two definitions of a
    canonical payload is how a signature that verifies on the signer's machine
    fails on every other one; the same is true of a ceremony.

    ASSURANCE LEVELS -- recorded, never faked. The runbook's preconditions
    (offline, witnessed, clean host) are properties of the ROOM. No program can
    observe them, and a tool that ticked them off would be manufacturing
    evidence. So the wizard offers two levels, records which was chosen, and
    writes every precondition answer as an ATTRIBUTED statement: what the
    operator stated, beside what the tool could actually observe.

      ``standard``  attended, on the operator's own machine, key encrypted on a
                    medium the operator names. No witness required.
      ``full``      the runbook's ceremony: offline machine, named witness,
                    clean host.

WRITE MODEL
    Journal: locked fresh-read RMW (``StoreLock`` + ``atomic_replace``) on TWO
    copies -- ``<medium>/ceremony-state.json`` beside the key, and a pointer
    copy at ``.intentops/ceremony/ceremony-state.json`` so ``--resume`` can
    find the medium without being told twice. NEITHER copy contains a
    passphrase, a private key, or any derivative of one; only public fields and
    the operator's stated facts.

    Carriers: the three-carrier edit is one atomic write per file via
    ``atomic_replace``, and only after the operator types the exact phrase. The
    wizard STAGES with ``git add`` and never commits: review is a human's job
    and a tool that could commit the pin unattended is the coup the three
    carriers exist to make visible.

BLIND SPOTS
    - It cannot make a sitting offline, witnessed, or clean. It observes what
      stdlib can observe (a default route, the interface count, whether a CI
      variable is set) and attributes everything else to the operator, by name.
    - It proves nothing about custody of the key after it is written. A key on
      a networked laptop and a key on an air-gapped machine are identical bytes.
    - ``--selftest`` exercises the carrier editors and the refusals on throwaway
      material in a temporary directory. A passing selftest says the surgery
      round-trips; it says nothing about whether the real ceremony was run well.
    - The clean-tree check is a WARN, never a refusal: a dirty tree is common
      and legitimate, and a wizard that refused on it would be routed around.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import getpass
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..store_guard import StoreLock, atomic_replace, lock_for

__all__ = [
    "CEREMONY_STATE_RELPATH",
    "ASSURANCE_LEVELS",
    "STEPS",
    "REMEDIES",
    "CeremonyRefused",
    "Console",
    "ScriptedConsole",
    "Wizard",
    "run",
    "selftest",
]

#: The journal's pointer copy inside the clone. The medium holds the copy that
#: matters; this one exists so ``--resume`` can find the medium.
CEREMONY_STATE_RELPATH = Path(".intentops") / "ceremony" / "ceremony-state.json"

#: The journal's own schema. A reader that half-understands a state file
#: produces a resume that runs and is wrong.
STATE_SCHEMA = "ceremony-state/v1"

#: The phrase that gates the three-carrier edit. Deliberately not the mint
#: tool's arming phrase: these are two different consents.
CARRIER_PHRASE = "apply the three-carrier edit"

#: The ten screens, in order. Each is journalled; the first six of the last
#: seven are skipped on resume when already complete.
STEPS: Tuple[str, ...] = (
    "preflight",
    "medium",
    "passphrase",
    "mint-root",
    "mint-intermediate",
    "sign-imprint",
    "three-carrier-edit",
    "verify",
    "record",
    "summary",
)

#: Steps that are RE-RUN on every invocation rather than skipped when complete.
#: The passphrase is the load-bearing one: it is never stored, so a resumed run
#: must ask for it again and prove it against the key already on the medium.
ALWAYS_RERUN = frozenset({"preflight", "medium", "passphrase", "summary"})

ROOT_ID = "R-INTENTOPS"
INTERMEDIATE_ID = "I-INTENTOPS-REL-2026"

ROOTS_RELPATH = Path("config") / "trust-roots.yaml"
PIN_RELPATH = (Path("packages") / "intentops-core" / "intentops_core"
               / "genesis" / "trust_pin.py")
GENESIS_DOC_RELPATH = Path("docs") / "GENESIS.md"
MINT_TOOL_RELPATH = Path("scripts") / "genesis" / "mint_release_root.py"
BUILD_MANIFEST_RELPATH = Path("scripts") / "genesis" / "build_manifest.py"

#: The markers around carrier #3 in docs/GENESIS.md. A marker pair, rather than
#: a regex over prose, because the one thing this edit must never do is guess
#: which line in a 226-line document is the fingerprint.
DOC_BEGIN = "<!-- BEGIN release-root-fingerprint -->"
DOC_END = "<!-- END release-root-fingerprint -->"

MIN_PASSPHRASE = 12

ASSURANCE_LEVELS: Dict[str, Dict[str, Any]] = {
    "standard": {
        "label": "standard",
        "summary": ("attended, on the operator's own machine, private key "
                    "encrypted on a medium the operator names"),
        "witness_required": False,
    },
    "full": {
        "label": "full",
        "summary": ("the runbook ceremony: offline machine, a named witness "
                    "present, a clean host"),
        "witness_required": True,
    },
}


class CeremonyRefused(RuntimeError):
    """The ceremony did not proceed, and says why, with a numbered remedy.

    Never a silent return and never a bare exception: every raise carries a
    remedy id, so the operator gets a row out of
    ``docs/CEREMONY-REMEDIATION.md`` rather than a traceback.
    """

    def __init__(self, remedy_id: str, detail: str = "") -> None:
        self.remedy_id = remedy_id
        self.detail = detail
        row = REMEDIES.get(remedy_id)
        self.code = row["code"] if row else 1
        super().__init__(detail or (row["what"] if row else remedy_id))


# ---------------------------------------------------------------------------
# the remedy table -- one definition, printed on failure and rendered to docs
# ---------------------------------------------------------------------------

#: Every failure this wizard can raise. ``code`` is the process exit code,
#: ``what`` says what happened in the operator's terms, ``remedy`` is what to
#: do, and ``paste`` names exactly what is safe to hand an assistant when
#: asking for help. ``paste`` is PUBLIC FIELDS ONLY, per row, because "paste
#: the error" is how a passphrase ends up in a chat log.
REMEDIES: Dict[str, Dict[str, Any]] = {
    "not-attended": {
        "code": 10,
        "what": "stdin is not a terminal, so a human may not be present",
        "remedy": ("Run the wizard yourself in a real terminal window. It is "
                   "never run from a loop tick, a workflow leg, a subagent, a "
                   "container entrypoint, or a scheduled task -- if a process "
                   "can trigger the ceremony, the key belongs to the process."),
        "paste": "the exit code and this remedy id",
    },
    "ci-set": {
        "code": 11,
        "what": "the CI environment variable is set",
        "remedy": ("Unset CI, or move to a machine that is not a build agent. "
                   "Consent from an automated job is not consent."),
        "paste": "the exit code and this remedy id",
    },
    "refused": {
        "code": 12,
        "what": "stdin ended, or the operator declined, at a prompt",
        "remedy": ("Nothing was lost. Re-run with --resume to continue at the "
                   "step that was open."),
        "paste": "the exit code, the remedy id, and the step name",
    },
    "python-too-old": {
        "code": 20,
        "what": "this interpreter is older than Python 3.11",
        "remedy": ("Run the ceremony under Python 3.11 or newer; the project "
                   "declares that floor in pyproject.toml."),
        "paste": "the exit code and the version line the wizard printed",
    },
    "crypto-unavailable": {
        "code": 21,
        "what": "the Ed25519 implementation is not importable",
        "remedy": ("pip install cryptography, then re-run. A ceremony that "
                   "cannot do the maths halts; it never degrades."),
        "paste": "the exit code and the remedy id",
    },
    "selftest-failed": {
        "code": 22,
        "what": "mint_release_root.py --selftest did not pass",
        "remedy": ("Do not proceed. Re-run "
                   "`python scripts/genesis/mint_release_root.py --selftest` "
                   "and read its report: it names which round-trip failed. A "
                   "ceremony run on tooling that cannot round-trip produces a "
                   "key nobody can verify."),
        "paste": "the whole selftest report line (it carries no key material)",
    },
    "manifest-drift": {
        "code": 23,
        "what": "build_manifest.py --check reports drift in the imprint bundle",
        "remedy": ("Resolve the drift BEFORE signing. A signature over a stale "
                   "hash block is worse than no signature. Run "
                   "`python scripts/genesis/build_manifest.py --check` to see "
                   "the findings; if the bundle legitimately changed, rebuild "
                   "with --write, review the diff, and start the ceremony over."),
        "paste": "the DRIFT findings (paths and hashes only)",
    },
    "medium-inside-repo": {
        "code": 30,
        "what": "the chosen output path is inside a git repository",
        "remedy": ("Choose a path outside every repository -- a removable "
                   "volume, or a directory under your home that git does not "
                   "track. A private key never enters a repository, public or "
                   "private."),
        "paste": "the exit code and the remedy id, NOT the path",
    },
    "medium-unwritable": {
        "code": 31,
        "what": "the output path could not be created, or a test write failed",
        "remedy": ("Check the medium is mounted, unlocked, and writable by "
                   "you, then re-run with --resume. The wizard writes a "
                   "throwaway probe file first precisely so this is found "
                   "before a key is minted, not after."),
        "paste": "the exit code and the OS error text",
    },
    "passphrase-weak": {
        "code": 40,
        "what": f"the passphrase is shorter than {MIN_PASSPHRASE} characters",
        "remedy": ("Use the passphrase you decided on before the sitting. The "
                   "runbook is explicit that it is chosen beforehand, not "
                   "composed at the prompt."),
        "paste": "the exit code and the remedy id ONLY -- never the passphrase",
    },
    "passphrase-mismatch": {
        "code": 41,
        "what": "the two passphrase entries did not match",
        "remedy": ("Re-run with --resume and type it again. The wizard allows "
                   "a limited number of attempts and then refuses, rather than "
                   "looping, because an unbounded prompt is a place to guess."),
        "paste": "the exit code and the remedy id ONLY -- never the passphrase",
    },
    "passphrase-wrong": {
        "code": 42,
        "what": ("the passphrase does not open the key already on the medium "
                 "(resume only)"),
        "remedy": ("This is the passphrase you set when the root was minted "
                   "earlier in this ceremony. If it is lost, the minted key is "
                   "unusable: delete the medium's key files and start a fresh "
                   "ceremony. Nothing in the repository has been changed yet."),
        "paste": "the exit code and the remedy id ONLY",
    },
    "mint-failed": {
        "code": 50,
        "what": "minting a key was refused by the primitive",
        "remedy": ("The refusal text names the cause -- most often an output "
                   "path inside a repository, or a missing passphrase. Fix it "
                   "and re-run with --resume; no partial key is left behind."),
        "paste": "the CEREMONY REFUSED line (it carries no key material)",
    },
    "sign-failed": {
        "code": 60,
        "what": "signing the imprint manifest was refused",
        "remedy": ("The commonest cause is that the manifest no longer carries "
                   "`signatures: []` -- it is already signed, or was edited. "
                   "Check `git diff genesis/imprint/IMPRINT-MANIFEST.yaml`. The "
                   "wizard refuses to guess where a signature belongs."),
        "paste": "the CEREMONY REFUSED line",
    },
    "carrier-shape": {
        "code": 70,
        "what": ("a carrier does not carry the placeholder text the edit "
                 "replaces"),
        "remedy": ("One of the three carriers has been edited by hand, or is "
                   "already minted. Do NOT make one carrier agree with another "
                   "-- that is exactly the tamper G1.3 and G1.4 exist to catch. "
                   "Restore the carriers from a clean checkout and start over."),
        "paste": "the exit code, the remedy id, and the carrier path",
    },
    "carrier-not-confirmed": {
        "code": 71,
        "what": "the operator did not type the three-carrier phrase",
        "remedy": ("Nothing was written. Review the diff the wizard printed. "
                   "When you are satisfied, re-run with --resume and type the "
                   f"phrase exactly: {CARRIER_PHRASE}"),
        "paste": "the exit code and the remedy id",
    },
    "verify-g17": {
        "code": 80,
        "what": "G1.7-imprint-signature did not read PASS against the edited tree",
        "remedy": ("The acceptance test for the whole ceremony failed. Read "
                   "the check's reason line: a key_id mismatch means the "
                   "manifest was signed by a key the trust-roots file does not "
                   "declare; a 'does NOT verify' means the manifest changed "
                   "after signing. Re-run "
                   "`python scripts/genesis/build_manifest.py --check` first."),
        "paste": ("the G1.7 check object from the JSON the wizard printed "
                  "(id, outcome, reason, evidence -- all public)"),
    },
    "verify-genesis": {
        "code": 81,
        "what": "a real `genesis --dry-run` did not reach G7 without the dev flag",
        "remedy": ("G1 now passes but a later phase halted. The wizard prints "
                   "the phase lines; the failing one names its own remedy. This "
                   "does not invalidate the ceremony -- the keys and the "
                   "signature stand -- but do not announce the release until it "
                   "is green."),
        "paste": "the phase lines the wizard printed",
    },
    "record-failed": {
        "code": 90,
        "what": "the ceremony record could not be written to the medium",
        "remedy": ("The keys and the carrier edit stand; only the record is "
                   "missing. Fix the medium and re-run with --resume, or copy "
                   "docs/CEREMONY-RECORD.template.md and fill it by hand from "
                   "the public fields the wizard printed."),
        "paste": "the exit code and the OS error text",
    },
    "state-unreadable": {
        "code": 91,
        "what": "--resume was asked for and no readable ceremony state was found",
        "remedy": ("Run without --resume to start a fresh ceremony, or pass "
                   "--out <medium> so the wizard can find the journal beside "
                   "the key. A state file that half-parses is never guessed at."),
        "paste": "the exit code and the remedy id",
    },
}


def remedy_table_markdown() -> str:
    """The remedy table as markdown. ONE definition; the doc renders from it."""
    lines = [
        "| id | exit | What happened | Remedy | Safe to paste when asking for help |",
        "|---|---:|---|---|---|",
    ]
    for rid in sorted(REMEDIES, key=lambda r: REMEDIES[r]["code"]):
        row = REMEDIES[rid]
        lines.append(
            f"| `{rid}` | {row['code']} | {row['what']} | "
            f"{row['remedy']} | {row['paste']} |"
        )
    return "\n".join(lines)


def render_remedy(remedy_id: str, detail: str = "") -> str:
    """The one row an operator needs, printed at the moment of failure."""
    row = REMEDIES.get(remedy_id)
    if row is None:  # pragma: no cover - every raise names a declared id
        return f"CEREMONY FAILED [{remedy_id}]: {detail}"
    out = [
        "",
        "-" * 72,
        f"CEREMONY STOPPED  [{remedy_id}]  exit {row['code']}",
        f"  what happened : {row['what']}",
    ]
    if detail:
        out.append(f"  detail       : {detail}")
    out += [
        f"  remedy       : {row['remedy']}",
        f"  safe to paste: {row['paste']}",
        "  full table   : docs/CEREMONY-REMEDIATION.md",
        "  nothing already completed was lost; re-run with --resume.",
        "-" * 72,
    ]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# the console -- attendance is a property of this object, and it is testable
# ---------------------------------------------------------------------------


def _stdin_is_the_null_device() -> bool:
    """True when stdin is Windows ``NUL`` dressed as a terminal.

    ``NUL`` is a character device, so ``isatty()`` and ``GetFileType`` both
    report it the same way a console is reported. ``GetConsoleMode`` is the one
    call that tells them apart: it succeeds on a console handle and fails on
    ``NUL``.

    Returns False on every non-Windows platform and on any error, because this
    is an EXTRA refusal layered on top of the TTY and CI checks -- see
    :meth:`Console.attendance` for why it fails open.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes  # noqa: PLC0415
        import msvcrt  # noqa: PLC0415

        handle = msvcrt.get_osfhandle(sys.stdin.fileno())
        mode = ctypes.c_uint32()
        ok = ctypes.windll.kernel32.GetConsoleMode(  # type: ignore[attr-defined]
            ctypes.c_void_p(handle), ctypes.byref(mode))
        return not bool(ok)
    except Exception:  # noqa: BLE001 - any probe failure means "cannot tell"
        return False


class Console:
    """Plain-text, colour-free, ASCII-only terminal IO.

    Attendance lives here so a test can drive the wizard without weakening the
    real check: :class:`ScriptedConsole` declares its own answer to
    :meth:`attendance`, and every refusal path is therefore reachable in a test
    without ever making a non-TTY run succeed.
    """

    def __init__(self, out: Any = None, env: Optional[Dict[str, str]] = None):
        self._out = out if out is not None else sys.stdout
        self.env = dict(os.environ if env is None else env)

    # -- attendance ---------------------------------------------------------

    def attendance(self) -> Tuple[bool, str]:
        """(attended, remedy_id_if_not).

        Three checks, in this order, because they catch different things.

        ``CI`` first and separately: a gate a job can reach is a gate a job can
        answer, and a build agent's stdin can be anything.

        Then ``isatty()``, which is the ordinary case.

        Then, on Windows, whether stdin is actually a CONSOLE. This is the
        clause the genesis machine documents and cannot enforce: a stdin
        redirected from the ``NUL`` device reports ``isatty()`` True, so a
        scheduled task, a service, or a shell redirect walks straight past a
        TTY-only check. Measured on this platform 2026-09-06 -- running the
        ceremony with stdin from ``/dev/null`` reached the assurance-level
        prompt before EOF stopped it, which means the refusal that makes this
        verb attended-only was not the thing doing the refusing.
        ``GetConsoleMode`` succeeds on a real console handle and fails on
        ``NUL``, which is exactly the distinction wanted.

        The console probe FAILS OPEN on its own error, deliberately: it is an
        additional refusal on top of the two above, and a probe that cannot run
        must not lock a legitimate operator out of their own ceremony. EOF at
        any prompt remains a refusal regardless, so the floor never drops.
        """
        if self.env.get("CI"):
            return False, "ci-set"
        try:
            if sys.stdin is None or sys.stdin.closed or not sys.stdin.isatty():
                return False, "not-attended"
        except (AttributeError, ValueError):  # pragma: no cover - exotic stdin
            return False, "not-attended"
        if _stdin_is_the_null_device():
            return False, "not-attended"
        return True, ""

    # -- output -------------------------------------------------------------

    def say(self, text: str = "") -> None:
        print(text, file=self._out)

    def screen(self, number: int, total: int, title: str, *,
               about: str, why: str, will_not: str) -> None:
        """Every screen states what, why, and what it will NOT do."""
        self.say()
        self.say("=" * 72)
        self.say(f"STEP {number} of {total}  --  {title}")
        self.say("=" * 72)
        self.say(f"  about    : {about}")
        self.say(f"  why      : {why}")
        self.say(f"  will NOT : {will_not}")
        self.say()

    # -- input --------------------------------------------------------------

    def ask(self, prompt: str, *, default: Optional[str] = None) -> str:
        suffix = f" [{default}]" if default else ""
        try:
            answer = input(f"{prompt}{suffix}\n> ").strip()
        except (EOFError, KeyboardInterrupt) as exc:
            raise CeremonyRefused("refused", "stdin ended at a prompt") from exc
        return answer or (default or "")

    def secret(self, prompt: str) -> str:
        try:
            return getpass.getpass(f"{prompt}: ")
        except (EOFError, KeyboardInterrupt) as exc:
            raise CeremonyRefused(
                "refused", "stdin ended at the passphrase prompt") from exc


class ScriptedConsole(Console):
    """A console driven by a queue of answers, for tests and ``--selftest``.

    It exists so every refusal path is reachable without a terminal. It does
    NOT make the wizard runnable unattended: ``attendance()`` returns whatever
    the test declares, and the production entrypoint builds a real
    :class:`Console` whose attendance is measured, never declared.
    """

    def __init__(self, answers: Sequence[str], secrets: Sequence[str] = (),
                 *, is_tty: bool = True, env: Optional[Dict[str, str]] = None,
                 out: Any = None):
        super().__init__(out=out, env=env or {})
        self.answers: List[str] = list(answers)
        self.secrets: List[str] = list(secrets)
        self.is_tty = is_tty
        self.transcript: List[str] = []

    def attendance(self) -> Tuple[bool, str]:
        if self.env.get("CI"):
            return False, "ci-set"
        if not self.is_tty:
            return False, "not-attended"
        return True, ""

    def say(self, text: str = "") -> None:
        self.transcript.append(text)
        if self._out is not None and self._out is not sys.stdout:
            print(text, file=self._out)

    def ask(self, prompt: str, *, default: Optional[str] = None) -> str:
        self.transcript.append(prompt)
        if not self.answers:
            raise CeremonyRefused("refused", "the scripted answers ran out")
        answer = self.answers.pop(0)
        return answer if answer != "" else (default or "")

    def secret(self, prompt: str) -> str:
        self.transcript.append(prompt)
        if not self.secrets:
            raise CeremonyRefused("refused", "the scripted secrets ran out")
        return self.secrets.pop(0)


# ---------------------------------------------------------------------------
# carrier surgery -- pure functions, so every one of them is unit-testable
# ---------------------------------------------------------------------------


def _pem_block(pem: str, indent: int = 4) -> str:
    """A PEM as a YAML literal block scalar at ``indent``.

    A block scalar rather than a quoted one-liner because a PEM is multi-line
    and ``\\n``-escaping it into a double-quoted scalar is a shape people get
    wrong once and then cannot see.
    """
    pad = " " * (indent + 2)
    body = "\n".join(pad + line for line in pem.strip().splitlines())
    return " " * indent + "public_key_pem: |\n" + body


def _ceremony_block(facts: Dict[str, Any], indent: int = 4) -> str:
    """The ``ceremony:`` mapping, carrying the recorded facts verbatim.

    Every statement the tool could not observe is written as an attributed
    statement -- ``operator states ...`` -- beside whatever the tool DID
    observe. That asymmetry is the whole design: a ceremony record that reads
    "offline: yes" is a claim wearing a measurement's clothes.
    """
    pad = " " * (indent + 2)
    lines = [" " * indent + "ceremony:"]
    for key in ("minted_at", "witnessed_by", "record", "assurance_level",
                "operator", "location", "machine", "network", "storage_medium",
                "passphrase_custody", "tool_commit", "tool_selftest",
                "observed"):
        if key not in facts:
            continue
        value = facts[key]
        if value is None:
            lines.append(f"{pad}{key}: null")
        elif isinstance(value, (dict, list)):
            lines.append(f"{pad}{key}: {json.dumps(value, sort_keys=True)}")
        else:
            lines.append(f"{pad}{key}: {json.dumps(str(value))}")
    return "\n".join(lines)


def edit_trust_roots(text: str, *, root: Dict[str, Any],
                     intermediate: Dict[str, Any],
                     root_facts: Dict[str, Any],
                     intermediate_facts: Dict[str, Any]) -> str:
    """Carrier #1: the pin and both root records, in one textual pass.

    Textual and not a YAML round-trip, deliberately: this file's comments carry
    the reasoning for every closed vocabulary in it, and a round-trip would
    silently delete all of them -- which is knowledge loss dressed as
    formatting (knowledge-retention.md).

    HALTS if any expected placeholder is absent, rather than writing a partial
    edit. A partial three-carrier edit is exactly the tamper G1.3 and G1.4
    exist to catch, so producing one here would be the tool committing the
    attack it is meant to prevent.
    """
    per_root = {
        ROOT_ID: (root, root_facts),
        INTERMEDIATE_ID: (intermediate, intermediate_facts),
    }
    scalar_keys = ("status", "fingerprint", "did", "key_id",
                   "valid_from", "valid_until", "evidence")
    applied: Dict[str, int] = {}
    out: List[str] = []
    current: Optional[str] = None
    skipping_ceremony = False

    for line in text.splitlines():
        if skipping_ceremony:
            # swallow the old ceremony children (indent >= 6); anything at a
            # shallower indent ends the block
            if line.strip() == "" or re.match(r"^ {6,}\S", line):
                continue
            skipping_ceremony = False

        top = re.match(r"^pinned_fingerprint:", line)
        if top:
            out.append(f'pinned_fingerprint: "{root["fingerprint"]}"')
            applied["pin"] = applied.get("pin", 0) + 1
            continue

        new_root = re.match(r"^  - id: (\S+)", line)
        if new_root:
            current = new_root.group(1)
            out.append(line)
            continue

        if current in per_root:
            record, facts = per_root[current]
            key_match = re.match(r"^    (\w+):", line)
            if key_match:
                key = key_match.group(1)
                if key == "public_key_pem":
                    out.append(_pem_block(record["public_key_pem"]))
                    applied[f"{current}.public_key_pem"] = 1
                    continue
                if key == "ceremony":
                    out.append(_ceremony_block(facts))
                    applied[f"{current}.ceremony"] = 1
                    skipping_ceremony = True
                    continue
                if key in scalar_keys:
                    value = record.get(key)
                    out.append(f"    {key}: {json.dumps(str(value))}")
                    applied[f"{current}.{key}"] = 1
                    continue
            elif line and not line.startswith(" "):
                current = None
        elif line and not line.startswith(" ") and not line.startswith("#"):
            current = None

        out.append(line)

    expected = {"pin"}
    for rid in per_root:
        expected.add(f"{rid}.public_key_pem")
        expected.add(f"{rid}.ceremony")
        for key in scalar_keys:
            expected.add(f"{rid}.{key}")
    missing = sorted(expected - set(applied))
    if missing:
        raise CeremonyRefused(
            "carrier-shape",
            f"{ROOTS_RELPATH.as_posix()} is missing expected fields: "
            + ", ".join(missing))

    result = "\n".join(out)
    return result + "\n" if text.endswith("\n") else result


def edit_trust_pin(text: str, fingerprint: str) -> str:
    """Carrier #2: the compiled pin. Both constants, or neither."""
    new_text, n_fp = re.subn(
        r"^ROOT_FINGERPRINT: str = .*$",
        f'ROOT_FINGERPRINT: str = "{fingerprint}"',
        text, count=1, flags=re.MULTILINE)
    new_text, n_state = re.subn(
        r'^PIN_STATE: str = .*$',
        'PIN_STATE: str = "minted"',
        new_text, count=1, flags=re.MULTILINE)
    if n_fp != 1 or n_state != 1:
        raise CeremonyRefused(
            "carrier-shape",
            f"{PIN_RELPATH.as_posix()} does not carry both assignments "
            f"(ROOT_FINGERPRINT={n_fp}, PIN_STATE={n_state})")
    return new_text


def edit_genesis_doc(text: str, authority_string: str, *,
                     as_of: str) -> str:
    """Carrier #3: the human-readable fingerprint, between its own markers."""
    if DOC_BEGIN not in text or DOC_END not in text:
        raise CeremonyRefused(
            "carrier-shape",
            f"{GENESIS_DOC_RELPATH.as_posix()} carries no "
            f"release-root-fingerprint marker pair; refusing to guess which "
            f"line is the fingerprint")
    head, rest = text.split(DOC_BEGIN, 1)
    _body, tail = rest.split(DOC_END, 1)
    block = (
        f"{DOC_BEGIN}\n"
        f"Minted {as_of}. Compare this against the project site, the signed "
        f"git tag and the release announcement before trusting a first clone:\n"
        f"\n"
        f"    {authority_string}\n"
        f"\n"
        f"{DOC_END}"
    )
    return head + block + tail


# ---------------------------------------------------------------------------
# the primitives, loaded rather than reimplemented
# ---------------------------------------------------------------------------


def load_mint_tool(repo_root: Path) -> Any:
    """Import ``scripts/genesis/mint_release_root.py`` from the target clone.

    Loaded by path, the same way ``provenance`` loads the bundle hasher: the
    mint tool is a script, not a package module, and reimplementing its mint or
    its sign here would be a second definition of the thing that must have
    exactly one.
    """
    path = repo_root / MINT_TOOL_RELPATH
    if not path.exists():
        raise CeremonyRefused(
            "selftest-failed",
            f"the ceremony primitive is absent: {MINT_TOOL_RELPATH.as_posix()}")
    pkg = repo_root / "packages" / "intentops-core"
    if pkg.is_dir() and str(pkg) not in sys.path:
        sys.path.insert(0, str(pkg))
    spec = importlib.util.spec_from_file_location(
        "_intentops_mint_release_root", path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise CeremonyRefused("selftest-failed", f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run(cmd: Sequence[str], *, cwd: Optional[Path] = None,
         env: Optional[Dict[str, str]] = None,
         timeout: float = 300.0) -> Tuple[int, str]:
    """Run a subprocess and return (rc, combined output). Never raises on rc."""
    try:
        proc = subprocess.run(
            list(cmd), cwd=str(cwd) if cwd else None, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except FileNotFoundError as exc:
        return 127, f"not found: {exc}"
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    return proc.returncode, proc.stdout or ""


def _subprocess_env(repo_root: Path) -> Dict[str, str]:
    """The environment a STRANGER's clone would verify under.

    The development flag is stripped, not merely unset in our own process: the
    acceptance test for the whole ceremony is that G1 passes on its own merits,
    and a flag inherited from the operator's shell would make it pass for the
    wrong reason.
    """
    env = dict(os.environ)
    env.pop("INTENTOPS_GENESIS_UNSIGNED_DEV", None)
    env.pop("CI", None)
    pkg = str(repo_root / "packages" / "intentops-core")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = pkg + (os.pathsep + existing if existing else "")
    return env


# ---------------------------------------------------------------------------
# the journal
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _blank_state(repo_root: Path) -> Dict[str, Any]:
    return {
        "schema": STATE_SCHEMA,
        "started_at": _now_iso(),
        "repo_root": str(repo_root),
        "out_path": None,
        "assurance_level": None,
        "steps": {},
        "note": ("PUBLIC FIELDS ONLY. This journal never carries a passphrase, "
                 "a private key, or any derivative of one."),
    }


def _write_state(state: Dict[str, Any], paths: Sequence[Path]) -> None:
    """Locked fresh-read RMW on every copy. Both copies or a loud failure."""
    text = json.dumps(state, indent=2, sort_keys=True) + "\n"
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        with StoreLock(lock_for(path)):
            atomic_replace(path, text)


def _read_state(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A state file that half-parses is never guessed at: a resume built on
        # a misread journal would re-mint a root that already exists.
        return None
    if not isinstance(doc, dict) or doc.get("schema") != STATE_SCHEMA:
        return None
    return doc


# ---------------------------------------------------------------------------
# what the tool can actually observe about the room
# ---------------------------------------------------------------------------


def observe_host() -> Dict[str, Any]:
    """Everything the wizard can honestly measure about this host.

    Deliberately short. Each value is a MEASUREMENT; the operator's statements
    live beside them in the record, attributed, and the two are never merged
    into a single adjective.
    """
    observed: Dict[str, Any] = {
        "python": ".".join(str(p) for p in sys.version_info[:3]),
        "platform": sys.platform,
        "ci_env_set": bool(os.environ.get("CI")),
    }
    try:
        addrs = socket.getaddrinfo(socket.gethostname(), None)
        observed["resolved_local_addresses"] = len({a[4][0] for a in addrs})
    except OSError:
        observed["resolved_local_addresses"] = "unknown"
    # A UDP connect() sends no packet: it only asks the kernel whether a route
    # exists. That is the strongest honest statement available about "offline"
    # without transmitting anything, and it is labelled as what it is.
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.settimeout(0.2)
        probe.connect(("8.8.8.8", 53))
        observed["default_route_present"] = True
        probe.close()
    except OSError:
        observed["default_route_present"] = False
    return observed


# ---------------------------------------------------------------------------
# the wizard
# ---------------------------------------------------------------------------


class Wizard:
    """Ten screens, one journal, one irreversible sitting."""

    def __init__(self, repo_root: Path, console: Console, *,
                 out_path: Optional[Path] = None, resume: bool = False,
                 now: Optional[_dt.date] = None,
                 runner: Callable[..., Tuple[int, str]] = _run) -> None:
        self.repo = Path(repo_root).resolve()
        self.console = console
        self.resume = resume
        self.today = now or _dt.date.today()
        self.run_cmd = runner
        self.local_state_path = self.repo / CEREMONY_STATE_RELPATH
        self.state: Dict[str, Any] = _blank_state(self.repo)
        self.passphrase: Optional[str] = None
        self.mint = None  # type: Any
        self.out: Optional[Path] = Path(out_path) if out_path else None
        #: The medium copy of the journal is written only AFTER the medium has
        #: been accepted. Otherwise a --out that turns out to be inside a
        #: repository would already have had a directory created in it by the
        #: preflight step's own journal write -- the tool committing, in
        #: miniature, the act it exists to refuse.
        self._medium_ok = False

    # -- journal helpers ----------------------------------------------------

    @property
    def state_paths(self) -> List[Path]:
        paths = [self.local_state_path]
        if self.out is not None and self._medium_ok:
            paths.insert(0, self.out / "ceremony-state.json")
        return paths

    def _done(self, step: str) -> bool:
        entry = self.state["steps"].get(step)
        return bool(entry and entry.get("status") == "complete")

    def _complete(self, step: str, data: Optional[Dict[str, Any]] = None) -> None:
        self.state["steps"][step] = {
            "status": "complete", "at": _now_iso(), "data": data or {},
        }
        _write_state(self.state, self.state_paths)

    def _data(self, step: str) -> Dict[str, Any]:
        return (self.state["steps"].get(step) or {}).get("data") or {}

    def _skip(self, step: str) -> bool:
        """Should this step be skipped? Only ever on an explicit resume."""
        return self.resume and step not in ALWAYS_RERUN and self._done(step)

    # -- screens ------------------------------------------------------------

    def screen_0_gate(self) -> None:
        attended, remedy = self.console.attendance()
        self.console.say()
        self.console.say("=" * 72)
        self.console.say("INTENTOPS RELEASE-ROOT CEREMONY")
        self.console.say("=" * 72)
        self.console.say(
            "  This wizard walks the sitting described in docs/TRUST-CEREMONY.md:\n"
            "  it mints the project's release root and its release intermediate,\n"
            "  signs the imprint manifest, and applies the three-carrier edit.\n")
        self.console.say(
            "  IT IS ATTENDED-ONLY. It is never run from a loop tick, a workflow\n"
            "  leg, a subagent, a container entrypoint, a scheduled task, or CI.\n"
            "  If a process can trigger this ceremony, the key belongs to the\n"
            "  process, not to you.\n")
        self.console.say(
            "  It will NEVER print, copy, or transmit private key material, and\n"
            "  it will NEVER commit. It stages the carrier edit for your review.\n")
        self.console.say(
            "  Every step is journalled. If something goes wrong you get a\n"
            "  numbered remedy and `--resume` continues where you stopped.\n")
        if not attended:
            raise CeremonyRefused(
                remedy,
                "the ceremony refuses to run without a human at the terminal")

    def step_preflight(self) -> None:
        self.console.screen(
            1, len(STEPS), "PREFLIGHT AND ASSURANCE LEVEL",
            about=("check the tooling round-trips, record what this host can be "
                   "observed to be, and record what you state it to be"),
            why=("a ceremony run on tooling that cannot round-trip produces a "
                 "key nobody can verify; and the runbook's preconditions are "
                 "properties of the room, which no program can observe"),
            will_not=("claim your sitting is offline, witnessed, or clean -- it "
                      "records your statement and attributes it to you"))

        if self.resume and self._done("preflight"):
            data = self._data("preflight")
            self.state["assurance_level"] = data.get("assurance_level")
            self.console.say("  resuming with the recorded preflight:")
            self.console.say(f"    assurance level : {data.get('assurance_level')}")
            self.console.say(f"    operator        : {data.get('operator')}")
            self.console.say(f"    witness         : {data.get('witnessed_by')}")
            return

        if sys.version_info < (3, 11):
            raise CeremonyRefused(
                "python-too-old",
                f"this interpreter is {'.'.join(str(p) for p in sys.version_info[:3])}")
        if importlib.util.find_spec("cryptography") is None:
            raise CeremonyRefused("crypto-unavailable", "cryptography is absent")

        rc, out = self.run_cmd(
            [sys.executable, str(self.repo / MINT_TOOL_RELPATH), "--selftest"],
            cwd=self.repo, env=_subprocess_env(self.repo))
        self.console.say(f"  mint_release_root --selftest : {out.strip()}")
        if rc != 0:
            raise CeremonyRefused("selftest-failed", out.strip())

        rc, out = self.run_cmd(
            [sys.executable, str(self.repo / BUILD_MANIFEST_RELPATH), "--check"],
            cwd=self.repo, env=_subprocess_env(self.repo))
        self.console.say(f"  build_manifest --check       : {out.strip()}")
        if rc != 0:
            raise CeremonyRefused("manifest-drift", out.strip())

        # rc is checked, not ignored. `_run` returns stdout and stderr
        # combined, so a non-zero git -- "fatal: not a git repository" -- would
        # otherwise be recorded AS the commit id, and its one line would count
        # as one uncommitted change. A ceremony record carrying an error string
        # in the tool-version field is worse than one carrying `unknown`,
        # because it looks like an answer.
        rc_commit, commit_out = self.run_cmd(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"],
            env=_subprocess_env(self.repo))
        commit = (commit_out.strip().splitlines()[0]
                  if rc_commit == 0 and commit_out.strip()
                  else "unknown (not a git repository, or git is unavailable)")
        rc_dirty, dirty = self.run_cmd(
            ["git", "-C", str(self.repo), "status", "--porcelain"],
            env=_subprocess_env(self.repo))
        dirty_count = (len([ln for ln in dirty.splitlines() if ln.strip()])
                       if rc_dirty == 0 else 0)
        self.console.say(f"  repository commit            : {commit}")
        if dirty_count:
            # WARN, never a refusal: a dirty tree is common and legitimate, and
            # a wizard that refused on it would simply be routed around.
            self.console.say(
                f"  WARN: {dirty_count} uncommitted change(s) in this tree. The "
                f"carrier edit will be staged alongside them -- review "
                f"`git diff --cached` before committing.")

        observed = observe_host()
        self.console.say(f"  observed                     : "
                         f"{json.dumps(observed, sort_keys=True)}")

        self.console.say()
        self.console.say("  Assurance level (recorded in the ceremony record):")
        for key, meta in ASSURANCE_LEVELS.items():
            self.console.say(f"    {key:<9} {meta['summary']}")
        level = ""
        while level not in ASSURANCE_LEVELS:
            level = self.console.ask(
                "  Which level is this sitting? (standard / full)",
                default="standard").strip().lower()
            if level not in ASSURANCE_LEVELS:
                self.console.say("  Answer 'standard' or 'full'.")

        operator = self.console.ask("  Operator (the person minting), as it "
                                    "should appear in the record")
        witness = self.console.ask(
            "  Witness (a second person, PRESENT). Blank if none.")
        if ASSURANCE_LEVELS[level]["witness_required"] and not witness:
            self.console.say(
                "  The `full` level requires a named witness present. Recording "
                "this sitting as `standard` instead, which is the honest label.")
            level = "standard"
        location = self.console.ask("  Location")
        machine = self.console.ask("  Machine (make/model; freshly booted?)")
        network = self.console.ask(
            "  Network state, in your words (e.g. 'cable unplugged, radio off')")
        medium = self.console.ask(
            "  Storage medium for the private key (decided BEFORE the sitting)")
        custody = self.console.ask(
            "  Passphrase custody -- WHERE the passphrase lives, never the "
            "passphrase itself")

        facts = {
            "assurance_level": level,
            "operator": f"operator states: {operator}" if operator else None,
            "witnessed_by": f"operator states: {witness}" if witness else None,
            "location": f"operator states: {location}" if location else None,
            "machine": f"operator states: {machine}" if machine else None,
            "network": (f"operator states: {network}; tool observed "
                        f"default_route_present="
                        f"{observed['default_route_present']}, "
                        f"resolved_local_addresses="
                        f"{observed['resolved_local_addresses']}"),
            "storage_medium": f"operator states: {medium}" if medium else None,
            "passphrase_custody": (f"operator states: {custody}"
                                   if custody else None),
            "tool_commit": commit,
            "tool_selftest": "mint_release_root --selftest PASS",
            "observed": observed,
        }
        self.state["assurance_level"] = level
        self._complete("preflight", facts)

    def step_medium(self) -> None:
        self.console.screen(
            2, len(STEPS), "THE MEDIUM",
            about="choose where the private keys and the record will be written",
            why=("a private key never lives in a repository -- it lives on a "
                 "medium you decided on before the sitting"),
            will_not="write anything to it yet beyond a throwaway probe file")

        recorded = self._data("medium").get("out_path")
        if self.out is None and recorded:
            self.out = Path(recorded)
        if self.out is None:
            answer = self.console.ask(
                "  Output directory (OUTSIDE every repository)")
            if not answer:
                raise CeremonyRefused("refused", "no output path was given")
            self.out = Path(answer).expanduser()

        out = self.out.resolve() if self.out.exists() else Path(
            os.path.abspath(str(self.out.expanduser())))
        self.out = out
        self._refuse_repo_path(out)

        try:
            out.mkdir(parents=True, exist_ok=True)
            probe = out / ".ceremony-write-probe"
            probe.write_text("probe\n", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            raise CeremonyRefused("medium-unwritable", str(exc)) from exc

        self._medium_ok = True
        self.state["out_path"] = str(out)
        self.console.say(f"  medium accepted: {out}")
        self._complete("medium", {"out_path": str(out)})

    def _refuse_repo_path(self, path: Path) -> None:
        """No key inside ANY repository, not merely inside this one.

        The mint primitive refuses paths inside its own clone. That is the
        right check and it is not enough: a key written into some OTHER
        checkout is just as committed, just as pushed, and just as public.
        """
        for candidate in [path, *path.parents]:
            if (candidate / ".git").exists():
                raise CeremonyRefused(
                    "medium-inside-repo",
                    f"a git repository was found at {candidate}")
        if self.mint is not None:
            try:
                self.mint.refuse_repo_path(path)
            except Exception as exc:  # noqa: BLE001 - the primitive's refusal
                raise CeremonyRefused("medium-inside-repo", str(exc)) from exc

    def step_passphrase(self) -> None:
        self.console.screen(
            3, len(STEPS), "THE PASSPHRASE",
            about=("take the passphrase that wraps the private keys, twice, "
                   "without echo"),
            why=("the primitive REFUSES to write an unencrypted release root, "
                 "and the runbook says the passphrase is chosen before the "
                 "sitting, not composed at the prompt"),
            will_not=("store it, journal it, print it, or pass it to any "
                      "subprocess"))

        root_key = self.out / f"{ROOT_ID}.key" if self.out else None
        if root_key is not None and root_key.exists():
            self.console.say(
                "  A root key already exists on this medium (this is a resumed\n"
                "  ceremony). Type the SAME passphrase; it is checked against\n"
                "  the key rather than trusted.")
            for _attempt in range(3):
                candidate = self.console.secret("  passphrase")
                if self._passphrase_opens(root_key, candidate):
                    self.passphrase = candidate
                    self.console.say("  passphrase verified against the minted key")
                    # Journalled even though it records nothing secret: a step
                    # that legitimately stores no data still stays in the
                    # journal's population, or a reader cannot tell "did not
                    # run" from "is not recorded" (no-silent-failures rule 1).
                    self._complete("passphrase",
                                   {"stored": False,
                                    "verified_against_minted_key": True})
                    return
                self.console.say("  that passphrase does not open the key.")
            raise CeremonyRefused("passphrase-wrong",
                                  "three attempts did not open the minted key")

        for _attempt in range(3):
            first = self.console.secret("  passphrase for the release keys")
            if len(first) < MIN_PASSPHRASE:
                self.console.say(
                    f"  too short: at least {MIN_PASSPHRASE} characters.")
                continue
            second = self.console.secret("  again")
            if first != second:
                self.console.say("  the two entries did not match.")
                continue
            self.passphrase = first
            self.console.say("  passphrase accepted (never stored)")
            self._complete("passphrase",
                           {"stored": False, "verified_against_minted_key": False})
            return
        raise CeremonyRefused(
            "passphrase-mismatch",
            "three attempts did not produce a matching, long-enough passphrase")

    def _passphrase_opens(self, key_path: Path, passphrase: str) -> bool:
        try:
            from cryptography.hazmat.primitives.serialization import (  # noqa: PLC0415
                load_der_private_key,
            )
            load_der_private_key(key_path.read_bytes(),
                                 password=passphrase.encode("utf-8"))
        except Exception:  # noqa: BLE001 - any failure means "does not open"
            return False
        return True

    def _mint_one(self, step: str, number: int, title: str, key_id: str,
                  about: str, why: str) -> Dict[str, Any]:
        self.console.screen(number, len(STEPS), title, about=about, why=why,
                            will_not=("print the private key, or write it "
                                      "anywhere but the medium you named"))
        if self._skip(step):
            record = self._data(step)
            self.console.say(f"  already minted in this ceremony: "
                             f"{record.get('fingerprint')}")
            return record
        assert self.out is not None and self.passphrase is not None
        target = self.out / f"{key_id}.key"
        try:
            record = self.mint.mint(target, self.passphrase)
        except Exception as exc:  # noqa: BLE001 - the primitive's own refusal
            raise CeremonyRefused("mint-failed", str(exc)) from exc
        public = {k: v for k, v in record.items() if k != "private_key_path"}
        public["private_key_written_to"] = str(target)
        self.console.say(f"  minted {key_id}. PUBLIC material follows.")
        for key in ("fingerprint", "did", "key_id", "authority_string"):
            self.console.say(f"    {key:<18}: {record[key]}")
        self.console.say(f"    private key       : written to {target} "
                         f"(encrypted, never printed)")
        self._complete(step, public)
        return public

    def step_mint_root(self) -> Dict[str, Any]:
        return self._mint_one(
            "mint-root", 4, f"MINT THE RELEASE ROOT ({ROOT_ID})", ROOT_ID,
            about="generate the project's publishing key",
            why=("the release root signs the release intermediate and the "
                 "trust-roots file; it lives offline with you"))

    def step_mint_intermediate(self) -> Dict[str, Any]:
        return self._mint_one(
            "mint-intermediate", 5,
            f"MINT THE RELEASE INTERMEDIATE ({INTERMEDIATE_ID})",
            INTERMEDIATE_ID,
            about="generate the short-lived key that signs release artifacts",
            why=("the intermediate signs the imprint manifest and the "
                 "archetype catalogue; its expiry alone revokes it for an "
                 "offline clone, which is why the root itself never signs them"))

    def step_sign(self, intermediate: Dict[str, Any]) -> Dict[str, Any]:
        self.console.screen(
            6, len(STEPS), "SIGN THE IMPRINT MANIFEST",
            about=("re-check the bundle hashes, then sign the manifest's "
                   "GENERATED artifacts block with the intermediate"),
            why=("a signature over a stale hash block is worse than no "
                 "signature, so the check runs immediately before the sign"),
            will_not=("sign anything else, or guess where a signature belongs "
                      "in an already-signed file"))
        if self._skip(step := "sign-imprint"):
            self.console.say(f"  already signed in this ceremony by "
                             f"{self._data(step).get('key_id')}")
            return self._data(step)

        rc, out = self.run_cmd(
            [sys.executable, str(self.repo / BUILD_MANIFEST_RELPATH), "--check"],
            cwd=self.repo, env=_subprocess_env(self.repo))
        self.console.say(f"  build_manifest --check : {out.strip()}")
        if rc != 0:
            raise CeremonyRefused("manifest-drift", out.strip())

        assert self.out is not None and self.passphrase is not None
        key_path = self.out / f"{INTERMEDIATE_ID}.key"
        try:
            signature = self.mint.sign_manifest(
                self.repo, key_path, self.passphrase, intermediate["key_id"])
        except Exception as exc:  # noqa: BLE001 - the primitive's own refusal
            raise CeremonyRefused("sign-failed", str(exc)) from exc
        data = {"key_id": intermediate["key_id"],
                "signature_prefix": signature[:16],
                "scope": "the GENERATED artifacts block, LF-normalised, UTF-8"}
        self.console.say(f"  signed by {intermediate['key_id']}: "
                         f"{signature[:16]}...")
        self._complete("sign-imprint", data)
        return data

    def step_carriers(self, root: Dict[str, Any],
                      intermediate: Dict[str, Any]) -> None:
        self.console.screen(
            7, len(STEPS), "THE THREE-CARRIER EDIT",
            about=("write the pin and both root records into config/, the "
                   "compiled pin into trust_pin.py, and the human-readable "
                   "fingerprint into docs/GENESIS.md -- as ONE change"),
            why=("three carriers make a PARTIAL tamper visible; a partial edit "
                 "is exactly what G1.3 and G1.4 exist to catch, so all three "
                 "move together or none does"),
            will_not=("commit. It stages the change for your review, and it "
                      "will not write anything until you type the phrase"))
        if self._skip("three-carrier-edit"):
            self.console.say("  already applied in this ceremony")
            return

        facts = self._data("preflight")
        record_name = f"ceremony-record-{self.today.isoformat()}.md"
        shared = {k: facts.get(k) for k in (
            "assurance_level", "operator", "location", "machine", "network",
            "storage_medium", "passphrase_custody", "tool_commit",
            "tool_selftest", "observed")}
        shared["minted_at"] = self.today.isoformat()
        shared["witnessed_by"] = facts.get("witnessed_by")
        shared["record"] = f"held with the private key medium as {record_name}"

        root_record = dict(root)
        root_record.update({
            "status": "active",
            "valid_from": self.today.isoformat(),
            "valid_until": self.today.replace(
                year=self.today.year + 10).isoformat(),
            "evidence": (f"[OBSERVED {self.today.isoformat()}] minted by "
                         f"intentops ceremony at assurance level "
                         f"{facts.get('assurance_level')}; record kept with "
                         f"the key medium as {record_name}"),
        })
        inter_record = dict(intermediate)
        inter_record.update({
            "status": "active",
            "valid_from": self.today.isoformat(),
            "valid_until": _plus_months(self.today, 15).isoformat(),
            "evidence": (f"[OBSERVED {self.today.isoformat()}] minted in the "
                         f"same sitting as {ROOT_ID}; signs the imprint "
                         f"manifest"),
        })

        edits = self._render_carrier_edits(root_record, inter_record, shared)
        for path, (before, after) in edits.items():
            self.console.say()
            self.console.say(f"  --- diff: {path.as_posix()} ---")
            for line in difflib.unified_diff(
                    before.splitlines(), after.splitlines(),
                    fromfile=f"a/{path.as_posix()}", tofile=f"b/{path.as_posix()}",
                    lineterm="", n=2):
                self.console.say(f"  {line}")

        self.console.say()
        typed = self.console.ask(
            f"  Review the diff above. To write all three carriers, type "
            f"exactly:\n  {CARRIER_PHRASE}")
        if typed.strip() != CARRIER_PHRASE:
            raise CeremonyRefused("carrier-not-confirmed",
                                  "the phrase was not typed exactly; "
                                  "nothing was written")

        for path, (_before, after) in edits.items():
            target = self.repo / path
            with StoreLock(lock_for(target)):
                atomic_replace(target, after)
            self.console.say(f"  wrote {path.as_posix()}")

        rc, out = self.run_cmd(
            ["git", "-C", str(self.repo), "add", "--"]
            + [p.as_posix() for p in edits],
            env=_subprocess_env(self.repo))
        if rc == 0:
            self.console.say("  staged all three carriers (NOT committed)")
        else:
            self.console.say(f"  NOT staged (this is not a git repository, or "
                             f"git refused): {out.strip()[:200]}")
        self._complete("three-carrier-edit",
                       {"carriers": [p.as_posix() for p in edits],
                        "staged": rc == 0})

    def _render_carrier_edits(self, root: Dict[str, Any],
                              intermediate: Dict[str, Any],
                              facts: Dict[str, Any]
                              ) -> Dict[Path, Tuple[str, str]]:
        roots_text = (self.repo / ROOTS_RELPATH).read_text(encoding="utf-8")
        pin_text = (self.repo / PIN_RELPATH).read_text(encoding="utf-8")
        doc_text = (self.repo / GENESIS_DOC_RELPATH).read_text(encoding="utf-8")
        inter_facts = dict(facts)
        inter_facts["record"] = facts.get("record")
        return {
            ROOTS_RELPATH: (roots_text, edit_trust_roots(
                roots_text, root=root, intermediate=intermediate,
                root_facts=facts, intermediate_facts=inter_facts)),
            PIN_RELPATH: (pin_text, edit_trust_pin(
                pin_text, root["fingerprint"])),
            GENESIS_DOC_RELPATH: (doc_text, edit_genesis_doc(
                doc_text, root["authority_string"],
                as_of=self.today.isoformat())),
        }

    def step_verify(self) -> Dict[str, Any]:
        self.console.screen(
            8, len(STEPS), "VERIFY FROM A STRANGER'S POSITION",
            about=("run G1 against the edited tree in a fresh process with the "
                   "development flag stripped, then bring a throwaway node up "
                   "to G7"),
            why=("G1.7 reading PASS is the acceptance test for the whole "
                 "ceremony; until 2026-09-06 that outcome was unreachable by "
                 "construction, so a green G1.7 is evidence rather than a "
                 "formality"),
            will_not=("touch your node, or accept the development flag as an "
                      "answer"))
        if self._skip("verify"):
            self.console.say("  already verified in this ceremony")
            return self._data("verify")

        env = _subprocess_env(self.repo)
        rc, out = self.run_cmd(
            [sys.executable, "-m", "intentops_core.genesis.provenance",
             "--repo-root", str(self.repo)], cwd=self.repo, env=env)
        g17 = None
        try:
            start = out.index("{")
            record = json.loads(out[start:])
            for check in record.get("checks", []):
                if str(check.get("id", "")).startswith("G1.7"):
                    g17 = check
        except (ValueError, TypeError):
            g17 = None
        if g17 is None:
            raise CeremonyRefused("verify-g17",
                                  f"G1 produced no G1.7 row (rc={rc}): "
                                  f"{out.strip()[:400]}")
        self.console.say(f"  G1.7-imprint-signature : {g17['outcome']}")
        self.console.say(f"    {g17['reason']}")
        if g17["outcome"] != "PASS":
            raise CeremonyRefused("verify-g17", g17["reason"])

        with tempfile.TemporaryDirectory(prefix="intentops-ceremony-") as td:
            rc2, out2 = self.run_cmd(
                [sys.executable, "-m", "intentops_core.cli",
                 "--node-root", td, "--repo-root", str(self.repo),
                 "genesis", "--dry-run", "--identity-repo", "new"],
                cwd=td, env=env)
        phase_lines = [ln for ln in out2.splitlines()
                       if re.match(r"^G\d\s|^final state:", ln)]
        for line in phase_lines:
            self.console.say(f"  {line}")
        reached = "final state: G7" in out2
        if not reached:
            raise CeremonyRefused(
                "verify-genesis",
                "\n".join(phase_lines) or out2.strip()[:400])
        self.console.say("  a throwaway node reached G7 WITHOUT the development "
                         "flag. The ceremony is accepted.")
        data = {"g17": g17["outcome"], "genesis_final_state": "G7",
                "genesis_rc": rc2}
        self._complete("verify", data)
        return data

    def step_record(self, root: Dict[str, Any],
                    intermediate: Dict[str, Any],
                    signature: Dict[str, Any]) -> Path:
        self.console.screen(
            9, len(STEPS), "THE CEREMONY RECORD",
            about=("write the completed record beside the key, and print the "
                   "authority string for out-of-band publication"),
            why=("three carriers make a partial tamper visible and nothing in "
                 "the repository can make a TOTAL substitution visible; the "
                 "out-of-band fingerprint is the only thing that can"),
            will_not=("write the record into the repository, or carry anything "
                      "but public fields"))
        assert self.out is not None
        facts = self._data("preflight")
        path = self.out / f"ceremony-record-{self.today.isoformat()}.md"
        if self._skip("record") and path.exists():
            self.console.say(f"  already written: {path}")
            return path
        text = render_record(facts, root, intermediate, signature,
                             verify=self._data("verify"),
                             today=self.today)
        try:
            with StoreLock(lock_for(path)):
                atomic_replace(path, text)
        except OSError as exc:
            raise CeremonyRefused("record-failed", str(exc)) from exc
        self.console.say(f"  record written: {path}")
        self.console.say()
        self.console.say("  PUBLISH THIS OUT OF BAND -- on the project site, in "
                         "the signed git tag, and in the release announcement:")
        self.console.say()
        self.console.say(f"      {root['authority_string']}")
        self.console.say()
        self.console.say("  An operator comparing a first clone against this "
                         "string is the only check that catches a total "
                         "substitution. Nothing in this repository can.")
        self._complete("record", {"record_path": str(path)})
        return path

    def step_summary(self) -> None:
        self.console.screen(
            10, len(STEPS), "WHAT TO DO NEXT",
            about="three commands, in this order",
            why=("the wizard stages and never commits: a tool that could "
                 "commit the pin unattended is the coup the three carriers "
                 "exist to make visible"),
            will_not="run any of them for you")
        version = (self.repo / "VERSION")
        tag = version.read_text(encoding="utf-8").strip() if version.is_file() \
            else "<version>"
        self.console.say("  1. review the staged change, every line of it:")
        self.console.say("       git diff --cached")
        self.console.say("  2. commit all three carriers together:")
        self.console.say('       git commit -m "trust: mint the release root '
                         'and sign the imprint"')
        self.console.say("  3. tag and push, signed:")
        self.console.say(f"       git tag -s v{tag} -m "
                         f'"release root minted" && git push --follow-tags')
        self.console.say()
        self.console.say("  The private keys stay on the medium. They never "
                         "enter this repository, and no step above copies them.")
        self._complete("summary", {"tag": tag})

    # -- the pass -----------------------------------------------------------

    def execute(self) -> int:
        self.screen_0_gate()
        self.mint = load_mint_tool(self.repo)

        if self.resume:
            loaded = _read_state(self.local_state_path)
            if loaded is None and self.out is not None:
                loaded = _read_state(self.out / "ceremony-state.json")
            if loaded is None:
                raise CeremonyRefused(
                    "state-unreadable",
                    f"no readable {STATE_SCHEMA} journal at "
                    f"{CEREMONY_STATE_RELPATH.as_posix()}")
            self.state = loaded
            if self.out is None and loaded.get("out_path"):
                self.out = Path(loaded["out_path"])
            self.console.say(
                f"  RESUMING. Completed so far: "
                f"{', '.join(s for s in STEPS if self._done(s)) or 'nothing'}")

        self.step_preflight()
        self.step_medium()
        self.step_passphrase()
        root = self.step_mint_root()
        intermediate = self.step_mint_intermediate()
        signature = self.step_sign(intermediate)
        self.step_carriers(root, intermediate)
        self.step_verify()
        self.step_record(root, intermediate, signature)
        self.step_summary()

        self.console.say()
        self.console.say("=" * 72)
        self.console.say("CEREMONY COMPLETE. Nothing is committed; review and "
                         "commit yourself.")
        self.console.say("=" * 72)
        return 0


def _plus_months(date: _dt.date, months: int) -> _dt.date:
    month_index = date.month - 1 + months
    year = date.year + month_index // 12
    month = month_index % 12 + 1
    day = min(date.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or
              year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30,
              31][month - 1])
    return _dt.date(year, month, day)


def render_record(facts: Dict[str, Any], root: Dict[str, Any],
                  intermediate: Dict[str, Any], signature: Dict[str, Any],
                  *, verify: Dict[str, Any], today: _dt.date) -> str:
    """The completed ceremony record. PUBLIC FIELDS ONLY, by construction.

    Follows ``docs/CEREMONY-RECORD.template.md``. Every operator statement
    keeps its ``operator states:`` prefix rather than being flattened into an
    assertion the record cannot support.
    """
    def cell(value: Any) -> str:
        return "" if value is None else str(value).replace("|", "\\|")

    lines = [
        f"# Release-root ceremony record -- {today.isoformat()}",
        "",
        "Written by `intentops ceremony`. PUBLIC FIELDS ONLY: no passphrase, no",
        "private key, and no derivative of one appears anywhere in this file.",
        "",
        "Statements the tool could not observe carry an explicit "
        "`operator states:` prefix.",
        "A ceremony record is the ONLY evidence that the sitting was performed",
        "as described; the tooling cannot supply it.",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Assurance level | {cell(facts.get('assurance_level'))} |",
        f"| Date (local date of the sitting) | {today.isoformat()} |",
        f"| Location | {cell(facts.get('location'))} |",
        f"| Operator | {cell(facts.get('operator'))} |",
        f"| Witness | {cell(facts.get('witnessed_by')) or 'none (standard level)'} |",
        f"| Machine | {cell(facts.get('machine'))} |",
        f"| Network state | {cell(facts.get('network'))} |",
        f"| Storage medium | {cell(facts.get('storage_medium'))} |",
        f"| Passphrase custody | {cell(facts.get('passphrase_custody'))} |",
        f"| Tool version (git commit) | {cell(facts.get('tool_commit'))} |",
        f"| Selftest before minting | {cell(facts.get('tool_selftest'))} |",
        f"| Observed by the tool | `{json.dumps(facts.get('observed') or {}, sort_keys=True)}` |",
        "",
        f"## Root: {ROOT_ID} (public fields)",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| `key_id` | {cell(root.get('key_id'))} |",
        f"| `fingerprint` | {cell(root.get('fingerprint'))} |",
        f"| `did` | {cell(root.get('did'))} |",
        f"| authority string | {cell(root.get('authority_string'))} |",
        f"| `valid_from` / `valid_until` | {cell(root.get('valid_from'))} / "
        f"{cell(root.get('valid_until'))} |",
        "",
        f"## Intermediate: {INTERMEDIATE_ID} (public fields)",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| `key_id` | {cell(intermediate.get('key_id'))} |",
        f"| `fingerprint` | {cell(intermediate.get('fingerprint'))} |",
        f"| `did` | {cell(intermediate.get('did'))} |",
        f"| `issued_by` | {ROOT_ID} |",
        "",
        "## Signing",
        "",
        "| Field | Value |",
        "|---|---|",
        "| `build_manifest --check` before signing | OK |",
        f"| Manifest signed with | {cell(signature.get('key_id'))} |",
        f"| Signature (first 16 hex) | {cell(signature.get('signature_prefix'))} |",
        f"| Scope | {cell(signature.get('scope'))} |",
        "",
        "## Acceptance",
        "",
        "| Check | Result |",
        "|---|---|",
        f"| `G1.7-imprint-signature` | {cell(verify.get('g17'))} |",
        f"| `genesis --dry-run` without the development flag | "
        f"{cell(verify.get('genesis_final_state'))} |",
        "",
        "## Attestation",
        "",
        "The operator, and the witness where one is named above, attest that the",
        "release root was minted attended, on the machine named above, with the",
        "private key written only to the medium named above in passphrase-wrapped",
        "form, and that no private key material was printed, copied, or",
        "transmitted.",
        "",
        "Operator signature: ______________________  "
        "Witness signature: ______________________",
        "",
        "## After the sitting",
        "",
        "1. `git diff --cached`, then commit all three carriers together.",
        "2. Tag the release, signed, and push with `--follow-tags`.",
        "3. Publish the authority string out of band: the project site, the",
        "   signed tag, and the release announcement. Three carriers make a",
        "   PARTIAL tamper visible; only the out-of-band copy can make a TOTAL",
        "   substitution visible.",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# selftest -- a wizard whose refusals have never fired is a claim
# ---------------------------------------------------------------------------


def selftest() -> Tuple[bool, str]:
    """Prove the carrier editors round-trip and every refusal can fire."""
    fired: List[str] = []
    failures: List[str] = []

    def expect(label: str, cond: bool) -> None:
        (fired if cond else failures).append(label)

    # 1. the pin editor moves both constants, and refuses a shape it cannot find
    pin_src = ('ROOT_FINGERPRINT: str = PLACEHOLDER_FINGERPRINT\n'
               'PIN_STATE: str = "placeholder"\n')
    fp = "sha256:" + "a" * 64
    edited = edit_trust_pin(pin_src, fp)
    expect("pin-editor-sets-fingerprint", f'"{fp}"' in edited)
    expect("pin-editor-sets-state", 'PIN_STATE: str = "minted"' in edited)
    try:
        edit_trust_pin("nothing here\n", fp)
    except CeremonyRefused:
        fired.append("pin-editor-refuses-unknown-shape")
    else:
        failures.append("pin-editor-refuses-unknown-shape")

    # 2. the doc editor refuses a document with no marker pair
    try:
        edit_genesis_doc("# Genesis\n", "intentops-root:v1:x:y",
                         as_of="2026-01-01")
    except CeremonyRefused:
        fired.append("doc-editor-refuses-missing-markers")
    else:
        failures.append("doc-editor-refuses-missing-markers")
    doc = f"a\n{DOC_BEGIN}\nold\n{DOC_END}\nb\n"
    out = edit_genesis_doc(doc, "intentops-root:v1:x:y", as_of="2026-01-01")
    expect("doc-editor-replaces-between-markers",
           "old" not in out and "intentops-root:v1:x:y" in out)

    # 3. the roots editor refuses a file missing the placeholders
    try:
        edit_trust_roots("roots: []\n", root={}, intermediate={},
                         root_facts={}, intermediate_facts={})
    except CeremonyRefused:
        fired.append("roots-editor-refuses-unknown-shape")
    else:
        failures.append("roots-editor-refuses-unknown-shape")

    # 4. a scripted console with no answers refuses rather than looping
    console = ScriptedConsole([], [])
    try:
        console.ask("anything")
    except CeremonyRefused:
        fired.append("console-eof-is-a-refusal")
    else:
        failures.append("console-eof-is-a-refusal")

    # 5. attendance refuses CI and a non-TTY, separately
    expect("attendance-refuses-ci",
           ScriptedConsole([], env={"CI": "1"}).attendance() == (False, "ci-set"))
    expect("attendance-refuses-non-tty",
           ScriptedConsole([], is_tty=False).attendance()
           == (False, "not-attended"))

    # 6. the Windows NUL probe is reachable and answers a bool, not a guess
    expect("null-device-probe-answers-a-bool",
           isinstance(_stdin_is_the_null_device(), bool))

    # 7. every remedy id carries a distinct exit code and a paste rule
    codes = [row["code"] for row in REMEDIES.values()]
    expect("remedy-codes-are-unique", len(set(codes)) == len(codes))
    expect("every-remedy-names-what-to-paste",
           all(row.get("paste") for row in REMEDIES.values()))

    report = (f"ceremony wizard selftest: {len(fired)} paths fired, "
              f"{len(failures)} failed"
              + (" -- " + ", ".join(failures) if failures else ""))
    return (not failures, report)


# ---------------------------------------------------------------------------
# entrypoint
# ---------------------------------------------------------------------------


def run(args: argparse.Namespace, repo_root: Path,
        console: Optional[Console] = None) -> int:
    """Dispatch for ``intentops ceremony``. Returns a process exit code."""
    if getattr(args, "selftest", False):
        ok, report = selftest()
        print(report)
        return 0 if ok else 1
    if getattr(args, "remedies", False):
        print(remedy_table_markdown())
        return 0

    console = console or Console()
    wizard = Wizard(repo_root, console,
                    out_path=Path(args.out) if getattr(args, "out", None)
                    else None,
                    resume=bool(getattr(args, "resume", False)))
    try:
        return wizard.execute()
    except CeremonyRefused as exc:
        console.say(render_remedy(exc.remedy_id, exc.detail))
        return exc.code
    except Exception as exc:  # noqa: BLE001 - an ungraded stop is still a stop
        # Deliberately NOT folded into a declared remedy id. This wizard's whole
        # contract is that a failure names itself; inventing a row for a failure
        # it does not model would be worse than admitting it has none. The
        # traceback is printed in full -- an operator mid-ceremony needs the
        # actual cause, not a reassuring summary of it.
        import traceback

        console.say("")
        console.say("-" * 72)
        console.say("CEREMONY STOPPED  [undeclared]  exit 1")
        console.say(f"  what happened : {type(exc).__name__}: {exc}")
        console.say("  remedy       : this is not a failure the wizard models, "
                    "so it has no numbered row. Nothing already completed was "
                    "lost -- the journal is written after each step, and "
                    "--resume continues at the step that was open.")
        console.say("  safe to paste: the traceback below. It is generated from "
                    "this repository's own code and carries no key material, "
                    "but read it before sending it.")
        console.say("-" * 72)
        console.say(traceback.format_exc())
        return 1
