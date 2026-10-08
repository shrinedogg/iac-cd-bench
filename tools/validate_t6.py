#!/usr/bin/env python3
"""Self-validation: every T6 golden answer key must pass its own grader.

Simulates the runner: creates a workspace, writes the golden answers.json
(extracted from golden/answer_key.md), and runs the task's pytest grader with
cwd = workspace. All 7 tests must pass for every stack.

Also negative-checks: an empty workspace must FAIL (grader can't vacuously pass).
"""

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS = ROOT / "tasks"
STACKS = ["knr-ops", "crossplane", "terraform", "pulumi-python", "pulumi-typescript", "chant", "bare"]

failures = []

for stack in STACKS:
    task_dir = TASKS / stack / "T6-semantics"
    if stack == "chant" and not (task_dir / "golden" / "answer_key.md").exists():
        # TODO(#3/#4): tasks/chant/T6-semantics doesn't exist yet; drop this
        # guard once the chant golden base + tasks land.
        print(f"SKIP {stack}: T6-semantics not yet materialized (see #3/#4)")
        continue
    if stack == "bare" and not (task_dir / "golden" / "answer_key.md").exists():
        # TODO(#37/#38): tasks/bare/T6-semantics doesn't exist yet; drop this
        # guard once the bare golden base + tasks land.
        print(f"SKIP {stack}: T6-semantics not yet materialized (see #37/#38)")
        continue
    key_md = (task_dir / "golden" / "answer_key.md").read_text()
    m = re.search(r"```json\s*\n(.*?)```", key_md, re.DOTALL)
    assert m, f"{stack}: no JSON block in golden answer key"
    golden = json.loads(m.group(1))

    # Positive: golden answers must pass all 7 graders
    with tempfile.TemporaryDirectory(prefix=f"t6-golden-{stack}-") as ws:
        (Path(ws) / "answers.json").write_text(json.dumps(golden))
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
             str(task_dir / "tests" / "test_task.py")],
            capture_output=True, text=True, cwd=ws, timeout=120,
        )
        if proc.returncode != 0:
            failures.append((stack, "golden-should-pass", proc.stdout[-1500:]))
        else:
            passed = re.search(r"(\d+) passed", proc.stdout)
            n = int(passed.group(1)) if passed else 0
            if n != 7:
                failures.append((stack, f"expected 7 passed, got {n}", proc.stdout[-800:]))
            else:
                print(f"OK   {stack}: golden answers pass 7/7")

    # Negative: empty workspace must fail (no vacuous pass)
    with tempfile.TemporaryDirectory(prefix=f"t6-empty-{stack}-") as ws:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
             str(task_dir / "tests" / "test_task.py")],
            capture_output=True, text=True, cwd=ws, timeout=120,
        )
        if proc.returncode == 0:
            failures.append((stack, "empty-workspace-should-fail", proc.stdout[-800:]))
        else:
            print(f"OK   {stack}: empty workspace fails as expected")

if failures:
    print("\nFAILURES:")
    for stack, kind, log in failures:
        print(f"--- {stack} [{kind}]\n{log}\n")
    sys.exit(1)

print("\nAll T6 graders self-validate.")
