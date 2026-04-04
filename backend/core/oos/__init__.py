"""
AQTS OOS (Out-of-Sample) 검증 파이프라인

모듈 구성:
- models: 데이터 모델 (OOSRun, OOSMetric, OOSShadowAction)
- walk_forward: Walk-forward 실행기
- gate_evaluator: 3단계 게이트 평가
- regime_mapping: 레짐 분류 체계 간 매핑
- job_manager: 비동기 작업 관리

Shadow 확장 포인트:
- OOSRunType.SHADOW (reserved enum)
- OOSShadowAction (nullable 확장 필드)
- shadow_config / shadow_summary (OOSRun 확장 필드)
"""

from .models import (
    GateLevel,
    GateResult,
    JobStatus,
    OOSJobResponse,
    OOSMetric,
    OOSRun,
    OOSRunRequest,
    OOSRunType,
    OOSShadowAction,
    OOSStatus,
    OOSWindowResult,
)
from .gate_evaluator import GateEvaluator
from .regime_mapping import RegimeMapper
from .walk_forward import WalkForwardEngine
from .job_manager import OOSJobManager

__all__ = [
    # Models
    "GateLevel",
    "GateResult",
    "JobStatus",
    "OOSJobResponse",
    "OOSMetric",
    "OOSRun",
    "OOSRunRequest",
    "OOSRunType",
    "OOSShadowAction",
    "OOSStatus",
    "OOSWindowResult",
    # Core
    "GateEvaluator",
    "RegimeMapper",
    "WalkForwardEngine",
    "OOSJobManager",
]
