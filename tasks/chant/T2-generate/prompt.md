## Task: Add a new ACK-managed S3 bucket for application logs

**Stack:** chant (TypeScript composites compiling to Flux + CAPI/CAPA + ACK Kubernetes manifests)

You are given a slice of a chant golden repo: `src/composites/` (the
scenario-local `Composite()` factories — `SecureBucket`, `ReaderIam`, and
their shared `defaults.ts`/`labels.ts`/`policies.ts`/`secrets.ts`) and the
existing dev and prod `infra` build roots (`src/envs/{dev,prod}/infra/main.ts`),
each of which already calls `SecureBucket` once for an application-assets
bucket and `ReaderIam` once for a reader identity scoped to that bucket.

### Current State

Each of `src/envs/dev/infra/main.ts` and `src/envs/prod/infra/main.ts`
already declares:
- `assets` — a `SecureBucket` call for the application-assets bucket
  (`myapp-assets-dev` / `myapp-assets-prod`)
- `reader` — a `ReaderIam` call for a service-account identity scoped to
  read that bucket

`chant build src/envs/<env>/infra` discovers every `.ts` file under that
directory, not just `main.ts` — a new file in the same directory is
built and its exports flow into the same output alongside `main.ts`'s.

### Your Task

Add a new S3 bucket for application logs to **both** `dev` and `prod`,
using the existing composites — do not hand-write a raw resource and do
not modify any file under `src/composites/`:

1. Bucket name: `myapp-logs-{env}` (`myapp-logs-dev` / `myapp-logs-prod`),
   via a `SecureBucket({...})` call. `SecureBucket` already pins
   versioning, encryption, and the public-access block unconditionally —
   you do not need (and should not add) props for any of those; there is
   no prop that would change them.
2. A reader identity scoped **only** to the new logs bucket, via a
   `ReaderIam({...})` call, granting at minimum `s3:GetObject` and
   `s3:PutObject` on `myapp-logs-{env}` — not on the existing assets
   bucket, and not via a wildcard action or a wildcard resource. Use the
   existing `additionalActions` prop (an enumerated string list) to add
   `s3:PutObject`; `ReaderIam`'s baseline policy already covers
   `s3:GetObject`.
3. Give the new reader the same `trust` shape the environment's existing
   `reader` call uses (`{ mode: "account", accountID: "123456789012" }`
   for dev; the OIDC trust block for prod — matching provider ARN, issuer
   host, namespace `app`, service account `myapp`) so the new identity is
   consistent with how this environment already authenticates.
4. Do this **independently** in `dev` and in `prod` — this arm has no
   parameterized entrypoint, so each environment's build root needs its
   own call site, the same way `assets`/`reader` are already declared
   twice, once per environment.

Suggested file layout: a new `src/envs/dev/infra/logs.ts` and
`src/envs/prod/infra/logs.ts`, each exporting the new `SecureBucket` and
`ReaderIam` calls — but any file under the respective `infra/` directory
works, since discovery picks up every `.ts` file there.

Write each new/changed file as its own fenced code block, preceded by its
file path in backticks, e.g.:

`src/envs/dev/infra/logs.ts`
```typescript
...
```

### Context Files

{{scenario_spec}}
