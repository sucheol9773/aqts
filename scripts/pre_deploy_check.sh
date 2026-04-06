#!/usr/bin/env bash
#
# AQTS Pre-Deploy Verification Script
# =====================================
# Phase 0 배포 전 필수 검증을 자동으로 수행합니다.
#
# 검증 항목:
#   1. Git 상태 (미커밋 변경 없음)
#   2. 린트/포맷 검사 (ruff + black)
#   3. 전체 테스트 통과 + 커버리지 기준 충족
#   4. 문서-코드 동기화 (check_doc_sync.py)
#   5. Docker 이미지 빌드 가능 확인
#   6. 환경 변수 필수 항목 확인
#   7. Release Gates 상태 확인
#
# Usage:
#   bash scripts/pre_deploy_check.sh [--skip-docker] [--skip-tests]
#

set -euo pipefail

# ── 색상 ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0
SKIP_DOCKER=false
SKIP_TESTS=false

for arg in "$@"; do
    case $arg in
        --skip-docker) SKIP_DOCKER=true ;;
        --skip-tests) SKIP_TESTS=true ;;
    esac
done

log_pass() { echo -e "${GREEN}✓ PASS${NC}: $1"; ((PASS_COUNT++)); }
log_fail() { echo -e "${RED}✗ FAIL${NC}: $1"; ((FAIL_COUNT++)); }
log_warn() { echo -e "${YELLOW}⚠ WARN${NC}: $1"; ((WARN_COUNT++)); }
log_skip() { echo -e "${BLUE}→ SKIP${NC}: $1"; }
log_step() { echo -e "\n${BLUE}── $1 ──${NC}"; }

# ── 프로젝트 루트 이동 ──
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "=================================================="
echo "  AQTS Pre-Deploy Verification"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "=================================================="
echo ""
echo "Project root: $PROJECT_ROOT"

# ═══════════════════════════════════════════════════════
# 1. Git 상태 확인
# ═══════════════════════════════════════════════════════
log_step "1. Git 상태 확인"

if git diff --quiet && git diff --cached --quiet; then
    log_pass "미커밋 변경 없음"
else
    log_fail "미커밋 변경이 있습니다. 커밋 후 다시 실행하세요."
    git status --short
fi

CURRENT_BRANCH=$(git branch --show-current)
echo "  현재 브랜치: $CURRENT_BRANCH"
echo "  최근 커밋: $(git log --oneline -1)"

# ═══════════════════════════════════════════════════════
# 2. 린트/포맷 검사
# ═══════════════════════════════════════════════════════
log_step "2. 린트/포맷 검사"

cd "$PROJECT_ROOT/backend"

if python -m ruff check . --config pyproject.toml 2>&1 | grep -q "All checks passed"; then
    log_pass "ruff 린트 통과"
else
    log_fail "ruff 린트 위반 발견"
    python -m ruff check . --config pyproject.toml 2>&1 | head -20
fi

if python -m black --check . --config pyproject.toml 2>&1 | grep -q "would be left unchanged"; then
    log_pass "black 포맷 통과"
else
    log_fail "black 포맷 위반 발견"
fi

cd "$PROJECT_ROOT"

# ═══════════════════════════════════════════════════════
# 3. 전체 테스트 + 커버리지
# ═══════════════════════════════════════════════════════
log_step "3. 테스트 실행"

if [ "$SKIP_TESTS" = true ]; then
    log_skip "테스트 (--skip-tests 플래그)"
else
    cd "$PROJECT_ROOT/backend"

    TEST_OUTPUT=$(python -m pytest tests/ -q --tb=short --cov=. --cov-report=term-missing 2>&1)
    TEST_EXIT=$?

    if [ $TEST_EXIT -eq 0 ]; then
        # 테스트 수 추출
        TEST_COUNT=$(echo "$TEST_OUTPUT" | grep -oP '\d+ passed' | grep -oP '\d+')
        log_pass "전체 테스트 통과 (${TEST_COUNT}건)"

        # 커버리지 추출
        COVERAGE=$(echo "$TEST_OUTPUT" | grep "^TOTAL" | awk '{print $NF}' | sed 's/%//')
        if [ -n "$COVERAGE" ]; then
            if [ "$COVERAGE" -ge 80 ]; then
                log_pass "커버리지 ${COVERAGE}% (기준: >= 80%)"
            else
                log_fail "커버리지 ${COVERAGE}% (기준: >= 80%)"
            fi
        fi
    else
        FAIL_COUNT_TEST=$(echo "$TEST_OUTPUT" | grep -oP '\d+ failed' | grep -oP '\d+' || echo "?")
        log_fail "테스트 실패 (${FAIL_COUNT_TEST}건 실패)"
        echo "$TEST_OUTPUT" | tail -20
    fi

    cd "$PROJECT_ROOT"
fi

# ═══════════════════════════════════════════════════════
# 4. 문서-코드 동기화
# ═══════════════════════════════════════════════════════
log_step "4. 문서-코드 동기화"

DOC_SYNC_OUTPUT=$(python scripts/check_doc_sync.py --verbose 2>&1)
DOC_SYNC_EXIT=$?

if [ $DOC_SYNC_EXIT -eq 0 ]; then
    ERRORS=$(echo "$DOC_SYNC_OUTPUT" | grep "Errors:" | awk '{print $2}')
    WARNINGS=$(echo "$DOC_SYNC_OUTPUT" | grep "Warnings:" | awk '{print $2}')

    if [ "$ERRORS" = "0" ] && [ "$WARNINGS" = "0" ]; then
        log_pass "문서 동기화 완전 통과 (0 errors, 0 warnings)"
    elif [ "$ERRORS" = "0" ]; then
        log_warn "문서 동기화 통과 (0 errors, ${WARNINGS} warnings)"
    fi
else
    log_fail "문서 동기화 실패"
    echo "$DOC_SYNC_OUTPUT" | grep -E "ERROR|WARNING"
fi

# ═══════════════════════════════════════════════════════
# 5. Docker 이미지 빌드
# ═══════════════════════════════════════════════════════
log_step "5. Docker 빌드 확인"

if [ "$SKIP_DOCKER" = true ]; then
    log_skip "Docker 빌드 (--skip-docker 플래그)"
else
    if command -v docker &>/dev/null; then
        if [ -f "$PROJECT_ROOT/backend/Dockerfile" ]; then
            if docker build -f "$PROJECT_ROOT/backend/Dockerfile" -t aqts-backend:pre-check "$PROJECT_ROOT/backend" --quiet 2>&1; then
                log_pass "Docker 이미지 빌드 성공"
                # non-root 확인
                USER_CHECK=$(docker run --rm aqts-backend:pre-check whoami 2>/dev/null || echo "unknown")
                if [ "$USER_CHECK" = "appuser" ]; then
                    log_pass "Docker non-root 사용자 (appuser)"
                else
                    log_warn "Docker 사용자: $USER_CHECK (appuser 권장)"
                fi
            else
                log_fail "Docker 이미지 빌드 실패"
            fi
        else
            log_fail "Dockerfile 미발견: backend/Dockerfile"
        fi
    else
        log_warn "Docker가 설치되어 있지 않습니다"
    fi
fi

# ═══════════════════════════════════════════════════════
# 6. 환경 변수 확인
# ═══════════════════════════════════════════════════════
log_step "6. 환경 변수 확인"

ENV_FILE="$PROJECT_ROOT/.env"
if [ -f "$ENV_FILE" ]; then
    log_pass ".env 파일 존재"

    REQUIRED_VARS=(
        "DB_PASSWORD"
        "MONGO_PASSWORD"
        "REDIS_PASSWORD"
        "KIS_TRADING_MODE"
        "ANTHROPIC_API_KEY"
    )

    for var in "${REQUIRED_VARS[@]}"; do
        if grep -q "^${var}=" "$ENV_FILE" 2>/dev/null; then
            VALUE=$(grep "^${var}=" "$ENV_FILE" | cut -d'=' -f2-)
            if [ -n "$VALUE" ] && [ "$VALUE" != "your_*" ] && [ "$VALUE" != "changeme" ]; then
                log_pass "  $var 설정됨"
            else
                log_warn "  $var 값이 플레이스홀더입니다"
            fi
        else
            log_fail "  $var 미설정"
        fi
    done

    # KIS_TRADING_MODE 확인
    KIS_MODE=$(grep "^KIS_TRADING_MODE=" "$ENV_FILE" 2>/dev/null | cut -d'=' -f2-)
    if [ "$KIS_MODE" = "DEMO" ]; then
        log_pass "  KIS_TRADING_MODE=DEMO (Phase 1 적합)"
    elif [ "$KIS_MODE" = "LIVE" ]; then
        log_warn "  KIS_TRADING_MODE=LIVE (Phase 2 전환 확인 필요)"
    elif [ "$KIS_MODE" = "BACKTEST" ]; then
        log_warn "  KIS_TRADING_MODE=BACKTEST (배포 시 DEMO로 변경 필요)"
    fi
else
    log_warn ".env 파일 미발견 (배포 서버에서 생성 필요)"
fi

# ═══════════════════════════════════════════════════════
# 7. Release Gates 확인
# ═══════════════════════════════════════════════════════
log_step "7. Release Gates 확인"

GATES_FILE="$PROJECT_ROOT/docs/operations/release-gates.md"
if [ -f "$GATES_FILE" ]; then
    for gate in "Gate A" "Gate B" "Gate C" "Gate D" "Gate E"; do
        if grep -q "${gate}: PASS" "$GATES_FILE"; then
            log_pass "$gate: PASS"
        elif grep -q "${gate}: CONDITIONAL" "$GATES_FILE"; then
            log_warn "$gate: CONDITIONAL"
        elif grep -q "${gate}: BLOCK" "$GATES_FILE"; then
            log_fail "$gate: BLOCK"
        fi
    done
else
    log_fail "release-gates.md 미발견"
fi

# ═══════════════════════════════════════════════════════
# 결과 요약
# ═══════════════════════════════════════════════════════
echo ""
echo "=================================================="
echo "  검증 결과 요약"
echo "=================================================="
echo -e "  ${GREEN}PASS${NC}:    $PASS_COUNT"
echo -e "  ${RED}FAIL${NC}:    $FAIL_COUNT"
echo -e "  ${YELLOW}WARN${NC}:    $WARN_COUNT"
echo ""

if [ $FAIL_COUNT -eq 0 ]; then
    echo -e "${GREEN}══════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  ✓ PRE-DEPLOY CHECK PASSED — 배포 진행 가능${NC}"
    echo -e "${GREEN}══════════════════════════════════════════════════${NC}"
    exit 0
else
    echo -e "${RED}══════════════════════════════════════════════════${NC}"
    echo -e "${RED}  ✗ PRE-DEPLOY CHECK FAILED — ${FAIL_COUNT}건 수정 필요${NC}"
    echo -e "${RED}══════════════════════════════════════════════════${NC}"
    exit 1
fi
