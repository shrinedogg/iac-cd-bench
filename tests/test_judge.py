"""
Unit tests for the rubric LLM judge and the idiom score axis.

Every test drives a stubbed adapter — nothing here touches the network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench import judge as judge_mod
from bench.judge import (
    Criterion,
    JudgeError,
    RubricJudge,
    build_prompt,
    load_rubric,
    load_spec,
    parse_verdict,
    read_answer_key,
    read_submission,
    weighted_score,
)
from bench.score import compute_score, idiom_score, judge_metadata

ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / "tasks"
T1 = TASKS_DIR / "knr-ops" / "T1-comprehend"
T2 = TASKS_DIR / "knr-ops" / "T2-generate"


class StubAdapter:
    """Records prompts, replays canned responses. No HTTP."""

    def __init__(self, replies: list[str], name: str = "stub-judge"):
        self.replies = list(replies)
        self.prompts: list[str] = []
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def complete(self, prompt: str, files: list[Path]) -> dict:
        self.prompts.append(prompt)
        if not self.replies:
            raise AssertionError("StubAdapter called more times than it has replies")
        return {"content": self.replies.pop(0), "input_tokens": 0, "output_tokens": 0}


def verdict_json(scores: list[float]) -> str:
    return json.dumps({
        "scores": [
            {"index": i, "score": s, "justification": f"j{i}"}
            for i, s in enumerate(scores, 1)
        ]
    })


# ── rubric loading ────────────────────────────────────────────────────────

def test_load_rubric_from_real_task():
    rubric = load_rubric(load_spec(T1))
    assert len(rubric) == 5
    assert rubric[0].criterion.startswith("Identifies that Flux reconciles")
    assert [c.weight for c in rubric] == [1, 2, 2, 2, 1]


def test_load_rubric_ignores_malformed_entries():
    spec = {"rubric": [
        {"criterion": "kept", "weight": 2},
        {"weight": 3},                       # no criterion text
        {"criterion": "zero weight", "weight": 0},
        {"criterion": "bad weight", "weight": "x"},  # falls back to 1
        "not a mapping",
    ]}
    rubric = load_rubric(spec)
    assert [(c.criterion, c.weight) for c in rubric] == [("kept", 2.0), ("bad weight", 1.0)]


def test_non_rubric_task_has_no_rubric():
    assert load_rubric(load_spec(T2)) == []


# tasks/schema.md requires golden/ for every task, but these two ship none.
# The judge degrades to rubric-only grading there; the gap is tracked as task
# content, not judge code. New gaps should fail this test.
KNOWN_MISSING_ANSWER_KEYS = {
    "pulumi-typescript/T1-comprehend",
    "pulumi-typescript/T5-review",
}


def test_rubric_tasks_ship_an_answer_key():
    missing = set()
    for spec_path in sorted(TASKS_DIR.rglob("spec.yaml")):
        task_dir = spec_path.parent
        if not load_rubric(load_spec(task_dir)):
            continue
        if not read_answer_key(task_dir).strip():
            missing.add(f"{task_dir.parent.name}/{task_dir.name}")
    assert missing <= KNOWN_MISSING_ANSWER_KEYS, f"new rubric tasks without a golden key: {missing - KNOWN_MISSING_ANSWER_KEYS}"


def test_judge_grades_without_an_answer_key(tmp_path):
    """A rubric task missing golden/ still grades, on rubric text alone."""
    spec = load_spec(T1)
    prompt = build_prompt(spec, load_rubric(spec), "", "some answer")
    assert "(none provided)" in prompt


# ── prompt ────────────────────────────────────────────────────────────────

def test_prompt_contains_rubric_answer_key_and_submission():
    spec = load_spec(T1)
    rubric = load_rubric(spec)
    prompt = build_prompt(spec, rubric, "REFERENCE KEY BODY", "MODEL SUBMISSION BODY")
    assert "REFERENCE KEY BODY" in prompt
    assert "MODEL SUBMISSION BODY" in prompt
    for c in rubric:
        assert c.criterion in prompt
    assert "(weight 2)" in prompt
    assert "Emit exactly 5 entries" in prompt
    assert "<submission>" in prompt and "</submission>" in prompt


def test_prompt_truncates_oversized_submission():
    spec = load_spec(T1)
    prompt = build_prompt(spec, load_rubric(spec), "key", "x" * 200000)
    assert "truncated at" in prompt
    assert len(prompt) < 200000


def test_prompt_hash_is_stable_and_short():
    assert judge_mod.prompt_hash() == judge_mod.prompt_hash()
    assert len(judge_mod.prompt_hash()) == 16


# ── parsing ───────────────────────────────────────────────────────────────

RUBRIC2 = [Criterion("a", 1), Criterion("b", 3)]


def test_parse_plain_json():
    criteria = parse_verdict(verdict_json([1.0, 0.5]), RUBRIC2)
    assert [c["score"] for c in criteria] == [1.0, 0.5]
    assert [c["weight"] for c in criteria] == [1, 3]
    assert criteria[0]["criterion"] == "a"


def test_parse_fenced_json():
    body = "```json\n" + verdict_json([0.0, 1.0]) + "\n```"
    assert [c["score"] for c in parse_verdict(body, RUBRIC2)] == [0.0, 1.0]


def test_parse_json_with_surrounding_prose():
    body = "Here is my grading:\n" + verdict_json([0.25, 0.75]) + "\nHope that helps."
    assert [c["score"] for c in parse_verdict(body, RUBRIC2)] == [0.25, 0.75]


def test_parse_reorders_by_index():
    body = json.dumps({"scores": [
        {"index": 2, "score": 1.0, "justification": "second"},
        {"index": 1, "score": 0.0, "justification": "first"},
    ]})
    criteria = parse_verdict(body, RUBRIC2)
    assert [c["justification"] for c in criteria] == ["first", "second"]


def test_parse_snaps_off_anchor_scores():
    body = json.dumps({"scores": [
        {"index": 1, "score": 0.9, "justification": ""},
        {"index": 2, "score": 7, "justification": ""},   # clamped to 1.0
    ]})
    assert [c["score"] for c in parse_verdict(body, RUBRIC2)] == [1.0, 1.0]


@pytest.mark.parametrize("body", [
    "",
    "not json at all",
    json.dumps({"verdict": "good"}),                      # no scores list
    json.dumps({"scores": [{"index": 1, "score": 1.0}]}),  # wrong count
    json.dumps({"scores": [{"index": 1, "score": 1.0}, {"index": 1, "score": 0.0}]}),
    json.dumps({"scores": [{"index": 1, "score": "high"}, {"index": 2, "score": 0.0}]}),
    json.dumps({"scores": [{"index": 9, "score": 1.0}, {"index": 2, "score": 0.0}]}),
    json.dumps({"scores": ["nope", "nope"]}),
])
def test_parse_rejects_malformed(body):
    with pytest.raises(JudgeError):
        parse_verdict(body, RUBRIC2)


def test_weighted_score():
    criteria = parse_verdict(verdict_json([1.0, 0.0]), RUBRIC2)
    assert weighted_score(criteria) == pytest.approx(0.25)  # 1*1 / 4
    assert weighted_score([]) == 0.0


# ── judge end to end (stubbed) ────────────────────────────────────────────

def test_judge_scores_task_with_stub():
    stub = StubAdapter([verdict_json([1.0, 1.0, 0.5, 0.0, 1.0])])
    verdict = RubricJudge(stub, "stub-judge").score_task(T1, content="my answer")

    assert verdict is not None
    # weights 1,2,2,2,1 -> (1 + 2 + 1 + 0 + 1) / 8
    assert verdict["idiom"] == pytest.approx(5 / 8)
    assert verdict["judge_model"] == "stub-judge"
    assert verdict["prompt_sha256"] == judge_mod.prompt_hash()
    assert len(verdict["criteria"]) == 5
    assert "my answer" in stub.prompts[0]
    assert len(stub.prompts) == 1


def test_judge_returns_none_for_task_without_rubric():
    stub = StubAdapter([])
    assert RubricJudge(stub).score_task(T2, content="whatever") is None
    assert stub.prompts == []


def test_judge_retries_once_on_malformed_output():
    stub = StubAdapter(["I think it's pretty good!", verdict_json([1, 1, 1, 1, 1])])
    verdict = RubricJudge(stub, "stub-judge").score_task(T1, content="answer")
    assert verdict["idiom"] == 1.0
    assert len(stub.prompts) == 2
    assert stub.prompts[1].endswith(judge_mod.RETRY_SUFFIX)


def test_judge_raises_when_retry_also_malformed():
    stub = StubAdapter(["garbage", "still garbage"])
    with pytest.raises(JudgeError):
        RubricJudge(stub, "stub-judge").score_task(T1, content="answer")
    assert len(stub.prompts) == 2


def test_judge_reads_workspace_output_file(tmp_path):
    (tmp_path / "model_output.md").write_text("WORKSPACE ANSWER TEXT")
    stub = StubAdapter([verdict_json([0, 0, 0, 0, 0])])
    RubricJudge(stub, "stub-judge").score_task(T1, workspace=tmp_path)
    assert "WORKSPACE ANSWER TEXT" in stub.prompts[0]


def test_read_submission_falls_back_to_workspace_files(tmp_path):
    (tmp_path / "answer.md").write_text("fallback body")
    assert "fallback body" in read_submission(tmp_path)
    assert read_submission(None) == ""


def test_judge_temperature_matches_model_family():
    assert judge_mod.judge_temperature("claude-haiku-4-5") == 0.0
    assert judge_mod.judge_temperature("claude-sonnet-4-5-20250929") == 0.0
    # The 4.7+/5 family rejects sampling parameters outright.
    assert judge_mod.judge_temperature("claude-opus-5") is None
    assert judge_mod.judge_temperature("claude-sonnet-5") is None


def test_build_judge_uses_runner_adapters(monkeypatch):
    from bench.runner import AnthropicAdapter

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("BENCH_JUDGE_MODEL", raising=False)
    judge = judge_mod.build_judge()
    assert isinstance(judge.adapter, AnthropicAdapter)
    assert judge.model == judge_mod.DEFAULT_JUDGE_MODEL
    assert judge.adapter.temperature == 0.0

    judge = judge_mod.build_judge(model="claude-opus-5")
    assert judge.adapter.temperature is None


def test_anthropic_adapter_omits_temperature_by_default():
    from bench.runner import AnthropicAdapter

    assert AnthropicAdapter("claude-opus-5", "sk-test").temperature is None


def test_anthropic_adapter_payload_carries_judge_temperature(monkeypatch):
    """The judge's temperature reaches the request body (httpx stubbed out)."""
    import httpx

    from bench.runner import AnthropicAdapter

    sent: dict = {}

    class FakeResponse:
        status_code = 200
        is_success = True

        def raise_for_status(self):
            return None

        def json(self):
            return {"content": [{"type": "text", "text": "{}"}],
                    "usage": {"input_tokens": 1, "output_tokens": 1}}

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.update(json)
        return FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)

    AnthropicAdapter("claude-haiku-4-5", "sk-test", temperature=0.0).complete("hi", [])
    assert sent["temperature"] == 0.0
    assert "thinking" not in sent

    sent.clear()
    AnthropicAdapter("claude-haiku-4-5", "sk-test").complete("hi", [])
    assert "temperature" not in sent


# ── score.py wiring ───────────────────────────────────────────────────────

def test_idiom_axis_uses_judge_verdict():
    result = {
        "stages": {
            "lint": {"passed": True},
            "static": {"passed": True},
            "semantic": {"passed": True, "passed_count": 2, "total_count": 2,
                         "safety_pass": True},
        },
        "judge": {"idiom": 0.75, "judge_model": "stub", "prompt_sha256": "abc",
                  "criteria": []},
    }
    scores = compute_score(result)
    assert scores["idiom"] == 0.75
    # correctness 1*3 + completeness 1*2 + idiom .75*1 + safety 1*2, over a
    # denominator of 8. `consistency` is no longer in the per-run composite
    # (#7): it is a cross-run metric with no per-run value, and including it as
    # a hardcoded 0.0 with weight 1 capped every composite below what it
    # claimed to measure.
    assert scores["composite"] == pytest.approx((3 + 2 + 0.75 + 2) / 8)
    assert judge_metadata(result) == {"judge_model": "stub", "prompt_sha256": "abc"}


def test_idiom_axis_degrades_without_judge():
    result = {"stages": {"lint": {"passed": True}}}
    assert compute_score(result)["idiom"] == 0.0
    assert judge_metadata(result) is None


@pytest.mark.parametrize("verdict", [None, {}, {"idiom": "high"}, {"idiom": None}, "nope"])
def test_idiom_score_is_defensive(verdict):
    assert idiom_score({"judge": verdict}) == 0.0


def test_idiom_score_clamps():
    assert idiom_score({"judge": {"idiom": 5}}) == 1.0
    assert idiom_score({"judge": {"idiom": -1}}) == 0.0


def test_aggregate_surfaces_judge_metadata():
    from bench.score import aggregate_scores

    runs = []
    for i in range(2):
        r = {
            "model": "m", "stack": "knr-ops", "task": "T1-comprehend", "run": i,
            "stages": {"lint": {"passed": True}, "static": {"passed": True},
                       "semantic": {"passed": True}},
            "judge": {"idiom": 0.5, "judge_model": "stub-judge",
                      "prompt_sha256": "deadbeef", "criteria": []},
        }
        r["score"] = compute_score(r)
        runs.append(r)

    agg = aggregate_scores(runs)["m/knr-ops/T1-comprehend"]
    assert agg["judged_runs"] == 2
    assert agg["avg_idiom"] == 0.5
    assert agg["judge_models"] == ["stub-judge"]
    assert agg["judge_prompts"] == ["deadbeef"]


def test_aggregate_omits_judge_metadata_when_unjudged():
    from bench.score import aggregate_scores

    r = {"model": "m", "stack": "knr-ops", "task": "T1-comprehend", "run": 0,
         "stages": {"lint": {"passed": True}}}
    r["score"] = compute_score(r)
    agg = aggregate_scores([r])["m/knr-ops/T1-comprehend"]
    assert "judged_runs" not in agg
    assert "judge_models" not in agg


# ── runner hook ───────────────────────────────────────────────────────────

def test_runner_run_task_records_judge_verdict(tmp_path, monkeypatch):
    """run_task attaches the verdict for rubric tasks without touching the network."""
    from bench import runner

    class StubModel:
        name = "stub-model"

        def complete(self, prompt, files):
            return {"content": "the model's answer", "input_tokens": 1, "output_tokens": 2}

    stub_judge = RubricJudge(StubAdapter([verdict_json([1, 1, 1, 1, 1])]), "stub-judge")
    results = runner.run_task(T1, StubModel(), k=1, judge=stub_judge)

    assert len(results) == 1
    assert results[0]["judge"]["idiom"] == 1.0
    assert results[0]["judge"]["judge_model"] == "stub-judge"
    assert compute_score(results[0])["idiom"] == 1.0


def test_runner_judge_failure_does_not_fail_the_run():
    from bench import runner

    class StubModel:
        name = "stub-model"

        def complete(self, prompt, files):
            return {"content": "answer", "input_tokens": 0, "output_tokens": 0}

    class ExplodingJudge:
        def score_task(self, *a, **kw):
            raise RuntimeError("judge exploded")

    results = runner.run_task(T1, StubModel(), k=1, judge=ExplodingJudge())
    assert "judge" not in results[0]
    assert "judge exploded" in results[0]["judge_error"]
    assert "error" not in results[0]


def test_runner_without_judge_flag_writes_no_verdict():
    from bench import runner

    class StubModel:
        name = "stub-model"

        def complete(self, prompt, files):
            return {"content": "answer", "input_tokens": 0, "output_tokens": 0}

    results = runner.run_task(T1, StubModel(), k=1)
    assert "judge" not in results[0]
    assert compute_score(results[0])["idiom"] == 0.0
