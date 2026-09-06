#!/usr/bin/env python3
"""loto_append -- the ONE sanctioned writer for the lockout/tagout ledger.

PURPOSE. Create a tagout, flip one, or record that its exit condition is met --
all under the ledger's lock, with the whole document validated before anything
is replaced. A failed validation writes NOTHING.

WRITE MODEL. Owned by ``intentops_core.loto.ledger``: locked fresh-read
read-modify-write with an atomic replace. This script is a thin command-line
front for those functions and holds no state of its own.

The ledger has a writer. Use it. Creating an entry by hand is the failure this
script exists to stop: an ad-hoc creation writer once handed the ledger itself
to a lock primitive that TRUNCATES the path it is given, and a sixty-entry
ledger became eleven bytes.

BLIND SPOTS. This script cannot tell whether the tag it records is actually at
the switch -- that is ``loto_check.py``'s carrier check. Nor can it tell whether
a stated ``reenergize_when`` is a real condition or a wish; it only refuses an
empty or placeholder one.

Usage::

    python scripts/ops/loto_append.py --action create \\
        --id LOTO-YYYY-MM-DD-SLUG --tier T3 --what "..." \\
        --carrier src/thing.py=LOTO-YYYY-MM-DD-SLUG \\
        --by "..." --authority "..." --reason "..." --reenergize-when "..."

    python scripts/ops/loto_append.py --action tagged_in \\
        --id LOTO-YYYY-MM-DD-SLUG --by "..." --authority "..." --reason "..."

    python scripts/ops/loto_append.py --action set_status \\
        --id LOTO-YYYY-MM-DD-SLUG --reenergize-status ready \\
        --by "..." --authority "..." --reason "..."

    python scripts/ops/loto_append.py --selftest
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional

try:
    from intentops_core.loto import ledger as loto
except ImportError:  # pragma: no cover - running from a source checkout
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]
                           / "packages" / "intentops-core"))
    from intentops_core.loto import ledger as loto


def _parse_carrier(raw: str) -> Dict[str, str]:
    """``path=probe`` -- both halves required, no default probe.

    A carrier with no probe cannot be verified, and an unverifiable tag is
    worse than no tag because it looks accounted for.
    """
    if "=" not in raw:
        raise SystemExit(f"--carrier must be 'path=probe', got {raw!r}")
    path, probe = raw.split("=", 1)
    if not path.strip() or not probe.strip():
        raise SystemExit(f"--carrier needs both a path and a probe: {raw!r}")
    return {"path": path.strip(), "probe": probe.strip()}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Append to the lockout/tagout ledger (the sanctioned "
                    "write path). A failed validation writes nothing.")
    ap.add_argument("--workspace", default=".", help="node repository root")
    ap.add_argument("--ledger", default=None,
                    help="ledger path (default: <workspace>/"
                         ".intentops/loto/LEDGER.yaml)")
    ap.add_argument("--action",
                    choices=["create", "tagged_out", "tagged_in", "set_status"],
                    help="create = mint a new tagout; tagged_out/tagged_in = "
                         "flip an existing one; set_status = record whether "
                         "the exit condition is met (does NOT flip the switch)")
    ap.add_argument("--id", dest="loto_id")
    ap.add_argument("--tier", choices=list(loto.TIERS))
    ap.add_argument("--what")
    ap.add_argument("--carrier", action="append", default=[],
                    metavar="PATH=PROBE")
    ap.add_argument("--by")
    ap.add_argument("--authority")
    ap.add_argument("--reason")
    ap.add_argument("--reenergize-when", dest="reenergize_when", default=None)
    ap.add_argument("--reenergize-status", dest="reenergize_status",
                    choices=list(loto.REENERGIZE_STATES), default=None)
    ap.add_argument("--evidence", action="append", default=[])
    ap.add_argument("--init", action="store_true",
                    help="create an empty-but-valid ledger if none exists")
    ap.add_argument("--selftest", action="store_true",
                    help="prove every refusal and posture can fire")
    args = ap.parse_args(argv)

    if args.selftest:
        ok, msg = loto.selftest()
        print(f"{'PASS' if ok else 'FAIL'} loto ledger selftest: {msg}")
        return 0 if ok else 1

    workspace = Path(args.workspace).resolve()
    path = Path(args.ledger) if args.ledger else workspace / loto.LEDGER_RELPATH

    if args.init:
        if path.exists():
            print(f"ledger already exists: {path}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            loto._write(path, loto.new_ledger())
            print(f"empty-but-valid ledger created: {path}")
        if not args.action:
            return 0

    if not args.action:
        ap.error("--action is required (or use --init / --selftest)")
    if not args.loto_id:
        ap.error("--id is required")
    if not path.is_file():
        print(f"ERROR: no ledger at {path} (run with --init first)",
              file=sys.stderr)
        return 2

    try:
        if args.action == "create":
            for name in ("tier", "what", "by", "authority", "reason"):
                if not getattr(args, name):
                    ap.error(f"--{name.replace('_', '-')} is required for "
                             "--action create")
            result = loto.create_entry(
                path, args.loto_id, what=args.what, tier=args.tier,
                carriers=[_parse_carrier(c) for c in args.carrier],
                by=args.by, authority=args.authority, reason=args.reason,
                reenergize_when=args.reenergize_when,
                evidence=args.evidence or None)
            print(f"{args.loto_id}: created, state={result['state']} "
                  f"({result['entries']} entries)")
        elif args.action == "set_status":
            if not args.reenergize_status:
                ap.error("--reenergize-status is required for "
                         "--action set_status")
            result = loto.set_reenergize_status(
                path, args.loto_id, args.reenergize_status,
                by=args.by or "", authority=args.authority or "",
                reason=args.reason or "")
            print(f"{args.loto_id}: reenergize_status -> "
                  f"{result['reenergize_status']} (state {result['state']}; "
                  "the switch itself was NOT flipped)")
        else:
            link: Dict[str, object] = {
                "action": args.action, "by": args.by or "",
                "authority": args.authority or "", "reason": args.reason or ""}
            if args.reenergize_when:
                link["reenergize_when"] = args.reenergize_when
            if args.evidence:
                link["evidence"] = args.evidence
            result = loto.append_chain_link(path, args.loto_id, link)
            print(f"{args.loto_id}: state -> {result['state']} "
                  f"(chain length {result['chain_length']})")
    except loto.LedgerError as exc:
        print(f"REFUSED (nothing written): {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
