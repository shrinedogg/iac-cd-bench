"""Semantic grader for pulumi-python T3-modify: add an auto-scaling policy
without triggering resource replacement.

Written to close #111 -- see tasks/terraform/T3-modify/tests/test_task.py for
why the four ungraded arms mattered.

The task has two halves and the second is the hard one. Adding a scaling policy
is straightforward; doing it without replacement means not perturbing the
inputs Pulumi uses to decide whether a resource can be updated in place. A
grader that only looked for the policy would score a destructive answer full
marks, so the no-replacement half is asserted separately.

Source is read as text. Pulumi programs are Python, and importing a model's
module to inspect it would execute arbitrary code inside the grader.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


def _source() -> str:
    chunks = []
    for p in sorted(Path(".").rglob("*.py")):
        if any(part in (".venv", "venv", "node_modules") for part in p.parts):
            continue
        try:
            chunks.append(p.read_text())
        except OSError:
            continue
    if not chunks:
        out = Path("model_output.md")
        if out.is_file():
            try:
                chunks.append(out.read_text())
            except OSError:
                pass
    return "\n".join(chunks)


@pytest.fixture(scope="module")
def src() -> str:
    return _source()


def test_answer_is_present(src):
    assert src.strip(), "no Python and no model_output.md — nothing to grade"


def test_an_autoscaling_policy_is_declared(src):
    assert re.search(r"(?i)appautoscaling|autoscaling", src) and re.search(r"Policy", src), (
        "expected an auto-scaling Policy resource (e.g. "
        "aws.appautoscaling.Policy or aws.autoscaling.Policy)"
    )


def test_a_scaling_target_is_declared(src):
    """A policy with nothing to scale is inert. Application Auto Scaling needs
    a registered Target; EC2 auto scaling needs a Group."""
    assert re.search(
        r"(?i)appautoscaling\.Target|autoscaling\.Group|scalable_target|scalableTarget",
        src), (
        "expected a scaling Target (appautoscaling.Target) or an autoscaling "
        "Group — a Policy alone has nothing to act on"
    )


def test_the_policy_declares_how_it_scales(src):
    """A policy needs a configuration: target-tracking, step, or simple."""
    assert re.search(
        r"(?i)target_tracking|targetTracking|step_scaling|stepScaling|"
        r"policy_type|policyType|adjustment", src), (
        "expected the policy to declare how it scales — a target-tracking or "
        "step-scaling configuration, or an explicit policy type"
    )


def test_existing_resources_are_not_replaced(src):
    """The half a policy-only check would miss.

    Replacement in Pulumi is driven by changes to immutable inputs and by
    identity. `delete_before_replace` is the clearest tell: it does not avoid
    a replacement, it sequences one, so its presence means the author expected
    a replacement to happen -- which is what the prompt forbids.
    """
    assert not re.search(r"delete_before_replace\s*=\s*True", src), (
        "found delete_before_replace=True — that sequences a replacement "
        "rather than avoiding one, and the prompt requires no replacement"
    )
    assert not re.search(r"(?i)replace_on_changes|replaceOnChanges", src), (
        "found replace_on_changes; the prompt requires the policy be added "
        "without replacing existing resources"
    )
