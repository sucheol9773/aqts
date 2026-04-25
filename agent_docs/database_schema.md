# AQTS Data Store Reference

> AQTS 는 **세 개의 저장소**(PostgreSQL, MongoDB, Redis)를 동시에 사용합니다. 각 저장소의 역할·스키마 진입점·주요 키/컬렉션/테이블을 정리합니다. 운영·변경 규칙(키 일관성, KST 통일, Wiring Rule)은 [development-policies.md](./development-policies.md) 를 우선 참조합니다.
>
> 본 문서의 모든 실제 값은 `.env` 가 아닌 `backend/config/settings.py` + `.env.example` 의 키 이름만 인용하며, 실값은 절대 포함하지 않습니다 (imported_knowledge custom_instructions).

---

## 1. 연결 스택 (`backend/db/database.py`)

| 저장소 | 라이브러리 | 엔진/세션 |
|---|---|---|
| PostgreSQL (TimescaleDB) | SQLAlchemy 2.x async + asyncpg | `engine = create_async_engine(settings.db.async_url, pool_size=20, max_overflow=10, pool_pre_ping=True, pool_recycle=3600)` |
| MongoDB | Motor (`motor.motor_asyncio.AsyncIOMotorClient`) | Async 클라이언트 |
| Redis | `redis.asyncio.Redis` (aioredis 계열) | |

FastAPI 의존성 주입 세션 공급자는 `backend/db/database.py` 의 `get_db_session()` 제너레이터이며, 예외 시 `rollback`, 정상 종료 시 `commit` 한다.

`.env.example` 에서 설정 키:

```
DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, DB_POOL_SIZE, DB_MAX_OVERFLOW
MONGO_HOST, MONGO_PORT, MONGO_DB, MONGO_USER, MONGO_PASSWORD
REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, REDIS_DB
```

실값은 `.env` 에만 존재하며, 본 문서·커밋·팀 프롬프트에 포함되지 않는다.

---

## 2. PostgreSQL — 트레이딩 원장·RBAC·감사

### 2.1 Alembic 리비전 (`backend/alembic/versions/`)

현재 **9 개** 리비전이 `001 → 002 → … → 009` 선형 체인으로 이어진다.

| Rev | 파일 | 요약 |
|---|---|---|
| 001 | `001_initial_schema.py` | 시장 데이터·사용자 프로필·유니버스·포트폴리오·주문·알림·감사·백테스트 등 18개 테이블 생성 |
| 002 | `002_rbac_users.py` | `roles`, `users` 테이블 추가 (RBAC) |
| 003 | `003_order_idempotency_keys.py` | `order_idempotency_keys` 테이블 (중복 주문 방지) |
| 004 | `004_user_role_version.py` | `users.role_version` INTEGER NOT NULL DEFAULT 0 컬럼 추가 (JWT `rv` 클레임 ↔ DB 대조로 역할 변경 즉시 세션 무효화) |
| 005 | `005_portfolio_positions.py` | `portfolio_positions` 테이블 (`PortfolioLedger` 영속 계층) |
| 006 | `006_rebalancing_history.py` | `rebalancing_history` 테이블 (`RebalancingEngine` / `EmergencyRebalancingMonitor` 가 INSERT/SELECT 하던 스키마-코드 불일치 해소) |
| 007 | `007_user_profiles_universe_columns.py` | `user_profiles.user_id` 컬럼 + `uq_user_profiles_user_id` + `idx_user_profiles_user_id` 인덱스; `universe.market_cap`, `universe.avg_daily_volume` 컬럼 |
| 008 | `008_orders_extended_fields.py` | `orders` 테이블에 감사 체인·슬리피지·거래 비용 컬럼 추가 (`slippage_bps`, `commission`, `decision_id`, `strategy_id`, `submitted_at`, `reason`) + 인덱스 2건 |
| 009 | `009_strategy_execution_logs.py` | `strategy_execution_logs` 테이블 (전략 앙상블 실행 이력: 레짐·신호·게이트 결과·실행 상태) |

Alembic 실행 패턴 (development-policies.md §15 회귀 사례 2):

```bash
docker compose -f docker-compose.yml run --rm -T backend \
  alembic -c alembic.ini upgrade head </dev/null
```

### 2.2 001 에서 생성된 18 개 테이블

`grep -A1 "op.create_table(" backend/alembic/versions/001_initial_schema.py` 기준 이름 추출:

1. `market_ohlcv`
2. `economic_indicators`
3. `financial_statements`
4. `exchange_rates`
5. `user_profiles`
6. `universe`
7. `portfolio_holdings`
8. `portfolio_snapshots`
9. `orders`
10. `alerts`
11. `audit_logs`
12. `business_calendars`
13. `backtest_results`
14. `strategy_weights`
15. `sentiment_scores`
16. `investment_opinions`
17. `ensemble_signals`
18. `weight_update_history`

### 2.3 ORM 모델 (`backend/db/models/`)

현재 코드베이스에 ORM 클래스로 명시된 테이블은 제한적이며, 대부분은 alembic 스키마 + 원시 SQL/Motor 로 접근한다.

| 파일 | 클래스 | 테이블 | 참고 |
|---|---|---|---|
| `backend/db/models/user.py` | `Role` | `roles` | `id`, `name(unique)`, `description` |
| `backend/db/models/user.py` | `User` | `users` | `id (UUID)`, `username (unique, indexed)`, `email (unique, nullable)`, `password_hash`, `role_id (FK)`, `role_version (NOT NULL DEFAULT 0)`, `is_active`, `is_locked`, `failed_login_attempts`, `totp_secret (nullable, Text)`, `totp_enabled`, `created_at`, `updated_at`, `last_login_at (nullable)` |
| `backend/db/models/portfolio_position.py` | `PortfolioPosition` | `portfolio_positions` | `ticker (PK, String(32))`, `quantity (Float, >0 CHECK ck_portfolio_positions_quantity_positive)`, `updated_at (server_default NOW())` |
| `backend/db/models/order.py` | `Order` | `orders` | 001 원본 컬럼(`id`, `order_id`, `ticker`, `market`, `side`, `order_type`, `quantity`, `price`, `filled_quantity`, `filled_price`, `status`, `trigger_type`, `created_at`, `filled_at`, `error_message`) + 008 확장(`slippage_bps`, `commission`, `decision_id (indexed)`, `strategy_id (indexed)`, `submitted_at`, `reason`) |
| `backend/db/models/strategy_execution_log.py` | `StrategyExecutionLog` | `strategy_execution_logs` | `id (BigInt PK)`, `ticker`, `strategy_name`, `regime`, `regime_confidence`, `ensemble_signal`, `ensemble_confidence`, `weights_used (JSON text)`, `final_action`, `signals_generated`, `orders_submitted`, `gate_results (JSON text)`, `status`, `execution_duration_ms`, `error_message`, `executed_at` |

### 2.4 리포지토리 (`backend/db/repositories/`)

| 파일 | 담당 테이블 |
|---|---|
| `audit_log.py` | `audit_logs` |
| `portfolio_positions.py` | `portfolio_positions` |

리포지토리가 존재하지 않는 테이블은 `backend/core/**` 에서 직접 SQL/ORM 으로 접근하거나 Motor(MongoDB) 로 접근한다.

### 2.5 중요한 스키마 규칙

- **`portfolio_positions.quantity > 0` CHECK**: 0 수량 포지션은 row 자체를 DELETE 로 제거한다 (`backend/db/models/portfolio_position.py`).
- **`users.role_version` 단조 증가**: JWT `rv` 클레임과 비교해 다르면 `get_current_user` 가 401 재로그인을 요구한다 (004 revision).
- **`order_idempotency_keys`** 는 Redis idempotency 캐시(`backend/core/idempotency/order_idempotency.py`) 와 이중화되어 있다. DB 저장 구현은 `backend/core/idempotency/db_store.py` 를 참조한다.
- **`audit_logs`** 는 `fail-closed` 원칙으로 운영된다. 기록 실패 시 상위 동작은 차단한다 (development-policies.md §8.2 "audit fail-closed" 경로).

---

## 3. MongoDB — 알림·분석·비정형 저장

Motor 클라이언트는 `backend/db/database.py` 에서 생성되고 런타임에 각 도메인 모듈로 주입된다.

### 3.1 주요 컬렉션 (코드 기반 추론)

| 컬렉션 용도 | 주입 지점 | 참조 파일 |
|---|---|---|
| 알림 (`alerts`) — 상태 머신 대상 | `AlertManager(mongo_collection=...)` | `backend/core/notification/alert_manager.py:226` |
| 의사결정 원장 (`decision_records`) — 7-step audit trail | `DecisionRecordStore` | `backend/core/audit/decision_record.py` |
| AI 분석 캐시 / 감성 / 의견 | `CACHE_PREFIX` 기반 키 | `backend/core/ai_analyzer/sentiment.py:275-288`, `backend/core/ai_analyzer/opinion.py:191-441` |
| 백테스트 결과 비정형 아티팩트 | 엔진 산출물 | `backend/core/backtest_engine/engine.py`, `docs/backtest/` |
| 뉴스 / 공시 원시 문서 | 수집 어댑터 | `backend/core/data_collector/` (DART, Reddit) |

### 3.2 `AlertManager` 상태 머신

development-policies.md §14 의 **레이어 1** 에 해당. 주요 메서드의 Mongo 접근 지점:

- `backend/core/notification/alert_manager.py:293` `save_alert` → `self._collection.insert_one`
- `backend/core/notification/alert_manager.py:408-417` `list_alerts` — `find().sort("created_at", -1).skip(offset).limit(limit)`
- `backend/core/notification/alert_manager.py:434-435` `get_alert` — `find_one({"id": alert_id})`
- `backend/core/notification/alert_manager.py:444-445` 미확인 개수 — `count_documents({"status": {"$ne": READ}})`
- `backend/core/notification/alert_manager.py:452-501` `mark_read` / `mark_sent` / `mark_failed` — `update_one` / `update_many`
- `backend/core/notification/alert_manager.py:523-611` `claim_for_sending`, `requeue_*` (재시도 상태 전이)
- `backend/core/notification/alert_manager.py:703-744` `dispatch_retriable_alerts`
- `backend/core/notification/alert_manager.py:883-888` 총계·레벨별 통계

**싱글톤**: `api.routes.alerts._alert_manager` 가 모듈 import 시점에 생성되며, `backend/main.py:132, 144, 496` 에서 참조된다. `NotificationRouter wired` 로그(backend/main.py:157) 가 lifespan 에 출력되지 않으면 noop 분기(§14.4 Commit 2 회고) 가 활성화된다.

### 3.3 인덱스 (운영 권장)

현재 코드에서 명시 생성이 확인되는 Mongo 인덱스는 제한적이다. 운영에서 권장되는 인덱스:

- `alerts.created_at` DESC (list_alerts 정렬)
- `alerts.id` unique (조회 키)
- `alerts.status` (unread 카운트)
- `alerts.level` (레벨별 집계)

인덱스를 신규 추가할 경우 **Wiring Rule**(§5) 대로 생성·호출 경로·로그 출력을 검증한다.

---

## 4. Redis — 상태·스냅샷·idempotency

### 4.1 대표 키 네임스페이스

| 네임스페이스 | 용도 | 참조 |
|---|---|---|
| `stock:<ticker>`, `sector:<name>`, `macro:market` | AI 분석 결과 캐시 | `backend/core/ai_analyzer/opinion.py:191, 248, 304` |
| `<CACHE_PREFIX><ticker>` | 감성 점수 캐시 | `backend/core/ai_analyzer/sentiment.py:275-288` |
| `<CACHE_PREFIX><prompt_type>:active` | 프롬프트 활성 버전 | `backend/core/ai_analyzer/prompt_manager.py:350-363` |
| idempotency (`user_id`, `route`, `key`) → `_build_key` | 중복 주문 방지 | `backend/core/idempotency/order_idempotency.py:186-228` |
| TradingGuard 상태 | 일일 손실/낙폭/연속손실 카운터 | `backend/core/trading_guard.py` + `docs/security/trading-guard-redis-migration.md` |
| 스케줄러 스냅샷 | `today_kst_str()` 기반 일일 키 | `backend/core/scheduler_handlers.py` (development-policies.md §8.3 회귀) |
| 스케줄러 heartbeat | 장애 감시 | `backend/core/scheduler_heartbeat.py` |
| 스케줄러 idempotency | 중복 실행 방지 | `backend/core/scheduler_idempotency.py` |

### 4.2 키 규칙

- **일일 키는 반드시 `today_kst_str()` 사용** — `datetime.now(timezone.utc).strftime("%Y-%m-%d")` 혼용 금지 (development-policies.md §8.3).
- **새 키 추가 시 생성 지점과 조회 지점 모두 grep 으로 전수 확인**하여 silent miss 를 방지한다 (development-policies.md §8.4).
- **테스트 fixture 키도 프로덕션 키와 동일한 함수로 생성**한다. UTC/KST 혼용 금지.

### 4.3 Idempotency 이중화

주문 idempotency 는 Redis + Postgres 이중 저장이다:

- Redis 경로: `backend/core/idempotency/order_idempotency.py:186-228` (`_build_key(user_id, route, key)` → TTL 있는 redis_key)
- Postgres 경로: `backend/core/idempotency/db_store.py:123` (`order_idempotency_keys` 테이블, alembic 003)

두 저장소가 일치하지 않으면 `reconcile mismatch` 로 critical 로그가 발생한다. 키 로직 변경 시 양쪽 모두 변경해야 한다 (development-policies.md §8.2).

---

## 5. 백업·복구

- 스크립트: `backend/scripts/backup_db.sh`, `restore_db.sh`, `backup_cron.sh`
- Compose 서비스: `db-backup`
- 절차·운영 런북: `docs/operations/` 하위의 백업/복구 관련 문서 (필요 시 확장)

---

## 6. 신규 스키마 변경 워크플로

1. `backend/alembic/versions/NNN_<name>.py` 에 upgrade/downgrade 작성.
2. SQL 모델이 있으면 `backend/db/models/` 에 추가, 리포지토리는 `backend/db/repositories/` 에 추가.
3. 관련 코드 경로(INSERT/SELECT) 를 모두 수정 — 스키마-코드 불일치 시 006 과 같은 `ProgrammingError` 회귀가 재발한다.
4. 테스트 추가 (`backend/tests/`) — **단위 + 통합** (development-policies.md §5 Wiring Rule).
5. 문서 갱신 — 본 문서의 표 + 필요 시 `docs/security/` 관련 문서.
6. 커밋 전 필수 검증 (development-policies.md §3): ruff / black / pytest + 해당 마이그레이션의 up/down 실제 실행.

---

## 문서 소유권

- 스키마 확장·변경 시 반드시 이 문서와 [architecture.md](./architecture.md) 를 동시에 갱신한다.
- 변경 규칙(키 일관성·Wiring Rule)은 [development-policies.md](./development-policies.md) 에만 존재하며, 본 문서는 이를 참조할 뿐 재정의하지 않는다.
