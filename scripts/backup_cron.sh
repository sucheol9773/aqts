#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
# AQTS DB 백업 cron 엔트리포인트
# ══════════════════════════════════════════════════════════════
# docker-compose의 db-backup 서비스가 실행하는 스크립트.
# cron 대신 sleep 루프로 구현 (컨테이너에서 cron보다 안정적).
#
# 환경변수:
#   BACKUP_INTERVAL_HOURS (기본: 24) — 백업 주기
#   BACKUP_RETENTION_DAYS (기본: 7)  — 로컬 보관 기간
#   GCS_BACKUP_BUCKET (선택)         — GCS 업로드 버킷
# ══════════════════════════════════════════════════════════════

set -euo pipefail

INTERVAL_HOURS="${BACKUP_INTERVAL_HOURS:-24}"
INTERVAL_SECONDS=$((INTERVAL_HOURS * 3600))

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] BACKUP-CRON: $*"; }

log "AQTS DB 백업 cron 시작"
log "  주기: ${INTERVAL_HOURS}시간"
log "  보관: ${BACKUP_RETENTION_DAYS:-7}일"
log "  GCS: ${GCS_BACKUP_BUCKET:-미설정}"

# 시작 직후 첫 백업 실행
log "초기 백업 실행..."
/scripts/backup_db.sh --upload --cleanup || log "WARNING: 초기 백업 실패 (계속 진행)"

# 이후 주기적 실행
while true; do
    log "다음 백업까지 ${INTERVAL_HOURS}시간 대기..."
    sleep "${INTERVAL_SECONDS}"

    log "정기 백업 실행..."
    /scripts/backup_db.sh --upload --cleanup || log "WARNING: 정기 백업 실패 (계속 진행)"
done
