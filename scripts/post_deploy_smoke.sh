#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
# AQTS post-deploy smoke test — 2026-04-08 회귀 contract 강제
# ══════════════════════════════════════════════════════════════════════
#
# 목적: `docker compose up -d` 이후 서버에 실제로 배포된 산출물이 다음
# 계약을 만족하는지 최종 관문에서 검증한다. CD 워크플로의 digest
# assertion(Step 5e) 과 중복되는 항목도 일부 있지만, 본 스크립트는 서버
# 쪽에서도 독립적으로 재실행 가능한 "운영자 수동 점검" 도구를 겸한다.
#
# 계약 (모두 0 tolerance):
#   C1. aqts-backend / aqts-scheduler 컨테이너가 존재하고 State.Running=true
#   C2a. 두 컨테이너의 Image(digest) 가 서로 일치 — drift 금지
#   C2b. 두 컨테이너의 `org.opencontainers.image.revision` 라벨이
#        서버 git HEAD 와 일치 — "두 컨테이너가 같은 구 digest 로 고정된 채
#        새 이미지가 한 번도 기동되지 않은 상태" 를 C2a 가 drift 없음으로
#        통과시키는 2026-04-09 §4.7/§4.8 회귀의 위양성 차단.
#   C3. aqts-scheduler 의 Config.Healthcheck.Test 가 heartbeat 기반
#       (scheduler_heartbeat.py 참조). 과거 Dockerfile 에서 상속된
#       `curl localhost:8000/api/system/health` 형태이면 실패.
#   C4. /tmp/scheduler.heartbeat 파일이 scheduler 컨테이너 내부에
#       존재하고 mtime 이 SCHEDULER_HEARTBEAT_MAX_AGE_SEC (기본 120초)
#       이내 — scheduler 프로세스가 실제로 살아있음 증명.
#   C5. backend /api/system/health 엔드포인트가 HTTP 200 반환.
#
# 사용법:
#   bash scripts/post_deploy_smoke.sh
#
# 환경변수:
#   SCHEDULER_HEARTBEAT_MAX_AGE_SEC  heartbeat mtime 허용 최대 age (초, default 120)
#   BACKEND_PORT                     backend 포트 (default 8000)
#   SMOKE_SKIP_HEALTH_ENDPOINT       "true" 면 C5 생략 (네트워크 격리 환경용)
#
# Exit code:
#   0  모든 계약 통과
#   1  하나 이상의 계약 실패 — CD 가 중단되어야 함
# ══════════════════════════════════════════════════════════════════════
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

FAIL=0
pass() { echo -e "  ${GREEN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; FAIL=$((FAIL + 1)); }

HEARTBEAT_MAX_AGE="${SCHEDULER_HEARTBEAT_MAX_AGE_SEC:-120}"
BACKEND_PORT="${BACKEND_PORT:-8000}"

echo "════════════════════════════════════════════════════════════"
echo " AQTS post-deploy smoke test"
echo " $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo " heartbeat_max_age=${HEARTBEAT_MAX_AGE}s backend_port=${BACKEND_PORT}"
echo "════════════════════════════════════════════════════════════"

# ─────────────────────────────────────────────────────────────────
# C1. 컨테이너 존재 + Running
# ─────────────────────────────────────────────────────────────────
echo -e "\n${BLUE}[C1] Container running${NC}"
for cname in aqts-backend aqts-scheduler; do
    RUNNING=$(docker inspect --format='{{.State.Running}}' "$cname" 2>/dev/null || echo "not_found")
    if [[ "$RUNNING" == "true" ]]; then
        pass "$cname: Running=true"
    else
        fail "$cname: $RUNNING"
    fi
done

# ─────────────────────────────────────────────────────────────────
# C2a. backend ↔ scheduler digest 일치
# ─────────────────────────────────────────────────────────────────
echo -e "\n${BLUE}[C2a] Atomic digest (backend ↔ scheduler)${NC}"
BACKEND_IMG=$(docker inspect --format='{{.Image}}' aqts-backend 2>/dev/null || echo "")
SCHEDULER_IMG=$(docker inspect --format='{{.Image}}' aqts-scheduler 2>/dev/null || echo "")
if [[ -z "$BACKEND_IMG" || -z "$SCHEDULER_IMG" ]]; then
    fail "digest 조회 실패 (backend='$BACKEND_IMG', scheduler='$SCHEDULER_IMG')"
elif [[ "$BACKEND_IMG" == "$SCHEDULER_IMG" ]]; then
    pass "digest match: ${BACKEND_IMG:0:19}…"
else
    fail "digest DRIFT: backend=${BACKEND_IMG:0:19}… scheduler=${SCHEDULER_IMG:0:19}…"
fi

# ─────────────────────────────────────────────────────────────────
# C2b. 라벨 revision ↔ 서버 git HEAD 교차 일치
# ─────────────────────────────────────────────────────────────────
# 회귀 맥락: 2026-04-09 §4.7/§4.8 에서 CD 가 Step 5d/5e 를 실행하지 못했고
# backend/scheduler 는 둘 다 같은 구 digest(sha-70eee29) 로 고정되어 있었다.
# 그 상태에서도 C2a 의 digest 동일성은 참이므로(같은 구 digest 끼리 일치)
# 위양성으로 통과했고, 실제로 "새 이미지가 한 번도 기동된 적 없음" 이라는
# 사실이 post-deploy 단에서 드러나지 않았다. C4 heartbeat 신선도가 간접적으로
# 잡아냈지만, 그 신호는 "scheduler 코드 자체가 문제인가" 와 "컨테이너가
# 교체되지 않은 것인가" 를 구분하지 못했다.
#
# C2b 는 두 컨테이너의 org.opencontainers.image.revision 라벨(docker/metadata-action
# 이 CI 빌드 시점 git SHA 로 자동 주입)을 읽어서 서버가 이번 배포에서
# fetch+reset 한 git HEAD 와 비교한다. 서버 git HEAD 는 cd.yml Step 1 의
# `git reset --hard origin/main` 직후 확정되며, 그 commit 으로 CI 가 빌드한
# 이미지의 revision 라벨이 같은 값이어야 한다.
#
# label 부재/빈 값인 경우는 "CI 가 label 을 박지 않았다" 는 공급망 구성
# 문제이므로 실패로 처리한다 (정책: ci.yml 의 docker/metadata-action 이 항상
# 해당 라벨을 주입한다는 계약).
echo -e "\n${BLUE}[C2b] Image revision label ↔ server git HEAD${NC}"
BACKEND_REV=$(docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' aqts-backend 2>/dev/null || echo "")
SCHEDULER_REV=$(docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' aqts-scheduler 2>/dev/null || echo "")
SERVER_HEAD=$(git -C "${AQTS_REPO_DIR:-$HOME/aqts}" rev-parse HEAD 2>/dev/null || echo "")

if [[ -z "$SERVER_HEAD" ]]; then
    fail "서버 git HEAD 조회 실패 (AQTS_REPO_DIR='${AQTS_REPO_DIR:-$HOME/aqts}')"
elif [[ -z "$BACKEND_REV" || -z "$SCHEDULER_REV" ]]; then
    fail "revision 라벨 부재 — backend='$BACKEND_REV', scheduler='$SCHEDULER_REV' (ci.yml metadata-action 구성 확인)"
elif [[ "$BACKEND_REV" != "$SCHEDULER_REV" ]]; then
    fail "revision drift: backend=${BACKEND_REV:0:12}… scheduler=${SCHEDULER_REV:0:12}…"
elif [[ "$BACKEND_REV" != "$SERVER_HEAD" ]]; then
    fail "server drift: container revision=${BACKEND_REV:0:12}… server HEAD=${SERVER_HEAD:0:12}… (force-recreate 누락 가능성)"
else
    pass "revision label matches server HEAD: ${BACKEND_REV:0:12}…"
fi

# ─────────────────────────────────────────────────────────────────
# C3. scheduler healthcheck 구성 — heartbeat 기반이어야 함
# ─────────────────────────────────────────────────────────────────
echo -e "\n${BLUE}[C3] Scheduler healthcheck config${NC}"
HC_TEST=$(docker inspect --format='{{json .Config.Healthcheck.Test}}' aqts-scheduler 2>/dev/null || echo "null")
if [[ "$HC_TEST" == *"scheduler_heartbeat"* || "$HC_TEST" == *"scheduler.heartbeat"* ]]; then
    pass "healthcheck is heartbeat-based"
elif [[ "$HC_TEST" == *"curl"* && "$HC_TEST" == *"api/system/health"* ]]; then
    fail "healthcheck 이 Dockerfile 의 inherit 상태 (curl /api/system/health) — override 누락"
elif [[ "$HC_TEST" == "null" || -z "$HC_TEST" ]]; then
    fail "healthcheck 미설정 — docker-compose.yml 의 scheduler healthcheck 블록 확인 필요"
else
    fail "예상치 못한 healthcheck Test: $HC_TEST"
fi

# ─────────────────────────────────────────────────────────────────
# C4. heartbeat 파일 mtime 신선도
# ─────────────────────────────────────────────────────────────────
echo -e "\n${BLUE}[C4] Scheduler heartbeat liveness${NC}"
# docker exec 으로 컨테이너 내부 /tmp/scheduler.heartbeat 의 mtime 을 UTC epoch 로 읽는다.
# `stat -c %Y` 는 mtime (epoch seconds). 파일이 없으면 stat 가 non-zero 로 종료된다.
HEARTBEAT_MTIME=$(docker exec aqts-scheduler stat -c %Y /tmp/scheduler.heartbeat 2>/dev/null || echo "")
if [[ -z "$HEARTBEAT_MTIME" ]]; then
    fail "/tmp/scheduler.heartbeat 파일이 컨테이너에 없음 — scheduler 프로세스가 heartbeat 를 기록하지 않고 있음"
else
    NOW_EPOCH=$(docker exec aqts-scheduler date +%s 2>/dev/null || date -u +%s)
    AGE=$((NOW_EPOCH - HEARTBEAT_MTIME))
    if (( AGE < 0 )); then
        fail "heartbeat mtime 이 현재보다 미래 (age=${AGE}s) — 시계 불일치 의심"
    elif (( AGE <= HEARTBEAT_MAX_AGE )); then
        pass "heartbeat age=${AGE}s (≤ ${HEARTBEAT_MAX_AGE}s)"
    else
        fail "heartbeat stale: age=${AGE}s > ${HEARTBEAT_MAX_AGE}s"
    fi
fi

# ─────────────────────────────────────────────────────────────────
# C5. backend /api/system/health 200
# ─────────────────────────────────────────────────────────────────
echo -e "\n${BLUE}[C5] Backend /api/system/health${NC}"
if [[ "${SMOKE_SKIP_HEALTH_ENDPOINT:-false}" == "true" ]]; then
    echo "  (skipped via SMOKE_SKIP_HEALTH_ENDPOINT=true)"
else
    HEALTH_HTTP=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:${BACKEND_PORT}/api/system/health" 2>/dev/null || echo "000")
    if [[ "$HEALTH_HTTP" == "200" ]]; then
        pass "GET /api/system/health → 200"
    else
        fail "GET /api/system/health → $HEALTH_HTTP"
    fi
fi

# ─────────────────────────────────────────────────────────────────
# 결과
# ─────────────────────────────────────────────────────────────────
echo
echo "════════════════════════════════════════════════════════════"
if [[ $FAIL -eq 0 ]]; then
    echo -e " ${GREEN}✓ post-deploy smoke passed${NC}"
    echo "════════════════════════════════════════════════════════════"
    exit 0
else
    echo -e " ${RED}✗ post-deploy smoke FAILED (${FAIL} contract violations)${NC}"
    echo "════════════════════════════════════════════════════════════"
    exit 1
fi
