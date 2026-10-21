# T6-semantics Golden Answer Key (krops)

Golden answers for the quiz — these must pass the grader 7/7
(`tools/validate_t6.py`).

```json
{
  "q1": "updated-in-place",
  "q2": {"deleted_by": "resource-set-operator", "bundle_survives": false},
  "q3": "deleted",
  "q4": "adopts the existing AWS resource (adopt-or-create) instead of failing with Resource already exists",
  "q5": {"changes": false, "reason": "the regions/ tree is not part of the flux-apps Kustomization's path (./mgmt/aws/addons/flux-apps); its kustomize build renders only flux-operator.yaml and flux-apps-rset.yaml, both unchanged — the new key reaches the workload cluster via the rendered flux-apps-eu-west-1 Kustomization, not this one"},
  "q6": {"new_object": "Kustomization flux-apps-eu-central-1", "applied_by": "kustomize-controller"},
  "q7": ["cert-manager", "capi-operator", "capi-system", "capa-system", "eu-north-1"]
}
```

## Rationale (grounding for each answer)

**Q1 — updated-in-place.** `dbInstanceClass` is a mutable field of the ACK
RDS `DBInstance` v1alpha1 API: the controller issues `ModifyDBInstance` and
AWS resizes the instance in place (brief failover/restart possible). The
primary key is `dbInstanceIdentifier`, which is unchanged here, so no
replacement. (Public ACK docs + krops' own double-apply design rely on this
mutability.)

**Q2 — resource-set-operator / false.** The flux-operator's ResourceSet
controller performs garbage collection of resources it previously generated
that are no longer present in the current revision — tracking them in
`.status.inventory` and deleting the diff (staged: Flux custom resources
first, waiting for their finalizers, then the rest). So removing the input
makes the operator itself delete the rendered `flux-apps-eu-west-1`
Kustomization — not the `flux-apps` management Ks (whose rendered output is
unchanged) and not "no one". Because the rendered Kustomization carries
`prune: true` (and Flux's default deletionPolicy is MirrorPrune), deleting a
Kustomization triggers removal of every object it had applied on the target
cluster — and its target is the eu-west-1 workload cluster via the CAPI
kubeconfig Secret. The bootstrap bag (namespace, cluster-vars, pull secret,
FluxInstance) is pruned from the workload cluster, and deleting the
FluxInstance makes the flux-operator GC the Flux distribution it had
installed. `bundle_survives: false`.

**Q3 — deleted.** The `workload-resources` Kustomization has `prune: true`,
so the removed Bucket manifest → CR deleted from the cluster. The CR carries
no `services.k8s.aws/deletion-policy` annotation, and ACK's default deletion
policy is `delete`: the controller deletes the underlying AWS S3 bucket
before removing the CR. (A `retain` annotation would have orphaned the
bucket.)

**Q4 — adopts.** Every workload CR carries
`services.k8s.aws/adoption-policy: adopt-or-create`. On the post-pivot
re-apply, the AWS resources already exist (created by the kind bootstrap
cluster); with adopt-or-create the controller adopts the existing resource
instead of going Terminal with "Resource already exists", and afterwards the
manifest is the source of truth. This is exactly why krops can apply the
same CRs twice per bootstrap.

**Q5 — false.** The `flux-apps` Kustomization's `spec.path` is
`./mgmt/aws/addons/flux-apps`, whose kustomization lists only
`flux-operator.yaml` and `flux-apps-rset.yaml`. The `regions/` subtree is a
ResourceSet *render template* — it is never part of that kustomize build, so
a change under `regions/eu-west-1/` changes nothing the `flux-apps` Ks
renders: it re-applies the identical HelmChartProxy + ResourceSet (no-op).
The changed ConfigMap reaches the workload cluster on the next reconcile of
the *rendered* `flux-apps-eu-west-1` Kustomization (in-place ConfigMap
update in `flux-system` there).

**Q6 — Kustomization flux-apps-eu-central-1 / kustomize-controller.** The
ResourceSet re-renders its template per input; the new input produces a new
Kustomization named `flux-apps-eu-central-1` in namespace `default` on the
management cluster (the rendered objects land where the operator runs, in
the namespace each declares). That Kustomization's `spec.kubeConfig.secretRef`
is `eu-central-1-workload-kubeconfig`, so the management cluster's
kustomize-controller remote-applies `./mgmt/aws/addons/flux-apps/regions/eu-central-1`
against the workload cluster.

**Q7 — cert-manager, capi-operator, capi-system, capa-system, eu-north-1.**
`dependsOn` edges in the seeded files: `capi-operator` ← `cert-manager`;
`capi-system` ← `capi-operator`; `capa-system` ← `capi-system` (and
health-checks the CAPA CRDs before reporting Ready); `eu-north-1` ←
`capa-system`. That chain is the first-reconcile order on a fresh bootstrap.
