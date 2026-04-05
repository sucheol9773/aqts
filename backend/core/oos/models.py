"""
OOS (Out-of-Sample) 검증 파이프라인 데이터 모델

3가지 핵심 스키마:
1. OOSRun: 검증 실행 단위 (walk-forward 1회)
2. OOSMetric: 실행별 성과 지표 (전체/레짐별)
3. OOSShadowAction: Shadow 확장용 최소 로그 (v2 준비)

Shadow 확장 설계 원칙:
- nullable 필드로 확장점 사전 배치
- run_type enum에 SHADOW reserved
- DB migration 없이 Shadow 메타데이터 저장 가능
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ══════════════════════════════════════
# Enums
# ══════════════════════════════════════
class OOSRunType(str, Enum):
    """실행 유형 (Shadow 확장 대비)"""

    OOS = "OOS"
    SHADOW = "SHADOW"  # reserved for v2


class OOSStatus(str, Enum):
    """실행 상태"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PASS = "PASS"
    REVIEW = "REVIEW"
    FAIL = "FAIL"
    ERROR = "ERROR"


class JobStatus(str, Enum):
    """비동기 작업 상태"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class GateLevel(str, Enum):
    """게이트 단계"""

    GATE_A = "GATE_A"  # 절대 기준
    GATE_B = "GATE_B"  # 상대 기준
    GATE_C = "GATE_C"  # 안정성 기준


class GateResult(str, Enum):
    """게이트 판정"""

    PASS = "PASS"
    REVIEW = "REVIEW"
    FAIL = "FAIL"


# ══════════════════════════════════════
# Core Data Models
# ══════════════════════════════════════
@dataclass
class OOSWindowResult:
    """단일 walk-forward 윈도우 결과"""

    window_index: int
    train_start: str  # YYYY-MM-DD
    train_end: str
    test_start: str
    test_end: str
    # 핵심 지표
    cagr: float = 0.0
    mdd: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    total_return: float = 0.0
    total_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    # 레짐별 지표
    regime_metrics: dict = field(default_factory=dict)


@dataclass
class OOSRun:
    """
    OOS 검증 실행 단위

    walk-forward 전체 실행의 메타데이터 + 집계 결과
    """

    run_id: str  # UUID
    run_type: OOSRunType = OOSRunType.OOS
    status: OOSStatus = OOSStatus.PENDING
    # 실행 정보
    strategy_version: str = ""
    data_version: str = ""  # 데이터 해시 (재현성)
    # 기간
    train_months: int = 24
    test_months: int = 3
    overall_start: str = ""  # 전체 데이터 시작일
    overall_end: str = ""  # 전체 데이터 종료일
    # 타이밍
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    # 집계 결과
    total_windows: int = 0
    passed_windows: int = 0
    avg_sharpe: float = 0.0
    avg_mdd: float = 0.0
    avg_cagr: float = 0.0
    avg_calmar: float = 0.0
    worst_mdd: float = 0.0
    sharpe_variance: float = 0.0  # 윈도우 간 Sharpe 분산 (안정성)
    # 게이트 판정
    gate_a_result: str = ""  # PASS/REVIEW/FAIL
    gate_b_result: str = ""
    gate_c_result: str = ""
    overall_gate: str = ""  # 최종 판정
    gate_reasons: list = field(default_factory=list)
    # 윈도우별 상세
    windows: list = field(default_factory=list)  # list[OOSWindowResult]
    # Shadow 확장 필드 (nullable)
    shadow_config: Optional[dict] = None  # Shadow 정책 설정 (v2)
    shadow_summary: Optional[dict] = None  # Shadow 집계 결과 (v2)
    # 에러
    error_message: str = ""
    error_code: str = ""

    def to_dict(self) -> dict:
        """API 응답용 직렬화"""
        return {
            "run_id": self.run_id,
            "run_type": self.run_type.value,
            "status": self.status.value,
            "strategy_version": self.strategy_version,
            "data_version": self.data_version,
            "train_months": self.train_months,
            "test_months": self.test_months,
            "overall_start": self.overall_start,
            "overall_end": self.overall_end,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "total_windows": self.total_windows,
            "passed_windows": self.passed_windows,
            "avg_sharpe": self.avg_sharpe,
            "avg_mdd": self.avg_mdd,
            "avg_cagr": self.avg_cagr,
            "avg_calmar": self.avg_calmar,
            "worst_mdd": self.worst_mdd,
            "sharpe_variance": self.sharpe_variance,
            "gate_a_result": self.gate_a_result,
            "gate_b_result": self.gate_b_result,
            "gate_c_result": self.gate_c_result,
            "overall_gate": self.overall_gate,
            "gate_reasons": self.gate_reasons,
            "windows": [
                {
                    "window_index": w.window_index,
                    "train_start": w.train_start,
                    "train_end": w.train_end,
                    "test_start": w.test_start,
                    "test_end": w.test_end,
                    "cagr": w.cagr,
                    "mdd": w.mdd,
                    "sharpe_ratio": w.sharpe_ratio,
                    "total_return": w.total_return,
                    "total_trades": w.total_trades,
                    "regime_metrics": w.regime_metrics,
                }
                for w in self.windows
            ],
            "error_message": self.error_message,
            "error_code": self.error_code,
        }

    def to_summary_dict(self) -> dict:
        """간략 요약 (목록 조회용)"""
        return {
            "run_id": self.run_id,
            "status": self.status.value,
            "overall_gate": self.overall_gate,
            "avg_sharpe": self.avg_sharpe,
            "worst_mdd": self.worst_mdd,
            "total_windows": self.total_windows,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
        }


@dataclass
class OOSMetric:
    """
    OOS 성과 지표 (개별 레코드)

    run_id + metric_name + regime 조합이 유니크
    """

    run_id: str
    metric_name: str  # cagr, mdd, sharpe, calmar, turnover, etc.
    metric_value: float
    regime: Optional[str] = None  # nullable; TRENDING_UP, BULL, etc.
    window_index: Optional[int] = None  # nullable; 특정 윈도우 지표


@dataclass
class OOSShadowAction:
    """
    Shadow 확장용 최소 로그 (v2 준비)

    OOS 실행 중 Shadow 정책이 활성화되면 기록.
    MVP에서는 baseline_threshold만 기록하고 나머지는 null.
    """

    run_id: str
    date: str  # YYYY-MM-DD
    regime: str  # 현재 레짐
    baseline_threshold: float  # 기존 정책의 임계값
    shadow_threshold: Optional[float] = None  # Shadow 정책 추천 임계값 (v2)
    reward_proxy: Optional[float] = None  # 보상 프록시 (v2)
    action_taken: Optional[str] = None  # 실제 취한 행동


# ══════════════════════════════════════
# API Request/Response Schemas (Pydantic)
# ══════════════════════════════════════
class OOSRunRequest(BaseModel):
    """OOS 실행 요청"""

    strategy_version: str = Field(
        default="current",
        description="전략 버전 식별자",
    )
    train_months: int = Field(
        default=24,
        ge=6,
        le=120,
        description="학습 기간 (개월)",
    )
    test_months: int = Field(
        default=3,
        ge=1,
        le=12,
        description="평가 기간 (개월)",
    )
    tickers: list[str] = Field(
        default_factory=lambda: ["005930"],
        description="대상 종목 리스트",
        min_length=1,
    )

    model_config = {"extra": "forbid"}


class OOSJobResponse(BaseModel):
    """OOS 작업 즉시 응답"""

    run_id: str
    status: str
    message: str

    model_config = {"extra": "forbid"}
