"""
유니버스 관리 모듈 테스트 (F-05-06)

UniverseItem과 UniverseManager의 포괄적 단위 테스트입니다.

테스트 범위:
- UniverseItem 데이터 구조 테스트
- UniverseManager 필터 파이프라인 테스트
- 비동기 작업 (build_universe, refresh_universe) 테스트
- DB 모킹 및 설정 모킹 테스트

주요 테스트:
1. TestUniverseItem: 데이터 구조 및 직렬화
2. TestUniverseManager: 필터 로직 및 비동기 파이프라인
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config.constants import AssetType, InvestmentStyle, Market, RiskProfile
from core.portfolio_manager.profile import InvestorProfile
from core.portfolio_manager.universe import UniverseItem, UniverseManager


# ══════════════════════════════════════════════════════════════════════════════
# 테스트 데이터: 샘플 UniverseItem
# ══════════════════════════════════════════════════════════════════════════════
@pytest.fixture
def sample_universe_items():
    """
    다양한 특성을 가진 샘플 UniverseItem 리스트

    10개 항목: 여러 섹터, 다양한 유동성, 활성/비활성 상태 포함
    """
    return [
        # 에너지 섹터 (2개) - 필터링 대상
        UniverseItem(
            ticker="005930",
            market=Market.KRX,
            sector="Energy",
            asset_type=AssetType.STOCK,
            market_cap=1_500_000_000_000,
            avg_daily_volume=5_000_000,
            is_active=True,
        ),
        UniverseItem(
            ticker="010130",
            market=Market.KRX,
            sector="Energy",
            asset_type=AssetType.STOCK,
            market_cap=800_000_000_000,
            avg_daily_volume=2_000_000,
            is_active=True,
        ),
        # IT 섹터 (3개)
        UniverseItem(
            ticker="000660",
            market=Market.KRX,
            sector="IT",
            asset_type=AssetType.STOCK,
            market_cap=2_000_000_000_000,
            avg_daily_volume=8_000_000,
            is_active=True,
        ),
        UniverseItem(
            ticker="035420",
            market=Market.KRX,
            sector="IT",
            asset_type=AssetType.STOCK,
            market_cap=1_200_000_000_000,
            avg_daily_volume=3_000_000,
            is_active=True,
        ),
        UniverseItem(
            ticker="036570",
            market=Market.KRX,
            sector="IT",
            asset_type=AssetType.STOCK,
            market_cap=900_000_000_000,
            avg_daily_volume=1_500_000,
            is_active=True,
        ),
        # 금융 섹터 (2개)
        UniverseItem(
            ticker="055550",
            market=Market.KRX,
            sector="Finance",
            asset_type=AssetType.STOCK,
            market_cap=1_100_000_000_000,
            avg_daily_volume=4_500_000,
            is_active=True,
        ),
        UniverseItem(
            ticker="024110",
            market=Market.KRX,
            sector="Finance",
            asset_type=AssetType.STOCK,
            market_cap=500_000_000_000,
            avg_daily_volume=800_000,
            is_active=True,
        ),
        # 헬스케어 섹터 (2개)
        UniverseItem(
            ticker="051910",
            market=Market.KRX,
            sector="Healthcare",
            asset_type=AssetType.STOCK,
            market_cap=1_300_000_000_000,
            avg_daily_volume=2_500_000,
            is_active=True,
        ),
        UniverseItem(
            ticker="068270",
            market=Market.KRX,
            sector="Healthcare",
            asset_type=AssetType.STOCK,
            market_cap=300_000_000_000,
            avg_daily_volume=600_000,
            is_active=False,  # 비활성
        ),
        # NYSE 미국 주식 (1개)
        UniverseItem(
            ticker="AAPL",
            market=Market.NYSE,
            sector="IT",
            asset_type=AssetType.STOCK,
            market_cap=2_500_000_000_000,  # USD 기준 (실제로는 원화 환산)
            avg_daily_volume=50_000_000,
            is_active=True,
        ),
    ]


@pytest.fixture
def sample_investor_profile():
    """
    테스트용 InvestorProfile 생성

    섹터 필터: Energy 제외
    지정 종목: 005930 포함 강제
    """
    return InvestorProfile(
        user_id="test_user_001",
        risk_profile=RiskProfile.BALANCED,
        seed_capital=50_000_000,
        investment_purpose="WEALTH_GROWTH",
        investment_style=InvestmentStyle.DISCRETIONARY,
        loss_tolerance=-0.10,
        sector_filters=["Energy"],  # Energy 섹터 제외
        designated_tickers=["005930"],  # 005930 강제 포함
    )


@pytest.fixture
def sample_investor_profile_no_filters():
    """
    필터가 없는 InvestorProfile
    """
    return InvestorProfile(
        user_id="test_user_002",
        risk_profile=RiskProfile.CONSERVATIVE,
        seed_capital=30_000_000,
        investment_purpose="INCOME",
        investment_style=InvestmentStyle.ADVISORY,
        loss_tolerance=-0.05,
        sector_filters=[],  # 필터 없음
        designated_tickers=[],  # 지정 종목 없음
    )


# ══════════════════════════════════════════════════════════════════════════════
# TestUniverseItem: 데이터 구조 테스트
# ══════════════════════════════════════════════════════════════════════════════
class TestUniverseItem:
    """UniverseItem 데이터클래스 테스트"""

    def test_create_item_with_all_fields(self):
        """
        모든 필드를 지정하여 UniverseItem 생성
        """
        now = datetime.now(timezone.utc)
        item = UniverseItem(
            ticker="005930",
            market=Market.KRX,
            sector="IT",
            asset_type=AssetType.STOCK,
            market_cap=1_500_000_000_000,
            avg_daily_volume=5_000_000,
            is_active=True,
            updated_at=now,
        )

        assert item.ticker == "005930"
        assert item.market == Market.KRX
        assert item.sector == "IT"
        assert item.asset_type == AssetType.STOCK
        assert item.market_cap == 1_500_000_000_000
        assert item.avg_daily_volume == 5_000_000
        assert item.is_active is True
        assert item.updated_at == now

    def test_create_item_with_defaults(self):
        """
        기본값으로 UniverseItem 생성 (is_active=True, updated_at=현재시각)
        """
        item = UniverseItem(
            ticker="AAPL",
            market=Market.NYSE,
            sector="Technology",
            asset_type=AssetType.STOCK,
        )

        assert item.ticker == "AAPL"
        assert item.market == Market.NYSE
        assert item.is_active is True
        assert item.avg_daily_volume is None
        assert item.market_cap is None
        assert isinstance(item.updated_at, datetime)

    def test_create_item_with_none_values(self):
        """
        선택적 필드를 None으로 설정
        """
        item = UniverseItem(
            ticker="TEST",
            market=Market.KRX,
            sector="Unknown",
            asset_type=AssetType.STOCK,
            market_cap=None,
            avg_daily_volume=None,
        )

        assert item.market_cap is None
        assert item.avg_daily_volume is None
        assert item.is_active is True

    def test_to_dict_conversion(self):
        """
        to_dict() 메서드로 딕셔너리 변환
        """
        now = datetime.now(timezone.utc)
        item = UniverseItem(
            ticker="005930",
            market=Market.KRX,
            sector="IT",
            asset_type=AssetType.STOCK,
            market_cap=1_500_000_000_000,
            avg_daily_volume=5_000_000,
            is_active=True,
            updated_at=now,
        )

        item_dict = item.to_dict()

        assert isinstance(item_dict, dict)
        assert item_dict["ticker"] == "005930"
        assert item_dict["market"] == "KRX"
        assert item_dict["sector"] == "IT"
        assert item_dict["asset_type"] == "STOCK"
        assert item_dict["market_cap"] == 1_500_000_000_000
        assert item_dict["avg_daily_volume"] == 5_000_000
        assert item_dict["is_active"] is True
        assert item_dict["updated_at"] == now.isoformat()

    def test_to_dict_with_none_values(self):
        """
        None 값을 포함한 to_dict() 변환
        """
        item = UniverseItem(
            ticker="TEST",
            market=Market.NYSE,
            sector="Unknown",
            asset_type=AssetType.ETF,
            market_cap=None,
            avg_daily_volume=None,
        )

        item_dict = item.to_dict()

        assert item_dict["market_cap"] is None
        assert item_dict["avg_daily_volume"] is None
        assert item_dict["is_active"] is True

    def test_to_dict_enum_conversion(self):
        """
        Enum 값이 문자열로 올바르게 변환되는지 확인
        """
        item = UniverseItem(
            ticker="005930",
            market=Market.KRX,
            sector="IT",
            asset_type=AssetType.STOCK,
        )

        item_dict = item.to_dict()

        # Enum 값이 문자열로 변환되어야 함
        assert isinstance(item_dict["market"], str)
        assert isinstance(item_dict["asset_type"], str)
        assert item_dict["market"] == "KRX"
        assert item_dict["asset_type"] == "STOCK"


# ══════════════════════════════════════════════════════════════════════════════
# TestUniverseManager: 필터 로직 테스트
# ══════════════════════════════════════════════════════════════════════════════
class TestUniverseManager:
    """UniverseManager 필터 로직 테스트"""

    @patch("core.portfolio_manager.universe.get_settings")
    def test_init_with_settings(self, mock_get_settings, sample_investor_profile):
        """
        UniverseManager 초기화 시 get_settings() 호출 확인
        """
        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        manager = UniverseManager(sample_investor_profile)

        assert manager._profile == sample_investor_profile
        assert manager._settings == mock_settings
        mock_get_settings.assert_called_once()

    # ──────────────────────────────────────────────────────────────────────────
    # 섹터 필터 테스트
    # ──────────────────────────────────────────────────────────────────────────
    @patch("core.portfolio_manager.universe.get_settings")
    def test_apply_sector_filter_removes_excluded_sectors(
        self, mock_get_settings, sample_universe_items, sample_investor_profile
    ):
        """
        섹터 필터: Energy 섹터 2개 제외, 8개 유지
        """
        mock_get_settings.return_value = MagicMock()
        manager = UniverseManager(sample_investor_profile)

        filtered = manager._apply_sector_filter(sample_universe_items, ["Energy"])

        # Energy 섹터 2개 제외되어야 함
        assert len(filtered) == 8
        # Energy 섹터 항목이 없어야 함
        for item in filtered:
            assert item.sector != "Energy"

    @patch("core.portfolio_manager.universe.get_settings")
    def test_apply_sector_filter_empty_sectors_list(
        self, mock_get_settings, sample_universe_items, sample_investor_profile
    ):
        """
        섹터 필터: 빈 리스트 전달 시 모든 항목 유지
        """
        mock_get_settings.return_value = MagicMock()
        manager = UniverseManager(sample_investor_profile)

        filtered = manager._apply_sector_filter(sample_universe_items, [])

        assert len(filtered) == len(sample_universe_items)

    @patch("core.portfolio_manager.universe.get_settings")
    def test_apply_sector_filter_multiple_sectors(
        self, mock_get_settings, sample_universe_items, sample_investor_profile
    ):
        """
        섹터 필터: Energy와 Finance 제외 (4개 제외, 6개 유지)
        """
        mock_get_settings.return_value = MagicMock()
        manager = UniverseManager(sample_investor_profile)

        filtered = manager._apply_sector_filter(sample_universe_items, ["Energy", "Finance"])

        assert len(filtered) == 6
        excluded_sectors = {"Energy", "Finance"}
        for item in filtered:
            assert item.sector not in excluded_sectors

    @patch("core.portfolio_manager.universe.get_settings")
    def test_apply_sector_filter_nonexistent_sector(
        self, mock_get_settings, sample_universe_items, sample_investor_profile
    ):
        """
        섹터 필터: 존재하지 않는 섹터 제외 시도 → 모든 항목 유지
        """
        mock_get_settings.return_value = MagicMock()
        manager = UniverseManager(sample_investor_profile)

        filtered = manager._apply_sector_filter(sample_universe_items, ["NonExistent"])

        assert len(filtered) == len(sample_universe_items)

    # ──────────────────────────────────────────────────────────────────────────
    # 지정 종목 강제 포함 테스트
    # ──────────────────────────────────────────────────────────────────────────
    @patch("core.portfolio_manager.universe.get_settings")
    def test_apply_designated_tickers_existing_ticker(
        self, mock_get_settings, sample_universe_items, sample_investor_profile
    ):
        """
        지정 종목: 005930이 이미 리스트에 존재 → 중복 추가 안 함
        """
        mock_get_settings.return_value = MagicMock()
        manager = UniverseManager(sample_investor_profile)

        result = manager._apply_designated_tickers(sample_universe_items, ["005930"])

        # 중복 추가 안 함
        assert len(result) == len(sample_universe_items)
        # 005930이 정확히 1개만 존재
        count_005930 = sum(1 for item in result if item.ticker == "005930")
        assert count_005930 == 1

    @patch("core.portfolio_manager.universe.get_settings")
    def test_apply_designated_tickers_missing_ticker(
        self, mock_get_settings, sample_universe_items, sample_investor_profile
    ):
        """
        지정 종목: 999999 (없는 종목) 강제 추가 → 더미 항목 생성
        """
        mock_get_settings.return_value = MagicMock()
        manager = UniverseManager(sample_investor_profile)

        result = manager._apply_designated_tickers(sample_universe_items, ["999999"])

        # 1개 추가됨
        assert len(result) == len(sample_universe_items) + 1
        # 999999가 존재
        tickers = [item.ticker for item in result]
        assert "999999" in tickers
        # 더미 항목의 기본값 확인
        dummy = next(item for item in result if item.ticker == "999999")
        assert dummy.market == Market.KRX
        assert dummy.sector == "Unknown"
        assert dummy.asset_type == AssetType.STOCK
        assert dummy.is_active is True

    @patch("core.portfolio_manager.universe.get_settings")
    def test_apply_designated_tickers_multiple_tickers(
        self, mock_get_settings, sample_universe_items, sample_investor_profile
    ):
        """
        지정 종목: 여러 개 (기존 1개 + 신규 2개)
        """
        mock_get_settings.return_value = MagicMock()
        manager = UniverseManager(sample_investor_profile)

        result = manager._apply_designated_tickers(sample_universe_items, ["005930", "111111", "222222"])

        # 기존 10개 + 신규 2개 = 12개
        assert len(result) == 12
        tickers = [item.ticker for item in result]
        assert "005930" in tickers
        assert "111111" in tickers
        assert "222222" in tickers

    @patch("core.portfolio_manager.universe.get_settings")
    def test_apply_designated_tickers_empty_list(
        self, mock_get_settings, sample_universe_items, sample_investor_profile
    ):
        """
        지정 종목: 빈 리스트 → 변경 없음
        """
        mock_get_settings.return_value = MagicMock()
        manager = UniverseManager(sample_investor_profile)

        result = manager._apply_designated_tickers(sample_universe_items, [])

        assert len(result) == len(sample_universe_items)
        assert result == sample_universe_items

    @patch("core.portfolio_manager.universe.get_settings")
    def test_apply_designated_tickers_no_duplicates_after_adding(self, mock_get_settings, sample_investor_profile):
        """
        지정 종목: 이미 존재하는 종목을 다시 추가해도 중복 없음
        """
        mock_get_settings.return_value = MagicMock()
        manager = UniverseManager(sample_investor_profile)

        items = [
            UniverseItem(
                ticker="AAA",
                market=Market.KRX,
                sector="IT",
                asset_type=AssetType.STOCK,
            ),
            UniverseItem(
                ticker="BBB",
                market=Market.KRX,
                sector="Finance",
                asset_type=AssetType.STOCK,
            ),
        ]

        result = manager._apply_designated_tickers(items, ["AAA", "CCC"])

        assert len(result) == 3  # AAA, BBB, CCC
        count_aaa = sum(1 for item in result if item.ticker == "AAA")
        assert count_aaa == 1

    # ──────────────────────────────────────────────────────────────────────────
    # 자동 필터 테스트
    # ──────────────────────────────────────────────────────────────────────────
    @patch("core.portfolio_manager.universe.get_settings")
    def test_apply_auto_filter_removes_bottom_20_percent_volume(self, mock_get_settings, sample_investor_profile):
        """
        자동 필터: 거래량 하위 20% 제외

        10개 항목의 거래량: 5M, 2M, 8M, 3M, 1.5M, 4.5M, 0.8M, 2.5M, 0.6M, 50M
        정렬: 0.6M, 0.8M, 1.5M, 2M, 2.5M, 3M, 4.5M, 5M, 8M, 50M
        하위 20% 기준: 10 * 0.2 = 2개 → 처음 2개(0.6M, 0.8M) 제외
        """
        mock_get_settings.return_value = MagicMock()
        manager = UniverseManager(sample_investor_profile)

        items = [
            UniverseItem(
                ticker="T1",
                market=Market.KRX,
                sector="IT",
                asset_type=AssetType.STOCK,
                avg_daily_volume=5_000_000,
                is_active=True,
            ),
            UniverseItem(
                ticker="T2",
                market=Market.KRX,
                sector="IT",
                asset_type=AssetType.STOCK,
                avg_daily_volume=2_000_000,
                is_active=True,
            ),
            UniverseItem(
                ticker="T3",
                market=Market.KRX,
                sector="IT",
                asset_type=AssetType.STOCK,
                avg_daily_volume=8_000_000,
                is_active=True,
            ),
            UniverseItem(
                ticker="T4",
                market=Market.KRX,
                sector="IT",
                asset_type=AssetType.STOCK,
                avg_daily_volume=3_000_000,
                is_active=True,
            ),
            UniverseItem(
                ticker="T5",
                market=Market.KRX,
                sector="IT",
                asset_type=AssetType.STOCK,
                avg_daily_volume=1_500_000,
                is_active=True,
            ),
            UniverseItem(
                ticker="T6",
                market=Market.KRX,
                sector="IT",
                asset_type=AssetType.STOCK,
                avg_daily_volume=4_500_000,
                is_active=True,
            ),
            UniverseItem(
                ticker="T7",
                market=Market.KRX,
                sector="IT",
                asset_type=AssetType.STOCK,
                avg_daily_volume=800_000,
                is_active=True,
            ),
            UniverseItem(
                ticker="T8",
                market=Market.KRX,
                sector="IT",
                asset_type=AssetType.STOCK,
                avg_daily_volume=2_500_000,
                is_active=True,
            ),
            UniverseItem(
                ticker="T9",
                market=Market.KRX,
                sector="IT",
                asset_type=AssetType.STOCK,
                avg_daily_volume=600_000,
                is_active=True,
            ),
            UniverseItem(
                ticker="T10",
                market=Market.KRX,
                sector="IT",
                asset_type=AssetType.STOCK,
                avg_daily_volume=50_000_000,
                is_active=True,
            ),
        ]

        filtered = manager._apply_auto_filter(items)

        # 거래량 하위 20% 제외
        # 하위 2개: T9(600K), T7(800K) 제외
        assert len(filtered) == 8
        tickers = [item.ticker for item in filtered]
        assert "T9" not in tickers  # 600K - 하위 20%
        assert "T7" not in tickers  # 800K - 하위 20%

    @patch("core.portfolio_manager.universe.get_settings")
    def test_apply_auto_filter_removes_inactive_items(self, mock_get_settings, sample_investor_profile):
        """
        자동 필터: 비활성 종목 제외
        """
        mock_get_settings.return_value = MagicMock()
        manager = UniverseManager(sample_investor_profile)

        items = [
            UniverseItem(
                ticker="ACTIVE1",
                market=Market.KRX,
                sector="IT",
                asset_type=AssetType.STOCK,
                avg_daily_volume=5_000_000,
                is_active=True,
            ),
            UniverseItem(
                ticker="INACTIVE1",
                market=Market.KRX,
                sector="IT",
                asset_type=AssetType.STOCK,
                avg_daily_volume=4_000_000,
                is_active=False,  # 비활성
            ),
            UniverseItem(
                ticker="ACTIVE2",
                market=Market.KRX,
                sector="Finance",
                asset_type=AssetType.STOCK,
                avg_daily_volume=3_000_000,
                is_active=True,
            ),
            UniverseItem(
                ticker="INACTIVE2",
                market=Market.KRX,
                sector="Finance",
                asset_type=AssetType.STOCK,
                avg_daily_volume=2_000_000,
                is_active=False,  # 비활성
            ),
        ]

        filtered = manager._apply_auto_filter(items)

        # 2개 유지 (ACTIVE1, ACTIVE2)
        assert len(filtered) == 2
        for item in filtered:
            assert item.is_active is True

    @patch("core.portfolio_manager.universe.get_settings")
    def test_apply_auto_filter_with_none_volumes(self, mock_get_settings, sample_investor_profile):
        """
        자동 필터: None 거래량 항목 처리

        None 값은 필터링에서 제외되고, 유지됨
        """
        mock_get_settings.return_value = MagicMock()
        manager = UniverseManager(sample_investor_profile)

        items = [
            UniverseItem(
                ticker="T1",
                market=Market.KRX,
                sector="IT",
                asset_type=AssetType.STOCK,
                avg_daily_volume=5_000_000,
                is_active=True,
            ),
            UniverseItem(
                ticker="T2",
                market=Market.KRX,
                sector="IT",
                asset_type=AssetType.STOCK,
                avg_daily_volume=None,  # None 값
                is_active=True,
            ),
            UniverseItem(
                ticker="T3",
                market=Market.KRX,
                sector="IT",
                asset_type=AssetType.STOCK,
                avg_daily_volume=3_000_000,
                is_active=True,
            ),
        ]

        filtered = manager._apply_auto_filter(items)

        # 모두 유지 (None은 필터링 제외)
        assert len(filtered) == 3

    @patch("core.portfolio_manager.universe.get_settings")
    def test_apply_auto_filter_empty_list(self, mock_get_settings, sample_investor_profile):
        """
        자동 필터: 빈 리스트 입력
        """
        mock_get_settings.return_value = MagicMock()
        manager = UniverseManager(sample_investor_profile)

        filtered = manager._apply_auto_filter([])

        assert len(filtered) == 0

    @patch("core.portfolio_manager.universe.get_settings")
    def test_apply_auto_filter_all_inactive(self, mock_get_settings, sample_investor_profile):
        """
        자동 필터: 모든 항목이 비활성 → 빈 결과
        """
        mock_get_settings.return_value = MagicMock()
        manager = UniverseManager(sample_investor_profile)

        items = [
            UniverseItem(
                ticker="T1",
                market=Market.KRX,
                sector="IT",
                asset_type=AssetType.STOCK,
                is_active=False,
            ),
            UniverseItem(
                ticker="T2",
                market=Market.KRX,
                sector="IT",
                asset_type=AssetType.STOCK,
                is_active=False,
            ),
        ]

        filtered = manager._apply_auto_filter(items)

        assert len(filtered) == 0

    # ──────────────────────────────────────────────────────────────────────────
    # 비동기 파이프라인 테스트
    # ──────────────────────────────────────────────────────────────────────────
    @pytest.mark.asyncio
    @patch("core.portfolio_manager.universe.async_session_factory")
    @patch("core.portfolio_manager.universe.get_settings")
    async def test_build_universe_complete_pipeline(
        self, mock_get_settings, mock_async_session, sample_universe_items, sample_investor_profile
    ):
        """
        build_universe: 전체 파이프라인 테스트

        로드 → 섹터 필터 → 지정 종목 → 자동 필터 → 저장
        """
        # Mock 설정
        mock_get_settings.return_value = MagicMock()
        mock_session = AsyncMock()
        mock_async_session.return_value.__aenter__.return_value = mock_session

        manager = UniverseManager(sample_investor_profile)

        # _load_all_active_stocks 모킹
        manager._load_all_active_stocks = AsyncMock(return_value=sample_universe_items)
        # _store_universe 모킹
        manager._store_universe = AsyncMock()

        # build_universe 실행
        result = await manager.build_universe()

        # 검증
        assert isinstance(result, list)
        assert len(result) > 0
        # 005930은 반드시 포함 (지정 종목)
        tickers = [item.ticker for item in result]
        assert "005930" in tickers
        # Energy 섹터 제외
        for item in result:
            assert item.sector != "Energy"
        # 저장 함수 호출 확인
        manager._store_universe.assert_called_once()

    @pytest.mark.asyncio
    @patch("core.portfolio_manager.universe.async_session_factory")
    @patch("core.portfolio_manager.universe.get_settings")
    async def test_refresh_universe_calls_same_pipeline(
        self,
        mock_get_settings,
        mock_async_session,
        sample_universe_items,
        sample_investor_profile,
    ):
        """
        refresh_universe: build_universe와 동일한 파이프라인 호출
        """
        mock_get_settings.return_value = MagicMock()
        mock_session = AsyncMock()
        mock_async_session.return_value.__aenter__.return_value = mock_session

        manager = UniverseManager(sample_investor_profile)
        manager._load_all_active_stocks = AsyncMock(return_value=sample_universe_items)
        manager._store_universe = AsyncMock()

        result = await manager.refresh_universe()

        assert isinstance(result, list)
        assert len(result) > 0
        manager._store_universe.assert_called_once()
        # 같은 필터 결과 확인
        tickers = [item.ticker for item in result]
        assert "005930" in tickers

    @pytest.mark.asyncio
    @patch("core.portfolio_manager.universe.async_session_factory")
    @patch("core.portfolio_manager.universe.get_settings")
    async def test_build_universe_empty_db(self, mock_get_settings, mock_async_session, sample_investor_profile):
        """
        build_universe: DB가 빈 경우 → 지정 종목만 추가
        """
        mock_get_settings.return_value = MagicMock()
        mock_session = AsyncMock()
        mock_async_session.return_value.__aenter__.return_value = mock_session

        manager = UniverseManager(sample_investor_profile)
        manager._load_all_active_stocks = AsyncMock(return_value=[])
        manager._store_universe = AsyncMock()

        result = await manager.build_universe()

        # 지정 종목만 추가됨
        assert len(result) == 1
        assert result[0].ticker == "005930"

    @pytest.mark.asyncio
    @patch("core.portfolio_manager.universe.async_session_factory")
    @patch("core.portfolio_manager.universe.get_settings")
    async def test_build_universe_no_filters_no_designated_tickers(
        self,
        mock_get_settings,
        mock_async_session,
        sample_universe_items,
        sample_investor_profile_no_filters,
    ):
        """
        build_universe: 필터 및 지정 종목 없음 → 자동 필터만 적용
        """
        mock_get_settings.return_value = MagicMock()
        mock_session = AsyncMock()
        mock_async_session.return_value.__aenter__.return_value = mock_session

        manager = UniverseManager(sample_investor_profile_no_filters)
        manager._load_all_active_stocks = AsyncMock(return_value=sample_universe_items)
        manager._store_universe = AsyncMock()

        result = await manager.build_universe()

        # 자동 필터: 비활성 제외, 거래량 하위 20% 제외
        assert len(result) > 0
        for item in result:
            assert item.is_active is True

    @pytest.mark.asyncio
    @patch("core.portfolio_manager.universe.async_session_factory")
    @patch("core.portfolio_manager.universe.get_settings")
    async def test_build_universe_with_exception(self, mock_get_settings, mock_async_session, sample_investor_profile):
        """
        build_universe: _load_all_active_stocks 실패 시 예외 전파
        """
        mock_get_settings.return_value = MagicMock()
        mock_session = AsyncMock()
        mock_async_session.return_value.__aenter__.return_value = mock_session

        manager = UniverseManager(sample_investor_profile)
        manager._load_all_active_stocks = AsyncMock(side_effect=Exception("DB 연결 실패"))

        with pytest.raises(Exception, match="DB 연결 실패"):
            await manager.build_universe()

    @pytest.mark.asyncio
    @patch("core.portfolio_manager.universe.async_session_factory")
    @patch("core.portfolio_manager.universe.get_settings")
    async def test_load_all_active_stocks_returns_empty(self, mock_get_settings, mock_async_session):
        """
        _load_all_active_stocks: DB 조회 실패 시 빈 리스트 반환
        """
        mock_get_settings.return_value = MagicMock()
        mock_session = AsyncMock()
        mock_session.execute.side_effect = Exception("DB 오류")
        mock_async_session.return_value.__aenter__.return_value = mock_session

        manager = UniverseManager(
            InvestorProfile(
                user_id="test",
                risk_profile=RiskProfile.BALANCED,
                seed_capital=50_000_000,
                investment_purpose="WEALTH_GROWTH",
                investment_style=InvestmentStyle.DISCRETIONARY,
                loss_tolerance=-0.10,
            )
        )

        result = await manager._load_all_active_stocks()

        assert result == []

    @pytest.mark.asyncio
    @patch("core.portfolio_manager.universe.async_session_factory")
    @patch("core.portfolio_manager.universe.get_settings")
    async def test_store_universe_inserts_items(self, mock_get_settings, mock_async_session, sample_universe_items):
        """
        _store_universe: 모든 항목이 DB에 삽입됨
        """
        mock_get_settings.return_value = MagicMock()
        mock_session = AsyncMock()
        mock_async_session.return_value.__aenter__.return_value = mock_session

        manager = UniverseManager(
            InvestorProfile(
                user_id="test",
                risk_profile=RiskProfile.BALANCED,
                seed_capital=50_000_000,
                investment_purpose="WEALTH_GROWTH",
                investment_style=InvestmentStyle.DISCRETIONARY,
                loss_tolerance=-0.10,
            )
        )

        await manager._store_universe(sample_universe_items)

        # execute 호출 횟수 확인 (항목 개수만큼)
        assert mock_session.execute.call_count == len(sample_universe_items)
        mock_session.commit.assert_called_once()


# ══════════════════════════════════════════════════════════════════════════════
# 통합 테스트
# ══════════════════════════════════════════════════════════════════════════════
class TestUniverseIntegration:
    """UniverseManager 통합 테스트"""

    @pytest.mark.asyncio
    @patch("core.portfolio_manager.universe.async_session_factory")
    @patch("core.portfolio_manager.universe.get_settings")
    async def test_end_to_end_universe_building(self, mock_get_settings, mock_async_session):
        """
        엔드-투-엔드: 프로필 생성 → 유니버스 구축
        """
        # Mock 설정
        mock_get_settings.return_value = MagicMock()
        mock_session = AsyncMock()
        mock_async_session.return_value.__aenter__.return_value = mock_session

        # 샘플 종목 데이터
        sample_items = [
            UniverseItem(
                ticker="005930",
                market=Market.KRX,
                sector="IT",
                asset_type=AssetType.STOCK,
                avg_daily_volume=5_000_000,
                is_active=True,
            ),
            UniverseItem(
                ticker="000660",
                market=Market.KRX,
                sector="IT",
                asset_type=AssetType.STOCK,
                avg_daily_volume=4_000_000,
                is_active=True,
            ),
            UniverseItem(
                ticker="010130",
                market=Market.KRX,
                sector="Energy",
                asset_type=AssetType.STOCK,
                avg_daily_volume=2_000_000,
                is_active=True,
            ),
        ]

        # 프로필 생성
        profile = InvestorProfile(
            user_id="user_001",
            risk_profile=RiskProfile.BALANCED,
            seed_capital=50_000_000,
            investment_purpose="WEALTH_GROWTH",
            investment_style=InvestmentStyle.DISCRETIONARY,
            loss_tolerance=-0.10,
            sector_filters=["Energy"],
            designated_tickers=["005930"],
        )

        # Manager 생성
        manager = UniverseManager(profile)
        manager._load_all_active_stocks = AsyncMock(return_value=sample_items)
        manager._store_universe = AsyncMock()

        # 유니버스 구축
        result = await manager.build_universe()

        # 검증
        assert len(result) == 2  # Energy 제외, 005930 + 000660
        tickers = [item.ticker for item in result]
        assert "005930" in tickers
        assert "000660" in tickers
        assert "010130" not in tickers  # Energy 섹터 제외
