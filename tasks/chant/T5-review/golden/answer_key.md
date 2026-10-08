# T5-review Reference Answer Key

## Expected security flags for the chant prod `database`/`reader` PR diff

### Issues to identify (ranked by severity):

**CRITICAL (P0): Plaintext database password hardcoded into `masterUserPassword`**
- The diff drops the `SecretRef`/`secretRef()` plumbing entirely and sets
  `spec.masterUserPassword: "Prod2026!DbPass"` — a real-looking literal
  string, committed directly to Git, on the raw `new DBInstance({...})`.
- **Impact:** Database credentials exposed in Git history to anyone with
  repo access, permanently (rotating the password afterward doesn't remove
  it from history). This is exactly the class of value chant's referenced-
  provenance model (`SecretRef` has no field that can hold material) exists
  to make structurally impossible through `PostgresInstance` — this diff
  routes around that guarantee by not using the composite at all.
- **Fix:** Restore a `SecretRef` (name/namespace/key pointing at a Secret
  created out of band) and pass it through `secretRef()` as
  `masterUserPassword`, the same way `PostgresInstance` does internally —
  or, more directly, restore the `PostgresInstance({...})` call and let the
  composite handle it.

**CRITICAL (P0): Production database made publicly accessible with deletion protection disabled**
- The raw `DBInstance` sets `publiclyAccessible: true` and
  `deletionProtection: false`. Both are **pinned** inside
  `PostgresInstance` — `true` and `false` respectively — specifically so no
  call site can weaken them; bypassing the composite is what makes setting
  the opposite values possible at all.
- **Impact:** The database becomes reachable from outside the VPC, and an
  accidental delete-and-recreate (a bad `chant build`/apply, an operator
  mistake) can now permanently destroy the production database with no
  safeguard — a direct reversal of SPEC acceptance criterion 2.
- **Fix:** Set `publiclyAccessible: false` and `deletionProtection: true`
  — or, again more directly, go back through `PostgresInstance`, which
  pins both without needing either to be spelled out at the call site.

**HIGH (P1): Wildcard S3 action added to the prod reader identity**
- `additionalActions: ["s3:*"]` is added to the prod `ReaderIam` call.
  `additionalActions` is an enumerated string list with no compile-time
  wildcard guard — nothing stops a literal `"s3:*"` from being passed, even
  though the composite's own documentation states there is deliberately no
  prop that appends a wildcard.
- **Impact:** The prod reader identity's policy document gains
  unrestricted S3 permissions across every action, on top of the
  bucket-scoped `GetObject`/`ListBucket` grant it's supposed to be limited
  to — directly violating SPEC acceptance criterion 4 (least privilege, no
  wildcard actions on prod).
- **Fix:** Remove the `additionalActions: ["s3:*"]` line. If a genuinely
  new action is needed, add it by its specific, enumerated name (e.g.
  `"s3:PutObject"`), never a wildcard.

**MEDIUM (P2): Abandoning the `PostgresInstance` composite is itself a red flag**
- Independent of the specific bad values above, replacing a
  `PostgresInstance({...})` call with a raw `new DBInstance({...})`
  construction silently drops **every** guarantee the composite enforces
  for this call site going forward — not just the two flipped here, but
  also the `backupRetentionDays >= 7` invariant (this diff's raw spec
  hardcodes `backupRetentionPeriod: 30`, which happens to still comply, but
  nothing would stop a future edit from setting it below 7 without the
  composite's build-time throw to catch it).
- **Impact:** Future edits to this call site lose the composite's
  build-time protection silently — there's no lint rule or build error
  that flags "this call site used to be composite-backed and no longer
  is," so the regression is easy to miss in a later, unrelated diff.
- **Fix:** Restore the `PostgresInstance({...})` call. The stated reason
  for the swap ("unblock a hotfix without waiting on a composite change")
  doesn't hold — nothing in this diff needed a composite change at all;
  every value set here is already a `PostgresInstance` prop.

### Severity ranking

1. Hardcoded plaintext database password (CRITICAL)
2. `publiclyAccessible: true` + `deletionProtection: false` on the prod
   database (CRITICAL)
3. `additionalActions: ["s3:*"]` wildcard IAM grant (HIGH)
4. Composite bypass as a practice, independent of the specific values
   (MEDIUM)

### Expected remediation steps

1. Restore the `PostgresInstance({...})` call in
   `src/envs/prod/infra/main.ts`, with the `SecretRef`/`secretRef()`
   plumbing for `masterPassword` intact — this single change also fixes
   issues 2 and 4 above, since `deletionProtection`/`publiclyAccessible`
   are pinned inside the composite and the composite-bypass concern
   disappears once the composite is back in use.
2. Remove the `additionalActions: ["s3:*"]` line from the `reader`
   `ReaderIam` call; add back only specific, named actions if one is
   actually needed.
