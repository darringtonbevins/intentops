"""Tests for the metabolism runner: the seam, the envelope, and the verdicts.

Everything here runs against a pytest temp directory, a stub provider, and (for
the one HTTP test) an in-process loopback server. No live service, no network
beyond this process's own socket, no scheduler, and no clock is read for a
decision -- ``now`` is injected everywhere.

The load-bearing assertions are the ones about what does NOT happen: a worker
does not run behind a ``seam: "null"`` declaration, a dry run does not open a
socket, an empty stage does not render green, and a run that spent nothing is
still in the ledger.
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "packages" / "intentops-core"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from intentops_core.inference.provider import (  # noqa: E402
    Completion,
    InferenceError,
    NullProvider,
    Provider,
    parse_pool,
)
from intentops_core.metabolism import runner as runner_mod  # noqa: E402
from intentops_core.metabolism import cli as metabolism_cli  # noqa: E402
from intentops_core.metabolism.cadence import parse_cadence  # noqa: E402
from intentops_core.metabolism.runner import (  # noqa: E402
    BIRTH_ENVELOPE,
    CALLABLES,
    CANDIDATES_RELDIR,
    CRYSTALLIZED_RELDIR,
    Envelope,
    PROMPTS_RELPATH,
    PromptTemplate,
    RUNS_RELPATH,
    RunnerError,
    load_prompts,
    load_runs,
    plan_stage,
    resolve_worker_kind,
    run_metabolism_stage,
)

COUNTABLE = {"absorb": "absorbed", "distill": "candidates",
             "crystallize": "crystallized", "method": "methods"}

NOW = "2026-09-06T00:00:00Z"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def cadence_doc(live: Optional[str] = None, **overrides: Any) -> Dict[str, Any]:
    """The shipped shape, with ONE stage optionally switched to a callable seam."""
    stages = []
    for sid in ("absorb", "distill", "crystallize", "method"):
        row: Dict[str, Any] = {
            "id": sid, "purpose": f"the {sid} stage", "seam": "null",
            "enabled": False, "produces": [COUNTABLE[sid]],
            "on_empty": "warn", "tier_ceiling": "T1"}
        if sid == live:
            row.update({"seam": "callable", "enabled": True,
                        "callable": CALLABLES[sid]})
            row.update(overrides)
        stages.append(row)
    return {"schema": "metabolism-cadence/v1", "as_of": "2026-09-06",
            "stages": stages}


def cadence(live: Optional[str] = None, **overrides: Any) -> Any:
    return parse_cadence(cadence_doc(live, **overrides))


@pytest.fixture()
def prompt() -> PromptTemplate:
    """The SHIPPED distill prompt, not a stand-in built for the test."""
    return load_prompts(REPO_ROOT / PROMPTS_RELPATH)["distill"]


@pytest.fixture()
def sources(tmp_path: Path) -> Path:
    directory = tmp_path / "sources"
    directory.mkdir()
    for name in ("alpha", "beta", "gamma"):
        (directory / f"{name}.md").write_text(f"# {name}\n\nthe {name} document\n",
                                              encoding="utf-8")
    return directory


class StubProvider(Provider):
    """Answers a canned text, counts what it was asked, opens no socket."""

    NAME = "stub"

    def __init__(self, answers: List[str], *, tokens: int = 10) -> None:
        super().__init__(None)
        self.answers = list(answers)
        self.tokens = tokens
        self.asked: List[str] = []

    def complete(self, prompt: str, max_tokens: Optional[int] = None) -> Completion:
        self.asked.append(prompt)
        text = self.answers[min(len(self.asked) - 1, len(self.answers) - 1)]
        return Completion(text=text, prompt_tokens=self.tokens,
                          completion_tokens=self.tokens, model="a-stub",
                          provider=self.NAME, pool_id="stub", latency_ms=1,
                          counted="reported")


class FailingProvider(Provider):
    NAME = "failing"

    def complete(self, prompt: str, max_tokens: Optional[int] = None) -> Completion:
        raise InferenceError("the pool refused", "start the runtime")


GOOD_ANSWER = ("PATTERN: a stage that produced nothing renders WARN\n"
               "REOPENS_WHEN: a stage is found rendering OK at zero output\n")


def no_sleep(_seconds: float) -> None:
    return None


def absorb_into(root: Path, sources: Path, envelope: Envelope = BIRTH_ENVELOPE) -> Any:
    return run_metabolism_stage(cadence("absorb"), "absorb", node_root=root,
                                now=NOW, envelope=envelope, source_dir=sources,
                                sleep=no_sleep)


# ---------------------------------------------------------------------------
# the seam: a declaration a reviewer reads must stay true
# ---------------------------------------------------------------------------


def test_a_null_seam_refuses_a_worker_rather_than_being_worked_around() -> None:
    with pytest.raises(RunnerError) as exc:
        resolve_worker_kind(cadence().stage("absorb"))
    assert "null" in exc.value.reason
    assert CALLABLES["absorb"] in exc.value.remedy


def test_a_callable_this_runner_does_not_declare_is_refused() -> None:
    """A cadence file must never become an execution vector."""
    with pytest.raises(RunnerError) as exc:
        resolve_worker_kind(cadence("absorb", callable="os:system").stage("absorb"))
    assert "exact string" in exc.value.remedy


def test_a_command_seam_is_left_to_the_host_saddle() -> None:
    with pytest.raises(RunnerError) as exc:
        resolve_worker_kind(cadence("absorb", seam="command",
                                    command="echo hi", callable=None
                                    ).stage("absorb"))
    assert "callable" in exc.value.remedy


def test_the_shipped_cadence_template_can_run_no_stage() -> None:
    """Today every stage is `seam: "null"` and the cadence can only SKIP."""
    template = yaml.safe_load(
        (REPO_ROOT / "config" / "metabolism-cadence.template.yaml").read_text(
            encoding="utf-8"))
    shipped = parse_cadence(template, birth=True)
    for stage in shipped.stages:
        assert stage.enabled is False
        assert stage.seam == "null"
        with pytest.raises(RunnerError):
            resolve_worker_kind(stage)


def test_an_unknown_stage_id_halts(tmp_path: Path) -> None:
    with pytest.raises(RunnerError):
        run_metabolism_stage(cadence(), "digest", node_root=tmp_path, now=NOW)


# ---------------------------------------------------------------------------
# a disabled stage skips, and is still recorded
# ---------------------------------------------------------------------------


def test_a_disabled_stage_skips_and_lands_in_the_ledger(tmp_path: Path) -> None:
    record = run_metabolism_stage(cadence(), "absorb", node_root=tmp_path,
                                  now=NOW, sleep=no_sleep)
    assert record.verdict == "SKIPPED"
    assert record.produced == {"absorbed": 0}
    rows = load_runs(tmp_path)
    assert len(rows) == 1 and rows[0]["verdict"] == "SKIPPED"


# ---------------------------------------------------------------------------
# absorb
# ---------------------------------------------------------------------------


def test_absorb_over_a_declared_directory_produces_work(tmp_path: Path,
                                                        sources: Path) -> None:
    record = absorb_into(tmp_path, sources)
    assert record.verdict == "OK"
    assert record.produced == {"absorbed": 3}
    written = list((tmp_path / ".intentops" / "knowledge" / "absorbed"
                    / "file").glob("*.md"))
    assert len(written) == 3


def test_absorb_with_no_declared_directory_halts(tmp_path: Path) -> None:
    with pytest.raises(RunnerError) as exc:
        run_metabolism_stage(cadence("absorb"), "absorb", node_root=tmp_path,
                             now=NOW, sleep=no_sleep)
    assert "no declared source directory" in exc.value.reason


def test_an_unreadable_source_directory_is_degraded_not_a_quiet_zero(
        tmp_path: Path) -> None:
    record = absorb_into(tmp_path, tmp_path / "nowhere")
    assert record.verdict == "DEGRADED"
    assert record.produced == {"absorbed": 0}
    assert record.unreadable, "DEGRADED must carry what could not be read"


def test_an_empty_source_directory_warns_and_never_reads_green(
        tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    record = absorb_into(tmp_path, empty)
    assert record.verdict == "WARN"
    assert "zero output is a state" in " ".join(record.notes)


# ---------------------------------------------------------------------------
# distill: the null provider is the default, and it WARNs
# ---------------------------------------------------------------------------


def test_the_null_provider_runs_produces_nothing_and_warns(
        tmp_path: Path, sources: Path, prompt: PromptTemplate) -> None:
    absorb_into(tmp_path, sources)
    record = run_metabolism_stage(
        cadence("distill"), "distill", node_root=tmp_path, now=NOW,
        provider=NullProvider(), prompt=prompt, sleep=no_sleep)
    assert record.verdict == "WARN"
    assert record.produced == {"candidates": 0}
    assert record.spend["tokens"] == 0
    assert record.spend["accounting"] == "none"


def test_the_default_provider_when_none_is_passed_is_the_null_one(
        tmp_path: Path, sources: Path, prompt: PromptTemplate) -> None:
    absorb_into(tmp_path, sources)
    record = run_metabolism_stage(cadence("distill"), "distill",
                                  node_root=tmp_path, now=NOW, prompt=prompt,
                                  sleep=no_sleep)
    assert record.detail["provider"]["is_null"] is True
    assert record.verdict == "WARN"


def test_a_good_answer_becomes_a_candidate_graded_inferred(
        tmp_path: Path, sources: Path, prompt: PromptTemplate) -> None:
    absorb_into(tmp_path, sources)
    provider = StubProvider([GOOD_ANSWER])
    record = run_metabolism_stage(
        cadence("distill"), "distill", node_root=tmp_path, now=NOW,
        provider=provider, prompt=prompt, sleep=no_sleep)
    assert record.verdict == "OK"
    assert record.produced == {"candidates": 3}
    files = sorted((tmp_path / CANDIDATES_RELDIR).glob("*.json"))
    assert len(files) == 3
    written = json.loads(files[0].read_text(encoding="utf-8"))
    assert written["grade"] == "INFERRED", (
        "a generator is not an instrument; nothing it returns is an observation")
    assert written["pattern"].startswith("a stage that produced nothing")
    assert written["reopens_when"]


def test_the_shipped_prompt_reaches_the_provider_with_the_document_in_it(
        tmp_path: Path, sources: Path, prompt: PromptTemplate) -> None:
    absorb_into(tmp_path, sources)
    provider = StubProvider([GOOD_ANSWER])
    run_metabolism_stage(cadence("distill"), "distill", node_root=tmp_path,
                         now=NOW, provider=provider, prompt=prompt,
                         sleep=no_sleep)
    asked = provider.asked[0]
    assert "PATTERN:" in asked and "REOPENS_WHEN:" in asked
    assert "the alpha document" in asked
    assert "{source}" not in asked and "{text}" not in asked


def test_no_claim_is_a_valid_answer_and_produces_nothing(
        tmp_path: Path, sources: Path, prompt: PromptTemplate) -> None:
    absorb_into(tmp_path, sources)
    record = run_metabolism_stage(
        cadence("distill"), "distill", node_root=tmp_path, now=NOW,
        provider=StubProvider(["NO_CLAIM"]), prompt=prompt, sleep=no_sleep)
    assert record.verdict == "WARN"
    assert record.detail["no_claim"] == 3
    assert record.detail["incomplete"] == []


def test_an_off_shape_answer_is_incomplete_and_is_never_repaired(
        tmp_path: Path, sources: Path, prompt: PromptTemplate) -> None:
    absorb_into(tmp_path, sources)
    record = run_metabolism_stage(
        cadence("distill"), "distill", node_root=tmp_path, now=NOW,
        provider=StubProvider(["here is a nice summary of the document"]),
        prompt=prompt, sleep=no_sleep)
    assert record.verdict == "WARN"
    assert len(record.detail["incomplete"]) == 3
    assert not list((tmp_path / CANDIDATES_RELDIR).glob("*.json"))


def test_a_run_whose_every_call_failed_is_degraded_never_warn(
        tmp_path: Path, sources: Path, prompt: PromptTemplate) -> None:
    absorb_into(tmp_path, sources)
    record = run_metabolism_stage(
        cadence("distill"), "distill", node_root=tmp_path, now=NOW,
        provider=FailingProvider(), prompt=prompt, sleep=no_sleep)
    assert record.verdict == "DEGRADED"


def test_token_accounting_is_carried_and_says_how_it_was_counted(
        tmp_path: Path, sources: Path, prompt: PromptTemplate) -> None:
    absorb_into(tmp_path, sources)
    record = run_metabolism_stage(
        cadence("distill"), "distill", node_root=tmp_path, now=NOW,
        provider=StubProvider([GOOD_ANSWER], tokens=7), prompt=prompt,
        sleep=no_sleep)
    assert record.spend["calls"] == 3
    assert record.spend["tokens"] == 3 * 14
    assert record.spend["measured_calls"] == 3
    assert record.spend["estimated_calls"] == 0
    assert record.spend["accounting"] == "measured"


# ---------------------------------------------------------------------------
# the envelope
# ---------------------------------------------------------------------------


def test_the_token_envelope_stops_the_run_and_says_so(
        tmp_path: Path, sources: Path, prompt: PromptTemplate) -> None:
    absorb_into(tmp_path, sources)
    # A short prompt so the per-call RESERVE (prompt estimate + answer ceiling)
    # is around 20 tokens, and a provider that reports 200 tokens a call. The
    # first call fits inside a 100-token envelope; after it, the SPENT 200
    # already exceeds it, so the second is never made.
    tiny = PromptTemplate(id="distill", purpose="p", max_tokens=8,
                          required_placeholders=("source", "text"),
                          template="{source}:{text}")
    provider = StubProvider([GOOD_ANSWER], tokens=100)
    tight = Envelope(max_tokens=100, max_items=25, duty_pause_s=0.0)
    record = run_metabolism_stage(
        cadence("distill"), "distill", node_root=tmp_path, now=NOW,
        envelope=tight, provider=provider, prompt=tiny, sleep=no_sleep)
    assert len(provider.asked) == 1, "the envelope did not stop the run"
    assert record.spend["stopped_on_envelope"] is True
    assert "envelope stopped this run" in " ".join(record.notes)
    assert record.produced == {"candidates": 1}


def test_the_item_envelope_caps_absorb_and_names_what_was_not_read(
        tmp_path: Path, sources: Path) -> None:
    record = absorb_into(tmp_path, sources,
                         Envelope(max_tokens=1000, max_items=2,
                                  duty_pause_s=0.0))
    assert record.produced == {"absorbed": 2}
    assert "not yet read" in " ".join(record.notes)


def test_the_duty_pause_fires_between_items(tmp_path: Path,
                                            sources: Path) -> None:
    paused: List[float] = []
    run_metabolism_stage(cadence("absorb"), "absorb", node_root=tmp_path,
                         now=NOW, source_dir=sources,
                         envelope=Envelope(max_tokens=100, max_items=25,
                                           duty_pause_s=0.5),
                         sleep=paused.append)
    assert paused == [0.5, 0.5, 0.5]


def test_a_zero_duty_pause_sleeps_not_at_all(tmp_path: Path,
                                             sources: Path) -> None:
    paused: List[float] = []
    absorb_into(tmp_path, sources,
                Envelope(max_tokens=100, max_items=25, duty_pause_s=0.0))
    assert paused == []


@pytest.mark.parametrize("bad", [{"max_tokens": 0}, {"max_items": 0},
                                 {"duty_pause_s": -1}, {"max_tokens": "many"}])
def test_an_envelope_that_bounds_nothing_is_refused(bad: Dict[str, Any]) -> None:
    kwargs: Dict[str, Any] = {"max_tokens": 10, "max_items": 1,
                              "duty_pause_s": 0.0}
    kwargs.update(bad)
    with pytest.raises(RunnerError):
        Envelope(**kwargs)


def test_the_shipped_envelope_is_visible_in_every_record(
        tmp_path: Path, sources: Path) -> None:
    record = absorb_into(tmp_path, sources)
    assert record.envelope == BIRTH_ENVELOPE.to_row()
    assert load_runs(tmp_path)[0]["envelope"]["max_tokens"] > 0


# ---------------------------------------------------------------------------
# crystallize and method
# ---------------------------------------------------------------------------


def test_the_whole_chain_absorb_distill_crystallize_method(
        tmp_path: Path, sources: Path, prompt: PromptTemplate) -> None:
    absorb_into(tmp_path, sources)
    run_metabolism_stage(cadence("distill"), "distill", node_root=tmp_path,
                         now=NOW, provider=StubProvider([GOOD_ANSWER]),
                         prompt=prompt, sleep=no_sleep)
    crystallized = run_metabolism_stage(cadence("crystallize"), "crystallize",
                                        node_root=tmp_path, now=NOW,
                                        sleep=no_sleep)
    assert crystallized.verdict == "OK"
    assert crystallized.produced == {"crystallized": 3}
    docs = sorted((tmp_path / CRYSTALLIZED_RELDIR).glob("CRYST-*.md"))
    assert [d.stem for d in docs] == ["CRYST-001", "CRYST-002", "CRYST-003"]
    assert "INFERRED" in docs[0].read_text(encoding="utf-8")

    method = run_metabolism_stage(cadence("method"), "method",
                                  node_root=tmp_path, now=NOW, sleep=no_sleep)
    assert method.verdict == "OK"
    assert method.produced == {"methods": 3}
    stations = {r["station"] for r in method.detail["records"]}
    assert stations == {"described"}, (
        "a station claimed without a literal reference is a promotion "
        "nobody earned")


def test_crystallize_ids_do_not_collide_across_runs(
        tmp_path: Path, sources: Path, prompt: PromptTemplate) -> None:
    absorb_into(tmp_path, sources)
    run_metabolism_stage(cadence("distill"), "distill", node_root=tmp_path,
                         now=NOW, provider=StubProvider([GOOD_ANSWER]),
                         prompt=prompt, sleep=no_sleep)
    run_metabolism_stage(cadence("crystallize"), "crystallize",
                         node_root=tmp_path, now=NOW, sleep=no_sleep)
    (tmp_path / CANDIDATES_RELDIR / "extra.json").write_text(json.dumps({
        "source": "extra.md", "pattern": "another claim",
        "reopens_when": "a counterexample"}), encoding="utf-8")
    second = run_metabolism_stage(cadence("crystallize"), "crystallize",
                                  node_root=tmp_path, now=NOW, sleep=no_sleep)
    ids = sorted(p.stem for p in (tmp_path / CRYSTALLIZED_RELDIR).glob("*.md"))
    assert len(ids) == len(set(ids))
    assert "CRYST-004" in ids
    assert second.produced["crystallized"] >= 1


def test_a_candidate_that_does_not_validate_is_not_crystallized_and_not_lost(
        tmp_path: Path) -> None:
    out = tmp_path / CANDIDATES_RELDIR
    out.mkdir(parents=True)
    (out / "hollow.json").write_text(json.dumps({
        "source": "hollow.md", "pattern": "", "reopens_when": ""}),
        encoding="utf-8")
    record = run_metabolism_stage(cadence("crystallize"), "crystallize",
                                  node_root=tmp_path, now=NOW, sleep=no_sleep)
    assert record.verdict == "WARN"
    assert record.produced == {"crystallized": 0}
    assert record.detail["refused"][0]["candidate"] == "hollow.json"
    assert (out / "hollow.json").is_file(), "a refused candidate is kept"
    assert not list((tmp_path / CRYSTALLIZED_RELDIR).glob("*.md"))


def test_an_unreadable_candidate_file_is_degraded(tmp_path: Path) -> None:
    out = tmp_path / CANDIDATES_RELDIR
    out.mkdir(parents=True)
    (out / "broken.json").write_text("{not json", encoding="utf-8")
    record = run_metabolism_stage(cadence("crystallize"), "crystallize",
                                  node_root=tmp_path, now=NOW, sleep=no_sleep)
    assert record.verdict == "DEGRADED"


def test_method_over_nothing_warns(tmp_path: Path) -> None:
    record = run_metabolism_stage(cadence("method"), "method",
                                  node_root=tmp_path, now=NOW, sleep=no_sleep)
    assert record.verdict == "WARN"


# ---------------------------------------------------------------------------
# the ledger
# ---------------------------------------------------------------------------


def test_every_run_is_appended_including_the_empty_ones(
        tmp_path: Path, sources: Path, prompt: PromptTemplate) -> None:
    absorb_into(tmp_path, sources)
    run_metabolism_stage(cadence("distill"), "distill", node_root=tmp_path,
                         now=NOW, provider=NullProvider(), prompt=prompt,
                         sleep=no_sleep)
    run_metabolism_stage(cadence(), "crystallize", node_root=tmp_path,
                         now=NOW, sleep=no_sleep)
    rows = load_runs(tmp_path)
    assert [r["verdict"] for r in rows] == ["OK", "WARN", "SKIPPED"]
    assert all(r["schema"] == "metabolism-run/v1" for r in rows)


def test_the_ledger_is_append_only_jsonl_and_survives_a_second_writer(
        tmp_path: Path, sources: Path) -> None:
    absorb_into(tmp_path, sources)
    absorb_into(tmp_path, sources)
    path = tmp_path / RUNS_RELPATH
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln]
    assert len(lines) == 2
    assert all(json.loads(ln)["stage"] == "absorb" for ln in lines)


def test_an_unreadable_ledger_line_is_named_never_skipped(
        tmp_path: Path, sources: Path) -> None:
    absorb_into(tmp_path, sources)
    path = tmp_path / RUNS_RELPATH
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")
    rows = load_runs(tmp_path)
    assert len(rows) == 2
    assert rows[1]["unreadable_line"] == 2


def test_append_can_be_suppressed_for_a_caller_that_owns_the_write(
        tmp_path: Path, sources: Path) -> None:
    run_metabolism_stage(cadence("absorb"), "absorb", node_root=tmp_path,
                         now=NOW, source_dir=sources, sleep=no_sleep,
                         append=False)
    assert load_runs(tmp_path) == []


# ---------------------------------------------------------------------------
# the plan: a dry run contacts nothing and writes nothing
# ---------------------------------------------------------------------------


def test_a_dry_run_opens_no_socket_even_with_an_enabled_pool(
        tmp_path: Path, sources: Path, prompt: PromptTemplate) -> None:
    absorb_into(tmp_path, sources)
    pool = parse_pool({
        "id": "local-inference", "kind": "model-pool",
        "limit_shape": "none-observed", "telemetry_source": "the runtime log",
        "max_concurrent": 1, "kill_switch": "stop the runtime",
        "data_fence": "local_only", "as_of": "2026-09-06",
        "endpoint": "http://127.0.0.1:9/v1", "model": "a-local-model",
        "max_tokens": 64, "timeout_s": 5, "enabled": True})

    def refusing_opener(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("the dry run opened a socket")

    from intentops_core.inference.openai_compat import OpenAICompatHTTPProvider
    live = OpenAICompatHTTPProvider(pool, opener=refusing_opener)
    plan = plan_stage(cadence("distill"), "distill", node_root=tmp_path,
                      provider=live, prompt=prompt)
    assert plan.runnable is True
    assert plan.worker["contacts_network"] is True, (
        "the plan must SAY that a real run would leave the process")
    assert plan.worker["absorbed_documents"] == 3
    assert plan.worker["would_call"] == 3
    assert plan.worker["provider"]["endpoint_class"] == "loopback"


def test_a_dry_run_writes_nothing(tmp_path: Path, sources: Path,
                                  prompt: PromptTemplate) -> None:
    absorb_into(tmp_path, sources)
    before = sorted(p.relative_to(tmp_path).as_posix()
                    for p in tmp_path.rglob("*") if p.is_file())
    plan_stage(cadence("distill"), "distill", node_root=tmp_path,
               provider=NullProvider(), prompt=prompt)
    after = sorted(p.relative_to(tmp_path).as_posix()
                   for p in tmp_path.rglob("*") if p.is_file())
    assert before == after


def test_a_plan_over_a_disabled_or_null_stage_names_both_blockers(
        tmp_path: Path, prompt: PromptTemplate) -> None:
    plan = plan_stage(cadence(), "distill", node_root=tmp_path,
                      provider=NullProvider(), prompt=prompt)
    assert plan.runnable is False
    joined = " ".join(plan.blockers)
    assert "disabled" in joined
    assert "null" in joined


def test_the_plan_renders_the_envelope_and_says_nothing_was_contacted(
        tmp_path: Path, prompt: PromptTemplate) -> None:
    text = plan_stage(cadence("distill"), "distill", node_root=tmp_path,
                      provider=NullProvider(), prompt=prompt).render()
    assert "max_tokens=4096" in text
    assert "nothing was contacted and nothing was written" in text


# ---------------------------------------------------------------------------
# a real loopback round trip through the runner
# ---------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - the stdlib's own naming
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        raw = json.dumps({
            "model": "a-local-model",
            "choices": [{"message": {"content": GOOD_ANSWER},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 30, "completion_tokens": 12}},
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args: Any) -> None:
        return


@pytest.fixture()
def local_endpoint() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_distill_against_a_fake_local_endpoint_end_to_end(
        tmp_path: Path, sources: Path, prompt: PromptTemplate,
        local_endpoint: str) -> None:
    absorb_into(tmp_path, sources)
    estate = tmp_path / "estate"
    estate.mkdir()
    (estate / "RESOURCES.yaml").write_text(yaml.safe_dump({
        "schema": "estate-resources/v1", "as_of": "2026-09-06",
        "entries": [{
            "id": "local-inference", "kind": "model-pool",
            "limit_shape": "none-observed",
            "telemetry_source": "the runtime's own request log",
            "max_concurrent": 1, "kill_switch": "stop the runtime",
            "data_fence": "local_only", "as_of": "2026-09-06",
            "endpoint": local_endpoint, "model": "a-local-model",
            "max_tokens": 64, "timeout_s": 10, "enabled": True}]},
        default_flow_style=False, sort_keys=False), encoding="utf-8")

    provider = runner_mod._resolve_provider(str(estate), None)
    assert provider.is_null is False
    record = run_metabolism_stage(
        cadence("distill"), "distill", node_root=tmp_path, now=NOW,
        provider=provider, prompt=prompt, sleep=no_sleep)
    assert record.verdict == "OK"
    assert record.produced == {"candidates": 3}
    assert record.spend["accounting"] == "measured"
    assert record.spend["tokens"] == 3 * 42


def test_an_estate_with_no_pool_yields_the_null_provider(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    estate.mkdir()
    (estate / "RESOURCES.yaml").write_text(
        "schema: estate-resources/v1\nas_of: \"2026-09-06\"\nentries: []\n",
        encoding="utf-8")
    assert runner_mod._resolve_provider(str(estate), None).is_null is True


def test_no_estate_at_all_yields_the_null_provider() -> None:
    assert runner_mod._resolve_provider(None, None).is_null is True


# ---------------------------------------------------------------------------
# the prompt file
# ---------------------------------------------------------------------------


def test_the_shipped_prompt_file_loads_and_declares_distill() -> None:
    prompts = load_prompts(REPO_ROOT / PROMPTS_RELPATH)
    assert "distill" in prompts
    assert prompts["distill"].required_placeholders == ("source", "text")
    assert prompts["distill"].max_tokens > 0


def test_a_missing_prompt_file_halts_rather_than_improvising(
        tmp_path: Path) -> None:
    with pytest.raises(RunnerError) as exc:
        load_prompts(tmp_path / "nowhere.yaml")
    assert "reviewed governance surface" in exc.value.remedy


def _prompt_doc(**overrides: Any) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "id": "distill", "purpose": "a purpose", "max_tokens": 32,
        "required_placeholders": ["source", "text"],
        "template": "{source}\n{text}\n"}
    row.update(overrides)
    return {"schema": "metabolism-prompts/v1", "as_of": "2026-09-06",
            "prompts": [row]}


def _write_prompts(tmp_path: Path, doc: Dict[str, Any]) -> Path:
    path = tmp_path / "prompts.yaml"
    path.write_text(yaml.safe_dump(doc, default_flow_style=False,
                                   sort_keys=False), encoding="utf-8")
    return path


def test_a_promised_placeholder_the_template_lacks_halts(tmp_path: Path) -> None:
    path = _write_prompts(tmp_path, _prompt_doc(template="{source}\n"))
    with pytest.raises(RunnerError) as exc:
        load_prompts(path)
    assert "text" in exc.value.reason


@pytest.mark.parametrize("field", ["id", "purpose", "max_tokens",
                                   "required_placeholders", "template"])
def test_a_missing_prompt_field_halts(tmp_path: Path, field: str) -> None:
    doc = _prompt_doc()
    doc["prompts"][0].pop(field)
    with pytest.raises(RunnerError):
        load_prompts(_write_prompts(tmp_path, doc))


def test_an_unknown_prompt_schema_is_refused(tmp_path: Path) -> None:
    doc = _prompt_doc()
    doc["schema"] = "metabolism-prompts/v9"
    with pytest.raises(RunnerError):
        load_prompts(_write_prompts(tmp_path, doc))


def test_a_file_with_no_distill_prompt_halts(tmp_path: Path) -> None:
    doc = _prompt_doc(id="method")
    with pytest.raises(RunnerError) as exc:
        load_prompts(_write_prompts(tmp_path, doc))
    assert "will not improvise" in exc.value.remedy


def test_rendering_without_a_declared_placeholder_halts() -> None:
    template = PromptTemplate(id="distill", purpose="p", max_tokens=8,
                              required_placeholders=("source", "text"),
                              template="{source}{text}")
    with pytest.raises(RunnerError):
        template.render(source="a.md")


def test_rendering_leaves_unrelated_braces_alone() -> None:
    """Prose contains braces; a template that raised on one would fail late."""
    template = PromptTemplate(id="distill", purpose="p", max_tokens=8,
                              required_placeholders=("source",),
                              template="answer in {json} shape for {source}")
    assert template.render(source="a.md") == "answer in {json} shape for a.md"


# ---------------------------------------------------------------------------
# the CLI
# ---------------------------------------------------------------------------


def test_the_cli_dry_run_prints_the_plan_and_exits_nonzero_when_blocked(
        tmp_path: Path, capsys: Any) -> None:
    node = tmp_path / "node"
    (node / ".intentops" / "metabolism").mkdir(parents=True)
    (node / ".intentops" / "metabolism" / "cadence.yaml").write_text(
        yaml.safe_dump(cadence_doc(), default_flow_style=False,
                       sort_keys=False), encoding="utf-8")
    code = runner_mod.main(["--stage", "distill", "--node-root", str(node),
                            "--prompts", str(REPO_ROOT / PROMPTS_RELPATH),
                            "--dry-run"])
    out = capsys.readouterr().out
    assert "metabolism run --stage distill --dry-run" in out
    assert "BLOCKED" in out
    assert code == 1, "a plan that cannot run must not exit clean"


def test_the_cli_dry_run_exits_clean_on_a_runnable_stage(
        tmp_path: Path, sources: Path, capsys: Any) -> None:
    node = tmp_path / "node"
    (node / ".intentops" / "metabolism").mkdir(parents=True)
    (node / ".intentops" / "metabolism" / "cadence.yaml").write_text(
        yaml.safe_dump(cadence_doc("absorb"), default_flow_style=False,
                       sort_keys=False), encoding="utf-8")
    code = runner_mod.main(["--stage", "absorb", "--node-root", str(node),
                            "--source-dir", str(sources), "--dry-run"])
    assert code == 0
    assert "files_found" in capsys.readouterr().out


def test_the_cli_run_exits_nonzero_on_warn(tmp_path: Path, capsys: Any) -> None:
    """A WARN is a reading a checklist must not step over."""
    node = tmp_path / "node"
    (node / ".intentops" / "metabolism").mkdir(parents=True)
    (node / ".intentops" / "metabolism" / "cadence.yaml").write_text(
        yaml.safe_dump(cadence_doc("distill"), default_flow_style=False,
                       sort_keys=False), encoding="utf-8")
    code = runner_mod.main(["--stage", "distill", "--node-root", str(node),
                            "--prompts", str(REPO_ROOT / PROMPTS_RELPATH)])
    assert code == 1
    assert "WARN" in capsys.readouterr().out


def test_the_run_verb_is_registered_on_the_metabolism_parser() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    metabolism_cli.add_parser(sub)
    args = parser.parse_args(["metabolism", "run", "--stage", "absorb",
                              "--dry-run"])
    assert args.metabolism_command == "run"
    assert args.dry_run is True


def test_the_metabolism_cli_selftest_fold_includes_the_runner() -> None:
    ok, report = metabolism_cli.selftest()
    assert ok, report
    assert "metabolism.runner" in report


# ---------------------------------------------------------------------------
# the detector can fire
# ---------------------------------------------------------------------------


def test_the_runner_selftest_passes() -> None:
    ok, report = runner_mod.selftest()
    assert ok, report


def test_the_runner_selftest_can_actually_fail() -> None:
    original = runner_mod.resolve_worker_kind
    try:
        runner_mod.resolve_worker_kind = lambda stage: stage.id  # type: ignore[assignment]
        ok, report = runner_mod.selftest()
        assert not ok, "the selftest passed with the seam refusal disabled"
        assert "null seam" in report
    finally:
        runner_mod.resolve_worker_kind = original  # type: ignore[assignment]


def test_the_run_ledger_is_a_birth_organ_with_a_declared_write_model() -> None:
    from intentops_core.genesis import organs as organs_mod

    row = [o for o in organs_mod.BIRTH_ORGANS if o.id == "metabolism-runs"]
    assert row, "the run ledger must be instantiated empty at genesis"
    assert row[0].write_model == "append-only-jsonl"
    assert row[0].relpath == RUNS_RELPATH.as_posix()


def test_the_runner_imports_no_vendor_sdk() -> None:
    text = (PACKAGE_ROOT / "intentops_core" / "metabolism"
            / "runner.py").read_text(encoding="utf-8")
    for token in ("import openai", "from openai", "import anthropic",
                  "import httpx", "import requests", "import importlib"):
        assert token not in text, f"runner.py carries {token!r}"


# ---------------------------------------------------------------------------
# the CLI's own verb surface -- a detector that cannot be invoked is broken
# ---------------------------------------------------------------------------


def test_selftest_is_reachable_without_naming_a_stage(
        capsys: Any) -> None:
    """``--selftest`` is a whole-module verb and names no stage.

    ``--stage`` was declared ``required=True``, and argparse enforces that
    BEFORE any code in ``main`` runs -- so both
    ``python -m intentops_core.metabolism.runner --selftest`` and
    ``intentops metabolism run --selftest`` (which delegates straight here)
    exited 2 with a usage line. The registry never noticed, because it calls
    the in-process ``selftest()`` callable; the only surface that broke was the
    one an operator actually types.
    """
    import intentops_core.metabolism.runner as runner_mod

    assert runner_mod.main(["--selftest"]) == 0
    out = capsys.readouterr().out
    assert "metabolism.runner" in out


def test_a_run_with_no_stage_still_halts() -> None:
    """Dropping ``required`` must not turn a missing stage into a default."""
    import intentops_core.metabolism.runner as runner_mod

    with pytest.raises(SystemExit) as caught:
        runner_mod.main([])
    assert caught.value.code == 2


def test_the_metabolism_cli_reaches_the_runner_selftest(capsys: Any) -> None:
    """The verb an operator types, end to end, with no stage named."""
    from intentops_core.metabolism import cli as metabolism_cli

    assert metabolism_cli.main(["metabolism", "run", "--selftest"]) == 0
    assert "metabolism.runner" in capsys.readouterr().out
