# AQTS Feature Status Matrix

> Single Source of Truth — 모든 기능의 구현 상태를 추적합니다.
>
> **성숙도 레벨**: Not Started → In Progress → Implemented → Tested → Production-ready → Blocked
>
> Last updated: 2026-04-04

## Status Summary

| Status | Count |
|--------|-------|
| Not Started | 36 |
| Implemented | 11 |
| Tested | 41 |
| Production-ready | 0 |
| Blocked | 0 |
| **TOTAL** | **88** |

---

## 1. Data Collection

| Module | Feature | Status | Code Path | Tests | Notes |
|--------|---------|--------|-----------|-------|-------|
| kis_client | KIS API 래퍼 (LIVE/DEMO/BACKTEST) | Tested | core/data_collector/kis_client.py | test_kis_client.py (14) | 한국투자증권 OpenAPI 통합, 3가지 모드 지원 |
| market_data | 시세 데이터 수집 및 무결성 검증 | Tested | core/data_collector/market_data.py | test_market_data.py (12) | 가격 데이터 OHLCV, 이상치 검증 |
| news_collector | RSS 뉴스 + DART 공시 수집 | Tested | core/data_collector/news_collector.py | test_news_collector.py (13) | Naver/Hankyung/Maekyung/Reuters 4개 소스 |
| economic_collector | FRED·ECOS 경제지표 수집 | Tested | core/data_collector/economic_collector.py | test_economic_collector.py (17) | 미국 9개 + 한국 5개 지표 |
| financial_collector | DART 재무제표 (하이브리드) | Implemented | core/data_collector/financial_collector.py | (no tests) | API + 일괄 txt, PER/PBR/ROE 파생 |
| social_collector | Reddit SNS 데이터 수집 | Implemented | core/data_collector/social_collector.py | (no tests) | OAuth2, 8개 서브레딧, 키워드 필터 |

---

## 2. Quant Engine

| Module | Feature | Status | Code Path | Tests | Notes |
|--------|---------|--------|-----------|-------|-------|
| factor_analyzer | 5팩터 분석 (Value·Momentum·Quality·LowVol·Size) | Tested | core/quant_engine/factor_analyzer.py | test_factor_analyzer.py (21) | Z-Score 정규화, Cross-Market 재정규화 |
| signal_generator | 기술적 시그널 생성 (RSI·MACD·Bollinger) | Tested | core/quant_engine/signal_generator.py | test_signal_generator.py (20) | 5개 기술적 시그널 |
| backtest_engine | 백테스트 엔진 + 전략 비교 | Tested | core/backtest_engine/engine.py | test_backtest_engine.py (25) | Sharpe/Alpha/Beta/Tracking Error 지표 |

---

## 3. AI Analyzer

| Module | Feature | Status | Code Path | Tests | Notes |
|--------|---------|--------|-----------|-------|-------|
| sentiment | Claude Haiku 감성 분석 (Mode A) | Tested | core/ai_analyzer/sentiment.py | test_sentiment.py (9) | 뉴스/공시 감성 점수 (-1.0 ~ +1.0) |
| opinion | Claude Sonnet 투자 의견 (Mode B) | Implemented | core/ai_analyzer/opinion.py | (no tests) | STOCK·SECTOR·MACRO 3단계 의견 |
| prompt_manager | 프롬프트 DB 버전 관리 | Implemented | core/ai_analyzer/prompt_manager.py | (no tests) | MongoDB 버전관리, 롤백, A/B 테스트 |

---

## 4. Strategy & Portfolio

| Module | Feature | Status | Code Path | Tests | Notes |
|--------|---------|--------|-----------|-------|-------|
| ensemble | 가중 앙상블 (Quant 4 + AI 감성 + Sharpe 재보정) | Tested | core/strategy_ensemble/engine.py | test_ensemble.py (13) | 4개 전략 통합 가중평균 |
| profile | 투자자 프로필 (위험성향·스타일·손실허용도) | Tested | core/portfolio_manager/profile.py | test_profile.py (22) | 5단계 위험성향 분류 |
| construction | 포트폴리오 구성 (MVO·Risk Parity·Black-Litterman) | Tested | core/portfolio_manager/construction.py | test_construction.py (77) | 3중 엔진, Ledoit-Wolf 축소, USD 하드캡 |
| rebalancing | 리밸런싱 (정기·긴급·방어) | Tested | core/portfolio_manager/rebalancing.py | test_rebalancing.py (36) | 3가지 리밸런싱 모드 |
| universe | 투자 유니버스 관리 | Tested | core/portfolio_manager/universe.py | test_universe.py (29) | 섹터 필터, 지정 종목, 유동성 필터 |
| exchange_rate | 환율 관리 (KIS+FRED, Redis 캐싱) | Tested | core/portfolio_manager/exchange_rate.py | test_exchange_rate.py (39) | 5분/24시간 TTL 캐시 |
| weight_optimizer | 가중치 자동 최적화 (Sharpe·Risk-Adjusted 등) | Tested | core/weight_optimizer.py | test_weight_optimizer.py (32) | 4가지 최적화 방식, Walk-Forward 포함 |

---

## 5. Order Execution & Risk

| Module | Feature | Status | Code Path | Tests | Notes |
|--------|---------|--------|-----------|-------|-------|
| executor | 주문 집행 (시장가·지정가·TWAP·VWAP) | Tested | core/order_executor/executor.py | test_executor.py (33) | TWAP 6분할, VWAP 가중치, 배치 실행 |
| trading_guard | 트레이딩 안전 장치 (7계층 보호) | Tested | core/trading_guard.py | test_trading_guard.py (72) | 환경·자본·손실·MDD·연속손실 검증 |
| emergency_monitor | 비상 리밸런싱 5분 모니터 | Tested | core/emergency_monitor.py | test_emergency_monitor.py (64) | 동적 손절, 방어 포트폴리오 전환 |

---

## 6. Operations & Monitoring

| Module | Feature | Status | Code Path | Tests | Notes |
|--------|---------|--------|-----------|-------|-------|
| mode_manager | 모드 전환 (BACKTEST→DEMO→LIVE) | Tested | core/mode_manager.py | test_mode_manager.py (41) | 조건 검증, 비상 다운그레이드, 이력 기록 |
| demo_verifier | DEMO 실전 가동 검증 (11항목) | Tested | core/demo_verifier.py | test_demo_verifier.py (73) | 11개 종합 체크리스트 |
| health_checker | 시스템 건전성 검사 (5항목) | Implemented | core/health_checker.py | (no tests) | DB·설정·모드 종합 점검 |
| trading_scheduler | 자동화 스케줄러 (KRX 장 시간 기반) | Tested | core/trading_scheduler.py | test_trading_scheduler.py (76) | 5단계 자동화, 거래일 판별 |
| daily_reporter | 일일 리포트 생성 및 발송 | Tested | core/daily_reporter.py | test_daily_reporter.py (70) | 수익률·거래·Top3 리포트 |
| daily_reporter_top_bottom | Top/Bottom 3 종목 자동 추출 | Tested | core/daily_reporter.py | test_daily_reporter_top_bottom.py (5) | 수익률 기준 상위/하위 종목 |
| periodic_reporter | 주간/월간 리포트 | Tested | core/periodic_reporter.py | test_periodic_reporter.py (27) | MDD/Sharpe 분석, 벤치마크 비교 |
| market_calendar | 마켓 캘린더 (KRX + NYSE) | Tested | core/market_calendar.py | test_market_calendar.py (44) | 미국 공휴일 자동 산출, DST 판별 |
| graceful_shutdown | 그레이스풀 셧다운 매니저 | Tested | core/graceful_shutdown.py | test_graceful_shutdown.py (25) | 3단계 셧다운, 주문 드레이닝 |

---

## 7. API Layer

| Module | Feature | Status | Code Path | Tests | Notes |
|--------|---------|--------|-----------|-------|-------|
| middleware/auth | JWT 인증 (HS256 Bearer Token) | Tested | api/middleware/auth.py | test_api.py (59) | 단일 사용자, bcrypt/평문 지원 |
| middleware/request_logger | 요청 로깅 미들웨어 | Tested | api/middleware/request_logger.py | test_api.py (59) | HTTP 요청/응답 로깅 |
| routes/auth | 인증 (로그인·토큰 갱신) | Tested | api/routes/auth.py | test_api.py (59) | 로그인, 토큰 갱신 엔드포인트 |
| routes/portfolio | 포트폴리오 (요약·보유·성과) | Tested | api/routes/portfolio.py | test_api.py (59) | 포트폴리오 조회, 성과 분석 |
| routes/orders | 주문 (생성·배치·조회·취소) | Tested | api/routes/orders.py | test_api.py (59) | 주문 CRUD 작업 |
| routes/profile | 투자자 프로필 (조회·수정) | Tested | api/routes/profile.py | test_api.py (59) | 프로필 조회 및 수정 |
| routes/market | 시장 정보 (환율·지수·지표·유니버스) | Tested | api/routes/market.py | test_api.py (59) | 시장 데이터 조회 |
| routes/alerts | 알림 (이력·통계·확인) | Tested | api/routes/alerts.py | test_api.py (59) | 알림 관리 |
| routes/system | 시스템 (설정·백테스트·리밸런싱) | Tested | api/routes/system.py | test_api.py (59) | 시스템 관리 엔드포인트 |
| schemas | Pydantic 요청/응답 모델 | Tested | api/schemas/common.py | test_api.py (59) | 18개 클래스, 6개 스키마 모듈 |

---

## 8. Notifications & Logging

| Module | Feature | Status | Code Path | Tests | Notes |
|--------|---------|--------|-----------|-------|-------|
| alert_manager | 알림 생성·관리·이력 (템플릿 기반) | Tested | core/notification/alert_manager.py | test_notification.py (72) | 레벨 필터링, MongoDB/메모리 이중 저장 |
| telegram_notifier | 텔레그램 봇 알림 발송 | Tested | core/notification/telegram_notifier.py | test_notification.py (72) | 레벨 필터(ALL/IMPORTANT/ERROR), 재시도 3회 |
| audit_log | 감사 로그 (결정 추적) | Implemented | db/repositories/audit_log.py | (no tests) | 결정 감사 추적 |

---

## 9. Infrastructure & Configuration

| Module | Feature | Status | Code Path | Tests | Notes |
|--------|---------|--------|-----------|-------|-------|
| database | DB 연결 관리 (PostgreSQL·MongoDB·Redis) | Implemented | db/database.py | (no tests) | 3개 데이터베이스 통합 |
| settings | 환경변수 기반 설정 (pydantic-settings) | Implemented | config/settings.py | (no tests) | Pydantic-settings 기반 설정 |
| constants | 상수·Enum 정의 | Implemented | config/constants.py | (no tests) | 시스템 상수 정의 |
| logging | Loguru 로깅 설정 | Implemented | config/logging.py | (no tests) | 중앙 로깅 설정 |
| main | FastAPI 엔트리포인트 | Tested | main.py | test_integration.py (30) | Lifespan, GracefulShutdownManager 통합 |

---

## 10. Roadmap Items (Pending - Stage 1)

| Module | Feature | Status | Code Path | Tests | Notes |
|--------|---------|--------|-----------|-------|-------|
| integration | 통합 테스트 (E2E) | Tested | core/pipeline.py | test_integration.py (30) | TradingGuard + ModeManager 연동 |

---

## 11. Roadmap Items (Pending - Stages 2-7)

### Stage 2-A: Data Contracts

| Item | Feature | Status | Code Path | Tests | Notes |
|------|---------|--------|-----------|-------|-------|
| contracts | PriceData Contract | Not Started | (pending) | N/A | 시가·종가·거래량 스키마 정의 |
| contracts | FinancialData Contract | Not Started | (pending) | N/A | 재무제표 표준 스키마 |
| contracts | NewsData Contract | Not Started | (pending) | N/A | 뉴스 메타데이터 스키마 |
| contracts | FeatureVector Contract | Not Started | (pending) | N/A | AI/Quant 피처 벡터 스키마 |
| contracts | Signal Contract | Not Started | (pending) | N/A | 투자 시그널 표준 형식 |
| contracts | Portfolio Contract | Not Started | (pending) | N/A | 포트폴리오 상태 스키마 |
| contracts | Order Contract | Not Started | (pending) | N/A | 주문 실행 계약 |
| contracts | Execution Contract | Not Started | (pending) | N/A | 체결 결과 계약 |
| contracts | RiskCheck Contract | Not Started | (pending) | N/A | 리스크 검증 계약 |
| config | operational_thresholds.yaml | Not Started | (pending) | N/A | 운영 임계값 정의 파일 |

### Stage 2-B: Pipeline Gates

| Item | Feature | Status | Code Path | Tests | Notes |
|------|---------|--------|-----------|-------|-------|
| gates | DataGate (수집 검증) | Not Started | (pending) | N/A | 수집 데이터 품질 검증 |
| gates | QualityGate (데이터 품질) | Not Started | (pending) | N/A | 이상치·누락 검증 |
| gates | FeatureGate (피처 생성) | Not Started | (pending) | N/A | 피처 벡터 생성 품질 |
| gates | SignalGate (시그널 검증) | Not Started | (pending) | N/A | 시그널 유효성 검증 |
| gates | DecisionGate (투자결정) | Not Started | (pending) | N/A | 포트폴리오 구성 검증 |
| gates | RiskGate (리스크 검증) | Not Started | (pending) | N/A | 포지션 리스크 검증 |
| gates | OrderGate (주문 검증) | Not Started | (pending) | N/A | 주문 매개변수 검증 |
| gates | ExecutionGate (체결 검증) | Not Started | (pending) | N/A | 체결 성공 검증 |
| gates | FillGate (채우기 검증) | Not Started | (pending) | N/A | 주문 완전성 검증 |
| state_machine | Pipeline State Machine | Not Started | (pending) | N/A | Gate 간 상태 전이 자동화 |
| schemas | GateResult Schema | Not Started | (pending) | N/A | Gate 결과 표준 형식 |
| registry | GateRegistry | Not Started | (pending) | N/A | Gate 등록/관리 메커니즘 |

### Stage 3-A: Backtest Integrity

| Item | Feature | Status | Code Path | Tests | Notes |
|------|---------|--------|-----------|-------|-------|
| backtest | Backtest Integrity Tools | Not Started | (pending) | N/A | 백테스트 검증, 과적합 탐지 |
| backtest | Out-of-Sample Validation | Not Started | (pending) | N/A | Walk-Forward 분석 자동화 |
| backtest | Parameter Sensitivity Analysis | Not Started | (pending) | N/A | 파라미터 민감도 분석 |

### Stage 4: Audit Trail

| Item | Feature | Status | Code Path | Tests | Notes |
|------|---------|--------|-----------|-------|-------|
| audit | Decision Audit Trail (구조화) | Not Started | (pending) | N/A | 모든 결정 추적 (입력→출력→선택) |
| audit | Audit Trail Visualization | Not Started | (pending) | N/A | 감사 추적 시각화 |
| audit | Regulatory Compliance Reports | Not Started | (pending) | N/A | 규제 준수 리포트 자동 생성 |

### Stage 5: Capital & Reconciliation

| Item | Feature | Status | Code Path | Tests | Notes |
|------|---------|--------|-----------|-------|-------|
| capital | Capital Budget (할당·추적) | Not Started | (pending) | N/A | 자본 할당 및 추적 |
| reconciliation | Reconciliation (거래·가격·포지션) | Not Started | (pending) | N/A | 일중/일말 정산 자동화 |
| reconciliation | Reconciliation Report | Not Started | (pending) | N/A | 정산 이상 보고 |

### Stage 6: Performance Validation

| Item | Feature | Status | Code Path | Tests | Notes |
|------|---------|--------|-----------|-------|-------|
| performance | Performance Validation Framework | Not Started | (pending) | N/A | 성과 검증 자동화 |
| performance | Benchmark Rebalancing Logic | Not Started | (pending) | N/A | 벤치마크 대비 성과 추적 |

### Stage 7: LLM Promotion

| Item | Feature | Status | Code Path | Tests | Notes |
|------|---------|--------|-----------|-------|-------|
| llm | LLM 승격 메커니즘 (Haiku→Sonnet) | Not Started | (pending) | N/A | 운영 성과 기반 자동 승격 |
| llm | LLM 성과 추적 | Not Started | (pending) | N/A | Haiku/Sonnet 성과 비교 |

---

## Feature Implementation Status Details

### Fully Tested (41 modules)
Modules with passing unit/integration tests:
- Data Collection: kis_client, market_data, news_collector, economic_collector
- Quant Engine: factor_analyzer, signal_generator, backtest_engine
- AI Analyzer: sentiment (Mode A only)
- Strategy & Portfolio: ensemble, profile, construction, rebalancing, universe, exchange_rate, weight_optimizer
- Order Execution: executor, trading_guard, emergency_monitor
- Operations: mode_manager, demo_verifier, trading_scheduler, daily_reporter, periodic_reporter, market_calendar, graceful_shutdown
- API Layer: all routes, schemas, middleware
- Notifications: alert_manager, telegram_notifier
- Integration: test_integration (E2E)

### Implemented but Not Tested (11 modules)
Modules with code but no dedicated tests:
- financial_collector (DART 재무제표)
- social_collector (Reddit SNS)
- opinion (Claude Sonnet 의견)
- prompt_manager (프롬프트 관리)
- health_checker (시스템 점검)
- audit_log (감사 로그)
- database (DB 연결)
- settings, constants, logging (설정)

### Not Started (36 items)
Pending roadmap items for Stages 2-7:
- 9 Data Contracts (Stage 2-A)
- operational_thresholds.yaml
- 9 Pipeline Gates (Stage 2-B)
- Pipeline State Machine, GateResult Schema, GateRegistry
- Backtest Integrity Tools (Stage 3-A)
- Decision Audit Trail (Stage 4)
- Capital Budget & Reconciliation (Stage 5)
- Performance Validation (Stage 6)
- LLM Promotion (Stage 7)

---

## Test Coverage Summary

```
Total Tests (Phase 11 Complete): 1,084 tests
├── Core Features: 33 modules with passing tests
├── Integration Tests: 30 tests (E2E scenarios)
├── API Tests: 59 tests (all endpoints)
├── Test Categories:
│   ├── Unit Tests: ~800 tests
│   ├── Integration Tests: 30 tests
│   └── E2E Tests: 30 tests
└── Uncovered Areas:
    ├── financial_collector (DART hybrid)
    ├── social_collector (Reddit OAuth2)
    ├── opinion (Claude Sonnet)
    ├── prompt_manager (MongoDB versioning)
    ├── health_checker (system validation)
    └── Roadmap items (pending implementation)
```

---

## Next Steps (Stage 1 → Stage 2)

1. **Stage 2-A: Data Contracts**
   - Define 9 contract schemas (PriceData, FinancialData, NewsData, etc.)
   - Create operational_thresholds.yaml for threshold management
   - Implement contract validation framework

2. **Stage 2-B: Pipeline Gates**
   - Implement 9 pipeline gates (DataGate through FillGate)
   - Create Pipeline State Machine for gate orchestration
   - Build GateRegistry for dynamic gate management

3. **Stage 3-A: Backtest Integrity**
   - Add backtest validation tools
   - Implement Walk-Forward analysis
   - Parameter sensitivity analysis framework

4. **Stage 4: Audit Trail**
   - Expand decision audit trail with full decision path tracking
   - Create audit visualization dashboard
   - Generate regulatory compliance reports

5. **Stage 5: Capital & Reconciliation**
   - Implement capital budget allocation and tracking
   - Create reconciliation engine (intra-day and EOD)
   - Build reconciliation exception reports

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

Last reviewed: 2026-04-04 | Maintained by: AQTS Team
