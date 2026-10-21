"""Semantic grader for krops T4-debug: SOPS creation rule no longer matches
most of the repo's encrypted secrets.

The seeded defect is entirely in `.sops.yaml`: its single creation rule was
narrowed to `path_regex: mgmt/aws/addons/flux-apps/regions/eu-north-1/.*\.sops\.yaml$`
(a leftover test scope), so SOPS has no rule for the eu-west-1 regional pull
secret or the konflate token, and every Kustomization decrypting a
`*.sops.yaml` outside that one region fails with "no matching creation rule
found". The fix is the rule's path coverage — the age recipient (a public
key, pinned below) and the `^(data|stringData)$` field scope must come back
unchanged.

`.sops.yaml` is a dotfile: the runner never passes dotfiles to the model as
workspace files, and extract_code_blocks' path matcher skips them, so the
model's corrected file normally arrives only as a fenced block in
model_output.md. This grader therefore reads the corrected content from the
last matching fenced block — the same fallback knr-ops's T4 uses — and
verifies the fix against the workspace's seeded SOPS-encrypted manifests by
TESTING the rule's regex against the paths that are actually failing in the
symptoms, not by string-comparing one canonical regex (the model is free to
write `mgmt/(aws|azure|gcp)/.*\.sops\.yaml$` or an equivalent union).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml


_DEFECTIVE_RULE = r"mgmt/aws/addons/flux-apps/regions/eu-north-1/.*\.sops\.yaml$"
_AGE_KEY = "age1amqtgevmdxutg07rkr3wggua43620zgncgfq53vkww0283wrgspqv9q0jw"

# The exact paths the symptoms show failing (plus the one already working,
# so a fix that keeps only the old scope cannot pass).
REQUIRED_PATHS = [
    "mgmt/aws/addons/flux-apps/regions/eu-north-1/flux-github-pat.sops.yaml",
    "mgmt/aws/addons/flux-apps/regions/eu-west-1/flux-github-pat.sops.yaml",
    "mgmt/aws/infrastructure/konflate/konflate-token.sops.yaml",
]


def _read_sops_config() -> str:
    """The model's edited `.sops.yaml`.

    A real `.sops.yaml` on disk counts only when it no longer carries the
    seeded defective rule (i.e. the model's edit actually landed there —
    current extraction cannot write dotfiles, so the seeded copy is the
    untouched one unless a future pipeline change lets the edit through).
    Otherwise read the last matching fenced block in model_output.md.
    """
    for p in sorted(Path(".").rglob(".sops.yaml")):
        try:
            text = p.read_text()
        except Exception:
            continue
        if _DEFECTIVE_RULE not in text:
            return text

    out_path = Path("model_output.md")
    if not out_path.exists():
        return ""
    text = out_path.read_text()
    blocks = re.findall(r"```[\w.-]*\n(.*?)```", text, re.DOTALL)
    candidates = [b for b in blocks if "creation_rules" in b and "age" in b]
    return candidates[-1].strip() if candidates else ""


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


def _rule_patterns(sops_config: dict) -> list[str]:
    return [
        str(rule.get("path_regex", ""))
        for rule in sops_config["creation_rules"]
        if isinstance(rule, dict) and rule.get("path_regex")
    ]


def test_rule_covers_every_sops_file_in_the_repo(sops_config):
    """The symptom (decrypt failures) is only fixed once every *.sops.yaml the
    management cluster decrypts matches at least one creation rule — the eu-
    west-1 pull secret AND the konflate token, not just the one region that
    still worked."""
    patterns = _rule_patterns(sops_config)
    assert patterns, "expected at least one creation_rules entry with a path_regex"
    for path in REQUIRED_PATHS:
        assert any(re.search(p, path) for p in patterns), (
            f"no creation rule matches {path!r} — Flux would still fail to "
            f"decrypt it. rules: {patterns!r}"
        )


def test_age_recipient_unchanged(sops_config):
    """The fix is path coverage, not a key rotation: every rule must still
    point at the repo's committed public age key."""
    for rule in sops_config["creation_rules"]:
        assert isinstance(rule, dict), f"malformed creation_rules entry: {rule!r}"
        age = str(rule.get("age", ""))
        assert age == _AGE_KEY, (
            f"creation_rules entry for {rule.get('path_regex')!r} has "
            f"age={age!r}; the repo's recipient is {_AGE_KEY!r} — rotating it "
            "orphans every already-encrypted secret"
        )


def test_encryption_scope_not_widened(sops_config):
    """The fix must not widen WHICH fields are encrypted: each rule's
    encrypted_regex must still cover data/stringData and nothing broader
    (a rule that also encrypts metadata or name would rewrite manifests
    well beyond the secret material)."""
    for rule in sops_config["creation_rules"]:
        er = rule.get("encrypted_regex")
        if er is None:
            continue  # sops default is to encrypt the whole doc value — but
        er = str(er)
        for field in ("data", "stringData"):
            assert re.match(er, field), (
                f"encrypted_regex {er!r} no longer matches {field!r} — the "
                "rule would stop encrypting the secret material"
            )
        for field in ("metadata", "name", "apiVersion"):
            assert not re.match(er, field), (
                f"encrypted_regex {er!r} would encrypt {field!r} — the repo "
                "keeps keys/metadata readable (only data/stringData)"
            )
