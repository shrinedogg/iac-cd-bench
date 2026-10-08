"""
Tests for `bench.report --compare` (the flag the Makefile's compare target
has always passed).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from bench.report import (archetype_of, collect_result_sets, generate_comparison,
                          integrity_section)

ROOT = Path(__file__).resolve().parent.parent


# A completion long enough to clear the validity gate's length floor and
# carrying a real code block, so these fixtures are runs the gates accept.
# Without it every fixture would be rejected as an empty completion and the
# comparison would legitimately have nothing to render.
FIXTURE_CONTENT = (
    "Here is the manifest set for the logs bucket, with the storage class "
    "pinned and the retention policy attached as the brief asks. The overlay "
    "patches the base rather than replacing it, so the shared labels survive.\n\n"
    "`infra/s3/logs/bucket.yaml`\n\n"
    "```yaml\napiVersion: s3.aws.upbound.io/v1beta1\nkind: Bucket\n"
    "metadata:\n  name: logs\n```\n"
)


def write_run(model_dir: Path, stack: str, task: str, run: int, *,
              passed: bool = True, judge: dict | None = None,
              content: str = FIXTURE_CONTENT) -> None:
    out = model_dir / stack / "warm"
    out.mkdir(parents=True, exist_ok=True)
    result = {
        "model": model_dir.name,
        "task": task,
        "stack": stack,
        "run": run,
        "condition": "warm",
        "content": content,
        "stages": {
            "lint": {"passed": passed},
            "static": {"passed": passed},
            "semantic": {"passed": passed, "passed_count": 1 if passed else 0,
                         "total_count": 1, "safety_pass": True},
        },
    }
    if judge:
        result["judge"] = judge
    (out / f"{task}_run{run}.json").write_text(json.dumps(result))


def test_archetype_of():
    assert archetype_of("T1-comprehend") == "comprehend"
    assert archetype_of("T6-semantics") == "semantics"
    assert archetype_of("weird-task") is None


def test_collect_result_sets_skips_non_dirs_and_empties(tmp_path):
    good = tmp_path / "model-a"
    write_run(good, "knr-ops", "T1-comprehend", 0)
    (tmp_path / "chart.png").write_bytes(b"\x89PNG")
    (tmp_path / "empty-dir").mkdir()

    sets = collect_result_sets([str(p) for p in sorted(tmp_path.iterdir())])
    assert [label for label, _ in sets] == ["model-a"]
    assert len(sets[0][1]) == 1


def test_generate_comparison_renders_columns_and_rows(tmp_path):
    a = tmp_path / "model-a"
    b = tmp_path / "model-b"
    write_run(a, "knr-ops", "T1-comprehend", 0, passed=True,
              judge={"idiom": 1.0, "judge_model": "claude-haiku-4-5",
                     "prompt_sha256": "abc123", "criteria": []})
    write_run(a, "terraform", "T2-generate", 0, passed=True)
    write_run(b, "knr-ops", "T1-comprehend", 0, passed=False)

    sets = collect_result_sets([str(a), str(b)])
    report = generate_comparison(sets)

    assert "| Stack | model-a | model-b |" in report
    assert "knr-ops / Comprehend" in report
    # crossplane has no runs in either set, so it is dropped from the detail table
    assert "crossplane / Comprehend" not in report
    # judge pinning shows up in the coverage table
    assert "claude-haiku-4-5" in report
    assert "abc123" in report
    assert "**Overall**" in report
    # a fully passing judged run beats a fully failing one
    lines = [l for l in report.splitlines() if l.startswith("| knr-ops |")]
    assert lines and lines[0].split("|")[2].strip() > lines[0].split("|")[3].strip()


def test_compare_cli_writes_report(tmp_path):
    a = tmp_path / "model-a"
    write_run(a, "knr-ops", "T1-comprehend", 0)
    out = tmp_path / "comparison.md"

    proc = subprocess.run(
        [sys.executable, "-m", "bench.report", "--compare", str(a), "--output", str(out)],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    assert out.exists()
    assert "Comparative Benchmark Report" in out.read_text()


def test_report_cli_requires_model_without_compare():
    proc = subprocess.run(
        [sys.executable, "-m", "bench.report"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert proc.returncode != 0
    assert "--model is required" in proc.stderr


def test_makefile_compare_target_flag_exists():
    """The Makefile's compare target and report.py agree on --compare."""
    makefile = (ROOT / "Makefile").read_text()
    assert "--compare" in makefile
    proc = subprocess.run(
        [sys.executable, "-m", "bench.report", "--help"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert "--compare" in proc.stdout


# ── integrity section: partial runs (#80) ────────────────────────────────

def _partial(reasons: list[str]) -> dict:
    """A scored run the gates flagged partial for the given reasons."""
    return {"_integrity": {"verdict": "partial", "partial_reasons": reasons,
                           "invalid_reasons": []}}


def test_no_stage_ran_is_not_reported_as_missing_provenance():
    """A spec-disabled stage set is not a provenance gap. Claiming it is (#80)
    told readers that 36 runs with intact provenance could not be compared."""
    section = "\n".join(integrity_section(
        "set-a",
        [_partial(["no_stage_ran: every stage was disabled by spec"])] * 3,
        [],
    ))

    assert "| `no_stage_ran` | 3 |" in section
    assert "incomplete provenance" not in section
    assert "cannot be compared" not in section
    assert "provenance is complete" in section


def test_real_provenance_gaps_still_say_so():
    section = "\n".join(integrity_section(
        "set-a",
        [_partial(["no_harness_commit: the harness commit was not recorded"]),
         _partial(["no_toolchain: binary versions were not recorded"])],
        [],
    ))

    assert "cannot be compared against another result set" in section
    assert "`no_harness_commit`" in section and "`no_toolchain`" in section
    assert "2 run(s) carry incomplete provenance" in section


def test_mixed_partial_reasons_are_counted_separately():
    """The comparability warning must name only the runs it applies to, not
    every partial run in the set."""
    section = "\n".join(integrity_section(
        "set-a",
        [_partial(["no_stage_ran: x"])] * 5 + [_partial(["no_prompt_hash: y"])],
        [],
    ))

    assert "| `no_stage_ran` | 5 |" in section
    assert "| `no_prompt_hash` | 1 |" in section
    # one run has the gap, not six
    assert "1 run(s) carry incomplete provenance" in section
