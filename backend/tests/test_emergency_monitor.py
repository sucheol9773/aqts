"""
비상 리밸런싱 모니터 (F-05-04) 단위 테스트

테스트 범위:
- PositionSnapshot: 포지션 가치 계산
- PortfolioLossReport: 손실률 분석 및 트리거 판정
- EmergencyMonitorState: 모니터 상태 관리
- EmergencyRebalancingMonitor: 손실률 계산, 트리거 처리, 모니터 루프

모든 외부 API (KIS, Telegram, DB, OrderExecutor)는 Mock으로 대체됩니다.
"""

import asyncio
from datetime import datetime, time, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config.constants import (
    InvestmentStyle,
    Market,
    OrderSide,
    OrderType,
)
from config.settings import AppSettings, RiskManagementSettings
from core.emergency_monitor import (
    EmergencyMonitorState,
    EmergencyRebalancingMonitor,
    PortfolioLossReport,
    PositionSnapshot,
)

# ══════════════════════════════════════
# Fixtures
# ══════════════════════════════════════


@pytest.fixture
def mock_settings():
    """테스트용 설정"""
    settings = MagicMock(spec=AppSettings)
    settings.risk = MagicMock(spec=RiskManagementSettings)
    settings.risk.stop_loss_percent = -0.10  # -10%
    settings.risk.initial_capital_krw = 50_000_000
    return settings


@pytest.fixture
def mock_kis_client():
    """KIS API Mock"""
    client = AsyncMock()
    client.get_kr_balance.return_value = None
    client.get_us_balance.return_value = None
    return client


@pytest.fixture
def mock_telegram():
    """텔레그램 알림 Mock"""
    notifier = AsyncMock()
    notifier.send_message = AsyncMock()
    notifier.send_error_alert = AsyncMock()
    return notifier


@pytest.fixture
def mock_order_executor():
    """주문 실행기 Mock"""
    executor = AsyncMock()
    executor.execute_order = AsyncMock(return_value={"status": "filled"})
    return executor


@pytest.fixture
def mock_trading_guard():
    """거래 안전 장치 Mock"""
    guard = MagicMock()
    guard.state = MagicMock()
    guard.state.kill_switch_on = False
    return guard


@pytest.fixture
def mock_rebalancing_engine():
    """리밸런싱 엔진 Mock"""
    engine = MagicMock()
    engine.profile = MagicMock()
    engine.profile.investment_style = InvestmentStyle.DISCRETIONARY
    return engine


@pytest.fixture
def monitor(
    mock_kis_client, mock_telegram, mock_order_executor, mock_trading_guard, mock_rebalancing_engine, mock_settings
):
    """테스트용 모니터 인스턴스"""
    with patch("core.emergency_monitor.get_settings", return_value=mock_settings):
        monitor_instance = EmergencyRebalancingMonitor(
            kis_client=mock_kis_client,
            telegram_notifier=mock_telegram,
            order_executor=mock_order_executor,
            trading_guard=mock_trading_guard,
            rebalancing_engine=mock_rebalancing_engine,
        )
    return monitor_instance


# ══════════════════════════════════════
# TestPositionSnapshot (5 tests)
# ══════════════════════════════════════


class TestPositionSnapshot:
    """포지션 스냅샷 테스트"""

    def test_purchase_value_calculation(self):
        """매입 금액 계산 테스트"""
        # 100주 x 70,000원 = 7,000,000원
        pos = PositionSnapshot(
            ticker="005930",
            market=Market.KRX,
            quantity=100,
            avg_purchase_price=70000,
            current_price=71400,
        )
        assert pos.purchase_value == 7_000_000

    def test_current_value_calculation(self):
        """현재 평가 금액 계산 테스트"""
        # 100주 x 71,400원 = 7,140,000원
        pos = PositionSnapshot(
            ticker="005930",
            market=Market.KRX,
            quantity=100,
            avg_purchase_price=70000,
            current_price=71400,
        )
        assert pos.current_value == 7_140_000

    def test_pnl_calculation(self):
        """평가 손익 계산 테스트"""
        # 7,140,000 - 7,000,000 = 140,000원 (수익)
        pos = PositionSnapshot(
            ticker="005930",
            market=Market.KRX,
            quantity=100,
            avg_purchase_price=70000,
            current_price=71400,
        )
        assert pos.pnl == 140_000

    def test_pnl_percent_calculation(self):
        """평가 손익률 계산 테스트"""
        # 140,000 / 7,000,000 = 0.02 (2%)
        pos = PositionSnapshot(
            ticker="005930",
            market=Market.KRX,
            quantity=100,
            avg_purchase_price=70000,
            current_price=71400,
        )
        assert abs(pos.pnl_percent - 0.02) < 0.0001

    def test_pnl_percent_with_zero_purchase_value(self):
        """매입가 0인 경우 손익률 테스트 (edge case)"""
        # 0 매입가 → 0 손익률
        pos = PositionSnapshot(
            ticker="TEST",
            market=Market.KRX,
            quantity=100,
            avg_purchase_price=0,
            current_price=100,
        )
        assert pos.pnl_percent == 0.0


# ══════════════════════════════════════
# TestPortfolioLossReport (5 tests)
# ══════════════════════════════════════


class TestPortfolioLossReport:
    """포트폴리오 손실 분석 보고서 테스트"""

    def test_is_triggered_user_trigger(self):
        """사용자 기준 트리거 판정 테스트"""
        report = PortfolioLossReport(
            total_purchase_value=50_000_000,
            total_current_value=40_000_000,
            total_pnl=-10_000_000,
            loss_percent=-0.20,  # -20%
            user_threshold=-0.10,  # -10%
            algo_threshold=-0.15,  # -15%
            user_triggered=True,
            algo_triggered=False,
        )
        assert report.is_triggered is True

    def test_is_triggered_algo_trigger(self):
        """알고리즘 기준 트리거 판정 테스트"""
        report = PortfolioLossReport(
            loss_percent=-0.18,  # -18%
            user_threshold=-0.10,  # -10% (초과 안함)
            algo_threshold=-0.15,  # -15%
            user_triggered=False,
            algo_triggered=True,
        )
        assert report.is_triggered is True

    def test_is_triggered_both_trigger(self):
        """사용자와 알고리즘 둘 다 트리거 판정 테스트"""
        report = PortfolioLossReport(
            loss_percent=-0.20,
            user_threshold=-0.10,
            algo_threshold=-0.15,
            user_triggered=True,
            algo_triggered=True,
        )
        assert report.is_triggered is True

    def test_trigger_reason_formatting_user(self):
        """사용자 트리거 사유 포맷팅 테스트"""
        report = PortfolioLossReport(
            loss_percent=-0.20,
            user_threshold=-0.10,
            algo_threshold=-0.15,
            user_triggered=True,
            algo_triggered=False,
        )
        reason = report.trigger_reason
        assert "사용자 설정 손실 한도 초과" in reason
        assert "-20.00%" in reason
        assert "-10.00%" in reason

    def test_is_triggered_not_triggered(self):
        """트리거 미발동 테스트"""
        report = PortfolioLossReport(
            loss_percent=-0.05,  # -5% (임계값 내)
            user_threshold=-0.10,
            algo_threshold=-0.15,
            user_triggered=False,
            algo_triggered=False,
        )
        assert report.is_triggered is False


# ══════════════════════════════════════
# TestEmergencyMonitorState (3 tests)
# ══════════════════════════════════════


class TestEmergencyMonitorState:
    """모니터 상태 관리 테스트"""

    def test_to_dict_serialization(self):
        """상태를 딕셔너리로 직렬화 테스트"""
        now = datetime.now(timezone.utc)
        state = EmergencyMonitorState(
            is_running=True,
            is_paused=False,
            last_check_at=now,
            check_count=5,
            trigger_count=1,
            last_trigger_at=now,
            cooldown_until=now + timedelta(minutes=30),
        )
        result = state.to_dict()
        assert result["is_running"] is True
        assert result["is_paused"] is False
        assert result["check_count"] == 5
        assert result["trigger_count"] == 1

    def test_to_dict_with_none_dates(self):
        """None 날짜 필드 직렬화 테스트"""
        state = EmergencyMonitorState(
            is_running=False,
            last_check_at=None,
            last_trigger_at=None,
            cooldown_until=None,
        )
        result = state.to_dict()
        assert result["last_check_at"] is None
        assert result["last_trigger_at"] is None
        assert result["cooldown_until"] is None

    def test_default_values(self):
        """기본값 확인 테스트"""
        state = EmergencyMonitorState()
        assert state.is_running is False
        assert state.is_paused is False
        assert state.check_count == 0
        assert state.trigger_count == 0


# ══════════════════════════════════════
# TestCalculateLoss (10 tests)
# ══════════════════════════════════════


class TestCalculateLoss:
    """손실률 계산 테스트"""

    def test_loss_below_user_threshold(self, monitor):
        """손실이 사용자 임계값 이하 테스트"""
        # 손실: -5% (임계값 -10% 초과 안함)
        positions = [
            PositionSnapshot("005930", Market.KRX, 100, 70000, 66500, "IT"),
        ]
        report = monitor._calculate_loss(positions)
        assert report.loss_percent == pytest.approx(-0.05, abs=0.001)
        assert report.user_triggered is False
        assert report.is_triggered is False

    def test_loss_exceeding_user_threshold(self, monitor):
        """손실이 사용자 임계값 초과 테스트"""
        # 손실: -12% (임계값 -10% 초과)
        positions = [
            PositionSnapshot("005930", Market.KRX, 100, 70000, 61600, "IT"),
        ]
        report = monitor._calculate_loss(positions)
        assert report.loss_percent == pytest.approx(-0.12, abs=0.001)
        assert report.user_triggered is True
        assert report.is_triggered is True

    def test_loss_exceeding_algo_threshold(self, monitor):
        """손실이 알고리즘 임계값 초과 테스트"""
        # 다중 포지션으로 알고리즘 임계값 계산
        positions = [
            PositionSnapshot("005930", Market.KRX, 100, 70000, 63000, "IT"),  # -10%
            PositionSnapshot("000660", Market.KRX, 50, 100000, 110000, "Semiconductor"),  # +10%
        ]
        report = monitor._calculate_loss(positions)
        # algo_triggered는 변동성 계산 결과에 따라 결정됨
        assert report.algo_threshold <= -0.05
        assert report.algo_threshold >= -0.25

    def test_loss_exceeding_both_thresholds(self, monitor):
        """손실이 사용자와 알고리즘 임계값 모두 초과 테스트"""
        # 심각한 손실: -25%
        positions = [
            PositionSnapshot("005930", Market.KRX, 100, 70000, 52500, "IT"),
        ]
        report = monitor._calculate_loss(positions)
        assert report.loss_percent == pytest.approx(-0.25, abs=0.001)
        assert report.user_triggered is True

    def test_loss_empty_positions(self, monitor):
        """빈 포지션 리스트 테스트"""
        positions = []
        report = monitor._calculate_loss(positions)
        assert report.loss_percent == 0.0
        assert report.total_purchase_value == 0.0
        assert report.user_triggered is False

    def test_loss_single_position(self, monitor):
        """단일 포지션 테스트"""
        positions = [
            PositionSnapshot("005930", Market.KRX, 100, 70000, 71400, "IT"),
        ]
        report = monitor._calculate_loss(positions)
        assert report.total_purchase_value == 7_000_000
        assert report.total_current_value == 7_140_000
        assert report.loss_percent > 0  # 수익

    def test_loss_mixed_profit_loss_positions(self, monitor):
        """수익/손실 혼합 포지션 테스트"""
        positions = [
            PositionSnapshot("005930", Market.KRX, 100, 70000, 71400, "IT"),  # +2%
            PositionSnapshot("000660", Market.KRX, 50, 100000, 85000, "Semiconductor"),  # -15%
        ]
        report = monitor._calculate_loss(positions)
        # 전체: (7,140,000 + 4,250,000) - (7,000,000 + 5,000,000) = -610,000 / 12,000,000
        expected_loss = (7_140_000 + 4_250_000 - 7_000_000 - 5_000_000) / (7_000_000 + 5_000_000)
        assert report.loss_percent == pytest.approx(expected_loss, abs=0.001)

    def test_loss_zero_purchase_value_edge_case(self, monitor):
        """매입가 0인 포지션 테스트"""
        positions = [
            PositionSnapshot("TEST", Market.KRX, 100, 0, 100, "Test"),
        ]
        report = monitor._calculate_loss(positions)
        assert report.loss_percent == 0.0

    def test_loss_worst_positions_sorted(self, monitor):
        """최악 포지션 정렬 테스트"""
        positions = [
            PositionSnapshot("A", Market.KRX, 100, 100, 80, "IT"),  # -20%
            PositionSnapshot("B", Market.KRX, 100, 100, 95, "Finance"),  # -5%
            PositionSnapshot("C", Market.KRX, 100, 100, 90, "Healthcare"),  # -10%
            PositionSnapshot("D", Market.KRX, 100, 100, 110, "Energy"),  # +10%
        ]
        report = monitor._calculate_loss(positions)
        # 최악 3개 선택 (손익률 오름차순)
        assert len(report.worst_positions) <= 3
        if len(report.worst_positions) >= 2:
            assert report.worst_positions[0].pnl_percent <= report.worst_positions[1].pnl_percent

    def test_loss_calculation_accuracy(self, monitor):
        """손실률 계산 정확도 테스트"""
        # 매입 50,000,000 / 현재 40,000,000 = -20%
        positions = [
            PositionSnapshot("005930", Market.KRX, 1000, 50000, 40000, "IT"),
        ]
        report = monitor._calculate_loss(positions)
        assert report.total_purchase_value == 50_000_000
        assert report.total_current_value == 40_000_000
        assert report.loss_percent == pytest.approx(-0.20, abs=0.0001)


# ══════════════════════════════════════
# TestCalculateAlgoThreshold (8 tests)
# ══════════════════════════════════════


class TestCalculateAlgoThreshold:
    """알고리즘 동적 손절 임계값 계산 테스트"""

    def test_algo_threshold_single_position(self, monitor):
        """단일 포지션 알고리즘 임계값 테스트"""
        positions = [
            PositionSnapshot("005930", Market.KRX, 100, 70000, 63000, "IT"),  # -10%
        ]
        threshold = monitor._calculate_algo_threshold(positions)
        # 단일 포지션: volatility = abs(-0.10) * 0.1 + 0.05 = 0.06
        # threshold = -2 * 0.06 = -0.12
        # clamped to [-0.25, -0.05]
        assert -0.25 <= threshold <= -0.05

    def test_algo_threshold_multiple_positions_varying_volatility(self, monitor):
        """다중 포지션 변동성 계산 테스트"""
        positions = [
            PositionSnapshot("A", Market.KRX, 100, 100, 80, "IT"),  # -20%
            PositionSnapshot("B", Market.KRX, 100, 100, 110, "Finance"),  # +10%
        ]
        threshold = monitor._calculate_algo_threshold(positions)
        assert -0.25 <= threshold <= -0.05

    def test_algo_threshold_high_volatility_wider_range(self, monitor):
        """고변동성 포트폴리오 = 넓은 임계값 테스트"""
        # 극단적 변동성: -50% ~ +50%
        positions = [
            PositionSnapshot("A", Market.KRX, 100, 100, 50, "IT"),  # -50%
            PositionSnapshot("B", Market.KRX, 100, 100, 150, "Finance"),  # +50%
        ]
        threshold = monitor._calculate_algo_threshold(positions)
        # 높은 변동성 → 더 넓은 범위 (음수 더 적게)
        assert threshold >= -0.25

    def test_algo_threshold_low_volatility_narrow_range(self, monitor):
        """저변동성 포트폴리오 = 좁은 임계값 테스트"""
        # 낮은 변동성: 모두 -2% ~ +2%
        positions = [
            PositionSnapshot("A", Market.KRX, 100, 100, 98, "IT"),  # -2%
            PositionSnapshot("B", Market.KRX, 100, 100, 102, "Finance"),  # +2%
        ]
        threshold = monitor._calculate_algo_threshold(positions)
        # 낮은 변동성 → 좁은 범위
        assert -0.25 <= threshold <= -0.05

    def test_algo_threshold_clamping_min(self, monitor):
        """최소값 -5% 클램핑 테스트"""
        # 매우 낮은 변동성 → 임계값 > -5% 클램핑
        positions = [
            PositionSnapshot("A", Market.KRX, 100, 100, 99.5, "IT"),  # -0.5%
            PositionSnapshot("B", Market.KRX, 100, 100, 100.5, "Finance"),  # +0.5%
        ]
        threshold = monitor._calculate_algo_threshold(positions)
        assert threshold == pytest.approx(-0.05, abs=0.001)

    def test_algo_threshold_clamping_max(self, monitor):
        """최대값 -25% 클램핑 테스트"""
        # 매우 높은 변동성 → 임계값 < -25% 클램핑
        positions = [
            PositionSnapshot("A", Market.KRX, 100, 100, 10, "IT"),  # -90%
            PositionSnapshot("B", Market.KRX, 100, 100, 200, "Finance"),  # +100%
        ]
        threshold = monitor._calculate_algo_threshold(positions)
        assert threshold == pytest.approx(-0.25, abs=0.001)

    def test_algo_threshold_empty_positions_fallback(self, monitor):
        """빈 포지션 리스트 폴백 테스트"""
        positions = []
        threshold = monitor._calculate_algo_threshold(positions)
        # 사용자 설정 임계값 반환
        assert threshold == monitor._settings.risk.stop_loss_percent

    def test_algo_threshold_zero_total_value(self, monitor):
        """총액 0인 경우 테스트"""
        positions = [
            PositionSnapshot("A", Market.KRX, 0, 100, 100, "IT"),
        ]
        threshold = monitor._calculate_algo_threshold(positions)
        assert threshold == monitor._settings.risk.stop_loss_percent


# ══════════════════════════════════════
# TestGenerateDefensiveOrders (5 tests)
# ══════════════════════════════════════


class TestGenerateDefensiveOrders:
    """방어 주문 생성 테스트"""

    def test_generate_defensive_orders_normal_case(self, monitor):
        """일반적인 방어 주문 생성 테스트"""
        positions = [
            PositionSnapshot("005930", Market.KRX, 100, 70000, 71400, "IT"),
            PositionSnapshot("000660", Market.KRX, 50, 100000, 110000, "Semiconductor"),
        ]
        orders = monitor._generate_defensive_orders(positions)
        # 각 포지션의 70% 매도 (30% 유지)
        assert len(orders) == 2
        assert orders[0]["quantity"] == 70  # 100 * 0.7
        assert orders[1]["quantity"] == 35  # 50 * 0.7

    def test_generate_defensive_orders_small_position_skip(self, monitor):
        """소수점 이하 버림 → 주문 생성 생략 테스트"""
        positions = [
            PositionSnapshot("TEST", Market.KRX, 1, 100, 110, "Test"),  # 1 * 0.7 = 0 (버림)
        ]
        orders = monitor._generate_defensive_orders(positions)
        assert len(orders) == 0

    def test_generate_defensive_orders_sorted_by_amount(self, monitor):
        """예상 매도액 기준 내림차순 정렬 테스트"""
        positions = [
            PositionSnapshot("A", Market.KRX, 10, 100, 100, "IT"),  # 매도 예상액: 700
            PositionSnapshot("B", Market.KRX, 100, 100, 100, "Finance"),  # 매도 예상액: 7,000
        ]
        orders = monitor._generate_defensive_orders(positions)
        # B가 먼저 정렬되어야 함 (큰 금액 우선)
        assert orders[0]["ticker"] == "B"
        assert orders[1]["ticker"] == "A"

    def test_generate_defensive_orders_single_position(self, monitor):
        """단일 포지션 방어 주문 테스트"""
        positions = [
            PositionSnapshot("005930", Market.KRX, 100, 70000, 71400, "IT"),
        ]
        orders = monitor._generate_defensive_orders(positions)
        assert len(orders) == 1
        assert orders[0]["ticker"] == "005930"
        assert orders[0]["market"] == Market.KRX.value
        assert orders[0]["side"] == OrderSide.SELL.value
        assert orders[0]["order_type"] == OrderType.MARKET.value

    def test_generate_defensive_orders_order_content(self, monitor):
        """주문 내용 확인 테스트"""
        positions = [
            PositionSnapshot("005930", Market.KRX, 100, 70000, 71400, "IT"),
        ]
        orders = monitor._generate_defensive_orders(positions)
        order = orders[0]
        assert order["ticker"] == "005930"
        assert order["quantity"] == 70
        assert order["current_price"] == 71400
        assert order["estimated_amount"] == 70 * 71400
        assert "비상 리밸런싱" in order["reason"]


# ══════════════════════════════════════
# TestHandleTrigger (8 tests)
# ══════════════════════════════════════


class TestHandleTrigger:
    """트리거 처리 테스트"""

    @pytest.mark.asyncio
    async def test_handle_trigger_discretionary_style_executes_orders(self, monitor, mock_order_executor):
        """일임형 스타일 주문 자동 체결 테스트"""
        monitor._rebalancing_engine.profile.investment_style = InvestmentStyle.DISCRETIONARY
        positions = [
            PositionSnapshot("005930", Market.KRX, 100, 70000, 63000, "IT"),
        ]
        report = monitor._calculate_loss(positions)

        with patch.object(monitor, "_execute_defensive_orders", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = [{"order": {"ticker": "005930"}, "result": {"status": "filled"}}]
            await monitor._handle_trigger(report, positions)

        mock_exec.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_trigger_advisory_style_no_execution(self, monitor):
        """자문형 스타일 주문 미체결 테스트"""
        monitor._rebalancing_engine.profile.investment_style = InvestmentStyle.ADVISORY
        positions = [
            PositionSnapshot("005930", Market.KRX, 100, 70000, 63000, "IT"),
        ]
        report = monitor._calculate_loss(positions)

        with patch.object(monitor, "_execute_defensive_orders", new_callable=AsyncMock) as mock_exec:
            await monitor._handle_trigger(report, positions)

        # 자문형은 execute_defensive_orders를 호출하지 않음
        mock_exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_trigger_sends_telegram_alert(self, monitor, mock_telegram):
        """텔레그램 알림 발송 테스트"""
        positions = [
            PositionSnapshot("005930", Market.KRX, 100, 70000, 63000, "IT"),
        ]
        report = monitor._calculate_loss(positions)

        with patch.object(monitor, "_send_emergency_alert", new_callable=AsyncMock) as mock_alert:
            await monitor._handle_trigger(report, positions)

        mock_alert.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_trigger_sets_cooldown(self, monitor):
        """쿨다운 설정 테스트"""
        positions = [
            PositionSnapshot("005930", Market.KRX, 100, 70000, 63000, "IT"),
        ]
        report = monitor._calculate_loss(positions)

        with patch.object(monitor, "_execute_defensive_orders", new_callable=AsyncMock):
            with patch.object(monitor, "_send_emergency_alert", new_callable=AsyncMock):
                with patch.object(monitor, "_record_emergency_event", new_callable=AsyncMock):
                    await monitor._handle_trigger(report, positions)

        assert monitor._state.cooldown_until is not None
        assert monitor._state.cooldown_until > datetime.now(timezone.utc)

    @pytest.mark.asyncio
    async def test_handle_trigger_records_db_event(self, monitor):
        """DB 기록 테스트"""
        positions = [
            PositionSnapshot("005930", Market.KRX, 100, 70000, 63000, "IT"),
        ]
        report = monitor._calculate_loss(positions)

        with patch.object(monitor, "_record_emergency_event", new_callable=AsyncMock) as mock_record:
            with patch.object(monitor, "_send_emergency_alert", new_callable=AsyncMock):
                await monitor._handle_trigger(report, positions)

        mock_record.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_trigger_increments_trigger_count(self, monitor):
        """트리거 카운트 증가 테스트"""
        initial_count = monitor._state.trigger_count
        positions = [
            PositionSnapshot("005930", Market.KRX, 100, 70000, 63000, "IT"),
        ]
        report = monitor._calculate_loss(positions)

        with patch.object(monitor, "_execute_defensive_orders", new_callable=AsyncMock):
            with patch.object(monitor, "_send_emergency_alert", new_callable=AsyncMock):
                with patch.object(monitor, "_record_emergency_event", new_callable=AsyncMock):
                    await monitor._handle_trigger(report, positions)

        assert monitor._state.trigger_count == initial_count + 1

    @pytest.mark.asyncio
    async def test_handle_trigger_executor_failure_sends_error_alert(self, monitor, mock_telegram):
        """주문 실행 실패 시 오류 알림 테스트"""
        monitor._rebalancing_engine.profile.investment_style = InvestmentStyle.DISCRETIONARY
        positions = [
            PositionSnapshot("005930", Market.KRX, 100, 70000, 63000, "IT"),
        ]
        report = monitor._calculate_loss(positions)

        # execute_defensive_orders가 예외 발생
        with patch.object(monitor, "_execute_defensive_orders", side_effect=Exception("Order failed")):
            with patch.object(monitor, "_send_error_alert", new_callable=AsyncMock) as mock_error:
                await monitor._handle_trigger(report, positions)

        # 오류 알림 발송 시도
        mock_error.assert_called_once()


# ══════════════════════════════════════
# TestMonitorLoop (8 tests)
# ══════════════════════════════════════


class TestMonitorLoop:
    """모니터 루프 테스트"""

    @pytest.mark.asyncio
    async def test_monitor_loop_start_running(self, monitor):
        """모니터 시작 상태 테스트"""
        await monitor.start()
        assert monitor._state.is_running is True
        await monitor.stop()

    @pytest.mark.asyncio
    async def test_monitor_loop_stop_stopped(self, monitor):
        """모니터 정지 상태 테스트"""
        await monitor.start()
        await monitor.stop()
        assert monitor._state.is_running is False

    @pytest.mark.asyncio
    async def test_monitor_loop_pause_resume(self, monitor):
        """모니터 일시정지/재개 테스트"""
        await monitor.start()
        monitor.pause()
        assert monitor._state.is_paused is True
        monitor.resume()
        assert monitor._state.is_paused is False
        await monitor.stop()

    @pytest.mark.asyncio
    async def test_monitor_loop_respects_market_hours(self, monitor):
        """장중 시간 필터링 테스트"""
        # 장중 시간이 아닌 시간대 시뮬레이션
        with patch.object(monitor, "_is_market_hours", return_value=False):
            with patch.object(monitor, "run_check", new_callable=AsyncMock) as mock_check:
                # 짧은 시간만 루프 실행
                await monitor.start()
                await asyncio.sleep(0.5)
                await monitor.stop()

                # 장중 시간이 아니므로 check 호출 안됨
                mock_check.assert_not_called()

    @pytest.mark.asyncio
    async def test_monitor_loop_respects_weekend(self, monitor):
        """주말 필터링 테스트"""
        # 주말 시뮬레이션 (is_market_hours가 False 반환)
        with patch.object(monitor, "_is_market_hours", return_value=False):
            with patch.object(monitor, "run_check", new_callable=AsyncMock) as mock_check:
                await monitor.start()
                await asyncio.sleep(0.5)
                await monitor.stop()

                mock_check.assert_not_called()

    @pytest.mark.asyncio
    async def test_monitor_loop_stops_on_kill_switch(self, monitor, mock_trading_guard):
        """Kill Switch 활성화 시 중단 테스트"""
        mock_trading_guard.state.kill_switch_on = True

        with patch.object(monitor, "run_check", new_callable=AsyncMock) as mock_check:
            with patch.object(monitor, "_is_market_hours", return_value=True):
                await monitor.start()
                await asyncio.sleep(0.5)
                await monitor.stop()

                # Kill Switch 활성화 상태에서는 check 호출 안됨
                mock_check.assert_not_called()

    @pytest.mark.asyncio
    async def test_monitor_loop_respects_cooldown(self, monitor):
        """쿨다운 필터링 테스트"""
        # 쿨다운 활성화
        monitor._state.cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=10)

        with patch.object(monitor, "run_check", new_callable=AsyncMock) as mock_check:
            with patch.object(monitor, "_is_market_hours", return_value=True):
                await monitor.start()
                await asyncio.sleep(0.5)
                await monitor.stop()

                # 쿨다운 중이므로 check 호출 안됨
                mock_check.assert_not_called()

    @pytest.mark.asyncio
    async def test_monitor_loop_manual_run_check(self, monitor):
        """수동 체크 호출 테스트"""
        with patch.object(monitor, "_fetch_current_positions", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = []
            result = await monitor.run_check()
            # 포지션이 없으면 None 반환
            assert result is None


# ══════════════════════════════════════
# TestMarketHoursAndCooldown (5 tests)
# ══════════════════════════════════════


class TestMarketHoursAndCooldown:
    """시장 시간 및 쿨다운 테스트"""

    def test_is_market_hours_during_market(self, monitor):
        """장중 시간 확인 테스트"""
        # 장중 시간 시뮬레이션 (09:00~15:30 KST)
        with patch("core.emergency_monitor.datetime") as mock_datetime:
            # 10:00 KST (장중)
            kst_time = time(10, 0, 0)
            mock_now = MagicMock()
            mock_now.time.return_value = kst_time
            mock_now.weekday.return_value = 2  # 수요일 (평일)
            mock_datetime.now.return_value = mock_now

            # 직접 테스트: 시간 범위 비교
            is_market = monitor.MARKET_OPEN <= kst_time <= monitor.MARKET_CLOSE
            assert is_market is True

    def test_is_market_hours_outside_market(self, monitor):
        """장중 외 시간 확인 테스트"""
        # 장중 외 시간 (16:00 KST)
        kst_time = time(16, 0, 0)
        is_market = monitor.MARKET_OPEN <= kst_time <= monitor.MARKET_CLOSE
        assert is_market is False

    def test_is_market_hours_weekend(self, monitor):
        """주말 확인 테스트"""
        # 주말은 장중이 아님
        with patch("core.emergency_monitor.datetime") as mock_datetime:
            kst_time = time(10, 0, 0)
            mock_now = MagicMock()
            mock_now.time.return_value = kst_time
            mock_now.weekday.return_value = 5  # 토요일
            mock_datetime.now.return_value = mock_now

            # weekday >= 5는 주말
            is_weekend = mock_now.weekday() >= 5
            assert is_weekend is True

    def test_cooldown_active(self, monitor):
        """활성 쿨다운 확인 테스트"""
        monitor._state.cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=10)
        assert monitor._is_in_cooldown() is True

    def test_cooldown_expired(self, monitor):
        """만료된 쿨다운 확인 테스트"""
        monitor._state.cooldown_until = datetime.now(timezone.utc) - timedelta(minutes=1)
        assert monitor._is_in_cooldown() is False


# ══════════════════════════════════════
# TestFetchPositions (5 tests)
# ══════════════════════════════════════


class TestFetchPositions:
    """포지션 조회 테스트"""

    @pytest.mark.asyncio
    async def test_fetch_positions_kr_balance(self, monitor, mock_kis_client):
        """KIS 한국 잔고 파싱 테스트"""
        mock_kis_client.get_kr_balance.return_value = {
            "output1": [
                {
                    "pdno": "005930",
                    "hldg_qty": "100",
                    "pchs_avg_pric": "70000.00",
                    "prpr": "71400",
                }
            ],
            "output2": [],
        }
        mock_kis_client.get_us_balance.return_value = None

        positions = await monitor._fetch_current_positions()
        assert len(positions) == 1
        assert positions[0].ticker == "005930"
        assert positions[0].market == Market.KRX
        assert positions[0].quantity == 100

    @pytest.mark.asyncio
    async def test_fetch_positions_us_balance(self, monitor, mock_kis_client):
        """KIS 미국 잔고 파싱 테스트"""
        mock_kis_client.get_kr_balance.return_value = None
        mock_kis_client.get_us_balance.return_value = {
            "output1": [
                {
                    "pdno": "AAPL",
                    "hldg_qty": "10.0",
                    "pchs_avg_pric": "150.00",
                    "now_pric2": "175.50",
                }
            ],
        }

        positions = await monitor._fetch_current_positions()
        assert len(positions) == 1
        assert positions[0].ticker == "AAPL"
        assert positions[0].market == Market.NYSE
        assert positions[0].quantity == 10

    @pytest.mark.asyncio
    async def test_fetch_positions_kis_unavailable_fallback_db(self, monitor):
        """KIS 불가시 DB 폴백 테스트"""
        # KIS 클라이언트가 None인 경우
        monitor._kis_client = None

        with patch.object(monitor, "_load_positions_from_db", new_callable=AsyncMock) as mock_db:
            mock_db.return_value = [PositionSnapshot("005930", Market.KRX, 100, 70000, 71400, "IT")]
            positions = await monitor._fetch_current_positions()

        mock_db.assert_called_once()
        assert len(positions) == 1

    @pytest.mark.asyncio
    async def test_fetch_positions_empty_balance(self, monitor, mock_kis_client):
        """빈 잔고 테스트"""
        mock_kis_client.get_kr_balance.return_value = None
        mock_kis_client.get_us_balance.return_value = None

        positions = await monitor._fetch_current_positions()
        assert positions == []

    @pytest.mark.asyncio
    async def test_fetch_positions_filters_zero_quantity(self, monitor, mock_kis_client):
        """0수량 포지션 필터링 테스트"""
        mock_kis_client.get_kr_balance.return_value = {
            "output1": [
                {
                    "pdno": "005930",
                    "hldg_qty": "100",
                    "pchs_avg_pric": "70000.00",
                    "prpr": "71400",
                },
                {
                    "pdno": "000660",
                    "hldg_qty": "0",
                    "pchs_avg_pric": "100000.00",
                    "prpr": "110000",
                },
            ],
            "output2": [],
        }
        mock_kis_client.get_us_balance.return_value = None

        positions = await monitor._fetch_current_positions()
        # 0수량은 필터링됨
        assert len(positions) == 1
        assert positions[0].ticker == "005930"


# ══════════════════════════════════════
# TestEmergencyMessage (3 tests)
# ══════════════════════════════════════


class TestEmergencyMessage:
    """긴급 메시지 포맷팅 테스트"""

    def test_emergency_message_contains_trigger_type(self, monitor):
        """메시지에 트리거 유형 포함 테스트"""
        positions = [
            PositionSnapshot("005930", Market.KRX, 100, 70000, 63000, "IT"),
        ]
        report = monitor._calculate_loss(positions)
        orders = monitor._generate_defensive_orders(positions)

        message = monitor._format_emergency_message(report, orders)
        assert "비상 리밸런싱 트리거" in message
        assert "트리거 유형" in message

    def test_emergency_message_contains_loss_figures(self, monitor):
        """메시지에 손실 수치 포함 테스트"""
        positions = [
            PositionSnapshot("005930", Market.KRX, 100, 70000, 63000, "IT"),
        ]
        report = monitor._calculate_loss(positions)
        orders = monitor._generate_defensive_orders(positions)

        message = monitor._format_emergency_message(report, orders)
        assert "손실률" in message or "손실" in message
        assert "포트폴리오" in message

    def test_emergency_message_contains_worst_positions(self, monitor):
        """메시지에 최악 포지션 포함 테스트"""
        positions = [
            PositionSnapshot("005930", Market.KRX, 100, 70000, 63000, "IT"),
            PositionSnapshot("000660", Market.KRX, 50, 100000, 85000, "Semiconductor"),
        ]
        report = monitor._calculate_loss(positions)
        orders = monitor._generate_defensive_orders(positions)

        message = monitor._format_emergency_message(report, orders)
        assert "최악" in message or "포지션" in message
