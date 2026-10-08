"""Semantic grader for chant T3-modify: add a spot node pool to prod only.

Greps the model's emitted/edited TypeScript for the required composite
call-site edit -- a second RegionNodePool({...}) call in
src/envs/prod/clusters/main.ts -- and asserts prod's original RegionCluster
call is untouched and dev's clusters build root was not touched at all.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


def _envs_ts_files() -> list[Path]:
    return sorted(p for p in Path(".").rglob("*.ts") if "envs" in p.parts)


def _read(path_glob: str) -> str:
    hits = [p for p in _envs_ts_files() if p.match(path_glob)]
    for p in hits:
        try:
            return p.read_text()
        except Exception:
            continue
    return ""


def _blocks(text: str, call: str) -> list[str]:
    return _props_of_call(text, call)
def _object_at(text: str, brace: int) -> str | None:
    """The balanced {...} starting at index `brace`."""
    depth = 0
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[brace:i + 1]
    return None


def _props_of_call(text: str, call: str) -> list[str]:
    """Every props object passed to `call(...)`, inline or by reference.

    `SecureBucket({...})` and

        const props = {...};
        SecureBucket(props);

    are the same declaration. Requiring the first spelling failed correct
    answers that used the second (#107) -- the same defect as #102's
    file-referenced kustomize patches: one syntactic form of a right answer
    recognised, the rest invisible.
    """
    out: list[str] = []
    pattern = rf"\b{re.escape(call)}\s*\(\s*(?:(\{{)|([A-Za-z_$][\w$]*))"
    for m in re.finditer(pattern, text):
        if m.group(1):
            block = _object_at(text, m.end() - 1)
        else:
            ident = m.group(2)
            decl = re.search(
                rf"(?:const|let|var)\s+{re.escape(ident)}\s*(?::[^=]+?)?=\s*\{{",
                text)
            block = _object_at(text, decl.end() - 1) if decl else None
        if block:
            out.append(block)
    return out



@pytest.fixture(scope="module")
def prod_clusters_text() -> str:
    # Prefer the canonical path; fall back to any prod/clusters file the
    # model may have written the edit to.
    hits = [
        p for p in _envs_ts_files()
        if "prod" in p.parts and "clusters" in p.parts
    ]
    for p in hits:
        try:
            return p.read_text()
        except Exception:
            continue
    return ""


@pytest.fixture(scope="module")
def dev_clusters_text() -> str:
    hits = [
        p for p in _envs_ts_files()
        if "dev" in p.parts and "clusters" in p.parts
    ]
    for p in hits:
        try:
            return p.read_text()
        except Exception:
            continue
    return ""


def test_second_pool_added(prod_clusters_text):
    blocks = _blocks(prod_clusters_text, "RegionNodePool")
    hits = [b for b in blocks if "myapp-prod-nodes-spot" in b]
    assert hits, "expected a RegionNodePool({...}) call naming myapp-prod-nodes-spot in prod/clusters"


def test_pool_joins_correct_cluster(prod_clusters_text):
    blocks = _blocks(prod_clusters_text, "RegionNodePool")
    hits = [b for b in blocks if "myapp-prod-nodes-spot" in b]
    assert hits, "no myapp-prod-nodes-spot RegionNodePool call found"
    assert any('"myapp-prod"' in b for b in hits), (
        "the new pool must set clusterName to myapp-prod (the existing cluster's name)"
    )


def test_pool_props(prod_clusters_text):
    blocks = _blocks(prod_clusters_text, "RegionNodePool")
    hits = [b for b in blocks if "myapp-prod-nodes-spot" in b]
    assert hits, "no myapp-prod-nodes-spot RegionNodePool call found"
    block = hits[0]
    assert re.search(r"""capacityType:\s*["']spot["']""", block), (
        f"expected capacityType: \"spot\", got block: {block[:300]!r}"
    )
    assert re.search(r"replicas:\s*2\b", block), f"expected replicas: 2, got: {block[:300]!r}"
    assert re.search(r"""instanceType:\s*["']t3\.large["']""", block), (
        f"expected instanceType: \"t3.large\", got: {block[:300]!r}"
    )


def test_original_cluster_call_preserved(prod_clusters_text):
    blocks = _blocks(prod_clusters_text, "RegionCluster")
    hits = [b for b in blocks if "myapp-prod" in b and "myapp-prod-nodes-spot" not in b]
    assert hits, "the original RegionCluster({...}) call for myapp-prod must still be present"
    block = hits[0]
    assert re.search(r"nodeCount:\s*4\b", block), (
        "the existing RegionCluster call's nodeCount must be unchanged (still 4)"
    )
    assert re.search(r"""instanceType:\s*["']t3\.large["']""", block), (
        "the existing RegionCluster call's instanceType must be unchanged"
    )


def test_dev_untouched(dev_clusters_text):
    assert "spot" not in dev_clusters_text.lower(), (
        "dev/clusters/main.ts must not be touched by this prod-only change"
    )
    dev_pool_blocks = _blocks(dev_clusters_text, "RegionNodePool")
    assert not dev_pool_blocks, (
        "dev must still declare exactly zero direct RegionNodePool call sites "
        "(its only pool comes from RegionCluster's internal one)"
    )
