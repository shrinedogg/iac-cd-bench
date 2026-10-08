## Task: Semantic prediction quiz — plain `kubectl apply` behavior

**Stack:** bare (plain hand-authored Kubernetes manifests, `kubectl apply -f`, no delivery tooling)

You are given a slice of a bare golden repo. There is no Flux, no
kustomize, no rendering pipeline, and no state store — every manifest is
applied directly with `kubectl apply -f dev/` or `kubectl apply -f prod/`,
and the cluster's actual state is whatever the most recent `kubectl apply`
left behind. Answer the questions about what plain `kubectl` actually does,
not what a GitOps controller would do — there is no controller here.

### Repo files (workspace)

- `dev/` — 9 files: `00-namespaces.yaml`, `app.yaml`, `cluster.yaml`,
  `controlplane.yaml`, `db-secret.yaml`, `iam.yaml`, `rds.yaml`,
  `s3-bucket.yaml`, `workers.yaml`
- `prod/rds.yaml` — ACK `DBInstance` `myapp-prod-db`, `dbInstanceClass:
  db.t3.medium`
- `prod/s3-bucket.yaml` — ACK `Bucket`s `myapp-assets-prod` and
  `myapp-assets-prod-replica`
- `prod/app.yaml` — `Deployment` `myapp-prod` with
  `spec.selector.matchLabels: {app: myapp, env: prod}` matching
  `spec.template.metadata.labels`, already created and running in the
  cluster exactly as the file describes

### Questions

Q1. A commit changes `prod/rds.yaml`'s `spec.dbInstanceClass` from
`db.t3.medium` to `db.r5.large`, and `kubectl apply -f prod/` runs. After
the ACK rds-controller reconciles, is the underlying AWS RDS instance
REPLACED (destroyed and recreated with a new identity) or UPDATED IN-PLACE
(same instance identity, possibly with downtime/reboot)?

Q2. `prod/s3-bucket.yaml` is deleted entirely from the repo, and someone
runs `kubectl apply -f prod/` again (no `--prune` flag, no `-l` selector).
Are the `myapp-assets-prod` and `myapp-assets-prod-replica` Bucket objects
removed from the cluster as a result? Why or why not?

Q3. Following Q2 — is the AWS S3 bucket `myapp-assets-prod` still there
afterward?

Q4. `prod/app.yaml`'s Deployment already exists in the cluster with
`spec.selector.matchLabels: {app: myapp, env: prod}`. A commit edits ONLY
the selector to add `tier: backend`, leaving
`spec.template.metadata.labels` unchanged. When `kubectl apply -f
prod/app.yaml` runs against the existing Deployment, does it succeed?

Q5. Plain `kubectl apply -f dev/` (no extra flags) — does it use
CLIENT-SIDE apply (a local three-way merge using the
`kubectl.kubernetes.io/last-applied-configuration` annotation) or
SERVER-SIDE apply (field-manager-based ownership tracked by the API
server)?

Q6. `kubectl apply -f dev/` is run against the 9 files listed above. In what
order does kubectl process them, and does the `Namespace` object end up
applied before the namespaced objects that reference `clusters`, `infra`,
and `app`?

Q7. `dev/` and `prod/` are two entirely separate directories with
independently named resources (`myapp-dev-*` / `myapp-prod-*`). If a change
is applied via `kubectl apply -f prod/` only, is there any mechanism (health
check gate, `dependsOn`, reconciliation controller) that could block or
delay this apply based on the current state of `dev/`'s resources?

### Answer format

Return ONLY a fenced JSON code block named `answers.json` in exactly this
shape (keys q1..q7, values as specified):

```json
{
  "q1": "replaced | updated-in-place",
  "q2": {"deleted": "<true|false>", "reason": "<short>"},
  "q3": "exists | deleted",
  "q4": {"succeeds": "<true|false>", "reason": "<short>"},
  "q5": "client-side | server-side",
  "q6": {"namespace_first": "<true|false>", "reason": "<short>"},
  "q7": {"blocked": "<true|false>", "reason": "<short>"}
}
```

Replace each placeholder with your answer. For q2 `"deleted"`, q4
`"succeeds"`, q6 `"namespace_first"`, and q7 `"blocked"`, answer with JSON
booleans (true/false).

### Context Files

{{scenario_spec}}
