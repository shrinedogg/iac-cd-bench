"""
Run provenance: what code, what prompt, what toolchain produced a result.

Ported from chant-bench / aws-bench's comparability discipline: a benchmark
number is only meaningful next to another number when both were produced by
the same harness, against the same task text, with the same tools installed.
Without that recorded per run, a re-run after any code change is silently a
different experiment and nobody notices (issue #59, failure mode 6).

Every field here is cheap to collect and is stamped onto every result JSON by
bench.runner. bench.validate reads it back to classify runs, and
bench.report's --compare path refuses to average result sets whose provenance
disagrees.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent

# Provenance schema version. Bump when a field's meaning changes so the
# validator can tell "old run, no such field" from "new run, field absent".
PROVENANCE_VERSION = 1


def sha256_text(text: str) -> str:
    """Short content hash used for prompt/spec fingerprints."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def sha256_file(path: Path) -> str | None:
    try:
        return sha256_text(path.read_text())
    except (OSError, UnicodeDecodeError):
        return None


@lru_cache(maxsize=1)
def harness_commit() -> dict[str, Any]:
    """The harness's own git state.

    `dirty` matters as much as the sha: a run produced from a working tree
    with uncommitted harness edits is not reproducible from the sha alone,
    and chant-bench treats that as a comparability break rather than a
    footnote.
    """
    info: dict[str, Any] = {"commit": None, "dirty": None, "branch": None}
    try:
        info["commit"] = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT),
            capture_output=True, text=True, timeout=15, check=True,
        ).stdout.strip()
        info["branch"] = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(ROOT),
            capture_output=True, text=True, timeout=15, check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=str(ROOT),
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout
        info["dirty"] = bool(status.strip())
    except (subprocess.SubprocessError, OSError, FileNotFoundError) as e:
        log.warning("Could not read harness git state: %s", e)
    return info


def task_fingerprint(task_dir: Path) -> dict[str, Any]:
    """Hashes of the inputs that define what the model was asked to do.

    A changed prompt.md or spec.yaml makes a re-run a different experiment
    even at the same harness commit, so both are hashed separately.
    """
    fp: dict[str, Any] = {
        "prompt_sha256": sha256_file(task_dir / "prompt.md"),
        "spec_sha256": sha256_file(task_dir / "spec.yaml"),
    }
    scenario = ROOT / "scenario" / "SPEC.md"
    if scenario.exists():
        fp["scenario_sha256"] = sha256_file(scenario)
    return fp


def build_provenance(
    *,
    provider: str,
    model: str,
    reasoning_effort: str | None,
    toolchain: dict[str, Any] | None = None,
    partial: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the per-run-set provenance block stamped onto every run.

    `partial` is set when the run set was started with tooling knowingly
    missing (bench.preflight's --allow-missing-tools escape hatch); the
    validator downgrades every run in such a set to `partial` so the numbers
    can never be quoted as a clean result.
    """
    prov: dict[str, Any] = {
        "provenance_version": PROVENANCE_VERSION,
        "harness": harness_commit(),
        "provider": provider,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "toolchain": toolchain or {},
        "partial": bool(partial),
    }
    if extra:
        prov.update(extra)
    return prov


def toolchain_fingerprint(toolchain: dict[str, Any]) -> str | None:
    """Stable hash of {binary: version} so two sets can be compared cheaply.

    Only name+version go into the hash — the absolute path a binary was found
    at differs between worktrees and machines without changing behaviour, and
    hashing it would flag every run set as incomparable (failure mode 5 is
    about *versions* drifting, not paths).
    """
    if not toolchain:
        return None
    pairs = {name: (info or {}).get("version") for name, info in sorted(toolchain.items())}
    return sha256_text(json.dumps(pairs, sort_keys=True))
