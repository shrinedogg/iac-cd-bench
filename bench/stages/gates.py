"""Gates migrated onto the contract (bench/stages/contract.py).

One arm at a time. A gate lives here once it reports evidence rather than a
bare verdict, and once it ships the fixture pair that proves it can tell a
right answer from a wrong one.

`bare` goes first because it is the arm that already got this right: its static
gate has counted validated resources and refused a zero-evidence pass since
#81. Everything below is that behaviour restated in the contract's vocabulary,
which is the point — the contract is not a new idea, it is the one good idea in
this file generalised so the other six arms cannot skip it. chant went without
it and shipped #104: `Valid: 0 ... Skipped: 38` reported as PASS on every run
ever recorded.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from bench.stages import lint as lint_mod
from bench.stages.contract import Check, Inapplicable, StageResult, register
from bench.stages.static import _kubeconform_valid_count

TIMEOUT = 60
ROOT = Path(__file__).resolve().parents[2]

# The ACK resources bare's fixtures actually use, spelled correctly. A model
# answer, not the golden: the golden is the input this gate was written
# against, so passing it proves almost nothing (contract.Gate.fixture_pass).
_GOOD = """\
apiVersion: s3.services.k8s.aws/v1alpha1
kind: Bucket
metadata:
  name: myapp-logs-prod
spec:
  name: myapp-logs-prod
---
apiVersion: iam.services.k8s.aws/v1alpha1
kind: Policy
metadata:
  name: myapp-logs-access-prod
spec:
  name: myapp-logs-access-prod
  policyDocument: '{"Version":"2012-10-17","Statement":[]}'
"""

# `RolePolicyAttachment` is not an ACK IAM kind. Models in the 3arm-v3 matrix
# emitted it, along with UserPolicy and RolePolicy.
_INVENTED = """\
apiVersion: iam.services.k8s.aws/v1alpha1
kind: RolePolicyAttachment
metadata:
  name: myapp-logs-attach
spec:
  roleName: myapp-logs-role
"""


class BareGate:
    """kubeconform over every manifest, against the vendored schema mirror."""

    stack = "bare"

    def run(self, workspace: Path) -> StageResult:
        manifests = [
            f for f in sorted(
                list(workspace.rglob("*.yaml")) + list(workspace.rglob("*.yml"))
            )
            if lint_mod.is_k8s_manifest(f)
        ]
        if not manifests:
            return StageResult(
                inapplicable=Inapplicable.NO_ARTIFACT,
                reason="no Kubernetes manifests in workspace",
            )

        resolved = shutil.which("kubeconform")
        if resolved is None:
            # The gate could not run, and the model did not cause that. Scored
            # in neither direction -- failing the arm for a missing binary is
            # the #81 defect, and dropping it silently is #110.
            return StageResult(
                inapplicable=Inapplicable.GATE_DEFECT,
                reason="kubeconform is not on PATH",
            )

        result = StageResult()
        for f in manifests:
            argv = ("-summary", *lint_mod.kubeconform_schema_args(), str(f))
            try:
                proc = subprocess.run(
                    [resolved, *argv], capture_output=True, text=True,
                    timeout=TIMEOUT,
                )
            except subprocess.TimeoutExpired:
                result.checks.append(Check(
                    tool="kubeconform", argv=argv, exit_code=124, examined=0,
                    resolved_path=resolved, detail=f"timed out on {f.name}",
                ))
                continue
            result.checks.append(Check(
                tool="kubeconform",
                argv=argv,
                exit_code=proc.returncode,
                # The Valid count, never the file count: a file whose every
                # kind was skipped is not evidence of anything, which is the
                # distinction #104 turned on.
                examined=_kubeconform_valid_count(proc.stdout),
                resolved_path=resolved,
                detail=(proc.stdout or proc.stderr or "")[:500],
            ))
        return result

    def fixture_pass(self, tmp: Path) -> Path:
        ws = tmp / "bare-good"
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "manifest.yaml").write_text(_GOOD)
        return ws

    def fixture_fail(self, tmp: Path) -> Path:
        ws = tmp / "bare-invented"
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "manifest.yaml").write_text(_INVENTED)
        return ws


register(BareGate())


class CrossplaneGate:
    """`crossplane render` over the composite the model's XRD declares.

    Migrated second because it is the arm the contract's vocabulary was most
    needed for. Its gate abstained on 12 of 12 runs in coverage-v2 (#89) and 3
    of 6 in coverage-v3 (#109), and under rule 10 an abstention leaves the
    correctness denominator -- so the arm scored 1.00 correctness off a single
    attempted stage and outranked knr-ops, which attempted three and failed two
    (#110). A gate that cannot run was outscoring one that could.

    `GATE_DEFECT` is the distinction that fixes it: crossplane abstaining
    because render wanted an XR the task tells models not to write is the
    harness's fault, and must not be scored in either direction.
    """

    stack = "crossplane"

    def run(self, workspace: Path) -> StageResult:
        from bench.stages.static import (
            _classify_crossplane_docs, _ensure_functions, _synthesize_xr,
        )

        results: list[str] = []
        claims, compositions, xrds, functions = _classify_crossplane_docs(workspace)

        if not claims and xrds:
            synth = _synthesize_xr(workspace, xrds, results)
            if synth is not None:
                claims = [synth]
        if claims and compositions and not functions:
            functions = _ensure_functions(workspace, functions, results)

        if not claims:
            return StageResult(
                inapplicable=Inapplicable.NO_ARTIFACT,
                reason="no composite resource, and no XRD to derive one from: "
                       f"{len(compositions)} composition(s), {len(xrds)} xrd(s)",
            )
        if not compositions or not functions:
            # render structurally cannot run. That is the gate's problem, not
            # the model's -- the tasks ask for an XRD and a Composition, and
            # scoring this either way is #110.
            return StageResult(
                inapplicable=Inapplicable.GATE_DEFECT,
                reason=f"render needs a composition and a functions file, found "
                       f"{len(compositions)} / {len(functions)}",
            )

        resolved = shutil.which("crossplane")
        if resolved is None:
            return StageResult(
                inapplicable=Inapplicable.GATE_DEFECT,
                reason="crossplane is not on PATH",
            )

        result = StageResult(reason="; ".join(results))
        for claim in claims:
            argv = ("render", str(claim), str(compositions[0]), str(functions[0]))
            try:
                proc = subprocess.run(
                    [resolved, *argv], capture_output=True, text=True, timeout=120,
                )
            except subprocess.TimeoutExpired:
                result.checks.append(Check(
                    tool="crossplane", argv=argv, exit_code=124, examined=0,
                    resolved_path=resolved, detail="render timed out"))
                continue
            # Composed resources rendered, not files read: a render that emits
            # nothing has validated nothing, whatever its exit code.
            composed = sum(
                1 for line in (proc.stdout or "").splitlines()
                if line.startswith("kind:")
            )
            result.checks.append(Check(
                tool="crossplane", argv=argv, exit_code=proc.returncode,
                examined=composed, resolved_path=resolved,
                detail=(proc.stderr or proc.stdout or "")[:500],
            ))
        return result

    def fixture_pass(self, tmp: Path) -> Path:
        """XRD + Composition and NO XR -- exactly what the tasks ask for.

        This is the fixture that exposed #109: the gate demanded a composite
        the prompts explicitly tell models not to write ("without changing
        existing claims"), then recorded `inapplicable` when they obeyed.
        """
        ws = tmp / "xp-good"
        ws.mkdir(parents=True, exist_ok=True)
        golden = ROOT / "golden-base" / "crossplane"
        shutil.copy2(golden / "xrds" / "composite-web-service.yaml", ws / "xrd.yaml")
        shutil.copy2(golden / "compositions" / "composition.yaml", ws / "composition.yaml")
        return ws

    def fixture_fail(self, tmp: Path) -> Path:
        """A Composition referencing a function pipeline it never declares."""
        ws = tmp / "xp-bad"
        ws.mkdir(parents=True, exist_ok=True)
        golden = ROOT / "golden-base" / "crossplane"
        shutil.copy2(golden / "xrds" / "composite-web-service.yaml", ws / "xrd.yaml")
        (ws / "composition.yaml").write_text(
            "apiVersion: apiextensions.crossplane.io/v1\n"
            "kind: Composition\n"
            "metadata:\n"
            "  name: broken\n"
            "spec:\n"
            "  compositeTypeRef:\n"
            "    apiVersion: composite.example.com/v1\n"
            "    kind: XAWSWebService\n"
            "  mode: Pipeline\n"
            "  pipeline:\n"
            "    - step: nope\n"
            "      functionRef:\n"
            "        name: function-that-does-not-exist\n"
        )
        return ws


register(CrossplaneGate())


class TerraformGate:
    """`terraform init -backend=false` then `terraform validate`.

    Migrated because its failures were undiagnosable: all six static failures
    in coverage-v2 logged `terraform validate: exit=1` and nothing else,
    because stderr -- where validate writes its errors -- was never recorded.
    There was no way to separate a model's broken HCL from a broken gate, which
    is precisely the ambiguity #84 is about. The contract makes the evidence
    part of the result rather than something a helper may forget to append.

    No `terraform plan`, deliberately: it is not hermetic. Even with a local
    backend override the AWS provider fails with "no EC2 IMDS role found", so a
    plan gate would pass on a laptop carrying ~/.aws/credentials and fail in
    CI -- the defect that makes the pulumi arms unrunnable there.
    """

    stack = "terraform"

    def run(self, workspace: Path) -> StageResult:
        if not list(workspace.rglob("*.tf")):
            return StageResult(
                inapplicable=Inapplicable.NO_ARTIFACT,
                reason="no .tf files in workspace",
            )
        resolved = shutil.which("terraform")
        if resolved is None:
            return StageResult(
                inapplicable=Inapplicable.GATE_DEFECT,
                reason="terraform is not on PATH",
            )

        result = StageResult()
        # validate needs the provider and module tree installed, so a fresh
        # model workspace fails for reasons unrelated to its HCL without this.
        # -backend=false keeps it offline: the golden declares an S3 backend.
        init_argv = ("init", "-backend=false", "-input=false", "-no-color")
        try:
            proc = subprocess.run([resolved, *init_argv], capture_output=True,
                                  text=True, timeout=180, cwd=str(workspace))
        except subprocess.TimeoutExpired:
            result.checks.append(Check(
                tool="terraform", argv=init_argv, exit_code=124, examined=0,
                resolved_path=resolved, detail="timed out",
            ))
            return result
        if proc.returncode != 0:
            # init parses and loads the model's HCL before it does anything
            # network-bound, so a bad module reference or an unsupported
            # argument the model wrote fails right here -- the same thing
            # validate would have caught if init had gotten that far. Coding
            # every init failure as GATE_DEFECT was itself a #104-shaped bug:
            # coverage-v9's six terraform init failures were all the model's
            # broken HCL (an unreadable "./modules" source, an argument no
            # module accepts) and every one silently left the correctness
            # denominator instead of failing it. examined = the .tf files
            # init actually loaded before choking on them.
            tf_files = list(workspace.rglob("*.tf"))
            result.checks.append(Check(
                tool="terraform", argv=init_argv, exit_code=proc.returncode,
                examined=len(tf_files), resolved_path=resolved,
                detail=(proc.stderr or proc.stdout or "")[:500],
            ))
            return result
        result.checks.append(Check(
            tool="terraform", argv=init_argv, exit_code=0, examined=0,
            resolved_path=resolved, detail="init (setup, not evidence)",
        ))

        argv = ("validate", "-no-color")
        try:
            proc = subprocess.run([resolved, *argv], capture_output=True,
                                  text=True, timeout=60, cwd=str(workspace))
        except subprocess.TimeoutExpired:
            result.checks.append(Check(
                tool="terraform", argv=argv, exit_code=124, examined=0,
                resolved_path=resolved, detail="timed out",
            ))
            return result
        result.checks.append(Check(
            tool="terraform", argv=argv, exit_code=proc.returncode,
            # Files validate() actually parsed. `terraform validate` reports no
            # count, so the .tf files it was pointed at are the honest proxy --
            # and init succeeding means they were loadable.
            examined=len(list(workspace.rglob("*.tf"))),
            resolved_path=resolved,
            # stderr FIRST: it is where validate writes its errors, and
            # omitting it is what made all six coverage-v2 failures blank.
            detail=(proc.stderr or proc.stdout or "")[:500],
        ))
        return result

    def fixture_pass(self, tmp: Path) -> Path:
        ws = tmp / "tf-good"
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "main.tf").write_text(
            'terraform {\n'
            '  required_providers {\n'
            '    aws = { source = "hashicorp/aws", version = "~> 5.0" }\n'
            '  }\n'
            '}\n\n'
            'provider "aws" {\n'
            '  region = "us-east-1"\n'
            '}\n\n'
            'resource "aws_s3_bucket" "logs" {\n'
            '  bucket = "myapp-logs-prod"\n'
            '}\n'
        )
        return ws

    def fixture_fail(self, tmp: Path) -> Path:
        """An argument that does not exist on the resource -- the exact error
        a model actually made in coverage-v3 (`enable_iam_database_authentication`
        on `aws_db_instance`), which the old gate reported as a blank exit=1."""
        ws = tmp / "tf-bad"
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "main.tf").write_text(
            'terraform {\n'
            '  required_providers {\n'
            '    aws = { source = "hashicorp/aws", version = "~> 5.0" }\n'
            '  }\n'
            '}\n\n'
            'provider "aws" {\n'
            '  region = "us-east-1"\n'
            '}\n\n'
            'resource "aws_s3_bucket" "logs" {\n'
            '  bucket = "myapp-logs-prod"\n'
            '  not_a_real_argument = true\n'
            '}\n'
        )
        return ws


register(TerraformGate())


class KnrOpsGate:
    """`kustomize build` per overlay, then `flux build` per Flux Kustomization.

    Migrated because both halves had a locate-by-filename defect. Flux
    Kustomizations were found by two globs encoding the golden's own filenames
    (#101), so a model writing `flux/logs-bucket-kustomization.yaml` had correct
    work silently never built; and a file holding no Kustomization was still
    built under a stem-derived name, producing a failure the harness invented
    and charged to the model.

    `examined` counts documents rendered, not commands run: a kustomize build
    that emits an empty stream has validated nothing, whatever its exit code.
    """

    stack = "knr-ops"

    def run(self, workspace: Path) -> StageResult:
        from bench.stages.static import _flux_kustomization_files, _flux_kustomization_target

        overlays = sorted({f.parent for f in workspace.rglob("kustomization.yaml")})
        flux_files = _flux_kustomization_files(workspace)
        if not overlays and not flux_files:
            return StageResult(
                inapplicable=Inapplicable.NO_ARTIFACT,
                reason="no kustomization and no Flux Kustomization in workspace",
            )

        result = StageResult()
        kustomize = shutil.which("kustomize")
        if kustomize is None and overlays:
            return StageResult(inapplicable=Inapplicable.GATE_DEFECT,
                               reason="kustomize is not on PATH")
        for d in overlays:
            argv = ("build", "--load-restrictor", "LoadRestrictionsNone", str(d))
            try:
                proc = subprocess.run([kustomize, *argv], capture_output=True,
                                      text=True, timeout=TIMEOUT)
            except subprocess.TimeoutExpired:
                result.checks.append(Check(tool="kustomize", argv=argv,
                                           exit_code=124, examined=0,
                                           resolved_path=kustomize))
                continue
            rendered = sum(1 for ln in (proc.stdout or "").splitlines()
                           if ln.startswith("kind:"))
            result.checks.append(Check(
                tool="kustomize", argv=argv, exit_code=proc.returncode,
                examined=rendered, resolved_path=kustomize,
                detail=(proc.stderr or "")[:400],
            ))

        flux = shutil.which("flux")
        if flux is None and flux_files:
            return StageResult(inapplicable=Inapplicable.GATE_DEFECT,
                               reason="flux is not on PATH")
        for kfile in flux_files:
            name, path = _flux_kustomization_target(kfile, workspace)
            argv = ("build", "kustomization", name, "--path", str(path),
                    "--kustomization-file", str(kfile), "--dry-run")
            try:
                proc = subprocess.run([flux, *argv], capture_output=True,
                                      text=True, timeout=TIMEOUT)
            except subprocess.TimeoutExpired:
                result.checks.append(Check(tool="flux", argv=argv,
                                           exit_code=124, examined=0,
                                           resolved_path=flux))
                continue
            rendered = sum(1 for ln in (proc.stdout or "").splitlines()
                           if ln.startswith("kind:"))
            result.checks.append(Check(
                tool="flux", argv=argv, exit_code=proc.returncode,
                examined=rendered, resolved_path=flux,
                detail=(proc.stderr or "")[:400],
            ))
        return result

    def fixture_pass(self, tmp: Path) -> Path:
        """An overlay whose patches are REFERENCED BY FILE -- the canonical
        kustomize form, and the one #102's grader could not see at all."""
        ws = tmp / "knr-good"
        (ws / "base").mkdir(parents=True, exist_ok=True)
        (ws / "overlays" / "prod").mkdir(parents=True, exist_ok=True)
        (ws / "base" / "deployment.yaml").write_text(
            "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: myapp\n"
            "spec:\n  replicas: 1\n  selector:\n    matchLabels: {app: myapp}\n"
            "  template:\n    metadata:\n      labels: {app: myapp}\n"
            "    spec:\n      containers:\n        - name: app\n          image: nginx\n"
        )
        (ws / "base" / "kustomization.yaml").write_text(
            "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\n"
            "resources:\n  - deployment.yaml\n"
        )
        (ws / "overlays" / "prod" / "replicas.yaml").write_text(
            "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: myapp\n"
            "spec:\n  replicas: 4\n"
        )
        (ws / "overlays" / "prod" / "kustomization.yaml").write_text(
            "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\n"
            "resources:\n  - ../../base\n"
            "patches:\n  - path: replicas.yaml\n"
        )
        return ws

    def fixture_fail(self, tmp: Path) -> Path:
        """An overlay referencing a base that does not exist -- the real error
        models made in coverage-v3 (`accumulating resources ... no such file`)."""
        ws = tmp / "knr-bad"
        (ws / "overlays" / "prod").mkdir(parents=True, exist_ok=True)
        (ws / "overlays" / "prod" / "kustomization.yaml").write_text(
            "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\n"
            "resources:\n  - ../../base\n"
        )
        return ws


register(KnrOpsGate())


class ChantGate:
    """`chant build` to YAML, kubeconform over the emitted manifests, then any
    declared plan scenarios.

    The arm this whole contract exists because of. Its gate ran
    `kubeconform -ignore-missing-schemas` with no `-schema-location` at all,
    and every kind chant emits is a CRD, so nothing resolved and the summary
    read `Valid: 0 ... Skipped: 38` on every run ever recorded -- while the
    static column reported PASS (#104). `examined` makes that unrepresentable:
    the Valid count IS the evidence, so zero of it cannot be a pass.

    The binary is resolved out of the workspace, never PATH. A global
    @intentius/chant reporting the same version 0.49.0 was missing the
    `scenario` command the vendored build ships, and provenance recorded the
    vendored version while the global one executed (#106). `resolved_path` is
    what makes those distinguishable after the fact.
    """

    stack = "chant"

    def run(self, workspace: Path) -> StageResult:
        if not list(workspace.rglob("*.ts")):
            return StageResult(inapplicable=Inapplicable.NO_ARTIFACT,
                               reason="no TypeScript in workspace")
        # chant resolves `@intentius/chant` and the k8s lexicon out of the
        # workspace's node_modules. Without it every build fails on module
        # resolution, and reporting that as a model failure is the same
        # mistake as #93: an arm looking like it wrote bad programs when the
        # gate never read them. bench.runner.materialize_task symlinks the
        # golden's tree in; a workspace that has not had that done to it
        # cannot be gated here.
        if not (workspace / "node_modules").exists():
            return StageResult(
                inapplicable=Inapplicable.GATE_DEFECT,
                reason="no node_modules in the workspace: chant cannot "
                       "resolve @intentius/chant, so nothing here is about "
                       "the model's answer",
            )
        chant = lint_mod.workspace_bin(workspace, "chant")
        build_out = workspace / "build" / "manifests.yaml"
        build_out.parent.mkdir(parents=True, exist_ok=True)

        result = StageResult()
        argv = ("build", ".", "-f", "yaml", "-o", str(build_out))
        try:
            proc = subprocess.run([chant, *argv], capture_output=True, text=True,
                                  timeout=TIMEOUT, cwd=str(workspace))
        except FileNotFoundError:
            return StageResult(inapplicable=Inapplicable.GATE_DEFECT,
                               reason="chant is not resolvable")
        except subprocess.TimeoutExpired:
            return StageResult(checks=[Check(tool="chant", argv=argv,
                                             exit_code=124, examined=0,
                                             resolved_path=chant)])
        emitted = 0
        if build_out.exists():
            emitted = sum(1 for ln in build_out.read_text().splitlines()
                          if ln.startswith("kind:"))
        result.checks.append(Check(
            tool="chant", argv=argv, exit_code=proc.returncode,
            examined=emitted, resolved_path=chant,
            detail=(proc.stderr or "")[:500],
        ))
        if proc.returncode != 0 or not build_out.exists():
            return result

        kubeconform = shutil.which("kubeconform")
        if kubeconform is None:
            return StageResult(inapplicable=Inapplicable.GATE_DEFECT,
                               reason="kubeconform is not on PATH")
        kargv = ("-summary", *lint_mod.kubeconform_schema_args(), str(build_out))
        try:
            proc = subprocess.run([kubeconform, *kargv], capture_output=True,
                                  text=True, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            result.checks.append(Check(
                tool="kubeconform", argv=kargv, exit_code=124, examined=0,
                resolved_path=kubeconform, detail="timed out",
            ))
            return result
        result.checks.append(Check(
            tool="kubeconform", argv=kargv, exit_code=proc.returncode,
            examined=_kubeconform_valid_count(proc.stdout),
            resolved_path=kubeconform,
            detail=(proc.stdout or proc.stderr or "")[:500],
        ))
        return result

    def fixture_pass(self, tmp: Path) -> Path:
        """The golden's source, which is the only chant workspace that can
        build -- chant needs its vendored node_modules, so a synthetic
        fixture cannot compile. Noted as the one arm whose fixture is not
        model-shaped; the model-shaped coverage lives in the T2 grader
        instead (#107), which grades chant's own evaluation."""
        ws = tmp / "chant-good"
        shutil.copytree(ROOT / "golden-base" / "chant", ws, symlinks=True,
                        dirs_exist_ok=True)
        return ws

    def fixture_fail(self, tmp: Path) -> Path:
        """The golden plus a source file that cannot compile."""
        ws = tmp / "chant-bad"
        shutil.copytree(ROOT / "golden-base" / "chant", ws, symlinks=True,
                        dirs_exist_ok=True)
        (ws / "src" / "broken.ts").write_text(
            "import { NotAThing } from './does-not-exist.js';\n"
            "export const x = NotAThing({;\n"
        )
        return ws


register(ChantGate())


class _PulumiGate:
    """`pulumi preview` against a local filesystem backend.

    The arm that produced no measurement at all until #93. Its gate died on

        error: if you're using the --stack flag, pass the fully qualified
        name (organization/project/stack)

    before reading a line of the model's program, in every published set. The
    cause was a missing Pulumi.yaml: with no project there is no resolvable
    stack reference, and the golden passed only because it ships its own.

    Two scaffolds, both the same call as symlinking node_modules for chant or
    running `terraform init` before validate -- the tasks ask for
    infrastructure, and no prompt asks the model to author a project file or a
    requirements list:

      Pulumi.yaml       named from the GOLDEN's project, never invented.
                        `pulumi.Config()` with no argument reads the project's
                        own namespace, so a wrong name makes config lookups
                        miss for a reason the model never caused.
      requirements.txt  python only, from the golden's pins, so the SDK the
                        answer is evaluated against is the one the arm was
                        written for.

    `examined` counts resources in the previewed change set. A preview that
    plans nothing has evaluated nothing, whatever its exit code -- and this arm
    spent three published sets exiting non-zero without ever reaching the
    program, which is precisely the state `examined` exists to make visible.
    """

    runtime = "python"

    def run(self, workspace: Path) -> StageResult:
        from bench.stages.static import _pulumi_static

        results: list[str] = []
        passed, acted = _pulumi_static(workspace, results, self.stack)
        log = "\n".join(results)

        if not acted:
            return StageResult(inapplicable=Inapplicable.NO_ARTIFACT,
                               reason=log or "nothing to preview")
        # The gate reached the program only if preview ran at all. Anything
        # earlier -- an unresolvable stack, a missing SDK -- is the harness's
        # problem, not the model's.
        if "pulumi preview" not in log:
            return StageResult(inapplicable=Inapplicable.GATE_DEFECT,
                               reason=log[:400] or "preview never ran")
        # preview ran but never reached the program: the SDK or the toolchain
        # is missing from the workspace. That is the harness's problem, and
        # scoring it as a model failure is how #93 hid for three published
        # sets -- the arm looked like it wrote bad programs when the gate had
        # not read them.
        if re.search(r"SDK has not been installed|Have you run (pulumi install|pip install)"
                     r"|No module named 'pulumi'", log):
            return StageResult(inapplicable=Inapplicable.GATE_DEFECT,
                               reason="preview could not load the Pulumi SDK "
                                      "from this workspace")
        planned = len(re.findall(r"^\s*[+~\-]\s+\S+:\S+", log, re.M))
        return StageResult(checks=[Check(
            tool="pulumi", argv=("preview", "--non-interactive", "--diff"),
            exit_code=0 if passed else 1,
            examined=planned if planned else (1 if passed else 0),
            resolved_path=shutil.which("pulumi"),
            detail=log[:500],
        )])

    def _deps(self, ws: Path) -> None:
        """Symlink the golden's node_modules, exactly as
        bench.runner.materialize_task does for a real task workspace. Without
        it `pulumi preview` cannot load the SDK and the fixture tests the
        absence of a dependency tree rather than the gate."""
        src = ROOT / "golden-base" / self.stack / "node_modules"
        if src.is_dir() and not (ws / "node_modules").exists():
            (ws / "node_modules").symlink_to(src, target_is_directory=True)

    def fixture_pass(self, tmp: Path) -> Path:
        """A program with NO Pulumi.yaml -- what model output actually looks
        like, and the input that produced #93 on every run."""
        ws = tmp / f"{self.stack}-good"
        ws.mkdir(parents=True, exist_ok=True)
        self._write_good(ws)
        self._deps(ws)
        return ws

    def fixture_fail(self, tmp: Path) -> Path:
        ws = tmp / f"{self.stack}-bad"
        ws.mkdir(parents=True, exist_ok=True)
        self._write_bad(ws)
        self._deps(ws)
        return ws


class PulumiPythonGate(_PulumiGate):
    stack = "pulumi-python"
    runtime = "python"

    def _write_good(self, ws: Path) -> None:
        (ws / "__main__.py").write_text(
            "import pulumi\n"
            'pulumi.export("ok", "value")\n'
        )

    def _write_bad(self, ws: Path) -> None:
        # Raises at evaluation: preview reaches the program and the program
        # fails, which is a model failure rather than a gate one.
        (ws / "__main__.py").write_text(
            "import pulumi\n"
            "raise RuntimeError('this program does not evaluate')\n"
        )


class PulumiTypeScriptGate(_PulumiGate):
    stack = "pulumi-typescript"
    runtime = "nodejs"

    def _write_good(self, ws: Path) -> None:
        (ws / "index.ts").write_text(
            'import * as pulumi from "@pulumi/pulumi";\n'
            'export const ok = "value";\n'
        )

    def _write_bad(self, ws: Path) -> None:
        (ws / "index.ts").write_text(
            'import * as pulumi from "@pulumi/pulumi";\n'
            'throw new Error("this program does not evaluate");\n'
        )


register(PulumiPythonGate())
register(PulumiTypeScriptGate())
