# Comparative Benchmark Report

Comparing 2 result sets: `claude-haiku-4-5-3arm`, `claude-opus-5-3arm`

## Composite Score by Stack

Mean composite score across all runs in the stack.

| Stack | claude-haiku-4-5-3arm | claude-opus-5-3arm |
| --- | --- | --- |
| knr-ops | 0.60 | 0.59 |
| crossplane | — | — |
| terraform | — | — |
| pulumi-python | — | — |
| pulumi-typescript | — | — |
| chant | 0.66 | 0.58 |
| bare | 0.60 | 0.60 |
| **Overall** | 0.62 | 0.59 |

## Composite Score by Stack × Archetype

| Stack / Archetype | claude-haiku-4-5-3arm | claude-opus-5-3arm |
| --- | --- | --- |
| knr-ops / Comprehend | 0.54 | 0.44 |
| knr-ops / Generate | 0.40 | 0.44 |
| knr-ops / Modify | 0.78 | 0.78 |
| knr-ops / Debug | 0.67 | 0.67 |
| knr-ops / Review | 0.44 | 0.44 |
| knr-ops / Semantics | 0.77 | 0.78 |
| chant / Comprehend | 0.54 | 0.55 |
| chant / Generate | 0.76 | 0.52 |
| chant / Modify | 0.78 | 0.78 |
| chant / Debug | 0.63 | 0.33 |
| chant / Review | 0.53 | 0.55 |
| chant / Semantics | 0.74 | 0.76 |
| bare / Comprehend | 0.54 | 0.56 |
| bare / Generate | 0.59 | 0.48 |
| bare / Modify | 0.67 | 0.67 |
| bare / Debug | 0.48 | 0.56 |
| bare / Review | 0.55 | 0.55 |
| bare / Semantics | 0.75 | 0.78 |

## Coverage

| Result set | Runs | Judged runs | Judge model | Judge prompt |
| --- | --- | --- | --- | --- |
| claude-haiku-4-5-3arm | 108 | 36 | claude-haiku-4-5 | 8bac4198059e310e |
| claude-opus-5-3arm | 36 | 12 | claude-haiku-4-5 | 8bac4198059e310e |

> Runs without a judge verdict score 0.0 on the idiom axis (weight 1 of 9),
> so composites are only strictly comparable between equally judged sets.