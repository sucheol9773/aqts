"""
레짐 매핑 계층 (Regime Mapping Layer)

두 분류 체계를 강제 통일하지 않고 매핑 테이블로 연결:

1. MarketRegimeDetector (실시간):
   TRENDING_UP, TRENDING_DOWN, SIDEWAYS, HIGH_VOLATILITY

2. RegimeAnalyzer (백테스트 사후분석):
   BULL, BEAR, HIGH_VOL, RISING_RATE

매핑은 operational_thresholds.yaml의 regime_mapping 섹션에서 로드.
unmapped 발생 시 fallback 정책을 적용하고 경고 로그를 남깁니다.
"""

from pathlib import Path
from typing import Optional

import yaml

from config.logging import logger


class RegimeMapper:
    """
    레짐 분류 체계 간 매핑

    config YAML에서 매핑을 로드하며,
    매핑 누락 시 fallback 값을 반환합니다.
    """

    # 하드코딩 기본값 (YAML 로드 실패 시 사용)
    DEFAULT_MAPPING = {
        # 실시간 → 백테스트
        "TRENDING_UP": "BULL",
        "TRENDING_DOWN": "BEAR",
        "SIDEWAYS": "BULL",
        "HIGH_VOLATILITY": "HIGH_VOL",
        # 백테스트 → 실시간
        "BULL": "TRENDING_UP",
        "BEAR": "TRENDING_DOWN",
        "HIGH_VOL": "HIGH_VOLATILITY",
        "RISING_RATE": "SIDEWAYS",
    }

    DEFAULT_FALLBACK = "SIDEWAYS"

    # 실시간 체계 유효값
    REALTIME_REGIMES = {"TRENDING_UP", "TRENDING_DOWN", "SIDEWAYS", "HIGH_VOLATILITY"}
    # 백테스트 체계 유효값
    BACKTEST_REGIMES = {"BULL", "BEAR", "HIGH_VOL", "RISING_RATE"}

    def __init__(self):
        self._mapping: dict[str, str] = {}
        self._fallback: str = self.DEFAULT_FALLBACK
        self._load_config()

    def _load_config(self) -> None:
        """operational_thresholds.yaml에서 매핑 로드"""
        config_path = (
            Path(__file__).parent.parent.parent / "config" / "operational_thresholds.yaml"
        )

        if config_path.exists():
            try:
                with open(config_path) as f:
                    config = yaml.safe_load(f)
                    if config and "regime_mapping" in config:
                        mapping_config = config["regime_mapping"]
                        self._fallback = mapping_config.pop("fallback", self.DEFAULT_FALLBACK)
                        self._mapping = {
                            str(k): str(v) for k, v in mapping_config.items()
                        }
                        logger.debug(
                            f"RegimeMapper: loaded {len(self._mapping)} mappings, "
                            f"fallback={self._fallback}"
                        )
                        return
            except Exception as e:
                logger.warning(f"RegimeMapper config load failed: {e}")

        self._mapping = self.DEFAULT_MAPPING.copy()
        logger.debug("RegimeMapper: using default mapping")

    def to_backtest(self, realtime_regime: str) -> str:
        """
        실시간 레짐 → 백테스트 레짐으로 변환

        Args:
            realtime_regime: TRENDING_UP, TRENDING_DOWN, SIDEWAYS, HIGH_VOLATILITY

        Returns:
            BULL, BEAR, HIGH_VOL, RISING_RATE 중 하나
        """
        mapped = self._mapping.get(realtime_regime)

        if mapped is None:
            logger.warning(
                f"RegimeMapper: unmapped realtime regime '{realtime_regime}', "
                f"using fallback '{self._fallback}'"
            )
            return self._fallback

        if mapped not in self.BACKTEST_REGIMES:
            logger.warning(
                f"RegimeMapper: mapped value '{mapped}' not in backtest regimes, "
                f"using fallback '{self._fallback}'"
            )
            return self._fallback

        return mapped

    def to_realtime(self, backtest_regime: str) -> str:
        """
        백테스트 레짐 → 실시간 레짐으로 변환

        Args:
            backtest_regime: BULL, BEAR, HIGH_VOL, RISING_RATE

        Returns:
            TRENDING_UP, TRENDING_DOWN, SIDEWAYS, HIGH_VOLATILITY 중 하나
        """
        mapped = self._mapping.get(backtest_regime)

        if mapped is None:
            logger.warning(
                f"RegimeMapper: unmapped backtest regime '{backtest_regime}', "
                f"using fallback '{self._fallback}'"
            )
            return self._fallback

        if mapped not in self.REALTIME_REGIMES:
            logger.warning(
                f"RegimeMapper: mapped value '{mapped}' not in realtime regimes, "
                f"using fallback '{self._fallback}'"
            )
            return self._fallback

        return mapped

    def get_all_mappings(self) -> dict[str, str]:
        """전체 매핑 테이블 반환 (리포트/감사용)"""
        return self._mapping.copy()

    def get_fallback(self) -> str:
        """현재 fallback 값 반환"""
        return self._fallback

    def validate_regime(self, regime: str) -> tuple[bool, str]:
        """
        레짐 값이 어느 체계에 속하는지 확인

        Returns:
            (is_valid, system): ("realtime" | "backtest" | "unknown")
        """
        if regime in self.REALTIME_REGIMES:
            return True, "realtime"
        if regime in self.BACKTEST_REGIMES:
            return True, "backtest"
        return False, "unknown"
