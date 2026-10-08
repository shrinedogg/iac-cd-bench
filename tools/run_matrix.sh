#!/usr/bin/env bash
#
# run_matrix.sh - launch tooling for iac-cd-bench#40 (chant vs knr-ops vs bare)
#
# Generates, and optionally executes, the exact bench.runner / bench.report
# invocation sequence signed off in issue #40:
#   - SMOKE:  one model, tasks/chant T1-comprehend, warm, k=1, judge on.
#   - FULL:   claude-opus-5 + claude-haiku-4-5, x {chant, knr-ops, bare},
#             x {cold, warm}, k=3, judge on (claude-haiku-4-5), reasoning
#             effort pinned "low" per model and recorded per run.
#
# Usage:
#   tools/run_matrix.sh smoke              # print (dry-run) the smoke command
#   tools/run_matrix.sh full               # print (dry-run) the full matrix
#   RUN_MATRIX_ACK=yes tools/run_matrix.sh smoke --execute
#   RUN_MATRIX_ACK=yes tools/run_matrix.sh full  --execute
#
# --execute NEVER fires without RUN_MATRIX_ACK=yes in the environment - this
# is deliberate, so the script can't fire live API calls by accident (e.g. by
# being run under a "just try it" invocation, or copy-pasted without the ack).
# With no --execute flag (the default), this script never touches the
# network: it only prints the commands it would run.
#
# ────────────────────────────────────────────────────────────────────────
# POST-MERGE SURFACE - READ BEFORE EDITING
# ────────────────────────────────────────────────────────────────────────
# This script is written against bench/runner.py and bench/report.py as they
# will look once every prerequisite PR below is merged into main - NOT as
# they look on main today (2026-08-25, iac-cd-bench@0f8b215). Some flags this
# script depends on do not exist on main yet:
#
#   #41 bench/chant-wiring  - registers "chant" in runner.py's all_stacks
#                             list; without it --stack chant silently no-ops
#                             (runner logs "Stack dir not found" and moves on)
#   #42 bench/idiom-judge   - adds --judge / --judge-model / --judge-provider
#                             / --judge-base-url to bench/runner.py, AND adds
#                             `bench/report.py --compare DIR [DIR...]`.
#                             report.py on main today takes ONLY --model; it
#                             has no --compare flag at all.
#   #45 bench/bare-tasks    - tasks/bare/{T1..T6}/ + golden-base/bare/
#   #46 bench/bare-wiring   - registers "bare" in all_stacks (stacked on the
#                             chant-wiring commits, NOT on #45 - both #45 and
#                             #46 must land for the bare arm to work)
#   #49 bench/chant-golden  - golden-base/chant/ (composites, SPEC, fixtures)
#   #50 bench/run-blockers  - closes #43/#44: knr-ops T1 answer-key fix,
#                             honest determinism README, and - load-bearing
#                             for this script's methodology - adds a
#                             `reasoning_effort` field to every run JSON so
#                             the pinned-effort-per-model methodology is
#                             auditable from results/ after the fact.
#   #47 (issue only, no PR yet as of writing) - golden-base/knr-ops uses
#                             upbound/Crossplane CRs instead of ACK, breaking
#                             cross-arm (bare vs knr-ops vs chant) resource
#                             comparability. The #40 sign-off comment gates
#                             the FULL run on this landing; SMOKE does not
#                             touch knr-ops so it is unaffected.
#   tasks/chant (#22-#25)   - in flight per the #40 comment thread; no branch
#                             has been pushed to origin as of writing. This
#                             worktree has an ad-hoc local `bench/chant-tasks`
#                             branch with tasks/chant/ + golden-base/chant/,
#                             but that branch is not on origin and is not a
#                             tracked PR - treat it as a preview, not a source
#                             of truth for when tasks/chant actually lands.
#
# --reasoning-effort itself IS already on main (bench/runner.py, argparse);
# only *recording* it into the run JSON (#50) and the --judge/--compare
# surface (#42) are still unmerged. If you're re-verifying this, run:
#   git diff main origin/bench/idiom-judge  -- bench/runner.py bench/report.py
#   git diff main origin/bench/run-blockers -- bench/runner.py
#   git diff main origin/bench/chant-wiring -- bench/runner.py bench/report.py
#   git diff main origin/bench/bare-wiring  -- bench/runner.py bench/report.py
#
# The preflight() function below re-checks the parts of this surface that
# are cheap to check statically (directory presence, --help flag presence)
# every time this script runs, and fails fast rather than silently
# no-op'ing a stack or dropping --judge on the floor.
# ────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"

# ── Configurable knobs (env-overridable; defaults match the #40 sign-off) ──
OPUS_MODEL="${OPUS_MODEL:-claude-opus-5}"
HAIKU_MODEL="${HAIKU_MODEL:-claude-haiku-4-5}"
JUDGE_MODEL="${JUDGE_MODEL:-claude-haiku-4-5}"
REASONING_EFFORT="${REASONING_EFFORT:-low}"
# Provider for the model under test and for the judge, independently
# overridable (bench/runner.py's --model-provider / --judge-provider both
# accept anthropic | openai-compat | claude-cli). Default anthropic matches
# runner.py's own flag default; this environment has no ANTHROPIC_API_KEY at
# all, so our actual runs pass claude-cli for both via env override.
MODEL_PROVIDER="${MODEL_PROVIDER:-anthropic}"
JUDGE_PROVIDER="${JUDGE_PROVIDER:-anthropic}"
# Smoke defaults to the cheap model: it's validating the live judge/extraction
# path, not scoring anything, and the owner's own framing ("the cheap model
# is the one that matters most") makes haiku the higher-value first live call
# anyway. Override with SMOKE_MODEL=claude-opus-5 if you want opus instead.
SMOKE_MODEL="${SMOKE_MODEL:-$HAIKU_MODEL}"
SMOKE_TAG="${SMOKE_TAG:-smoke}"
FULL_TAG_SUFFIX="${FULL_TAG_SUFFIX:-${REASONING_EFFORT}-3arm}"

ARMS=(chant knr-ops bare)
CONDITIONS=(cold warm)

# ── Preflight: fail fast on the known unmerged-branch gaps ──────────────
preflight_common() {
    local help_out
    if ! help_out="$($PYTHON -m bench.runner --help 2>&1)"; then
        echo "FATAL: 'python3 -m bench.runner --help' failed to run at all:" >&2
        echo "$help_out" >&2
        exit 1
    fi
    if ! grep -q -- '--judge' <<<"$help_out"; then
        echo "FATAL: bench/runner.py has no --judge flag." >&2
        echo "  This means bench/idiom-judge (PR #42) is not merged into" >&2
        echo "  your checkout yet. Merge/rebase onto it before running." >&2
        exit 1
    fi
    if ! grep -q -- '--reasoning-effort' <<<"$help_out"; then
        echo "FATAL: bench/runner.py has no --reasoning-effort flag (unexpected" >&2
        echo "  regression - this flag is already on main)." >&2
        exit 1
    fi
    if ! grep -q -- '--results-tag' <<<"$help_out"; then
        echo "FATAL: bench/runner.py has no --results-tag flag." >&2
        exit 1
    fi

    local report_help
    if ! report_help="$($PYTHON -m bench.report --help 2>&1)"; then
        echo "FATAL: 'python3 -m bench.report --help' failed to run." >&2
        exit 1
    fi
    if ! grep -q -- '--compare' <<<"$report_help"; then
        echo "FATAL: bench/report.py has no --compare flag." >&2
        echo "  This means bench/idiom-judge (PR #42) is not merged into" >&2
        echo "  your checkout yet - report.py on main is --model-only." >&2
        exit 1
    fi
}

preflight_chant() {
    if [[ ! -d "$ROOT/tasks/chant" ]]; then
        echo "FATAL: tasks/chant is absent." >&2
        echo "  tasks/chant (#22-#25) hasn't landed on any branch pushed to" >&2
        echo "  origin as of writing. It is a hard prerequisite for both the" >&2
        echo "  SMOKE run (which targets tasks/chant/T1-comprehend directly)" >&2
        echo "  and the FULL matrix (chant is one of the three arms)." >&2
        exit 1
    fi
    if [[ ! -d "$ROOT/golden-base/chant" ]]; then
        echo "FATAL: golden-base/chant is absent." >&2
        echo "  Needed by chant tasks' semantic stage / answer keys. Land" >&2
        echo "  bench/chant-golden (#49) first." >&2
        exit 1
    fi
    if [[ ! -d "$ROOT/tasks/chant/T1-comprehend" ]]; then
        echo "FATAL: tasks/chant/T1-comprehend is absent (tasks/chant exists" >&2
        echo "  but is incomplete). SMOKE needs this exact task directory." >&2
        exit 1
    fi
}

preflight_bare() {
    if [[ ! -d "$ROOT/tasks/bare" ]]; then
        echo "FATAL: tasks/bare is absent. Land bench/bare-tasks (#45) first." >&2
        exit 1
    fi
    if [[ ! -d "$ROOT/golden-base/bare" ]]; then
        echo "FATAL: golden-base/bare is absent. Land bench/bare-tasks (#45) first." >&2
        exit 1
    fi
}

preflight_knr_ops_ack() {
    # #47 is a soft gate: we can't statically detect "still upbound-flavored"
    # vs "rewritten to ACK" without a golden-file diff this script has no
    # business carrying, so this only checks knr-ops tasks/golden exist at
    # all and reminds the caller that #47 is a sign-off gate, not a file
    # presence gate.
    if [[ ! -d "$ROOT/tasks/knr-ops" || ! -d "$ROOT/golden-base/knr-ops" ]]; then
        echo "FATAL: tasks/knr-ops or golden-base/knr-ops is absent." >&2
        exit 1
    fi
    echo "NOTE: #47 (golden-base/knr-ops ACK-vs-upbound realignment) is a" >&2
    echo "  sign-off gate the owner tied to the FULL run, not something this" >&2
    echo "  script can verify by file presence. Confirm #47 has landed (or" >&2
    echo "  been explicitly waived) before running FULL with --execute." >&2
}

# ── Command builders ─────────────────────────────────────────────────────
# Each function prints one `python3 -m bench.runner ...` invocation per line
# to stdout; run() either echoes it (dry-run) or execs it (--execute).

smoke_commands() {
    cat <<EOF
$PYTHON -m bench.runner --model $SMOKE_MODEL --model-provider $MODEL_PROVIDER --stack chant --task T1-comprehend -k 1 --condition warm --judge --judge-model $JUDGE_MODEL --judge-provider $JUDGE_PROVIDER --reasoning-effort $REASONING_EFFORT --results-tag $SMOKE_TAG
EOF
}

full_commands() {
    local model tag arm cond
    for model in "$OPUS_MODEL" "$HAIKU_MODEL"; do
        tag="${model}-${FULL_TAG_SUFFIX}"
        for arm in "${ARMS[@]}"; do
            for cond in "${CONDITIONS[@]}"; do
                echo "$PYTHON -m bench.runner --model $model --model-provider $MODEL_PROVIDER --stack $arm --tasks all -k 3 --condition $cond --judge --judge-model $JUDGE_MODEL --judge-provider $JUDGE_PROVIDER --reasoning-effort $REASONING_EFFORT --results-tag $tag"
            done
        done
    done
}

report_commands() {
    # Emitted for visibility only - not executed by --execute (report
    # generation is idempotent and cheap; run it yourself once the matrix
    # finishes, or after inspecting partial results).
    local opus_tag="${OPUS_MODEL}-${FULL_TAG_SUFFIX}"
    local haiku_tag="${HAIKU_MODEL}-${FULL_TAG_SUFFIX}"
    cat <<EOF
$PYTHON -m bench.report --compare results/$opus_tag results/$haiku_tag
EOF
}

# ── Runner ────────────────────────────────────────────────────────────────

usage() {
    cat >&2 <<EOF
Usage: $0 <smoke|full> [--execute]

  smoke          print (default) or run the SMOKE invocation
  full           print (default) or run the FULL matrix invocations
  --execute      actually run the API calls (requires RUN_MATRIX_ACK=yes)

Env overrides: OPUS_MODEL HAIKU_MODEL JUDGE_MODEL REASONING_EFFORT
               MODEL_PROVIDER JUDGE_PROVIDER
               SMOKE_MODEL SMOKE_TAG FULL_TAG_SUFFIX PYTHON
EOF
    exit 1
}

main() {
    local target="${1:-}"
    local execute=0
    local cmds=""
    shift || true
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --execute) execute=1 ;;
            --dry-run) execute=0 ;;
            *) usage ;;
        esac
        shift
    done

    case "$target" in
        smoke)
            preflight_common
            preflight_chant
            cmds="$(smoke_commands)"
            ;;
        full)
            preflight_common
            preflight_chant
            preflight_bare
            preflight_knr_ops_ack
            cmds="$(full_commands)"
            ;;
        *)
            usage
            ;;
    esac

    if [[ "$execute" -eq 0 ]]; then
        echo "# DRY RUN ($target) - no API calls will be made. Re-run with" >&2
        echo "# RUN_MATRIX_ACK=yes $0 $target --execute to actually launch." >&2
        echo "$cmds"
        if [[ "$target" == "full" ]]; then
            echo ""
            echo "# Report generation, once results/ has both models' runs:"
            report_commands
        fi
        return 0
    fi

    if [[ "${RUN_MATRIX_ACK:-}" != "yes" ]]; then
        echo "FATAL: --execute given but RUN_MATRIX_ACK is not 'yes'." >&2
        echo "  This is deliberate: re-run as" >&2
        echo "    RUN_MATRIX_ACK=yes $0 $target --execute" >&2
        echo "  to confirm you mean to make live, billed API calls." >&2
        exit 1
    fi
    # ANTHROPIC_API_KEY is only load-bearing for the "anthropic" provider;
    # claude-cli shells out to the machine's existing Claude Code auth
    # instead (see bench/runner.py's ClaudeCliAdapter) and openai-compat
    # uses its own OPENAI_API_KEY / --api-key.
    if [[ "$MODEL_PROVIDER" == "anthropic" || "$JUDGE_PROVIDER" == "anthropic" ]] \
        && [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
        echo "FATAL: --execute given but ANTHROPIC_API_KEY is not set in the" >&2
        echo "  environment, and MODEL_PROVIDER=$MODEL_PROVIDER /" >&2
        echo "  JUDGE_PROVIDER=$JUDGE_PROVIDER needs it. bench.runner falls" >&2
        echo "  back to a placeholder key that will fail auth against the" >&2
        echo "  real API - set the key explicitly, or pass" >&2
        echo "  MODEL_PROVIDER=claude-cli / JUDGE_PROVIDER=claude-cli to use" >&2
        echo "  the machine's Claude Code auth instead." >&2
        exit 1
    fi

    echo "# EXECUTING ($target) - RUN_MATRIX_ACK=yes, live API calls follow." >&2
    local line
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        echo "+ $line" >&2
        eval "$line"
    done <<<"$cmds"
}

main "$@"
