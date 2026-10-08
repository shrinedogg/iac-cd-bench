## Task: Fix a `chant build` failure in the prod database call site

**Stack:** chant (TypeScript composites compiling to Flux + CAPI/CAPA + ACK Kubernetes manifests)

You are given `src/composites/postgres-instance.ts` (the `PostgresInstance`
composite factory, plus its shared `defaults.ts`/`labels.ts`/`secrets.ts`)
and `src/envs/prod/infra/main.ts`, which calls `PostgresInstance({...})`
once for the prod database.

### Symptoms

```
$ chant build src/envs/prod/infra
Error: PostgresInstance("myapp-prod-db"): backupRetentionDays must be at
least 7 (SPEC acceptance criterion 2), got 5
```

The build fails outright — this isn't a lint warning, it's a thrown error
from inside the composite factory itself, so no output is produced for
`dist/prod/infra/manifests.yaml` at all.

### Seeded Defect

`src/envs/prod/infra/main.ts`'s `PostgresInstance({...})` call sets
`backupRetentionDays: 5`. `postgres-instance.ts` enforces SPEC acceptance
criterion 2 (backup retention at least 7 days) as an invariant in the
composite's own factory body — `backupRetentionDays` below
`MINIMUM_BACKUP_RETENTION_DAYS` (7, from `defaults.ts`) throws rather than
silently shipping a non-compliant `DBInstance`.

### Your Task

1. Identify exactly which prop at the `src/envs/prod/infra/main.ts` call
   site violates the invariant, and what the invariant actually requires.
2. Fix `src/envs/prod/infra/main.ts` so the call satisfies it —
   `backupRetentionDays` must be `>= 7`.
3. **Do not** edit `postgres-instance.ts` or `defaults.ts` to weaken or
   remove the invariant. It is not part of this PR, and it is the thing
   protecting every other call site (including dev's) from the same
   mistake.
4. Leave every other prop on the `PostgresInstance` call (`instanceClass`,
   `multiAZ`, `storageEncrypted`, `vpcSecurityGroupIDs`, etc.) unchanged —
   this fix is one value, not a rewrite of the call site.

Return the corrected `src/envs/prod/infra/main.ts` as a single fenced code
block, preceded by its file path in backticks.

### Context Files

{{scenario_spec}}
