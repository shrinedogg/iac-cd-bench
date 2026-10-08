"""Every grader must fail its own unmodified seed (#75).

The counterpart to tests/test_golden_gates.py. That file pins "a correct
answer passes"; this one pins "a no-op fails". A benchmark needs both, and
this repo had neither:

- Gates that could not pass turned correct answers into failures (#81, #82).
- Graders that could not fail turned no-ops into full marks — terraform
  T4-debug scored 3/3 against its own untouched seed, so a model that emitted
  nothing at all earned the debug archetype outright.

The seed is the ideal negative fixture: it is exactly the input the task
describes as broken, so a grader that accepts it is by definition not
measuring the fix.

Partial credit on a seed is allowed and often correct — a task can assert
invariants the seed already satisfies ("the database still exists"). What is
never acceptable is the whole grader passing.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from bench.stages import semantic as semantic_mod

ROOT = Path(__file__).resolve().parent.parent
TASKS = ROOT / "tasks"


def _tasks_with_seed_and_grader() -> list[tuple[str, Path]]:
    found = []
    for stack_dir in sorted(p for p in TASKS.iterdir() if p.is_dir()):
        for task_dir in sorted(p for p in stack_dir.iterdir() if p.is_dir()):
            if (task_dir / "tests" / "test_task.py").is_file() and (task_dir / "seed").is_dir():
                found.append((f"{stack_dir.name}/{task_dir.name}", task_dir))
    return found


CASES = _tasks_with_seed_and_grader()


def test_there_are_graders_to_check():
    """If this ever collects nothing, the parametrization broke and every
    assertion below silently stopped running."""
    assert len(CASES) >= 15, f"only found {len(CASES)} seeded graders"


@pytest.mark.parametrize("name,task_dir", CASES, ids=[c[0] for c in CASES])
def test_grader_rejects_its_own_seed(name, task_dir):
    ws = Path(tempfile.mkdtemp(prefix="seedcheck-"))
    try:
        shutil.copytree(task_dir / "seed", ws, dirs_exist_ok=True, symlinks=True)
        result = semantic_mod.run_semantic(task_dir, ws)
    finally:
        shutil.rmtree(ws, ignore_errors=True)

    if result.get("inapplicable") or result.get("skipped"):
        pytest.skip("grader is inapplicable for this task")

    passed_count = result.get("passed_count", 0)
    total_count = result.get("total_count", 0)

    assert not result.get("passed"), (
        f"{name}: the grader PASSES its own unmodified seed "
        f"({passed_count}/{total_count} assertions). A model that changes "
        "nothing scores full marks on this archetype. Assert the defect is "
        "absent and the correction present, not that a string the seed "
        "already contains is still there."
    )
