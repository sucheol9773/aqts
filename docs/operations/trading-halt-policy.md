# 매매 중단/재개 정책 (Trading Halt/Resume Policy)

**문서 번호**: OPS-001
**버전**: 1.1
**최종 수정**: 2026-04-15
**승인자**: 운영책임자

## 0. 개정 이력

| 버전 | 일자 | 주요 변경 |
|------|------|-----------|
| 1.0 | 2026-04-04 | 초안 작성 |
| 1.1 | 2026-04-15 | §2.2·§6 개정: `PipelineStateMachine.HALTED` 와 `TradingGuard.kill_switch_on` 을 명확히 분리. §3.5 에 실존 엔드포인트(`/api/system/kill-switch/status|deactivate`) 추가 반영. §4 재개 절차를 kill switch 해제 경로와 일반 재개 경로로 이원화. |

## 1. 목적

시스템 이상, 시장 급변, 리스크 한도 초과 시 자동/수동 매매 중단 및 재개 절차를 정의합니다.
본 문서는 두 계층의 중단 개념을 구분하여 다룬다.

| 계층 | 관리 객체 | 상태값 | 복구 방식 |
|------|-----------|--------|-----------|
| 파이프라인 상태 | `PipelineStateMachine` (`core/state_machine.py`) | `IDLE / COLLECTING / ... / HALTED / ERROR` | 조건 해소 시 자동 또는 운영자 트리거 |
| 전역 매매 차단 | `TradingGuard` (`core/trading_guard.py`) | `kill_switch_on: bool` (+ `reason`) | **감사 선행 수동 해제 전용** |

`kill_switch_on=True` 는 파이프라인 상태 기계와 독립적으로 **모든 실거래 주문**을 거부한다. 두 개념을 섞어 기록하지 말 것.

## 2. 중단 트리거

### 2.1 자동 중단 — PipelineStateMachine → HALTED

| 트리거 | 조건 | 중단 범위 | 복구 조건 |
|--------|------|----------|----------|
| 게이트 블록 | 리스크 게이트 BLOCK | 해당 파이프라인 사이클 | 다음 사이클 재시도 |
| 주문 연속 실패 | 3회 연속 실패 | 해당 종목 사이클 중단 | 원인 분석 후 수동 재시도 |
| API 연결 장애 | KIS API 응답 없음 30초 | 해당 사이클 중단 | API 정상 복구 확인 |

### 2.2 자동 차단 — TradingGuard.kill_switch_on

TradingGuard 는 파이프라인 사이클과 무관하게 **실거래 주문 전체**를 거부하는 전역 차단이다.

| 트리거 | 조건 | 복구 경로 |
|--------|------|-----------|
| 일일 손실 한도 | 포트폴리오 -3% 이상 (`risk.daily_loss_limit_krw`) | 익일 장 개시 시 자동 리셋 (state 초기화) |
| 주간 손실 한도 | 포트폴리오 -5% 이상 | **§3.5 수동 해제 절차 필수** |
| MIDDAY_CHECK mismatch | HTS vs Ledger 재계산 불일치 CRITICAL | **§3.5 수동 해제 절차 필수** |
| 환율 급변 | USD/KRW ±2% 이내 5분 | 환율 안정화 확인 후 **§3.5 수동 해제** |
| 감사 장애 | `AuditLogger.log_strict()` 실패 | fail-closed: 감사 복구 + **§3.5 수동 해제** |

### 2.3 수동 중단

- **운영자 긴급 중단**: Telegram `/halt` 명령(후속 구현 예정)
- **정기 점검**: 스케줄러에 의한 사전 예약 중단 (파이프라인 suspend)
- **외부 요인**: 거래소 서킷브레이커, 시장 전체 중단 시

> **주의**: `POST /api/system/halt`, `POST /api/system/resume` 는 현재 구현되어 있지 않다. 파이프라인 일시 정지는 스케줄러 cron 제어 또는 Redis flag 로 운용한다. kill switch 해제는 §3.5 의 두 엔드포인트로 일원화된다.

## 3. 중단 절차

```
1. 트리거 감지
   ↓
2. TradingGuard.activate_kill_switch(reason) 또는 PipelineStateMachine → HALTED
   ↓
3. 미체결 주문 일괄 취소 (KIS API cancel_order)
   ↓
4. 감사 로그 기록 (AuditLogger: TRADING_HALTED / KILL_SWITCH_ACTIVATED)
   ↓
5. 알림 발송 (Telegram + 이메일)
   ↓
6. Prometheus gauge `aqts_trading_guard_kill_switch_active` → 1
   ↓
7. 운영 대시보드 상태 갱신
```

### 3.5 TradingGuard 수동 해제 경로 (필수 엔드포인트)

kill switch 는 감사 선행 조건 하에서만 수동으로 해제한다. 자동 해제 경로는 존재하지 않는다 (일일 손실 한도만 익일 state 리셋으로 자연 해제된다).

| 단계 | 엔드포인트 / 명령 | 권한 | 설명 |
|------|-------------------|------|------|
| 1 | `GET /api/system/kill-switch/status` | `require_viewer` | 현재 상태 snapshot (`kill_switch_on`, `kill_switch_reason`) 조회 |
| 2 | `POST /api/system/kill-switch/deactivate` | `require_admin` | body: `{"reason": "<10자 이상 사유>", "confirm": true}` — 감사 선행 후 해제 + ledger 재hydrate |

`/kill-switch/deactivate` 의 동작 사양:

1. `confirm=false` → 400 `CONFIRM_REQUIRED` (해제되지 않음).
2. `AuditLogger.log_strict(action_type="KILL_SWITCH_DEACTIVATE", before_state, after_state, metadata={"username": ...})` 선행 호출. 실패 시 503 `AUDIT_UNAVAILABLE` 반환 + **kill switch 유지**(fail-closed).
3. 감사 성공 후 `TradingGuard.deactivate_kill_switch()` 호출 → 상태 전이 + Prometheus gauge → 0.
4. `PortfolioLedger.hydrate()` 호출로 DB → 인메모리 캐시 재동기화. 실패는 warning 로그만 남기고 해제 자체는 유효로 간주.
5. 응답: `was_on`, `previous_reason`, `deactivated_at`, `ledger_rehydrated`, `ledger_positions_count`, `operator`.

호출 예:

```bash
# 1) 현재 상태 확인
curl -sS -H "Authorization: Bearer $ADMIN_TOKEN" \
  http://<backend>/api/system/kill-switch/status | jq .

# 2) 수동 해제
curl -sS -X POST \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"reason":"DEMO 환경 MIDDAY_CHECK mismatch 원인 확인 후 해제","confirm":true}' \
  http://<backend>/api/system/kill-switch/deactivate | jq .
```

## 4. 재개 절차

kill switch 해제(§3.5)와 파이프라인 재개는 별개의 작업이다. kill switch 가 off 여도 파이프라인은 여전히 HALTED 상태일 수 있다.

```
1. 중단 사유 해소 확인 (손실 한도, 환율, 감사 장애, mismatch 원인 등)
   ↓
2. 시스템 상태 점검 (DB, Redis, KIS API 연결 확인)
   ↓
3. 미체결 주문 상태 동기화 (reconcile 절차)
   ↓
4. 운영책임자 승인 (주간 손실 한도 / mismatch CRITICAL 시 필수)
   ↓
5. TradingGuard kill switch 해제 (§3.5 — 감사 선행)
   ↓
6. PipelineStateMachine → IDLE (필요 시)
   ↓
7. 감사 로그는 §3.5 의 KILL_SWITCH_DEACTIVATE 가 자동 기록. 추가 TRADING_RESUMED 는 파이프라인 재개 시 별도 기록.
   ↓
8. 알림 발송 (매매 재개 통지)
```

## 5. 에스컬레이션

| 단계 | 시간 | 대상 | 액션 |
|------|------|------|------|
| L1 | 즉시 | 시스템 (자동) | 매매 중단 + 알림 |
| L2 | 5분 | 운영 담당자 | 원인 분석 + 판단 |
| L3 | 30분 | 운영책임자 | 재개/연장 결정 |
| L4 | 2시간 | 경영진 | 장기 중단 판단 |

## 6. 코드 연동 포인트

- `core/state_machine.py`: `PipelineState.HALTED` 전이 로직 (파이프라인 사이클)
- `core/trading_guard.py`:
  - `TradingGuard._activate_kill_switch(reason)` / `deactivate_kill_switch()` — 전역 차단 전이
  - `TRADING_GUARD_KILL_SWITCH_ACTIVE` Prometheus gauge 와이어링
- `core/pipeline.py`: 게이트 BLOCK 시 HALTED 전이
- `core/order_executor/executor.py`: 미체결 주문 취소 메서드
- `api/routes/system.py`:
  - `GET /kill-switch/status` / `POST /kill-switch/deactivate` — §3.5 구현체
  - 두 엔드포인트는 RBAC 정적 검사기 (`scripts/check_rbac_coverage.py`) 에 의해 가드 누락이 자동 차단된다
- `db/repositories/audit_log.py`: `AuditLogger.log_strict()` — fail-closed 감사 선행
- `core/portfolio_ledger.py`: `PortfolioLedger.hydrate()` — kill switch 해제 직후 DB 재동기화
- `config/settings.py`: 손실 한도 임계값 설정
- `config/operational_thresholds.yaml`: 중단 임계값 운영 설정 (환율 변동폭, 손실 한도 등)
- `backend/tests/test_kill_switch_routes.py`: 해제 경로 전 사이클 회귀 테스트 9건

## 7. 관련 문서

- `docs/security/rbac-policy.md` — `/kill-switch/*` 권한 매트릭스
- `docs/operations/phase1-demo-verification-2026-04-11.md` §10.17 — 엔드포인트 도입 경위
- `docs/security/security-integrity-roadmap.md` §9 — P0-5 후속 항목
