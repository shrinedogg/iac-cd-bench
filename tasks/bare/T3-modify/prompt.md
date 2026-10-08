## Task: Scale worker node counts in dev and prod

**Stack:** bare (plain hand-authored Kubernetes manifests, `kubectl apply -f`, no delivery tooling)

You are given `dev/workers.yaml` and `prod/workers.yaml` from a bare golden
repo: plain Kubernetes manifests applied with `kubectl apply -f dev/` and
`kubectl apply -f prod/`. There is no Flux, no kustomize, no shared base —
`dev/` and `prod/` are two fully independent, hand-written directories, and
`prod/workers.yaml` is not a patch over `dev/workers.yaml`; it's a complete,
standalone copy with different sizing.

### Current State

- `dev/workers.yaml`: `MachineDeployment` `myapp-dev-workers`, `replicas: 2`,
  `AWSMachineTemplate` `myapp-dev-workers`, `instanceType: t3.medium`
- `prod/workers.yaml`: `MachineDeployment` `myapp-prod-workers`,
  `replicas: 4`, `AWSMachineTemplate` `myapp-prod-workers`,
  `instanceType: t3.large`

### Your Task

Scale up worker capacity in both environments, keeping instance types
unchanged:

1. `dev/workers.yaml`: change `myapp-dev-workers` replicas from `2` to `3`
2. `prod/workers.yaml`: change `myapp-prod-workers` replicas from `4` to `6`
3. `instanceType` stays `t3.medium` in dev and `t3.large` in prod — do not
   touch it
4. Because this arm has no shared base or overlay, this change must be made
   in **both** files by hand — editing one has no effect on the other

Return the two updated files as fenced code blocks, each preceded by its
file path in backticks (`dev/workers.yaml`, `prod/workers.yaml`).

### Context Files

{{scenario_spec}}
