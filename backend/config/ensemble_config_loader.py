"""
Ensemble Configuration Loader

YAML 기반 동적 앙상블 설정을 로드, 검증, 저장합니다.

구조:
  1. load_ensemble_config() → YAML/기본값으로부터 설정 로드
  2. save_ensemble_config() → 설정을 YAML로 저장
  3. apply_hyperopt_results() → hyperopt JSON 결과를 YAML에 적용
  4. validate_ensemble_config() → 설정값 검증

계층 구조:
  코드 기본값 < YAML 설정 < 함수 파라미터 (가장 높은 우선순위)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

from config.logging import logger

_CONFIG_DIR = Path(__file__).parent
_ENSEMBLE_CONFIG_PATH = _CONFIG_DIR / "ensemble_config.yaml"

# ── 코드 기본값 (최종 폴백) ──
_CODE_DEFAULTS = {
    "ensemble": {
        "adx_threshold": 25,
        "vol_pct_threshold": 0.75,
        "perf_window": 60,
        "softmax_temperature": 5.0,
        "perf_blend": 0.3,
        "target_vol": 0.25,
    },
    "regime_weights": {
        "TRENDING_UP": {"TF": 0.55, "MR": 0.15, "RP": 0.30},
        "TRENDING_DOWN": {"TF": 0.40, "MR": 0.15, "RP": 0.45},
        "HIGH_VOLATILITY": {"TF": 0.20, "MR": 0.20, "RP": 0.60},
        "SIDEWAYS": {"TF": 0.25, "MR": 0.45, "RP": 0.30},
    },
    "risk": {
        "stop_loss_atr_multiplier": 2.0,
        "trailing_stop_atr_multiplier": 3.0,
        "max_drawdown_limit": 0.20,
        "drawdown_cooldown_days": 20,
        "dd_cushion_start": 0.08,
        "dd_cushion_floor": 0.25,
    },
}


def load_ensemble_config() -> dict:
    """
    YAML 설정을 로드합니다.

    파일이 존재하면 YAML을 로드하고, 없으면 코드 기본값을 반환합니다.

    Returns:
        {
            "ensemble": {...},
            "regime_weights": {regime: {TF, MR, RP}},
            "risk": {...},
        }
    """
    if not _ENSEMBLE_CONFIG_PATH.exists():
        logger.info(f"Ensemble config YAML not found at {_ENSEMBLE_CONFIG_PATH}. " "Using code defaults.")
        return _CODE_DEFAULTS.copy()

    try:
        with open(_ENSEMBLE_CONFIG_PATH, "r", encoding="utf-8") as f:
            yaml_config = yaml.safe_load(f) or {}

        # 기본값으로 초기화 후 YAML로 오버라이드
        config = _CODE_DEFAULTS.copy()

        if "ensemble" in yaml_config and yaml_config["ensemble"]:
            config["ensemble"].update(yaml_config["ensemble"])

        if "regime_weights" in yaml_config and yaml_config["regime_weights"]:
            config["regime_weights"].update(yaml_config["regime_weights"])

        if "risk" in yaml_config and yaml_config["risk"]:
            config["risk"].update(yaml_config["risk"])

        logger.info(f"Loaded ensemble config from {_ENSEMBLE_CONFIG_PATH}")
        return config

    except Exception as e:
        logger.error(f"Failed to load ensemble config from {_ENSEMBLE_CONFIG_PATH}: {e}. " "Using code defaults.")
        return _CODE_DEFAULTS.copy()


def save_ensemble_config(config: dict, metadata: Optional[dict] = None, config_path: Optional[Path] = None) -> Path:
    """
    YAML 파일에 설정을 저장합니다.

    Args:
        config: {"ensemble": {...}, "regime_weights": {...}, "risk": {...}}
        metadata: 메타데이터 딕셔너리 (last_optimized, oos_sharpe_baseline 등)
        config_path: 저장 경로 (기본: ensemble_config.yaml)

    Returns:
        저장된 파일 경로

    Raises:
        ValueError: 설정 검증 실패
    """
    if not config_path:
        config_path = _ENSEMBLE_CONFIG_PATH

    # 설정 검증
    errors = validate_ensemble_config(config)
    if errors:
        msg = "Invalid ensemble config:\n" + "\n".join(errors)
        logger.error(msg)
        raise ValueError(msg)

    # YAML 구조 생성
    yaml_config = {
        "metadata": metadata
        or {
            "last_optimized": None,
            "oos_sharpe_baseline": None,
            "version": "1.0",
        },
        "ensemble": config.get("ensemble", {}),
        "regime_weights": config.get("regime_weights", {}),
        "risk": config.get("risk", {}),
    }

    # 파일 쓰기
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(
                yaml_config,
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )

        logger.info(f"Saved ensemble config to {config_path}")
        return config_path

    except Exception as e:
        logger.error(f"Failed to save ensemble config to {config_path}: {e}")
        raise


def validate_ensemble_config(config: dict) -> list[str]:
    """
    설정값을 검증합니다.

    Returns:
        에러 메시지 목록 (빈 리스트 = 유효)
    """
    errors = []

    # ── 앙상블 파라미터 검증 ──
    ensemble = config.get("ensemble", {})

    if "adx_threshold" in ensemble:
        val = ensemble["adx_threshold"]
        if not 10 <= val <= 50:
            errors.append(f"ensemble.adx_threshold must be in [10, 50], got {val}")

    if "vol_pct_threshold" in ensemble:
        val = ensemble["vol_pct_threshold"]
        if not 0.0 <= val <= 1.0:
            errors.append(f"ensemble.vol_pct_threshold must be in [0.0, 1.0], got {val}")

    if "perf_window" in ensemble:
        val = ensemble["perf_window"]
        if not isinstance(val, int) or val < 5:
            errors.append(f"ensemble.perf_window must be int >= 5, got {val}")

    if "softmax_temperature" in ensemble:
        val = ensemble["softmax_temperature"]
        if not 0.1 <= val <= 50.0:
            errors.append(f"ensemble.softmax_temperature must be in [0.1, 50.0], got {val}")

    if "perf_blend" in ensemble:
        val = ensemble["perf_blend"]
        if not 0.0 <= val <= 1.0:
            errors.append(f"ensemble.perf_blend must be in [0.0, 1.0], got {val}")

    if "target_vol" in ensemble:
        val = ensemble["target_vol"]
        if not 0.01 <= val <= 1.0:
            errors.append(f"ensemble.target_vol must be in [0.01, 1.0], got {val}")

    # ── 레짐 가중치 검증 ──
    regime_weights = config.get("regime_weights", {})
    valid_regimes = {"TRENDING_UP", "TRENDING_DOWN", "HIGH_VOLATILITY", "SIDEWAYS"}

    for regime, weights in regime_weights.items():
        if regime not in valid_regimes:
            errors.append(f"Invalid regime: {regime}. Must be one of {valid_regimes}")
            continue

        if not isinstance(weights, dict):
            errors.append(f"regime_weights.{regime} must be dict, got {type(weights)}")
            continue

        required_keys = {"TF", "MR", "RP"}
        if set(weights.keys()) != required_keys:
            errors.append(f"regime_weights.{regime} must have keys {required_keys}, " f"got {set(weights.keys())}")
            continue

        # 합계 검증 (1.0이어야 함)
        total = sum(weights.values())
        if abs(total - 1.0) > 1e-6:
            errors.append(f"regime_weights.{regime} sum must be 1.0, got {total:.4f}")

        # 각 값 범위 검증 (0 ~ 1)
        for key, val in weights.items():
            if not 0.0 <= val <= 1.0:
                errors.append(f"regime_weights.{regime}.{key} must be in [0.0, 1.0], got {val}")

    # ── 리스크 파라미터 검증 ──
    risk = config.get("risk", {})

    if "stop_loss_atr_multiplier" in risk:
        val = risk["stop_loss_atr_multiplier"]
        if not 0.5 <= val <= 10.0:
            errors.append(f"risk.stop_loss_atr_multiplier must be in [0.5, 10.0], got {val}")

    if "trailing_stop_atr_multiplier" in risk:
        val = risk["trailing_stop_atr_multiplier"]
        if not 0.5 <= val <= 10.0:
            errors.append(f"risk.trailing_stop_atr_multiplier must be in [0.5, 10.0], got {val}")

    if "max_drawdown_limit" in risk:
        val = risk["max_drawdown_limit"]
        if not 0.05 <= val <= 0.5:
            errors.append(f"risk.max_drawdown_limit must be in [0.05, 0.5], got {val}")

    if "drawdown_cooldown_days" in risk:
        val = risk["drawdown_cooldown_days"]
        if not isinstance(val, int) or val < 1:
            errors.append(f"risk.drawdown_cooldown_days must be int >= 1, got {val}")

    if "dd_cushion_start" in risk:
        val = risk["dd_cushion_start"]
        if not 0.01 <= val <= 0.3:
            errors.append(f"risk.dd_cushion_start must be in [0.01, 0.3], got {val}")

    if "dd_cushion_floor" in risk:
        val = risk["dd_cushion_floor"]
        if not 0.1 <= val <= 0.9:
            errors.append(f"risk.dd_cushion_floor must be in [0.1, 0.9], got {val}")

    return errors


def apply_hyperopt_results(result_path: str, config_path: Optional[Path] = None) -> dict:
    """
    Hyperopt JSON 결과를 로드하여 YAML 설정에 적용합니다.

    JSON 구조 예상:
    {
        "best_params": {
            "adx_threshold": 28.5,
            "w_trending_up_tf": 0.52,
            ...
        },
        "best_value": 0.95,
        "best_trial": 42
    }

    Args:
        result_path: Hyperopt JSON 결과 파일 경로
        config_path: YAML 저장 경로 (기본: ensemble_config.yaml)

    Returns:
        업데이트된 설정 딕셔너리

    Raises:
        FileNotFoundError: JSON 파일을 찾을 수 없음
        ValueError: JSON 파싱 실패 또는 설정 검증 실패
    """
    if not config_path:
        config_path = _ENSEMBLE_CONFIG_PATH

    # JSON 파일 로드
    result_file = Path(result_path)
    if not result_file.exists():
        raise FileNotFoundError(f"Hyperopt result file not found: {result_path}")

    try:
        with open(result_file, "r", encoding="utf-8") as f:
            result = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse JSON from {result_path}: {e}")

    best_params = result.get("best_params", {})
    best_value = result.get("best_value", None)

    if not best_params:
        raise ValueError(f"No best_params found in {result_path}")

    logger.info(f"Applying hyperopt results: best_value={best_value}, " f"best_trial={result.get('best_trial', 'N/A')}")

    # 파라미터를 앙상블/레짐/리스크 그룹으로 변환
    config = _convert_hyperopt_params_to_config(best_params)

    # 메타데이터 생성
    metadata = {
        "last_optimized": datetime.now().strftime("%Y-%m-%d"),
        "oos_sharpe_baseline": float(best_value) if best_value else None,
        "version": "1.0",
    }

    # YAML에 저장
    save_ensemble_config(config, metadata=metadata, config_path=config_path)

    logger.info(f"Hyperopt results applied and saved to {config_path}")
    return config


def _convert_hyperopt_params_to_config(params: dict) -> dict:
    """
    Hyperopt 파라미터를 설정 딕셔너리로 변환합니다.

    입력: flat 파라미터 dict (hyperopt trial output)
    출력: {ensemble: {...}, regime_weights: {...}, risk: {...}}
    """
    config = {
        "ensemble": {},
        "regime_weights": {},
        "risk": {},
    }

    # ── 앙상블 파라미터 매핑 ──
    ensemble_keys = {
        "adx_threshold",
        "vol_pct_threshold",
        "perf_window",
        "softmax_temperature",
        "perf_blend",
        "target_vol",
    }
    for key in ensemble_keys:
        if key in params:
            config["ensemble"][key] = params[key]

    # ── 레짐 가중치 매핑 ──
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

            config["regime_weights"][regime_name] = {
                "TF": round(float(tf_val), 4),
                "MR": round(float(mr_val), 4),
                "RP": round(float(rp_val), 4),
            }

    # ── 리스크 파라미터 매핑 ──
    risk_keys = {
        "stop_loss_atr_multiplier",
        "trailing_stop_atr_multiplier",
        "max_drawdown_limit",
        "drawdown_cooldown_days",
        "dd_cushion_start",
        "dd_cushion_floor",
    }
    for key in risk_keys:
        if key in params:
            config["risk"][key] = params[key]

    return config
