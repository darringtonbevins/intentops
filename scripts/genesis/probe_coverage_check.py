#!/usr/bin/env python
"""Probe coverage -- does every organ this node carries have a probe row?

PURPOSE
    The house rule is *new organ, new probe, same session*. A rule enforced by
    memory is enforced by nobody: six organs were born into the reference
    estate over a fortnight while its own census sat frozen, and nothing
    noticed because nothing counted. This is the counter.

    Two populations, derived rather than listed:

    1. **Birth organs** -- every entry in ``genesis.organs.BIRTH_ORGANS``.
    2. **Packages that own something** -- every package under
       ``intentops_core/`` that has a STORE (a ``.intentops/`` path, a
       ``*_RELPATH`` constant, or a ``StoreLock``) or a CLI VERB (a
       ``main(argv)`` entrypoint, a ``cli``/``__main__`` module, or a verb
       registered in ``intentops_core/cli.py``). A package with neither owns
       nothing a fresh window has to find, and is outside the population --
       which is a declaration, not a silent filter: it is reported.

    Coverage is DECLARED, never guessed. A probe row carries
    ``covers: [<member id>, ...]``, and a member is covered iff some row names
    it. Matching a probe to an organ by substring would false-pass on the first
    short token that happened to appear in a question, which is the same blind
    spot the probe runner publishes about its own matching -- once is enough.

    A member that legitimately owes no probe goes in ``coverage_exemptions:``
    WITH A REASON. An undeclared, unexempt member is exit 1. There is no
    "probably fine" state.

WRITE MODEL
    None. This is a pure read over ``config/genesis-probes.yaml`` and the
    package tree, and it writes nothing anywhere.

BLIND SPOTS
    - It proves a probe row NAMES an organ. It does not prove the row asks a
      useful question about it, and it cannot: a row that covers an organ and
      probes for the string "the" would pass here and tell a newborn nothing.
    - The store/CLI heuristics read module TEXT. A package that reaches a store
      only through another package's helper, with no local marker, reads as
      owning nothing. That direction is the safe one -- it shrinks the
      population rather than the coverage -- but it is a floor, not a census.
    - Saddle packages are outside the population. They are separate
      distributions with their own contract tests; the one saddle organ that
      lands inside a node's ``.intentops/`` tree is named in the exemptions
      with the probe that covers it.
    - It does not run the probes. ``self_probe`` answers whether a probe
      PASSES; this answers whether one EXISTS.

Usage:
    python scripts/genesis/probe_coverage_check.py
    python scripts/genesis/probe_coverage_check.py --json
    python scripts/genesis/probe_coverage_check.py --selftest
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[2]
_CORE = _REPO / "packages" / "intentops-core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import yaml  # noqa: E402

PROBES_RELPATH = Path("config") / "genesis-probes.yaml"
CORE_RELPATH = Path("packages") / "intentops-core" / "intentops_core"

#: Markers that say a package owns a store.
_STORE_MARKERS = (re.compile(r"\.intentops[/\\]"),
                  re.compile(r"^[A-Z_]*RELPATH\s*=", re.MULTILINE),
                  re.compile(r"\bStoreLock\b"))
#: Markers that say a package exposes a CLI verb of its own.
_CLI_MARKERS = (re.compile(r"^def main\(", re.MULTILINE),
                re.compile(r"\badd_argument\b"))
_VERB_RE = re.compile(r"sub\.add_parser\(\s*[\"']([a-z0-9-]+)[\"']")


@dataclass
class Member:
    """One thing that may owe a probe row."""

    id: str
    population: str          # "birth-organ" | "package"
    why: str                 # why it is in the population

    def to_row(self) -> Dict[str, str]:
        return {"id": self.id, "population": self.population, "why": self.why}


@dataclass
class Coverage:
    members: List[Member] = field(default_factory=list)
    covered: Dict[str, List[str]] = field(default_factory=dict)   # member -> probe ids
    exempt: Dict[str, str] = field(default_factory=dict)          # member -> reason
    missing: List[Member] = field(default_factory=list)
    orphan_covers: Dict[str, List[str]] = field(default_factory=dict)  # token -> probes
    outside: List[Tuple[str, str]] = field(default_factory=list)  # package, why not
    errors: List[str] = field(default_factory=list)

    @property
    def owed(self) -> int:
        return len([m for m in self.members if m.id not in self.exempt])

    @property
    def satisfied(self) -> int:
        return len([m for m in self.members
                    if m.id not in self.exempt and m.id in self.covered])

    @property
    def ok(self) -> bool:
        return not self.missing and not self.errors and not self.orphan_covers


# --------------------------------------------------------------------------
# population
# --------------------------------------------------------------------------


def birth_organ_ids() -> List[str]:
    """Imported, never re-parsed: the organ list has exactly one definition."""
    from intentops_core.genesis.organs import BIRTH_ORGANS

    return [organ.id for organ in BIRTH_ORGANS]


def registered_verbs(cli_path: Path) -> Set[str]:
    if not cli_path.is_file():
        return set()
    return set(_VERB_RE.findall(cli_path.read_text(encoding="utf-8",
                                                   errors="replace")))


def package_members(core_dir: Path) -> Tuple[List[Member], List[Tuple[str, str]]]:
    """``([members], [(package, why it is outside)])``."""
    members: List[Member] = []
    outside: List[Tuple[str, str]] = []
    verbs = registered_verbs(core_dir / "cli.py")
    for pkg in sorted(p for p in core_dir.iterdir()
                      if p.is_dir() and (p / "__init__.py").is_file()):
        name = pkg.name
        if name.startswith("_") or name == "__pycache__":
            continue
        text = ""
        for module in sorted(pkg.rglob("*.py")):
            if "__pycache__" in module.parts:
                continue
            text += module.read_text(encoding="utf-8", errors="replace") + "\n"
        has_store = any(rx.search(text) for rx in _STORE_MARKERS)
        has_cli = (any(rx.search(text) for rx in _CLI_MARKERS)
                   or (pkg / "cli.py").is_file()
                   or (pkg / "__main__.py").is_file()
                   or name in verbs)
        if has_store or has_cli:
            why = " and ".join(
                [w for w, on in (("a store", has_store), ("a CLI verb", has_cli))
                 if on])
            members.append(Member(id=name, population="package",
                                  why=f"owns {why}"))
        else:
            outside.append((name, "owns no store and exposes no CLI verb"))
    return members, outside


# --------------------------------------------------------------------------
# the check
# --------------------------------------------------------------------------


def load_probes(path: Path) -> Dict[str, Any]:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError(f"{path}: probe suite root must be a mapping")
    return doc


def evaluate(organs: Sequence[str], packages: Sequence[Member],
             doc: Dict[str, Any]) -> Coverage:
    """Pure over its inputs, so the selftest can plant an unprobed organ."""
    cov = Coverage()
    # A name can be BOTH a birth organ and a package (``wisdom`` is the store
    # and the code that writes it). They are one thing to probe, so they are
    # merged into one member rather than counted twice -- a denominator
    # inflated by aliases makes the coverage rate a fiction.
    merged: Dict[str, Member] = {}
    for source in ([Member(o, "birth-organ", "declared in BIRTH_ORGANS")
                    for o in organs] + list(packages)):
        seen = merged.get(source.id)
        if seen is None:
            merged[source.id] = source
        else:
            merged[source.id] = Member(
                source.id, f"{seen.population}+{source.population}",
                f"{seen.why}; {source.why}")
    cov.members = list(merged.values())
    ids = set(merged)

    probes = doc.get("probes")
    if not isinstance(probes, list) or not probes:
        cov.errors.append("the probe suite declares no probes")
        probes = []

    for probe in probes:
        if not isinstance(probe, dict):
            cov.errors.append(f"probe entry is not a mapping: {probe!r}")
            continue
        pid = str(probe.get("id") or "<unnamed>")
        covers = probe.get("covers")
        if covers is None:
            continue                       # a probe may cover nothing in particular
        if not isinstance(covers, list):
            cov.errors.append(f"{pid}: `covers` must be a list")
            continue
        for token in covers:
            token = str(token)
            if token in ids:
                cov.covered.setdefault(token, []).append(pid)
            else:
                # A `covers` naming nothing is a rename nobody finished. It is
                # an ERROR, not a shrug: it would otherwise read as coverage.
                cov.orphan_covers.setdefault(token, []).append(pid)

    exemptions = doc.get("coverage_exemptions") or {}
    if not isinstance(exemptions, dict):
        cov.errors.append("`coverage_exemptions` must be a mapping of "
                          "member id -> reason")
        exemptions = {}
    for member, reason in exemptions.items():
        member = str(member)
        if not str(reason or "").strip():
            cov.errors.append(
                f"exemption for {member!r} carries no reason. 'Will do it "
                "later' is a deferral, not a reason")
            continue
        if member not in ids:
            cov.errors.append(
                f"exemption for {member!r} names nothing in the population")
            continue
        cov.exempt[member] = str(reason)

    for member in cov.members:
        if member.id in cov.exempt or member.id in cov.covered:
            continue
        cov.missing.append(member)
    return cov


def run(repo_root: Path) -> Coverage:
    organs = birth_organ_ids()
    packages, outside = package_members(repo_root / CORE_RELPATH)
    cov = evaluate(organs, packages, load_probes(repo_root / PROBES_RELPATH))
    cov.outside = outside
    return cov


def render(cov: Coverage) -> str:
    L = ["PROBE COVERAGE -- new organ, new probe", ""]
    L.append(f"  members owing a probe   {cov.owed}")
    L.append(f"  satisfied               {cov.satisfied}/{cov.owed}")
    L.append(f"  exempt (with a reason)  {len(cov.exempt)}")
    L.append(f"  outside the population  {len(cov.outside)}")
    L.append("")
    if cov.outside:
        L.append("OUTSIDE THE POPULATION (reported, never silently filtered):")
        for name, why in cov.outside:
            L.append(f"  - {name}: {why}")
        L.append("")
    if cov.exempt:
        L.append("EXEMPT:")
        for member, reason in sorted(cov.exempt.items()):
            L.append(f"  - {member}: {reason}")
        L.append("")
    if cov.orphan_covers:
        L.append("ORPHAN `covers` (a probe claims to cover something that does "
                 "not exist):")
        for token, pids in sorted(cov.orphan_covers.items()):
            L.append(f"  ! {token} claimed by {', '.join(pids)}")
        L.append("")
    if cov.errors:
        L.append("ERRORS:")
        for err in cov.errors:
            L.append(f"  ! {err}")
        L.append("")
    if cov.missing:
        L.append(f"MISSING A PROBE ROW ({len(cov.missing)}):")
        for member in cov.missing:
            L.append(f"  ? {member.id} [{member.population}] -- {member.why}")
        L.append("")
        L.append("Add a row to config/genesis-probes.yaml carrying "
                 "`covers: [<id>]`, or an entry under `coverage_exemptions:` "
                 "with a reason.")
    else:
        L.append("every member owing a probe has one")
    return "\n".join(L)


# --------------------------------------------------------------------------
# selftest -- plant an unprobed organ and prove the checker catches it
# --------------------------------------------------------------------------


def selftest() -> Tuple[bool, str]:
    failures: List[str] = []

    def check(label: str, cond: bool) -> None:
        if not cond:
            failures.append(label)

    good = {"probes": [{"id": "P-a", "covers": ["alpha"]},
                       {"id": "P-b", "covers": ["beta"]}],
            "coverage_exemptions": {"gamma": "it is a lock directory nobody "
                                            "reads; the store beside it is "
                                            "probed instead"}}
    pkgs = [Member("beta", "package", "owns a store")]

    cov = evaluate(["alpha", "gamma"], pkgs, good)
    check(f"a fully covered suite reported missing: {[m.id for m in cov.missing]}",
          cov.ok and not cov.missing)
    check("the exemption was not counted", cov.exempt == {"gamma": cov.exempt.get("gamma", "")})
    check(f"owed/satisfied wrong: {cov.satisfied}/{cov.owed}",
          cov.owed == 2 and cov.satisfied == 2)

    # THE POINT OF THE SELFTEST: plant an organ nobody probed.
    cov = evaluate(["alpha", "gamma", "delta"], pkgs, good)
    check("an unprobed organ was NOT caught",
          [m.id for m in cov.missing] == ["delta"] and not cov.ok)

    # an unprobed PACKAGE is caught the same way
    cov = evaluate(["alpha", "gamma"],
                   pkgs + [Member("epsilon", "package", "owns a CLI verb")], good)
    check("an unprobed package was NOT caught",
          [m.id for m in cov.missing] == ["epsilon"])

    # an exemption with no reason is an error, not an exemption
    cov = evaluate(["alpha"], [], {"probes": [{"id": "P-a", "covers": ["alpha"]}],
                                   "coverage_exemptions": {"alpha": "  "}})
    check("a blank exemption reason was accepted",
          any("no reason" in e for e in cov.errors))

    # an exemption for a member that does not exist is an error
    cov = evaluate(["alpha"], [], {"probes": [{"id": "P-a", "covers": ["alpha"]}],
                                   "coverage_exemptions": {"zeta": "gone"}})
    check("an exemption naming nothing was accepted",
          any("names nothing" in e for e in cov.errors))

    # a `covers` token naming nothing is an orphan, never silent coverage
    cov = evaluate(["alpha"], [], {"probes": [{"id": "P-a",
                                               "covers": ["alpha", "ghost"]}]})
    check("an orphan covers token was accepted",
          "ghost" in cov.orphan_covers and not cov.ok)

    # an empty suite is an error, never a clean sweep of nothing
    cov = evaluate(["alpha"], [], {"probes": []})
    check("an empty probe suite read clean",
          bool(cov.errors) and not cov.ok)

    ok = not failures
    return ok, ("a planted unprobed organ and an unprobed package were both "
                "caught, a blank and a dangling exemption errored, an orphan "
                "covers token was refused, and an empty suite did not read "
                "clean" if ok else "; ".join(failures))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="every organ owes a probe row -- does it have one?")
    ap.add_argument("--repo-root", default=str(_REPO))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        ok, msg = selftest()
        print(f"{'PASS' if ok else 'FAIL'} probe_coverage_check selftest: {msg}")
        return 0 if ok else 1

    cov = run(Path(args.repo_root))
    if args.json:
        print(json.dumps({
            "owed": cov.owed, "satisfied": cov.satisfied,
            "exempt": cov.exempt,
            "outside": [{"package": n, "why": w} for n, w in cov.outside],
            "missing": [m.to_row() for m in cov.missing],
            "orphan_covers": cov.orphan_covers,
            "errors": cov.errors,
            "covered": cov.covered,
        }, indent=2))
    else:
        print(render(cov))
    return 0 if cov.ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
