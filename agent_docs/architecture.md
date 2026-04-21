# AQTS Architecture Reference

> 각 팀메이트가 자기 담당 구역의 코드 경계를 빠르게 파악하기 위한 **파일:라인** 중심 참조 문서입니다. 본 문서는 "무엇을 해야 하는가" 가 아니라 "어디에 있는가" 만 다룹니다. 규칙·절차는 [development-policies.md](./development-policies.md), 데이터 저장소는 [database_schema.md](./database_schema.md), HTTP 엔드포인트는 [api_contracts.md](./api_contracts.md), 백테스트는 [backtest-operations.md](./backtest-operations.md) 를 참조합니다.

---

## 1. 런타임 토폴로지

### 1.1 컨테이너 레이아웃 (`docker-compose.yml`)

운영 compose 파일은 `docker-compose.yml` 이 단일 진실원천이며 개발 override 는 `docker-compose.override.yml` 에만 둔다 (development-policies.md §13).

| 서비스 | 역할 | 비고 |
|---|---|---|
| `postgres` | 트레이딩 기준 원장, RBAC 사용자·알림·감사 로그 | `backend/db/database.py` |
| `mongodb` | 분석 산출물, 알림 이벤트, 백테스트 결과 저장소 | 대용량 문서성 데이터 |
| `redis` | TradingGuard 상태, 스케줄러 스냅샷, idempotency 키 캐시 | KST 일일 키 (§14.3 회귀 사례) |
| `backend` | FastAPI HTTP/REST + WebSocket + `/metrics` | `backend/main.py:368` |
| `scheduler` | 트레이딩 스케줄러 + 환율 루프 + reconciliation | `backend/scheduler_main.py:63` |
| `prometheus` | 메트릭 수집 + 알림 규칙 로드 | `rule_files` 절대 경로 고정 |
| `alertmanager` | Prometheus 알림 라우팅 → Telegram/이메일 | |
| `grafana` | 대시보드 | |
| `otel-collector`, `jaeger` | 트레이스 수집/조회 | |
| `node-exporter` | 호스트 메트릭 | |
| `db-backup` | Postgres 정기 백업 (`scripts/backup_cron.sh`) | `scripts/backup_db.sh`, `scripts/restore_db.sh` |

### 1.2 베이스 이미지

`backend/Dockerfile` 은 `python:3.11-slim-bookworm` 을 사용하며 (rolling 3.11 태그, 패치는 3.11.14+), `backend/pyproject.toml` 의 `[tool.ruff]` / `[tool.black]` 모두 `target-version = "py311"` 로 정렬되어 있다. 레포 루트의 `.python-version` = `3.11.14` 로 로컬 pyenv 기준도 동일 버전으로 고정한다 (CI `PYTHON_VERSION: "3.11"` 과 일치). 본 정렬은 2026-04-21 `chore/python-version-align` 브랜치에서 py310 → py311 상향으로 확정되었다 — 3.11 전용 문법(`typing.Self`, `except*`, `TaskGroup` 등) 은 현재 사용처가 없으나 향후 도입 시 즉시 가능하도록 타깃을 런타임과 일치시켰다.

---

## 2. FastAPI 백엔드 (`backend/main.py`)

총 599줄. 주요 구조:

| 섹션 | 라인 | 설명 |
|---|---|---|
| 시그널 핸들러 | `backend/main.py:96` | `_signal_handler` — graceful shutdown 훅 |
| lifespan 시작 | `backend/main.py:106` | `@asynccontextmanager` lifespan |
| NotificationRouter 주입 | `backend/main.py:157` | `NotificationRouter wired: telegram → file → console cascade` 로그 출력 (development-policies.md §14.1) |
| AlertRetryLoop task | `backend/main.py:169-192` | `_alert_retry_loop` 정의 169 · `AlertRetryLoop started` 로그 179 · `asyncio.create_task` 192 |
| lifespan shutdown | `backend/main.py:340-347` | `_alert_retry_task` await + `AlertRetryLoop stopped` 로그 (347) |
| FastAPI 인스턴스 | `backend/main.py:368` | `app = FastAPI(..., lifespan=lifespan)` |
| 표준 HTTP 예외 핸들러 | `backend/main.py:383` (정의) / `backend/main.py:400` (등록) | `_standard_http_exception_handler` |
| `/api/system/health` | `backend/main.py:427` | 시스템 헬스체크 |
| `/api/info` | `backend/main.py:551` | |
| `/` (대시보드) | `backend/main.py:562` | 프론트엔드 SPA 서빙 (`frontend/index.html`) |
| Router 등록 | `backend/main.py:578-595` | 13개 `include_router` 호출 (자세한 것은 [api_contracts.md](./api_contracts.md)) |

**필수 배포 후 확인 로그 3종** (development-policies.md §14.2):

1. `NotificationRouter wired` — `backend/main.py:157`
2. `AlertRetryLoop started` — `backend/main.py:179`
3. `/metrics` 에 `aqts_alert_dispatch_*` 계열 노출 — `NotificationRouter.dispatch` 내부 try/finally (backend/core/notification/fallback_notifier.py)

---

## 3. Scheduler (`backend/scheduler_main.py`)

178줄의 단일 진입점. 주요 섹션:

| 섹션 | 라인 | 설명 |
|---|---|---|
| 모듈 docstring | `backend/scheduler_main.py:1-20` | 실행 방식 `python scheduler_main.py` |
| 환율 루프 | `backend/scheduler_main.py:37` | `async def _exchange_rate_loop(stop_event)` |
| 메인 엔트리 | `backend/scheduler_main.py:63` | `async def main()` |
| reconcile wiring | `backend/scheduler_main.py:131` 부근 | `_run_reconciliation_if_wired` 및 provider 호출 단 |
| 엔트리포인트 | `backend/scheduler_main.py:177-178` | `asyncio.run(main())` |

**스케줄링 도메인 로직**은 `backend/core/trading_scheduler.py` + `backend/core/scheduler_handlers.py` + `backend/core/scheduler_heartbeat.py` + `backend/core/scheduler_idempotency.py` 로 분할된다. KST 통일 회귀 사례는 `scheduler_handlers.py` 의 `today_kst_str()` 키와 테스트 fixture 간 드리프트였다 (development-policies.md §8.3).

**PYTHONUNBUFFERED 필수**: `docker-compose.yml` 의 scheduler `environment:` 에 `PYTHONUNBUFFERED: "1"` 가 있어야 `docker compose logs scheduler` 가 실시간으로 출력된다 (development-policies.md §8.2 "출력 채널 버퍼링 silent miss", `docs/operations/phase1-demo-verification-2026-04-11.md §10.14`).

---

## 4. 투자 의사결정 파이프라인 (`backend/core/pipeline.py`)

총 608줄. `InvestmentDecisionPipeline` 클래스가 다단계 분석을 조립한다.

| 구성 요소 | 라인 | 역할 |
|---|---|---|
| `PipelineResult` dataclass | `backend/core/pipeline.py:52` | 파이프라인 반환 타입 |
| `_build_default_gate_registry` | `backend/core/pipeline.py:71` | 기본 게이트 구성 |
| `InvestmentDecisionPipeline` | `backend/core/pipeline.py:81` | 파사드 클래스 |
| `run_full_analysis` | `backend/core/pipeline.py:148` | 단일 티커 전체 분석 |
| `run_batch_analysis` | `backend/core/pipeline.py:267` | 배치 실행 (내부에서 `run_full_analysis` 반복 호출) |
| `run_news_collection` | `backend/core/pipeline.py:314` | 뉴스 수집 단계 |
| `run_sector_analysis` | `backend/core/pipeline.py:323` | 섹터 레벨 분석 |
| `run_macro_analysis` | `backend/core/pipeline.py:380` | 매크로 (DART/FRED/ECOS) 분석 |
| `run_dynamic_ensemble` | `backend/core/pipeline.py:414` | 단일 티커 동적 앙상블 |
| `run_dynamic_ensemble_batch` | `backend/core/pipeline.py:499` | 배치 동적 앙상블 |

`run_dynamic_ensemble` 은 `backend/core/strategy_ensemble/runner.py` 의 `Runner.run_with_ohlcv` (pipeline.py:453 호출) 를 통해 개별 전략을 실행한다.

---

## 5. 주문 실행 레이어 (`backend/core/order_executor/`)

| 파일 | 역할 |
|---|---|
| `executor.py` | 주문 접수 → 상태 머신 → 체결 통지까지의 오케스트레이션 |
| `order_state_machine.py` | 주문 상태 전이 (development-policies.md §10 대상) |
| `price_guard.py` | 가격 상·하한 가드 — 위반 시 critical 로그 (development-policies.md §8.2 loguru mismatch 대상) |
| `quote_provider_kis.py` | KIS 시세 조회 어댑터 |
| `settlement_poller.py` | 체결 폴링 루프 |
| `slippage.py` | 슬리피지 모델 |
| `time_rules.py` | 장 시간·거래 제한 규칙 |
| `ws_execution_handler.py` | KIS WebSocket 체결 통지 수신 (RealtimeManager 와 연결) |

**TradingGuard 연동**: `backend/core/trading_guard.py:73` `TradingGuard` 가 주문 접수 직전 `check_pre_order` (line 335) 로 게이트 역할을 수행하고, 위반 시 `TradingGuardBlocked` (line 428) 예외로 거절한다. 일일 손실/최대 낙폭/연속 손실 체크는 각각 line 147/160/179 에 정의된다.

---

## 6. 전략·백테스트 (`backend/core/strategy_ensemble/`, `backend/core/backtest_engine/`, `backend/core/quant_engine/`)

| 모듈 | 파일 | 역할 |
|---|---|---|
| 앙상블 엔진 | `strategy_ensemble/engine.py` | 정적 가중 앙상블 |
| 동적 앙상블 | `strategy_ensemble/dynamic_ensemble.py` | 레짐 기반 가중 동적 조정 |
| 레짐 판별 | `strategy_ensemble/regime.py` | 시장 레짐 분류 |
| 러너 | `strategy_ensemble/runner.py` | `Runner.run_with_ohlcv` (pipeline.py:453 호출) |
| 백테스트 엔진 | `backtest_engine/engine.py` | 메인 시뮬레이터 |
| 앨러블이션 | `backtest_engine/ablation.py` | 컴포넌트 기여도 분해 |
| 벤치마크 | `backtest_engine/benchmark.py` | 벤치마크 대비 지표 |
| bias 체크 | `backtest_engine/bias_checker.py` | look-ahead / survivorship bias |
| Fill·Impact | `backtest_engine/fill_model.py`, `impact_model.py` | 체결·시장 충격 모델 |
| 메트릭 | `backtest_engine/metrics_calculator.py` | 샤프, MDD 등 |
| pass/fail | `backtest_engine/pass_fail.py` | OOS 합격 판정 |
| regime 분석 | `backtest_engine/regime_analyzer.py` | 레짐별 성과 |
| 유의성 | `backtest_engine/significance.py` | 통계적 유의성 검증 |
| 시그널 생성 | `quant_engine/signal_generator.py`, `vectorized_signals.py` | 팩터 → 시그널 변환 |
| 팩터 분석 | `quant_engine/factor_analyzer.py` | |

백테스트 전용 운용 규칙은 [backtest-operations.md](./backtest-operations.md) 참조.

---

## 7. 알림 파이프라인 (`backend/core/notification/`)

development-policies.md §14 의 "5개 레이어 wiring" 이 실제로 위치하는 파일:

| 레이어 | 파일 | 핵심 식별자 |
|---|---|---|
| 상태 머신 메서드 | `notification/alert_manager.py` | `claim_for_sending`, `mark_*`, `requeue_*` |
| NotificationRouter | `notification/fallback_notifier.py` | `NotificationRouter.dispatch` |
| 재시도 루프 정의 | `backend/main.py:169` | `async def _alert_retry_loop()` (내부 `AlertRetryLoop started` 로그 179) |
| Prometheus 훅 | `backend/core/monitoring/` 의 `metrics.py` | `aqts_alert_dispatch_*` Counter/Histogram |
| 메타알림 룰 | `prometheus/rules/*.yml` → `aqts_alerts.yml` 내 `aqts_alert_pipeline` | Alertmanager 로드 |

보조 파일: `retry_policy.py`, `telegram_adapter.py`, `telegram_notifier.py`, `telegram_transport.py`.

아키텍처 상세: `docs/architecture/notification-pipeline.md`.

---

## 8. 데이터 수집 (`backend/core/data_collector/`)

KIS REST/WebSocket, DART (공시), FRED·ECOS (거시), Reddit 등 외부 소스 어댑터. 핵심 파일:

- `kis_client.py`, `kis_startup.py`, `kis_recovery.py`, `kis_websocket.py` — KIS 브로커 어댑터 (승계의 RealtimeManager 와 연결)
- DART/FRED/ECOS/Reddit — 각 소스별 어댑터 모듈
- `.env.example` 의 `KIS_*`, `DART_*`, `FRED_*`, `ECOS_*`, `REDDIT_*` 환경변수로 구동

**환경변수는 코드에 하드코딩 금지** — 반드시 `core.utils.env.env_bool()` 및 `os.getenv(...)` 통한 런타임 주입 (imported_knowledge custom_instructions, development-policies.md §11).

---

## 9. 포트폴리오·원장 (`backend/core/portfolio_manager/`, `backend/core/portfolio_ledger.py`)

- `portfolio_ledger.py` — 포지션·현금 원장, `ledger refuse` 경로는 critical 로그 대상 (development-policies.md §8.2)
- `portfolio_manager/` — 주문 배분, 리밸런싱
- `reconciliation.py`, `reconciliation_providers.py`, `reconciliation_runner.py` — 브로커 체결과 원장 대조, `reconcile mismatch` 는 critical (development-policies.md §8.2)
- DB 모델: `backend/db/models/portfolio_position.py` + 리포지토리 `backend/db/repositories/portfolio_positions.py`

---

## 10. 감사·컴플라이언스 (`backend/core/audit/`, `backend/core/compliance/`)

- `audit/` — `AuditLog` 기록, fail-closed 모드 (`audit fail-closed` 메시지가 critical 경로)
- `compliance/` — 규제 제한·허용 리스트
- DB 모델: `backend/db/repositories/audit_log.py`

---

## 11. 게이트·서킷브레이커

- `backend/core/gate_registry.py` + `backend/core/gates/` — 파이프라인 내 게이트 정의
- `backend/core/circuit_breaker.py` — 시스템 레벨 서킷브레이커
- `backend/core/emergency_monitor.py` — 긴급 상황 모니터링

---

## 12. 스케줄링 세부

- `backend/core/trading_scheduler.py` — 일간·주간 작업 스케줄
- `backend/core/scheduler_handlers.py` — 각 작업의 핸들러 (일일 보고서, 스냅샷 등). `today_kst_str()` 기반 Redis 키 사용 (development-policies.md §8.3)
- `backend/core/scheduler_heartbeat.py` — 하트비트 기록 (§15 회귀 사례 1 의 heartbeat age 감시 대상)
- `backend/core/scheduler_idempotency.py` — 중복 실행 방지
- `backend/core/market_calendar.py` — 거래소 휴장일
- `backend/core/periodic_reporter.py` — 주기 리포트
- `backend/core/daily_reporter.py` — 일일 보고서

---

## 13. 모드·설정 관리

- `backend/config/settings.py` — Pydantic Settings, `.env` 로드
- `backend/config/constants.py` — 상수
- `backend/config/operational_thresholds.yaml` — 운영 임계값 (DEFAULT_THRESHOLDS 와 동기 필수, development-policies.md §4)
- `backend/config/ensemble_config.yaml` + `ensemble_config_loader.py` — 앙상블 설정
- `backend/config/logging.py` — loguru 초기화 (development-policies.md §8.2 loguru mismatch 방지 대상)
- `backend/core/mode_manager.py` — 운영 모드 (dry_run / paper / live) 스위칭

---

## 14. 유틸리티 (`backend/core/utils/`)

공통 유틸은 전 팀메이트가 read-only 로 참조한다. 수정은 리드(사용자) 승인을 요구한다.

- `env.py` — `env_bool()` 단일 진입점 (development-policies.md §11)
- `time.py` — `today_kst_str()`, KST 관련 헬퍼 (development-policies.md §8.3)

---

## 15. 테스트·정적 검사 (`backend/tests/`, `backend/scripts/`)

- `backend/tests/` — 총 **159** 테스트 파일 (실제 수집 시점 pytest 러너가 155~160 사이로 보고)
- `backend/scripts/check_doc_sync.py` — 문서 싱크 린터
- `backend/scripts/check_rbac_coverage.py` — RBAC AST 정적 분석 (development-policies.md §12.1)
- `backend/scripts/check_bool_literals.py` — Boolean 표기 검사 (development-policies.md §11)
- `backend/scripts/check_loguru_style.py` — loguru `%` posarg 검출 (development-policies.md §8.2)
- `backend/scripts/check_cd_stdin_guard.py` — CD heredoc stdin 가드 (development-policies.md §15)
- `backend/scripts/post_deploy_smoke.sh`, `pre_deploy_check.sh`, `canary_deploy.sh`, `deploy.sh` — 배포 파이프라인

---

## 16. Alembic 마이그레이션

- 경로: `backend/alembic/versions/` — 현재 리비전 파일 **7 개**
- `alembic.ini` 는 `docker-compose run -T backend alembic -c alembic.ini upgrade head </dev/null` 형태로 호출한다 (development-policies.md §15 회귀 사례 2)

---

## 17. 추가 참조 문서

- HTTP 계약: [api_contracts.md](./api_contracts.md)
- DB 스키마: [database_schema.md](./database_schema.md)
- 백테스트 운영: [backtest-operations.md](./backtest-operations.md)
- 거버넌스 (외부 도구 통합 정책): [governance.md](./governance.md)
- 팀 프롬프트 초안: [team_prompt_draft.md](./team_prompt_draft.md)
- 기존 심층 아키텍처: `docs/architecture/notification-pipeline.md`, `docs/architecture/production-grade-roadmap.md`
- 운영 런북: `docs/operations/` 전체 (`phase1-demo-verification-2026-04-11.md`, `cd-auto-prune-2026-04-16.md`, `daily-report-regression-2026-04-08.md` 등)
- 보안 정책: `docs/security/supply-chain-policy.md`, `rbac-policy.md`, `trading-guard-redis-migration.md`, `kis-websocket.md`, `admin-bootstrap.md`, `rbac.md`
- 컨벤션: `docs/conventions/boolean-config.md`
- 기능 현황: `docs/FEATURE_STATUS.md`, `docs/PRD.md`, `docs/YAML_CONFIG_GUIDE.md`

---

## 문서 소유권

- 이 문서는 구조 매핑만 담당한다. 규칙·절차는 [development-policies.md](./development-policies.md) 에만 둔다.
- 파일 경로·라인 번호는 `2026-04-17` 시점 기준이며, 리팩토링 시 함께 갱신한다. 갱신 담당은 변경을 가한 팀메이트가 우선, 주간 싱크에서 리드가 확인한다.
