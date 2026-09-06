"""The reaches-reality classifier -- does this operation touch the world?

PURPOSE
    Answer one question, purely and deterministically: does this operation
    reach outside the node into the world, where a mistake cannot be undone by
    the node alone? The tier LADDER is a label; this classifier is the
    discriminator, and where the two disagree the classifier is what the gate
    acts on.

    Six classes, each a separate pure function so a caller can ask about one:

      * ``remote push``            -- publishing work to a remote others read
      * ``prod change``            -- mutating a live system
      * ``outbound comms``         -- speech leaving the node toward a person
      * ``money``                  -- anything that moves or records value
      * ``irreversible delete``    -- destruction with no way back
      * ``third-party disclosure`` -- data leaving toward someone who is not the
                                      operator

    DESIGN INVARIANT, and it is load-bearing: classify off the OPERATION -- the
    command SKELETON with quoted data blanked out, or the target PATH -- and
    NEVER off file content. A workspace write whose body merely mentions a
    recursive delete is self-scoped and reversible; treating it as the act is
    how a gate ends up blocking its own documentation.

    NO ESTATE IS NAMED HERE. Everything an estate makes specific -- which hosts
    are production, which remotes are delegated, whose mailbox is the
    operator's own -- arrives as a ``RealityIndicators`` value, built from
    ``estate/ESTATE-MAP.yaml``. The built-in patterns are generic operations
    (a push, a recursive delete, a destructive SQL statement) that are hazards
    on any node anywhere.

    Nothing in this module exits a process, writes a file, prints a protocol
    frame, or raises on an unrecognised operation. It returns a value.

WRITE MODEL
    None -- pure functions over their arguments.

BLIND SPOTS
    * Skeleton parsing is a regex over a shell string, not a shell parser. A
      command assembled at runtime, base64-decoded, or read from a variable is
      invisible. This classifier raises the cost of an accident; it is not a
      defence against a determined evasion, and must never be described as one.
    * Quoted segments are blanked, so an operation legitimately expressed
      entirely inside quotes is missed. That trade is deliberate: the opposite
      error (reading data as an act) fires constantly and teaches people to
      route around the gate.
    * ``delegated_remote_owners`` is the only indicator that can WEAKEN a
      verdict, and it only applies when the target owner is provable from the
      command text. Anything it cannot prove stays at the stricter reading.
    * A tier of T0-T2 here means "not human-gated by this classifier". It does
      not mean safe, and it does not mean some other gate has no opinion.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .verdict import Verdict, allow, ask

__all__ = [
    "FILE_WRITE_TOOLS",
    "MCP_PREFIX",
    "RealityIndicators",
    "SHELL_COMMAND_TOOLS",
    "classify_irreversible_delete",
    "classify_money",
    "classify_outbound_comms",
    "classify_prod_change",
    "classify_reaches_reality",
    "classify_remote_push",
    "classify_third_party_disclosure",
    "command_skeleton",
    "indicators_from_estate_map",
    "selftest",
    "verdict_for",
]

#: The names that carry a shell command in the REFERENCE host. Documentation,
#: not a gate: :func:`classify_reaches_reality` fires the shell classes on any
#: call carrying a non-empty ``command``, whatever it is called, because a
#: backend tool named ``shell`` is a shell.
SHELL_COMMAND_TOOLS: Tuple[str, ...] = ("Bash", "PowerShell", "Shell")
#: These ARE name-gated: the file classes read ``file_path``, a field far more
#: likely to appear innocently on an unrelated tool than ``command`` is.
FILE_WRITE_TOOLS: Tuple[str, ...] = ("Write", "Edit", "MultiEdit", "NotebookEdit")
MCP_PREFIX = "mcp__"


# ---------------------------------------------------------------------------
# the estate seam
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RealityIndicators:
    """The estate-specific half of the classification, as data.

    Every field defaults to empty, and an empty field NARROWS nothing: with no
    indicators at all the generic operation patterns still fire. A blank estate
    is therefore gated on operations and not on names, which is the honest
    behaviour for a node that has not been told anything yet.
    """

    #: Tokens that mark a live system (host fragments, environment names).
    production_keywords: Tuple[str, ...] = ()
    #: Tokens that mark an outbound-communication surface.
    outbound_keywords: Tuple[str, ...] = ()
    #: Tokens that mark value moving or being recorded.
    money_keywords: Tuple[str, ...] = ()
    #: Hosts that are not the operator: sending data there is a disclosure.
    third_party_hosts: Tuple[str, ...] = ()
    #: Remote owners the operator has delegated. The ONLY weakening indicator.
    delegated_remote_owners: Tuple[str, ...] = ()
    #: Identities that are the operator's own; reading their mailbox is not a
    #: third-party disclosure. Empty means EVERY identified mailbox read is
    #: treated as third-party, which is the fail-toward-gating direction.
    self_identities: Tuple[str, ...] = ()

    def normalized(self) -> "RealityIndicators":
        low = lambda xs: tuple(sorted({str(x).strip().lower() for x in xs if str(x).strip()}))
        return RealityIndicators(
            production_keywords=low(self.production_keywords),
            outbound_keywords=low(self.outbound_keywords),
            money_keywords=low(self.money_keywords),
            third_party_hosts=low(self.third_party_hosts),
            delegated_remote_owners=low(self.delegated_remote_owners),
            self_identities=low(self.self_identities),
        )


def indicators_from_estate_map(data: Mapping[str, Any]) -> RealityIndicators:
    """Build indicators from a loaded estate-map mapping.

    Two sources, both optional:

      * a top-level ``reality_indicators`` block, whose keys are the
        ``RealityIndicators`` fields;
      * the keywords of every declared SERVED estate, which are added to
        ``production_keywords`` -- a served estate is the one whose production
        the node may not touch on its own word, so its vocabulary belongs in
        the strictest class by default.

    An unknown key inside ``reality_indicators`` RAISES. A block that is half
    understood produces a gate that runs and is wrong.
    """
    block = data.get("reality_indicators") or {}
    if not isinstance(block, Mapping):
        raise ValueError("'reality_indicators' must be a mapping")
    fields = {
        "production_keywords", "outbound_keywords", "money_keywords",
        "third_party_hosts", "delegated_remote_owners", "self_identities",
    }
    unknown = set(block) - fields
    if unknown:
        raise ValueError(
            f"reality_indicators declares unknown key(s): {sorted(unknown)}. "
            f"Known keys: {sorted(fields)}. An undeclared key is a hard exit, "
            "never an ignored line."
        )
    values: Dict[str, Tuple[str, ...]] = {}
    for name in fields:
        raw = block.get(name) or ()
        if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
            raise ValueError(f"reality_indicators.{name} must be a list of strings")
        values[name] = tuple(str(x) for x in raw)

    served_keywords: List[str] = []
    for entry in data.get("entries") or []:
        if isinstance(entry, Mapping) and str(entry.get("kind", "")).lower() == "served":
            for kw in entry.get("keywords") or []:
                served_keywords.append(str(kw))
    values["production_keywords"] = tuple(values["production_keywords"]) + tuple(served_keywords)
    return RealityIndicators(**values).normalized()  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# the skeleton parser
# ---------------------------------------------------------------------------

# Strip single/double-quoted segments (data literals) so we match only the
# executable skeleton of a command. This is what stops a commit message that
# mentions a recursive delete from reading as one.
_QUOTED_SEGMENT = re.compile(r'"[^"]*"|\'[^\']*\'')


def command_skeleton(command: str) -> str:
    """Return the command with quoted (data) segments blanked out."""
    return _QUOTED_SEGMENT.sub(" ", command or "")


# Known database clients. A destructive statement only reaches reality when it
# is actually dispatched to an engine, so a client must co-occur.
_SQL_CLIENT = (r"(?:sqlcmd|psql|mysql|mariadb|sqlite3|bcp|pgcli|mycli|"
               r"Invoke-Sqlcmd|Invoke-SqlCmd)")

_LOCALHOST = r"localhost|127\.0\.0\.1|::1"


# ---------------------------------------------------------------------------
# class 1 -- remote push
# ---------------------------------------------------------------------------

_PUSH_FORCE = re.compile(
    r"\bgit\b(?:\s+(?:-C\s+\S+|--git-dir=\S+|--work-tree=\S+))*\s+push\b[^|&;]*"
    r"(?:--force\b|--force-with-lease\b|(?<!\w)-f\b)"
)
_PUSH = re.compile(
    r"\bgit\b(?:\s+(?:-C\s+\S+|--git-dir=\S+|--work-tree=\S+))*\s+push\b"
)
_HOOK_BYPASS = re.compile(r"--no-verify\b|--no-gpg-sign\b")
_REMOTE_MUTATION = re.compile(
    r"\bgh\s+(?:pr|issue|release|run|repo|workflow)\s+"
    r"(?:create|merge|close|delete|edit|fork|disable|enable|rename|transfer)\b",
    re.IGNORECASE,
)
_REMOTE_API_MUTATION = re.compile(
    r"\bgh\s+api\b[^|&;]*(?:-X|--method)\s+(?:POST|PUT|PATCH|DELETE)\b", re.IGNORECASE
)


def classify_remote_push(command: str, indicators: RealityIndicators) -> Optional[Tuple[str, str]]:
    """Publishing to a remote. Force-push is T4; an ordinary push is T3.

    ``delegated_remote_owners`` can lower an ordinary push to T2, and ONLY when
    the owner is provable from the raw command text. A force push, a hook
    bypass, and any target that cannot be proven stay at the strict reading --
    a weakening indicator that guesses is worse than no indicator.
    """
    skeleton = command_skeleton(command)
    if _PUSH_FORCE.search(skeleton):
        return "T4", "force push to a remote (rewrites history others may hold)"
    if _PUSH.search(skeleton):
        owner = _proven_remote_owner(command, indicators)
        if owner:
            return "T2", f"push to a delegated remote owner ({owner})"
        return "T3", "push to a remote"
    if _HOOK_BYPASS.search(skeleton):
        return "T3", "version-control hook or signature bypass"
    if _REMOTE_MUTATION.search(skeleton) or _REMOTE_API_MUTATION.search(skeleton):
        owner = _proven_remote_owner(command, indicators)
        if owner:
            return "T2", f"remote mutation against a delegated owner ({owner})"
        return "T3", "mutating operation against a code-hosting remote"
    return None


def _proven_remote_owner(command: str, indicators: RealityIndicators) -> str:
    """The delegated owner this command provably targets, or ''.

    Checked against the RAW command because the target usually lives inside a
    quoted path that the skeleton blanks out. Proof is a literal occurrence of
    the declared owner token; nothing is inferred.
    """
    low = (command or "").lower()
    for owner in indicators.normalized().delegated_remote_owners:
        if owner and owner in low:
            return owner
    return ""


# ---------------------------------------------------------------------------
# class 2 -- production change
# ---------------------------------------------------------------------------

_CLOUD_MUTATION = re.compile(
    r"\b(?:az|aws|gcloud)\b[^|&;]*\b(?:delete|purge|create|update|"
    r"set(?:-policy|-secret|-key|-config|-rule)?|assign|deploy)\b",
    re.IGNORECASE,
)
_ORCHESTRATOR_MUTATION = re.compile(
    r"\bkubectl\s+(?:delete|apply|scale|rollout|patch)\b|"
    r"\bterraform\s+(?:destroy|apply)\b|"
    r"\bhelm\s+(?:install|upgrade|uninstall|rollback)\b|"
    r"\bdocker\s+(?:service|stack)\s+\w+",
    re.IGNORECASE,
)
_SQL_WRITE = re.compile(
    rf"\b{_SQL_CLIENT}\b.*\b(?:UPDATE\s+\w|INSERT\s+INTO)\b", re.IGNORECASE | re.DOTALL
)
_PROD_WORD = re.compile(r"\b(prod|production|live)\b", re.IGNORECASE)


def classify_prod_change(command: str, indicators: RealityIndicators) -> Optional[Tuple[str, str]]:
    """Mutating a live system. T3 -- reversible only by whoever owns that system."""
    skeleton = command_skeleton(command)
    ind = indicators.normalized()
    if _CLOUD_MUTATION.search(skeleton):
        return "T3", "cloud control-plane mutation"
    if _ORCHESTRATOR_MUTATION.search(skeleton):
        return "T3", "orchestrator mutation against a running deployment"
    if _SQL_WRITE.search(command or ""):
        return "T3", "database write dispatched through a client"
    low = (command or "").lower()
    for kw in ind.production_keywords:
        if kw in low and (_PROD_WORD.search(low) or _CLOUD_MUTATION.search(skeleton)):
            return "T3", f"operation naming a declared production surface ({kw})"
    return None


# ---------------------------------------------------------------------------
# class 3 -- outbound communication
# ---------------------------------------------------------------------------

_SEND_VERB = re.compile(
    r"\b(?:send|sends|sending|reply|replies|reply all|forward|forwards|publish|"
    r"broadcast|post|posts)\b", re.IGNORECASE
)
_DRAFT_VERB = re.compile(r"\bdrafts?\b", re.IGNORECASE)


def _verb_words(name: str) -> str:
    """Normalise a tool verb to space-separated words.

    ``send_message`` and ``send-message`` both have to read as a send. An
    anchored ``\\b`` alone does not see a boundary at ``_``, so a separator-blind
    pattern silently misses the most common spelling -- which is exactly the
    kind of quiet miss that makes a gate look like it is working.
    """
    return re.sub(r"[-_]+", " ", name or "")
_COMMS_SHELL = re.compile(
    r"\b(?:send-?mail|sendmail|mailx|mutt|Send-MailMessage|smtp)\b", re.IGNORECASE
)


def classify_outbound_comms(tool: str, tool_input: Mapping[str, Any],
                            indicators: RealityIndicators) -> Optional[Tuple[str, str]]:
    """Speech leaving the node toward a person. Always T3, per event.

    Draft creation is deliberately excluded: a draft transmits nothing, and it
    is the safe path this class exists to make available. A verb that both
    drafts AND sends is a send.
    """
    ind = indicators.normalized()
    name = (tool or "").lower()
    if name.startswith(MCP_PREFIX):
        verb = _verb_words(name.split("__")[-1])
        # A transmission verb wins outright, even beside "draft": `send_draft`
        # sends. Checking the draft exclusion first is what would let the one
        # verb that both drafts AND transmits slip through as safe.
        if _SEND_VERB.search(verb):
            return "T3", f"outbound communication (send/reply/forward) [{tool}]"
        if _DRAFT_VERB.search(verb):
            return None  # a draft transmits nothing
        for kw in ind.outbound_keywords:
            if kw in name and _SEND_VERB.search(verb):
                return "T3", f"outbound communication on a declared surface ({kw})"
        return None
    command = str(tool_input.get("command") or "")
    if command and _COMMS_SHELL.search(command_skeleton(command)):
        return "T3", "outbound mail dispatched from the shell"
    return None


# ---------------------------------------------------------------------------
# class 4 -- money
# ---------------------------------------------------------------------------

#: Table and endpoint tokens that mean value. Generic on purpose: a node that
#: keeps accounts keeps them under names like these whoever it belongs to.
# Prefix-anchored, suffix-open: a trailing \b never matches the inflected or
# suffixed form, and a value table is far more often `ledger_entries` than
# `ledger`. Same class of miss as a separator-blind verb pattern.
_MONEY_TOKEN = re.compile(
    r"(?<![\w])(?:ledger\w*|trust[-_ ]?account\w*|payment\w*|payout\w*|payroll\w*|"
    r"invoice\w*|disburse\w*|remittance\w*|wire[-_ ]?transfer\w*)",
    re.IGNORECASE,
)
_MONEY_VERB = re.compile(
    r"\b(?:transfer|disburse|refund|charge|capture|void|settle|pay)\b", re.IGNORECASE
)


def classify_money(command: str, indicators: RealityIndicators) -> Optional[Tuple[str, str]]:
    """Anything that moves or records value.

    A money table touched through a database client is T4 REGARDLESS of verb --
    a read of value records is a disclosure of them, and value records are the
    one place where a mistake is somebody else's money. A money verb dispatched
    over the network is T3.
    """
    raw = command or ""
    skeleton = command_skeleton(raw)
    ind = indicators.normalized()
    if re.search(rf"\b{_SQL_CLIENT}\b", raw, re.IGNORECASE) and _MONEY_TOKEN.search(raw):
        return "T4", "value records referenced through a database client"
    for kw in ind.money_keywords:
        if kw in raw.lower() and re.search(rf"\b{_SQL_CLIENT}\b", raw, re.IGNORECASE):
            return "T4", f"declared value surface referenced through a database client ({kw})"
    if _MONEY_VERB.search(skeleton) and _MONEY_TOKEN.search(skeleton):
        return "T3", "operation that moves or records value"
    return None


# ---------------------------------------------------------------------------
# class 5 -- irreversible delete
# ---------------------------------------------------------------------------

# `(?<!git )` so `git rm` (a reversible index operation) is NOT caught -- only
# the shell `rm` (an irreversible filesystem delete) is.
_RECURSIVE_RM = re.compile(r"(?<!git )\brm\s+(?:-\S+\s+)*-\S*[rR]")
_WINDOWS_RECURSIVE = re.compile(
    r"\b(?:del|erase)\s+/[a-zA-Z]*s\b|\brmdir\s+/[a-zA-Z]*s\b|"
    r"Remove-Item\b[^|&;]*-Recurse\b",
    re.IGNORECASE,
)
_HARD_RESET = re.compile(r"\bgit\s+reset\s+--hard\b|\bgit\s+clean\s+-[a-zA-Z]*f")
_DESTRUCTIVE_SQL = re.compile(
    rf"\b{_SQL_CLIENT}\b.*\b(?:DROP|TRUNCATE)\s+(?:TABLE|DATABASE|SCHEMA)\b",
    re.IGNORECASE | re.DOTALL,
)
_SQL_DELETE = re.compile(rf"\b{_SQL_CLIENT}\b.*\bDELETE\s+FROM\b",
                         re.IGNORECASE | re.DOTALL)


def classify_irreversible_delete(command: str) -> Optional[Tuple[str, str]]:
    """Destruction with no way back. T4 for data, T3 for recoverable history."""
    raw = command or ""
    skeleton = command_skeleton(raw)
    if _RECURSIVE_RM.search(skeleton):
        return "T4", "recursive filesystem delete"
    if _WINDOWS_RECURSIVE.search(skeleton):
        return "T4", "recursive delete"
    if _DESTRUCTIVE_SQL.search(raw):
        return "T4", "destructive schema statement dispatched through a client"
    if _SQL_DELETE.search(raw):
        return "T4", "row deletion dispatched through a client"
    if _HARD_RESET.search(skeleton):
        return "T3", "discards committed or untracked work"
    return None


# ---------------------------------------------------------------------------
# class 6 -- third-party disclosure
# ---------------------------------------------------------------------------

_HTTP_MUTATION_PS = re.compile(
    r"\b(?:Invoke-WebRequest|Invoke-RestMethod|iwr|irm)\b"
    rf"(?=(?:(?!{_LOCALHOST}).)*-Method\s+(?:Post|Put|Patch|Delete)\b)"
    rf"(?=(?:(?!{_LOCALHOST}).)*https?://(?!localhost\b|127\.0\.0\.1))",
    re.IGNORECASE | re.DOTALL,
)
_HTTP_MUTATION_CURL = re.compile(
    r"\bcurl\b"
    rf"(?=(?:(?!{_LOCALHOST}).)*(?:-X|--request)\s+(?:POST|PUT|PATCH|DELETE)\b)"
    rf"(?=(?:(?!{_LOCALHOST}).)*https?://(?!localhost\b|127\.0\.0\.1))",
    re.IGNORECASE | re.DOTALL,
)
# A mailbox-shaped path naming somebody: /users/<id>/messages and its
# entity-key spellings, including the URL-encoded quote forms.
_MAILBOX_PATH = re.compile(
    r"/users[/(](?:%27|['\"])?([^/\s'\"&?%()+]+)(?:%27|['\"])?[/)]"
    r"/?(?:messages|mailfolders|mailboxsettings)\b",
    re.IGNORECASE,
)
_IDENTITY_KEYS: Tuple[str, ...] = ("userId", "user", "mailbox", "upn", "emailAddress")
_MAIL_SURFACE = re.compile(r"mail|message|mailbox|inbox", re.IGNORECASE)


def classify_third_party_disclosure(
    tool: str, tool_input: Mapping[str, Any], indicators: RealityIndicators
) -> Optional[Tuple[str, str]]:
    """Data leaving toward someone who is not the operator. T3.

    Two shapes: content pushed outward over the network, and another person's
    correspondence read inward. Both are disclosures; only the direction
    differs, and only one of them looks like a read.
    """
    ind = indicators.normalized()
    name = (tool or "").lower()
    payload = json.dumps(dict(tool_input), default=str) if tool_input else ""

    # somebody else's mailbox, named in a path
    hit = _mailbox_read(payload, ind)
    if hit:
        return hit

    # somebody else's mailbox, named in an identity field on a mail surface
    if name.startswith(MCP_PREFIX) and (
        _MAIL_SURFACE.search(name) or _MAIL_SURFACE.search(payload)
    ):
        for key in _IDENTITY_KEYS:
            value = tool_input.get(key)
            if isinstance(value, str) and value.strip():
                identity = value.strip().lower()
                if identity not in ind.self_identities:
                    return "T3", (
                        f"reads a mailbox belonging to another person "
                        f"({key}) -- authorization for the surface is not "
                        "authorization for the person"
                    )

    command = str(tool_input.get("command") or "")
    if command:
        if _HTTP_MUTATION_PS.search(command) or _HTTP_MUTATION_CURL.search(command):
            return "T3", "sends data to a non-local endpoint"
        low = command.lower()
        for host in ind.third_party_hosts:
            if host in low:
                return "T3", f"sends data to a declared third-party host ({host})"
    return None


def _mailbox_read(text: str, indicators: RealityIndicators) -> Optional[Tuple[str, str]]:
    for m in _MAILBOX_PATH.finditer(text or ""):
        identity = m.group(1).strip().lower()
        if identity in indicators.self_identities:
            continue  # the operator's own mailbox
        return "T3", ("reads a mailbox belonging to another person -- "
                      "authorization for the surface is not authorization for "
                      "the person")
    return None


# ---------------------------------------------------------------------------
# target-path rules for file writes
# ---------------------------------------------------------------------------

_SECRET_PATH = re.compile(
    r"(?:^|[\\/])\.env(?:\.[\w-]+)?$|\.pem$|\.key$|\.pfx$|\.p12$|"
    r"(?:^|[\\/])id_(?:rsa|ed25519|ecdsa)\b|credential|"
    r"(?<!_)secrets?\.(?:ya?ml|json|txt|ini)$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# the aggregator
# ---------------------------------------------------------------------------

_TIER_RANK = {"T0": 0, "T1": 1, "T2": 2, "T3": 3, "T4": 4}


def classify_reaches_reality(
    tool: str,
    tool_input: Mapping[str, Any],
    *,
    indicators: Optional[RealityIndicators] = None,
) -> Optional[Tuple[str, str]]:
    """Classify one operation. Returns ``(tier, reason)`` or None.

    None means "this classifier has no opinion", never "this is safe". T3 and
    T4 are the human-gated tiers; a T2 return is a classified but non-gated
    operation, and it is returned rather than swallowed so a caller can see
    that something WAS classified.

    Pure and total: an unrecognised tool, a malformed input, or an empty
    command all return None rather than raising.
    """
    ind = (indicators or RealityIndicators()).normalized()
    if not isinstance(tool_input, Mapping):
        return None

    hits: List[Tuple[str, str]] = []

    outbound = classify_outbound_comms(tool, tool_input, ind)
    if outbound:
        hits.append(outbound)
    disclosure = classify_third_party_disclosure(tool, tool_input, ind)
    if disclosure:
        hits.append(disclosure)

    # The shell classes read the COMMAND, not the tool's NAME. Gating them on
    # a name list was how a backend tool called `shell`, `run_command` or
    # `exec` carried a force-push or an `rm -rf /` past a reading that catches
    # the identical arguments under the name `Bash` -- observed 2026-09-06
    # through the gateway, where an MCP backend's tool arrives under its own
    # local name and never matches this list. `classify_outbound_comms`
    # already reads the field unconditionally (see its shell branch), so the
    # old asymmetry was internal to this module.
    #
    # SHELL_COMMAND_TOOLS survives as the documented set of names that carry
    # a `command` in the reference host; it is no longer the gate.
    command = str(tool_input.get("command") or "")
    if command:
        for fn in (classify_money, classify_irreversible_delete,
                   classify_remote_push, classify_prod_change):
            hit = (fn(command) if fn is classify_irreversible_delete
                   else fn(command, ind))  # type: ignore[operator]
            if hit:
                hits.append(hit)
    if tool in FILE_WRITE_TOOLS:
        target = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        if target and _SECRET_PATH.search(target):
            hits.append(("T4", "writes a credential, secret or key file"))

    if not hits:
        return None
    return max(hits, key=lambda h: _TIER_RANK.get(h[0], 0))


def verdict_for(
    tool: str,
    tool_input: Mapping[str, Any],
    *,
    indicators: Optional[RealityIndicators] = None,
) -> Verdict:
    """The classifier's answer as a Verdict. Never exits, never prints.

    T3/T4 -> ASK: a human word is required for THIS act. Anything else is an
    ALLOW that still carries its reason, so a caller can record what was
    classified rather than only what was refused.
    """
    hit = classify_reaches_reality(tool, tool_input, indicators=indicators)
    if hit is None:
        return allow("T0", reasons=("no reaches-reality signature",))
    tier, reason = hit
    if tier in ("T3", "T4"):
        return ask(tier, reason, reaches_reality=True)
    return allow(tier, reasons=(reason,))


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


def selftest() -> int:
    """Prove every class fires, and that data is not read as an act."""
    failures: List[str] = []
    ind = RealityIndicators(
        production_keywords=("orders-service",),
        third_party_hosts=("uploads.example",),
        delegated_remote_owners=("my-own-org",),
        self_identities=("operator-identity",),
    )

    def expect(label: str, tool: str, payload: Dict[str, Any], tier: Optional[str]) -> None:
        got = classify_reaches_reality(tool, payload, indicators=ind)
        got_tier = got[0] if got else None
        if got_tier != tier:
            failures.append(f"{label}: expected {tier}, got {got_tier} ({got})")

    expect("remote push", "Bash", {"command": "git push origin main"}, "T3")
    expect("force push", "Bash", {"command": "git push --force origin main"}, "T4")
    expect("delegated push", "Bash",
           {"command": 'cd "/repos/my-own-org/app" && git push origin main'}, "T2")
    expect("prod change", "Bash", {"command": "kubectl apply -f deploy.yaml"}, "T3")
    expect("irreversible delete", "Bash", {"command": "rm -rf ./build"}, "T4")
    expect("money", "Bash",
           {"command": 'psql -c "SELECT * FROM ledger_entries"'}, "T4")
    expect("third-party disclosure", "Bash",
           {"command": "curl -X POST https://api.example.test/v1/items"}, "T3")
    expect("outbound comms", "mcp__mailbox__send_message", {"to": "someone"}, "T3")
    expect("draft is not a send", "mcp__mailbox__create_draft", {"to": "someone"}, None)
    expect("send_draft is a send", "mcp__mailbox__send_draft", {"id": "d1"}, "T3")
    expect("secret file write", "Write", {"file_path": "/etc/app/id_ed25519"}, "T4")
    expect("local read", "Bash", {"command": "cat README.md"}, None)
    expect("local write", "Write", {"file_path": "docs/notes.md"}, None)
    expect("mailbox of another person", "mcp__mail__get_messages",
           {"userId": "another-identity"}, "T3")
    expect("own mailbox", "mcp__mail__get_messages",
           {"userId": "operator-identity"}, None)

    # data is not an act: the operation lives in the quotes
    expect("quoted hazard is data", "Bash",
           {"command": 'git commit -m "document the rm -rf recovery path"'}, None)
    expect("git rm is not rm", "Bash", {"command": "git rm --cached notes.md"}, None)

    v = verdict_for("Bash", {"command": "git push origin main"}, indicators=ind)
    if not v.is_refusal or v.decision.value != "ASK":
        failures.append(f"verdict_for did not ASK on a remote push: {v.decision}")
    v2 = verdict_for("Bash", {"command": "cat README.md"}, indicators=ind)
    if not v2.allowed:
        failures.append("verdict_for did not ALLOW a local read")

    # the indicator loader refuses what it does not understand
    try:
        indicators_from_estate_map({"reality_indicators": {"nonsense": []}})
    except ValueError:
        pass
    else:
        failures.append("indicators_from_estate_map accepted an undeclared key")

    print("gate.classify selftest:")
    for f in failures:
        print(f"  FAIL: {f}")
    print(f"  {'PASS' if not failures else 'FAIL'} ({len(failures)} failure(s))")
    return 1 if failures else 0


def _main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="the reaches-reality classifier")
    ap.add_argument("--selftest", action="store_true",
                    help="prove every class fires and data is not read as an act")
    ap.add_argument("--tool", default="Bash")
    ap.add_argument("--command", help="classify this shell command")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if not args.command:
        ap.error("--command is required (or use --selftest)")
    print(verdict_for(args.tool, {"command": args.command}).render())
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
