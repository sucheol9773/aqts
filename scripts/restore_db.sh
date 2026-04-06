#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
# AQTS DB 복원 스크립트
# ══════════════════════════════════════════════════════════════
# 용도: pg_dump 백업에서 PostgreSQL 복원, MongoDB 복원
#
# 사용법:
#   ./scripts/restore_db.sh --pg backups/pg/aqts_pg_20260407_030000.sql
#   ./scripts/restore_db.sh --mongo backups/mongo/aqts_mongo_20260407_030000.tar.gz
#   ./scripts/restore_db.sh --pg <file> --mongo <file>   # 둘 다 복원
#   ./scripts/restore_db.sh --list                        # 백업 목록 확인
#   ./scripts/restore_db.sh --gcs-list                    # GCS 백업 목록
#   ./scripts/restore_db.sh --gcs-download <gcs_path>     # GCS에서 다운로드
#
# WARNING: 복원은 기존 데이터를 덮어씁니다!
# ══════════════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="${BACKUP_DIR:-${PROJECT_ROOT}/backups}"

# .env 로드
if [ -f "${PROJECT_ROOT}/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "${PROJECT_ROOT}/.env"
    set +a
fi

PG_HOST="${DB_HOST:-localhost}"
PG_PORT="${DB_PORT:-5432}"
PG_USER="${DB_USER:-aqts_user}"
PG_DB="${DB_NAME:-aqts}"
PG_PASSWORD="${DB_PASSWORD:-}"

MONGO_HOST_ADDR="${MONGO_HOST:-localhost}"
MONGO_PORT_NUM="${MONGO_PORT:-27017}"
MONGO_USER_NAME="${MONGO_USER:-aqts_user}"
MONGO_DB_NAME="${MONGO_DB:-aqts}"
MONGO_PASSWORD_VAL="${MONGO_PASSWORD:-}"

GCS_BUCKET="${GCS_BACKUP_BUCKET:-}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# ── 플래그 파싱 ──
PG_FILE=""
MONGO_FILE=""
ACTION=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --pg)      PG_FILE="$2"; shift 2 ;;
        --mongo)   MONGO_FILE="$2"; shift 2 ;;
        --list)    ACTION="list"; shift ;;
        --gcs-list) ACTION="gcs-list"; shift ;;
        --gcs-download) ACTION="gcs-download"; GCS_PATH="$2"; shift 2 ;;
        --help|-h)
            head -15 "$0" | grep "^#" | sed 's/^# *//'
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# ── 백업 목록 ──
list_backups() {
    log "═══ 로컬 백업 목록 ═══"
    echo ""
    echo "PostgreSQL:"
    ls -lh "${BACKUP_DIR}/pg/" 2>/dev/null || echo "  (없음)"
    echo ""
    echo "MongoDB:"
    ls -lh "${BACKUP_DIR}/mongo/" 2>/dev/null || echo "  (없음)"
}

gcs_list() {
    if [ -z "${GCS_BUCKET}" ]; then
        log "ERROR: GCS_BACKUP_BUCKET 미설정"
        exit 1
    fi
    log "═══ GCS 백업 목록 ═══"
    gsutil ls -l "gs://${GCS_BUCKET}/aqts-backups/" 2>/dev/null || echo "  (없음 또는 접근 불가)"
}

gcs_download() {
    local gcs_path="$1"
    local dest="${BACKUP_DIR}/$(basename "${gcs_path}")"
    log "GCS 다운로드: ${gcs_path} → ${dest}"
    gsutil cp "${gcs_path}" "${dest}"
    log "다운로드 완료: ${dest}"
}

# ── PostgreSQL 복원 ──
restore_pg() {
    local file="$1"

    if [ ! -f "${file}" ]; then
        log "ERROR: 파일 없음: ${file}"
        exit 1
    fi

    log "═══ PostgreSQL 복원 시작 ═══"
    log "파일: ${file}"
    log "대상: ${PG_DB}@${PG_HOST}:${PG_PORT}"
    log ""
    log "WARNING: 기존 데이터가 덮어씌워집니다!"
    read -r -p "계속하시겠습니까? (yes/no): " confirm
    if [ "${confirm}" != "yes" ]; then
        log "복원 취소"
        exit 0
    fi

    # 기존 연결 종료
    log "기존 DB 연결 종료..."
    PGPASSWORD="${PG_PASSWORD}" psql \
        -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d postgres \
        -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${PG_DB}' AND pid <> pg_backend_pid();" \
        2>/dev/null || true

    # 복원 (pg_restore for custom format)
    log "pg_restore 실행..."
    PGPASSWORD="${PG_PASSWORD}" pg_restore \
        -h "${PG_HOST}" \
        -p "${PG_PORT}" \
        -U "${PG_USER}" \
        -d "${PG_DB}" \
        --clean \
        --if-exists \
        --verbose \
        "${file}" \
        2>&1 | tail -10

    log "PostgreSQL 복원 완료"
}

# ── MongoDB 복원 ──
restore_mongo() {
    local file="$1"

    if [ ! -f "${file}" ]; then
        log "ERROR: 파일 없음: ${file}"
        exit 1
    fi

    log "═══ MongoDB 복원 시작 ═══"
    log "파일: ${file}"
    log "대상: ${MONGO_DB_NAME}@${MONGO_HOST_ADDR}:${MONGO_PORT_NUM}"
    log ""
    log "WARNING: 기존 데이터가 덮어씌워집니다!"
    read -r -p "계속하시겠습니까? (yes/no): " confirm
    if [ "${confirm}" != "yes" ]; then
        log "복원 취소"
        exit 0
    fi

    # tar 풀기
    local tmp_dir
    tmp_dir=$(mktemp -d)
    tar -xzf "${file}" -C "${tmp_dir}"

    # mongorestore
    log "mongorestore 실행..."
    mongorestore \
        --host="${MONGO_HOST_ADDR}" \
        --port="${MONGO_PORT_NUM}" \
        --username="${MONGO_USER_NAME}" \
        --password="${MONGO_PASSWORD_VAL}" \
        --authenticationDatabase=admin \
        --db="${MONGO_DB_NAME}" \
        --gzip \
        --drop \
        "${tmp_dir}/${MONGO_DB_NAME}" \
        2>&1 | tail -10

    rm -rf "${tmp_dir}"
    log "MongoDB 복원 완료"
}

# ── 메인 ──
case "${ACTION}" in
    list)     list_backups; exit 0 ;;
    gcs-list) gcs_list; exit 0 ;;
    gcs-download) gcs_download "${GCS_PATH}"; exit 0 ;;
esac

if [ -z "${PG_FILE}" ] && [ -z "${MONGO_FILE}" ]; then
    echo "사용법: $0 --pg <file> [--mongo <file>]"
    echo "       $0 --list"
    echo "       $0 --help"
    exit 1
fi

if [ -n "${PG_FILE}" ]; then
    restore_pg "${PG_FILE}"
fi

if [ -n "${MONGO_FILE}" ]; then
    restore_mongo "${MONGO_FILE}"
fi

log "═══ 복원 작업 완료 ═══"
