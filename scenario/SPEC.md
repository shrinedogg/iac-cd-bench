# Canonical Scenario Specification

The infrastructure scenario implemented idiomatically in every stack.

## Spine

A stateless web application with supporting infrastructure, deployed to two environments (dev and prod).

### Resources

| Resource | Dev | Prod |
|---|---|---|
| Kubernetes cluster (EKS) | 2 nodes, t3.medium | 4 nodes, t3.large |
| S3 bucket (application assets) | versioned, encryption | versioned, encryption, cross-region replication to us-west-2 |
| RDS PostgreSQL instance | db.t3.micro, single AZ | db.t3.medium, multi-AZ, encrypted |
| IAM user + role (service account) | programmatic access | least-privilege role, OIDC trust |
| HTTPS exposure | internal ALB or CloudFront | CloudFront + ACM cert |
| Secret (DB connection string) | SOPS-encrypted (knr-ops), Crossplane SecretStore/ProviderSecret, Terraform `-var-file` or SOPS with `sops` provider, Pulumi `ConfigSecret`, chant referenced-provenance secret ref (no committed ciphertext — see `golden-base/chant/README.md`, "Secrets: the SOPS interim") |

### Environments

Two environments: `dev` and `prod`. Each has the same resource topology but different sizing and region. Environment separation must be achieved idiomatically per stack:

- **knr-ops**: kustomize overlays (`base/` → `overlays/dev/`, `overlays/prod/`)
- **Crossplane**: provider configs + composition parameters per cluster/region
- **Terraform**: workspaces or `*-dev.tfvars`/`*-prod.tfvars`
- **Pulumi**: stack configs (`dev.yaml`, `prod.yaml`)
- **chant**: two entrypoint directories, one build root each (`src/envs/dev`, `src/envs/prod`), invoking shared `Composite()` factories with per-environment props. `chant build src/envs/dev` never reads anything under `src/envs/prod`. Build-time parameters (`--param env=prod` over a single entrypoint) are the available alternative and are deliberately not used — see `golden-base/chant/README.md`, "Environment isolation".

## Acceptance Criteria (applies to all stacks)

1. **Bucket**: versioning enabled, server-side encryption (AES-256 or KMS), no public access
2. **RDS**: deletion protection on, backup retention >= 7 days, no publicly accessible
3. **Secret**: never stored in plaintext in Git; encrypted at rest via stack-native mechanism
4. **IAM**: principle of least privilege; no wildcard actions on prod
5. **Cluster**: managed node groups, instance types and replicas match env specs above
6. **HTTPS**: listener on 443, valid cert reference, no 80->443 redirect forced (ALB default)
7. **Environment isolation**: dev and prod are independently deployable; changing prod does not modify dev state

## Not in Scope

- Database migrations or schema management
- Application code (container image is assumed built)
- CI/CD pipeline definitions (focus is the IaC delivery layer)
- Monitoring/observability tooling

## Fairness Note

All stacks implement the same spec. Differences in implementation length, idiom, or complexity are expected and measured, not normalized away.
