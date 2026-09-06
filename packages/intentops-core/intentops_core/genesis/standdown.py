"""The stand-down -- one command, no reasons, no argument from the node.

PURPOSE
    An operator who has just been handed a system must be able to switch it
    off before consent, before calibration, and while the node believes it is
    right. That is why the stand-down is a FILE, not a request: it does not
    require the node's cooperation, its agreement, or its participation.

    Three properties hold, and each of them is load-bearing:

    1. **No reason is required or recorded.** If an operator switches this off,
       they should not have to explain themselves to the thing being switched
       off. Only the date is recorded, in the identity repo's epoch list.
    2. **A stood-down node is OFF, not broken.** The exit code is 0, the
       certificate reads ``STOOD-DOWN``, and no instrument reports a defect.
       Nothing bad happens to the operator for using it, and the banner says
       so in those words.
    3. **There is no ``stand-up``.** Removing the marker is the operator's own
       act, deliberately manual. A command that could switch the node back on
       is a command the node could one day be asked to run.

WRITE MODEL
    ``.intentops/halt.marker`` -- create-only, single writer, no lock. It is
    the one store where a lock would be wrong: the marker's whole purpose is
    to be writable when everything else is wedged, including a hung writer
    holding a lock. Its CONTENT carries no meaning; its PRESENCE is the fact,
    which is why a partial write cannot produce a wrong reading.

    ``<identity-repo>/identity/epochs.yaml`` -- locked fresh-read RMW, and
    append-only in content: an epoch is added, never edited or removed.

BLIND SPOTS
    - This module writes the marker. Whether the host actually refuses tool
      calls while it exists is the saddle's S5 operation, not this module's;
      a host with no S5 gets a marker nobody reads.
    - It cannot tell a marker written by the operator from one written by any
      other process on the machine, and that is deliberate: a stand-down that
      checked its own authorship would be a stand-down that could argue.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..store_guard import StoreLock, atomic_replace, lock_for
from . import HALT_MARKER_RELPATH

__all__ = ["stand_down", "is_stood_down", "status", "BANNER", "selftest"]

BANNER = (
    "STOOD DOWN. This node is OFF, not broken. Nothing bad happens to you for "
    "using this. No reason was asked for and none was recorded. To bring it "
    "back, delete the marker file yourself -- there is no command that does it."
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def marker_path(node_root: Path | str) -> Path:
    return Path(node_root) / HALT_MARKER_RELPATH


def is_stood_down(node_root: Path | str) -> bool:
    """True iff the marker exists. No parsing, no interpretation."""
    return marker_path(node_root).exists()


def _record_epoch(identity_repo: Path) -> Optional[str]:
    """Append a dated stand-down epoch. The date, and nothing else."""
    import yaml

    path = Path(identity_repo) / "identity" / "epochs.yaml"
    if not path.exists():
        return None
    with StoreLock(lock_for(path)):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(doc, dict):
            return None
        epochs: List[Dict[str, Any]] = list(doc.get("epochs") or [])
        epochs.append({"epoch": len(epochs) + 1, "event": "stand-down",
                       "date": _today()})
        doc["epochs"] = epochs
        atomic_replace(path, yaml.safe_dump(doc, default_flow_style=False,
                                            sort_keys=False, allow_unicode=True))
    return path.as_posix()


def stand_down(node_root: Path | str,
               identity_repo: Optional[Path | str] = None) -> Dict[str, Any]:
    """Switch the node off. Never prompts, never refuses, never asks why.

    Idempotent: standing down a node that is already down is not an error and
    does not add a second epoch.
    """
    path = marker_path(node_root)
    already = path.exists()
    if not already:
        path.parent.mkdir(parents=True, exist_ok=True)
        # No lock, and content that means nothing on purpose (see WRITE MODEL).
        path.write_text(f"stood-down at {_now()}\n", encoding="utf-8")
    epoch_file = None
    if identity_repo is not None and not already:
        epoch_file = _record_epoch(Path(identity_repo))
    return {
        "state": "STOOD-DOWN",
        "marker": path.as_posix(),
        "already_down": already,
        "epoch_recorded_in": epoch_file,
        "reason_recorded": False,
        "banner": BANNER,
    }


def status(node_root: Path | str) -> Dict[str, Any]:
    """Whether the marker exists. Exit code 0 either way: OFF is not a defect."""
    path = marker_path(node_root)
    return {"state": "STOOD-DOWN" if path.exists() else "RUNNING",
            "marker": path.as_posix(),
            "marker_present": path.exists()}


def selftest() -> Tuple[bool, str]:
    """Prove the marker lands, is idempotent, records no reason, and reads back."""
    import tempfile

    failures: List[str] = []
    fired: List[str] = []

    def expect(label: str, cond: bool) -> None:
        (fired if cond else failures).append(label)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        expect("clean-node-reads-running", status(root)["state"] == "RUNNING")

        # works with no organs at all -- mid-G1, before consent, before anything
        first = stand_down(root)
        expect("marker-lands-with-no-organs", is_stood_down(root))
        expect("no-reason-recorded", first["reason_recorded"] is False)
        expect("banner-says-off-not-broken", "OFF, not broken" in first["banner"])
        expect("status-reads-stood-down", status(root)["state"] == "STOOD-DOWN")

        second = stand_down(root)
        expect("idempotent", second["already_down"] is True)

        # the epoch is dated and carries no reason field
        import yaml

        ident = root / "identity-repo"
        (ident / "identity").mkdir(parents=True, exist_ok=True)
        (ident / "identity" / "epochs.yaml").write_text(
            "schema: epochs/v1\nepochs:\n  - epoch: 1\n    event: birth\n",
            encoding="utf-8")
        other = root / "node2"
        result = stand_down(other, identity_repo=ident)
        doc = yaml.safe_load((ident / "identity" / "epochs.yaml")
                             .read_text(encoding="utf-8"))
        last = doc["epochs"][-1]
        expect("epoch-appended", last["event"] == "stand-down")
        expect("epoch-carries-date-only",
               set(last) == {"epoch", "event", "date"})
        expect("epoch-file-named", bool(result["epoch_recorded_in"]))

    report = (f"standdown selftest: {len(fired)} paths fired, "
              f"{len(failures)} failed"
              + (" -- " + ", ".join(failures) if failures else ""))
    return (not failures, report)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="stand a node down")
    parser.add_argument("--node-root", default=".")
    parser.add_argument("--identity-repo", default=None)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        ok, report = selftest()
        print(report)
        return 0 if ok else 1
    if args.status:
        print(json.dumps(status(args.node_root), indent=2))
        return 0
    result = stand_down(args.node_root, args.identity_repo)
    print(result["banner"])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
