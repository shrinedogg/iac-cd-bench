"""Semantic grader for knr-ops T4-debug: SOPS secret referencing wrong age key.

The seeded defect is entirely in `.sops.yaml`: its two `creation_rules`
entries (`*.yaml`, `*.yml`) both reference a placeholder age public key
instead of the real one committed alongside it in `age-key.txt`. This
grader reads the correct key directly from the workspace's own
`age-key.txt` (present in the seed, not part of the fix) rather than
hardcoding it, and checks the fix landed on every creation rule -- not just
one -- without the seeded companion files being rewritten around it.

`.sops.yaml` is a dotfile, so bench.runner.extract_code_blocks's own path
matcher skips it (`path_str.startswith(".")`) and its generic fallback
writer requires `apiVersion` in the block (a SOPS config has none) -- it can
never land as a real extracted file under the current extraction pipeline.
This grader therefore reads the corrected content straight out of
model_output.md's fenced blocks, the same fallback knr-ops's T6-semantics
grader uses for answers.json.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml


_WRONG_KEY = "age1wr0ngk3y1234567890abcdefghijklmnop"


def _read_sops_config() -> str:
    """The model's edited `.sops.yaml`.

    The task seeds `.sops.yaml` into the workspace, and extraction never
    overwrites it (dotfiles are skipped by path-matching, and the generic
    fallback writer requires `apiVersion`, which a SOPS config has none of)
    -- so a real `.sops.yaml` on disk is the untouched seed copy unless it no
    longer carries the seeded wrong key (i.e. a future extraction fix let a
    real edit through). Otherwise this reads the model's actual answer from
    the last matching fenced block in model_output.md.
    """
    for p in sorted(Path(".").rglob(".sops.yaml")):
        try:
            text = p.read_text()
        except Exception:
            continue
        if _WRONG_KEY not in text:
            return text

    out_path = Path("model_output.md")
    if not out_path.exists():
        return ""
    text = out_path.read_text()
    blocks = re.findall(r"```[\w.-]*\n(.*?)```", text, re.DOTALL)
    candidates = [b for b in blocks if "creation_rules" in b and "age" in b]
    return candidates[-1].strip() if candidates else ""


def _correct_age_key() -> str:
    """The seeded ground-truth key. Read tolerantly: a missing or unreadable
    age-key.txt is an assertion failure with a message, never a
    FileNotFoundError that errors out the rest of the module (issue #72)."""
    for key_path in sorted(Path(".").rglob("age-key.txt")):
        try:
            text = key_path.read_text().strip()
        except Exception:
            continue
        if text:
            return text
    pytest.fail("age-key.txt (seeded, not part of the fix) is missing from the workspace")


@pytest.fixture(scope="module")
def sops_config() -> dict:
    text = _read_sops_config()
    assert text, "expected a corrected .sops.yaml in the model's output"
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        pytest.fail(f".sops.yaml did not parse as YAML: {e}\n{text[:400]!r}")
    assert isinstance(doc, dict) and isinstance(doc.get("creation_rules"), list), (
        f"expected a creation_rules list in .sops.yaml, got: {text[:400]!r}"
    )
    return doc


def test_age_key_fixed_on_every_rule(sops_config):
    """The wrong-key symptom (Flux failing to decrypt) is only actually
    fixed once every creation rule that touches YAML secrets points at the
    real key -- fixing only the first rule leaves *.yml secrets broken."""
    correct_key = _correct_age_key()
    rules = sops_config["creation_rules"]
    assert rules, "expected at least one creation_rules entry"

    for rule in rules:
        assert isinstance(rule, dict), f"malformed creation_rules entry: {rule!r}"
        age = str(rule.get("age", ""))
        assert age != _WRONG_KEY, (
            f"creation_rules entry for {rule.get('path')!r} still references "
            f"the wrong age key -- Flux would still fail to decrypt"
        )
        assert age == correct_key, (
            f"creation_rules entry for {rule.get('path')!r} has age={age!r}, "
            f"expected the real key from age-key.txt ({correct_key!r})"
        )


def test_fix_is_scoped(sops_config):
    """The fix is one value (repeated per rule), not a rewrite of the SOPS
    config's scope, and not a workaround that edits the ground-truth key
    file or the unrelated seeded secret manifest instead of .sops.yaml."""
    paths = sorted(str(rule.get("path", "")) for rule in sops_config["creation_rules"])
    assert paths == ["*.yaml", "*.yml"], (
        f"creation_rules should still cover exactly *.yaml and *.yml, got: {paths!r}"
    )

    # age-key.txt is the seeded ground truth, not the thing being edited --
    # a "fix" that instead rewrites it to match the wrong key would make
    # both files agree on the wrong value and still not decrypt anything
    # real.
    assert _correct_age_key() == (
        "age1mmngvhy2xuyjd49hdmzg0n6fum5l83u38w5npdd6jpwsuey7fy9svrhspg"
    ), "age-key.txt (the ground-truth key) must not be modified -- fix .sops.yaml instead"

    secret_path = Path("infra/secret.yaml")
    if secret_path.exists():
        assert "db-password-dev" in secret_path.read_text(), (
            "infra/secret.yaml is not part of this PR and should be left alone"
        )
