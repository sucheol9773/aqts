#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
# AQTS 배포 검증 스크립트 (Phase 0-4 완료 기준)
# 사용법: bash scripts/verify_deployment.sh
# ══════════════════════════════════════════════════════════════════════
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PASS=0
FAIL=0
WARN=0

check_pass() { echo -e "  ${GREEN}✓${NC} $1"; PASS=$((PASS + 1)); }
check_fail() { echo -e "  ${RED}✗${NC} $1"; FAIL=$((FAIL + 1)); }
check_warn() { echo -e "  ${YELLOW}!${NC} $1"; WARN=$((WARN + 1)); }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

echo "════════════════════════════════════════════════════════════"
echo " AQTS 배포 검증"
echo " $(date '+%Y-%m-%d %H:%M:%S')"
echo "════════════════════════════════════════════════════════════"
echo

# ══════════════════════════════════════
# 1. 컨테이너 상태 확인
# ══════════════════════════════════════
echo -e "${BLUE}[1/6] 컨테이너 상태${NC}"

CONTAINERS=("aqts-postgres" "aqts-mongodb" "aqts-redis" "aqts-backend")
for cname in "${CONTAINERS[@]}"; do
    STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$cname" 2>/dev/null || echo "not_found")
    if [[ "$STATUS" == "healthy" ]]; then
        check_pass "$cname: healthy"
    elif [[ "$STATUS" == "not_found" ]]; then
        check_fail "$cname: 컨테이너 없음"
    else
        check_fail "$cname: $STATUS"
    fi
done
echo

# ══════════════════════════════════════
# 2. API 헬스체크
# ══════════════════════════════════════
echo -e "${BLUE}[2/6] API 헬스체크${NC}"

BACKEND_PORT=$(grep -E "^BACKEND_PORT=" .env 2>/dev/null | cut -d'=' -f2- || echo "8000")
HEALTH_URL="http://localhost:${BACKEND_PORT}/api/system/health"

HEALTH_RESPONSE=$(curl -sf "$HEALTH_URL" 2>/dev/null || echo "FAIL")
if [[ "$HEALTH_RESPONSE" != "FAIL" ]]; then
    check_pass "GET /api/system/health → 200 OK"
else
    check_fail "GET /api/system/health → 연결 실패"
fi
echo

# ══════════════════════════════════════
# 3. 데이터베이스 연결 확인
# ══════════════════════════════════════
echo -e "${BLUE}[3/6] 데이터베이스 연결${NC}"

# PostgreSQL
PG_RESULT=$(docker exec aqts-postgres pg_isready -U "${DB_USER:-aqts_user}" -d "${DB_NAME:-aqts}" 2>/dev/null || echo "FAIL")
if echo "$PG_RESULT" | grep -q "accepting connections"; then
    check_pass "PostgreSQL 연결 정상"
else
    check_fail "PostgreSQL 연결 실패"
fi

# MongoDB
MONGO_RESULT=$(docker exec aqts-mongodb mongosh --quiet --eval "db.adminCommand('ping').ok" 2>/dev/null || echo "0")
if [[ "$MONGO_RESULT" == *"1"* ]]; then
    check_pass "MongoDB 연결 정상"
else
    check_fail "MongoDB 연결 실패"
fi

# Redis
REDIS_PASS=$(grep -E "^REDIS_PASSWORD=" .env 2>/dev/null | cut -d'=' -f2- || echo "")
REDIS_RESULT=$(docker exec aqts-redis redis-cli -a "$REDIS_PASS" ping 2>/dev/null || echo "FAIL")
if [[ "$REDIS_RESULT" == *"PONG"* ]]; then
    check_pass "Redis 연결 정상"
else
    check_fail "Redis 연결 실패"
fi
echo

# ══════════════════════════════════════
# 4. torch 버전 (CVE 해소 확인)
# ══════════════════════════════════════
echo -e "${BLUE}[4/6] torch CVE 해소 확인${NC}"

TORCH_VERSION=$(docker exec aqts-backend pip show torch 2>/dev/null | grep -oP 'Version: \K.*' || echo "")
if [[ -n "$TORCH_VERSION" ]]; then
    MAJOR=$(echo "$TORCH_VERSION" | cut -d'.' -f1)
    MINOR=$(echo "$TORCH_VERSION" | cut -d'.' -f2)
    if [[ "$MAJOR" -ge 2 && "$MINOR" -ge 6 ]] || [[ "$MAJOR" -ge 3 ]]; then
        check_pass "torch $TORCH_VERSION (>= 2.6.0, CVE 해소)"
    else
        check_fail "torch $TORCH_VERSION (2.6.0 미만, CVE 미해소)"
    fi

    # CPU 전용 확인
    CUDA_AVAILABLE=$(docker exec aqts-backend python -c "import torch; print(torch.cuda.is_available())" 2>/dev/null || echo "ERROR")
    if [[ "$CUDA_AVAILABLE" == "False" ]]; then
        check_pass "torch CPU 전용 빌드 확인"
    elif [[ "$CUDA_AVAILABLE" == "True" ]]; then
        check_warn "torch GPU 버전 설치됨 (CPU 전용 권장, 이미지 크기 증가)"
    else
        check_warn "torch CUDA 확인 불가"
    fi
else
    check_fail "torch 설치 확인 불가"
fi
echo

# ══════════════════════════════════════
# 5. 거래 모드 확인
# ══════════════════════════════════════
echo -e "${BLUE}[5/6] 거래 모드 확인${NC}"

KIS_MODE=$(grep -E "^KIS_TRADING_MODE=" .env 2>/dev/null | cut -d'=' -f2- || echo "UNKNOWN")
ENVIRONMENT=$(grep -E "^ENVIRONMENT=" .env 2>/dev/null | cut -d'=' -f2- || echo "UNKNOWN")

if [[ "$KIS_MODE" == "DEMO" ]]; then
    check_pass "KIS_TRADING_MODE=DEMO (모의 투자)"
elif [[ "$KIS_MODE" == "LIVE" ]]; then
    check_warn "KIS_TRADING_MODE=LIVE (실전 거래)"
elif [[ "$KIS_MODE" == "BACKTEST" ]]; then
    check_pass "KIS_TRADING_MODE=BACKTEST (백테스트)"
else
    check_fail "KIS_TRADING_MODE=$KIS_MODE (알 수 없는 모드)"
fi

if [[ "$ENVIRONMENT" == "production" && "$KIS_MODE" == "LIVE" ]]; then
    check_warn "is_live_trading=True — 실전 거래 활성 상태"
else
    check_pass "is_live_trading=False — 안전 모드"
fi
echo

# ══════════════════════════════════════
# 6. 텔레그램 알림 테스트
# ══════════════════════════════════════
echo -e "${BLUE}[6/6] 텔레그램 알림${NC}"

TELEGRAM_TOKEN=$(grep -E "^TELEGRAM_BOT_TOKEN=" .env 2>/dev/null | cut -d'=' -f2- || echo "")
TELEGRAM_CHAT=$(grep -E "^TELEGRAM_CHAT_ID=" .env 2>/dev/null | cut -d'=' -f2- || echo "")

if [[ -n "$TELEGRAM_TOKEN" && "$TELEGRAM_TOKEN" != *"your_"* && -n "$TELEGRAM_CHAT" && "$TELEGRAM_CHAT" != *"your_"* ]]; then
    # 테스트 메시지 발송 시도
    TG_RESULT=$(curl -sf "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage" \
        -d "chat_id=${TELEGRAM_CHAT}" \
        -d "text=🟢 [AQTS] 배포 검증 완료 ($(date '+%Y-%m-%d %H:%M'))" \
        -d "parse_mode=HTML" 2>/dev/null || echo "FAIL")
    if echo "$TG_RESULT" | grep -q '"ok":true'; then
        check_pass "텔레그램 테스트 메시지 발송 성공"
    else
        check_fail "텔레그램 발송 실패 — 봇 토큰/채팅 ID 확인 필요"
    fi
else
    check_warn "텔레그램 설정 미완료 (선택사항)"
fi
echo

# ══════════════════════════════════════
# 결과 요약
# ══════════════════════════════════════
echo "════════════════════════════════════════════════════════════"
echo -e " 결과: ${GREEN}PASS ${PASS}${NC} / ${RED}FAIL ${FAIL}${NC} / ${YELLOW}WARN ${WARN}${NC}"
echo "════════════════════════════════════════════════════════════"

if [[ $FAIL -eq 0 ]]; then
    echo -e "${GREEN}✓ Phase 0 배포 검증 통과${NC}"
    echo
    echo "Gate A/B torch CVE 해소 확인 후 CONDITIONAL → PASS 전환 가능"
    echo "다음 단계: Phase 1 DEMO 모드 검증 시작"
    echo "참조: docs/operations/deployment-roadmap.md"
    exit 0
else
    echo -e "${RED}✗ Phase 0 배포 검증 실패 (FAIL ${FAIL}건)${NC}"
    echo "위 FAIL 항목을 해소한 후 다시 실행하세요."
    exit 1
fi
