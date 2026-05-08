#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PLAN_PATH="PRD.md"
ARTIFACTS_DIR=".agent-loop"
MAX_ROUNDS=4
REVIEWER="codex"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/agent-loop.sh [options]

Runs a reviewer/fixer loop against a plan file.

Options:
  --plan PATH             Plan file to verify against. Default: PRD.md
  --max-rounds N          Maximum review/fix rounds. Default: 4
  --reviewer NAME         codex or claude. Fixed reviewer for every round. Default: codex
  --artifacts-dir PATH    Directory for prompts, findings, summaries, logs. Default: .agent-loop
  -h, --help              Show this help.

Examples:
  bash scripts/agent-loop.sh --reviewer codex --max-rounds 6
  bash scripts/agent-loop.sh --reviewer claude --max-rounds 3
EOF
}

die() {
  printf 'agent-loop: %s\n' "$*" >&2
  exit 1
}

need_command() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

opposite_agent() {
  case "$1" in
    codex) printf 'claude' ;;
    claude) printf 'codex' ;;
    *) die "unknown agent: $1" ;;
  esac
}

first_nonempty_line_matches() {
  local file="$1"
  local expected="$2"
  awk 'NF { print; exit }' "$file" | grep -Eq "^${expected}([[:space:]]|$)"
}

make_review_prompt() {
  local round="$1"
  local reviewer="$2"
  local fixer="$3"
  local prompt_file="$4"
  local previous_fix="${5:-}"
  local loop_label="automated Codex/Claude handoff loop"
  local next_fixer_label="$fixer"

  if [[ "$reviewer" == "claude" ]]; then
    loop_label="automated agent handoff loop"
    if [[ "$fixer" == "codex" ]]; then
      next_fixer_label="counterpart fixer"
    fi
  fi

  cat >"$prompt_file" <<EOF
You are the reviewer in an $loop_label.

Repository root: $ROOT_DIR
Plan file: $PLAN_PATH
Round: $round
Reviewer: $reviewer
Next fixer: $next_fixer_label
Previous fixer summary: ${previous_fix:-none}

Your job:
1. Verify whether the implementation in this codebase correctly satisfies the plan.
2. Do not edit files.
3. Report only concrete, plan-relevant implementation gaps or bugs.
4. Ignore speculative improvements, style preferences, and unrelated code health issues.
5. Treat AGENTS.md as repository policy.

Evidence rules:
- Every finding must cite at least one real file path.
- Prefer line numbers when available.
- Each finding must be tied to a specific plan section or acceptance requirement.
- Do not report an issue if the code already implements it.

Output format:
- If there are no findings, the first non-empty line must be exactly:
NO_FINDINGS
- Otherwise, the first non-empty line must be exactly:
FINDINGS
- After FINDINGS, write markdown with one finding per section:
  - ID
  - Severity: blocker, high, medium, or low
  - Plan reference
  - Evidence
  - Expected behavior
  - Actual behavior
  - Suggested fix
  - Suggested verification

Keep the output concise and actionable.
EOF
}

make_fix_prompt() {
  local round="$1"
  local fixer="$2"
  local reviewer="$3"
  local findings_file="$4"
  local prompt_file="$5"
  local loop_label="automated Codex/Claude handoff loop"
  local reviewer_label="$reviewer"

  if [[ "$fixer" == "claude" ]]; then
    loop_label="automated agent handoff loop"
    if [[ "$reviewer" == "codex" ]]; then
      reviewer_label="counterpart reviewer"
    fi
  fi

  cat >"$prompt_file" <<EOF
You are the validator/fixer in an $loop_label.

Repository root: $ROOT_DIR
Plan file: $PLAN_PATH
Round: $round
Fixer: $fixer
Reviewer that produced findings: $reviewer_label
Findings file: $findings_file

Your job:
1. Read the findings file, plan file, AGENTS.md, and relevant code.
2. Validate every finding before editing.
3. Fix only findings that are real, plan-relevant, and supported by code evidence.
4. Reject findings that are already implemented, speculative, unclear, or unrelated to the plan.
5. Do not commit changes.
6. Do not revert unrelated user changes.
7. Keep edits tightly scoped to validated findings.
8. Nothing that already works should break as a result of your changes.
9. Run the narrowest relevant verification commands from AGENTS.md or package scripts when practical.
10. If a relevant check cannot be run, explain exactly why.

Output format:
- Start with a short summary.
- Then include these sections:
  - Validated and fixed
  - Rejected findings
  - Tests run
  - Remaining risks or blockers

If no findings are valid, do not edit files. Say that clearly.
EOF
}

run_codex_review() {
  local prompt_file="$1"
  local output_file="$2"

  codex exec \
    -C "$ROOT_DIR" \
    -s read-only \
    -o "$output_file" \
    - <"$prompt_file"
}

run_codex_fix() {
  local prompt_file="$1"
  local output_file="$2"

  codex exec \
    -C "$ROOT_DIR" \
    -s workspace-write \
    -o "$output_file" \
    - <"$prompt_file"
}

run_claude_review() {
  local prompt_file="$1"
  local output_file="$2"

  claude -p \
    --permission-mode plan \
    --allowedTools "Read,Grep,Glob,Bash" \
    --output-format text \
    <"$prompt_file" >"$output_file"
}

run_claude_fix() {
  local prompt_file="$1"
  local output_file="$2"

  claude -p \
    --permission-mode acceptEdits \
    --allowedTools "Read,Grep,Glob,Edit,MultiEdit,Bash" \
    --output-format text \
    <"$prompt_file" >"$output_file"
}

run_reviewer() {
  local reviewer="$1"
  local prompt_file="$2"
  local output_file="$3"

  case "$reviewer" in
    codex) run_codex_review "$prompt_file" "$output_file" ;;
    claude) run_claude_review "$prompt_file" "$output_file" ;;
    *) die "unknown reviewer: $reviewer" ;;
  esac
}

run_fixer() {
  local fixer="$1"
  local prompt_file="$2"
  local output_file="$3"

  case "$fixer" in
    codex) run_codex_fix "$prompt_file" "$output_file" ;;
    claude) run_claude_fix "$prompt_file" "$output_file" ;;
    *) die "unknown fixer: $fixer" ;;
  esac
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --plan)
      PLAN_PATH="${2:-}"
      [[ -n "$PLAN_PATH" ]] || die "--plan requires a path"
      shift 2
      ;;
    --max-rounds)
      MAX_ROUNDS="${2:-}"
      [[ "$MAX_ROUNDS" =~ ^[0-9]+$ ]] || die "--max-rounds requires a positive integer"
      shift 2
      ;;
    --reviewer|--first-reviewer)
      REVIEWER="${2:-}"
      [[ "$REVIEWER" == "codex" || "$REVIEWER" == "claude" ]] || die "--reviewer must be codex or claude"
      shift 2
      ;;
    --artifacts-dir)
      ARTIFACTS_DIR="${2:-}"
      [[ -n "$ARTIFACTS_DIR" ]] || die "--artifacts-dir requires a path"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown option: $1"
      ;;
  esac
done

(( MAX_ROUNDS > 0 )) || die "--max-rounds must be greater than zero"

cd "$ROOT_DIR"

need_command codex
need_command claude
need_command git
need_command awk
need_command grep

[[ -f "$PLAN_PATH" ]] || die "plan file not found: $PLAN_PATH"
[[ -f "AGENTS.md" ]] || die "AGENTS.md not found at repository root"

mkdir -p "$ARTIFACTS_DIR"

git status --short >"$ARTIFACTS_DIR/initial-git-status.txt"

printf 'Agent loop starting\n'
printf '  repo: %s\n' "$ROOT_DIR"
printf '  plan: %s\n' "$PLAN_PATH"
printf '  artifacts: %s\n' "$ARTIFACTS_DIR"
printf '  max rounds: %s\n' "$MAX_ROUNDS"
printf '  reviewer: %s\n' "$REVIEWER"

previous_fix_file=""
fixer="$(opposite_agent "$REVIEWER")"

for (( round = 1; round <= MAX_ROUNDS; round++ )); do
  reviewer="$REVIEWER"
  round_prefix="$(printf 'round-%02d' "$round")"

  review_prompt="$ARTIFACTS_DIR/${round_prefix}-review-prompt.txt"
  review_output="$ARTIFACTS_DIR/${round_prefix}-findings.md"
  fix_prompt="$ARTIFACTS_DIR/${round_prefix}-fix-prompt.txt"
  fix_output="$ARTIFACTS_DIR/${round_prefix}-fix-summary.md"

  printf '\n[%s] %s reviewing, %s fixing if needed\n' "$round_prefix" "$reviewer" "$fixer"

  make_review_prompt "$round" "$reviewer" "$fixer" "$review_prompt" "$previous_fix_file"
  run_reviewer "$reviewer" "$review_prompt" "$review_output"

  if first_nonempty_line_matches "$review_output" "NO_FINDINGS"; then
    printf '[%s] reviewer reported no findings. Stopping.\n' "$round_prefix"
    git status --short >"$ARTIFACTS_DIR/final-git-status.txt"
    exit 0
  fi

  make_fix_prompt "$round" "$fixer" "$reviewer" "$review_output" "$fix_prompt"
  run_fixer "$fixer" "$fix_prompt" "$fix_output"
  previous_fix_file="$fix_output"

  git status --short >"$ARTIFACTS_DIR/${round_prefix}-post-fix-git-status.txt"
done

git status --short >"$ARTIFACTS_DIR/final-git-status.txt"
printf '\nReached max rounds (%s). Review %s for remaining findings.\n' "$MAX_ROUNDS" "$ARTIFACTS_DIR"
