## Task: Semantic prediction quiz — krops delivery behavior

**Stack:** krops (Flux + CAPI/CAPA + ACK)

You are given the live state of a krops repository slice (below). Answer the
questions about what the toolchain will ACTUALLY do. These are questions
about runtime semantics, not syntax.

### Repo files

The workspace contains:

- `mgmt/aws/infrastructure/flux-ks.yaml` — eight Flux Kustomizations that
  reconcile the management cluster: `cert-manager`, `capi-operator`
  (dependsOn cert-manager), `capa-identity` (dependsOn capi-operator),
  `aws-managed-clusters` (dependsOn capa-identity), `ack-controllers`
  (no deps, `wait: true`), `workload-resources` (dependsOn
  ack-controllers), `aws-global-iam` (dependsOn ack-controllers),
  `konflate` (no deps, SOPS decryption)
- `mgmt/aws/capi-providers/flux-ks.yaml` — `capi-system` (dependsOn
  capi-operator, `wait: true`), `capa-system` (dependsOn capi-system,
  health-checks the CAPA CRDs), `caaph-system` (dependsOn capi-system,
  health-checks the HelmChartProxy CRD)
- `mgmt/aws/addons/flux-ks.yaml` — `flux-apps` (dependsOn caaph-system, SOPS
  decryption, path `./mgmt/aws/addons/flux-apps`)
- `mgmt/aws/clusters/flux-ks.yaml` — `eu-north-1` and `eu-west-1` (both
  dependsOn capa-system, prune: true)
- `mgmt/aws/addons/flux-apps/flux-apps-rset.yaml` — the templated ResourceSet
  with inputs for `eu-north-1` / `eu-west-1`
- `mgmt/aws/infrastructure/workload-resources/dbinstance.yaml` — ACK
  DBInstances `db-eu-north-1` and `db-eu-west-1` (namespace `ack-system`,
  `adopt-or-create`, eu-west-1 carrying `services.k8s.aws/region`)
- `mgmt/aws/infrastructure/workload-resources/bucket.yaml` — ACK Buckets
  `data-eu-north-1` and `data-eu-west-1` (same annotations)

Read the actual files in the workspace before answering.

### Questions

Q1. A commit changes `dbInstanceClass` from `db.t4g.micro` to `db.m6g.large`
on the `db-eu-west-1` DBInstance. After Flux reconciles and the ACK RDS
controller applies the change, is the RDS instance REPLACED (destroyed and
recreated with a new identity) or UPDATED IN-PLACE (same instance identity,
possibly with a brief downtime window)?

Q2. A commit removes the `eu-west-1` entry from the ResourceSet's
`spec.inputs` (nothing else changes; the cluster itself and everything in
`mgmt/aws/clusters/` stays). (a) Who/what deletes the Kustomization
`flux-apps-eu-west-1` that the ResourceSet had rendered into the management
cluster? (b) After that deletion, does the eu-west-1 workload cluster's Flux
bootstrap bundle — the namespace, `cluster-vars`, pull secret and
`FluxInstance` it applied into `flux-system` on the workload cluster —
survive there?

Q3. The `data-eu-north-1` Bucket manifest is removed from Git and the commit
merges. Given the manifests as written (no deletion-policy annotation) and
the ACK default deletion policy, does the AWS S3 bucket
`krops-120392301094-eu-north-1-workload-data` still exist in AWS afterwards?

Q4. During bootstrap the disposable kind cluster applies these same
Bucket/DBInstance CRs (creating the real AWS resources), then a
`clusterctl move` pivot moves Flux to the management cluster, whose Flux
re-applies the identical CRs. The AWS resources already exist. What does the
post-pivot ACK controller do, given the annotations on these manifests?

Q5. A commit touches ONLY
`mgmt/aws/addons/flux-apps/regions/eu-west-1/cluster-vars.yaml` (adds a new
key to the ConfigMap). Given the `flux-apps` Kustomization spec as written,
does that Kustomization apply or modify any object on the management cluster
as a result?

Q6. A commit adds a new ResourceSet input
`{region: eu-central-1, cluster: eu-central-1-workload}` plus a matching
`regions/eu-central-1/` bundle; the CAPI Cluster `eu-central-1-workload`
exists and CAPA has created its kubeconfig Secret. (a) What new object
appears in the management cluster as a result (name and kind)? (b) Which
controller is it that applies the bundle to the workload cluster?

Q7. On a fresh management-cluster bootstrap, in what order do these
Kustomizations first reconcile, given the `dependsOn` chains in the seeded
files: `eu-north-1`, `capi-system`, `cert-manager`, `capa-system`,
`capi-operator`?

### Answer format

Return ONLY a fenced JSON code block named `answers.json` in exactly this
shape (keys q1..q7, values as specified):

```json
{
  "q1": "replaced | updated-in-place",
  "q2": {"deleted_by": "<who/what deletes the rendered Kustomization>", "bundle_survives": true},
  "q3": "exists | deleted",
  "q4": "<short phrase: what the post-pivot controller does>",
  "q5": {"changes": true, "reason": "<short>"},
  "q6": {"new_object": "<kind name>", "applied_by": "<controller>"},
  "q7": ["first", "second", "third", "fourth", "fifth"]
}
```

Replace each placeholder with your answer. For q5 `changes` answer with a
JSON boolean (true/false). For q7 list the five Kustomization names in
first-reconcile order.

### Context Files

{{scenario_spec}}
