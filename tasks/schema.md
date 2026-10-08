# Task Schema

Every task lives in `tasks/<stack>/<task-id>/` with this contract:

## Required files

- `prompt.md` — the exact prompt rendered to the model (supports {{scenario_spec}} template)
- `seed/` — directory tree copied into the model's workspace; represents the repo state the model starts from
- `spec.yaml` — task configuration (see schema below)
- `golden/` — reference solution (not shown to model, used for diff scoring)

## Optional files

- `docs/` — warm-condition context slices (omitted for cold runs)
- `tests/test_task.py` — pytest semantic assertions; auto-discovered by runner

## spec.yaml Schema

```yaml
stack: knr-ops|chant|bare|crossplane|terraform|pulumi-python|pulumi-typescript
archetype: comprehend|generate|modify|debug|review|semantics
id: "T1-comprehend"
title: "Predict delivery behavior from repo slice"

# Stage configuration
stages:
  lint:
    enabled: true    # default true
    tools:          # per-stack defaults applied if omitted
      - kubeconform
      - yq
  static:
    enabled: true
    tools:
      - kustomize build
  semantic:
    enabled: true
    assertion_count: 5  # expected assertions in test_task.py
    pass_threshold: 0.6 # optional: fraction of assertions that must pass;
                        # omit = all must pass (pytest exit 0)
  e2e:
    enabled: false    # default false; requires --e2e flag

# Answer format for free-text tasks (T1, T5)
answer_format: rubric    # rubric = scored by LLM judge; code = validated by ladder

# Seeded defect for T4 (debug) tasks
defect:
  description: "SOPS secret references wrong age key"
  # The golden/ contains the fixed version; seed/ contains the defect

# Rubric criteria for T1/T5 (comprehend/review).
# Read by bench/judge.py: each criterion is scored 0-1 by the judge model
# against golden/answer_key.md, and the weighted mean becomes the idiom axis
# (only when the runner is invoked with --judge).
rubric:
  - criterion: "Identifies all resources that will reconcile"
    weight: 1
  - criterion: "Predicts correct reconciliation order"
    weight: 2
  - criterion: "Flags destructive changes (recreate/delete)"
    weight: 3
```

## Semantics tasks (T6)

`T6-semantics` tasks probe runtime-behavior understanding of each stack: the
model reads real config from `seed/` and predicts what the toolchain actually
does (replace vs in-place, prune vs orphan, secret masking, ordering,
lifecycle guards). Answers are a structured `answers.json` fenced block graded
question-by-question in `tests/test_task.py` (each question = one pytest
assertion, `pass_threshold` grants partial credit). Lint/static are disabled —
the JSON answer sheet is not IaC code; the semantic stage carries the whole
signal and runs with cwd set to the model's workspace.
