# Stage 7: LLM Production Promotion Checklist

## Overview

AQTS의 LLM 기반 AI 분석 (Mode A: Sentiment, Mode B: Opinion)이 프로덕션 환경으로 승격하기 위한 품질 보증 프로세스입니다.

두 가지 배포 계층이 있습니다:
- **Production Tier 1**: 모든 기준을 만족하는 프로덕션 환경 배포
- **Research Tier 2**: 부분적으로 기준을 충족하는 연구/검증 환경 유지

## Two-Tier Deployment System

### Production Tier 1 (PROMOTE)
- **대상**: 모든 4가지 평가 기준을 충족한 모델
- **위험도**: 낮음
- **배포 환경**: 실거래 포트폴리오에 직접 반영
- **모니터링**: 월별 재평가

### Research Tier 2 (HOLD)
- **대상**: 2~3가지 기준만 충족한 모델
- **위험도**: 중간~높음
- **배포 환경**: 백테스트, 시뮬레이션, 논문 작성용
- **목표**: 부족한 기준을 개선하여 Tier 1로 승격

### Retrain/Demote (DEMOTE)
- **대상**: 1개 이하의 기준만 충족한 모델
- **위험도**: 매우 높음
- **조치**: 재학습 또는 프롬프트 재설계 필요

## Mode A: Sentiment Analysis Criteria

Mode A는 Claude Haiku 4.5를 사용한 뉴스/공시 감성 점수 산출입니다.

| 기준 | 요구사항 | 설명 |
|------|--------|------|
| **IR Delta** | > 0.10 | Information Ratio 초과 |
| **Reproducibility Std** | < 0.10 | 동일 입력에 대한 재현성 (표준편차) |
| **Drift KS-test** | p-value > 0.05 | Kolmogorov-Smirnov 검정으로 분포 변화 감지 |
| **Cost Ratio** | < 20% | API 비용 ÷ 초과수익 |

**의미**:
- **IR Delta > 0.10**: 동일 기간 벤치마크 대비 초과 수익률이 0.1 이상
- **Std < 0.10**: 동일 뉴스를 5~10회 재분석했을 때 점수의 변동이 적음 (재현성 높음)
- **KS p-value > 0.05**: 최근 월간 점수 분포가 초기 참조 분포와 통계적으로 유의한 차이가 없음
- **Cost < 20%**: Claude API 호출 비용이 생성한 초과수익의 20% 미만

### Example: Mode A Decision

```
IR Delta:              0.12 PASS (> 0.10)
Reproducibility Std:   0.08 PASS (< 0.10)
Drift KS-test:         p=0.12 PASS (> 0.05)
Cost Ratio:            0.18 PASS (< 0.20)

Pass Count: 4/4
Decision: PROMOTE
```

## Mode B: Opinion Generation Criteria

Mode B는 Claude Sonnet 4를 사용한 투자 의견(BUY/HOLD/SELL) 생성입니다.

| 기준 | 요구사항 | 설명 |
|------|--------|------|
| **IR Delta** | > 0.15 | Information Ratio 초과 (Mode A보다 높음) |
| **Opinion Match Rate** | > 80% | 동일 입력에 대한 의견 일치도 |
| **Drift KS-test** | p-value > 0.05 | 분포 변화 감지 (KS-test) |
| **Cost Ratio** | < 20% | API 비용 ÷ 초과수익 |

**의미**:
- **IR Delta > 0.15**: Mode A보다 높은 IR 기준 (더 복잡한 거시분석이므로 높은 기준)
- **Match Rate > 80%**: 동일 입력을 5회 분석했을 때 4회 이상 동일한 의견
- **KS p-value > 0.05**: 최근 월간 의견 분포가 초기 참조와 차이 없음
- **Cost < 20%**: 의견 생성 API 비용이 초과수익의 20% 미만

### Example: Mode B Decision

```
IR Delta:               0.16 PASS (> 0.15)
Opinion Match Rate:     0.82 PASS (> 0.80)
Drift KS-test:          p=0.08 PASS (> 0.05)
Cost Ratio:             0.15 PASS (< 0.20)

Pass Count: 4/4
Decision: PROMOTE
```

## Decision Process

### 1. 데이터 수집 (Weekly)
- 지난 1주일간의 감성 점수 및 의견 수집
- API 호출 수, 비용 기록
- 포트폴리오 성과 (IR 계산)

### 2. 월별 재평가 (Monthly)
- 지난 1개월간 누적 데이터로 평가
- 드리프트 검사: 참조 분포 vs 현재 월간 분포
- 재현성 테스트: 샘플 입력에 대한 반복 실행
- 비용-편익 분석

### 3. 프로덕션 승격 결정
- **PROMOTE (4/4 pass)**: 즉시 Tier 1 승격
- **HOLD (2-3/4 pass)**: Tier 2 유지, 다음 월 재평가
- **DEMOTE (0-1/4 pass)**: 즉시 재학습/조정 필요

### 4. 서명 및 로깅
- 결정 메모 생성 및 기록
- MongoDB에 평가 이력 저장
- 데이터 분석팀에 보고

## Implementation Details

### Drift Monitoring (KS-test)
```python
from core.ai_analyzer.drift_monitor import DriftMonitor

monitor = DriftMonitor()
# 초기 학습 기간의 점수로 참조 분포 설정
monitor.set_reference([0.5, 0.52, 0.48, 0.55, ...])

# 현재 월간 점수로 드리프트 검사
result = monitor.check_drift([0.51, 0.53, 0.49, ...])
# {'ks_statistic': 0.08, 'p_value': 0.12, 'is_drifted': False}
```

### Reproducibility Testing
```python
from core.ai_analyzer.reproducibility import ReproducibilityTest

test = ReproducibilityTest()

# Mode A: 동일 뉴스를 5회 분석
scores = [0.50, 0.51, 0.49, 0.50, 0.52]
result = test.test_sentiment_reproducibility(scores)
# {'mean': 0.504, 'std': 0.011, 'is_reproducible': True}

# Mode B: 동일 이슈에 대한 5가지 의견
opinions = ["BUY", "BUY", "HOLD", "BUY", "BUY"]
result = test.test_opinion_reproducibility(opinions)
# {'mode': 'BUY', 'match_rate': 0.8, 'is_reproducible': True}
```

### Cost-Benefit Analysis
```python
from core.ai_analyzer.cost_analyzer import CostAnalyzer

analyzer = CostAnalyzer(max_cost_ratio=0.20)

# 월간 데이터
monthly = [
    {"api_calls": 1000, "cost_per_call": 0.001,
     "excess_return_pct": 0.05, "portfolio_value": 10_000_000}
]

summary = analyzer.monthly_summary(monthly)
# {'total_cost': 1000, 'total_benefit': 500_000,
#  'avg_ratio': 0.002, 'is_cost_effective': True}
```

### Promotion Checklist
```python
from core.ai_analyzer.promotion_checklist import PromotionChecklist

checklist = PromotionChecklist()

# Mode A 평가
result_a = checklist.check_mode_a(
    ir_delta=0.12,
    reproducibility_std=0.08,
    drift_p_value=0.12,
    cost_ratio=0.18
)
# result_a['overall_decision'] = PromotionDecision.PROMOTE

# Mode B 평가
result_b = checklist.check_mode_b(
    ir_delta=0.16,
    match_rate=0.82,
    drift_p_value=0.08,
    cost_ratio=0.15
)
# result_b['overall_decision'] = PromotionDecision.PROMOTE

# 메모 생성
memo = checklist.generate_memo(result_a, result_b)
print(memo)
```

## Sign-Off Section

### Mode A Sign-Off Template
```
Date: 2026-04-04
Reviewed By: [AI Analyst Name]
Status: PROMOTE / HOLD / DEMOTE

Mode A (Sentiment):
  - IR Delta: 0.12 ✓
  - Reproducibility: 0.08 ✓
  - Drift Test: p=0.12 ✓
  - Cost Ratio: 18% ✓

Tier Assignment: Production Tier 1
Next Review: 2026-05-04
```

### Mode B Sign-Off Template
```
Date: 2026-04-04
Reviewed By: [AI Analyst Name]
Status: PROMOTE / HOLD / DEMOTE

Mode B (Opinion):
  - IR Delta: 0.16 ✓
  - Opinion Match: 82% ✓
  - Drift Test: p=0.08 ✓
  - Cost Ratio: 15% ✓

Tier Assignment: Production Tier 1
Next Review: 2026-05-04
```

## Monitoring & Alerting

### Critical Alerts
- **Drift Alert**: KS p-value < 0.05 → 즉시 재학습 검토
- **Cost Alert**: Cost ratio > 0.25 → 프롬프트 최적화 필요
- **Reproducibility Alert**: Std > 0.15 또는 Match rate < 70% → 즉시 조사

### Monthly Cadence
- 1일: 지난 월간 데이터 수집
- 2-5일: 드리프트/재현성/비용 검사
- 6일: 프로덕션 승격 메모 작성
- 7일: 경영진 보고 및 승인

## Reference

- Config: `/config/operational_thresholds.yaml`
- Modules:
  - `core.ai_analyzer.drift_monitor.DriftMonitor`
  - `core.ai_analyzer.cost_analyzer.CostAnalyzer`
  - `core.ai_analyzer.reproducibility.ReproducibilityTest`
  - `core.ai_analyzer.promotion_checklist.PromotionChecklist`
- Tests: `tests/test_llm_promotion.py`
