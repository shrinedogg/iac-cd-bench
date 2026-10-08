"""
Static validation stage runner for IaC/CD benchmark.

Runs tool-native validation: kustomize build, flux build, crossplane render,
terraform plan, pulumi preview.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import logging
from pathlib import Path

import yaml

from bench.stages import lint as lint_mod

log = logging.getLogger(__name__)

# Vendored CRD JSON schemas (#83) live in bench.stages.lint, which this module
# already imports; both gates validate against the same mirror.
SCHEMA_DIR = lint_mod.SCHEMA_DIR
ROOT = Path(__file__).resolve().parents[2]


def run_static(workspace: Path, stack: str) -> dict:
    """Run tool-native static validation for the stack.

    Dispatches to the contract gate registry (bench.stages.gates) for every
    stack migrated onto it -- as of #111 that is all seven. tools/gate_diff.py
    is what earned this: it rebuilt each of coverage-v9's 84 stored workspaces
    and ran both the legacy per-stack helpers below and the contract gate
    against fresh copies, and the two disagreed in exactly the two ways that
    mattered -- a chant node_modules race in the differential tool itself
    (fixed by locking bench.stages.e2e.ensure_chant_node_modules) and
    TerraformGate coding every `terraform init` failure as GATE_DEFECT when
    six of coverage-v9's were the model's own broken HCL (fixed there). After
    both fixes, every disagreement left was the contract correctly excluding
    a legacy vacuous pass or a legacy hard-fail on no-artifact runs -- the
    same class of bug #99 and #110 fixed elsewhere, so those are the contract
    working as designed, not a discrepancy to chase.

    Import is deferred: bench.stages.gates imports _kubeconform_valid_count
    from this module, so importing it at module scope here is a cycle.

    The legacy `_knr_ops_static` / `_crossplane_static` / ... helpers below
    stay in place -- tests/test_static_gates.py and tests/test_preflight.py
    exercise them directly, and bench.stages.e2e still calls a couple for its
    own preflight checks. They are dead code from run_static's perspective,
    not deletable from the module's.
    """
    import bench.stages.gates  # noqa: F401 -- populates GATES via register()
    from bench.stages.contract import GATES

    gate = GATES.get(stack)
    if gate is None:
        return lint_mod.inapplicable(
            f"no static commands for stack: {stack}", "gate_defect")
    return gate.run(workspace).to_legacy()


def _kubeconform_valid_count(summary: str) -> int:
    """How many resources kubeconform actually validated, from its summary.

    `Summary: N resources found in 1 file - Valid: 2, Invalid: 0, Errors: 0,
    Skipped: 3`. Skipped resources are ones whose schema was missing; they are
    not evidence of anything, so only the Valid count is returned.
    """
    m = re.search(r"Valid:\s*(\d+)", summary)
    return int(m.group(1)) if m else 0


def _flux_kustomization_target(kfile: Path, workspace: Path) -> tuple[str, Path]:
    """The (name, --path) a Flux Kustomization file should be built with.

    The name comes from the first Kustomization document's `metadata.name`,
    and the path from its `spec.path` resolved inside the workspace. Both fall
    back to something usable rather than raising: an unreadable or nameless
    file still gets built (and fails on its own merits) instead of taking the
    whole stage down with a parse error.
    """
    name = kfile.stem
    path = kfile.parent
    try:
        docs = [d for d in yaml.safe_load_all(kfile.read_text())
                if isinstance(d, dict)]
    except (OSError, yaml.YAMLError):
        return name, path

    for doc in docs:
        if doc.get("kind") != "Kustomization":
            continue
        name = (doc.get("metadata") or {}).get("name") or name
        spec_path = (doc.get("spec") or {}).get("path")
        if spec_path:
            candidate = (workspace / str(spec_path).lstrip("./")).resolve()
            if candidate.is_dir() and workspace.resolve() in candidate.parents:
                path = candidate
        break
    return name, path


FLUX_KUSTOMIZE_GROUP = "kustomize.toolkit.fluxcd.io"


def _flux_kustomization_files(workspace: Path) -> list[Path]:
    """Every YAML holding a Flux Kustomization, found by reading it (#101).

    `kind: Kustomization` alone is ambiguous -- kustomize's own config file
    uses `kustomize.config.k8s.io/v1beta1` for the same kind -- so the
    apiVersion group is load-bearing, not decoration. A file that parses to
    no Flux Kustomization is not returned at all, which is what keeps the
    stem-derived-name fallback from ever firing.
    """
    hits: list[Path] = []
    for f in sorted(workspace.rglob("*.y*ml")):
        if "node_modules" in f.parts:
            continue
        try:
            docs = yaml.safe_load_all(f.read_text())
            if any(isinstance(d, dict)
                   and d.get("kind") == "Kustomization"
                   and str(d.get("apiVersion", "")).startswith(FLUX_KUSTOMIZE_GROUP)
                   for d in docs):
                hits.append(f)
        except (OSError, yaml.YAMLError):
            continue
    return hits


def _knr_ops_static(workspace: Path, results: list[str]) -> tuple[bool, bool]:
    """Run kustomize build and flux build for knr-ops."""
    passed = True

    # Find kustomization.yaml files
    kustomizations = list(workspace.rglob("kustomization.yaml"))
    for kfile in kustomizations:
        overlay_dir = str(kfile.parent)
        log.info("kustomize build %s", overlay_dir)
        try:
            proc = subprocess.run(
                # LoadRestrictionsNone (#F8): an overlay legitimately
                # references a base above it (`../../clusters/kustomization.yaml`),
                # and kustomize's default restrictor refuses that with
                # `security; file ... is not in or below ...` — failing the
                # build for the layout the stack is supposed to use. The
                # workspace is a disposable temp dir, so the restriction buys
                # nothing here.
                ["kustomize", "build", "--load-restrictor",
                 "LoadRestrictionsNone", overlay_dir],
                capture_output=True, text=True, timeout=60,
            )
            results.append(f"kustomize build {overlay_dir}: exit={proc.returncode}")
            if proc.stderr:
                results.append(f"ERR: {proc.stderr[:500]}")
            if proc.returncode != 0:
                passed = False
        except subprocess.TimeoutExpired:
            results.append(f"TIMEOUT: kustomize build {overlay_dir}")
            passed = False
        except FileNotFoundError:
            results.append(f"NOT FOUND: kustomize")
            log.warning("Command not found: kustomize")
            passed = False

    # Find flux kustomizations by content, never by filename (#101).
    #
    # This used to glob `**/kustomization_*.yaml` and `**/flux/kustomizations.yaml`
    # -- both of which encode the golden's own filenames. A model that wrote its
    # Kustomization to `flux/logs-bucket-kustomization.yaml` matched neither, so
    # correct work was silently never built; and a `flux/kustomizations.yaml`
    # holding no Kustomization document still got built under a stem-derived
    # name, producing `failed find kustomization with name 'kustomizations'` --
    # a failure the harness invented, charged to the model.
    #
    # Third instance of this family, after crossplane's claims (#89) and the
    # path-exact graders (#72). Rule 8: locate by content, never by path.
    flux_kustomizations = _flux_kustomization_files(workspace)
    for kfile in flux_kustomizations:
        log.info("flux build kustomization %s", kfile)
        try:
            # `flux build kustomization` takes a NAME, with the manifests
            # located by --path and the Kustomization itself by
            # --kustomization-file. Passing the file as the positional NAME
            # and omitting both flags (#82) left --path empty, so every call
            # exited 1 on `invalid resource path ""` before flux read a
            # manifest — the check could not pass, for any input, ever.
            name, path = _flux_kustomization_target(kfile, workspace)
            proc = subprocess.run(
                ["flux", "build", "kustomization", name,
                 "--path", str(path),
                 "--kustomization-file", str(kfile),
                 "--dry-run"],
                capture_output=True, text=True, timeout=60,
            )
            results.append(f"flux build {kfile.name}: exit={proc.returncode}")
            if proc.stderr:
                results.append(f"ERR: {proc.stderr[:500]}")
            if proc.returncode != 0:
                passed = False
        except subprocess.TimeoutExpired:
            results.append(f"TIMEOUT: flux build {kfile}")
            passed = False
        except FileNotFoundError:
            results.append(f"NOT FOUND: flux")
            log.warning("Command not found: flux")
            passed = False

    if kustomizations or flux_kustomizations:
        return passed, True

    # Nothing to build, but that is not the same as nothing to check. knr-ops
    # T4-debug asks for a SOPS age key to be fixed in `.sops.yaml`, so a
    # correct answer contains no kustomization at all -- and this gate, being
    # kustomize-and-flux only, abstained on it in both conditions. That was the
    # single reason knr-ops sat at 2.25 attempted stages while every other arm
    # reached 2.50, and under #110 a smaller denominator inflates the score.
    #
    # The arm still emits Kubernetes manifests, so validate them the way `bare`
    # does. Same mirror, same strictness: an unresolvable kind is a kind that
    # does not exist, which is a real defect worth failing (#83).
    manifests = [f for f in sorted(list(workspace.rglob("*.yaml"))
                                   + list(workspace.rglob("*.yml")))
                 if lint_mod.is_k8s_manifest(f)]
    if not manifests:
        results.append("no kustomization, no Flux Kustomization, no manifests")
        return passed, False

    validated = 0
    for yfile in manifests:
        log.info("kubeconform %s", yfile)
        try:
            proc = subprocess.run(
                ["kubeconform", "-summary",
                 *lint_mod.kubeconform_schema_args(), str(yfile)],
                capture_output=True, text=True, timeout=60,
            )
            results.append(f"kubeconform {yfile.name}: exit={proc.returncode}")
            if proc.stdout:
                results.append(proc.stdout[:300])
                validated += _kubeconform_valid_count(proc.stdout)
            if proc.stderr:
                results.append(f"ERR: {proc.stderr[:300]}")
            if proc.returncode != 0:
                passed = False
        except subprocess.TimeoutExpired:
            results.append(f"TIMEOUT: kubeconform {yfile}")
            passed = False
        except FileNotFoundError:
            results.append("NOT FOUND: kubeconform")
            log.warning("Command not found: kubeconform")
            return False, True

    # A pass that validated nothing is not a pass (#104).
    if passed and validated == 0:
        results.append(
            "no manifest resolved to a known schema — nothing was validated")
        return passed, False
    return passed, True


def _classify_crossplane_docs(workspace: Path):
    """Sort a workspace's YAML into (renderables, compositions, xrds, functions)
    by reading each document, not by matching its filename.

    `crossplane render` takes a composite resource. Which kinds those are is
    declared by the XRDs present: `spec.names.kind` is the XR, and
    `spec.claimNames.kind` the claim when the XRD offers one. So the workspace
    tells us what to look for rather than us guessing from a filename — this
    is rule 8 (locate by content, never by path), learned for graders in #72.

    Falls back to "a namespaced resource that is not Crossplane machinery"
    when no XRD declares anything, so a workspace shipping only a claim is
    still measurable.
    """
    MACHINERY = {"CompositeResourceDefinition", "Composition", "Function",
                 "Provider", "ProviderConfig", "DeploymentRuntimeConfig",
                 "ControllerConfig", "Configuration"}

    parsed: list[tuple[Path, list[dict]]] = []
    for f in sorted(workspace.rglob("*.y*ml")):
        try:
            docs = [d for d in yaml.safe_load_all(f.read_text())
                    if isinstance(d, dict) and d.get("kind")]
        except (OSError, yaml.YAMLError):
            continue
        if docs:
            parsed.append((f, docs))

    # COMPOSITE kinds only. `crossplane render` takes an XR and does no
    # claim-to-XR translation, so handing it a claim fails with
    #
    #   composition's compositeTypeRef.kind (VersionedBucket) does not match
    #   XR's kind (VersionedBucketClaim)
    #
    # -- a failure the harness caused by choosing the wrong input, charged to
    # the model. Models legitimately write claims; that is what a user applies.
    # The gate's job is to render the composite the Composition declares, and
    # `_synthesize_xr` builds one from the XRD when the answer contains none.
    renderable_kinds: set[str] = set()
    claim_kinds: set[str] = set()
    for _f, docs in parsed:
        for d in docs:
            if d.get("kind") != "CompositeResourceDefinition":
                continue
            spec = d.get("spec") or {}
            kind = (spec.get("names") or {}).get("kind")
            if kind:
                renderable_kinds.add(kind)
            ckind = (spec.get("claimNames") or {}).get("kind")
            if ckind:
                claim_kinds.add(ckind)

    renderables, compositions, xrds, functions = [], [], [], []
    for f, docs in parsed:
        kinds = {d["kind"] for d in docs}
        if "CompositeResourceDefinition" in kinds:
            xrds.append(f)
        if "Composition" in kinds:
            compositions.append(f)
        if "Function" in kinds:
            functions.append(f)
        for d in docs:
            kind = d["kind"]
            if kind in renderable_kinds:
                renderables.append(f); break
            if kind in claim_kinds:
                # A claim, not a composite. Not renderable; the XR gets
                # synthesised from the XRD instead.
                continue
            if not renderable_kinds and kind not in MACHINERY and \
                    (d.get("metadata") or {}).get("namespace"):
                renderables.append(f); break
    return renderables, compositions, xrds, functions


_UNFILLABLE = object()


def _schema_default(prop: dict):
    """A value satisfying one required property of an XRD's openAPIV3Schema.

    Prefers the schema's own vocabulary over anything invented: an enum's first
    member, then `default`, then a type-appropriate empty value. Returns the
    sentinel None only when the property declares a type we cannot fill, which
    the caller treats as "do not synthesise" rather than "guess".
    """
    if "default" in prop:
        return prop["default"]
    enum = prop.get("enum")
    if enum:
        return enum[0]
    t = prop.get("type")
    if t == "string":
        return "synthesized"
    if t in ("integer", "number"):
        return 0
    if t == "boolean":
        return False
    if t == "array":
        return []
    if t == "object":
        props = prop.get("properties") or {}
        req = prop.get("required") or []
        out = {}
        for name in req:
            v = _schema_default(props.get(name) or {})
            if v is _UNFILLABLE:
                return _UNFILLABLE
            out[name] = v
        return out
    return _UNFILLABLE


def _synthesize_xr(workspace: Path, xrd_files: list[Path],
                   results: list[str]) -> Path | None:
    """Build a minimal composite resource from an XRD so render has an input.

    The crossplane tasks never ask for one -- T2 says "Author XRD +
    Composition", and T3 says to add a region "without changing existing
    claims". So the gate was demanding an artifact the task tells the model not
    to write, and recording `inapplicable` when it obeyed (#109). Under rule 10
    an unmeasured axis is dropped rather than failed, which means abstaining
    inflated the arm's correctness (#110) -- the gate's own defect was scoring
    as a result.

    Everything needed is declared by the XRD: `spec.group`, the first served
    version, `spec.names.kind` (or `claimNames.kind` where the XRD offers a
    claim), and the required fields of its openAPIV3Schema.

    Returns None rather than guessing when a required field cannot be filled
    from the schema. An honest abstention beats a synthesised XR that fails for
    a reason the model did not cause.
    """
    for f in xrd_files:
        try:
            docs = [d for d in yaml.safe_load_all(f.read_text())
                    if isinstance(d, dict)]
        except (OSError, yaml.YAMLError):
            continue
        for d in docs:
            if d.get("kind") != "CompositeResourceDefinition":
                continue
            spec = d.get("spec") or {}
            group = spec.get("group")
            versions = [v for v in (spec.get("versions") or [])
                        if v.get("served")] or (spec.get("versions") or [])
            if not group or not versions:
                continue
            version = versions[0]
            # The COMPOSITE kind, never the claim kind. A Composition's
            # `compositeTypeRef.kind` names the composite, so synthesising a
            # claim makes render reject it with
            #
            #   composition's compositeTypeRef.kind (WebApp) does not match XR
            #
            # -- a failure the harness caused, charged to the model. Four of
            # six crossplane static failures in coverage-v7 were exactly this:
            # the model declared names.kind=WebApp / claimNames.kind=WebAppClaim,
            # correctly, and the gate built the claim.
            #
            # The golden hid it: its XRD has no claimNames at all, so the
            # fallback happened to pick the right kind and the fixture passed.
            kind = (spec.get("names") or {}).get("kind")
            if not kind:
                continue

            schema = (version.get("schema") or {}).get("openAPIV3Schema") or {}
            spec_schema = ((schema.get("properties") or {})
                           .get("spec") or {})
            props = spec_schema.get("properties") or {}
            body = {}
            for name in (spec_schema.get("required") or []):
                value = _schema_default(props.get(name) or {})
                if value is _UNFILLABLE:
                    results.append(
                        f"cannot synthesise an XR: required field {name!r} has "
                        "no fillable schema")
                    return None
                body[name] = value

            xr = {
                "apiVersion": f"{group}/{version['name']}",
                "kind": kind,
                "metadata": {"name": "harness-synthesized"},
                "spec": body,
            }
            out = workspace / "harness-synthesized-xr.yaml"
            out.write_text(yaml.safe_dump(xr, sort_keys=False))
            results.append(
                f"synthesised {kind} from the XRD (no XR in workspace; the "
                "tasks do not ask for one)")
            return out
    return None


def _ensure_functions(workspace: Path, functions: list[Path],
                      results: list[str]) -> list[Path]:
    """Supply the Function declarations render needs, if the answer has none.

    `crossplane render` needs the pipeline's functions declared. T2 tells the
    model to "use Crossplane functions mode (function-patch-and-transform)" --
    i.e. to reference it -- not to author the Function resource, which is
    cluster machinery the same way ProviderConfig is. Scaffolding it is the
    same call as Pulumi.yaml (#93) or `terraform init` before validate.
    """
    if functions:
        return functions
    src = ROOT / "golden-base" / "crossplane" / "compositions" / "functions.yaml"
    if not src.exists():
        return functions
    dest = workspace / "harness-functions.yaml"
    dest.write_text(src.read_text())
    results.append("scaffolded the Function declarations render requires")
    return [dest]


def _synthesize_xr_from_composition(workspace: Path, compositions: list[Path],
                                    results: list[str]) -> Path | None:
    """Build a composite from a Composition's `compositeTypeRef`.

    The fallback for an XRD that declares no `spec.names.kind`. A Composition
    always names the apiVersion and kind it composes, and that pair is exactly
    what `crossplane render` checks the XR against -- so it is a better source
    than guessing, and it cannot disagree with the Composition by construction.

    Emits a spec with no fields: without the XRD's schema there is nothing to
    fill required fields from, and inventing values would fail the render for a
    reason the model did not cause.
    """
    for f in compositions:
        try:
            docs = [d for d in yaml.safe_load_all(f.read_text())
                    if isinstance(d, dict)]
        except (OSError, yaml.YAMLError):
            continue
        for d in docs:
            if d.get("kind") != "Composition":
                continue
            ref = (d.get("spec") or {}).get("compositeTypeRef") or {}
            api, kind = ref.get("apiVersion"), ref.get("kind")
            if not api or not kind:
                continue
            xr = {"apiVersion": api, "kind": kind,
                  "metadata": {"name": "harness-synthesized"}, "spec": {}}
            out = workspace / "harness-synthesized-xr.yaml"
            out.write_text(yaml.safe_dump(xr, sort_keys=False))
            results.append(
                f"synthesised {kind} from the Composition's compositeTypeRef "
                "(the XRD declares no spec.names.kind)")
            return out
    return None


def _crossplane_static(workspace: Path, results: list[str]) -> tuple[bool, bool]:
    """Run crossplane render for Crossplane."""
    passed = True

    # Find claims
    # Locate every artifact by CONTENT, never by path — rule 8 of
    # docs/result-integrity.md, learned for graders in #72 and never applied
    # to the gates.
    #
    # Filename matching failed twice over. The golden names its claims
    # `claims/dev.yaml`, which `*claim*.yaml` never matched, so the gate
    # abstained on its own reference implementation (#89). And a model that
    # writes YAML without declaring a path gets the extractor's fallback name
    # `generated_0.yaml`, which matches nothing either — so even after #89 the
    # gate abstained on every model run in coverage-v2 while the models had in
    # fact produced perfectly good claims.
    #
    # An abstention is worse here than a failure: it removes the arm from
    # measurement silently AND lifts its composite, because an inapplicable
    # stage leaves the correctness denominator.
    claims, compositions, xrds, functions = _classify_crossplane_docs(workspace)

    # The tasks never ask for a composite resource (#109). T2 says "Author XRD
    # + Composition"; T3 says to add a region "without changing existing
    # claims". So the gate was demanding an artifact the task tells the model
    # NOT to write, and abstaining when it obeyed -- which under rule 10 left
    # the correctness denominator and INFLATED the arm's score (#110). The
    # gate's own defect was scoring as a result.
    #
    # Everything render needs is declared by the model's own XRD, so build it
    # rather than abstain. Same for the Function declarations: T2 tells the
    # model to *use* function-patch-and-transform, not to author the Function
    # resource, which is cluster machinery like ProviderConfig.
    if not claims and (xrds or compositions):
        synthesized = _synthesize_xr(workspace, xrds, results) if xrds else None
        if synthesized is None and compositions:
            # The XRD declared no `spec.names.kind` --
            # tasks/crossplane/T4-debug's seed is exactly this shape, and the
            # gate abstained on it in both conditions. The Composition still
            # says what it composes, and `compositeTypeRef` is precisely the
            # apiVersion/kind pair render checks the XR against, so take it
            # from there rather than abstain on an otherwise complete answer.
            synthesized = _synthesize_xr_from_composition(
                workspace, compositions, results)
        if synthesized is not None:
            claims = [synthesized]
    if claims and compositions and not functions:
        functions = _ensure_functions(workspace, functions, results)

    for claim in claims:
        log.info("crossplane render %s", claim)
        if not compositions or not functions:
            results.append(
                f"crossplane render {claim.name}: skipped — render needs a "
                "composition and a functions file, found "
                f"{len(compositions)} / {len(functions)}"
            )
            passed = False
            continue
        try:
            # `crossplane render <xr> <composition> <functions>`. The old call
            # was `crossplane beta render <claim>`: `beta render` was promoted
            # to a top-level `render` in the pinned CLI (1.20) and errors with
            # `unexpected argument render`, and two of the three required
            # arguments were never passed. The gate could not pass for any
            # input, on any version — see #82 for the identical shape in flux.
            proc = subprocess.run(
                ["crossplane", "render", str(claim),
                 str(compositions[0]), str(functions[0])],
                capture_output=True, text=True, timeout=120,
            )
            results.append(f"crossplane render {claim.name}: exit={proc.returncode}")
            if proc.stderr:
                results.append(f"ERR: {proc.stderr[:500]}")
            if proc.returncode != 0:
                passed = False
        except subprocess.TimeoutExpired:
            results.append(f"TIMEOUT: crossplane render {claim}")
            passed = False
        except FileNotFoundError:
            results.append(f"NOT FOUND: crossplane")
            log.warning("Command not found: crossplane")
            passed = False

    if not claims:
        results.append(
            "no crossplane claim (*claim*.yaml) in workspace: "
            f"{len(compositions)} composition(s), {len(xrds)} xrd(s) found, "
            "nothing to render"
        )

    return passed, bool(claims)


def _terraform_static(workspace: Path, results: list[str]) -> tuple[bool, bool]:
    """Init the workspace, then `terraform validate`.

    The docstring used to say "validate and plan". There is no plan here and
    there should not be: `terraform plan` is not hermetic. Even with a local
    backend override the AWS provider demands credentials and fails with
    "failed to refresh cached credentials, no EC2 IMDS role found", so a plan
    gate would pass on a laptop with ~/.aws/credentials and fail in CI --
    exactly the defect that makes the pulumi arms unrunnable there.

    Two real problems fixed here:

    `terraform init` was never run. validate needs the provider and module
    tree installed, so in a fresh model workspace it fails for reasons that
    have nothing to do with the model's HCL. `-backend=false` keeps it
    offline: the golden declares an S3 backend, and initialising that would
    need credentials.

    stderr was never recorded. validate writes its errors there, so every
    failure logged exactly `terraform validate: exit=1` and nothing else.
    All six terraform static failures in coverage-v2 are undiagnosable for
    this reason -- there is no way to tell a model's broken HCL from a
    harness problem, which is the same ambiguity #84 was about.
    """
    passed = True

    log.info("terraform init -backend=false")
    try:
        proc = subprocess.run(
            ["terraform", "init", "-backend=false", "-input=false", "-no-color"],
            capture_output=True, text=True, timeout=180,
            cwd=str(workspace),
        )
        results.append(f"terraform init: exit={proc.returncode}")
        if proc.returncode != 0:
            results.append(f"ERR: {(proc.stderr or proc.stdout)[:500]}")
            return False, True
    except subprocess.TimeoutExpired:
        results.append("TIMEOUT: terraform init")
        return False, True
    except FileNotFoundError:
        results.append("NOT FOUND: terraform")
        log.warning("Command not found: terraform")
        return False, True

    log.info("terraform validate")
    try:
        proc = subprocess.run(
            ["terraform", "validate", "-no-color"],
            capture_output=True, text=True, timeout=60,
            cwd=str(workspace),
        )
        results.append(f"terraform validate: exit={proc.returncode}")
        if proc.stdout:
            results.append(proc.stdout[:500])
        if proc.stderr:
            results.append(f"ERR: {proc.stderr[:500]}")
        if proc.returncode != 0:
            passed = False
    except subprocess.TimeoutExpired:
        results.append("TIMEOUT: terraform validate")
        passed = False
    except FileNotFoundError:
        results.append("NOT FOUND: terraform")
        log.warning("Command not found: terraform")
        passed = False

    return passed, True


def _pulumi_static(workspace: Path, results: list[str],
                   stack: str = "pulumi-python") -> tuple[bool, bool]:
    """Run pulumi preview for Pulumi stacks.

    Sets up a local filesystem backend for Pulumi and initializes the stack,
    so preview can run offline without requiring a Pulumi Cloud account.
    Installs Python dependencies from requirements.txt if present.

    The workspace gets a `Pulumi.yaml` if it has none (#93). Without one there
    is no project name, so pulumi cannot resolve a stack reference at all and
    every invocation dies on

        error: if you're using the --stack flag, pass the fully qualified
        name (organization/project/stack)

    before it reads a line of the model's program. That is what the whole
    pulumi column of coverage-v2 recorded: 6 of 6 static FAILs on both arms,
    none of which measured anything the model wrote. The golden passes only
    because it ships its own Pulumi.yaml.

    A project file is scaffolding, not an answer: the tasks ask for
    infrastructure, and no prompt asks the model to author one. Supplying it
    is the same call as symlinking node_modules for chant or running
    `terraform init` before validate.
    """
    passed = True

    # Derived from the stack under test, not from what happens to be in the
    # workspace, and computed unconditionally: it is read again by the
    # requirements scaffold below, which runs whether or not a Pulumi.yaml was
    # written here.
    runtime = "nodejs" if stack.endswith("typescript") else "python"

    if not any((workspace / n).exists() for n in ("Pulumi.yaml", "Pulumi.yml")):
        # The project NAME is load-bearing, not cosmetic: `pulumi.Config()`
        # with no argument reads the project's own namespace, so inventing a
        # name here would make config lookups miss for a reason the model
        # never caused. Take the golden's, so a scaffolded workspace and the
        # reference implementation resolve config identically.
        golden_yaml = ROOT / "golden-base" / stack / "Pulumi.yaml"
        name = f"iac-cd-bench-{stack}"
        if golden_yaml.exists():
            m = re.search(r"^name:\s*(\S+)", golden_yaml.read_text(), re.M)
            if m:
                name = m.group(1)
        (workspace / "Pulumi.yaml").write_text(
            f"name: {name}\n"
            f"runtime: {runtime}\n"
            "description: project scaffold supplied by the benchmark harness\n"
        )
        results.append(f"scaffolded Pulumi.yaml (name: {name}, runtime: {runtime})")

    # Same call, same reason: a python workspace with no requirements.txt gets
    # no venv below, so `pulumi preview` runs against an interpreter with no
    # pulumi SDK and dies on "No module named 'pulumi'" -- a failure about the
    # workspace, not the model's program. The pins are the golden's own, so the
    # SDK the answer is evaluated against is the SDK the arm was written for.
    if not (workspace / "requirements.txt").exists() and not (workspace / "package.json").exists():
        golden_reqs = ROOT / "golden-base" / "pulumi-python" / "requirements.txt"
        if golden_reqs.exists() and runtime == "python":
            (workspace / "requirements.txt").write_text(golden_reqs.read_text())
            results.append("scaffolded requirements.txt from the golden's pins")

    # Check if requirements.txt exists and install dependencies
    requirements_file = workspace / "requirements.txt"
    venv_dir = workspace / ".venv"
    if requirements_file.exists():
        log.info("Installing Python dependencies from requirements.txt")
        try:
            # Create and activate a virtual environment
            proc = subprocess.run(
                ["python3", "-m", "venv", str(venv_dir)],
                capture_output=True, text=True, timeout=120,
            )
            if proc.returncode != 0:
                results.append(f"Failed to create venv: {proc.stderr[:500]}")
                log.warning("venv creation failed")
            else:
                # Install dependencies in the venv
                python_exe = venv_dir / "bin" / "python3"
                proc = subprocess.run(
                    [str(python_exe), "-m", "pip", "install", "-q", "-r", str(requirements_file)],
                    capture_output=True, text=True, timeout=300,
                    cwd=str(workspace),
                )
                if proc.returncode != 0:
                    results.append(f"pip install failed: {proc.stderr[:500]}")
                    log.warning("pip install failed: %s", proc.stderr[:200])
                else:
                    # Fix namespace package: create pulumi/aws/__init__.py that imports from pulumi_aws
                    # This makes "from pulumi.aws import X" work properly (PEP 420 namespace package setup)
                    try:
                        pulumi_aws_dir = venv_dir / "lib"
                        # Find the site-packages directory (handle different Python versions)
                        site_packages = None
                        for lib_dir in pulumi_aws_dir.glob("python*"):
                            sp = lib_dir / "site-packages"
                            if sp.exists():
                                site_packages = sp
                                break
                        if site_packages:
                            pulumi_dir = site_packages / "pulumi"
                            pulumi_aws_dir_path = pulumi_dir / "aws"
                            pulumi_aws_pkg = site_packages / "pulumi_aws"
                            if pulumi_dir.exists() and pulumi_aws_pkg.exists():
                                # Create pulumi/aws directory with __init__.py that acts as a namespace package
                                if not pulumi_aws_dir_path.exists():
                                    pulumi_aws_dir_path.mkdir(parents=True, exist_ok=True)
                                init_file = pulumi_aws_dir_path / "__init__.py"
                                if not init_file.exists():
                                    # Use a namespace package approach that delegates to pulumi_aws
                                    # This allows both 'from pulumi.aws import x' and 'from pulumi.aws.s3 import x'
                                    init_file.write_text(
                                        "import sys\n"
                                        "from pathlib import Path\n"
                                        "import pulumi_aws\n"
                                        "# Copy pulumi_aws module into this namespace\n"
                                        "sys.modules['pulumi.aws'] = pulumi_aws\n"
                                        "for attr in dir(pulumi_aws):\n"
                                        "    if not attr.startswith('_'):\n"
                                        "        globals()[attr] = getattr(pulumi_aws, attr)\n"
                                    )
                    except Exception as e:
                        log.warning("Failed to fix pulumi.aws namespace: %s", e)
        except Exception as e:
            results.append(f"Failed to set up venv: {str(e)}")
            log.warning("venv setup failed: %s", e)

    # Set up local filesystem backend for Pulumi
    backend_dir = workspace / ".pulumi-backend"
    backend_dir.mkdir(parents=True, exist_ok=True)
    backend_url = f"file://{backend_dir.resolve()}"

    # Environment for pulumi commands with local backend
    pulumi_env = {
        **os.environ,
        "PULUMI_BACKEND_URL": backend_url,
        "PULUMI_CONFIG_PASSPHRASE": "",  # Allow empty passphrase for local dev
    }

    # If a venv was created, set PYTHONPATH to include the site-packages
    if venv_dir.exists():
        for lib_dir in venv_dir.glob("lib/python*"):
            site_packages = lib_dir / "site-packages"
            if site_packages.exists():
                existing_pythonpath = os.environ.get("PYTHONPATH", "")
                pythonpath = str(site_packages)
                if existing_pythonpath:
                    pythonpath = f"{pythonpath}:{existing_pythonpath}"
                pulumi_env["PYTHONPATH"] = pythonpath
                break

    # Initialize the stack locally before running preview
    log.info("pulumi stack init dev with local backend")
    try:
        proc = subprocess.run(
            # Selected, not --no-select: with a filestate backend an
            # unqualified `-s dev` is not always a resolvable stack
            # reference, and letting preview use the selected stack sidesteps
            # having to reconstruct `organization/<project>/dev` by hand.
            ["pulumi", "stack", "init", "dev"],
            capture_output=True, text=True, timeout=60,
            cwd=str(workspace),
            env=pulumi_env,
        )
        results.append(f"pulumi stack init: exit={proc.returncode}")
        if proc.returncode != 0:
            # Stack might already exist (idempotent), or there's a real error
            if "already exists" not in proc.stderr:
                results.append(f"ERR: {proc.stderr[:500]}")
                # Only mark as failed if it's not the "already exists" case
                if proc.returncode != 0:
                    log.info("Stack init failed: %s", proc.stderr[:200])
    except subprocess.TimeoutExpired:
        results.append("TIMEOUT: pulumi stack init")
        passed = False
        return passed, True
    except FileNotFoundError:
        results.append("NOT FOUND: pulumi")
        log.warning("Command not found: pulumi")
        return False, True

    log.info("pulumi preview")
    try:
        proc = subprocess.run(
            ["pulumi", "preview", "--non-interactive", "--diff"],
            capture_output=True, text=True, timeout=120,
            cwd=str(workspace),
            env=pulumi_env,
        )
        results.append(f"pulumi preview: exit={proc.returncode}")
        if proc.stdout:
            results.append(proc.stdout[:2000])
        if proc.stderr:
            results.append(f"ERR: {proc.stderr[:500]}")
        # Exit 0 is the only success. `pulumi preview` does not signal
        # "changes detected" with a non-zero code — that is what
        # --expect-no-changes is for, and it is not passed here. Verified
        # against the golden (exit 0) and against deliberately broken
        # programs, a syntax error and an invalid resource argument, which
        # both exit 255. An earlier version treated exit 1 as success unless
        # stderr happened to contain the substring "error"; that allowance
        # covered a case that does not arise, and would have masked any
        # failure mode that did exit 1 with a quiet stderr.
        if proc.returncode != 0:
            passed = False
    except subprocess.TimeoutExpired:
        results.append("TIMEOUT: pulumi preview")
        passed = False
    except FileNotFoundError:
        results.append("NOT FOUND: pulumi")
        log.warning("Command not found: pulumi")
        passed = False

    return passed, True


def _chant_static(workspace: Path, results: list[str]) -> tuple[bool, bool]:
    """Build the chant workspace to YAML, then validate the emitted manifests
    with kubeconform against the vendored schema mirror.

    The kubeconform call used to pass `-ignore-missing-schemas` and no
    `-schema-location` at all, and every kind chant emits is a CRD -- ACK,
    CAPI/CAPA, Flux, not one core Kubernetes kind. So nothing resolved, the
    flag swallowed all of it, and the summary read `Valid: 0 ... Skipped: 38`
    on every run ever recorded (#104). The gate reduced to "did `chant build`
    exit 0", and an invented kind scored as fine -- the same defect #83 fixed
    for `bare`, whose gate carries the comment explaining why.

    Now it matches bare: the mirror first, and no `-ignore-missing-schemas`,
    so a kind that fails to resolve is a kind that does not exist. That is
    only safe because `tools/vendor_schemas.py` covers the Flux and v1beta2
    CAPI groups chant emits; adding the flag back would be easier than adding
    a schema and is the wrong trade.
    """
    passed = True

    # Build into a private temp dir, never into the workspace.
    #
    # `preflight_chant_golden` runs this gate against `golden-base/chant`
    # ITSELF, so writing to `<workspace>/build/manifests.yaml` meant writing
    # into the golden — a shared path every concurrent runner would target at
    # once. That is the one true race blocking parallel execution: two workers
    # start, both build, and one reads what the other is mid-write.
    #
    # Nothing outside this function reads the artifact; the kubeconform call
    # below is its only consumer. So a temp dir is strictly better, and it also
    # stops task workspaces accumulating build output that later stages then
    # have to ignore.
    _build_tmp = tempfile.mkdtemp(prefix="chant-build-")
    build_out = Path(_build_tmp) / "manifests.yaml"

    log.info("chant build -f yaml")
    try:
        build_out.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            [lint_mod.workspace_bin(workspace, "chant"),
             "build", ".", "-f", "yaml", "-o", str(build_out)],
            capture_output=True, text=True, timeout=60,
            cwd=str(workspace),
        )
        results.append(f"chant build -f yaml: exit={proc.returncode}")
        if proc.stdout:
            results.append(proc.stdout[:500])
        if proc.stderr:
            results.append(f"ERR: {proc.stderr[:500]}")
        if proc.returncode != 0:
            passed = False
    except subprocess.TimeoutExpired:
        results.append("TIMEOUT: chant build -f yaml")
        passed = False
    except FileNotFoundError:
        results.append("NOT FOUND: chant")
        log.warning("Command not found: chant")
        return False, True

    if not build_out.exists():
        results.append("chant build produced no manifest; skipping kubeconform")
        return passed, True

    log.info("kubeconform %s", build_out)
    try:
        proc = subprocess.run(
            ["kubeconform", "-summary",
             *lint_mod.kubeconform_schema_args(), str(build_out)],
            capture_output=True, text=True, timeout=60,
        )
        results.append(f"kubeconform {build_out.name}: exit={proc.returncode}")
        if proc.stdout:
            results.append(proc.stdout[:500])
        if proc.stderr:
            results.append(f"ERR: {proc.stderr[:500]}")
        if proc.returncode != 0:
            passed = False
        validated = _kubeconform_valid_count(proc.stdout)
    except subprocess.TimeoutExpired:
        results.append(f"TIMEOUT: kubeconform {build_out}")
        return False, True
    except FileNotFoundError:
        results.append("NOT FOUND: kubeconform")
        log.warning("Command not found: kubeconform")
        return False, True

    # A pass that examined nothing is not a pass. `bare` has enforced this
    # since #81; chant did not, and reported `Valid: 0 ... Skipped: 38` on
    # every run ever recorded while its static column read PASS (#104). The
    # guard is the general invariant: a verdict is only worth as much as the
    # evidence behind it, so a gate that validated zero resources reports
    # `inapplicable` rather than claiming the model got it right.
    if passed and validated == 0:
        results.append(
            "no resource in the emitted manifests resolved to a known schema — "
            "every kind was skipped, so nothing was validated (see #104)"
        )
        return passed, False

    shutil.rmtree(_build_tmp, ignore_errors=True)
    passed = _chant_scenarios(workspace, results) and passed
    return passed, True


def _chant_scenarios(workspace: Path, results: list[str]) -> bool:
    """Check any declared plan scenarios, offline.

    Everything else in this file validates *shape*: kustomize build,
    kubeconform, terraform validate, `chant build` all answer "is this
    well-formed". None of them answer "what does this change do". A chant
    `Scenario` is a declared assertion about the resulting change set --
    `noop: true`, exact create/delete counts, or `deletes: [{name, ownership}]`
    -- evaluated against a recorded snapshot standing in for a live read. No
    cluster, no credentials, no network.

    A workspace that declares none is not a failure: `chant scenario check`
    exits 0 with "No scenarios declared", and this returns True. The check only
    bites when an answer makes a claim about its own effect and breaks it.
    """
    try:
        proc = subprocess.run(
            [lint_mod.workspace_bin(workspace, "chant"), "scenario", "check"],
            capture_output=True, text=True, timeout=120,
            cwd=str(workspace),
        )
    except subprocess.TimeoutExpired:
        results.append("TIMEOUT: chant scenario check")
        return False
    except FileNotFoundError:
        # Already reported by the build step above.
        return True

    out = (proc.stdout or "") + (proc.stderr or "")
    if "No scenarios declared" in out:
        results.append("chant scenario check: none declared")
        return True

    results.append(f"chant scenario check: exit={proc.returncode}")
    if out.strip():
        results.append(out[:800])
    return proc.returncode == 0


def _bare_static(workspace: Path, results: list[str]) -> tuple[bool, bool]:
    """Validate bare's plain YAML manifests with a client-side kubectl dry
    run, one file at a time. --dry-run=client (not =server) deliberately:
    static validation has no cluster dependency (unlike e2e, which applies
    for real against kind), and server-side dry-run would require a live
    API server to talk to just to check the manifests are well-formed.

    That intent is not reachable with kubectl and never was (#81). Even with
    `--validate=false`, `kubectl apply --dry-run=client` contacts an API
    server to resolve API groups before it can map a kind to a resource, so
    with no cluster up every invocation died — first on `failed to download
    openapi`, then on `couldn't get current server API group list` — and the
    stage failed 100% of bare runs without judging a single manifest.

    kubeconform is used instead: schema-based, offline by construction, and
    already pinned in the toolchain. It runs stricter than lint runs it —
    lint omits `-strict`, this adds it — so the two stages stay distinct
    gates rather than one gate run twice.

    bare's tasks are ACK and Cluster API resources, whose CRD schemas
    kubeconform does not ship, so `schemas/` vendors them (#83) and
    `-schema-location` points at that mirror ahead of the built-in one. The
    versions are not a choice made here: they are the ones the task fixtures
    already use — ACK s3/iam/rds at v1alpha1, Cluster API at v1beta1.

    `-ignore-missing-schemas` is deliberately NOT passed. With the schemas
    vendored, a kind that still fails to resolve is a kind that does not
    exist, and that is a real defect worth failing: the v3 matrix has models
    emitting `UserPolicy`, `RolePolicyAttachment` and friends, none of which
    are ACK IAM kinds. Skipping them would score an invented resource as
    fine.

    `-strict` is deliberately NOT passed either, for the opposite reason. It
    rejects unknown fields, and field sets drift between CRD releases: under
    it the bare *golden* failed on `spec.replication.roleRef` and
    `AWSMachineTemplate.spec.template.spec.instanceProfile`, both real fields
    from a release other than the one the mirror pins. A gate that fails the
    reference implementation is the #81/#82 defect wearing a different hat.
    Dropping it keeps everything that matters — an unresolvable kind still
    fails, and so does a field of the wrong type — while tolerating the
    version skew between the mirror and the fixtures.
    """
    passed = True
    validated = 0

    yaml_files = list(workspace.rglob("*.yaml")) + list(workspace.rglob("*.yml"))
    if not yaml_files:
        results.append("no YAML files in workspace")
        return passed, False

    for yfile in yaml_files:
        log.info("kubeconform %s", yfile)
        try:
            proc = subprocess.run(
                ["kubeconform", "-summary",
                 *lint_mod.kubeconform_schema_args(), str(yfile)],
                capture_output=True, text=True, timeout=60,
            )
            results.append(f"kubeconform {yfile.name}: exit={proc.returncode}")
            if proc.stdout:
                results.append(proc.stdout[:500])
                validated += _kubeconform_valid_count(proc.stdout)
            if proc.stderr:
                results.append(f"ERR: {proc.stderr[:500]}")
            if proc.returncode != 0:
                passed = False
        except subprocess.TimeoutExpired:
            results.append(f"TIMEOUT: kubeconform {yfile}")
            passed = False
        except FileNotFoundError:
            results.append("NOT FOUND: kubeconform")
            log.warning("Command not found: kubeconform")
            passed = False
            break

    if passed and validated == 0:
        results.append(
            "no resource in the workspace resolved to a known schema — every "
            "kind was skipped, so nothing was validated (see #81)"
        )
        return passed, False

    return passed, True
