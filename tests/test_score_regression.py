"""
Regression tests pinning what the historical result JSONs score.

This file has two jobs, and they pull in opposite directions on purpose.

1. Honest stage gating (spec.yaml `stages.*.enabled`, landed in #56) must NOT
   change any *pre-gating* historical composite. A pre-gating run carries no
   `skipped` key, so every stage recorded there is still counted as
   attempted. Result sets produced by a harness that already honors stage
   gating (e.g. the claude-*-3arm and claude-*-probe sets added for #40/#59)
   legitimately carry `skipped` stages for tasks whose spec disables them —
   those are a different experiment for this check's purposes, not evidence
   of drift, so they are filtered out of `_historical_results()` rather than
   asserted against the pre-gating formula.

2. The vacuous-pass guard DOES change the surviving pre-gating composites,
   and that change is the point. Before it, a stage with nothing to act on
   recorded `passed: True`: "no YAML files in workspace", "no TypeScript
   files in workspace", "static validation passed" (emitted when no
   kustomization/claim/manifest was found), "no semantic tests". A run that
   produced no extractable output therefore collected free passes on lint
   and static, and `completeness` defaulted to 1.0 whenever no assertion was
   evaluated — full marks on the second-heaviest axis for a run nothing had
   checked. The runs that flattered hardest were exactly the most broken
   ones.

   Measured over the pre-gating result JSONs under results/ (1218 of the
   1296 total on disk; 78 carry `skipped` and are out of scope per #1 above):

     - 773 runs carried at least one vacuously-passed stage
     - 127 runs had *every* enabled stage inapplicable — no measurement at
       all; bench.validate rejects these outright
     - 791 composites change, every one of them downward

   Zero composites move up. That is the check worth keeping: removing credit
   that was never earned can only lower a score, so an increase anywhere would
   mean the guard had broken something rather than fixed it.

The pinned values below are the corrected ones. They were captured from
compute_score after the guard landed and are asserted exactly, so a later
change to scoring semantics has to be a deliberate re-pin rather than a silent
drift.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench.score import compute_score, stage_inapplicable

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "results"


# (path relative to results/, composite before the vacuous-pass guard,
#  composite after). The spread was chosen to cover trivially-passing
# comprehend tasks through runs with real failures, so the assertion
# exercises inapplicable stages, real passes and real failures alike.
SAMPLED_HISTORICAL_JSONS = [
    # Re-pinned for #99: this run attempted no stage at all, so correctness is
    # now dropped from the composite instead of scored 0 against its weight of
    # 3. 0.2857 -> 0.5. The knr-ops T1-comprehend fixture below did attempt
    # stages and is unmoved, which is the intended surgical scope.
    ("claude-opus-4-8-low/crossplane/warm/T1-comprehend_run0.json",
     1.0, 1.0),
    ("claude-opus-4-8-low/crossplane/warm/T2-generate_run0.json",
     0.5714285714285714, 0.5),
    ("claude-opus-4-8-low/crossplane/warm/T2-generate_run2.json",
     0.42857142857142855, 0.2857142857142857),
    ("claude-opus-4-8-low/crossplane/warm/T3-modify_run1.json",
     0.8571428571428571, 0.4),
    ("claude-opus-4-8-low/knr-ops/warm/T1-comprehend_run1.json",
     0.7142857142857143, 0.4),
    ("claude-opus-4-8-low/knr-ops/warm/T2-generate_run0.json",
     0.47619047619047616, 0.4761904761904762),
]

# Counts measured over the pre-gating subset of results/ (see module
# docstring #1) after merging in the #59 result sets that already carry
# `skipped` flags. Pinned so a later change to VACUOUS_LOG_MARKERS, or a
# stage runner quietly changing a log body, shows up as a test failure
# rather than as a moved leaderboard.
EXPECTED_TOTAL_RUNS = 1758
EXPECTED_VACUOUS_RUNS = 949
EXPECTED_FULLY_VACUOUS_RUNS = 158
EXPECTED_CHANGED_COMPOSITES = 587


def _old_composite(stages: dict) -> float:
    """The pre-guard formula, kept here as the thing being compared against.

    correctness = passed-stage count over a fixed denominator of 3 (+1 when
    `e2e` is present at all); completeness defaults to 1.0 when no assertion
    ran. Idiom and consistency are omitted from both sides -- see below.
    """
    stage_pass = sum(
        1 for name in ("lint", "static", "semantic")
        if stages.get(name, {}).get("passed", False)
    )
    total = 3
    if stages.get("e2e"):
        total = 4
        stage_pass += 1 if stages["e2e"].get("passed", False) else 0
    correctness = stage_pass / total if total else 0

    semantic = stages.get("semantic", {})
    passed_count = semantic.get("passed_count", 0)
    total_count = semantic.get("total_count", 0)
    completeness = passed_count / total_count if total_count else 1.0
    safety = 1.0 if semantic.get("safety_pass", True) else 0.0

    # Idiom and consistency are deliberately EXCLUDED, matching what
    # compute_score now does (#7). They were previously included as a hardcoded
    # `0.0 * 1` each, which capped every composite at 7/9 = 0.778 regardless of
    # performance -- chant's perfect runs scored exactly that, because it was
    # the ceiling and not a result.
    #
    # They must be excluded HERE too, or this baseline and the current formula
    # differ in two ways at once and the comparison below measures neither. The
    # invariant this file exists to guard is specifically about the vacuous-pass
    # correction, so the baseline has to differ from current in that respect
    # alone. Leaving them in produced 562 spurious "increases" -- every run rose
    # simply because the ceiling was removed.
    return (correctness * 3 + completeness * 2 + safety * 2) / 7


def _historical_results():
    """Pre-gating result JSONs only — see module docstring #1.

    A result whose `stages` carry a `skipped` key came from a harness that
    already honors spec-driven stage gating (#56) and is out of scope for
    this file's "gating alone changes nothing" claim; it is not evidence of
    drift, it is a different experiment.
    """
    for json_path in sorted(RESULTS_DIR.rglob("*.json")):
        try:
            result = json.loads(json_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(result, dict) or "stages" not in result:
            continue
        if any(
            isinstance(sr, dict) and "skipped" in sr
            for sr in result["stages"].values()
        ):
            continue
        yield json_path, result


def test_sampled_historical_composites_match_pinned_corrections():
    for rel_path, old_composite, new_composite in SAMPLED_HISTORICAL_JSONS:
        json_path = RESULTS_DIR / rel_path
        assert json_path.exists(), f"fixture missing: {json_path}"
        result = json.loads(json_path.read_text())

        # Sanity: genuinely pre-gating fixtures, so this exercises the
        # "stage dict present, no skipped flag" backward-compat path.
        for stage_result in result.get("stages", {}).values():
            assert "skipped" not in stage_result, (
                f"{rel_path} unexpectedly carries a `skipped` stage flag; "
                "pick a different historical fixture for this regression test"
            )

        # approx on the old side only: it is recomputed here with a
        # different association order than the original implementation used,
        # and the corrected value below is what is pinned exactly.
        assert _old_composite(result["stages"]) == pytest.approx(old_composite), (
            f"{rel_path}: the recorded pre-guard composite no longer "
            "reproduces — the fixture itself changed"
        )
        # approx, not ==: the last bit of a repeating fraction differs between
        # macOS and Linux, so exact equality made this pass locally and fail in
        # CI on 0.37037037037037035 vs 0.3703703703703704.
        assert compute_score(result)["composite"] == pytest.approx(new_composite), (
            f"{rel_path}: composite drifted from the pinned corrected value"
        )


def test_stage_gating_alone_changes_nothing():
    """No historical run carries a `skipped` flag, so spec-driven stage gating
    is inert over results/ — every change measured below comes from the
    vacuous-pass guard, not from #56."""
    for json_path, result in _historical_results():
        for stage_result in result["stages"].values():
            assert "skipped" not in stage_result, (
                f"{json_path} carries a `skipped` flag; the delta attribution "
                "in this file's docstring no longer holds"
            )


def test_vacuous_pass_correction_is_the_measured_size_and_only_lowers_scores():
    total = vacuous = fully_vacuous = changed = increased = 0
    vacuous_increases: list[str] = []

    for _json_path, result in _historical_results():
        total += 1
        stages = result["stages"]
        inapplicable = [
            s for s in stages.values()
            if isinstance(s, dict) and stage_inapplicable(s)
        ]
        if inapplicable:
            vacuous += 1
        enabled = [
            s for s in stages.values()
            if isinstance(s, dict) and not s.get("skipped")
        ]
        if enabled and all(stage_inapplicable(s) for s in enabled):
            fully_vacuous += 1

        old = _old_composite(stages)
        new = compute_score(result)["composite"]
        if abs(new - old) > 1e-12:
            changed += 1
            if new > old:
                increased += 1
                # Did the old formula credit an inapplicable stage as a pass?
                # Only then is an increase a contradiction of the guard.
                if any(isinstance(s, dict) and stage_inapplicable(s)
                       and s.get("passed") for s in stages.values()):
                    vacuous_increases.append(str(_json_path))

    assert total == EXPECTED_TOTAL_RUNS, (
        f"expected {EXPECTED_TOTAL_RUNS} historical result JSONs, found {total} — "
        "re-pin the counts below if runs were added or removed"
    )
    assert vacuous == EXPECTED_VACUOUS_RUNS
    assert fully_vacuous == EXPECTED_FULLY_VACUOUS_RUNS
    assert changed == EXPECTED_CHANGED_COMPOSITES
    # The original form of this assertion was `increased == 0`, on the
    # reasoning that withdrawing credit never earned can only lower a score.
    # That is true only of the case it was written against — an inapplicable
    # stage the old formula counted as a PASS. The old formula divided by a
    # fixed denominator of 3, so it also counted an inapplicable stage as a
    # FAILURE, and excluding one of those necessarily RAISES correctness.
    #
    # The historical corpus happened to contain only the first kind, so the
    # blanket claim held by accident until solid-haiku-v1 added runs of the
    # second kind (e.g. knr-ops T4-debug: lint pass, static inapplicable,
    # semantic fail — 1/3 becomes 1/2).
    #
    # So the invariant is narrower than it was written: removing a *vacuous
    # pass* may only lower. That is the part worth pinning, and it is what the
    # guard actually claims.
    assert not vacuous_increases, (
        f"{len(vacuous_increases)} run(s) whose inapplicable stage was scored "
        "as a PASS by the old formula went UP. Withdrawing credit that was "
        f"never earned can only lower a score: {vacuous_increases[:3]}"
    )


# ── #99: an unattempted axis is dropped, not failed ──────────────────────

def _run(stages: dict) -> dict:
    return {"stack": "chant", "task": "T1-comprehend", "stages": stages}


def test_correctness_is_dropped_when_no_stage_was_attempted():
    """A spec that disables every build stage means correctness was never
    measured. Scoring it 0 while keeping its weight of 3 penalises a task for
    a gate it was never meant to have — the same category error the
    vacuous-pass guard fixed pointing the other way (#99)."""
    scored = compute_score(_run({
        "lint": {"skipped": True}, "static": {"skipped": True},
        "semantic": {"skipped": True},
    }))

    assert "correctness" not in scored["applicable_axes"]
    assert "completeness" not in scored["applicable_axes"]


def test_correctness_is_kept_when_a_stage_ran_and_failed():
    """The other direction: a stage that ran and failed is a real 0, and must
    stay in the denominator."""
    scored = compute_score(_run({
        "lint": {"passed": False, "logs": "boom"},
        "static": {"skipped": True}, "semantic": {"skipped": True},
    }))

    assert "correctness" in scored["applicable_axes"]
    assert scored["correctness"] == 0.0


def test_rubric_only_task_is_not_capped_below_a_gated_one():
    """The symptom that surfaced #99: a rubric-only task scoring well on the
    judge still landed below every gated task, because it could not earn the
    heaviest axis."""
    rubric_only = compute_score(_run({
        "lint": {"skipped": True}, "static": {"skipped": True},
        "semantic": {"skipped": True},
    }) | {"judge": {"idiom": 1.0}})

    assert rubric_only["composite"] > 0.5, (
        "a rubric-only task judged perfectly still cannot clear 0.5 — "
        "correctness is being counted as a failure rather than dropped"
    )
