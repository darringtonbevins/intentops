"""Provider-neutral routing policy -- a file that decides, not a client that calls.

PURPOSE
    Answer one question deterministically: given the SHAPE of a piece of work
    and the ESTATE RING it belongs to, which pool should serve it, at which
    tier, with which effort and cache TTL -- and may it cross to that pool at
    all.

    The whole point is that the answer is provider-neutral. This module names
    no vendor, no product and no model id; it reads ``config/routing-policy.yaml``,
    whose vocabularies are all closed, and returns an :class:`Assignment`. It
    opens no socket and imports no provider SDK. A node that runs entirely on
    its own machine and a node drawing on four providers read the same policy.

    The separation is deliberate and is the lesson this module was built from:
    a provider-neutral router that no caller uses is indistinguishable from no
    router at all. So the seed ships this with exactly one caller wired --
    ``intentops route`` -- rather than shipping a larger design nothing
    exercises.

WRITE MODEL
    None. This module holds no store: it reads a reviewed config file and
    returns a value. It counts nothing in flight, appends nothing, and cannot
    lose anything. A scheduler that wants to honour ``max_concurrent`` keeps
    that ledger itself, under its own declared write model.

BLIND SPOTS
    * The fence classifies on a pool's DECLARED provider class. Nothing here
      verifies that a pool declared ``local`` is local. A mis-declared pool
      defeats the fence silently and no test in this repository can catch it.
    * The estate ring is an INPUT. This module does not decide which ring work
      belongs to; ``intentops_core.estate_classifier`` answers that, with its
      own blind spots, and ``unknown`` never reaches here because it is omitted
      from the ring vocabulary rather than defaulted.
    * PERMITTED means the fence did not refuse. It says nothing about whether
      the pool is reachable, funded or awake -- there is no probe here, by
      design, because a probe would make this impure and untestable offline.
    * Preference order is the file's order. That makes resolution deterministic
      and it also means a pool's position is policy: reordering ``entries``
      changes answers, and the reviewer of that commit is the only check.
    * A refusal stays in the population. ``resolve`` never raises for "no pool"
      or "fenced out": it returns an Assignment carrying the verdict and a
      reason, because a routing decision that vanishes is a routing decision
      nobody can count.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

try:  # pragma: no cover - exercised by every real run
    import yaml
except ImportError as exc:  # pragma: no cover - dependency is declared
    raise ImportError(
        "routing policy needs pyyaml; it is a declared dependency of "
        "intentops-core") from exc

__all__ = [
    "Assignment",
    "Policy",
    "PolicyError",
    "Pool",
    "SCHEMA",
    "DEFAULT_POLICY_PATH",
    "FENCE_PERMITTED",
    "FENCE_REFUSED",
    "FENCE_UNMET",
    "load_policy",
    "parse_policy",
    "resolve",
    "selftest",
    "main",
]

SCHEMA = "routing-policy/v1"

#: Repo-relative default. ``<repo>/packages/intentops-core/intentops_core/routing``
DEFAULT_POLICY_PATH = (
    Path(__file__).resolve().parents[4] / "config" / "routing-policy.yaml")

#: The three fence verdicts. Every resolve returns exactly one of them, and
#: the two negative ones are distinct on purpose: UNMET means nothing could
#: have served this shape at all, REFUSED means something could and the ring
#: forbade it. Collapsing them would hide a fence behind an empty registry.
FENCE_PERMITTED = "permitted"
FENCE_REFUSED = "refused"
FENCE_UNMET = "unmet"

#: Pool fields with no default. A missing one HALTs.
_POOL_REQUIRED: Tuple[str, ...] = (
    "id", "provider_class", "families", "status", "max_concurrent",
    "telemetry_source")

#: Top-level sections with no default.
_REQUIRED_SECTIONS: Tuple[str, ...] = (
    "provider_classes", "model_families", "efforts", "cache_ttls",
    "telemetry_sources", "estate_rings", "tiers", "task_shapes", "fence",
    "pools", "statuses")

_UNMETERED = "none"


class PolicyError(RuntimeError):
    """A halt. The policy could not be understood, so nothing is routed.

    Raised for an undeclared vocabulary value, a missing load-bearing field, a
    ring with no fence rule, or an unknown task shape at resolve time. It is
    never raised for "no pool available" -- that is an answer, not a defect,
    and it comes back as an Assignment.
    """


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Pool:
    """One declared source of model capacity. Provider-agnostic by construction."""

    id: str
    provider_class: str
    families: Tuple[str, ...]
    status: str
    max_concurrent: int
    telemetry_source: str
    note: str = ""

    @property
    def unmetered(self) -> bool:
        return self.telemetry_source == _UNMETERED


@dataclass(frozen=True)
class Assignment:
    """The whole answer, including the negative ones.

    ``pool_id`` is ``None`` whenever ``fence_verdict`` is not ``permitted``.
    Tier, effort and TTL are still populated in that case: they say what the
    work NEEDED, which is what a reader has to know to fix the gap.
    """

    task_shape: str
    estate_ring: str
    tier: str
    families: Tuple[str, ...]
    effort: str
    cache_ttl: str
    cache_ttl_seconds: int
    fence_verdict: str
    allowed_provider_classes: Tuple[str, ...]
    pool_id: Optional[str] = None
    provider_class: Optional[str] = None
    family: Optional[str] = None
    fenced_out: Tuple[str, ...] = ()
    rules_applied: Tuple[str, ...] = ()
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_shape": self.task_shape,
            "estate_ring": self.estate_ring,
            "tier": self.tier,
            "families": list(self.families),
            "effort": self.effort,
            "cache_ttl": self.cache_ttl,
            "cache_ttl_seconds": self.cache_ttl_seconds,
            "fence_verdict": self.fence_verdict,
            "allowed_provider_classes": list(self.allowed_provider_classes),
            "pool_id": self.pool_id,
            "provider_class": self.provider_class,
            "family": self.family,
            "fenced_out": list(self.fenced_out),
            "rules_applied": list(self.rules_applied),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Policy:
    """A validated policy document. Every vocabulary in it is closed."""

    source: str
    provider_classes: Tuple[str, ...]
    model_families: Tuple[str, ...]
    efforts: Tuple[str, ...]
    cache_ttls: Dict[str, int]
    telemetry_sources: Tuple[str, ...]
    estate_rings: Tuple[str, ...]
    statuses: Tuple[str, ...]
    tiers: Dict[str, Dict[str, Any]]
    task_shapes: Dict[str, Dict[str, Any]]
    egress: Dict[str, Tuple[str, ...]]
    pools: Tuple[Pool, ...]
    example_loaded: bool = False

    @property
    def unmetered_pools(self) -> Tuple[str, ...]:
        """The headline. A pool nothing measures can be spent unnoticed."""
        return tuple(p.id for p in self.pools if p.unmetered)

    def resolve(self, task_shape: str, estate_ring: str) -> Assignment:
        return resolve(self, task_shape, estate_ring)


# ---------------------------------------------------------------------------
# loading -- every failure below is a HALT
# ---------------------------------------------------------------------------


def _halt(source: str, what: str) -> PolicyError:
    return PolicyError(f"{source}: {what}")


def _declared_keys(doc: Mapping[str, Any], section: str, source: str) -> Tuple[str, ...]:
    block = doc.get(section)
    if not isinstance(block, Mapping) or not block:
        raise _halt(source, f"section '{section}' is missing or empty -- an "
                            "undeclared vocabulary is a hard exit, never a default")
    return tuple(str(k) for k in block)


def _require_declared(value: Any, declared: Sequence[str], where: str,
                      source: str) -> str:
    text = str(value)
    if text not in declared:
        raise _halt(source, f"{where}: '{text}' is not declared. Declared: "
                            f"{', '.join(declared)}")
    return text


def parse_policy(doc: Mapping[str, Any], source: str = "<memory>",
                 use_example: bool = False) -> Policy:
    """Validate a policy document. Raises :class:`PolicyError` on any halt.

    ``use_example`` swaps in the fictional ``example`` block's pools and fence
    and validates them through this same function, so the shipped example can
    never drift out of schema without a test noticing.
    """
    if not isinstance(doc, Mapping):
        raise _halt(source, "policy is not a mapping")
    schema = doc.get("schema")
    if schema != SCHEMA:
        raise _halt(source, f"schema is {schema!r}, expected {SCHEMA!r}")
    for section in _REQUIRED_SECTIONS:
        if section not in doc:
            raise _halt(source, f"required section '{section}' is absent")

    provider_classes = _declared_keys(doc, "provider_classes", source)
    model_families = _declared_keys(doc, "model_families", source)
    efforts = _declared_keys(doc, "efforts", source)
    telemetry_sources = _declared_keys(doc, "telemetry_sources", source)
    estate_rings = _declared_keys(doc, "estate_rings", source)
    statuses = _declared_keys(doc, "statuses", source)

    # cache TTLs carry a load-bearing integer; a TTL with no seconds is a name.
    cache_ttls: Dict[str, int] = {}
    for name, spec in dict(doc["cache_ttls"]).items():
        if not isinstance(spec, Mapping) or "seconds" not in spec:
            raise _halt(source, f"cache_ttl '{name}' declares no 'seconds'")
        try:
            cache_ttls[str(name)] = int(spec["seconds"])
        except (TypeError, ValueError) as exc:
            raise _halt(source, f"cache_ttl '{name}' seconds is not an integer") from exc
    if not cache_ttls:
        raise _halt(source, "no cache_ttls declared")

    # tiers
    tiers: Dict[str, Dict[str, Any]] = {}
    for name, spec in dict(doc["tiers"]).items():
        if not isinstance(spec, Mapping):
            raise _halt(source, f"tier '{name}' is not a mapping")
        for key in ("families", "default_effort", "default_cache_ttl"):
            if key not in spec:
                raise _halt(source, f"tier '{name}' is missing '{key}' -- a "
                                    "load-bearing field is never defaulted")
        fams = spec["families"]
        if not isinstance(fams, list) or not fams:
            raise _halt(source, f"tier '{name}' declares no families")
        tiers[str(name)] = {
            "families": tuple(
                _require_declared(f, model_families, f"tier '{name}' family", source)
                for f in fams),
            "default_effort": _require_declared(
                spec["default_effort"], efforts, f"tier '{name}' default_effort", source),
            "default_cache_ttl": _require_declared(
                spec["default_cache_ttl"], tuple(cache_ttls),
                f"tier '{name}' default_cache_ttl", source),
            "meaning": str(spec.get("meaning", "")),
        }
    if not tiers:
        raise _halt(source, "no tiers declared")

    # task shapes
    task_shapes: Dict[str, Dict[str, Any]] = {}
    for name, spec in dict(doc["task_shapes"]).items():
        if not isinstance(spec, Mapping) or "tier" not in spec:
            raise _halt(source, f"task shape '{name}' declares no tier")
        tier = _require_declared(spec["tier"], tuple(tiers),
                                 f"task shape '{name}' tier", source)
        entry: Dict[str, Any] = {"tier": tier, "meaning": str(spec.get("meaning", ""))}
        if "effort" in spec:
            entry["effort"] = _require_declared(
                spec["effort"], efforts, f"task shape '{name}' effort", source)
        if "cache_ttl" in spec:
            entry["cache_ttl"] = _require_declared(
                spec["cache_ttl"], tuple(cache_ttls),
                f"task shape '{name}' cache_ttl", source)
        task_shapes[str(name)] = entry
    if not task_shapes:
        raise _halt(source, "no task_shapes declared")

    if use_example:
        example = doc.get("example")
        if not isinstance(example, Mapping):
            raise _halt(source, "example block requested but absent or malformed")
        for key in ("pools", "fence"):
            if key not in example:
                raise _halt(source, f"example block declares no '{key}'")
        fence_block, pools_block = example["fence"], example["pools"]
    else:
        fence_block, pools_block = doc["fence"], doc["pools"]

    # fence -- every declared ring needs a rule, and no rule may name an
    # undeclared ring or class. A hole in a fence reads as a fence.
    if not isinstance(fence_block, Mapping) or "egress" not in fence_block:
        raise _halt(source, "fence declares no egress rules")
    egress: Dict[str, Tuple[str, ...]] = {}
    for rule in fence_block["egress"] or []:
        if not isinstance(rule, Mapping) or "ring" not in rule or "allow" not in rule:
            raise _halt(source, "an egress rule is missing 'ring' or 'allow'")
        ring = _require_declared(rule["ring"], estate_rings, "egress ring", source)
        if ring in egress:
            raise _halt(source, f"egress ring '{ring}' is ruled twice")
        allow = rule["allow"]
        if not isinstance(allow, list):
            raise _halt(source, f"egress ring '{ring}' allow is not a list")
        egress[ring] = tuple(
            _require_declared(c, provider_classes, f"egress '{ring}' allow", source)
            for c in allow)
    missing = [r for r in estate_rings if r not in egress]
    if missing:
        raise _halt(source, "declared rings with no egress rule: "
                            f"{', '.join(missing)} -- a ring with no rule HALTs "
                            "rather than falling through to permitted")

    # pools
    if not isinstance(pools_block, Mapping) or "entries" not in pools_block:
        raise _halt(source, "pools block declares no 'entries' key -- an empty "
                            "registry is written as 'entries: []', never omitted")
    raw_entries = pools_block["entries"]
    if raw_entries is None:
        raise _halt(source, "pools.entries is null; write 'entries: []' for none")
    if not isinstance(raw_entries, list):
        raise _halt(source, "pools.entries is not a list")
    pools: List[Pool] = []
    seen: List[str] = []
    for idx, spec in enumerate(raw_entries):
        if not isinstance(spec, Mapping):
            raise _halt(source, f"pool #{idx} is not a mapping")
        for key in _POOL_REQUIRED:
            if key not in spec:
                raise _halt(source, f"pool #{idx} is missing '{key}' -- a "
                                    "load-bearing field is never defaulted")
        pool_id = str(spec["id"])
        if pool_id in seen:
            raise _halt(source, f"pool id '{pool_id}' is declared twice")
        seen.append(pool_id)
        fams = spec["families"]
        if not isinstance(fams, list) or not fams:
            raise _halt(source, f"pool '{pool_id}' serves no families")
        try:
            max_concurrent = int(spec["max_concurrent"])
        except (TypeError, ValueError) as exc:
            raise _halt(source, f"pool '{pool_id}' max_concurrent is not an integer") from exc
        if max_concurrent < 1:
            raise _halt(source, f"pool '{pool_id}' max_concurrent must be >= 1")
        pools.append(Pool(
            id=pool_id,
            provider_class=_require_declared(
                spec["provider_class"], provider_classes,
                f"pool '{pool_id}' provider_class", source),
            families=tuple(
                _require_declared(f, model_families, f"pool '{pool_id}' family", source)
                for f in fams),
            status=_require_declared(
                spec["status"], statuses, f"pool '{pool_id}' status", source),
            max_concurrent=max_concurrent,
            telemetry_source=_require_declared(
                spec["telemetry_source"], telemetry_sources,
                f"pool '{pool_id}' telemetry_source", source),
            note=str(spec.get("note", "")),
        ))

    return Policy(
        source=source,
        provider_classes=provider_classes,
        model_families=model_families,
        efforts=efforts,
        cache_ttls=cache_ttls,
        telemetry_sources=telemetry_sources,
        estate_rings=estate_rings,
        statuses=statuses,
        tiers=tiers,
        task_shapes=task_shapes,
        egress=egress,
        pools=tuple(pools),
        example_loaded=use_example,
    )


def load_policy(path: Optional[Path] = None, use_example: bool = False) -> Policy:
    """Read and validate the policy file. Absence is a HALT, not an empty policy."""
    target = Path(path) if path is not None else DEFAULT_POLICY_PATH
    if not target.exists():
        raise PolicyError(f"{target}: routing policy is absent. A node with no "
                          "routing policy routes nothing; it does not route "
                          "everywhere.")
    try:
        doc = yaml.safe_load(target.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PolicyError(f"{target}: routing policy is unparseable: {exc}") from exc
    return parse_policy(doc, source=target.as_posix(), use_example=use_example)


# ---------------------------------------------------------------------------
# resolve -- pure, deterministic, no network
# ---------------------------------------------------------------------------


def resolve(policy: Policy, task_shape: str, estate_ring: str) -> Assignment:
    """Which pool serves this shape, in this ring, and may it cross.

    Deterministic: the same policy and the same two arguments always produce
    the same Assignment, because preference is the file's declared order and
    nothing here reads a clock, a socket or an environment variable.

    Rules, in order:

      R-SHAPE-TIER    the shape names its tier; the tier names its families
      R-EFFORT-*      the shape's override, else the tier default
      R-TTL-*         the shape's override, else the tier default
      R-FENCE-RING    the ring's allowed provider classes are read first, and
                      nothing below may widen them
      R-POOL-PICK     the first live pool, in file order, whose class is
                      allowed and which serves the most-preferred family it can
      R-FENCE-REFUSED a pool could have served and its class is fenced out
      R-POOL-UNMET    nothing declared could have served this shape at all
    """
    if task_shape not in policy.task_shapes:
        raise PolicyError(
            f"{policy.source}: task shape '{task_shape}' is not declared. "
            f"Declared: {', '.join(policy.task_shapes)}. An undeclared shape "
            "halts rather than routing to a guess.")
    if estate_ring not in policy.egress:
        raise PolicyError(
            f"{policy.source}: estate ring '{estate_ring}' is not declared. "
            f"Declared: {', '.join(policy.estate_rings)}. An unobserved estate "
            "never resolves to a permissive default.")

    shape = policy.task_shapes[task_shape]
    tier_name = shape["tier"]
    tier = policy.tiers[tier_name]
    families: Tuple[str, ...] = tier["families"]
    rules: List[str] = ["R-SHAPE-TIER"]

    if "effort" in shape:
        effort, rules_effort = shape["effort"], "R-EFFORT-SHAPE"
    else:
        effort, rules_effort = tier["default_effort"], "R-EFFORT-TIER"
    rules.append(rules_effort)

    if "cache_ttl" in shape:
        ttl, rules_ttl = shape["cache_ttl"], "R-TTL-SHAPE"
    else:
        ttl, rules_ttl = tier["default_cache_ttl"], "R-TTL-TIER"
    rules.append(rules_ttl)

    allowed = policy.egress[estate_ring]
    rules.append("R-FENCE-RING")

    live = [p for p in policy.pools if p.status == "live"]
    # Preference is family order first, file order second: the policy's stated
    # preference outranks the order somebody happened to add pools in.
    for family in families:
        for pool in live:
            if family not in pool.families:
                continue
            if pool.provider_class in allowed:
                rules.append("R-POOL-PICK")
                return Assignment(
                    task_shape=task_shape, estate_ring=estate_ring,
                    tier=tier_name, families=families, effort=effort,
                    cache_ttl=ttl, cache_ttl_seconds=policy.cache_ttls[ttl],
                    fence_verdict=FENCE_PERMITTED,
                    allowed_provider_classes=allowed,
                    pool_id=pool.id, provider_class=pool.provider_class,
                    family=family, rules_applied=tuple(rules),
                    reason=(f"{task_shape} needs {tier_name}; pool '{pool.id}' "
                            f"serves {family} and class '{pool.provider_class}' "
                            f"is permitted for ring '{estate_ring}'"))

    fenced = tuple(
        p.id for p in live
        if any(f in p.families for f in families) and p.provider_class not in allowed)
    if fenced:
        rules.append("R-FENCE-REFUSED")
        return Assignment(
            task_shape=task_shape, estate_ring=estate_ring, tier=tier_name,
            families=families, effort=effort, cache_ttl=ttl,
            cache_ttl_seconds=policy.cache_ttls[ttl],
            fence_verdict=FENCE_REFUSED, allowed_provider_classes=allowed,
            fenced_out=fenced, rules_applied=tuple(rules),
            reason=(f"{len(fenced)} live pool(s) could serve {tier_name} for "
                    f"'{task_shape}', and every one of them sits outside the "
                    f"classes ring '{estate_ring}' may reach "
                    f"({', '.join(allowed)}). Refused, not downgraded."))

    rules.append("R-POOL-UNMET")
    return Assignment(
        task_shape=task_shape, estate_ring=estate_ring, tier=tier_name,
        families=families, effort=effort, cache_ttl=ttl,
        cache_ttl_seconds=policy.cache_ttls[ttl],
        fence_verdict=FENCE_UNMET, allowed_provider_classes=allowed,
        rules_applied=tuple(rules),
        reason=(f"no live pool declares any of {', '.join(families)}. This is "
                "an empty registry, not a refusal: the fence never had a "
                "chance to have an opinion."))


# ---------------------------------------------------------------------------
# selftest -- a detector that has never fired is indistinguishable from a
# broken one, so every path below must actually fire.
# ---------------------------------------------------------------------------


def selftest(path: Optional[Path] = None) -> Tuple[bool, str]:
    """Prove each verdict and each halt can fire. No files written, no network."""
    lines: List[str] = []
    ok = True

    def check(label: str, condition: bool, detail: str = "") -> None:
        nonlocal ok
        lines.append(f"  [{'PASS' if condition else 'FAIL'}] {label}"
                     + (f" -- {detail}" if detail else ""))
        if not condition:
            ok = False

    target = Path(path) if path is not None else DEFAULT_POLICY_PATH
    try:
        shipped = load_policy(target)
    except PolicyError as exc:
        return False, f"selftest could not load the shipped policy: {exc}"

    check("shipped policy loads", True, f"{len(shipped.task_shapes)} shapes, "
          f"{len(shipped.pools)} pools")
    check("shipped registry is empty on purpose", not shipped.pools)

    a = resolve(shipped, "interactive", "internal")
    check("empty registry resolves UNMET, not permitted",
          a.fence_verdict == FENCE_UNMET, a.fence_verdict)

    try:
        resolve(shipped, "no-such-shape", "internal")
        check("undeclared shape HALTs", False, "no PolicyError raised")
    except PolicyError:
        check("undeclared shape HALTs", True)

    try:
        resolve(shipped, "interactive", "no-such-ring")
        check("undeclared ring HALTs", False, "no PolicyError raised")
    except PolicyError:
        check("undeclared ring HALTs", True)

    try:
        example = load_policy(target, use_example=True)
    except PolicyError as exc:
        return False, "\n".join(lines + [f"  [FAIL] example block loads -- {exc}"])
    check("example block loads through the same loader", bool(example.pools),
          f"{len(example.pools)} pools")

    p = resolve(example, "interactive", "internal")
    check("example permits an internal interactive turn",
          p.fence_verdict == FENCE_PERMITTED and p.pool_id is not None,
          f"{p.fence_verdict}/{p.pool_id}")

    r = resolve(example, "interactive", "served")
    check("fence REFUSES a served-ring crossing",
          r.fence_verdict == FENCE_REFUSED and bool(r.fenced_out),
          f"{r.fence_verdict}, fenced_out={list(r.fenced_out)}")

    local_ok = resolve(example, "tick", "served")
    check("served ring still reaches an on-machine pool",
          local_ok.fence_verdict == FENCE_PERMITTED
          and local_ok.provider_class == "local",
          f"{local_ok.fence_verdict}/{local_ok.pool_id}")

    repeats = {json.dumps(resolve(example, "review", "venture").to_dict(),
                          sort_keys=True) for _ in range(25)}
    check("resolve is deterministic across repeats", len(repeats) == 1,
          f"{len(repeats)} distinct results")

    check("disabled pools are never selected",
          all(resolve(example, s, r_).pool_id != "example-retired-lane"
              for s in example.task_shapes for r_ in example.estate_rings))

    doc = yaml.safe_load(target.read_text(encoding="utf-8"))
    broken = json.loads(json.dumps(doc))  # deep copy, plain types only
    broken["example"]["pools"]["entries"][0].pop("telemetry_source")
    try:
        parse_policy(broken, source="<selftest>", use_example=True)
        check("a pool missing a load-bearing field HALTs", False,
              "no PolicyError raised")
    except PolicyError:
        check("a pool missing a load-bearing field HALTs", True)

    holed = json.loads(json.dumps(doc))
    holed["fence"]["egress"] = holed["fence"]["egress"][:1]
    try:
        parse_policy(holed, source="<selftest>")
        check("a ring with no egress rule HALTs", False, "no PolicyError raised")
    except PolicyError:
        check("a ring with no egress rule HALTs", True)

    check("unmetered pools are counted, not hidden",
          "example-second-provider" in example.unmetered_pools,
          f"{len(example.unmetered_pools)} unmetered")

    header = "routing policy selftest: " + ("PASS" if ok else "FAIL")
    return ok, "\n".join([header] + lines)


# ---------------------------------------------------------------------------
# module CLI (the node CLI's ``route`` verb calls resolve directly)
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m intentops_core.routing.policy",
        description="Resolve a routing assignment from the policy file.")
    p.add_argument("--policy", default=None, help="path to routing-policy.yaml")
    p.add_argument("--shape", default=None, help="task shape")
    p.add_argument("--ring", default=None, help="estate ring")
    p.add_argument("--example", action="store_true",
                   help="load the fictional example pools and fence")
    p.add_argument("--json", action="store_true")
    p.add_argument("--selftest", action="store_true")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.selftest:
        ok, report = selftest(Path(args.policy) if args.policy else None)
        print(report)
        return 0 if ok else 1
    if not args.shape or not args.ring:
        print("--shape and --ring are both required (or use --selftest)",
              file=sys.stderr)
        return 2
    try:
        policy = load_policy(Path(args.policy) if args.policy else None,
                             use_example=args.example)
        assignment = resolve(policy, args.shape, args.ring)
    except PolicyError as exc:
        print(f"HALT: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(assignment.to_dict(), indent=2, sort_keys=True))
    else:
        print(render(assignment, policy))
    return 0


def render(assignment: Assignment, policy: Optional[Policy] = None) -> str:
    """Human rendering. Leads with the verdict, because that is the answer."""
    a = assignment
    out = [
        f"{a.task_shape} / ring '{a.estate_ring}': {a.fence_verdict.upper()}",
        f"  tier      : {a.tier}  (families: {', '.join(a.families)})",
        f"  effort    : {a.effort}",
        f"  cache ttl : {a.cache_ttl} ({a.cache_ttl_seconds}s)",
        f"  may reach : {', '.join(a.allowed_provider_classes)}",
    ]
    if a.pool_id:
        out.append(f"  pool      : {a.pool_id} [{a.provider_class}] serving {a.family}")
    if a.fenced_out:
        out.append(f"  fenced out: {', '.join(a.fenced_out)}")
    out.append(f"  rules     : {', '.join(a.rules_applied)}")
    out.append(f"  reason    : {a.reason}")
    if policy is not None:
        unmetered = policy.unmetered_pools
        out.append(f"  unmetered : {len(unmetered)} of {len(policy.pools)} pool(s)"
                   + (f" -- {', '.join(unmetered)}" if unmetered else ""))
    return "\n".join(out)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
