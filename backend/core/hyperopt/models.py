"""
하이퍼파라미터 최적화 데이터 모델

최적화 시행(trial) 결과 및 전체 스터디 결과를 담는 데이터 클래스.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class TrialResult:
    """단일 최적화 시행 결과"""

    trial_number: int
    params: dict[str, float]
    # OOS 성과 (Walk-Forward)
    oos_sharpe: float
    oos_cagr: float
    oos_mdd: float
    oos_sortino: float
    oos_calmar: float
    oos_win_rate: float
    # 추가 메트릭
    oos_window_count: int = 0  # OOS 윈도우 수
    oos_positive_windows: int = 0  # Sharpe > 0 윈도우 수
    oos_sharpe_variance: float = 0.0  # 윈도우 간 Sharpe 분산
    # 메타
    duration_seconds: float = 0.0
    pruned: bool = False

    def to_dict(self) -> dict:
        """직렬화"""
        return {
            "trial_number": self.trial_number,
            "params": self.params,
            "oos_sharpe": round(self.oos_sharpe, 4),
            "oos_cagr": round(self.oos_cagr, 4),
            "oos_mdd": round(self.oos_mdd, 4),
            "oos_sortino": round(self.oos_sortino, 4),
            "oos_calmar": round(self.oos_calmar, 4),
            "oos_win_rate": round(self.oos_win_rate, 4),
            "oos_window_count": self.oos_window_count,
            "oos_positive_windows": self.oos_positive_windows,
            "oos_sharpe_variance": round(self.oos_sharpe_variance, 4),
            "duration_seconds": round(self.duration_seconds, 1),
            "pruned": self.pruned,
        }


@dataclass
class OptimizationResult:
    """전체 최적화 결과"""

    study_name: str
    n_trials: int
    n_completed: int
    n_pruned: int
    # 최적 파라미터
    best_params: dict[str, float]
    best_oos_sharpe: float
    best_trial_number: int
    # 현재 (최적화 전) 기준선
    baseline_oos_sharpe: float
    baseline_params: dict[str, float]
    # 개선율
    improvement_pct: float  # (best - baseline) / |baseline| * 100
    # 전체 trial 기록
    trials: list[TrialResult] = field(default_factory=list)
    # 메타
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    total_duration_seconds: float = 0.0
    # 파라미터 중요도 (Optuna fANOVA)
    param_importances: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """API 응답용 직렬화"""
        return {
            "study_name": self.study_name,
            "n_trials": self.n_trials,
            "n_completed": self.n_completed,
            "n_pruned": self.n_pruned,
            "best_params": {k: round(v, 6) for k, v in self.best_params.items()},
            "best_oos_sharpe": round(self.best_oos_sharpe, 4),
            "best_trial_number": self.best_trial_number,
            "baseline_oos_sharpe": round(self.baseline_oos_sharpe, 4),
            "baseline_params": {k: round(v, 6) for k, v in self.baseline_params.items()},
            "improvement_pct": round(self.improvement_pct, 2),
            "total_duration_seconds": round(self.total_duration_seconds, 1),
            "param_importances": {k: round(v, 4) for k, v in self.param_importances.items()},
            "top_5_trials": [t.to_dict() for t in sorted(self.trials, key=lambda t: t.oos_sharpe, reverse=True)[:5]],
        }
