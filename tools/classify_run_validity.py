#!/usr/bin/env python3
"""Classify the #40 result set (results/claude-*-3arm/) under the run-validity
gate added for #59, and print per (model, stack, condition) counts.

This is the "re-validate the existing result set" deliverable from #59: the
144-run matrix committed on bench/results-40 predates the gate (its runs
carry `content` but no `validity` key, since ClaudeCliAdapter hadn't been
fixed yet and the gate didn't exist). Nothing here mutates those result
JSONs - they stay committed as-is, as provenance, matching the "a run the
gates rejected is not published" rule from the chant-bench-spirit gate
itself: the evidence is kept, the classification is printed as a report.

Usage:
    python3 tools/classify_run_validity.py [DIR ...]

With no arguments, classifies every results/claude-*-3arm/ directory.
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bench.validity import run_validity  # noqa: E402

DEFAULT_GLOB = "results/claude-*-3arm"


def find_result_sets(patterns: list[str]) -> list[Path]:
    dirs: list[Path] = []
    for pattern in patterns:
        matches = sorted(glob.glob(str(ROOT / pattern)))
        if not matches and (ROOT / pattern).is_dir():
            matches = [str(ROOT / pattern)]
        dirs.extend(Path(m) for m in matches if Path(m).is_dir())
    return dirs


def load_runs(result_dirs: list[Path]) -> list[dict]:
    runs = []
    for d in result_dirs:
        for f in sorted(d.rglob("*.json")):
            if f.name == "report.md" or "report" in f.stem:
                continue
            try:
                result = json.loads(f.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(result, dict) or "content" not in result:
                continue
            result["_path"] = str(f.relative_to(ROOT))
            runs.append(result)
    return runs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dirs", nargs="*", default=[DEFAULT_GLOB],
                         help="Result-set directories or globs (default: %(default)s)")
    args = parser.parse_args()

    result_dirs = find_result_sets(args.dirs)
    if not result_dirs:
        print(f"No result-set directories matched {args.dirs}", file=sys.stderr)
        sys.exit(1)

    runs = load_runs(result_dirs)
    if not runs:
        print("No run JSONs with a `content` field found.", file=sys.stderr)
        sys.exit(1)

    # (model, stack, condition) -> {"valid": n, "invalid": n, "reasons": {...}}
    buckets: dict[tuple[str, str, str], dict] = defaultdict(
        lambda: {"valid": 0, "invalid": 0, "reasons": defaultdict(int)}
    )
    invalid_rows = []
    total_valid = total_invalid = 0

    for r in runs:
        key = (r.get("model", "unknown"), r.get("stack", ""), r.get("condition", ""))
        v = run_validity(r)
        if v["valid"]:
            buckets[key]["valid"] += 1
            total_valid += 1
        else:
            buckets[key]["invalid"] += 1
            buckets[key]["reasons"][v["reason"]] += 1
            total_invalid += 1
            invalid_rows.append((r["_path"], key, v["reason"], v["content_length"]))

    print(f"Classified {len(runs)} runs across {len(result_dirs)} result set(s): "
          f"{[d.name for d in result_dirs]}")
    print(f"valid={total_valid} invalid={total_invalid}\n")

    print(f"{'model':22s} {'stack':12s} {'condition':6s}  valid/total  rejected reasons")
    print("-" * 90)
    for key in sorted(buckets):
        model, stack, condition = key
        b = buckets[key]
        total = b["valid"] + b["invalid"]
        reasons = ", ".join(f"{reason}:{n}" for reason, n in sorted(b["reasons"].items())) or "-"
        print(f"{model:22s} {stack:12s} {condition:6s}  {b['valid']:3d}/{total:<3d}      {reasons}")

    print("\nRejected runs (path, reason, content length):")
    for path, key, reason, length in sorted(invalid_rows, key=lambda row: row[3] or 0):
        print(f"  {length!s:>6}  {reason:28s} {path}")


if __name__ == "__main__":
    main()
