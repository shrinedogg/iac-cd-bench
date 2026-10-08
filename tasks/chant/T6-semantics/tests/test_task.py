"""Semantic-understanding quiz grader -- chant T6.

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
        blocks = re.findall(r"```([\w.-]*)[ \t]*\n(.*?)```", text, re.DOTALL)
        for _info, raw in reversed(blocks):
            raw = raw.strip()
            if not raw.startswith("{"):
                continue
            try:
                return json.loads(raw)
            except Exception:
                continue
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


def test_q1_fold_falls_back_not_a_build_error(answers):
    """A file that can't fold falls back to running, per file; it's not a build failure."""
    q1 = answers.get("q1") or {}
    assert "fall" in _norm(q1.get("behavior", "")) or "run" in _norm(q1.get("behavior", "")), \
        f"q1 behavior: expected falls-back-to-run, got {q1.get('behavior')!r}"


def test_q2_ownership_answered_by_live_marker(answers):
    """chant has no authoritative state file; ownership is read from a live marker."""
    q2 = answers.get("q2") or {}
    assert "marker" in _norm(q2.get("answered_by", "")), \
        f"q2 answered_by: expected live-marker, got {q2.get('answered_by')!r}"


def test_q3_orphan_classification(answers):
    """Live, never declared, never in any prior snapshot -- textbook orphan."""
    assert _norm(answers.get("q3", "")) == "orphan", \
        f"q3: expected orphan, got {answers.get('q3')!r}"


def test_q4_unmarked_orphan_is_adopt_not_delete(answers):
    """delete requires a confirming ownership marker; an unmarked orphan is adopt."""
    q4 = answers.get("q4") or {}
    assert _norm(q4.get("action", "")) == "adopt", \
        f"q4 action: expected adopt, got {q4.get('action')!r}"


def test_q5_build_never_calls_cloud(answers):
    """chant build is pure synthesis -- no cloud/cluster network calls."""
    q5 = answers.get("q5") or {}
    calls_cloud = _as_bool(q5.get("calls_cloud"))
    assert calls_cloud is False, f"q5 calls_cloud: expected false, got {q5.get('calls_cloud')!r}"


def test_q6_snapshot_needs_no_controller_reconcile(answers):
    """lifecycle snapshot only needs objects to exist and be GET-able, not reconciled."""
    q6 = answers.get("q6") or {}
    requires = _as_bool(q6.get("requires_controller_reconcile"))
    assert requires is False, \
        f"q6 requires_controller_reconcile: expected false, got {q6.get('requires_controller_reconcile')!r}"


def test_q7_missing_edges_specific_to_k8s_lexicon(answers):
    """The --at/--live edge gap is per-lexicon (k8s today), not a format limitation."""
    q7 = answers.get("q7") or {}
    specific = _as_bool(q7.get("specific_to_k8s_lexicon"))
    assert specific is True, \
        f"q7 specific_to_k8s_lexicon: expected true, got {q7.get('specific_to_k8s_lexicon')!r}"
