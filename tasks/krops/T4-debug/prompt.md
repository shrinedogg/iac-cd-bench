## Task: Fix SOPS decryption failures after a `.sops.yaml` edit

**Stack:** krops (Flux + SOPS + age)

You are given a krops GitOps repository. SOPS + age is the repo's secret
mechanism: only the `data`/`stringData` fields of manifests named
`*.sops.yaml` under the cloud trees (`mgmt/aws/`, `mgmt/azure/`,
`mgmt/gcp/`) are encrypted; the age **public** key lives in `.sops.yaml`
(safe to commit) and the matching **private** key is loaded into the cluster
as the `sops-age` Secret so Flux can decrypt at reconcile time. Flux
Kustomizations that carry `decryption: {provider: sops, secretRef:
sops-age}` decrypt the `*.sops.yaml` files under their `spec.path`.

### Symptoms

After yesterday's `.sops.yaml` "cleanup" commit, the management cluster's
`flux get kustomizations` shows exactly two failures — both Kustomizations
that decrypt `*.sops.yaml` files outside one narrow tree:

```
NAME                 READY   MESSAGE
cert-manager         True    Last reconciliation complete
capi-operator        True    Last reconciliation complete
capi-system          True    Last reconciliation complete
capa-system          True    Last reconciliation complete
caaph-system         True    Last reconciliation complete
capa-identity        True    Last reconciliation complete
aws-managed-clusters True    Last reconciliation complete
ack-controllers      True    Last reconciliation complete
workload-resources   True    Last reconciliation complete
flux-apps            True    Last reconciliation complete
konflate             False   Kustomization is stuck: failed to decrypt manifest "mgmt/aws/infrastructure/konflate/konflate-token.sops.yaml": no matching creation rule found
```

```
$ flux get kustomizations -n default
NAME                   READY   MESSAGE
flux-apps-eu-north-1   True    Last reconciliation complete
flux-apps-eu-west-1    False   Kustomization is stuck: failed to decrypt manifest "mgmt/aws/addons/flux-apps/regions/eu-west-1/flux-github-pat.sops.yaml": no matching creation rule found
```

Everything that does not decrypt SOPS secrets is healthy, and the eu-north-1
regional pull secret decrypts fine.

### The current `.sops.yaml` (the file to fix)

```yaml
# SOPS configuration. Encrypts only the `data`/`stringData` fields of any
# manifest named `*.sops.yaml` under mgmt/aws/, mgmt/azure/ or mgmt/gcp/,
# leaving keys/metadata readable.
#
# The `age` recipient below is a PUBLIC key — safe to commit. The matching
# PRIVATE key lives in `age.agekey` (gitignored) and is loaded into the
# cluster as the `sops-age` secret by bootstrap.sh so Flux can decrypt on
# reconcile.
#
# To rotate or add recipients, edit this file and run:  mise run sops-updatekeys
creation_rules:
  - path_regex: mgmt/aws/addons/flux-apps/regions/eu-north-1/.*\.sops\.yaml$
    encrypted_regex: "^(data|stringData)$"
    age: age1amqtgevmdxutg07rkr3wggua43620zgncgfq53vkww0283wrgspqv9q0jw
```

### Your task

1. Explain in one or two sentences what the bad edit did.
2. Fix `.sops.yaml` so the creation rule again covers the repo layout: every
   `*.sops.yaml` under `mgmt/aws/`, `mgmt/azure/` and `mgmt/gcp/` must match.
3. Keep the age recipient and the `encrypted_regex` exactly as they are —
   the fix is the rule's path coverage, not a key rotation or a widening of
   which fields get encrypted.

Return the corrected `.sops.yaml` as a fenced code block, preceded by its
file path in backticks. The seeded SOPS-encrypted manifests
(`mgmt/aws/infrastructure/konflate/konflate-token.sops.yaml`,
`mgmt/aws/addons/flux-apps/regions/*/flux-github-pat.sops.yaml`) are NOT
part of this PR — do not modify or re-emit them.

### Context Files

{{scenario_spec}}
