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
│   │   ├── portfolio_manager/       # Phase 4
│   │   ├── order_executor/          # Phase 4
│   │   └── notification/            # Phase 5
│   ├── api/
│   │   ├── routes/                  # Phase 5
│   │   ├── schemas/                 # Phase 5
│   │   └── middleware/              # Phase 5
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
│       └── test_signal_generator.py  # 시그널 생성 (16 tests)
├── frontend/                        # Phase 5
└── scripts/
    └── init_db.sql                  # DB 초기화 스크립트
```

## 개발 단계

| Phase | 내용 | 상태 |
|-------|------|------|
| Phase 1 | 인프라 구축, 한투 API 연동, 데이터 수집 파이프라인 | ✅ 완료 |
| Phase 2 | 퀀트 전략 엔진 (5팩터 분석, 시그널 생성, 백테스트) | ✅ 완료 |
| Phase 3 | AI 정성적 분석, 전략 앙상블, 데이터 소스 확장 | ✅ 완료 |
| Phase 4 | 포트폴리오 관리, 리밸런싱, 자동매매 | ⏳ 예정 |
| Phase 5 | 웹 대시보드, API, 알림 시스템 | ⏳ 예정 |
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

## 테스트 실행

```bash
cd backend
pytest                    # 전체 테스트 (138+ tests)
pytest -v                 # 상세 출력
pytest --cov=core --cov=config  # 커버리지 포함
```

## 라이선스

Private - All rights reserved
