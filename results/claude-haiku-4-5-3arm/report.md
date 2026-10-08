# Benchmark Report: claude-haiku-4-5-3arm

## Stack × Archetype Matrix

Each cell shows: **pass@1 / pass@k / avg composite score**

| Stack | Comprehend | Generate | Modify | Debug | Review | Semantics | Average |
| --- | --- | --- | --- | --- | --- | --- | --- |
| knr-ops | 0% / 0% / 0.54 | 67% / 100% / 0.40 | 100% / 100% / 0.78 | 0% / 0% / 0.67 | 0% / 0% / 0.44 | 0% / 0% / 0.77 | 0.60 |
| crossplane | — | — | — | — | — | — | 0.00 |
| terraform | — | — | — | — | — | — | 0.00 |
| pulumi-python | — | — | — | — | — | — | 0.00 |
| pulumi-typescript | — | — | — | — | — | — | 0.00 |
| chant | 0% / 0% / 0.54 | 83% / 100% / 0.76 | 100% / 100% / 0.78 | 67% / 100% / 0.63 | 0% / 0% / 0.53 | 0% / 0% / 0.74 | 0.66 |
| bare | 0% / 0% / 0.54 | 17% / 100% / 0.59 | 0% / 0% / 0.67 | 0% / 0% / 0.48 | 0% / 0% / 0.55 | 0% / 0% / 0.75 | 0.60 |

## knr-ops Cold vs Warm Delta

| Task | Cold pass@1 | Warm pass@1 | Delta |
| --- | --- | --- | --- |
| T1-comprehend | 0% | 0% | +0% |
| T2-generate | 78% | 100% | +22% |
| T3-modify | 100% | 100% | +0% |
| T4-debug | 44% | 67% | +22% |
| T5-review | 0% | 0% | +0% |
| T6-semantics | 0% | 0% | +0% |

> **Cold/warm delta measures how much in-context documentation** knr-ops needs to match training-data-driven stacks.

## Token Usage

- Input tokens: 972
- Output tokens: 325,319
- Total: 326,291
- Runs: 108