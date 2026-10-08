# Golden answers — bare T6-semantics

```json
{
  "q1": "updated-in-place",
  "q2": {"deleted": false, "reason": "kubectl apply -f has no prune step by default; removing a file from the source directory does not remove the corresponding object from the cluster"},
  "q3": "exists",
  "q4": {"succeeds": false, "reason": "spec.selector is immutable on an existing Deployment; the API server rejects changes to it after creation"},
  "q5": "client-side",
  "q6": {"namespace_first": true, "reason": "kubectl apply -f <dir> processes files in lexical filename order; the 00- prefix makes 00-namespaces.yaml sort first"},
  "q7": {"blocked": false, "reason": "plain kubectl apply has no dependsOn/health-check gating mechanism; each apply just processes the given files independently of any other directory's state"}
}
```

## Rationale

- **q1**: `dbInstanceClass` is a mutable RDS attribute; ACK issues
  ModifyDBInstance. The instance identity (`dbInstanceIdentifier`) is
  unchanged — in-place update, possibly with a reboot, never replacement.
- **q2**: `kubectl apply -f <dir>` with no `--prune` flag never deletes
  objects that are absent from the given files — it only creates/updates
  what's present. Removing a file from the directory has zero effect on
  objects already in the cluster.
- **q3**: Since the Bucket custom resource in the cluster was never
  deleted (no prune happened), ACK's reconciler was never told to delete
  anything — the AWS S3 bucket continues to exist exactly as before.
- **q4**: `spec.selector` on an existing Deployment is immutable; the API
  server rejects the apply for that object with a "field is immutable"
  error. The other objects in the same `kubectl apply -f` invocation are
  unaffected.
- **q5**: Plain `kubectl apply -f <file>` (no `--server-side` flag) is
  client-side apply by default — it computes a three-way merge locally
  using the `kubectl.kubernetes.io/last-applied-configuration` annotation.
- **q6**: kubectl has no dependency graph; it processes files in a
  directory in lexical/alphabetical filename order. The `00-` prefix on
  `00-namespaces.yaml` is a manual naming convention specifically to
  guarantee it sorts and applies first.
- **q7**: There is no controller, no `dependsOn`, and no health-check gate
  in plain kubectl. `kubectl apply -f prod/` only knows about the files
  given to it; it has no mechanism to check or wait on anything in `dev/`.
