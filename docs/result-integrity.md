# Result integrity

A benchmark number is only worth reading if the run behind it measured the
model. This harness has published numbers that did not, six times, each costing
hours before anyone noticed. The gates below exist so that each of those
failures is now either structurally impossible or loudly detected.

The design is ported from two harnesses that already learned these lessons:
[chant-bench](https://github.com/lex00/chant-bench) (the publishing side) and
[aws-bench](https://github.com/lex00/aws-bench) (the running side). Their
governing rule, which this file adopts:

> A run the gates rejected is not published. Neither is one whose provenance is
> incomplete. A number nobody can trace, or one that measured a broken harness
> rather than a tool, is worse than no number.

## The rules

### 1. Nothing starts without the tools

`bench/preflight.py` probes every binary the selected stacks' lint, static and
e2e stages will shell out to, records `name -> {path, version}`, and raises
`PreflightError` before the adapter is built if any is absent. `bench.runner`
exits 2 on that.

A stage whose binary is missing cannot tell a correct answer from an unchecked
one. Fixing the stage runners to report `passed: False` on `FileNotFoundError`
(#56) stops the lie per-stage but still burns a full matrix of API spend to
produce a wall of failures, so the check has to run before the first token.

`--allow-missing-tools` is the deliberate escape hatch. It stamps the result
set `partial: true`, and `bench.validate` downgrades every run in it, so a
knowingly-incomplete run can never later be quoted as a clean one.

Run it standalone with `make preflight STACKS=knr-ops,chant`.

### 2. Every run carries its provenance

`bench/provenance.py` stamps each result JSON with:

| field | why |
| --- | --- |
| `harness.commit`, `harness.branch`, `harness.dirty` | a re-run after any harness change is a different experiment; `dirty` matters as much as the sha, because a sha alone cannot describe a tree with uncommitted edits |
| `task.prompt_sha256`, `task.spec_sha256`, `task.scenario_sha256` | the instruction is part of the experiment |
| `toolchain` | `{binary: {path, version}}` from the preflight |
| `provider`, `model`, `reasoning_effort` | what was actually pinned, not what was intended |
| `judge_model`, `judge_prompt_sha256` | when `--judge` was used |
| `partial` | set when the preflight was overridden |

`_provenance.json` in the result-set directory carries the same block plus the
preflight report that authorised the set.

`toolchain_fingerprint()` hashes name+version only, never paths: two worktrees
resolve the same binary at different paths without any behavioural difference,
and a fingerprint that moved on path alone would flag every set as
incomparable and be ignored within a week.

### 3. A stage with nothing to act on is `inapplicable`, not `passed`

This is the highest-value guard and the one that had never been fixed anywhere.

`lint` used to return `{"passed": True, "logs": "no YAML files in workspace"}`.
`static` used to return `{"passed": True, "logs": "static validation passed"}`
when it found no kustomization, claim or manifest. `semantic` used to return a
pass for a task shipping no grader. `completeness` defaulted to `1.0` whenever
no assertion was evaluated.

The result: a run that produced no extractable output scored 2 of 3 stages plus
full marks on the second-heaviest axis. **The most broken runs scored highest.**

Now those paths return `bench.stages.lint.inapplicable(reason)` — a dict with
no `passed` key at all, so every existing `.get("passed", False)` reader treats
it as not-passed — and `bench.score`:

- excludes inapplicable stages from both sides of the correctness ratio;
- drops `completeness` from the weighted composite when no assertion ran,
  rather than defaulting it;
- recognises the old log bodies (`VACUOUS_LOG_MARKERS`) so stored results are
  re-scored honestly instead of the fix applying only to future runs.

A run whose every enabled stage was inapplicable is rejected outright.

**Measured impact on the 1140 historical result JSONs** (pinned in
`tests/test_score_regression.py`):

- 744 runs (65%) carried at least one vacuously-passed stage
  — semantic 621, static 346, lint 182
- 119 runs had *every* enabled stage inapplicable
- 762 composites change, **every one of them downward**, mean −0.22

Zero composites move up, which is the invariant the test asserts: withdrawing
credit that was never earned can only lower a score.

### 4. A completion that is not an answer is rejected, not scored

`bench/validity.py` classifies the raw completion before any stage runs:

- **empty** — the provider returned no text;
- **too short** — under 200 characters, below any usable answer in this suite;
- **agent transcript** — tool-call markup (`<invoke name=`, Claude Code's `⏺`
  bullets, `TodoWrite`/`str_replace_editor`, "I'll use the Read tool"), or two
  distinct narration markers, or three narration lines;
- **no extractable output** — the task's enabled stages act on files the model
  was to produce, and the completion has no fenced code block.

That last one is what makes #59 and the vacuous pass the same failure: a
provider returning preambles gives lint and static nothing to check, so the
arm's gate rate goes *up* because its provider is broken.

The gate is pure text classification — no network, no subprocess — so it
re-runs identically over historical result JSONs. Swept over all 1140 of them
it flags 58 empty completions, 43 too-short ones, and 2 genuine agent
transcripts, with no false positives on real answers.

### 5. A rejected run gets no number

`bench/report.py` partitions runs before averaging anything. A rejected run
contributes to no cell, and the count is stated above the matrix as
**`rejected: N`** with a reason histogram, not in a footnote.

chant-bench's `rate()` is the source: *"An invalid run gets no number. Not a
low one, not a caveated one — none."* terraform-m1 published `1.000` beside an
`invalid` badge after losing 22 of its 24 trials, and the badge lost to the
number.

`--fail-on-rejected` makes that non-zero-exit for CI.

### 6. Sets that are not the same experiment do not get a table

`bench.report --compare` refuses (exit 2) when the sets disagree on harness
commit, toolchain versions, provider or reasoning effort. `--allow-incomparable`
renders it anyway with the conflicts stated in the report body.

The model is deliberately not a comparability axis: differing models is what a
comparison is *for*.

Sets predating the provenance stamp are labelled **unverifiable** rather than
agreeing — a set that cannot be shown to differ has not been shown to match.

### 7. The validator

```
python3 -m bench.validate results/claude-opus-5 --verbose
make validate DIRS="results/claude-opus-5 results/gpt-5.4"
```

Classifies each run `valid` / `partial` / `invalid` with reasons, then judges
the set. A set is **refused** (exit 1) when:

- errored runs exceed `CRASH_LIMIT` (10%) of the set — a rate over the
  survivors is not a rate over the run set;
- rejected runs exceed `REJECT_LIMIT` (10%);
- run counts per task are uneven — a 1-run task beside 3-run tasks is a smoke
  test that landed on the leaderboard;
- harness commits, toolchains or providers are mixed *within* one set.

Errored runs stay in the denominator throughout. Dropping them from both sides
of the ratio is how a catastrophe reads as a triumph.

### 8. Graders locate artifacts by content, never by path

A grader that reads `Path("infra/s3/logs-bucket.yaml").read_text()` is grading
file placement. When the task prompt never stated a layout, an answer that
chose `infra/s3/logs/bucket.yaml` scores zero on a convention it was never
given — and because `read_text()` on a missing path raises rather than
asserting, *every* assertion in the module errors on that one wrong guess.
That is #72, caught live: a knr-ops T2-generate run whose `yq` parse and
`kustomize build` both passed scored 0/6 semantic.

The bias is not symmetric across arms, which is what makes it worse than a
uniform harshness. chant's graders parse emitted source structurally and
tolerate layout variation; knr-ops T2 demanded one exact tree. The arm
comparison this benchmark exists to make was being decided partly by which
arm's grader happened to be written which way.

The rules now:

- Artifacts are found by what they are — apiVersion/kind/name for YAML, a
  structural feature of the source for HCL/TypeScript/Python — anywhere in the
  workspace. `tasks/_grader_lib.py` is the shared implementation.
- A missing artifact is an assertion failure carrying an inventory of what the
  workspace *did* contain, never an exception.
- Locating by content must not become grepping the workspace: a document
  counts only when it identifies itself as the thing under test.
  `tests/test_grader_robustness.py` asserts both directions for every grader
  it covers — the golden-derived answer in a deliberately non-canonical layout
  passes, and each targeted defect still fails its own assertion.

### 9. A grader fix is applied to stored runs, not paid for again

Every result JSON carries the model's completion in `content`, so the exact
workspace the grader saw can be rebuilt: seed, `model_output.md`, and
`bench.runner.extract_code_blocks` (the runner's own extractor, imported, not
reimplemented).

```
python3 tools/regrade_offline.py results/<set> --out results-regraded/<set>
```

The input tree is read-only. Corrected runs go to a parallel tree, each
carrying a `regrade` block with the before/after semantic verdict and the
sha256 of the grader that produced it, so a regraded set can never be mistaken
for an original. Only the semantic stage is recomputed — lint and static ran
real tools against the same workspace and their verdicts stand — and scores
are recomputed in full, since correctness and completeness both read semantic.

### 10. An axis nothing was measured on is dropped, not failed

The mirror of rule 3. That rule stops an unmeasured stage counting as a
*pass*; this one stops an unmeasured axis counting as a *failure*.

`correctness` used to score 0 when no stage was attempted at all, while
keeping its weight of 3 in the denominator. Tasks whose spec disables every
build stage — `T1-comprehend` and `T5-review` are rubric-only by design — were
therefore penalised for gates they were never meant to have. In
`solid-haiku-v1` this made chant's four *lowest* scoring runs the four that no
gate ran on, while those same runs earned the *highest* idiom scores in the
set (0.70-0.94). The number said "worst"; the evidence said "unmeasured".

`correctness` is now dropped from numerator and denominator when
`attempted_stages == 0`, exactly as `completeness` is dropped when no
assertion was evaluated. A stage that ran and *failed* is a real zero and
stays (`tests/test_score_regression.py` pins both directions).

`safety` was the last axis still holding a free mark, and it was the largest
one. It defaulted to 1.0 whenever the semantic grader produced no verdict,
while keeping its weight of 2 against a 3-weight denominator. Measured on
coverage-v9: 28 of 84 runs are rubric tasks, not one of them carried a safety
verdict, and every one was therefore floored at 0.667 however badly the model
did — a third of each arm's runs with two thirds of the score handed over
before anything was read.

Dropping it had been tried before and deliberately reverted, on the grounds
that a rubric-only task with `safety` gone too would score exactly 0.0 on every
axis: "nothing was measured" rendered as "the model did terribly", the same
misleading number pointing the other way. #7 is what made that reasoning stale.
Idiom is now a real measurement on precisely the tasks safety cannot reach, so
dropping safety leaves those runs scored on the judge's verdict rather than on
nothing. The axis is now dropped unless the grader emitted `safety_pass`; the
four gated tasks all emit one, so their scores are untouched.

**This still does not make composites comparable across archetypes.** The two
groups are scored on disjoint axes, and no amount of axis bookkeeping changes
that:

| task group | earns | cannot earn |
| --- | --- | --- |
| T1-comprehend, T5-review | idiom | correctness, completeness, safety |
| T2 / T3 / T4 / T6 | correctness, completeness, safety | idiom |

A rubric task's composite is now exactly its idiom score. That is a narrower
measurement than a gated task's, not a weaker performance, and a per-stack
**mean composite remains partly a function of that stack's archetype mix**.
Compare like archetype with like; treat one averaged number per stack as a
summary, not a measurement.

Historical effect, measured by replaying every stored run: dropping
`correctness` moves rubric-only composites *upward*, dropping `safety` moves
them sharply *down*, and the second is much the larger. Per-arm means fell
between 0.005 (bare) and 0.151 (pulumi-python), **and the ranking changed** —
knr-ops and pulumi-typescript swap. The `increased == 0` invariant in
`test_score_regression.py` is scoped to the vacuous-pass guard and is unaffected
by either, because `_historical_results()` filters out runs carrying `skipped`
stages, which is precisely the rubric-only case.

## Mechanism map

| iac-cd-bench | ported from | what it fixes here |
| --- | --- | --- |
| `bench/preflight.py` `check()` | aws-bench `benchmarks/agent-env/preflight.py`, `aws_bench/cli/preflight.py` (`preflight_docker_cli` / `_daemon`) | #56, failure mode 1 |
| `preflight.probe_binary` version capture | aws-bench `tool-version.py` (last non-empty line, never a notice-as-version) | failure mode 5 |
| `_chant_package_version` reads the installed tree | aws-bench `arms.py` `_PKG_VERSION` — "read off the installed tree by path" | chant's pin vs its installed version |
| `--allow-missing-tools` → `partial` | chant-bench `gates.tool_missing` + the publish refusal | deliberate partial runs |
| `bench/provenance.py` `harness_commit()` incl. `dirty` | aws-bench `emit-result.py::git_commit` (`-dirty` suffix) | failure mode 6 |
| `task_fingerprint()` prompt/spec hashes | chant-bench `briefing.sha256`; aws-bench `emit-result.py` briefing block | failure mode 6 |
| `toolchain_fingerprint()` | aws-bench `prepare.py::workspace_fingerprint` (stat-only digest, cheap and stable) | failure mode 5 |
| `lint.inapplicable()` + `score.stage_inapplicable` | aws-bench `aggregation.py` `_tool_stats` (`success_ratio` 0 when no calls), `estate-check.py`'s "no instances at all" backstop | failure mode 3 |
| composite drops unmeasurable axes | aws-bench `emit-result.py` `"pass_rate": ... if trials else None` — null rather than a number | failure mode 3 |
| `bench/validity.py` | aws-bench `audit.py` classification ladder; chant-bench postflight audit | #59, failure mode 4 |
| `validate.classify_run` `tool_missing_scored_as_pass` | aws-bench `audit.py` `MISSING` / `MISSING_NAME` regexes | #56 in stored results |
| `validate.CRASH_LIMIT = 0.10` | chant-bench `validate_results.py::CRASH_LIMIT`, aws-bench `audit.py` — collapsed into one constant here, since aws-bench carries it twice and the copies can drift | errored-run denominators |
| uneven-k refusal | chant-bench `validate_results.py` trial-count homogeneity (mode of `expected_trials`) | smoke runs on the board |
| errored runs stay in the denominator | aws-bench `emit-result.py` `trials` / `expected_trials` / `completed` / `errored` | terraform-m1's 2/2 = 1.000 |
| `report.partition_by_validity` + `rejected: N` | chant-bench `build_pages.py::valid()` / `rate()` | failure mode 3, 4 |
| `--compare` comparability refusal | chant-bench `skills/chant-bench-results.md` ("same harness commit and briefing SHA"); the gap this closes is that chant-bench states the rule but never enforces it in code | failure mode 5, 6 |
| `python3 -m bench.validate` CLI | chant-bench `scripts/validate_results.py` | all of the above |

## Not yet guarded

- **Judge-model drift.** `judge_model` / `judge_prompt_sha256` are recorded and
  surfaced in the coverage table, but the comparability check does not refuse
  on them; two sets judged by different judges still render side by side.
- **Task-content drift across sets.** `task.prompt_sha256` is recorded per run,
  but `comparability()` compares harness/toolchain/provider/effort only. Two
  sets run against different versions of the same task's `prompt.md` would
  compare cleanly.
- **The `safety` heuristic is weak where it does run.** The free-mark half of
  this is fixed (rule 10 — the axis is dropped where no verdict exists), but on
  the four gated tasks the verdict itself comes from
  `"safety" not in output.lower()` in `bench/stages/semantic.py`. That is a
  string search, not a safety gate. An arm that passes it has not been shown to
  be safe; it has been shown not to mention the word.
- **A run measured on no axis at all scores 0.0 rather than nothing.** Every
  axis is now droppable, so this shape is reachable in principle. `bench.validate`
  rejects such runs and they contribute no number to any table, and
  `compute_score` guards the division, but the honest value is a sentinel and
  0.0 is not it.
- **The pulumi arms' static gate needs real AWS credentials.** `pulumi preview`
  is not offline: the AWS provider validates against STS before previewing
  anything, dummy values do not satisfy it, and `skipCredentialsValidation` did
  not take effect through the gate. So pulumi static passes on a machine with
  `~/.aws/credentials` and fails without — the environment deciding the verdict,
  which is #81's defect in new clothes. `tests/test_golden_gates.py` skips those
  arms when credentials are absent rather than reporting a gate defect, and CI
  therefore does not exercise them. Read a pulumi static verdict knowing it was
  produced somewhere with credentials.
- **Independence / tool-use evidence.** aws-bench audits the agent trajectory
  to prove the arm's own CLI ran. This harness is one-shot and has no
  trajectory, so there is nothing equivalent to audit; the validity gate is a
  weaker proxy that reads only the completion.
- **`consistency` is computed at aggregate level only** and is 0.0 in every
  per-run composite, which drags every composite down by a fixed amount rather
  than measuring anything.
- **Three graders still pass on their own unmodified seed.** terraform
  T4-debug's first two assertions, and pulumi-python T4-debug's two, are
  satisfied by strings the seed already contains (`aws_db_instance`,
  `deletion_protection`, the word `Secret` inside a defect comment). This is
  the opposite error to #72 — too loose rather than too strict — and it was
  deliberately left alone while fixing #72, because tightening it would move
  every historical terraform/pulumi-python T4 number in the same commit that
  moves the knr-ops ones. `tests/test_grader_robustness.py` records the
  current behaviour so a later fix is a visible change, not a silent one.
- **Four T3-modify tasks declare `assertion_count` but ship no grader**
  (crossplane, pulumi-python, pulumi-typescript, terraform). Their semantic
  stage is `inapplicable`, which rule 3 handles honestly, but the declared
  count is a promise the task does not keep.
- **No CI enforcement.** chant-bench gates its build on
  `validate_results.py` and on regenerating pages and diffing them. Nothing
  here runs `bench.validate` automatically.
- **Historical result sets are all refused.** Every set under `results/` fails
  validation today (no provenance, plus the stored `NOT FOUND: pulumi` passes).
  They are left on disk deliberately — they are the evidence — but no number
  from them should be quoted without re-running.
