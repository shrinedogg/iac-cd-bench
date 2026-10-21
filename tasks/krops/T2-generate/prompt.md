## Task: Onboard the eu-central-1 workload cluster, end to end

**Stack:** krops (Flux + CAPI/CAPA + ACK, GitOps monorepo)

You are given a krops GitOps repository (the AWS slice). It currently runs
two workload clusters, `eu-north-1-workload` and `eu-west-1-workload`, from
AWS account `120392301094`. Your job is to onboard a THIRD workload cluster
in region **eu-central-1** (`eu-central-1-workload`), following the repo's
existing patterns exactly.

### How the current two clusters are wired (the pattern you must follow)

1. **Cluster definition** — `mgmt/aws/clusters/<region>/`:
   - `kustomization.yaml` lists `staging`
   - `staging/kustomization.yaml` applies `namePrefix: <region>-`, declares
     `configurations: [capi-nameref.yaml]` and `resources: [cluster.yaml]`
   - `staging/capi-nameref.yaml` is the nameReference block mapping
     `Cluster.spec/infrastructureRef/name` → `AWSManagedCluster`,
     `Cluster.spec/controlPlaneRef/name` → `AWSManagedControlPlane`,
     `MachinePool.spec/template/spec/infrastructureRef/name` →
     `AWSManagedMachinePool`, and `MachinePool.spec/clusterName` +
     `spec/template/spec/clusterName` → `Cluster`
   - `staging/cluster.yaml` holds, in namespace `default`: a `Cluster` named
     `workload` labeled `fluxcd: enabled` + `region: <region>` (refs:
     `controlPlaneRef` kind `AWSManagedControlPlane` name
     `workload-control-plane`; `infrastructureRef` kind
     `AWSManagedCluster` name `<region>-workload` — that AWSManagedCluster
     object itself lives outside this build, in
     `infrastructure/aws-clusters-resources/managedclusters.yaml`, so it is
     written at its final, already-prefixed name); an
     `AWSManagedControlPlane` named `workload-control-plane` (region,
     version `"1.34.6"`); and for each node pool a `MachinePool` named
     `workload-x86` + a matching `AWSManagedMachinePool` named
     `workload-x86` (both in this build, so kustomize's nameReference
     rewrites the ref after the prefix is applied)
   - `mgmt/aws/clusters/kustomization.yaml` lists the region dirs
   - `mgmt/aws/clusters/flux-ks.yaml` has one Flux Kustomization per region:
     `name: <region>`, `path: ./mgmt/aws/clusters/<region>`, `prune: true`,
     `wait: false`, `postBuild.substituteFrom: [ConfigMap krops-run
     (optional)]`, `dependsOn: [capa-system]`
   - `mgmt/aws/infrastructure/aws-clusters-resources/managedclusters.yaml`
     holds one `AWSManagedCluster` per workload cluster (e.g. name
     `eu-north-1-workload`, namespace `default`, `spec: {}`)

2. **Flux bootstrap bag** — `mgmt/aws/addons/flux-apps/`:
   - `flux-apps-rset.yaml`: a `ResourceSet` (fluxcd.controlplane.io/v1) in
     namespace `default` with `spec.inputs` (one per workload cluster:
     `region` + `cluster`) and a `resourcesTemplate` that renders one Flux
     `Kustomization` per input, named `flux-apps-<< inputs.region >>` in
     namespace `default`, with `prune: true`,
     `kubeConfig.secretRef.name: << inputs.cluster >>-kubeconfig`,
     `path: ./mgmt/aws/addons/flux-apps/regions/<< inputs.region >>`, and SOPS
     decryption via `sops-age`
   - `regions/<region>/`: `namespace.yaml` (flux-system), `cluster-vars.yaml`
     (ConfigMap `cluster-vars` in flux-system with `AWS_REGION`,
     `CLUSTER_NAME` — the full prefixed cluster name — and
     `AWS_ACCOUNT_ID`), `flux-instance.yaml` (a `FluxInstance` in flux-system:
     `distribution.version: "2.x"`, registry `ghcr.io/fluxcd`, artifact
     `oci://ghcr.io/controlplaneio-fluxcd/flux-operator-manifests`,
     components source-controller/kustomize-controller/helm-controller/
     notification-controller, `sync.kind: GitRepository`,
     `sync.path: workload/<region>-01`, `pullSecret: flux-github-pat`), and
     `kustomization.yaml` listing the bundle's manifests

3. **Per-cluster AWS resources** — `mgmt/aws/infrastructure/workload-resources/`
   (reconciled by the ACK controllers ON THE MANAGEMENT CLUSTER; namespace
   `ack-system`), one object per workload cluster, all annotated
   `services.k8s.aws/adoption-policy: adopt-or-create`:
   - `bucket.yaml`: `Bucket` (s3.services.k8s.aws/v1alpha1) named
     `data-<region>`, `spec.name: krops-120392301094-<region>-workload-data`,
     `createBucketConfiguration.locationConstraint: <region>`, ALL FOUR
     `publicAccessBlock` flags true, AES256 default encryption with
     `bucketKeyEnabled: true`, versioning `Enabled`,
     `ownershipControls` `BucketOwnerEnforced`, a bucket policy denying
     non-TLS requests (`DenyInsecureTransport`), krops tags. Clusters outside
     the controllers' default region (eu-north-1) carry the annotation
     `services.k8s.aws/region: <region>`
   - `dbinstance.yaml` / `role.yaml`: same per-cluster pattern (not part of
     this PR)

### Your task

Add everything needed for the new `eu-central-1-workload` cluster:

1. `mgmt/aws/addons/flux-apps/flux-apps-rset.yaml` — add the new input
   (`region: eu-central-1`, `cluster: eu-central-1-workload`). Do not change
   the existing inputs or the template.
2. `mgmt/aws/addons/flux-apps/regions/eu-central-1/` — the per-region
   bootstrap bag: `namespace.yaml`, `cluster-vars.yaml`
   (`AWS_REGION: "eu-central-1"`, `CLUSTER_NAME: "eu-central-1-workload"`,
   `AWS_ACCOUNT_ID: "120392301094"`), `flux-instance.yaml` (sync path
   `workload/eu-central-01`, same distribution/components as the existing
   bundles), and a `kustomization.yaml` for the bundle. NOTE: the regional
   pull-secret manifest (`flux-github-pat.sops.yaml`) is created by a
   follow-up operator step (SOPS re-encryption), so your bundle
   kustomization lists ONLY the three non-secret manifests.
3. `mgmt/aws/clusters/eu-central-1/` — the cluster definition following the
   pattern above: `kustomization.yaml`, `staging/kustomization.yaml`
   (`namePrefix: eu-central-1-`), `staging/capi-nameref.yaml`, and
   `staging/cluster.yaml` with a `Cluster` labeled `fluxcd: enabled` +
   `region: eu-central-1`, an `AWSManagedControlPlane` (region eu-central-1,
   version `"1.34.6"`), and ONE x86 node pool: `MachinePool` +
   `AWSManagedMachinePool` named `workload-x86` with `instanceType:
   t3.medium`, `replicas: 1`, `scaling: {minSize: 1, maxSize: 2}`,
   `amiType: AL2023_x86_64_STANDARD`, `capacityType: onDemand`.
4. `mgmt/aws/clusters/kustomization.yaml` — list the new region dir.
   (If you re-emit this file, list only the region dirs that exist in YOUR
   workspace — a minimal answer lists just `eu-central-1`.)
5. `mgmt/aws/clusters/flux-ks.yaml` — add the `eu-central-1` Flux
   Kustomization (same shape as the other two region Kustomizations).
   (The harness `flux build`s the Flux Kustomizations in your workspace:
   if you re-emit this file, include only the documents whose `spec.path`
   directory exists in your workspace — a minimal answer contains just the
   new `eu-central-1` document.)
6. `mgmt/aws/infrastructure/aws-clusters-resources/managedclusters.yaml` —
   add the `eu-central-1-workload` AWSManagedCluster.
7. `mgmt/aws/infrastructure/workload-resources/bucket.yaml` — add the
   `data-eu-central-1` Bucket for the new cluster, with the full
   secure-by-default posture described above (including the
   `services.k8s.aws/region: eu-central-1` annotation, since eu-central-1 is
   not the controllers' default region). Do not modify the existing buckets.

Return every added or changed file as a fenced YAML code block, each
preceded by its repo path in backticks (e.g. `` `mgmt/aws/clusters/kustomization.yaml` ``).
Files you do not return are assumed unchanged. Keep the whole tree
`kustomize build`-able and every Flux Kustomization `flux build`-able —
"whole tree" means the directories and files present in your workspace, so
do not list or reference region dirs you did not create.

### Context Files

{{scenario_spec}}
