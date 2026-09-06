"""``python -m intentops_core.loops`` -- the loop surface without the node CLI.

PURPOSE. A node whose top-level CLI is unavailable (a partial install, a
different entry point) can still ask the two questions that matter: is the
schedule conformant, and what would this charter render to. Nothing here
decides anything; it delegates to :mod:`intentops_core.loops.cli`.

WRITE MODEL. None.

BLIND SPOTS. Exit code 2 means "the verb was not understood", never "clean".
"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":  # pragma: no cover - process entry point
    sys.exit(main())
