# bare (plain kubectl) Documentation Snippets

## `kubectl apply -f <directory>`

`kubectl apply -f prod/` applies every manifest file in `prod/` to the
cluster. There is no controller loop and no reconciliation graph behind
this arm:

- Files in a directory are processed in **lexical (alphabetical) filename
  order**, not dependency order. There is no `dependsOn`, no health-check
  gate, and no waiting for one object to become ready before the next is
  applied.
- Each object in the file set is applied **independently**. If one object's
  apply is rejected by the API server, the others in the same invocation
  still apply — kubectl reports the failure for that object and moves on,
  it does not abort the whole batch.
- `dev/` and `prod/` are separate directories with separate, hand-duplicated
  files. Applying one directory never touches the other.

## Immutable fields

Some fields on Kubernetes objects can only be set at creation time; the API
server rejects any `kubectl apply` that tries to change them on an existing
object:

- `Deployment.spec.selector` (and the equivalent on `ReplicaSet`, `Job`,
  `StatefulSet`) — immutable once the object exists.
- `Service.spec.clusterIP` — immutable (for `ClusterIP` services with an
  assigned IP).
- `PersistentVolumeClaim.spec.storageClassName` / `.volumeName` — immutable.
- A rejected apply due to an immutable field does **not** trigger an
  automatic delete-and-recreate. The object simply keeps its old value
  until an operator deletes and re-applies it manually.

## Client-side vs. server-side apply

Plain `kubectl apply -f <file>` (no extra flags) uses **client-side apply**
by default:

- kubectl computes a three-way merge locally, using the
  `kubectl.kubernetes.io/last-applied-configuration` annotation it stores on
  the object to know what it applied last time.
- `kubectl apply --server-side` is a different mode: the API server itself
  tracks field ownership per "field manager" and resolves conflicts
  server-side. This arm's manifests don't require it, but nothing about
  plain YAML + kubectl prevents an operator from choosing it.

## No pruning by default

`kubectl apply -f <dir>` never deletes objects on its own:

- Removing a file from the directory, or deleting a resource block from a
  file, does not remove the corresponding object from the cluster. The
  object is simply no longer described by anything in the source directory
  — it is not "orphaned" by any tooling, because there is no tooling
  tracking ownership at that level.
- Deleting the object requires an explicit `kubectl delete -f <old-file>`
  (before the file is removed) or `kubectl apply -f <dir> --prune -l
  <selector>`, which needs every managed object labeled for the selector to
  work correctly. Neither is automatic.

## dev/prod duplication

Because there is no shared base or overlay mechanism, anything that differs
between `dev/` and `prod/` (replica counts, instance types, DB sizing, IAM
trust policy, bucket replication) is written out twice, by hand, in fully
independent files. A change meant to apply to both environments has to be
made in both places; there is no single edit that propagates.
