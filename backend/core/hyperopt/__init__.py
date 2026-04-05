"""
하이퍼파라미터 자동 최적화 모듈 (Hyperparameter Optimization)

Optuna 기반 베이지안 최적화로 백테스트 + OOS 성과를 극대화하는
파라미터 조합을 자동 탐색합니다.

주요 컴포넌트:
  - SearchSpace: 최적화 대상 파라미터 범위 정의
  - ObjectiveFunction: 파라미터 → OOS Sharpe 평가 함수
  - HyperoptOptimizer: Optuna study 오케스트레이터
  - OptimizationResult: 최적화 결과 데이터 모델
"""

from core.hyperopt.models import OptimizationResult, TrialResult
from core.hyperopt.optimizer import HyperoptOptimizer
from core.hyperopt.search_space import SearchSpace

__all__ = [
    "HyperoptOptimizer",
    "OptimizationResult",
    "SearchSpace",
    "TrialResult",
]
