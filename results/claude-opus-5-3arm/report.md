# Benchmark Report: claude-opus-5-3arm

## Stack × Archetype Matrix

Each cell shows: **pass@1 / pass@k / avg composite score**

| Stack | Comprehend | Generate | Modify | Debug | Review | Semantics | Average |
| --- | --- | --- | --- | --- | --- | --- | --- |
| knr-ops | 0% / 0% / 0.44 | 100% / 100% / 0.44 | 100% / 100% / 0.78 | 0% / 0% / 0.67 | 0% / 0% / 0.44 | 0% / 0% / 0.78 | 0.59 |
| crossplane | — | — | — | — | — | — | 0.00 |
| terraform | — | — | — | — | — | — | 0.00 |
| pulumi-python | — | — | — | — | — | — | 0.00 |
| pulumi-typescript | — | — | — | — | — | — | 0.00 |
| chant | 0% / 0% / 0.55 | 100% / 100% / 0.52 | 100% / 100% / 0.78 | 0% / 0% / 0.33 | 0% / 0% / 0.55 | 0% / 0% / 0.76 | 0.58 |
| bare | 0% / 0% / 0.56 | 50% / 100% / 0.48 | 0% / 0% / 0.67 | 0% / 0% / 0.56 | 0% / 0% / 0.55 | 0% / 0% / 0.78 | 0.60 |

## knr-ops Cold vs Warm Delta

| Task | Cold pass@1 | Warm pass@1 | Delta |
| --- | --- | --- | --- |
| T1-comprehend | 0% | 0% | +0% |
| T2-generate | 100% | 100% | +0% |
| T3-modify | 100% | 100% | +0% |
| T4-debug | 67% | 67% | +0% |
| T5-review | 0% | 0% | +0% |
| T6-semantics | 0% | 0% | +0% |

> **Cold/warm delta measures how much in-context documentation** knr-ops needs to match training-data-driven stacks.

## Token Usage

- Input tokens: 72
- Output tokens: 67,852
- Total: 67,924
- Runs: 36