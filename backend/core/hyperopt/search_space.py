"""
하이퍼파라미터 탐색 공간 정의

최적화 대상 파라미터의 범위, 기본값, 카테고리를 정의합니다.
Optuna trial에서 suggest_*() 호출에 사용됩니다.

파라미터 그룹:
  1. 동적 앙상블 파라미터 (DynamicEnsembleService)
  2. 레짐별 전략 가중치 (REGIME_WEIGHTS)
  3. 리스크 관리 파라미터 (BacktestConfig)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ParamType(str, Enum):
    """파라미터 타입"""

    FLOAT = "float"
    INT = "int"
    CATEGORICAL = "categorical"


@dataclass
class ParamSpec:
    """단일 파라미터 스펙"""

    name: str
    param_type: ParamType
    low: Optional[float] = None
    high: Optional[float] = None
    step: Optional[float] = None
    choices: Optional[list] = None
    default: Optional[float] = None
    group: str = ""
    description: str = ""


class SearchSpace:
    """
    탐색 공간 정의

    ENSEMBLE 전략 기준 최적화 대상 파라미터를 제공합니다.
    파라미터 그룹별 활성/비활성화가 가능합니다.
    """

    # ── 동적 앙상블 핵심 파라미터 ──
    ENSEMBLE_PARAMS: list[ParamSpec] = [
        ParamSpec(
            name="adx_threshold",
            param_type=ParamType.FLOAT,
            low=15.0,
            high=40.0,
            step=1.0,
            default=25.0,
            group="ensemble",
            description="ADX 추세 판정 임계값",
        ),
        ParamSpec(
            name="vol_pct_threshold",
            param_type=ParamType.FLOAT,
            low=0.5,
            high=0.95,
            step=0.05,
            default=0.75,
            group="ensemble",
            description="변동성 백분위 HIGH_VOL 임계값",
        ),
        ParamSpec(
            name="perf_window",
            param_type=ParamType.INT,
            low=20,
            high=120,
            step=10,
            default=60,
            group="ensemble",
            description="롤링 성과 측정 윈도우 (영업일)",
        ),
        ParamSpec(
            name="softmax_temperature",
            param_type=ParamType.FLOAT,
            low=1.0,
            high=20.0,
            step=0.5,
            default=5.0,
            group="ensemble",
            description="softmax 온도 (높을수록 균등 가중)",
        ),
        ParamSpec(
            name="perf_blend",
            param_type=ParamType.FLOAT,
            low=0.0,
            high=0.7,
            step=0.05,
            default=0.3,
            group="ensemble",
            description="성과 블렌딩 비율 (레짐 vs 성과)",
        ),
        ParamSpec(
            name="target_vol",
            param_type=ParamType.FLOAT,
            low=0.10,
            high=0.50,
            step=0.025,
            default=0.25,
            group="ensemble",
            description="연환산 목표 변동성",
        ),
    ]

    # ── 레짐별 가중치 ──
    # TF, MR 가중치를 suggest → RP = 1.0 - TF - MR (합계 1.0 제약)
    REGIME_WEIGHT_PARAMS: list[ParamSpec] = [
        # TRENDING_UP
        ParamSpec(
            name="w_trending_up_tf",
            param_type=ParamType.FLOAT,
            low=0.30,
            high=0.80,
            step=0.05,
            default=0.55,
            group="regime_weights",
            description="TRENDING_UP 레짐 TF 가중치",
        ),
        ParamSpec(
            name="w_trending_up_mr",
            param_type=ParamType.FLOAT,
            low=0.05,
            high=0.35,
            step=0.05,
            default=0.15,
            group="regime_weights",
            description="TRENDING_UP 레짐 MR 가중치",
        ),
        # TRENDING_DOWN
        ParamSpec(
            name="w_trending_down_tf",
            param_type=ParamType.FLOAT,
            low=0.20,
            high=0.60,
            step=0.05,
            default=0.40,
            group="regime_weights",
            description="TRENDING_DOWN 레짐 TF 가중치",
        ),
        ParamSpec(
            name="w_trending_down_mr",
            param_type=ParamType.FLOAT,
            low=0.05,
            high=0.35,
            step=0.05,
            default=0.15,
            group="regime_weights",
            description="TRENDING_DOWN 레짐 MR 가중치",
        ),
        # HIGH_VOLATILITY
        ParamSpec(
            name="w_high_vol_tf",
            param_type=ParamType.FLOAT,
            low=0.05,
            high=0.40,
            step=0.05,
            default=0.20,
            group="regime_weights",
            description="HIGH_VOLATILITY 레짐 TF 가중치",
        ),
        ParamSpec(
            name="w_high_vol_mr",
            param_type=ParamType.FLOAT,
            low=0.05,
            high=0.40,
            step=0.05,
            default=0.20,
            group="regime_weights",
            description="HIGH_VOLATILITY 레짐 MR 가중치",
        ),
        # SIDEWAYS
        ParamSpec(
            name="w_sideways_tf",
            param_type=ParamType.FLOAT,
            low=0.10,
            high=0.45,
            step=0.05,
            default=0.25,
            group="regime_weights",
            description="SIDEWAYS 레짐 TF 가중치",
        ),
        ParamSpec(
            name="w_sideways_mr",
            param_type=ParamType.FLOAT,
            low=0.20,
            high=0.65,
            step=0.05,
            default=0.45,
            group="regime_weights",
            description="SIDEWAYS 레짐 MR 가중치",
        ),
    ]

    # ── 리스크 관리 파라미터 ──
    RISK_PARAMS: list[ParamSpec] = [
        ParamSpec(
            name="stop_loss_atr_multiplier",
            param_type=ParamType.FLOAT,
            low=1.0,
            high=4.0,
            step=0.25,
            default=2.0,
            group="risk",
            description="ATR 기반 손절 배수",
        ),
        ParamSpec(
            name="trailing_stop_atr_multiplier",
            param_type=ParamType.FLOAT,
            low=1.5,
            high=5.0,
            step=0.25,
            default=3.0,
            group="risk",
            description="ATR 기반 트레일링 손절 배수",
        ),
        ParamSpec(
            name="max_drawdown_limit",
            param_type=ParamType.FLOAT,
            low=0.10,
            high=0.35,
            step=0.025,
            default=0.20,
            group="risk",
            description="포트폴리오 DD 한도",
        ),
        ParamSpec(
            name="drawdown_cooldown_days",
            param_type=ParamType.INT,
            low=5,
            high=40,
            step=5,
            default=20,
            group="risk",
            description="DD 발동 후 대기 영업일",
        ),
        ParamSpec(
            name="dd_cushion_start",
            param_type=ParamType.FLOAT,
            low=0.04,
            high=0.18,
            step=0.02,
            default=0.08,
            group="risk",
            description="DD 비례 축소 시작점",
        ),
        ParamSpec(
            name="dd_cushion_floor",
            param_type=ParamType.FLOAT,
            low=0.10,
            high=0.50,
            step=0.05,
            default=0.25,
            group="risk",
            description="DD 비례 축소 최소 포지션 비율",
        ),
    ]

    @classmethod
    def get_all_params(cls) -> list[ParamSpec]:
        """전체 파라미터 목록"""
        return cls.ENSEMBLE_PARAMS + cls.REGIME_WEIGHT_PARAMS + cls.RISK_PARAMS

    @classmethod
    def get_params_by_groups(cls, groups: list[str]) -> list[ParamSpec]:
        """그룹별 파라미터 필터"""
        all_params = cls.get_all_params()
        return [p for p in all_params if p.group in groups]

    @classmethod
    def get_defaults(cls) -> dict[str, float]:
        """전체 기본값 딕셔너리"""
        return {p.name: p.default for p in cls.get_all_params()}

    @classmethod
    def suggest_params(
        cls,
        trial,
        groups: Optional[list[str]] = None,
    ) -> dict[str, float]:
        """
        Optuna trial에서 파라미터 샘플링

        Args:
            trial: optuna.trial.Trial
            groups: 탐색할 그룹 목록 (None이면 전체)

        Returns:
            {param_name: sampled_value}
        """
        if groups:
            params = cls.get_params_by_groups(groups)
        else:
            params = cls.get_all_params()

        sampled = {}
        for spec in params:
            if spec.param_type == ParamType.FLOAT:
                sampled[spec.name] = trial.suggest_float(
                    spec.name,
                    spec.low,
                    spec.high,
                    step=spec.step,
                )
            elif spec.param_type == ParamType.INT:
                sampled[spec.name] = trial.suggest_int(
                    spec.name,
                    int(spec.low),
                    int(spec.high),
                    step=int(spec.step) if spec.step else 1,
                )
            elif spec.param_type == ParamType.CATEGORICAL:
                sampled[spec.name] = trial.suggest_categorical(
                    spec.name,
                    spec.choices,
                )

        # ── 레짐 가중치 합계 1.0 제약 검증 ──
        cls._validate_regime_weights(sampled, trial)

        return sampled

    @classmethod
    def _validate_regime_weights(cls, params: dict[str, float], trial) -> None:
        """
        레짐별 TF + MR <= 0.90 제약을 검증하고, 위반 시 trial을 prune.
        RP = 1.0 - TF - MR 이므로 RP >= 0.10 보장.
        """
        import optuna

        regime_prefixes = [
            "w_trending_up",
            "w_trending_down",
            "w_high_vol",
            "w_sideways",
        ]

        for prefix in regime_prefixes:
            tf_key = f"{prefix}_tf"
            mr_key = f"{prefix}_mr"

            if tf_key in params and mr_key in params:
                tf_val = params[tf_key]
                mr_val = params[mr_key]

                if tf_val + mr_val > 0.90:
                    raise optuna.TrialPruned(
                        f"{prefix}: TF({tf_val:.2f}) + MR({mr_val:.2f}) " f"= {tf_val + mr_val:.2f} > 0.90"
                    )

    @classmethod
    def params_to_ensemble_config(cls, params: dict[str, float]) -> dict:
        """
        샘플된 파라미터를 DynamicEnsembleService용 config로 변환

        Returns:
            {
                "ensemble_params": {...},
                "regime_weights": {regime: {TF, MR, RP}},
                "risk_params": {...},
            }
        """
        ensemble_params = {}
        regime_weights = {}
        risk_params = {}

        # 앙상블 파라미터
        ensemble_keys = {p.name for p in cls.ENSEMBLE_PARAMS}
        for k, v in params.items():
            if k in ensemble_keys:
                ensemble_params[k] = v

        # 레짐 가중치
        regime_map = {
            "w_trending_up": "TRENDING_UP",
            "w_trending_down": "TRENDING_DOWN",
            "w_high_vol": "HIGH_VOLATILITY",
            "w_sideways": "SIDEWAYS",
        }
        for prefix, regime_name in regime_map.items():
            tf_key = f"{prefix}_tf"
            mr_key = f"{prefix}_mr"
            if tf_key in params and mr_key in params:
                tf_val = params[tf_key]
                mr_val = params[mr_key]
                rp_val = max(1.0 - tf_val - mr_val, 0.0)
                regime_weights[regime_name] = {
                    "TF": round(tf_val, 4),
                    "MR": round(mr_val, 4),
                    "RP": round(rp_val, 4),
                }

        # 리스크 파라미터
        risk_keys = {p.name for p in cls.RISK_PARAMS}
        for k, v in params.items():
            if k in risk_keys:
                risk_params[k] = v

        return {
            "ensemble_params": ensemble_params,
            "regime_weights": regime_weights,
            "risk_params": risk_params,
        }
