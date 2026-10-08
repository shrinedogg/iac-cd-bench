"""Semantic grader for crossplane T3-modify: add a second region to the
composition without changing existing claims.

Written to close #111. See tasks/terraform/T3-modify/tests/test_task.py for
why the four ungraded arms mattered.

The prompt is unusually explicit about the negative half -- "without changing
existing claims" -- so that is asserted as its own criterion rather than left
implicit. It is also the reason crossplane's static gate had to stop demanding
a claim (#109): the task tells the model not to write one, and the gate
abstained when it complied.

Documents are parsed as YAML rather than pattern-matched, and located by
content rather than filename, which is rule 8 and the whole of #89: the golden
names its composite `claims/dev.yaml`, the extractor names an unlabelled block
`generated_0.yaml`, and any filename-based rule misses one or the other.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WEST = "us-west-2"


def _docs() -> list[dict]:
    out: list[dict] = []
    for p in sorted(Path(".").rglob("*.y*ml")):
        if "node_modules" in p.parts:
            continue
        try:
            out.extend(d for d in yaml.safe_load_all(p.read_text())
                       if isinstance(d, dict) and d.get("kind"))
        except (OSError, yaml.YAMLError):
            continue
    return out


def _text() -> str:
    chunks = []
    for p in sorted(Path(".").rglob("*.y*ml")):
        if "node_modules" in p.parts:
            continue
        try:
            chunks.append(p.read_text())
        except OSError:
            continue
    if not chunks:
        out = Path("model_output.md")
        if out.is_file():
            try:
                chunks.append(out.read_text())
            except OSError:
                pass
    return "\n".join(chunks)


@pytest.fixture(scope="module")
def docs() -> list[dict]:
    return _docs()


@pytest.fixture(scope="module")
def text() -> str:
    return _text()


def test_answer_is_present(text):
    assert text.strip(), "no YAML and no model_output.md — nothing to grade"


def test_a_composition_is_present(docs, text):
    """The artifact the task edits."""
    assert any(d.get("kind") == "Composition" for d in docs) or "kind: Composition" in text, (
        "expected a Composition — the task asks for the second region to be "
        "added to the composition"
    )


def test_second_region_is_declared(text):
    assert WEST in text, (
        f"expected {WEST} somewhere in the emitted resources — the task asks "
        "for a second AWS region"
    )


def test_second_region_reaches_the_composition(docs, text):
    """The region must land in the Composition, not only in a stray
    ProviderConfig: adding a config nothing references changes nothing."""
    comps = [d for d in docs if d.get("kind") == "Composition"]
    if not comps:
        assert WEST in text, "no parseable Composition, and no us-west-2 in the output"
        return
    assert any(WEST in yaml.safe_dump(c) for c in comps), (
        f"{WEST} appears in the workspace but not inside any Composition. A "
        "ProviderConfig nothing references does not add a region to the "
        "composition"
    )


def test_a_second_provider_config_is_referenced(docs, text):
    """Two regions need two provider configs, and the composition has to name
    the new one -- `providerConfigRef` (or `providerConfigName`) is how a
    composed resource picks its region."""
    dumped = "\n".join(yaml.safe_dump(d) for d in docs) or text
    assert "providerConfigRef" in dumped or "providerConfigName" in dumped, (
        "expected a providerConfigRef/providerConfigName selecting the "
        "second region's provider config"
    )


def test_existing_claims_are_not_modified(docs):
    """The prompt's explicit constraint: 'without changing existing claims'.

    A composite/claim that now names us-west-2 is the failure this guards --
    the region belongs in the composition so existing claims keep working
    untouched. Asserted here rather than assumed, because it is the half of
    the task a model is most likely to get wrong by doing too much.
    """
    machinery = {"Composition", "CompositeResourceDefinition", "Function",
                 "Provider", "ProviderConfig", "CompositionRevision",
                 "DeploymentRuntimeConfig", "Configuration"}
    claims = [d for d in docs if d.get("kind") not in machinery]
    offenders = [
        (d.get("kind"), (d.get("metadata") or {}).get("name"))
        for d in claims if WEST in yaml.safe_dump(d)
    ]
    assert not offenders, (
        f"these claims/composites were changed to name {WEST}: {offenders!r}. "
        "The task requires the second region be added without changing "
        "existing claims"
    )
