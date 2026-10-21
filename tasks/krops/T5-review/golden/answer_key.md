# T5-review Reference Answer Key (krops)

## Expected findings for PR #91 ("onboard eu-central-1 + production-grade eu-west-1")

### CRITICAL (merge-blocking)

**1. Plaintext database password committed to Git.**
`db-password.yaml` adds a plain `Secret` (`stringData.password:
Sup3r-S3cret!2026`) in namespace `ack-system`, and the new `db-eu-central-1`
DBInstance references it via `spec.masterUserPassword`. Two independent
violations of the repo's secret model:
- The repo's RDS idiom is `manageMasterUserPassword: true` (RDS generates
  the password; Secrets Manager holds it). A DB master password should
  never be authored in Git at all.
- The file is not named `*.sops.yaml`, so SOPS' creation rule does not even
  match it — the value sits in Git in plaintext forever, and every clone /
  konflate render exposes it. Once merged it must be rotated, the file
  deleted, and the CR reverted to `manageMasterUserPassword: true`.

**2. The new eu-central-1 data bucket is publicly readable.**
- `blockPublicPolicy: false` while the other two buckets set all four
  `publicAccessBlock` flags true — combined with the policy below, the
  bucket accepts public access policy.
- The `PublicRead` statement grants `s3:GetObject` to `Principal: {"AWS":
  "*"}` on `krops-120392301094-eu-central-1-workload-data/*` — anyone on the
  internet can read every object.
- The `DenyInsecureTransport` statement the other two buckets carry is
  absent, so TLS-only access is not enforced either.
Remediation: all four block flags true, drop the `PublicRead` statement
entirely (or replace with the scoped read grant for the reader role),
restore `DenyInsecureTransport`.

### HIGH

**3. The new reader role trusts any AWS account and is over-privileged.**
- `assumeRolePolicyDocument` uses `"Principal": {"AWS":
  "arn:aws:iam::*:root"}` — the wildcard account. The repo convention (and
  its own comment) is that the trust principal must be the single account
  root `arn:aws:iam::120392301094:root`; a wildcard trust lets any other AWS
  account that can call `sts:AssumeRole` here assume the role.
- The inline policy grants `s3:*` and `rds:*` on `krops-*` — wildcard
  actions, including destructive ones (DeleteBucket, DeleteDBInstance,
  PutLifecycleConfiguration...), where the existing roles grant only
  explicit read-only actions (s3:Get*/List*, rds:DescribeDBInstances,
  rds:ListTagsForResource).
Remediation: scope the trust to `arn:aws:iam::120392301094:root` and reduce
the inline policy to the same read-only action set as `reader-eu-north-1`.

### WARNING (operational, not directly a security hole — but must be fixed
before merge)

**4. The new bucket omits `services.k8s.aws/region: eu-central-1`.**
The ACK controllers' default region is eu-north-1 (set in the HelmRelease
values); non-default-region CRs carry the region annotation (both existing
eu-west-1 CRs do, and the new DBInstance in this same diff does). Without
it, the bucket is created in eu-north-1, contradicting its own
`locationConstraint`/naming and the cluster it is for.

**5. The new bucket omits `services.k8s.aws/adoption-policy:
adopt-or-create`.**
Every other CR in this tree carries it because the manifests are applied
twice per bootstrap (kind bootstrap cluster, then post-pivot management
Flux). Without it the post-pivot reconcile goes Terminal with "Resource
already exists" and the bucket stays unmanaged.

### NOT problems (do not flag)

- The `db-eu-west-1` class change `db.t4g.micro` → `db.m6g.large` +
  `multiAZ: true` — a legitimate production-grade sizing change;
  `dbInstanceClass` is mutable in ACK, so it is an in-place
  `ModifyDBInstance`, and `manageMasterUserPassword: true` is preserved.
- The eu-west-1 `workload-arm` MachinePool scale-up (3→5, maxSize 5→8) —
  in-place scaling of the EKS managed node group.

### Severity ranking (expected)

1. Plaintext DB credential (critical) — committed secret material, breaks
   the no-passwords-in-Git invariant.
2. Public S3 exposure (critical) — data exfiltration path on merge.
3. Wildcard IAM trust + wildcard permissions (high) — privilege escalation
   surface across accounts.
4. Missing region annotation / missing adoption annotation (warning) —
   wrong-region resource and unmanageable bootstrap.

### Acceptable remediation summary

- Delete `db-password.yaml`; set `manageMasterUserPassword: true` on
  `db-eu-central-1`; rotate anything ever using the committed password.
- Set all four `publicAccessBlock` flags true, delete the `PublicRead`
  statement, restore `DenyInsecureTransport`.
- Trust `arn:aws:iam::120392301094:root`; inline policy = the existing
  read-only statement set.
- Add `services.k8s.aws/region: eu-central-1` and
  `services.k8s.aws/adoption-policy: adopt-or-create` to the new bucket.
