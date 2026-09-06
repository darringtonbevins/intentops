"""The routing policy decides, and it refuses -- and a test says so.

PURPOSE
    Four properties carry the whole design, and each one is the property whose
    quiet failure would be invisible:

      1. An undeclared task shape or estate ring HALTs. A router that guesses
         at an unrecognised input is a router that routes the one request
         nobody classified to whichever pool happened to be first.
      2. The fence REFUSES a ring crossing, and refusing is distinguishable
         from having nothing to offer. Collapsing "fenced out" into "no pool"
         would hide the fence behind an empty registry -- the number would look
         identical and only one of them is a control.
      3. ``resolve`` is deterministic. It reads no clock, no socket and no
         environment variable, so the same policy and arguments always produce
         the same assignment.
      4. The shipped example block resolves through the same loader that reads
         the real policy, so it cannot rot into a fiction that no longer
         parses.

    Everything runs against temp files and in-memory documents. No live
    service, no network, no provider SDK.

BLIND SPOTS
    * These tests prove the fence refuses on a pool's DECLARED provider class.
      Nothing here can prove a pool declared ``local`` is local; that is a
      review property and is stated in the module's own blind spots.
    * The vendor-name scan below is a keyword list. A provider nobody thought
      of is invisible to it, exactly as the exposure fence's own denylist is.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from intentops_core.estate_classifier import ESTATE_KINDS
from intentops_core.routing import policy as rp

REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = REPO_ROOT / "config" / "routing-policy.yaml"


@pytest.fixture(scope="module")
def shipped() -> rp.Policy:
    return rp.load_policy(POLICY_PATH)


@pytest.fixture(scope="module")
def example() -> rp.Policy:
    return rp.load_policy(POLICY_PATH, use_example=True)


@pytest.fixture(scope="module")
def raw_doc() -> dict:
    return yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))


def _mutable(doc: dict) -> dict:
    """A deep copy through plain JSON types -- no shared nested structures."""
    return json.loads(json.dumps(doc))


# ---------------------------------------------------------------------------
# 1. the shipped policy
# ---------------------------------------------------------------------------


def test_shipped_policy_loads_and_is_empty_on_purpose(shipped: rp.Policy) -> None:
    assert shipped.pools == ()
    assert shipped.task_shapes
    assert shipped.tiers
    # entries: [] is written, not omitted -- a node that has drawn on nothing
    # says so rather than leaving the question out.
    assert "entries" in yaml.safe_load(
        POLICY_PATH.read_text(encoding="utf-8"))["pools"]


def test_tiers_are_the_four_declared_roles(shipped: rp.Policy) -> None:
    assert tuple(shipped.tiers) == ("general", "specialist", "workforce", "tooling")


def test_every_declared_ring_covers_the_cores_estate_kinds(shipped: rp.Policy) -> None:
    """The fence must be able to rule on any estate the classifier can return.

    A ring the classifier produces and the fence has never heard of would HALT
    at resolve time -- which is the safe direction, but it is a policy hole,
    and this test is the thing that finds it before a caller does.
    """
    for kind in ESTATE_KINDS:
        assert kind in shipped.estate_rings, kind
        assert kind in shipped.egress, kind


def test_empty_registry_resolves_unmet_never_permitted(shipped: rp.Policy) -> None:
    for shape in shipped.task_shapes:
        for ring in shipped.estate_rings:
            a = rp.resolve(shipped, shape, ring)
            assert a.fence_verdict == rp.FENCE_UNMET
            assert a.pool_id is None
            # the refusal stays in the population: tier and effort are still
            # answered, because that is what a reader needs to fix the gap.
            assert a.tier and a.effort and a.cache_ttl_seconds > 0


def test_seed_fence_ships_the_strictest_reading(shipped: rp.Policy) -> None:
    for ring, allowed in shipped.egress.items():
        assert allowed == ("local",), (ring, allowed)


def test_no_vendor_or_model_id_appears_in_the_policy() -> None:
    """Provider-neutral means the file names no breed at all."""
    text = POLICY_PATH.read_text(encoding="utf-8").lower()
    for vendor in ("openai", "gpt-", "anthropic", "claude", "gemini", "chatgpt",
                   "llama", "mistral", "cohere", "ollama", "azure", "bedrock",
                   "vertex", "grok", "deepseek", "qwen"):
        assert vendor not in text, vendor


# ---------------------------------------------------------------------------
# 2. halts -- an undeclared value is a hard exit, never a default
# ---------------------------------------------------------------------------


def test_undeclared_task_shape_halts(shipped: rp.Policy) -> None:
    with pytest.raises(rp.PolicyError) as exc:
        rp.resolve(shipped, "sculpt-a-horse", "internal")
    assert "not declared" in str(exc.value)


def test_undeclared_estate_ring_halts(shipped: rp.Policy) -> None:
    with pytest.raises(rp.PolicyError) as exc:
        rp.resolve(shipped, "interactive", "somebody-elses-ring")
    assert "not declared" in str(exc.value)


def test_unknown_is_not_a_ring(shipped: rp.Policy) -> None:
    """``unknown`` is omitted from the vocabulary, so it cannot resolve at all."""
    assert "unknown" not in shipped.estate_rings
    with pytest.raises(rp.PolicyError):
        rp.resolve(shipped, "interactive", "unknown")


@pytest.mark.parametrize("missing", list(rp._POOL_REQUIRED))
def test_pool_missing_any_load_bearing_field_halts(raw_doc: dict, missing: str) -> None:
    doc = _mutable(raw_doc)
    doc["example"]["pools"]["entries"][1].pop(missing)
    with pytest.raises(rp.PolicyError) as exc:
        rp.parse_policy(doc, source="<test>", use_example=True)
    assert missing in str(exc.value)


def test_undeclared_provider_class_on_a_pool_halts(raw_doc: dict) -> None:
    doc = _mutable(raw_doc)
    doc["example"]["pools"]["entries"][0]["provider_class"] = "somewhere-else"
    with pytest.raises(rp.PolicyError):
        rp.parse_policy(doc, source="<test>", use_example=True)


def test_undeclared_family_on_a_tier_halts(raw_doc: dict) -> None:
    doc = _mutable(raw_doc)
    doc["tiers"]["general"]["families"] = ["a_family_nobody_declared"]
    with pytest.raises(rp.PolicyError):
        rp.parse_policy(doc, source="<test>")


def test_a_ring_with_no_egress_rule_halts(raw_doc: dict) -> None:
    doc = _mutable(raw_doc)
    doc["fence"]["egress"] = doc["fence"]["egress"][:2]
    with pytest.raises(rp.PolicyError) as exc:
        rp.parse_policy(doc, source="<test>")
    assert "no egress rule" in str(exc.value)


def test_duplicate_pool_id_halts(raw_doc: dict) -> None:
    doc = _mutable(raw_doc)
    entries = doc["example"]["pools"]["entries"]
    entries.append(_mutable(entries[0]))
    with pytest.raises(rp.PolicyError) as exc:
        rp.parse_policy(doc, source="<test>", use_example=True)
    assert "twice" in str(exc.value)


def test_null_entries_halts_but_empty_list_does_not(raw_doc: dict) -> None:
    doc = _mutable(raw_doc)
    doc["pools"]["entries"] = None
    with pytest.raises(rp.PolicyError):
        rp.parse_policy(doc, source="<test>")
    doc["pools"]["entries"] = []
    assert rp.parse_policy(doc, source="<test>").pools == ()


def test_absent_policy_file_halts(tmp_path: Path) -> None:
    with pytest.raises(rp.PolicyError) as exc:
        rp.load_policy(tmp_path / "nothing-here.yaml")
    assert "absent" in str(exc.value)


def test_unparseable_policy_halts(tmp_path: Path) -> None:
    bad = tmp_path / "routing-policy.yaml"
    bad.write_text("schema: routing-policy/v1\npools: [unclosed\n", encoding="utf-8")
    with pytest.raises(rp.PolicyError):
        rp.load_policy(bad)


def test_wrong_schema_halts(raw_doc: dict) -> None:
    doc = _mutable(raw_doc)
    doc["schema"] = "routing-policy/v99"
    with pytest.raises(rp.PolicyError):
        rp.parse_policy(doc, source="<test>")


# ---------------------------------------------------------------------------
# 3. the fence
# ---------------------------------------------------------------------------


def test_example_block_resolves(example: rp.Policy) -> None:
    a = rp.resolve(example, "interactive", "internal")
    assert a.fence_verdict == rp.FENCE_PERMITTED
    assert a.pool_id == "example-house-subscription"
    assert a.tier == "general"
    assert a.family == "frontier_general"
    assert a.effort == "high"
    assert a.cache_ttl_seconds == 3600
    assert "R-POOL-PICK" in a.rules_applied


def test_fence_refuses_a_served_ring_crossing(example: rp.Policy) -> None:
    a = rp.resolve(example, "interactive", "served")
    assert a.fence_verdict == rp.FENCE_REFUSED
    assert a.pool_id is None
    # and it names WHICH pools it refused, so the refusal is auditable
    assert set(a.fenced_out) == {"example-house-subscription", "example-second-provider"}
    assert a.allowed_provider_classes == ("local",)
    assert "R-FENCE-REFUSED" in a.rules_applied


def test_refused_is_distinguishable_from_unmet(example: rp.Policy,
                                               shipped: rp.Policy) -> None:
    """The two negatives are different facts and must not collapse into one."""
    refused = rp.resolve(example, "interactive", "served")
    unmet = rp.resolve(shipped, "interactive", "served")
    assert refused.fence_verdict != unmet.fence_verdict
    assert refused.fenced_out and not unmet.fenced_out


def test_a_fenced_ring_still_reaches_its_own_machine(example: rp.Policy) -> None:
    a = rp.resolve(example, "tick", "served")
    assert a.fence_verdict == rp.FENCE_PERMITTED
    assert a.provider_class == "local"
    assert a.pool_id == "example-on-machine"


def test_fence_is_never_widened_by_a_downgrade(example: rp.Policy) -> None:
    """A refusal is a refusal: nothing below the fence may pick a fenced pool."""
    for shape in example.task_shapes:
        for ring, allowed in example.egress.items():
            a = rp.resolve(example, shape, ring)
            if a.pool_id is not None:
                assert a.provider_class in allowed, (shape, ring, a.pool_id)


def test_venture_ring_stays_in_family(example: rp.Policy) -> None:
    a = rp.resolve(example, "review", "venture")
    assert a.fence_verdict == rp.FENCE_PERMITTED
    assert a.provider_class == "in_family"
    assert a.tier == "specialist"
    assert a.family == "frontier_long_horizon"   # tier preference order wins
    assert a.effort == "xhigh"


def test_disabled_and_staged_pools_are_never_selected(example: rp.Policy) -> None:
    for shape in example.task_shapes:
        for ring in example.estate_rings:
            assert rp.resolve(example, shape, ring).pool_id != "example-retired-lane"


# ---------------------------------------------------------------------------
# 4. determinism, tiers and posture
# ---------------------------------------------------------------------------


def test_resolve_is_deterministic(example: rp.Policy) -> None:
    for shape in example.task_shapes:
        for ring in example.estate_rings:
            seen = {json.dumps(rp.resolve(example, shape, ring).to_dict(),
                               sort_keys=True) for _ in range(20)}
            assert len(seen) == 1, (shape, ring)


def test_determinism_survives_a_reload(example: rp.Policy) -> None:
    reloaded = rp.load_policy(POLICY_PATH, use_example=True)
    for shape in example.task_shapes:
        for ring in example.estate_rings:
            assert (rp.resolve(example, shape, ring).to_dict()
                    == rp.resolve(reloaded, shape, ring).to_dict())


def test_fanout_and_tick_never_reach_a_frontier_tier(example: rp.Policy) -> None:
    """A leg never inherits the caller's tier -- the shape pins it downward."""
    assert rp.resolve(example, "fanout_leg", "internal").tier == "workforce"
    assert rp.resolve(example, "tick", "internal").tier == "tooling"
    assert rp.resolve(example, "tick", "internal").cache_ttl_seconds == 300


def test_shape_override_beats_tier_default(example: rp.Policy) -> None:
    a = rp.resolve(example, "bulk_read", "internal")
    assert a.effort == "low" and "R-EFFORT-SHAPE" in a.rules_applied
    b = rp.resolve(example, "interactive", "internal")
    assert b.effort == "high" and "R-EFFORT-TIER" in b.rules_applied


def test_unmetered_pools_are_counted_not_hidden(example: rp.Policy) -> None:
    unmetered = example.unmetered_pools
    assert "example-second-provider" in unmetered
    # every pool is either metered by a declared source or counted here
    assert all(p.telemetry_source in example.telemetry_sources for p in example.pools)


def test_assignment_round_trips_to_plain_json(example: rp.Policy) -> None:
    d = rp.resolve(example, "review", "internal").to_dict()
    assert json.loads(json.dumps(d)) == d


# ---------------------------------------------------------------------------
# 5. the caller -- a policy nothing exercises is indistinguishable from none
# ---------------------------------------------------------------------------


def test_selftest_passes() -> None:
    ok, report = rp.selftest(POLICY_PATH)
    assert ok, report
    assert "FAIL" not in report


def test_cli_route_prints_an_assignment(capsys) -> None:
    from intentops_core import cli

    rc = cli.main(["--repo-root", str(REPO_ROOT), "route",
                   "--shape", "interactive", "--ring", "served", "--example"])
    out = capsys.readouterr().out
    assert rc == 0                     # a refusal is an answer, not a crash
    assert "REFUSED" in out
    assert "fenced out" in out


def test_cli_route_json_and_halt(capsys) -> None:
    from intentops_core import cli

    rc = cli.main(["--repo-root", str(REPO_ROOT), "route", "--shape", "tick",
                   "--ring", "served", "--example", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["fence_verdict"] == "permitted"
    assert payload["pool_id"] == "example-on-machine"

    rc = cli.main(["--repo-root", str(REPO_ROOT), "route", "--shape", "tick",
                   "--ring", "not-a-ring"])
    err = capsys.readouterr().err
    assert rc == 1 and "HALT" in err


def test_cli_route_requires_both_arguments(capsys) -> None:
    from intentops_core import cli

    rc = cli.main(["--repo-root", str(REPO_ROOT), "route", "--shape", "tick"])
    assert rc == 2
    assert "no default" in capsys.readouterr().err


def test_cli_route_selftest(capsys) -> None:
    from intentops_core import cli

    rc = cli.main(["--repo-root", str(REPO_ROOT), "route", "--selftest"])
    assert rc == 0
    assert "PASS" in capsys.readouterr().out
