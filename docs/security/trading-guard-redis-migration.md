# TradingGuard Redis-backed State 이행 설계 (P0)

**문서 번호**: SEC-007
**버전**: 0.3 (DRAFT — 커밋별 Task Breakdown + 관측/설정/롤백/검사기/게이트 섹션 추가)
**최종 수정**: 2026-04-15
**승인 대기**: 운영책임자 + 아키텍처 검토
**상위 문서**: `docs/security/security-integrity-roadmap.md` §9 (2026-04-15 P0 승격 엔트리), `docs/operations/phase1-demo-verification-2026-04-11.md` §10.18

## 0. 개정 이력

| 버전 | 일자 | 주요 변경 |
|------|------|-----------|
| 0.1 | 2026-04-15 | 초안. Redis 이전 범위, pub/sub 유실 정책, 내일 집행 분기에 대한 미해결 질문 3건. |
| 0.2 | 2026-04-15 | "전체 서비스 완성도 우선" 원칙으로 §10 3건 해소. (1) 이전 범위를 `TradingGuardState` 10개 필드 전체로 확장. (2) pub/sub 에 monotonic seq 도입, gap-detect 즉시 reconcile + 1초 주기 reconcile. (3) 내일 11:30 Path A 무조건 취소, 시간 데드라인 제거. §4·§5·§8·§10 갱신. |
| 0.3 | 2026-04-15 | 실행 단계 준비. §8 을 커밋별 Task Breakdown(6 요소 × 5 커밋)으로 확장. §11 관측(Prometheus 지표·로그 필드·대시보드 패널), §12 설정(env var·settings·`.env.example`), §13 롤백 계획(rehearsal 포함), §14 정적 검사기 영향, §15 커밋별 게이트 체크리스트 신설. 기존 §11(관련 문서)은 §16 으로 밀림. Commit C 의 scheduler wiring 지점을 `scheduler_main.py:main()` 으로 명시. |

## 1. 배경

§10.18 에서 확인된 P0 결함:

- `backend/core/trading_guard.py:407` 의 `_guard_instance: Optional[TradingGuard]` 는 **프로세스 전역 in-memory 싱글톤**.
- 현재 배포는 `backend` (`SCHEDULER_ENABLED=false`, HTTP API) 와 `scheduler` (`SCHEDULER_ENABLED=true`, `ReconciliationRunner`/`handle_midday_check`) 를 **별도 컨테이너 = 별도 프로세스**로 기동.
- 결과: scheduler 가 `_activate_kill_switch` 로 on 전이해도 backend `/api/system/kill-switch/status` 는 off 반환. `/kill-switch/deactivate` 는 backend 자기 프로세스의 null 상태만 해제.

정의된 HTTP 계약이 실제 공유 상태에 도달하지 못하는 silent miss. **"정의 ≠ 적용"** 원칙의 상태 공유 도메인 재발.

## 2. 목표

- `TradingGuard` 의 kill switch 관련 상태를 **교차프로세스 단일 진실원천**으로 이전.
- 기존 HTTP 계약(`/kill-switch/status`, `/kill-switch/deactivate`) 의 의미가 실제 공유 상태에 일치하도록 재구성.
- 자동 활성화(`_activate_kill_switch`) 가 어느 프로세스에서 발생하더라도 **모든 프로세스가 즉시 인지**.
- 장애 모드: fail-closed (Redis 불가 시 기존 주문 차단 정책과 일치).
- 기존 단위 테스트 9건(`test_kill_switch_routes.py`) 은 회귀 방지용으로 유지하되, 교차프로세스 통합 테스트를 신설.

## 3. 비목표

- 기존 `OrderExecutor` · `ReconciliationRunner` · `handle_midday_check` · HTTP 라우트 API 변경 없음. 내부 구현만 교체.
- Redis HA/클러스터 도입은 범위 밖. 단일 Redis 인스턴스(`aqts-redis`) 가 단일 실패점인 것은 기존 P0-2/P0-3 과 동일하게 수용.
- **비고**: 과거 v0.1 에서 "kill switch 계열 2개 필드만 이전" 을 비목표로 제시했으나, v0.2 에서 이 제한을 제거했다. 근거는 §4.1 "전체 범위 선택 근거" 참조 — 부분 이전이 비대칭 구조를 고착시키고 `daily_realized_pnl` 의 프로세스 재기동 리셋으로 인한 서킷브레이커 우회 경로를 잠복시킨다.

## 4. 상태 모델

### 4.1 전체 범위 선택 근거

`TradingGuardState` 의 10개 필드를 모두 Redis 로 이전한다. 부분 이전(kill switch 2개만) 을 기각한 이유:

1. **서킷브레이커 우회 경로 차단**: `daily_realized_pnl` 이 프로세스 로컬이면 scheduler 재기동(배포/OOM/panic 복구) 시 0 으로 리셋된다. `-3%` 누적 후 재기동이 일어나면 `check_daily_loss_limit` 가 정상 pass 로 판정하여 일일 손실 한도 서킷브레이커가 **일상 운영 이벤트만으로 우회**된다. §3.6.5 P0-5 fail-closed 정책과 정면 충돌.
2. **아키텍처 비대칭 제거**: "kill switch 는 공유, 발동 조건 카운터는 로컬" 구조는 6개월 뒤 carveout 메울 때 두 경로에 의존하는 코드가 생겨 마이그레이션이 두 배 비싸진다. 지금 한 번에 이전하면 `check_*` 메서드 전체가 균일한 상태 소스를 갖는다.
3. **원자성 설계 강제**: `HINCRBYFLOAT` / `HINCRBY` / Lua script 로 원자적 업데이트를 지금 도입하면, 다중 프로세스가 `record_trade` 호출하는 미래 확장(예: 멀티 전략 병렬)에서 재설계가 불필요하다.

### 4.2 공유 상태 (Redis)

단일 hash 로 통합 관리한다. 키 다수로 쪼개면 원자성 경계가 흐려진다.

| 키 | 타입 | 필드 | 타입 | 의미 |
|----|------|------|------|------|
| `aqts:trading_guard:state` | Hash | `is_active` | `"1"`/`"0"` | guard 활성 여부 |
| | | `kill_switch_on` | `"1"`/`"0"` | kill switch bool |
| | | `kill_switch_reason` | str | 활성화 사유 |
| | | `activated_at` | ISO-8601 KST | 최근 활성화 시각 |
| | | `activated_by` | str | `"auto:daily_loss"` / `"auto:mdd"` / `"auto:consecutive_loss"` / `"auto:midday_mismatch"` / `"manual:<username>"` |
| | | `previous_reason` | str | 직전 사유 (해제 API 응답용) |
| | | `daily_realized_pnl` | float (str) | 당일 실현 손익 |
| | | `daily_order_count` | int (str) | 당일 주문 건수 |
| | | `consecutive_losses` | int (str) | 연속 손실 횟수 |
| | | `current_drawdown` | float (str) | 현재 낙폭 |
| | | `peak_portfolio_value` | float (str) | 고점 포트폴리오 가치 |
| | | `current_portfolio_value` | float (str) | 현재 포트폴리오 가치 |
| | | `last_updated` | ISO-8601 KST | 최근 state 갱신 시각 |
| `aqts:trading_guard:seq` | String | - | int64 (monotonic) | 상태 변경 순번. 모든 mutation 은 `INCR` 후 그 값을 hash 의 `seq` 필드에도 기록 |

**TTL**:
- hash `aqts:trading_guard:state`: 없음(영구). kill switch 는 수동 해제 / 일일 리셋에 의해서만 off 전이.
- seq counter: 없음(영구). 프로세스 재기동과 무관하게 단조 증가.

**원자성**:
- 단순 set: `HSET state f1 v1 f2 v2 ...` (다중 필드 단일 명령)
- 카운터 증감: `HINCRBYFLOAT state daily_realized_pnl <pnl>` / `HINCRBY state daily_order_count 1`
- 연속 손실 갱신: 값 조건부 변경 → Lua script (`if pnl<0 then HINCRBY ... else HSET ... 0 end`)
- seq 와 hash 필드를 함께 원자적으로 갱신하기 위해 **Lua script 필수**. 샘플:
  ```lua
  -- KEYS[1]=state, KEYS[2]=seq, ARGV=[field, value, field, value, ...]
  local s = redis.call('INCR', KEYS[2])
  for i=1,#ARGV,2 do
    redis.call('HSET', KEYS[1], ARGV[i], ARGV[i+1])
  end
  redis.call('HSET', KEYS[1], 'seq', s)
  return s
  ```
  모든 mutation 은 이 script 를 경유. 반환된 seq 는 publish 페이로드에 동봉.

### 4.3 Pub/Sub 채널 (Gap-detect 강화)

| 채널 | 페이로드 | 발행자 |
|------|----------|--------|
| `aqts:trading_guard:state_change` | JSON `{"seq": int, "event": "activate"\|"deactivate"\|"counter_update"\|"reset_daily", "summary": {...}}` | 모든 mutation 경로 (Lua script 실행 직후) |

구독자 규약:
1. 수신 시 `last_seen_seq` 와 비교. `seq == last_seen_seq + 1` 이면 정상 증분.
2. `seq > last_seen_seq + 1` (gap 감지) 이면 **즉시** `reconcile_from_redis()` 호출. 폴링 주기 대기 금지.
3. `seq <= last_seen_seq` (중복/역전) 이면 silent drop.
4. 페이로드 `summary` 는 관측용이며 **상태 판단 기준이 아니다**. 로컬 캐시는 항상 Redis HGET 결과로 갱신 (단일 진실원천은 Redis hash).

### 4.4 프로세스-로컬 캐시와 복구 주기

각 프로세스의 `TradingGuard` 인스턴스는 `_state` 를 **Redis 의 캐시**로만 보유한다.

- 기동 시 1회 `hydrate_from_redis()` 호출 (Redis 장애 시 fail-closed — §6 참조).
- Pub/sub 구독 백그라운드 태스크: 메시지 수신 → seq 비교 → 정상이면 Redis HGET 재조회 후 로컬 캐시 갱신, gap 이면 즉시 reconcile.
- `check_*` 계열은 로컬 캐시만 조회 (정상 경로 latency <10ms).
- **주기 reconcile: 1초**. 5초에서 1초로 단축한 이유는 kill switch on 전이가 유실되면 그 사이 주문이 통과할 수 있기 때문. Redis 호출은 로컬 네트워크 μs 단위라 1초 주기 부담 없음. Gap-detect 가 1차 방어선이므로 1초 폴링은 sanity 목적.
- Redis reconnect / `CLIENT PAUSE` 같은 edge 에서도 seq 불일치로 자동 gap 감지.

## 5. 인터페이스 변경 (최소 침습)

### 5.1 `TradingGuard` 메서드 시그니처

**변경 없음** — 호출부 수정 최소화. 내부 구현만 async 호출 변환.

단 `_activate_kill_switch` / `deactivate_kill_switch` / `activate_kill_switch` 는 Redis write + publish 를 포함한다. 이들 메서드는 기존에 sync 였으므로, 동기 호출부(`check_daily_loss_limit` 등) 와의 호환성을 위해 `redis.client.Redis` (sync) 를 사용한다. `redis.asyncio` 는 HTTP 라우트에서 별도 경로로 사용.

```python
# 기존 (unchanged signature)
def activate_kill_switch(self, reason: str, *, activated_by: str = "auto:unknown") -> None:
    ...

def deactivate_kill_switch(self, *, activated_by: str = "manual:unknown") -> None:
    ...
```

새 kwarg `activated_by` 는 기본값이 있어서 기존 호출부는 수정 없이 동작. 내부 자동 활성화 경로는 PR 에서 함께 업데이트 (`"auto:daily_loss"` 등).

### 5.2 HTTP 라우트

`/api/system/kill-switch/status` / `/kill-switch/deactivate` 는 **응답 스키마 변경 없음**. 내부 구현만 Redis hash 를 직접 조회·기록.

`/kill-switch/deactivate` 는 기존대로:
1. Pydantic validation (reason min_length=10, confirm=true)
2. `AuditLogger.log_strict(action_type="KILL_SWITCH_DEACTIVATE", ...)` 선행 — fail 시 503 `AUDIT_UNAVAILABLE` + kill switch **유지**
3. Redis HSET (`on=0`, `reason=""`, `previous_reason=<기존 reason>`, `activated_by="manual:{username}"`, `activated_at=<now>`) → Redis PUBLISH
4. Redis 장애 시 503 `REDIS_UNAVAILABLE` + kill switch **유지** (감사는 이미 기록된 상태로 남지만 실제 state 는 미변경 — 감사 선행 정책과 충돌하지 않도록 응답 필드 `redis_unavailable=true` 로 명시)
5. `PortfolioLedger.hydrate()` 는 기존 그대로 호출

### 5.3 Prometheus gauge

`TRADING_GUARD_KILL_SWITCH_ACTIVE` gauge 는 각 프로세스에서 **자기 로컬 캐시 기준** 으로 `.set(0/1)`. 두 프로세스 모두 `/metrics` 를 expose 하므로(backend: 8000, scheduler: 9102 등), 두 엔드포인트가 **동일 값**을 반환해야 정상이다. dry-run 시 두 엔드포인트의 gauge 값 일치를 반드시 확인한다.

## 6. 장애 모드 (Failure Mode Policy)

| 상태 | 정책 | 근거 |
|------|------|------|
| Redis 불가 (connection error) on `activate_kill_switch` | **로컬 캐시만 on 으로 전이** + Prometheus gauge set(1) + `logger.critical` + 별도 알림. Redis write 는 재시도 큐에 적재(백그라운드에서 복구 시 flush) | on 전이는 fail-closed. "Redis 죽음 = 자동 off" 경로를 원천 차단 |
| Redis 불가 on `deactivate_kill_switch` (HTTP) | 503 `REDIS_UNAVAILABLE` 반환. 감사는 이미 기록됐으므로 응답 metadata 에 `audit_recorded=true, state_applied=false` 명시. 운영자는 Redis 복구 후 재호출 | Fail-closed 의 해제 측면 — 감사는 있지만 상태 변경은 안 됨 |
| Redis 불가 on `hydrate_from_redis()` (startup) | 프로세스는 **kill_switch_on=true, reason="redis_unavailable_at_startup"** 로 기동 + 주기 재시도 | 기동 시 Redis 불가는 관측 레이어 단절 — 보수적으로 차단 유지 |
| Pub/sub 메시지 유실 | 1차: subscriber 의 seq gap-detect 가 즉시 `reconcile_from_redis()` 트리거 (지연 0). 2차: 1초 주기 `reconcile_from_redis()` 가 sanity 복구 (gap 감지 실패 edge 대비). | v0.2 결정 §10 #2 — 5초 폴링 시 kill switch on 지연 중 주문 통과 가능. 1초 + seq gap 으로 복구 latency 실효 0 에 근접 |
| Redis 다운 → 복구 | 프로세스 로컬 캐시가 stale 한 상태에서 Redis 가 살아나면 `reconcile_from_redis()` 가 로컬을 Redis 기준으로 덮어쓴다. 로컬에 on 이 있고 Redis 가 off 면 로컬도 off | 단일 진실원천은 Redis. 로컬은 캐시일 뿐 |

`P0-3` (order idempotency) 과 동일 fail-closed 정책. `core/idempotency/order_idempotency.py:16-18` 참조.

## 7. 파일 변경 범위

| 파일 | 변경 |
|------|------|
| `backend/core/trading_guard.py` | `TradingGuard` 내부에 Redis client + pub/sub listener 도입. `_state` 는 로컬 캐시로만 사용. `_activate_kill_switch` / `deactivate_kill_switch` / `activate_kill_switch` 는 Redis write + publish. `hydrate_from_redis` / `reconcile_from_redis` / `_start_subscriber` 신규 메서드 |
| `backend/core/trading_guard_redis_store.py` (신규) | Redis 접근 헬퍼 (HGET/HSET/PUBLISH/SUBSCRIBE 래퍼). `core/idempotency/order_idempotency.py` 의 Redis 클라이언트 패턴을 모방해 재사용성 확보 |
| `backend/main.py` | lifespan 의 `get_trading_guard()` 호출부에 `await guard.hydrate_from_redis()` + subscriber task 시작 + shutdown 에서 cancel/await |
| `backend/scheduler_main.py` (`async def main()`) | backend 와 동일한 hydrate + subscriber/reconcile task 기동. `RedisManager.connect()` 이후 + `portfolio_ledger.hydrate()` 앞 위치. shutdown 은 `exchange_rate_task` 와 동일 패턴으로 cancel + await. |
| `backend/api/routes/system.py` | `/kill-switch/status` 는 `trading_guard.snapshot()` 호출로 변경(현재는 `_state` 직접 읽음). `/kill-switch/deactivate` 는 Redis 장애 분기 추가 |
| `backend/core/monitoring/metrics.py` | 변경 없음. gauge 는 기존대로 |
| `backend/tests/test_kill_switch_routes.py` | 기존 9건 유지 + Redis mocking fixture 추가 (TestClient 는 단일 프로세스라 pub/sub 는 mocked) |
| `backend/tests/integration/test_trading_guard_cross_process.py` (신규) | docker-compose 실환경에서 scheduler 활성화 → backend 관측, backend 해제 → scheduler 관측 검증. 최소 3 시나리오 |
| `backend/tests/test_trading_guard_redis.py` (신규) | Redis 클라이언트 단위 테스트. hydrate, activate, deactivate, reconcile, subscriber 수신, fail-closed 경로 |
| `docs/operations/trading-halt-policy.md` | §6 코드 연동 포인트에 Redis key + channel 추가. v1.2 로 승격 |
| `docs/operations/midday-check-path-a-runbook.md` | §0.1 재개 조건 4건 충족 확인 후 v1.1 로 SUSPENDED 해제 + T7 "scheduler→backend 교차관측 확인" 전제 추가 |

## 8. 구현 순서 (Commit Plan)

각 커밋은 (i) 추가/변경할 함수 시그니처, (ii) 작성할 테스트 함수 이름과 시나리오, (iii) 건드릴 config 키, (iv) 영향받는 정적 검사기, (v) 커밋 전 검증 명령, (vi) 롤백 절차의 6 요소를 명시한다. `ruff check` / `black --check` / `check_rbac_coverage.py` / `check_loguru_style.py` / `check_bool_literals.py` / `pytest` 는 **모든 커밋에서 공통 게이트**다 — 아래 "검사기" 항목은 해당 커밋에서 **신규로 영향을 받는** 검사기만 열거한다.

### 8.A Commit A — 설계 문서 v0.3 머지 (doc-only)

| 요소 | 내용 |
|------|------|
| 파일 변경 | `docs/security/trading-guard-redis-migration.md` (이 문서 자체, v0.2 → v0.3) |
| 함수 시그니처 변경 | 없음 |
| 추가 테스트 | 없음 |
| 설정 키 | 없음 |
| 검사기 | `check_doc_sync.py` (문서 링크/버전 동기화) |
| 커밋 전 검증 | `python scripts/check_doc_sync.py --verbose` (0 errors + 0 warnings); `python scripts/check_bool_literals.py` (문서에 bool 예시가 포함되면); 코드 zero-diff 이므로 CLAUDE.md "문서-only" 예외 적용, 전체 pytest 생략 가능 |
| 롤백 | `git revert <commit>` — 코드 영향 없음. 후속 커밋 B~E 는 이 문서 버전을 참조하므로 롤백 시 후속 PR 의 문서 링크도 함께 되돌린다. |

### 8.B Commit B — `TradingGuardRedisStore` 신설 + 단위 테스트

Redis 접근을 `TradingGuard` 에서 분리한 헬퍼 모듈. `TradingGuard` 는 이 커밋에서 **변경하지 않는다** — store 와 guard 의 리팩터링을 한 커밋에 섞으면 회귀 발생 시 책임 경계가 흐려진다 (CLAUDE.md "bug fix 커밋에 무관한 '이왕 고치는 김에' 변경 금지" 규칙의 일반화).

**신규 파일**: `backend/core/trading_guard_redis_store.py`

| 요소 | 내용 |
|------|------|
| 함수 시그니처 | `class TradingGuardRedisStore:` <br> `  def __init__(self, client: redis.Redis) -> None` <br> `  def hydrate(self) -> TradingGuardState \| None` — hash 부재 시 `None` 반환 (caller 가 fail-closed 판정) <br> `  def atomic_update(self, fields: dict[str, str]) -> int` — Lua script 경유, 반환 seq <br> `  def increment_counter(self, field: str, amount: float \| int) -> int` — `HINCRBYFLOAT`/`HINCRBY` + seq Lua <br> `  def publish_change(self, seq: int, event: str, summary: dict) -> int` — `PUBLISH` 반환 subscriber 수 <br> `  def get_current_seq(self) -> int` — `GET aqts:trading_guard:seq` <br> `  def subscribe_changes(self) -> "redis.client.PubSub"` — 구독자는 caller 가 생애주기 관리 |
| 예외 클래스 | `class RedisUnavailable(RuntimeError)` — `redis.exceptions.RedisError` 계열을 상위에서 래핑 |
| 모듈 상수 | `STATE_KEY = "aqts:trading_guard:state"`, `SEQ_KEY = "aqts:trading_guard:seq"`, `CHANNEL = "aqts:trading_guard:state_change"`. 하드코딩 금지 규칙에 따라 `config/settings.py` 의 `redis` 섹션에 prefix 를 선언하고 f-string 으로 조립 (§12 참조) |
| 신규 테스트 | `backend/tests/test_trading_guard_redis_store.py` <br> `test_hydrate_returns_none_when_hash_missing` — `FLUSHDB` 후 `hydrate()` → `None` <br> `test_atomic_update_increments_seq` — 2회 호출 시 seq 가 `N`, `N+1` <br> `test_atomic_update_writes_all_fields` — dict 5개 필드 → `HGETALL` 결과 일치 <br> `test_increment_counter_float_precision` — `HINCRBYFLOAT` 로 `-0.01` 누적 10회 → `-0.10` ± 1e-9 <br> `test_publish_returns_subscriber_count` — subscriber 0/1/2 상황에서 기대값 일치 <br> `test_get_current_seq_initial_zero` — 초기 상태 0 <br> `test_subscribe_changes_receives_publish` — subscriber 연결 후 publish → receive (asyncio.wait_for 5s 타임아웃) <br> `test_redis_unavailable_raises_on_connection_error` — `redis.ConnectionError` → `RedisUnavailable` <br> `test_redis_unavailable_raises_on_timeout` — `redis.TimeoutError` → `RedisUnavailable` <br> `test_lua_script_atomicity_under_concurrent_writes` — 2 스레드 동시 호출 10회 → seq 단조 증가 + 필드 값 일관 <br> `test_atomic_update_preserves_unlisted_fields` — A 필드 갱신이 B 필드를 건드리지 않음 <br> `test_seq_field_in_hash_equals_seq_counter` — atomic_update 후 `HGET state seq` == `GET seq` <br> `test_increment_counter_integer_type` — `HINCRBY` 경로 (int 전용) <br> `test_publish_payload_json_shape` — 페이로드에 `seq`, `event`, `summary` 키 존재 |
| 테스트 fixture | `backend/tests/conftest.py` 에 `real_redis` 또는 `fakeredis` fixture. `fakeredis>=2.0` 은 Lua/pub-sub 부분 지원 → 통합 테스트 단에서 실제 Redis 로 보강. 단위 레벨은 `fakeredis.FakeRedis` 로 충분. Lua 스크립트는 `FakeRedis` 가 지원. |
| 설정 키 | §12 에서 상세 — `REDIS_TRADING_GUARD_KEY_PREFIX` (기본 `aqts:trading_guard`), `REDIS_TRADING_GUARD_LUA_HASH_CACHE` (boolean) |
| 검사기 | `check_loguru_style.py` (새 모듈에 logger 호출 포함 시); 하드코딩 키/TTL 회피 — `check_bool_literals.py` (신규 env 추가 시 화이트리스트 갱신) |
| 커밋 전 검증 | `cd backend && python -m ruff check . --config pyproject.toml`; `cd backend && python -m black --check . --config pyproject.toml`; `cd backend && python -m pytest tests/test_trading_guard_redis_store.py -v`; `cd backend && python -m pytest tests/ -q --tb=short` (타임아웃 600s); `python scripts/check_bool_literals.py`; `python scripts/check_loguru_style.py`; `python scripts/check_doc_sync.py --verbose` |
| 롤백 | 코드 롤백만 (`git revert <B>`). 이 커밋은 `TradingGuard` 를 건드리지 않으므로 운영 영향 없음. Redis 키는 어차피 아직 생성되지 않음. |

### 8.C Commit C — `TradingGuard` 리팩터링 + HTTP 라우트 Redis 장애 분기 + lifespan wiring

**변경 파일**: `backend/core/trading_guard.py`, `backend/main.py`, `backend/scheduler_main.py`, `backend/api/routes/system.py`, `backend/tests/test_kill_switch_routes.py`, `backend/tests/test_trading_guard.py`

| 요소 | 내용 |
|------|------|
| 함수 시그니처 변경 | `TradingGuard.__init__(self, *, redis_store: TradingGuardRedisStore \| None = None)` — DI 가능. 기본 `None` 이면 `get_redis_client()` 로 생성. <br> 신규: `def hydrate_from_redis(self) -> None` (sync) — 부팅 1회. Redis 불가 시 `_state.kill_switch_on = True; _state.kill_switch_reason = "redis_unavailable_at_startup"` + critical 로그 + gauge set(1). <br> 신규: `def reconcile_from_redis(self) -> None` — 로컬 캐시를 Redis 기준으로 덮어쓰기 + Prometheus counter 증분. <br> 신규: `async def _subscriber_loop(self, stop_event: asyncio.Event) -> None` — pub/sub 수신 + seq gap-detect. <br> 신규: `async def _reconcile_loop(self, stop_event: asyncio.Event, interval_sec: float = 1.0) -> None` — 1초 주기 sanity reconcile. <br> 신규: `async def start_background_tasks(self, stop_event: asyncio.Event) -> tuple[asyncio.Task, asyncio.Task]` — lifespan 에서 호출. <br> 기존 서명 유지: `activate_kill_switch`, `deactivate_kill_switch`, `_activate_kill_switch` (단, 내부에서 `redis_store.atomic_update()` + `publish_change()` 호출. Redis 실패 시 §6 정책). |
| `TradingGuard` 내부 변경 | `_state` 는 Redis HGET 결과의 캐시. `_last_seen_seq: int` 필드 추가. 모든 `_state.*` 쓰기는 `_apply_from_redis_snapshot()` 단일 경로로 수렴. `check_*` 계열은 캐시만 읽음. |
| `api/routes/system.py` 변경 | `/kill-switch/status` 는 `guard.snapshot()` 호출로 수정 (현재 `_state.*` 직접 접근). `/kill-switch/deactivate` 에 Redis 장애 분기 추가: `RedisUnavailable` 포착 시 503 + `{"error_code": "REDIS_UNAVAILABLE", "audit_recorded": true, "state_applied": false}`. 감사는 이미 기록된 상태로 유지 (§5.2). |
| `main.py` lifespan | `get_trading_guard()` 직후 `guard.hydrate_from_redis()` 호출 + `asyncio.create_task(guard._subscriber_loop(stop_event))` + `_reconcile_loop(stop_event)` 두 task 를 `app.state` 에 저장. shutdown 에서 `stop_event.set()` → `await task` (cancel 후 `asyncio.CancelledError` 포착). 기동 로그: `TradingGuard wired (hydrated_from_redis=<bool>, initial_kill_switch=<bool>)`. |
| `scheduler_main.py` wiring | `async def main()` 의 `RedisManager.connect()` 직후 + `portfolio_ledger.hydrate()` 앞 위치에 `guard = get_trading_guard(); guard.hydrate_from_redis()` 호출. 이후 `exchange_rate_task` 와 같은 패턴으로 `subscriber_task = asyncio.create_task(guard._subscriber_loop(stop_event))`, `reconcile_task = asyncio.create_task(guard._reconcile_loop(stop_event))`. shutdown 에서 `exchange_rate_task` 와 동일하게 `cancel()` + `await`. 기동 로그 동일 문구. |
| 신규/수정 테스트 | `backend/tests/test_trading_guard.py` 보강: <br> `test_hydrate_populates_local_cache_from_redis` <br> `test_hydrate_redis_unavailable_sets_killswitch_on_with_reason_startup` <br> `test_activate_kill_switch_writes_redis_and_publishes` <br> `test_deactivate_kill_switch_writes_redis_and_publishes` <br> `test_activate_redis_unavailable_keeps_local_on_and_queues_retry` <br> `test_reconcile_overwrites_local_from_redis` <br> `test_subscriber_loop_applies_event_when_seq_increments_by_one` <br> `test_subscriber_loop_triggers_reconcile_on_seq_gap` <br> `test_subscriber_loop_drops_stale_or_duplicate_seq` <br> `test_record_trade_increments_redis_counter_atomically` <br> `backend/tests/test_kill_switch_routes.py` 수정: 기존 9건에 `redis_store` mock fixture 도입. 신규 4건: <br> `test_deactivate_503_when_redis_unavailable_with_audit_recorded_true` <br> `test_status_reads_from_redis_hash` <br> `test_status_reflects_remote_activation_after_reconcile` <br> `test_deactivate_emits_pubsub_event_to_other_process_simulated` (mock subscriber). |
| 설정 키 | §12 참조 — `TRADING_GUARD_RECONCILE_INTERVAL_SEC` (기본 1.0, settings.py `TradingGuardSettings`), `REDIS_TRADING_GUARD_KEY_PREFIX` (기본 `aqts:trading_guard`). `.env.example` 갱신. |
| 검사기 | `check_rbac_coverage.py` — `system.py` 의 라우트에 이미 `require_viewer`/`require_admin` 가드가 있으므로 영향 없음이 **정상**. 회귀 방지로 이 커밋 후에도 0 errors 유지 확인. <br> `check_loguru_style.py` — 새로 추가되는 `logger.critical` / `logger.info` 호출 모두 f-string 또는 `{variable}` 바인딩 사용 (stdlib `%` 포맷 금지). <br> `check_bool_literals.py` — `TRADING_GUARD_*` 신규 env 를 `BOOL_ENV_KEYS` 화이트리스트에 추가 여부 확인 (boolean 이면). <br> `check_doc_sync.py` — `trading-halt-policy.md` §6 의 Redis 키 언급과 본 문서 동기화. |
| 커밋 전 검증 | `cd backend && python -m ruff check . --config pyproject.toml` 0 errors; `cd backend && python -m black --check . --config pyproject.toml` 0 errors; `python scripts/check_rbac_coverage.py` 0 errors; `python scripts/check_loguru_style.py` 0 errors; `python scripts/check_bool_literals.py` 0 errors + 0 warnings; `python scripts/check_doc_sync.py --verbose` 0 errors; `cd backend && python -m pytest tests/ -q --tb=short` (타임아웃 600s, 전수 통과 확인); 수동: `docker compose up -d backend scheduler` 후 `docker compose logs backend --tail=100 \| grep "TradingGuard wired"` 과 `docker compose logs scheduler --tail=100 \| grep "TradingGuard wired"` 양쪽 출력 확인. |
| 롤백 | `git revert <C>` + docker 재배포. Redis 의 `aqts:trading_guard:state` hash 는 **남겨둔다** (삭제하면 Commit C 재롤포워드 시 fail-closed 로 차단되어 복구가 느려짐). seq counter 도 유지. Commit B 는 살아있으므로 store 모듈은 import 가능한 상태 유지. |

### 8.D Commit D — 교차프로세스 통합 테스트

**신규 파일**: `backend/tests/integration/test_trading_guard_cross_process.py`

| 요소 | 내용 |
|------|------|
| 실행 방식 | `pytest.mark.integration` + `pytest.mark.requires_docker`. pytest 가 `docker compose -f docker-compose.test.yml up -d` 로 redis + backend + scheduler 기동 후 실행. CI 에서 별도 잡으로 분리 (단위 테스트 잡의 타임라인에 얹지 않음). |
| 신규 테스트 함수 | `test_scheduler_activate_observed_by_backend` — scheduler 쪽에서 `check_daily_loss_limit` 경로로 `_activate_kill_switch` 호출 → 최대 2초 내 backend `/kill-switch/status` 가 `on=true` 반환 <br> `test_backend_deactivate_observed_by_scheduler` — backend `/kill-switch/deactivate` 호출 → 최대 2초 내 scheduler 의 guard 인스턴스 캐시가 `on=false` <br> `test_both_processes_see_auto_activation` — 자동 활성화 시 양쪽 `/metrics` gauge 모두 1.0 <br> `test_manual_deactivate_leaves_counters_intact` — deactivate 후 `daily_realized_pnl` 등 카운터가 그대로 남아있음 (리셋은 별도 경로) <br> `test_redis_restart_reconciliation` — `docker compose restart redis` 후 양 프로세스가 hydrate 재시도 → 기존 state hash 가 유지되어 정상 재연결 <br> `test_redis_stopped_fail_closed_activate_path` — `docker compose stop redis` 상태에서 guard 가 `redis_unavailable_at_startup` 로 진입하는지 (별도 프로세스 재기동) <br> `test_seq_gap_triggers_immediate_reconcile` — 한쪽 프로세스의 subscriber 를 일시 pause → 3 번 mutation → unpause → 즉시 reconcile 로 캐시 수렴 (`aqts_trading_guard_reconcile_total{reason="seq_gap"}` 증가 확인) <br> `test_gauge_consistency_between_processes_over_time` — 30초 동안 매 100ms polling 하여 두 `/metrics` 의 gauge 값이 모든 샘플에서 일치 <br> `test_concurrent_counter_increment_atomicity` — 양 프로세스에서 `record_trade(-0.01)` 을 100회씩 동시 호출 → 최종 `daily_realized_pnl` 이 `-2.00` ± 1e-9 |
| 설정 키 | `TRADING_GUARD_INTEGRATION_TIMEOUT_SEC` (기본 10), `TRADING_GUARD_CROSS_PROCESS_POLL_MS` (기본 100). settings.py 에 `TradingGuardSettings` 섹션 포함. |
| 검사기 | `check_doc_sync.py` — 신규 테스트 파일이 `docs/testing/integration-tests.md` (있다면) 에 등록되어 있는지 확인. <br> `check_loguru_style.py` — 테스트 코드의 logger 호출도 동일 규칙 적용. |
| 커밋 전 검증 | 모든 단위 테스트 (`cd backend && python -m pytest tests/ -q --tb=short --ignore=tests/integration`) 통과. 통합 테스트: `cd backend && python -m pytest tests/integration/test_trading_guard_cross_process.py -v --tb=short` (타임아웃 300s). `docker compose logs backend scheduler \| grep -E "seq_gap\|reconcile"` 로 Prometheus counter 와 로그 일관성 확인. |
| 롤백 | 통합 테스트 자체는 런타임에 무영향. `git revert <D>` 로 테스트만 제거. 단 Path A 재개 체크리스트 §9 항목 2번이 PASS 에서 미충족으로 복귀하므로 `midday-check-path-a-runbook.md` 의 SUSPENDED 도 유지. |

### 8.E Commit E — 문서 갱신 + 릴리스 노트

**변경 파일**: `docs/operations/trading-halt-policy.md` (v1.1 → v1.2), `docs/operations/midday-check-path-a-runbook.md` (v1.0.2 → v1.1, 재개 자격 확인 후), `docs/security/security-integrity-roadmap.md` (§9 완료 엔트리 append), `docs/operations/phase1-demo-verification-2026-04-11.md` (§10.18 재예행 결과 append), `docs/architecture/notification-pipeline.md` (참조 업데이트 필요 시), `CHANGELOG.md` (있는 경우)

| 요소 | 내용 |
|------|------|
| 파일별 변경 | `trading-halt-policy.md` §6 "코드 연동 포인트" 에 Redis key (`aqts:trading_guard:state`) + pub/sub 채널 + seq gap 정책 추가. v1.1 → v1.2. <br> `midday-check-path-a-runbook.md` — §9 체크리스트 5건 전수 PASS 확인 후에만 SUSPENDED 배너 제거, T1~T9 타임라인에 "T0-10m: scheduler/backend 양쪽 `/metrics` gauge 일치 확인" 전제 추가, 버전 1.0.2 → 1.1. <br> `security-integrity-roadmap.md` §9 에 "2026-04-XX: P0-5 Redis 이전 완료" 엔트리 append (일자는 Commit C/D 머지 일자 기준). <br> `phase1-demo-verification-2026-04-11.md` §10.18 에 재예행 결과 append — 자동 활성화 / 수동 해제 / seq gap 재조정 / 양 프로세스 gauge 일치 4가지 관측 증거표. |
| 함수 시그니처 변경 | 없음 |
| 신규 테스트 | 없음 |
| 설정 키 | 없음 |
| 검사기 | `check_doc_sync.py` 0 errors + 0 warnings (모든 문서 크로스링크 일관). `check_bool_literals.py` (예시 블록에 env 가 있으면). |
| 커밋 전 검증 | CLAUDE.md "문서-only 커밋" 예외 적용 — 코드 zero-diff 이므로 전체 pytest 생략 가능. 대신 필수 게이트: `ruff check`, `black --check` (zero impact 확인), `check_bool_literals.py`, `check_doc_sync.py --verbose`, `pytest tests/test_doc_sync.py` (있으면). 수동: 본 문서 §9 체크리스트 5건 전수 PASS 캡처. |
| 롤백 | `git revert <E>` — 문서만 복귀, 운영 영향 없음. 단 Path A 재개가 이미 이루어진 경우에는 별도 중단 결정이 필요 (문서 롤백 ≠ 운영 중단). |

---

**커밋 분리 원칙**: 한 커밋에 인프라 + 기능 + 문서를 섞지 않는다. Commit B (Redis store) 는 Commit C (guard 리팩터링) 와 분리해야, guard 리팩터링에서 회귀가 발생해도 Redis store 자체는 롤백하지 않고 재시도할 수 있다. Commit D 는 Commit C 의 wiring 회귀를 잡는 안전망이므로 반드시 C 머지 후 바로 이어서 머지. Commit E 는 D 의 관측 결과를 반영해야 하므로 E 를 C 와 묶지 않는다.

## 9. 검증 체크리스트 (SUSPENDED 해제 전)

`midday-check-path-a-runbook.md` §0.1 재개 조건과 1:1 매핑:

- [ ] Redis 단일 저장소 이전 완료 — hash `aqts:trading_guard:state` + seq counter + Lua script (Commit B~C 머지)
- [ ] 교차프로세스 통합 테스트 PASS (Commit D): scheduler→backend / backend→scheduler 양방향 + 자동 활성화 + 수동 해제 + Redis 재기동 후 reconcile 회복 + seq gap 주입 시 즉시 reconcile 검증
- [ ] Redis 장애 시 fail-closed 검증: unit + 통합 테스트에서 Redis down 시나리오 (`docker compose stop redis` 후 activate/deactivate 동작, hydrate_from_redis 실패 시 kill_switch_on=true 로 기동)
- [ ] cosign 서명 이미지 배포 + DEMO 환경 재예행 성공 (§10.18 후속 작업)
- [ ] **최소 1 거래일 이상 양 프로세스 gauge 일치 관측** — backend/scheduler `/metrics` 의 `aqts_trading_guard_kill_switch_active` 가 단 한 번도 불일치하지 않고, `reconcile_from_redis` 호출 횟수 / pub-sub latency 히스토그램을 운영 대시보드로 확인

다섯 항목 모두 PASS + 운영책임자 별건 결정 후 Path A 런북 SUSPENDED 배너 제거.

## 10. 결정 기록 (v0.2 에서 해소)

v0.1 에서 제기된 세 질문은 "전체 서비스 완성도 우선" 원칙으로 다음과 같이 확정했다. 반영 위치를 함께 기록한다.

| # | 질문 | 결정 | 반영 위치 |
|---|------|------|-----------|
| 1 | Redis 이전 범위 | **전체 10개 필드 이전**. `daily_realized_pnl` 이 프로세스 로컬이면 scheduler 재기동 시 서킷브레이커 우회 경로가 잠복. 부분 이전은 아키텍처 비대칭 고착. 원자성은 Lua script + HINCRBYFLOAT/HINCRBY 로 지금 해결. | §3 (비목표에서 제외), §4.1 근거, §4.2 hash 구조 |
| 2 | Pub/sub 유실 정책 | **monotonic seq + gap-detect 즉시 reconcile + 1초 주기 reconcile**. 5초 폴링은 유실 edge 에서 kill switch on 이 5초 지연되어 그 사이 주문 통과 가능. seq 기반 gap 감지가 1차 방어선이고 1초 폴링은 sanity. | §4.2 seq 설계, §4.3 구독자 규약, §4.4 주기 |
| 3 | 내일 11:30 집행 | **무조건 취소**. 시간 데드라인(04:00 KST PASS 시 재개) 은 검증 품질을 깎아 내리는 인센티브를 만든다. Path A 재개는 Commit D PASS + 1 거래일 이상 관측 안정화 + 운영책임자 별건 결정. | §9 체크리스트 4번 수정, `midday-check-path-a-runbook.md` §0.1 재개 조건 5번 추가(아래 별도 편집), `phase1-demo-verification-2026-04-11.md` §10.18 후속 작업표 #6 삭제 |

### 10.1 미래 확장용 열린 질문 (별건 트래킹)

본 이전에서 다루지 않지만 후속 P1 로 관리할 항목:

- Redis HA/sentinel/cluster 도입 여부. 현재 단일 Redis 는 P0-2/P0-3 과 동일한 SPOF 를 공유. 이행 시 접근 패턴(pub/sub + hash + Lua) 이 sentinel 환경에서 동작하는지 검증 필요.
- `TradingGuardState` 의 `is_active` 플래그가 실제로 사용되는지 전수 감사. 현재 기본 `True` 고정이고 off 로 전이하는 경로가 없다면 이전 시 제거 후보.
- 일일 리셋 루틴(`reset_daily_state`) 이 어느 프로세스에서 호출되는지 단일화. 여러 프로세스가 동시에 호출하면 seq 경합만 날 뿐 결과는 같지만, 감사상 단일 경로로 수렴하는 것이 바람직.

## 11. 관측 (Observability)

공유 상태 이전은 관측 레이어가 동반되지 않으면 회귀 발견이 지연된다. 아래 지표·로그·대시보드는 Commit C 에서 함께 추가한다 — 알림 파이프라인 Wiring Rule 의 "정의 ≠ 적용" 관점으로, 지표를 정의만 하고 `.inc()`/`.observe()` 를 호출하지 않으면 무의미하다.

### 11.1 Prometheus 지표 (신규)

| 지표 | 타입 | 라벨 | 증가 지점 | 용도 |
|------|------|------|-----------|------|
| `aqts_trading_guard_reconcile_total` | Counter | `reason={"seq_gap","periodic","startup","redis_reconnect"}` | `reconcile_from_redis()` 진입 시 | 유실/드리프트 빈도 관측 |
| `aqts_trading_guard_pubsub_latency_seconds` | Histogram | (none) | publish timestamp 와 subscriber 수신 timestamp 차이 (페이로드에 `publisher_ts` 추가) | 채널 지연 SLO |
| `aqts_trading_guard_redis_error_total` | Counter | `operation={"hydrate","atomic_update","publish","subscribe","increment"}` | `RedisUnavailable` 포착 지점 | 장애 빈도 추적 |
| `aqts_trading_guard_seq` | Gauge | `process={"backend","scheduler"}` | subscriber 가 캐시에 적용한 마지막 seq | 프로세스 간 seq 차이로 드리프트 조기 감지 |
| `aqts_trading_guard_subscriber_connected` | Gauge | `process={"backend","scheduler"}` | subscriber task 가 loop 시작 시 1, 종료/예외 시 0 | subscriber 사망 감지 |
| `aqts_trading_guard_atomic_update_duration_seconds` | Histogram | `event` | Lua script 호출 duration | Redis 성능 회귀 감지 |

기존 `TRADING_GUARD_KILL_SWITCH_ACTIVE` gauge 는 유지하며, 프로세스별 값이 달라질 수 있으므로 `process` 라벨을 추가하지 않는다(프로세스별 `/metrics` 엔드포인트가 이미 분리되어 있음). Alertmanager 에 불일치 감지 alert 를 별도 작성: `abs(aqts_trading_guard_seq{process="backend"} - aqts_trading_guard_seq{process="scheduler"}) > 5 for 30s` → warning, `> 20 for 60s` → critical.

### 11.2 구조화 로그 필드

`TradingGuard` 에서 발화하는 critical/info/warning 로그에 다음 키를 일관되게 포함한다 (loguru bind + f-string, § CLAUDE.md §10.15/10.16 의 `%` 포맷 금지 규칙 준수):

- `seq`: 이벤트 시점의 Redis seq
- `event`: `activate` / `deactivate` / `counter_update` / `reset_daily` / `reconcile`
- `reason`: kill switch 사유 또는 reconcile 트리거
- `activated_by`: `auto:*` / `manual:*`
- `process`: `backend` / `scheduler` (`LOG_PROCESS_NAME` env 기반)
- `redis_available`: true/false

기동 로그: `TradingGuard wired (process=<>, hydrated_from_redis=<bool>, initial_kill_switch=<bool>, initial_seq=<int>)` — 양 프로세스 모두 출력. `docker compose logs backend scheduler | grep 'TradingGuard wired'` 로 사후 확인 (알림 파이프라인 Wiring Rule §필수 배포 후 검증과 동일 패턴).

### 11.3 대시보드 패널

`docs/operations/grafana-dashboards.md` (존재 시) 에 다음 패널 추가. 없으면 본 문서 §11.3 의 JSON 스니펫을 참조하여 구성.

1. **Kill switch 상태** — `TRADING_GUARD_KILL_SWITCH_ACTIVE` 프로세스별 stat panel (backend / scheduler 2개)
2. **Seq 일치 여부** — `aqts_trading_guard_seq` time series, 두 프로세스 overlay
3. **Reconcile 빈도** — `rate(aqts_trading_guard_reconcile_total[5m])` by reason
4. **Pub/sub latency p50/p99** — `aqts_trading_guard_pubsub_latency_seconds` histogram_quantile
5. **Redis error rate** — `rate(aqts_trading_guard_redis_error_total[5m])` by operation
6. **Subscriber connected** — `aqts_trading_guard_subscriber_connected` 가 30초 이상 0 이면 alert

## 12. 설정 (Configuration)

CLAUDE.md "하드코딩 절대 금지" 원칙에 따라 모든 임계값/키 prefix 는 `config/settings.py` 의 `TradingGuardSettings` 섹션으로 노출한다. env 변환은 반드시 `core.utils.env.env_bool()` / settings 의 `Field(..., env=...)` 경유 — ad-hoc `os.environ.get(...)` 금지.

### 12.1 신규 settings 필드 (`TradingGuardSettings`)

| 필드 | 타입 | 기본값 | env 키 | 비고 |
|------|------|--------|--------|------|
| `redis_key_prefix` | str | `"aqts:trading_guard"` | `TRADING_GUARD_REDIS_KEY_PREFIX` | hash/seq/channel 공통 prefix. 해시는 `<prefix>:state`, seq 는 `<prefix>:seq`, 채널은 `<prefix>:state_change` |
| `reconcile_interval_sec` | float | `1.0` | `TRADING_GUARD_RECONCILE_INTERVAL_SEC` | sanity 폴링 주기. §4.4 근거로 1.0 권장. 운영 조정 여지 남김. |
| `subscriber_reconnect_backoff_sec` | float | `0.5` | `TRADING_GUARD_SUBSCRIBER_BACKOFF_SEC` | Redis 연결 끊김 시 재시도 간격 (exponential backoff 상한 10s) |
| `hydrate_retry_interval_sec` | float | `2.0` | `TRADING_GUARD_HYDRATE_RETRY_SEC` | 기동 시 Redis 불가 상태에서 주기적 재시도 |
| `integration_poll_timeout_sec` | float | `10.0` | `TRADING_GUARD_INTEGRATION_TIMEOUT_SEC` | Commit D 통합 테스트 전용 |
| `log_process_name` | str \| None | `None` | `LOG_PROCESS_NAME` | `backend` / `scheduler` 구분 (docker-compose 에서 주입) |

**결정**: `reconcile_interval_sec` 은 env 로 노출하지만 **운영 기본값은 1.0 을 권장**. 1.0 미만으로 조정하면 Redis 부하 대비 이득이 거의 없고, 1.0 초과로 조정하면 §4.4 근거가 약화된다. 운영자가 의도적으로 조정하는 경우에만 사용 (ex. Redis 장애 대응 drill 시 5.0 으로 조정해 복구 지연 관측).

### 12.2 `.env.example` 갱신

Commit C 에서 다음 블록을 `.env.example` 에 추가:

```
# TradingGuard Redis 이전 (SEC-007)
TRADING_GUARD_REDIS_KEY_PREFIX=aqts:trading_guard
TRADING_GUARD_RECONCILE_INTERVAL_SEC=1.0
TRADING_GUARD_SUBSCRIBER_BACKOFF_SEC=0.5
TRADING_GUARD_HYDRATE_RETRY_SEC=2.0
TRADING_GUARD_INTEGRATION_TIMEOUT_SEC=10.0
# docker-compose 가 주입: backend / scheduler
# LOG_PROCESS_NAME=backend
```

CLAUDE.md "환경변수 Boolean 표기 표준 규칙" — 위 env 는 모두 숫자/문자열이므로 `BOOL_ENV_KEYS` 화이트리스트 대상이 아니다. bool env 를 추가하는 경우에만 `scripts/check_bool_literals.py::BOOL_ENV_KEYS` 에 등록하고 `docs/conventions/boolean-config.md` 에 예시 추가.

### 12.3 `docker-compose.yml` 영향

`backend` 와 `scheduler` 서비스의 `environment:` 에 `LOG_PROCESS_NAME: "backend"` / `LOG_PROCESS_NAME: "scheduler"` 추가. 이는 Python 실행 경로에 직접 영향이 없으나(단순 라벨), CLAUDE.md "docker-compose.yml 예외" 기준으로는 environment 변경이 "코드가 읽는 변수" 이므로 **전체 pytest 를 실행해야 한다**. 즉 Commit C 는 문서-only 커밋 예외에 해당하지 **않는다**.

## 13. 롤백 계획 (Rollback Plan)

`security-integrity-roadmap.md` §4.1 "롤백 예행연습" 요구와 연결. 각 커밋은 개별 롤백 가능해야 하며, Commit C 롤백이 가장 복잡하다.

### 13.1 커밋별 롤백 절차

| 커밋 | 롤백 명령 | 운영 영향 | 데이터 정리 | 예행연습 필수 |
|------|-----------|-----------|------------|---------------|
| A (v0.3 문서) | `git revert <A>` | 없음 | 없음 | 불필요 |
| B (store 모듈) | `git revert <B>` | 없음 (store 는 아직 사용되지 않음) | 없음 (키 생성 전) | 불필요 |
| C (guard 이전) | `git revert <C>` + `docker compose up -d --force-recreate backend scheduler` | 로컬 싱글톤 복귀. **이 사이 발생한 kill switch on 상태가 메모리에만 남아있을 수 있으므로**, 롤백 전 Redis hash 를 확인해 `kill_switch_on=1` 이면 배포자가 수동으로 과거 코드의 `/kill-switch/status` 를 확인 후 필요시 `/kill-switch/deactivate` 재호출 | Redis 의 `aqts:trading_guard:state` hash 와 seq counter 는 **유지한다**. 삭제하면 재롤포워드 시 `hydrate_from_redis` 가 null 을 보고 fail-closed 로 차단. | **필수** — 스테이징에서 최소 1회 rehearsal 후 운영 적용 |
| D (통합 테스트) | `git revert <D>` | 없음 (테스트만) | 없음 | 불필요 |
| E (문서) | `git revert <E>` | 없음 | 없음. 단 Path A 재개가 이미 이루어졌다면 별도 중단 결정 | 불필요 |

### 13.2 롤백 예행연습 체크리스트

Commit C 배포 전에 스테이징에서 다음을 실행한 기록을 남긴다:

1. Commit C 이전 SHA 로 스테이징 기동 → `/kill-switch/status` off 확인
2. Commit C 배포 → 양 프로세스 기동 로그 `TradingGuard wired` 확인, gauge 일치 확인
3. `curl -X POST .../kill-switch/deactivate ...` 으로 (이미 off 여도) deactivate 시도 → 200 + Redis hash 갱신 확인
4. `docker exec redis redis-cli HGETALL aqts:trading_guard:state` 로 hash 내용 dump
5. **롤백**: `git revert <C>` → 재배포 → 양 프로세스 기동 → `/kill-switch/status` 가 off 유지 확인
6. Redis hash 는 유지되어 있는지 재확인 (운영자가 `DEL` 하지 않음)
7. **재롤포워드**: Commit C 다시 적용 → 기동 → `/kill-switch/status` 가 Redis hash 의 `kill_switch_on=0` 을 반영하는지 확인

`security-integrity-roadmap.md` §4.1 의 rollback 요건에 따라 이 체크리스트의 전 단계 실행 기록을 PR 본문 또는 별도 운영 로그 문서에 남긴다.

### 13.3 재난 복구 (Redis 완전 소실)

Redis 인스턴스 자체가 소실되어 `aqts:trading_guard:state` 가 복구 불가능한 경우:

1. 양 프로세스는 `hydrate_from_redis` 가 null 을 반환 → fail-closed 로 `kill_switch_on=true` 진입
2. 운영자가 Redis 를 재기동 → 양 프로세스의 `hydrate_retry_interval_sec` 주기로 자동 재시도
3. 재연결 성공 후 Redis hash 가 비어있으므로 양 프로세스가 `hydrate_from_redis` 에서 초기 state 를 **씨드** 한다 — 이 경로는 Commit C 에서 명시적으로 구현 (단일 프로세스만 씨드 시도: Lua `SETNX`-유사 패턴으로 경합 방지)
4. 씨드된 초기 state 는 `is_active=1, kill_switch_on=1, kill_switch_reason="post_disaster_recovery"` 로 진입 → 운영자가 수동 해제로 정상 모드 복귀
5. 해제 전까지 scheduler 는 주문을 집행하지 않는다 (fail-closed)

## 14. 정적 검사기 영향 (Static Checker Impact)

CLAUDE.md "정적 방어선 커버리지 결손" 원칙 — 검사기를 추가한 것과 새 코드가 검사에 잡히는 것은 다른 문제다. 본 이전으로 영향받는 검사기를 전수 열거하고, 각 검사기의 회귀 가능 영역을 점검한다.

| 검사기 | 영향 | 점검 항목 | 수정 필요 여부 |
|--------|------|-----------|----------------|
| `scripts/check_rbac_coverage.py` | 라우트 변경 없음 (status/deactivate 기존 유지). 단 `system.py` 수정 시 AST 파서 기준에서 기존 `require_viewer`/`require_admin` 데코레이터가 보존되는지 확인 | 0 errors + 0 warnings 유지 | 불필요 (회귀 방지 확인만) |
| `scripts/check_loguru_style.py` | 신규 `logger.critical` / `logger.info` 호출 다수. 모두 f-string 또는 `{kwarg}` 바인딩 사용 — `%d`/`%s` posarg 금지 (CLAUDE.md §10.15/10.16) | 0 errors + 0 warnings 유지. 검사기가 새 호출을 모두 scan 하는지 회귀 테스트 1건 추가 권장 | 수정 가능 (검사기 자체 테스트 `test_check_loguru_style.py` 에 TradingGuard-유사 케이스 추가) |
| `scripts/check_bool_literals.py` | `TRADING_GUARD_*` env 중 boolean 이 없으므로 `BOOL_ENV_KEYS` 갱신 불필요. 그러나 `.env.example` 에 추가되는 env 가 모두 숫자/문자열인지 검사기가 false-positive 를 내지 않는지 확인 | 0 errors + 0 warnings 유지 | 불필요 |
| `scripts/check_doc_sync.py` | 본 문서 v0.3 + `trading-halt-policy.md` v1.2 + `midday-check-path-a-runbook.md` v1.1 + `security-integrity-roadmap.md` §9 + `phase1-demo-verification-2026-04-11.md` §10.18 의 버전/링크 전수 일관 | 0 errors + 0 warnings. `TEST_COUNT` warning 이 뜨면 FEATURE_STATUS.md 의 테스트 수를 실제 값으로 갱신 (CLAUDE.md "CI/CD 검증 결과 전수 처리" 원칙) | Commit E 에서 최종 확인 |
| `pip-audit` (CI) | `redis` 파이썬 라이브러리는 기존에 이미 pin. 추가 의존성 없음 (fakeredis 는 dev-only). 단 `fakeredis>=2.0` 추가 시 pip-audit 결과 재확인 | Commit B 에서 `pip-audit` 실행 결과 0 HIGH+ 유지 확인 | `fakeredis` 추가 시 `backend/.pip-audit-ignore` 반드시 미사용 (만료일+사유 없이 화이트리스트 금지) |
| `grype` (CI 컨테이너 스캔) | 이미지 베이스 변경 없음 | 0 errors + 0 high CVE | 불필요 |
| `cosign verify` (CD) | 이미지 digest 변경됨 → 재서명 필요. CD 파이프라인이 정상 동작하면 자동 처리 | `docker compose up` 전에 `cosign verify` 로그 확인 (CLAUDE.md 공급망 보안 규칙) | 불필요 (CI/CD 자동) |
| `scripts/check_rbac_coverage.py` 의 테스트 (`test_check_rbac_coverage.py`) | 기존 테스트 유지 | 0 errors | 불필요 |

**신규 정적 검사기 필요성 검토**: `check_trading_guard_wiring.py` 같은 전용 검사기를 만들 필요가 있는가? 판단: 현 시점에는 불필요. 이유는 (a) Commit D 의 통합 테스트가 wiring 를 직접 검증하고 (b) `check_rbac_coverage` 와 달리 TradingGuard wiring 은 라우터처럼 데코레이터 기반 패턴이 아니라 lifespan 내부 호출이어서 AST 로 "모든 위치에 있어야 함" 을 선언적으로 표현하기 어렵다. 대신 기동 로그 `TradingGuard wired` 를 CI 스모크 테스트에서 grep 하는 방식을 채택 (알림 파이프라인 Wiring Rule 과 동일 패턴). 이 grep 스모크는 Commit D 에 포함.

## 15. 커밋별 게이트 체크리스트 (Commit Gate Checklist)

각 커밋은 아래 체크리스트를 PR 본문에 체크 상태로 포함한다. CI 녹색 ≠ 안전 — 수동 확인 항목은 별도 캡처를 첨부한다.

### 15.A Commit A 게이트

- [ ] `python scripts/check_doc_sync.py --verbose` → 0 errors + 0 warnings
- [ ] 본 문서의 §0 개정 이력에 v0.3 엔트리 존재
- [ ] `security-integrity-roadmap.md` §9 에서 본 문서 v0.3 참조 업데이트
- [ ] 코드 변경 zero-diff (`git diff --stat` 에 `.py`/`.toml`/`.sh`/`Dockerfile*`/`.github/workflows/*.yml` 없음)

### 15.B Commit B 게이트

- [ ] `cd backend && python -m ruff check . --config pyproject.toml` → 0 errors
- [ ] `cd backend && python -m black --check . --config pyproject.toml` → 0 errors
- [ ] `cd backend && python -m pytest tests/test_trading_guard_redis_store.py -v` → 신규 14 건 전수 PASS
- [ ] `cd backend && python -m pytest tests/ -q --tb=short` → 전체 PASS (타임아웃 600s)
- [ ] `python scripts/check_bool_literals.py` → 0 errors + 0 warnings
- [ ] `python scripts/check_loguru_style.py` → 0 errors + 0 warnings
- [ ] `pip-audit` → 0 HIGH+ (fakeredis 추가 시에만)
- [ ] `TradingGuard` 는 이 커밋에서 변경되지 않음 (`git diff backend/core/trading_guard.py` → empty)

### 15.C Commit C 게이트

- [ ] Commit B 의 모든 게이트 재확인 (회귀 방지)
- [ ] `python scripts/check_rbac_coverage.py` → 0 errors (`system.py` 수정 후)
- [ ] `python scripts/check_loguru_style.py` → 0 errors (신규 logger 호출 포함)
- [ ] `python scripts/check_bool_literals.py` → 0 errors + 0 warnings
- [ ] `python scripts/check_doc_sync.py --verbose` → 0 errors + 0 warnings
- [ ] `cd backend && python -m pytest tests/test_trading_guard.py -v` → 신규 10건 PASS
- [ ] `cd backend && python -m pytest tests/test_kill_switch_routes.py -v` → 기존 9건 + 신규 4건 전수 PASS
- [ ] `cd backend && python -m pytest tests/ -q --tb=short` → 전체 PASS (타임아웃 600s)
- [ ] **수동**: `docker compose up -d backend scheduler redis` 후 `docker compose logs backend --tail=100 | grep "TradingGuard wired"` → 1건 출력
- [ ] **수동**: `docker compose logs scheduler --tail=100 | grep "TradingGuard wired"` → 1건 출력
- [ ] **수동**: `curl -s http://localhost:8000/metrics | grep aqts_trading_guard` → 지표 6종 노출
- [ ] **수동**: `docker exec aqts-redis redis-cli HGETALL aqts:trading_guard:state` → hash 존재 + seq > 0
- [ ] **수동**: Prometheus gauge `TRADING_GUARD_KILL_SWITCH_ACTIVE` 가 backend/scheduler 양쪽에서 동일 값

### 15.D Commit D 게이트

- [ ] Commit C 의 모든 게이트 재확인
- [ ] `cd backend && python -m pytest tests/integration/test_trading_guard_cross_process.py -v --tb=short` → 신규 9건 PASS (타임아웃 300s)
- [ ] `aqts_trading_guard_reconcile_total{reason="seq_gap"}` 이 seq gap 테스트 후 증가 확인
- [ ] `aqts_trading_guard_pubsub_latency_seconds` histogram 에 샘플 존재
- [ ] 통합 테스트 러너 시간 관측값을 기록 (다음 CI 타임아웃 조정 근거)

### 15.E Commit E 게이트 (문서 + 재개)

- [ ] `python scripts/check_doc_sync.py --verbose` → 0 errors + 0 warnings
- [ ] `trading-halt-policy.md` 버전 v1.1 → v1.2
- [ ] `midday-check-path-a-runbook.md` §9 체크리스트 5건 전수 PASS 증거 첨부 후에만 SUSPENDED 해제 (v1.0.2 → v1.1)
- [ ] `security-integrity-roadmap.md` §9 에 "P0-5 Redis 이전 완료" 엔트리 append
- [ ] `phase1-demo-verification-2026-04-11.md` §10.18 에 재예행 결과 append (증거표 포함)
- [ ] CLAUDE.md "문서-only 커밋" 예외 적용 — 전체 pytest 생략 가능 (단 `ruff`/`black`/`check_bool_literals`/`check_doc_sync` 는 실행)
- [ ] **운영책임자 최종 승인** 기록 (Path A 재개 결정은 별건 승인)

---

§9 "검증 체크리스트 (SUSPENDED 해제 전)" 은 Commit E 의 전제 조건이다 — 그 5 개 항목이 PASS 되지 않으면 Commit E 의 `midday-check-path-a-runbook.md` v1.1 재개는 실행하지 않는다.

## 16. 관련 문서

- `docs/operations/phase1-demo-verification-2026-04-11.md` §10.17, §10.18
- `docs/operations/trading-halt-policy.md` v1.1 §3.5
- `docs/operations/midday-check-path-a-runbook.md` v1.0.2 (SUSPENDED)
- `docs/security/security-integrity-roadmap.md` §3.6.5 (P0-5 장애 정책), §9 (진행 기록)
- `docs/architecture/notification-pipeline.md` (Wiring Rule 참조)
- `backend/core/idempotency/order_idempotency.py` (Redis fail-closed 패턴 참조 구현)
- `backend/main.py` (lifespan wiring 포인트), `backend/scheduler_main.py` `main()` (scheduler wiring 포인트)
- `CLAUDE.md` (정적 검사기 / Wiring Rule / SSH heredoc / 하드코딩 금지 / 환경변수 bool 표기 규칙)
