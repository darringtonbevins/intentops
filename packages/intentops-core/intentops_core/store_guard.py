"""store_guard -- shared write discipline for multi-writer stores.

PURPOSE
    Give every store in this package one honest way to be written by more than
    one process: a kernel-released OS file lock, a fresh read INSIDE the lock,
    and an atomic replace. The recurring data-loss class this retires is the
    shared whiteboard -- a process reads a whole store file, mutates its copy,
    and rewrites the whole file, so two overlapping writers end as
    last-write-wins and one edit dies with no signal anywhere.

WRITE MODEL
    This module is the primitive, not a store; it owns no file of its own. It
    implements two of the three sanctioned write models:

      * ``locked_rmw``  -- locked fresh-read read-modify-write + atomic replace
                           (right for low-contention whole-file stores)
      * ``StoreLock``   -- the lock an append-only journal holds while it
                           appends (right for event journals; state is then a
                           pure fold of the journal)

    The third model -- per-item files, collision-safe by construction -- needs
    no primitive at all and is preferred for new stores whose records are
    independent.

BLIND SPOTS
    * The lock is advisory between cooperating processes only. A writer that
      does not take the lock is not stopped by it.
    * ``StoreLock`` refuses any path not ending in ``.lock`` because it
      TRUNCATES the path it is handed. That guard cannot fire for a file whose
      content legitimately lives at a ``.lock`` name -- such a file would still
      be truncated, and nothing here can tell the difference by construction.
      (Observed cost of the missing guard elsewhere: a 183KB append-only ledger
      cut to 11 bytes because a store path was passed where a lock path was
      expected.)
    * On a platform with neither ``msvcrt`` nor ``fcntl`` the backend degrades
      to best-effort: the context manager still serialises nothing. This is
      reported by ``lock_backend()`` and by ``--selftest``; it is never silent.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "StoreLock",
    "StoreLockTimeout",
    "atomic_replace",
    "lock_backend",
    "lock_for",
    "locked_rmw",
]

# --- OS lock backend --------------------------------------------------------
try:
    import msvcrt  # Windows
    _LOCK_BACKEND = "msvcrt"
except ImportError:  # pragma: no cover - platform dependent
    msvcrt = None  # type: ignore[assignment]
    try:
        import fcntl  # POSIX
        _LOCK_BACKEND = "fcntl"
    except ImportError:  # pragma: no cover
        fcntl = None  # type: ignore[assignment]
        _LOCK_BACKEND = "none"


def lock_backend() -> str:
    """Which OS lock backend is in force: ``msvcrt`` | ``fcntl`` | ``none``.

    ``none`` means locking is a no-op on this platform. Callers that care must
    surface that rather than assume serialisation they are not getting.
    """
    return _LOCK_BACKEND


class StoreLockTimeout(RuntimeError):
    """Raised when the store lock stays held past the acquire timeout."""


class StoreLock:
    """Blocking-with-timeout OS-level exclusive lock on a sibling .lock file.

    Store writers WAIT briefly rather than failing: a state save should queue
    behind a concurrent writer. The kernel releases the lock on handle close or
    process death, so there is no stale-lock reclaim problem.
    """

    def __init__(self, path: Path, timeout_s: float = 10.0, retry_s: float = 0.05) -> None:
        self.path = Path(path)
        # Fail CLOSED on a non-lock path. __enter__ opens this path "a+",
        # truncates it and stamps a pid line -- so handing it a STORE instead of
        # the store's sibling .lock destroys the store, and does so before the
        # caller's first read. Raising in __init__ rather than __enter__
        # guarantees the refusal lands before any handle opens.
        if self.path.suffix != ".lock":
            raise ValueError(
                f"StoreLock path must be a .lock file, got {self.path.name!r}. "
                "StoreLock TRUNCATES the path it is given -- passing a store "
                "would destroy it. Use lock_for(store_path) or "
                "store_path.with_suffix('.lock')."
            )
        self.timeout_s = timeout_s
        self.retry_s = retry_s
        self._fh: Any = None

    def _try_acquire(self) -> bool:
        self._fh = open(self.path, "a+")
        try:
            if _LOCK_BACKEND == "msvcrt":
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
            elif _LOCK_BACKEND == "fcntl":
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            # "none": best-effort, no lock available on this platform
            return True
        except OSError:
            self._fh.close()
            self._fh = None
            return False

    def __enter__(self) -> "StoreLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_s
        while True:
            if self._try_acquire():
                try:
                    self._fh.seek(0)
                    self._fh.truncate()
                    self._fh.write(f"pid={os.getpid()}\n")
                    self._fh.flush()
                except Exception:  # noqa: BLE001 - stamping is diagnostic only
                    logger.debug("store_guard: lock stamp failed (non-fatal)")
                return self
            if time.monotonic() >= deadline:
                raise StoreLockTimeout(
                    f"store lock held past {self.timeout_s}s: {self.path}"
                )
            time.sleep(self.retry_s)

    def __exit__(self, *exc: Any) -> bool:
        if self._fh is not None:
            try:
                if _LOCK_BACKEND == "msvcrt":
                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                elif _LOCK_BACKEND == "fcntl":
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            except Exception:  # noqa: BLE001 - the kernel releases regardless
                logger.debug("store_guard: unlock failed (kernel will release)")
            self._fh.close()
            self._fh = None
        return False


def lock_for(store_path: Path) -> Path:
    """Canonical sibling lock path for a store file."""
    p = Path(store_path)
    return p.with_name(p.name + ".lock")


def atomic_replace(store_path: Path, text: str, encoding: str = "utf-8") -> None:
    """Write text to a temp file in the same directory, then os.replace.

    Same-directory temp guarantees same-volume rename atomicity; a reader never
    observes a half-written store.
    """
    store_path = Path(store_path)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=store_path.name + ".", suffix=".tmp", dir=str(store_path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, store_path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def locked_rmw(
    store_path: Path,
    transform: Callable[[Optional[Any]], Any],
    *,
    loader: Callable[[Path], Any],
    dumper: Callable[[Any], str],
    lock_path: Optional[Path] = None,
    timeout_s: float = 10.0,
) -> Any:
    """Fresh-read read-modify-write under an exclusive lock, atomic replace.

    ``transform`` receives the CURRENT on-disk value (None when the store does
    not exist yet) and returns the full new value. Reading inside the lock is
    the point: a value read before acquisition may already be stale.
    """
    store_path = Path(store_path)
    with StoreLock(lock_path or lock_for(store_path), timeout_s=timeout_s):
        current = loader(store_path) if store_path.exists() else None
        updated = transform(current)
        atomic_replace(store_path, dumper(updated))
        return updated


# ---------------------------------------------------------------------------
# selftest -- a guard that has never fired is indistinguishable from a broken one
# ---------------------------------------------------------------------------


def selftest() -> int:
    """Prove the .lock refusal fires and the RMW round-trips. 0 = pass."""
    failures: list[str] = []

    # 1. the refusal fires on a store path
    try:
        StoreLock(Path("some-store.yaml"))
    except ValueError:
        pass
    else:
        failures.append("StoreLock accepted a non-.lock path -- the guard is dead")

    # 2. the refusal does NOT fire on a real lock path
    try:
        StoreLock(lock_for(Path("some-store.yaml")))
    except ValueError as exc:
        failures.append(f"StoreLock refused a valid lock path: {exc}")

    # 3. locked_rmw round-trips through a real file
    with tempfile.TemporaryDirectory() as td:
        store = Path(td) / "counter.txt"
        for _ in range(2):
            locked_rmw(
                store,
                lambda cur: str(int(cur or "0") + 1),
                loader=lambda p: p.read_text(encoding="utf-8"),
                dumper=lambda v: v,
            )
        got = store.read_text(encoding="utf-8")
        if got != "2":
            failures.append(f"locked_rmw round-trip wrong: expected '2', got {got!r}")

    print(f"store_guard selftest: lock backend = {lock_backend()}")
    if lock_backend() == "none":
        print("  WARN: no OS lock backend on this platform -- locking is best-effort")
    for f in failures:
        print(f"  FAIL: {f}")
    print(f"  {'PASS' if not failures else 'FAIL'} ({len(failures)} failure(s))")
    return 1 if failures else 0


def _main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="store write discipline primitives")
    ap.add_argument("--selftest", action="store_true",
                    help="prove the .lock refusal fires and the RMW round-trips")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    ap.print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
