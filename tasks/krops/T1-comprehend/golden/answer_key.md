# T1-comprehend Reference Answer Key (krops)

## Expected answers for krops delivery-behavior prediction

### Scenario: PR #87 merges four changes at once — the eu-west-1 RDS class
bump, a new eu-central-1 workload cluster (region bundle + ResourceSet input
+ AWSManagedCluster + cluster dir + Flux Ks), the eu-north-1 MachinePool
scale-up, and a new `cluster-vars` key for eu-west-1.

**Q1: What reconciles when PR #87 merges, and in what order?**

**Answer:** The management cluster's GitRepository source picks up the merge
on its next poll (or via webhook), and every Flux Kustomization whose
`spec.path` overlaps the changed tree is re-queued. What actually lands:

- `workload-resources` re-renders `mgmt/aws/infrastructure/workload-resources`
  and SSA-applies the changed `db-eu-west-1` DBInstance. It is gated by
  `dependsOn: [ack-controllers]`, and `ack-controllers` runs with
  `wait: true`, so this Kustomization only reconciles once the ACK HelmReleases
  (and therefore the `rds.services.k8s.aws` CRDs) are Ready.
- `eu-north-1` (the per-region cluster Ks in `clusters/flux-ks.yaml`)
  re-renders `mgmt/aws/clusters/eu-north-1` and applies the changed
  MachinePool. It is gated by `dependsOn: [capa-system]`, which itself
  health-checks the CAPA CRDs (`awsmanagedclusters...`,
  `awsmanagedcontrolplanes...`) before it reports Ready.
- `eu-central-1` (new Ks in `clusters/flux-ks.yaml`) reconciles the new
  cluster directory, also gated on `capa-system`.
- `aws-managed-clusters` re-applies the changed
  `aws-clusters-resources/managedclusters.yaml` (new
  `eu-central-1-workload` AWSManagedCluster). It is gated on `capa-identity`,
  which is gated on `capi-operator`, which is gated on `cert-manager` — the
  bootstrap spine of the management cluster.
- `flux-apps` re-renders `mgmt/aws/addons/flux-apps` and applies the changed
  ResourceSet. It is gated by `dependsOn: [caaph-system]` — the CAPI addons
  (AddonProvider helm) Kustomization, which health-checks the
  `helmchartproxies` CRD — so the `fluxcd.controlplane.io/v1` ResourceSet API
  and the addon provider exist before the ResourceSet is applied.
- Nothing else is affected: the unchanged Kustomizations re-render and
  SSA-apply identical output (no-ops).

Ordering in short: cert-manager → capi-operator → capa-identity →
aws-managed-clusters (one spine); capi-system → capa-system → {eu-north-1,
eu-central-1} (another); caaph-system → flux-apps (third); ack-controllers →
workload-resources (fourth). The spines are independent of each other except
where `dependsOn` says otherwise, so Flux interleaves them as readiness
permits.

**Q2: Does the eu-west-1 RDS instance get recreated?**

**Answer:** No. `dbInstanceClass` is a mutable field of the ACK RDS
`DBInstance` v1alpha1 API: the controller calls `ModifyDBInstance` and AWS
resizes the instance in place (a brief failover/restart window is possible,
`applyImmediately`-dependent). The instance keeps its
`dbInstanceIdentifier` (`krops-eu-west-1-workload-db`), storage, and
endpoint. ACK only replaces a DBInstance for primary-key-level changes such
as the identifier itself. Note the CR also carries
`services.k8s.aws/region: eu-west-1`, which routes the Modify call to
eu-west-1 even though the controller's default region is eu-north-1 — so the
change lands on the right instance.

**Q3: What appears on the management cluster for eu-central-1, and what
happens on the new workload cluster?**

**Answer:** The `flux-apps` Kustomization applies the updated ResourceSet;
the flux-operator (running on the management cluster) re-renders
`resourcesTemplate` once per input. For the new input it creates a NEW
Kustomization `flux-apps-eu-central-1` in namespace `default` on the
management cluster, whose `spec.kubeConfig.secretRef` is
`eu-central-1-workload-kubeconfig` — the CAPI kubeconfig Secret CAPA creates
for the Cluster (the Cluster is named `workload` in Git but the staging
kustomization's `namePrefix: eu-central-1-` renders it
`eu-central-1-workload`). The management cluster's kustomize-controller then
remote-applies `./mgmt/aws/addons/flux-apps/regions/eu-central-1` against
that kubeconfig (with SOPS decryption), landing the namespace, the
`cluster-vars` ConfigMap, the pull secret, and the `FluxInstance` in
`flux-system` on the eu-central-1 workload cluster. The
`HelmChartProxy flux-operator` (installed by the `flux-apps` kustomize tree)
has already installed the flux-operator on the cluster because the Cluster
carries `labels: {fluxcd: enabled}`; the flux-operator then applies the Flux
distribution named by the FluxInstance, and that Flux instance's
GitRepository starts syncing `workload/eu-central-01` from this repo.

The two EXISTING inputs are re-rendered and their rendered Kustomizations
(`flux-apps-eu-north-1`, `flux-apps-eu-west-1`) re-applied via SSA — identical
output, so no-op; nothing is recreated or re-triggered on those workload
clusters.

**Q4: What does CAPA do about the eu-north-1 MachinePool change?**

**Answer:** CAPA updates the EKS **managed node group** in place:
`AWSManagedMachinePool` maps to an EKS Nodegroup, so changing
`spec.template.spec...` + `replicas`/`scaling` results in
`UpdateNodegroupConfig` (desiredSize, max size) — nodes are added to the
ASG. The control plane, the cluster, and the other node pool are untouched;
there is no cluster recreation and no data-plane teardown.

**Q5: How does TELEMETRY_ENABLED reach the eu-west-1 workload cluster, and
does the management flux-apps Kustomization apply anything new?**

**Answer:** The `regions/` tree is NOT part of any Flux Kustomization's
kustomize build on the management cluster — `flux-apps`'s `spec.path` is
`./mgmt/aws/addons/flux-apps`, whose kustomization lists only
`flux-operator.yaml` and `flux-apps-rset.yaml`. So the management `flux-apps`
Ks applies nothing new: it re-renders the (unchanged) ResourceSet and
no-ops. The new key reaches the workload cluster via the ResourceSet's
rendered Kustomization `flux-apps-eu-west-1`, whose `spec.path` is
`./mgmt/aws/addons/flux-apps/regions/eu-west-1`: on its next reconcile
(interval 5m, or a re-trigger because the ResourceSet changed) it
remote-applies the changed bundle, updating the `cluster-vars` ConfigMap in
`flux-system` on the workload cluster in place (server-side apply of the same
object — the ConfigMap is modified, not replaced).
