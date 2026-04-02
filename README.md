# AQTS - AI Quant Trade System

AI 기반 정량·정성적 분석 통합 퀀트 트레이딩 시스템

## 시스템 구성

| 구성요소 | 기술 |
|---------|------|
| Backend | Python 3.11 + FastAPI |
| Database | PostgreSQL 16 + TimescaleDB 2.14 |
| Document DB | MongoDB 7.0 |
| Cache/Queue | Redis 7.2 |
| AI/LLM | Anthropic Claude API |
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
│   │   ├── settings.py              # 환경변수 기반 설정
│   │   ├── constants.py             # 상수 정의
│   │   └── logging.py               # 로깅 설정
│   ├── core/
│   │   ├── data_collector/
│   │   │   ├── kis_client.py        # 한투 API 래퍼
│   │   │   └── market_data.py       # 시세 데이터 수집
│   │   ├── quant_engine/            # Phase 2
│   │   ├── ai_analyzer/             # Phase 3
│   │   ├── strategy_ensemble/       # Phase 3
│   │   ├── portfolio_manager/       # Phase 4
│   │   ├── order_executor/          # Phase 4
│   │   ├── backtest_engine/         # Phase 2
│   │   └── notification/            # Phase 5
│   ├── api/
│   │   ├── routes/                  # Phase 5
│   │   ├── schemas/                 # Phase 5
│   │   └── middleware/              # Phase 5
│   ├── db/
│   │   ├── database.py              # DB 연결 관리
│   │   ├── models/
│   │   └── repositories/
│   │       └── audit_log.py         # 감사 로그
│   └── tests/
│       ├── conftest.py              # 공통 Fixture
│       ├── test_kis_client.py       # KIS 클라이언트 테스트
│       └── test_market_data.py      # 데이터 수집 테스트
├── frontend/                        # Phase 5
└── scripts/
    └── init_db.sql                  # DB 초기화 스크립트
```

## 개발 단계

| Phase | 내용 | 상태 |
|-------|------|------|
| Phase 1 | 인프라 구축, 한투 API 연동, 데이터 수집 | 🔨 진행 중 |
| Phase 2 | 퀀트 전략 엔진, 백테스트 | ⏳ 예정 |
| Phase 3 | AI 정성적 분석, 전략 앙상블 | ⏳ 예정 |
| Phase 4 | 포트폴리오 관리, 리밸런싱, 자동매매 | ⏳ 예정 |
| Phase 5 | 웹 대시보드, 리포트, 알림 | ⏳ 예정 |
| Phase 6 | 통합 테스트, 모의투자 검증, 실투자 전환 | ⏳ 예정 |

## 테스트 실행

```bash
cd backend
pytest
```

## 라이선스

Private - All rights reserved
