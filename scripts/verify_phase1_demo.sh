#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# AQTS Phase 1 DEMO 검증 스크립트
#
# 용도: 거래일(월~금)에 각 시점별 스케줄러 핸들러 실행 결과를 자동 확인.
#       서버에서 직접 실행하거나 gcloud compute ssh 로 원격 실행.
#
# 사용법:
#   ./scripts/verify_phase1_demo.sh              # 전체 검증
#   ./scripts/verify_phase1_demo.sh pre_market    # 08:30 구간만
#   ./scripts/verify_phase1_demo.sh market_close  # 15:30 구간만
#   ./scripts/verify_phase1_demo.sh post_market   # 16:00 구간만
#   ./scripts/verify_phase1_demo.sh exchange_rate # 환율 수집만
#   ./scripts/verify_phase1_demo.sh health        # 시스템 상태만
#
# 문서: docs/operations/phase1-demo-verification-2026-04-11.md
# ═══════════════════════════════════════════════════════════════════════

set -euo pipefail

# ── 색상 정의 ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# ── 카운터 ──
PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0

TODAY=$(TZ=Asia/Seoul date +%Y-%m-%d)
# docker compose logs --since 은 UTC 기준이므로, KST 00:00 = UTC 전날 15:00
TODAY_UTC=$(TZ=Asia/Seoul date -d "${TODAY} 00:00:00" -u +%Y-%m-%dT%H:%M:%S 2>/dev/null \
    || date -u -d "$(TZ=Asia/Seoul date +%Y-%m-%dT00:00:00%z)" +%Y-%m-%dT%H:%M:%S 2>/dev/null \
    || echo "${TODAY}T00:00:00")
COMPOSE="docker compose"

# ── 배포 전 로그 백업 디렉토리 ──
# CD 파이프라인이 --force-recreate 직전에 ~/aqts/logs/deploy-backups/ 에 백업한다.
# 컨테이너 재생성 후에는 docker compose logs 에 이전 이벤트 로그가 없으므로,
# 당일 백업 파일도 함께 검색하여 로그 유실로 인한 false-negative 를 방지한다.
LOG_BACKUP_DIR="${HOME}/aqts/logs/deploy-backups"

# 컨테이너 로그 + 당일 백업 로그를 합산하여 반환하는 함수
# 인자: container_name
_combined_logs() {
    local container="$1"
    # 1) 현재 컨테이너 로그
    $COMPOSE logs "$container" --since "${TODAY_UTC}" 2>/dev/null || true
    # 2) 당일 백업 로그 (파일명에 날짜가 YYYYMMDD 형태로 포함됨)
    local today_compact
    today_compact=$(echo "${TODAY}" | tr -d '-')
    if [ -d "${LOG_BACKUP_DIR}" ]; then
        for f in "${LOG_BACKUP_DIR}/${container}-pre-deploy-${today_compact}"*.log \
                 "${LOG_BACKUP_DIR}/${container}-pre-rollback-${today_compact}"*.log; do
            [ -f "$f" ] && cat "$f" 2>/dev/null || true
        done
    fi
}

# ── 유틸리티 함수 ──
pass() {
    PASS_COUNT=$((PASS_COUNT + 1))
    echo -e "  ${GREEN}✓${NC} $1"
}

fail() {
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo -e "  ${RED}✗${NC} $1"
}

warn() {
    WARN_COUNT=$((WARN_COUNT + 1))
    echo -e "  ${YELLOW}⚠${NC} $1"
}

info() {
    echo -e "  ${CYAN}ℹ${NC} $1"
}

header() {
    echo ""
    echo -e "${BOLD}━━━ $1 ━━━${NC}"
}

# 로그에서 오늘 날짜 기준으로 특정 패턴 검색
# 인자: container pattern [min_count]
check_log() {
    local container="$1"
    local pattern="$2"
    local description="$3"
    local min_count="${4:-1}"

    local count
    # 컨테이너 로그 + 당일 백업 로그를 합산 검색.
    # grep -c 대신 grep | wc -l 을 사용하여 멀티라인 카운트 문제를 방지한다.
    # pipefail 환경에서 grep 0건 매칭 시 exit 1 → 스크립트 종료 방지를 위해 || true
    count=$(_combined_logs "$container" \
        | { grep "$pattern" 2>/dev/null || true; } | wc -l)
    count=$((count + 0))  # 안전한 정수 변환

    if [ "$count" -ge "$min_count" ]; then
        pass "$description (${count}건)"
    else
        fail "$description (${count}건, 최소 ${min_count}건 필요)"
    fi
}

# 로그에서 오늘 날짜 기준으로 에러 패턴이 없는지 확인
check_no_error() {
    local container="$1"
    local pattern="$2"
    local description="$3"

    local count
    count=$(_combined_logs "$container" \
        | { grep "$pattern" 2>/dev/null || true; } | wc -l)
    count=$((count + 0))  # 안전한 정수 변환

    if [ "$count" -eq 0 ]; then
        pass "$description"
    else
        warn "$description (${count}건 에러 발견)"
    fi
}

# ══════════════════════════════════════════════════════════════════════
# 검증 구간별 함수
# ══════════════════════════════════════════════════════════════════════

verify_health() {
    header "시스템 상태 (Health Check)"

    # Docker 컨테이너 상태
    local running
    running=$($COMPOSE ps --format json 2>/dev/null | { grep '"running"' || true; } | wc -l)
    running=$((running + 0))
    if [ "$running" -ge 11 ]; then
        pass "Docker 컨테이너 전체 가동 (${running}개)"
    else
        fail "Docker 컨테이너 일부 미가동 (${running}/11)"
    fi

    # Backend health
    local health_status
    health_status=$(curl -sf http://localhost:8000/api/system/health 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('status', 'unknown'))
except:
    print('error')
" 2>/dev/null || echo "error")

    if [ "$health_status" = "healthy" ] || [ "$health_status" = "ok" ]; then
        pass "Backend health: ${health_status}"
    else
        fail "Backend health: ${health_status}"
    fi

    # Scheduler heartbeat — 별도 컨테이너이므로 Docker health status로 확인
    # (backend health API는 SCHEDULER_ENABLED=false라 "external" 반환)
    local sched_health
    sched_health=$($COMPOSE ps scheduler --format json 2>/dev/null | python3 -c "
import sys, json
try:
    for line in sys.stdin:
        d = json.loads(line.strip())
        # Health 필드: 'healthy', 'unhealthy', 'starting', '' (no healthcheck)
        h = d.get('Health', d.get('health', ''))
        s = d.get('State', d.get('state', ''))
        if h:
            print(f'{s}({h})')
        else:
            print(s)
        break
    else:
        print('not_found')
except:
    print('parse_error')
" 2>/dev/null || echo "error")

    sched_health=$(echo "$sched_health" | tr -d '[:space:]')
    if echo "$sched_health" | grep -q "healthy"; then
        pass "Scheduler 컨테이너: ${sched_health}"
    elif echo "$sched_health" | grep -q "running"; then
        warn "Scheduler 컨테이너: ${sched_health} (healthcheck 미설정 또는 starting)"
    else
        fail "Scheduler 컨테이너: ${sched_health}"
    fi

    # 스케줄러 이벤트 실행 확인 (오늘 거래일인 경우)
    # 거래일 시작 로그: "=== 거래일 2026-04-13 (#N) ==="
    # 또는 멱등성 복원 후 스케줄 완료 로그에 당일 날짜가 포함
    check_log "scheduler" "거래일 ${TODAY}\|멱등성 복원: ${TODAY}" "오늘(${TODAY}) 거래일 인식"
}

verify_pre_market() {
    header "08:30 PreMarket 검증"

    # 이벤트 시작/완료 (멱등성 복원 로그도 실행 증거로 인정)
    check_log "scheduler" "▶.*PRE_MARKET\|멱등성.*PRE_MARKET\|이미 실행된 이벤트.*PRE_MARKET" "PRE_MARKET 이벤트 실행 확인"
    check_log "scheduler" "✓.*PRE_MARKET.*완료\|멱등성.*PRE_MARKET\|이미 실행된 이벤트.*PRE_MARKET" "PRE_MARKET 이벤트 완료 확인"
    check_no_error "scheduler" "✗.*PRE_MARKET.*실패" "PRE_MARKET 이벤트 실패 없음"

    # Step 2: 뉴스 수집
    check_log "scheduler" "뉴스 수집 완료" "NewsCollector 뉴스 수집"
    check_no_error "scheduler" "뉴스 수집 실패" "NewsCollector 에러 없음"

    # Step 3: 경제지표
    check_log "scheduler" "경제지표 수집 완료" "FRED/ECOS 경제지표 수집"
    check_no_error "scheduler" "경제지표 수집 실패" "경제지표 에러 없음"

    # DB 저장 확인 (TimescaleDB)
    local econ_count
    econ_count=$(docker exec aqts-postgres bash -c \
        "psql -U aqts_user -d aqts -t -A -c \"SELECT count(*) FROM economic_indicators WHERE time::date = '${TODAY}'\"" \
        </dev/null 2>/dev/null || echo "0")
    econ_count=$(echo "$econ_count" | tr -d '[:space:]')

    if [ "$econ_count" -gt 0 ] 2>/dev/null; then
        pass "경제지표 DB 저장 (${econ_count}건)"
    else
        warn "경제지표 DB 저장 확인 불가 (${econ_count}건)"
    fi

    # 뉴스 MongoDB 저장 확인
    local news_count
    news_count=$(docker exec aqts-mongodb bash -c \
        "mongosh --quiet --eval 'db.news_articles.countDocuments({collected_at: {\$gte: new Date(\"${TODAY}\")}})' aqts" \
        </dev/null 2>/dev/null || echo "0")
    news_count=$(echo "$news_count" | tr -d '[:space:]')

    if [ "$news_count" -gt 0 ] 2>/dev/null; then
        pass "뉴스 MongoDB 저장 (${news_count}건)"
    else
        warn "뉴스 MongoDB 저장 확인 불가 (${news_count}건)"
    fi
}

verify_exchange_rate() {
    header "장중 환율 수집 검증"

    # 환율 로그 (환율 수집은 scheduler 컨테이너의 ExchangeRateCollectionLoop 에서 실행)
    check_log "scheduler" "환율 DB 저장\|\[ExchangeRate\] 수집 완료" "환율 수집 성공"
    check_no_error "scheduler" "환율.*실패\|ExchangeRate.*error\|ExchangeRate.*실패" "환율 수집 에러 없음"

    # DB 저장 확인
    local rate_count
    rate_count=$(docker exec aqts-postgres bash -c \
        "psql -U aqts_user -d aqts -t -A -c \"SELECT count(*) FROM exchange_rates WHERE time::date = '${TODAY}'\"" \
        </dev/null 2>/dev/null || echo "0")
    rate_count=$(echo "$rate_count" | tr -d '[:space:]')

    if [ "$rate_count" -gt 0 ] 2>/dev/null; then
        pass "환율 DB 저장 (${rate_count}건)"
    else
        warn "환율 DB 저장 확인 불가 (${rate_count}건)"
    fi
}

verify_market_close() {
    header "15:30 MarketClose 검증"

    # 이벤트 시작/완료 (멱등성 복원 로그도 실행 증거로 인정)
    check_log "scheduler" "▶.*MARKET_CLOSE\|멱등성.*MARKET_CLOSE\|이미 실행된 이벤트.*MARKET_CLOSE" "MARKET_CLOSE 이벤트 실행 확인"
    check_log "scheduler" "✓.*MARKET_CLOSE.*완료\|멱등성.*MARKET_CLOSE\|이미 실행된 이벤트.*MARKET_CLOSE" "MARKET_CLOSE 이벤트 완료 확인"
    check_no_error "scheduler" "✗.*MARKET_CLOSE.*실패" "MARKET_CLOSE 이벤트 실패 없음"

    # 핸들러 세부 — 정상 완료 또는 skip (KIS 실패 등으로 skip은 방어 동작)
    check_log "scheduler" "\[MarketClose\] 완료:" "MarketClose 핸들러 실행 완료"
    check_no_error "scheduler" "\[MarketClose\].*실패" "MarketClose 에러 없음"

    # KIS 실패에 의한 snapshot skip은 별도 경고로 표시
    local mc_skip
    mc_skip=$(_combined_logs scheduler \
        | { grep "\[MarketClose\].*skip" 2>/dev/null || true; } | wc -l)
    mc_skip=$((mc_skip + 0))
    if [ "$mc_skip" -gt 0 ]; then
        warn "MarketClose snapshot skip 발생 (${mc_skip}건 — KIS API 실패 등)"
    fi

    # 스냅샷 Redis 저장
    local snapshot
    snapshot=$(docker exec aqts-redis redis-cli GET "portfolio:snapshot:${TODAY}" </dev/null 2>/dev/null || echo "")

    if [ -n "$snapshot" ] && [ "$snapshot" != "(nil)" ]; then
        pass "포트폴리오 스냅샷 Redis 저장"
    else
        warn "포트폴리오 스냅샷 미확인 (키: portfolio:snapshot:${TODAY})"
    fi
}

verify_post_market() {
    header "16:00 PostMarket 검증"

    # 이벤트 시작/완료 (멱등성 복원 로그도 실행 증거로 인정)
    check_log "scheduler" "▶.*POST_MARKET\|멱등성.*POST_MARKET\|이미 실행된 이벤트.*POST_MARKET" "POST_MARKET 이벤트 실행 확인"
    check_log "scheduler" "✓.*POST_MARKET.*완료\|멱등성.*POST_MARKET\|이미 실행된 이벤트.*POST_MARKET" "POST_MARKET 이벤트 완료 확인"
    check_no_error "scheduler" "✗.*POST_MARKET.*실패" "POST_MARKET 이벤트 실패 없음"

    # 핸들러 세부 — 정상 완료 또는 skip (KIS 실패 등으로 skip은 에러가 아닌 방어 동작)
    check_log "scheduler" "\[PostMarket\] 완료:\|\[PostMarket\].*skip" "PostMarket 핸들러 실행 확인"
    check_no_error "scheduler" "\[PostMarket\].*실패" "PostMarket 에러 없음"

    # 텔레그램 발송 확인
    local telegram_ok
    telegram_ok=$(_combined_logs scheduler \
        | { grep "Telegram.*발송\|send_text.*success\|텔레그램.*완료" 2>/dev/null || true; } | wc -l)
    telegram_ok=$((telegram_ok + 0))

    local telegram_err
    telegram_err=$(_combined_logs scheduler \
        | { grep "Telegram 발송 실패\|텔레그램.*미설정" 2>/dev/null || true; } | wc -l)
    telegram_err=$((telegram_err + 0))

    if [ "$telegram_ok" -gt 0 ]; then
        pass "텔레그램 리포트 발송 (${telegram_ok}건)"
    elif [ "$telegram_err" -gt 0 ]; then
        fail "텔레그램 발송 실패 (${telegram_err}건 에러)"
    else
        warn "텔레그램 발송 기록 미확인"
    fi

    # 리포트 Redis 저장
    local report
    report=$(docker exec aqts-redis redis-cli GET "daily_report:${TODAY}" </dev/null 2>/dev/null || echo "")

    if [ -n "$report" ] && [ "$report" != "(nil)" ]; then
        pass "일일 리포트 Redis 저장"
    else
        warn "일일 리포트 미확인 (키: daily_report:${TODAY})"
    fi
}

# ══════════════════════════════════════════════════════════════════════
# 메인 실행
# ══════════════════════════════════════════════════════════════════════

echo -e "${BOLD}═══════════════════════════════════════════════════════${NC}"
echo -e "${BOLD} AQTS Phase 1 DEMO 검증 — ${TODAY}${NC}"
echo -e "${BOLD}═══════════════════════════════════════════════════════${NC}"

TARGET="${1:-all}"

case "$TARGET" in
    health)
        verify_health
        ;;
    pre_market)
        verify_health
        verify_pre_market
        ;;
    exchange_rate)
        verify_exchange_rate
        ;;
    market_close)
        verify_market_close
        ;;
    post_market)
        verify_post_market
        ;;
    all)
        verify_health
        verify_pre_market
        verify_exchange_rate
        verify_market_close
        verify_post_market
        ;;
    *)
        echo "사용법: $0 [health|pre_market|exchange_rate|market_close|post_market|all]"
        exit 1
        ;;
esac

# ── 요약 ──
echo ""
echo -e "${BOLD}━━━ 결과 요약 ━━━${NC}"
echo -e "  ${GREEN}PASS${NC}: ${PASS_COUNT}  ${RED}FAIL${NC}: ${FAIL_COUNT}  ${YELLOW}WARN${NC}: ${WARN_COUNT}"

if [ "$FAIL_COUNT" -gt 0 ]; then
    echo -e "\n  ${RED}${BOLD}일부 항목 실패. 로그를 확인하세요:${NC}"
    echo "  docker compose logs scheduler --since '${TODAY_UTC}' | less"
    exit 1
elif [ "$WARN_COUNT" -gt 0 ]; then
    echo -e "\n  ${YELLOW}경고 항목이 있습니다. 해당 시점이 아직 안 지났을 수 있습니다.${NC}"
    exit 0
else
    echo -e "\n  ${GREEN}${BOLD}전체 검증 통과!${NC}"
    exit 0
fi
