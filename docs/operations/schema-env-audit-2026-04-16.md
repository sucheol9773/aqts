# 스키마 · DB · 환경변수 정리 감사 (2026-04-16)

## 0. 배경과 감사 범위

TradingGuard Redis 이행(`docs/security/trading-guard-redis-migration.md` v0.3)의 Commit B 착수 전, 최근 반복적으로 발견된 "정의 ≠ 적용" 드리프트가 데이터·설정 계층에 잠복해 있는지 직접 점검한다. 이번 감사는 다음 4개 계층을 관찰 우선 원칙으로 전수 확인한다.

1. PostgreSQL 스키마 (Alembic 마이그레이션 7개 파일)
2. SQLAlchemy ORM 모델 (`backend/db/models/`)
3. Redis 키/채널 네임스페이스
4. 환경변수 `.env.example` ↔ `backend/config/settings.py` ↔ 코드 직접 read ↔ `docker-compose.yml`

**선행 작업 참조**: `docs/operations/schema-code-mismatch-fix-2026-04-13.md` — PostgreSQL 스키마와 raw SQL 쿼리 전수 비교에서 HIGH 4건 수정 완료. 본 문서는 그 이후의 잔여 드리프트와 `.env`/Redis 계층을 추가 감사한다.

## 1. 감사 방법론과 서브에이전트 거짓양성 주의

본 감사 1차 패스(서브에이전트 자동 수집)에서 다음 3건의 CRITICAL 보고가 모두 거짓양성으로 확인됐다. 최종 보고에서 제거하고 기록으로 남긴다.

| 서브에이전트 주장 | 실제 관찰 | 거짓양성 원인 |
|---|---|---|
| `AQTS_REVOCATION_BACKEND` 가 `.env.example` 에 없음 | `.env.example:138` 에 `AQTS_REVOCATION_BACKEND=redis` 존재 | grep 범위 실수 |
| `ensemble:latest:_summary` Redis TTL 미설정 | `backend/core/scheduler_handlers.py:947,956` 에서 `pipe.set(..., ex=86400)` 로 24h TTL 설정됨 | `redis.set()` 만 grep 하고 `ex=` kwarg 확인 누락 |
| `rebalancing:status:{task_id}` Redis TTL 미설정 | `backend/api/routes/system.py:197-200` 에서 `ex=REBALANCING_STATUS_TTL=86400` 으로 24h TTL 설정됨 | 동일 사유 |

**교훈**: 감사 파이프라인의 자동 수집 결과도 "정의 ≠ 적용" 패턴에서 자유롭지 않다. 보고된 드리프트는 반드시 파일:라인을 직접 읽어 검증한 뒤에만 수정 대상으로 승격한다. 이 회귀 패턴은 CLAUDE.md 의 "오류 수정 시 관찰 우선 원칙" 이 감사 도구 자체에도 적용됨을 의미한다.

## 2. PostgreSQL 스키마 현황 (확정)

### 2.1 테이블 목록 (Alembic 001~007 기준)

총 **23개 테이블** 정의. 상세 컬럼·제약·인덱스는 `backend/alembic/versions/*.py` 원본 참조.

| 마이그레이션 | 테이블 | 특이사항 |
|---|---|---|
| 001 | `market_ohlcv`, `economic_indicators`, `exchange_rates`, `portfolio_snapshots`, `audit_logs`, `sentiment_scores`, `investment_opinions`, `ensemble_signals` | TimescaleDB 하이퍼테이블(8개) |
| 001 | `financial_statements`, `user_profiles`, `universe`, `portfolio_holdings`, `orders`, `alerts`, `business_calendars`, `backtest_results`, `strategy_weights`, `weight_update_history` | 일반 테이블(10개) |
| 002 | `roles`, `users` | RBAC 기초 |
| 003 | `order_idempotency_keys` | BRIN 인덱스 (`ix_order_idempotency_expires_at_brin`) |
| 004 | `users.role_version` 컬럼 추가 | 세션 무효화용 |
| 005 | `portfolio_positions` | PortfolioLedger — `CHECK(quantity > 0)` |
| 006 | `rebalancing_history` | 2026-04-13 에 누락 발견 후 정식 추가 (HIGH #4 수정분) |
| 007 | `user_profiles.user_id` / `universe.market_cap` / `universe.avg_daily_volume` 컬럼 추가 | 프로필·유니버스 확장 |

### 2.2 SQLAlchemy ORM 모델 커버리지

모델 정의: **3개** (`Role`, `User`, `PortfolioPosition`) — `backend/db/models/user.py` + `backend/db/models/portfolio_position.py`.

**나머지 20개 테이블은 raw SQL(asyncpg) 로 접근**한다. 이는 드리프트가 아닌 **의도된 아키텍처 선택**이다. 근거:

1. `backend/core/portfolio_manager/profile.py`, `backend/core/portfolio_manager/universe.py`, `backend/core/order_executor/*`, `backend/core/emergency_monitor.py`, `backend/core/strategy_ensemble/runner.py`, `backend/core/data_collector/*`, `backend/core/scheduler_handlers.py` 가 모두 `asyncpg` 의 `fetch/execute/copy_records_to_table` 로 직접 질의.
2. TimescaleDB 하이퍼테이블 8개는 ORM 추상화로는 `create_hypertable()` 호출을 표현하기 어렵고, `INSERT ... ON CONFLICT` + BRIN 인덱스 활용이 raw SQL 에서 더 명확.
3. ORM 가 관리하는 영역(인증·포트폴리오 포지션 레저)과 raw SQL 가 관리하는 영역(시계열·이벤트 로그·배치 데이터)은 의도적으로 분리.

### 2.3 선행 감사(2026-04-13)에서 해소된 스키마-코드 불일치 4건

`docs/operations/schema-code-mismatch-fix-2026-04-13.md` 참조:

| # | 대상 | 상태 |
|---|---|---|
| 1 | `financial_statements` INSERT/SELECT 컬럼 매핑 | ✅ 수정 완료 (DART API → DB 컬럼 매핑 레이어 추가) |
| 2 | 존재하지 않는 `company_info` 테이블 참조 | ✅ 수정 완료 |
| 3 | 존재하지 않는 `positions` 테이블 → `portfolio_holdings` | ✅ 수정 완료 |
| 4 | 누락된 `rebalancing_history` 테이블 → Alembic 006 로 추가 | ✅ 수정 완료 |

본 감사 시점(2026-04-16) 기준으로 raw SQL ↔ 스키마 불일치는 **신규 발견 없음**. 다만 Alembic 이후 마이그레이션이 추가될 때마다 `schema-code-mismatch-fix-2026-04-13.md` §수정 검증 절차를 반복 실행해야 한다.

## 3. Redis 키·채널 카탈로그 (검증됨)

### 3.1 센트럴라이즈된 키 프리픽스 (`aqts:*`)

| 키 패턴 | 프리픽스 상수 | 파일:라인 | TTL | 값 형식 |
|---|---|---|---|---|
| `aqts:order_idem:{user}:{route}:{key}` | `KEY_PREFIX` | `core/order_idempotency.py:73` | 30s (claim) / 24h (result) | JSON: `{fingerprint, status_code, body, created_at}` |
| `aqts:ensemble_weights:{risk_profile}` | `WEIGHT_CACHE_PREFIX` | `core/strategy_ensemble/engine.py:126` | 86400s | JSON: `{FACTOR, MEAN_REVERSION, ...}` |
| `aqts:sentiment:{ticker}` | `CACHE_PREFIX` | `core/ai_analyzer/sentiment.py:106` | `ANTHROPIC_CACHE_TTL` (4h default) | JSON: `{score, confidence, positive_factors, negative_factors}` |
| `aqts:opinion:{cache_key}` | `CACHE_PREFIX` | `core/ai_analyzer/opinion.py:160` | `ANTHROPIC_CACHE_TTL` (4h default) | JSON: `{action, conviction, target_weight, ...}` |
| `aqts:prompt:{prompt_type}:active` | `CACHE_PREFIX` | `core/ai_analyzer/prompt_manager.py:91` | `ANTHROPIC_CACHE_TTL` (4h default) | JSON: `{prompt_type, content, version, ...}` |
| `aqts:revoked:{jti}` | `key_prefix` (생성자 인자) | `api/middleware/token_revocation.py:110` | 토큰 남은 TTL (dynamic) | String `"1"` |

### 3.2 센트럴라이즈된 키 프리픽스 (도메인 키, `aqts:` 미사용)

| 키 패턴 | 프리픽스 상수 | 파일:라인 | TTL | 값 형식 |
|---|---|---|---|---|
| `scheduler:executed:{date}:{event}` | `KEY_PREFIX` | `core/scheduler_idempotency.py:56` | 86400s | String `"true"` |
| `economic_indicator:{name}` | `CACHE_PREFIX` | `core/data_collector/economic_collector.py:642` | `CACHE_TTL` (7d typical) | JSON |
| `rebalancing:status:{task_id}` | `REBALANCING_STATUS_PREFIX` | `api/routes/system.py:48` | `REBALANCING_STATUS_TTL=86400` | JSON: `{task_id, status, updated_at, ...}` |
| `rebalancing:lock` | `REBALANCING_LOCK_KEY` | `api/routes/system.py` | `REBALANCING_LOCK_TTL` | String (lock payload) |

### 3.3 인라인 키 패턴 (상수화 미비 — 구조적 개선 여지)

다음 키들은 `f"..."` 문자열이 여러 파일에 산재되어 있어, 네이밍 변경 시 전수 grep 누락 위험이 있다.

| 키 패턴 | 사용 위치 | TTL | 개선 제안 |
|---|---|---|---|
| `ensemble:latest:{ticker}` / `ensemble:latest:_summary` | `core/scheduler_handlers.py:946, 950` (write)<br>`core/scheduler_handlers.py:275`, `api/routes/ensemble.py:52,91`, `api/routes/system.py:377` (read) | `ex=86400` | 프리픽스 상수(`ENSEMBLE_LATEST_PREFIX = "ensemble:latest:"`)를 `core/strategy_ensemble/engine.py` 에 centralize |
| `portfolio:snapshot:{date}` | `core/scheduler_handlers.py:471, 485, 680` | 86400s | 프리픽스 상수(`PORTFOLIO_SNAPSHOT_PREFIX`)를 centralize |
| `report:daily:{date}` | `core/scheduler_handlers.py:844` | 86400s | 프리픽스 상수(`DAILY_REPORT_PREFIX`)를 centralize |

### 3.4 TradingGuard Redis 이행과의 관계

Commit B 에서 도입될 새 키/채널은 **모두 `aqts:` 프리픽스 규약을 준수**할 계획이다 (v0.3 §4.1 참조).

- `aqts:trading_guard:state` (hash)
- `aqts:trading_guard:seq` (integer counter)
- `aqts:trading_guard:state_change` (pub/sub channel)

인라인 키 3종의 상수화는 별도 후속 리팩터링 (Commit E 이후) 로 분리한다.

## 4. 환경변수 드리프트 (검증된 실제 드리프트)

### 4.1 방법론

1. `.env.example` 선언 키 추출: `^[A-Z_]+=` 정규식으로 78건 수집.
2. 코드 read 수집:
   - `os.environ.get(...)` / `os.getenv(...)` 호출 AST 스캔
   - `env_bool(...)` 호출 스캔
   - `pydantic.BaseSettings` 서브클래스의 `model_config.env_prefix` + 필드 이름 조합
   - `Field(alias="...")` 리터럴
3. `docker-compose.yml` 의 `${VAR}`/`${VAR:-...}`/`${VAR:?...}` 참조 수집.
4. 3자 비교로 드리프트 계산.

### 4.2 Category A — `.env.example` 에 있으나 코드에서 전혀 읽지 않음 (10건)

이 키들은 `.env.example` 에 있지만 `backend/` 전체에서 어떤 경로로도 참조되지 않는다. 복사해 설정하는 운영자에게 "뭔가 의미 있는 설정"처럼 보이지만 실제 효과가 없다.

| 키 | 추정 도입 맥락 | 권장 조치 |
|---|---|---|
| `BACKTEST_DEFAULT_START_DATE` | 초기 백테스트 엔진 설계 잔재 | 제거 |
| `BACKTEST_DEFAULT_END_DATE` | 동상 | 제거 |
| `BACKTEST_DEFAULT_INTERVAL` | 동상 | 제거 |
| `DATA_CACHE_DIR` | 파일시스템 캐시 계획 잔재 (현재는 Redis) | 제거 |
| `RESULTS_DIR` | 초기 파일 기반 리포트 잔재 | 제거 |
| `LOG_DIR` | 현재는 stdout + docker logs | 제거 |
| `DEBUG` | 사용처 없음 (LOG_LEVEL 이 역할 대체) | 제거 |
| `DOCKER_IMAGE_NAME` | 초기 단일 이미지 설계 잔재 | 제거 |
| `DOCKER_CONTAINER_NAME` | 동상 | 제거 |
| `WORKER_COUNT` | uvicorn worker 수 — 현재는 compose 에서 고정 | 제거 또는 활성화 결정 |

### 4.3 Category B — 코드에서 읽히는데 `.env.example` 에 없음 (14건)

**서브카테고리 B1: pydantic 필드 (자동 read) 인데 `.env.example` 누락**

| 키 | 클래스:필드 | 기본값 |
|---|---|---|
| `KIS_TOKEN_RETRY_COUNT` | `KISSettings.token_retry_count` | `settings.py:115` |
| `KIS_TOKEN_RETRY_MAX_WAIT` | `KISSettings.token_retry_max_wait` | `settings.py:119` |
| `KIS_WS_INSECURE_ALLOW` | `KISSettings.ws_insecure_allow` | `settings.py:127` |
| `KIS_WS_EXCEPTION_TICKET` | `KISSettings.ws_exception_ticket` | `settings.py:131` |
| `KIS_WS_EXCEPTION_EXPIRES_AT` | `KISSettings.ws_exception_expires_at` | `settings.py:135` |
| `DASHBOARD_ACCESS_TOKEN_EXPIRE_HOURS` | `DashboardSettings.access_token_expire_hours` | `settings.py:427` |
| `DASHBOARD_REFRESH_TOKEN_EXPIRE_DAYS` | `DashboardSettings.refresh_token_expire_days` | `settings.py:428` |
| `DASHBOARD_PREVIOUS_SECRET_KEY` | `DashboardSettings.previous_secret_key` | `settings.py:423` — JWT 롤링 키 |

**서브카테고리 B2: `os.environ.get` / `os.getenv` / `env_bool` 직접 read (6건)**

| 키 | 파일:라인 | 기본값 |
|---|---|---|
| `ALERT_RETRY_LOOP_ENABLED` | `main.py:166` (env_bool) | `True` |
| `KIS_RECOVERY_COOLDOWN_SECONDS` | `main.py:242` (os.environ.get) | `75` |
| `KIS_STARTUP_JITTER_MAX_SECONDS` | `main.py:269` (os.environ.get) | `15.0` |
| `OTEL_ENABLED` | `core/monitoring/tracing.py:24` (env_bool) | `False` |
| `SCHEDULER_HEARTBEAT_PATH` | `core/scheduler_heartbeat.py:28` | `/tmp/scheduler.heartbeat` |
| `SCHEDULER_HEARTBEAT_STALE_SECONDS` | `core/scheduler_heartbeat.py:29` | `180` |

### 4.4 Category C — `docker-compose.yml` 에 참조되는데 `.env.example` 에 없음 (9건)

| 키 | 사용처 | 기본값 (compose 내) | 영향 |
|---|---|---|---|
| `IMAGE_TAG` | `backend.image`, `scheduler.image` | `latest` | compose parse 는 기본값으로 진행하나, 운영에서는 `sha-<digest>` 로 pin 해야 배포 추적 가능. 운영 `.env` 필수. |
| `BACKUP_INTERVAL_HOURS` | `db-backup` 서비스 | `24` | 기본값 작동은 하지만 운영자에게 존재 자체가 불투명 |
| `BACKUP_RETENTION_DAYS` | `db-backup` 서비스 | `7` | 동상 |
| `GCS_BACKUP_BUCKET` | `db-backup` 서비스 | `""` (미설정 시 로컬 백업 전용) | GCP 업로드 기능 활성화 스위치 — 운영자가 알 방법이 없음 |
| `GRAFANA_USER` | `grafana.GF_SECURITY_ADMIN_USER` | `admin` | `.env.example` 에 `GRAFANA_PASSWORD` 는 있는데 USER 가 없음 |
| `GRAFANA_PORT` | `grafana.ports` | `3000` | 운영 포트 변경 시 `.env` 필요 |
| `PROMETHEUS_PORT` | `prometheus.ports` | `9090` | 동상 |
| `ALERTMANAGER_PORT` | `alertmanager.ports` | `9093` | 동상 |
| `JAEGER_UI_PORT` | `jaeger.ports` | `16686` | 동상 |

### 4.5 Category D — 구조적 드리프트: RL 모듈의 DB 접속 키 분기

`backend/core/rl/data_loader.py:273-277` 가 다음 5개 키를 독자적으로 읽는다.

```python
host = os.getenv("POSTGRES_HOST", "localhost")
port = os.getenv("POSTGRES_PORT", "5432")
user = os.getenv("POSTGRES_USER", "aqts")
password = os.getenv("POSTGRES_PASSWORD", "aqts")
db = os.getenv("POSTGRES_DB", "aqts")
```

반면 나머지 백엔드 전체는 `DatabaseSettings` (env_prefix=`DB_`) 를 통해 `DB_HOST`/`DB_PORT`/`DB_USER`/`DB_PASSWORD`/`DB_NAME` 을 읽는다.

**왜 문제인가**:

1. 두 경로는 같은 PostgreSQL 인스턴스를 가리켜야 하지만, 키 자체가 다르므로 자격증명 드리프트가 발생할 수 있다.
2. `.env.example` 에는 `DB_*` 만 선언돼 있어, RL 모듈이 기본값(`localhost/aqts/aqts/aqts`) 으로 fallback 하면 프로덕션 DB 에 연결 실패.
3. 이 결함은 "RL 데이터 로더를 독립 CLI 로 실행"하는 시나리오가 사라졌으므로 현재 제품 흐름에서는 발화하지 않고 있다 — 전형적인 silent miss 후보.

**권장 조치**: `data_loader.py` 의 키를 `DB_*` 로 통일하고, 공통 `DatabaseSettings` 를 재사용한다. 기본값은 제거하고 환경변수 부재 시 ValueError 승격.

**상태 (2026-04-16 해결)**: `_build_db_url()` 을 `DatabaseSettings().sync_url` 로 치환하여 단일 진실원천을 공유하도록 수정. `DB_PASSWORD` 미설정 시 `ValidationError` 로 승격되며 (required Field), 이는 비밀번호 없는 배포를 차단하는 의도된 게이트. 테스트 3건 (`test_build_db_url_with_env`, `test_build_db_url_defaults`, `test_build_db_url_requires_password`) 으로 wiring 고정.

### 4.6 Category E — 운영에만 쓰이는데 `.env.example` 에 없는 알림 키 (보조)

`TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` 는 `.env.example` 에 있고 `TelegramSettings` 로 read 된다 (정상).

`TELEGRAM_ALERT_LEVEL` 은 `.env.example:118` 에 있으나 `TelegramSettings.alert_level` 로 read 된다 (정상).

**이슈 없음** — 본 카테고리는 감사 중 다시 검증해 제외 처리됨.

### 4.7 Commit 3 변경 내역 및 의미 중복 분석 (2026-04-16)

**의미 중복 사전 분석**:

1. **Cat B/C 23건과 기존 78개 활성 키 사이의 의미 직접 중복은 발견되지 않음**. 이는 "같은 값을 다른 이름으로 읽는" 구조는 없다는 의미이다 (Cat D 와 같은 드리프트는 이미 별건으로 해소됨).
2. **네이밍 혼동 가능 쌍**:
   - `KIS_API_RETRY_COUNT` vs `KIS_TOKEN_RETRY_COUNT` — 전자는 시세/주문 조회 REST, 후자는 토큰 발급 전용. 의미가 다르지만 이름이 비슷하므로 `.env.example` 에서 주석으로 용도를 명시해 구분.
3. **기존 주석 상태였던 4건**은 원래 audit regex (`^[A-Z_]+=`) 범위 밖이어서 Cat B/C 집계에 포함되었다:
   - `KIS_WS_INSECURE_ALLOW` / `KIS_WS_EXCEPTION_TICKET` / `KIS_WS_EXCEPTION_EXPIRES_AT` (이전 L63-65, 주석)
   - `IMAGE_TAG` (이전 L184, 주석)
4. **SMTP 관련 5건 (`SMTP_SERVER`/`SMTP_PORT`/`SMTP_USERNAME`/`SMTP_PASSWORD`/`ALERT_EMAIL`)** 은 "향후 확장용" 으로 의도적으로 주석 처리된 상태. 현재 미사용이지만 장래 계획이 있으므로 본 커밋에서 건드리지 않음.
5. **DOCKER_IMAGE_NAME / DOCKER_CONTAINER_NAME** 은 `IMAGE_NAMESPACE` + `IMAGE_TAG` 체제로 완전히 대체되었으므로 legacy 레퍼런스를 유지할 이유가 없음. 제거.

**.env.example 변경 델타 (+23, -2)**:

| 구분 | 키 | 위치 | 비고 |
|------|-----|------|------|
| 추가 (Cat B1, pydantic) | `KIS_TOKEN_RETRY_COUNT` | KIS 공통 블록 | 기본 5 |
| 추가 (Cat B1, pydantic) | `KIS_TOKEN_RETRY_MAX_WAIT` | KIS 공통 블록 | 기본 60 |
| 주석 해제 (Cat B1, pydantic) | `KIS_WS_INSECURE_ALLOW` | WebSocket 예외 블록 | 기본 `false` (차단 유지) |
| 주석 해제 (Cat B1, pydantic) | `KIS_WS_EXCEPTION_TICKET` | 동상 | 기본 공란 |
| 주석 해제 (Cat B1, pydantic) | `KIS_WS_EXCEPTION_EXPIRES_AT` | 동상 | 기본 공란 |
| 추가 (Cat B1, pydantic) | `DASHBOARD_PREVIOUS_SECRET_KEY` | 대시보드 블록 | JWT 롤링용 |
| 추가 (Cat B1, pydantic) | `DASHBOARD_ACCESS_TOKEN_EXPIRE_HOURS` | 대시보드 블록 | 기본 1 |
| 추가 (Cat B1, pydantic) | `DASHBOARD_REFRESH_TOKEN_EXPIRE_DAYS` | 대시보드 블록 | 기본 14 |
| 추가 (Cat B2, 직접 read) | `ALERT_RETRY_LOOP_ENABLED` | 알림 파이프라인 블록 | 기본 true |
| 추가 (Cat B2, 직접 read) | `KIS_RECOVERY_COOLDOWN_SECONDS` | KIS 공통 블록 | 기본 75 |
| 추가 (Cat B2, 직접 read) | `KIS_STARTUP_JITTER_MAX_SECONDS` | KIS 공통 블록 | 기본 15.0 |
| 추가 (Cat B2, 직접 read) | `OTEL_ENABLED` | OTEL 블록 | 기본 false |
| 추가 (Cat B2, 직접 read) | `SCHEDULER_HEARTBEAT_PATH` | Scheduler heartbeat 블록 | 기본 /tmp/scheduler.heartbeat |
| 추가 (Cat B2, 직접 read) | `SCHEDULER_HEARTBEAT_STALE_SECONDS` | 동상 | 기본 180 |
| 주석 해제 + 기본값 명시 (Cat C) | `IMAGE_TAG` | 컨테이너 레지스트리 블록 | 기본 `latest` + 운영 pin 경고 |
| 추가 (Cat C) | `BACKUP_INTERVAL_HOURS` | DB 백업 블록 | 기본 24 |
| 추가 (Cat C) | `BACKUP_RETENTION_DAYS` | DB 백업 블록 | 기본 7 |
| 추가 (Cat C) | `GCS_BACKUP_BUCKET` | DB 백업 블록 | 기본 공란 (미업로드) |
| 추가 (Cat C) | `GRAFANA_USER` | 모니터링 블록 | 기본 admin |
| 추가 (Cat C) | `GRAFANA_PORT` | 모니터링 블록 | 기본 3000 |
| 추가 (Cat C) | `PROMETHEUS_PORT` | 모니터링 블록 | 기본 9090 |
| 추가 (Cat C) | `ALERTMANAGER_PORT` | 모니터링 블록 | 기본 9093 |
| 추가 (Cat C) | `JAEGER_UI_PORT` | 모니터링 블록 | 기본 16686 |
| 제거 (Cat A) | `DOCKER_IMAGE_NAME` | — | legacy, IMAGE_NAMESPACE/TAG 로 대체 |
| 제거 (Cat A) | `DOCKER_CONTAINER_NAME` | — | 동상 |

**유지된 Cat A 키 (Commit 4 P1-3 로 분리)**:
- `BACKTEST_DEFAULT_START_DATE` / `BACKTEST_DEFAULT_END_DATE` / `BACKTEST_DEFAULT_INTERVAL`
- `DATA_CACHE_DIR` / `RESULTS_DIR` / `LOG_DIR`
- `DEBUG`
- `WORKER_COUNT` — 사용자 결정에 따라 "활성화 예정" 으로 유지. 실제 wiring (Dockerfile CMD / compose command override) 은 환경 튜닝 PR 로 분리.

**코드 변경 0건**: pydantic 필드와 직접 read 경로 모두 이미 기본값을 갖고 있어, 본 커밋이 반영된 `.env.example` 에서 새 키를 비우거나 기본값으로 두어도 부팅 동작은 완전히 동일하다. 따라서 본 커밋은 "문서-only" 에 해당하며 전체 pytest 실행을 생략할 수 있는 CLAUDE.md 예외 경로에 해당한다.

### 4.8 Commit 4 변경 내역 — Cat A 미사용 7건 정리 (2026-04-16)

**목표**: §4.2 Category A 잔여 7건을 `.env.example` 에서 제거하고 부수적으로 deadcode가 된 bool whitelist 엔트리(`DEBUG`)를 정합시켜 환경변수 드리프트 감사 P1 라인을 종결한다. `WORKER_COUNT` 는 §4.7 결정에 따라 "활성화 예정" 주석과 함께 유지한다.

**사전 검증 (grep 전수)**:

7개 키 각각에 대해 `backend/`, `scripts/`, `docker-compose*.yml`, `.github/workflows/*.yml`, `Dockerfile*`, `alembic/` 전수 grep 수행. 결과:

| 키 | 코드 read | docker-compose / workflow 참조 | `.env.example` 외 문서 참조 |
|---|---|---|---|
| `BACKTEST_DEFAULT_START_DATE` | 0건 | 0건 | 본 감사 문서 §4.2 |
| `BACKTEST_DEFAULT_END_DATE` | 0건 | 0건 | 본 감사 문서 §4.2 |
| `BACKTEST_DEFAULT_INTERVAL` | 0건 | 0건 | 본 감사 문서 §4.2 |
| `DATA_CACHE_DIR` | 0건 | 0건 | 본 감사 문서 §4.2 |
| `RESULTS_DIR` | 0건 | 0건 | 본 감사 문서 §4.2 |
| `LOG_DIR` | 0건 | 0건 | `phase1-demo-verification-2026-04-11.md` §5.1 (과거 `/proc/1/environ` 스냅샷 캡처 — 역사적 기록이므로 수정하지 않음) |
| `DEBUG` | 0건 (`settings.py` 에 Field 정의 없음; 테스트 내 `"DEBUG"` 문자열은 loguru 로그 레벨로 무관) | 0건 | `scripts/check_bool_literals.py::BOOL_ENV_KEYS` whitelist + `docs/conventions/boolean-config.md` 정적 검사 설명 |

**변경 델타 (-7 in `.env.example`, -1 in `check_bool_literals.py`, -1 in `boolean-config.md`)**:

| 파일 | 변경 |
|------|------|
| `.env.example` | "데이터 및 백테스트 설정" 섹션 통째로 삭제(6키) + 고급 설정 섹션의 `DEBUG=false` 1줄 삭제. `WORKER_COUNT=4` 와 기존 주석은 원형 유지. |
| `scripts/check_bool_literals.py` | `BOOL_ENV_KEYS` 집합에서 `"DEBUG"` 엔트리 제거. 다른 엔트리/주석/검사 로직은 비변경. |
| `docs/conventions/boolean-config.md` | "정적 검사" 섹션의 알려진 bool 키 목록에서 `DEBUG` 제거. 나머지 가이드·마이그레이션 절차는 비변경. |

**Silence Error 의심 점검 (CLAUDE.md §코드 수정 시 Silence Error 의심 원칙)**:

1. "키 제거로 기존에 실패하던 경로가 조용히 성공하게 되지 않았는가?" — `.env.example` 의 키는 런타임 read 경로가 전혀 없었으므로 제거해도 새로운 분기를 만들지 않음. `settings.py` 의 Field 재정의도 없음. 부팅 시퀀스 영향 0.
2. "에러가 사라진 이유가 문제 해결인가, 다른 경로로 빠진 것인가?" — 본 커밋은 에러를 해소한 것이 아니라 미사용 선언을 제거한 것. 새 에러가 발생할 수 있는 분기를 만들지 않음.
3. `check_bool_literals.py` 에서 `DEBUG` 제거: 이 키가 `.env` / compose / workflow 어디에도 없으므로 검사기가 절대 매치하지 않는 deadcode 였다. 제거 후에도 동일 표기 검사 논리는 유지.

**테스트 전략**:

- 코드 경로(backend/ 아래 `.py`)는 zero-diff. 단 `scripts/check_bool_literals.py` 자체를 수정했으므로 CLAUDE.md "문서-only 커밋" 예외 조건(`git diff --stat` 에 `.py` 0건) 을 **초과**한다 → 전체 `pytest tests/` 실행 필수.
- 추가로 `python scripts/check_bool_literals.py`, `python scripts/check_doc_sync.py --verbose` 를 실행해 정적 검사기 자체의 회귀 0 errors 확인.
- `ruff`, `black --check` 실행.

**P1-3 종결 상태**: 본 커밋 반영 후 §4.2 Category A 는 `WORKER_COUNT` (의도적 유지) 외 미사용 잔재 0 건. 스키마/환경변수 드리프트 감사의 P1 라인(P1-1 Cat B, P1-2 Cat C, P1-3 Cat A, P1-D RL DB 키)은 모두 해결. 다음 후보는 P2(env_bool 강격화) / P3(`scripts/check_env_drift.py` 정적 검사기 신설).

## 5. Boolean 표기 표준 위반 여부 (검증됨)

Ad-hoc bool 파싱(`os.environ.get(...).lower() in ("true","1","yes")` 패턴) 은 **검출되지 않음**. `env_bool()` 단일 진입점 사용이 일관되게 유지되고 있다 (CLAUDE.md 환경변수 Boolean 표기 표준 규칙 준수).

단, `KIS_WS_INSECURE_ALLOW` 는 String 필드로 선언된 뒤 `_parse_ws_insecure_allow()` 로 수동 bool 변환한다 (`settings.py:127, 148, 203`). 이는 `env_bool()` 진입점을 우회하지만 ad-hoc 가 아닌 구조화된 파서이므로 즉시 위반은 아니다. 다만 Phase 2 (`AQTS_STRICT_BOOL=true`) 에서 동일한 표준을 적용하려면 pydantic `bool` 필드 + validator 로 통일하는 것이 바람직하다.

## 6. 우선순위별 조치 제안

### P0 (즉시 조치 — Commit B 착수 전)

없음. TradingGuard Redis 이행에 직접 블로킹하는 결함은 본 감사에서 발견되지 않았다.

### P1 (Commit E 와 병합하거나 직전 커밋으로 분리)

1. **`.env.example` 의 Category B (14건) 전량 추가**: pydantic 자동 read 8건 + 직접 read 6건. 특히 `DASHBOARD_PREVIOUS_SECRET_KEY` 는 JWT 키 롤링 운영에 필수이므로 최상위 우선. **[해결: 2026-04-16, Commit 3]** — §4.7 상태 블록 참조.
2. **`.env.example` 의 Category C (9건) 전량 추가**: `IMAGE_TAG` 가 가장 중요 (운영 digest pin). **[해결: 2026-04-16, Commit 3]** — §4.7 상태 블록 참조.
3. **RL 모듈 DB 키 통일 (Category D)**: `POSTGRES_*` → `DB_*` 로 이동, `DatabaseSettings` 재사용. 관련 테스트 추가. **[해결: 2026-04-16, Commit 2 (P1-D)]** — §4.5 상태 블록 참조.

### P2 (리팩터링, 별건 PR)

1. **Category A (10건) 제거**: 미사용 키 정리. `.env.example` 간결화. **[부분 해결: 2026-04-16, Commit 3 (2건) + Commit 4 (7건). `WORKER_COUNT` 1건은 "활성화 예정" 으로 의도 유지]** — §4.7, §4.8 상태 블록 참조.
2. **Redis 인라인 키 상수화**: `ensemble:latest:`, `portfolio:snapshot:`, `report:daily:` 3종을 모듈 상수로 이동.
3. **`KIS_WS_INSECURE_ALLOW` pydantic bool + validator 통일**: `_parse_ws_insecure_allow()` 제거.

### P3 (문서 전용)

1. **`docs/conventions/env-vars.md` 신설**: 현재 `docs/conventions/` 에는 `boolean-config.md` 만 존재. 환경변수 전체 카탈로그·네이밍 규칙(서브카테고리 프리픽스: `KIS_`/`DB_`/`REDIS_`/`MONGO_`/`ANTHROPIC_`/`TELEGRAM_`/`DASHBOARD_`/`AQTS_`)·추가 절차(`.env.example` + settings.py + check_bool_literals 등록)를 한 곳에 정리.

## 7. 후속 정적 검사기 도입 제안 (P2, 별건 PR)

환경변수 드리프트 방지를 위해 `scripts/check_env_drift.py` 도입을 권장한다. 이미 존재하는 정적 방어선들(RBAC Wiring Rule, 공급망 Wiring Rule, 알림 파이프라인 Wiring Rule, check_loguru_style)과 같은 패턴으로 구성한다.

**검사 항목**:

1. `backend/` 에서 `os.environ.get` / `os.getenv` / `env_bool` 호출 AST 수집 → `.env.example` 에 선언되어 있는지 확인.
2. `backend/config/settings.py` 의 `BaseSettings` 서브클래스 필드 + `env_prefix` 조합 → `.env.example` 확인.
3. `docker-compose.yml` 의 `${VAR}` 참조 → `.env.example` 확인.
4. `.env.example` 의 모든 키 → 위 세 경로 중 하나 이상에서 read 되는지 확인 (역방향 검증).

**Wiring Rule 도메인 확장 근거**:
- RBAC Wiring Rule (`scripts/check_rbac_coverage.py`): 라우트 정의 ≠ 가드 적용
- 공급망 Wiring Rule (cosign verify CD 게이트): 이미지 빌드 ≠ 서명된 이미지 배포
- 알림 파이프라인 Wiring Rule (`docs/architecture/notification-pipeline.md`): NotificationRouter 정의 ≠ lifespan 주입
- **환경변수 Wiring Rule (신규 제안)**: `.env.example` 선언 ≠ 코드 read 매칭

본 정적 검사기는 `env_bool` 호출 수집이 regex 로는 포착 불가능한 edge case 를 포함하므로 **AST 기반으로 구현**한다 (CLAUDE.md "정적 검사기 추가 시 필수 점검" 참조).

## 8. 감사 실행 명령 (재현 가능)

```bash
# PostgreSQL 스키마 ↔ raw SQL 불일치 (선행 감사 재실행)
cd /sessions/practical-eager-davinci/mnt/aqts
python scripts/check_schema_alignment.py  # 존재 시

# 환경변수 드리프트 (본 감사의 기반 스크립트)
# (현재는 ad-hoc Python 스크립트로 실행 — P2 단계에서 scripts/check_env_drift.py 로 정식화)

# Redis 키 카탈로그 (grep 기반)
grep -rn --include="*.py" "redis\.\(get\|set\|setex\|hset\|publish\|subscribe\|xadd\|lrange\)" backend/ | grep -v __pycache__
```

## 9. 회고

본 감사에서 가장 중요한 교훈 2가지:

1. **자동 감사 도구의 거짓양성도 정의 ≠ 적용 패턴**: 1차 패스 CRITICAL 3건이 모두 거짓양성이었다. 보고 자동화 자체가 다시 검증 대상이다. CLAUDE.md "오류 수정 시 관찰 우선 원칙" 은 감사 도구의 출력에도 그대로 적용된다.
2. **raw SQL 아키텍처 선택은 드리프트가 아님**: 20/23 테이블에 ORM 모델이 없는 것은 AQTS 의 의도된 패턴이다. "모델이 없다" 를 기계적으로 드리프트로 분류하지 않는다. 단, raw SQL 의 컬럼 레퍼런스와 alembic 의 실제 스키마 간 불일치는 여전히 드리프트이며 2026-04-13 에 4건 수정 완료 상태이다.

## 10. 관련 문서

- `docs/operations/schema-code-mismatch-fix-2026-04-13.md` — 선행 스키마 감사 (22 테이블 × 61 쿼리 전수)
- `docs/security/trading-guard-redis-migration.md` v0.3 — Commit B 이후 새 Redis 키 도입 계획
- `docs/conventions/boolean-config.md` — 환경변수 Boolean 표기 표준
- `backend/config/settings.py` — pydantic BaseSettings 정의 전수
- `.env.example` — 환경변수 레퍼런스 (본 감사 후 Category B/C/D 조치 필요)
