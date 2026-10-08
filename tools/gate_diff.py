#!/usr/bin/env python3
"""Differential check: does the contract gate agree with the helper it replaces?

`bench/stages/contract.py` and `bench/stages/gates.py` are, as of this commit,
imported by nothing but `tests/test_gate_contract.py`. `run_static` still
dispatches to the legacy `_chant_static` / `_terraform_static` / ... helpers, so
every invariant the contract enforces -- a pass must have examined something, an
abstention must say which of the three reasons it is -- is enforced only over
the conformance fixtures and not over a single real run. The safety net is
written and not installed.

Installing it is a one-line change in `run_static`. What makes that change
hard to justify is that nobody has shown the two implementations agree, and if
they disagree then results either side of the switch are not comparable -- the
`--compare` refusal in `bench.validate` exists for exactly this.

So: rebuild the workspace each stored run actually produced (the same
`rematerialize` the offline regrade uses -- the model's completion is in the
result JSON), run BOTH implementations against it, and compare verdicts.

    python3 tools/gate_diff.py results/claude-haiku-4-5-coverage-v10

Every disagreement is a finding in one direction or the other. A contract gate
that abstains where the helper passed is usually invariant 1 catching a vacuous
pass -- that is the tool working. A contract gate that passes where the helper
failed is a regression in the migration. Both need reading; neither is decided
here.

Two caveats on the method, both load-bearing:

  * Each implementation gets its OWN freshly materialized workspace. The gates
    mutate what they touch -- `terraform init` writes `.terraform`, pulumi
    writes stack state -- so running the second against the first's leavings
    compares a clean input to a dirty one and finds differences that are not
    there.
  * Both shell out to real tools, so this is only as reproducible as the
    machine. A stack whose gate needs credentials (pulumi, see the "not yet
    guarded" note in docs/result-integrity.md) will agree or disagree
    differently depending on whether `~/.aws/credentials` exists. Run it where
    the benchmark runs.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bench.stages.gates  # noqa: F401,E402 -- populates GATES via register()
from bench.stages.contract import GATES  # noqa: E402
from bench.stages import lint as lint_mod  # noqa: E402
from bench.stages import static as static_mod  # noqa: E402
from tools.regrade_offline import rematerialize  # noqa: E402

TASKS_DIR = ROOT / "tasks"

# Stacks whose gate reads a shared node_modules. `rematerialize` skips the
# bootstrap by default because a semantic regrade does not need it; a static
# gate does.
NEEDS_NODE_MODULES = ("chant", "pulumi-typescript")

# static.run_static now dispatches to GATES itself (#111), so "legacy" here
# calls the pre-migration per-stack helpers directly -- the same dispatch
# run_static used to do -- rather than through run_static, which would just
# compare the contract to itself. Keeps this tool meaningful for whichever
# gate gets touched next.
_LEGACY_STATIC = {
    "knr-ops": static_mod._knr_ops_static,
    "crossplane": static_mod._crossplane_static,
    "terraform": static_mod._terraform_static,
    "chant": static_mod._chant_static,
    "bare": static_mod._bare_static,
}


def _legacy_static(workspace: Path, stack: str) -> dict[str, Any]:
    if stack in ("pulumi-python", "pulumi-typescript"):
        fn = lambda ws, results: static_mod._pulumi_static(ws, results, stack)  # noqa: E731
    else:
        fn = _LEGACY_STATIC.get(stack)
    if fn is None:
        return lint_mod.inapplicable(
            f"no static commands for stack: {stack}", "gate_defect")
    results: list[str] = []
    all_passed, acted = fn(workspace, results)
    if not acted:
        return lint_mod.inapplicable("\n".join(results) or "nothing to build in workspace")
    return {
        "passed": all_passed,
        "logs": "\n".join(results) if results else "static validation passed",
    }


def verdict(stage: dict[str, Any] | None) -> str:
    """Collapse a stage dict to the string the two implementations must match.

    The reason code travels with an abstention deliberately. `by_spec` and
    `gate_defect` are both "inapplicable" and scoring treats them completely
    differently (#110), so two gates that abstain for different reasons have
    NOT agreed.
    """
    if not isinstance(stage, dict):
        return "missing"
    if stage.get("inapplicable"):
        return f"inapplicable:{stage.get('inapplicable_reason') or 'unclassified'}"
    if "passed" not in stage:
        return "missing"
    return "pass" if stage.get("passed") else "fail"


def _run_one(result: dict[str, Any], impl: str) -> dict[str, Any]:
    """Materialize a fresh workspace and gate it with one implementation."""
    stack = str(result.get("stack") or "")
    task = str(result.get("task") or "")
    task_dir = TASKS_DIR / stack / task
    if not task_dir.is_dir():
        return {"missing_task_dir": str(task_dir)}

    workspace = Path(tempfile.mkdtemp(prefix=f"gatediff-{impl}-{stack}-"))
    try:
        rematerialize(result, task_dir, workspace,
                      with_node_modules=stack in NEEDS_NODE_MODULES)
        if impl == "legacy":
            return _legacy_static(workspace, stack)
        return GATES[stack].run(workspace).to_legacy()
    except Exception as exc:  # noqa: BLE001 -- a crash IS the finding
        return {"crashed": f"{type(exc).__name__}: {exc}"}
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def compare_run(path: Path) -> dict[str, Any] | None:
    """Gate one stored run both ways. Returns None for runs not worth gating."""
    result = json.loads(path.read_text())
    stack = str(result.get("stack") or "")
    if result.get("error"):
        return None
    if stack not in GATES:
        return None
    # A task whose spec disables the static stage never exercised either
    # implementation; comparing them here would invent a measurement.
    stored = (result.get("stages") or {}).get("static")
    if isinstance(stored, dict) and stored.get("skipped"):
        return None

    legacy = _run_one(result, "legacy")
    contract = _run_one(result, "contract")
    return {
        "path": str(path),
        "stack": stack,
        "task": result.get("task"),
        "condition": result.get("condition"),
        "stored": verdict(stored),
        "legacy": verdict(legacy),
        "contract": verdict(contract),
        "agree": verdict(legacy) == verdict(contract),
        "contract_examined": contract.get("examined"),
        "legacy_logs": (legacy.get("logs") or legacy.get("crashed") or "")[-1200:],
        "contract_logs": (contract.get("logs") or contract.get("crashed") or "")[-1200:],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("results_dir", type=Path)
    ap.add_argument("--stacks", default="", help="comma-separated subset")
    ap.add_argument("--out", type=Path, default=None,
                    help="write the full comparison, disagreements included, as JSON")
    ap.add_argument("--jobs", type=int, default=4,
                    help="runs to gate concurrently (each pair gets its own "
                         "tempdir per run, so this is safe to raise)")
    args = ap.parse_args()

    wanted = {s.strip() for s in args.stacks.split(",") if s.strip()}
    all_paths = sorted(args.results_dir.glob("*/*/*run*.json"))
    if not all_paths:
        print(f"no runs under {args.results_dir}", file=sys.stderr)
        return 2

    paths: list[Path] = []
    for p in all_paths:
        try:
            peek = json.loads(p.read_text())
        except Exception:  # noqa: BLE001
            continue
        if wanted and peek.get("stack") not in wanted:
            continue
        paths.append(p)

    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        futures = {pool.submit(compare_run, p): p for p in paths}
        for fut in as_completed(futures):
            row = fut.result()
            if row is None:
                continue
            rows.append(row)
            flag = "ok " if row["agree"] else "DIFF"
            print(f"  {flag} {row['stack']:<18} {row['task']:<14} {row['condition']:<5} "
                  f"legacy={row['legacy']:<26} contract={row['contract']}")
    rows.sort(key=lambda r: (r["stack"], r["task"], r["condition"]))

    print()
    per_stack: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        per_stack[r["stack"]]["agree" if r["agree"] else "differ"] += 1

    print(f"{'stack':<20} {'agree':>6} {'differ':>7}")
    for stack in sorted(per_stack):
        c = per_stack[stack]
        print(f"{stack:<20} {c['agree']:>6} {c['differ']:>7}")
    differ = [r for r in rows if not r["agree"]]
    print(f"\n{len(rows)} runs gated both ways, {len(differ)} disagreements")

    if differ:
        print("\n--- disagreements ---")
        for r in differ:
            print(f"\n{r['stack']}/{r['task']}/{r['condition']}  {r['path']}")
            print(f"  legacy   : {r['legacy']}")
            print(f"  contract : {r['contract']}  examined={r['contract_examined']}")

    if args.out:
        args.out.write_text(json.dumps(rows, indent=2))
        print(f"\nwrote {args.out}")

    # Disagreement is a finding, not a failure: this tool reports, the human
    # decides which implementation is right. Non-zero only if nothing ran.
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
