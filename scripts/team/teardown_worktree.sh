#!/usr/bin/env bash
# scripts/team/teardown_worktree.sh <team_num>
#
# Safely removes a team worktree after checking for uncommitted work.
# Does NOT delete the branch (branch cleanup is separate, via git branch -d).
#
# Exit codes:
#   0  success (worktree removed)
#   2  usage error
#   3  environment error (worktree missing, uncommitted changes, etc.)
#
# Refuses to remove team 4 (ADR-002 Stage 2 Pilot).
set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

readonly TEAM_NUM="${1:-}"

if [[ ! "${TEAM_NUM}" =~ ^[1-4]$ ]]; then
  echo "ERROR: team number must be 1, 2, 3, or 4 (got: '${TEAM_NUM}')" >&2
  exit 2
fi

if [[ "${TEAM_NUM}" == "4" ]]; then
  echo "ERROR: team 4 worktree ('aqts-team4-skills-pilot') is reserved for ADR-002 Stage 2 Pilot" >&2
  echo "       and must not be torn down by this script until 2026-05-06." >&2
  exit 2
fi

case "${TEAM_NUM}" in
  1) ROLE="strategy" ;;
  2) ROLE="scheduler" ;;
  3) ROLE="api" ;;
esac

readonly REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "${REPO_ROOT}" ]]; then
  echo "ERROR: not inside a git repository" >&2
  exit 3
fi

readonly WT_NAME="aqts-team${TEAM_NUM}-${ROLE}"
readonly WT_PATH="${REPO_ROOT%/*}/${WT_NAME}"

if [[ ! -e "${WT_PATH}" ]]; then
  echo "ERROR: worktree path does not exist: ${WT_PATH}" >&2
  exit 3
fi

# Uncommitted changes check
cd "${WT_PATH}"
if ! git diff --quiet HEAD -- 2>/dev/null; then
  echo "ERROR: worktree has uncommitted changes: ${WT_PATH}" >&2
  echo "       Commit or stash them before running teardown." >&2
  git status --short >&2
  exit 3
fi

# Untracked files check (ignored files are fine)
if [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
  echo "ERROR: worktree has untracked files: ${WT_PATH}" >&2
  echo "       Commit, stash, or delete them before teardown." >&2
  git status --short >&2
  exit 3
fi

cd "${REPO_ROOT}"
echo "Removing worktree: ${WT_PATH}"
git worktree remove "${WT_PATH}"
echo "✓ Worktree removed."
echo ""
echo "Note: the branch itself was NOT deleted. To remove the branch:"
echo "  git branch -d team${TEAM_NUM}/<slug>     # safe (refuses if not merged)"
echo "  git branch -D team${TEAM_NUM}/<slug>     # force (use only if intended)"
