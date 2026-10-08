"""
Run-validity gate (#59).

Two independent ports of the same fix landed in parallel (bench/cli-provider-fix
and bench/integrity-gates) and were merged together on bench/run-ready rather
than picking a winner, because each has callers the other doesn't: bench.score
and the older report-generation path read the simple `run_validity`/
`check_validity` shape below; bench.validate, bench.preflight and bench.runner's
gate-refusal path read the richer `check_content`/`check_result` shape further
down. Both are kept; `bench.runner.run_task` stamps a run's `result["validity"]`
with the union of both verdicts (disjoint key sets, so `{**old, **new}` merges
cleanly) so every consumer finds the field it expects.

## Background

`ClaudeCliAdapter` shells out to `claude --print`, which runs Claude Code
itself. Even with tools disabled (`--tools ""`), models reliably reached for
the agentic framing baked into Claude Code's *default* system prompt instead
of answering: narrating an intent to explore the workspace, emitting
Claude-Code-specific tool-call markup for tools that were never wired up, or
stalling out asking for files/tool access instead of using what the prompt
already gave them. Since `--print` returns only the model's first turn as
`result`, that preamble (or a full but still-agentic transcript, sometimes
tens of thousands of characters of simulated `Read`/`Edit`/`Glob` calls) *is*
the run's entire recorded output.

Both classifiers below reject rather than score contaminated runs, in the
"chant-bench spirit": a run the gates reject is recorded with a reason and
excluded from scoring, never scored as a failure and never silently dropped.

## Part 1: `check_validity` / `check_run_validity` / `run_validity`

Calibrated from the 144-run #40 result set, `results/claude-*-3arm/`,
committed as provenance in the same commit as this module — see
`tools/classify_run_validity.py`.

- STRONG signals are literal leakage of Claude Code's own agent machinery
  into the answer text: tool-invocation markup (`<invoke name=...>`,
  `**Bash**`/`**Tool call: Bash**`, a `- Bash (...)` list-style tool call),
  the `★ Insight` UI divider, or a real filesystem path under this machine's
  `.claude/worktrees/agent-*` (the workspace a run is answered from is an
  isolated tempdir - a real worktree path can only appear if the model
  actually saw its own host environment). These never appear in a genuine
  prose/code answer, so they invalidate a run at any length - some
  contaminated transcripts ran to 50,000+ characters of simulated tool use
  and would pass a pure length check easily.
- WEAK signals are exploration/refusal phrasing ("I'll start by exploring",
  "I don't see the actual diff", "could you provide...") that also shows up,
  rarely, as an aside inside otherwise-complete answers. Those only
  invalidate a run when the run is ALSO short (< WEAK_PATTERN_FLOOR chars) -
  long answers that mention "let me check" once in passing are not stubs.
- An absolute floor (ABSOLUTE_FLOOR) rejects near-empty content outright,
  regardless of pattern match.

WEAK_PATTERN_FLOOR is set below the shortest genuine answer this dataset
produced for a non-quiz task and comfortably below the observed valid-run
median (~2500-3500 chars per arm), while staying above every confirmed stub
in the calibration set. T6-semantics (a short JSON-quiz task) has genuine
full answers as short as ~450 chars; because those carry no WEAK pattern
match, they clear the gate untouched by the floor. Recorded stubs in the
calibration set topped out at 996 chars.

## Part 2: `check_content` / `expects_artifacts` / `check_result`

The rule ported from chant-bench's postflight audit is that a trial which did
not measure the tool is not a low score, it is not a measurement — so this
half REJECTS rather than scores too, but classifies differently: structural
transcript markers (XML tool-call tags, Claude Code's own UI bullets, tool
names appearing in prose) are decisive on their own; narration phrases only
count once they cross a repetition/variety threshold; and a completion with
no fenced code block on a task whose enabled stages act on model-produced
files is flagged as the vacuous-pass generator described in issue #59 (lint
had no YAML to lint, static had nothing to build, so a stub scored 2 of 3).

Two independent classifiers live in this module, both closing issue #59 from
different angles and both kept because different callers need each shape:

- `check_validity`/`check_run_validity`/`run_validity` (above) run pattern
  calibration against the 144-run #40 set and are what `bench.runner` stamps
  onto a result at generation time and what `bench.score`/`bench.report`
  filter on directly.
- `check_content`/`expects_artifacts`/`check_result` (below) are the
  structural/narration classifier `bench.validate.classify_run` (#60) uses to
  re-derive a verdict for historical results and to catch the vacuous-pass
  case where a task expected an artifact and got prose instead.

`bench.validate.classify_run` is the single source of truth downstream: it
prefers a result's already-stamped `validity` block (this module's first
API), and otherwise recomputes with `check_result` (this module's second
API), so the two do not disagree silently — anything either classifier
rejects, `classify_run` rejects.

No network, no subprocess in either half: this is pure text classification
over the recorded completion, so both re-run identically over historical
result JSONs.
"""

from __future__ import annotations

import re
from typing import Any

# ─────────────────────────────────────────────────────────────────────────
# Part 1: check_validity / check_run_validity / run_validity
# (bench.score.aggregate_scores, the older report-generation helpers)
# ─────────────────────────────────────────────────────────────────────────

# Below this many (stripped) characters, content is invalid regardless of
# pattern match - there is no legitimate task answer this short.
ABSOLUTE_FLOOR = 50

# WEAK patterns only invalidate a run when content is also under this floor.
# Calibrated between the longest confirmed stub (996 chars, a "please share
# the diff" refusal) and the shortest genuine non-quiz answer in the
# calibration set (~1600+ chars); T6-semantics quiz answers run shorter but
# never trip a WEAK pattern, so they are unaffected by this floor.
WEAK_PATTERN_FLOOR = 1500

# Claude Code's own agent/tool-call machinery leaking into what should be a
# single-turn completion. Any of these invalidates a run at any length.
STRONG_PATTERNS: list[tuple[str, str]] = [
    (r"<invoke\s+name=", "tool_invocation_markup"),
    (r"</invoke>", "tool_invocation_markup"),
    (r"<parameter\s+name=", "tool_invocation_markup"),
    (r"\*\*(?:Tool call:\s*)?Bash\*\*", "tool_invocation_markup"),
    (r"★\s*Insight", "claude_code_ui_marker"),
    (r"^\s*-\s*Bash\s*\(", "tool_invocation_markup"),
    (r"\.claude/worktrees/agent-", "leaked_host_worktree_path"),
]
_STRONG_RE = re.compile(
    "|".join(f"(?P<p{i}>{pat})" for i, (pat, _label) in enumerate(STRONG_PATTERNS)),
    re.IGNORECASE | re.MULTILINE,
)


def _strong_reason(content: str) -> str | None:
    m = _STRONG_RE.search(content)
    if not m or m.lastgroup is None:
        return None
    idx = int(m.lastgroup[1:])  # named group "p{i}" -> i
    return STRONG_PATTERNS[idx][1]


# Exploration-intent or clarifying-refusal phrasing: a model reaching for
# tools it doesn't have, or asking for inputs it was already given (the
# prompt describes the diff/scenario inline; it never withholds it pending a
# follow-up). Only disqualifying when the run is also short - see module
# docstring.
_WEAK_RE = re.compile(
    r"\bi'?ll (?:start by |first )?(?:look|examine|explore|survey|check)\b|"
    r"\blet me (?:first )?(?:check|try|start)\b|"
    r"\bi (?:don't|do not) see the actual\b|"
    r"\bi need to see the actual\b|"
    r"\bcould you (?:please )?provide\b|"
    r"\bcan you provide\b|"
    r"\bonce you (?:share|provide)\b",
    re.IGNORECASE,
)


def check_validity(content: str | None) -> dict[str, Any]:
    """Classify one run's model output. Returns {"valid": bool, "reason": str|None}.

    `reason` is always populated when `valid` is False, and always None when
    `valid` is True - callers should treat that as the contract, not guess.
    """
    text = content or ""
    stripped = text.strip()

    if len(stripped) < ABSOLUTE_FLOOR:
        return {"valid": False, "reason": "empty_or_near_empty", "content_length": len(stripped)}

    strong = _strong_reason(text)
    if strong is not None:
        return {"valid": False, "reason": strong, "content_length": len(stripped)}

    if len(stripped) < WEAK_PATTERN_FLOOR and _WEAK_RE.search(text):
        return {"valid": False, "reason": "short_stub", "content_length": len(stripped)}

    return {"valid": True, "reason": None, "content_length": len(stripped)}


def check_run_validity(result: dict[str, Any]) -> dict[str, Any]:
    """Same as `check_validity`, reading `content` off a run result dict."""
    return check_validity(result.get("content"))


def run_validity(result: dict[str, Any]) -> dict[str, Any]:
    """Validity of an already-scored run, for use by score.py/report.py.

    Prefers a `validity` block the runner already stamped on the run JSON
    (recomputing would be redundant and, in principle, could drift from what
    was actually recorded). Falls back to computing fresh from `content` for
    result sets written before this gate existed - the 144-run #40 set among
    them (see tools/classify_run_validity.py).

    A run with no `content` key at all predates content being recorded, or
    is a synthetic test fixture — it cannot be judged by this gate, so it is
    treated as valid (grandfathered), not silently rejected.
    """
    existing = result.get("validity")
    if isinstance(existing, dict) and "valid" in existing:
        return existing
    if "content" not in result:
        return {"valid": True, "reason": None, "content_length": None}
    return check_validity(result.get("content"))


# ─────────────────────────────────────────────────────────────────────────
# Part 2: check_content / expects_artifacts / check_result
# (bench.validate, bench.preflight, bench.runner's gate-refusal path)
# ─────────────────────────────────────────────────────────────────────────

# Below this many characters the completion cannot contain a usable answer to
# any task in this suite (the shortest golden answer key is ~900 chars). Set
# deliberately low: this is a floor for "the provider returned nothing usable",
# not a quality bar.
MIN_CONTENT_CHARS = 200

# Structural markers: these appear only in an agent transcript, never in a
# completion. A single hit is decisive.
TRANSCRIPT_STRUCTURAL = [
    (re.compile(r"<function_calls>|<invoke\s+name=|<function_results>"), "xml tool-call markup"),
    (re.compile(r"^\s*[⏺●]\s", re.MULTILINE), "Claude Code tool-call bullets"),
    (re.compile(r"\bstr_replace_editor\b|\bTodoWrite\b|\bmulti_tool_use\b"), "tool names in prose"),
    (re.compile(r"^\s*(?:Tool|Function)\s+(?:use|call|result)\s*:", re.MULTILINE), "tool-call transcript header"),
    # First-person future tense naming a tool it is about to call. A model
    # writing an answer recommends a tool ("use kustomize for this"); only an
    # agent transcript says "I'll use the Read tool".
    (re.compile(r"\bI(?:'ll| will)\s+(?:now\s+)?(?:use|call|invoke)\s+the\s+\w+\s+tool\b", re.I), "announces a tool call"),
]

# Narration markers: individually these can appear in a legitimate answer
# ("Let me walk through the composition"), so they only count in aggregate.
TRANSCRIPT_NARRATION = [
    (re.compile(r"^\s*(?:Let me|I'll|I will)\s+(?:start by\s+)?(?:read|check|search|look|explor|examin|inspect)\w*\b", re.I | re.MULTILINE), "announces reading the workspace"),
    (re.compile(r"\bI (?:don't|do not) have (?:access to|any) (?:the )?(?:tools|filesystem|workspace)\b", re.I), "reports missing tool access"),
    (re.compile(r"^\s*(?:Reading|Searching|Editing|Writing|Listing)\s+(?:the\s+)?(?:file|director|workspace)", re.I | re.MULTILINE), "narrates a filesystem action"),
    (re.compile(r"\bI'll (?:begin|get started) by\b", re.I), "agentic preamble"),
]

# Two ways to clear the narration bar, because a transcript gives itself away
# either by variety or by repetition:
#   - two *different* narration markers, or
#   - three narration lines of any kind.
# Neither is one: models legitimately open a review answer with "Let me walk
# through what changed", and a single such line must not void a run that
# otherwise produced real artifacts. Swept against all 1140 historical result
# JSONs, these thresholds flag exactly one run — a genuine #59 preamble
# ("I'll start by exploring the project structure...") — and nothing else.
NARRATION_KINDS_THRESHOLD = 2
NARRATION_LINES_THRESHOLD = 3

FENCE_RE = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)


def _fenced_blocks(content: str) -> list[tuple[str, str]]:
    return [(m.group(1), m.group(2)) for m in FENCE_RE.finditer(content)]


def check_content(
    content: str | None,
    *,
    expects_artifacts: bool = False,
    extracted_files: list[Any] | None = None,
    min_chars: int = MIN_CONTENT_CHARS,
) -> dict[str, Any]:
    """Classify one completion.

    Args:
        content: the raw completion text the provider returned.
        expects_artifacts: True when the task's spec enables a stage that acts
            on files the model was supposed to produce (lint/static/e2e). For
            such a task a completion with no fenced code block and no extracted
            file is the vacuous-pass generator described in issue #59.
        extracted_files: what bench.runner.extract_code_blocks actually wrote.

    Returns a verdict dict:
        {"verdict": "valid"|"invalid", "reasons": [...], "checks": {...}}
    """
    reasons: list[str] = []
    text = content or ""
    stripped = text.strip()

    blocks = _fenced_blocks(text)
    checks: dict[str, Any] = {
        "content_chars": len(stripped),
        "fenced_blocks": len(blocks),
        "extracted_files": len(extracted_files or []),
        "min_chars": min_chars,
        "expects_artifacts": bool(expects_artifacts),
    }

    if not stripped:
        reasons.append("empty_completion: the provider returned no text at all")
    elif len(stripped) < min_chars:
        reasons.append(
            f"content_too_short: {len(stripped)} chars < {min_chars} floor — "
            "no task in this suite has a usable answer this short"
        )

    structural_hits = [label for rx, label in TRANSCRIPT_STRUCTURAL if rx.search(text)]
    narration_counts = {
        label: len(rx.findall(text))
        for rx, label in TRANSCRIPT_NARRATION
        if rx.search(text)
    }
    checks["transcript_structural"] = structural_hits
    checks["transcript_narration"] = narration_counts

    if structural_hits:
        reasons.append(
            "agent_transcript: completion carries tool-call markup "
            f"({', '.join(structural_hits)}) — the provider returned a "
            "transcript, not an answer (#59)"
        )
    elif (
        len(narration_counts) >= NARRATION_KINDS_THRESHOLD
        or sum(narration_counts.values()) >= NARRATION_LINES_THRESHOLD
    ):
        detail = ", ".join(f"{label} x{n}" for label, n in sorted(narration_counts.items()))
        reasons.append(
            f"agent_transcript: completion narrates agent actions ({detail}) "
            "rather than answering (#59)"
        )

    if expects_artifacts and not blocks and not (extracted_files or []):
        reasons.append(
            "no_extractable_output: the task's enabled stages act on files the "
            "model was to produce, and the completion contains no code block — "
            "lint and static would have nothing to check and would pass vacuously"
        )

    return {
        "verdict": "invalid" if reasons else "valid",
        "reasons": reasons,
        "checks": checks,
    }


def expects_artifacts(spec: dict[str, Any] | None) -> bool:
    """Whether a task's spec enables a stage that acts on model-produced files.

    Mirrors bench.runner._stage_enabled's default-True semantics, so a spec
    without a `stages:` block is treated as expecting artifacts.
    """
    if not spec:
        return True
    stages = spec.get("stages") or {}
    for name in ("lint", "static", "e2e"):
        stage = stages.get(name) or {}
        if bool(stage.get("enabled", True)):
            return True
    return False


def check_result(result: dict[str, Any], spec: dict[str, Any] | None = None) -> dict[str, Any]:
    """Re-derive a validity verdict from a stored result JSON.

    Used by bench.validate to classify historical runs written before the gate
    existed, so old result sets get the same verdict a fresh run would.
    """
    return check_content(
        result.get("content"),
        expects_artifacts=expects_artifacts(spec) if spec is not None else False,
        extracted_files=result.get("extracted_files"),
    )
