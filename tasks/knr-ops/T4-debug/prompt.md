## Task: Fix SOPS secret referencing wrong age key

**Stack:** knr-ops (Flux + SOPS + kustomize)

You are given `.sops.yaml` (seeded defect), `age-key.txt` (known-good), and
`infra/secret.yaml` from a knr-ops GitOps repository. SOPS encrypts/decrypts
`*.yaml`/`*.yml` secrets per the `creation_rules` in `.sops.yaml`, keyed by
an age public key.

### Symptoms

Flux kustomizations are stuck in a failed state:
```
flux get kustomizations
NAME        READY   MESSAGE
myapp-dev   False   failed to merge command output: yaml: line 1: did not find expected key
myapp-prod  False   failed to merge command output: yaml: line 1: did not find expected key
```

Flux cannot decrypt the SOPS-encrypted secrets it needs to reconcile, so
both kustomizations fail.

### Seeded Defect

`.sops.yaml`'s `creation_rules` (both the `*.yaml` and the `*.yml` entry)
reference a placeholder age public key that does not match the real key
committed in `age-key.txt`. SOPS refuses to decrypt anything against the
wrong key.

### Your Task

1. Identify which key `.sops.yaml` should be using (it's already in the
   repo, in `age-key.txt`)
2. Fix the `age` value on every `creation_rules` entry in `.sops.yaml`
3. Leave `age-key.txt` and `infra/secret.yaml` untouched -- they are not
   part of this PR

Return the corrected `.sops.yaml` as a fenced code block, preceded by its
file path in backticks.

### Context Files

{{scenario_spec}}
