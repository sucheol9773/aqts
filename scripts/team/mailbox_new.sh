#!/usr/bin/env bash
# scripts/team/mailbox_new.sh <from_team> <to_team> <subject-slug>
#
# Creates a new mailbox message at:
#   agent_docs/mailboxes/team<to>/inbox/YYYYMMDD-HHMM-<subject-slug>.md
#
# Message is seeded with a front-matter header (from, to, subject, timestamp,
# priority placeholder) and prints the path on stdout so the caller can pipe
# it into an editor (e.g. $EDITOR "$(scripts/team/mailbox_new.sh 2 1 test)").
#
# Exit codes:
#   0  success (path printed on stdout)
#   2  usage error
#   3  environment error (repo root missing, mailbox dir uncreatable)
set -euo pipefail

if [[ $# -lt 3 || "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  echo "Usage: $0 <from_team 1-4|lead> <to_team 1-4|lead> <subject-slug>" >&2
  echo "       Creates agent_docs/mailboxes/team<to>/inbox/<timestamp>-<slug>.md" >&2
  exit 2
fi

readonly FROM="${1}"
readonly TO="${2}"
readonly SLUG="${3}"

if [[ ! "${FROM}" =~ ^[1-4]$ && "${FROM}" != "lead" ]]; then
  echo "ERROR: from_team must be 1, 2, 3, 4, or 'lead' (got: '${FROM}')" >&2
  exit 2
fi

if [[ ! "${TO}" =~ ^[1-4]$ && "${TO}" != "lead" ]]; then
  echo "ERROR: to_team must be 1, 2, 3, 4, or 'lead' (got: '${TO}')" >&2
  exit 2
fi

if [[ ! "${SLUG}" =~ ^[a-z0-9-]+$ ]]; then
  echo "ERROR: subject-slug must contain only [a-z0-9-] (got: '${SLUG}')" >&2
  exit 2
fi

readonly REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "${REPO_ROOT}" ]]; then
  echo "ERROR: not inside a git repository" >&2
  exit 3
fi

readonly TO_DIR_NAME="$([[ "${TO}" == "lead" ]] && echo "lead" || echo "team${TO}")"
readonly INBOX="${REPO_ROOT}/agent_docs/mailboxes/${TO_DIR_NAME}/inbox"
mkdir -p "${INBOX}"

readonly TS="$(date +%Y%m%d-%H%M)"
readonly PATH_OUT="${INBOX}/${TS}-${SLUG}.md"

cat > "${PATH_OUT}" <<EOF
---
from: ${FROM}
to: ${TO}
subject: ${SLUG}
created: $(date -u +%Y-%m-%dT%H:%M:%SZ)
priority: FYI  # [P0] 긴급, [Ask] 응답 요청, [FYI] 참고, [Lead-Approval] 리드 승인
---

# ${SLUG}

## 요약
<!-- 1-2줄 요약 -->

## 맥락
<!-- 왜 이 메시지가 필요한지, 관련 커밋/PR/파일 경로 -->

## 요청 / 정보
<!-- 구체적인 요청 또는 공유 정보 -->

## 응답 기한
<!-- "없음" 또는 구체 날짜 -->
EOF

echo "${PATH_OUT}"
