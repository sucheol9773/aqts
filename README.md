# AQTS - AI Quant Trade System

AI 기반 정량·정성적 분석 통합 퀀트 트레이딩 시스템

## 시스템 구성

| 구성요소 | 기술 |
|---------|------|
| Backend | Python 3.11 + FastAPI 0.135.3 |
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

> 상세 환경 구성: [docs/operations/docker-setup-guide.md](docs/operations/docker-setup-guide.md) 참조

### 2. Docker 실행

```bash
# 개발 환경 (소스 마운트 + 자동 리로드)
docker compose up -d

# 프로덕션 환경 (override 제외)
docker compose -f docker-compose.yml up -d
```

### 3. 시스템 헬스체크

```bash
curl http://localhost:8000/api/system/health
```

## 프로젝트 구조

```
aqts/
├── docker-compose.yml               # 서비스 오케스트레이션 (PostgreSQL, MongoDB, Redis, Backend)
├── docker-compose.override.yml      # 개발 환경 오버라이드 (자동 병합)
├── .env.example                     # 환경변수 템플릿
├── README.md
├── docs/
│   ├── PRD.md                       # 제품 요구사항 문서
│   ├── FEATURE_STATUS.md            # 기능 구현 현황 (Single Source of Truth)
│   ├── backtest/
│   │   └── oos-analysis-2026-04-06.md  # OOS 분석 리포트 (17개 섹션)
│   └── operations/                  # 운영 문서 (OPS-001 ~ OPS-008)
│       ├── trading-halt-policy.md   # OPS-001: 매매 중단/재개 정책
│       ├── incident-runbook.md      # OPS-002: 장애 대응 런북
│       ├── model-change-policy.md   # OPS-003: 모델 변경 정책
│       ├── release-gates.md         # OPS-004: 릴리스 승인 게이트 (Gate A~E)
│       ├── rollback-plan.md         # OPS-005: 배포 롤백 계획
│       ├── customer-notice.md       # OPS-006: 고객 공지/면책
│       ├── docker-setup-guide.md    # OPS-007: Docker 환경 세팅 가이드
│       └── deployment-roadmap.md    # OPS-008: 배포 및 검증 로드맵
├── backend/
│   ├── Dockerfile                   # 멀티 스테이지 빌드 (Python 3.11.9)
│   ├── requirements.txt             # Python 의존성
│   ├── pyproject.toml               # pytest/ruff/black 설정
│   ├── main.py                      # FastAPI 엔트리포인트 (Lifespan, GracefulShutdown)
│   ├── config/
│   │   ├── settings.py              # 환경변수 기반 설정 (pydantic-settings)
│   │   ├── constants.py             # 상수·Enum 정의 (20+ Enum, 매핑 테이블)
│   │   ├── logging.py               # Loguru 로깅 설정 (dev/production 분리)
│   │   ├── operational_thresholds.yaml  # 전 스테이지 임계값 중앙관리
│   │   ├── ensemble_config.yaml     # 앙상블 하이퍼파라미터 설정 (YAML 관리)
│   │   └── ensemble_config_loader.py # YAML 설정 로더·검증·Hyperopt 연동
│   ├── contracts/                   # 데이터 계약 (9개 도메인)
│   │   ├── converters.py            # 계약 ↔ 엔진 변환기
│   │   ├── price_data.py            # 가격 데이터 계약
│   │   ├── financial_data.py        # 재무 데이터 계약 (look-ahead 방지)
│   │   ├── news_data.py             # 뉴스 데이터 계약
│   │   ├── feature_vector.py        # 피처 벡터 계약
│   │   ├── signal.py                # 시그널 계약
│   │   ├── portfolio.py             # 포트폴리오 계약 (weight 합 ≈ 1.0)
│   │   ├── order.py                 # 주문 계약
│   │   ├── execution.py             # 체결 계약
│   │   └── risk_check.py            # 리스크 체크 계약
│   ├── core/
│   │   ├── data_collector/
│   │   │   ├── kis_client.py        # 한투 API 래퍼 (LIVE/DEMO/BACKTEST)
│   │   │   ├── market_data.py       # 시세 데이터 수집·무결성 검증
│   │   │   ├── news_collector.py    # RSS 뉴스 + DART 공시 수집
│   │   │   ├── economic_collector.py # FRED·ECOS 경제지표 수집
│   │   │   ├── financial_collector.py # DART 재무제표 (하이브리드)
│   │   │   ├── social_collector.py  # Reddit SNS 수집
│   │   │   └── corp_action.py       # 기업 이벤트 처리
│   │   ├── quant_engine/
│   │   │   ├── factor_analyzer.py   # 5팩터 분석 (Value·Momentum·Quality·LowVol·Size)
│   │   │   ├── signal_generator.py  # 기술적 시그널 생성
│   │   │   └── vectorized_signals.py # 벡터화 시그널 (MR/TF/RP, 고속 연산)
│   │   ├── ai_analyzer/
│   │   │   ├── sentiment.py         # Mode A: Claude Haiku 감성 분석
│   │   │   ├── opinion.py           # Mode B: Claude Sonnet 투자 의견 (STOCK·SECTOR·MACRO)
│   │   │   ├── prompt_manager.py    # 프롬프트 DB 버전 관리
│   │   │   ├── cost_analyzer.py     # AI 사용 비용 분석
│   │   │   ├── drift_monitor.py     # 모델 드리프트 모니터링
│   │   │   ├── promotion_checklist.py # LLM 승격 체크리스트
│   │   │   └── reproducibility.py   # 재현성 검증
│   │   ├── strategy_ensemble/
│   │   │   ├── engine.py            # 가중 앙상블 + Sharpe 기반 재보정
│   │   │   ├── regime.py            # 레짐 감지 (상승/하락/횡보/고변동)
│   │   │   ├── dynamic_ensemble.py  # 동적 레짐 기반 앙상블 서비스 (OOS 검증)
│   │   │   └── runner.py            # 앙상블 실행 오케스트레이터
│   │   ├── backtest_engine/
│   │   │   ├── engine.py            # 백테스트 엔진 + 전략 비교 + 벤치마크 지표
│   │   │   ├── metrics_calculator.py # 성과 지표 산출
│   │   │   ├── benchmark.py         # 벤치마크 비교 (Alpha/Beta/IR/TE)
│   │   │   ├── fill_model.py        # 체결 모델
│   │   │   ├── impact_model.py      # 시장 충격 모델
│   │   │   ├── bias_checker.py      # 백테스트 편향 검사
│   │   │   ├── significance.py      # 통계적 유의성 검정
│   │   │   ├── regime_analyzer.py   # 레짐별 성과 분석
│   │   │   ├── ablation.py          # 전략 제거 효과 분석
│   │   │   └── pass_fail.py         # 합격/불합격 판정
│   │   ├── pipeline.py              # 투자 의사결정 통합 파이프라인
│   │   ├── state_machine.py         # 10-state 파이프라인 상태 머신
│   │   ├── gate_registry.py         # Gate 동적 등록/실행
│   │   ├── fallback_handler.py      # Gate BLOCK 시 폴백 처리
│   │   ├── gates/                   # 9개 파이프라인 게이트
│   │   │   ├── base.py              # GateResult 스키마
│   │   │   ├── data_gate.py         # 수집 데이터 품질 검증
│   │   │   ├── factor_gate.py       # 팩터 벡터 생성 품질
│   │   │   ├── signal_gate.py       # 시그널 유효성 검증
│   │   │   ├── ensemble_gate.py     # 앙상블 결과 검증
│   │   │   ├── portfolio_gate.py    # 포트폴리오 구성 검증
│   │   │   ├── trading_guard_gate.py # 포지션 리스크 사전검증
│   │   │   ├── recon_gate.py        # 거래-포지션 대사 검증
│   │   │   ├── execution_gate.py    # 체결 성공/실패 검증
│   │   │   └── fill_gate.py         # 주문 완전성 검증
│   │   ├── portfolio_manager/
│   │   │   ├── profile.py           # 투자자 프로필 관리
│   │   │   ├── construction.py      # 포트폴리오 구성 (MVO·Risk Parity·Black-Litterman)
│   │   │   ├── rebalancing.py       # 리밸런싱 엔진 (정기·긴급·방어)
│   │   │   ├── universe.py          # 투자 유니버스 관리
│   │   │   └── exchange_rate.py     # 환율 관리 (KIS+FRED, Redis 캐싱)
│   │   ├── order_executor/
│   │   │   ├── executor.py          # 주문 집행 (시장가·지정가·TWAP·VWAP)
│   │   │   ├── slippage.py          # 슬리피지 모델
│   │   │   └── time_rules.py        # 시간대별 거래 규칙
│   │   ├── hyperopt/                # Optuna 하이퍼파라미터 자동 최적화
│   │   │   ├── search_space.py      # 20개 파라미터 탐색 공간 (3그룹)
│   │   │   ├── objective.py         # Walk-Forward OOS 목적함수
│   │   │   ├── optimizer.py         # TPE 베이지안 최적화 오케스트레이터
│   │   │   └── models.py            # TrialResult·OptimizationResult
│   │   ├── rl/                      # 강화학습 에이전트
│   │   │   ├── environment.py       # Gymnasium 트레이딩 환경 (11차원 관찰)
│   │   │   ├── trainer.py           # PPO/SAC 학습 파이프라인
│   │   │   ├── config.py            # RL 설정 (25개 파라미터)
│   │   │   ├── data_loader.py       # RL 데이터 로더 (DB OHLCV → 학습 데이터)
│   │   │   ├── multi_asset_env.py   # 멀티에셋 트레이딩 환경
│   │   │   ├── hyperopt_rl.py       # RL 하이퍼파라미터 최적화 (Optuna)
│   │   │   ├── model_registry.py    # 모델 레지스트리 (버전 관리, 챔피언 선정)
│   │   │   └── inference.py         # RL 추론 서비스 (배치 추론, 앙상블 블렌딩)
│   │   ├── data_collector/
│   │   │   ├── daily_collector.py   # 일일 OHLCV 자동 수집 (KIS API)
│   │   │   ├── kis_websocket.py     # KIS 실시간 WebSocket (체결가+호가)
│   │   │   └── realtime_manager.py  # 실시간 시세 관리 (인메모리 캐시)
│   │   ├── scheduler_handlers.py    # 스케줄러 이벤트 핸들러 (5개 시간대)
│   │   ├── oos/                     # Out-of-Sample 검증
│   │   │   ├── models.py            # OOS 데이터 모델
│   │   │   ├── walk_forward.py      # Walk-Forward 엔진
│   │   │   ├── gate_evaluator.py    # 3단계 Gate 평가 (A/B/C)
│   │   │   ├── regime_mapping.py    # 레짐 매핑 레이어
│   │   │   └── job_manager.py       # OOS 작업 관리자
│   │   ├── param_sensitivity/       # 파라미터 민감도 분석
│   │   │   ├── models.py            # 민감도 분석 데이터 모델
│   │   │   ├── sweep_generator.py   # Grid/Random/OAT 스윕 생성기
│   │   │   ├── analyzer.py          # 탄성치/토네이도/안정구간 분석
│   │   │   └── engine.py            # 민감도 분석 엔진
│   │   ├── compliance/              # 규제 준수
│   │   │   ├── audit_integrity.py   # 감사 로그 무결성 (SHA-256 해시 체인)
│   │   │   ├── retention_policy.py  # 거래 기록 보존 정책 (5년/10년)
│   │   │   ├── pii_masking.py       # PII 마스킹 (7종 패턴)
│   │   │   ├── compliance_report.py # 규제 준수 리포트 생성
│   │   │   └── secret_manager.py    # 비밀키 관리 (등록/로테이션/폐기)
│   │   ├── audit/                   # 감사 추적
│   │   │   ├── collectors.py        # 감사 데이터 수집
│   │   │   ├── decision_record.py   # 의사결정 기록
│   │   │   └── visualization.py     # 감사 추적 시각화 (타임라인/히트맵)
│   │   ├── monitoring/              # 모니터링
│   │   │   └── dashboard.py         # 모니터링 대시보드 (서비스 상태/메트릭/알림)
│   │   ├── notification/
│   │   │   ├── alert_manager.py     # 알림 생성·관리·이력 (템플릿 기반)
│   │   │   ├── telegram_notifier.py # 텔레그램 봇 알림 발송
│   │   │   ├── fallback_notifier.py # 백업 알림 채널 (File/Console 폴백)
│   │   │   └── telegram_adapter.py  # Telegram 채널 어댑터
│   │   ├── trading_guard.py         # 트레이딩 안전 장치 (7계층 보호)
│   │   ├── health_checker.py        # 시스템 건전성 검사 (5항목)
│   │   ├── mode_manager.py          # 모드 전환 관리 (BACKTEST→DEMO→LIVE)
│   │   ├── demo_verifier.py         # DEMO 모드 실전 가동 검증 (11항목)
│   │   ├── trading_scheduler.py     # 자동화 스케줄러 (KRX 장 시간 기반)
│   │   ├── daily_reporter.py        # 일일 리포트 자동 생성·발송
│   │   ├── periodic_reporter.py     # 주간/월간 리포트 (MDD/Sharpe 분석)
│   │   ├── emergency_monitor.py     # 비상 리밸런싱 5분 모니터
│   │   ├── graceful_shutdown.py     # 그레이스풀 셧다운 매니저
│   │   ├── circuit_breaker.py       # 외부 API 장애 자동 차단 (4 서비스)
│   │   ├── market_calendar.py       # 마켓 캘린더 (KRX + NYSE, DST)
│   │   ├── weight_optimizer.py      # 앙상블 가중치 자동 최적화
│   │   ├── capital_budget.py        # 자본 배분
│   │   ├── capital_protection.py    # 자본 보호
│   │   └── reconciliation.py        # 거래-포지션 대사
│   ├── api/
│   │   ├── routes/
│   │   │   ├── auth.py              # 인증 (로그인·토큰 갱신)
│   │   │   ├── portfolio.py         # 포트폴리오 (요약·보유·성과)
│   │   │   ├── orders.py            # 주문 (생성·배치·조회·취소)
│   │   │   ├── profile.py           # 투자자 프로필 (조회·수정)
│   │   │   ├── market.py            # 시장 (환율·지수·경제지표·유니버스)
│   │   │   ├── alerts.py            # 알림 (이력·통계·확인 처리)
│   │   │   ├── system.py            # 시스템 (설정·백테스트·리밸런싱·파이프라인)
│   │   │   ├── audit.py             # 감사 추적 API
│   │   │   ├── oos.py               # OOS 검증 API (4 엔드포인트)
│   │   │   ├── param_sensitivity.py # 민감도 분석 API (3 엔드포인트)
│   │   │   ├── ensemble.py          # 동적 앙상블 API (4 엔드포인트)
│   │   │   └── realtime.py          # 실시간 시세 API (시세·스냅샷·상태)
│   │   ├── schemas/
│   │   │   ├── common.py            # 공통 응답 (APIResponse·PaginatedResponse)
│   │   │   ├── auth.py              # 인증 스키마
│   │   │   ├── portfolio.py         # 포트폴리오 스키마
│   │   │   ├── orders.py            # 주문 스키마
│   │   │   ├── profile.py           # 프로필 스키마
│   │   │   ├── alerts.py            # 알림 스키마
│   │   │   └── ensemble.py          # 앙상블 응답 스키마
│   │   └── middleware/
│   │       ├── auth.py              # JWT 인증 (HS256, Bearer Token)
│   │       ├── request_logger.py    # 요청 로깅 미들웨어 (X-Request-ID)
│   │       └── rate_limiter.py      # Rate Limiting (slowapi)
│   ├── db/
│   │   ├── database.py              # DB 연결 관리 (PostgreSQL·MongoDB·Redis)
│   │   ├── models/                  # SQLAlchemy 모델
│   │   └── repositories/
│   │       └── audit_log.py         # 감사 로그 (AuditLogger)
│   └── tests/                       # 3,666 tests (전체 통과)
│       ├── conftest.py              # 공통 Fixture + 환경변수 설정
│       └── test_*.py                # 75+ 테스트 파일
├── frontend/
│   └── index.html                   # SPA 대시보드 (Chart.js)
└── scripts/
    ├── init_db.sql                  # DB 초기화 스크립트 (17 테이블, 6 hypertable)
    ├── check_doc_sync.py            # 문서-코드-테스트 동기화 검증 (CI용)
    ├── run_backtest.py              # 백테스트 실행 CLI
    ├── run_scheduler.py             # 스케줄러 CLI (장 전/시작/중간/마감/후)
    ├── run_hyperopt.py              # Optuna 하이퍼파라미터 최적화 CLI
    └── run_rl_training.py           # RL 에이전트 학습/평가 CLI (PPO/SAC)
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
| Phase 12 | 데이터 계약, 파이프라인 게이트 (9개), 상태 머신, OOS 검증, 파라미터 민감도 | ✅ 완료 |
| Phase 13 | Release Gates (A~E), 컴플라이언스, 모니터링 대시보드, 운영 문서 8종 | ✅ 완료 |
| Phase 14 | OOS Walk-Forward 검증, 동적 레짐 앙상블, 벡터화 시그널, MDD 방어 | ✅ 완료 |
| Phase 15 | KIS API 실시간 연동, OHLCV 자동 수집, 스케줄러 파이프라인 (5핸들러) | ✅ 완료 |
| Phase 16 | 동적 앙상블 REST API, Optuna 하이퍼파라미터 최적화, YAML 설정 관리, RL 에이전트 (Gym+PPO/SAC) | ✅ 완료 |

## 테스트 실행

```bash
cd backend

# 전체 테스트 (3,666 tests)
python -m pytest

# 스모크 테스트 (413 tests, < 13초)
python -m pytest -m smoke

# 커버리지 포함
python -m pytest --cov=core --cov=config --cov=api --cov=db --cov=contracts

# 특정 모듈
python -m pytest tests/test_infrastructure.py -v

# 문서-코드 동기화 검증
python scripts/check_doc_sync.py --verbose
```

## 운영 문서

| Doc ID | 문서 | 내용 |
|--------|------|------|
| OPS-001 | [매매 중단/재개 정책](docs/operations/trading-halt-policy.md) | 자동/수동 중단 트리거, 재개 절차 |
| OPS-002 | [장애 대응 런북](docs/operations/incident-runbook.md) | SEV-1~4 등급별 대응, 시나리오별 런북 |
| OPS-003 | [모델 변경 정책](docs/operations/model-change-policy.md) | 변경 분류, 백테스트/OOS 검증 요건 |
| OPS-004 | [릴리스 승인 게이트](docs/operations/release-gates.md) | Gate A~E 5단계 승인 |
| OPS-005 | [배포 롤백 계획](docs/operations/rollback-plan.md) | 앱/DB/설정 롤백 절차 |
| OPS-006 | [고객 공지](docs/operations/customer-notice.md) | 투자 위험 고지, SLA |
| OPS-007 | [Docker 환경 세팅](docs/operations/docker-setup-guide.md) | 개발/프로덕션 환경 구성 |
| OPS-008 | [배포 검증 로드맵](docs/operations/deployment-roadmap.md) | Phase 0~4 단계별 절차 |
| SEC-001 | [RBAC 정책](docs/security/rbac-policy.md) | 라우트별 권한 매트릭스, wiring 강제 검사 |
| SEC-002 | [공급망 보안 정책](docs/security/supply-chain-policy.md) | SBOM/cosign keyless/grype/pip-audit, CD `cosign verify` 가드, 수동 검증 절차 |

## 라이선스

Private - All rights reserved
