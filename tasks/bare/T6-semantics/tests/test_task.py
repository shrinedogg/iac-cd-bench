"""Semantic-understanding quiz grader — bare T6.

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


def _as_bool(v):
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes")
    return v


def test_q1_rds_class_change_in_place(answers):
    """dbInstanceClass is mutable: ModifyDBInstance, not replacement."""
    assert "updated" in _norm(answers.get("q1", "")) or _norm(answers.get("q1", "")) == "in-place", \
        f"q1: expected updated-in-place, got {answers.get('q1')!r}"


def test_q2_no_prune_by_default(answers):
    """Plain kubectl apply -f never deletes objects absent from the given files."""
    q2 = answers.get("q2") or {}
    deleted = _as_bool(q2.get("deleted"))
    assert deleted is False, f"q2 deleted: expected false, got {q2.get('deleted')!r}"
    assert "prune" in _norm(q2.get("reason", "")), \
        f"q2 reason should mention the absence of pruning, got {q2.get('reason')!r}"


def test_q3_aws_bucket_persists(answers):
    """No prune happened, so ACK was never told to delete the AWS bucket."""
    assert _norm(answers.get("q3", "")) == "exists", \
        f"q3: expected exists, got {answers.get('q3')!r}"


def test_q4_immutable_selector_rejected(answers):
    """spec.selector is immutable on an existing Deployment."""
    q4 = answers.get("q4") or {}
    succeeds = _as_bool(q4.get("succeeds"))
    assert succeeds is False, f"q4 succeeds: expected false, got {q4.get('succeeds')!r}"
    assert "immutable" in _norm(q4.get("reason", "")), \
        f"q4 reason should mention immutability, got {q4.get('reason')!r}"


def test_q5_client_side_apply_default(answers):
    """Plain kubectl apply -f defaults to client-side apply."""
    assert "client" in _norm(answers.get("q5", "")), \
        f"q5: expected client-side, got {answers.get('q5')!r}"


def test_q6_namespace_applied_first(answers):
    """00-namespaces.yaml sorts first by filename; kubectl has no dependency graph."""
    q6 = answers.get("q6") or {}
    namespace_first = _as_bool(q6.get("namespace_first"))
    assert namespace_first is True, \
        f"q6 namespace_first: expected true, got {q6.get('namespace_first')!r}"


def test_q7_no_cross_directory_gating(answers):
    """Plain kubectl apply has no dependsOn/health-check gate across directories."""
    q7 = answers.get("q7") or {}
    blocked = _as_bool(q7.get("blocked"))
    assert blocked is False, f"q7 blocked: expected false, got {q7.get('blocked')!r}"
