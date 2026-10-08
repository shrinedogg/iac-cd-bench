# T1-comprehend Reference Answer Key

## Expected answers for bare (kubectl apply) delivery behavior prediction

### Scenario: PR changes prod/workers.yaml replicas, prod/app.yaml selector, and removes the prod/s3-bucket.yaml replica bucket

**Q1: Processing order, and does it matter?**
**Answer:** `kubectl apply -f prod/` reads the directory and applies files in
lexical filename order: `00-namespaces.yaml`, `app.yaml`, `s3-bucket.yaml`,
`workers.yaml`. There is no dependency graph — kubectl has no concept of
`dependsOn` or reconciliation ordering. The `00-` prefix on the namespaces
file is a manual naming convention specifically so it sorts first, since
every other object in these files lives in the `clusters`, `infra`, or `app`
namespace it creates. For this particular PR the order doesn't otherwise
matter: none of the three changed files depend on each other at apply time.

**Q2: Does the workers.yaml replica change succeed?**
**Answer:** Yes. `spec.replicas` on a `MachineDeployment` is a mutable
field. `kubectl apply` sends a merge patch that updates it in place — the
`MachineDeployment` object itself is not replaced or recreated. The CAPI
machine-deployment controller then reconciles toward 6 replicas, which
(outside of kubectl's own responsibility) results in 2 additional worker
nodes being provisioned over time.

**Q3: Does the app.yaml selector change succeed?**
**Answer:** No. `spec.selector` on an existing `Deployment` is immutable
after creation — the Kubernetes API server rejects any attempt to change it
with a "field is immutable" validation error. `kubectl apply -f prod/`
applies each object independently: this one object fails, but the other
three files (`00-namespaces.yaml`, `s3-bucket.yaml`, `workers.yaml`) are
unaffected and still apply successfully. Unlike a tool that diffs and
replaces destructively, plain `kubectl apply` does not delete-and-recreate
the Deployment automatically — the operator has to do that by hand
(`kubectl delete deployment myapp-prod -n app` then re-apply) if the
selector change is actually wanted.

**Q4: Does the replica bucket still exist afterward?**
**Answer:** Yes, both the `myapp-assets-prod-replica` Bucket object in the
cluster and the AWS S3 bucket ACK manages for it still exist. Plain
`kubectl apply -f` performs no pruning by default — removing a manifest
from the source directory does not delete the corresponding object from the
cluster. Deleting it would require an explicit `kubectl delete -f
<old-file>` (or `--prune`) that this PR doesn't include.

**Q5: Does this PR affect dev/?**
**Answer:** No. `dev/` and `prod/` are two fully independent, hand-written
directories with their own resource names (`myapp-dev-*` vs `myapp-prod-*`)
and no shared base or overlay mechanism. `kubectl apply -f prod/` only ever
touches the files given to it; it has no way to know `dev/` exists.
