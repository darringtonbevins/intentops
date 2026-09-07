"""
PURPOSE: verify the eight files under config/ (trust-roots, trust-revocation,
core-surface, capability-kinds, alignment-interview.template, genesis-probes,
saddles, ports) parse as valid YAML/JSON, declare the schema+as_of pair every
one of them promises in its own header, and keep their closed vocabularies
internally consistent (no reference to an id that is not declared).

WRITE MODEL: read-only test module -- no store, no fixtures written to disk.

BLIND SPOTS: this suite checks SHAPE, not truth. It cannot tell a well-formed
placeholder from a well-formed real key (that is the operator's ceremony,
never a unit test's job), and it does not exercise any runtime loader code --
those loaders live in intentops-core / a saddle package and have their own
tests. No live services are used or required.

The estate-token sweep additionally inherits EVERY blind spot the exposure
fence publishes (see config/exposure-fence.yaml): a hash denylist finds only
what it was told to look for, so a private name nobody thought of is invisible
to it and a green run says nothing about that population.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"

YAML_FILES = [
    "trust-roots.yaml",
    "core-surface.yaml",
    "capability-kinds.yaml",
    "alignment-interview.template.yaml",
    "genesis-probes.yaml",
    "saddles.yaml",
    "ports.yaml",
]

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE_PATH = REPO_ROOT / "scripts" / "ops" / "exposure_gate.py"
FENCE_PATH = REPO_ROOT / "config" / "exposure-fence.yaml"


def _load_gate():
    """Load the shipped gate module by path (the convention in tests/test_exposure_gate.py).

    Failure here is a hard error, never a skip: a fence sweep that quietly does
    not run is exactly the silent-failure shape the fence exists to prevent.
    """
    spec = importlib.util.spec_from_file_location("exposure_gate", GATE_PATH)
    assert spec and spec.loader, f"could not load the exposure gate at {GATE_PATH}"
    module = importlib.util.module_from_spec(spec)
    sys.modules["exposure_gate"] = module
    spec.loader.exec_module(module)
    return module


def _load_yaml(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict), f"{name} did not parse to a mapping"
    return data


@pytest.mark.parametrize("name", YAML_FILES)
def test_yaml_files_parse(name: str) -> None:
    _load_yaml(name)


def test_trust_revocation_json_parses() -> None:
    path = CONFIG_DIR / "trust-revocation.json"
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["schema"] == "trust-revocation/v1"
    assert data["sequence"] == 0
    assert data["entries"] == []


@pytest.mark.parametrize("name", YAML_FILES)
def test_every_yaml_declares_schema_and_as_of(name: str) -> None:
    data = _load_yaml(name)
    assert "schema" in data, f"{name} missing required `schema` field"
    assert "as_of" in data, f"{name} missing required `as_of` field (honesty.md Belief Currency)"


def test_trust_roots_roots_nonempty_and_minted() -> None:
    """The release-root ceremony ran on 2026-09-07, so this asserts the MINTED
    shape -- strictly, because "not a placeholder" is a far weaker claim than
    "a well-formed key".

    This test asserted the opposite until that day: every root `proposed`,
    every fingerprint, key and DID a literal PLACEHOLDER. That shape has not
    stopped mattering and its refusals have not been retired; they moved to the
    `placeholder_tree` fixture in `tests/conftest.py`, which builds a
    pre-ceremony tree of its own rather than reading these live carriers.
    """
    data = _load_yaml("trust-roots.yaml")
    roots = data["roots"]
    assert len(roots) >= 1, "an empty roots list must be a HALT upstream, never a default"
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", data["pinned_fingerprint"]), \
        "the pinned fingerprint must be a real sha256 over the DER SPKI bytes"
    for root in roots:
        rid = root["id"]
        assert root["status"] == "active", f"{rid} is not active after the ceremony"
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", root["fingerprint"]), rid
        assert root["public_key_pem"].strip().startswith(
            "-----BEGIN PUBLIC KEY-----"), rid
        assert root["did"].startswith("did:key:z"), rid
        assert re.fullmatch(r"[0-9A-F]{16}", root["key_id"]), rid
        # nothing anywhere in the record may still be a placeholder: a root
        # that is half minted is the partial tamper G1.3 and G1.4 exist to catch
        assert "PLACEHOLDER" not in yaml.safe_dump(root), rid
        assert root["ceremony"]["minted_at"], f"{rid} claims no ceremony date"
    assert data["pinned_fingerprint"] == next(
        r["fingerprint"] for r in roots if r["role"] == "release_root"), \
        "the pin must name the release root, not some other root"
    assert data["trust_policy"]["unsigned_bundle"] == "refuse"
    assert data["trust_policy"]["crypto_unavailable"] == "halt"
    assert data["trust_policy"]["expired_root"] == "refuse"


def test_trust_roots_signs_vocabulary_is_closed() -> None:
    data = _load_yaml("trust-roots.yaml")
    closed = {
        "invariant_bundle", "imprint_manifest", "archetype_catalogue",
        "release_artifact", "trust_roots_file", "revocation_list",
        "node_identity_cert", "alignment_record", "ordering_ruling",
        "core_mechanic_change",
    }
    for root in data["roots"]:
        for value in root["signs"]:
            assert value in closed, f"undeclared `signs` value {value!r} on root {root['id']}"


def test_core_surface_every_glob_has_a_reason() -> None:
    data = _load_yaml("core-surface.yaml")
    surface = data["surface"]
    assert len(surface) >= 1
    for entry in surface:
        assert entry.get("glob"), "a core-surface entry with no glob is meaningless"
        assert entry.get("reason"), f"core-surface glob {entry.get('glob')!r} has no stated reason"


def test_capability_kinds_every_kind_states_all_three_accountings() -> None:
    data = _load_yaml("capability-kinds.yaml")
    required = {"probe", "anchor", "monitored"}
    for kind_name, kind in data["kinds"].items():
        accountings = kind["accountings"]
        assert required.issubset(accountings.keys()), f"kind {kind_name!r} missing an accounting"
        for acc_name, acc in accountings.items():
            assert acc["status"] in ("applies", "n/a"), f"{kind_name}.{acc_name} has an unknown status"
            assert acc.get("why"), f"{kind_name}.{acc_name} states a status with no reason"


def test_alignment_interview_variable_floor_cannot_be_removed() -> None:
    data = _load_yaml("alignment-interview.template.yaml")
    floor = data["variables_floor"]["cannot_remove"]
    assert "reaches_person" in floor
    assert "their_visibility" in floor
    # every declared variable in the floor must actually be defined
    for var in floor:
        assert var in data["variables"], f"floor variable {var!r} is not defined in `variables`"


def test_alignment_interview_consent_questions_carry_no_recommendation() -> None:
    data = _load_yaml("alignment-interview.template.yaml")
    for module in data["modules"]:
        for question in module["questions"]:
            if question["kind"] == "consent":
                assert question["recommended"] is None, (
                    f"consent question {question['id']!r} carries a recommendation; "
                    "the loader must refuse this"
                )


def test_alignment_interview_options_never_exceed_four() -> None:
    data = _load_yaml("alignment-interview.template.yaml")
    for module in data["modules"]:
        for question in module["questions"]:
            assert len(question.get("options") or []) <= 4, (
                f"question {question['id']!r} carries more than 4 options"
            )


def test_alignment_interview_replay_question_names_no_real_approval() -> None:
    # No real ruling ships in the public template -- replay.approval_id must
    # stay null (or absent) everywhere in this file.
    data = _load_yaml("alignment-interview.template.yaml")
    for module in data["modules"]:
        for question in module["questions"]:
            if question["kind"] == "replay":
                replay = question.get("replay") or {}
                assert replay.get("approval_id") is None


def test_alignment_interview_calibration_bar_matches_design() -> None:
    data = _load_yaml("alignment-interview.template.yaml")
    bar = data["calibration_bar"]
    assert bar["minimum_rulings"] == 20
    assert bar["minimum_accuracy"] == 0.80
    assert bar["direction"] == "forward_only"


def test_genesis_probes_ids_are_unique() -> None:
    data = _load_yaml("genesis-probes.yaml")
    ids = [probe["id"] for probe in data["probes"]]
    assert len(ids) == len(set(ids)), "duplicate genesis probe id"
    for probe in data["probes"]:
        assert probe["id"].startswith("GEN-")


def test_saddles_claudecode_is_the_reference_grade() -> None:
    data = _load_yaml("saddles.yaml")
    hosts = {h["id"]: h for h in data["hosts"]}
    assert hosts["claudecode"]["grade"] == "reference"
    for op in ("S1", "S2", "S3", "S4", "S5"):
        assert hosts["claudecode"]["operations"][op] == "implemented"


def test_saddles_planned_hosts_have_no_implemented_claims() -> None:
    data = _load_yaml("saddles.yaml")
    for host in data["hosts"]:
        if host["grade"] == "planned":
            for op_status in host["operations"].values():
                assert op_status != "implemented", (
                    f"host {host['id']!r} is graded planned but claims an implemented operation"
                )


def test_ports_yaml_bindings_default_to_loopback() -> None:
    data = _load_yaml("ports.yaml")
    for name, svc in data["services"].items():
        assert svc["binding"] == "127.0.0.1", f"service {name!r} does not default to loopback"
    assert data["meta"]["policy"]["default_binding"] == "127.0.0.1"


def test_the_fence_sweep_can_actually_fire() -> None:
    """Positive control: prove the sweep below is capable of red.

    A denylist test that has never fired is indistinguishable from a broken
    one, and this one would pass vacuously if the fence loaded with zero
    tokens. The planted token is invented at runtime via the gate's own
    ``_synthetic_fence`` helper, so proving the MECHANISM fires never writes a
    real fenced name into this file -- which is the whole reason this test
    stopped carrying its own plaintext list.
    """
    gate = _load_gate()
    planted = "zzsynthetic"
    res = gate.scan_text(planted, gate._synthetic_fence(planted), path="<control>")
    assert res.findings, "the fence sweep did not fire on a planted token"

    # And the real fence must be non-empty, or the sweep below proves nothing.
    fence = gate.load_fence(FENCE_PATH)
    assert fence.tokens, f"{FENCE_PATH} declares no tokens -- the sweep would pass vacuously"


@pytest.mark.parametrize("name", YAML_FILES + ["trust-revocation.json"])
def test_no_forbidden_estate_tokens(name: str) -> None:
    """No fenced estate token appears in the shipped config.

    This reuses the SHIPPED fence (``config/exposure-fence.yaml``) through the
    gate's own ``scan_text`` rather than keeping a second copy of the denylist
    here. Two reasons, in order of weight:

      1. The plaintext copy this test used to carry put all 21 fenced words
         into the repository the fence exists to keep them out of -- the gate
         flagged this file as its only hit in the tree. A denylist that cannot
         be written down is a hashed denylist, and there is already one.
      2. Two lists that must be kept in step will drift, and the drift is
         silent in the direction that matters: a token added to the fence and
         not here leaves this test quietly weaker than the gate.

    Findings never carry the matched text (``Finding`` redacts it), so an
    assertion message is safe to paste into a CI log.
    """
    gate = _load_gate()
    fence = gate.load_fence(FENCE_PATH)
    text = (CONFIG_DIR / name).read_text(encoding="utf-8")
    res = gate.scan_text(text, fence, path=f"config/{name}")

    assert not res.findings, (
        f"exposure-fence hit(s) in config/{name}:\n  "
        + "\n  ".join(f.render() for f in res.findings)
    )
    assert not res.bad_markers, (
        f"unexplained allow marker in config/{name}:\n  "
        + "\n  ".join(f.render() for f in res.bad_markers)
    )
