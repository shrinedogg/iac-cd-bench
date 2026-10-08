"""
Score computation and aggregation for benchmark results.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bench.validity import run_validity

# Score axes and their weights
AXES = {
    "correctness": 3,
    "completeness": 2,
    "idiom": 1,
    "safety": 2,
    "consistency": 1,
}

# Log bodies a stage runner wrote when it had nothing to act on, back when
# "nothing to act on" was recorded as `passed: True`. Historical result JSONs
# carry no `inapplicable` key, so these markers are how a stored run is
# re-classified honestly — without them the fix would only apply to runs made
# after it landed, and every published composite would keep quoting the
# inflated number. bench.stages.lint.inapplicable is what new runs write.
#
# Matched on the whole stripped log body, never as a substring: a real lint
# run that happens to mention "no YAML files in workspace" in a tool's stderr
# must not be demoted.
VACUOUS_LOG_MARKERS = frozenset({
    "no YAML files in workspace",
    "no TypeScript files in workspace",
    "no lint commands for stack",
    "no semantic tests",
    "static validation passed",
    "lint passed",
})


def stage_inapplicable(stage: dict[str, Any] | None) -> bool:
    """Whether a stage had nothing to act on, and so measured nothing.

    An inapplicable stage is neither a pass nor a fail: it is excluded from
    the correctness ratio entirely. This is the vacuous-pass guard — a run
    that produced no extractable output used to score lint and static as
    passes ("nothing to lint", "nothing to build") while only the semantic
    grader failed, so the most broken runs collected 2 of 3 on the axis that
    weighs most.
    """
    if not isinstance(stage, dict):
        return False
    if stage.get("inapplicable"):
        return True
    # Backward compatibility with result JSONs written before the guard.
    if stage.get("passed") and (stage.get("logs") or "").strip() in VACUOUS_LOG_MARKERS:
        return True
    return False


def stage_attempted(stage: dict[str, Any] | None) -> bool:
    """A stage counts toward correctness only if it ran and checked something."""
    if not isinstance(stage, dict) or not stage:
        return False
    if stage.get("skipped"):
        return False
    return not stage_inapplicable(stage)


def stage_gate_defect(stage: dict[str, Any] | None) -> bool:
    """Whether this stage abstained because the HARNESS could not run it (#110).

    `correctness = passed / attempted`, and an abstention leaves the
    denominator — so a gate that cannot run *raises* the arm's score. Measured
    on coverage-v3, same task, same model, same condition:

        crossplane  T3  semantic=inapplicable static=abstained
                        attempted=1  correctness=1.00  composite=0.714
        knr-ops     T3  semantic=FAIL         static=FAIL
                        attempted=3  correctness=0.33  composite=0.444

    Both models produced a modification. crossplane's went unevaluated by two
    of three gates, so it took full correctness off the one that ran. That is
    most of why it outranked knr-ops in every published table.

    Failing the stage instead is not the fix — #99 removed exactly that, and
    correctly: punishing an arm for an axis the harness failed to measure is
    worse than dropping it. The honest position is that such a run is not
    comparable, so this is counted and surfaced rather than silently absorbed,
    and `bench.report` refuses a cross-arm ranking when any arm carries one.

    Three reasons are distinguished, per bench.stages.contract.Inapplicable:

      by_spec       the task declares no such stage (T1 has nothing to build).
                    Legitimate; leaving the denominator is correct.
      no_artifact   the model produced nothing to check. A real result about
                    the model.
      gate_defect   the harness's own fault. Not scoreable in either direction.

    Results written before the contract carry `inapplicable` with no reason.
    Those stay unclassified and are treated as by_spec, which is what the old
    behaviour assumed — this widens the record without retroactively
    reclassifying runs whose reason nobody recorded.
    """
    if not isinstance(stage, dict):
        return False
    return stage.get("inapplicable_reason") == "gate_defect"


def idiom_score(result: dict[str, Any]) -> float:
    """Idiom axis: the rubric judge's weighted verdict, or 0.0 when absent.

    A run only carries `judge` when it was produced with `--judge` on a task
    that has a `rubric:` block; everything else degrades to the pre-judge
    behaviour.
    """
    verdict = result.get("judge")
    if not isinstance(verdict, dict):
        return 0.0
    try:
        return max(0.0, min(1.0, float(verdict.get("idiom", 0.0))))
    except (TypeError, ValueError):
        return 0.0


def judge_metadata(result: dict[str, Any]) -> dict[str, Any] | None:
    """Reproducibility metadata for a judged run: model id + prompt hash."""
    verdict = result.get("judge")
    if not isinstance(verdict, dict):
        return None
    return {
        "judge_model": verdict.get("judge_model"),
        "prompt_sha256": verdict.get("prompt_sha256"),
    }


def compute_score(result: dict[str, Any]) -> dict[str, Any]:
    """Compute per-run scores from stage results."""
    stages = result.get("stages", {})
    scores = {}

    # Correctness: stage gates passed, averaged only over stages that
    # actually ran AND had something to act on. Two exclusions, both from
    # numerator and denominator: a stage the spec disabled (recorded by
    # run_task as {"skipped": True, ...}) and a stage that found nothing to
    # check (`inapplicable`). Neither may count as a pass.
    #
    # The second exclusion applies retroactively to stored results via
    # VACUOUS_LOG_MARKERS, so historical composites DO move — 762 of the 1140
    # under results/, all downward. That delta is measured and pinned in
    # tests/test_score_regression.py rather than avoided.
    stage_pass = 0
    total_stages = 0
    for name in ("lint", "static", "semantic"):
        stage_result = stages.get(name, {})
        if not stage_attempted(stage_result):
            continue
        total_stages += 1
        if stage_result.get("passed", False):
            stage_pass += 1
    e2e_result = stages.get("e2e")
    if e2e_result and stage_attempted(e2e_result):
        total_stages += 1
        if e2e_result.get("passed", False):
            stage_pass += 1
    # `total_stages == 0` means no stage was attempted at all — every one was
    # disabled by the task's spec (T1-comprehend and T5-review are rubric-only
    # by design). That is not a failed measurement, it is an absent one, so
    # correctness is dropped from the composite below rather than scored 0
    # (#99). Scoring it 0 while keeping its weight of 3 in the denominator
    # penalised a task for a gate it was never meant to have, and capped
    # rubric-only tasks near 0.42 no matter how well they were judged.
    correctness_applicable = bool(total_stages)
    scores["correctness"] = stage_pass / total_stages if total_stages else 0
    scores["attempted_stages"] = total_stages

    # #110. An abstention caused by the HARNESS shrinks the denominator above,
    # which raises correctness — so the arm whose gate is most broken scores
    # highest. The composite is left alone (failing the stage would re-create
    # the #99 defect), but the count travels with the score so a reader, and
    # bench.report, can tell a measurement from an artefact. A run carrying one
    # is not comparable with a run that does not.
    scores["gate_defects"] = sum(
        1 for name in ("lint", "static", "semantic", "e2e")
        if stage_gate_defect(stages.get(name))
    )

    # Completeness: semantic assertion coverage. `total_count == 0` means no
    # assertion was evaluated — the task ships no grader, or pytest never
    # collected. That used to score 1.0, a full mark on the second-heaviest
    # axis for a run nothing checked; it is now excluded from the composite
    # the same way an inapplicable stage is excluded from correctness.
    semantic = stages.get("semantic", {})
    passed_count = semantic.get("passed_count", 0)
    total_count = semantic.get("total_count", 0)
    completeness_applicable = bool(total_count)
    scores["completeness"] = passed_count / total_count if total_count else 0.0

    # Safety: binary flag read out of the semantic grader's own output.
    #
    # This axis used to keep a 1.0 default when nothing ran, and the reasoning
    # was sound at the time: dropping it too would make an unjudged rubric-only
    # task score 0.0 on every axis, which misleads in the other direction.
    #
    # #7 made that reasoning stale. Idiom is now a real measurement on exactly
    # the tasks safety cannot measure — T1-comprehend and T5-review run the
    # rubric judge — so dropping safety leaves them scored on idiom rather than
    # on nothing.
    #
    # Leaving the default in place had become the larger distortion. Measured
    # on coverage-v9: 28 of 84 runs are rubric tasks, NONE of them carries a
    # safety verdict, and safety's weight of 2 against a 3-weight denominator
    # floored every one of them at 0.667 however badly the model did. A third
    # of every arm's runs had two thirds of their score handed to them.
    #
    # The four gated tasks all carry a real verdict, so this changes nothing
    # for them.
    safety_applicable = isinstance(semantic, dict) and "safety_pass" in semantic
    scores["safety"] = 1.0 if semantic.get("safety_pass", True) else 0.0

    # Consistency: placeholder, computed across runs in aggregate
    scores["consistency"] = 0.0  # computed at aggregate level

    # Idiom: rubric LLM judge verdict, written by the runner under --judge.
    # Tasks without a rubric, and runs scored before the judge existed, keep
    # the historical 0.0 so composites stay comparable across result sets.
    scores["idiom"] = idiom_score(result)
    # Measurable only when the judge actually returned a verdict. A run without
    # one has no idiom evidence, and 0.0 is not evidence of bad idiom.
    idiom_applicable = isinstance(result.get("judge"), dict)

    # Weighted composite over the axes that were measurable. An axis nothing
    # could be measured on is dropped from numerator and denominator alike,
    # rather than contributing a default — the same rule the correctness axis
    # applies to inapplicable stages, one level up.
    applicable = dict(AXES)
    if not completeness_applicable:
        applicable.pop("completeness")
    if not correctness_applicable:
        applicable.pop("correctness")

    # #7. Two axes were contributing a guaranteed 0.0 to every composite while
    # keeping their weight, which is the exact defect rule 10 exists to stop --
    # an unmeasured axis dropped, not failed:
    #
    #   idiom        0.0 unless the run carries a judge verdict, and only
    #                rubric tasks run the judge. On every gated task it was a
    #                hard zero with weight 1.
    #   consistency  hardcoded 0.0 with the comment "computed at aggregate
    #                level" -- it is a cross-run metric that has no per-run
    #                value at all, yet it was weighted in every per-run score.
    #
    # Together that is weight 2 of 9, so no run could exceed 7/9 = 0.778 no
    # matter how good it was. chant's perfect runs scored exactly 0.778, which
    # was the ceiling rather than a result.
    #
    # This raises every composite. It does not change the ORDERING, because
    # both axes were constant across arms -- what it removes is a uniform
    # depression that made every published number lower than the thing it
    # claimed to measure.
    if not idiom_applicable:
        applicable.pop("idiom")
    applicable.pop("consistency", None)
    if not safety_applicable:
        applicable.pop("safety")

    # Every axis can now be dropped, so a run where nothing at all was measured
    # would divide by zero. That run is not a 0.0 — it is an absence, and
    # bench.validate rejects it outright rather than letting it contribute a
    # number. Guarded here anyway, because a scoring function that raises on
    # real input is worse than one that says "nothing measured".
    if not applicable:
        scores["applicable_axes"] = []
        scores["composite"] = 0.0
        return scores
    scores["applicable_axes"] = sorted(applicable)
    denom = sum(applicable.values())
    scores["composite"] = (
        sum(scores[axis] * applicable[axis] for axis in applicable) / denom
        if denom else 0.0
    )

    return scores


def aggregate_scores(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate scores across runs, stacks, tasks.

    #59: a run the validity gate rejects (tool-narration leak, or a stub too
    short to be a real answer — see bench/validity.py) is excluded from
    every metric here rather than scored as a failure. It is never silently
    dropped, though: `rejected_runs` (and `rejected_reasons`, broken down by
    cause) are always recorded on the aggregate entry, so a report built
    from this can surface "rejected: N" instead of a composite score that
    quietly folded contaminated runs in as ordinary failures.

    Runs with no `content` key at all predate content being recorded (or are
    synthetic test fixtures) and can't be judged by the gate — they are
    treated as valid, unaffected by this change.
    """
    # Group by model × stack × task
    groups: dict[tuple[str, ...], list[dict]] = {}
    for r in results:
        key = (r.get("model", "unknown"), r.get("stack", ""), r.get("task", ""))
        groups.setdefault(key, []).append(r)

    aggregates = {}
    for key, runs in groups.items():
        model, stack, task = key

        valid_runs = [r for r in runs if run_validity(r)["valid"]]
        invalid_runs = [r for r in runs if not run_validity(r)["valid"]]

        # Compute consistency: fraction of (valid) runs producing the same
        # outcome. A rejected run's stage results are gate noise (a run that
        # narrated tool use instead of answering emits no code, so lint/
        # static/semantic trivially fail on nothing) — folding it in would
        # confound consistency with contamination, exactly what #59 flagged.
        outcomes = set()
        for run in valid_runs:
            stages = run.get("stages", {})
            outcome = tuple(
                stages.get(s, {}).get("passed", False) for s in ("lint", "static", "semantic")
            )
            outcomes.add(outcome)
        consistency = (
            (1.0 if len(outcomes) <= 1 else 1.0 - (len(outcomes) - 1) / len(valid_runs))
            if valid_runs else 0.0
        )

        # Update consistency scores in each valid run
        for run in valid_runs:
            score = run.setdefault("score", {})
            score["consistency"] = consistency

        # Pass@1: fraction of single (valid) runs passing all stages
        pass_at_1 = (
            sum(
                1 for run in valid_runs
                if all(run.get("stages", {}).get(s, {}).get("passed", False) for s in ("lint", "static"))
            ) / len(valid_runs)
            if valid_runs else 0.0
        )

        # Pass@k: at least one valid run passing
        pass_at_k = (
            1
            if any(
                all(run.get("stages", {}).get(s, {}).get("passed", False) for s in ("lint", "static"))
                for run in valid_runs
            )
            else 0
        )

        # Average composite
        composites = [run.get("score", {}).get("composite", 0) for run in valid_runs]
        avg_composite = sum(composites) / len(composites) if composites else 0

        # Idiom coverage: which runs carried a judge verdict, and under which
        # judge model / prompt version (pinned for reproducibility).
        judged = [r for r in valid_runs if judge_metadata(r)]
        idioms = [r.get("score", {}).get("idiom", 0.0) for r in judged]

        rejected_reasons: dict[str, int] = {}
        for r in invalid_runs:
            reason = run_validity(r)["reason"] or "unknown"
            rejected_reasons[reason] = rejected_reasons.get(reason, 0) + 1

        entry = {
            "pass_at_1": pass_at_1,
            "pass_at_k": pass_at_k,
            "consistency": consistency,
            "avg_composite": avg_composite,
            "num_runs": len(valid_runs),
            "rejected_runs": len(invalid_runs),
        }
        if rejected_reasons:
            entry["rejected_reasons"] = rejected_reasons
        if judged:
            entry["judged_runs"] = len(judged)
            entry["avg_idiom"] = sum(idioms) / len(idioms)
            entry["judge_models"] = sorted(
                {m["judge_model"] for r in judged if (m := judge_metadata(r)) and m["judge_model"]}
            )
            entry["judge_prompts"] = sorted(
                {m["prompt_sha256"] for r in judged if (m := judge_metadata(r)) and m["prompt_sha256"]}
            )
        aggregates[f"{model}/{stack}/{task}"] = entry

    return aggregates


def load_result_set(model_dir: Path) -> list[dict[str, Any]]:
    """Load and score every run JSON under one result-set directory."""
    if not model_dir.is_dir():
        return []

    results = []
    for f in sorted(model_dir.rglob("*.json")):
        if "run" in f.stem:
            with open(f) as fp:
                result = json.load(fp)
                # Compute scores
                result["score"] = compute_score(result)
                results.append(result)

    return results


def load_results(results_dir: Path, model: str) -> list[dict[str, Any]]:
    """Load all result JSON files for a model."""
    return load_result_set(results_dir / model.replace("/", "-"))


def save_aggregate(results: list[dict[str, Any]], out_path: Path) -> None:
    """Save aggregated scores to JSON."""
    aggregates = aggregate_scores(results)
    with open(out_path, "w") as f:
        json.dump(aggregates, f, indent=2)
