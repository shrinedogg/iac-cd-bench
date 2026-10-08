"""
Generate markdown report from benchmark results.
Produces a stack x archetype matrix with per-model scores.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from bench import validate as validate_mod
from bench.score import compute_score, judge_metadata, load_result_set, load_results

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "results"

STACKS = ["knr-ops", "crossplane", "terraform", "pulumi-python", "pulumi-typescript", "chant", "bare"]
ARCHETYPES = ["comprehend", "generate", "modify", "debug", "review", "semantics"]
ARCHETYPE_LABELS = {
    "comprehend": "Comprehend",
    "generate": "Generate",
    "modify": "Modify",
    "debug": "Debug",
    "review": "Review",
    "semantics": "Semantics",
}


def partition_by_validity(
    results: list[dict], spec_lookup: bool = True,
) -> tuple[list[dict], list[dict]]:
    """Split runs into (scored, rejected).

    A rejected run gets no number at all — not a low one, not a caveated one.
    That is chant-bench's hardest-won rule: terraform-m1 published 1.000 next
    to an `invalid` badge after losing 22 of 24 trials, and the badge lost to
    the number. Averaging a rejected run in, even flagged, reproduces exactly
    that.
    """
    scored: list[dict] = []
    rejected: list[dict] = []
    for r in results:
        classification = validate_mod.classify_run(r) if spec_lookup else {"verdict": "valid"}
        r["_integrity"] = classification
        (rejected if classification["verdict"] == "invalid" else scored).append(r)
    return scored, rejected


def integrity_section(label: str, scored: list[dict], rejected: list[dict]) -> list[str]:
    """The rejected-run block every report opens with."""
    total = len(scored) + len(rejected)
    lines = [
        "## Result Integrity",
        "",
        f"- runs: **{total}**",
        f"- scored: **{len(scored)}**",
        f"- **rejected: {len(rejected)}**"
        + ("" if not rejected else "  — excluded from every number below"),
    ]
    if rejected:
        reasons: Counter[str] = Counter()
        for r in rejected:
            for reason in r["_integrity"]["invalid_reasons"]:
                reasons[reason.split(":", 1)[0]] += 1
        lines += ["", "| Rejection reason | Runs |", "| --- | --- |"]
        lines += [f"| `{k}` | {v} |" for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])]
        lines += [
            "",
            "> A rejected run did not measure the model: the provider returned "
            "no usable completion, a stage's binary was absent while the stage "
            "recorded a pass, or every enabled stage had nothing to act on. "
            "Such a run is re-run, not reported. "
            f"`python3 -m bench.validate results/{label} --verbose` lists them.",
        ]
    partial = [r for r in scored if r["_integrity"]["verdict"] == "partial"]
    if partial:
        partial_reasons: Counter[str] = Counter()
        for r in partial:
            for reason in r["_integrity"]["partial_reasons"]:
                partial_reasons[reason.split(":", 1)[0]] += 1
        lines += [
            "",
            f"- partial: **{len(partial)}** run(s) are scored but flagged.",
            "",
            "| Partial reason | Runs |",
            "| --- | --- |",
        ]
        lines += [
            f"| `{k}` | {v} |"
            for k, v in sorted(partial_reasons.items(), key=lambda kv: -kv[1])
        ]
        notes = []
        if "no_stage_ran" in partial_reasons:
            notes.append(
                "`no_stage_ran` means the task's spec disables every build "
                "stage, so the run scores on the rubric judge alone. Its "
                "provenance is complete and it compares normally."
            )
        # Only these actually leave the run unable to name the experiment it
        # came from. Saying it of every partial run — #80 — discredited runs
        # whose provenance was intact.
        provenance_gaps = sorted(
            partial_reasons.keys()
            & {"dirty_harness", "no_harness_commit", "no_prompt_hash",
               "no_provenance", "no_toolchain", "partial_toolchain"}
        )
        if provenance_gaps:
            n = sum(partial_reasons[k] for k in provenance_gaps)
            notes.append(
                f"{n} run(s) carry incomplete provenance "
                f"({', '.join('`' + g + '`' for g in provenance_gaps)}) and "
                "cannot be compared against another result set."
            )
        if notes:
            lines += ["", "> " + "  ".join(notes)]
    lines.append("")
    return lines


def generate_report(model: str, results: list[dict]) -> str:
    """Generate markdown report from results.

    Rejected runs are partitioned out before anything is averaged, and the
    count is stated above the matrix rather than in a footnote.
    """
    results, rejected = partition_by_validity(results)
    lines = [
        f"# Benchmark Report: {model}",
        "",
        *integrity_section(model.replace("/", "-"), results, rejected),
        "## Stack × Archetype Matrix",
        "",
        "Each cell shows: **pass@1 / pass@k / avg composite score**",
        "",
        "| Stack | " + " | ".join(ARCHETYPE_LABELS[a] for a in ARCHETYPES) + " | Average |",
        "| " + " | ".join(["---"] * (len(ARCHETYPES) + 2)) + " |",
    ]

    matrix = {}
    for stack in STACKS:
        for arch in ARCHETYPES:
            matrix[(stack, arch)] = {"runs": [], "tasks": []}

    # Populate matrix (valid runs only — see docstring)
    for result in results:
        stack = result.get("stack", "")
        task = result.get("task", "")
        score = result.get("score", {})
        composite = score.get("composite", 0)
        passed = all(
            result.get("stages", {}).get(s, {}).get("passed", False)
            for s in ("lint", "static")
        )

        for arch in ARCHETYPES:
            key = (stack, arch)
            if arch in task.lower():
                matrix[key]["runs"].append({
                    "passed": passed,
                    "composite": composite,
                })

    # Render rows
    for stack in STACKS:
        row = [stack]
        stack_composites = []
        for arch in ARCHETYPES:
            key = (stack, arch)
            runs = matrix[key]["runs"]
            if runs:
                pass_at_1 = sum(1 for r in runs if r["passed"]) / len(runs)
                pass_at_k = 1.0 if any(r["passed"] for r in runs) else 0.0
                avg_comp = sum(r["composite"] for r in runs) / len(runs)
                cell = f"{pass_at_1:.0%} / {pass_at_k:.0%} / {avg_comp:.2f}"
                stack_composites.append(avg_comp)
            else:
                cell = "—"
                stack_composites.append(0)
            row.append(cell)
        stack_avg = sum(stack_composites) / len(stack_composites) if stack_composites else 0
        row.append(f"{stack_avg:.2f}")
        lines.append("| " + " | ".join(row) + " |")

    lines.append("")

    # knr-ops cold vs warm delta (if cold runs exist)
    cold_results = [r for r in results if r.get("condition") == "cold"]
    warm_results = [r for r in results if r.get("condition") == "warm"]
    if cold_results and warm_results:
        lines.append("## knr-ops Cold vs Warm Delta")
        lines.append("")
        lines.append("| Task | Cold pass@1 | Warm pass@1 | Delta |")
        lines.append("| --- | --- | --- | --- |")

        cold_by_task = {}
        warm_by_task = {}
        for r in cold_results:
            t = r.get("task", "")
            cold_by_task.setdefault(t, []).append(r)
        for r in warm_results:
            t = r.get("task", "")
            warm_by_task.setdefault(t, []).append(r)

        for task_id in cold_by_task:
            if task_id not in warm_by_task:
                continue
            cold_pass = sum(1 for r in cold_by_task[task_id] if r.get("stages", {}).get("lint", {}).get("passed")) / len(cold_by_task[task_id])
            warm_pass = sum(1 for r in warm_by_task[task_id] if r.get("stages", {}).get("lint", {}).get("passed")) / len(warm_by_task[task_id])
            delta = warm_pass - cold_pass
            lines.append(f"| {task_id} | {cold_pass:.0%} | {warm_pass:.0%} | {delta:+.0%} |")

        lines.append("")
        lines.append("> **Cold/warm delta measures how much in-context documentation** knr-ops needs to match training-data-driven stacks.")

    # Token usage summary
    lines.append("")
    lines.append("## Token Usage")
    lines.append("")
    total_input = sum(r.get("tokens", {}).get("input", 0) for r in results)
    total_output = sum(r.get("tokens", {}).get("output", 0) for r in results)
    lines.append(f"- Input tokens: {total_input:,}")
    lines.append(f"- Output tokens: {total_output:,}")
    lines.append(f"- Total: {total_input + total_output:,}")
    lines.append(f"- Runs scored: {len(results)} (rejected: {len(rejected)})")

    return "\n".join(lines)


def archetype_of(task: str) -> str | None:
    """Map a task id (T1-comprehend) onto its archetype."""
    task = (task or "").lower()
    for arch in ARCHETYPES:
        if arch in task:
            return arch
    return None


def _cell(values: list[float]) -> str:
    return f"{sum(values) / len(values):.2f}" if values else "—"


def comparability_section(dirs: list[Path]) -> tuple[list[str], bool]:
    """Whether these result sets are the same experiment, rendered.

    chant-bench's rule, ported: two runs are comparable when they share a
    harness commit and a briefing SHA — different either, different
    experiment. Here the axes are harness commit, toolchain versions,
    provider and reasoning effort, and a conflict on any of them makes the
    table a category error rather than a comparison. Sets whose provenance
    predates the stamp cannot be shown to differ, which is not the same as
    agreeing, so they are labelled unverifiable.
    """
    reports = [validate_mod.validate_result_set(d) for d in dirs if d.is_dir()]
    comp = validate_mod.comparability(reports)
    lines = ["## Comparability", ""]
    if comp["conflicts"]:
        lines.append("**These result sets are NOT the same experiment.**")
        lines.append("")
        lines += [f"- CONFLICT: {c}" for c in comp["conflicts"]]
    elif comp["unverifiable"]:
        lines.append(
            "**Comparability could not be verified.** These sets predate the "
            "provenance stamp, so nothing proves they were produced by the "
            "same harness against the same tools."
        )
        lines.append("")
        lines += [f"- UNVERIFIABLE: {u}" for u in comp["unverifiable"]]
    else:
        lines.append(
            "Harness commit, toolchain versions, provider and reasoning effort "
            "agree across every set below."
        )
    lines.append("")
    return lines, bool(comp["conflicts"])


def generate_comparison(
    result_sets: list[tuple[str, list[dict]]],
    dirs: list[Path] | None = None,
) -> str:
    """Side-by-side composite scores across result-set directories.

    One column per result set; rows are stacks, then stack × archetype. Cells
    are the mean composite score of the runs in that bucket, or — when the set
    has no runs there. Rejected runs never reach a cell.
    """
    rejected_counts: dict[str, int] = {}
    cleaned: list[tuple[str, list[dict]]] = []
    for label, results in result_sets:
        scored, rejected = partition_by_validity(results)
        rejected_counts[label] = len(rejected)
        cleaned.append((label, scored))
    result_sets = cleaned

    labels = [label for label, _ in result_sets]
    header = "| " + " | ".join(["Stack", *labels]) + " |"
    divider = "| " + " | ".join(["---"] * (len(labels) + 1)) + " |"

    # bucket[label][(stack, archetype)] -> [composite, ...]  (already-scored
    # runs only — result_sets was filtered by partition_by_validity above)
    buckets: dict[str, dict[tuple[str, str], list[float]]] = {}
    for label, results in result_sets:
        bucket: dict[tuple[str, str], list[float]] = {}
        for r in results:
            arch = archetype_of(r.get("task", ""))
            if arch is None:
                continue
            composite = r.get("score", {}).get("composite", 0.0)
            bucket.setdefault((r.get("stack", ""), arch), []).append(composite)
        buckets[label] = bucket

    lines = [
        "# Comparative Benchmark Report",
        "",
        f"Comparing {len(result_sets)} result sets: " + ", ".join(f"`{l}`" for l in labels),
        "",
    ]
    if dirs:
        comp_lines, _ = comparability_section(dirs)
        lines += comp_lines
    lines += [
        "## Composite Score by Stack",
        "",
        "Mean composite score across all runs in the stack.",
        "",
        header,
        divider,
    ]

    for stack in STACKS:
        row = [stack]
        for label in labels:
            values = [
                c for (s, _a), cs in buckets[label].items() if s == stack for c in cs
            ]
            row.append(_cell(values))
        lines.append("| " + " | ".join(row) + " |")

    overall = ["**Overall**"]
    for label in labels:
        overall.append(_cell([c for cs in buckets[label].values() for c in cs]))
    lines.append("| " + " | ".join(overall) + " |")

    # Stack × archetype detail
    lines += [
        "",
        "## Composite Score by Stack × Archetype",
        "",
        "| " + " | ".join(["Stack / Archetype", *labels]) + " |",
        divider,
    ]
    for stack in STACKS:
        for arch in ARCHETYPES:
            cells = [_cell(buckets[label].get((stack, arch), [])) for label in labels]
            if all(c == "—" for c in cells):
                continue
            lines.append(
                "| " + " | ".join([f"{stack} / {ARCHETYPE_LABELS[arch]}", *cells]) + " |"
            )

    # Coverage: run counts, rejections, and the judge pinning behind any idiom
    # scores. `rejected` sits beside `scored` rather than under it — a column
    # averaging 39 surviving runs of 90 is a different claim from one
    # averaging 90.
    lines += ["", "## Coverage", "",
              "| Result set | Scored | Rejected | Judged runs | Judge model | Judge prompt |",
              "| --- | --- | --- | --- | --- | --- |"]
    for label, results in result_sets:
        judged = [m for r in results if (m := judge_metadata(r))]
        models = sorted({m["judge_model"] for m in judged if m["judge_model"]}) or ["—"]
        prompts = sorted({m["prompt_sha256"] for m in judged if m["prompt_sha256"]}) or ["—"]
        lines.append(
            f"| {label} | {len(results)} | **{rejected_counts.get(label, 0)}** | "
            f"{len(judged)} | {', '.join(models)} | {', '.join(prompts)} |"
        )

    lines += [
        "",
        "> Rejected runs contribute to no cell above. A run the gates rejected did",
        "> not measure the model, so it has to happen again rather than be averaged in.",
        "",
        "> Runs without a judge verdict score 0.0 on the idiom axis (weight 1 of 9),",
        "> so composites are only strictly comparable between equally judged sets.",
    ]

    return "\n".join(lines)


def collect_result_sets(paths: list[str]) -> list[tuple[str, list[dict]]]:
    """Load each result-set directory, skipping non-directories and empties."""
    result_sets: list[tuple[str, list[dict]]] = []
    for raw in paths:
        path = Path(raw)
        if not path.is_dir():
            continue
        results = load_result_set(path)
        if not results:
            print(f"Skipping {path}: no run JSONs found")
            continue
        result_sets.append((path.name, results))
    return result_sets


def main():
    parser = argparse.ArgumentParser(description="Generate benchmark report")
    parser.add_argument("--model", default=None, help="Model identifier")
    parser.add_argument("--compare", nargs="+", metavar="DIR", default=None,
                        help="Result-set directories to compare side by side "
                             "(e.g. --compare results/claude-opus-5 results/gpt-5.4)")
    parser.add_argument("--output", default=None, help="Output path (default: results/<model>/report.md)")
    parser.add_argument("--allow-incomparable", action="store_true",
                        help="Render a comparison whose result sets disagree on harness "
                             "commit, toolchain versions, provider or effort. Without "
                             "this the comparison is refused, because such a table is a "
                             "category error rather than a comparison.")
    parser.add_argument("--fail-on-rejected", action="store_true",
                        help="Exit non-zero if any run in the reported sets was rejected "
                             "by the validity gates (for CI).")
    args = parser.parse_args()

    if args.compare:
        result_sets = collect_result_sets(args.compare)
        if not result_sets:
            print("No result sets with runs found; nothing to compare")
            return
        dirs = [Path(p) for p in args.compare if Path(p).is_dir()]
        comp_lines, incomparable = comparability_section(dirs)
        if incomparable and not args.allow_incomparable:
            print("\n".join(comp_lines), file=sys.stderr)
            print(
                "Refusing to render a comparison across result sets that are not "
                "the same experiment. Re-run the sets under one harness commit and "
                "toolchain, or pass --allow-incomparable to render it with the "
                "conflicts stated in the report.",
                file=sys.stderr,
            )
            raise SystemExit(2)
        report = generate_comparison(result_sets, dirs=dirs)
        out_path = Path(args.output) if args.output else RESULTS_DIR / "comparison.md"
        rejected_total = sum(
            len(partition_by_validity(rs)[1]) for _label, rs in collect_result_sets(args.compare)
        )
    else:
        if not args.model:
            parser.error("--model is required unless --compare is given")
        results = load_results(RESULTS_DIR, args.model)
        if not results:
            print(f"No results found for model: {args.model}")
            return
        rejected_total = len(partition_by_validity(list(results))[1])
        report = generate_report(args.model, results)
        out_path = (
            Path(args.output) if args.output
            else RESULTS_DIR / args.model.replace("/", "-") / "report.md"
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)
    print(f"Wrote report to {out_path}")
    print()
    print(report)

    if args.fail_on_rejected and rejected_total:
        print(
            f"\n--fail-on-rejected: {rejected_total} run(s) were excluded by the "
            "validity gates; re-run them rather than publishing the remainder.",
            file=sys.stderr,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
