# T1-comprehend Reference Answer Key

## Expected answers for knr-ops delivery behavior prediction

### Scenario: PR #42 merges three changes at once — RDS `instanceClass` bumped from
`db.t3.micro` to `db.t3.medium`, a new S3 bucket manifest added under `infra/s3/`,
and a prod-only overlay patch setting `replicas: 4` on the app deployment.

**Q1: What reconciles when PR #42 merges, and in what order?**
**Answer:** Flux's `GitRepository` source picks up the merge commit on its next
poll (or immediately via webhook), which marks every `Kustomization` that
watches the changed paths as needing reconciliation. The `infra` Kustomization
(covering `infra/s3/`, `infra/rds/`, `infra/iam/`) reconciles first since
nothing depends on it. The `dev` overlay Kustomization reconciles next. The
`prod` overlay Kustomization reconciles last because it declares `dependsOn:
[dev]` — Flux will not apply prod's Kustomization until dev's has reported a
ready/successful status. So the order is: infra (RDS + S3 + IAM) → dev overlay
→ prod overlay.

**Q2: Does the RDS instance get recreated? Why or why not?**
**Answer:** No. Changing `instanceClass` on an ACK `Instance` resource maps
directly to AWS's `ModifyDBInstance` API, which AWS performs as an in-place
resize: the instance keeps its identifier, storage, and endpoint, and AWS
swaps the underlying compute host under it. This causes a brief reboot/
downtime window (immediately, or deferred to the next maintenance window
depending on `applyImmediately`), but it is not a destroy-and-recreate.
ACK does not treat `instanceClass` as an immutable/replace-triggering field,
so Flux's reconciliation of the new spec results in a modify call, not a
delete+create.

**Q3: What happens to the S3 bucket if its manifest is deleted from Git?**
**Answer:** Because Flux's `Kustomization` prunes resources that disappear
from the rendered output (pruning is enabled by default for ACK-managed
Kustomizations in this repo), deleting the S3 bucket manifest causes Flux to
delete the corresponding `Bucket` custom resource on the next reconciliation.
The ACK S3 controller then deletes the underlying AWS bucket to match desired
state — unless the bucket is non-empty and lacks a force-delete annotation,
in which case the ACK controller's delete call fails and Flux reports the
Kustomization as not-ready until the conflict is resolved.

**Q4: How does Flux handle the prod-only replica change?**
**Answer:** The `replicas: 4` patch lives only in the `overlays/prod`
kustomization, so `kustomize build` only applies it when rendering the prod
overlay. The dev overlay's Kustomization renders `overlays/dev` and never
sees the patch, so dev's replica count is unaffected. Flux applies each
overlay's Kustomization independently against the resources that overlay
renders, so the change is scoped entirely to the prod Deployment.

**Q5: What role does the `dependsOn` relationship between kustomizations play?**
**Answer:** `dependsOn` is what enforces the ordering in Q1: the prod
Kustomization names `dev` in its `dependsOn` list, so Flux's controller
will not attempt to reconcile prod until the dev Kustomization has a current,
successful `Ready` condition. This gives a manual gate against rolling a
change to prod before it has landed (and reconciled cleanly) in dev — if dev
fails to reconcile, prod is never touched, regardless of what changed in the
prod overlay itself.
