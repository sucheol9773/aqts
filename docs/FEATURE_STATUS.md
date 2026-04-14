# AQTS Feature Status Matrix

> Single Source of Truth — 모든 기능의 구현 상태를 추적합니다.
>
> **성숙도 레벨**: Not Started → In Progress → Implemented → Tested → Production-ready → Blocked
>
> Last updated: 2026-04-06

## Status Summary

| Status | Count |
|--------|-------|
| Not Started | 0 |
| Implemented | 1 |
| Tested | 135 |
| Production-ready | 0 |
| Blocked | 0 |
| **TOTAL** | **136** |

---

## 1. Data Collection

| Module | Feature | Status | Code Path | Tests | Notes |
|--------|---------|--------|-----------|-------|-------|
| kis_client | KIS API 래퍼 (LIVE/DEMO/BACKTEST) | Tested | core/data_collector/kis_client.py | test_kis_client.py (19) | 한국투자증권 OpenAPI 통합, 3가지 모드 지원 |
| market_data | 시세 데이터 수집 및 무결성 검증 | Tested | core/data_collector/market_data.py | test_market_data.py (12) | 가격 데이터 OHLCV, 이상치 검증 |
| news_collector | RSS 뉴스 + DART 공시 수집 | Tested | core/data_collector/news_collector.py | test_news_collector.py (13) | Naver/Hankyung/Maekyung/Reuters 4개 소스 |
| economic_collector | FRED·ECOS 경제지표 수집 | Tested | core/data_collector/economic_collector.py | test_economic_collector.py (21) | 미국 9개 + 한국 5개 지표 |
| financial_collector | DART 재무제표 (하이브리드) | Tested | core/data_collector/financial_collector.py | test_financial_collector.py (41) | API + 일괄 txt, PER/PBR/ROE 파생 |
| social_collector | Reddit SNS 데이터 수집 | Tested | core/data_collector/social_collector.py | test_social_collector.py (59) | OAuth2, 8개 서브레딧, 키워드 필터 |
| kis_websocket | KIS 실시간 WebSocket (체결가+호가) | Tested | core/data_collector/kis_websocket.py | test_realtime.py (20) | H0STCNT0/H0STASP0, PINGPONG, 지수 백오프 재연결, 최대 40 구독 |
| realtime_manager | 실시간 시세 관리 (인메모리 캐시) | Tested | core/data_collector/realtime_manager.py | test_realtime.py (20) | WebSocket 라이프사이클, IntradayBar OHLCV 누적, 스냅샷 |

---

## 2. Quant Engine

| Module | Feature | Status | Code Path | Tests | Notes |
|--------|---------|--------|-----------|-------|-------|
| factor_analyzer | 5팩터 분석 (Value·Momentum·Quality·LowVol·Size) | Tested | core/quant_engine/factor_analyzer.py | test_factor_analyzer.py (21) | Z-Score 정규화, Cross-Market 재정규화 |
| signal_generator | 기술적 시그널 생성 (RSI·MACD·Bollinger) | Tested | core/quant_engine/signal_generator.py | test_signal_generator.py (20) | 5개 기술적 시그널 |
| backtest_engine | 백테스트 엔진 + 전략 비교 + 성능 개선 | Tested | core/backtest_engine/engine.py | test_backtest_engine.py (34) + test_backtest_improvements.py (22) | Sharpe/Alpha/Beta + CRISIS 방어 + 변동성 스케일링 + 점진적 재진입 + 동적 임계값 |

---

## 3. AI Analyzer

| Module | Feature | Status | Code Path | Tests | Notes |
|--------|---------|--------|-----------|-------|-------|
| sentiment | Claude Haiku 감성 분석 (Mode A) | Tested | core/ai_analyzer/sentiment.py | test_sentiment.py (9) | 뉴스/공시 감성 점수 (-1.0 ~ +1.0) |
| opinion | Claude Sonnet 투자 의견 (Mode B) | Tested | core/ai_analyzer/opinion.py | test_opinion.py (38) | STOCK·SECTOR·MACRO 3단계 의견 |
| prompt_manager | 프롬프트 DB 버전 관리 | Tested | core/ai_analyzer/prompt_manager.py | test_prompt_manager.py (43) | MongoDB 버전관리, 롤백, A/B 테스트 |

---

## 4. Strategy & Portfolio

| Module | Feature | Status | Code Path | Tests | Notes |
|--------|---------|--------|-----------|-------|-------|
| ensemble | 가중 앙상블 (Quant 4 + AI 감성 + Sharpe 재보정) | Tested | core/strategy_ensemble/engine.py | test_ensemble.py (13) | 4개 전략 통합 가중평균 |
| regime_detector | 실시간 시장 레짐 탐지 (5 regimes) | Tested | core/strategy_ensemble/regime.py | test_regime.py (31) + test_backtest_improvements.py (22) | TRENDING_UP/DOWN/SIDEWAYS/HIGH_VOLATILITY/CRISIS |
| profile | 투자자 프로필 (위험성향·스타일·손실허용도) | Tested | core/portfolio_manager/profile.py | test_profile.py (22) | 5단계 위험성향 분류 |
| construction | 포트폴리오 구성 (MVO·Risk Parity·Black-Litterman) | Tested | core/portfolio_manager/construction.py | test_construction.py (77) | 3중 엔진, Ledoit-Wolf 축소, USD 하드캡 |
| rebalancing | 리밸런싱 (정기·긴급·방어) | Tested | core/portfolio_manager/rebalancing.py | test_rebalancing.py (48) | 3가지 리밸런싱 모드 |
| universe | 투자 유니버스 관리 | Tested | core/portfolio_manager/universe.py | test_universe.py (29) | 섹터 필터, 지정 종목, 유동성 필터 |
| exchange_rate | 환율 관리 (KIS+FRED, Redis 캐싱) | Tested | core/portfolio_manager/exchange_rate.py | test_exchange_rate.py (48) | 5분/24시간 TTL 캐시 |
| weight_optimizer | 가중치 자동 최적화 (Sharpe·Risk-Adjusted 등) | Tested | core/weight_optimizer.py | test_weight_optimizer.py (32) | 4가지 최적화 방식, Walk-Forward 포함 |

---

## 5. Order Execution & Risk

| Module | Feature | Status | Code Path | Tests | Notes |
|--------|---------|--------|-----------|-------|-------|
| executor | 주문 집행 (시장가·지정가·TWAP·VWAP) | Tested | core/order_executor/executor.py | test_executor.py (36) | TWAP 6분할, VWAP 가중치, 배치 실행, dry_run 모드 지원 |
| dry_run_engine | 드라이런 엔진 (주문 인터셉트 + 가상 기록) | Tested | core/dry_run/engine.py | test_dry_run_engine.py (33) + test_dry_run_api.py (13) | DryRunSession/Order/Report, 6개 API 엔드포인트 |
| trading_guard | 트레이딩 안전 장치 (7계층 보호) | Tested | core/trading_guard.py | test_trading_guard.py (72) | 환경·자본·손실·MDD·연속손실 검증 |
| emergency_monitor | 비상 리밸런싱 5분 모니터 | Tested | core/emergency_monitor.py | test_emergency_monitor.py (64) | 동적 손절, 방어 포트폴리오 전환 |

---

## 6. Operations & Monitoring

| Module | Feature | Status | Code Path | Tests | Notes |
|--------|---------|--------|-----------|-------|-------|
| mode_manager | 모드 전환 (BACKTEST→DEMO→LIVE) | Tested | core/mode_manager.py | test_mode_manager.py (41) | 조건 검증, 비상 다운그레이드, 이력 기록 |
| demo_verifier | DEMO 실전 가동 검증 (11항목) | Tested | core/demo_verifier.py | test_demo_verifier.py (73) | 11개 종합 체크리스트 |
| health_checker | 시스템 건전성 검사 (5항목) | Tested | core/health_checker.py | test_health_checker.py (19) | DB·설정·모드 종합 점검 |
| trading_scheduler | 자동화 스케줄러 (KRX 장 시간 기반) | Tested | core/trading_scheduler.py | test_trading_scheduler.py (76) | 5단계 자동화, 거래일 판별 |
| daily_reporter | 일일 리포트 생성 및 발송 | Tested | core/daily_reporter.py | test_daily_reporter.py (71) | 수익률·거래·Top3 리포트 |
| daily_reporter_top_bottom | Top/Bottom 3 종목 자동 추출 | Tested | core/daily_reporter.py | test_daily_reporter_top_bottom.py (7) | 수익률 기준 상위/하위 종목 |
| periodic_reporter | 주간/월간 리포트 | Tested | core/periodic_reporter.py | test_periodic_reporter.py (27) | MDD/Sharpe 분석, 벤치마크 비교 |
| market_calendar | 마켓 캘린더 (KRX + NYSE) | Tested | core/market_calendar.py | test_market_calendar.py (44) | 미국 공휴일 자동 산출, DST 판별 |
| graceful_shutdown | 그레이스풀 셧다운 매니저 | Tested | core/graceful_shutdown.py | test_graceful_shutdown.py (25) | 3단계 셧다운, 주문 드레이닝 |
| circuit_breaker | 외부 API 장애 자동 차단 (4 서비스) | Tested | core/circuit_breaker.py | test_circuit_breaker.py (17) | KIS/FRED/DART/Claude, half-open 복구 |

---

## 7. API Layer

| Module | Feature | Status | Code Path | Tests | Notes |
|--------|---------|--------|-----------|-------|-------|
| middleware/auth | JWT 인증 (HS256 Bearer Token) + RBAC + TOTP MFA | Tested | api/middleware/auth.py | test_api.py (60), test_jwt_security.py (17), test_rbac.py (8), test_mfa.py (15) | Key Rotation (kid), jti + revocation, bcrypt, username/password 인증, role 클레임, TOTP 2FA |
| middleware/rbac | 역할 기반 접근 제어 (viewer/operator/admin) | Tested | api/middleware/rbac.py | test_rbac.py (8) | require_viewer/operator/admin 의존성, 권한 검증 |
| middleware/request_logger | 요청 로깅 미들웨어 | Tested | api/middleware/request_logger.py | test_api.py (60) | HTTP 요청/응답 로깅 |
| routes/auth | 인증 (로그인·토큰·MFA·로그아웃) | Tested | api/routes/auth.py | test_api.py (60), test_mfa.py (15) | username/password 로그인, 토큰 갱신, MFA enroll/verify/disable, 로그아웃 |
| routes/users | 사용자 관리 (Admin only) | Tested | api/routes/users.py | test_users_api.py (8) | CRUD, 비밀번호 리셋, 잠금/해제, 역할 변경 |
| schemas/auth | 인증 스키마 | Tested | api/schemas/auth.py | test_api.py (60) | LoginRequest (username/password/totp), MFAEnrollResponse, TokenResponse |
| schemas/users | 사용자 관리 스키마 | Tested | api/schemas/users.py | test_users_api.py (8) | UserCreateRequest, UserUpdateRequest, UserResponse |
| routes/portfolio | 포트폴리오 (요약·보유·성과) | Tested | api/routes/portfolio.py | test_api.py (60) | 포트폴리오 조회, 성과 분석 |
| routes/orders | 주문 (생성·배치·조회·취소) | Tested | api/routes/orders.py | test_api.py (60) | 주문 CRUD 작업 |
| routes/profile | 투자자 프로필 (조회·수정) | Tested | api/routes/profile.py | test_api.py (60) | 프로필 조회 및 수정 |
| routes/market | 시장 정보 (환율·지수·지표·유니버스) | Tested | api/routes/market.py | test_api.py (60) | 시장 데이터 조회 |
| routes/alerts | 알림 (이력·통계·확인) | Tested | api/routes/alerts.py | test_api.py (60) | 알림 관리 |
| routes/system | 시스템 (설정·백테스트·리밸런싱) | Tested | api/routes/system.py | test_api.py (60) | 시스템 관리 엔드포인트 |
| schemas | Pydantic 요청/응답 모델 | Tested | api/schemas/common.py | test_api.py (60) | 18개 클래스, 6개 스키마 모듈 |
| routes/realtime | 실시간 시세 API (시세·스냅샷·상태) | Tested | api/routes/realtime.py | test_realtime.py (20) | GET /quotes, /quotes/{ticker}, /status |
| middleware/rate_limiter | API Rate Limiting (slowapi) | Tested | api/middleware/rate_limiter.py | test_rate_limiter.py (7) | 로그인/API 엔드포인트 4개 제한 |

---

## 8. Notifications & Logging

| Module | Feature | Status | Code Path | Tests | Notes |
|--------|---------|--------|-----------|-------|-------|
| alert_manager | 알림 생성·관리·이력 (템플릿 기반) | Tested | core/notification/alert_manager.py | test_notification.py (72) | 레벨 필터링, MongoDB/메모리 이중 저장 |
| telegram_notifier | 텔레그램 봇 알림 발송 | Tested | core/notification/telegram_notifier.py | test_notification.py (72) | 레벨 필터(ALL/IMPORTANT/ERROR), 재시도 3회 |
| fallback_notifier | 백업 알림 채널 (File/Console 폴백) | Tested | core/notification/fallback_notifier.py | test_gate_c_notification.py (46) | FileNotifier+ConsoleNotifier, ChannelHealth 추적 |
| telegram_adapter | Telegram 채널 어댑터 (프로토콜 적합) | Tested | core/notification/telegram_adapter.py | test_gate_c_notification.py (46) | NotificationChannel 프로토콜 래핑 |
| notification_router | 알림 라우터 (1차→백업 자동 폴백) | Tested | core/notification/fallback_notifier.py | test_gate_c_notification.py (46) | Telegram→File→Console 순차 폴백 |
| audit_log | 감사 로그 (결정 추적) | Tested | db/repositories/audit_log.py | test_infrastructure.py (70) | 결정 감사 추적 |

---

## 9. Infrastructure & Configuration

| Module | Feature | Status | Code Path | Tests | Notes |
|--------|---------|--------|-----------|-------|-------|
| database | DB 연결 관리 (PostgreSQL·MongoDB·Redis) | Tested | db/database.py | test_infrastructure.py (70) | 3개 데이터베이스 통합 |
| settings | 환경변수 기반 설정 (pydantic-settings) | Tested | config/settings.py | test_infrastructure.py (70) | Pydantic-settings 기반 설정 |
| constants | 상수·Enum 정의 | Tested | config/constants.py | test_infrastructure.py (70) | 시스템 상수 정의 |
| logging | Loguru 로깅 설정 | Tested | config/logging.py | test_infrastructure.py (70) | 중앙 로깅 설정 |
| main | FastAPI 엔트리포인트 | Tested | main.py | test_integration.py (30) | Lifespan, GracefulShutdownManager 통합 |

---

## 10. Compliance (Gate D)

| Module | Feature | Status | Code Path | Tests | Notes |
|--------|---------|--------|-----------|-------|-------|
| audit_integrity | 감사 로그 무결성 검증 (SHA-256 해시 체인) | Tested | core/compliance/audit_integrity.py | test_gate_d_compliance.py (57) | 변조 탐지, 조회, 통계 |
| retention_policy | 거래 기록 보존 정책 (5년/10년 보존) | Tested | core/compliance/retention_policy.py | test_gate_d_compliance.py (57) | 8개 카테고리, 조기 삭제 방지 |
| pii_masking | 개인정보 마스킹 검증 (7종 PII 탐지) | Tested | core/compliance/pii_masking.py | test_gate_d_compliance.py (57) | 주민번호/전화/이메일/계좌/카드/IP/API키 |
| compliance_report | 규제 준수 리포트 자동 생성 | Tested | core/compliance/compliance_report.py | test_gate_d_report_secret.py (40) | 4개 섹션 생성기, 종합 등급 산출 |
| secret_manager | 비밀키 관리 (로테이션/볼트) | Tested | core/compliance/secret_manager.py | test_gate_d_report_secret.py (40) | 등록/로테이션/폐기/건강검사, 6종 시크릿 타입 |

---

## 11. Monitoring (Gate E)

| Module | Feature | Status | Code Path | Tests | Notes |
|--------|---------|--------|-----------|-------|-------|
| monitoring_dashboard | 모니터링 대시보드 (핵심 지표 실시간 확인) | Tested | core/monitoring/dashboard.py | test_gate_e_monitoring.py (53) | 서비스 상태/메트릭/알림 통합, 임계값 자동 알림 |
| prometheus_metrics | Prometheus 메트릭 수집 + Grafana 시각화 | Tested | core/monitoring/metrics.py | test_prometheus_metrics.py (30) | HTTP latency/count, 컴포넌트 상태, 비즈니스 메트릭, 서킷브레이커 |
| json_structured_logging | JSON 구조화 로그 (운영 환경) | Tested | config/logging.py | test_prometheus_metrics.py (30) | 운영: JSON stdout + 파일 로테이션, 개발: 컬러 콘솔 (로깅 테스트 2건 포함) |
| canary_deployment | 카나리 배포 인프라 | Tested | nginx/nginx-canary.conf | test_canary_deployment.py (19) | nginx split_clients 트래픽 분할 (10→30→50→100%), docker-compose.canary.yml, canary_deploy.sh 5개 명령 |
| pre_deploy_check | 배포 전 자동 검증 스크립트 | Tested | scripts/pre_deploy_check.sh | test_pre_deploy_check.py (10) | 7단계 검증 (Git/린트/테스트/문서/Docker/환경변수/릴리즈게이트) |
| prometheus_alerting | Prometheus 알림 규칙 + Alertmanager | Tested | monitoring/prometheus/rules/aqts_alerts.yml | test_prometheus_alerting.py (17), test_alert_rules.py (10) | 7그룹 34규칙 (가용성/API성능/서킷브레이커/데이터수집/트레이딩/KIS복원/보안정합성/파이프라인), Alertmanager 텔레그램 연동, 심각도별 라우팅 |
| alembic_migrations | DB 스키마 마이그레이션 (Alembic) | Tested | alembic/env.py | test_alembic_migrations.py (11) | init_db.sql 베이스라인 마이그레이션, settings.py sync_url 연동, black post-write hook |
| jwt_security | JWT 보안 강화 (Key Rotation + Revocation) | Tested | api/middleware/auth.py | test_jwt_security.py (17) | kid 헤더 기반 key rotation, jti UUID4 + TokenRevocationStore, bcrypt 전용 인증, 로그아웃 엔드포인트 |
| ssh_hardening | CD 파이프라인 SSH 하드닝 | Tested | .github/workflows/cd.yml | test_ssh_hardening.py (7) | StrictHostKeyChecking no 제거, GCP_HOST_KEY 시크릿 기반 known_hosts 검증 (MITM 방지) |
| db_backup | DB 백업 자동화 (pg_dump + mongodump + GCS) | Tested | scripts/backup_db.sh | test_scheduler_separation.py (33) | 24시간 주기 cron 컨테이너, GCS 업로드, 로컬 보관 정리, 복원 스크립트 |
| pitr_wal_archive | PostgreSQL PITR (WAL 아카이빙) | Tested | docker-compose.yml | test_scheduler_separation.py (33) | wal_level=replica, archive_mode=on, 5분 archive_timeout, WAL 전용 볼륨 |
| scheduler_separation | 스케줄러 컨테이너 분리 (장애 격리) | Tested | scheduler_main.py | test_scheduler_separation.py (33) | SCHEDULER_ENABLED 환경변수, 헬스체크 external 상태, API/스케줄러 독립 장애 격리 |
| otel_tracing | OpenTelemetry 분산 추적 | Tested | core/monitoring/tracing.py | test_otel_tracing.py (28) | FastAPI/SQLAlchemy/httpx/Redis 자동 계측, OTel Collector + Jaeger, trace_id 로그/헤더 전파, NoOp fallback |
| env_bool_standardization | 환경변수 bool 표기 표준화 (env_bool 단일 진입점) | Tested | core/utils/env.py | test_env_bool.py (20) | 표준 'true'/'false' 강제, 하위호환 1/yes/on 경고 1회 + Prometheus counter, AQTS_STRICT_BOOL Phase 2 승격, 정적 검사 scripts/check_bool_literals.py |
| docs_ssot_automation | 문서 SSOT 자동화 (테스트 수/총계 자동 동기화) | Tested | scripts/gen_status.py | test_gen_status.py (14) | FEATURE_STATUS/README/release-gates 테스트 수치를 backend/tests AST 카운트로 자동 갱신, --check/--update 모드, changelog 라인 보존, Doc Sync 워크플로 통합 |

---

## 12. Roadmap Items (Completed - Stage 1)

| Module | Feature | Status | Code Path | Tests | Notes |
|--------|---------|--------|-----------|-------|-------|
| integration | 통합 테스트 (E2E) | Tested | core/pipeline.py | test_integration.py (30) | TradingGuard + ModeManager 연동 |
| realtime_pipeline_e2e | 실시간 파이프라인 E2E 통합 테스트 | Tested | core/scheduler_handlers.py | test_realtime_pipeline_e2e.py (25) | 마켓 데이 사이클, 장애 복원, RL-앙상블 블렌딩, 데이터 흐름, 레지스트리-추론 연동, Redis 캐시, 스케줄러 상태, IntradayBar 누적 |

---

## 13. Roadmap Items (Stages 2-8)

### Stage 2-A: Data Contracts ✅

| Item | Feature | Status | Code Path | Tests | Notes |
|------|---------|--------|-----------|-------|-------|
| contracts | PriceData Contract | Tested | contracts/price_data.py | test_contracts/test_price_data.py (16) | OHLC 일관성 검증, ticker 포맷 |
| contracts | FinancialData Contract | Tested | contracts/financial_data.py | test_contracts/test_financial_data.py (15) | filing_date ≥ period_end (look-ahead 방지) |
| contracts | NewsData Contract | Tested | contracts/news_data.py | test_contracts/test_news_data.py (17) | sentiment [-1,+1], 대소문자 무관 |
| contracts | FeatureVector Contract | Tested | contracts/feature_vector.py | test_contracts/test_feature_vector.py (16) | factor scores [-1,+1], RSI [0,100] |
| contracts | Signal Contract | Tested | contracts/signal.py | test_contracts/test_signal.py (18) | BUY/SELL confidence > 0 교차검증 |
| contracts | Portfolio Contract | Tested | contracts/portfolio.py | test_contracts/test_portfolio.py (17) | weight 합 ≈ 1.0 (±0.01), 중복 ticker 금지 |
| contracts | Order Contract | Tested | contracts/order.py | test_contracts/test_order.py (19) | LIMIT requires limit_price |
| contracts | Execution Contract | Tested | contracts/execution.py | test_contracts/test_execution.py (18) | filled ≤ requested, FILLED requires price |
| contracts | RiskCheck Contract | Tested | contracts/risk_check.py | test_contracts/test_risk_check.py (18) | BLOCK 일관성 검증 |
| config | operational_thresholds.yaml | Tested | config/operational_thresholds.yaml | test_operational_thresholds.py (9) | 전 스테이지 임계값 중앙관리 |

### Stage 2-B: Pipeline Gates ✅

| Item | Feature | Status | Code Path | Tests | Notes |
|------|---------|--------|-----------|-------|-------|
| gates | DataGate (수집 검증) | Tested | core/gates/data_gate.py | test_state_machine.py (59) | 수집 데이터 품질 검증 |
| gates | FactorGate (팩터 분석) | Tested | core/gates/factor_gate.py | test_state_machine.py (59) | 팩터 벡터 생성 품질 |
| gates | SignalGate (시그널 검증) | Tested | core/gates/signal_gate.py | test_state_machine.py (59) | 시그널 유효성 검증 |
| gates | EnsembleGate (앙상블 검증) | Tested | core/gates/ensemble_gate.py | test_state_machine.py (59) | 앙상블 결과 검증 |
| gates | PortfolioGate (포트폴리오 검증) | Tested | core/gates/portfolio_gate.py | test_state_machine.py (59) | 포트폴리오 구성 검증 |
| gates | TradingGuardGate (리스크 검증) | Tested | core/gates/trading_guard_gate.py | test_state_machine.py (59) | 포지션 리스크 사전검증 |
| gates | ReconGate (대사 검증) | Tested | core/gates/recon_gate.py | test_state_machine.py (59) | 거래-포지션 대사 |
| gates | ExecutionGate (체결 검증) | Tested | core/gates/execution_gate.py | test_state_machine.py (59) | 체결 성공/실패 검증 |
| gates | FillGate (채움 검증) | Tested | core/gates/fill_gate.py | test_state_machine.py (59) | 주문 완전성 검증 |
| state_machine | Pipeline State Machine | Tested | core/state_machine.py | test_state_machine.py (59) | 10-state 전이, FallbackHandler 연동 |
| schemas | GateResult Schema | Tested | core/gates/base.py | test_state_machine.py (59) | PASS/BLOCK, severity, context |
| registry | GateRegistry | Tested | core/gate_registry.py | test_state_machine.py (59) | 동적 등록, stop_on_block 지원 |

### Stage 3: Backtest Integrity & Advanced Realism ✅

| Item | Feature | Status | Code Path | Tests | Notes |
|------|---------|--------|-----------|-------|-------|
| backtest | BiasChecker (point-in-time, lookahead, survivorship) | Tested | core/backtest_engine/bias_checker.py | test_backtest_integrity.py (74) | 3가지 바이어스 탐지 |
| backtest | SlippageModel (spread, impact, slippage) | Tested | core/order_executor/slippage.py | test_backtest_integrity.py (74) | 스프레드+마켓임팩트 모델 |
| backtest | FillModel (partial fill, ADV cap) | Tested | core/backtest_engine/fill_model.py | test_backtest_integrity.py (74) | 부분 체결, 주문 분할 |
| backtest | CorporateActionProcessor | Tested | core/data_collector/corp_action.py | test_backtest_advanced.py (79) | 액면분할/배당 조정 |
| backtest | MarketImpactModel (Almgren-Chriss) | Tested | core/backtest_engine/impact_model.py | test_backtest_advanced.py (79) | 영구+일시적 임팩트 |
| backtest | TimeOfDayRules (KRX/NYSE) | Tested | core/order_executor/time_rules.py | test_backtest_advanced.py (79) | 장 시간/구간 규칙 |

### Stage 4: Decision Audit Trail ✅

| Item | Feature | Status | Code Path | Tests | Notes |
|------|---------|--------|-----------|-------|-------|
| audit | DecisionRecord + DecisionRecordStore | Tested | core/audit/decision_record.py | test_audit_trail.py (37) | 의사결정 기록 + 저장소 |
| audit | 5 Collectors (Input/Feature/Signal/Risk/Gate) | Tested | core/audit/collectors.py | test_audit_trail.py (37) | 파이프라인 단계별 수집기 |
| audit | REST API Endpoints (4개) | Tested | api/routes/audit.py | test_audit_trail.py (37) | 감사 기록 조회/필터 API |
| audit | Audit Trail Visualization | Tested | core/audit/visualization.py | test_audit_visualization.py (31) | 7단계 타임라인, 게이트 히트맵, 일별/시간별 집계, 상태 분포 |

### Stage 5: Capital Protection ✅

| Item | Feature | Status | Code Path | Tests | Notes |
|------|---------|--------|-----------|-------|-------|
| capital | CapitalBudget + AssetClassLimiter | Tested | core/capital_budget.py | test_capital_protection.py (98) | 자본 할당 및 자산군 한도 |
| reconciliation | ReconciliationEngine | Tested | core/reconciliation.py | test_capital_protection.py (98) | 일중/일말 정산 자동화 |
| protection | 5 Capital Guards | Tested | core/capital_protection.py | test_capital_protection.py (98) | 주문한도/호가검증/AI지연/API장애/현금바닥 |

### Stage 6: Performance Validation ✅

| Item | Feature | Status | Code Path | Tests | Notes |
|------|---------|--------|-----------|-------|-------|
| metrics | MetricsCalculator (9 metrics) | Tested | core/backtest_engine/metrics_calculator.py | test_performance_validation.py (73) | CAGR/MDD/Sharpe/Sortino/Calmar/IR/Hit/PF/Turnover |
| benchmark | BenchmarkManager (5 defaults) | Tested | core/backtest_engine/benchmark.py | test_performance_validation.py (73) | KOSPI/SP500/SPY/BALANCED/PASSIVE |
| regime | RegimeAnalyzer (4 regimes) | Tested | core/backtest_engine/regime_analyzer.py | test_performance_validation.py (73) | BULL/BEAR/HIGH_VOL/RISING_RATE |
| ablation | AblationStudy | Tested | core/backtest_engine/ablation.py | test_performance_validation.py (73) | 레이어별 기여도 분석 |
| significance | Bootstrap CI + t-test | Tested | core/backtest_engine/significance.py | test_performance_validation.py (73) | 통계적 유의성 검증 |
| judge | PerformanceJudge (PASS/REVIEW/FAIL) | Tested | core/backtest_engine/pass_fail.py | test_performance_validation.py (73) | 성과 합격 판정 |
| vol_scaling | 변동성 스케일링 (vol_target) | Tested | core/backtest_engine/engine.py | test_backtest_improvements.py (22) | 실현 변동성 역비례 포지션 조절 |
| gradual_reentry | 점진적 재진입 (gradual_reentry_days) | Tested | core/backtest_engine/engine.py | test_backtest_improvements.py (22) | 쿨다운 후 10%→100% 선형 복귀 |
| dynamic_threshold | 동적 임계값 (레짐 기반) | Tested | core/backtest_engine/engine.py | test_backtest_improvements.py (22) | 레짐별 매수/매도 임계값 자동 조정 (regime.py 연동) |

### Stage 7: LLM Promotion ✅

| Item | Feature | Status | Code Path | Tests | Notes |
|------|---------|--------|-----------|-------|-------|
| drift | DriftMonitor (KS-test) | Tested | core/ai_analyzer/drift_monitor.py | test_llm_promotion.py (49) | 분포 드리프트 탐지 |
| cost | CostAnalyzer | Tested | core/ai_analyzer/cost_analyzer.py | test_llm_promotion.py (49) | API 비용 대비 수익 분석 |
| reproducibility | ReproducibilityTest | Tested | core/ai_analyzer/reproducibility.py | test_llm_promotion.py (49) | 결정 재현성 검증 |
| promotion | PromotionChecklist (PROMOTE/HOLD/DEMOTE) | Tested | core/ai_analyzer/promotion_checklist.py | test_llm_promotion.py (49) | 2-tier 승격 체계 |

### Stage 8-B: Parameter Sensitivity Analysis ✅

| Item | Feature | Status | Code Path | Tests | Notes |
|------|---------|--------|-----------|-------|-------|
| models | 민감도 분석 데이터 모델 (ParamRange/Trial/Elasticity/Run) | Tested | core/param_sensitivity/models.py | test_param_sensitivity.py (40) | SweepMethod, ParamCategory Enum, impact_score 가중평균 |
| sweep_generator | 파라미터 스윕 생성기 (Grid/Random/OAT) | Tested | core/param_sensitivity/sweep_generator.py | test_param_sensitivity.py (40) | 10개 기본 파라미터, max_trials 캡, OAT 스윕 |
| analyzer | 민감도 분석기 (탄성치/토네이도/안정구간) | Tested | core/param_sensitivity/analyzer.py | test_param_sensitivity.py (40) | 탄성치 계산, 단조성, 토네이도 랭킹 |
| engine | ParamSensitivityEngine (BacktestEngine 래핑) | Tested | core/param_sensitivity/engine.py | test_param_sensitivity.py (40) | OAT/Grid 스윕, 최적 파라미터 탐색 |
| api | 민감도 분석 REST API (3 엔드포인트) | Tested | api/routes/param_sensitivity.py | test_param_sensitivity.py (40) | run/latest/tornado |
| integration | E2E 파이프라인 + 비용 민감도 직관 검증 | Tested | core/param_sensitivity/engine.py | test_param_sensitivity.py (40) | 전체 워크플로, 비용 파라미터 영향도 |

### Stage 8: OOS Validation ✅

| Item | Feature | Status | Code Path | Tests | Notes |
|------|---------|--------|-----------|-------|-------|
| oos_models | OOS 데이터 모델 (Run/Metric/Window/Gate) | Tested | core/oos/models.py | test_oos.py (55) | OOSRun, 6 Enum, Shadow 확장 필드 |
| walk_forward | Walk-Forward 엔진 (BacktestEngine 재사용) | Tested | core/oos/walk_forward.py | test_oos.py (55) | 기간 분할, 윈도우별 백테스트, 집계 |
| gate_evaluator | 3단계 Gate 평가 (A/B/C) | Tested | core/oos/gate_evaluator.py | test_oos.py (55) | MDD/Sharpe/Calmar/Variance 임계값 |
| regime_mapping | 레짐 매핑 레이어 (실시간↔백테스트) | Tested | core/oos/regime_mapping.py | test_oos.py (55) | dict 기반 양방향 매핑, 폴백 정책 |
| job_manager | OOS 작업 관리자 (싱글톤, 멱등성) | Tested | core/oos/job_manager.py | test_oos.py (55) | 파라미터 해시 기반 중복 방지 |
| oos_api | OOS REST API (4 엔드포인트) | Tested | api/routes/oos.py | test_oos.py (55) | run/latest/gate-status/detail |

### Stage 9: RL v2 & Production Pipeline ✅

| Item | Feature | Status | Code Path | Tests | Notes |
|------|---------|--------|-----------|-------|-------|
| data_loader | RL 데이터 로더 (DB OHLCV → 학습 데이터) | Tested | core/rl/data_loader.py | test_rl_v2.py (28) | 종목별 OHLCV 로드, 정규화, 결측치 보간 |
| multi_asset_env | 멀티에셋 트레이딩 환경 (Gymnasium) | Tested | core/rl/multi_asset_env.py | test_rl_v2.py (28) | 다종목 동시 관찰·행동, 포트폴리오 보상 |
| hyperopt_rl | RL 하이퍼파라미터 최적화 (Optuna) | Tested | core/rl/hyperopt_rl.py | test_rl_v2.py (28) | PPO/SAC 하이퍼파라미터 탐색, FrozenTrial 기반 |
| model_registry | RL 모델 레지스트리 (버전 관리) | Tested | core/rl/model_registry.py | test_rl_production.py (20) | 버전 등록, OOS Sharpe 기반 챔피언 선정, manifest.json |
| inference | RL 추론 서비스 (배치 추론·시그널 변환) | Tested | core/rl/inference.py | test_rl_production.py (20) | 챔피언 모델 자동 로드, 앙상블 블렌딩 (RL 40% + 앙상블 60%), 섀도 모드 |
| run_rl_training | RL 학습/평가 CLI (레지스트리 연동) | Tested | scripts/run_rl_training.py | test_rl_production.py (20) | --registry-dir, --no-register, 자동 등록 |

---

## Remaining Not Started Items (0)

모든 기능이 구현/테스트 완료되었습니다.

---

## Operations Documents

| Doc ID | Document | Path | Description |
|--------|----------|------|-------------|
| OPS-001 | 릴리즈 게이트 | docs/operations/release-gates.md | Gate A~E 통과 기준 |
| OPS-002 | 인시던트 런북 | docs/operations/incident-runbook.md | 장애 진단 및 복구 절차 |
| OPS-003 | 거래 중지 정책 | docs/operations/trading-halt-policy.md | 자동/수동 중지 조건 |
| OPS-004 | 모델 변경 정책 | docs/operations/model-change-policy.md | ML/LLM 모델 변경 절차 |
| OPS-005 | 롤백 계획 | docs/operations/rollback-plan.md | 장애 시 롤백 절차 |
| OPS-006 | 고객 공지 | docs/operations/customer-notice.md | 투자 위험 고지 |
| OPS-007 | Docker 환경 세팅 | docs/operations/docker-setup-guide.md | 개발/스테이징/프로덕션 환경 구성 |
| OPS-008 | 배포 및 검증 로드맵 | docs/operations/deployment-roadmap.md | Phase 0~4 단계별 배포/검증/확장 절차 |
| OPS-009 | GCP 프로비저닝 가이드 | docs/operations/gcp-provisioning-guide.md | GCP VM 생성, 방화벽, SSL, 배포, NCP 이전 절차 |
| OPS-010 | CI/CD 파이프라인 | .github/workflows/ci.yml, cd.yml | GitHub Actions CI/CD (Lint→Smoke→Test→Build→Deploy) |

---

## Known Technical Debt

| Item | Description | Priority | Status |
|------|-------------|----------|--------|
| ~~pipeline_integration~~ | ~~Pipeline ↔ Gate/StateMachine 미연결~~ | ~~P1~~ | ✅ Resolved |
| ~~contracts_unused~~ | ~~Contracts 실사용 연결 없음~~ | ~~P1~~ | ✅ Resolved |
| ~~no_request_id~~ | ~~RequestLoggingMiddleware에 추적 체계 없음~~ | ~~P2~~ | ✅ Resolved |
| ~~startup_todo~~ | ~~main.py 스케줄러/토큰 TODO 잔존~~ | ~~P1~~ | ✅ Resolved |
| ~~no_test_markers~~ | ~~pytest smoke/regression 마커 없음~~ | ~~P2~~ | ✅ Resolved |
| ~~pre_existing_test_failures~~ | ~~79개 테스트 이벤트 루프 의존 실패~~ — conftest.py deprecated session-scoped event_loop 제거 + IsolatedAsyncioTestCase 전환으로 전면 해소 (1,769 all pass) | ~~P2~~ | ✅ Resolved |

---

## Test Coverage Summary

```
Total Tests: 3,939 tests (413 smoke-marked) — ALL PASS, Coverage 90%
├── Core Features: 40+ modules with passing tests
├── Data Contracts: 154 tests (9 contracts) [smoke]
├── Pipeline Gates: 59 tests (12 components)
├── Pipeline ↔ Gate Integration: 20 tests
├── Contract Converters: 20 tests [smoke]
├── Request Logging Middleware: 10 tests [smoke]
├── Startup Health: 6 tests
├── Health Checker: 19 tests [smoke]
├── System API Routes: 14 tests [smoke]
├── Financial Collector: 36 tests [smoke]
├── Social Collector: 59 tests [smoke]
├── Opinion Generator: 38 tests [smoke]
├── Prompt Manager: 43 tests [smoke]
├── Backtest Integrity: 153 tests (integrity + advanced)
├── Audit Trail: 37 tests
├── Capital Protection: 98 tests
├── Performance Validation: 73 tests
├── LLM Promotion: 49 tests
├── Rate Limiting: 7 tests [NEW]
├── Circuit Breaker: 17 tests [NEW]
├── Regime Detection: 31 tests [NEW]
├── OOS Validation: 55 tests [NEW]
├── Gate B Security: 10 tests [NEW]
├── Gate C Loss Simulation: 22 tests [NEW]
├── Gate C Halt/Resume: 20 tests [NEW]
├── Parameter Sensitivity: 40 tests [NEW]
├── Gate C Notification: 46 tests [NEW]
├── Gate D Compliance: 57 tests [NEW]
├── Gate D Report+Secret: 40 tests [NEW]
├── Gate E Monitoring: 53 tests [NEW]
├── Audit Visualization: 31 tests [NEW]
├── Infrastructure: 70 tests [NEW] (database/settings/constants/logging/audit_log)
├── RL v2: 28 tests [NEW] (data_loader/multi_asset_env/hyperopt_rl)
├── RL Production: 20 tests [NEW] (model_registry/inference/scheduler 통합)
├── Realtime Data: 20 tests [NEW] (kis_websocket/realtime_manager/realtime API)
├── Realtime Pipeline E2E: 25 tests [NEW] (마켓사이클/장애복원/RL블렌딩/레지스트리연동)
├── Integration Tests: 30 tests (E2E scenarios)
├── API Routes Coverage v2: 76 tests [NEW] (market/portfolio/orders/audit/realtime/profile/alerts/param_sensitivity/oos)
├── Data Collectors Coverage v2: 81 tests [NEW] (market_data/economic/news/kis_websocket)
├── Stress/Load Tests: 28 tests [NEW] (백테스트 스케일링/동시성/상태머신/API부하/파이프라인/메모리/서킷브레이커)
├── API Tests: 73 tests (all endpoints)
├── Smoke Tests: 413 tests (< 13초, CI 필수)
└── Remaining Uncovered:
    └── 0 Not Started items (ALL FEATURES IMPLEMENTED)
    └── 0 Implemented items (ALL FEATURES TESTED)
```

---

## Resolved Technical Debt (2026-04-04)

1. ✅ **Pipeline ↔ Gate Integration**: InvestmentDecisionPipeline에 GateRegistry + PipelineStateMachine + FallbackHandler 직접 연결. DataGate/SignalGate/EnsembleGate 평가, BLOCK 시 폴백 상태 전이, PipelineResult로 gate_results 수집.
2. ✅ **Contracts 실사용**: contracts/converters.py 추가. SignalGenerator.generate_all_signals()에서 계약 검증, OrderExecutor.execute_order()에서 OrderIntent 계약 강제.
3. ✅ **Request ID 도입**: RequestLoggingMiddleware에 X-Request-ID/X-Correlation-ID 생성·전파·응답 헤더 포함. request.state로 하위 핸들러 접근 가능.
4. ✅ **Startup TODO 제거**: TradingScheduler 시작 + KIS 토큰 초기화 구현. 실패 시 degraded 모드 + health 엔드포인트 반영.
5. ✅ **Test Markers**: pytest smoke/regression/integration/slow 마커 도입. CI에 smoke-tests 별도 job 추가 (full suite는 smoke 통과 후 실행).

---

## Legend

- **Status Levels**:
  - `Not Started`: 계획/설계만 존재, 구현 시작 전
  - `In Progress`: 구현 진행 중
  - `Implemented`: 코드 완성, 테스트 미작성/미통과
  - `Tested`: 단위/통합 테스트 통과, 운영 준비도 미검증
  - `Production-ready`: 테스트 통과 + Gate 통합 + Audit 연동 + 실거래 투입 가능
  - `Blocked`: 선행 조건 미충족

- **Code Path**: 상대경로 (backend/ 기준)
- **Tests**: test_file.py (count) 형식, 또는 "(no tests)" 표기

---

Last reviewed: 2026-04-05 | Maintained by: AQTS Team
