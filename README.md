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
│   │   │   ├── construction.py      # 포트폴리오 구성 (MVO·Risk Parity)
│   │   │   ├── rebalancing.py       # 리밸런싱 엔진 (정기·긴급·방어)
│   │   │   ├── universe.py          # 투자 유니버스 관리
│   │   │   └── exchange_rate.py     # 환율 관리 (KIS+FRED, Redis 캐싱)
│   │   ├── order_executor/
│   │   │   └── executor.py          # 주문 집행 (시장가·지정가·TWAP·VWAP)
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
│       ├── test_construction.py      # 포트폴리오 구성 (44 tests)
│       ├── test_rebalancing.py       # 리밸런싱 엔진 (36 tests)
│       ├── test_universe.py          # 유니버스 관리 (29 tests)
│       ├── test_exchange_rate.py     # 환율 관리 (39 tests)
│       ├── test_executor.py          # 주문 집행 (33 tests)
│       ├── test_notification.py      # 알림 시스템 (72 tests)
│       └── test_api.py               # API·인증·스키마 (59 tests)
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
| Phase 6 | 통합 테스트, 모의투자 검증, 실투자 전환 | ⏳ 예정 |

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
| 포트폴리오 구성 | Mean-Variance Optimization + Risk Parity 이중 엔진 | construction.py |
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

## 테스트 실행

```bash
cd backend
pytest                    # 전체 테스트 (472 tests)
pytest -v                 # 상세 출력
pytest --cov=core --cov=config  # 커버리지 포함
```

## 라이선스

Private - All rights reserved
