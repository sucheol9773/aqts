#!/usr/bin/env bash
# scripts/team/mailbox_check_sla.sh [team_number|all]
#
# Scans mailbox inboxes for messages that exceed response SLA.
#
# SLA thresholds (governance.md §4.1):
#   [P0]            — 4 hours
#   [Ask]           — 24 hours
#   [Lead-Approval] — 48 hours
#   [FYI]           — no SLA (response not required, just move to processed/)
#
# Usage:
#   scripts/team/mailbox_check_sla.sh          # check all teams + lead
#   scripts/team/mailbox_check_sla.sh 2        # check team 2 only
#   scripts/team/mailbox_check_sla.sh lead     # check lead inbox only
#
# Exit codes:
#   0  all within SLA (or no messages)
#   1  SLA violation(s) found
#   2  usage error
set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

readonly REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
readonly MAILBOX_ROOT="${REPO_ROOT}/agent_docs/mailboxes"

if [[ ! -d "${MAILBOX_ROOT}" ]]; then
  echo "SKIP: mailbox directory not found at ${MAILBOX_ROOT}" >&2
  exit 0
fi

# SLA thresholds in seconds
readonly SLA_P0=$((4 * 3600))            # 4 hours
readonly SLA_ASK=$((24 * 3600))          # 24 hours
readonly SLA_LEAD_APPROVAL=$((48 * 3600))  # 48 hours

NOW_EPOCH=$(date +%s)
VIOLATIONS=0
CHECKED=0

# Determine which inboxes to scan
TARGET="${1:-all}"
if [[ "${TARGET}" == "all" ]]; then
  INBOX_DIRS=("${MAILBOX_ROOT}"/*/inbox/)
elif [[ "${TARGET}" =~ ^[1-4]$ ]]; then
  INBOX_DIRS=("${MAILBOX_ROOT}/team${TARGET}/inbox/")
elif [[ "${TARGET}" == "lead" ]]; then
  INBOX_DIRS=("${MAILBOX_ROOT}/lead/inbox/")
else
  echo "ERROR: argument must be 1-4, 'lead', or 'all' (got: '${TARGET}')" >&2
  exit 2
fi

for inbox_dir in "${INBOX_DIRS[@]}"; do
  [[ -d "${inbox_dir}" ]] || continue

  # Extract team name from path
  team_name=$(basename "$(dirname "${inbox_dir}")")

  for msg_file in "${inbox_dir}"*.md; do
    [[ -f "${msg_file}" ]] || continue
    CHECKED=$((CHECKED + 1))

    filename=$(basename "${msg_file}")

    # Parse priority from YAML frontmatter
    priority=$(grep -m1 '^priority:' "${msg_file}" 2>/dev/null | awk '{print $2}' || echo "")

    # Parse created timestamp from YAML frontmatter
    created=$(grep -m1 '^created:' "${msg_file}" 2>/dev/null | awk '{print $2}' || echo "")

    if [[ -z "${created}" ]]; then
      echo "  WARNING: ${team_name}/${filename} — created 필드 없음, SLA 판정 불가"
      continue
    fi

    # Convert ISO timestamp to epoch
    # macOS date and GNU date have different syntax; try both
    if created_epoch=$(date -j -f "%Y-%m-%dT%H:%M:%SZ" "${created}" +%s 2>/dev/null); then
      :
    elif created_epoch=$(date -d "${created}" +%s 2>/dev/null); then
      :
    else
      echo "  WARNING: ${team_name}/${filename} — created 파싱 실패: '${created}'"
      continue
    fi

    age=$((NOW_EPOCH - created_epoch))
    age_hours=$((age / 3600))

    # Determine SLA threshold based on priority
    case "${priority}" in
      P0)
        threshold=${SLA_P0}
        threshold_label="4h"
        ;;
      Ask)
        threshold=${SLA_ASK}
        threshold_label="24h"
        ;;
      Lead-Approval)
        threshold=${SLA_LEAD_APPROVAL}
        threshold_label="48h"
        ;;
      FYI)
        # FYI has no SLA, but warn if sitting for >7 days
        if [[ ${age} -gt $((7 * 86400)) ]]; then
          echo "  INFO: ${team_name}/${filename} — [FYI] ${age_hours}h in inbox (>7d, consider moving to processed/)"
        fi
        continue
        ;;
      *)
        echo "  WARNING: ${team_name}/${filename} — 알 수 없는 priority '${priority}', SLA 판정 생략"
        continue
        ;;
    esac

    if [[ ${age} -gt ${threshold} ]]; then
      echo "  ⚠ SLA VIOLATION: ${team_name}/${filename} — [${priority}] SLA ${threshold_label} 초과 (${age_hours}h elapsed)"
      VIOLATIONS=$((VIOLATIONS + 1))
    fi
  done
done

echo ""
if [[ ${CHECKED} -eq 0 ]]; then
  echo "✓ MAILBOX SLA CHECK — inbox 메시지 0건."
  exit 0
fi

if [[ ${VIOLATIONS} -gt 0 ]]; then
  echo "✗ MAILBOX SLA CHECK — ${VIOLATIONS} violation(s) / ${CHECKED} message(s) checked."
  echo "  응답하거나 processed/ 로 이동하세요."
  exit 1
fi

echo "✓ MAILBOX SLA CHECK — ${CHECKED} message(s) checked, all within SLA."
exit 0
