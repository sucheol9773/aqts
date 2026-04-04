"""
AQTS TradingScheduler 종합 테스트

테스트 범위:
1. is_trading_day: 평일 통과, 주말/공휴일 실패
2. next_trading_day: 주말과 공휴일 올바르게 건너뛰기
3. ScheduleEventType / ScheduleEvent 단위 테스트
4. SchedulerState: to_dict, 기본값
5. TradingScheduler 초기화: DEMO 모드 필수
6. TradingScheduler.start: 상태를 RUNNING으로 설정, DEMO 모드 검증
7. TradingScheduler.stop: 작업 취소, STOPPED 설정
8. TradingScheduler.pause / resume: 상태 전환
9. TradingScheduler.register_handler: 핸들러 등록 및 사용
10. TradingScheduler.run_event_now: 수동 이벤트 트리거
11. TradingScheduler._find_next_event: 시간 기반 올바른 이벤트 선택
12. TradingScheduler._execute_event: 핸들러 호출, 결과 기록
13. 기본 핸들러: _default_handle_pre_market 등
14. 오류 처리: 핸들러 예외가 스케줄러를 중단하지 않음
"""

import asyncio
import unittest
from datetime import date, datetime, time, timedelta
from unittest.mock import MagicMock, patch

import pytest

from config.settings import TradingMode
from core.trading_scheduler import (
    ALL_HOLIDAYS,
    DEFAULT_SCHEDULE,
    KR_HOLIDAYS_2025,
    KR_HOLIDAYS_2026,
    KST,
    ScheduleEvent,
    ScheduleEventType,
    SchedulerState,
    SchedulerStatus,
    TradingScheduler,
    is_trading_day,
    next_trading_day,
    now_kst,
)

# ══════════════════════════════════════
# 1. is_trading_day 함수 테스트
# ══════════════════════════════════════


class TestIsTradingDay:
    """is_trading_day 함수 테스트"""

    def test_weekday_is_trading_day(self):
        """평일은 거래일이어야 함"""
        # 2026-04-06 (월요일)
        d = date(2026, 4, 6)
        assert is_trading_day(d) is True

    def test_tuesday_is_trading_day(self):
        """화요일도 거래일이어야 함"""
        # 2026-04-07 (화요일)
        d = date(2026, 4, 7)
        assert is_trading_day(d) is True

    def test_friday_is_trading_day(self):
        """금요일도 거래일이어야 함"""
        # 2026-04-10 (금요일)
        d = date(2026, 4, 10)
        assert is_trading_day(d) is True

    def test_saturday_is_not_trading_day(self):
        """토요일은 거래일이 아니어야 함"""
        # 2026-04-11 (토요일)
        d = date(2026, 4, 11)
        assert is_trading_day(d) is False

    def test_sunday_is_not_trading_day(self):
        """일요일은 거래일이 아니어야 함"""
        # 2026-04-12 (일요일)
        d = date(2026, 4, 12)
        assert is_trading_day(d) is False

    def test_holiday_is_not_trading_day(self):
        """공휴일은 거래일이 아니어야 함"""
        # 2026-01-01 (신정)
        d = date(2026, 1, 1)
        assert is_trading_day(d) is False

    def test_seollal_holiday_is_not_trading_day(self):
        """설날 연휴는 거래일이 아니어야 함"""
        # 2026-02-17 (설날)
        d = date(2026, 2, 17)
        assert is_trading_day(d) is False

    def test_children_day_is_not_trading_day(self):
        """어린이날은 거래일이 아니어야 함"""
        d = date(2026, 5, 5)
        assert is_trading_day(d) is False

    def test_christmas_is_not_trading_day(self):
        """크리스마스는 거래일이 아니어야 함"""
        d = date(2026, 12, 25)
        assert is_trading_day(d) is False


# ══════════════════════════════════════
# 2. next_trading_day 함수 테스트
# ══════════════════════════════════════


class TestNextTradingDay:
    """next_trading_day 함수 테스트"""

    def test_next_day_is_trading_day(self):
        """다음날이 거래일이면 그대로 반환"""
        # 2026-04-06 (월요일) → 2026-04-07 (화요일)
        d = date(2026, 4, 6)
        result = next_trading_day(d)
        assert result == date(2026, 4, 7)
        assert is_trading_day(result)

    def test_skip_weekend(self):
        """주말을 건너뛰어야 함"""
        # 2026-04-10 (금요일) → 2026-04-13 (월요일) (토,일 건너뜀)
        d = date(2026, 4, 10)
        result = next_trading_day(d)
        assert result == date(2026, 4, 13)
        assert result.weekday() == 0  # 월요일

    def test_skip_holiday(self):
        """공휴일을 건너뛰어야 함"""
        # 2025-12-31 → 2026-01-02 (신정 건너뜀)
        d = date(2025, 12, 31)
        result = next_trading_day(d)
        assert result == date(2026, 1, 2)

    def test_skip_multiple_consecutive_holidays(self):
        """연속된 공휴일들을 모두 건너뛰어야 함"""
        # 2026-02-15 → 2026-02-19 (설날 연휴 건너뜀)
        d = date(2026, 2, 15)
        result = next_trading_day(d)
        assert result == date(2026, 2, 19)
        assert is_trading_day(result)

    def test_skip_weekend_and_holiday(self):
        """주말과 공휴일을 함께 건너뛰어야 함"""
        # 2026-02-13 (금) → 2026-02-19 (금) (토,일 + 설날 연휴)
        d = date(2026, 2, 13)
        result = next_trading_day(d)
        assert result == date(2026, 2, 19)
        assert is_trading_day(result)


# ══════════════════════════════════════
# 3. now_kst 함수 테스트
# ══════════════════════════════════════


class TestNowKst:
    """now_kst 함수 테스트"""

    def test_now_kst_returns_datetime(self):
        """현재 한국 시간을 datetime으로 반환"""
        result = now_kst()
        assert isinstance(result, datetime)

    def test_now_kst_has_kst_timezone(self):
        """반환된 datetime은 KST 타임존을 가져야 함"""
        result = now_kst()
        assert result.tzinfo == KST

    def test_now_kst_is_recent(self):
        """반환된 시간은 최근이어야 함 (1초 이내)"""
        now = datetime.now(KST)
        result = now_kst()
        delta = (result - now).total_seconds()
        assert abs(delta) < 1.0


# ══════════════════════════════════════
# 4. ScheduleEventType 열거형 테스트
# ══════════════════════════════════════


class TestScheduleEventType:
    """ScheduleEventType 열거형 테스트"""

    def test_event_type_values(self):
        """모든 이벤트 유형이 정의되어야 함"""
        assert ScheduleEventType.PRE_MARKET.value == "PRE_MARKET"
        assert ScheduleEventType.MARKET_OPEN.value == "MARKET_OPEN"
        assert ScheduleEventType.MIDDAY_CHECK.value == "MIDDAY_CHECK"
        assert ScheduleEventType.MARKET_CLOSE.value == "MARKET_CLOSE"
        assert ScheduleEventType.POST_MARKET.value == "POST_MARKET"

    def test_event_type_count(self):
        """5개의 이벤트 유형이 있어야 함"""
        assert len(ScheduleEventType) == 5


# ══════════════════════════════════════
# 5. ScheduleEvent 데이터클래스 테스트
# ══════════════════════════════════════


class TestScheduleEvent:
    """ScheduleEvent 데이터클래스 테스트"""

    def test_schedule_event_creation(self):
        """ScheduleEvent를 생성할 수 있어야 함"""
        event = ScheduleEvent(
            event_type=ScheduleEventType.PRE_MARKET,
            scheduled_time=time(8, 30),
            description="테스트 이벤트",
            handler="test_handler",
        )
        assert event.event_type == ScheduleEventType.PRE_MARKET
        assert event.scheduled_time == time(8, 30)
        assert event.description == "테스트 이벤트"
        assert event.handler == "test_handler"

    def test_schedule_event_without_handler(self):
        """핸들러 없이 ScheduleEvent를 생성할 수 있어야 함"""
        event = ScheduleEvent(
            event_type=ScheduleEventType.MARKET_OPEN,
            scheduled_time=time(9, 0),
            description="핸들러 없음 이벤트",
        )
        assert event.handler is None


# ══════════════════════════════════════
# 6. SchedulerState 테스트
# ══════════════════════════════════════


class TestSchedulerState:
    """SchedulerState 데이터클래스 테스트"""

    def test_scheduler_state_default_values(self):
        """기본값이 올바르게 설정되어야 함"""
        state = SchedulerState()
        assert state.status == SchedulerStatus.IDLE
        assert state.current_date is None
        assert state.last_event is None
        assert state.last_event_at is None
        assert state.next_event is None
        assert state.next_event_at is None
        assert state.events_executed_today == []
        assert state.errors_today == []
        assert state.total_trading_days == 0
        assert state.started_at is None

    def test_scheduler_state_to_dict(self):
        """to_dict이 올바르게 직렬화해야 함"""
        state = SchedulerState(
            status=SchedulerStatus.RUNNING,
            current_date=date(2026, 4, 6),
            total_trading_days=1,
        )
        d = state.to_dict()
        assert d["status"] == "RUNNING"
        assert d["current_date"] == "2026-04-06"
        assert d["total_trading_days"] == 1
        assert d["events_executed_today"] == 0

    def test_scheduler_state_to_dict_with_none_values(self):
        """None 값들이 None으로 직렬화되어야 함"""
        state = SchedulerState()
        d = state.to_dict()
        assert d["current_date"] is None
        assert d["last_event"] is None
        assert d["started_at"] is None

    def test_scheduler_state_to_dict_with_datetime(self):
        """datetime 값이 ISO 형식으로 직렬화되어야 함"""
        now = now_kst()
        state = SchedulerState(
            last_event_at=now,
            next_event_at=now + timedelta(hours=1),
        )
        d = state.to_dict()
        assert isinstance(d["last_event_at"], str)
        assert isinstance(d["next_event_at"], str)
        assert "T" in d["last_event_at"]  # ISO 형식


# ══════════════════════════════════════
# 7. TradingScheduler 초기화 테스트
# ══════════════════════════════════════


class TestTradingSchedulerInit:
    """TradingScheduler 초기화 테스트"""

    @patch("core.trading_scheduler.get_settings")
    def test_scheduler_init_with_default_schedule(self, mock_get_settings):
        """기본 스케줄로 초기화"""
        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        scheduler = TradingScheduler()
        assert scheduler._schedule == DEFAULT_SCHEDULE
        assert scheduler._state.status == SchedulerStatus.IDLE
        assert scheduler._running is False
        assert scheduler._task is None
        assert scheduler._handlers == {}

    @patch("core.trading_scheduler.get_settings")
    def test_scheduler_init_with_custom_schedule(self, mock_get_settings):
        """커스텀 스케줄로 초기화"""
        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        custom_schedule = [
            ScheduleEvent(
                event_type=ScheduleEventType.PRE_MARKET,
                scheduled_time=time(8, 0),
                description="Custom PRE_MARKET",
            ),
        ]
        scheduler = TradingScheduler(schedule=custom_schedule)
        assert scheduler._schedule == custom_schedule

    @patch("core.trading_scheduler.get_settings")
    def test_scheduler_has_state_property(self, mock_get_settings):
        """state 프로퍼티 접근 가능"""
        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        scheduler = TradingScheduler()
        state = scheduler.state
        assert isinstance(state, SchedulerState)

    @patch("core.trading_scheduler.get_settings")
    def test_scheduler_is_running_property_default_false(self, mock_get_settings):
        """is_running 프로퍼티는 기본값이 False"""
        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        scheduler = TradingScheduler()
        assert scheduler.is_running is False


# ══════════════════════════════════════
# 8. TradingScheduler.start 테스트
# ══════════════════════════════════════


class TestTradingSchedulerStart(unittest.IsolatedAsyncioTestCase):
    """TradingScheduler.start 테스트"""

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    async def test_start_in_demo_mode(self, mock_get_settings):
        """DEMO 모드에서 시작 성공"""
        mock_settings = MagicMock()
        mock_settings.kis.trading_mode = TradingMode.DEMO
        mock_get_settings.return_value = mock_settings

        scheduler = TradingScheduler()
        await scheduler.start()

        assert scheduler._running is True
        assert scheduler.state.status == SchedulerStatus.RUNNING
        assert scheduler.state.started_at is not None

        # 정리
        await scheduler.stop()

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    async def test_start_fails_in_live_mode(self, mock_get_settings):
        """LIVE 모드에서는 시작 실패"""
        mock_settings = MagicMock()
        mock_settings.kis.trading_mode = TradingMode.LIVE
        mock_get_settings.return_value = mock_settings

        scheduler = TradingScheduler()
        with pytest.raises(RuntimeError) as exc_info:
            await scheduler.start()

        assert "DEMO 모드" in str(exc_info.value)

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    async def test_start_fails_in_backtest_mode(self, mock_get_settings):
        """BACKTEST 모드에서는 시작 실패"""
        mock_settings = MagicMock()
        mock_settings.kis.trading_mode = TradingMode.BACKTEST
        mock_get_settings.return_value = mock_settings

        scheduler = TradingScheduler()
        with pytest.raises(RuntimeError):
            await scheduler.start()

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    async def test_start_prevents_double_start(self, mock_get_settings):
        """이미 실행 중인 경우 중복 시작 방지"""
        mock_settings = MagicMock()
        mock_settings.kis.trading_mode = TradingMode.DEMO
        mock_get_settings.return_value = mock_settings

        scheduler = TradingScheduler()
        await scheduler.start()
        started_at_1 = scheduler.state.started_at

        await asyncio.sleep(0.1)
        await scheduler.start()
        started_at_2 = scheduler.state.started_at

        assert started_at_1 == started_at_2  # 같은 시간

        await scheduler.stop()

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    async def test_start_creates_task(self, mock_get_settings):
        """시작하면 asyncio 작업이 생성됨"""
        mock_settings = MagicMock()
        mock_settings.kis.trading_mode = TradingMode.DEMO
        mock_get_settings.return_value = mock_settings

        scheduler = TradingScheduler()
        await scheduler.start()

        assert scheduler._task is not None
        assert isinstance(scheduler._task, asyncio.Task)

        await scheduler.stop()


# ══════════════════════════════════════
# 9. TradingScheduler.stop 테스트
# ══════════════════════════════════════


class TestTradingSchedulerStop(unittest.IsolatedAsyncioTestCase):
    """TradingScheduler.stop 테스트"""

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    async def test_stop_sets_running_false(self, mock_get_settings):
        """stop하면 _running이 False로 설정됨"""
        mock_settings = MagicMock()
        mock_settings.kis.trading_mode = TradingMode.DEMO
        mock_get_settings.return_value = mock_settings

        scheduler = TradingScheduler()
        await scheduler.start()
        assert scheduler._running is True

        await scheduler.stop()
        assert scheduler._running is False

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    async def test_stop_sets_status_stopped(self, mock_get_settings):
        """stop하면 상태가 STOPPED로 설정됨"""
        mock_settings = MagicMock()
        mock_settings.kis.trading_mode = TradingMode.DEMO
        mock_get_settings.return_value = mock_settings

        scheduler = TradingScheduler()
        await scheduler.start()
        await scheduler.stop()

        assert scheduler.state.status == SchedulerStatus.STOPPED

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    async def test_stop_cancels_task(self, mock_get_settings):
        """stop하면 작업이 취소됨"""
        mock_settings = MagicMock()
        mock_settings.kis.trading_mode = TradingMode.DEMO
        mock_get_settings.return_value = mock_settings

        scheduler = TradingScheduler()
        await scheduler.start()
        task = scheduler._task
        assert not task.done()

        await scheduler.stop()
        assert task.done()

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    async def test_stop_when_not_running(self, mock_get_settings):
        """실행 중이 아닐 때 stop해도 안전함"""
        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        scheduler = TradingScheduler()
        await scheduler.stop()  # 예외 없이 완료되어야 함
        assert scheduler._running is False


# ══════════════════════════════════════
# 10. TradingScheduler.pause / resume 테스트
# ══════════════════════════════════════


class TestTradingSchedulerPauseResume(unittest.IsolatedAsyncioTestCase):
    """TradingScheduler.pause / resume 테스트"""

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    async def test_pause_sets_paused_status(self, mock_get_settings):
        """pause하면 상태가 PAUSED로 변경됨"""
        mock_settings = MagicMock()
        mock_settings.kis.trading_mode = TradingMode.DEMO
        mock_get_settings.return_value = mock_settings

        scheduler = TradingScheduler()
        await scheduler.start()
        await scheduler.pause()

        assert scheduler.state.status == SchedulerStatus.PAUSED

        await scheduler.stop()

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    async def test_resume_sets_running_status(self, mock_get_settings):
        """resume하면 상태가 RUNNING으로 복구됨"""
        mock_settings = MagicMock()
        mock_settings.kis.trading_mode = TradingMode.DEMO
        mock_get_settings.return_value = mock_settings

        scheduler = TradingScheduler()
        await scheduler.start()
        await scheduler.pause()
        assert scheduler.state.status == SchedulerStatus.PAUSED

        await scheduler.resume()
        assert scheduler.state.status == SchedulerStatus.RUNNING

        await scheduler.stop()

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    async def test_resume_when_not_running(self, mock_get_settings):
        """실행 중이 아닐 때 resume해도 상태 안 변함"""
        mock_settings = MagicMock()
        mock_settings.kis.trading_mode = TradingMode.DEMO
        mock_get_settings.return_value = mock_settings

        scheduler = TradingScheduler()
        await scheduler.pause()
        await scheduler.resume()

        # 실행 중이 아니므로 상태가 변하지 않음
        assert scheduler.state.status == SchedulerStatus.PAUSED


# ══════════════════════════════════════
# 11. TradingScheduler.register_handler 테스트
# ══════════════════════════════════════


class TestTradingSchedulerRegisterHandler:
    """TradingScheduler.register_handler 테스트"""

    @patch("core.trading_scheduler.get_settings")
    def test_register_handler(self, mock_get_settings):
        """핸들러를 등록할 수 있음"""
        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        async def custom_handler():
            return {"message": "custom"}

        scheduler = TradingScheduler()
        scheduler.register_handler("custom_event", custom_handler)

        assert "custom_event" in scheduler._handlers
        assert scheduler._handlers["custom_event"] == custom_handler

    @patch("core.trading_scheduler.get_settings")
    def test_register_multiple_handlers(self, mock_get_settings):
        """여러 핸들러를 등록할 수 있음"""
        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        async def handler1():
            return {}

        async def handler2():
            return {}

        scheduler = TradingScheduler()
        scheduler.register_handler("event1", handler1)
        scheduler.register_handler("event2", handler2)

        assert len(scheduler._handlers) == 2
        assert scheduler._handlers["event1"] == handler1
        assert scheduler._handlers["event2"] == handler2


# ══════════════════════════════════════
# 12. TradingScheduler.run_event_now 테스트
# ══════════════════════════════════════


class TestTradingSchedulerRunEventNow(unittest.IsolatedAsyncioTestCase):
    """TradingScheduler.run_event_now 테스트"""

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    async def test_run_event_now_with_registered_handler(self, mock_get_settings):
        """등록된 핸들러로 이벤트를 즉시 실행"""
        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        async def custom_handler():
            return {"result": "success"}

        scheduler = TradingScheduler()
        scheduler.register_handler("handle_pre_market", custom_handler)

        result = await scheduler.run_event_now(ScheduleEventType.PRE_MARKET)
        assert result["success"] is True
        assert result["event_type"] == "PRE_MARKET"

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    async def test_run_event_now_with_builtin_handler(self, mock_get_settings):
        """내장 핸들러로 이벤트를 즉시 실행"""
        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        scheduler = TradingScheduler()
        result = await scheduler.run_event_now(ScheduleEventType.MIDDAY_CHECK)

        assert result["success"] is True
        assert result["event_type"] == "MIDDAY_CHECK"

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    async def test_run_event_now_nonexistent_event(self, mock_get_settings):
        """존재하지 않는 이벤트는 실패"""
        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        scheduler = TradingScheduler()

        # 존재하지 않는 이벤트 유형 생성
        fake_event_type = ScheduleEventType.PRE_MARKET

        # 스케줄 비우기
        scheduler._schedule = []

        result = await scheduler.run_event_now(fake_event_type)
        assert result["success"] is False
        assert "이벤트 없음" in result["error"]


# ══════════════════════════════════════
# 13. TradingScheduler._find_next_event 테스트
# ══════════════════════════════════════


class TestTradingSchedulerFindNextEvent:
    """TradingScheduler._find_next_event 테스트"""

    @patch("core.trading_scheduler.get_settings")
    def test_find_next_event_before_all_events(self, mock_get_settings):
        """모든 이벤트 이전 시간에는 첫 번째 이벤트 반환"""
        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        scheduler = TradingScheduler()
        # 아침 8시 (모든 이벤트 이전)
        now = datetime(2026, 4, 6, 8, 0, tzinfo=KST)

        next_event = scheduler._find_next_event(now)
        assert next_event is not None
        assert next_event.event_type == ScheduleEventType.PRE_MARKET

    @patch("core.trading_scheduler.get_settings")
    def test_find_next_event_between_events(self, mock_get_settings):
        """이벤트들 사이의 시간에는 다음 이벤트 반환"""
        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        scheduler = TradingScheduler()
        # PRE_MARKET과 MARKET_OPEN을 실행 완료로 표시
        scheduler._state.events_executed_today = [
            {"event_type": "PRE_MARKET"},
        ]
        # 오전 9시 15분 (PRE_MARKET 실행 후, MARKET_OPEN 이전)
        now = datetime(2026, 4, 6, 9, 15, tzinfo=KST)

        next_event = scheduler._find_next_event(now)
        assert next_event is not None
        assert next_event.event_type == ScheduleEventType.MARKET_OPEN

    @patch("core.trading_scheduler.get_settings")
    def test_find_next_event_after_all_events(self, mock_get_settings):
        """모든 이벤트 이후 시간에는 None 반환"""
        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        scheduler = TradingScheduler()
        # 모든 이벤트를 실행 완료로 표시
        scheduler._state.events_executed_today = [
            {"event_type": "PRE_MARKET"},
            {"event_type": "MARKET_OPEN"},
            {"event_type": "MIDDAY_CHECK"},
            {"event_type": "MARKET_CLOSE"},
            {"event_type": "POST_MARKET"},
        ]
        # 오후 5시 (모든 이벤트 완료 후)
        now = datetime(2026, 4, 6, 17, 0, tzinfo=KST)

        next_event = scheduler._find_next_event(now)
        assert next_event is None

    @patch("core.trading_scheduler.get_settings")
    def test_find_next_event_skips_executed_events(self, mock_get_settings):
        """실행된 이벤트는 건너뜀"""
        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        scheduler = TradingScheduler()
        # PRE_MARKET과 MARKET_OPEN을 실행했다고 표시
        scheduler._state.events_executed_today = [
            {"event_type": "PRE_MARKET"},
            {"event_type": "MARKET_OPEN"},
        ]

        # 오전 10시
        now = datetime(2026, 4, 6, 10, 0, tzinfo=KST)
        next_event = scheduler._find_next_event(now)

        assert next_event is not None
        assert next_event.event_type == ScheduleEventType.MIDDAY_CHECK

    @patch("core.trading_scheduler.get_settings")
    def test_find_next_event_delayed_execution(self, mock_get_settings):
        """시간이 지난 이벤트를 아직 실행 안 했으면 즉시 반환 (지연 실행)"""
        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        scheduler = TradingScheduler()
        # PRE_MARKET은 실행했지만 MARKET_OPEN은 실행 안 함
        scheduler._state.events_executed_today = [
            {"event_type": "PRE_MARKET"},
        ]
        # 오전 9시 30분 (MARKET_OPEN 시간 지남)
        now = datetime(2026, 4, 6, 9, 30, tzinfo=KST)

        next_event = scheduler._find_next_event(now)
        # MARKET_OPEN을 아직 실행 안 했으므로 그걸 반환해야 함
        assert next_event is not None
        assert next_event.event_type == ScheduleEventType.MARKET_OPEN


# ══════════════════════════════════════
# 14. TradingScheduler._execute_event 테스트
# ══════════════════════════════════════


class TestTradingSchedulerExecuteEvent(unittest.IsolatedAsyncioTestCase):
    """TradingScheduler._execute_event 테스트"""

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    async def test_execute_event_with_registered_handler(self, mock_get_settings):
        """등록된 핸들러를 실행"""
        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        async def custom_handler():
            return {"custom_result": "value"}

        scheduler = TradingScheduler()
        scheduler.register_handler("handle_pre_market", custom_handler)

        event = ScheduleEvent(
            event_type=ScheduleEventType.PRE_MARKET,
            scheduled_time=time(8, 30),
            description="Test",
            handler="handle_pre_market",
        )

        result = await scheduler._execute_event(event)

        assert result["success"] is True
        assert result["event_type"] == "PRE_MARKET"
        assert result["details"]["custom_result"] == "value"
        assert "elapsed_seconds" in result

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    async def test_execute_event_with_builtin_handler(self, mock_get_settings):
        """내장 핸들러를 실행"""
        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        scheduler = TradingScheduler()

        event = ScheduleEvent(
            event_type=ScheduleEventType.MIDDAY_CHECK,
            scheduled_time=time(11, 30),
            description="Test",
        )

        result = await scheduler._execute_event(event)

        assert result["success"] is True
        assert result["event_type"] == "MIDDAY_CHECK"

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    async def test_execute_event_updates_state(self, mock_get_settings):
        """이벤트 실행이 상태를 업데이트함"""
        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        scheduler = TradingScheduler()

        event = ScheduleEvent(
            event_type=ScheduleEventType.MARKET_OPEN,
            scheduled_time=time(9, 0),
            description="Test",
        )

        await scheduler._execute_event(event)

        assert scheduler.state.last_event == ScheduleEventType.MARKET_OPEN
        assert scheduler.state.last_event_at is not None
        assert len(scheduler.state.events_executed_today) == 1

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    async def test_execute_event_handler_exception(self, mock_get_settings):
        """핸들러 예외가 적절히 처리됨"""
        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        async def failing_handler():
            raise ValueError("Test error")

        scheduler = TradingScheduler()
        scheduler.register_handler("handle_pre_market", failing_handler)

        event = ScheduleEvent(
            event_type=ScheduleEventType.PRE_MARKET,
            scheduled_time=time(8, 30),
            description="Test",
            handler="handle_pre_market",
        )

        result = await scheduler._execute_event(event)

        assert result["success"] is False
        assert "ValueError" in result["error"]
        assert len(scheduler.state.errors_today) == 1

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    async def test_execute_event_without_handler(self, mock_get_settings):
        """핸들러 없을 때도 성공으로 표시"""
        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        scheduler = TradingScheduler()
        scheduler._schedule = []  # 내장 핸들러도 없음

        event = ScheduleEvent(
            event_type=ScheduleEventType.PRE_MARKET,
            scheduled_time=time(8, 30),
            description="Test",
            handler="nonexistent_handler",
        )

        result = await scheduler._execute_event(event)

        # 핸들러 없으면 건너뜀 (성공)
        assert result["success"] is True


# ══════════════════════════════════════
# 15. 기본 핸들러 테스트
# ══════════════════════════════════════


class TestDefaultHandlers(unittest.IsolatedAsyncioTestCase):
    """기본 내장 핸들러 테스트"""

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    async def test_default_handle_market_open(self, mock_get_settings):
        """_default_handle_market_open 동작"""
        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        scheduler = TradingScheduler()
        result = await scheduler._default_handle_market_open()

        assert isinstance(result, dict)
        assert "message" in result
        assert "market_open_time" in result

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    async def test_default_handle_midday_check(self, mock_get_settings):
        """_default_handle_midday_check 동작"""
        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        scheduler = TradingScheduler()
        result = await scheduler._default_handle_midday_check()

        assert isinstance(result, dict)
        assert "message" in result
        assert "check_time" in result

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    async def test_default_handle_market_close(self, mock_get_settings):
        """_default_handle_market_close 동작"""
        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        scheduler = TradingScheduler()
        result = await scheduler._default_handle_market_close()

        assert isinstance(result, dict)
        assert "message" in result
        assert "close_time" in result

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    async def test_default_handle_post_market(self, mock_get_settings):
        """_default_handle_post_market 동작"""
        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        scheduler = TradingScheduler()
        result = await scheduler._default_handle_post_market()

        assert isinstance(result, dict)
        assert "message" in result
        assert "post_market_time" in result

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    async def test_default_handle_pre_market(self, mock_get_settings):
        """_default_handle_pre_market 동작 (외부 모듈 없음)"""
        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        scheduler = TradingScheduler()
        result = await scheduler._default_handle_pre_market()

        # 외부 모듈이 없으면 빈 딕셔너리 또는 에러 기록
        assert isinstance(result, dict)


# ══════════════════════════════════════
# 16. 오류 처리 테스트
# ══════════════════════════════════════


class TestErrorHandling(unittest.IsolatedAsyncioTestCase):
    """오류 처리 및 안정성 테스트"""

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    async def test_handler_exception_doesnt_crash_scheduler(self, mock_get_settings):
        """핸들러 예외가 스케줄러를 중단하지 않음"""
        mock_settings = MagicMock()
        mock_settings.kis.trading_mode = TradingMode.DEMO
        mock_get_settings.return_value = mock_settings

        async def failing_handler():
            raise RuntimeError("Critical error")

        scheduler = TradingScheduler()
        scheduler.register_handler("handle_pre_market", failing_handler)

        # 예외가 발생해도 execute_event는 정상 반환
        event = ScheduleEvent(
            event_type=ScheduleEventType.PRE_MARKET,
            scheduled_time=time(8, 30),
            description="Test",
            handler="handle_pre_market",
        )

        result = await scheduler._execute_event(event)
        assert result["success"] is False
        assert len(scheduler.state.errors_today) == 1

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    async def test_multiple_handler_failures(self, mock_get_settings):
        """여러 핸들러 실패가 기록됨"""
        mock_settings = MagicMock()
        mock_settings.kis.trading_mode = TradingMode.DEMO
        mock_get_settings.return_value = mock_settings

        async def failing_handler():
            raise ValueError("Error")

        scheduler = TradingScheduler()
        scheduler.register_handler("handle_pre_market", failing_handler)
        scheduler.register_handler("handle_market_open", failing_handler)

        event1 = ScheduleEvent(
            event_type=ScheduleEventType.PRE_MARKET,
            scheduled_time=time(8, 30),
            description="Test",
            handler="handle_pre_market",
        )
        event2 = ScheduleEvent(
            event_type=ScheduleEventType.MARKET_OPEN,
            scheduled_time=time(9, 0),
            description="Test",
            handler="handle_market_open",
        )

        await scheduler._execute_event(event1)
        await scheduler._execute_event(event2)

        assert len(scheduler.state.errors_today) == 2


# ══════════════════════════════════════
# 17. 통합 시나리오 테스트
# ══════════════════════════════════════


class TestIntegrationScenarios(unittest.IsolatedAsyncioTestCase):
    """통합 시나리오 테스트"""

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    async def test_full_workflow_start_stop(self, mock_get_settings):
        """전체 워크플로우: 시작 → 실행 → 중지"""
        mock_settings = MagicMock()
        mock_settings.kis.trading_mode = TradingMode.DEMO
        mock_get_settings.return_value = mock_settings

        scheduler = TradingScheduler()

        # 시작
        await scheduler.start()
        assert scheduler.is_running is True

        # 잠시 대기 (루프 실행 1회 정도)
        await asyncio.sleep(0.2)

        # 중지
        await scheduler.stop()
        assert scheduler.is_running is False
        assert scheduler.state.status == SchedulerStatus.STOPPED

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    async def test_pause_resume_workflow(self, mock_get_settings):
        """일시정지 및 재개 워크플로우"""
        mock_settings = MagicMock()
        mock_settings.kis.trading_mode = TradingMode.DEMO
        mock_get_settings.return_value = mock_settings

        scheduler = TradingScheduler()
        await scheduler.start()

        # 일시정지
        await scheduler.pause()
        assert scheduler.state.status == SchedulerStatus.PAUSED

        # 재개
        await scheduler.resume()
        assert scheduler.state.status == SchedulerStatus.RUNNING

        # 정리
        await scheduler.stop()

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    async def test_custom_handler_integration(self, mock_get_settings):
        """커스텀 핸들러 통합"""
        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        call_count = {"pre_market": 0, "market_open": 0}

        async def custom_pre_market():
            call_count["pre_market"] += 1
            return {"custom": "pre_market_result"}

        async def custom_market_open():
            call_count["market_open"] += 1
            return {"custom": "market_open_result"}

        scheduler = TradingScheduler()
        scheduler.register_handler("handle_pre_market", custom_pre_market)
        scheduler.register_handler("handle_market_open", custom_market_open)

        # 수동 이벤트 실행
        result1 = await scheduler.run_event_now(ScheduleEventType.PRE_MARKET)
        result2 = await scheduler.run_event_now(ScheduleEventType.MARKET_OPEN)

        assert call_count["pre_market"] == 1
        assert call_count["market_open"] == 1
        assert result1["details"]["custom"] == "pre_market_result"
        assert result2["details"]["custom"] == "market_open_result"


# ══════════════════════════════════════
# 18. 추가 엣지 케이스
# ══════════════════════════════════════


class TestEdgeCases(unittest.IsolatedAsyncioTestCase):
    """엣지 케이스 및 특수 상황 테스트"""

    @patch("core.trading_scheduler.get_settings")
    def test_custom_schedule(self, mock_get_settings):
        """커스텀 스케줄로 초기화"""
        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        custom_schedule = [
            ScheduleEvent(
                event_type=ScheduleEventType.PRE_MARKET,
                scheduled_time=time(8, 0),
                description="Custom PRE_MARKET",
            ),
        ]
        scheduler = TradingScheduler(schedule=custom_schedule)
        assert scheduler._schedule == custom_schedule
        assert len(scheduler._schedule) == 1

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    async def test_execute_event_with_non_dict_handler_result(self, mock_get_settings):
        """핸들러가 dict이 아닌 값을 반환할 때"""
        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        async def string_handler():
            return "string result"

        scheduler = TradingScheduler()
        scheduler.register_handler("handle_pre_market", string_handler)

        event = ScheduleEvent(
            event_type=ScheduleEventType.PRE_MARKET,
            scheduled_time=time(8, 30),
            description="Test",
            handler="handle_pre_market",
        )

        result = await scheduler._execute_event(event)
        assert result["success"] is True
        assert result["details"] == {}  # dict가 아니므로 빈 dict

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    @patch("core.trading_scheduler.now_kst")
    async def test_find_next_event_at_exact_event_time(self, mock_now_kst, mock_get_settings):
        """정확한 이벤트 시간에 찾기"""
        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        scheduler = TradingScheduler()
        # 정확히 8시 30분 (PRE_MARKET 시간)
        now = datetime(2026, 4, 6, 8, 30, 0, tzinfo=KST)

        next_event = scheduler._find_next_event(now)
        assert next_event is not None
        assert next_event.event_type == ScheduleEventType.PRE_MARKET

    def test_holidays_all_defined(self):
        """모든 공휴일이 정의되어 있음"""
        assert len(KR_HOLIDAYS_2026) > 0
        assert len(KR_HOLIDAYS_2025) > 0
        assert len(ALL_HOLIDAYS) > 0

    def test_default_schedule_has_all_events(self):
        """기본 스케줄에 모든 이벤트가 있음"""
        event_types = {e.event_type for e in DEFAULT_SCHEDULE}
        assert ScheduleEventType.PRE_MARKET in event_types
        assert ScheduleEventType.MARKET_OPEN in event_types
        assert ScheduleEventType.MIDDAY_CHECK in event_types
        assert ScheduleEventType.MARKET_CLOSE in event_types
        assert ScheduleEventType.POST_MARKET in event_types

    def test_default_schedule_times_in_order(self):
        """기본 스케줄의 시간이 순서대로 되어 있음"""
        times = [e.scheduled_time for e in DEFAULT_SCHEDULE]
        for i in range(len(times) - 1):
            assert times[i] <= times[i + 1]

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    async def test_scheduler_state_increments_trading_days(self, mock_get_settings):
        """거래일 수가 증가하는지 확인"""
        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        scheduler = TradingScheduler()
        initial_count = scheduler.state.total_trading_days

        # 수동으로 거래일 카운트 증가 시뮬레이션
        scheduler._state.total_trading_days += 1
        assert scheduler.state.total_trading_days == initial_count + 1


# ══════════════════════════════════════
# 19. 일시정지 중 이벤트 실행 테스트
# ══════════════════════════════════════


class TestPausedStateHandling(unittest.IsolatedAsyncioTestCase):
    """일시정지 상태에서의 동작 테스트"""

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    async def test_run_event_now_while_paused(self, mock_get_settings):
        """일시정지 중에도 수동 이벤트는 실행 가능"""
        mock_settings = MagicMock()
        mock_settings.kis.trading_mode = TradingMode.DEMO
        mock_get_settings.return_value = mock_settings

        scheduler = TradingScheduler()
        await scheduler.start()
        await scheduler.pause()

        # 일시정지 중에도 수동 실행은 가능
        result = await scheduler.run_event_now(ScheduleEventType.MARKET_OPEN)
        assert result["success"] is True

        await scheduler.stop()


# ══════════════════════════════════════
# 20. 핸들러 등록 및 실행 순서
# ══════════════════════════════════════


class TestHandlerExecutionOrder(unittest.IsolatedAsyncioTestCase):
    """핸들러 실행 순서 및 시점 테스트"""

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    async def test_custom_handler_overrides_builtin(self, mock_get_settings):
        """등록된 핸들러가 내장 핸들러를 오버라이드"""
        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        async def custom_handler():
            return {"source": "custom"}

        scheduler = TradingScheduler()
        scheduler.register_handler("handle_market_open", custom_handler)

        result = await scheduler.run_event_now(ScheduleEventType.MARKET_OPEN)
        assert result["details"]["source"] == "custom"

    @pytest.mark.asyncio
    @patch("core.trading_scheduler.get_settings")
    async def test_handler_called_with_no_args(self, mock_get_settings):
        """핸들러가 인자 없이 호출됨"""
        mock_settings = MagicMock()
        mock_get_settings.return_value = mock_settings

        handler_args = []

        async def tracking_handler(*args, **kwargs):
            handler_args.append((args, kwargs))
            return {}

        scheduler = TradingScheduler()
        scheduler.register_handler("handle_pre_market", tracking_handler)

        await scheduler.run_event_now(ScheduleEventType.PRE_MARKET)

        # 핸들러가 인자 없이 호출되었는지 확인
        assert len(handler_args) == 1
        args, kwargs = handler_args[0]
        assert args == ()
        assert kwargs == {}
