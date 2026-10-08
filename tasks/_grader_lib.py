"""Shared artifact-location helpers for task graders (issue #72).

Graders run with cwd pinned to the model's materialized workspace (see
bench.stages.semantic.run_semantic), and until #72 several of them located
the thing they grade by hardcoded relative path -- `Path("infra/s3/logs-bucket.yaml").read_text()`.
Two consequences, both measured live during the v2 matrix:

1. A correct answer that chose an equally valid layout
   (`infra/s3/logs/bucket.yaml`) scored zero on a naming convention the
   task prompt never stated.
2. `read_text()` on a missing path raises FileNotFoundError, which pytest
   records as an *error* -- so a single wrong guess about the layout took
   out every assertion in the module at once, rather than failing the one
   assertion it actually bore on.

This module gives graders one way to do it right: find artifacts by what
they *are* (apiVersion/kind/name, or a structural feature of the source
text) rather than by where they sit, and report absence as an ordinary
assertion failure carrying an inventory of what the workspace did contain.

Import it from a grader with:

    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # tasks/
    import _grader_lib as gl

The `parents[3]` hop is relative to the grader file
(tasks/<stack>/<task>/tests/test_task.py), not to cwd, so it resolves the
same whether pytest was invoked from the workspace, the repo root, or
anywhere else.

Nothing in here raises on a missing or malformed file: readers return ""
or [], and the `require_*` helpers turn absence into pytest.fail with a
message naming what was looked for and what was found instead.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Iterable

import pytest
import yaml

# Directories that are never the model's answer. `node_modules` is the
# symlinked chant package tree (issue #58); `context/` is the docs the warm
# condition injects, which must never satisfy an assertion about what the
# model wrote.
SKIP_PARTS = frozenset({"node_modules", ".git", "__pycache__", "context", ".pytest_cache"})

YAML_SUFFIXES = (".yaml", ".yml")


# ──────────────────────────────────────────────────────────────────────────
# File discovery
# ──────────────────────────────────────────────────────────────────────────

def workspace() -> Path:
    """The model's workspace. Graders run with cwd pinned to it."""
    return Path(".")


def _skipped(p: Path) -> bool:
    return any(part in SKIP_PARTS for part in p.parts)


def iter_files(*suffixes: str, include_hidden: bool = False) -> list[Path]:
    """Every file in the workspace with one of `suffixes` (e.g. ".tf").

    Sorted for determinism, with node_modules/context/VCS noise excluded.
    Pass no suffixes to get every file.
    """
    out: list[Path] = []
    for p in sorted(workspace().rglob("*")):
        if not p.is_file() or _skipped(p):
            continue
        if not include_hidden and p.name.startswith(".") and p.name != ".sops.yaml":
            continue
        if suffixes and p.suffix not in suffixes:
            continue
        out.append(p)
    return out


def yaml_files() -> list[Path]:
    return iter_files(*YAML_SUFFIXES)


def read_text(path: Path) -> str:
    """File contents, or "" for anything unreadable. Never raises."""
    try:
        return path.read_text()
    except Exception:
        return ""


def concat_text(*suffixes: str) -> str:
    """Every matching file's text, joined. Used for 'is X mentioned anywhere'
    checks where the artifact's location genuinely does not matter."""
    return "\n---\n".join(read_text(p) for p in iter_files(*suffixes))


def inventory(limit: int = 40) -> str:
    """A short listing of what the workspace actually holds.

    Every absence message ends with this, so a failing grader says "here is
    what you wrote instead" rather than only naming what it wanted.
    """
    files = [str(p) for p in iter_files()]
    if not files:
        return "(workspace is empty)"
    shown = files[:limit]
    tail = "" if len(files) <= limit else f" (+{len(files) - limit} more)"
    return ", ".join(shown) + tail


# ──────────────────────────────────────────────────────────────────────────
# Model output / fenced blocks
# ──────────────────────────────────────────────────────────────────────────

def model_output() -> str:
    """The raw completion the runner wrote to model_output.md."""
    return read_text(workspace() / "model_output.md")


def fenced_blocks(text: str | None = None, langs: Iterable[str] | None = None) -> list[tuple[str, str]]:
    """(lang, code) for every fenced block, optionally filtered by info string.

    Mirrors bench.runner.extract_code_blocks's own block regex, so a grader
    falling back to the raw completion sees what the runner would have
    extracted had the block carried a recognisable path.
    """
    text = model_output() if text is None else text
    blocks = [(m.group(1), m.group(2).strip())
              for m in re.finditer(r"```(\w*)\n(.*?)```", text, re.DOTALL)]
    if langs is None:
        return blocks
    wanted = set(langs)
    return [(lang, code) for lang, code in blocks if lang in wanted]


# ──────────────────────────────────────────────────────────────────────────
# YAML documents
# ──────────────────────────────────────────────────────────────────────────

def load_docs(path: Path) -> list[dict]:
    """Every mapping document in one YAML file. Never raises: an unparseable
    or missing file yields []."""
    text = read_text(path)
    if not text:
        return []
    try:
        return [d for d in yaml.safe_load_all(text) if isinstance(d, dict)]
    except yaml.YAMLError:
        return []


def all_docs(include_model_output: bool = True) -> list[tuple[Path, dict]]:
    """(source, document) for every YAML mapping in the workspace.

    When the workspace holds no parseable YAML at all -- extraction can miss
    a block whose path was never backticked -- and `include_model_output` is
    set, the raw completion's fenced yaml blocks are parsed instead, tagged
    with a pseudo-path of model_output.md. This is a fallback, not a
    supplement: real extracted files always win, so a block the model
    labelled "alternative approach" cannot quietly satisfy an assertion the
    written files failed.
    """
    docs: list[tuple[Path, dict]] = []
    for p in yaml_files():
        for d in load_docs(p):
            docs.append((p, d))
    if docs or not include_model_output:
        return docs

    pseudo = workspace() / "model_output.md"
    for lang, code in fenced_blocks(langs=("", "yaml", "yml")):
        try:
            for d in yaml.safe_load_all(code):
                if isinstance(d, dict):
                    docs.append((pseudo, d))
        except yaml.YAMLError:
            continue
    return docs


def doc_text(doc: dict) -> str:
    """A document re-serialised, for loose 'mentions X anywhere' checks."""
    try:
        return yaml.safe_dump(doc, default_flow_style=False, sort_keys=False)
    except Exception:
        return str(doc)


def name_of(doc: dict) -> str:
    meta = doc.get("metadata")
    if isinstance(meta, dict):
        return str(meta.get("name", ""))
    return ""


def find_docs(
    docs: list[tuple[Path, dict]] | None = None,
    *,
    kind: str | Iterable[str] | None = None,
    api_version_contains: str | None = None,
    name: str | None = None,
    name_contains: str | None = None,
    mentions: str | None = None,
    where: Callable[[dict], bool] | None = None,
) -> list[tuple[Path, dict]]:
    """Locate YAML documents by what they are.

    Every filter is optional and ANDed. `kind` accepts a string or a set of
    acceptable kinds; `api_version_contains` matches a substring of
    apiVersion (so a grader can say "an ACK/upbound S3 resource" without
    pinning the API version); `mentions` matches a substring anywhere in the
    re-serialised document.
    """
    docs = all_docs() if docs is None else docs
    kinds = {kind} if isinstance(kind, str) else (set(kind) if kind else None)

    out = []
    for path, doc in docs:
        if kinds is not None and str(doc.get("kind", "")) not in kinds:
            continue
        if api_version_contains and api_version_contains not in str(doc.get("apiVersion", "")):
            continue
        if name is not None and name_of(doc) != name:
            continue
        if name_contains is not None and name_contains not in name_of(doc):
            continue
        if mentions is not None and mentions not in doc_text(doc):
            continue
        if where is not None and not where(doc):
            continue
        out.append((path, doc))
    return out


def require_docs(what: str, **kwargs) -> list[tuple[Path, dict]]:
    """find_docs, but absence is a clean assertion failure naming what the
    workspace held instead -- never a FileNotFoundError that errors out the
    rest of the module."""
    hits = find_docs(**kwargs)
    if not hits:
        pytest.fail(f"{what} not found in the workspace.\nWorkspace contains: {inventory()}")
    return hits


# ──────────────────────────────────────────────────────────────────────────
# Structural walking
# ──────────────────────────────────────────────────────────────────────────

def walk_values(node: Any, key: str) -> list:
    """Every value bound to leaf `key` anywhere in a parsed structure.

    Dialect-tolerant by construction: an ACK/upbound field can appear as a
    mapping (`versioningConfiguration: {status: Enabled}`) or as a
    single-element list (`versioningConfiguration: [{status: Enabled}]`)
    depending on which CRD generation the model targeted, and both reach the
    same leaf here.
    """
    found: list = []
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if k == key:
                    found.append(v)
                stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)
    return found


def values_under(node: Any, outer_key: str, inner_key: str) -> list:
    """Every `inner_key` value found anywhere beneath an `outer_key` subtree.

    `values_under(doc, "versioningConfiguration", "status")` finds Enabled
    whether the config is a mapping, a list of mappings, or nested one level
    deeper inside spec.forProvider.
    """
    out: list = []
    for sub in walk_values(node, outer_key):
        out.extend(walk_values(sub, inner_key))
        if isinstance(sub, (str, int, bool)):
            out.append(sub)
    return out


def deep_get(doc: Any, *keys: str, default: Any = None) -> Any:
    """dict.get chained, tolerant of a missing or non-mapping level."""
    cur = doc
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def truthy(value: Any) -> bool:
    """YAML booleans, and the strings models write instead of them."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "yes", "enabled", "on")


# ──────────────────────────────────────────────────────────────────────────
# Source-text location (non-YAML stacks)
# ──────────────────────────────────────────────────────────────────────────

def require_text(what: str, *suffixes: str, must_contain: str | None = None) -> str:
    """Concatenated text of the workspace's `suffixes` files, with absence
    reported as an assertion failure rather than an open() traceback."""
    text = concat_text(*suffixes)
    if not text.strip():
        pytest.fail(
            f"{what}: no {'/'.join(suffixes) or 'source'} files in the workspace.\n"
            f"Workspace contains: {inventory()}"
        )
    if must_contain is not None and must_contain not in text:
        pytest.fail(
            f"{what}: no {'/'.join(suffixes)} file contains {must_contain!r}.\n"
            f"Workspace contains: {inventory()}"
        )
    return text


def answer_text(
    what: str,
    *suffixes: str,
    defect_marker: str,
    fallback_block_marker: str | None = None,
) -> str:
    """The model's *corrected* version of a seeded file, located by content.

    A T4-debug workspace can hold both the seeded defective file and the
    model's rewrite under some other name, so "which file is the answer"
    cannot be decided by path. It is decided here by the defect: the first
    candidate file that no longer carries `defect_marker` is the answer. If
    every candidate still carries it -- the model changed nothing, or its
    rewrite was never extracted -- the raw completion's fenced blocks are
    searched for `fallback_block_marker`, and failing that the seeded text
    is returned unchanged so the assertions fail on the real defect rather
    than on a missing file.

    This is the same shape knr-ops T4-debug already used for `.sops.yaml`,
    generalised.
    """
    candidates = iter_files(*suffixes)
    seeded = ""
    for p in candidates:
        text = read_text(p)
        if not text:
            continue
        if defect_marker not in text:
            return text
        seeded = seeded or text

    marker = fallback_block_marker or defect_marker
    for _lang, code in fenced_blocks():
        if marker in code and defect_marker not in code:
            return code

    if seeded:
        return seeded
    pytest.fail(
        f"{what}: nothing in the workspace or the model's output looks like the "
        f"file under repair.\nWorkspace contains: {inventory()}"
    )

