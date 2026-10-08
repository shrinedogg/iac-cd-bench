#!/bin/bash
# Coverage pass, fanned out over (stack, condition).
#
# Why that partition and not another: results land at
#   results/<model>-<tag>/<stack>/<condition>/<task>_run<N>.json
# so (stack, condition) units write into disjoint subtrees — nothing collides
# and there is nothing to merge. Do NOT partition within a cell by run index;
# that collides on `_run<N>`.
#
# `bench.prepare` runs FIRST, serially. It performs every write that lands
# outside a run's own workspace — the npm installs into golden-base, and the
# chant golden preflight. After it returns, golden-base is read-only as far as
# the workers are concerned and each one is isolated: its own mkdtemp
# workspace, its own PULUMI_BACKEND_URL beneath it, its own .terraform, its
# own venv.
#
# Concurrency defaults to 4, not 14. The model provider throttles well before
# 14 concurrent streams, and a rate-limit storm would trip several chunks
# before anything noticed. Raise it deliberately.
#
#   tools/run_coverage_parallel.sh <tag> [jobs]
set -u

TAG="${1:?usage: run_coverage_parallel.sh <tag> [jobs]}"
JOBS="${2:-4}"
MODEL="${MODEL:-claude-haiku-4-5}"
PROVIDER="${PROVIDER:-claude-cli}"
EFFORT="${EFFORT:-low}"
K="${K:-1}"
STACKS="${STACKS:-chant knr-ops bare terraform crossplane pulumi-python pulumi-typescript}"
CONDITIONS="${CONDITIONS:-cold warm}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Logs live OUTSIDE the repo. `bench.provenance` computes `dirty` from
# `git status --porcelain`, which counts untracked files — so writing logs
# into the tree marks every run of this script dirty, and bench.validate
# treats that as a comparability break. Learned by doing it: a smoke run
# stamped dirty=True with no tracked file modified.
LOGS="${COVERAGE_LOGS:-${TMPDIR:-/tmp}}/iac-cd-bench-logs/$TAG"
mkdir -p "$LOGS"
cd "$ROOT" || exit 1

echo "=== $TAG on $(git rev-parse --short HEAD), jobs=$JOBS, k=$K ==="
echo "=== tree clean: $(git status --porcelain | grep -v '^?? ' | wc -l | tr -d ' ') modified ==="

# --- shared setup, once, before anything forks ------------------------------
echo "--- prepare (serial) ---"
if ! mise x -- python3 -m bench.prepare --stacks "$(echo $STACKS | tr ' ' ',')" 2>&1 | sed 's/^/    /'; then
  echo "ABORT: shared setup failed; refusing to fan out"
  exit 1
fi

# --- fan out ----------------------------------------------------------------
# A global kill switch, not just the per-chunk guard: in parallel a rate-limit
# storm hits several workers at once, and each would otherwise abort only
# itself while the rest keep spending.
ABORT="$LOGS/.abort"   # also outside the repo, same reason
rm -f "$ABORT"

run_cell () {
  local stack="$1" cond="$2"
  [ -f "$ABORT" ] && { echo "[skip] $stack/$cond (aborted)"; return 0; }
  local log="$LOGS/${stack}-${cond}.log"
  echo "[$(date +%H:%M:%S)] START $stack/$cond"
  mise x -- python3 -m bench.runner \
    --model "$MODEL" --model-provider "$PROVIDER" \
    --stack "$stack" --tasks all -k "$K" --condition "$cond" \
    --reasoning-effort "$EFFORT" \
    --judge --judge-model "$MODEL" --judge-provider "$PROVIDER" \
    --results-tag "$TAG" > "$log" 2>&1
  local rc=$?
  local dir="results/${MODEL}-${TAG}/${stack}/${cond}"
  local tot err
  tot=$(find "$dir" -name '*run*.json' 2>/dev/null | wc -l | tr -d ' ')
  err=$(grep -l '"error"' "$dir"/*run*.json 2>/dev/null | wc -l | tr -d ' ')
  echo "[$(date +%H:%M:%S)] DONE  $stack/$cond exit=$rc runs=$tot errors=$err"
  if [ "${tot:-0}" -gt 0 ] && [ "${err:-0}" -gt 0 ]; then
    if [ $(( err * 100 / tot )) -gt 25 ]; then
      echo "ABORT: $stack/$cond errored >25% — stopping every worker"
      touch "$ABORT"
    fi
  fi
}

pids=()
for stack in $STACKS; do
  for cond in $CONDITIONS; do
    while [ "$(jobs -rp | wc -l | tr -d ' ')" -ge "$JOBS" ]; do wait -n 2>/dev/null || sleep 1; done
    [ -f "$ABORT" ] && break 2
    run_cell "$stack" "$cond" &
    pids+=($!)
  done
done
wait

total=$(find "results/${MODEL}-${TAG}" -name '*run*.json' 2>/dev/null | wc -l | tr -d ' ')
echo "=== complete $(date) — ${total} runs ==="
[ -f "$ABORT" ] && { echo "=== ABORTED ==="; exit 9; }
exit 0
