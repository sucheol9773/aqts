# MIDDAY_CHECK Path A 운영자 런북

**문서 번호**: OPS-006
**버전**: 1.0.2 (SUSPENDED)
**최종 수정**: 2026-04-15
**승인자**: 운영책임자
**적용 대상**: 2026-04-16 11:30 KST Path A 집행은 **취소되었다**. 재개 조건은 §0.1 에 명시.

> **⛔ 집행 중단 — 본 런북의 §4.1 이후 절차는 현재 환경에서 수행 금지**
>
> 2026-04-15 DEMO dry-run 에서 P0 결함 확인:
> `TradingGuard` 가 **프로세스 전역 in-memory 싱글톤**(`backend/core/trading_guard.py:407`)이고
> 현재 배포는 backend / scheduler 가 **별도 컨테이너**(`docker-compose.yml:126`, `SCHEDULER_ENABLED=false/true` 분기)이다.
> scheduler 프로세스에서 `ReconciliationRunner` 가 활성화한 kill switch 는 backend 프로세스의 `/api/system/kill-switch/*` 엔드포인트에서 **관측·해제 불가능**하다 (서로 다른 싱글톤).
>
> 본 런북이 가정하는 단일 진실원천(single source of truth) 전제가 성립하지 않으므로 **집행 시 kill switch 상태가 분열된 상태로 운영될 위험**이 있다. Redis-backed state + pub/sub sync 로의 이행 완료 및 재검증 전까지 본 런북의 §4.1 이후 절차는 수행하지 않는다.
>
> 상세 근거 및 결정: `docs/operations/phase1-demo-verification-2026-04-11.md` §10.18.

## 0. 개정 이력

| 버전 | 일자 | 주요 변경 |
|------|------|-----------|
| 1.0 | 2026-04-15 | 초안 작성. P0-5 후속 `/api/system/kill-switch/*` 엔드포인트 도입(§10.17) 직후, 내일 11:30 KST Path A 집행을 대비한 운영자 체크리스트 분리 본문화. |
| 1.0.1 | 2026-04-15 | **SUSPENDED**. DEMO dry-run 에서 backend/scheduler 교차프로세스 싱글톤 분열 P0 확인(§10.18). 재개 조건은 §0.1. 본문 절차 본문 로직 변경 없음(상태 게이트만 추가). |
| 1.0.2 | 2026-04-15 | §0.1 재개 조건을 4건 → 5건으로 확장. 시간 데드라인 제거 — 2026-04-16 11:30 KST Path A 는 **무조건 취소**, 재개는 Commit D 통합 테스트 PASS + 1 거래일 안정화 관측 + 운영책임자 별건 결정 후에만. 상세 근거: `docs/security/trading-guard-redis-migration.md` v0.2 §10. |

## 0.1 재개 조건 (Resumption Gate)

다음 **다섯** 조건이 모두 충족되기 전까지 본 런북의 §4.1 이후 절차는 집행하지 않는다. 시간 데드라인은 **없다** — 품질을 시간에 맞추지 않는다.

1. `TradingGuard` 상태(`TradingGuardState` 10개 필드 전체 + 해제 메타데이터) 가 **Redis 단일 저장소**로 이전 완료 (`aqts:trading_guard:state` hash + `aqts:trading_guard:seq` counter + Lua script 원자적 업데이트). 프로세스 내 cache 는 pub/sub 업데이트·주기 reconcile 로만 갱신.
2. backend ↔ scheduler 양방향 cross-process 통합 테스트 PASS — scheduler 에서 activate 한 kill switch 가 backend `/api/system/kill-switch/status` 에 즉시 관측되고, backend `/api/system/kill-switch/deactivate` 가 scheduler 프로세스 cache 에도 반영되며, monotonic seq gap 주입 시 즉시 reconcile 이 호출되는 시나리오 포함.
3. Redis 장애 시 fail-closed 동작 검증 — `activate` 는 로컬 차단 + 경보 + 재시도 큐 적재, `deactivate` 는 503 `REDIS_UNAVAILABLE` 반환(감사는 기록되지만 state 는 미변경), 기동 시 Redis 불가 시 `kill_switch_on=true, reason="redis_unavailable_at_startup"` 로 기동.
4. cosign 서명 + grype/pip-audit 통과 이미지로 배포 완료 및 DEMO 환경에서 §4 전 절차 재예행 성공.
5. **최소 1 거래일 이상 양 프로세스 gauge 일치 관측** — backend/scheduler 두 `/metrics` 엔드포인트의 `aqts_trading_guard_kill_switch_active` 가 불일치 0건이고, `reconcile_from_redis` 호출 카운터 / pub-sub latency 히스토그램이 운영 대시보드에서 확인됨.

다섯 조건 충족 + 운영책임자 별건 결정 후 본 문서 버전을 1.1 로 올리고 SUSPENDED 배너를 제거한다. 2026-04-16 11:30 KST Path A 집행은 이미 **무조건 취소**되었으므로, 재개는 그 이후의 임의 MIDDAY_CHECK 일정에 대해 별도 공지한다.

## 1. 목적

`docs/operations/phase1-demo-verification-2026-04-11.md` §10.17 에서 결정된 **Path A 집행 전 과정을 운영자 체크리스트로 분리**한다. 관련 배경·설계 근거는 §10.17 에서 유지되며, 본 문서는 현장에서 즉시 따라 읽는 행동 지침이다.

두 계층의 개념을 본 런북 전체에 걸쳐 혼동 없이 사용한다. 단일 진실원천은 `docs/operations/trading-halt-policy.md` v1.1 §1.

| 계층 | 관리 객체 | 상태값 | 해제 경로 |
|------|-----------|--------|-----------|
| 파이프라인 상태 | `PipelineStateMachine` | `IDLE / COLLECTING / ... / HALTED / ERROR` | 조건 해소 시 자동 또는 수동 |
| 전역 매매 차단 | `TradingGuard` | `kill_switch_on: bool` | **§4 감사 선행 수동 해제 전용** |

## 2. 전제 조건 (집행 전 체크)

| # | 전제 | 확인 방법 |
|---|------|-----------|
| T1 | backend/scheduler 이미지 digest 가 P0-5 머지(`8ac864d`) 이후 | `docker compose ps --format '{{.Image}}'` 에 현재 tag 확인 |
| T2 | `/api/system/kill-switch/*` 라우트가 배포됨 | `curl -sS -I http://<backend>/api/system/kill-switch/status` 가 401 반환(토큰 없음, 인증 요구) — 404 이면 배포 누락 |
| T3 | admin 권한 사용자와 운영자 token 발급 경로 확보 | §3 에 따라 `POST /api/auth/login` 이 200 반환 |
| T4 | `docs/operations/trading-halt-policy.md` v1.1 과 본 런북 내용이 일치 | §3.5 `kill-switch/status|deactivate` 엔드포인트와 본 §4 절차 일치 |
| T5 | Telegram/이메일 알림 채널 활성 | 이전 배포 알림이 실제 수신됨 |
| T6 | `aqts_trading_guard_kill_switch_active` gauge 가 `/metrics` 에 노출 | `curl -sS http://<backend>/metrics \| grep aqts_trading_guard_kill_switch_active` |

T1~T6 중 하나라도 미충족이면 Path A 집행 직전에 반드시 해소하고 본 런북에 해소 사실을 체크한다.

## 3. 운영자 token 발급

Path A 집행 전 반드시 admin 권한 token 을 발급해 환경 변수로 고정한다. Token 은 heredoc 스크립트로 분기하기 쉽도록 단일 변수로 유지한다.

```bash
# 비밀번호는 터미널 입력으로만 받는다. history 에 평문 노출 금지.
read -s -p "ADMIN_PASSWORD: " ADMIN_PASSWORD
echo

ADMIN_TOKEN=$(curl -sS -X POST \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"admin\",\"password\":\"${ADMIN_PASSWORD}\"}" \
  http://<backend>/api/auth/login | jq -r .data.access_token)

# 발급 확인
test -n "${ADMIN_TOKEN}" && echo "ADMIN_TOKEN length=${#ADMIN_TOKEN}"

unset ADMIN_PASSWORD
```

`ADMIN_TOKEN` 길이가 350 이상이면 발급 성공. `null` 또는 공백이면 `/api/auth/login` 응답을 직접 확인한다. 추측 금지.

## 4. 집행 타임라인 (2026-04-16 기준)

### 4.1 09:00 KST — 장 개장 직후 준비

| # | 확인 항목 | 기대 관측 |
|---|-----------|-----------|
| C1 | pipeline cycle 이 `hydrate()` 완료 상태 | `docker compose logs backend --tail=200 \| grep "PortfolioLedger.hydrate"` 에서 `positions=0` (또는 현재 포지션 수) 로그 1건 이상 |
| C2 | kill switch 상태 off | `curl -sS -H "Authorization: Bearer ${ADMIN_TOKEN}" http://<backend>/api/system/kill-switch/status \| jq .` → `kill_switch_on=false` |
| C3 | gauge 0 | `curl -sS http://<backend>/metrics \| grep 'aqts_trading_guard_kill_switch_active 0'` 가 1줄 |

C1~C3 이 모두 충족된 상태로 11:30 를 맞이한다.

### 4.2 11:30 KST — MIDDAY_CHECK 자동 집행 (관찰만)

운영자는 **개입하지 않는다**. 스케줄러가 자동으로 아래 시퀀스를 집행한다.

```
midday_mismatch_check 스케줄러 트리거
  ↓
HTS 13종목 vs ledger 0종목 비교
  ↓
Reconciliation mismatch detected: count=13 diff_abs=... mismatches=[...] (CRITICAL)
  ↓
TradingGuard.activate_kill_switch("MIDDAY_CHECK mismatch — count=13")
  ↓
Prometheus gauge aqts_trading_guard_kill_switch_active → 1
  ↓
Telegram CRITICAL 알림 발송
  ↓
이후 모든 주문 요청 거부 (TradingGuardBlocked)
```

운영자는 다음 세 관측을 **시간 순서대로** 확보해 기록한다. 순서가 뒤집히면 관측 레이어 silent miss 의심.

| 순서 | 관측 포인트 | 명령 |
|------|-------------|------|
| O1 | mismatch CRITICAL 로그 | `docker compose logs backend --tail=500 \| grep "Reconciliation mismatch detected" \| tail -1` |
| O2 | kill switch 활성 | `curl -sS -H "Authorization: Bearer ${ADMIN_TOKEN}" http://<backend>/api/system/kill-switch/status \| jq .` → `kill_switch_on=true`, `kill_switch_reason` 에 "MIDDAY_CHECK mismatch" 포함 |
| O3 | gauge 1 | `curl -sS http://<backend>/metrics \| grep 'aqts_trading_guard_kill_switch_active 1'` 가 1줄 |

O1 메시지에 literal `%d`/`%s` 가 포함되면 §10.15·§10.16 loguru 포맷 회귀다 — 즉시 별도 장애로 분류하고 Path A 는 중단한다.

### 4.3 11:31 ~ 운영자 분석 단계 (mismatch 원인 추적)

**절대 규칙**: 원인 분석 전에 해제하지 않는다. 해제가 감사에 남더라도 "왜 mismatch 가 발생했는가" 를 설명하지 못하면 감사 품질은 0 이다.

분석 포인트 (예시 — 실제 내용은 O1 로그의 `mismatches=[...]` 값으로 결정):

1. HTS 에는 있지만 ledger 에 없는 종목군의 최근 거래일·거래 경로 추적 (DB `trades` 테이블)
2. 해당 종목이 `PortfolioLedger.hydrate()` 에서 누락되는 원인 — repository `list_all_positions()` 쿼리 조건 검증
3. stale cache 가능성 — 마지막 hydrate 타임스탬프 (`SELECT ... FROM portfolio_ledger_cache ORDER BY updated_at DESC LIMIT 5;` 또는 로그)
4. 외부 주문 이벤트(KIS push) 수신 실패 여부

원인이 특정되면 **수정 범위가 해제 후 다음 cycle 에서 즉시 필요한지** 판단한다. 필요하면 fix 머지·재배포 후 해제. 당일 수정이 필요 없다면 바로 §4.4 로 진행.

### 4.4 해제 집행

사전 체크:

- [ ] O1/O2/O3 세 관측이 기록되어 있다
- [ ] mismatch 원인이 특정됐거나, 당일 수정 불필요 판단이 명문으로 기록되어 있다
- [ ] 운영책임자 승인 (mismatch CRITICAL 해제는 `trading-halt-policy.md` §4 에 따라 필수 승인)

해제 실행:

```bash
curl -sS -X POST \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"reason":"MIDDAY_CHECK mismatch 원인 특정 완료 (상세: <티켓번호>), 해제 집행","confirm":true}' \
  http://<backend>/api/system/kill-switch/deactivate | jq .
```

`reason` 은 **10자 이상** (Pydantic `min_length=10`). 10자 미만이면 Pydantic `ValidationError`. 원인 티켓/인시던트 번호 포함 권장.

응답 필드 전수 검증:

| 필드 | 기대 | 실패 시 |
|------|------|---------|
| HTTP status | 200 | 400 `CONFIRM_REQUIRED` → `confirm` 누락 확인. 503 `AUDIT_UNAVAILABLE` → DB 장애, kill switch **유지됨**. 403 → 토큰 권한 재확인. 500 → 예외 추적 필요 |
| `was_on` | `true` | `false` 면 이미 해제된 상태(운영자 중복 호출) |
| `previous_reason` | `"MIDDAY_CHECK mismatch"` 포함 | 다른 값이면 기존 reason 과 불일치 — 감사 이력 재확인 |
| `deactivated_at` | ISO-8601 KST timestamp | 파싱 불가 시 코드 회귀 |
| `ledger_rehydrated` | `true` | `false` 면 §4.6 "ledger 재hydrate 실패" 분기 |
| `ledger_positions_count` | `>= 0` | 음수/문자열 이상치 시 회귀 |
| `operator` | admin user id (현재 발급자) | 다른 값이면 토큰 혼용 |

### 4.5 해제 후 즉시 검증 (3분 이내)

| # | 검증 | 명령 | 기대 |
|---|------|------|------|
| V1 | gauge 0 전이 | `curl -sS http://<backend>/metrics \| grep 'aqts_trading_guard_kill_switch_active 0'` | 1줄 |
| V2 | status 재확인 | `curl -sS -H "Authorization: Bearer ${ADMIN_TOKEN}" http://<backend>/api/system/kill-switch/status \| jq .` | `kill_switch_on=false` |
| V3 | 감사 선행 기록 존재 | `docker exec aqts-postgres psql -U aqts_user -d aqts -c "SELECT time, action_type, metadata->>'release_reason', metadata->>'username' FROM audit_logs WHERE action_type='KILL_SWITCH_DEACTIVATE' ORDER BY time DESC LIMIT 3;"` | 최신 행의 `time` 이 §4.4 API 응답의 `deactivated_at` 보다 **앞서야 함** (감사 선행 invariant) |
| V4 | 다음 pipeline cycle 정상 진행 | `docker compose logs backend --since=2m \| grep -E "pipeline|hydrate"` | cycle 실행 로그 존재, `TradingGuardBlocked` 재발 없음 |

V3 이 뒤집히면 (API 응답이 audit 보다 이른 시각) **감사 선행 설계가 무너진 것** — 즉시 별도 인시던트로 분류한다. 이는 §10.17 의 핵심 invariant 이다.

### 4.6 실패 분기

| 분기 | 관측 | 조치 |
|------|------|------|
| 503 `AUDIT_UNAVAILABLE` | 응답 body `error_code=AUDIT_UNAVAILABLE` | DB/감사 층 장애. kill switch 는 **유지**됨을 V2 로 재확인. DB 복구 후 §4.4 재시도 |
| 400 `CONFIRM_REQUIRED` | `confirm=false` 또는 body 누락 | 명령 재작성. 절대 강제 해제하지 않음 |
| `ledger_rehydrated=false` | 응답 body 에서 관측 | 해제 자체는 유효. 다음 cycle 에서 `hydrate()` 가 재시도되는지 V4 로 확인. 실패 반복 시 repository/DB 점검 후 수동 재hydrate (별도 내부 경로는 현재 없음 — 재시작이 최종 수단) |
| 403 Forbidden | admin 토큰 만료/권한 부족 | §3 에 따라 token 재발급. viewer 토큰 혼용 여부 확인 |
| 500 Internal Server Error | 기타 예외 | `docker compose logs backend --tail=200` 로 스택트레이스 확보 후 별도 인시던트 분류. kill switch 상태는 V2 로 별도 재확인 |

## 5. 사후 기록

집행 완료 후 `docs/operations/phase1-demo-verification-2026-04-11.md` 에 §10.18 (또는 다음 번호) 로 다음 구조로 결과 추가:

1. 집행 타임라인 (O1/O2/O3 → §4.4 → V1/V2/V3/V4 각 관측 시각)
2. mismatch 상세 (`count=`, `mismatches=[...]` 전문)
3. 원인 분석 결론
4. 해제 응답 body 전문 (jq 출력)
5. 감사 행 (V3 결과)
6. silent miss 재발 여부 (literal `%`, ledger rehydrate 실패, stale cache 등)
7. 회귀 발생 시 후속 P0 등록 계획

이 기록이 누락되면 §10.17 의 "운영 절차 silent miss 해소" 가 실제로 닫힌 것이 아니다.

## 6. 관련 문서

- `docs/operations/trading-halt-policy.md` v1.1 — 단일 진실원천 (중단/재개 정책, §3.5 엔드포인트 사양)
- `docs/operations/phase1-demo-verification-2026-04-11.md` §10.17 — 배경·설계 근거
- `docs/security/rbac-policy.md` — `/kill-switch/*` 권한 매트릭스
- `docs/security/security-integrity-roadmap.md` §9 — P0-5 후속
- `backend/api/routes/system.py` — 엔드포인트 구현
- `backend/tests/test_kill_switch_routes.py` — 9건 회귀 테스트
