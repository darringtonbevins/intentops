"""The ``ceremony`` verb's parser and dispatch.

Additive-elsewhere, the same seam ``loops`` and ``metabolism`` use: the node
CLI gains one registration and one dispatch row, so a defect in this surface
cannot strand ``intentops genesis`` or ``intentops verify``.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any


def selftest() -> Any:
    """Re-exported so the selftest registry can reach the instrument.

    A verb that advertises --selftest and exposes no module-level callable is
    an instrument the registry cannot run -- which reads as coverage while
    being none.
    """
    from .wizard import selftest as _selftest

    return _selftest()


def add_parser(sub: Any) -> argparse.ArgumentParser:
    """Register ``intentops ceremony`` on the node CLI's subparsers."""
    parser = sub.add_parser(
        "ceremony",
        help="the attended release-root ceremony, as a resumable wizard")
    parser.add_argument(
        "--out", default=None,
        help="the medium the private keys and the record are written to. It "
             "must be OUTSIDE every repository; the wizard asks if it is "
             "omitted.")
    parser.add_argument(
        "--resume", action="store_true",
        help="continue at the last incomplete step, using the journal at "
             ".intentops/ceremony/ceremony-state.json. Nothing already done is "
             "repeated, and no key already minted is minted again.")
    parser.add_argument(
        "--selftest", action="store_true",
        help="prove the carrier editors round-trip and every refusal can fire")
    parser.add_argument(
        "--remedies", action="store_true",
        help="print the remedy table (the source docs/CEREMONY-REMEDIATION.md "
             "renders from)")
    return parser


def run(args: argparse.Namespace, repo_root: Path) -> int:
    from .wizard import run as _run

    return _run(args, repo_root)
