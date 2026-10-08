## Task: Fix a Deployment selector that `kubectl apply` rejects

**Stack:** bare (plain hand-authored Kubernetes manifests, `kubectl apply -f`, no delivery tooling)

You are given `dev/app.yaml` (known-good) and `prod/app.yaml` (seeded
defect) from a bare golden repo: plain Kubernetes manifests applied with
`kubectl apply -f dev/` and `kubectl apply -f prod/`. There is no Flux, no
kustomize, no admission webhook layer beyond the Kubernetes API server
itself.

### Symptoms

```
$ kubectl apply -f prod/app.yaml
serviceaccount/myapp-prod unchanged
The Deployment "myapp-prod" is invalid: spec.template.metadata.labels: Invalid value:
map[string]string{"app":"myapp","env":"prod"}: `selector` does not match template `labels`
service/myapp-prod unchanged
```

The Deployment is rejected by the API server. The ServiceAccount and Service
in the same file still apply fine — `kubectl apply -f` processes each
object in the file independently and reports failures per-object.

### Seeded Defect

`prod/app.yaml`'s Deployment has a `spec.selector.matchLabels` that isn't
fully present on `spec.template.metadata.labels`. `dev/app.yaml` doesn't
have this problem — its Deployment's selector and template labels agree.

### Your Task

1. Identify exactly which label `prod/app.yaml`'s selector requires that
   its pod template doesn't provide
2. Fix `prod/app.yaml` so the Deployment's `spec.selector.matchLabels` is
   fully satisfied by `spec.template.metadata.labels` (every key/value pair
   in the selector must also appear in the template labels)
3. Make sure the Service in the same file still selects the Deployment's
   pods after your fix (the Service's `spec.selector` must remain a subset
   of the Deployment's template labels)
4. Leave `dev/app.yaml` untouched — it is not part of this PR

Return the corrected `prod/app.yaml` as a fenced code block preceded by its
file path in backticks.

### Context Files

{{scenario_spec}}
