"""
Unit tests for the run-validity gate (#59) — bench/validity.py — and its
wiring into bench/runner.py, bench/score.py, and bench/report.py.

No network, no subprocess: everything here drives `check_validity` directly
against literal content strings pulled from the real contamination patterns
found in results/claude-*-3arm/ (see tools/classify_run_validity.py), or
drives run_task with a stub adapter.
"""

from __future__ import annotations

from pathlib import Path

from bench.report import generate_comparison, generate_report
from bench.score import aggregate_scores, compute_score
from bench.validity import check_run_validity, check_validity, run_validity

ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / "tasks"
T1 = TASKS_DIR / "knr-ops" / "T1-comprehend"

# A real, substantive answer (well over any floor here) with no leak markers.
GENUINE_ANSWER = (
    "## Security Review\n\n" + ("This is a substantive prose analysis. " * 80)
)
assert len(GENUINE_ANSWER) > 2000

# Genuine short answers this dataset actually produces (T6-semantics quiz
# JSON) - short is not, by itself, a violation.
GENUINE_SHORT_QUIZ_ANSWER = (
    '```json\n{"q1": "updated-in-place", "q2": {"deleted": false, '
    '"reason": "kubectl apply without --prune only applies files present"}}\n```'
)


# ── check_validity: contract ────────────────────────────────────────────

def test_valid_run_has_no_reason():
    result = check_validity(GENUINE_ANSWER)
    assert result["valid"] is True
    assert result["reason"] is None


def test_invalid_run_always_has_a_reason():
    result = check_validity("")
    assert result["valid"] is False
    assert result["reason"] is not None


def test_none_content_is_invalid():
    result = check_validity(None)
    assert result["valid"] is False
    assert result["reason"] == "empty_or_near_empty"


# ── STRONG signals: reject regardless of length ─────────────────────────

def test_invoke_tag_leak_rejected_even_at_length():
    # Real example shape: a 55,000-char run whose entire body was a
    # simulated agent transcript, still flagged despite passing any length
    # floor easily.
    content = GENUINE_ANSWER + '\n<invoke name="Bash">\n<parameter name="command">ls -la</parameter>\n</invoke>\n'
    result = check_validity(content)
    assert result["valid"] is False
    assert result["reason"] == "tool_invocation_markup"


def test_bash_tool_marker_rejected():
    content = "I'll look for the diff.\n\n**Bash**\n```\nls; find . -iname '*diff*'\n```\n"
    result = check_validity(content)
    assert result["valid"] is False
    assert result["reason"] == "tool_invocation_markup"


def test_insight_ui_marker_rejected():
    content = GENUINE_ANSWER + "\n★ Insight ─────\nSome commentary.\n─────\n"
    result = check_validity(content)
    assert result["valid"] is False
    assert result["reason"] == "claude_code_ui_marker"


def test_list_style_bash_invocation_rejected():
    content = "I'll start by exploring the repository structure.\n\n- Bash (Explore repo structure)\n - command: find . -type f\n"
    result = check_validity(content)
    assert result["valid"] is False
    assert result["reason"] == "tool_invocation_markup"


def test_leaked_host_worktree_path_rejected():
    # The one case in the calibration set with no XML/marker leak at all,
    # just a real filesystem path from this machine bleeding into the
    # answer - a dead giveaway the model saw its own host environment.
    content = GENUINE_ANSWER + (
        "\nConfirm the worktree is intact: ls -la in "
        "/Users/alex/Documents/checkouts/iac-cd-bench/.claude/worktrees/agent-a6d49e5abb7ab7b38"
    )
    result = check_validity(content)
    assert result["valid"] is False
    assert result["reason"] == "leaked_host_worktree_path"


# ── WEAK signals: only disqualifying when also short ────────────────────

def test_exploration_preamble_short_is_stub():
    content = "I'll examine the existing repo structure to understand the current setup.\n\nLet me first check the layout.\n"
    assert len(content.strip()) < 1500
    result = check_validity(content)
    assert result["valid"] is False
    assert result["reason"] == "short_stub"


def test_clarifying_refusal_short_is_stub():
    content = (
        "I don't see the actual PR diff in your message. Could you provide "
        "the diff content or a link to the PR?"
    )
    result = check_validity(content)
    assert result["valid"] is False
    assert result["reason"] == "short_stub"


def test_weak_phrase_as_aside_in_long_answer_is_not_flagged():
    # A real answer that happens to mention "let me check" in passing is not
    # a stub - only short + weak-pattern is disqualifying.
    content = GENUINE_ANSWER + " Let me check whether that covers everything, and note the tradeoffs above hold."
    result = check_validity(content)
    assert result["valid"] is True


# ── length floor must not punish genuinely short valid answers ──────────

def test_genuine_short_quiz_answer_is_valid():
    assert len(GENUINE_SHORT_QUIZ_ANSWER) < 600
    result = check_validity(GENUINE_SHORT_QUIZ_ANSWER)
    assert result["valid"] is True
    assert result["reason"] is None


def test_check_run_validity_reads_content_from_result_dict():
    result = check_run_validity({"content": GENUINE_ANSWER})
    assert result["valid"] is True
    result = check_run_validity({"content": ""})
    assert result["valid"] is False


# ── run_validity: score.py/report.py entry point ────────────────────────

def test_run_validity_prefers_persisted_validity_block():
    # Even if content looks fine now, a persisted validity block (stamped by
    # the runner at generation time) is authoritative - don't recompute and
    # silently diverge from what was actually recorded on the run.
    result = {"content": GENUINE_ANSWER, "validity": {"valid": False, "reason": "tool_invocation_markup"}}
    assert run_validity(result)["valid"] is False
    assert run_validity(result)["reason"] == "tool_invocation_markup"


def test_run_validity_falls_back_to_content_when_no_validity_block():
    # The historical shape: results/claude-*-3arm/ was written before this
    # gate existed and carries `content` but no `validity` key.
    result = {"content": "I'll look for the actual diff artifacts in the repo before reviewing.\n\n**Bash**\n```\nls; find . -iname '*diff*' -not -path './.git/*' | head -50\n```\n"}
    assert run_validity(result)["valid"] is False


def test_run_validity_grandfathers_runs_with_no_content_key():
    # Predates content being recorded at all, or a synthetic test fixture
    # (e.g. tests/test_report_compare.py's write_run helper) - can't be
    # judged by this gate, so it must not be silently rejected.
    result = {"stages": {"lint": {"passed": True}}}
    v = run_validity(result)
    assert v["valid"] is True
    assert v["reason"] is None


# ── score.py: aggregate_scores excludes rejected runs, never silently ───

def _run(content, **stage_overrides):
    stages = {"lint": {"passed": True}, "static": {"passed": True}, "semantic": {"passed": True}}
    stages.update(stage_overrides)
    r = {
        "model": "m", "stack": "knr-ops", "task": "T5-review", "run": 0,
        "condition": "cold", "stages": stages, "content": content,
    }
    r["score"] = compute_score(r)
    return r


def test_aggregate_scores_excludes_invalid_runs_from_metrics():
    runs = [_run(GENUINE_ANSWER), _run("I'll look for the actual diff artifacts in the repo before reviewing.\n\n**Bash**\n```\nls; find . -iname '*diff*' -not -path './.git/*' | head -50\n```\n")]
    agg = aggregate_scores(runs)["m/knr-ops/T5-review"]
    assert agg["num_runs"] == 1
    assert agg["rejected_runs"] == 1
    assert agg["rejected_reasons"] == {"tool_invocation_markup": 1}
    # pass@1 must be computed over the one valid run, not diluted by 1/2.
    assert agg["pass_at_1"] == 1.0


def test_aggregate_scores_all_rejected_group_reports_zero_not_a_crash():
    runs = [_run("I'll look for the actual diff artifacts in the repo before reviewing.\n\n**Bash**\n```\nls; find . -iname '*diff*' -not -path './.git/*' | head -50\n```\n")]
    agg = aggregate_scores(runs)["m/knr-ops/T5-review"]
    assert agg["num_runs"] == 0
    assert agg["rejected_runs"] == 1
    assert agg["pass_at_1"] == 0.0
    assert agg["pass_at_k"] == 0


def test_aggregate_scores_no_rejected_key_when_nothing_rejected():
    runs = [_run(GENUINE_ANSWER)]
    agg = aggregate_scores(runs)["m/knr-ops/T5-review"]
    assert agg["rejected_runs"] == 0
    assert "rejected_reasons" not in agg


# ── report.py: rejected: N surfaces, never silently ─────────────────────
#
# bench/run-ready merged bench/report.py from bench/integrity-gates (#62)
# wholesale — its `partition_by_validity`/`bench.validate.classify_run`-based
# integrity gate is the richer, authoritative one (comparability refusal,
# provenance-aware, reason histogram) — rather than #61's simpler
# `run_validity`-based version these tests originally pinned. The assertions
# below are updated to match that report's actual wording/shape; the thing
# they still verify — a rejected run is surfaced with a count and a reason,
# never silently folded in — is unchanged.

# A run classify_run (bench.validate, #60) rejects on structural grounds -
# an XML tool-invocation marker, which none of check_content's calibration
# exemptions cover. generate_report/generate_comparison run on
# bench.validate.classify_run via partition_by_validity, not on the
# check_validity/run_validity pattern classifier used above, so the report
# tests use a fixture the #60 classifier actually flags.
TRANSCRIPT_LEAK_ANSWER = GENUINE_ANSWER + (
    '\n<invoke name="Bash">\n<parameter name="command">ls -la</parameter>\n</invoke>\n'
)


def test_generate_report_surfaces_rejected_count():
    runs = [_run(GENUINE_ANSWER), _run(TRANSCRIPT_LEAK_ANSWER)]
    report = generate_report("m", runs)
    assert "- **rejected: 1**" in report
    assert "agent_transcript" in report


def test_generate_report_states_zero_rejected_explicitly():
    runs = [_run(GENUINE_ANSWER)]
    report = generate_report("m", runs)
    assert "- **rejected: 0**" in report


def test_generate_comparison_coverage_table_has_rejected_column():
    runs = [_run(GENUINE_ANSWER), _run(TRANSCRIPT_LEAK_ANSWER)]
    report = generate_comparison([("set-a", runs)])
    assert "| Result set | Scored | Rejected | Judged runs | Judge model | Judge prompt |" in report
    assert "| set-a | 1 | **1** |" in report


# ── runner.py: run_task stamps validity on every run ─────────────────────

def test_run_task_stamps_validity_on_clean_content():
    # bench.runner stamps result["validity"] with bench.validity.check_content
    # (the #60 classifier: verdict/reasons/checks) rather than check_validity's
    # valid/reason shape - see the module docstring on which classifier each
    # caller uses. classify_run (bench.validate) is the downstream SSOT that
    # reconciles both.
    from bench import runner

    class StubModel:
        name = "stub-model"

        def complete(self, prompt, files):
            return {"content": GENUINE_ANSWER, "input_tokens": 1, "output_tokens": 2}

    results = runner.run_task(T1, StubModel(), k=1)
    assert results[0]["validity"]["verdict"] == "valid"
    assert results[0]["validity"]["reasons"] == []


def test_run_task_stamps_validity_on_contaminated_content():
    from bench import runner

    class StubModel:
        name = "stub-model"

        def complete(self, prompt, files):
            return {
                "content": (
                    "I'll start by exploring the repository.\n\n"
                    '<invoke name="Bash">\n<parameter name="command">ls -la</parameter>\n</invoke>\n'
                ),
                "input_tokens": 1, "output_tokens": 2,
            }

    results = runner.run_task(T1, StubModel(), k=1)
    assert results[0]["validity"]["verdict"] == "invalid"
    assert any("agent_transcript" in r for r in results[0]["validity"]["reasons"])
