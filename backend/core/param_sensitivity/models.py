"""
파라미터 민감도 분석 데이터 모델

파라미터 스윕 정의, 실행 결과, 탄성치 분석 결과를 표현합니다.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

# ══════════════════════════════════════
# Enums
# ══════════════════════════════════════


class SweepMethod(str, Enum):
    """파라미터 스윕 방법"""

    GRID = "GRID"  # 격자 탐색
    RANDOM = "RANDOM"  # 무작위 샘플링


class SensitivityStatus(str, Enum):
    """분석 실행 상태"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


class ParamCategory(str, Enum):
    """파라미터 카테고리"""

    FACTOR_WEIGHT = "FACTOR_WEIGHT"  # 팩터 가중치
    TECHNICAL = "TECHNICAL"  # 기술적 지표 파라미터
    SIGNAL_THRESHOLD = "SIGNAL_THRESHOLD"  # 시그널 임계값
    RISK = "RISK"  # 리스크 관련
    COST = "COST"  # 비용 관련


# ══════════════════════════════════════
# 파라미터 정의
# ══════════════════════════════════════


@dataclass
class ParamRange:
    """단일 파라미터의 스윕 범위"""

    name: str
    category: ParamCategory
    base_value: float
    min_value: float
    max_value: float
    step: Optional[float] = None  # GRID 방식에서 사용
    n_samples: int = 10  # RANDOM 방식에서 사용
    description: str = ""

    def validate(self) -> bool:
        """범위 유효성 검증"""
        if self.min_value > self.max_value:
            return False
        if not (self.min_value <= self.base_value <= self.max_value):
            return False
        if self.step is not None and self.step <= 0:
            return False
        return True

    def grid_values(self) -> list[float]:
        """GRID 스윕을 위한 값 목록 생성"""
        if self.step is None or self.step <= 0:
            # step이 없으면 n_samples 기반으로 균등 분할
            if self.n_samples <= 1:
                return [self.base_value]
            step = (self.max_value - self.min_value) / (self.n_samples - 1)
        else:
            step = self.step

        values = []
        current = self.min_value
        while current <= self.max_value + step * 0.01:  # float 오차 허용
            values.append(round(current, 10))
            current += step
        return values


# ══════════════════════════════════════
# 실행 결과
# ══════════════════════════════════════


@dataclass
class ParamTrialResult:
    """단일 파라미터 조합의 백테스트 결과"""

    param_values: dict[str, float]
    sharpe_ratio: float = 0.0
    cagr: float = 0.0
    mdd: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    profit_factor: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0
    total_return: float = 0.0

    def to_dict(self) -> dict:
        return {
            "param_values": self.param_values,
            "sharpe_ratio": round(self.sharpe_ratio, 4),
            "cagr": round(self.cagr, 4),
            "mdd": round(self.mdd, 4),
            "sortino_ratio": round(self.sortino_ratio, 4),
            "calmar_ratio": round(self.calmar_ratio, 4),
            "profit_factor": round(self.profit_factor, 4),
            "win_rate": round(self.win_rate, 4),
            "total_trades": self.total_trades,
            "total_return": round(self.total_return, 4),
        }


@dataclass
class ParamElasticity:
    """단일 파라미터의 탄성치 (민감도 지표)"""

    param_name: str
    category: ParamCategory
    base_value: float
    # 핵심 탄성치: (Δmetric / metric) / (Δparam / param)
    sharpe_elasticity: float = 0.0
    cagr_elasticity: float = 0.0
    mdd_elasticity: float = 0.0
    # 변동 범위
    sharpe_range: tuple[float, float] = (0.0, 0.0)
    cagr_range: tuple[float, float] = (0.0, 0.0)
    mdd_range: tuple[float, float] = (0.0, 0.0)
    # 단조성 (monotonicity): -1(반비례) ~ 0(무관) ~ +1(정비례)
    monotonicity: float = 0.0
    # 안정 구간: metric 변동이 ±10% 이내인 파라미터 범위
    stable_range: tuple[float, float] = (0.0, 0.0)

    @property
    def impact_score(self) -> float:
        """종합 임팩트 점수 (탄성치 절대값 가중 평균)"""
        return abs(self.sharpe_elasticity) * 0.4 + abs(self.cagr_elasticity) * 0.3 + abs(self.mdd_elasticity) * 0.3

    def to_dict(self) -> dict:
        return {
            "param_name": self.param_name,
            "category": self.category.value,
            "base_value": self.base_value,
            "sharpe_elasticity": round(self.sharpe_elasticity, 4),
            "cagr_elasticity": round(self.cagr_elasticity, 4),
            "mdd_elasticity": round(self.mdd_elasticity, 4),
            "sharpe_range": [round(v, 4) for v in self.sharpe_range],
            "cagr_range": [round(v, 4) for v in self.cagr_range],
            "mdd_range": [round(v, 4) for v in self.mdd_range],
            "monotonicity": round(self.monotonicity, 4),
            "stable_range": [round(v, 4) for v in self.stable_range],
            "impact_score": round(self.impact_score, 4),
        }


@dataclass
class SensitivityRun:
    """민감도 분석 실행 기록"""

    run_id: str
    strategy_version: str
    sweep_method: SweepMethod
    param_ranges: list[ParamRange]
    status: SensitivityStatus = SensitivityStatus.PENDING
    total_trials: int = 0
    completed_trials: int = 0
    trial_results: list[ParamTrialResult] = field(default_factory=list)
    elasticities: list[ParamElasticity] = field(default_factory=list)
    best_params: Optional[dict[str, float]] = None
    best_sharpe: float = 0.0
    base_sharpe: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    error_message: str = ""

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "strategy_version": self.strategy_version,
            "sweep_method": self.sweep_method.value,
            "status": self.status.value,
            "total_trials": self.total_trials,
            "completed_trials": self.completed_trials,
            "best_params": self.best_params,
            "best_sharpe": round(self.best_sharpe, 4),
            "base_sharpe": round(self.base_sharpe, 4),
            "improvement": round(self.best_sharpe - self.base_sharpe, 4),
            "elasticities": [e.to_dict() for e in self.elasticities],
            "created_at": self.created_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error_message": self.error_message,
        }

    def to_summary_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "status": self.status.value,
            "total_trials": self.total_trials,
            "completed_trials": self.completed_trials,
            "best_sharpe": round(self.best_sharpe, 4),
            "base_sharpe": round(self.base_sharpe, 4),
            "top_sensitive_params": [
                e.param_name for e in sorted(self.elasticities, key=lambda x: x.impact_score, reverse=True)[:3]
            ],
        }


# ══════════════════════════════════════
# API 요청 모델
# ══════════════════════════════════════


class SensitivityRunRequest(BaseModel):
    """민감도 분석 실행 요청"""

    strategy_version: str = Field(..., description="전략 버전")
    sweep_method: SweepMethod = Field(default=SweepMethod.GRID, description="스윕 방법")
    tickers: list[str] = Field(default_factory=lambda: ["005930", "000660", "035420"], description="분석 대상 종목")
    param_overrides: Optional[dict[str, Any]] = Field(default=None, description="파라미터 범위 커스텀 오버라이드")

    class Config:
        extra = "forbid"
