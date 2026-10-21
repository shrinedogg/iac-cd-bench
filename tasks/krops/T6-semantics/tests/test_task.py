"""Semantic-understanding quiz grader — krops T6.

Parses the model's answers.json (from the workspace or from the last JSON
fenced block in model_output.md) and grades each question independently.
Runs with cwd = model workspace.
"""

import json
import re
from pathlib import Path

import pytest


def _load_answers() -> dict:
    ws = Path(".")
    # Preferred: extracted answers.json anywhere in the workspace
    candidates = sorted(ws.rglob("answers.json"))
    for c in candidates:
        try:
            return json.loads(c.read_text())
        except Exception:
            continue
    # Fallback: last JSON fenced block in model_output.md
    out = ws / "model_output.md"
    if out.exists():
        text = out.read_text()
        # Accept any fence info string (```json, ```answers.json, bare ```, etc.)
        blocks = re.findall(r"```([\w.-]*)[ \t]*\n(.*?)```", text, re.DOTALL)
        for _info, raw in reversed(blocks):
            raw = raw.strip()
            if not raw.startswith("{"):
                continue
            try:
                return json.loads(raw)
            except Exception:
                continue
        # Bare JSON: whole text, or the outermost {...} span (no fence at all)
        m = re.search(r"\{.*\}", text, re.DOTALL)
        for candidate in filter(None, (text.strip(), m.group(0) if m else "")):
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict) and ("q1" in parsed or "q2" in parsed):
                    return parsed
            except Exception:
                continue
    pytest.fail("no parseable answers.json found in workspace or model_output.md")


@pytest.fixture(scope="module")
def answers() -> dict:
    return _load_answers()


def _norm(v) -> str:
    return str(v).strip().lower().replace("_", "-")


def test_q1_rds_class_change_in_place(answers):
    """dbInstanceClass is mutable in ACK: ModifyDBInstance, not replacement —
    the instance keeps its dbInstanceIdentifier."""
    assert "updated" in _norm(answers.get("q1", "")) or _norm(answers.get("q1", "")) == "in-place", \
        f"q1: expected updated-in-place, got {answers.get('q1')!r}"


def test_q2_removed_input_gc_cascade(answers):
    """The ResourceSet operator GCs resources it rendered that no longer exist
    in the new revision (inventory-driven, not the management flux-apps
    Kustomization). And because the rendered Kustomization carries
    prune: true, deleting it triggers removal of the objects it had applied
    to the workload cluster — the bundle (incl. the FluxInstance) does NOT
    survive."""
    q2 = answers.get("q2") or {}
    deleted_by = _norm(q2.get("deleted_by", ""))
    assert "resource-set" in deleted_by or "resourceset" in deleted_by, \
        f"q2 deleted_by: expected the ResourceSet operator's own GC, got {q2.get('deleted_by')!r}"
    survives = q2.get("bundle_survives")
    if isinstance(survives, str):
        survives = survives.strip().lower() in ("true", "yes")
    assert survives is False, \
        f"q2 bundle_survives: expected false (the rendered Ks has prune: true, so its " \
        f"deletion prunes the remote inventory incl. the FluxInstance), got {q2.get('bundle_survives')!r}"


def test_q3_default_deletion_policy_deletes(answers):
    """No deletion-policy annotation on the Bucket: ACK's default policy is
    delete — the AWS S3 bucket is removed when the CR is pruned."""
    assert "delet" in _norm(answers.get("q3", "")), \
        f"q3: expected deleted, got {answers.get('q3')!r}"


def test_q4_adopt_or_create(answers):
    """adoption-policy: adopt-or-create — the post-pivot controller adopts the
    existing AWS resource instead of going Terminal with 'Resource already
    exists' (or creating a duplicate)."""
    assert "adopt" in _norm(answers.get("q4", "")), \
        f"q4: expected adoption of the existing resource, got {answers.get('q4')!r}"


def test_q5_cluster_vars_change_touches_nothing_in_mgmt(answers):
    """The regions/ tree is not part of the flux-apps Kustomization's path —
    its kustomize build renders only flux-operator.yaml and
    flux-apps-rset.yaml, neither of which changed. The new key reaches the
    workload cluster via the RENDERED flux-apps-eu-west-1 Kustomization, not
    via this one."""
    q5 = answers.get("q5") or {}
    changes = q5.get("changes")
    if isinstance(changes, str):
        changes = changes.strip().lower() in ("true", "yes")
    assert changes is False, f"q5 changes: expected false, got {q5.get('changes')!r}"
    reason = _norm(q5.get("reason", ""))
    assert any(tok in reason for tok in ("path", "not part", "no-op", "noop", "resources")), \
        f"q5 reason should ground the no-op in the Kustomization's path/rendered output, got {q5.get('reason')!r}"


def test_q6_new_input_renders_remote_apply(answers):
    """A new input renders a new Kustomization flux-apps-eu-central-1 in
    namespace default on the management cluster; the management cluster's
    kustomize-controller remote-applies the bundle via the CAPI kubeconfig
    Secret."""
    q6 = answers.get("q6") or {}
    new_object = _norm(q6.get("new_object", ""))
    assert "flux-apps-eu-central-1" in new_object, \
        f"q6 new_object: expected the rendered Kustomization flux-apps-eu-central-1, got {q6.get('new_object')!r}"
    applied_by = _norm(q6.get("applied_by", ""))
    assert "kustomize" in applied_by, \
        f"q6 applied_by: expected the kustomize-controller (remote apply via the kubeConfig secretRef), got {q6.get('applied_by')!r}"


def test_q7_reconcile_order(answers):
    """Bootstrap order follows the dependsOn chains: cert-manager →
    capi-operator → capi-system → capa-system → eu-north-1."""
    q7 = answers.get("q7") or []
    normed = [_norm(x) for x in q7]
    assert normed == ["cert-manager", "capi-operator", "capi-system", "capa-system", "eu-north-1"], \
        f"q7: expected [cert-manager, capi-operator, capi-system, capa-system, eu-north-1], got {q7!r}"
