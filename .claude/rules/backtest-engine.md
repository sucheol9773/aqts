---
paths:
  - "backend/core/strategy_ensemble/**/*.py"
  - "backend/core/backtest_engine/**/*.py"
  - "backend/core/oos/**/*.py"
  - "backend/core/hyperopt/**/*.py"
  - "backend/core/param_sensitivity/**/*.py"
  - "backend/core/quant_engine/**/*.py"
  - "backend/core/weight_optimizer.py"
  - "scripts/run_backtest.py"
  - "scripts/run_hyperopt.py"
  - "scripts/run_walk_forward.py"
  - "backend/config/ensemble_config.yaml"
  - "backend/config/ensemble_config_loader.py"
---

# Strategy / Backtest 영역 가드

**소유**: 팀메이트 1 (Strategy / Backtest). 상세: `agent_docs/governance.md §2.1`.
**SSOT**: `agent_docs/backtest-operations.md` — OOS / 하이퍼옵트 운영 절차.

## 절대 규칙

1. **테스트 기대값 수정 금지**. OOS Sharpe, 게이트 통과율, 수익률 등 기대값은 임계값·입력 조정으로만 대응 (`agent_docs/development-policies.md §1`).
2. **하드코딩 금지**. 임계값·상수는 `backend/config/ensemble_config.yaml` 또는 `operational_thresholds.yaml` 에서 로드.
3. **하이퍼옵트·OOS 는 LLM 세션에서 돌리지 않는다**. `scripts/run_hyperopt.py` / `scripts/run_backtest.py` 로 오프라인 실행 후 결과만 세션으로 가져온다 (`agent_docs/governance.md §6`).

## Wiring Rule (설정-전달 일관성)

`STRATEGY_RISK_PRESETS` 또는 유사 프리셋 dict 에 새 키를 추가할 때 반드시:

- 프리셋 키 추가 → `BacktestConfig(...)` 생성부에서 해당 파라미터를 실제로 전달하는지 확인
- 통합 테스트 또는 실행 로그로 값이 런타임에 반영됐는지 확인 (유닛테스트만으로는 검증 불가)
- 예: `dd_cushion_start` 추가 시 `BacktestConfig(dd_cushion_start=...)` wiring 확인

## 설정값 일관성 규칙

- `operational_thresholds.yaml` ↔ 코드 내 `DEFAULT_THRESHOLDS` 는 항상 동기.
- 임계값 수정 시 `grep -rn "<키명>" backend/` 로 모든 참조 위치 확인.
- yaml override 구조에서 yaml 누락 시 코드 변경이 무효화되므로 특히 주의.

## 상태 전이 로직

risk-off / cooldown / 회복 조건 추가 시 edge case 검토:

- 특정 상태에 진입 후 빠져나올 수 있는 경로가 수학적으로 존재하는가? (예: 현금 100% 상태에서 고점 회복 가능?)
- 상태 전환 조건이 순환·교착되지 않는가?
- 각 상태에서의 거래 횟수가 기대 범위 내인가?

## 커밋 전 체크

```bash
cd backend && python -m ruff check . --config pyproject.toml
cd backend && python -m black --check . --config pyproject.toml
cd backend && python -m pytest tests/ -q --tb=short   # 540s timeout 권장
```

OOS/백테스트 결과가 바뀌는 변경은 `docs/backtest/` 분석 리포트에 결과 비교 테이블 추가 (`agent_docs/development-policies.md §2`).

## 소유권 경계

- `backend/core/utils/`, `backend/config/settings.py`, `.env.example` 은 리드 전용. 수정 필요 시 `[Lead-Approval]` 메일박스.
- `backend/api/`, `backend/core/order_executor/` 등 타팀 영역은 `[Ask]` 메일박스로 위임.
