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

- [2026-04-09] P1-보안: `get_current_user` DB 재확인 — `api/middleware/auth.py` 의 `get_current_user` 가 JWT 서명/만료/revocation 통과 후에도 `db_session: Optional[AsyncSession] = Depends(get_db_session)` 을 주입받아 `select(User).where(User.id == uid)` 로 실제 사용자 레코드를 재확인하도록 강화. (1) 사용자 미존재 → 401 `user no longer exists`, (2) `is_active=False` → 401 `inactive`, (3) `is_locked=True` → 403 `locked`, (4) DB `role.name != token_role` → 401 `role has changed; please re-authenticate`, (5) DB 장애 (execute 예외) → 503 `User store unavailable` + `Retry-After: 5` (token revocation 의 fail-closed 정책과 동일), (6) `user.role` 관계 누락 → 401 `role missing`. 반환값은 토큰이 아니라 **DB 의 현재 username/role** 로 구성되어 운영자가 사용자 역할을 강등해도 기존 토큰의 인가 경로가 즉시 끊긴다 — 이전 구조는 JWT role 클레임만 믿었기 때문에 role 변경이 토큰 만료까지 반영되지 않는 silent window 가 존재했다. 단위 테스트 7건 추가 (`tests/test_get_current_user_db_recheck.py`): 사용자 부재 / inactive / locked / role 불일치 / DB 장애 503 / role=None / 정상 경로가 DB role 반환. 기존 `tests/test_rbac.py::TestGetCurrentUser` 3건은 `db_session` fixture 를 명시적으로 주입하도록 수정, `tests/test_rbac.py::test_me_endpoint_requires_authentication` 의 uid 를 fixture 에 존재하는 `test-viewer-uuid` 로 정정, `tests/test_auth_401_verification.py::test_valid_token_returns_200` 을 `authenticated_app` fixture (mock DB session override) 로 전환. `tests/conftest.py` 의 `db_session` / `authenticated_app` 목 execute 를 `query.compile(literal_binds=True)` 로 컴파일한 뒤 쿼리 문자열에서 `User.id` UUID / username 으로 매칭하도록 업그레이드 — `get_current_user` 가 `username` 대신 `id` 로 조회하는 신규 쿼리와도 호환. RBAC Wiring Rule 의 "authn ≠ authz" 구분은 *라우트 게이트* 에 적용됐지만, 이번 작업은 그 아래 계층인 *토큰 → 사용자 상태 일관성* 을 강제한다. role_version 필드는 향후 schema migration 과 함께 2차 강화 (현재는 role.name 등식이 대체 지표).
- [2026-04-09] P1-정합성: ReconciliationEngine → TradingScheduler wiring (Wiring Rule, 정합성 도메인 확장) — 종전에는 `core/reconciliation.py` 의 `ReconciliationEngine` 이 정의만 되어 있고 어떤 스케줄러 핸들러에서도 호출되지 않아 형식적 통제였다. P0-5 의 "정의 ≠ 적용" 패턴을 정합성 축에 그대로 확장. `core/reconciliation_runner.py` 신설: `PositionProvider` Protocol + `ReconciliationRunner(engine, broker_provider, internal_provider, guard, mismatch_threshold)` 단일 진입점 — provider 호출 → `ReconciliationEngine.reconcile` → metric 갱신 → mismatch 가 임계 초과 시 `TradingGuard.activate_kill_switch` (싱글톤 기본). guard 미주입 시 `__post_init__` 에서 `get_trading_guard()` 자동 주입하므로 P0-5 의 OrderExecutor 차단 경로와 자동 결합. provider 장애는 `result="error"` 카운터 증가 후 fail-closed 재전파. `core/trading_scheduler.py` 의 `TradingScheduler.register_reconciliation_runner()` 추가, `_default_handle_midday_check` / `_default_handle_post_market` 가 `_run_reconciliation_if_wired()` 헬퍼를 호출하여 등록된 runner 가 있을 때만 reconcile 사이클 실행 (미등록 시 `wired=False, skipped=True` 로 종전 동작 유지). `core/monitoring/metrics.py` 에 3종 추가: `aqts_reconciliation_runs_total{result∈matched|mismatch|error}`, `aqts_reconciliation_mismatches_total` (cumulative), `aqts_reconciliation_ledger_diff_abs` Gauge (알람 임계 0). 통합 테스트 9건 추가 (`tests/test_reconciliation_runner.py`): matched 정상 / 임계 초과 mismatch 시 kill switch 활성화 + Counter / 임계 미달 mismatch 는 활성화 안 함 / provider 실패 → error 카운터 + 재전파 / 싱글톤 guard 자동 주입 검증 / TradingScheduler MIDDAY_CHECK 가 실제 runner 호출 / POST_MARKET 핸들러가 mismatch 시 kill switch 활성화 / runner 미주입 시 stub 결과 / 음수 임계 거부. 핵심 불변식: ledger 불일치가 발견되면 → kill switch 활성화 → P0-5 wiring 으로 OrderExecutor 가 모든 후속 주문 차단.
- [2026-04-08] P0-5: TradingGuard → OrderExecutor wiring (Wiring Rule 적용) — `core/trading_guard.py` 에 프로세스 전역 싱글톤 `get_trading_guard()` + `reset_trading_guard()` (테스트 전용) + `TradingGuardBlocked` 예외 + `check_pre_order(ticker, side, quantity, limit_price)` 단일 진입점 추가. `check_pre_order` 는 (1) `kill_switch_on` 명시 확인 → (2) `run_all_checks()` (환경/일일손실/MDD/연속손실) → (3) BUY + `limit_price` 알려진 경우 주문 금액 한도 순으로 평가. `core/order_executor/executor.py` 의 `OrderExecutor.__init__` 에 `trading_guard` 파라미터 추가, 기본값은 싱글톤 — 관리자 API 의 kill switch 조작이 즉시 모든 executor 에 전파된다. `execute_order` 는 contract 검증 직후 `check_pre_order` 를 호출하고 차단 시 `_map_guard_reason_code` 로 한국어 reason 을 Prometheus 라벨(`kill_switch|daily_loss|max_drawdown|consecutive_losses|order_amount|environment|capital|other`)로 매핑한 뒤 `aqts_trading_guard_blocks_total{reason_code}` 증가 + `logger.critical` + `TradingGuardBlocked` 전파. `TRADING_GUARD_KILL_SWITCH_ACTIVE` Gauge 는 `_activate/deactivate_kill_switch` 에서 0/1 로 갱신되어 외부 알람이 즉시 감지 가능. 통합 테스트 6건 추가 (`tests/test_order_executor_trading_guard.py`): 직접 kill switch / 싱글톤 kill switch 전파 검증 / BUY 주문 금액 한도 차단 / 일일 손실 reason_code 매핑 / 정상 경로는 `_execute_market_order` 에 실제 도달 / `reset_trading_guard` 가 Gauge 도 0 으로 리셋. 핵심: "헬퍼 정의 ≠ 적용" 의 RBAC Wiring Rule 을 TradingGuard 도메인에 그대로 확장 — 종전에는 `TradingGuard` 가 정의만 되어 있었고 `OrderExecutor.execute_order` 경로에서는 호출되지 않아 kill switch 가 형식적 통제에 불과했다.
- [2026-04-08] P0-4: 감사 로그 fail-closed — `db/repositories/audit_log.py` 에 `AuditWriteFailure` 예외 + `log_strict()` 신설. 기존 `log()` 는 읽기/통계 경로 전용 fail-open 으로 한정 (실패 시 `aqts_audit_write_failures_total{mode="soft"}` 증가 + rollback best-effort + 삼킴). `log_strict()` 는 금전적 쓰기 경로 전용 fail-closed (실패 시 `{mode="strict"}` 증가 + `logger.critical` + rollback + `AuditWriteFailure` 재전파). `api/routes/orders.py` 의 `create_order` / `create_batch_orders` 는 **pre-flight** `log_strict("ORDER_REQUESTED" / "BATCH_ORDER_REQUESTED")` 로 감사 DB 생존을 주문 체결 전에 증명 — 실패하면 OrderExecutor 를 호출하지 않고 `release_claim` + 503 `AUDIT_UNAVAILABLE` (`Retry-After: 30`) 반환. 실행 후 `log_strict("ORDER_CREATED" / "BATCH_ORDER_CREATED")` post-audit 를 수행하며, 이 단계 실패 시 브로커는 이미 집행된 상태이므로 `logger.critical` 로 수동 reconcile 경보 + 503. `cancel_order` 도 동일하게 `log_strict("ORDER_CANCELLED")` 로 교체. 핵심 불변식: "감사 레코드 없는 브로커 실행" 이 수학적으로 불가능 — audit DB down → pre-flight 실패 → executor 미호출. 단위 테스트 4건 추가 (`tests/test_audit_log_strict.py`): strict 성공 / execute 실패 시 strict 재전파 + 카운터 증가 / log() fail-open 삼킴 + soft 카운터 / commit 단계 실패도 strict 재전파. 알람 임계: `{mode="strict"}` = 0.
- [2026-04-08] P0-3b: 주문 idempotency DB durability 계층 — alembic 003 `order_idempotency_keys` 테이블 신설 (`UNIQUE (user_id, route, idempotency_key)`, `response_body JSONB`, BRIN(`expires_at`) 청소용 인덱스). `core/idempotency/db_store.py` 에 `PgOrderIdempotencyStore` (SQLAlchemy sync engine, fail-closed: `SQLAlchemyError → IdempotencyStoreUnavailable`, `IntegrityError` 시 기존 fingerprint 와 비교하여 conflict/in_progress/replay 로 분기) + `TwoTierOrderIdempotencyStore` (Redis hot → DB cold 순서로 lookup, warm-up 실패 허용 / try_claim 은 DB 먼저 사전 조회 후 Redis SET NX / store_result 는 **DB 우선 INSERT 후 Redis** 로 durability first 순서, Redis 후단 실패는 swallow). 팩토리에 `two_tier` 백엔드 추가 (`AQTS_ORDER_IDEMPOTENCY_BACKEND=two_tier`). 단위 테스트 24건 추가 (`tests/test_order_idempotency_db_store.py`) — PgStore 장애/충돌/replay 분기 + TwoTier 순서 보장 + Redis warm 실패 시 DB 레코드 반환 검증. Counter 라벨은 `op="db_lookup|db_store_result"` 로 기존 `aqts_order_idempotency_store_failure_total{op}` 를 재사용.
- [2026-04-08] P0-3a: 주문 경로 idempotency key (Redis tier) — `core/idempotency/order_idempotency.py` 신설. `OrderIdempotencyStore` Protocol + `InMemoryOrderIdempotencyStore` / `RedisOrderIdempotencyStore` (`AQTS_ORDER_IDEMPOTENCY_BACKEND=memory|redis`). 두 단계 저장(`try_claim`: `SET NX EX 30s` 마커 → `store_result`: 24h TTL 최종 레코드), 실패 시 `release_claim` 으로 재시도 허용. canonical JSON sha256 fingerprint 로 동일 키 + 다른 body → `IdempotencyConflict` (422), 동시 진행 → `IdempotencyInProgress` (409), Redis 장애 → `IdempotencyStoreUnavailable` (503, fail-closed — 스케줄러 fail-open 과 정책 반대). `api/routes/orders.py` 의 `POST /` 와 `POST /batch` 에 `Idempotency-Key` 헤더 필수 적용, 미첨부는 400 (`IDEMPOTENCY_KEY_REQUIRED`). Counter 4종: `aqts_order_idempotency_hit_total`, `..._in_progress_total`, `..._conflict_total`, `..._store_failure_total{op}`. 단위 테스트 21건 추가 (`tests/test_order_idempotency_store.py`) + 기존 `test_api.py`/`test_coverage_api_routes_v2.py` 주문 테스트에 헤더 전달 경로 반영. DB durability 계층(`order_idempotency_keys` 테이블 + UNIQUE 제약) 은 P0-3b 로 분리.
- [2026-04-08] P0-1: refresh 엔드포인트 토큰 type 강제 검증 — `payload["type"] != "refresh"` 인 경우 401 + `WWW-Authenticate: Bearer error="invalid_token"` 반환, `aqts_token_refresh_from_access_total{reason}` Counter 증가 (reason: `missing_type` | `non_refresh:<type>`). access token 으로 refresh 발급 경로 차단. 통합 테스트 5건 추가 (`tests/test_refresh_token_type.py`).
- [2026-04-08] P0-2b: rate limiter Redis storage + 복합 키 + fail-closed — `api/middleware/rate_limiter.py` 의 `storage_uri` 를 운영 시 `settings.redis.url`, 테스트 시 `memory://` 로 분기. `key_func` 을 `composite_rate_key` 로 교체 (인증 토큰 있으면 `user:<sub>`, 없으면 `ip:<addr>`) — NAT 공유 사용자 격리 + 무인증 무차별 공격 차단. `swallow_errors=False`, `in_memory_fallback_enabled=False` 명시 적용으로 storage 장애 시 limits `StorageError` 전파, 신규 핸들러 `rate_limit_storage_unavailable_handler` 가 503 + `RATE_LIMIT_STORE_UNAVAILABLE` 반환 (fail-closed). Counter 추가: `aqts_rate_limit_exceeded_total{route}`, `aqts_rate_limit_storage_failure_total`. 단위 테스트 13건 추가 (`tests/test_rate_limiter_redis.py`).
- [2026-04-08] P0-2a: TokenRevocationStore Redis 백엔드 + fail-closed — `api/middleware/token_revocation.py` 신설. `InMemoryTokenRevocationStore` (테스트/개발) / `RedisTokenRevocationStore` (운영, sync `redis.Redis` 클라이언트, TTL = 토큰 잔여 수명) 분리, `AQTS_REVOCATION_BACKEND=redis|memory` 환경변수로 선택. Redis 장애 시 `RevocationBackendUnavailable` 전파 → `verify_token` 이 401 대신 503 반환 (fail-closed), `aqts_revocation_backend_failure_total{op}` Counter 증가. 단위/통합 테스트 14건 추가 (`tests/test_token_revocation_store.py`). P0-2 의 rate limiter Redis 마이그레이션은 별도 커밋 (P0-2b) 으로 분리.

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
