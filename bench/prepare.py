"""One-time shared setup, so parallel runners share nothing.

A runner writes only into its own temp workspace — with one exception per
stack, and those exceptions are what make two concurrent runners unsafe:

    chant              `ensure_chant_node_modules()` npm-installs into
                       golden-base/chant. Idempotent, but two cold starts race
                       on the same directory.
    pulumi-typescript  the same, into golden-base/pulumi-typescript.
    chant              `preflight_chant_golden()` runs lint and static against
                       golden-base/chant ITSELF. The build artifact used to
                       land in that directory (fixed in 5c85e4b), and the
                       node_modules it needs is the shared install above.

Run this once, serially, before fanning out. After it returns, `golden-base/`
is read-only as far as the runners are concerned and every worker is isolated:
its own `tempfile.mkdtemp` workspace, its own `PULUMI_BACKEND_URL` beneath it,
its own `.terraform`, its own venv.

Partition the fan-out on `(stack, condition)`. Results land at
`results/<model>-<tag>/<stack>/<condition>/<task>_run<N>.json`, so those units
write into disjoint subtrees and there is nothing to merge afterwards. Do NOT
partition within a cell by run index — that collides on `_run<N>`.

    python3 -m bench.prepare --stacks chant,knr-ops,bare,...

Exits non-zero if a stack's shared setup fails, so a driver can refuse to fan
out rather than discovering it 14 times in parallel.
"""

from __future__ import annotations

import argparse
import logging
import sys

from bench.report import STACKS
from bench.stages import e2e

log = logging.getLogger(__name__)

# Stacks whose setup writes outside a run's own workspace. Everything else
# needs nothing prepared.
SHARED_SETUP = ("chant", "pulumi-typescript")


def prepare(stacks: tuple[str, ...]) -> tuple[bool, list[str]]:
    """Do every shared write up front. Returns (ok, log lines)."""
    lines: list[str] = []
    ok = True

    if "chant" in stacks:
        try:
            golden = e2e.ensure_chant_node_modules()
            lines.append(f"chant node_modules ready: {golden}")
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            lines.append(f"chant node_modules FAILED: {exc}")
            return False, lines

        # Runs lint+static against golden-base/chant itself. Doing it here
        # means it happens once rather than once per worker, and no worker
        # touches the golden afterwards.
        verdict = e2e.preflight_chant_golden()
        if verdict.get("skipped"):
            lines.append("chant golden preflight: SKIPPED")
        elif verdict.get("passed"):
            lines.append("chant golden preflight: PASS")
        else:
            lines.append(f"chant golden preflight: FAIL\n{verdict.get('logs', '')}")
            ok = False

    if "pulumi-typescript" in stacks:
        try:
            golden = e2e.ensure_pulumi_typescript_node_modules()
            lines.append(f"pulumi-typescript node_modules ready: {golden}")
        except Exception as exc:  # noqa: BLE001
            lines.append(f"pulumi-typescript node_modules FAILED: {exc}")
            ok = False

    untouched = [s for s in stacks if s not in SHARED_SETUP]
    if untouched:
        lines.append(f"no shared setup needed: {', '.join(untouched)}")
    return ok, lines


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--stacks", default=",".join(STACKS),
                    help="comma-separated stacks the fan-out will cover")
    args = ap.parse_args()

    stacks = tuple(s.strip() for s in args.stacks.split(",") if s.strip())
    unknown = [s for s in stacks if s not in STACKS]
    if unknown:
        print(f"unknown stack(s): {', '.join(unknown)}", file=sys.stderr)
        return 2

    ok, lines = prepare(stacks)
    for line in lines:
        print(f"  {line}")
    print("-> READY" if ok else "-> NOT READY (do not fan out)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
