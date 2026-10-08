# T5-review Reference Answer Key

## Expected security flags for the bare (kubectl apply) PR diff review

### Issues to identify (ranked by severity):

**CRITICAL (P0): Plaintext database password committed in `prod/db-secret.yaml`**
- The documented stub value (`REPLACE_OUT_OF_BAND_NEVER_COMMIT_REAL_VALUE`)
  is replaced with a real-looking plaintext password, committed directly to
  Git.
- **Impact:** Database credentials exposed in Git history to anyone with
  repo access, permanently (rotating the password doesn't remove it from
  history).
- **Fix:** Revert to the placeholder pattern; supply the real value
  out-of-band immediately before `kubectl apply` (e.g.
  `kubectl create secret ... --dry-run=client -o yaml | kubectl apply -f -`),
  never committed to Git.

**CRITICAL (P0): Public read access on the prod assets bucket in `prod/s3-bucket.yaml`**
- The `publicAccessBlock` (all four flags) is removed entirely, and a bucket
  policy is added granting `s3:GetObject` to `Principal: "*"` — anyone on
  the internet can read every object in the bucket.
- **Impact:** Public exposure of production application assets; combined
  with the removed access block, this also removes the safety net against
  any *future* accidental public grant on this bucket.
- **Fix:** Restore the `publicAccessBlock` with all four flags `true`;
  remove the public-read bucket policy entirely, or scope it to a specific,
  justified principal if public read is genuinely intended for some subset
  of objects.

**HIGH (P1): Production RDS instance made publicly accessible with deletion protection disabled in `prod/rds.yaml`**
- `publiclyAccessible` flips from `false` to `true` and `deletionProtection`
  flips from `true` to `false`, together.
- **Impact:** The database becomes reachable from outside the VPC, and an
  accidental `kubectl delete` or bad `kubectl apply` can now permanently
  destroy the production database with no safeguard.
- **Fix:** Set `publiclyAccessible: false` and `deletionProtection: true`,
  matching the values this PR removes.

**MEDIUM (P2): Missing container resource requests/limits in `prod/app.yaml`**
- The `resources` block (requests and limits) is removed from the `myapp`
  container.
- **Impact:** No CPU/memory guarantees or ceilings — a single misbehaving
  pod can starve other workloads on the same node, and there's no signal to
  the scheduler about what this workload needs.
- **Fix:** Restore `resources.requests` (`cpu: 100m`, `memory: 128Mi`) and
  `resources.limits` (`cpu: 500m`, `memory: 256Mi`).

### Severity ranking

1. Plaintext database password (CRITICAL)
2. Public S3 bucket read access (CRITICAL)
3. RDS publicly accessible + deletion protection disabled (HIGH)
4. Missing container resource requests/limits (MEDIUM)

### Expected remediation steps

1. Revert `prod/db-secret.yaml` to the placeholder pattern; never commit a
   real credential value.
2. Restore `publicAccessBlock` in `prod/s3-bucket.yaml` and drop the public
   bucket policy.
3. Restore `publiclyAccessible: false` and `deletionProtection: true` in
   `prod/rds.yaml`.
4. Restore the `resources` block on the `myapp` container in
   `prod/app.yaml`.
