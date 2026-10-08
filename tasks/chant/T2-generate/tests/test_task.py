"""Semantic coverage grader for chant T2-generate.

Grades chant's *evaluated* output, not its source text.

The previous version grepped the emitted TypeScript for `SecureBucket({` and
`ReaderIam({`, which required a literal inline props object. A correct answer
written as

    const logsProps = { name: "myapp-logs-dev", env: ENV, region: REGION };
    export const logs = SecureBucket(logsProps);

matched nothing and scored zero (#107) -- the same defect as #102's kustomize
patches: the grader recognised one syntactic form of a right answer.

chant answers both questions itself, for any call form that actually produces
the resource:

  `chant explain --format okf`  one concept per entity, whose frontmatter
                                carries `composite:` and `composite_instance:`
                                -- exactly what "via the composite, not a raw
                                resource" means.
  `chant build -f yaml`         the emitted manifests, carrying the resource's
                                real properties.

Grading the built manifests also puts this arm on the same three safety
assertions as `bare` and `knr-ops` -- versioning, encryption, public-access
block (#103). That is not testing the library: the model still has to route
through a composite that provides them, and a hand-rolled bucket still fails
`no_raw_bucket_bypass`. It means chant is *measured* on the properties the
other arms are measured on, against the same kind of artifact, instead of
being credited for them by assumption.

A workspace where chant cannot run at all falls back to a form-agnostic source
scan rather than failing everything: a build failure is the static gate's
verdict to deliver, not this one's.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

TIMEOUT = 120


def _chant_bin() -> str:
    """The workspace's own chant, never a global install (#106)."""
    local = Path("node_modules") / ".bin" / "chant"
    return str(local) if local.exists() else "chant"


def _run(*args: str) -> str | None:
    try:
        proc = subprocess.run([_chant_bin(), *args], capture_output=True,
                              text=True, timeout=TIMEOUT, cwd=".")
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


@pytest.fixture(scope="module")
def entities() -> dict[str, dict[str, str]]:
    """logical id -> OKF frontmatter, from chant's evaluation of the source."""
    out = _run("explain", "--format", "okf")
    if not out:
        return {}
    try:
        files = json.loads(out).get("files") or {}
    except json.JSONDecodeError:
        return {}

    parsed: dict[str, dict[str, str]] = {}
    for path, content in files.items():
        if not path.endswith(".md") or path.endswith("index.md"):
            continue
        m = re.match(r"---\n(.*?)\n---", content, re.S)
        if not m:
            continue
        fm = {}
        for line in m.group(1).splitlines():
            key, sep, value = line.partition(":")
            if sep:
                fm[key.strip()] = value.strip()
        if fm.get("name"):
            parsed[fm["name"]] = fm
    return parsed


@pytest.fixture(scope="module")
def built() -> list[dict]:
    """The emitted manifests."""
    out = _run("build", ".", "-f", "yaml")
    if not out:
        return []
    try:
        return [d for d in yaml.safe_load_all(out) if isinstance(d, dict)]
    except yaml.YAMLError:
        return []


@pytest.fixture(scope="module")
def source_text() -> str:
    """Fallback only. Excludes node_modules -- a materialized chant workspace
    symlinks in the package's own .ts/.d.ts files (#58)."""
    chunks = []
    for p in sorted(Path(".").rglob("*.ts")):
        if "node_modules" in p.parts:
            continue
        try:
            chunks.append(p.read_text())
        except OSError:
            continue
    return "\n---\n".join(chunks)


def _bucket(built: list[dict], name: str) -> dict | None:
    for d in built:
        if d.get("kind") == "Bucket" and (d.get("spec") or {}).get("name") == name:
            return d
    return None


def _composite_of(entities: dict[str, dict[str, str]], substr: str,
                  kind: str) -> set[str]:
    """Composites that produced an entity of `kind` whose instance names
    `substr` -- e.g. which composite built the "logs" Bucket."""
    return {
        fm["composite"]
        for fm in entities.values()
        if fm.get("composite")
        and kind in (fm.get("type") or "")
        and substr in (fm.get("composite_instance") or "").lower()
    }


def _calls_composite(text: str, composite: str, near: str) -> bool:
    """Form-agnostic fallback: `<composite>(` appears, and the target name
    appears in the same file. Accepts an inline object, a named props
    variable, or a spread -- anything the old `\\(\\s*\\{` regex refused."""
    for chunk in text.split("\n---\n"):
        if re.search(rf"\b{composite}\s*\(", chunk) and near in chunk:
            return True
    return False


def _env_cases():
    return [("dev", "myapp-logs-dev"), ("prod", "myapp-logs-prod")]


@pytest.mark.parametrize("env,bucket_name", _env_cases())
def test_logs_bucket_via_composite(entities, built, source_text, env, bucket_name):
    """The logs bucket exists and came from SecureBucket."""
    if entities or built:
        assert _bucket(built, bucket_name) is not None, (
            f"expected an emitted Bucket named {bucket_name}; chant built: "
            f"{sorted((d.get('spec') or {}).get('name') for d in built if d.get('kind') == 'Bucket')!r}"
        )
        assert "SecureBucket" in _composite_of(entities, "logs", "S3::Bucket"), (
            f"the {bucket_name} bucket must come from the SecureBucket composite; "
            f"chant attributes the logs bucket to "
            f"{_composite_of(entities, 'logs', 'S3::Bucket') or 'no composite'}"
        )
        return
    assert _calls_composite(source_text, "SecureBucket", bucket_name), (
        f"expected a SecureBucket call declaring {bucket_name}"
    )


@pytest.mark.parametrize("env,bucket_name", _env_cases())
def test_logs_bucket_is_secure(built, env, bucket_name):
    """The three properties bare and knr-ops are graded on (#103), read off
    chant's emitted manifest rather than assumed from the composite."""
    if not built:
        pytest.skip("chant build produced nothing; the static gate reports that")
    b = _bucket(built, bucket_name)
    assert b is not None, f"no emitted Bucket named {bucket_name}"
    spec = b.get("spec") or {}
    assert (spec.get("versioning") or {}).get("status") == "Enabled", (
        f"{bucket_name} must have versioning enabled, got {spec.get('versioning')!r}"
    )
    assert spec.get("encryption"), f"{bucket_name} must set server-side encryption"
    assert spec.get("publicAccessBlock"), f"{bucket_name} must block public access"


def test_no_raw_bucket_bypass(entities, built, source_text):
    """The logs buckets must go through SecureBucket, not a hand-rolled
    resource. chant attributes every entity to its composite, so a raw
    declaration shows up as one with no composite at all."""
    if entities:
        raw = [
            fm.get("name")
            for fm in entities.values()
            if "S3::Bucket" in (fm.get("type") or "")
            and not fm.get("composite")
        ]
        assert not raw, (
            f"these buckets were declared without a composite: {raw!r} -- the "
            "logs bucket must be declared via SecureBucket"
        )
        return
    raw_blocks = re.findall(r"new S3Bucket\(\s*\{[^}]*\}", source_text)
    assert not [b for b in raw_blocks if "myapp-logs-" in b], (
        "myapp-logs bucket(s) must be declared via the SecureBucket composite"
    )


@pytest.mark.parametrize("env,bucket_name", _env_cases())
def test_logs_reader_scoped(entities, built, source_text, env, bucket_name):
    """A reader identity scoped to the logs bucket, granting PutObject, and
    not reaching the assets bucket."""
    if not (entities or built):
        assert _calls_composite(source_text, "ReaderIam", bucket_name), (
            f"expected a ReaderIam call for {bucket_name}"
        )
        return

    assert "ReaderIam" in _composite_of(entities, "logsreader", "Iam::") \
        or "ReaderIam" in _composite_of(entities, "logs", "Iam::"), (
        "the logs reader identity must come from the ReaderIam composite"
    )

    docs = [
        (d.get("spec") or {}).get("policyDocument") or ""
        for d in built if d.get("kind") == "Policy"
    ]
    scoped = [doc for doc in docs if bucket_name in doc]
    assert scoped, (
        f"expected an IAM policy naming {bucket_name}; policies emitted: {len(docs)}"
    )
    assert any("PutObject" in doc for doc in scoped), (
        f"the {bucket_name} reader must grant s3:PutObject via additionalActions"
    )
    assets = bucket_name.replace("myapp-logs-", "myapp-assets-")
    assert not any(assets in doc for doc in scoped), (
        f"the {bucket_name} reader must not also reference {assets}"
    )


def test_no_wildcard_actions(built, source_text):
    """SPEC criterion 4: enumerated actions, never a wildcard. Checked against
    the emitted policy documents when there are any, since that is where a
    wildcard would actually take effect."""
    haystack = "\n".join(
        (d.get("spec") or {}).get("policyDocument") or ""
        for d in built if d.get("kind") == "Policy"
    ) if built else source_text

    assert not re.search(r"""["']s3:\*["']""", haystack), (
        "found a wildcard S3 action (s3:*) -- SPEC criterion 4 requires "
        "enumerated actions"
    )
    assert not re.search(r'"Action"\s*:\s*"\*"', haystack), (
        "found a bare wildcard IAM action"
    )


def test_secret_axis_is_exempted_for_this_arm_by_the_spec():
    """chant is not graded on the secret requirement, and that is deliberate.

    `scenario/SPEC.md` line 18 names each arm's mechanism for the encrypted
    secret and carves this one out explicitly:

        Secret (DB connection string) | SOPS-encrypted (knr-ops), Crossplane
        SecretStore/ProviderSecret, Terraform -var-file or SOPS, Pulumi
        ConfigSecret, **chant referenced-provenance secret ref (no committed
        ciphertext)**

    So `bare` and `knr-ops` each carry four secret/SOPS assertions in their
    T2 graders and this arm carries none. That traces to a real feature gap
    (#6), and grading chant on a primitive it does not have would be wrong.

    What was wrong was that the exemption was INVISIBLE: the composite treated
    chant's denominator as equivalent to the others' while it was measured on
    strictly less. This test does not assert behaviour — it makes the carve-out
    legible at the place a reader looks for it, so nobody re-derives it from
    a grep of assertion counts the way #103 had to.

    It is deliberately not a skip: a skip reads as "not run yet". This IS the
    current, intended state, and it should turn into a real assertion the day
    chant ships a committed-encrypted primitive (#6, #67).
    """
    import pathlib

    spec = pathlib.Path(__file__).resolve().parents[4] / "scenario" / "SPEC.md"
    if not spec.is_file():
        pytest.skip("scenario/SPEC.md not reachable from the workspace")
    assert "no committed ciphertext" in spec.read_text(), (
        "SPEC.md no longer carves chant out of the secret requirement. If the "
        "exemption is gone, this arm needs the four secret assertions bare and "
        "knr-ops carry, and #103 should be reopened"
    )
