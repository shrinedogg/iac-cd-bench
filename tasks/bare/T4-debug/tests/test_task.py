"""Semantic grader for bare T4-debug: Deployment selector/template mismatch.

Accepts either valid fix strategy (add the missing label to the template,
or drop it from the selector) by checking the actual Kubernetes validation
rule directly: every key/value pair in spec.selector.matchLabels must also
appear in spec.template.metadata.labels. Also checks the Service still
selects the (fixed) pods.

The Deployment and Service are located by kind+name anywhere in the
workspace rather than in `prod/app.yaml` specifically (issue #72): nothing
in the prompt fixes that filename, and both objects carry `myapp-prod` as
their own identity. Every matching Deployment must satisfy the rule -- a
corrected copy written beside an untouched broken one is still a manifest
set kubectl would reject.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import _grader_lib as gl  # noqa: E402


@pytest.fixture(scope="module")
def all_docs():
    return gl.all_docs()


@pytest.fixture(scope="module")
def deployments(all_docs):
    return [d for _p, d in gl.find_docs(all_docs, kind="Deployment", name="myapp-prod")]


@pytest.fixture(scope="module")
def services(all_docs):
    return [d for _p, d in gl.find_docs(all_docs, kind="Service", name="myapp-prod")]


def test_selector_matches_template_labels(deployments):
    """The actual Kubernetes API server rule: selector must be a subset of
    the pod template's labels, or the object is rejected on apply."""
    assert deployments, ("myapp-prod Deployment not found anywhere in the workspace. "
                         f"Workspace contains: {gl.inventory()}")
    for deploy in deployments:
        selector = gl.deep_get(deploy, "spec", "selector", "matchLabels", default={}) or {}
        template_labels = gl.deep_get(
            deploy, "spec", "template", "metadata", "labels", default={}) or {}
        missing = {k: v for k, v in selector.items() if template_labels.get(k) != v}
        assert not missing, (
            f"Deployment selector requires labels not present on the pod template: {missing!r} "
            f"-- kubectl apply would still be rejected"
        )
        # Sanity: the fix shouldn't have gutted the original identifying labels
        assert selector.get("app") == "myapp" and selector.get("env") == "prod"


def test_service_still_selects_pods(deployments, services):
    """The Service's selector must remain satisfied by the (fixed) template labels."""
    assert deployments and services, (
        "myapp-prod Deployment or Service missing from the workspace. "
        f"Workspace contains: {gl.inventory()}"
    )
    template_labels = gl.deep_get(
        deployments[0], "spec", "template", "metadata", "labels", default={}) or {}
    for svc in services:
        svc_selector = gl.deep_get(svc, "spec", "selector", default={}) or {}
        missing = {k: v for k, v in svc_selector.items() if template_labels.get(k) != v}
        assert not missing, f"Service selector no longer matches pod template labels: {missing!r}"
