"""
투자 프로필 관리 모듈 테스트 (test_profile.py)

InvestorProfile과 InvestorProfileManager의 기능을 포괄적으로 검증합니다.
모든 외부 의존성(DB, async_session_factory)은 Mock으로 대체합니다.

테스트 범위:
- InvestorProfile: 데이터 구조, 직렬화/역직렬화
- InvestorProfileManager: 비동기 CRUD 작업, 전략 파라미터 매핑
"""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config.constants import (
    InvestmentStyle,
    RebalancingFrequency,
    RiskProfile,
    StrategyType,
)
from core.portfolio_manager.profile import (
    InvestorProfile,
    InvestorProfileManager,
)


# ══════════════════════════════════════
# InvestorProfile 테스트
# ══════════════════════════════════════
class TestInvestorProfile:
    """투자 프로필 데이터 구조 테스트"""

    def test_create_profile_dataclass(self):
        """기본 투자 프로필 인스턴스 생성 테스트

        프로필의 모든 필드가 올바르게 초기화되는지 검증합니다.
        """
        # Given: 프로필 생성 파라미터
        user_id = "user_001"
        risk_profile = RiskProfile.BALANCED
        seed_capital = 50_000_000.0
        investment_purpose = "자산 증식"
        investment_style = InvestmentStyle.DISCRETIONARY
        loss_tolerance = 0.10
        sector_filters = ["IT", "Healthcare"]
        designated_tickers = ["005930", "000660"]
        rebalancing_frequency = RebalancingFrequency.MONTHLY

        # When: 프로필 생성
        profile = InvestorProfile(
            user_id=user_id,
            risk_profile=risk_profile,
            seed_capital=seed_capital,
            investment_purpose=investment_purpose,
            investment_style=investment_style,
            loss_tolerance=loss_tolerance,
            sector_filters=sector_filters,
            designated_tickers=designated_tickers,
            rebalancing_frequency=rebalancing_frequency,
        )

        # Then: 모든 필드 검증
        assert profile.user_id == user_id
        assert profile.risk_profile == risk_profile
        assert profile.seed_capital == seed_capital
        assert profile.investment_purpose == investment_purpose
        assert profile.investment_style == investment_style
        assert profile.loss_tolerance == loss_tolerance
        assert profile.sector_filters == sector_filters
        assert profile.designated_tickers == designated_tickers
        assert profile.rebalancing_frequency == rebalancing_frequency
        assert profile.created_at is not None
        assert profile.updated_at is not None

    def test_create_profile_with_defaults(self):
        """기본값으로 프로필 생성 테스트

        선택적 필드들이 기본값으로 초기화되는지 검증합니다.
        """
        # Given: 필수 필드만 지정
        profile = InvestorProfile(
            user_id="user_002",
            risk_profile=RiskProfile.AGGRESSIVE,
            seed_capital=30_000_000.0,
            investment_purpose="수익 창출",
            investment_style=InvestmentStyle.ADVISORY,
            loss_tolerance=0.15,
        )

        # Then: 기본값 검증
        assert profile.sector_filters == []
        assert profile.designated_tickers == []
        assert profile.rebalancing_frequency == RebalancingFrequency.MONTHLY

    def test_to_dict(self):
        """딕셔너리 변환 테스트

        to_dict() 메서드가 enum 및 리스트를 올바르게 변환하는지 검증합니다.
        """
        # Given: 프로필 인스턴스
        profile = InvestorProfile(
            user_id="user_003",
            risk_profile=RiskProfile.CONSERVATIVE,
            seed_capital=100_000_000.0,
            investment_purpose="은퇴 자금",
            investment_style=InvestmentStyle.DISCRETIONARY,
            loss_tolerance=0.05,
            sector_filters=["Energy", "Utilities"],
            designated_tickers=["010950", "011200"],
            rebalancing_frequency=RebalancingFrequency.QUARTERLY,
        )

        # When: 딕셔너리 변환
        result = profile.to_dict()

        # Then: 변환 결과 검증
        assert result["user_id"] == "user_003"
        assert result["risk_profile"] == "CONSERVATIVE"  # enum.value
        assert result["seed_capital"] == 100_000_000.0
        assert result["investment_purpose"] == "은퇴 자금"
        assert result["investment_style"] == "DISCRETIONARY"  # enum.value
        assert result["loss_tolerance"] == 0.05
        # 리스트는 JSON 문자열로 변환
        assert result["sector_filters"] == json.dumps(["Energy", "Utilities"])
        assert result["designated_tickers"] == json.dumps(["010950", "011200"])
        assert result["rebalancing_frequency"] == "QUARTERLY"  # enum.value
        assert isinstance(result["created_at"], datetime)
        assert isinstance(result["updated_at"], datetime)

    def test_to_dict_empty_lists(self):
        """빈 리스트 직렬화 테스트

        빈 리스트가 올바르게 JSON으로 변환되는지 검증합니다.
        """
        # Given: 빈 리스트를 가진 프로필
        profile = InvestorProfile(
            user_id="user_004",
            risk_profile=RiskProfile.BALANCED,
            seed_capital=50_000_000.0,
            investment_purpose="테스트",
            investment_style=InvestmentStyle.ADVISORY,
            loss_tolerance=0.10,
        )

        # When: 딕셔너리 변환
        result = profile.to_dict()

        # Then: 빈 JSON 배열 검증
        assert result["sector_filters"] == "[]"
        assert result["designated_tickers"] == "[]"

    def test_from_dict(self):
        """딕셔너리에서 프로필 생성 테스트

        from_dict() 메서드가 enum 및 JSON 문자열을 올바르게 복원하는지 검증합니다.
        """
        # Given: 직렬화된 프로필 데이터
        now = datetime.now(timezone.utc)
        data = {
            "user_id": "user_005",
            "risk_profile": "BALANCED",
            "seed_capital": 50_000_000.0,
            "investment_purpose": "자산 증식",
            "investment_style": "DISCRETIONARY",
            "loss_tolerance": 0.10,
            "sector_filters": json.dumps(["IT", "Finance"]),
            "designated_tickers": json.dumps(["005930", "207940"]),
            "rebalancing_frequency": "BIMONTHLY",
            "created_at": now,
            "updated_at": now,
        }

        # When: 프로필 복원
        profile = InvestorProfile.from_dict(data)

        # Then: 역직렬화 결과 검증
        assert profile.user_id == "user_005"
        assert profile.risk_profile == RiskProfile.BALANCED
        assert profile.seed_capital == 50_000_000.0
        assert profile.investment_purpose == "자산 증식"
        assert profile.investment_style == InvestmentStyle.DISCRETIONARY
        assert profile.loss_tolerance == 0.10
        assert profile.sector_filters == ["IT", "Finance"]
        assert profile.designated_tickers == ["005930", "207940"]
        assert profile.rebalancing_frequency == RebalancingFrequency.BIMONTHLY
        assert profile.created_at == now
        assert profile.updated_at == now

    def test_from_dict_with_defaults(self):
        """기본값을 포함한 from_dict 테스트

        선택적 필드가 누락된 경우 기본값으로 초기화되는지 검증합니다.
        """
        # Given: 필수 필드만 포함한 데이터
        data = {
            "user_id": "user_006",
            "risk_profile": "AGGRESSIVE",
            "seed_capital": 30_000_000.0,
            "investment_purpose": "단기 수익",
            "investment_style": "ADVISORY",
            "loss_tolerance": 0.15,
        }

        # When: 프로필 복원
        profile = InvestorProfile.from_dict(data)

        # Then: 기본값 검증
        assert profile.sector_filters == []
        assert profile.designated_tickers == []
        assert profile.rebalancing_frequency == RebalancingFrequency.MONTHLY

    def test_from_dict_roundtrip(self):
        """왕복 테스트 (to_dict → from_dict)

        직렬화 후 역직렬화했을 때 원본과 동일한지 검증합니다.
        """
        # Given: 원본 프로필
        original = InvestorProfile(
            user_id="user_007",
            risk_profile=RiskProfile.DIVIDEND,
            seed_capital=100_000_000.0,
            investment_purpose="배당 수입",
            investment_style=InvestmentStyle.DISCRETIONARY,
            loss_tolerance=0.08,
            sector_filters=["Utilities", "Finance", "Real Estate"],
            designated_tickers=["010950", "011170", "000270"],
            rebalancing_frequency=RebalancingFrequency.QUARTERLY,
        )

        # When: 직렬화 및 역직렬화
        serialized = original.to_dict()
        restored = InvestorProfile.from_dict(serialized)

        # Then: 모든 필드 비교
        assert restored.user_id == original.user_id
        assert restored.risk_profile == original.risk_profile
        assert restored.seed_capital == original.seed_capital
        assert restored.investment_purpose == original.investment_purpose
        assert restored.investment_style == original.investment_style
        assert restored.loss_tolerance == original.loss_tolerance
        assert restored.sector_filters == original.sector_filters
        assert restored.designated_tickers == original.designated_tickers
        assert restored.rebalancing_frequency == original.rebalancing_frequency


# ══════════════════════════════════════
# InvestorProfileManager 테스트
# ══════════════════════════════════════
class TestInvestorProfileManager:
    """투자 프로필 관리 엔진 테스트"""

    @pytest.fixture
    def manager(self):
        """프로필 매니저 인스턴스"""
        return InvestorProfileManager()

    @pytest.fixture
    def mock_session(self):
        """비동기 DB 세션 Mock"""
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        return mock_session

    @pytest.mark.asyncio
    async def test_create_profile(self, manager, mock_session):
        """프로필 생성 테스트

        create_profile 메서드가 새 프로필을 생성하고 저장하는지 검증합니다.
        """
        # Given: 프로필 생성 파라미터
        with patch(
            "core.portfolio_manager.profile.async_session_factory",
            return_value=mock_session,
        ):
            user_id = "user_create_001"
            risk_profile = RiskProfile.BALANCED
            seed_capital = 50_000_000.0
            investment_purpose = "자산 증식"
            investment_style = InvestmentStyle.DISCRETIONARY
            loss_tolerance = 0.10

            # When: 프로필 생성
            result = await manager.create_profile(
                user_id=user_id,
                risk_profile=risk_profile,
                seed_capital=seed_capital,
                investment_purpose=investment_purpose,
                investment_style=investment_style,
                loss_tolerance=loss_tolerance,
            )

            # Then: 반환된 프로필 검증
            assert result.user_id == user_id
            assert result.risk_profile == risk_profile
            assert result.seed_capital == seed_capital
            assert result.investment_purpose == investment_purpose
            assert result.investment_style == investment_style
            assert result.loss_tolerance == loss_tolerance
            # DB 저장 메서드 호출 검증
            mock_session.execute.assert_called_once()
            mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_profile_with_optional_fields(self, manager, mock_session):
        """선택적 필드를 포함한 프로필 생성 테스트

        sector_filters, designated_tickers, rebalancing_frequency를
        포함하여 프로필을 생성하는지 검증합니다.
        """
        # Given: 모든 필드를 지정
        with patch(
            "core.portfolio_manager.profile.async_session_factory",
            return_value=mock_session,
        ):
            # When: 프로필 생성
            result = await manager.create_profile(
                user_id="user_create_002",
                risk_profile=RiskProfile.AGGRESSIVE,
                seed_capital=30_000_000.0,
                investment_purpose="수익 창출",
                investment_style=InvestmentStyle.ADVISORY,
                loss_tolerance=0.15,
                sector_filters=["IT", "Healthcare"],
                designated_tickers=["005930", "000660"],
                rebalancing_frequency=RebalancingFrequency.BIMONTHLY,
            )

            # Then: 선택적 필드 검증
            assert result.sector_filters == ["IT", "Healthcare"]
            assert result.designated_tickers == ["005930", "000660"]
            assert result.rebalancing_frequency == RebalancingFrequency.BIMONTHLY

    @pytest.mark.asyncio
    async def test_get_profile_found(self, manager, mock_session):
        """기존 프로필 조회 테스트

        DB에서 프로필을 조회하여 반환하는지 검증합니다.
        """
        # Given: DB에서 반환할 행 데이터
        now = datetime.now(timezone.utc)
        result_row = (
            "user_get_001",  # user_id
            "BALANCED",  # risk_profile
            50_000_000.0,  # seed_capital
            "자산 증식",  # investment_purpose
            "DISCRETIONARY",  # investment_style
            0.10,  # loss_tolerance
            json.dumps(["IT", "Finance"]),  # sector_filters
            json.dumps(["005930"]),  # designated_tickers
            "MONTHLY",  # rebalancing_frequency
            now,  # created_at
            now,  # updated_at
        )
        # execute()는 async이고, fetchone()은 sync
        mock_result = MagicMock()
        mock_result.fetchone = MagicMock(return_value=result_row)
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch(
            "core.portfolio_manager.profile.async_session_factory",
            return_value=mock_session,
        ):
            # When: 프로필 조회
            result = await manager.get_profile("user_get_001")

            # Then: 조회된 프로필 검증
            assert result is not None
            assert result.user_id == "user_get_001"
            assert result.risk_profile == RiskProfile.BALANCED
            assert result.seed_capital == 50_000_000.0
            assert result.investment_purpose == "자산 증식"
            assert result.sector_filters == ["IT", "Finance"]
            assert result.designated_tickers == ["005930"]
            mock_session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_profile_not_found(self, manager, mock_session):
        """프로필 미존재 조회 테스트

        DB에서 프로필을 찾지 못한 경우 None을 반환하는지 검증합니다.
        """
        # Given: DB에서 None 반환
        mock_result = MagicMock()
        mock_result.fetchone = MagicMock(return_value=None)
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch(
            "core.portfolio_manager.profile.async_session_factory",
            return_value=mock_session,
        ):
            # When: 프로필 조회
            result = await manager.get_profile("nonexistent_user")

            # Then: None 반환 검증
            assert result is None

    @pytest.mark.asyncio
    async def test_update_profile(self, manager, mock_session):
        """프로필 갱신 테스트

        기존 프로필을 조회하여 필드를 갱신하고 저장하는지 검증합니다.
        """
        # Given: 기존 프로필 데이터 (get_profile에서 반환)
        now = datetime.now(timezone.utc)
        result_row = (
            "user_update_001",
            "BALANCED",
            50_000_000.0,
            "자산 증식",
            "DISCRETIONARY",
            0.10,
            json.dumps([]),
            json.dumps([]),
            "MONTHLY",
            now,
            now,
        )
        mock_result = MagicMock()
        mock_result.fetchone = MagicMock(return_value=result_row)
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch(
            "core.portfolio_manager.profile.async_session_factory",
            return_value=mock_session,
        ):
            # When: 프로필 갱신
            result = await manager.update_profile(
                user_id="user_update_001",
                risk_profile=RiskProfile.AGGRESSIVE,
                loss_tolerance=0.15,
            )

            # Then: 갱신된 필드 검증
            assert result.risk_profile == RiskProfile.AGGRESSIVE
            assert result.loss_tolerance == 0.15
            # 원래 필드는 유지
            assert result.seed_capital == 50_000_000.0
            # updated_at이 갱신됨
            assert result.updated_at > now
            # execute 2번 호출: get_profile 1회 + update 1회
            assert mock_session.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_update_profile_not_found(self, manager, mock_session):
        """프로필 미존재 갱신 테스트

        존재하지 않는 프로필을 갱신하려 할 때 ValueError를 발생하는지 검증합니다.
        """
        # Given: DB에서 None 반환 (프로필 미존재)
        mock_result = MagicMock()
        mock_result.fetchone = MagicMock(return_value=None)
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch(
            "core.portfolio_manager.profile.async_session_factory",
            return_value=mock_session,
        ):
            # When & Then: ValueError 발생
            with pytest.raises(ValueError, match="Profile not found"):
                await manager.update_profile(
                    user_id="nonexistent_user",
                    risk_profile=RiskProfile.CONSERVATIVE,
                )

    @pytest.mark.asyncio
    async def test_update_profile_multiple_fields(self, manager, mock_session):
        """여러 필드 갱신 테스트

        한 번에 여러 필드를 갱신하는지 검증합니다.
        """
        # Given: 기존 프로필
        now = datetime.now(timezone.utc)
        result_row = (
            "user_update_002",
            "BALANCED",
            50_000_000.0,
            "자산 증식",
            "DISCRETIONARY",
            0.10,
            json.dumps(["IT"]),
            json.dumps(["005930"]),
            "MONTHLY",
            now,
            now,
        )
        mock_result = MagicMock()
        mock_result.fetchone = MagicMock(return_value=result_row)
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch(
            "core.portfolio_manager.profile.async_session_factory",
            return_value=mock_session,
        ):
            # When: 여러 필드 갱신
            result = await manager.update_profile(
                user_id="user_update_002",
                seed_capital=100_000_000.0,
                loss_tolerance=0.20,
                sector_filters=["Energy", "Finance"],
                rebalancing_frequency=RebalancingFrequency.QUARTERLY,
            )

            # Then: 모든 갱신 필드 검증
            assert result.seed_capital == 100_000_000.0
            assert result.loss_tolerance == 0.20
            assert result.sector_filters == ["Energy", "Finance"]
            assert result.rebalancing_frequency == RebalancingFrequency.QUARTERLY

    def test_apply_profile_to_strategy_balanced(self, manager):
        """BALANCED 프로필 전략 파라미터 매핑 테스트

        균형형 프로필이 올바른 전략 파라미터로 변환되는지 검증합니다.
        """
        # Given: BALANCED 프로필
        profile = InvestorProfile(
            user_id="user_strategy_001",
            risk_profile=RiskProfile.BALANCED,
            seed_capital=50_000_000.0,
            investment_purpose="자산 증식",
            investment_style=InvestmentStyle.DISCRETIONARY,
            loss_tolerance=0.10,
            rebalancing_frequency=RebalancingFrequency.MONTHLY,
        )

        # When: 전략 파라미터 매핑
        result = manager._apply_profile_to_strategy(profile)

        # Then: BALANCED에 해당하는 파라미터 검증
        assert result["trading_frequency"] == "스윙"  # HOLDING_PERIOD_MAP 확인
        assert result["min_holding_days"] == 3
        assert result["max_holding_days"] == 21
        assert result["rebalancing_frequency"] == "MONTHLY"
        assert result["loss_tolerance"] == 0.10
        assert result["investment_style"] == "DISCRETIONARY"
        # 가중치 검증 (StrategyType 기반)
        assert "ensemble_weights" in result
        weights = result["ensemble_weights"]
        assert weights[StrategyType.FACTOR.value] == 0.25
        assert weights[StrategyType.TREND_FOLLOWING.value] == 0.20
        assert weights["SENTIMENT"] == 0.25

    def test_apply_profile_to_strategy_conservative(self, manager):
        """CONSERVATIVE 프로필 전략 파라미터 매핑 테스트

        보수형 프로필이 올바른 전략 파라미터로 변환되는지 검증합니다.
        """
        # Given: CONSERVATIVE 프로필
        profile = InvestorProfile(
            user_id="user_strategy_002",
            risk_profile=RiskProfile.CONSERVATIVE,
            seed_capital=100_000_000.0,
            investment_purpose="은퇴 자금",
            investment_style=InvestmentStyle.ADVISORY,
            loss_tolerance=0.05,
            rebalancing_frequency=RebalancingFrequency.QUARTERLY,
        )

        # When: 전략 파라미터 매핑
        result = manager._apply_profile_to_strategy(profile)

        # Then: CONSERVATIVE에 해당하는 파라미터 검증
        assert result["trading_frequency"] == "포지션"
        assert result["min_holding_days"] == 14
        assert result["max_holding_days"] == 180
        assert result["rebalancing_frequency"] == "QUARTERLY"
        assert result["loss_tolerance"] == 0.05
        assert result["investment_style"] == "ADVISORY"
        # 가중치: RISK_PARITY 높음, SENTIMENT 20%
        weights = result["ensemble_weights"]
        assert weights[StrategyType.RISK_PARITY.value] == 0.30
        assert weights["SENTIMENT"] == 0.20

    def test_apply_profile_to_strategy_aggressive(self, manager):
        """AGGRESSIVE 프로필 전략 파라미터 매핑 테스트

        공격형 프로필이 올바른 전략 파라미터로 변환되는지 검증합니다.
        """
        # Given: AGGRESSIVE 프로필
        profile = InvestorProfile(
            user_id="user_strategy_003",
            risk_profile=RiskProfile.AGGRESSIVE,
            seed_capital=30_000_000.0,
            investment_purpose="수익 창출",
            investment_style=InvestmentStyle.DISCRETIONARY,
            loss_tolerance=0.15,
            rebalancing_frequency=RebalancingFrequency.MONTHLY,
        )

        # When: 전략 파라미터 매핑
        result = manager._apply_profile_to_strategy(profile)

        # Then: AGGRESSIVE에 해당하는 파라미터 검증
        assert result["trading_frequency"] == "단타~스윙"
        assert result["min_holding_days"] == 1
        assert result["max_holding_days"] == 7
        assert result["rebalancing_frequency"] == "MONTHLY"
        assert result["loss_tolerance"] == 0.15
        # 가중치: TREND_FOLLOWING 높음, SENTIMENT 30%
        weights = result["ensemble_weights"]
        assert weights[StrategyType.TREND_FOLLOWING.value] == 0.30
        assert weights[StrategyType.MEAN_REVERSION.value] == 0.15
        assert weights["SENTIMENT"] == 0.30

    def test_apply_profile_to_strategy_dividend(self, manager):
        """DIVIDEND 프로필 전략 파라미터 매핑 테스트

        배당형 프로필이 올바른 전략 파라미터로 변환되는지 검증합니다.
        """
        # Given: DIVIDEND 프로필
        profile = InvestorProfile(
            user_id="user_strategy_004",
            risk_profile=RiskProfile.DIVIDEND,
            seed_capital=100_000_000.0,
            investment_purpose="배당 수입",
            investment_style=InvestmentStyle.ADVISORY,
            loss_tolerance=0.08,
            rebalancing_frequency=RebalancingFrequency.QUARTERLY,
        )

        # When: 전략 파라미터 매핑
        result = manager._apply_profile_to_strategy(profile)

        # Then: DIVIDEND에 해당하는 파라미터 검증
        assert result["trading_frequency"] == "포지션"
        assert result["min_holding_days"] == 60
        assert result["max_holding_days"] == 365
        assert result["rebalancing_frequency"] == "QUARTERLY"
        assert result["loss_tolerance"] == 0.08
        # 가중치: FACTOR 높음(0.35), RISK_PARITY 25%
        weights = result["ensemble_weights"]
        assert weights[StrategyType.FACTOR.value] == 0.35
        assert weights[StrategyType.RISK_PARITY.value] == 0.25

    def test_apply_profile_to_strategy_weights_sum(self, manager):
        """전략 가중치 합계 검증 테스트

        모든 프로필의 가중치 합계가 1.0인지 검증합니다.
        """
        # Given: 모든 RiskProfile 유형
        profiles = [
            InvestorProfile(
                user_id=f"user_{rp.value.lower()}",
                risk_profile=rp,
                seed_capital=50_000_000.0,
                investment_purpose="test",
                investment_style=InvestmentStyle.DISCRETIONARY,
                loss_tolerance=0.10,
            )
            for rp in [
                RiskProfile.CONSERVATIVE,
                RiskProfile.BALANCED,
                RiskProfile.AGGRESSIVE,
                RiskProfile.DIVIDEND,
            ]
        ]

        # When & Then: 모든 프로필의 가중치 합계 검증
        for profile in profiles:
            result = manager._apply_profile_to_strategy(profile)
            weights_sum = sum(result["ensemble_weights"].values())
            assert abs(weights_sum - 1.0) < 0.001, f"{profile.risk_profile.value} 가중치 합계: {weights_sum}"

    def test_apply_profile_to_strategy_contains_required_fields(self, manager):
        """필수 전략 파라미터 필드 검증 테스트

        반환된 딕셔너리에 모든 필수 필드가 포함되는지 검증합니다.
        """
        # Given: 테스트 프로필
        profile = InvestorProfile(
            user_id="user_fields_test",
            risk_profile=RiskProfile.BALANCED,
            seed_capital=50_000_000.0,
            investment_purpose="test",
            investment_style=InvestmentStyle.DISCRETIONARY,
            loss_tolerance=0.10,
        )

        # When: 전략 파라미터 매핑
        result = manager._apply_profile_to_strategy(profile)

        # Then: 모든 필수 필드 존재 검증
        required_fields = [
            "trading_frequency",
            "min_holding_days",
            "max_holding_days",
            "ensemble_weights",
            "rebalancing_frequency",
            "loss_tolerance",
            "investment_style",
        ]
        for field in required_fields:
            assert field in result, f"Missing required field: {field}"
            assert result[field] is not None, f"Field {field} is None"


# ══════════════════════════════════════
# 통합 테스트 (Integration)
# ══════════════════════════════════════
class TestInvestorProfileIntegration:
    """InvestorProfile과 InvestorProfileManager의 통합 테스트"""

    @pytest.fixture
    def manager(self):
        """프로필 매니저 인스턴스"""
        return InvestorProfileManager()

    @pytest.fixture
    def mock_session(self):
        """비동기 DB 세션 Mock"""
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        return mock_session

    @pytest.mark.asyncio
    async def test_create_and_retrieve_workflow(self, manager, mock_session):
        """프로필 생성 및 조회 워크플로우 테스트

        프로필을 생성 후 조회했을 때 동일한 데이터를 반환하는지 검증합니다.
        """
        # Given: 프로필 생성 파라미터
        user_id = "workflow_user_001"
        risk_profile = RiskProfile.BALANCED
        seed_capital = 50_000_000.0
        investment_purpose = "자산 증식"
        investment_style = InvestmentStyle.DISCRETIONARY
        loss_tolerance = 0.10
        sector_filters = ["IT", "Healthcare"]
        designated_tickers = ["005930", "000660"]

        with patch(
            "core.portfolio_manager.profile.async_session_factory",
            return_value=mock_session,
        ):
            # When: 프로필 생성
            created_profile = await manager.create_profile(
                user_id=user_id,
                risk_profile=risk_profile,
                seed_capital=seed_capital,
                investment_purpose=investment_purpose,
                investment_style=investment_style,
                loss_tolerance=loss_tolerance,
                sector_filters=sector_filters,
                designated_tickers=designated_tickers,
            )

            # 생성된 프로필의 데이터로 get_profile 응답 설정
            created_dict = created_profile.to_dict()
            result_row = tuple(
                created_dict[key]
                for key in [
                    "user_id",
                    "risk_profile",
                    "seed_capital",
                    "investment_purpose",
                    "investment_style",
                    "loss_tolerance",
                    "sector_filters",
                    "designated_tickers",
                    "rebalancing_frequency",
                    "created_at",
                    "updated_at",
                ]
            )
            mock_result = MagicMock()
            mock_result.fetchone = MagicMock(return_value=result_row)
            mock_session.execute = AsyncMock(return_value=mock_result)

            # When: 프로필 조회
            retrieved_profile = await manager.get_profile(user_id)

            # Then: 생성된 프로필과 조회된 프로필이 동일
            assert retrieved_profile.user_id == created_profile.user_id
            assert retrieved_profile.risk_profile == created_profile.risk_profile
            assert retrieved_profile.seed_capital == created_profile.seed_capital
            assert retrieved_profile.investment_purpose == created_profile.investment_purpose
            assert retrieved_profile.investment_style == created_profile.investment_style
            assert retrieved_profile.loss_tolerance == created_profile.loss_tolerance
            assert retrieved_profile.sector_filters == created_profile.sector_filters
            assert retrieved_profile.designated_tickers == created_profile.designated_tickers

    @pytest.mark.asyncio
    async def test_create_apply_strategy_workflow(self, manager, mock_session):
        """프로필 생성 및 전략 적용 워크플로우 테스트

        생성된 프로필이 전략 파라미터로 올바르게 변환되는지 검증합니다.
        """
        # Given: 프로필 생성
        with patch(
            "core.portfolio_manager.profile.async_session_factory",
            return_value=mock_session,
        ):
            created_profile = await manager.create_profile(
                user_id="workflow_user_002",
                risk_profile=RiskProfile.CONSERVATIVE,
                seed_capital=100_000_000.0,
                investment_purpose="은퇴 자금",
                investment_style=InvestmentStyle.ADVISORY,
                loss_tolerance=0.05,
            )

            # When: 전략 파라미터 적용
            strategy_params = manager._apply_profile_to_strategy(created_profile)

            # Then: 보수형 프로필에 맞는 파라미터 검증
            assert strategy_params["min_holding_days"] == 14
            assert strategy_params["max_holding_days"] == 180
            assert strategy_params["trading_frequency"] == "포지션"
            assert strategy_params["loss_tolerance"] == 0.05
            assert strategy_params["investment_style"] == "ADVISORY"
