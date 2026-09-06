"""``python -m intentops_gateway`` -- the same entry point as the console script."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
