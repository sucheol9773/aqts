# AQTS - AI Quant Trade System

AI 기반 정량·정성적 분석 통합 퀀트 트레이딩 시스템

## 시스템 구성

| 구성요소 | 기술 |
|---------|------|
| Backend | Python 3.11 + FastAPI |
| Database | PostgreSQL 16 + TimescaleDB 2.14 |
| Document DB | MongoDB 7.0 |
| Cache/Queue | Redis 7.2 |
| AI/LLM | Anthropic Claude API (Haiku 4.5 + Sonnet 4) |
| Broker | 한국투자증권 OpenAPI |
| Container | Docker + Docker Compose |

## 시작하기

### 1. 환경변수 설정

```bash
cp .env.example .env
# .env 파일을 열어 실제 값을 입력하세요
```

### 2. Docker 실행

```bash
docker-compose up -d
```

### 3. 시스템 헬스체크

```bash
curl http://localhost:8000/api/system/health
```

## 프로젝트 구조

```
aqts/
├── docker-compose.yml
├── .env.example
├── .gitignore
├── README.md
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── pyproject.toml
│   ├── main.py                      # FastAPI 엔트리포인트
│   ├── config/
│   │   ├── settings.py              # 환경변수 기반 설정 (pydantic-settings)
│   │   ├── constants.py             # 상수·Enum 정의
│   │   └── logging.py               # Loguru 로깅 설정
│   ├── core/
│   │   ├── data_collector/
│   │   │   ├── kis_client.py        # 한투 API 래퍼 (LIVE/DEMO/BACKTEST)
│   │   │   ├── market_data.py       # 시세 데이터 수집·무결성 검증
│   │   │   ├── news_collector.py    # RSS 뉴스 + DART 공시 수집
│   │   │   ├── economic_collector.py # FRED·ECOS 경제지표 수집
│   │   │   ├── financial_collector.py # DART 재무제표 (하이브리드)
│   │   │   └── social_collector.py  # Reddit SNS 수집
│   │   ├── quant_engine/
│   │   │   ├── factor_analyzer.py   # 5팩터 분석 (Value·Momentum·Quality·LowVol·Size)
│   │   │   └── signal_generator.py  # 기술적 시그널 생성
│   │   ├── ai_analyzer/
│   │   │   ├── sentiment.py         # Mode A: Claude Haiku 감성 분석
│   │   │   ├── opinion.py           # Mode B: Claude Sonnet 투자 의견 (STOCK·SECTOR·MACRO)
│   │   │   └── prompt_manager.py    # 프롬프트 DB 버전 관리
│   │   ├── strategy_ensemble/
│   │   │   └── engine.py            # 가중 앙상블 + Sharpe 기반 재보정
│   │   ├── backtest_engine/
│   │   │   └── engine.py            # 백테스트 엔진 + 전략 비교
│   │   ├── pipeline.py              # 투자 의사결정 통합 파이프라인
│   │   ├── portfolio_manager/
│   │   │   ├── profile.py           # 투자자 프로필 관리 (위험성향·스타일)
│   │   │   ├── construction.py      # 포트폴리오 구성 (MVO·Risk Parity·Black-Litterman)
│   │   │   ├── rebalancing.py       # 리밸런싱 엔진 (정기·긴급·방어)
│   │   │   ├── universe.py          # 투자 유니버스 관리
│   │   │   └── exchange_rate.py     # 환율 관리 (KIS+FRED, Redis 캐싱)
│   │   ├── order_executor/
│   │   │   └── executor.py          # 주문 집행 (시장가·지정가·TWAP·VWAP)
│   │   ├── trading_guard.py         # 트레이딩 안전 장치 (7계층 보호)
│   │   ├── health_checker.py        # 시스템 건전성 검사 (5항목)
│   │   ├── mode_manager.py          # 모드 전환 관리 (BACKTEST→DEMO→LIVE)
│   │   ├── demo_verifier.py         # DEMO 모드 실전 가동 검증 (11항목)
│   │   ├── trading_scheduler.py     # 모의투자 자동화 스케줄러 (KRX 장 시간)
│   │   ├── daily_reporter.py        # 일일 리포트 자동 생성·발송
│   │   ├── emergency_monitor.py     # 비상 리밸런싱 5분 모니터 (F-05-04)
│   │   ├── graceful_shutdown.py     # 그레이스풀 셧다운 매니저 (NFR-06)
│   │   └── notification/
│   │       ├── alert_manager.py     # 알림 생성·관리·이력 (템플릿 기반)
│   │       └── telegram_notifier.py # 텔레그램 봇 알림 발송 (레벨 필터·재시도)
│   ├── api/
│   │   ├── routes/
│   │   │   ├── auth.py              # 인증 (로그인·토큰 갱신)
│   │   │   ├── portfolio.py         # 포트폴리오 (요약·보유·성과)
│   │   │   ├── orders.py            # 주문 (생성·배치·조회·취소)
│   │   │   ├── profile.py           # 투자자 프로필 (조회·수정)
│   │   │   ├── market.py            # 시장 (환율·지수·경제지표·유니버스)
│   │   │   ├── alerts.py            # 알림 (이력·통계·확인 처리)
│   │   │   └── system.py            # 시스템 (설정·백테스트·리밸런싱·파이프라인)
│   │   ├── schemas/
│   │   │   ├── common.py            # 공통 응답 (APIResponse·PaginatedResponse)
│   │   │   ├── auth.py              # 인증 스키마
│   │   │   ├── portfolio.py         # 포트폴리오 스키마
│   │   │   ├── orders.py            # 주문 스키마
│   │   │   ├── profile.py           # 프로필 스키마
│   │   │   └── alerts.py            # 알림 스키마
│   │   └── middleware/
│   │       ├── auth.py              # JWT 인증 (HS256, Bearer Token)
│   │       └── request_logger.py    # 요청 로깅 미들웨어
│   ├── db/
│   │   ├── database.py              # DB 연결 관리 (PostgreSQL·MongoDB·Redis)
│   │   ├── models/
│   │   └── repositories/
│   │       └── audit_log.py         # 감사 로그
│   └── tests/
│       ├── conftest.py              # 공통 Fixture
│       ├── test_backtest_engine.py   # 백테스트 엔진 (19 tests)
│       ├── test_economic_collector.py # 경제지표 수집 (18 tests)
│       ├── test_ensemble.py          # 앙상블 엔진 (13 tests)
│       ├── test_factor_analyzer.py   # 팩터 분석 (21 tests)
│       ├── test_kis_client.py        # KIS 클라이언트 (12 tests)
│       ├── test_market_data.py       # 시장 데이터 (7 tests)
│       ├── test_news_collector.py    # 뉴스 수집 (10 tests)
│       ├── test_sentiment.py         # 감성 분석 (9 tests)
│       ├── test_signal_generator.py  # 시그널 생성 (16 tests)
│       ├── test_profile.py           # 투자자 프로필 (22 tests)
│       ├── test_construction.py      # 포트폴리오 구성 (77 tests)
│       ├── test_rebalancing.py       # 리밸런싱 엔진 (36 tests)
│       ├── test_universe.py          # 유니버스 관리 (29 tests)
│       ├── test_exchange_rate.py     # 환율 관리 (39 tests)
│       ├── test_executor.py          # 주문 집행 (33 tests)
│       ├── test_notification.py      # 알림 시스템 (72 tests)
│       ├── test_api.py               # API·인증·스키마 (59 tests)
│       ├── test_trading_guard.py     # 트레이딩 안전 장치 (72 tests)
│       ├── test_mode_manager.py      # 모드 전환 관리 (44 tests)
│       ├── test_integration.py       # 통합·E2E 테스트 (30 tests)
│       ├── test_demo_verifier.py     # DEMO 가동 검증 (73 tests)
│       ├── test_trading_scheduler.py # 자동화 스케줄러 (76 tests)
│       ├── test_daily_reporter.py    # 일일 리포트 (70 tests)
│       ├── test_emergency_monitor.py # 비상 리밸런싱 모니터 (64 tests)
│       ├── test_backtest_engine.py  # 백테스트 엔진 (25 tests)
│       ├── test_graceful_shutdown.py # 그레이스풀 셧다운 (25 tests)
│       ├── test_weight_optimizer.py  # 가중치 자동 최적화 (32 tests)
│       ├── test_market_calendar.py   # 마켓 캘린더 (44 tests)
│       ├── test_periodic_reporter.py # 주간/월간 리포트 (27 tests)
│       ├── test_daily_reporter_top_bottom.py # Top/Bottom 3 (5 tests)
│       └── test_cross_market_factor.py # Cross-Market 팩터 (14 tests)
├── frontend/
│   └── index.html                   # SPA 대시보드 (Chart.js)
└── scripts/
    └── init_db.sql                  # DB 초기화 스크립트
```

## 개발 단계

| Phase | 내용 | 상태 |
|-------|------|------|
| Phase 1 | 인프라 구축, 한투 API 연동, 데이터 수집 파이프라인 | ✅ 완료 |
| Phase 2 | 퀀트 전략 엔진 (5팩터 분석, 시그널 생성, 백테스트) | ✅ 완료 |
| Phase 3 | AI 정성적 분석, 전략 앙상블, 데이터 소스 확장 | ✅ 완료 |
| Phase 4 | 포트폴리오 관리, 리밸런싱, 자동매매 | ✅ 완료 |
| Phase 5 | 웹 대시보드, API, 알림 시스템 | ✅ 완료 |
| Phase 6 | 통합 테스트, 모의투자 검증, 실투자 전환 | ✅ 완료 |
| Phase 7 | DEMO 모드 실전 가동, 자동화 스케줄러, 일일 리포트 | ✅ 완료 |
| Phase 8 | GAP 보완: 비상 리밸런싱 모니터, 동적 손절, 통합 연동 | ✅ 완료 |
| Phase 9 | 포트폴리오 최적화 완성: Black-Litterman, 실제 공분산 MVO, ERC Risk Parity | ✅ 완료 |
| Phase 10 | TWAP/VWAP 분할, 벤치마크 성과지표, 그레이스풀 셧다운 | ✅ 완료 |
| Phase 11 | 가중치 자동 최적화, NYSE 캘린더, 주간/월간 리포트, Cross-Market 팩터 | ✅ 완료 |

### Phase 3 상세 구현 내역

| 기능 | 설명 | 모듈 |
|------|------|------|
| AI 감성 분석 (Mode A) | Claude Haiku 기반 뉴스/공시 감성 점수 (-1.0~+1.0) | sentiment.py |
| AI 투자 의견 (Mode B) | Claude Sonnet 기반 STOCK/SECTOR/MACRO 투자 의견 | opinion.py |
| 전략 앙상블 | Quant 4전략 + AI 감성 가중 평균, Sharpe 기반 재보정 | engine.py |
| 뉴스 수집 | Naver/Hankyung/Maekyung/Reuters RSS + DART 공시 | news_collector.py |
| 경제지표 수집 | FRED 9개 미국지표 + ECOS 5개 한국지표 | economic_collector.py |
| 재무제표 수집 | DART 하이브리드 (API + 일괄 txt), PER/PBR/ROE 파생 | financial_collector.py |
| SNS 수집 | Reddit OAuth2, 8개 서브레딧, 키워드/스팸 필터 | social_collector.py |
| 프롬프트 관리 | MongoDB 버전 관리, 롤백, A/B 테스트 메트릭 | prompt_manager.py |
| 투자 파이프라인 | 뉴스→감성→의견→앙상블 통합 자동화 | pipeline.py |

### Phase 4 상세 구현 내역

| 기능 | 설명 | 모듈 |
|------|------|------|
| 투자자 프로필 | 위험성향(5단계)·투자스타일·손실허용도 관리 | profile.py |
| 포트폴리오 구성 | MVO(공분산 기반) + Risk Parity(ERC) + Black-Litterman 삼중 엔진 | construction.py |
| 리밸런싱 엔진 | 정기(임계값 기반)·긴급(손실률)·방어(전량 매도) 리밸런싱 | rebalancing.py |
| 투자 유니버스 | 섹터 필터·지정 종목·자동 유동성 필터 | universe.py |
| 환율 관리 | KIS API + FRED Fallback, Redis 캐싱 (장중 5분/장외 24시간 TTL) | exchange_rate.py |
| 주문 집행 | 시장가·지정가·TWAP(6분할)·VWAP 주문, 배치 실행 (SELL 우선) | executor.py |

### Phase 5 상세 구현 내역

| 기능 | 설명 | 모듈 |
|------|------|------|
| JWT 인증 | HS256 Bearer Token, 단일 사용자 인증 (bcrypt/평문 지원) | middleware/auth.py |
| API 라우터 | 인증·포트폴리오·주문·프로필·시장·알림·시스템 7개 도메인 | routes/*.py |
| Pydantic 스키마 | 요청/응답 모델 18개 클래스, APIResponse 제네릭 래퍼 | schemas/*.py |
| 요청 로깅 | Starlette 미들웨어 기반 HTTP 요청/응답 로깅 | middleware/request_logger.py |
| 알림 관리 | 템플릿 기반 알림 생성, 레벨 필터링, MongoDB/메모리 이중 저장 | alert_manager.py |
| 텔레그램 발송 | 알림 → 텔레그램 전달, 레벨 필터(ALL/IMPORTANT/ERROR), 재시도 3회 | telegram_notifier.py |
| 웹 대시보드 | SPA 대시보드 (Chart.js), 포트폴리오·주문·알림·설정 화면 | frontend/index.html |

### Phase 6 상세 구현 내역

| 기능 | 설명 | 모듈 |
|------|------|------|
| 트레이딩 안전 장치 | 7계층 보호: 환경·자본금·일일손실·MDD·연속손실·주문사전검증·Kill Switch | trading_guard.py |
| 시스템 건전성 검사 | PostgreSQL·MongoDB·Redis·설정유효성·거래모드 종합 점검 | health_checker.py |
| 모드 전환 관리 | BACKTEST→DEMO→LIVE 전환 조건 검증, 비상 다운그레이드, 이력 기록 | mode_manager.py |
| 통합 테스트 | TradingGuard+ModeManager 연동, 서킷브레이커 시나리오, 알림 연동 (30 tests) | test_integration.py |
| TradingGuard 테스트 | 환경검증·자본금·서킷브레이커·Kill Switch·주문검증 (72 tests) | test_trading_guard.py |
| ModeManager 테스트 | BACKTEST→DEMO→LIVE 전환, 교차검증, 이력관리 (44 tests) | test_mode_manager.py |

### Phase 7 상세 구현 내역

| 기능 | 설명 | 모듈 |
|------|------|------|
| DEMO 가동 검증 | KIS 토큰 발급·잔고 조회·DB·AI·Telegram 11항목 종합 체크리스트 | demo_verifier.py |
| 자동화 스케줄러 | KRX 장 시간 기반 5단계 자동화 (장 전→개장→중간점검→마감→마감후) | trading_scheduler.py |
| 일일 리포트 | 수익률·거래·포지션·서킷브레이커 리포트 자동 생성 및 Telegram 발송 | daily_reporter.py |
| 거래일 판별 | 주말·한국공휴일(2025~2026) 제외, next_trading_day 자동 계산 | trading_scheduler.py |
| KIS 잔고 수집 | KIS API 연동 잔고·포지션 자동 수집 및 PositionSnapshot 변환 | daily_reporter.py |

### Phase 8 상세 구현 내역 (GAP 보완)

| 기능 | 설명 | 모듈 |
|------|------|------|
| 비상 리밸런싱 모니터 | 장중 5분 간격 손실률 모니터링, 사용자/알고리즘 이중 임계값 트리거 | emergency_monitor.py |
| 매입가 기반 손실률 | KIS API 잔고에서 평균 매입가 추출, 정확한 포트폴리오 손익 계산 | emergency_monitor.py |
| 알고리즘 동적 손절 | 포트폴리오 가중 변동성 기반 2σ 손절 기준 (-5%~-25% 클램핑) | emergency_monitor.py |
| 방어 포트폴리오 전환 | 전 포지션 70% 매도, 현금 비중 확대, 시장가 주문 자동 생성 | emergency_monitor.py |
| Telegram 비상 알림 | 손실률·최악 포지션·방어 주문 상세 포맷, 일임/자문형 분기 처리 | emergency_monitor.py |
| 리밸런싱 OrderExecutor 연동 | 정기/비상 리밸런싱 시 OrderExecutor 자동 주문 체결 (매도 우선) | rebalancing.py |
| 리밸런싱 Telegram 연동 | 정기/비상 리밸런싱 결과를 TelegramNotifier로 실시간 알림 발송 | rebalancing.py |
| 트리거 쿨다운 | 연속 트리거 방지 (30분 쿨다운), Kill Switch 연동 자동 중단 | emergency_monitor.py |

### Phase 9 상세 구현 내역 (포트폴리오 최적화 완성)

| 기능 | 설명 | 모듈 |
|------|------|------|
| Black-Litterman 모델 | 시장 균형 수익률(사전분포) + 앙상블 시그널(투자자 뷰) 결합 사후 최적화 | construction.py |
| 실제 공분산 기반 MVO | 가격 시계열 로그 수익률 → 연율화 공분산 + Ledoit-Wolf 축소 추정 | construction.py |
| 변동성 기반 Risk Parity | 역변동성 초기값 + 수치 최적화(ERC) 정밀 균등 위험 기여 배분 | construction.py |
| USD 비중 하드캡 | 미국 자산(NYSE/NASDAQ/AMEX) 합산 60% 초과 시 비례 축소, KR 재배분 | construction.py |
| 프로필별 현금 비중 | CONSERVATIVE 15%, BALANCED 5%, AGGRESSIVE 0%, DIVIDEND 10% 최소 보장 | construction.py |
| 위험회피 계수 적용 | 리스크 프로필별 λ (1.0~5.0) MVO/BL 목적함수에 반영 | construction.py |

### Phase 10 상세 구현 내역 (GAP 보완 3-5)

| 기능 | 설명 | 모듈 |
|------|------|------|
| TWAP 분할 주문 | 설정 가능한 구간 수/간격, 적응적 이월(carryover), 구간 재시도 | executor.py |
| VWAP 거래량 프로필 | U자형 일중 거래량 커브(22/12/10/10/16/30%) 기반 비균등 분할 | executor.py |
| 벤치마크 성과 지표 | Alpha, Beta, Information Ratio, Tracking Error 연율 산출 | engine.py (backtest) |
| 전략 비교 확장 | StrategyComparator에 벤치마크 지표 컬럼 추가 | engine.py (backtest) |
| 그레이스풀 셧다운 매니저 | 3단계 셧다운(DRAINING→STOPPING→CLEANUP), LIFO 서비스 종료 | graceful_shutdown.py |
| 주문 드레이닝 | 진행 중 주문 대기(타임아웃) + 미완료 시 Task.cancel() 강제 취소 | graceful_shutdown.py |
| main.py 통합 | GracefulShutdownManager + DB cleanup 콜백 → lifespan yield 후 실행 | main.py |

### Phase 11 상세 구현 내역 (GAP 보완 6-10)

| 기능 | 설명 | 모듈 |
|------|------|------|
| 가중치 자동 최적화 (F-04-02) | BacktestEngine→EnsembleEngine 피드백 루프. Sharpe/Risk-Adjusted/Min-Variance/Walk-Forward 4종 방식, 제약 조건(5~40%), 지수평활화 | weight_optimizer.py |
| NYSE 영업일 캘린더 (F-10-01-A) | 미국 공휴일 자동 산출(고정+이동+부활절), 관찰 규칙, 조기폐장일, DST 판별, KRX/NYSE 통합 MarketCalendar | market_calendar.py |
| 주간/월간 리포트 (F-09) | 금요일 주간, 월말 월간 리포트. MDD/Sharpe/변동성, Best/Worst일, 벤치마크 대비 초과수익, 전략별 기여도, Telegram 포맷 | periodic_reporter.py |
| Top/Bottom 3 종목 (F-09-01) | DailyReport에 수익률 기준 상위/하위 3종목 자동 추출, Telegram 메시지 포함 | daily_reporter.py |
| Cross-Market 팩터 정규화 (F-02-01-A) | KR/US 시장별 별도 Z-Score 산출 후 전체 유니버스 재정규화. 프로필별 가중치 반영 | factor_analyzer.py |

## 테스트 실행

```bash
cd backend
pytest                    # 전체 테스트 (1084 tests)
pytest -v                 # 상세 출력
pytest --cov=core --cov=config  # 커버리지 포함
```

## 라이선스

Private - All rights reserved
