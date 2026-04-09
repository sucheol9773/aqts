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

## 3.6 P0 공통 장애 정책 (Failure Mode Policy)

P0 항목들이 의존하는 외부 저장소(Redis, 감사 DB) 장애 시의 동작은 사전에
정책으로 고정한다. "그때 가서 결정" 은 금융 시스템에서 가장 위험한 안티패턴이다.

### 3.6.1 핵심 원칙

- **주문 경로(write 계열)**: 보수적 fail-closed. 의심스러우면 차단한다.
  잘못된 차단(false positive)은 운영 사고로 끝나지만, 잘못된 통과(false
  negative)는 자금 손실로 끝난다.
- **읽기 전용 경로(read 계열)**: degraded mode 허용. 단, degraded 상태는
  반드시 Prometheus gauge 로 가시화하고 alert 한다.
- **모든 fail-closed 차단은 감사 로그에 기록**한다. "왜 차단되었는가" 를
  사후 재구성 가능해야 한다.

### 3.6.2 P0-2 장애 정책 (Token Revocation + Rate Limiter)

**Redis 장애 시:**

| 컴포넌트 | 정책 | 응답 | 근거 |
| --- | --- | --- | --- |
| Token revocation 조회 | **fail-closed** | 401 Unauthorized + `WWW-Authenticate: Bearer error="server_error"` | 차단된 토큰을 통과시키면 탈취 토큰 재활용. 절대 허용 불가 |
| Rate limiter 조회 | **fail-closed (보수적)** | 429 Too Many Requests + `Retry-After: 5` | brute-force 방어선이 무너지는 것보다 일시적 차단이 안전 |
| 메트릭 | `aqts_redis_dependency_failure_total{component="revocation\|ratelimit"}` | — | 즉시 alert 임계 0 |

**예외**: `/health`, `/api/health/liveness`, `/metrics` 등 인증 불필요 엔드포인트는
fail-closed 대상에서 제외한다 (모니터링 자체가 막히면 안 됨).

### 3.6.3 P0-3 장애 정책 (Order Idempotency Store)

**Redis 장애 시:**

| 단계 | 정책 | 응답 |
| --- | --- | --- |
| 키 등록 시도 | **fail-closed** | 503 Service Unavailable + `Retry-After: 10` |
| DB 유니크 제약 | 이중 방어선으로 항상 동작 | 동일 키 재시도 시 409 Conflict |
| 메트릭 | `aqts_order_idempotency_store_failure_total` | 즉시 alert |

**근거**: 주문은 자금 이동이 따르는 행위이므로, idempotency 보장이 깨진 상태에서
주문을 받아주는 것은 중복 체결 위험을 그대로 떠안는 행위다. Redis 장애 시
**모든 신규 주문을 503 으로 일시 차단**하고 운영자가 복구 후 재개한다.

**DB 유니크 제약은 fallback 방어선**: Redis 가 살아 있어도 DB 유니크가
이중으로 검증한다. 둘 중 하나라도 실패하면 즉시 차단.

### 3.6.4 P0-4 장애 정책 (Audit Log Fail-Closed)

**감사 DB 장애 시:**

| 항목 | 정책 |
| --- | --- |
| HTTP 응답 코드 | **503 Service Unavailable** (500 아님) |
| 응답 본문 | `{"success": false, "error_code": "AUDIT_UNAVAILABLE", "message": "감사 시스템 일시 장애로 주문이 차단되었습니다", "retry_after_seconds": 30}` |
| `Retry-After` 헤더 | `30` (운영자 개입 시간 확보) |
| 트랜잭션 | rollback (주문 미체결) |
| 메트릭 | `aqts_audit_write_failures_total` (즉시 alert, 임계 0) |

**500 이 아닌 503 인 이유**: 500 은 "서버 코드 버그" 를 의미하므로 클라이언트가
재시도하면 안 되는 신호로 해석된다. 503 은 "일시적 장애" 를 명확히 하므로
재시도가 가능하면서도 backoff 를 강제한다.

**클라이언트 재시도 정책 (권장)**:
- 최대 재시도: 3회
- Backoff: exponential with jitter (5s, 15s, 45s)
- `Retry-After` 헤더가 있으면 그 값을 우선
- 3회 실패 시 운영자 알림 + 사용자에게 명시적 실패 표시
- 동일 idempotency key 사용 (P0-3 와 결합)

**SLO**: 감사 DB 가용성 99.95% 이상. 그 미만이면 감사 DB 자체를 HA 로 재설계해야 함.

### 3.6.5 P0-5 장애 정책 (TradingGuard)

**TradingGuard 내부 상태(in-memory) 손상 또는 의존 데이터 조회 실패 시:**

| 상황 | 정책 |
| --- | --- |
| Guard 상태 로드 실패 | **fail-closed**: 모든 신규 주문 차단, 503 응답 |
| Kill switch 상태 불명 | **fail-closed**: kill switch on 으로 간주 |
| 일일 손실 데이터 조회 실패 | **fail-closed**: 한도 초과로 간주 |

**근거**: Guard 가 "판단할 수 없는 상태" 에서 주문을 통과시키면 가드의 존재
이유가 사라진다.

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

## 4.1 롤백 리허설 (Rollback Rehearsal)

각 P0 항목은 **배포 전에 롤백 절차를 미리 리허설** 한다. CLAUDE.md "오류 수정
시 관찰 우선" 원칙의 배포 버전이다. 사고가 났을 때 처음으로 롤백 명령을
실행하면 늦다.

### 4.1.1 리허설 항목 (P0 공통)

각 P0 항목 PR 의 description 에 다음 4가지를 반드시 포함한다.

1. **적용 전 검증 (pre-deploy verification)**
   - 운영 트래픽 영향 없는 경로에서 dry-run 또는 canary
   - 영향 받는 메트릭의 baseline 값 (적용 후 비교용)
   - 데이터 마이그레이션이 있다면 reversibility 확인

2. **적용 후 검증 (post-deploy verification)**
   - smoke test 명령 (직접 curl 또는 통합 테스트)
   - 핵심 메트릭이 baseline 대비 정상 범위인지
   - 새로 추가된 카운터/gauge 가 실제로 발사되는지 (`curl /metrics | grep ...`)
   - 5분간 에러율 baseline 유지 확인

3. **장애 시 롤백 커맨드 (rollback runbook)**
   - 코드 롤백: 정확한 git 커맨드 (`git revert <hash>` 또는 이전 image tag)
   - 환경변수 롤백: 변경된 키와 이전 값
   - DB 롤백: 마이그레이션이 있다면 `alembic downgrade <revision>` 의 revision
   - Redis 키 정리: 새로 추가된 키 패턴과 `SCAN + DEL` 명령
   - 예상 소요 시간 (목표: 5분 이내)

4. **롤백 후 데이터 정합성 체크**
   - 주문 / 잔고 / 감사 로그가 정상 상태인지 확인하는 SQL 또는 API 호출
   - 롤백 전후로 누락·중복·순서역전이 없는지
   - reconciliation 한 번 강제 실행 (P1 reconciliation wiring 후에는 자동)

### 4.1.2 P0 항목별 롤백 시나리오 (요약)

**P0-1 (Refresh type 검증)**
- 롤백: `git revert <hash>` 만으로 충분 (코드 변경만)
- 정합성 체크: 기존 발급된 토큰의 정상 동작, refresh 카운터

**P0-2 (Revocation/RateLimit Redis)**
- 롤백: 코드 revert + 환경변수 `AQTS_REVOCATION_BACKEND=memory` 임시 fallback (제공 시)
- Redis 키 정리: `SCAN MATCH "aqts:revocation:*" + DEL`, `aqts:ratelimit:*`
- 정합성 체크: 토큰 무효화 동작, rate limit 차단 카운터

**P0-3 (주문 idempotency)**
- 롤백: 코드 revert + alembic downgrade (DB 유니크 제약 제거)
- Redis 키 정리: `SCAN MATCH "aqts:order:idem:*" + DEL`
- 정합성 체크: 주문 수와 fill 수 일치, 중복 주문 0건 SQL 검증

**P0-4 (감사 fail-closed)**
- 롤백: 코드 revert (트랜잭션 경계만 되돌림)
- 정합성 체크: 감사 로그 누락 구간 SQL 조회 (롤백 전후 timestamp range)
- **주의**: 롤백 시 fail-open 으로 돌아가므로 즉시 후속 fix 필요

**P0-5 (TradingGuard wiring)**
- 롤백: 코드 revert (executor 진입부 가드 호출 제거)
- 환경변수: `AQTS_TRADING_GUARD_ENABLED=false` 로 즉시 비활성화 가능하게 환경변수 게이트 추가 권장
- 정합성 체크: 롤백 후 차단된 주문 목록 (재실행 필요 여부 판단), `aqts_trading_guard_reject_total`

### 4.1.3 리허설 책임

- **PR 작성자**는 위 4가지를 PR description 에 작성한다.
- **머지 전**: 운영자가 롤백 커맨드를 staging 환경에서 한 번 실제로 실행해본다.
- **머지 후**: 본 문서 §9 진행 기록에 "rollback rehearsal: passed/failed" 를 기록한다.
- **실패 시**: 롤백 절차를 수정하고 다시 리허설. 통과 전까지 prod 배포 금지.

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

- [2026-04-09] P1-정합성: ReconciliationRunner 실제 wiring + PortfolioLedger + KIS broker provider (§7.3) — 이전까지 `ReconciliationEngine`/`ReconciliationRunner` 는 정의되어 있었으나 production 부트스트랩(`scheduler_main.py`, `main.py` embedded mode) 어디에서도 `register_reconciliation_runner` 가 호출되지 않아 통제가 형식적이었다 (Wiring Rule 의 정합성 도메인 사례, 9위 RBAC 회고와 동일 유형). 본 커밋은 정의-적용 간극을 단일 vertical slice 로 닫는다. **(1) `core/portfolio_ledger.py` 신설** — `PortfolioLedger` 는 OrderExecutor 가 체결 직후 호출하는 단일 mutator. `record_fill(ticker, side, qty)` 는 BUY 양수/SELL 음수로 누적하며 결과가 음수가 되면 `LedgerInvariantError` 로 거부(long-only 정합성 가정), 0 잔량은 dict 에서 즉시 제거하여 ReconciliationEngine 비교 시 불필요한 mismatch 가 생기지 않도록 한다. `asyncio.Lock` 으로 직렬화. `get_positions()` 는 외부 mutation 영향을 받지 않는 dict copy 반환. 프로세스 전역 싱글톤(`get_portfolio_ledger`/`reset_portfolio_ledger`). 영속화는 후속 항목으로 분리(현 단계는 in-memory) — DB 전환 시 본 모듈만 교체하면 ReconciliationRunner/Engine 은 변경 불필요(의존성 역전). **(2) `core/order_executor/executor.py` wiring** — `execute_order` 의 성공 경로(`status in {FILLED, PARTIAL} and filled_quantity > 0`)에서 `get_portfolio_ledger().record_fill(...)` 호출. 부분 체결도 그 시점의 `filled_quantity` 만 누적하여 reconcile 정확성을 보장. ledger 가 `LedgerInvariantError` 를 raise 하면(이미 정합성이 깨진 상태) 주문은 롤백 불가하므로 `logger.critical` 로만 기록하고 reconcile 사이클이 mismatch 를 잡도록 둔다. **(3) `core/reconciliation_providers.py` 신설** — `KISBrokerPositionProvider` 는 `kis_client.get_kr_balance()` 응답의 `output1` 배열을 PositionMap 으로 정규화. 5가지 fail-closed 검증: (a) response 가 dict 가 아니면 거부, (b) `output1` 이 None 이면 빈 dict (정상), (c) list 가 아니면 거부, (d) row 가 dict 가 아니거나 `pdno`/`hldg_qty` 누락 시 거부, (e) 수량이 non-numeric 또는 음수면 거부. 모두 raw KIS dict 외부 노출 없이 `BrokerPositionParseError` 로 정규화. 0주 종목은 결과에서 제외하여 ledger 와 동일 정책. `LedgerPositionProvider` 는 ledger 싱글톤 snapshot 을 PositionMap 으로 반환. **(4) Bootstrap wiring** — `scheduler_main.py` (별도 컨테이너 모드)와 `main.py` (embedded scheduler 모드) 둘 다 KIS 토큰 초기화 직후에 `ReconciliationRunner(engine, broker_provider=KISBrokerPositionProvider, internal_provider=LedgerPositionProvider)` 를 생성하고 `scheduler.register_reconciliation_runner(runner)` 호출. degraded 모드(KIS 토큰 실패) 또는 backtest 모드에서는 reconcile 자체가 무의미하므로 등록 생략 + 경고 로그. 등록되면 `_default_handle_intraday_check` (MIDDAY_CHECK) 와 `_default_handle_post_market` 가 매 사이클에 `_run_reconciliation_if_wired` 를 통해 reconcile 을 실행하고, mismatch 발견 시 `RECONCILIATION_MISMATCHES_TOTAL` Counter + `TradingGuard.activate_kill_switch()` 가 즉시 작동하여 OrderExecutor 의 모든 후속 주문을 P0-5 wiring 으로 차단. **(5) 테스트 26건 신규**: `tests/test_portfolio_ledger.py` 11건 — buy/sell 누적, 부분 매도 잔존, 다종목 독립, **short 거부 + 거부 후 ledger 미변경**, 매수 없이 매도 거부, snapshot mutation 격리, 빈 ticker / 0/음수 quantity 거부, 50건 동시 매수 직렬화, 싱글톤 idempotence/reset. `tests/test_reconciliation_providers.py` 11건 — 정상 파싱, 0주 row 제외, missing/non-list/non-dict/missing field/non-numeric/negative 거부, upstream exception 래핑, ledger provider 기본 싱글톤. `tests/test_reconciliation_wiring.py` 4건 — `scheduler_main.py` 와 `main.py` 가 모두 `register_reconciliation_runner` 를 AST 로 호출하는지 정적 검사(둘 중 하나라도 빠지면 실패), end-to-end match 시나리오(ledger 두 건 → static broker 두 건 → matched=True), end-to-end mismatch 시나리오(ledger 100 vs broker 70 → mismatches[0] context 검증 + `TradingGuard._state.kill_switch_on is True`). **핵심 불변식**: 본 커밋 이후 운영 부트스트랩에서 ReconciliationRunner 가 register 되지 않은 경로는 AST 정적 검사로 차단되며(둘 중 하나라도 호출이 빠지면 CI 실패), MIDDAY_CHECK / POST_MARKET 사이클마다 broker ↔ ledger 정합성이 자동 검증되어 mismatch 는 kill switch 로 즉시 거래 중단을 유발한다. 사후 reconcile 까지 포함하여 §7.3 의 "주문 직전(quote guard) → 체결(executor) → 사후(reconcile)" 3단 안전망이 비로소 모두 작동한다. 게이트: pytest 3625 passed, ruff/black/check_rbac_coverage/check_bool_literals/check_doc_sync 모두 PASS.
- [2026-04-09] P1-정합성: KIS QuoteProvider 실구현 + TTL 캐시 + orders.py wiring (§7.3) — `core/order_executor/quote_provider_kis.py` 신설. `KISQuoteProvider` 는 `price_guard.QuoteProvider` Protocol 의 운영 구현체로 `KISClient` 를 의존성 주입 받아 단위 테스트에서 fake 로 교체 가능. **TTL 캐시**: `(ticker, market)` 키로 `_CacheEntry(quote, expires_monotonic)` 를 보관, 기본 `cache_ttl_seconds=1.5` 로 `PriceGuardConfig.max_quote_age_seconds=5.0` 보다 충분히 작아 캐시된 quote 가 stale 임계를 절대 초과하지 않음을 수학적으로 보장. **Stampede 방지**: 키별 `asyncio.Lock` + `_global_lock` 으로 lock-map 자체의 mutation 을 직렬화. 동일 키 동시 미스가 발생해도 upstream KIS 호출은 1회만 발생 (10병렬 → 1호출 검증). **마켓 라우팅**: `Market.KRX → get_kr_stock_price` (응답 `output.stck_prpr`), `NASDAQ/NYSE/AMEX → get_us_stock_price` 로 분기, 거래소 코드는 `_US_EXCHANGE_CODE = {NAS, NYS, AMS}` 단일 dict 에 격리. **Fail-closed 파싱**: `_parse_kr_price`/`_parse_us_price` 가 dict 형식, 필수 필드, 숫자 변환 가능성, 0 이하 값을 모두 `QuoteFetchError` 로 정규화 — raw KIS dict 는 모듈 외부로 절대 누출되지 않음. **메모리 상한**: `max_cache_entries=4096` 도달 시 `expires_monotonic` 이 가장 빠른 항목 1건 evict. **관측**: `aqts_quote_cache_hits_total{market}` / `aqts_quote_cache_misses_total{market}` Counter + `aqts_quote_fetch_latency_seconds{market}` Histogram (5ms~5s buckets, miss path 한정). **싱글톤**: `get_kis_quote_provider()` / `reset_kis_quote_provider()` 가 프로세스 전역 단일 인스턴스를 공유하여 라우트마다 `OrderExecutor` 를 새로 만들어도 캐시는 유지. **Wiring**: `api/routes/orders.py` 의 `POST /` 와 `POST /batch` 두 경로에서 `OrderExecutor(quote_provider=get_kis_quote_provider())` 로 주입 — 정의 ≠ 적용 원칙에 따라 `tests/test_orders_quote_provider_wiring.py` 가 AST 정적 검사로 모든 `OrderExecutor()` 호출이 `quote_provider=get_kis_quote_provider()` 키워드 인자를 갖는지 강제. **테스트**: `tests/test_kis_quote_provider.py` 27건 + `tests/test_orders_quote_provider_wiring.py` 5건 = 32건 신규. 게이트: ruff 0 errors, black clean, pytest 3599 passed (전체), check_rbac_coverage / check_bool_literals / check_doc_sync 모두 PASS. 핵심 불변식: 본 커밋으로 운영 라우트 전체에 실제 KIS 시세를 흘려보내는 단일 진입점이 확립됨 — `OrderExecutor` 의 `quote_provider=None` 경로가 코드상 존재하더라도 라우트 정적 검사로 차단되어 운영 진입 자체가 불가능.
- [2026-04-09] P1-정합성: Stale quote + pre/post-trade price guard (§7.3) — `core/order_executor/price_guard.py` 신설. 순수 함수 + `QuoteProvider` Protocol + dataclass `Quote`/`PriceGuardConfig` + 예외 계층(`PriceGuardError`/`StaleQuoteError`/`PriceDeviationError`/`QuoteFetchError`) 로 OrderExecutor 와 독립된 무의존 가드 계층을 확립. `Quote.__post_init__` 이 non-positive price 와 naive datetime 을 즉시 거부하여 "존재하는 Quote 객체는 항상 유효" 불변식 보장. `PriceGuardConfig` 기본값은 보수적: `max_quote_age_seconds=5.0`, `max_pre_trade_deviation_pct=0.02`, `max_post_trade_slippage_pct=0.01`. 핵심 함수 3종: (1) `assert_quote_fresh` — 조회 시각 기준 age 가 limit 초과 또는 **미래 시각**(시각 skew/조작 의심)이면 `aqts_stale_quote_rejects_total{market}` 증가 후 `StaleQuoteError` raise, (2) `assert_pre_trade_price` — BUY 는 `order > reference*(1+dev)`, SELL 은 `order < reference*(1-dev)` 만 차단하고 유리한 방향(BUY 가 싸게 / SELL 이 비싸게)은 통과 — 체결 가능성만 낮을 뿐 무결성 위반이 아니기 때문, (3) `check_post_trade_slippage` — 브로커 체결 이후이므로 **예외를 raise 하지 않고** `aqts_post_trade_slippage_alerts_total{severity,market}` 만 증가; severity 는 deviation > 2x 이면 `critical` 아니면 `warn`; 유리 방향은 silent skip. 합성 헬퍼 `fetch_and_validate_quote` 는 provider 의 임의 예외를 `QuoteFetchError` 로 정규화하며 `aqts_quote_fetch_failures_total{market,reason∈provider_error|unexpected|identity_mismatch}` 를 분기 증가 — 반환된 Quote 의 ticker/market 이 요청과 불일치하면 identity_mismatch 로 거부. `core/monitoring/metrics.py` 에 Counter 4종 추가. OrderExecutor wiring: `OrderExecutor.__init__` 에 `quote_provider: Optional[QuoteProvider]`, `price_guard_config: Optional[PriceGuardConfig]` 파라미터 추가. `execute_order` 는 contract 검증 + TradingGuard + `_validate_order` 통과 후, **live 경로에서만** (`not self._dry_run and not self._kis_client.is_backtest`) 다음 블록을 실행: (1) `quote_provider is None → QuoteFetchError("refusing to trade blind")` fail-closed (운영 경로에서 provider 주입 누락 자체가 배포 버그), (2) `fetch_and_validate_quote` 로 기준 시세 획득 + stale 검증, (3) `OrderType.LIMIT + limit_price` 있으면 `assert_pre_trade_price` 로 방향성 밴드 검증. 디스패치 후 `filled_quantity > 0 and avg_price > 0` 이면 동일 reference_quote 로 `check_post_trade_slippage` 호출, 임계 초과 시 `logger.critical("Post-trade slippage exceeded: ...")` 기록 (롤백 불가이므로 관측만). dry_run/backtest 경로는 모의 가격 100.0 을 사용하므로 전 가드 우회. 유닛 테스트 35건 (`tests/test_price_guard.py`): Quote/Config 입력 검증, compute_deviation_pct, assert_quote_fresh (fresh/stale/future/max_age<=0), assert_pre_trade_price (BUY/SELL 각 4케이스 + non-positive 3종), check_post_trade_slippage (밴드 내/유리 방향 2종/warn/critical/SELL warn/fill<=0 skip/max<=0 거부), StaticQuoteProvider hit/miss, fetch_and_validate_quote (성공/QuoteFetchError 재전파/unexpected 래핑/identity_mismatch/fetch 후 stale). 통합 테스트 6건 (`tests/test_order_executor_price_guard.py`): `live_executor_factory` fixture 가 `KISClient.is_backtest` 프로퍼티를 `False` 로 override 한 뒤 테스트 종료 시 원복 (프로세스 전역 상태 누출 방지). (1) live + provider=None → `QuoteFetchError`, `_execute_market_order` 호출되지 않음, (2) live + 30초 stale quote → `StaleQuoteError`, (3) live + LIMIT 77000 (reference 70000, +10%) → `PriceDeviationError`, `_execute_limit_order` 호출되지 않음, (4) live + LIMIT 70700 (+1%, band 내) → 체결 경로 도달, (5) live + market 체결 71050 (+1.5% > 1% band) → 주문 성공 + `aqts_post_trade_slippage_alerts_total{severity="warn",market="KRX"}` 정확히 +1, (6) dry_run + provider=None → guard 전체 우회. 핵심 불변식: **운영 경로에서 quote 없이 주문이 브로커로 흘러가는 경로가 수학적으로 존재하지 않는다** — OrderExecutor 의 하드코딩 `estimated_price=100.0` 가 silent 체결을 유발하던 사각지대(§7.3 위험 요소)를 provider 주입 강제 + fail-closed 로 차단한다. 사후 slippage 는 fail-open 이지만 Counter + critical log 로 관측 가능. 설계 근거: 주문 시점의 시세와 체결 시점의 브로커 응답 사이 계약이 없으면, 스프레드/레이턴시/오타 어느 원인이든 "의도한 가격으로 체결됐는가?" 를 사후 검증할 수 없다. 게이트: pytest 1796+ passed (price_guard 41건 포함, gen_status 재생성), ruff/black 0 errors, check_rbac_coverage/check_bool_literals 0 errors.
- [2026-04-09] P1-정합성: 주문 상태 전이 유효성 검증 (OrderStateMachine wiring, §7.3) — `core/order_executor/order_state_machine.py` 신설. `VALID_ORDER_TRANSITIONS: Dict[OrderStatus, Set[OrderStatus]]` 단일 진실원천 정의: `PENDING→{SUBMITTED,CANCELLED,FAILED}`, `SUBMITTED→{PARTIAL,FILLED,CANCELLED,FAILED}`, `PARTIAL→{FILLED,CANCELLED,FAILED}`, `FILLED/CANCELLED/FAILED→∅` (종결 상태). `InvalidOrderTransition` 예외 + `assert_order_transition(from,to,*,order_id)` / `assert_can_cancel(current,*,order_id)` / `parse_order_status(raw)` / `can_transition_order` / `is_terminal_order_state` 헬퍼 제공. 거부 시 `aqts_order_state_transition_rejects_total{from_state,to_state}` Counter 증가 — 알람 임계 0 (무결성 위반 또는 코드 경로 버그 신호). `api/errors.py` 에 `INVALID_ORDER_TRANSITION`, `ORDER_STORE_UNAVAILABLE` 두 enum 추가. `api/routes/orders.py::cancel_order` 의 기존 인라인 하드코딩 비교(`if current_status not in ("PENDING", "SUBMITTED")` → 200 + `success=False` + 한국어 메시지) 를 OrderStateMachine 경로로 교체: (1) `parse_order_status` 로 DB raw 값 → enum 파싱, 무결성 위반(알 수 없는 값) → 503 `ORDER_STORE_UNAVAILABLE` + `Retry-After: 30` fail-closed, (2) `assert_can_cancel` 로 전이 가능 여부 검증, 거부 시 409 `INVALID_ORDER_TRANSITION` + `{order_id, current_status, target_status}` context, (3) audit `previous_status` 메타데이터를 enum value 로 정규화. 이전 구현의 200+success=False 는 HTTP 의미(200=성공)와 응답(`success=False`) 이 모순되어 프론트엔드가 구조적으로 분기할 수 없었고, 기계 판독 가능한 `error.code` 도 없었다. 유닛 테스트 23건 (`tests/test_order_state_machine.py`): `TestTransitionMatrix` — 매트릭스가 OrderStatus 전 값을 포함하고 종결 상태는 outgoing 집합이 공집합, `TERMINAL_ORDER_STATES` / `CANCELLABLE_ORDER_STATES` 상수가 매트릭스에서 유도됨을 검증. `TestCanTransitionPositive` — 허용된 10개 전이 parametrize. `TestCanTransitionNegative` — skip 전이(`PENDING→FILLED`), 역방향(`SUBMITTED→PENDING`), 종결 outgoing 등 10개 거부 케이스. `TestAssertOrderTransition` — 거부 시 `InvalidOrderTransition` raise 및 Counter 정확히 +1, 예외 attributes (from_state/to_state/order_id) 와 문자열 포맷 검증. `TestAssertCanCancel` — `PENDING/SUBMITTED/PARTIAL` 통과, 종결 3종은 모두 Counter 증가 + 예외. `TestIsTerminalOrderState` / `TestParseOrderStatus` — 경계 케이스(공백, 소문자, trailing space) 전부 `ValueError`. 통합 테스트 9건 (`tests/test_order_state_machine_cancel_route.py`): `authenticated_app` + `operator_token` + `_override_db_for_status` 헬퍼로 cancel_order 라우트 전체 경로(JWT→DB 재확인→require_operator→text() SELECT status→assert_can_cancel→UPDATE→audit)를 ASGITransport 로 end-to-end 검증. (1) `PENDING/SUBMITTED/PARTIAL` → 200 `{status:"CANCELLED"}`, (2) `FILLED/CANCELLED/FAILED` → 409 `INVALID_ORDER_TRANSITION` + context 필드(`current_status`/`target_status`/`order_id`) + Counter +1, (3) 알 수 없는 DB 값 `"WEIRD_VALUE"` → 503 `ORDER_STORE_UNAVAILABLE` + `Retry-After: 30`, (4) 존재하지 않는 order_id → 404 `ORDER_NOT_FOUND`, (5) viewer 토큰 → 403 (RBAC 가드 우회 없이 실제 실행). 레거시 테스트 `tests/test_coverage_api_routes_v2.py::test_cancel_order_non_cancellable` 은 이전의 200+success=False 계약을 검증하고 있었으므로 `HTTPException(409, INVALID_ORDER_TRANSITION)` 을 기대하도록 갱신 (기대값을 낮춘 것이 아니라, 상위 계약이 "성공으로 위장된 실패" → "명시적 실패" 로 상향됨). Wiring Rule 도메인 확장: "전이 매트릭스 정의 ≠ 라우트 적용" — 종전에는 라우트가 직접 문자열 비교를 하고 있어 규칙 변경이 즉시 반영되지 않았다. 본 커밋은 단일 진실원천을 확립하고 Prometheus Counter 로 "정의 ≠ 적용" 회귀를 관측 가능하게 만든다. 전체 게이트: pytest 3527 passed, ruff/black 0 errors.
- [2026-04-09] P1-보안: `/api/alerts` RBAC 회귀 테스트 (e2e dependency_overrides 우회 보완) — 기존 `tests/test_alerts_route_e2e.py` 는 `require_viewer` / `require_operator` 를 `dependency_overrides` 로 우회한 뒤 라우트 본문 동작만 검증했기 때문에 실제 RBAC 가드가 프로덕션처럼 작동하는지는 한 번도 검증된 적이 없었다. 본 커밋은 `tests/test_alerts_rbac_regression.py` 16건을 신설하여 auth 가드를 **우회하지 않은** 상태에서 다음 불변식을 검증한다. **읽기 경로** (`GET /api/alerts/`, `GET /api/alerts/stats`): (1) 토큰 없음 → 401, (2) viewer/operator/admin 토큰 → 200 (parametrize 3-way), 모든 역할이 읽기는 허용되어야 한다. **변경 경로** (`PUT /api/alerts/{id}/read`, `PUT /api/alerts/read-all`): (1) 토큰 없음 → 401, (2) viewer 토큰 → 403 (read-only 역할 mutation 금지), (3) operator/admin 토큰 → 200 (parametrize 2-way), 응답 body 의 `marked_count` / `success` 까지 검증. `AlertManager` 는 `app.dependency_overrides[get_alert_manager] = lambda: manager` 로만 주입하여 Mongo 없이 라우트가 동작하도록 하되, **이 override 는 AlertManager 주입 지점만 건드릴 뿐 `require_viewer` / `require_operator` RBAC 가드는 그대로 통과시킨다** — 즉 테스트는 실제 JWT 검증 → `get_current_user` → `require_*` 가드 경로 전체를 소비한다. `authenticated_app` fixture 가 제공하는 admin/operator/viewer UUID 와 `conftest.py` 의 `*_token` fixture(`rv=0` 포함)가 DB 재확인 경로까지 투명하게 통과한다. 핵심: RBAC 가드는 "정의했다 ≠ 적용했다" 의 최우선 점검 대상이며, dependency override 로 우회한 기존 테스트는 가드가 사라져도 알람이 울리지 않는다 — 이번 회귀 테스트가 이 사각지대를 메운다. (전체 pytest 3469 passed / ruff / black / check_rbac_coverage / check_bool_literals / check_doc_sync 0 errors)
- [2026-04-09] P2-역할 변경 즉시 세션 무효화 (role_version monotonic counter): `users.role_version INTEGER NOT NULL DEFAULT 0` 컬럼을 `alembic/versions/004_user_role_version.py` 마이그레이션으로 신설하고 `db/models/user.py` 에 `Mapped[int]` 로 반영. `AuthService.authenticate` 의 로그인 토큰 발급부가 access/refresh 두 토큰 모두에 `rv` 클레임(현재 DB 값)을 포함하도록 확장. `api/middleware/auth.py::get_current_user` 는 JWT 서명/type/만료/revocation/DB 재확인(P1-보안) 통과 후 추가로 `payload["rv"]` 와 `user.role_version` 의 **완전 일치**만을 통과시킨다 — (1) `rv` 누락(legacy token) → 401 `ROLE_VERSION_MISMATCH`, (2) 비-int `rv` → 401, (3) `token_rv < db_rv` (역할 변경 이후) → 401, (4) `token_rv > db_rv` (DB 롤백/조작 의심, 단조 증가 invariant 위반) → 401. `api/routes/auth.py::refresh_token` 도 `db_session` 을 주입받아 refresh 토큰의 `rv` 를 검증하고 DB 에서 현재 `role_version` 을 재조회해 새 access/refresh 를 **DB 현재값**으로 재발급한다 — 즉 refresh 경로도 역할 변경 직후 silent window 없이 즉시 거부된다. `api/routes/users.py::update_user` 의 역할 변경 경로는 `previous_role_id != role.id` 일 때만 `user.role_version += 1` 로 단조 증가 — 같은 역할로의 재지정은 세션을 터뜨리지 않지만 `operator→viewer→operator` 복구처럼 **이름이 같아진** 변경도 role_id 전이 이력이 남기 때문에 기존 토큰이 전부 무효화된다 (role.name 비교만으로는 포착 불가능했던 사각지대). `api/errors.py` 에 `ROLE_VERSION_MISMATCH` 추가. `tests/conftest.py` 의 admin/operator/viewer fixture 토큰과 User 객체에 `rv=0` / `role_version=0` 주입, `tests/test_get_current_user_db_recheck.py`, `tests/test_rbac.py`, `tests/test_refresh_token_type.py`, `tests/test_auth_401_verification.py` 의 수동 조립 토큰 전부에 `rv: 0` 추가. `tests/test_refresh_token_type.py` 는 P2 이후 refresh 가 DB 재조회를 요구하므로 `_override_db_with_user()` 헬퍼로 `get_db_session` dependency override 를 주입해 정상 경로 테스트를 복구. 신규 테스트 10건 (`tests/test_role_version_enforcement.py`): `TestGetCurrentUserRoleVersion` — rv 누락/하위/상위/비-int 모두 401 `ROLE_VERSION_MISMATCH`, 정확히 일치 시 통과. `TestUsersPatchRoleVersionUnit` — role_id 변경 시 단조 증가, 동일 역할 재지정 시 불변, `viewer→operator→admin→viewer→(동일)→operator` 5-step 전이 시 `[1,2,3,3,4]` 단조성 검증. `TestJwtTokenIncludesRvClaim` — `AuthService.create_access_token` / `create_refresh_token` 이 `rv` 를 왕복 인코딩. 전체 pytest 3453 통과, ruff/black/check_rbac_coverage/check_bool_literals/check_doc_sync 0 errors. 핵심 불변식: **토큰의 rv 가 DB 의 role_version 과 완전히 일치하지 않으면 어떤 인가도 부여되지 않는다** — P1-보안의 role.name 등식이 포착하지 못하던 "이름 복귀" 시나리오와 "DB 롤백" 시나리오를 동시에 방어하며, 운영자가 `PATCH /users/{id}` 로 role_id 를 바꾸는 순간 해당 사용자의 **모든** 발급 토큰이 다음 요청에서 거부된다 (refresh 포함). RBAC Wiring Rule 의 authn/authz 분리 원칙을 토큰-DB 일관성 계층까지 확장.
- [2026-04-09] P1-알람 규칙: `monitoring/prometheus/rules/aqts_alerts.yml` 에 `aqts_security_integrity` 그룹 신설 — P0/P1 에서 instrument 한 모든 보안/정합성 메트릭에 대해 1:1 알람 14건 추가. (1) **TradingGuardKillSwitchActive** (`aqts_trading_guard_kill_switch_active == 1`, critical, for=0m) — P0-5 kill switch 가 활성화되면 즉시 알람. (2) **TradingGuardBlocksSpike** (`sum by (reason_code) (increase(...[5m])) > 5`, warning) — reason_code 별 5분 5건 이상 차단 감지. (3) **AuditWriteFailureStrict** (`increase(aqts_audit_write_failures_total{mode="strict"}[5m]) > 0`, critical) — P0-4 금전 쓰기 경로 감사 실패 단 1건도 허용하지 않음. (4) **AuditWriteFailureSoftSpike** (15분 10건, warning) — 읽기 경로의 soft 실패 급증. (5) **OrderIdempotencyStoreUnavailable** (`sum by (op) (increase(...[5m])) > 0`, critical) — P0-3a/3b store fail-closed 즉시 감지. (6) **AccessTokenReusedForRefresh** (`sum by (reason) (increase(aqts_token_refresh_from_access_total[10m])) > 0`, warning) — P0-1 refresh 엔드포인트 비정상 토큰 사용. (7) **RevocationBackendUnavailable** (critical) — P0-2a token revocation 백엔드 장애. (8) **RateLimitStorageUnavailable** (critical) — P0-2b slowapi storage 장애. (9) **RateLimitExceededSpike** (5분 50건, warning) — route 별 429 급증. (10) **ReconciliationLedgerDiffNonZero** (`aqts_reconciliation_ledger_diff_abs > 0`, critical, for=0m) — P1-정합성 원장 불일치 임계 0. (11) **ReconciliationMismatchDetected** (15분 mismatch 1건 이상, critical). (12) **ReconciliationRunnerErrors** (30분 error 결과, warning) — provider 장애. (13) **ReconciliationRunnerMissing** (`absent(...) == 1 or sum(increase(...[24h])) == 0`, warning, for=6h) — Wiring Rule 회귀 감지 (스케줄러는 동작하지만 runner 가 register 되지 않은 상태). (14) **EnvBoolNonStandardUsage** (1시간 1건, warning) — Phase 2 strict 승격 준비용 관찰 알람. 모든 규칙에 `domain ∈ {security, integrity, config}` 라벨 부여. 유닛 테스트 21건 추가 (`tests/test_alert_rules.py`): YAML 파싱/그룹 구조 검증, 모든 알람의 필수 필드(alert/expr/labels.severity/annotations.summary/description) 검증, `aqts_security_integrity` 그룹 알람의 `domain` 라벨 검증, `REQUIRED_METRIC_TO_ALERT` 매핑 12쌍 parametrized 테스트 (신규 메트릭이 추가되어도 알람이 누락되면 즉시 실패), TradingGuardKillSwitchActive 가 critical + 즉시 발화, AuditWriteFailureStrict 가 mode="strict" 필터 포함, ReconciliationLedgerDiffNonZero 가 `> 0` 임계 유지, 그룹 내 알람 이름 중복 없음 검증. 원칙: "알람 없는 metric 은 형식적 통제에 불과하다" — 정의 ≠ 적용 패턴의 감시 계층 적용. fail-closed 코드 경로가 있어도 운영자가 탐지하지 못하면 MTTR 이 무한대에 가깝다.
- [2026-04-09] P1-에러 메시지 표준화: `api/errors.py` 신설 — `ErrorCode` enum (22종: `VALIDATION_ERROR`, `UNAUTHORIZED`, `FORBIDDEN`, `NOT_FOUND`, `CONFLICT`, `INTERNAL_ERROR`, `USER_STORE_UNAVAILABLE`, `AUDIT_UNAVAILABLE`, `INVALID_TOKEN_TYPE`, `IDEMPOTENCY_*` 5종, `ORDER_NOT_FOUND`, `DRY_RUN_SESSION_NOT_FOUND`/`_CONFLICT`/`_UNAVAILABLE`, `PARAM_SENSITIVITY_NOT_FOUND`/`_INVALID_METRIC`, `RATE_LIMIT_EXCEEDED`/`_STORE_UNAVAILABLE`) + `raise_api_error(status, code, message, *, headers=None, **context)` 단일 진입점 + `normalize_error_body(status, detail)` — dict detail (신규 `{error_code, message, context}`), 문자열 detail, legacy dict (P0-2b rate limiter / P0-3a idempotency) 의 extras 를 `context` 로 이동시키는 하위호환 로직 포함. `main.py` 에 `app.add_exception_handler(HTTPException, _standard_http_exception_handler)` 등록 — 모든 `HTTPException` 응답이 일관된 `{"success": False, "error": {"code", "message", "context"?}}` 스키마로 직렬화된다. `api/routes/users.py` 의 7개 `str(e)` 유출 경로 (list/get/create/update/reset/lock/delete) 를 `raise_api_error(500, USER_STORE_UNAVAILABLE, <한국어 일반 메시지>)` 로 교체 — 더 이상 SQLAlchemy 스택/쿼리 단편이 클라이언트에 노출되지 않는다. `api/routes/param_sensitivity.py` 3개 사이트 (latest 404, tornado 404, tornado 400 invalid metric) + `api/routes/dry_run.py` 4개 사이트 (start 409 with `active_session_id`, stop 404, get_session 404 with `session_id`, clear 409) + `api/routes/orders.py` 2개 사이트 (404 `ORDER_NOT_FOUND` with `order_id`) + `api/routes/auth.py` refresh 401 (`INVALID_TOKEN_TYPE`, `WWW-Authenticate` 헤더 유지) 모두 `raise_api_error` 로 마이그레이션 — Prometheus `WWW-Authenticate` 헤더, rate limiter 의 `retry_after`, idempotency detail 등 기존 dict detail 경로는 `normalize_error_body` 의 legacy extras 로직이 자동으로 `context` 로 흡수하여 신규 스키마와 투명하게 호환된다. 단위 테스트 15건 신설 (`tests/test_error_standardization.py`): `TestRaiseApiError` (dict detail/string code/headers forward) + `TestNormalizeErrorBody` (dict+error_code/dict+context/dict missing code/legacy extras/string detail/500 default/422 default/non-string detail) + `TestGlobalHandlerIntegration` (orders not_found/param_sensitivity latest 404/dry_run stop 404 `DRY_RUN_SESSION_NOT_FOUND`/auth refresh with access token 401 `INVALID_TOKEN_TYPE` + 토큰 원문 비유출 검증). 기존 테스트 2건 수정 (`test_auth_401_verification.py::test_no_token_returns_401_not_403`, `test_refresh_token_type.py::test_access_token_is_rejected_with_401`) — 레거시 `body["detail"]` 대신 신규 `body["error"]["code"]` 스키마를 검증하도록 갱신. 핵심: 에러 응답의 **기계 판독 가능한 `error.code`** 를 단일 진실원천으로 확립하여 프론트엔드 / 알람 / SIEM 이 안정적으로 분류할 수 있고, `str(e)` 유출로 인한 내부 구조 정보 노출 경로를 일괄 제거한다.
- [2026-04-09] P1-보안: `get_current_user` DB 재확인 — `api/middleware/auth.py` 의 `get_current_user` 가 JWT 서명/만료/revocation 통과 후에도 `db_session: Optional[AsyncSession] = Depends(get_db_session)` 을 주입받아 `select(User).where(User.id == uid)` 로 실제 사용자 레코드를 재확인하도록 강화. (1) 사용자 미존재 → 401 `user no longer exists`, (2) `is_active=False` → 401 `inactive`, (3) `is_locked=True` → 403 `locked`, (4) DB `role.name != token_role` → 401 `role has changed; please re-authenticate`, (5) DB 장애 (execute 예외) → 503 `User store unavailable` + `Retry-After: 5` (token revocation 의 fail-closed 정책과 동일), (6) `user.role` 관계 누락 → 401 `role missing`. 반환값은 토큰이 아니라 **DB 의 현재 username/role** 로 구성되어 운영자가 사용자 역할을 강등해도 기존 토큰의 인가 경로가 즉시 끊긴다 — 이전 구조는 JWT role 클레임만 믿었기 때문에 role 변경이 토큰 만료까지 반영되지 않는 silent window 가 존재했다. 단위 테스트 7건 추가 (`tests/test_get_current_user_db_recheck.py`): 사용자 부재 / inactive / locked / role 불일치 / DB 장애 503 / role=None / 정상 경로가 DB role 반환. 기존 `tests/test_rbac.py::TestGetCurrentUser` 3건은 `db_session` fixture 를 명시적으로 주입하도록 수정, `tests/test_rbac.py::test_me_endpoint_requires_authentication` 의 uid 를 fixture 에 존재하는 `test-viewer-uuid` 로 정정, `tests/test_auth_401_verification.py::test_valid_token_returns_200` 을 `authenticated_app` fixture (mock DB session override) 로 전환. `tests/conftest.py` 의 `db_session` / `authenticated_app` 목 execute 를 `query.compile(literal_binds=True)` 로 컴파일한 뒤 쿼리 문자열에서 `User.id` UUID / username 으로 매칭하도록 업그레이드 — `get_current_user` 가 `username` 대신 `id` 로 조회하는 신규 쿼리와도 호환. RBAC Wiring Rule 의 "authn ≠ authz" 구분은 *라우트 게이트* 에 적용됐지만, 이번 작업은 그 아래 계층인 *토큰 → 사용자 상태 일관성* 을 강제한다. role_version 필드는 향후 schema migration 과 함께 2차 강화 (현재는 role.name 등식이 대체 지표).
- [2026-04-09] P1-정합성: ReconciliationEngine → TradingScheduler wiring (Wiring Rule, 정합성 도메인 확장) — 종전에는 `core/reconciliation.py` 의 `ReconciliationEngine` 이 정의만 되어 있고 어떤 스케줄러 핸들러에서도 호출되지 않아 형식적 통제였다. P0-5 의 "정의 ≠ 적용" 패턴을 정합성 축에 그대로 확장. `core/reconciliation_runner.py` 신설: `PositionProvider` Protocol + `ReconciliationRunner(engine, broker_provider, internal_provider, guard, mismatch_threshold)` 단일 진입점 — provider 호출 → `ReconciliationEngine.reconcile` → metric 갱신 → mismatch 가 임계 초과 시 `TradingGuard.activate_kill_switch` (싱글톤 기본). guard 미주입 시 `__post_init__` 에서 `get_trading_guard()` 자동 주입하므로 P0-5 의 OrderExecutor 차단 경로와 자동 결합. provider 장애는 `result="error"` 카운터 증가 후 fail-closed 재전파. `core/trading_scheduler.py` 의 `TradingScheduler.register_reconciliation_runner()` 추가, `_default_handle_midday_check` / `_default_handle_post_market` 가 `_run_reconciliation_if_wired()` 헬퍼를 호출하여 등록된 runner 가 있을 때만 reconcile 사이클 실행 (미등록 시 `wired=False, skipped=True` 로 종전 동작 유지). `core/monitoring/metrics.py` 에 3종 추가: `aqts_reconciliation_runs_total{result∈matched|mismatch|error}`, `aqts_reconciliation_mismatches_total` (cumulative), `aqts_reconciliation_ledger_diff_abs` Gauge (알람 임계 0). 통합 테스트 9건 추가 (`tests/test_reconciliation_runner.py`): matched 정상 / 임계 초과 mismatch 시 kill switch 활성화 + Counter / 임계 미달 mismatch 는 활성화 안 함 / provider 실패 → error 카운터 + 재전파 / 싱글톤 guard 자동 주입 검증 / TradingScheduler MIDDAY_CHECK 가 실제 runner 호출 / POST_MARKET 핸들러가 mismatch 시 kill switch 활성화 / runner 미주입 시 stub 결과 / 음수 임계 거부. 핵심 불변식: ledger 불일치가 발견되면 → kill switch 활성화 → P0-5 wiring 으로 OrderExecutor 가 모든 후속 주문 차단.
- [2026-04-08] P0-5: TradingGuard → OrderExecutor wiring (Wiring Rule 적용) — `core/trading_guard.py` 에 프로세스 전역 싱글톤 `get_trading_guard()` + `reset_trading_guard()` (테스트 전용) + `TradingGuardBlocked` 예외 + `check_pre_order(ticker, side, quantity, limit_price)` 단일 진입점 추가. `check_pre_order` 는 (1) `kill_switch_on` 명시 확인 → (2) `run_all_checks()` (환경/일일손실/MDD/연속손실) → (3) BUY + `limit_price` 알려진 경우 주문 금액 한도 순으로 평가. `core/order_executor/executor.py` 의 `OrderExecutor.__init__` 에 `trading_guard` 파라미터 추가, 기본값은 싱글톤 — 관리자 API 의 kill switch 조작이 즉시 모든 executor 에 전파된다. `execute_order` 는 contract 검증 직후 `check_pre_order` 를 호출하고 차단 시 `_map_guard_reason_code` 로 한국어 reason 을 Prometheus 라벨(`kill_switch|daily_loss|max_drawdown|consecutive_losses|order_amount|environment|capital|other`)로 매핑한 뒤 `aqts_trading_guard_blocks_total{reason_code}` 증가 + `logger.critical` + `TradingGuardBlocked` 전파. `TRADING_GUARD_KILL_SWITCH_ACTIVE` Gauge 는 `_activate/deactivate_kill_switch` 에서 0/1 로 갱신되어 외부 알람이 즉시 감지 가능. 통합 테스트 6건 추가 (`tests/test_order_executor_trading_guard.py`): 직접 kill switch / 싱글톤 kill switch 전파 검증 / BUY 주문 금액 한도 차단 / 일일 손실 reason_code 매핑 / 정상 경로는 `_execute_market_order` 에 실제 도달 / `reset_trading_guard` 가 Gauge 도 0 으로 리셋. 핵심: "헬퍼 정의 ≠ 적용" 의 RBAC Wiring Rule 을 TradingGuard 도메인에 그대로 확장 — 종전에는 `TradingGuard` 가 정의만 되어 있었고 `OrderExecutor.execute_order` 경로에서는 호출되지 않아 kill switch 가 형식적 통제에 불과했다.
- [2026-04-08] P0-4: 감사 로그 fail-closed — `db/repositories/audit_log.py` 에 `AuditWriteFailure` 예외 + `log_strict()` 신설. 기존 `log()` 는 읽기/통계 경로 전용 fail-open 으로 한정 (실패 시 `aqts_audit_write_failures_total{mode="soft"}` 증가 + rollback best-effort + 삼킴). `log_strict()` 는 금전적 쓰기 경로 전용 fail-closed (실패 시 `{mode="strict"}` 증가 + `logger.critical` + rollback + `AuditWriteFailure` 재전파). `api/routes/orders.py` 의 `create_order` / `create_batch_orders` 는 **pre-flight** `log_strict("ORDER_REQUESTED" / "BATCH_ORDER_REQUESTED")` 로 감사 DB 생존을 주문 체결 전에 증명 — 실패하면 OrderExecutor 를 호출하지 않고 `release_claim` + 503 `AUDIT_UNAVAILABLE` (`Retry-After: 30`) 반환. 실행 후 `log_strict("ORDER_CREATED" / "BATCH_ORDER_CREATED")` post-audit 를 수행하며, 이 단계 실패 시 브로커는 이미 집행된 상태이므로 `logger.critical` 로 수동 reconcile 경보 + 503. `cancel_order` 도 동일하게 `log_strict("ORDER_CANCELLED")` 로 교체. 핵심 불변식: "감사 레코드 없는 브로커 실행" 이 수학적으로 불가능 — audit DB down → pre-flight 실패 → executor 미호출. 단위 테스트 4건 추가 (`tests/test_audit_log_strict.py`): strict 성공 / execute 실패 시 strict 재전파 + 카운터 증가 / log() fail-open 삼킴 + soft 카운터 / commit 단계 실패도 strict 재전파. 알람 임계: `{mode="strict"}` = 0.
- [2026-04-08] P0-3b: 주문 idempotency DB durability 계층 — alembic 003 `order_idempotency_keys` 테이블 신설 (`UNIQUE (user_id, route, idempotency_key)`, `response_body JSONB`, BRIN(`expires_at`) 청소용 인덱스). `core/idempotency/db_store.py` 에 `PgOrderIdempotencyStore` (SQLAlchemy sync engine, fail-closed: `SQLAlchemyError → IdempotencyStoreUnavailable`, `IntegrityError` 시 기존 fingerprint 와 비교하여 conflict/in_progress/replay 로 분기) + `TwoTierOrderIdempotencyStore` (Redis hot → DB cold 순서로 lookup, warm-up 실패 허용 / try_claim 은 DB 먼저 사전 조회 후 Redis SET NX / store_result 는 **DB 우선 INSERT 후 Redis** 로 durability first 순서, Redis 후단 실패는 swallow). 팩토리에 `two_tier` 백엔드 추가 (`AQTS_ORDER_IDEMPOTENCY_BACKEND=two_tier`). 단위 테스트 24건 추가 (`tests/test_order_idempotency_db_store.py`) — PgStore 장애/충돌/replay 분기 + TwoTier 순서 보장 + Redis warm 실패 시 DB 레코드 반환 검증. Counter 라벨은 `op="db_lookup|db_store_result"` 로 기존 `aqts_order_idempotency_store_failure_total{op}` 를 재사용.
- [2026-04-08] P0-3a: 주문 경로 idempotency key (Redis tier) — `core/idempotency/order_idempotency.py` 신설. `OrderIdempotencyStore` Protocol + `InMemoryOrderIdempotencyStore` / `RedisOrderIdempotencyStore` (`AQTS_ORDER_IDEMPOTENCY_BACKEND=memory|redis`). 두 단계 저장(`try_claim`: `SET NX EX 30s` 마커 → `store_result`: 24h TTL 최종 레코드), 실패 시 `release_claim` 으로 재시도 허용. canonical JSON sha256 fingerprint 로 동일 키 + 다른 body → `IdempotencyConflict` (422), 동시 진행 → `IdempotencyInProgress` (409), Redis 장애 → `IdempotencyStoreUnavailable` (503, fail-closed — 스케줄러 fail-open 과 정책 반대). `api/routes/orders.py` 의 `POST /` 와 `POST /batch` 에 `Idempotency-Key` 헤더 필수 적용, 미첨부는 400 (`IDEMPOTENCY_KEY_REQUIRED`). Counter 4종: `aqts_order_idempotency_hit_total`, `..._in_progress_total`, `..._conflict_total`, `..._store_failure_total{op}`. 단위 테스트 21건 추가 (`tests/test_order_idempotency_store.py`) + 기존 `test_api.py`/`test_coverage_api_routes_v2.py` 주문 테스트에 헤더 전달 경로 반영. DB durability 계층(`order_idempotency_keys` 테이블 + UNIQUE 제약) 은 P0-3b 로 분리.
- [2026-04-08] P0-1: refresh 엔드포인트 토큰 type 강제 검증 — `payload["type"] != "refresh"` 인 경우 401 + `WWW-Authenticate: Bearer error="invalid_token"` 반환, `aqts_token_refresh_from_access_total{reason}` Counter 증가 (reason: `missing_type` | `non_refresh:<type>`). access token 으로 refresh 발급 경로 차단. 통합 테스트 5건 추가 (`tests/test_refresh_token_type.py`).
- [2026-04-08] P0-2b: rate limiter Redis storage + 복합 키 + fail-closed — `api/middleware/rate_limiter.py` 의 `storage_uri` 를 운영 시 `settings.redis.url`, 테스트 시 `memory://` 로 분기. `key_func` 을 `composite_rate_key` 로 교체 (인증 토큰 있으면 `user:<sub>`, 없으면 `ip:<addr>`) — NAT 공유 사용자 격리 + 무인증 무차별 공격 차단. `swallow_errors=False`, `in_memory_fallback_enabled=False` 명시 적용으로 storage 장애 시 limits `StorageError` 전파, 신규 핸들러 `rate_limit_storage_unavailable_handler` 가 503 + `RATE_LIMIT_STORE_UNAVAILABLE` 반환 (fail-closed). Counter 추가: `aqts_rate_limit_exceeded_total{route}`, `aqts_rate_limit_storage_failure_total`. 단위 테스트 13건 추가 (`tests/test_rate_limiter_redis.py`).
- [2026-04-08] P0-2a: TokenRevocationStore Redis 백엔드 + fail-closed — `api/middleware/token_revocation.py` 신설. `InMemoryTokenRevocationStore` (테스트/개발) / `RedisTokenRevocationStore` (운영, sync `redis.Redis` 클라이언트, TTL = 토큰 잔여 수명) 분리, `AQTS_REVOCATION_BACKEND=redis|memory` 환경변수로 선택. Redis 장애 시 `RevocationBackendUnavailable` 전파 → `verify_token` 이 401 대신 503 반환 (fail-closed), `aqts_revocation_backend_failure_total{op}` Counter 증가. 단위/통합 테스트 14건 추가 (`tests/test_token_revocation_store.py`). P0-2 의 rate limiter Redis 마이그레이션은 별도 커밋 (P0-2b) 으로 분리.

- [2026-04-09] P1-정합성: PortfolioLedger DB 영속화 (§7.3 후속) — 직전 커밋의 in-memory `PortfolioLedger` 는 프로세스 재시작 시 모든 잔량을 잃어 ReconciliationRunner 가 부팅 직후 mismatch 로 kill switch 를 트리거하는 회귀 경로였다. 본 커밋은 그 경로를 닫는다. **(1) 스키마** — Alembic `005_portfolio_positions` 추가. `portfolio_positions(ticker PK, quantity FLOAT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())` + `CHECK (quantity > 0)` 제약으로 0/음수 잔량은 row 가 아예 존재할 수 없게 강제 (long-only 정합성 가정의 DB-side 보강). ORM 모델 `db/models/portfolio_position.py` 신설, `db/models/__init__.py` 에 등록하여 `Base.metadata` 자동 인지. **(2) Repository 계층** — `core/portfolio_ledger.py` 에 `LedgerRepository` Protocol(`@runtime_checkable`) 추가, `load_all()`/`apply_delta(ticker, delta) -> new_qty` 두 메서드만 노출 (의존성 역전, 테스트는 fake repo 로 대체 가능). 구현체 `db/repositories/portfolio_positions.py::SqlPortfolioLedgerRepository` 는 `async_session_factory` 를 받아 `session.begin()` 트랜잭션 내에서 `SELECT ... FOR UPDATE` 로 row-level lock → 새 잔량 계산 → 결과가 음수면 `LedgerInvariantError` raise → 0 이면 DELETE, 그 외는 INSERT/UPDATE 분기. text() 파라미터 바인딩으로 SQL injection 차단 (`audit_log.py` 패턴 준수). **(3) PortfolioLedger 리팩터** — `__init__(repository=None)` 으로 repository 옵셔널화. `record_fill` 의 lock 내부에서 repository 가 있으면 DB 에 위임하고 **commit 성공 후에만** cache 를 갱신 (cache-after-commit 불변식: DB 가 raise 하면 cache 는 unchanged). repository 가 없으면 in-memory 만 (테스트/백테스트/dry-run 호환). 신규 메서드 `hydrate()` 는 부팅 시 1회 DB → cache 단방향 로드, repository 가 없으면 no-op. `configure_portfolio_ledger(repository)` 헬퍼로 싱글톤 교체. **(4) Bootstrap wiring** — `main.py` lifespan 과 `scheduler_main.py` 둘 다 PostgreSQL 준비 직후 `configure_portfolio_ledger(SqlPortfolioLedgerRepository(async_session_factory))` → `await ledger.hydrate()` 호출. 두 진입점 모두 동일 패턴이며 wiring 누락은 정적 검사 + 통합 테스트 양쪽으로 강제. `main.py` 는 모듈 top-level import 로 끌어올려 `patch("main.SqlPortfolioLedgerRepository")` 가 가능하게 함. **(5) 테스트 추가 (20건)** — `tests/test_portfolio_ledger_persistence.py` 16건: `LedgerRepository` Protocol satisfaction, hydrate(load/필터/no-op/idempotent), record_fill 위임 (BUY/SELL-zero-DELETE/부분 SELL/short rejection/repository 실패 시 cache 보존), `configure_portfolio_ledger` 싱글톤 교체, `SqlPortfolioLedgerRepository` 의 SELECT FOR UPDATE → INSERT/UPDATE/DELETE 분기와 short rejection 시 mutation 미발생을 mocked AsyncSession 으로 검증. `tests/test_portfolio_ledger_wiring.py` 4건: `main.py`/`scheduler_main.py` 에 대해 AST 파싱 후 `configure_portfolio_ledger` 와 `hydrate` 호출 존재를 강제 (Wiring Rule 정적 검사 — 정의 ≠ 적용). **(6) 테스트 격리 보강** — `tests/test_alert_manager_persistence.py::test_main_startup_injects_alerts_collection_into_singleton` 가 lifespan 을 실제로 돌리면서 SQL repo 싱글톤이 다음 테스트에 누설되어 `record_fill` 계열이 실 DB 에 도달하던 회귀를 발견. 해당 테스트에 `patch("main.SqlPortfolioLedgerRepository", return_value=fake_repo)` + `fake_repo.load_all = AsyncMock(return_value={})` 주입, finally 에 `reset_portfolio_ledger()` 추가. 안전망으로 `tests/conftest.py` 에 autouse `_reset_portfolio_ledger_singleton` fixture 등록. **게이트** — ruff/black/RBAC/bool-literals/doc-sync 전부 0 errors + 0 warnings, `pytest tests/ -q` 3646 passed (직전 3625 → +20 신규 + 1 기존 patch).

- [2026-04-09] CD-fix: 배포 파이프라인 alembic 마이그레이션 자동화 — 직전 PortfolioLedger DB 영속화 커밋(`4bbf062`) 배포 시 운영 DB 에 `portfolio_positions` 테이블이 없어 `hydrate()` 가 `UndefinedTableError` 로 실패, backend lifespan 이 종료되어 health check 가 120s 후 실패 (`runs/24164471315`). 근본 원인은 `cd.yml` 에 alembic 마이그레이션 단계가 부재한 것 — 기존 001~004 마이그레이션은 운영자가 수동 적용해 왔으나 매 배포마다 수동 개입을 강제하는 것은 현실적이지 않고, 새 스키마가 추가될 때마다 동일한 회귀가 재발한다. **수정**: `.github/workflows/cd.yml` 의 deploy job 에 Step 5a/5b/5c 를 추가했다. (5a) `docker compose up -d postgres` 로 DB 만 먼저 기동, (5b) `docker compose run --rm backend alembic -c alembic.ini upgrade head` 로 새 이미지의 마이그레이션 컨테이너를 일회성 실행, (5c) 나머지 서비스 `up -d`. 마이그레이션이 실패하면 backend 컨테이너는 기동하지 않으므로 fail-closed 가 보장된다 (DB 가 잘못된 상태로 코드만 새 버전이 올라가는 경로 차단). rollback 경로는 의도적으로 손대지 않는다 — 마이그레이션은 forward-compatible (additive) 가정 하에 작성되며, 이전 코드 버전은 새 스키마와 호환된다. 회고: 본 회귀는 "수동 절차에 의존하는 통제는 형식적 통제" 라는 §11 (RBAC 회고)·공급망 보안 §10 의 동일 원칙이 마이그레이션 도메인에 그대로 적용된 사례다. 정의(`alembic upgrade head` 가능) ≠ 적용(CD 가 매 배포마다 실행) 은 인증/인가, SBOM/서명, 정합성 wiring 에 이어 네 번째 회귀 패턴이며, 본 수정으로 매 배포마다 자동 실행되는 단일 경로로 통합되었다.

- [2026-04-09] CD-fix2: alembic baseline 자동 감지 (init_db.sql 호환) — 직전 cd-fix(`628f307`) 의 5b 단계가 운영 DB 에서 `relation "market_ohlcv" already exists` 로 실패 (`runs/24165328003`). 원인은 운영 DB 가 init_db.sql 로 부트스트랩된 채 `alembic_version` 이 비어 있는 상태였고, `alembic upgrade head` 가 001 부터 새로 실행되며 이미 존재하는 테이블을 다시 CREATE 하려 했기 때문. 001 마이그레이션 docstring 에 "init_db.sql 로 생성된 DB 는 `alembic stamp head` 로 마킹만 한다" 라는 사용 지침이 있었으나 CD 경로에는 그 단계가 한 번도 코드화된 적이 없었다 — 또 한 번의 "정의 ≠ 적용" 회귀. **수정**: cd.yml 5b 단계에 baseline 자동 감지 로직을 추가했다. (1) `alembic_version` 테이블 존재 여부를 `psql -tAc` 로 확인. (2) 부재 시, 핵심 스키마(`market_ohlcv`/`users`/`order_idempotency_keys`/`users.role_version`/`portfolio_positions`) 의 존재를 information_schema 단일 쿼리로 관찰. (3) 매트릭스 매핑(portfolio_positions→005, role_version→004, idempotency_keys→003, users→002, market_ohlcv→001) 으로 가장 높은 일치 리비전을 결정하여 `alembic stamp <BASELINE>` 실행. (4) `market_ohlcv` 조차 없는 빈 DB 는 운영 환경에서 발생 불가하므로 fail-closed 로 중단. (5) baseline 결정 후 `alembic upgrade head` 로 잔여 마이그레이션 적용 (5c). 매트릭스는 002~005 의 마이그레이션 파일을 직접 grep 으로 검증한 뒤 코드화했다 (추론 ≠ 확정). **운영 1차 복구** (서버에서 수동 실행, 본 커밋 이전) — `cosign verify` + pull 후 `docker run --rm --network aqts_aqts-network --env-file ~/aqts/.env <new_image> bash -c 'alembic stamp 001 && alembic upgrade head'` 로 005 까지 일괄 적용. 검증: `alembic_version=005`, `ck_portfolio_positions_quantity_positive | CHECK ((quantity > (0)::double precision))` 모두 정상. 본 수동 절차는 미래 환경(staging, DR, 신규 region) 에서 동일 회귀가 재발하지 않도록 본 커밋의 5b 자동 감지 로직으로 단일 경로 통합되었다. **별도 후속 항목** — 마이그레이션 002 가 `ADMIN_BOOTSTRAP_PASSWORD not set. Admin user will NOT be created.` 경고를 출력함. 운영 DB 에 admin 계정이 없어 RBAC admin 라우트가 호출 불가 상태이므로, prod secret 주입 또는 일회성 admin 생성 + 즉시 회전 절차를 별도 커밋으로 결정·적용해야 함.

- [2026-04-09] P1-보안: admin 사용자 일회성 부트스트랩 CLI + 운영 절차 문서 — 직전 alembic baseline 복구 시 마이그레이션 002 가 `ADMIN_BOOTSTRAP_PASSWORD not set. Admin user will NOT be created.` 경고를 출력했다. 002 의 자동 시드 경로는 마이그레이션이 처음 적용되는 시점에만 동작하는데, 본 환경처럼 `alembic stamp 001` 후 `upgrade head` 로 002 가 적용된 경우에도 시드 함수는 실행되지만 `ADMIN_BOOTSTRAP_PASSWORD` 가 비어있어 admin 이 생성되지 않은 채로 통과됐다. 결과적으로 운영 DB 에 admin 계정이 한 명도 없는 상태였고, 모든 admin 전용 RBAC 라우트(사용자 생성, 역할 변경, MFA 강제 등) 가 사실상 호출 불가 상태였다. **운영 정책 결정**: CD 파이프라인에 비밀번호를 흘리지 않는 옵션 (2) 채택. CD secret 으로 매 배포마다 비밀번호가 흐르는 옵션 (1) 은 환경 lifetime 당 1회만 필요한 부트스트랩에 비해 노출 면적이 과도하고 본 프로젝트의 cosign keyless / fail-closed 성향과도 일관되지 않는다. **신규 산출물**: (1) `backend/scripts/create_admin.py` 일회성 CLI — 환경변수 검증(최소 12자 + 영문/숫자/특수문자 중 2종 이상), `roles` 테이블에서 admin 역할 id 를 동적 조회(하드코딩 금지 — 마이그레이션 INSERT 순서가 바뀌어도 안전), 멱등성 보장(admin 역할 사용자가 1명 이상이면 변경 없이 종료 코드 0), username 중복 차단, `AuthService.hash_password` 로 002 와 동일한 bcrypt 경로 사용, `async_session_factory` + `session.begin()` 트랜잭션. (2) `backend/tests/test_create_admin.py` 21건 — `validate_password`/`read_env`/`find_admin_role_id`/`admin_already_exists`/`create_admin` 의 모든 분기를 mocked AsyncSession 으로 검증 (DB 의존성 없음). 멱등성, role id 동적 조회, username 충돌 거부, 정상 생성 시 hash_password 호출/role_id 매핑까지 모두 검증. (3) `docs/security/admin-bootstrap.md` 운영 절차 문서 — 배경, 운영 정책(CD 비주입/일회성/멱등/즉시 회전), 비밀번호 정책, 사전 조건, 5단계 실행 절차(네트워크/이미지 식별 → `read -s` 일회성 비밀번호 입력 → `docker run --rm` CLI 실행 → 환경변수 즉시 unset + history 삭제 → 비밀번호 회전), 검증 쿼리, 종료 코드 매핑, 보안 주의사항. **재실행 조건**: 환경 lifetime 당 1회. 평상시 배포·재시작·CD 재실행과 무관하며, 신규 region/DR 복제 후 첫 기동/admin 전원 삭제 사고 시에만 재실행한다. **본 회귀의 패턴**: 마이그레이션 자동 시드라는 "정의" 가 실제 실행 시점 환경변수 부재로 "적용" 되지 않은 사례 — 정의 ≠ 적용 패턴이 운영 부트스트랩 도메인으로 한 번 더 확장됐다. CD 5b 자동 baseline stamp(`85e8ade`) 와 본 admin 부트스트랩 CLI 가 결합되어, 신규 환경에서도 alembic 적용 → admin 생성 두 단계가 모두 명시적·관찰 가능한 단일 경로로 통합되었다.

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

---

## 11. 상위 전략 문서 — production-grade 로드맵

본 문서의 §7.3 정합성/보안 강화는 현재 시스템(retail REST API 기반)을 운영
가능한 수준으로 끌어올리는 데 초점이 있다. 자본 규모 확장, 외부 자금 유치,
다중 시장 진출 등으로 production-grade 인프라가 요구되는 시점에 보강해야 할
항목(이벤트 소싱, FIX 전환, pre-trade risk gateway 마이크로서비스, WORM 감사
저장소, DR/멀티리전, secret rotation 등)은 별도 문서로 분리되어 있다:

- [`docs/architecture/production-grade-roadmap.md`](../architecture/production-grade-roadmap.md)

§7.3 의 P0/P1 항목을 마무리한 뒤 위 문서의 우선순위 매트릭스를 기준으로 다음
단계를 선정한다.
