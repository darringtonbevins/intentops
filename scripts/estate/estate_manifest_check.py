#!/usr/bin/env python
"""Validate the six blank-estate manifests. Code rung, no model in the path.

PURPOSE
    Answer one question mechanically: are this node's estate manifests
    conformant, and if not, exactly which field in which file is wrong and what
    is the remedy. Run at G3 (ORGANS) and from checklists.

    It is deliberately NOT a blocking pre-tool hook. A contract check must never
    be able to wedge an unrelated session; the precedent is the wish-register
    validator, which gates only where a checklist invokes it.

WRITE MODEL
    Not a store. This script writes nothing outside a temporary directory it
    creates and removes during ``--selftest``.

EXIT CODES
    0  clean (warnings may still have been printed -- a warning is a recorded
       finding, never a silent drop, and never a failure)
    1  at least one violation, or a selftest case that failed to fire

BLIND SPOTS
    - It validates SHAPE and VOCABULARY, never truth. A manifest can be
      perfectly conformant and describe a world that moved last week.
    - ``--selftest`` proves each violation class CAN fire against a mutated copy
      of the shipped estate. It does not prove the absence of a class nobody
      thought to construct; the case list is the coverage claim and it is
      printed, so what is untested is readable rather than assumed.
    - Cross-file ``estate:`` references go unchecked when ESTATE-MAP.yaml itself
      failed to load; the loader documents that and does not invent a verdict.
"""

from __future__ import annotations

import argparse
import copy
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PACKAGE_ROOT = _REPO_ROOT / "packages" / "intentops-core"
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

from intentops_core.estate.manifests import (  # noqa: E402
    MANIFEST_FILENAMES,
    ManifestError,
    ManifestWarning,
    collect_problems,
)

_OPERATOR_ROOT = "aaaabbbbccccdddd"
_FOREIGN_ROOT = "ffff0000ffff0000"


# --------------------------------------------------------------------------
# Fixtures used only by --selftest
# --------------------------------------------------------------------------


def _estate_entry() -> dict[str, Any]:
    return {
        "id": "e1",
        "label": "First estate",
        "kind": "internal",
        "autonomy": "propose",
        "keywords": ["first"],
        "ruled_by": "the operator, in the founding conversation",
        "as_of": "2026-09-06",
    }


def _asset_entry() -> dict[str, Any]:
    return {
        "id": "a1",
        "name": "A repository",
        "kind": "repo",
        "estate": "e1",
        "owner": "the operator",
        "sensitivity": "public",
        "location": "file:///workspace/a1",
        "as_of": "2026-09-06",
        "evidence": "cloned locally",
    }


def _resource_entry() -> dict[str, Any]:
    return {
        "id": "r1",
        "kind": "compute",
        "limit_shape": "none observed",
        "telemetry_source": "the local runtime's own request log",
        "max_concurrent": 1,
        "kill_switch": "stop the process",
        "data_fence": "local only",
        "as_of": "2026-09-06",
    }


def _dependency_entry() -> dict[str, Any]:
    return {
        "id": "d1",
        "kind": "service",
        "depends_on": [],
        "health_probe": "GET http://127.0.0.1:1/health returns 200",
        "criticality": "required",
        "fallback": "none",
        "as_of": "2026-09-06",
    }


def _grant_entry() -> dict[str, Any]:
    return {
        "id": "g1",
        "granted_by": _OPERATOR_ROOT,
        "scope": "read the node's own workspace",
        "tier_ceiling": "T0",
        "estate": "e1",
        "reaches_reality": False,
        "expires_at": "2026-12-31",
        "reopens_when": "",
        "revoked_at": None,
        "evidence": "founding conversation, 2026-09-06",
    }


def _capability_entry() -> dict[str, Any]:
    return {"id": "c1", "kind": "organ", "as_of": "2026-09-06"}


def _read(path: Path) -> Any:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _write(path: Path, data: Any) -> None:
    import yaml

    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=False),
        encoding="utf-8",
    )


def _mutate(root: Path, filename: str, fn: Callable[[dict[str, Any]], None]) -> None:
    path = root / filename
    data = _read(path)
    fn(data)
    _write(path, data)


def _seed_valid_entries(root: Path) -> None:
    """Put one valid entry in every manifest, so a mutation has something to break."""
    _mutate(root, "ESTATE-MAP.yaml", lambda d: d.__setitem__("entries", [_estate_entry()]))
    _mutate(root, "ASSETS.yaml", lambda d: d.__setitem__("entries", [_asset_entry()]))
    _mutate(root, "RESOURCES.yaml", lambda d: d.__setitem__("entries", [_resource_entry()]))
    _mutate(root, "DEPENDENCIES.yaml", lambda d: d.__setitem__("entries", [_dependency_entry()]))
    _mutate(root, "GRANTS.yaml", lambda d: d.__setitem__("entries", [_grant_entry()]))
    _mutate(root, "CAPABILITIES.yaml", lambda d: d.__setitem__("population", [_capability_entry()]))


# --------------------------------------------------------------------------
# The selftest case list. Each case says what it EXPECTS, and a case that does
# not get what it expects is a failure -- including the two that expect a clean
# load, because a check that fires on everything is as useless as one that
# never fires.
# --------------------------------------------------------------------------


def _drop(container: dict[str, Any], key: str) -> None:
    container.pop(key, None)


SELFTEST_CASES: list[tuple[str, str, Callable[[Path], None]]] = [
    (
        "shipped-blank-estate-is-valid",
        "clean",
        lambda root: None,
    ),
    (
        "seeded-estate-is-valid",
        "clean",
        _seed_valid_entries,
    ),
    (
        "missing-manifest-file",
        "problem",
        lambda root: (root / "ASSETS.yaml").unlink(),
    ),
    (
        "unknown-schema",
        "problem",
        lambda root: _mutate(root, "ASSETS.yaml", lambda d: d.__setitem__("schema", "estate-assets/v99")),
    ),
    (
        "missing-schema",
        "problem",
        lambda root: _mutate(root, "ASSETS.yaml", lambda d: _drop(d, "schema")),
    ),
    (
        "missing-as-of",
        "problem",
        lambda root: _mutate(root, "RESOURCES.yaml", lambda d: _drop(d, "as_of")),
    ),
    (
        "missing-entries-key",
        "problem",
        lambda root: _mutate(root, "ASSETS.yaml", lambda d: _drop(d, "entries")),
    ),
    (
        "entries-null-is-not-empty",
        "problem",
        lambda root: _mutate(root, "ASSETS.yaml", lambda d: d.__setitem__("entries", None)),
    ),
    (
        "undeclared-vocabulary-value",
        "problem",
        lambda root: _seed_then(root, "ASSETS.yaml", "entries", {"kind": "spaceship"}),
    ),
    (
        "undeclared-autonomy-value",
        "problem",
        lambda root: _seed_then(root, "ESTATE-MAP.yaml", "entries", {"autonomy": "prod-ask"}),
    ),
    (
        "null-telemetry-source",
        "problem",
        lambda root: _seed_then(root, "RESOURCES.yaml", "entries", {"telemetry_source": None}),
    ),
    (
        "missing-max-concurrent",
        "problem",
        lambda root: _seed_drop(root, "RESOURCES.yaml", "entries", "max_concurrent"),
    ),
    (
        "missing-health-probe",
        "problem",
        lambda root: _seed_drop(root, "DEPENDENCIES.yaml", "entries", "health_probe"),
    ),
    (
        "null-health-probe-without-reason",
        "problem",
        lambda root: _seed_then(root, "DEPENDENCIES.yaml", "entries", {"health_probe": None}),
    ),
    (
        "null-health-probe-with-reason-is-unprobeable-not-dropped",
        "warning:DEPENDENCY-UNPROBEABLE",
        lambda root: _seed_then(
            root,
            "DEPENDENCIES.yaml",
            "entries",
            {
                "health_probe": None,
                "unprobeable_reason": "no endpoint exists to ask; the state is only "
                "observable by a human at the keyboard",
            },
        ),
    ),
    (
        "grant-without-expires-at",
        "problem",
        lambda root: _seed_drop(root, "GRANTS.yaml", "entries", "expires_at"),
    ),
    (
        "grant-with-null-expires-at",
        "problem",
        lambda root: _seed_then(root, "GRANTS.yaml", "entries", {"expires_at": None}),
    ),
    (
        "grant-from-foreign-root-is-ignored-not-applied",
        "warning:GRANT-FOREIGN-ROOT",
        lambda root: _seed_then(root, "GRANTS.yaml", "entries", {"granted_by": _FOREIGN_ROOT}),
    ),
    (
        "grant-with-undeclared-tier",
        "problem",
        lambda root: _seed_then(root, "GRANTS.yaml", "entries", {"tier_ceiling": "T9"}),
    ),
    (
        "capability-kind-missing",
        "problem",
        lambda root: _mutate(root, "CAPABILITIES.yaml", lambda d: d["kinds"].pop("organ", None)),
    ),
    (
        "capability-accounting-status-undeclared",
        "problem",
        lambda root: _mutate(
            root,
            "CAPABILITIES.yaml",
            lambda d: d["kinds"]["organ"]["owes"]["probe"].__setitem__("status", "maybe"),
        ),
    ),
    (
        "capability-accounting-reason-blank",
        "problem",
        lambda root: _mutate(
            root,
            "CAPABILITIES.yaml",
            lambda d: d["kinds"]["organ"]["owes"]["monitored"].__setitem__("why", "   "),
        ),
    ),
    (
        "duplicate-entry-id",
        "problem",
        lambda root: _seed_duplicate(root),
    ),
    (
        "asset-names-an-undeclared-estate",
        "problem",
        lambda root: _seed_then(root, "ASSETS.yaml", "entries", {"estate": "nowhere"}),
    ),
]


def _seed_then(root: Path, filename: str, key: str, overrides: dict[str, Any]) -> None:
    _seed_valid_entries(root)

    def apply(data: dict[str, Any]) -> None:
        data[key][0].update(overrides)

    _mutate(root, filename, apply)


def _seed_drop(root: Path, filename: str, key: str, field: str) -> None:
    _seed_valid_entries(root)

    def apply(data: dict[str, Any]) -> None:
        data[key][0].pop(field, None)

    _mutate(root, filename, apply)


def _seed_duplicate(root: Path) -> None:
    _seed_valid_entries(root)

    def apply(data: dict[str, Any]) -> None:
        data["entries"].append(copy.deepcopy(data["entries"][0]))

    _mutate(root, "ASSETS.yaml", apply)


# --------------------------------------------------------------------------
# Runners
# --------------------------------------------------------------------------


def _copy_shipped_estate(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    missing = [name for name in MANIFEST_FILENAMES if not (source / name).is_file()]
    if missing:
        raise SystemExit(
            "SELFTEST CANNOT RUN: the shipped estate is incomplete at "
            f"{source} -- missing {', '.join(missing)}.\n"
            "  remedy: run the selftest from a checkout that carries estate/, or pass "
            "--estate. A selftest with no pristine baseline proves nothing."
        )
    for name in MANIFEST_FILENAMES:
        shutil.copy2(source / name, destination / name)


def run_selftest(shipped_estate: Path) -> int:
    """Construct each violation in a temp tree and prove the check fires."""
    print(f"selftest: baseline = {shipped_estate}")
    failures: list[str] = []
    for name, expectation, mutate in SELFTEST_CASES:
        with tempfile.TemporaryDirectory(prefix="intentops-estate-selftest-") as tmp:
            work = Path(tmp) / "estate"
            _copy_shipped_estate(shipped_estate, work)
            mutate(work)
            problems, warnings, estate = collect_problems(work, _OPERATOR_ROOT)

            if expectation == "clean":
                ok = not problems
                detail = "" if ok else f"unexpected: {problems[0]}"
            elif expectation == "problem":
                ok = bool(problems)
                detail = "" if ok else "the check did NOT fire"
            elif expectation.startswith("warning:"):
                code = expectation.split(":", 1)[1]
                codes = [w.code for w in warnings]
                ok = not problems and code in codes
                if problems:
                    detail = f"expected a warning, got a HALT: {problems[0]}"
                else:
                    detail = "" if ok else f"expected warning {code}, saw {codes}"
                if ok and code == "GRANT-FOREIGN-ROOT" and estate is not None:
                    if estate.applied_grants:
                        ok = False
                        detail = "the foreign grant was APPLIED; it must raise nothing"
            else:  # pragma: no cover - programming error in the case list
                ok, detail = False, f"unknown expectation {expectation!r}"

            status = "FIRED " if ok else "MISSED"
            print(f"  [{status}] {name} (expected {expectation}) {detail}".rstrip())
            if not ok:
                failures.append(name)

    total = len(SELFTEST_CASES)
    print(f"selftest: {total - len(failures)}/{total} cases behaved as declared")
    if failures:
        print("SELFTEST FAILED: " + ", ".join(failures))
        return 1
    print("SELFTEST PASS -- every violation class above can fire, and the two "
          "clean cases still load")
    return 0


def _print_report(
    estate_dir: Path,
    problems: list[ManifestError],
    warnings: list[ManifestWarning],
) -> int:
    print(f"estate: {estate_dir}")
    for warning in warnings:
        print(f"  {warning}")
    if not problems:
        print(f"  manifests {len(MANIFEST_FILENAMES)}/{len(MANIFEST_FILENAMES)} conformant; "
              f"warnings {len(warnings)}")
        print("VERDICT: CONFORMANT")
        return 0
    for problem in problems:
        print(f"  {problem}")
    print(f"  violations {len(problems)}; warnings {len(warnings)}")
    print("VERDICT: NOT CONFORMANT")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the six blank-estate manifests (Code rung, no model)."
    )
    parser.add_argument(
        "--estate",
        type=Path,
        default=_REPO_ROOT / "estate",
        help="the estate directory to check (default: <repo>/estate)",
    )
    parser.add_argument(
        "--operator-root",
        default=None,
        help=(
            "this node's operator-root key_id. Without it no grant is applied and "
            "every grant is reported as GRANT-NO-OPERATOR-ROOT"
        ),
    )
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="construct each violation in a temp tree and prove this check fires",
    )
    args = parser.parse_args(argv)

    if args.selftest:
        return run_selftest(args.estate)

    problems, warnings, _estate = collect_problems(args.estate, args.operator_root)
    return _print_report(args.estate, problems, warnings)


if __name__ == "__main__":
    raise SystemExit(main())
