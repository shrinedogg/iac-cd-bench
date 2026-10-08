"""Crossplane T2-generate: XRD + Composition + claim + ProviderConfig.

Located by apiVersion/kind rather than by path (issue #72). The prompt asks
for "a Crossplane XRD and Composition"; it never names
`xrds/composite-web-service.yaml` or `compositions/composition.yaml`, which
is what this grader used to require. The four checks are unchanged: the
composite API is defined, a Composition implements it, something
instantiates it, and at least one ProviderConfig exists.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import _grader_lib as gl  # noqa: E402


@pytest.fixture(scope="module")
def docs():
    return gl.all_docs()


@pytest.fixture(scope="module")
def xrds(docs):
    return [d for _p, d in gl.find_docs(docs, kind="CompositeResourceDefinition")]


def test_xrd_exists(xrds):
    """An XRD defines the composite API."""
    assert xrds, (
        "expected a CompositeResourceDefinition (apiextensions.crossplane.io). "
        f"Workspace contains: {gl.inventory()}"
    )


def test_composition_exists(docs):
    """A Composition implements the composite API."""
    gl.require_docs("a Composition (apiextensions.crossplane.io)",
                    docs=docs, kind="Composition")


def test_claim_exists(docs, xrds):
    """Something instantiates the composite: a claim or an XR whose kind and
    API group come from the XRD the answer itself defined.

    Deriving the expected kind from the XRD rather than hardcoding
    `claims/dev.yaml` is what makes this check work on any naming the model
    chose -- and it stays strict, because an instance of some unrelated kind
    still fails."""
    assert xrds, "cannot check for a claim without an XRD defining the composite API"
    wanted_kinds = set()
    wanted_groups = set()
    for xrd in xrds:
        spec = xrd.get("spec") or {}
        wanted_groups.add(str(spec.get("group", "")))
        for names_key in ("names", "claimNames"):
            names = spec.get(names_key)
            if isinstance(names, dict) and names.get("kind"):
                wanted_kinds.add(str(names["kind"]))
    wanted_kinds.discard("")
    wanted_groups.discard("")

    instances = [
        d for _p, d in docs
        if str(d.get("kind", "")) in wanted_kinds
        or any(g and str(d.get("apiVersion", "")).startswith(g + "/") for g in wanted_groups)
    ]
    assert instances, (
        f"expected a claim or composite instance of {sorted(wanted_kinds)!r} "
        f"(group {sorted(wanted_groups)!r}). "
        f"Kinds found: {sorted({d.get('kind') for _p, d in docs})!r}"
    )


def test_provider_config_exists(docs):
    """At least one ProviderConfig supplies credentials to the composed
    managed resources."""
    configs = gl.find_docs(docs, kind="ProviderConfig")
    assert configs, (
        "At least one ProviderConfig should exist. "
        f"Kinds found: {sorted({d.get('kind') for _p, d in docs})!r}"
    )
