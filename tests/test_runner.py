"""
Integration tests for the benchmark runner.
Tests task materialization, stage runners, and result formats without needing a model.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import bench.runner as runner
from bench.grounding import SchemaCache

ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / "tasks"
RESULTS_DIR = ROOT / "results"


def test_runner_executes():
    """The runner can be invoked without crashing."""
    result = subprocess.run(
        [sys.executable, "-m", "bench.runner", "--help"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0
    assert "IaC/CD Benchmark Runner" in result.stdout
    assert "--grounding" in result.stdout


def test_grounding_validation_accepts_only_supported_stacks():
    runner.validate_grounding_stacks(
        ["knr-ops", "crossplane"], grounding=True, condition="cold", results_tag="grounded"
    )

    with pytest.raises(ValueError, match="supports knr-ops and crossplane only"):
        runner.validate_grounding_stacks(
            ["terraform"], grounding=True, condition="cold", results_tag="grounded"
        )


def test_grounding_validation_rejects_all_when_it_contains_unsupported_stacks():
    with pytest.raises(ValueError, match="unsupported stack.*pulumi-python"):
        runner.validate_grounding_stacks(
            runner.ALL_STACKS, grounding=True, condition="cold", results_tag="grounded"
        )


def test_grounding_validation_requires_cold_condition():
    with pytest.raises(ValueError, match="--grounding requires --condition cold"):
        runner.validate_grounding_stacks(
            ["knr-ops"], grounding=True, condition="warm", results_tag="grounded"
        )

    runner.validate_grounding_stacks(
        ["knr-ops"], grounding=True, condition="cold", results_tag="grounded"
    )


def test_grounding_validation_requires_non_empty_results_tag():
    with pytest.raises(ValueError, match="--grounding requires a non-empty --results-tag"):
        runner.validate_grounding_stacks(
            ["knr-ops"], grounding=True, condition="cold", results_tag=None
        )

    with pytest.raises(ValueError, match="--grounding requires a non-empty --results-tag"):
        runner.validate_grounding_stacks(
            ["knr-ops"], grounding=True, condition="cold", results_tag=""
        )

    with pytest.raises(ValueError, match="--grounding requires a non-empty --results-tag"):
        runner.validate_grounding_stacks(
            ["knr-ops"], grounding=True, condition="cold", results_tag="   "
        )


def test_non_grounded_validation_preserves_warm_condition():
    runner.validate_grounding_stacks(["terraform"], grounding=False, condition="warm")


def test_non_grounded_validation_preserves_optional_results_tag():
    runner.validate_grounding_stacks(["terraform"], grounding=False, results_tag=None)


def test_cli_rejects_grounding_with_warm_condition():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "bench.runner",
            "--model",
            "fake",
            "--stack",
            "knr-ops",
            "--grounding",
            "--condition",
            "warm",
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )

    assert result.returncode == 2
    assert "--grounding requires --condition cold" in result.stderr


class CapturingAdapter(runner.ModelAdapter):
    def __init__(self):
        self.prompts = []

    @property
    def name(self):
        return "fake"

    def complete(self, prompt, files):
        self.prompts.append(prompt)
        return {"content": "No generated files.", "input_tokens": 1, "output_tokens": 1}


class FakeGroundingClient:
    def __init__(self, schema='{"type": "object"}'):
        self.schema = schema
        self.calls = []

    def get_schema(self, kind, api_version):
        self.calls.append((kind, api_version))
        return self.schema


def _grounded_task(tmp_path):
    task_dir = tmp_path / "T-grounded"
    (task_dir / "seed").mkdir(parents=True)
    (task_dir / "spec.yaml").write_text("id: T-grounded\nstack: knr-ops\n")
    (task_dir / "prompt.md").write_text("Write the manifest.\n")
    (task_dir / "seed" / "resource.yaml").write_text(
        "apiVersion: example.org/v1\nkind: Widget\nspec: {}\n"
    )
    return task_dir


def test_run_task_appends_grounding_prompt_and_records_metadata(tmp_path, monkeypatch):
    task_dir = _grounded_task(tmp_path)
    adapter = CapturingAdapter()
    client = FakeGroundingClient()
    cache = SchemaCache(tmp_path / "cache")
    monkeypatch.setattr(runner.lint, "run_lint", lambda *_: {"passed": True})
    monkeypatch.setattr(runner.static, "run_static", lambda *_: {"passed": True})
    monkeypatch.setattr(runner.semantic, "run_semantic", lambda *_: {"passed": True})

    results = runner.run_task(
        task_dir,
        adapter,
        1,
        False,
        "warm",
        grounding=True,
        grounding_client=client,
        grounding_cache=cache,
    )

    assert len(adapter.prompts) == 1
    assert "### Reference schemas" in adapter.prompts[0]
    assert '"type": "object"' in adapter.prompts[0]
    assert client.calls == [("Widget", "example.org/v1")]
    assert results[0]["grounding"]["discovered_kinds"] == ["example.org/v1/Widget"]
    assert results[0]["grounding"]["resolved_kinds"] == ["example.org/v1/Widget"]
    assert results[0]["grounding"]["unavailable_kinds"] == []
    section = "### Reference schemas" + adapter.prompts[0].split("### Reference schemas", 1)[1]
    assert results[0]["grounding"]["section_chars"] == len(section)
    assert "error" not in results[0]


def test_run_task_records_grounding_failure_without_invoking_model(tmp_path, monkeypatch):
    task_dir = _grounded_task(tmp_path)
    adapter = CapturingAdapter()

    class FailingClient:
        def get_schema(self, kind, api_version):
            raise RuntimeError("catalog unavailable")

    monkeypatch.setattr(runner.lint, "run_lint", lambda *_: {"passed": True})

    results = runner.run_task(
        task_dir,
        adapter,
        1,
        False,
        "warm",
        grounding=True,
        grounding_client=FailingClient(),
        grounding_cache=SchemaCache(tmp_path / "cache"),
    )

    assert adapter.prompts == []
    assert results[0]["error"] == (
        "grounding schema coverage failed: example.org/v1/Widget: catalog unavailable"
    )
    assert results[0]["grounding"] == {
        "discovered_kinds": ["example.org/v1/Widget"],
        "resolved_kinds": [],
        "unavailable_kinds": [
            {"pair": "example.org/v1/Widget", "error": "catalog unavailable"}
        ],
        "section_chars": 0,
    }
    assert results[0]["stages"]["lint"]["passed"] is False


def test_run_task_reports_all_unavailable_grounding_kinds_before_model(tmp_path):
    task_dir = _grounded_task(tmp_path)
    (task_dir / "seed" / "missing.yaml").write_text(
        "apiVersion: z.example.org/v1\nkind: Missing\nspec: {}\n"
    )
    adapter = CapturingAdapter()

    class PartiallyAvailableClient:
        def __init__(self):
            self.calls = []

        def get_schema(self, kind, api_version):
            self.calls.append((kind, api_version))
            if kind == "Missing":
                raise RuntimeError("catalog unavailable")
            return '{"type": "object"}'

    client = PartiallyAvailableClient()
    results = runner.run_task(
        task_dir,
        adapter,
        1,
        False,
        "warm",
        grounding=True,
        grounding_client=client,
        grounding_cache=SchemaCache(tmp_path / "cache"),
    )

    assert client.calls == [
        ("Widget", "example.org/v1"),
        ("Missing", "z.example.org/v1"),
    ]
    assert adapter.prompts == []
    assert results[0]["grounding"] == {
        "discovered_kinds": ["example.org/v1/Widget", "z.example.org/v1/Missing"],
        "resolved_kinds": ["example.org/v1/Widget"],
        "unavailable_kinds": [
            {"pair": "z.example.org/v1/Missing", "error": "catalog unavailable"}
        ],
        "section_chars": 0,
    }
    assert results[0]["error"] == (
        "grounding schema coverage failed: "
        "z.example.org/v1/Missing: catalog unavailable"
    )
    assert results[0]["stages"]["lint"]["passed"] is False


def _schema_less_task(tmp_path):
    task_dir = tmp_path / "T-plain"
    (task_dir / "seed").mkdir(parents=True)
    (task_dir / "spec.yaml").write_text("id: T-plain\nstack: knr-ops\n")
    (task_dir / "prompt.md").write_text("Write the manifest.\n")
    (task_dir / "seed" / "notes.md").write_text("No Kubernetes YAML here.\n")
    return task_dir


def test_run_task_skips_grounding_section_when_no_kinds_discovered(tmp_path, monkeypatch):
    task_dir = _schema_less_task(tmp_path)
    monkeypatch.setattr(runner.lint, "run_lint", lambda *_: {"passed": True})

    grounded_adapter = CapturingAdapter()
    client = FakeGroundingClient()
    grounded = runner.run_task(
        task_dir,
        grounded_adapter,
        1,
        False,
        "cold",
        grounding=True,
        grounding_client=client,
        grounding_cache=SchemaCache(tmp_path / "cache"),
    )

    ungrounded_adapter = CapturingAdapter()
    runner.run_task(task_dir, ungrounded_adapter, 1, False, "cold")

    assert client.calls == []
    assert grounded_adapter.prompts == ungrounded_adapter.prompts
    assert "### Reference schemas" not in grounded_adapter.prompts[0]
    assert grounded[0]["grounding"] == {
        "discovered_kinds": [],
        "resolved_kinds": [],
        "unavailable_kinds": [],
        "section_chars": 0,
    }
    assert "error" not in grounded[0]


def test_openai_compat_default_api_key_is_ascii():
    # httpx encodes header values as ascii; a non-ascii default key crashes
    # header construction before any request is attempted.
    adapter = runner.OpenAICompatAdapter("m", "http://localhost:8000")
    adapter.api_key.encode("ascii")


def test_task_dirs_exist():
    """All 30 task directories exist (42 once chant and bare land, see #3/#4, #37/#38)."""
    stacks = ["knr-ops", "crossplane", "terraform", "pulumi-python", "pulumi-typescript", "chant", "bare"]
    task_names = ["T1-comprehend", "T2-generate", "T3-modify", "T4-debug", "T5-review", "T6-semantics"]
    for stack in stacks:
        if stack == "chant" and not (TASKS_DIR / stack).exists():
            # TODO(#3/#4): tasks/chant doesn't exist yet; drop this guard once it lands.
            continue
        if stack == "bare" and not (TASKS_DIR / stack).exists():
            # TODO(#37/#38): tasks/bare doesn't exist yet; drop this guard once it lands.
            continue
        for task in task_names:
            task_dir = TASKS_DIR / stack / task
            assert task_dir.exists(), f"Task dir missing: {task_dir}"
            assert (task_dir / "prompt.md").exists(), f"Prompt missing: {task_dir}"
            assert (task_dir / "spec.yaml").exists(), f"Spec missing: {task_dir}"


def test_semantics_tasks_have_graders():
    """T6-semantics tasks ship a seed, golden key, and 7-question grader."""
    stacks = ["knr-ops", "crossplane", "terraform", "pulumi-python", "pulumi-typescript", "chant", "bare"]
    for stack in stacks:
        t6 = TASKS_DIR / stack / "T6-semantics"
        if stack == "chant" and not t6.exists():
            # TODO(#3/#4): tasks/chant/T6-semantics doesn't exist yet; drop this guard once it lands.
            continue
        if stack == "bare" and not t6.exists():
            # TODO(#37/#38): tasks/bare/T6-semantics doesn't exist yet; drop this guard once it lands.
            continue
        assert (t6 / "seed").is_dir(), f"seed/ missing: {t6}"
        assert (t6 / "golden" / "answer_key.md").exists(), f"golden answer key missing: {t6}"
        test_file = t6 / "tests" / "test_task.py"
        assert test_file.exists(), f"grader missing: {t6}"
        source = test_file.read_text()
        graders = [l for l in source.splitlines() if l.startswith("def test_q")]
        assert len(graders) == 7, f"{stack} T6 grader should have 7 question tests, has {len(graders)}"


def test_semantics_golden_answers_pass_graders():
    """Every T6 golden answer key passes its own grader (and empty fails)."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "validate_t6.py")],
        capture_output=True, text=True, cwd=str(ROOT), timeout=300,
    )
    assert result.returncode == 0, f"T6 self-validation failed:\n{result.stdout[-2000:]}"


def test_golden_implementations_exist():
    """Golden implementations have core files."""
    golden_dirs = ROOT / "golden-base"
    assert (golden_dirs / "knr-ops" / "clusters" / "eksa" / "cluster.yaml").exists()
    assert (golden_dirs / "crossplane" / "xrds" / "composite-web-service.yaml").exists()
    assert (golden_dirs / "terraform" / "infrastructure.tf").exists()
    assert (golden_dirs / "pulumi-python" / "__main__.py").exists()
    assert (golden_dirs / "pulumi-typescript" / "index.ts").exists()
    # TODO(#3): golden-base/chant doesn't exist yet; assert its entry point
    # unconditionally once it lands instead of this existence-gated check.
    chant_dir = golden_dirs / "chant"
    if chant_dir.exists():
        assert any(chant_dir.rglob("*.ts")), f"no .ts entry point found under {chant_dir}"
    # TODO(#37): golden-base/bare doesn't exist yet; assert its entry point
    # unconditionally once it lands instead of this existence-gated check.
    bare_dir = golden_dirs / "bare"
    if bare_dir.exists():
        assert any(bare_dir.rglob("*.yaml")) or any(bare_dir.rglob("*.yml")), \
            f"no YAML manifests found under {bare_dir}"


# The chant golden lands on its own branch, so these assertions are guarded on
# the directory existing: whichever of bench/chant-golden and bench/chant-wiring
# merges first, the suite stays green. Once both are on main the guard is a
# no-op — golden-base/chant is always there.
CHANT_GOLDEN = ROOT / "golden-base" / "chant"

chant_golden_required = pytest.mark.skipif(
    not CHANT_GOLDEN.is_dir(),
    reason="golden-base/chant not present yet (bench/chant-golden not merged)",
)


@chant_golden_required
def test_chant_golden_scaffold_exists():
    """The chant golden ships a buildable chant project."""
    for relative in (
        "chant.config.ts",
        "package.json",
        "tsconfig.json",
        "README.md",
    ):
        assert (CHANT_GOLDEN / relative).exists(), f"missing: golden-base/chant/{relative}"


@chant_golden_required
def test_chant_golden_entrypoints_exist():
    """dev and prod are separate build roots, not one parameterized entrypoint.

    Each environment is further split into infra/clusters/delivery build
    roots (#19) so the FluxAppFor paths and the actual `chant build` output
    agree — see golden-base/chant/README.md, "Build output layout".
    """
    for env in ("dev", "prod"):
        for sub_root in ("infra", "clusters", "delivery"):
            assert (CHANT_GOLDEN / "src" / "envs" / env / sub_root / "main.ts").exists(), (
                f"missing: golden-base/chant/src/envs/{env}/{sub_root}/main.ts"
            )


@chant_golden_required
def test_chant_golden_build_output_layout_declared():
    """package.json builds each Flux-reconciled path into its own file.

    The delivery Kustomizations name "./dist/<env>/infra" and
    "./dist/<env>/clusters" as reconciliation paths; the build scripts must
    emit into those exact locations for FLUX002/FLUX003 and reality to agree.
    """
    package_json = json.loads((CHANT_GOLDEN / "package.json").read_text())
    scripts = package_json.get("scripts", {})
    for env in ("dev", "prod"):
        for sub_root, out in (
            ("infra", f"dist/{env}/infra/manifests.yaml"),
            ("clusters", f"dist/{env}/clusters/manifests.yaml"),
            ("delivery", f"dist/{env}/delivery.yaml"),
        ):
            key = f"build:{env}:{sub_root}"
            assert key in scripts, f"package.json scripts missing {key}"
            assert out in scripts[key], f"{key} does not build to {out}"

    for env in ("dev", "prod"):
        source = (CHANT_GOLDEN / "src" / "envs" / env / "delivery" / "main.ts").read_text()
        assert f'"./dist/{env}/infra"' in source, (
            f"src/envs/{env}/delivery/main.ts must declare the infra FluxAppFor path"
        )
        assert f'"./dist/{env}/clusters"' in source, (
            f"src/envs/{env}/delivery/main.ts must declare the clusters FluxAppFor path"
        )


@chant_golden_required
def test_chant_golden_composites_exist():
    """The scenario-local Composite() factories the golden is written over."""
    composites = CHANT_GOLDEN / "src" / "composites"
    for module, factory in (
        ("region-cluster.ts", "RegionCluster"),
        ("secure-bucket.ts", "SecureBucket"),
        ("postgres-instance.ts", "PostgresInstance"),
        ("reader-iam.ts", "ReaderIam"),
    ):
        path = composites / module
        assert path.exists(), f"missing composite module: {path}"
        source = path.read_text()
        assert f'"{factory}"' in source, f"{module} does not name the {factory} composite"


@chant_golden_required
def test_chant_golden_vendors_the_lexicon():
    """The CAPI/CAPA/ACK lexicon is vendored until upstream publishes it."""
    vendor = CHANT_GOLDEN / "vendor"
    assert vendor.is_dir(), "golden-base/chant/vendor missing"
    assert (vendor / "README.md").exists(), "vendor/README.md must explain the file: deps"
    tarballs = sorted(vendor.glob("*.tgz"))
    assert tarballs, "no vendored chant tarball in golden-base/chant/vendor"
    package_json = json.loads((CHANT_GOLDEN / "package.json").read_text())
    deps = package_json.get("dependencies", {})
    assert deps.get("@intentius/chant-lexicon-k8s", "").startswith("file:vendor/"), (
        "the k8s lexicon must resolve from vendor/ until the CAPI/CAPA/ACK kinds publish"
    )


def test_stage_runners_import():
    """Stage runners import cleanly."""
    from bench.stages import lint, static, semantic, e2e
    assert hasattr(lint, "run_lint")
    assert hasattr(static, "run_static")
    assert hasattr(semantic, "run_semantic")
    assert hasattr(e2e, "run_e2e")


def test_score_module_imports():
    """Score module imports cleanly."""
    from bench import score
    assert hasattr(score, "AXES")
    assert hasattr(score, "compute_score")
    assert hasattr(score, "aggregate_scores")


def test_report_module_imports():
    """Report module imports cleanly."""
    from bench import report
    assert hasattr(report, "main")


def test_model_adapters_import():
    """Model adapters import cleanly."""
    from bench.runner import AnthropicAdapter, OpenAICompatAdapter, ClaudeCliAdapter, ModelAdapter
    assert issubclass(AnthropicAdapter, ModelAdapter)
    assert issubclass(OpenAICompatAdapter, ModelAdapter)
    assert issubclass(ClaudeCliAdapter, ModelAdapter)
