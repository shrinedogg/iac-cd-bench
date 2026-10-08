#!/usr/bin/env python3
"""Estimate token + dollar cost of the #40 SMOKE and FULL comparison runs.

Reads existing results/*/<stack>/<condition>/*.json run files (they carry a
`tokens: {input, output}` field per run - schema verified against
results/claude-opus-5-low/pulumi-python/warm/T2-generate_run2.json during
authorship) to derive per-archetype token averages, then projects those
averages across the SMOKE and FULL invocation counts described in
tools/run_matrix.sh and iac-cd-bench#40.

No network access - this is a projection from historical run JSONs plus a
hardcoded price table, not a live pricing or token-counting call.

Two things this script CANNOT measure directly from results/, and estimates
instead (both are flagged in the printed report, not just here):

  1. The `chant` and `bare` arms have never run - results/ only has stack
     history for {knr-ops, crossplane, terraform, pulumi-python,
     pulumi-typescript}. This script averages tokens PER ARCHETYPE (which is
     stack-agnostic across the five stacks it does have) as the best
     available proxy for chant/bare. chant in particular emits TypeScript
     composites rather than raw YAML/HCL and may run shorter or longer than
     this proxy suggests - treat chant estimates as low-confidence until the
     SMOKE run lands real numbers.

  2. There is no `claude-haiku-4-5` history in results/ at all. The rubric
     judge verdict dict (bench/judge.py on bench/idiom-judge, PR #42) also
     carries no token usage field, so judge-call cost is ALWAYS estimated
     (never measured), for every model, even once haiku history exists.
"""

from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "results"

ARCHETYPES = ["comprehend", "generate", "modify", "debug", "review", "semantics"]
# Tasks with a rubric block (answer_format: rubric) - the only ones the
# judge scores. Verified against tasks/knr-ops/*/spec.yaml and
# tasks/bare/*/spec.yaml (bench/bare-tasks): T1-comprehend and T5-review
# consistently, across every stack checked.
RUBRIC_ARCHETYPES = {"comprehend", "review"}

ARMS = ["chant", "knr-ops", "bare"]
CONDITIONS = ["cold", "warm"]
K_FULL = 3

# ──────────────────────────────────────────────────────────────────────────
# PRICE TABLE - edit here only. $ per million tokens (MTok).
# Source: the `claude-api` Anthropic-API skill's cached model/pricing table
# (cache stamped 2026-06-24), re-checked live via that skill during this
# script's authorship on 2026-08-25. If this script is reused later and the
# numbers look stale, re-invoke the `claude-api` skill (or check
# https://claude.com/pricing) rather than trusting this cache silently.
# ──────────────────────────────────────────────────────────────────────────
PRICE_PER_MTOK: dict[str, dict[str, float]] = {
    "claude-opus-5":    {"input": 5.00, "output": 25.00},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
}

OPUS_MODEL = "claude-opus-5"
HAIKU_MODEL = "claude-haiku-4-5"
JUDGE_MODEL = "claude-haiku-4-5"  # per #40 sign-off
SMOKE_MODEL = HAIKU_MODEL  # matches tools/run_matrix.sh's SMOKE_MODEL default

# When a model has no direct results/ history, fall back to averaging these
# result-set directories as a stand-in "cheap model" token profile. All are
# -low (reasoning_effort=low) result sets, matching the #40 methodology
# amendment (effort pinned low for continuity with the most recent
# low-reasoning leaderboard baselines). TODO-verify: replace with real
# claude-haiku-4-5 numbers once the SMOKE run lands - this proxy is a
# verbosity stand-in across unrelated model families, not a haiku estimate.
HAIKU_PROXY_SOURCES = ["glm-5.3-low", "gpt-5.4-low", "gpt-5.6-sol-low", "qwen 3.8 - local-low"]

# Rubric judge prompt overhead, in tokens, NOT measured from any run JSON
# (judge verdicts carry no token usage field - see module docstring point 2).
# Derived by inspecting bench/judge.py on bench/idiom-judge (PR #42) during
# authorship:
#   - JUDGE_PROMPT_TEMPLATE itself (excl. rubric/answer-key/submission
#     placeholders): 2497 chars =~ 625 tokens (chars/4 heuristic)
#   - rendered rubric block: 5-criterion tasks measured at 254-429 chars
#     (tasks/knr-ops/T1-comprehend, T5-review spec.yaml) =~ 65-110 tokens
#   - golden/answer_key.md: measured 1376-1570 chars across knr-ops and
#     pulumi-python T1/T5 =~ 345-395 tokens
# TODO-verify against real judge call logs once --judge has actually fired
# (the smoke run is exactly this first live call, per the #40 preflight
# comment).
JUDGE_TEMPLATE_OVERHEAD_TOKENS = 625
JUDGE_RUBRIC_BLOCK_TOKENS = 90
JUDGE_ANSWER_KEY_TOKENS = 370
# MAX_SUBMISSION_CHARS in bench/judge.py is 60000 -> ~15000 token cap on the
# submission portion of the judge's input.
JUDGE_SUBMISSION_TOKEN_CAP = 15000
# The judge's own output is a small JSON verdict (index/score/justification
# per criterion, justification capped at 400 chars). Not measured (see
# above) - flat estimate for a ~5-criterion rubric.
JUDGE_OUTPUT_TOKENS_ESTIMATE = 350


def archetype_of(task: str) -> str | None:
    task = (task or "").lower()
    for a in ARCHETYPES:
        if a in task:
            return a
    return None


def load_archetype_tokens(result_set_dirs: list[str]) -> dict[str, dict[str, float]]:
    """Average (input, output) tokens per archetype across the given
    results/<dir> result sets. Returns {} entries with 0 runs are omitted."""
    buckets: dict[str, list[tuple[int, int]]] = {a: [] for a in ARCHETYPES}
    for dirname in result_set_dirs:
        for path in glob.glob(str(RESULTS_DIR / dirname / "*" / "*" / "*.json")):
            try:
                data = json.loads(Path(path).read_text())
            except (json.JSONDecodeError, OSError):
                continue
            arch = archetype_of(data.get("task", ""))
            tok = data.get("tokens") or {}
            if arch and tok:
                buckets[arch].append((tok.get("input", 0), tok.get("output", 0)))

    out: dict[str, dict[str, float]] = {}
    for arch, vals in buckets.items():
        if not vals:
            continue
        n = len(vals)
        out[arch] = {
            "n": n,
            "avg_input": sum(v[0] for v in vals) / n,
            "avg_output": sum(v[1] for v in vals) / n,
        }
    return out


def resolve_sources(model_id: str, effort: str) -> tuple[list[str], str]:
    """Find results/ directories to source token stats from for `model_id`.

    Prefers direct history for the exact model (matching effort tag when
    possible), falls back to HAIKU_PROXY_SOURCES, else no data.
    """
    if not RESULTS_DIR.is_dir():
        return [], "none"
    all_dirs = [p.name for p in RESULTS_DIR.iterdir() if p.is_dir()]
    direct = [d for d in all_dirs if d == model_id or d.startswith(model_id + "-")]
    if direct:
        effort_tagged = [d for d in direct if d.endswith(f"-{effort}")]
        return (effort_tagged or direct), "direct"
    if model_id == HAIKU_MODEL:
        proxy = [d for d in HAIKU_PROXY_SOURCES if (RESULTS_DIR / d).is_dir()]
        if proxy:
            return proxy, "proxy"
    return [], "none"


def fallback_fill(stats: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
    """Fill any archetype with no data using the cross-archetype average,
    so a single missing bucket (e.g. no `semantics` runs yet for a tag)
    doesn't zero out the whole estimate."""
    if not stats:
        return {a: {"n": 0, "avg_input": 0.0, "avg_output": 0.0} for a in ARCHETYPES}
    overall_in = sum(s["avg_input"] * s["n"] for s in stats.values()) / sum(s["n"] for s in stats.values())
    overall_out = sum(s["avg_output"] * s["n"] for s in stats.values()) / sum(s["n"] for s in stats.values())
    filled = dict(stats)
    for a in ARCHETYPES:
        if a not in filled:
            filled[a] = {"n": 0, "avg_input": overall_in, "avg_output": overall_out}
    return filled


def cost(model_id: str, input_tokens: float, output_tokens: float) -> float:
    price = PRICE_PER_MTOK.get(model_id)
    if price is None:
        return float("nan")
    return (input_tokens / 1_000_000) * price["input"] + (output_tokens / 1_000_000) * price["output"]


def judge_cost_for(gen_output_tokens: float) -> tuple[float, float, float]:
    """Estimate one judge call's (input_tokens, output_tokens, dollar cost)
    given the generation's output token count (the submission being judged)."""
    submission_tokens = min(gen_output_tokens, JUDGE_SUBMISSION_TOKEN_CAP)
    judge_in = JUDGE_TEMPLATE_OVERHEAD_TOKENS + JUDGE_RUBRIC_BLOCK_TOKENS + JUDGE_ANSWER_KEY_TOKENS + submission_tokens
    judge_out = JUDGE_OUTPUT_TOKENS_ESTIMATE
    return judge_in, judge_out, cost(JUDGE_MODEL, judge_in, judge_out)


def estimate_model_full(model_id: str, effort: str) -> dict[str, Any]:
    sources, mode = resolve_sources(model_id, effort)
    stats = fallback_fill(load_archetype_tokens(sources))

    n_calls_per_archetype = len(ARMS) * len(CONDITIONS) * K_FULL  # 3 * 2 * 3 = 18
    gen_input = gen_output = 0.0
    judge_input = judge_output = 0.0
    n_gen_calls = 0
    n_judge_calls = 0

    per_archetype: dict[str, Any] = {}
    for arch in ARCHETYPES:
        s = stats[arch]
        arch_gen_in = s["avg_input"] * n_calls_per_archetype
        arch_gen_out = s["avg_output"] * n_calls_per_archetype
        gen_input += arch_gen_in
        gen_output += arch_gen_out
        n_gen_calls += n_calls_per_archetype

        arch_judge_in = arch_judge_out = 0.0
        n_judge = 0
        if arch in RUBRIC_ARCHETYPES:
            ji, jo, _ = judge_cost_for(s["avg_output"])
            n_judge = n_calls_per_archetype
            arch_judge_in = ji * n_judge
            arch_judge_out = jo * n_judge
            judge_input += arch_judge_in
            judge_output += arch_judge_out
            n_judge_calls += n_judge

        per_archetype[arch] = {
            "n_gen_calls": n_calls_per_archetype,
            "avg_input": s["avg_input"], "avg_output": s["avg_output"],
            "n_judge_calls": n_judge,
        }

    gen_cost = cost(model_id, gen_input, gen_output)
    judge_cost = cost(JUDGE_MODEL, judge_input, judge_output)

    return {
        "model": model_id, "source_mode": mode, "sources": sources,
        "n_gen_calls": n_gen_calls, "n_judge_calls": n_judge_calls,
        "gen_input_tokens": gen_input, "gen_output_tokens": gen_output,
        "judge_input_tokens": judge_input, "judge_output_tokens": judge_output,
        "gen_cost": gen_cost, "judge_cost": judge_cost,
        "total_cost": gen_cost + judge_cost,
        "per_archetype": per_archetype,
    }


def estimate_smoke() -> dict[str, Any]:
    sources, mode = resolve_sources(SMOKE_MODEL, "low")
    stats = fallback_fill(load_archetype_tokens(sources))
    s = stats["comprehend"]

    gen_cost = cost(SMOKE_MODEL, s["avg_input"], s["avg_output"])
    ji, jo, judge_cost = judge_cost_for(s["avg_output"])

    return {
        "model": SMOKE_MODEL, "source_mode": mode, "sources": sources,
        "avg_input": s["avg_input"], "avg_output": s["avg_output"],
        "gen_cost": gen_cost,
        "judge_input_tokens": ji, "judge_output_tokens": jo, "judge_cost": judge_cost,
        "total_cost": gen_cost + judge_cost,
    }


def fmt_usd(x: float) -> str:
    return f"${x:,.4f}" if x < 1 else f"${x:,.2f}"


def print_report(smoke: dict[str, Any], full: list[dict[str, Any]], effort: str) -> None:
    print("=" * 78)
    print("iac-cd-bench#40 - SMOKE + FULL matrix cost estimate (no network calls)")
    print("=" * 78)
    print()
    print("CAVEATS (see tools/estimate_matrix_cost.py module docstring for detail):")
    print("  - chant/bare arms have zero history; per-archetype averages across the")
    print("    five existing stacks are used as a stack-agnostic proxy.")
    print("  - claude-haiku-4-5 has zero history in results/; " +
          ("using proxy sources: " + ", ".join(smoke["sources"]) if smoke["source_mode"] == "proxy" else "no data found at all"))
    print("  - judge call cost is ALWAYS estimated (bench/judge.py's verdict dict")
    print("    carries no token usage field) - see JUDGE_* constants for the method.")
    print()

    print("-" * 78)
    print(f"SMOKE  (model={smoke['model']}, stack=chant, task=T1-comprehend, k=1,")
    print(f"        condition=warm, judge={JUDGE_MODEL}, effort={effort})")
    print("-" * 78)
    print(f"  token source: {smoke['source_mode']}"
          + (f" ({', '.join(smoke['sources'])})" if smoke["sources"] else " (NO DATA - all-zero estimate)"))
    print(f"  generation:  ~{smoke['avg_input']:,.0f} in / ~{smoke['avg_output']:,.0f} out tokens"
          f"  -> {fmt_usd(smoke['gen_cost'])}")
    print(f"  judge call:  ~{smoke['judge_input_tokens']:,.0f} in / ~{smoke['judge_output_tokens']:,.0f} out tokens"
          f"  -> {fmt_usd(smoke['judge_cost'])}")
    print(f"  SMOKE TOTAL: {fmt_usd(smoke['total_cost'])}")
    print()

    print("-" * 78)
    print(f"FULL matrix (models x {{{', '.join(ARMS)}}} x {{{', '.join(CONDITIONS)}}} x k={K_FULL}, "
          f"judge={JUDGE_MODEL}, effort={effort})")
    print("-" * 78)
    grand_total = smoke["total_cost"] * 0  # start at 0.0, keep smoke separate
    for m in full:
        print(f"  {m['model']}  (token source: {m['source_mode']}"
              + (f", {', '.join(m['sources'])})" if m["sources"] else ", NO DATA)"))
        print(f"    generation calls: {m['n_gen_calls']:3d}   "
              f"tokens: ~{m['gen_input_tokens']:,.0f} in / ~{m['gen_output_tokens']:,.0f} out"
              f"   -> {fmt_usd(m['gen_cost'])}")
        print(f"    judge calls:      {m['n_judge_calls']:3d}   "
              f"tokens: ~{m['judge_input_tokens']:,.0f} in / ~{m['judge_output_tokens']:,.0f} out"
              f"   -> {fmt_usd(m['judge_cost'])}")
        print(f"    subtotal: {fmt_usd(m['total_cost'])}")
        grand_total += m["total_cost"]
    print()
    print(f"  FULL MATRIX TOTAL ({len(full)} models): {fmt_usd(grand_total)}")
    print()
    print("=" * 78)
    print(f"GRAND TOTAL (SMOKE + FULL): {fmt_usd(grand_total + smoke['total_cost'])}")
    print("=" * 78)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--effort", default="low",
                         help="Reasoning effort tag to prefer when resolving token sources (default: low, matching #40)")
    parser.add_argument("--json", action="store_true", help="Also dump the raw numbers as JSON")
    args = parser.parse_args()

    smoke = estimate_smoke()
    full = [estimate_model_full(OPUS_MODEL, args.effort), estimate_model_full(HAIKU_MODEL, args.effort)]

    print_report(smoke, full, args.effort)

    if args.json:
        print()
        print(json.dumps({"smoke": smoke, "full": full}, indent=2, default=str))


if __name__ == "__main__":
    main()
