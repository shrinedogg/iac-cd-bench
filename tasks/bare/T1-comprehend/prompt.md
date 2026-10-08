## Task: Predict `kubectl apply` behavior from a repo slice

**Stack:** bare (plain hand-authored Kubernetes manifests, `kubectl apply -f`, no delivery tooling)

You are given a slice of the `prod/` directory of a bare golden repo: plain
Kubernetes manifests deployed with `kubectl apply -f prod/`. There is no
Flux, no kustomize, no rendering pipeline, and no state store — `dev/` and
`prod/` are two independent, fully-written-out directories.

### Repo Slice (workspace)

```
prod/00-namespaces.yaml   # Namespace objects: clusters, infra, app
prod/app.yaml             # ServiceAccount + Deployment + Service for myapp-prod
prod/workers.yaml         # CAPI MachineDeployment + AWSMachineTemplate, 4x t3.large
prod/s3-bucket.yaml       # ACK Buckets: myapp-assets-prod + myapp-assets-prod-replica
```

The cluster already has these objects applied and running exactly as the
files describe (this is not a first-ever apply — `myapp-assets-prod`,
`myapp-assets-prod-replica`, the Deployment, and the MachineDeployment all
already exist with the identities in the seed files).

### Scenario

A PR is about to be merged and then applied with `kubectl apply -f prod/`.
It makes three changes:

1. `prod/workers.yaml`: the `MachineDeployment`'s `spec.replicas` is changed
   from `4` to `6`. `instanceType` is unchanged.
2. `prod/app.yaml`: the Deployment's `spec.selector.matchLabels` gains a new
   key, `version: v2`. `spec.template.metadata.labels` is left unchanged
   (still just `app: myapp, env: prod`).
3. `prod/s3-bucket.yaml`: the `myapp-assets-prod-replica` `Bucket` object is
   deleted from the file entirely — only `myapp-assets-prod` remains.

Nothing under `dev/` is touched by this PR.

### Questions

1. In what order does `kubectl apply -f prod/` process these four files, and
   why does that order happen to matter (or not matter) for this PR?
2. Does the workers.yaml replica change succeed when applied? What happens
   to the cluster as a result?
3. Does the app.yaml selector change succeed when applied to the
   already-existing Deployment? Explain what `kubectl apply` does (or
   doesn't do) when it hits that object, and what happens to the other
   three files in the same `kubectl apply -f prod/` invocation.
4. After this PR merges and `kubectl apply -f prod/` runs, does the
   `myapp-assets-prod-replica` Bucket object — and the AWS S3 bucket ACK
   created for it — still exist? Why or why not?
5. Does any part of this PR affect `dev/`, directly or indirectly?

### Context Files

{{scenario_spec}}
