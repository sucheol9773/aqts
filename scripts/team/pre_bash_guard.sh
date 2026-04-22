#!/usr/bin/env bash
# scripts/team/pre_bash_guard.sh
#
# PreToolUse hook for Claude Code — receives JSON on stdin describing the
# tool invocation and decides whether to allow or block Bash commands.
#
# Blocks (exit 2):
#   - `git push` targeting main/master with --force or --force-with-lease
#   - `git reset --hard` in the main repo (allowed in worktrees only if
#     explicitly acknowledged via AQTS_ALLOW_HARD_RESET=1)
#   - `rm -rf /`, `rm -rf ~`, `rm -rf *`, `rm -rf .`
#
# Allows (exit 0): everything else — the harness settings.json deny list is
# the primary defense; this script covers only the highest-risk destructive
# patterns that benefit from a semantic (not substring) check.
#
# Hook protocol reference:
#   https://docs.claude.com/en/docs/claude-code/hooks
#
# Exit codes:
#   0  allow (continue)
#   2  block (reason printed to stderr)
set -euo pipefail

INPUT="$(cat)"

# Only process Bash tool invocations.
TOOL_NAME="$(printf '%s' "${INPUT}" | python3 -c "import json,sys; print(json.load(sys.stdin).get('tool_name', ''))" 2>/dev/null || echo '')"
if [[ "${TOOL_NAME}" != "Bash" ]]; then
  exit 0
fi

COMMAND="$(printf '%s' "${INPUT}" | python3 -c "import json,sys; print(json.load(sys.stdin).get('tool_input', {}).get('command', ''))" 2>/dev/null || echo '')"

if [[ -z "${COMMAND}" ]]; then
  exit 0
fi

# Force push to main/master
if echo "${COMMAND}" | grep -qE 'git\s+push\s+.*(--force|--force-with-lease)\b.*(origin\s+)?(main|master)\b'; then
  echo "BLOCKED: force-push to main/master is disallowed by pre_bash_guard.sh" >&2
  echo "         Create a PR and merge via review instead." >&2
  exit 2
fi

if echo "${COMMAND}" | grep -qE 'git\s+push\s+.*(origin\s+)?(main|master)\s+.*(--force|--force-with-lease)\b'; then
  echo "BLOCKED: force-push to main/master is disallowed by pre_bash_guard.sh" >&2
  exit 2
fi

# Hard reset in main repo (not a worktree) — allowed with explicit ack
if echo "${COMMAND}" | grep -qE 'git\s+reset\s+--hard\b'; then
  if [[ "${AQTS_ALLOW_HARD_RESET:-}" != "1" ]]; then
    echo "BLOCKED: 'git reset --hard' requires explicit acknowledgment." >&2
    echo "         Set AQTS_ALLOW_HARD_RESET=1 in the session if intentional." >&2
    exit 2
  fi
fi

# rm -rf on dangerous targets
if echo "${COMMAND}" | grep -qE 'rm\s+-rf?\s+(/|~|\*|\.)(\s|$)'; then
  echo "BLOCKED: 'rm -rf' with root/home/wildcard/current-directory target is disallowed." >&2
  echo "         Use explicit paths instead (e.g. rm -rf ./build/)." >&2
  exit 2
fi

exit 0
