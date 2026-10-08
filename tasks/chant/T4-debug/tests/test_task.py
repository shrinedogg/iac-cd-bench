"""Semantic grader for chant T4-debug: PostgresInstance backupRetentionDays.

Accepts any backupRetentionDays value >= 7 (the composite's own invariant,
MINIMUM_BACKUP_RETENTION_DAYS in defaults.ts) rather than requiring the
exact original value, and checks the fix didn't come at the cost of the
invariant itself or the call site's other required props.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


def _find_text(*name_parts: str) -> str:
    for p in sorted(Path(".").rglob("*.ts")):
        if all(part in p.parts for part in name_parts):
            try:
                return p.read_text()
            except Exception:
                continue
    return ""


def _postgres_instance_block(text: str, needle: str) -> str | None:
    for block in _props_of_call(text, "PostgresInstance"):
        if needle in block:
            return block
    return None
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
def prod_infra_text() -> str:
    return _find_text("prod", "infra")


def test_backup_retention_meets_invariant(prod_infra_text):
    """The fixed call site must satisfy backupRetentionDays >= 7."""
    block = _postgres_instance_block(prod_infra_text, "myapp-prod-db")
    assert block is not None, "expected a PostgresInstance({...}) call for myapp-prod-db"
    m = re.search(r"backupRetentionDays:\s*(\d+)", block)
    assert m, f"no backupRetentionDays prop found in the myapp-prod-db call: {block[:400]!r}"
    value = int(m.group(1))
    assert value >= 7, (
        f"backupRetentionDays={value} still violates the composite's own "
        f"invariant (must be >= 7) -- chant build would still fail"
    )


def test_other_props_and_invariant_untouched(prod_infra_text):
    """The fix must be the one value, not a rewrite -- and the invariant
    itself (MINIMUM_BACKUP_RETENTION_DAYS / the throw in postgres-instance.ts)
    must not have been weakened to route around the fix."""
    block = _postgres_instance_block(prod_infra_text, "myapp-prod-db")
    assert block is not None, "expected a PostgresInstance({...}) call for myapp-prod-db"
    assert re.search(r"""instanceClass:\s*["']db\.t3\.medium["']""", block), (
        "instanceClass must remain db.t3.medium -- this fix is one value, not a rewrite"
    )
    assert re.search(r"multiAZ:\s*true\b", block), (
        "multiAZ must remain true -- this fix is one value, not a rewrite"
    )

    # If postgres-instance.ts is present in the workspace (it wasn't supposed
    # to be touched), the invariant itself must still be intact.
    for p in sorted(Path(".").rglob("postgres-instance.ts")):
        try:
            composite_src = p.read_text()
        except Exception:
            continue
        assert "MINIMUM_BACKUP_RETENTION_DAYS" in composite_src and "throw" in composite_src, (
            "postgres-instance.ts's backupRetentionDays invariant must not be "
            "removed or weakened -- fix the call site, not the composite"
        )
