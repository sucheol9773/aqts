#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
# AQTS DB 백업 스크립트
# ══════════════════════════════════════════════════════════════
# 용도: PostgreSQL + MongoDB 자동 백업 (cron 또는 수동 실행)
#
# 사용법:
#   ./scripts/backup_db.sh                    # 전체 백업 (PG + Mongo)
#   ./scripts/backup_db.sh --pg-only          # PostgreSQL만
#   ./scripts/backup_db.sh --mongo-only       # MongoDB만
#   ./scripts/backup_db.sh --upload           # 백업 후 GCS 업로드
#   ./scripts/backup_db.sh --upload --cleanup # 백업 + 업로드 + 로컬 정리
#
# 환경변수 (docker-compose .env에서 로드):
#   DB_USER, DB_PASSWORD, DB_NAME, DB_HOST, DB_PORT
#   MONGO_USER, MONGO_PASSWORD, MONGO_DB, MONGO_HOST, MONGO_PORT
#   GCS_BACKUP_BUCKET (업로드 시 필수)
#   BACKUP_RETENTION_DAYS (기본: 7)
# ══════════════════════════════════════════════════════════════

set -euo pipefail

# ── 설정 ──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="${BACKUP_DIR:-${PROJECT_ROOT}/backups}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"

# .env 로드 (있으면)
if [ -f "${PROJECT_ROOT}/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "${PROJECT_ROOT}/.env"
    set +a
fi

# DB 연결 정보
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

# GCS 설정
GCS_BUCKET="${GCS_BACKUP_BUCKET:-}"

# ── 플래그 파싱 ──
PG_BACKUP=true
MONGO_BACKUP=true
UPLOAD=false
CLEANUP=false

for arg in "$@"; do
    case $arg in
        --pg-only)    MONGO_BACKUP=false ;;
        --mongo-only) PG_BACKUP=false ;;
        --upload)     UPLOAD=true ;;
        --cleanup)    CLEANUP=true ;;
        --help|-h)
            head -20 "$0" | grep "^#" | sed 's/^# *//'
            exit 0
            ;;
        *)
            echo "Unknown option: $arg"
            exit 1
            ;;
    esac
done

# ── 디렉토리 생성 ──
mkdir -p "${BACKUP_DIR}/pg" "${BACKUP_DIR}/mongo"

# ── 로깅 ──
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# ══════════════════════════════════════
# PostgreSQL 백업
# ══════════════════════════════════════
pg_backup() {
    local dump_file="${BACKUP_DIR}/pg/aqts_pg_${TIMESTAMP}.sql.gz"
    log "PostgreSQL 백업 시작: ${PG_DB}@${PG_HOST}:${PG_PORT}"

    PGPASSWORD="${PG_PASSWORD}" pg_dump \
        -h "${PG_HOST}" \
        -p "${PG_PORT}" \
        -U "${PG_USER}" \
        -d "${PG_DB}" \
        --format=custom \
        --compress=6 \
        --verbose \
        --file="${dump_file%.gz}" \
        2>&1 | tail -5

    # custom format은 이미 압축되므로 .gz 제거
    local actual_file="${dump_file%.gz}"
    local size
    size=$(du -sh "${actual_file}" | cut -f1)
    log "PostgreSQL 백업 완료: ${actual_file} (${size})"
    echo "${actual_file}"
}

# ══════════════════════════════════════
# MongoDB 백업
# ══════════════════════════════════════
mongo_backup() {
    local dump_dir="${BACKUP_DIR}/mongo/aqts_mongo_${TIMESTAMP}"
    log "MongoDB 백업 시작: ${MONGO_DB_NAME}@${MONGO_HOST_ADDR}:${MONGO_PORT_NUM}"

    mongodump \
        --host="${MONGO_HOST_ADDR}" \
        --port="${MONGO_PORT_NUM}" \
        --username="${MONGO_USER_NAME}" \
        --password="${MONGO_PASSWORD_VAL}" \
        --authenticationDatabase=admin \
        --db="${MONGO_DB_NAME}" \
        --out="${dump_dir}" \
        --gzip \
        2>&1 | tail -5

    # tar로 묶기
    local archive="${dump_dir}.tar.gz"
    tar -czf "${archive}" -C "$(dirname "${dump_dir}")" "$(basename "${dump_dir}")"
    rm -rf "${dump_dir}"

    local size
    size=$(du -sh "${archive}" | cut -f1)
    log "MongoDB 백업 완료: ${archive} (${size})"
    echo "${archive}"
}

# ══════════════════════════════════════
# GCS 업로드
# ══════════════════════════════════════
upload_to_gcs() {
    local file="$1"
    local date_prefix
    date_prefix="$(date +%Y/%m/%d)"

    if [ -z "${GCS_BUCKET}" ]; then
        log "WARNING: GCS_BACKUP_BUCKET 미설정 — 업로드 건너뜀"
        return 1
    fi

    if ! command -v gsutil &>/dev/null; then
        log "WARNING: gsutil 미설치 — 업로드 건너뜀"
        return 1
    fi

    local dest="gs://${GCS_BUCKET}/aqts-backups/${date_prefix}/$(basename "${file}")"
    log "GCS 업로드: ${file} → ${dest}"
    gsutil -q cp "${file}" "${dest}"
    log "GCS 업로드 완료"
}

# ══════════════════════════════════════
# 로컬 오래된 백업 정리
# ══════════════════════════════════════
cleanup_old_backups() {
    log "로컬 백업 정리: ${RETENTION_DAYS}일 이상 된 파일 삭제"

    local count=0
    while IFS= read -r -d '' file; do
        rm -f "$file"
        ((count++))
    done < <(find "${BACKUP_DIR}" -type f \( -name "*.sql" -o -name "*.tar.gz" \) -mtime "+${RETENTION_DAYS}" -print0)

    log "정리 완료: ${count}개 파일 삭제"
}

# ══════════════════════════════════════
# 메인 실행
# ══════════════════════════════════════
main() {
    log "═══════════════════════════════════"
    log "AQTS DB 백업 시작"
    log "═══════════════════════════════════"

    local files=()

    if [ "${PG_BACKUP}" = true ]; then
        pg_file=$(pg_backup)
        files+=("${pg_file}")
    fi

    if [ "${MONGO_BACKUP}" = true ]; then
        mongo_file=$(mongo_backup)
        files+=("${mongo_file}")
    fi

    if [ "${UPLOAD}" = true ]; then
        for f in "${files[@]}"; do
            upload_to_gcs "${f}" || true
        done
    fi

    if [ "${CLEANUP}" = true ]; then
        cleanup_old_backups
    fi

    log "═══════════════════════════════════"
    log "AQTS DB 백업 완료"
    log "  파일: ${#files[@]}개"
    log "  위치: ${BACKUP_DIR}"
    log "═══════════════════════════════════"
}

main
