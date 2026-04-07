# AQTS 보안·정합성 통합 로드맵

Last updated: 2026-04-08
Status: Active (P0 트랙 진행 중)

## 0. 문서의 목적

본 문서는 AQTS 의 보안·정합성·관측성 공백을 단일 진실원천(SSOT)으로 정리한
실행 로드맵이다. 직접 코드 관찰, 외부 전문가 두 차례의 교차 검증, CLAUDE.md
원칙(추론 ≠ 확정, 정의 ≠ 적용, fail-closed 우선)을 종합한 결과이며, 우선순위는
"의사결정 피로 최소화 + 실행력 최대화" 를 기준으로 P0 5개로 잠겨 있다.

## 1. 기본 원칙

- **추론 ≠ 확정**: 작업 착수 전 해당 파일을 직접 관찰하여 공백이 실제로 존재하는지
  확인한다. 추론만으로 코드를 작성하지 않는다.
- **정의 ≠ 적용**: 모듈이 정의되어 있다는 사실과 운영 경로에서 호출된다는 사실은
  다르다. wiring 은 통합 테스트 또는 로그로 검증한다.
- **한 커밋 한 원인**: bug fix 커밋에 무관한 "이왕 고치는 김에" 식 변경을
  끼워넣지 않는다.
- **fail-closed 우선**: 핵심 정합성·감사 검증이 실패하면 비즈니스 동작을 차단한다.
  특히 주문·권한변경·출금성 행위는 fail-open 을 허용하지 않는다.
- **CLAUDE.md 검증 절차 필수**: 매 커밋 전 `ruff` / `black --check` / `pytest`,
  관련 .md 업데이트, `gen_status.py --update`, doc-sync 0 errors + 0 warnings.

## 2. 위협 모델 요약

금융 자동매매 시스템 + 최고 수준 공격자(APT/내부자/공급망 침해) 가정. 일반적인
CIA(기밀성·무결성·가용성) 를 넘어 **Correctness + Determinism + Auditability** 까지
통제 대상에 포함한다. 보안 침해와 정합성 사고는 동일 층위의 손실로 취급하며,
다음 6축이 모두 동등한 우선순위로 관리된다.

1. 레이턴시 예산 (signal → order → broker_ack → fill)
2. 가격 정합성 (주문가 vs 직전호가/현주가/체결가 괴리)
3. 시간 정합성 (서버/DB/브로커 타임스탬프 일관성, NTP 드리프트)
4. 계좌/포지션 정합성 (주문 이벤트와 잔고·포지션 원장 일치)
5. 로그/감사 정합성 (누락·순서역전·중복·변조 불가)
6. 재현 가능성 (사후에 특정 주문의 원인·입력·모델·시점·권한 100% 재구성)

## 3. P0 — 즉시 처리 (5개 작업, 5개 커밋)

전문가 축약안과 직접 관찰 결과의 교집합. 이 5개가 모두 머지되고 CI/CD 녹색이
확인될 때까지 다른 항목은 P0 로 추가하지 않는다.

### P0-1. Refresh 토큰 type 강제 검증

| 항목 | 내용 |
| --- | --- |
| 위치 | `backend/api/routes/auth.py`, `backend/services/auth_service.py` 또는 동등 |
| 작업 | refresh 핸들러에서 `payload["type"] == "refresh"` 검증, 위반 시 401 |
| 단위 테스트 | access token 으로 refresh 시도 → 401, 정상 refresh token → 200 |
| 통합 테스트 | 실제 라우트 호출 경로로 type mismatch 401 검증 |
| 관측성 | `aqts_token_refresh_from_access_total` counter 추가 (즉시 alert) |
| 효과 | 탈취 access token 의 세션 장기화 경로 차단 |
| 비용 | 매우 낮음 (코드 수 줄 + 테스트 + 카운터) |

근거:
- 외부 전문가 1차 답변 §B "Refresh 엔드포인트가 토큰 타입 검증을 안 함"
- 외부 전문가 2차 답변 신뢰도 평가에서 "타당" 으로 확정

### P0-2. Token revocation + Rate limiter Redis 전환

| 항목 | 내용 |
| --- | --- |
| 위치 | `TokenRevocationStore`, `backend/api/middleware/rate_limiter.py` |
| 작업 | 인메모리 → Redis TTL 영속 저장소. Rate limit 키를 IP 단일 → 계정/디바이스 복합 키 |
| 단위 테스트 | Redis mock 으로 set/exists/expire 동작 검증 |
| 통합 테스트 | 백엔드 재시작 후 revoked token 수용률 0%, 멀티 인스턴스 키 공유 검증 |
| 관측성 | `aqts_revoked_token_acceptance_total`, `aqts_rate_limit_block_total` |
| 효과 | 재시작·스케일아웃 무력화 차단, brute-force/credential stuffing 방어선 복구 |
| 비용 | 중 (Redis 키 설계 + 마이그레이션 + 멀티 인스턴스 테스트) |

근거:
- 전문가 1차 §A, §D, 2차 신뢰도 평가에서 모두 "타당"
- `core/scheduler_idempotency.py` 에 이미 Redis 패턴이 잡혀 있어 동일 패턴으로 적용 가능

### P0-3. 주문 경로 idempotency key 도입

| 항목 | 내용 |
| --- | --- |
| 위치 | `backend/api/routes/orders.py`, `backend/api/schemas/orders.py`, `backend/core/order_executor/executor.py` |
| 작업 | `Idempotency-Key` 헤더 또는 `OrderCreateRequest.idempotency_key` 도입. DB 유니크 제약 + Redis 중복 차단(짧은 TTL window). 동일 키 재요청 시 첫 결과 그대로 반환 |
| 단위 테스트 | 키 생성·검증·만료 |
| 통합 테스트 | 동일 키 재요청 → DB 단일 row, KIS 호출 1회, 응답 동일 |
| 관측성 | `aqts_order_idempotency_hit_total`, `aqts_order_duplicate_blocked_total` |
| 효과 | 네트워크 재시도·클라이언트 retry·스케줄러 재기동 시 중복 체결 차단 |
| 비용 | 중상 (스키마 + DB 마이그레이션 + 라우트 + executor + 테스트) |

근거:
- 직접 관찰: `OrderCreateRequest` 에 idempotency 필드 부재, executor 에 중복 차단 없음
- 전문가 2차 신뢰도 평가에서 "타당" 으로 확정
- `scheduler_idempotency.py` 는 "이벤트 타입 + 거래일" 단위라 주문 단위와 무관

### P0-4. 핵심 감사로그 fail-closed

| 항목 | 내용 |
| --- | --- |
| 위치 | `backend/api/routes/orders.py`, `backend/api/routes/users.py`, 권한 변경 라우트 |
| 작업 | `audit.log()` 실패 시 트랜잭션 rollback + 비즈니스 응답 실패. try/except 안으로 이동, 실패 카운터 증가, 즉시 alert |
| 단위 테스트 | audit DB 강제 실패 주입 시 핸들러가 예외/실패 반환 |
| 통합 테스트 | audit DB 장애 시 주문 미체결 + 5xx 응답 + counter 증가 |
| 관측성 | `aqts_audit_write_failures_total` (즉시 alert, 임계 0) |
| 효과 | "체결은 있었는데 증거가 없는" 규제·포렌식 공백 제거 |
| 비용 | 중 (라우트 수정 + 트랜잭션 경계 정비 + 통합 테스트) |

근거:
- 직접 관찰: `orders.py` 의 `audit.log()` 가 try/except 바깥에서 호출됨
- 전문가 1차 §G, 2차 신뢰도 평가에서 모두 "타당"

### P0-5. TradingGuard → OrderExecutor wiring

| 항목 | 내용 |
| --- | --- |
| 위치 | `backend/core/order_executor/executor.py`, `backend/core/trading_guard.py` |
| 작업 | `execute_order` 진입부에서 `TradingGuard.check_pre_order(...)` 호출. 거부 시 reject + 감사 로그 + counter. kill switch 활성 시 모든 신규 주문 차단 |
| 단위 테스트 | guard reject 시나리오, kill switch on/off, 일일 손실 한도 초과 |
| 통합 테스트 | executor → guard → reject 의 실제 호출 경로 검증 (유닛만으로는 wiring 검증 불가) |
| 관측성 | `aqts_trading_guard_reject_total{reason}`, `aqts_kill_switch_active` gauge |
| 효과 | 정의되어 있으나 적용되지 않은 가드(kill switch·일일 손실·드로다운·주문별 사전 검증)가 운영 경로에서 활성화 |
| 비용 | 중 (wiring + 통합 테스트 + reject 응답 정의) |

근거:
- 직접 관찰: `trading_guard.py` 345 lines 에 docstring §6 "주문별 사전 검증" 명시,
  그러나 `executor.py` 에서 `TradingGuard|trading_guard` grep 결과 0건
- 9위 RBAC 회고와 동일한 "정의 ≠ 적용" 패턴의 재발

## 4. 실행 절차 (P0 각 항목 공통)

각 P0 항목은 다음 사이클로 닫는다. 한 사이클이 완료되어 CI/CD 녹색이 확인된
뒤에야 다음 P0 항목에 착수한다.

1. **관찰**: 해당 파일·호출부를 직접 읽어 공백을 확정. 공백이 다르면 작업 재검토.
2. **수정**: 한 커밋 한 원인. 무관한 개선 금지.
3. **테스트**: 단위 + 통합. 통합 테스트는 wiring 을 실제 호출 경로로 검증한다.
4. **검증**: `cd backend && python -m ruff check . --config pyproject.toml`,
   `python -m black --check . --config pyproject.toml`,
   `python -m pytest tests/ -q --tb=short`. 모두 0 errors + 0 warnings.
5. **문서**: 본 로드맵의 해당 P0 절에 결과(완료 일자, 커밋 해시, 평가 지표 측정값)
   를 추가. 관련 운영 문서(`docs/security/`, `docs/operations/`) 업데이트.
   `python scripts/gen_status.py --update`.
6. **커밋**: HEREDOC commit message + Co-Authored-By.
7. **CI/CD 녹색 확인**: 다음 P0 착수.

## 5. 평가 지표

각 P0 머지 후 다음 지표를 측정해 본 문서에 기록한다.

- **P0-1**: refresh API 에 access token 주입 시 401 비율 100%.
  `aqts_token_refresh_from_access_total` 0 유지(정상 운영).
- **P0-2**: 백엔드 재시작 후 revoked token 수용률 0%. Rate limit 우회 시도 차단률.
- **P0-3**: 동일 idempotency key 재요청 시 KIS API 호출 1회. DB 단일 row.
- **P0-4**: audit DB 장애 주입 시 주문 체결률 0%. `aqts_audit_write_failures_total`.
- **P0-5**: kill switch on 상태에서 신규 주문 차단률 100%.
  `aqts_trading_guard_reject_total` 가시화.

## 6. 잠금 규칙

P0 5개가 모두 머지되기 전에는:

- 새 항목을 P0 로 추가하지 않는다.
- "이왕 고치는 김에" 식 변경을 P0 커밋에 끼워넣지 않는다.
- P1/P2 항목을 발견하더라도 본 문서 §7/§8 에 적어두고 P0 완료 후 재정렬한다.

P0 5개 머지 후, 본 로드맵을 갱신하여 새 P0 5개를 선정한다.

## 7. P1 — P0 머지 후 재검토 (현재 잠금)

지금은 어느 항목도 P0 로 승격하지 않는다. 발견되는 신규 항목은 본 절에
누적해두고 P0 완료 후 재정렬한다.

### 7.1 보안 축

- MFA 시크릿 평문 저장 → KMS/envelope encryption (코드 관찰로 확정 필요)
- `get_current_user` JWT claim 만 신뢰 → DB 재검증(`is_active`/`is_locked`/`role_version`)
- 감사 무결성 체인 인메모리 → WORM/hash chain 영속 저장소
- 내부 오류 메시지 외부 노출 제거 (error code 표준화, `Database error: ...` 제거)
- `/api/alerts` 권한 경계 회귀 테스트 (e2e dependency_overrides 우회 보완)

### 7.2 인프라/네트워크 축

- DB/Redis/Mongo/Prometheus/Grafana/Jaeger host port publish 제거 → private network only
- Grafana 기본 비밀번호 폴백(`aqts2026`) 제거 + 미설정 시 fail-closed 부팅
- Nginx TLS/HSTS/modern TLS, KIS websocket `wss://` 강제, DB 연결 TLS 강제
- `/metrics` IP allowlist 또는 인증 보호
- CORS 환경변수 키 통일 (`CORS_ALLOWED_ORIGINS` 단일화)

### 7.3 정합성 축

- ReconciliationEngine 스케줄러 wiring + 불일치 임계 초과 시 Kill Switch
- Stale quote 감지 + pre-trade price guard + post-trade slippage guard
- 주문 상태 전이 유효성 (PENDING→SUBMITTED→FILLED/CANCELLED 외 차단)
- 주문 체인 단계별 히스토그램 (`signal_generated_at → order_sent_at → broker_ack_at → fill_at`)
- 알림 영속화 실패 관측성 (`aqts_alert_persist_failure_total` + AlertRule)

### 7.4 관측성 축

알람 규칙 8종 추가:
- `order_reject_rate` (5분 윈도우)
- `slippage_p95` (전략/시장별)
- `ledger_diff_abs` (즉시 alert, 임계 0)
- `token_refresh_from_access_detected` (즉시 alert)
- `audit_write_failures` (즉시 alert)
- `clock_drift_ms` (NTP)
- `stale_quote_order_attempts` (즉시 alert)
- `revoked_token_acceptance_detected` (즉시 alert)

추가:
- KIS degraded Grafana 패널 스테이징 육안 검증
- 로그 PII/자격증명 마스킹 processor 체인

## 8. P2 — 중장기 (1~2개월)

- 감사 로그 WORM + 서명/해시 앵커링 (Merkle tree 또는 외부 timestamping)
- 역할 변경 즉시 세션 무효화 (`role_version` 증가 시 기존 토큰 전수 거부)
- 재현 가능 포렌식 패키지 (주문 1건 단위 — 입력/모델/시드/권한 완전 재생성)
- 공급망 게이트(`pip-audit`/`grype`/`cosign`) 실작동 상시 검증
- DB 백업 복원 테스트 자동화
- 임계값 실측 기반 재조정 (`DEFAULT_ALERT_THRESHOLD` 등)
- FEATURE_STATUS / release-gates 의 KIS 복구 섹션 §8 → §8.10 수동 정리

## 9. 진행 기록 (Append-only)

각 P0 항목 완료 시 아래에 추가한다. 형식: `[YYYY-MM-DD] P0-N: <한 줄 요약> (<commit>)`.

- (대기 중)

## 10. 부록 — 직접 관찰 결과 요약

본 로드맵의 근거가 된 직접 코드 관찰 결과(2026-04-08).

### 10.1 `backend/core/order_executor/executor.py`

- `grep "TradingGuard|trading_guard"` → 0건. 가드 미적용.
- `grep "stale|quote|tick_age|last_tick|reference_price|current_price|market_price"` → 0건.
- Market order 경로에 `estimated_price=100.0` 하드코딩, mock 경로 `avg_price=100.0`.

### 10.2 `backend/core/reconciliation.py`

- 122 lines. 비교 엔진만 존재.
- 호출부: `tests/test_capital_protection.py` 1건. 운영 경로 wiring 0건.
- `datetime.utcnow()` (deprecated) 사용. 수량 비교 `abs(diff) > 1e-6` (정수 수량에 부적절).

### 10.3 `backend/core/scheduler_idempotency.py`

- "거래일 + 이벤트 타입" 단위 idempotency. 주문 단위 idempotency 와 무관.
- Redis TTL 패턴은 잘 잡혀 있어 P0-2 의 reference 로 활용 가능.

### 10.4 `backend/api/routes/orders.py`

- RBAC: `require_operator`/`require_viewer` 모두 적용 (통과).
- `audit.log()` 가 try/except 바깥에서 호출 (P0-4 대상).
- `OrderCreateRequest` 에 idempotency 필드 부재 (P0-3 대상).
- 실패 응답이 HTTP 200 + `success=False` (P1 후보).

### 10.5 `backend/core/trading_guard.py`

- 345 lines. docstring §6 에 "주문별 사전 검증" 명시.
- `trading_scheduler.py` 의 일일 리셋 경로에서만 import. 주문 경로 wiring 0건.

---

본 문서는 P0 진행 및 P1/P2 재정렬에 따라 지속적으로 갱신된다. 모든 변경은
관련 커밋과 함께 §9 진행 기록에 추가된다.
