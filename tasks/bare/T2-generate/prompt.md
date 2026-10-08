## Task: Add a new ACK-managed S3 bucket for application logs

**Stack:** bare (plain hand-authored Kubernetes manifests, `kubectl apply -f`, no delivery tooling)

You are given a bare golden repo: plain Kubernetes manifests applied with
`kubectl apply -f dev/` and `kubectl apply -f prod/`. There is no Flux, no
kustomize, no rendering pipeline. `dev/` and `prod/` are two independent,
fully-written-out directories — nothing is shared or overlaid between them.

### Current State

Each of `dev/` and `prod/` already has (among other files):
- `s3-bucket.yaml` — an ACK `Bucket` (`s3.services.k8s.aws/v1alpha1`) for
  application assets, named `myapp-assets-{env}`
- `iam.yaml` — ACK IAM resources (`iam.services.k8s.aws/v1alpha1`) for the
  app's service account: a `User` + `Policy` in dev, a `Role` + `Policy` in
  prod

### Your Task

Add a new S3 bucket for application logs to **both** `dev/` and `prod/`:

1. Bucket name: `myapp-logs-{env}` (`myapp-logs-dev` / `myapp-logs-prod`)
2. Versioning enabled
3. Server-side encryption with AES256
4. Public access fully blocked (all four `publicAccessBlock` flags `true`)
5. Grant the existing per-environment service principal (the dev `User`'s
   policy, the prod `Role`'s policy) `s3:GetObject` and `s3:PutObject` on
   this new bucket only — do not widen access to the existing assets bucket
6. Because this arm has no shared base or overlay, the bucket and the
   permission grant must each be added **independently** in `dev/` and in
   `prod/` — an edit in one directory has no effect on the other

Write each new/changed manifest as its own fenced code block, preceded by
its file path in backticks, e.g.:

`dev/logs-bucket.yaml`
```yaml
...
```

Suggested file layout: a new `logs-bucket.yaml` in each of `dev/` and
`prod/` for the bucket, and either a new `logs-policy.yaml` in each
directory or an edit to the existing `iam.yaml` in each directory for the
permission grant — either is acceptable as long as the resulting policy
document only allows access to the logs bucket.

### Context Files

{{scenario_spec}}
