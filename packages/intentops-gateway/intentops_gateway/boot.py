"""The boot image -- what a cold client asks the gateway for, first.

PURPOSE
    A fresh context window on some other harness knows nothing about this node.
    The boot image is the smallest set of facts that lets it act correctly
    anyway: WHICH IMPRINT this node carries, WHAT RULES it is under, WHETHER IT
    IS ALIVE and stood down or not, and HOW CALIBRATED it is -- which is to say,
    how much of its own judgement it is entitled to use.

    FOUR PARTS, AND THE FOURTH IS THE ONE PEOPLE SKIP.

      imprint      version, status, and the bundle index of the birth bundle
      rules        the node's own boot corpus, as text -- the refusals in full
      aliveness    the last recorded reading, or an honest "never taken"
      calibration  the honest string: calibrated, or N of 20 and therefore
                   telemetry rather than authority

    READ-ONLY, STRUCTURALLY. Nothing in THIS MODULE opens a file for writing,
    and the image is assembled fresh per request from files on disk. There is
    no cache to go stale and no boot state to poison. (The gateway's request
    ledger still records that the image was asked for -- that append belongs
    to the transport, not to the image, and a boot call that left no trace
    would be the one request an audit could not see.)

    HASHES TRAVEL, BYTES OF THE MANIFEST DO NOT. The image carries the
    imprint's version, status, signature count and bundle names -- enough for a
    client to say WHICH imprint it is talking to and to notice that it changed.
    It does not carry the whole manifest: a client that wants to verify the
    chain has ``intentops.status`` and the repository, and shipping several
    hundred hashes down a boot path costs every client on every cold start.

WRITE MODEL
    None -- read-only. This module opens files and never writes one.

BLIND SPOTS
    * An UNSIGNED imprint is reported as unsigned, with its signature count.
      The gateway does not verify the chain here; genesis does that at G1, and
      a second half-verification on the boot path would be a claim this module
      cannot back. ``signatures: 0`` is a fact travelling to the client, not a
      pass.
    * The rules are read from the NODE's copy (``.intentops-rules/``), which is
      what the node actually carries -- not from the repository, which is what
      it was born from. Those can differ, and the difference is exactly what a
      client should see. Absence is reported as absence, never as an empty
      corpus that reads like a node with no refusals.
    * The aliveness reading is the LAST one recorded. It ages: a reading from
      last month is reported with its own timestamp and nothing here decides
      how old is too old, because that judgement depends on what the client
      is about to do.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = ["ALIVENESS_RELPATH", "RULES_RELDIR", "boot_image"]

RULES_RELDIR = Path(".intentops-rules")
ALIVENESS_RELPATH = Path(".intentops") / "genesis" / "aliveness.jsonl"
_IMPRINT_MANIFEST = Path("genesis") / "imprint" / "IMPRINT-MANIFEST.yaml"

#: A rule file larger than this is served as a pointer with its size rather
#: than inline. Nothing in the shipped corpus is close; the cap exists so a
#: node whose operator adds a large rule does not silently make every cold
#: boot expensive.
_MAX_RULE_BYTES = 64_000


def boot_image(node_root: Path | str, repo_root: Optional[Path | str],
               *, include_rule_text: bool = True) -> Dict[str, Any]:
    """Assemble the boot image. Never raises on a missing part; absence is data."""
    node = Path(node_root)
    repo = Path(repo_root) if repo_root else None
    return {
        "schema": "gateway-boot/v1",
        "node_root": str(node),
        "repo_root": str(repo) if repo else None,
        "imprint": _imprint(repo),
        "rules": _rules(node, include_text=include_rule_text),
        "aliveness": _aliveness(node),
        "calibration": _calibration(node),
        "read_only": True,
        "note": (
            "The boot image is assembled fresh per request from files on disk; "
            "assembling it writes nothing. Every absent part is reported as "
            "absent rather than omitted, so a client can tell 'this node has "
            "no reading' from 'this gateway did not look'."
        ),
    }


def _imprint(repo: Optional[Path]) -> Dict[str, Any]:
    if repo is None:
        return {"status": "UNPROBEABLE",
                "reason": "the framework checkout could not be located, so the "
                          "imprint manifest cannot be read"}
    path = repo / _IMPRINT_MANIFEST
    if not path.is_file():
        return {"status": "ABSENT", "path": str(path),
                "reason": "no imprint manifest at the expected path"}
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return {"status": "UNREADABLE", "path": str(path), "error": str(exc)}
    if not isinstance(data, dict):
        return {"status": "UNREADABLE", "path": str(path),
                "error": "the manifest is not a mapping"}
    bundles = data.get("bundles")
    names: List[str] = []
    if isinstance(bundles, list):
        names = [str(b.get("id") or b.get("name") or "")
                 for b in bundles if isinstance(b, dict)]
    elif isinstance(bundles, dict):
        names = [str(k) for k in bundles]
    signatures = data.get("signatures")
    signature_count = len(signatures) if isinstance(signatures, list) else 0
    return {
        "status": "PRESENT",
        "path": str(path),
        "schema": str(data.get("schema") or ""),
        "imprint_version": str(data.get("imprint_version") or ""),
        "as_of": str(data.get("as_of") or ""),
        "manifest_status": str(data.get("status") or ""),
        "hash_algorithm": str(data.get("hash_algorithm") or ""),
        "bundles": [n for n in names if n],
        "signatures": signature_count,
        "signed": signature_count > 0,
        "note": ("`signed: false` is the honest state of an imprint that has "
                 "not been through a root ceremony. It is a fact travelling to "
                 "the client, not a verification result -- the chain is "
                 "verified at genesis, not on this path."),
    }


def _rules(node: Path, *, include_text: bool) -> Dict[str, Any]:
    directory = node / RULES_RELDIR
    if not directory.is_dir():
        return {
            "status": "ABSENT",
            "path": str(directory),
            "count": 0,
            "reason": ("this node carries no rules corpus. That is not an "
                       "empty set of refusals -- it is a node whose boot "
                       "corpus never landed, and a client should treat it as "
                       "a node it cannot read the rules of."),
        }
    entries: List[Dict[str, Any]] = []
    for path in sorted(directory.glob("*.md")):
        row: Dict[str, Any] = {"name": path.name}
        try:
            size = path.stat().st_size
        except OSError as exc:
            row.update({"status": "UNREADABLE", "error": str(exc)})
            entries.append(row)
            continue
        row["bytes"] = size
        if not include_text:
            row["status"] = "PRESENT"
        elif size > _MAX_RULE_BYTES:
            row.update({"status": "OVERSIZE",
                        "reason": f"larger than {_MAX_RULE_BYTES} bytes; read "
                                  "it from the node rather than the boot path"})
        else:
            try:
                row["text"] = path.read_text(encoding="utf-8")
                row["status"] = "PRESENT"
            except OSError as exc:
                row.update({"status": "UNREADABLE", "error": str(exc)})
        entries.append(row)
    return {"status": "PRESENT", "path": str(directory),
            "count": len(entries), "entries": entries}


def _aliveness(node: Path) -> Dict[str, Any]:
    path = node / ALIVENESS_RELPATH
    if not path.is_file():
        return {"status": "NEVER-TAKEN", "path": str(path),
                "reason": "no aliveness reading has been recorded on this node"}
    last: Optional[str] = None
    malformed = 0
    total = 0
    try:
        with path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                total += 1
                last = raw
    except OSError as exc:
        return {"status": "UNREADABLE", "path": str(path), "error": str(exc)}
    if last is None:
        return {"status": "EMPTY", "path": str(path), "readings": 0,
                "reason": "the ledger exists and holds no reading"}
    import json
    try:
        row = json.loads(last)
    except json.JSONDecodeError as exc:
        malformed = 1
        row = {"error": f"the last line is malformed: {exc}"}
    if not isinstance(row, dict):
        malformed = 1
        row = {"error": "the last line is not a JSON object"}
    return {"status": "PRESENT" if not malformed else "MALFORMED",
            "path": str(path), "readings": total, "last": row}


def _calibration(node: Path) -> Dict[str, Any]:
    """The honest calibration statement.

    Delegated to the MCP saddle's implementation rather than recomputed: the
    bar (20 rulings at 80%, a partial grade counting as a miss) must have ONE
    definition, and a second copy is a second thing to drift. If the two
    surfaces ever disagreed about whether a node is calibrated, an operator
    would have no way to tell which was lying.
    """
    from intentops_saddle_mcp.tools import ToolError, calibration_string

    try:
        return calibration_string(node)
    except ToolError as exc:
        return {"status": "UNREADABLE", "error": str(exc),
                "note": ("the calibration journal is present and unreadable; "
                         "an unreadable journal is not an uncalibrated node "
                         "and must not read as one")}
