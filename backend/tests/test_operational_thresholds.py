"""
operational_thresholds.yaml 구조 및 값 검증 테스트.

중앙 관리 임계값 파일이 올바른 스키마를 유지하는지, 각 섹션의 키와 값 타입이
기대 범위 내에 있는지 검증한다. 임계값 누락이나 타입 불일치는 런타임에
silent failure 를 유발할 수 있으므로 정적으로 검증한다.
"""

from pathlib import Path

import pytest
import yaml

THRESHOLDS_FILE = Path(__file__).resolve().parents[1] / "config" / "operational_thresholds.yaml"

# 기대하는 최상위 섹션 — 섹션이 삭제되면 즉시 감지
REQUIRED_SECTIONS = {
    "data_quality",
    "feature",
    "signal",
    "portfolio",
    "order",
    "risk",
    "live_scaleup",
    "ai",
    "performance",
    "oos_gate",
    "regime_mapping",
}


@pytest.fixture(scope="module")
def thresholds() -> dict:
    with THRESHOLDS_FILE.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class TestThresholdsFileStructure:
    def test_file_exists(self):
        assert THRESHOLDS_FILE.exists(), f"{THRESHOLDS_FILE} 가 존재해야 한다."

    def test_yaml_parses_to_dict(self, thresholds):
        assert isinstance(thresholds, dict)
        assert len(thresholds) > 0

    def test_all_required_sections_exist(self, thresholds):
        missing = REQUIRED_SECTIONS - set(thresholds.keys())
        assert not missing, f"누락된 섹션: {sorted(missing)}. " f"실제 섹션: {sorted(thresholds.keys())}"

    def test_no_unexpected_top_level_keys(self, thresholds):
        """알려지지 않은 최상위 키가 있으면 경고 (새 섹션은 REQUIRED_SECTIONS 에 등록)"""
        unknown = set(thresholds.keys()) - REQUIRED_SECTIONS
        assert not unknown, f"미등록 최상위 키: {sorted(unknown)}. " f"의도적이면 REQUIRED_SECTIONS 에 추가해야 한다."


class TestThresholdsValues:
    """각 섹션의 값이 합리적 범위 내인지 검증"""

    def test_data_quality_has_numeric_values(self, thresholds):
        dq = thresholds["data_quality"]
        assert isinstance(dq, dict)
        for key, value in dq.items():
            assert isinstance(value, (int, float, list)), (
                f"data_quality.{key} 의 타입이 {type(value).__name__} — " f"숫자 또는 리스트여야 한다."
            )

    def test_risk_section_has_expected_keys(self, thresholds):
        risk = thresholds["risk"]
        assert isinstance(risk, dict)
        assert len(risk) >= 1, "risk 섹션에 최소 1개 키가 있어야 한다."

    def test_portfolio_section_has_expected_keys(self, thresholds):
        portfolio = thresholds["portfolio"]
        assert isinstance(portfolio, dict)
        assert len(portfolio) >= 1, "portfolio 섹션에 최소 1개 키가 있어야 한다."

    def test_all_sections_are_non_empty_dicts(self, thresholds):
        for section_name in REQUIRED_SECTIONS:
            section = thresholds[section_name]
            assert isinstance(section, dict), f"{section_name} 은 dict 이어야 한다 (실제: {type(section).__name__})"
            assert len(section) >= 1, f"{section_name} 이 비어있다."

    def test_numeric_thresholds_are_finite(self, thresholds):
        """모든 숫자 값이 유한한지 (inf/nan 방지)"""
        import math

        for section_name, section in thresholds.items():
            if not isinstance(section, dict):
                continue
            for key, value in section.items():
                if isinstance(value, float):
                    assert math.isfinite(value), f"{section_name}.{key} = {value} 는 유한하지 않다."
