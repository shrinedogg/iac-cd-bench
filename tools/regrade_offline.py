#!/usr/bin/env python3
"""Re-run task graders against stored runs, without spending a model call.

Every result JSON the runner writes carries the model's full completion in
its `content` field. That is enough to rebuild the exact workspace the
semantic stage saw: materialize the task seed, write model_output.md, and
re-extract the fenced code blocks with the runner's own
`extract_code_blocks`. So when a grader is found to be wrong -- issue #72's
path-exact knr-ops T2-generate grader, which errored all six of its
assertions on one FileNotFoundError -- the affected runs do not have to be
re-run at the cost of another few hours of model calls. They have to be
re-graded.

    python3 tools/regrade_offline.py results/claude-haiku-4-5-3arm-v2 \
        --out results-regraded/claude-haiku-4-5-3arm-v2

The input tree is opened read-only and never written to. Corrected runs are
written to a parallel tree with the same layout, each carrying a `regrade`
block recording what the semantic verdict was before, what it is now, and
which grader (by content hash) produced it -- so a regraded result set can
never be mistaken for an original one.

Only the semantic stage is recomputed. lint and static shell out to real
tools against a workspace the model's own toolchain already saw; their
stored verdicts stand. Scores are recomputed in full from the corrected
stages via bench.score.compute_score, since correctness and completeness
both read the semantic stage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bench import runner as runner_mod  # noqa: E402
from bench import score as score_mod  # noqa: E402
from bench.stages import lint as lint_mod  # noqa: E402
from bench.stages import semantic as semantic_mod  # noqa: E402
from bench.stages import static as static_mod  # noqa: E402

TASKS_DIR = ROOT / "tasks"

# Which stages a regrade may recompute. `semantic` is pure Python against the
# workspace; `lint` and `static` shell out to the stack's real toolchain, so
# they are opt-in (--stages) and only meaningful on a machine that has the
# same tools the run had. Issue #76's damage landed on `static` — a file
# written to the wrong directory built nothing — so recovering those verdicts
# needs more than a semantic regrade.
STAGE_RUNNERS = {"lint", "static", "semantic"}


def _grader_fingerprint(task_dir: Path) -> str | None:
    """sha256 of the grader that produced a verdict, so a regraded set names
    the grader version it was graded under."""
    test_file = task_dir / "tests" / "test_task.py"
    if not test_file.exists():
        return None
    return hashlib.sha256(test_file.read_bytes()).hexdigest()[:12]


def _semantic_verdict(stage: dict[str, Any] | None) -> tuple[bool, int, int]:
    stage = stage or {}
    return (
        bool(stage.get("passed")),
        int(stage.get("passed_count") or 0),
        int(stage.get("total_count") or 0),
    )


def _stage_verdict(stage: dict[str, Any] | None) -> bool | None:
    stage = stage or {}
    if stage.get("skipped") or stage.get("inapplicable"):
        return None
    return bool(stage.get("passed"))


def rematerialize(result: dict[str, Any], task_dir: Path, workspace: Path,
                  with_node_modules: bool = False,
                  errors: list[str] | None = None) -> list[Path]:
    """Rebuild the workspace the grader saw: seed, completion, extracted files.

    Extraction is `bench.runner.extract_code_blocks` itself, not a copy of
    it -- a regrade that extracted differently from the runner would be
    measuring a workspace no run ever had.

    The chant node_modules symlink is skipped by default: it exists for the
    lint/static/e2e toolchain, which a regrade does not re-run, and every
    chant grader excludes node_modules from its own file walk anyway.
    """
    content = result.get("content") or ""
    condition = result.get("condition", "warm")

    original_bootstrap = runner_mod._bootstrap_chant_workspace
    if not with_node_modules:
        runner_mod._bootstrap_chant_workspace = lambda _ws: None
    try:
        runner_mod.materialize_task(task_dir, workspace, condition)
    finally:
        runner_mod._bootstrap_chant_workspace = original_bootstrap

    (workspace / "model_output.md").write_text(content)
    stack = result.get("stack", "knr-ops")
    written, refusals = runner_mod.extract_code_blocks_detailed(content, workspace, stack)
    if errors is not None:
        errors.extend(refusals)
    return written


def regrade_run(result: dict[str, Any], tasks_dir: Path,
                with_node_modules: bool = False,
                stages: tuple[str, ...] = ("semantic",)) -> dict[str, Any]:
    """Return a corrected copy of one run. The input dict is not mutated."""
    out = json.loads(json.dumps(result, default=str))
    stack = result.get("stack")
    task_id = result.get("task")
    task_dir = tasks_dir / str(stack) / str(task_id)

    before = _semantic_verdict((result.get("stages") or {}).get("semantic"))

    if not task_dir.is_dir():
        out["regrade"] = {"status": "skipped", "reason": f"task dir not found: {task_dir}"}
        return out
    if not (result.get("content") or "").strip():
        out["regrade"] = {"status": "skipped", "reason": "run has no stored content"}
        return out

    old_stage = (result.get("stages") or {}).get("semantic") or {}
    if "semantic" in stages and old_stage.get("skipped"):
        out["regrade"] = {"status": "skipped", "reason": "semantic stage disabled by spec"}
        return out

    stage_before = {name: _stage_verdict((result.get("stages") or {}).get(name))
                    for name in stages}
    refusals: list[str] = []
    new_stages: dict[str, Any] = {}
    workspace = Path(tempfile.mkdtemp(prefix=f"regrade-{stack}-"))
    try:
        # lint/static shell out to the stack's toolchain, which for chant means
        # the node_modules the runner bootstraps; force it on whenever they run.
        need_modules = with_node_modules or bool({"lint", "static"} & set(stages))
        rematerialize(result, task_dir, workspace,
                      with_node_modules=need_modules, errors=refusals)
        for name in stages:
            existing = (result.get("stages") or {}).get(name) or {}
            if existing.get("skipped"):
                continue
            if name == "semantic":
                new_stages[name] = semantic_mod.run_semantic(task_dir, workspace)
            elif name == "lint":
                new_stages[name] = lint_mod.run_lint(workspace, str(stack))
            elif name == "static":
                new_stages[name] = static_mod.run_static(workspace, str(stack))
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    out.setdefault("stages", {}).update(new_stages)
    after = _semantic_verdict(out["stages"].get("semantic"))
    out["score"] = score_mod.compute_score(out)
    if refusals:
        out["extraction_errors"] = refusals
    out["regrade"] = {
        "status": "regraded",
        "grader_sha256": _grader_fingerprint(task_dir),
        "stages": list(stages),
        "before": {"passed": before[0], "passed_count": before[1], "total_count": before[2]},
        "after": {"passed": after[0], "passed_count": after[1], "total_count": after[2]},
        "stage_verdicts": {
            name: {"before": stage_before.get(name),
                   "after": _stage_verdict(out["stages"].get(name))}
            for name in stages
        },
        "direction": (
            "fail_to_pass" if after[0] and not before[0]
            else "pass_to_fail" if before[0] and not after[0]
            else "unchanged"
        ),
        "composite_before": score_mod.compute_score(result).get("composite"),
        "composite_after": out["score"].get("composite"),
    }
    return out


def regrade_tree(results_dir: Path, out_dir: Path, tasks_dir: Path = TASKS_DIR,
                 with_node_modules: bool = False,
                 verbose: bool = True,
                 stages: tuple[str, ...] = ("semantic",)) -> dict[str, Any]:
    """Regrade every run under `results_dir` into a parallel tree."""
    summary: dict[str, Any] = {
        "source": str(results_dir),
        "out": str(out_dir),
        "stages": list(stages),
        "runs": 0,
        "regraded": 0,
        "skipped": 0,
        "fail_to_pass": 0,
        "pass_to_fail": 0,
        "unchanged": 0,
        "assertion_delta": 0,
        "stage_flips": {name: {"fail_to_pass": 0, "pass_to_fail": 0} for name in stages},
        "extraction_refusals": 0,
        "changed": [],
    }

    for src in sorted(results_dir.rglob("*.json")):
        rel = src.relative_to(results_dir)
        dest = out_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)

        if "run" not in src.stem:
            # Set-level manifests (_provenance.json) are copied verbatim.
            shutil.copy2(src, dest)
            continue

        result = json.loads(src.read_text())
        summary["runs"] += 1
        corrected = regrade_run(result, tasks_dir, with_node_modules=with_node_modules,
                                stages=stages)
        dest.write_text(json.dumps(corrected, indent=2, default=str))

        info = corrected.get("regrade", {})
        if info.get("status") != "regraded":
            summary["skipped"] += 1
            continue
        summary["regraded"] += 1
        summary[info["direction"]] += 1
        delta = info["after"]["passed_count"] - info["before"]["passed_count"]
        summary["assertion_delta"] += delta
        if corrected.get("extraction_errors"):
            summary["extraction_refusals"] += 1

        flips = {}
        for name, v in (info.get("stage_verdicts") or {}).items():
            if v["before"] is None or v["after"] is None or v["before"] == v["after"]:
                continue
            key = "fail_to_pass" if v["after"] else "pass_to_fail"
            summary["stage_flips"].setdefault(name, {"fail_to_pass": 0, "pass_to_fail": 0})
            summary["stage_flips"][name][key] += 1
            flips[name] = f"{v['before']} -> {v['after']}"

        if info["direction"] != "unchanged" or delta or flips:
            entry = {
                "run": str(rel),
                "model": corrected.get("model"),
                "stack": corrected.get("stack"),
                "task": corrected.get("task"),
                "condition": corrected.get("condition"),
                "direction": info["direction"],
                "stage_flips": flips,
                "assertions": f"{info['before']['passed_count']}/{info['before']['total_count']}"
                              f" -> {info['after']['passed_count']}/{info['after']['total_count']}",
                "composite": f"{info['composite_before']:.3f} -> {info['composite_after']:.3f}",
            }
            summary["changed"].append(entry)
            if verbose:
                print(f"  {entry['direction']:<13} {rel}  {entry['assertions']}  "
                      f"composite {entry['composite']}  {flips or ''}")

    (out_dir / "_regrade_summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def format_summary(summary: dict[str, Any]) -> str:
    lines = [
        f"Regraded {summary['regraded']} of {summary['runs']} runs "
        f"({summary['skipped']} skipped)",
        f"  semantic verdict fail -> pass: {summary['fail_to_pass']}",
        f"  semantic verdict pass -> fail: {summary['pass_to_fail']}",
        f"  unchanged verdict:             {summary['unchanged']}",
        f"  net assertions recovered:      {summary['assertion_delta']:+d}",
    ]
    for name, flips in (summary.get("stage_flips") or {}).items():
        if flips["fail_to_pass"] or flips["pass_to_fail"]:
            lines.append(f"  {name} stage flips:{'':14.14}"
                         f"+{flips['fail_to_pass']} / -{flips['pass_to_fail']}")
    if summary.get("extraction_refusals"):
        lines.append(f"  runs with refused extractions: {summary['extraction_refusals']}")
    lines.append(f"  corrected results written to:  {summary['out']}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results_dir", type=Path, help="A result set directory (read-only)")
    ap.add_argument("--out", type=Path, required=True,
                    help="Destination for the corrected parallel tree")
    ap.add_argument("--tasks-dir", type=Path, default=TASKS_DIR,
                    help="Task definitions to grade against (default: ./tasks)")
    ap.add_argument("--with-node-modules", action="store_true",
                    help="Bootstrap chant's node_modules into regraded workspaces "
                         "(only needed if a grader ever inspects installed packages)")
    ap.add_argument("--stages", default="semantic",
                    help="Comma-separated stages to recompute: semantic (default), "
                         "lint, static. lint/static shell out to the stack's real "
                         "toolchain, so only run them where that toolchain is present.")
    args = ap.parse_args()

    stages = tuple(s.strip() for s in args.stages.split(",") if s.strip())
    unknown = set(stages) - STAGE_RUNNERS
    if unknown:
        raise SystemExit(f"unknown stage(s): {', '.join(sorted(unknown))}")

    if not args.results_dir.is_dir():
        raise SystemExit(f"not a directory: {args.results_dir}")
    if args.out.resolve() == args.results_dir.resolve():
        raise SystemExit("refusing to write the corrected tree over the originals")

    args.out.mkdir(parents=True, exist_ok=True)
    summary = regrade_tree(args.results_dir, args.out, args.tasks_dir,
                           with_node_modules=args.with_node_modules, stages=stages)
    print(format_summary(summary))


if __name__ == "__main__":
    main()
