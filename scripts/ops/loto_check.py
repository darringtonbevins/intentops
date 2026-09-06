#!/usr/bin/env python3
"""loto_check -- the remediation oracle.

PURPOSE. Validate the lockout/tagout ledger and then answer, mechanically, the
question that is otherwise answered from memory: **are we fully remediated, or
is something blocking?**

``REMEDIATED``  nothing safety-critical is off.
``ATTENTION``   safety or detection controls are off, but every one is GOVERNED
                -- an authority and a stated way back. Parked, not forgotten.
``BLOCKED``     a tagout is UNGOVERNED (no authority, or no stated way back), or
                its exit condition is already met and it should have been
                switched back on. Exits 1.

WRITE MODEL. None -- this is a pure read. The ledger's only writer is
``loto_append.py``.

An empty ledger reads REMEDIATED, and that is a true statement about a node that
has switched nothing off.

BLIND SPOTS. The oracle can only see switches somebody tagged: a silent disable
is invisible here by construction, so no verdict is evidence that nothing ELSE
is off. Carrier verification is textual, so a probe string that also appears in
an unrelated comment reads as present. Further blind spots are declared in
``intentops_core.loto.ledger``.

This is deliberately NOT a blocking pre-tool hook: a tagging ledger must never
wedge an unrelated session. It is run from checklists, and any time the question
"is this done?" comes up.

Usage::

    python scripts/ops/loto_check.py                 # verdict; exit 1 if BLOCKED
    python scripts/ops/loto_check.py --strict        # ATTENTION also exits 1
    python scripts/ops/loto_check.py --selftest
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

try:
    from intentops_core.loto import ledger as loto
except ImportError:  # pragma: no cover - running from a source checkout
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]
                           / "packages" / "intentops-core"))
    from intentops_core.loto import ledger as loto


def _resolve_roots(workspace_arg: Optional[str],
                   ledger_arg: Optional[str]) -> tuple[Path, Path]:
    """(workspace, ledger path), with the carrier root implied by the ledger.

    Carriers are resolved RELATIVE to the workspace. Pointing ``--ledger`` at
    another node's ledger while the workspace stayed at the current directory
    therefore looked for that node's carriers under this one, and reported
    "carrier does not exist" -- a true statement about the wrong tree, which is
    the most expensive kind of finding to chase. When ``--ledger`` names a path
    of the canonical shape and no workspace was given, the workspace is the
    root that path implies. An explicit ``--workspace`` always wins.
    """
    if ledger_arg is not None:
        path = Path(ledger_arg).resolve()
        if workspace_arg is not None:
            return Path(workspace_arg).resolve(), path
        depth = len(Path(loto.LEDGER_RELPATH).parts)
        implied = path
        for _ in range(depth):
            implied = implied.parent
        if (implied / loto.LEDGER_RELPATH).resolve() == path:
            return implied, path
        return Path.cwd().resolve(), path
    workspace = Path(workspace_arg or ".").resolve()
    return workspace, workspace / loto.LEDGER_RELPATH


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Validate the lockout/tagout ledger and report posture")
    ap.add_argument("--workspace", default=None,
                    help="node repository root (default: the current "
                         "directory, or the root implied by --ledger)")
    ap.add_argument("--ledger", default=None,
                    help="ledger path (default: <workspace>/"
                         ".intentops/loto/LEDGER.yaml)")
    ap.add_argument("--strict", action="store_true",
                    help="ATTENTION also exits 1")
    ap.add_argument("--selftest", action="store_true",
                    help="prove every refusal and posture can fire")
    args = ap.parse_args(argv)

    if args.selftest:
        ok, msg = loto.selftest()
        print(f"{'PASS' if ok else 'FAIL'} loto ledger selftest: {msg}")
        return 0 if ok else 1

    workspace, path = _resolve_roots(args.workspace, args.ledger)
    if not path.is_file():
        # A missing ledger is NOT remediation. It is an unreadable source, and
        # it stays in the denominator rather than reading as a clean bill.
        print(f"[FAIL] ledger missing: {path}")
        print("RESULT: BLOCKED -- there is no ledger to read, which is not the "
              "same as nothing being switched off")
        return 1

    try:
        doc = loto.load_ledger(path)
    except (OSError, loto.LedgerError) as exc:
        print(f"[FAIL] ledger unreadable: {exc}")
        print("RESULT: BLOCKED -- an unreadable ledger is not a clean one")
        return 1

    failures = 0
    for name, findings in (("schema", loto.check_schema(doc)),
                           ("carriers", loto.check_carriers(doc, workspace)),
                           ("tier_rules", loto.check_tier_rules(doc))):
        if findings:
            failures += len(findings)
            print(f"[FAIL] {name} ({len(findings)})")
            for finding in findings:
                print(f"  FAIL {finding}")
        else:
            print(f"[OK]   {name}")

    verdict, lines = loto.posture(doc)
    print("")
    for line in lines:
        print(line)
    print("")
    print(f"RESULT: {verdict} -- {path}")
    if failures:
        print(f"({failures} validation finding(s) above)")

    if verdict == "BLOCKED" or failures:
        return 1
    if verdict == "ATTENTION" and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
