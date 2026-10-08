"""
Results validator: classify every run in a result directory before anything
quotes a number from it.

Ported from chant-bench's `scripts/validate_results.py` and aws-bench's
`audit.py`. The rule both of them settled on, after publishing a run that had
lost 22 of its 24 trials and still printed 1.000, is that a run which did not
measure the model is not a low score — it is not a measurement, and it has to
happen again. So this classifies rather than averages, and bench.report gives
a rejected run no number at all.

Usage:
    python3 -m bench.validate results/claude-opus-5
    python3 -m bench.validate results/*            # every set
    python3 -m bench.validate results/x --json     # machine-readable

Exit codes: 0 when every set is publishable, 1 when any set is refused.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from bench import validity
from bench.score import stage_attempted, stage_inapplicable

ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / "tasks"

# Share of a set's runs that may have died in the harness before the set stops
# describing the model. chant-bench's CRASH_LIMIT, same value and same
# reasoning: at 1 in 24 the denominator barely moves, at 1 in 6 a rate over
# the survivors is not a rate over the run. One constant, not two — aws-bench
# carries this number twice and the copies can drift.
CRASH_LIMIT = 0.10

# Share of a set's runs the validity gates may reject before the set as a
# whole is unpublishable. Deliberately the same 10%: a couple of empty
# completions in a 90-run suite is noise, a third of them is a broken
# provider wearing a score.
REJECT_LIMIT = 0.10

STAGE_NAMES = ("lint", "static", "semantic", "e2e")


# ──────────────────────────────────────────────────────────────────────────
# Per-run classification
# ──────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def _load_spec(stack: str, task: str) -> dict[str, Any] | None:
    """Cached: classifying 1140 runs otherwise re-reads ~40 specs 1140 times."""
    spec_path = TASKS_DIR / stack / task / "spec.yaml"
    if not spec_path.exists():
        return None
    try:
        return yaml.safe_load(spec_path.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return None


def _spec_for(result: dict[str, Any]) -> dict[str, Any] | None:
    stack, task = result.get("stack"), result.get("task")
    if not stack or not task:
        return None
    return _load_spec(str(stack), str(task))


def _dedupe_reasons(reasons: list[str]) -> list[str]:
    """Collapse reasons that describe the same failure more than once.

    A freshly-derived check and a result's own stored verdict can each
    contribute a reason for the same underlying label — `runner_error` is the
    concrete case: `classify_run` appends it directly from `result["error"]`
    and then again from the stored `validity["reasons"]` the runner stamped
    at the same call site, double-counting one harness failure as two.
    Dedupe by label (the text before the first colon), keeping first-seen
    order but preferring whichever phrasing is more informative — the longer
    string — when the two copies differ only in detail (e.g. one truncated).
    """
    kept: dict[str, str] = {}
    order: list[str] = []
    for reason in reasons:
        label = reason.split(":", 1)[0]
        if label not in kept:
            order.append(label)
            kept[label] = reason
        elif len(reason) > len(kept[label]):
            kept[label] = reason
    return [kept[label] for label in order]


def classify_run(result: dict[str, Any], spec: dict[str, Any] | None = None) -> dict[str, Any]:
    """Classify one run as valid / partial / invalid, with reasons.

    `invalid` means rejected: the run did not measure the model and must not
    contribute a number anywhere. `partial` means the run is usable but its
    provenance is incomplete, so it cannot be compared against another set.
    """
    invalid: list[str] = []
    partial: list[str] = []

    stages = result.get("stages") or {}

    # 1. The harness itself failed. Kept in the denominator (it is a run that
    #    was asked for and produced no answer), never in the numerator.
    if result.get("error"):
        invalid.append(f"runner_error: {str(result['error'])[:160]}")

    # 2. Content shape (#59). Re-derived rather than trusted, so a result
    #    written before the gate existed gets the same verdict a fresh run
    #    would — the point of making the gate pure text classification.
    recorded = result.get("validity")
    if isinstance(recorded, dict) and recorded.get("reasons") is not None:
        verdict = recorded
    else:
        if spec is None:
            spec = _spec_for(result)
        verdict = validity.check_result(result, spec)
    if verdict.get("verdict") == "invalid":
        invalid.extend(verdict.get("reasons", []))

    # 3. A stage that recorded a pass while saying its binary was absent is
    #    the #56 lie in stored form. Fixed at run time; still on disk in every
    #    result written before the fix, so it is caught here too.
    for name in STAGE_NAMES:
        stage = stages.get(name)
        if not isinstance(stage, dict):
            continue
        if stage.get("passed") and "NOT FOUND:" in (stage.get("logs") or ""):
            missing = [
                line.split("NOT FOUND:", 1)[1].strip()
                for line in (stage.get("logs") or "").splitlines()
                if "NOT FOUND:" in line
            ]
            invalid.append(
                f"tool_missing_scored_as_pass: stage `{name}` recorded a pass "
                f"with its binary absent ({', '.join(missing) or 'unknown'}) — "
                "nothing was checked (#56)"
            )

    # 4. Every stage the spec enabled had nothing to act on. The run produced
    #    no artifact any gate could look at, so there is no measurement here
    #    however the individual stages were recorded (#3, the vacuous pass).
    present = [stages.get(n) for n in STAGE_NAMES if isinstance(stages.get(n), dict)]
    enabled = [s for s in present if not s.get("skipped")]
    if enabled and all(stage_inapplicable(s) for s in enabled):
        invalid.append(
            "all_stages_inapplicable: every enabled stage had nothing to act "
            "on — the run produced no output any gate could check"
        )
    if present and not enabled:
        partial.append(
            "no_stage_ran: every stage was disabled by spec; this run scores "
            "on the rubric judge alone"
        )

    # 5. Provenance. Without it a re-run after any harness change is silently
    #    a different experiment (chant-bench's comparability rule).
    prov = result.get("provenance")
    if not isinstance(prov, dict):
        partial.append(
            "no_provenance: run predates the provenance stamp — harness "
            "commit, prompt hash and toolchain versions are unrecoverable, so "
            "it cannot be compared against another set"
        )
    else:
        if not (prov.get("harness") or {}).get("commit"):
            partial.append("no_harness_commit: harness git state was not recorded")
        elif (prov.get("harness") or {}).get("dirty"):
            partial.append(
                "dirty_harness: produced from a working tree with uncommitted "
                "changes, so the recorded commit does not describe the code that ran"
            )
        if not prov.get("toolchain"):
            partial.append("no_toolchain: binary versions were not recorded")
        if prov.get("partial"):
            partial.append(
                "partial_toolchain: run set was started with --allow-missing-tools"
            )
        if not (prov.get("task") or {}).get("prompt_sha256"):
            partial.append("no_prompt_hash: the task prompt was not fingerprinted")

    invalid = _dedupe_reasons(invalid)
    partial = _dedupe_reasons(partial)

    if invalid:
        state = "invalid"
    elif partial:
        state = "partial"
    else:
        state = "valid"

    return {
        "verdict": state,
        "invalid_reasons": invalid,
        "partial_reasons": partial,
        "attempted_stages": sum(1 for s in present if stage_attempted(s)),
    }


# ──────────────────────────────────────────────────────────────────────────
# Set-level validation
# ──────────────────────────────────────────────────────────────────────────

def _load_runs(result_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    runs: list[tuple[Path, dict[str, Any]]] = []
    for f in sorted(result_dir.rglob("*.json")):
        if "run" not in f.stem:
            continue
        try:
            data = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError) as e:
            runs.append((f, {"_unreadable": str(e)}))
            continue
        if isinstance(data, dict) and "stages" in data:
            runs.append((f, data))
    return runs


def validate_result_set(result_dir: Path) -> dict[str, Any]:
    """Classify every run in one result-set directory and judge the set."""
    runs = _load_runs(result_dir)
    report: dict[str, Any] = {
        "path": str(result_dir),
        "label": result_dir.name,
        "total": len(runs),
        "runs": [],
        "counts": Counter(),
        "reasons": Counter(),
        "problems": [],
    }

    if not runs:
        report["verdict"] = "empty"
        report["problems"].append("no run JSONs found")
        return report

    per_task: Counter[tuple[str, str]] = Counter()
    harness_commits: set[str] = set()
    toolchain_versions: dict[str, set[str]] = {}
    providers: set[str] = set()
    efforts: set[str] = set()

    for path, data in runs:
        if "_unreadable" in data:
            entry = {
                "file": str(path), "verdict": "invalid",
                "invalid_reasons": [f"unreadable_json: {data['_unreadable']}"],
                "partial_reasons": [],
            }
        else:
            entry = {"file": str(path), **classify_run(data)}
            per_task[(data.get("stack", "?"), data.get("task", "?"))] += 1
            prov = data.get("provenance")
            if isinstance(prov, dict):
                commit = (prov.get("harness") or {}).get("commit")
                if commit:
                    harness_commits.add(
                        f"{commit}-dirty" if (prov.get("harness") or {}).get("dirty") else commit
                    )
                # Recorded per binary, not as one fingerprint over the whole
                # toolchain dict: preflight only probes the binaries a stack
                # actually needs, so a multi-stack set legitimately records a
                # different binary set per run (a `bare` run never touches
                # `tsc`; a `chant` run never touches `flux`). A real drift is
                # one binary's OWN version disagreeing across runs, not the
                # member lists of two stacks differing from each other.
                for name, info in (prov.get("toolchain") or {}).items():
                    version = (info or {}).get("version")
                    if version:
                        toolchain_versions.setdefault(name, set()).add(version)
                if prov.get("provider"):
                    providers.add(str(prov["provider"]))
                efforts.add(str(prov.get("reasoning_effort")))
            else:
                efforts.add(str(data.get("reasoning_effort")))
        report["runs"].append(entry)
        report["counts"][entry["verdict"]] += 1
        for reason in entry["invalid_reasons"] + entry["partial_reasons"]:
            report["reasons"][reason.split(":", 1)[0]] += 1

    total = len(runs)
    rejected = report["counts"]["invalid"]
    errored = report["reasons"].get("runner_error", 0)

    report["rejected"] = rejected
    report["reject_share"] = rejected / total
    report["errored"] = errored
    report["error_share"] = errored / total
    report["harness_commits"] = sorted(harness_commits)
    # {binary: [versions observed in this set]} — one binary can legitimately
    # be absent from a stack's toolchain probe, but if present its version
    # must agree with every other run's, in this set or another.
    report["toolchains"] = {name: sorted(vs) for name, vs in sorted(toolchain_versions.items())}
    report["providers"] = sorted(providers)
    report["efforts"] = sorted(efforts)

    if report["error_share"] > CRASH_LIMIT:
        report["problems"].append(
            f"{errored} of {total} runs died in the harness "
            f"({report['error_share']:.0%} > {CRASH_LIMIT:.0%}) — a rate over "
            "the survivors is not a rate over the run set"
        )
    if report["reject_share"] > REJECT_LIMIT:
        report["problems"].append(
            f"{rejected} of {total} runs were rejected by the validity gates "
            f"({report['reject_share']:.0%} > {REJECT_LIMIT:.0%}) — re-run this "
            "set, do not publish it"
        )

    # Run-count homogeneity, ported from chant-bench's trial-count check: a
    # task run 1 time sitting beside tasks run 3 times is not the same
    # experiment, and averaging them hides it.
    if per_task:
        usual = Counter(per_task.values()).most_common(1)[0][0]
        odd = {f"{s}/{t}": n for (s, t), n in sorted(per_task.items()) if n != usual}
        report["runs_per_task"] = usual
        if odd:
            report["problems"].append(
                f"uneven k: {odd} where every other task ran {usual} time(s) — "
                "not the same experiment, so they cannot be averaged together"
            )

    # Internal provenance consistency: one set must be one experiment.
    if len(harness_commits) > 1:
        report["problems"].append(
            f"mixed harness commits within one set: {', '.join(sorted(harness_commits))}"
        )
    conflicting_binaries = {
        name: vs for name, vs in report["toolchains"].items() if len(vs) > 1
    }
    if conflicting_binaries:
        detail = "; ".join(
            f"{name} ({' vs '.join(vs)})" for name, vs in sorted(conflicting_binaries.items())
        )
        report["problems"].append(
            f"mixed toolchain versions within one set: {detail} — the same "
            "binary was recorded at different versions across runs"
        )
    if len(providers) > 1:
        report["problems"].append(
            f"mixed providers within one set: {', '.join(sorted(providers))}"
        )
    if len(efforts) > 1:
        # Reasoning effort was guarded across sets (comparability's "reasoning
        # effort" axis) but not within one, so a set could silently mix
        # efforts and still validate. Per the benchmark author, effort moved
        # results more than model choice did in places — which makes a set
        # that mixes it not one experiment but several averaged together, and
        # makes this a harder refusal than the model axis, not a softer one.
        report["problems"].append(
            f"mixed reasoning effort within one set: "
            f"{', '.join(sorted(efforts))} — effort moves results more than "
            "model choice does, so these runs are not one experiment"
        )

    report["verdict"] = (
        "refused" if report["problems"]
        else "partial" if report["counts"]["partial"]
        else "valid"
    )
    return report


def format_set_report(report: dict[str, Any], verbose: bool = False) -> str:
    label = report["label"]
    counts = report["counts"]
    lines = [
        f"{label}: {report['total']} run(s) — "
        f"{counts.get('valid', 0)} valid, {counts.get('partial', 0)} partial, "
        f"rejected: {counts.get('invalid', 0)}",
    ]
    for reason, n in sorted(report["reasons"].items(), key=lambda kv: -kv[1]):
        lines.append(f"    {n:>4}  {reason}")
    for problem in report["problems"]:
        lines.append(f"  REFUSED: {problem}")
    if verbose:
        for entry in report["runs"]:
            if entry["verdict"] == "valid":
                continue
            lines.append(f"  [{entry['verdict']}] {entry['file']}")
            for reason in entry["invalid_reasons"] + entry["partial_reasons"]:
                lines.append(f"      {reason}")
    lines.append(f"  -> {report['verdict'].upper()}")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────
# Comparability
# ──────────────────────────────────────────────────────────────────────────

def comparability(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Can these result sets sit in one table?

    chant-bench's rule: two runs are comparable when they share a harness
    commit and a briefing SHA — different either, different experiment. Here
    the axes are harness commit, toolchain, provider and reasoning effort.
    The model is deliberately not an axis: differing models is what a
    comparison is *for*.

    A set with no provenance at all cannot be shown to differ, which is not
    the same as being shown to match — those are reported as unverifiable
    rather than as agreement.
    """
    axes = {
        "harness commit": "harness_commits",
        "provider": "providers",
        "reasoning effort": "efforts",
    }
    conflicts: list[str] = []
    unverifiable: list[str] = []

    for label, key in axes.items():
        observed = {r["label"]: r.get(key) or [] for r in reports}
        missing = [name for name, vals in observed.items() if not vals]
        distinct = {v for vals in observed.values() for v in vals}
        if missing and key != "efforts":
            unverifiable.append(
                f"{label}: not recorded for {', '.join(sorted(missing))}"
            )
        if len(distinct) > 1:
            detail = "; ".join(
                f"{name}={', '.join(vals) or '?'}" for name, vals in sorted(observed.items())
            )
            conflicts.append(f"{label} differs across sets ({detail})")

    # Toolchain is its own axis, compared per binary rather than as one
    # opaque value per set: `report["toolchains"]` is {binary: [versions
    # observed in that set]}, and different sets legitimately probe different
    # binaries (a bare-manifests set never touches `tsc`; a chant set never
    # touches `flux`) — same reasoning as the within-set check above. Two
    # sets disagree on toolchain only when a binary they BOTH recorded shows
    # up at a different version in each.
    toolchain_observed = {r["label"]: r.get("toolchains") or {} for r in reports}
    missing_toolchain = [name for name, vals in toolchain_observed.items() if not vals]
    if missing_toolchain:
        unverifiable.append(
            f"toolchain: not recorded for {', '.join(sorted(missing_toolchain))}"
        )
    binaries = {b for vals in toolchain_observed.values() for b in vals}
    for binary in sorted(binaries):
        per_set = {
            name: vals[binary] for name, vals in toolchain_observed.items() if vals.get(binary)
        }
        distinct_versions = {v for vs in per_set.values() for v in vs}
        if len(distinct_versions) > 1:
            detail = "; ".join(f"{name}={', '.join(vs)}" for name, vs in sorted(per_set.items()))
            conflicts.append(f"toolchain differs across sets on {binary} ({detail})")

    return {
        "comparable": not conflicts,
        "conflicts": conflicts,
        "unverifiable": unverifiable,
    }


# ──────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate benchmark result sets before anything quotes them",
    )
    parser.add_argument("dirs", nargs="+", metavar="DIR", help="Result-set directories")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="List every non-valid run with its reasons")
    parser.add_argument("--json", action="store_true", help="Emit the reports as JSON")
    parser.add_argument("--allow-rejected", action="store_true",
                        help="Exit 0 even when a set is refused (inspection only)")
    args = parser.parse_args(argv)

    reports = [
        validate_result_set(Path(d)) for d in args.dirs if Path(d).is_dir()
    ]
    if not reports:
        print("no result-set directories found", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(
            [{**r, "counts": dict(r["counts"]), "reasons": dict(r["reasons"])} for r in reports],
            indent=2, default=str,
        ))
    else:
        for report in reports:
            print(format_set_report(report, verbose=args.verbose))
            print()
        if len(reports) > 1:
            comp = comparability(reports)
            print("Comparability across these sets:")
            for note in comp["conflicts"]:
                print(f"  CONFLICT: {note}")
            for note in comp["unverifiable"]:
                print(f"  UNVERIFIABLE: {note}")
            if comp["comparable"] and not comp["unverifiable"]:
                print("  every axis agrees; these sets are the same experiment")
            print()

        ok = sum(1 for r in reports if r["verdict"] != "refused")
        print(f"{ok}/{len(reports)} result set(s) publishable")

    refused = any(r["verdict"] == "refused" for r in reports)
    return 1 if refused and not args.allow_rejected else 0


if __name__ == "__main__":
    raise SystemExit(main())
