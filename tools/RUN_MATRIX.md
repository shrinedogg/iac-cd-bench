# Running the #40 comparison matrix

Launch tooling for [iac-cd-bench#40](https://github.com/lex00/iac-cd-bench/issues/40):
chant vs knr-ops vs bare, cold + warm, k=3, judge on. Parameters below are the
ones signed off in the issue thread (2026-08-25) - see that thread for the
full discussion, including the reasoning-effort amendment.

## Prerequisites

`tools/run_matrix.sh` invokes `python3 -m bench.runner` and
`python3 -m bench.report` using flags that do not all exist on `main` yet. Its
preflight checks re-verify the parts of this that are cheap to check
statically (directory presence, `--help` flag presence) every run, but the
PRs below still need to be merged first - the preflight can't detect
semantic-only fixes like #47 or the not-yet-pushed tasks/chant branch.

### For SMOKE

| # | What | Status as of writing |
|---|------|----|
| [#41](https://github.com/lex00/iac-cd-bench/pull/41) | `bench/chant-wiring` - registers `chant` in runner.py's stack list | open |
| [#42](https://github.com/lex00/iac-cd-bench/pull/42) | `bench/idiom-judge` - adds `--judge`/`--judge-model`/`--judge-provider`/`--judge-base-url` to the runner, and `report.py --compare` | open |
| [#49](https://github.com/lex00/iac-cd-bench/pull/49) | `bench/chant-golden` - `golden-base/chant/` (composites, SPEC, fixtures) | open |
| tasks/chant (#22-#25) | The chant task suite itself (`tasks/chant/T1-comprehend` etc.) | **in flight, no branch on origin yet** - a local `bench/chant-tasks` branch in this checkout has a preview, but it is not a tracked PR |

### For FULL (all of the above, plus)

| # | What | Status as of writing |
|---|------|----|
| [#45](https://github.com/lex00/iac-cd-bench/pull/45) | `bench/bare-tasks` - `tasks/bare/{T1..T6}` + `golden-base/bare` | open |
| [#46](https://github.com/lex00/iac-cd-bench/pull/46) | `bench/bare-wiring` - registers `bare` in runner.py's stack list (stacked on the chant-wiring commits, **not** on #45 - both PRs are needed) | open |
| [#50](https://github.com/lex00/iac-cd-bench/pull/50) | `bench/run-blockers` - closes #43/#44: knr-ops T1 answer-key fix, honest determinism README, and records `reasoning_effort` in every run JSON | open |
| #47 (no PR yet) | golden-base/knr-ops ACK-vs-upbound realignment - cross-arm resource comparability. The owner's sign-off comment gates the FULL run on this; it's a semantic fix `run_matrix.sh` cannot detect by file presence, so confirm it by hand | **issue only, unopened as a branch** |

`--reasoning-effort` itself is already on `main` (`bench/runner.py` argparse) -
only *recording* it into the run JSON (#50) and the `--judge`/`--compare`
surface (#42) are missing today. Re-verify any of the above with:

```bash
git diff main origin/bench/idiom-judge  -- bench/runner.py bench/report.py
git diff main origin/bench/run-blockers -- bench/runner.py
git diff main origin/bench/chant-wiring -- bench/runner.py bench/report.py
git diff main origin/bench/bare-wiring  -- bench/runner.py bench/report.py
```

## The two commands

Both default to a dry run - they print the exact invocation sequence and
touch no network. Nothing fires without `--execute` **and**
`RUN_MATRIX_ACK=yes` in the environment together; either alone is refused.

```bash
# Print (default) or run the smoke command: one model (claude-haiku-4-5 by
# default - override with SMOKE_MODEL=claude-opus-5), tasks/chant/T1-comprehend,
# warm, k=1, judge on.
tools/run_matrix.sh smoke
RUN_MATRIX_ACK=yes tools/run_matrix.sh smoke --execute

# Print (default) or run the full matrix: claude-opus-5 + claude-haiku-4-5,
# x {chant, knr-ops, bare}, x {cold, warm}, k=3, judge on
# (claude-haiku-4-5), reasoning effort pinned "low" per model.
tools/run_matrix.sh full
RUN_MATRIX_ACK=yes tools/run_matrix.sh full --execute
```

`ANTHROPIC_API_KEY` must be set in the environment before `--execute` - the
script refuses to run without it rather than falling through to the runner's
placeholder-key default (which would just fail auth against the real API).

Before spending real API budget, get a cost projection from historical
token usage (no network calls):

```bash
python3 tools/estimate_matrix_cost.py
```

## Where results land

`bench.runner` writes to `results/<model>[-<results-tag>]/<stack>/<condition>/<task>_run<N>.json`.
`run_matrix.sh` tags every full-matrix invocation `<model>-<effort>-3arm`
(e.g. `claude-opus-5-low-3arm`), so a full run for one model accumulates
under a single directory covering all three arms:

```
results/claude-opus-5-low-3arm/
  chant/{cold,warm}/T1-comprehend_run0.json ...
  knr-ops/{cold,warm}/...
  bare/{cold,warm}/...
results/claude-haiku-4-5-low-3arm/
  ...same layout...
```

The smoke run tags its single invocation `<model>-smoke`
(`results/claude-haiku-4-5-smoke/chant/warm/T1-comprehend_run0.json` by
default).

## Rendering the three-arm tables

`bench/report.py --compare DIR [DIR...]` (PR #42) takes one result-set
directory per column - here, one per model, each already containing all
three arms as subdirectories:

```bash
python3 -m bench.report --compare results/claude-opus-5-low-3arm results/claude-haiku-4-5-low-3arm
```

This produces `results/comparison.md` with:

- **Composite Score by Stack** - one row per stack in `report.py`'s `STACKS`
  constant (which #41 + #46 together extend to include `chant` and `bare`
  alongside the five pre-existing stacks), one column per model, mean
  composite score across all runs in that stack. Since each result-set
  directory here only has `chant`/`knr-ops`/`bare` data, the other four rows
  render as `—`; the three that matter are the table that answers "does warm
  chant beat knr-ops, and does either beat bare".
- **Composite Score by Stack x Archetype** - the same breakdown crossed with
  task archetype (comprehend/generate/modify/debug/review/semantics), so a
  stack-level delta can be traced to a specific archetype rather than
  averaged away.
- **Coverage** - run counts, judged-run counts, and the judge model +
  prompt hash actually used per result set, so composites are only read as
  comparable between equally-judged sets (an unjudged run scores 0.0 on the
  idiom axis, per `bench/score.py`'s `idiom_score`).

Cold vs warm and per-task deltas live in the single-model report
(`python3 -m bench.report --model <model>`), which `--compare` does not
replace - run both if you want the knr-ops-style cold/warm delta table
alongside the cross-arm comparison.
