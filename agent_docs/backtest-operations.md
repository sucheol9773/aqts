# AQTS Backtest & OOS Operations

> AQTS 의 **백테스트·하이퍼옵트·OOS·파라미터 민감도** 파이프라인이 어떤 모듈·스크립트·설정으로 구성되는지, 어떤 커맨드가 프로덕션/리서치 모드에서 실행되는지 정리합니다. 엔진 코드 내부 설계는 각 모듈의 docstring 을, 운영 규칙은 [development-policies.md](./development-policies.md) 를 단일 진실원천으로 삼습니다.

---

## 1. 실행 스크립트 (`scripts/` 루트)

리서치/오프라인 실행은 프로젝트 루트의 `scripts/` 디렉토리에 모여 있습니다. `backend/scripts/` 는 CI/CD·보조 운영 도구(검사기, 배포 스크립트) 를 담당하며 구분됩니다.

| 스크립트 | 용도 | 대표 옵션 |
|---|---|---|
| `scripts/run_backtest.py` | DB OHLCV 기반 전략 백테스트 | `--tickers`, `--start`, `--end`, `--market {kr,us}`, `--output <csv>` |
| `scripts/run_hyperopt.py` | Optuna 기반 베이지안 최적화 (OOS Sharpe 목적함수) | `--groups {ensemble,risk,...}`, `--trials`, `--timeout`, `--market`, `--tickers` |
| `scripts/run_walk_forward.py` | Walk-Forward OOS 검증 | `--all`, `--market`, `--train <개월>`, `--test <개월>`, `--tickers` |
| `scripts/run_scheduler.py` | 로컬/수동 스케줄러 실행 | (컨테이너 `scheduler` 서비스의 CLI 대체 경로) |
| `scripts/backfill_market_data.py` | 시장 데이터 백필 | |
| `scripts/verify_phase1_demo.sh` | Phase 1 데모 검증 런북 실행 | `docs/operations/phase1-demo-verification-2026-04-11.md` |

`.env` 로드는 각 스크립트가 `dotenv.load_dotenv(project_root/.env)` 로 수행합니다. `.env` 의 실값은 본 문서·커밋·팀 프롬프트에 포함되지 않습니다.

---

## 2. 백테스트 엔진 (`backend/core/backtest_engine/`)

| 파일 | 역할 |
|---|---|
| `engine.py` | 메인 시뮬레이터 (이벤트 루프, 포지션 갱신) |
| `ablation.py` | 컴포넌트 기여도 분해 (각 전략 on/off 효과) |
| `benchmark.py` | 벤치마크 (KOSPI / S&P500 등) 대비 지표 |
| `bias_checker.py` | look-ahead / survivorship bias 검출 |
| `fill_model.py` | 체결 모델 (시장가/지정가, partial fill) |
| `impact_model.py` | 시장 충격 모델 |
| `metrics_calculator.py` | Sharpe, Sortino, Calmar, MDD 등 |
| `pass_fail.py` | OOS 합격/검토/탈락 판정 (오퍼레이셔널 임계값 기반) |
| `regime_analyzer.py` | 레짐별 성과 분해 |
| `significance.py` | 통계적 유의성 검증 (bootstrap 등) |

**임계값 소스**: `backend/config/operational_thresholds.yaml` 의 해당 섹션. `DEFAULT_THRESHOLDS` 딕셔너리(예: `backend/core/oos/gate_evaluator.py:40` 의 `GateEvaluator.DEFAULT_THRESHOLDS`) 와 yaml 은 **항상 동일한 값**을 유지해야 합니다 (development-policies.md §4).

---

## 3. OOS Validation (`backend/core/oos/`)

| 파일 | 역할 |
|---|---|
| `walk_forward.py` | 학습/검증 윈도우 분할, 롤링 재학습, 윈도우별 성과 집계 |
| `gate_evaluator.py` | 3단계 게이트(A/B/C) 판정 |
| `job_manager.py` | 비동기 OOS 잡 관리 |
| `models.py` | `OOSWindowResult`, `GateResult` 등 데이터클래스 |
| `regime_mapping.py` | 레짐별 매핑 테이블 |

### 3.1 게이트 규칙 (`gate_evaluator.py`)

엔진 docstring 에 명시된 기준 요약:

- **Gate-A (절대 기준)**: MDD 상한(-40%), turnover 상한. 위반 시 즉시 **FAIL**.
- **Gate-B (상대 기준)**: Sharpe/Calmar 최소, 레짐별 최악 MDD. 미달 시 **REVIEW** 또는 **FAIL**.
- **Gate-C (안정성 기준)**: 윈도우 간 Sharpe 분산, 양수 윈도우 비율. 불안정 시 **REVIEW**.

임계값 근거:
- MDD -40%: S&P500 역대 최대 낙폭(-56.8%, 2008) 대비 개별 전략 허용 범위.
- Sharpe 분산 5.0: 3개월 윈도우 96개(26년) 기준 3~6 이 일반. 5 이상은 불안정 경고.
- 양수 윈도우 50%: 무작위 전략 기대 비율(50%) 보다 높아야 유의.

임계값은 `operational_thresholds.yaml` 의 `oos_gate` 섹션에서 로드되며, yaml 수정을 빠뜨리면 코드 기본값이 사용되어 **의도와 다르게 통과/탈락 판정이 날 수 있으므로** (development-policies.md §4) 반드시 쌍으로 갱신합니다.

### 3.2 API 노출

`backend/api/routes/oos.py` 가 `POST /api/system/oos/run`, `GET /api/system/oos/latest`, `GET /api/system/oos/gate-status`, `GET /api/system/oos/{run_id}` 를 제공합니다. RBAC 는 [api_contracts.md §2.9](./api_contracts.md) 참조.

---

## 4. Hyperparameter Optimization (`backend/core/hyperopt/`)

| 파일 | 역할 |
|---|---|
| `optimizer.py` | Optuna study 생성·실행 |
| `objective.py` | OOS Sharpe 기반 목적함수 정의 |
| `search_space.py` | 그룹별 파라미터 탐색 범위 |
| `models.py` | 실행 결과 자료형 |

`scripts/run_hyperopt.py` 는 `--groups` 인자로 `ensemble`, `risk` 등 그룹을 지정해 부분 최적화가 가능합니다. 장시간 실행이므로 `--timeout` 권장.

**주의**: 하이퍼옵트 산출 파라미터를 프리셋(예: `STRATEGY_RISK_PRESETS`) 에 반영할 때 반드시 **Wiring Rule** (development-policies.md §5) 을 따릅니다 — dict 키 추가 → config 객체 생성부 → 엔진 사용부 전체 경로를 통합 테스트로 검증합니다.

---

## 5. Parameter Sensitivity (`backend/core/param_sensitivity/`)

| 파일 | 역할 |
|---|---|
| `engine.py` | 민감도 스윕 실행 |
| `sweep_generator.py` | 파라미터 조합 생성 |
| `analyzer.py` | 결과 분석 (토네이도 차트 입력 등) |
| `models.py` | 결과 자료형 |

API: `POST /api/system/param-sensitivity/run`, `GET /latest`, `GET /tornado` ([api_contracts.md §2.10](./api_contracts.md)).

---

## 6. 앙상블 설정 (`backend/config/ensemble_config.yaml` + `ensemble_config_loader.py`)

동적 앙상블의 전략 가중/레짐 규칙은 yaml 로 외부화되어 있습니다. 신규 전략을 추가할 때:

1. `backend/core/strategy_ensemble/dynamic_ensemble.py` 에 전략 등록.
2. `ensemble_config.yaml` 에 가중/레짐 매핑 추가.
3. `ensemble_config_loader.py` 가 파싱 가능한지 로드 테스트.
4. OOS 재검증 (`scripts/run_walk_forward.py`) → 게이트 통과 확인.
5. 결과 비교 테이블을 분석 리포트(`docs/backtest/oos-analysis-*.md`) 에 추가 (development-policies.md §2).

---

## 7. 백테스트 결과 저장

| 저장소 | 용도 |
|---|---|
| PostgreSQL `backtest_results` (alembic 001) | 요약 메트릭 |
| PostgreSQL `rebalancing_history` (alembic 006) | 리밸런싱 실행 이력 |
| MongoDB 컬렉션 | 시계열 원시 결과, 큰 아티팩트 |
| 로컬 CSV | `scripts/run_backtest.py --output results/*.csv` (리서치 용) |
| `docs/backtest/*.md` | 사람이 읽는 비교 리포트 |

---

## 8. 리포트 표준

백테스트/OOS 결과가 바뀌는 변경은 반드시 **결과 비교 테이블**을 분석 리포트에 추가합니다 (development-policies.md §2). 현재 리포지토리의 리포트:

- `docs/backtest/BACKTEST_SIGNAL_TEST_REPORT.md`
- `docs/backtest/backtest-report-2026-04-05.md`
- `docs/backtest/oos-analysis-2026-04-06.md`

신규 리포트 파일명 규칙: `backtest-report-YYYY-MM-DD.md` 또는 `oos-analysis-YYYY-MM-DD.md`. 변경 이유·파라미터·before/after 지표(Sharpe, MDD, Calmar, turnover) 를 반드시 포함합니다.

---

## 9. 커밋 전 검증 (백테스트 관련)

백테스트 코드·임계값을 수정한 커밋의 **필수** 체크:

1. development-policies.md §3 의 ruff / black / pytest 게이트 통과.
2. 임계값 수정 시 `operational_thresholds.yaml` ↔ `DEFAULT_THRESHOLDS` 동기 확인 (development-policies.md §4).
3. 설정 dict 키 추가 시 config 객체 생성부까지 전달 확인 (development-policies.md §5).
4. 상태 전이 로직(cooldown, risk-off 회복) 변경 시 수학적 회복 경로 존재 확인 (development-policies.md §10).
5. `scripts/run_walk_forward.py` 로 OOS 재검증 → 게이트 결과 비교 테이블 추가.
6. `docs/backtest/` 에 리포트 추가 또는 기존 리포트 갱신.

---

## 10. 팀 분배 관점

본 영역은 마이그레이션 계획서(`docs/migration/cowork-to-agent-teams-plan.md` §6-1) 의 **팀메이트 1: Strategy/Backtest** 담당입니다. 파일 소유권:

- 전속: `backend/core/strategy_ensemble/`, `backend/core/backtest_engine/`, `backend/core/oos/`, `backend/core/hyperopt/`, `backend/core/param_sensitivity/`, `backend/core/quant_engine/`, `backend/core/weight_optimizer.py`, `backend/config/ensemble_config.yaml`, `backend/config/ensemble_config_loader.py`
- 공유(리드 승인 필요): `backend/config/operational_thresholds.yaml`, `backend/core/utils/`, `backend/config/settings.py`
- 리서치 스크립트: `scripts/run_backtest.py`, `scripts/run_hyperopt.py`, `scripts/run_walk_forward.py`

충돌을 피하기 위해 API 라우트(`backend/api/routes/oos.py`, `ensemble.py`, `param_sensitivity.py`) 는 **팀메이트 3: API/RBAC** 이 인터페이스 소유권을 가지며, Strategy/Backtest 팀은 내부 엔진·계산 레이어만 수정합니다. 엔진 시그니처가 변경되면 메일박스로 API 팀에 통보합니다.

---

## 문서 소유권

- 백테스트 관련 규칙·절차가 바뀌면 본 문서와 [development-policies.md](./development-policies.md) (§4 / §5 / §10) 를 동시에 확인합니다.
- 엔드포인트 변경은 [api_contracts.md](./api_contracts.md) 도 함께 갱신합니다.
- 결과 비교 리포트는 `docs/backtest/` 하위에 별도 파일로 남기며, 본 문서는 위치만 가리킵니다.
