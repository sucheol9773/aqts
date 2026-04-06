#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
# AQTS 카나리 배포 스크립트
# ══════════════════════════════════════════════════════════════════════
#
# 사용법:
#   bash scripts/canary_deploy.sh start     # 카나리 배포 시작 (10%)
#   bash scripts/canary_deploy.sh promote   # 비중 증가 (10→30→50→100%)
#   bash scripts/canary_deploy.sh rollback  # 카나리 즉시 롤백
#   bash scripts/canary_deploy.sh status    # 현재 상태 확인
#   bash scripts/canary_deploy.sh finish    # 카나리 종료 (100% 프로모션 후)
#

set -euo pipefail

# ── 색상 ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { echo -e "${BLUE}[INFO]${NC} $1"; }
log_ok()    { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ── 프로젝트 루트 ──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

COMPOSE_CMD="docker compose -f docker-compose.yml -f docker-compose.canary.yml"
NGINX_CONF="nginx/nginx-canary.conf"
CANARY_STATE_FILE=".canary_state"

# ── 현재 비중 읽기 ──
get_current_weight() {
    if [ -f "$CANARY_STATE_FILE" ]; then
        cat "$CANARY_STATE_FILE"
    else
        echo "0"
    fi
}

# ── 비중 단계 ──
WEIGHT_STAGES=(10 30 50 100)

get_next_weight() {
    local current=$1
    for w in "${WEIGHT_STAGES[@]}"; do
        if [ "$w" -gt "$current" ]; then
            echo "$w"
            return
        fi
    done
    echo "$current"
}

# ── nginx 비중 업데이트 ──
update_nginx_weight() {
    local weight=$1
    local canary_pct="${weight}%"

    # split_clients 블록에서 카나리 비중 수정
    if [ "$weight" -eq 100 ]; then
        # 100%: 모든 트래픽을 카나리로
        sed -i "s/[0-9]*%  backend_canary;/100% backend_canary;/" "$NGINX_CONF"
        sed -i "s/\*    backend_stable;/0%   backend_stable;/" "$NGINX_CONF"
    else
        sed -i "s/[0-9]*%  backend_canary;/${canary_pct}  backend_canary;/" "$NGINX_CONF"
        # * (나머지)는 그대로 유지
        sed -i "s/[0-9]*%   backend_stable;/*    backend_stable;/" "$NGINX_CONF"
    fi

    # X-Canary-Weight 헤더 업데이트
    sed -i "s/X-Canary-Weight \"[0-9]*\"/X-Canary-Weight \"${weight}\"/" "$NGINX_CONF"

    log_info "nginx 설정 업데이트: 카나리 ${weight}%"
}

# ── 헬스체크 ──
check_canary_health() {
    local port="${CANARY_PORT:-80}"
    local max_retries=3
    local retry=0

    while [ $retry -lt $max_retries ]; do
        # 카나리 직접 헬스체크
        if curl -sf "http://localhost:${port}/canary/api/system/health" > /dev/null 2>&1; then
            return 0
        fi
        retry=$((retry + 1))
        sleep 2
    done
    return 1
}

check_stable_health() {
    local port="${CANARY_PORT:-80}"
    if curl -sf "http://localhost:${port}/stable/api/system/health" > /dev/null 2>&1; then
        return 0
    fi
    return 1
}

# ══════════════════════════════════════
# 명령어 처리
# ══════════════════════════════════════

cmd_start() {
    echo "══════════════════════════════════════════════════════════"
    echo " AQTS 카나리 배포 시작"
    echo "══════════════════════════════════════════════════════════"
    echo

    # 사전 검증
    if [ ! -f .env ]; then
        log_error ".env 파일이 없습니다."
        exit 1
    fi

    # 안정 버전 태그 기록
    CURRENT_TAG=$(docker inspect --format='{{.Config.Image}}' aqts-backend 2>/dev/null || echo "unknown")
    echo "$CURRENT_TAG" > .last_stable_version
    log_ok "안정 버전 태그 기록: $CURRENT_TAG"

    # .env 백업
    cp .env ".env.backup.pre_canary_$(date +%Y%m%d_%H%M%S)"
    log_ok ".env 백업 완료"

    # 초기 비중: 10%
    update_nginx_weight 10
    echo "10" > "$CANARY_STATE_FILE"

    # 카나리 서비스 시작
    log_info "카나리 서비스 빌드 및 시작..."
    $COMPOSE_CMD up -d --build backend-canary backend-stable nginx

    # 안정화 대기
    log_info "서비스 안정화 대기 (최대 90초)..."
    local wait=0
    while [ $wait -lt 90 ]; do
        if check_canary_health && check_stable_health; then
            break
        fi
        sleep 5
        wait=$((wait + 5))
        echo -ne "  ${wait}s / 90s\r"
    done
    echo

    if check_canary_health; then
        log_ok "카나리 헬스체크 통과"
    else
        log_error "카나리 헬스체크 실패 — 롤백합니다"
        cmd_rollback
        exit 1
    fi

    if check_stable_health; then
        log_ok "안정 버전 헬스체크 통과"
    else
        log_warn "안정 버전 헬스체크 실패 — 확인 필요"
    fi

    echo
    log_ok "카나리 배포 시작 완료 (10% 트래픽)"
    echo
    echo "다음 단계:"
    echo "  1. 모니터링: bash scripts/canary_deploy.sh status"
    echo "  2. 비중 증가: bash scripts/canary_deploy.sh promote"
    echo "  3. 문제 발생: bash scripts/canary_deploy.sh rollback"
}

cmd_promote() {
    local current=$(get_current_weight)

    if [ "$current" -eq 0 ]; then
        log_error "카나리가 실행 중이 아닙니다. 먼저 start를 실행하세요."
        exit 1
    fi

    if [ "$current" -ge 100 ]; then
        log_warn "이미 100% 프로모션 완료. finish 명령으로 카나리를 종료하세요."
        exit 0
    fi

    # 프로모션 전 헬스체크
    if ! check_canary_health; then
        log_error "카나리 헬스체크 실패 — 프로모션 불가. 롤백을 검토하세요."
        exit 1
    fi

    local next=$(get_next_weight "$current")

    echo "══════════════════════════════════════════════════════════"
    echo " 카나리 프로모션: ${current}% → ${next}%"
    echo "══════════════════════════════════════════════════════════"

    update_nginx_weight "$next"
    echo "$next" > "$CANARY_STATE_FILE"

    # nginx 설정 리로드
    $COMPOSE_CMD exec nginx nginx -s reload
    log_ok "nginx 리로드 완료"

    # 프로모션 후 헬스체크
    sleep 5
    if check_canary_health; then
        log_ok "프로모션 완료: 카나리 ${next}%"
    else
        log_error "프로모션 후 헬스체크 실패"
        log_warn "롤백 검토: bash scripts/canary_deploy.sh rollback"
    fi

    if [ "$next" -eq 100 ]; then
        echo
        log_ok "100% 프로모션 완료!"
        echo "  안정 확인 후: bash scripts/canary_deploy.sh finish"
    fi
}

cmd_rollback() {
    echo "══════════════════════════════════════════════════════════"
    echo " 카나리 롤백"
    echo "══════════════════════════════════════════════════════════"

    # 트래픽을 안정 버전으로 100% 전환
    update_nginx_weight 0
    sed -i "s/0%  backend_canary;/0%   backend_canary;/" "$NGINX_CONF"
    sed -i "s/0%   backend_stable;/*    backend_stable;/" "$NGINX_CONF"

    # nginx 리로드 (가능한 경우)
    $COMPOSE_CMD exec nginx nginx -s reload 2>/dev/null || true

    # 카나리 컨테이너 중지
    log_info "카나리 컨테이너 중지..."
    $COMPOSE_CMD stop backend-canary 2>/dev/null || true
    $COMPOSE_CMD rm -f backend-canary 2>/dev/null || true

    # nginx/카나리 인프라 정리
    $COMPOSE_CMD stop nginx 2>/dev/null || true
    $COMPOSE_CMD rm -f nginx 2>/dev/null || true

    # 원래 비중으로 nginx 설정 복원
    update_nginx_weight 10
    echo "0" > "$CANARY_STATE_FILE"

    log_ok "카나리 롤백 완료 — 안정 버전으로 복귀"
    echo
    echo "다음 단계:"
    echo "  1. 안정 버전 확인: curl http://localhost:8000/api/system/health"
    echo "  2. 원인 분석 후 재시도"
}

cmd_status() {
    local weight=$(get_current_weight)
    local port="${CANARY_PORT:-80}"

    echo "══════════════════════════════════════════════════════════"
    echo " AQTS 카나리 배포 상태"
    echo " $(date '+%Y-%m-%d %H:%M:%S')"
    echo "══════════════════════════════════════════════════════════"
    echo

    echo "  카나리 비중: ${weight}%"
    echo "  안정 비중:   $((100 - weight))%"
    echo

    # 컨테이너 상태
    echo "── 컨테이너 상태 ──"
    $COMPOSE_CMD ps 2>/dev/null || echo "  (카나리 서비스 미실행)"
    echo

    # 헬스체크
    echo "── 헬스체크 ──"
    if check_stable_health; then
        echo -e "  안정 버전: ${GREEN}HEALTHY${NC}"
    else
        echo -e "  안정 버전: ${RED}UNHEALTHY${NC}"
    fi

    if [ "$weight" -gt 0 ]; then
        if check_canary_health; then
            echo -e "  카나리 버전: ${GREEN}HEALTHY${NC}"
        else
            echo -e "  카나리 버전: ${RED}UNHEALTHY${NC}"
        fi
    fi

    # nginx 상태
    if curl -sf "http://localhost:${port}/nginx-health" > /dev/null 2>&1; then
        echo -e "  nginx: ${GREEN}HEALTHY${NC}"
    else
        echo -e "  nginx: ${YELLOW}NOT RUNNING${NC}"
    fi
}

cmd_finish() {
    local weight=$(get_current_weight)

    if [ "$weight" -lt 100 ]; then
        log_error "아직 100% 프로모션이 완료되지 않았습니다 (현재: ${weight}%)"
        echo "  먼저: bash scripts/canary_deploy.sh promote"
        exit 1
    fi

    echo "══════════════════════════════════════════════════════════"
    echo " 카나리 → 안정 프로모션 완료"
    echo "══════════════════════════════════════════════════════════"

    # 카나리 이미지를 안정 태그로 재태그
    CANARY_IMG=$(docker inspect --format='{{.Config.Image}}' aqts-backend-canary 2>/dev/null || echo "")
    if [ -n "$CANARY_IMG" ]; then
        docker tag "$CANARY_IMG" aqts-backend:stable
        log_ok "카나리 이미지를 stable 태그로 승격"
    fi

    # 카나리 인프라 정리
    log_info "카나리 인프라 정리..."
    $COMPOSE_CMD stop nginx backend-canary backend-stable 2>/dev/null || true
    $COMPOSE_CMD rm -f nginx backend-canary backend-stable 2>/dev/null || true

    # 일반 모드로 서비스 재시작
    log_info "일반 모드로 서비스 재시작..."
    docker compose up -d backend

    rm -f "$CANARY_STATE_FILE"

    log_ok "카나리 프로모션 완료 — 일반 배포 모드로 복귀"
    echo
    echo "  .last_stable_version 파일이 업데이트되었습니다."
}

# ══════════════════════════════════════
# 메인
# ══════════════════════════════════════
ACTION="${1:-help}"

case "$ACTION" in
    start)    cmd_start ;;
    promote)  cmd_promote ;;
    rollback) cmd_rollback ;;
    status)   cmd_status ;;
    finish)   cmd_finish ;;
    *)
        echo "AQTS 카나리 배포 관리"
        echo
        echo "사용법: bash scripts/canary_deploy.sh <command>"
        echo
        echo "명령어:"
        echo "  start     카나리 배포 시작 (10% 트래픽)"
        echo "  promote   트래픽 비중 증가 (10→30→50→100%)"
        echo "  rollback  즉시 롤백 (안정 버전 복귀)"
        echo "  status    현재 배포 상태 확인"
        echo "  finish    카나리 프로모션 완료 (일반 모드 복귀)"
        ;;
esac
