"""Placement tests for bench.runner.extract_code_blocks (issue #76).

The extractor decides where a model's fenced code blocks land in the
workspace. When it guesses wrong, a correct answer fails its build: the
run that opened #76 identified the seeded defect exactly, emitted the fix,
and still scored static=False and semantic=False, because the block it
declared for `src/envs/prod/infra/main.ts` was written to the workspace
root as `defaults.ts` — a filename mentioned once in an earlier explanatory
sentence. Its `../../../composites/index.js` import then resolved three
directories above the workspace and chant built nothing.

These tests pin the declaration forms, the containment rule, and the
pre-existing behaviours the fix had to leave alone.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bench.runner import extract_code_blocks, extract_code_blocks_detailed  # noqa: E402


def rel(workspace: Path, written: list[Path]) -> list[str]:
    root = workspace.resolve()
    return [str(p.resolve().relative_to(root)) for p in written]


# ── the #76 regression ───────────────────────────────────────────────────

ISSUE_76 = '''\
The violating prop is `backupRetentionDays: 5` on the `PostgresInstance(...)`
call. `PostgresInstance`'s factory refuses any value below
`MINIMUM_BACKUP_RETENTION_DAYS` (7, from `defaults.ts`), per SPEC acceptance
criterion 2. Raising it to `7` at the call site is the whole fix — the
invariant in `postgres-instance.ts`/`defaults.ts` is untouched.

`src/envs/prod/infra/main.ts`

```ts
import { PostgresInstance } from "../../../composites/index.js";

export const database = PostgresInstance({
  name: "myapp-prod-db",
  backupRetentionDays: 7,
});
```
'''


def test_issue_76_block_lands_at_its_declared_path(tmp_path):
    """The declared path wins over a filename mentioned in earlier prose."""
    written = extract_code_blocks(ISSUE_76, tmp_path, "chant")

    assert rel(tmp_path, written) == ["src/envs/prod/infra/main.ts"]
    # The prose mention must not have captured the block...
    assert not (tmp_path / "defaults.ts").exists()
    # ...and the parent directories are created for it.
    assert (tmp_path / "src/envs/prod/infra/main.ts").read_text().startswith("import")


def test_issue_76_sibling_import_resolves_inside_the_workspace(tmp_path):
    """The whole point of correct placement: the relative import resolves to a
    real path under the workspace, not to `/private/var/folders/jz/composites`."""
    (tmp_path / "src/composites").mkdir(parents=True)
    (tmp_path / "src/composites/index.js").write_text("export const PostgresInstance = 0;\n")

    written = extract_code_blocks(ISSUE_76, tmp_path, "chant")
    emitted = written[0]

    target = (emitted.parent / "../../../composites/index.js").resolve()
    assert target == (tmp_path / "src/composites/index.js").resolve()
    assert target.exists()


def test_issue_76_seed_file_is_not_clobbered(tmp_path):
    """The misplacement also overwrote the seed's own defaults.ts, destroying
    the invariant the answer was reasoning about."""
    (tmp_path / "defaults.ts").write_text("export const MINIMUM_BACKUP_RETENTION_DAYS = 7;\n")

    extract_code_blocks(ISSUE_76, tmp_path, "chant")

    assert "MINIMUM_BACKUP_RETENTION_DAYS" in (tmp_path / "defaults.ts").read_text()


# ── declaration forms ────────────────────────────────────────────────────

@pytest.mark.parametrize("content,expected", [
    # fence info string, bare
    ("```ts src/composites/defaults.ts\nexport const X = 7;\n```", "src/composites/defaults.ts"),
    # fence info string, title=
    ('```typescript title="src/composites/defaults.ts"\nexport const X = 7;\n```',
     "src/composites/defaults.ts"),
    # fence info string, lang:path
    ("```ts:src/composites/defaults.ts\nexport const X = 7;\n```", "src/composites/defaults.ts"),
    # standalone backticked line above the fence
    ("`src/composites/defaults.ts`\n\n```ts\nexport const X = 7;\n```",
     "src/composites/defaults.ts"),
    # bold + label decoration
    ("**File: `src/composites/defaults.ts`**\n\n```ts\nexport const X = 7;\n```",
     "src/composites/defaults.ts"),
    # first-line path comment inside the block
    ("```ts\n// src/composites/defaults.ts\nexport const X = 7;\n```",
     "src/composites/defaults.ts"),
])
def test_declaration_forms_place_the_file(tmp_path, content, expected):
    written = extract_code_blocks(content, tmp_path, "chant")
    assert rel(tmp_path, written) == [expected]


def test_fence_info_path_does_not_scramble_following_blocks(tmp_path):
    """`(\\w*)\\n` failed to match an annotated fence at all, so the block's
    *closing* fence was paired with the next block's opening one — dropping
    the first block and writing prose as code."""
    content = (
        "```ts src/a.ts\nexport const a = 1;\n```\n\n"
        "Some prose between the blocks.\n\n"
        "```ts src/b.ts\nexport const b = 2;\n```\n"
    )
    written = extract_code_blocks(content, tmp_path, "chant")

    assert rel(tmp_path, written) == ["src/a.ts", "src/b.ts"]
    assert (tmp_path / "src/a.ts").read_text().strip() == "export const a = 1;"
    assert (tmp_path / "src/b.ts").read_text().strip() == "export const b = 2;"
    assert "Some prose" not in (tmp_path / "src/b.ts").read_text()


# ── workspace containment ────────────────────────────────────────────────

@pytest.mark.parametrize("declared", [
    "../../etc/passwd.ts",
    "../outside.ts",
    "src/../../escape.ts",
    "/tmp/pwned.ts",
])
def test_escaping_paths_are_refused_loudly(tmp_path, declared):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    content = f"```ts {declared}\nexport const pwned = 1;\n```"

    written, errors = extract_code_blocks_detailed(content, workspace, "chant")

    assert written == []
    assert len(errors) == 1
    assert declared in errors[0]
    assert "outside the workspace" in errors[0]
    # Nothing anywhere near the escape target, and nothing silently relocated
    # into the workspace root either.
    assert list(workspace.rglob("*")) == []
    assert not (tmp_path / "outside.ts").exists()
    assert not (tmp_path / "escape.ts").exists()


def test_escaping_prose_path_is_refused_not_relocated(tmp_path):
    """An absolute path in prose used to be written for real: `workspace /
    "/tmp/pwned.ts"` is just `/tmp/pwned.ts`, so the extractor wrote outside
    the workspace with no complaint at all."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    target = tmp_path / "outside" / "pwned.ts"
    target.parent.mkdir()
    content = f"Write it to `{target}` please.\n\n```ts\nexport const pwned = 1;\n```"

    written, errors = extract_code_blocks_detailed(content, workspace, "chant")

    assert written == []
    assert errors and "outside the workspace" in errors[0]
    assert not target.exists()


def test_symlinked_directory_cannot_be_used_to_escape(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (tmp_path / "elsewhere").mkdir()
    (workspace / "link").symlink_to(tmp_path / "elsewhere", target_is_directory=True)

    written, errors = extract_code_blocks_detailed(
        "```ts link/pwned.ts\nexport const pwned = 1;\n```", workspace, "chant")

    assert written == []
    assert errors and "outside the workspace" in errors[0]
    assert not (tmp_path / "elsewhere/pwned.ts").exists()


def test_containment_does_not_reject_ordinary_nested_paths(tmp_path):
    written, errors = extract_code_blocks_detailed(
        "```ts src/envs/prod/infra/main.ts\nexport const ok = 1;\n```", tmp_path, "chant")

    assert errors == []
    assert rel(tmp_path, written) == ["src/envs/prod/infra/main.ts"]


# ── preserved pre-existing behaviour ─────────────────────────────────────

def test_prose_path_immediately_before_a_fence_still_works(tmp_path):
    content = "Update `base/deployment.yaml`:\n\n```yaml\napiVersion: apps/v1\nkind: Deployment\n```"
    written = extract_code_blocks(content, tmp_path, "knr-ops")
    assert rel(tmp_path, written) == ["base/deployment.yaml"]


def test_two_prose_paths_then_two_fences_keep_document_order(tmp_path):
    """`a.yaml` and `b.yaml` named together, then two blocks: the legacy
    nearest-following-block pairing is what gets this right, and the fix must
    not reverse it."""
    content = (
        "Update `base/a.yaml` and `base/b.yaml`:\n\n"
        "```yaml\napiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: a\n```\n\n"
        "```yaml\napiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: b\n```\n"
    )
    written = extract_code_blocks(content, tmp_path, "knr-ops")

    assert rel(tmp_path, written) == ["base/a.yaml", "base/b.yaml"]
    assert "name: a" in (tmp_path / "base/a.yaml").read_text()
    assert "name: b" in (tmp_path / "base/b.yaml").read_text()


def test_unnamed_block_falls_back_to_generated_name(tmp_path):
    written = extract_code_blocks(
        "```yaml\napiVersion: v1\nkind: ConfigMap\n```", tmp_path, "knr-ops")
    assert rel(tmp_path, written) == ["generated_0.yaml"]


def test_k8s_stack_skips_yaml_without_apiversion(tmp_path):
    content = "`base/values.yaml`\n\n```yaml\nreplicas: 3\nimage: nginx\n```"
    assert extract_code_blocks(content, tmp_path, "knr-ops") == []


def test_chant_stack_skips_non_typescript_blocks(tmp_path):
    content = (
        "`src/main.ts`\n\n```yaml\napiVersion: v1\nkind: ConfigMap\n```\n\n"
        "```bash\nchant build\n```\n"
    )
    assert extract_code_blocks(content, tmp_path, "chant") == []


def test_terraform_stack_only_takes_hcl(tmp_path):
    content = (
        "`main.tf`\n\n```hcl\nresource \"aws_s3_bucket\" \"b\" {}\n```\n\n"
        "```json\n{\"not\": \"hcl\"}\n```\n"
    )
    written = extract_code_blocks(content, tmp_path, "terraform")
    assert rel(tmp_path, written) == ["main.tf"]


def test_no_fenced_blocks_returns_empty(tmp_path):
    written, errors = extract_code_blocks_detailed("Just prose about `a.ts`.", tmp_path, "chant")
    assert written == [] and errors == []


def test_dotfile_prose_mentions_are_still_ignored(tmp_path):
    content = "Configured in `.eslintrc.json`.\n\n```yaml\napiVersion: v1\nkind: ConfigMap\n```"
    written = extract_code_blocks(content, tmp_path, "knr-ops")
    assert rel(tmp_path, written) == ["generated_0.yaml"]


def test_repeated_prose_mention_does_not_clobber_the_first_block(tmp_path):
    """The old loop let one filename claim two blocks, writing both to the
    same destination and losing the first block's content entirely."""
    content = (
        "Edit `base/a.yaml`. Then `base/a.yaml` again.\n\n"
        "```yaml\napiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: first\n```\n\n"
        "```yaml\napiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: second\n```\n"
    )
    written = extract_code_blocks(content, tmp_path, "knr-ops")

    assert "name: first" in (tmp_path / "base/a.yaml").read_text()
    assert len(written) == len({str(p) for p in written}) == 2
    assert "name: second" in (tmp_path / "generated_1.yaml").read_text()


# ── issue #108: TypeScript code snippets should not be materialised ─────────

def test_issue_108_snippet_without_declarations_is_not_materialised(tmp_path):
    """A code snippet quoted from a file (no imports, exports, or declarations)
    should not be written to the workspace, even with a declared path (issue #108).
    This is an illustration, not a module."""
    content = (
        "The violating prop is `backupRetentionDays: 5` on the call.\n\n"
        "```typescript\nbackupRetentionDays: 5,\n```"
    )
    written = extract_code_blocks(content, tmp_path, "chant")

    assert written == []
    assert not (tmp_path / "generated_0.ts").exists()


def test_issue_108_real_module_with_import_is_materialised(tmp_path):
    """A real TypeScript module with imports should be materialised,
    even if unnamed, to preserve the case where a task legitimately expects
    a single unlabelled code block containing real code."""
    content = (
        "Here's the fix:\n\n"
        "```typescript\n"
        "import { PostgresInstance } from \"../../../composites/index.js\";\n"
        "export const database = PostgresInstance({...});\n"
        "```"
    )
    written = extract_code_blocks(content, tmp_path, "chant")

    assert len(written) == 1
    assert written[0].name == "generated_0.ts"
    assert "import" in written[0].read_text()


def test_issue_108_real_module_with_declaration_is_materialised(tmp_path):
    """A real TypeScript module with top-level declarations should be materialised."""
    content = (
        "`src/composites/index.ts`\n\n"
        "```ts\n"
        "export const MINIMUM_BACKUP_RETENTION_DAYS = 7;\n"
        "```"
    )
    written = extract_code_blocks(content, tmp_path, "chant")

    assert rel(tmp_path, written) == ["src/composites/index.ts"]
    assert "MINIMUM_BACKUP_RETENTION_DAYS" in (tmp_path / "src/composites/index.ts").read_text()


def test_issue_108_real_module_with_export_is_materialised(tmp_path):
    """A real TypeScript module with export statements should be materialised."""
    content = (
        "```ts\n"
        "export const database = PostgresInstance({\n"
        "  name: \"myapp-prod-db\",\n"
        "  backupRetentionDays: 7,\n"
        "});\n"
        "```"
    )
    written = extract_code_blocks(content, tmp_path, "chant")

    assert len(written) == 1
    assert written[0].name == "generated_0.ts"
    assert "backupRetentionDays: 7" in written[0].read_text()


def test_issue_108_const_declaration_is_a_module(tmp_path):
    """Code with just a const declaration (no imports/exports) is a module."""
    content = (
        "```ts\n"
        "const retention = props.backupRetentionDays ?? MINIMUM_BACKUP_RETENTION_DAYS;\n"
        "if (retention < MINIMUM_BACKUP_RETENTION_DAYS) {\n"
        "  throw new Error(\"retention too low\");\n"
        "}\n"
        "```"
    )
    written = extract_code_blocks(content, tmp_path, "chant")

    assert len(written) == 1
    assert written[0].name == "generated_0.ts"


def test_issue_108_function_declaration_is_a_module(tmp_path):
    """Code with a function declaration is a module."""
    content = (
        "```ts\n"
        "function validateBackupRetention(days: number): boolean {\n"
        "  return days >= 7;\n"
        "}\n"
        "```"
    )
    written = extract_code_blocks(content, tmp_path, "chant")

    assert len(written) == 1
    assert written[0].name == "generated_0.ts"


def test_issue_108_snippet_with_no_module_markers_unnamed_is_dropped(tmp_path):
    """An unnamed block with no module markers is dropped entirely."""
    content = (
        "The failing line:\n\n"
        "```ts\nbackupRetentionDays: 5,\n```"
    )
    written = extract_code_blocks(content, tmp_path, "chant")

    assert written == []


def test_issue_108_snippet_with_no_module_markers_named_is_dropped(tmp_path):
    """Even with an explicit path, a snippet with no module markers is not written."""
    content = (
        "`src/config.ts`\n\n"
        "```ts\nbackupRetentionDays: 5,\n```"
    )
    written = extract_code_blocks(content, tmp_path, "chant")

    assert written == []
    assert not (tmp_path / "src/config.ts").exists()
