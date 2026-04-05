"""
Ensemble Configuration Loader Tests

ensemble_config_loader 모듈을 검증합니다:
  - load_ensemble_config() → YAML/기본값 로드
  - save_ensemble_config() → YAML 저장
  - validate_ensemble_config() → 설정 검증
  - apply_hyperopt_results() → hyperopt JSON 적용
  - DynamicEnsembleService와의 통합

테스트 범위:
  1. 기본값 로드 (YAML 미존재)
  2. YAML 로드 및 기본값 오버라이드
  3. YAML 저장 및 재로드 일관성
  4. 설정 검증 (유효/무효 케이스)
  5. Hyperopt 결과 적용
  6. DynamicEnsembleService에서 YAML 사용
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from config.ensemble_config_loader import (
    apply_hyperopt_results,
    load_ensemble_config,
    save_ensemble_config,
    validate_ensemble_config,
)
from core.strategy_ensemble.dynamic_ensemble import (
    DynamicEnsembleService,
    DynamicRegime,
)


class TestLoadEnsembleConfig:
    """load_ensemble_config() 테스트"""

    def test_load_default_config_when_yaml_missing(self):
        """YAML이 없을 때 코드 기본값 반환"""
        with patch("config.ensemble_config_loader._ENSEMBLE_CONFIG_PATH") as mock_path:
            mock_path.exists.return_value = False
            config = load_ensemble_config()

            assert "ensemble" in config
            assert "regime_weights" in config
            assert "risk" in config

            # 기본값 확인
            assert config["ensemble"]["adx_threshold"] == 25
            assert config["ensemble"]["vol_pct_threshold"] == 0.75
            assert config["ensemble"]["perf_window"] == 60
            assert config["ensemble"]["softmax_temperature"] == 5.0
            assert config["ensemble"]["perf_blend"] == 0.3
            assert config["ensemble"]["target_vol"] == 0.25

    def test_load_yaml_config(self):
        """YAML 파일 로드"""
        config_yaml = """
ensemble:
  adx_threshold: 28
  vol_pct_threshold: 0.80
regime_weights:
  TRENDING_UP:
    TF: 0.52
    MR: 0.18
    RP: 0.30
risk:
  max_drawdown_limit: 0.25
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "ensemble_config.yaml"
            config_path.write_text(config_yaml)

            with patch("config.ensemble_config_loader._ENSEMBLE_CONFIG_PATH", config_path):
                config = load_ensemble_config()

                # YAML 값 확인
                assert config["ensemble"]["adx_threshold"] == 28
                assert config["ensemble"]["vol_pct_threshold"] == 0.80

                # 기본값 유지
                assert config["ensemble"]["perf_window"] == 60

                # 레짐 가중치 확인
                assert config["regime_weights"]["TRENDING_UP"]["TF"] == 0.52

    def test_yaml_overrides_defaults(self):
        """YAML 값이 기본값을 오버라이드"""
        config_yaml = """
ensemble:
  adx_threshold: 30
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "ensemble_config.yaml"
            config_path.write_text(config_yaml)

            with patch("config.ensemble_config_loader._ENSEMBLE_CONFIG_PATH", config_path):
                config = load_ensemble_config()

                # YAML 값
                assert config["ensemble"]["adx_threshold"] == 30
                # 기본값 (YAML에 명시되지 않음)
                assert config["ensemble"]["perf_blend"] == 0.3


class TestValidateEnsembleConfig:
    """validate_ensemble_config() 테스트"""

    def test_validate_valid_config(self):
        """유효한 설정은 에러 없음"""
        config = {
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
        errors = validate_ensemble_config(config)
        assert errors == []

    def test_validate_adx_threshold_out_of_range(self):
        """ADX 임계값 범위 검증"""
        config = {"ensemble": {"adx_threshold": 60}}
        errors = validate_ensemble_config(config)
        assert any("adx_threshold" in e for e in errors)

    def test_validate_vol_pct_threshold_out_of_range(self):
        """변동성 백분위 임계값 범위 검증"""
        config = {"ensemble": {"vol_pct_threshold": 1.5}}
        errors = validate_ensemble_config(config)
        assert any("vol_pct_threshold" in e for e in errors)

    def test_validate_perf_window_invalid_type(self):
        """성과 윈도우 타입 검증"""
        config = {"ensemble": {"perf_window": 3}}  # 5 미만
        errors = validate_ensemble_config(config)
        assert any("perf_window" in e for e in errors)

    def test_validate_regime_weights_sum_not_one(self):
        """레짐 가중치 합 검증"""
        config = {"regime_weights": {"TRENDING_UP": {"TF": 0.50, "MR": 0.30, "RP": 0.15}}}  # sum = 0.95
        errors = validate_ensemble_config(config)
        assert any("TRENDING_UP" in e and "sum" in e for e in errors)

    def test_validate_regime_weights_missing_key(self):
        """레짐 가중치 키 검증"""
        config = {"regime_weights": {"TRENDING_UP": {"TF": 0.55, "MR": 0.15}}}  # RP 누락
        errors = validate_ensemble_config(config)
        assert any("TRENDING_UP" in e for e in errors)

    def test_validate_invalid_regime_name(self):
        """유효하지 않은 레짐 이름"""
        config = {"regime_weights": {"INVALID_REGIME": {"TF": 0.5, "MR": 0.3, "RP": 0.2}}}
        errors = validate_ensemble_config(config)
        assert any("Invalid regime" in e for e in errors)

    def test_validate_risk_stop_loss_atr_out_of_range(self):
        """손절 ATR 배수 범위 검증"""
        config = {"risk": {"stop_loss_atr_multiplier": 15.0}}
        errors = validate_ensemble_config(config)
        assert any("stop_loss_atr_multiplier" in e for e in errors)

    def test_validate_risk_max_drawdown_limit_out_of_range(self):
        """최대 드로우다운 한도 범위 검증"""
        config = {"risk": {"max_drawdown_limit": 0.02}}
        errors = validate_ensemble_config(config)
        assert any("max_drawdown_limit" in e for e in errors)

    def test_validate_drawdown_cooldown_days_invalid(self):
        """드로우다운 쿨다운 검증"""
        config = {"risk": {"drawdown_cooldown_days": 0}}
        errors = validate_ensemble_config(config)
        assert any("drawdown_cooldown_days" in e for e in errors)


class TestSaveAndReloadConfig:
    """save_ensemble_config() 및 재로드 일관성"""

    def test_save_and_reload_consistency(self):
        """저장 후 재로드 일관성"""
        config = {
            "ensemble": {"adx_threshold": 27, "perf_blend": 0.35},
            "regime_weights": {"TRENDING_UP": {"TF": 0.52, "MR": 0.16, "RP": 0.32}},
            "risk": {"max_drawdown_limit": 0.22},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "test_config.yaml"

            # 저장
            save_ensemble_config(config, config_path=config_path)
            assert config_path.exists()

            # 재로드
            with patch("config.ensemble_config_loader._ENSEMBLE_CONFIG_PATH", config_path):
                reloaded = load_ensemble_config()

                assert reloaded["ensemble"]["adx_threshold"] == 27
                assert reloaded["ensemble"]["perf_blend"] == 0.35
                assert reloaded["regime_weights"]["TRENDING_UP"]["TF"] == 0.52
                assert reloaded["risk"]["max_drawdown_limit"] == 0.22

    def test_save_invalid_config_raises_error(self):
        """유효하지 않은 설정 저장 시 에러"""
        invalid_config = {"regime_weights": {"TRENDING_UP": {"TF": 0.80, "MR": 0.30, "RP": 0.0}}}  # sum > 1.0

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "invalid.yaml"

            with pytest.raises(ValueError):
                save_ensemble_config(invalid_config, config_path=config_path)

    def test_save_config_with_metadata(self):
        """메타데이터와 함께 저장"""
        config = {
            "ensemble": {"adx_threshold": 26},
            "regime_weights": {},
            "risk": {},
        }
        metadata = {
            "last_optimized": "2026-04-06",
            "oos_sharpe_baseline": 0.95,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "with_metadata.yaml"
            save_ensemble_config(config, metadata=metadata, config_path=config_path)

            content = config_path.read_text()
            assert "2026-04-06" in content
            assert "0.95" in content


class TestApplyHyperoptResults:
    """apply_hyperopt_results() 테스트"""

    def test_apply_hyperopt_results(self):
        """Hyperopt JSON 결과 적용"""
        hyperopt_result = {
            "best_params": {
                "adx_threshold": 28.5,
                "vol_pct_threshold": 0.78,
                "perf_window": 65,
                "softmax_temperature": 5.5,
                "perf_blend": 0.32,
                "target_vol": 0.26,
                "w_trending_up_tf": 0.52,
                "w_trending_up_mr": 0.16,
                "w_trending_down_tf": 0.42,
                "w_trending_down_mr": 0.14,
                "w_high_vol_tf": 0.22,
                "w_high_vol_mr": 0.18,
                "w_sideways_tf": 0.27,
                "w_sideways_mr": 0.43,
                "stop_loss_atr_multiplier": 2.1,
                "trailing_stop_atr_multiplier": 3.2,
                "max_drawdown_limit": 0.21,
                "drawdown_cooldown_days": 22,
                "dd_cushion_start": 0.09,
                "dd_cushion_floor": 0.26,
            },
            "best_value": 0.98,
            "best_trial": 42,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            # Hyperopt JSON 파일 생성
            json_path = Path(tmpdir) / "hyperopt_result.json"
            json_path.write_text(json.dumps(hyperopt_result))

            # 결과 적용
            config_path = Path(tmpdir) / "applied.yaml"
            with patch("config.ensemble_config_loader._ENSEMBLE_CONFIG_PATH", config_path):
                config = apply_hyperopt_results(str(json_path), config_path=config_path)

            # 앙상블 파라미터 검증
            assert config["ensemble"]["adx_threshold"] == 28.5
            assert config["ensemble"]["vol_pct_threshold"] == 0.78
            assert config["ensemble"]["perf_window"] == 65

            # 레짐 가중치 검증
            assert config["regime_weights"]["TRENDING_UP"]["TF"] == 0.52
            assert config["regime_weights"]["TRENDING_UP"]["MR"] == 0.16
            assert config["regime_weights"]["TRENDING_UP"]["RP"] == 0.32  # 1.0 - 0.52 - 0.16

            # 리스크 파라미터 검증
            assert config["risk"]["stop_loss_atr_multiplier"] == 2.1

    def test_apply_hyperopt_results_file_not_found(self):
        """Hyperopt 파일 미존재"""
        with pytest.raises(FileNotFoundError):
            apply_hyperopt_results("/nonexistent/path.json")

    def test_apply_hyperopt_results_invalid_json(self):
        """잘못된 JSON 형식"""
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "invalid.json"
            json_path.write_text("not valid json {")

            with pytest.raises(ValueError):
                apply_hyperopt_results(str(json_path))

    def test_apply_hyperopt_results_no_best_params(self):
        """best_params 없음"""
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "empty.json"
            json_path.write_text(json.dumps({"best_value": 0.5}))

            with pytest.raises(ValueError):
                apply_hyperopt_results(str(json_path))


class TestDynamicEnsembleIntegration:
    """DynamicEnsembleService와의 통합 테스트"""

    def test_dynamic_ensemble_uses_yaml_config(self):
        """DynamicEnsembleService가 YAML 설정을 사용"""
        config = {
            "ensemble": {"adx_threshold": 28, "target_vol": 0.28},
            "regime_weights": {"TRENDING_UP": {"TF": 0.52, "MR": 0.16, "RP": 0.32}},
            "risk": {},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "test.yaml"
            save_ensemble_config(config, config_path=config_path)

            with patch("config.ensemble_config_loader._ENSEMBLE_CONFIG_PATH", config_path):
                service = DynamicEnsembleService()

                # YAML 값이 적용됨
                assert service._params["adx_threshold"] == 28
                assert service._params["target_vol"] == 0.28
                assert service._regime_weights[DynamicRegime.TRENDING_UP]["TF"] == 0.52

    def test_dynamic_ensemble_params_override_yaml(self):
        """명시적 파라미터가 YAML을 오버라이드"""
        yaml_config = {
            "ensemble": {"adx_threshold": 28},
            "regime_weights": {"TRENDING_UP": {"TF": 0.52, "MR": 0.16, "RP": 0.32}},
            "risk": {},
        }

        with patch("config.ensemble_config_loader.load_ensemble_config") as mock_load:
            mock_load.return_value = yaml_config

            # 파라미터로 오버라이드
            service = DynamicEnsembleService(params={"adx_threshold": 30})

            # 파라미터 값이 우선
            assert service._params["adx_threshold"] == 30

    def test_dynamic_ensemble_regime_weights_from_yaml(self):
        """DynamicEnsembleService의 레짐 가중치가 YAML에서 로드됨"""
        import numpy as np
        import pandas as pd

        yaml_config = {
            "ensemble": {"adx_threshold": 25},
            "regime_weights": {
                "TRENDING_UP": {"TF": 0.53, "MR": 0.17, "RP": 0.30},
                "TRENDING_DOWN": {"TF": 0.40, "MR": 0.15, "RP": 0.45},
                "HIGH_VOLATILITY": {"TF": 0.20, "MR": 0.20, "RP": 0.60},
                "SIDEWAYS": {"TF": 0.25, "MR": 0.45, "RP": 0.30},
            },
            "risk": {},
        }

        with patch("config.ensemble_config_loader.load_ensemble_config") as mock_load:
            mock_load.return_value = yaml_config

            service = DynamicEnsembleService()

            # 레짐 가중치 검증
            trending_up_weights = service._regime_weights[DynamicRegime.TRENDING_UP]
            assert trending_up_weights["TF"] == 0.53
            assert trending_up_weights["MR"] == 0.17

            # compute() 호출 시에도 사용되는지 확인
            n = 100
            dates = pd.date_range("2023-01-01", periods=n, freq="B")
            np.random.seed(42)

            ohlcv = pd.DataFrame(
                {
                    "open": 100.0 + np.cumsum(np.random.randn(n) * 0.5),
                    "high": 101.0 + np.cumsum(np.random.randn(n) * 0.5),
                    "low": 99.0 + np.cumsum(np.random.randn(n) * 0.5),
                    "close": 100.0 + np.cumsum(np.random.randn(n) * 0.5),
                    "volume": np.full(n, 1000000.0),
                },
                index=dates,
            )
            mr = pd.Series(np.random.uniform(-0.5, 0.5, n), index=dates)
            tf = pd.Series(np.random.uniform(-0.5, 0.5, n), index=dates)
            rp = pd.Series(np.random.uniform(-0.5, 0.5, n), index=dates)

            result = service.compute(ohlcv, mr, tf, rp)

            # 결과가 유효함
            assert isinstance(result.ensemble_signal, float)
            assert abs(sum(result.weights.values()) - 1.0) < 1e-6
