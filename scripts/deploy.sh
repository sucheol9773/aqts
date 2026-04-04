#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
# AQTS 배포 스크립트 (Phase 0)
# 사용법: bash scripts/deploy.sh [--prod]
#   --prod: 프로덕션 모드 (override 파일 제외)
#   기본값: 개발 모드 (override 파일 자동 병합)
# ══════════════════════════════════════════════════════════════════════
set -euo pipefail

# ── 색상 정의 ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info()  { echo -e "${BLUE}[INFO]${NC} $1"; }
log_ok()    { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ── 프로젝트 루트 이동 ──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# ── 모드 결정 ──
PROD_MODE=false
if [[ "${1:-}" == "--prod" ]]; then
    PROD_MODE=true
fi

echo "════════════════════════════════════════════════════════════"
echo " AQTS 배포 스크립트"
if $PROD_MODE; then
    echo " 모드: 프로덕션 (docker-compose.yml only)"
else
    echo " 모드: 개발 (docker-compose.yml + override)"
fi
echo "════════════════════════════════════════════════════════════"
echo

# ══════════════════════════════════════
# Step 1: 사전 조건 검증
# ══════════════════════════════════════
log_info "Step 1/6: 사전 조건 검증"

# Docker 설치 확인
if ! command -v docker &> /dev/null; then
    log_error "Docker가 설치되어 있지 않습니다."
    echo "  설치: https://docs.docker.com/engine/install/"
    exit 1
fi
log_ok "Docker $(docker --version | grep -oP '\d+\.\d+\.\d+')"

# Docker Compose 확인 (v2 plugin)
if ! docker compose version &> /dev/null; then
    log_error "Docker Compose v2가 설치되어 있지 않습니다."
    echo "  설치: https://docs.docker.com/compose/install/"
    exit 1
fi
log_ok "Docker Compose $(docker compose version --short)"

# Docker 데몬 실행 확인
if ! docker info &> /dev/null; then
    log_error "Docker 데몬이 실행되고 있지 않습니다."
    echo "  실행: sudo systemctl start docker"
    exit 1
fi
log_ok "Docker 데몬 실행 중"

# ══════════════════════════════════════
# Step 2: 환경변수 파일 검증
# ══════════════════════════════════════
log_info "Step 2/6: 환경변수 파일 검증"

if [[ ! -f .env ]]; then
    log_error ".env 파일이 존재하지 않습니다."
    echo "  1. cp .env.example .env"
    echo "  2. .env 파일을 열어 실제 값을 입력하세요."
    echo "  참조: docs/operations/docker-setup-guide.md 3절"
    exit 1
fi
log_ok ".env 파일 존재"

# 필수 환경변수 검증
REQUIRED_VARS=(
    "DB_PASSWORD"
    "MONGO_PASSWORD"
    "REDIS_PASSWORD"
    "ANTHROPIC_API_KEY"
)

MISSING_VARS=()
for var in "${REQUIRED_VARS[@]}"; do
    value=$(grep -E "^${var}=" .env 2>/dev/null | cut -d'=' -f2- || true)
    if [[ -z "$value" || "$value" == *"your_"* || "$value" == *"_here"* ]]; then
        MISSING_VARS+=("$var")
    fi
done

if [[ ${#MISSING_VARS[@]} -gt 0 ]]; then
    log_error "다음 환경변수가 설정되지 않았습니다:"
    for var in "${MISSING_VARS[@]}"; do
        echo "  - $var"
    done
    echo "  .env 파일을 편집하여 실제 값을 입력하세요."
    exit 1
fi
log_ok "필수 환경변수 설정 확인"

# KIS 모드 확인
KIS_MODE=$(grep -E "^KIS_TRADING_MODE=" .env 2>/dev/null | cut -d'=' -f2- || echo "UNKNOWN")
if $PROD_MODE && [[ "$KIS_MODE" == "LIVE" ]]; then
    log_warn "⚠️  KIS_TRADING_MODE=LIVE (실전 거래 모드)"
    echo "  실제 자금이 투입됩니다. 계속하시겠습니까? (y/N)"
    read -r confirm
    if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
        log_info "배포를 취소합니다."
        exit 0
    fi
else
    log_ok "KIS_TRADING_MODE=$KIS_MODE"
fi

# ══════════════════════════════════════
# Step 3: 기존 서비스 정리
# ══════════════════════════════════════
log_info "Step 3/6: 기존 서비스 정리"

if docker compose ps -q 2>/dev/null | grep -q .; then
    log_warn "기존 서비스가 실행 중입니다. 중지합니다..."
    if $PROD_MODE; then
        docker compose -f docker-compose.yml down
    else
        docker compose down
    fi
    log_ok "기존 서비스 중지 완료"
else
    log_ok "실행 중인 서비스 없음"
fi

# ══════════════════════════════════════
# Step 4: 이미지 빌드
# ══════════════════════════════════════
log_info "Step 4/6: Docker 이미지 빌드"

if $PROD_MODE; then
    docker compose -f docker-compose.yml build --no-cache backend
else
    docker compose build --no-cache backend
fi
log_ok "백엔드 이미지 빌드 완료"

# ══════════════════════════════════════
# Step 5: 서비스 시작
# ══════════════════════════════════════
log_info "Step 5/6: 서비스 시작"

if $PROD_MODE; then
    docker compose -f docker-compose.yml up -d
else
    docker compose up -d
fi

# 서비스 안정화 대기
log_info "서비스 안정화 대기 (최대 60초)..."
MAX_WAIT=60
WAIT=0
while [[ $WAIT -lt $MAX_WAIT ]]; do
    HEALTHY=$(docker compose ps --format json 2>/dev/null | grep -c '"healthy"' || true)
    TOTAL=$(docker compose ps -q 2>/dev/null | wc -l || echo 0)

    if [[ "$HEALTHY" -ge 4 ]]; then
        break
    fi

    sleep 5
    WAIT=$((WAIT + 5))
    echo -ne "  ${WAIT}s / ${MAX_WAIT}s — healthy: ${HEALTHY}/${TOTAL}\r"
done
echo

if [[ "$HEALTHY" -ge 4 ]]; then
    log_ok "전체 서비스 healthy ($HEALTHY/$TOTAL)"
else
    log_warn "일부 서비스가 아직 healthy 상태가 아닙니다 ($HEALTHY/$TOTAL)"
    docker compose ps
fi

# ══════════════════════════════════════
# Step 6: 배포 검증
# ══════════════════════════════════════
log_info "Step 6/6: 배포 검증"

# 헬스체크
BACKEND_PORT=$(grep -E "^BACKEND_PORT=" .env 2>/dev/null | cut -d'=' -f2- || echo "8000")
HEALTH_URL="http://localhost:${BACKEND_PORT}/api/system/health"

if curl -sf "$HEALTH_URL" > /dev/null 2>&1; then
    log_ok "API 헬스체크 통과 ($HEALTH_URL)"
else
    log_warn "API 헬스체크 실패 — 백엔드가 아직 시작 중일 수 있습니다"
    echo "  수동 확인: curl $HEALTH_URL"
fi

# torch 버전 확인
TORCH_VERSION=$(docker exec aqts-backend pip show torch 2>/dev/null | grep -oP 'Version: \K.*' || echo "확인 불가")
if [[ "$TORCH_VERSION" != "확인 불가" ]]; then
    MAJOR=$(echo "$TORCH_VERSION" | cut -d'.' -f1)
    MINOR=$(echo "$TORCH_VERSION" | cut -d'.' -f2)
    if [[ "$MAJOR" -ge 2 && "$MINOR" -ge 6 ]] || [[ "$MAJOR" -ge 3 ]]; then
        log_ok "torch $TORCH_VERSION (CVE 해소 ✓)"
    else
        log_warn "torch $TORCH_VERSION — 2.6.0 이상 필요 (CVE 미해소)"
    fi
else
    log_warn "torch 버전 확인 불가 — 백엔드 컨테이너 확인 필요"
fi

# 서비스 상태 출력
echo
echo "════════════════════════════════════════════════════════════"
echo " 배포 완료"
echo "════════════════════════════════════════════════════════════"
docker compose ps
echo
echo "다음 단계:"
echo "  1. bash scripts/verify_deployment.sh  # 상세 검증"
echo "  2. Phase 1 DEMO 모드 검증 시작"
echo "  참조: docs/operations/deployment-roadmap.md"
echo "════════════════════════════════════════════════════════════"
