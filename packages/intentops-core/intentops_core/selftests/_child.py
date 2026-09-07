"""Run ONE module's ``selftest()`` in a fresh interpreter and print a verdict.

PURPOSE
    The registry never calls an instrument's ``selftest()`` in its own
    process. A selftest plants failures, writes temporary trees, mutates
    ``os.environ``, and occasionally imports a heavy optional dependency; one
    of them raising ``SystemExit`` or leaving a global set would silently
    change the verdict of every instrument after it. A subprocess per
    instrument makes each verdict independent, and makes a hang bounded by a
    timeout rather than by patience.

    The verdict travels in a DELIMITED FRAME on stdout::

        __INTENTOPS_SELFTEST__ {"module": ..., "ok": true, "report": "..."}

    Text outside that frame is data, never instruction (SIG-1 clause 6):
    instruments print freely, and the parent reads only the frame. A child
    that produces no frame has produced no verdict, and the registry grades
    that ERROR rather than reading the exit code as an answer -- an exit code
    is the one signal a crashed interpreter can still emit by accident.

    Three return shapes are accepted because three are in the tree, and
    normalising them here is cheaper than rewriting fifty instruments:

    ==========================  ===========================================
    ``int``                     ``0`` is PASS, anything else FAIL
    ``(bool, str)``             the bool is the verdict, the str the report
    ``bool``                    the verdict, with no report
    ==========================  ===========================================

    ``None`` is NOT one of them. A selftest that returns nothing has returned
    no verdict, and absence of a verdict is not a verdict
    (``no-silent-failures`` rule: silence is loss, not success).

WRITE MODEL
    None. It writes nothing anywhere; whatever the instrument itself writes is
    the instrument's own declared model, unchanged by being run from here.

BLIND SPOTS
    - It calls ``selftest()`` with NO arguments. An instrument whose selftest
      requires one is reported ERROR with the ``TypeError``, which is the
      honest reading: the registry cannot invent a repository root that the
      instrument declined to default.
    - It cannot distinguish an instrument that took a long time from one that
      is wedged. The registry's timeout is the only bound, and it is a wall
      clock, not a diagnosis.
    - stdin is closed by the parent. A selftest that waits for a terminal
      reads EOF and fails here rather than hanging -- deliberately, because a
      selftest that needs a TTY cannot run in CI and is a defect, not a skip.
"""

from __future__ import annotations

import importlib
import json
import sys
import traceback
from typing import Any, Dict, List, Optional

#: The frame marker. One token, greppable, and deliberately not a word that
#: appears in ordinary instrument output.
FRAME = "__INTENTOPS_SELFTEST__"

__all__ = ["FRAME", "interpret", "main"]


def interpret(value: Any) -> Dict[str, Any]:
    """Normalise a selftest return value to ``{ok, report}``.

    ``ok`` is ``None`` when the shape carries no verdict -- never coerced to
    ``False``, because "this instrument answered badly" and "this instrument
    did not answer" are different findings and the registry grades them
    differently (FAIL vs ERROR).
    """
    if value is None:
        return {"ok": None,
                "report": "selftest() returned None: absence of a verdict is "
                          "not a verdict"}
    if isinstance(value, bool):
        return {"ok": value, "report": ""}
    if isinstance(value, int):
        return {"ok": value == 0, "report": f"exit code {value}"}
    if isinstance(value, (tuple, list)):
        if not value:
            return {"ok": None,
                    "report": "selftest() returned an empty sequence: no "
                              "verdict in it"}
        head = value[0]
        report = ""
        if len(value) > 1 and value[1] is not None:
            report = str(value[1])
        if isinstance(head, bool):
            return {"ok": head, "report": report}
        if isinstance(head, int):
            return {"ok": head == 0, "report": report or f"exit code {head}"}
        return {"ok": None,
                "report": f"selftest() returned {type(head).__name__} as its "
                          f"verdict; expected bool or int"}
    return {"ok": None,
            "report": f"selftest() returned {type(value).__name__}; expected "
                      f"int, bool or (bool, str)"}


def _emit(payload: Dict[str, Any]) -> None:
    # Bounded: a selftest that prints a novel into its report must not turn
    # the registry's own output into one.
    report = str(payload.get("report") or "")
    if len(report) > 4000:
        payload["report"] = report[:4000] + " ... [truncated]"
    sys.stdout.write(FRAME + " " + json.dumps(payload) + "\n")
    sys.stdout.flush()


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        _emit({"module": None, "ok": None,
               "report": "usage: python -m intentops_core.selftests._child "
                         "<dotted.module>"})
        return 2
    name = args[0]
    payload: Dict[str, Any] = {"module": name}
    try:
        module = importlib.import_module(name)
    except BaseException:  # noqa: BLE001 - an import failure is a finding
        payload.update(ok=None,
                       report="import failed:\n"
                              + traceback.format_exc(limit=8))
        _emit(payload)
        return 1
    fn = getattr(module, "selftest", None)
    if not callable(fn):
        payload.update(ok=None,
                       report="the module exposes no callable selftest()")
        _emit(payload)
        return 1
    try:
        value = fn()
    except SystemExit as exc:  # a selftest that exits is still answering
        code = exc.code
        value = 0 if code is None else code
    except BaseException:  # noqa: BLE001 - an instrument that raises is ERROR
        payload.update(ok=None,
                       report="selftest() raised:\n"
                              + traceback.format_exc(limit=8))
        _emit(payload)
        return 1
    payload.update(interpret(value))
    _emit(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
