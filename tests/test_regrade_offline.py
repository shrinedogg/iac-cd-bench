"""Tests for tools/regrade_offline.py.

The tool exists so a grader fix does not cost another matrix of model
calls: every stored run carries the model's completion, so the workspace
the grader saw can be rebuilt and re-graded offline. Two properties matter
and are checked here -- it must rebuild the workspace the way the *runner*
does (not a lookalike), and it must never touch the originals.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "regrade_offline", ROOT / "tools" / "regrade_offline.py")
regrade_offline = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(regrade_offline)


KNR_ANSWER = """\
Here is the logs bucket.

`infra/s3/logs/bucket.yaml`
```yaml
apiVersion: s3.aws.upbound.io/v1beta1
kind: Bucket
metadata:
  name: logs-bucket
spec:
  forProvider:
    region: us-east-1
    versioningConfiguration:
      status: Enabled
    serverSideEncryptionConfiguration:
      rules:
        - applyServerSideEncryptionByDefault:
            sseAlgorithm: AES256
---
apiVersion: s3.aws.upbound.io/v1beta1
kind: BucketPublicAccessBlock
metadata:
  name: logs-bucket-pab
spec:
  forProvider:
    bucket: myapp-logs-dev
    blockPublicAcls: true
    blockPublicPolicy: true
    ignorePublicAcls: true
    restrictPublicBuckets: true
```

`infra/iam/logs-role.yaml`
```yaml
apiVersion: iam.aws.upbound.io/v1beta1
kind: Role
metadata:
  name: myapp-logs-irsa
spec:
  forProvider:
    path: /
```

`flux/logs-kustomization.yaml`
```yaml
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: myapp-logs
  namespace: flux-system
spec:
  interval: 5m0s
  path: ./overlays/dev/logs
  prune: true
  sourceRef:
    kind: GitRepository
    name: myapp-infra
```
"""


def _stored_run(content: str, semantic: dict) -> dict:
    return {
        "model": "test-model",
        "task": "T2-generate",
        "stack": "knr-ops",
        "run": 0,
        "condition": "cold",
        "content": content,
        "stages": {
            "lint": {"passed": True, "logs": "yq: exit=0"},
            "static": {"passed": True, "logs": "kustomize build: exit=0"},
            "semantic": semantic,
        },
    }


@pytest.fixture()
def results_tree(tmp_path):
    src = tmp_path / "results-in"
    (src / "knr-ops" / "cold").mkdir(parents=True)
    run = _stored_run(
        KNR_ANSWER,
        # The verdict the path-exact grader produced: every assertion
        # errored out on one FileNotFoundError.
        {"passed": False, "passed_count": 0, "total_count": 6,
         "logs": "FileNotFoundError: 'infra/s3/logs-bucket.yaml'"},
    )
    (src / "knr-ops" / "cold" / "T2-generate_run0.json").write_text(
        json.dumps(run, indent=2))
    (src / "_provenance.json").write_text(json.dumps({"provenance": {}}))
    return src


def test_regrade_recovers_a_run_the_old_grader_zeroed(results_tree, tmp_path):
    out = tmp_path / "results-out"
    summary = regrade_offline.regrade_tree(results_tree, out, verbose=False)

    assert summary["runs"] == 1
    assert summary["regraded"] == 1
    assert summary["fail_to_pass"] == 1
    assert summary["assertion_delta"] == 6

    corrected = json.loads(
        (out / "knr-ops" / "cold" / "T2-generate_run0.json").read_text())
    assert corrected["stages"]["semantic"]["passed"] is True
    assert corrected["stages"]["semantic"]["passed_count"] == 6
    assert corrected["regrade"]["direction"] == "fail_to_pass"
    assert corrected["regrade"]["grader_sha256"]
    # Scores are recomputed, not carried over: correctness and completeness
    # both read the semantic stage.
    assert corrected["score"]["composite"] > 0


def test_originals_are_never_mutated(results_tree, tmp_path):
    before = (results_tree / "knr-ops" / "cold" / "T2-generate_run0.json").read_text()
    regrade_offline.regrade_tree(results_tree, tmp_path / "out", verbose=False)
    after = (results_tree / "knr-ops" / "cold" / "T2-generate_run0.json").read_text()
    assert before == after


def test_set_level_manifests_are_copied_not_regraded(results_tree, tmp_path):
    out = tmp_path / "out"
    regrade_offline.regrade_tree(results_tree, out, verbose=False)
    assert json.loads((out / "_provenance.json").read_text()) == {"provenance": {}}


def test_run_without_content_is_skipped(tmp_path):
    src = tmp_path / "in"
    (src / "knr-ops" / "cold").mkdir(parents=True)
    run = _stored_run("", {"passed": False, "passed_count": 0, "total_count": 6})
    (src / "knr-ops" / "cold" / "T2-generate_run0.json").write_text(json.dumps(run))
    summary = regrade_offline.regrade_tree(src, tmp_path / "out", verbose=False)
    assert summary["skipped"] == 1 and summary["regraded"] == 0


def test_rematerialize_uses_the_runners_own_extractor(tmp_path):
    """Not a lookalike: the extraction must be the runner's own
    extract_code_blocks_detailed, or a regrade measures a workspace no run
    ever had."""
    from bench import runner as runner_mod

    calls = []
    original = runner_mod.extract_code_blocks_detailed

    def spy(content, workspace, stack="knr-ops"):
        calls.append(stack)
        return original(content, workspace, stack)

    runner_mod.extract_code_blocks_detailed = spy
    ws = Path(tempfile.mkdtemp(prefix="regrade-test-"))
    try:
        written = regrade_offline.rematerialize(
            _stored_run(KNR_ANSWER, {}), ROOT / "tasks" / "knr-ops" / "T2-generate", ws)
    finally:
        runner_mod.extract_code_blocks_detailed = original
        shutil.rmtree(ws, ignore_errors=True)

    assert calls == ["knr-ops"]
    assert any(str(p).endswith("infra/s3/logs/bucket.yaml") for p in written)


def test_extraction_refusals_are_recorded_on_the_regraded_run(tmp_path):
    """A path that would escape the workspace is refused, and the refusal
    travels on the regraded result rather than vanishing (#76)."""
    run = _stored_run(
        "Put it at `/etc/pwned.yaml`:\n\n"
        "```yaml\napiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: x\n```\n", {})
    corrected = regrade_offline.regrade_run(run, ROOT / "tasks")

    assert corrected["extraction_errors"]
    assert "outside the workspace" in corrected["extraction_errors"][0]
    assert not Path("/etc/pwned.yaml").exists()
