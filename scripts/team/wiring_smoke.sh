#!/usr/bin/env bash
# scripts/team/wiring_smoke.sh
#
# Validates that .claude/settings.json is structurally well-formed and contains
# the required guardrails for governance.md §2.5 lead-only files.
#
# This is a *structural* check — it does not launch Claude Code to verify
# actual permission enforcement. Runtime verification must be done manually
# by launching `claude` in each team worktree and attempting a forbidden
# Write/Edit. See verify command below.
#
# Exit codes:
#   0  all checks passed
#   1  structural failure (missing keys, malformed JSON, etc.)
#   2  usage error
#
# Usage:
#   scripts/team/wiring_smoke.sh         # default: check repo root settings.json
#   scripts/team/wiring_smoke.sh <path>  # check specified settings.json
set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

readonly REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
readonly SETTINGS_PATH="${1:-${REPO_ROOT}/.claude/settings.json}"

if [[ ! -f "${SETTINGS_PATH}" ]]; then
  echo "FAIL: settings.json not found at: ${SETTINGS_PATH}" >&2
  exit 1
fi

# JSON validity
if ! python3 -c "import json,sys; json.load(open(sys.argv[1]))" "${SETTINGS_PATH}" 2>/dev/null; then
  echo "FAIL: ${SETTINGS_PATH} is not valid JSON" >&2
  exit 1
fi

# Required guardrail checks — governance.md §2.5 lead-only paths must appear
# as Write(...) OR Edit(...) deny entries. We accept either with or without
# './' prefix.
readonly -a REQUIRED_LEAD_ONLY=(
  "CLAUDE.md"
  "agent_docs/development-policies.md"
  "backend/config/settings.py"
  "backend/core/utils/env.py"
  "backend/core/utils/time.py"
  ".env.example"
)

FAILED=0
for path in "${REQUIRED_LEAD_ONLY[@]}"; do
  if ! python3 -c "
import json, sys, re
data = json.load(open(sys.argv[1]))
deny = data.get('permissions', {}).get('deny', [])
target = sys.argv[2]
# Accept Write(./path), Write(path), Edit(./path), Edit(path)
patterns = [f'Write(./{target})', f'Write({target})', f'Edit(./{target})', f'Edit({target})']
if not any(p in deny for p in patterns):
    sys.exit(1)
" "${SETTINGS_PATH}" "${path}" 2>/dev/null; then
    echo "FAIL: lead-only path not guarded in deny list: ${path}" >&2
    FAILED=1
  fi
done

# Bash deny guardrails — force push & hard reset
readonly -a REQUIRED_BASH_DENY_SUBSTRINGS=(
  "git push --force"
  "git reset --hard"
)
for substr in "${REQUIRED_BASH_DENY_SUBSTRINGS[@]}"; do
  if ! python3 -c "
import json, sys
data = json.load(open(sys.argv[1]))
deny = data.get('permissions', {}).get('deny', [])
substr = sys.argv[2]
if not any(substr in entry for entry in deny if entry.startswith('Bash(')):
    sys.exit(1)
" "${SETTINGS_PATH}" "${substr}" 2>/dev/null; then
    echo "FAIL: Bash deny missing required substring: ${substr}" >&2
    FAILED=1
  fi
done

# Env guardrail — PYTHONUNBUFFERED=1 (CLAUDE.md §5 회귀 방지)
if ! python3 -c "
import json, sys
data = json.load(open(sys.argv[1]))
if data.get('env', {}).get('PYTHONUNBUFFERED') != '1':
    sys.exit(1)
" "${SETTINGS_PATH}" 2>/dev/null; then
  echo "FAIL: env.PYTHONUNBUFFERED='1' is required (CLAUDE.md §5 scheduler stdout silent miss 방지)" >&2
  FAILED=1
fi

if [[ "${FAILED}" != "0" ]]; then
  echo "" >&2
  echo "✗ WIRING SMOKE FAILED — fix the above and re-run." >&2
  exit 1
fi

echo "✓ WIRING SMOKE PASSED — settings.json structurally valid and guardrails present."
echo ""
echo "Manual verification (per worktree):"
echo "  1. cd <worktree path>"
echo "  2. claude"
echo "  3. Attempt: Write CLAUDE.md (should be denied by harness)"
echo "  4. Check session logs for deny confirmation."
