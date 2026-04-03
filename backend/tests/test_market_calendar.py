"""
마켓 캘린더 테스트 (F-10-01-A)

MarketCalendar의 종합 단위 테스트

테스트 범위:
- NYSE 공휴일 정확성 (고정/이동 공휴일)
- KRX 공휴일 기본 검증
- 거래일 판별 (주말/공휴일/평일)
- 다음/이전 거래일
- 구간 내 거래일 목록
- 조기 폐장일 (NYSE)
- 부활절 계산 (Gauss 알고리즘)
- 관찰 공휴일 규칙
- DST 판별
- 장 운영 시간 확인
"""

from datetime import date, datetime, time, timedelta, timezone

import pytest

from core.market_calendar import (
    EDT,
    EST,
    KST,
    Market,
    MarketCalendar,
    _calculate_easter,
    _nth_weekday,
    _last_weekday,
    _observed_holiday,
    get_nyse_holidays,
    get_nyse_early_close_dates,
)


@pytest.fixture
def cal():
    return MarketCalendar()


# ══════════════════════════════════════
# 유틸리티 함수 테스트
# ══════════════════════════════════════
class TestUtilityFunctions:
    """유틸리티 함수 테스트"""

    def test_nth_weekday_mlk_2026(self):
        """2026년 MLK Day: 1월 셋째 월요일 = 1/19"""
        result = _nth_weekday(2026, 1, 0, 3)
        assert result == date(2026, 1, 19)

    def test_nth_weekday_thanksgiving_2026(self):
        """2026년 Thanksgiving: 11월 넷째 목요일 = 11/26"""
        result = _nth_weekday(2026, 11, 3, 4)
        assert result == date(2026, 11, 26)

    def test_last_weekday_memorial_day_2026(self):
        """2026년 Memorial Day: 5월 마지막 월요일 = 5/25"""
        result = _last_weekday(2026, 5, 0)
        assert result == date(2026, 5, 25)

    def test_observed_saturday(self):
        """토요일 → 금요일로 이동"""
        # 2026-07-04는 토요일
        result = _observed_holiday(date(2026, 7, 4))
        assert result == date(2026, 7, 3)

    def test_observed_sunday(self):
        """일요일 → 월요일로 이동"""
        # 2027-07-04는 일요일
        result = _observed_holiday(date(2027, 7, 4))
        assert result == date(2027, 7, 5)

    def test_observed_weekday(self):
        """평일은 그대로"""
        result = _observed_holiday(date(2025, 7, 4))  # 금요일
        assert result == date(2025, 7, 4)


# ══════════════════════════════════════
# 부활절 계산 테스트
# ══════════════════════════════════════
class TestEasterCalculation:
    """부활절 계산 (Gauss 알고리즘) 테스트"""

    def test_easter_2025(self):
        """2025년 부활절: 4/20"""
        assert _calculate_easter(2025) == date(2025, 4, 20)

    def test_easter_2026(self):
        """2026년 부활절: 4/5"""
        assert _calculate_easter(2026) == date(2026, 4, 5)

    def test_easter_2024(self):
        """2024년 부활절: 3/31"""
        assert _calculate_easter(2024) == date(2024, 3, 31)

    def test_good_friday_in_holidays(self):
        """Good Friday가 NYSE 공휴일에 포함"""
        holidays = get_nyse_holidays(2026)
        easter = _calculate_easter(2026)
        good_friday = easter - timedelta(days=2)
        assert good_friday in holidays


# ══════════════════════════════════════
# NYSE 공휴일 테스트
# ══════════════════════════════════════
class TestNYSEHolidays:
    """NYSE 공휴일 생성 테스트"""

    def test_2026_holiday_count(self):
        """2026년 NYSE 공휴일 개수 (10개: 고정 4 + 이동 6)"""
        holidays = get_nyse_holidays(2026)
        assert len(holidays) == 10

    def test_2026_new_years_observed(self):
        """2026년 New Year's Day (1/1 목요일 → 그대로)"""
        holidays = get_nyse_holidays(2026)
        assert date(2026, 1, 1) in holidays

    def test_2026_juneteenth(self):
        """2026년 Juneteenth (6/19 금요일)"""
        holidays = get_nyse_holidays(2026)
        assert date(2026, 6, 19) in holidays

    def test_2026_independence_day_observed(self):
        """2026년 Independence Day (7/4 토요일 → 7/3 금요일로 관찰)"""
        holidays = get_nyse_holidays(2026)
        assert date(2026, 7, 3) in holidays  # 관찰 규칙

    def test_2026_christmas(self):
        """2026년 Christmas (12/25 금요일)"""
        holidays = get_nyse_holidays(2026)
        assert date(2026, 12, 25) in holidays

    def test_2026_labor_day(self):
        """2026년 Labor Day: 9월 첫째 월요일 = 9/7"""
        holidays = get_nyse_holidays(2026)
        assert date(2026, 9, 7) in holidays


# ══════════════════════════════════════
# NYSE 조기 폐장 테스트
# ══════════════════════════════════════
class TestNYSEEarlyClose:
    """NYSE 조기 폐장일 테스트"""

    def test_thanksgiving_friday(self):
        """추수감사절 다음날 금요일이 조기 폐장"""
        early = get_nyse_early_close_dates(2026)
        thanksgiving = _nth_weekday(2026, 11, 3, 4)
        assert (thanksgiving + timedelta(days=1)) in early

    def test_christmas_eve_if_weekday(self):
        """12/24가 평일이면 조기 폐장"""
        # 2026-12-24는 목요일
        early = get_nyse_early_close_dates(2026)
        assert date(2026, 12, 24) in early

    def test_early_close_time(self, cal):
        """조기 폐장일의 마감 시각 = 13:00"""
        early_dates = get_nyse_early_close_dates(2026)
        for d in early_dates:
            close = cal.get_close_time(d, Market.NYSE)
            assert close == time(13, 0)
            break  # 하나만 확인


# ══════════════════════════════════════
# 거래일 판별 테스트
# ══════════════════════════════════════
class TestTradingDayDetection:
    """거래일 판별 테스트"""

    def test_weekday_is_trading_day_nyse(self, cal):
        """평일이면서 공휴일 아닌 날은 거래일 (NYSE)"""
        # 2026-01-02 (금) — 평일, 공휴일 아님
        assert cal.is_trading_day(date(2026, 1, 2), Market.NYSE) is True

    def test_weekend_not_trading_nyse(self, cal):
        """주말은 거래일 아님 (NYSE)"""
        assert cal.is_trading_day(date(2026, 1, 3), Market.NYSE) is False  # 토

    def test_holiday_not_trading_nyse(self, cal):
        """공휴일은 거래일 아님 (NYSE)"""
        assert cal.is_trading_day(date(2026, 1, 1), Market.NYSE) is False

    def test_weekday_is_trading_day_krx(self, cal):
        """평일이면서 공휴일 아닌 날은 거래일 (KRX)"""
        assert cal.is_trading_day(date(2026, 1, 2), Market.KRX) is True

    def test_kr_holiday_not_trading(self, cal):
        """한국 공휴일은 거래일 아님"""
        assert cal.is_trading_day(date(2026, 2, 17), Market.KRX) is False  # 설날

    def test_is_holiday_weekday(self, cal):
        """공휴일 여부 (주말 제외)"""
        assert cal.is_holiday(date(2026, 1, 1), Market.NYSE) is True
        assert cal.is_holiday(date(2026, 1, 3), Market.NYSE) is False  # 토요일


# ══════════════════════════════════════
# 다음/이전 거래일 테스트
# ══════════════════════════════════════
class TestNextPrevTradingDay:
    """다음/이전 거래일 테스트"""

    def test_next_from_friday(self, cal):
        """금요일 다음 거래일 → 월요일"""
        # 2026-01-02 (금)
        nxt = cal.next_trading_day(date(2026, 1, 2), Market.NYSE)
        assert nxt == date(2026, 1, 5)  # 월

    def test_next_skips_holiday(self, cal):
        """공휴일 건너뛰기"""
        # 2025-12-24 수요일 → 12/25 공휴일 → 12/26 금
        nxt = cal.next_trading_day(date(2025, 12, 24), Market.NYSE)
        assert nxt == date(2025, 12, 26)

    def test_prev_from_monday(self, cal):
        """월요일 이전 거래일 → 금요일"""
        prv = cal.prev_trading_day(date(2026, 1, 5), Market.NYSE)
        assert prv == date(2026, 1, 2)

    def test_prev_skips_weekend_and_holiday(self, cal):
        """주말+공휴일 건너뛰기"""
        # 2026-01-02 이전 → 12/31 (수) [1/1 공휴일]
        prv = cal.prev_trading_day(date(2026, 1, 2), Market.NYSE)
        assert prv == date(2025, 12, 31)


# ══════════════════════════════════════
# 구간 내 거래일 테스트
# ══════════════════════════════════════
class TestTradingDaysBetween:
    """구간 내 거래일 테스트"""

    def test_one_week(self, cal):
        """평일 한 주 = 5 거래일"""
        start = date(2026, 1, 5)  # 월
        end = date(2026, 1, 9)    # 금
        days = cal.trading_days_between(start, end, Market.NYSE)
        assert len(days) == 5

    def test_includes_endpoints(self, cal):
        """시작/종료 날짜 포함"""
        start = date(2026, 1, 5)
        end = date(2026, 1, 5)
        days = cal.trading_days_between(start, end, Market.NYSE)
        assert days == [date(2026, 1, 5)]

    def test_holiday_excluded(self, cal):
        """공휴일이 포함된 주"""
        # 2026-01-01 (목) 공휴일
        start = date(2025, 12, 29)  # 월
        end = date(2026, 1, 2)      # 금
        days = cal.trading_days_between(start, end, Market.NYSE)
        assert date(2026, 1, 1) not in days

    def test_trading_day_count(self, cal):
        """거래일 수 카운트"""
        count = cal.trading_day_count(
            date(2026, 1, 1), date(2026, 1, 31), Market.NYSE
        )
        assert 19 <= count <= 22  # 대략 20일 내외


# ══════════════════════════════════════
# DST 테스트
# ══════════════════════════════════════
class TestDST:
    """DST (서머타임) 테스트"""

    def test_summer_is_dst(self, cal):
        """여름은 DST"""
        assert cal.is_dst(date(2026, 7, 1)) is True

    def test_winter_not_dst(self, cal):
        """겨울은 DST 아님"""
        assert cal.is_dst(date(2026, 1, 15)) is False

    def test_dst_boundary_march(self, cal):
        """3월 DST 시작일 확인"""
        # 2026년 DST 시작: 3월 둘째 일요일 = 3/8
        assert cal.is_dst(date(2026, 3, 7)) is False
        assert cal.is_dst(date(2026, 3, 8)) is True

    def test_market_tz_summer(self, cal):
        """여름 NYSE 시간대 = EDT"""
        tz = cal.get_market_tz(date(2026, 7, 1), Market.NYSE)
        assert tz == EDT

    def test_market_tz_winter(self, cal):
        """겨울 NYSE 시간대 = EST"""
        tz = cal.get_market_tz(date(2026, 1, 15), Market.NYSE)
        assert tz == EST


# ══════════════════════════════════════
# 장 운영 시간 테스트
# ══════════════════════════════════════
class TestMarketOpen:
    """장 운영 시간 테스트"""

    def test_nyse_open_during_hours(self, cal):
        """NYSE 장 중 (10:00 ET)"""
        dt = datetime(2026, 1, 5, 10, 0, tzinfo=EST)  # 월요일 10am
        assert cal.is_market_open(dt, Market.NYSE) is True

    def test_nyse_closed_before_open(self, cal):
        """NYSE 장 전 (09:00 ET)"""
        dt = datetime(2026, 1, 5, 9, 0, tzinfo=EST)
        assert cal.is_market_open(dt, Market.NYSE) is False

    def test_nyse_closed_weekend(self, cal):
        """주말은 장 외"""
        dt = datetime(2026, 1, 3, 12, 0, tzinfo=EST)  # 토
        assert cal.is_market_open(dt, Market.NYSE) is False

    def test_krx_open_during_hours(self, cal):
        """KRX 장 중 (10:00 KST)"""
        dt = datetime(2026, 1, 5, 10, 0, tzinfo=KST)
        assert cal.is_market_open(dt, Market.KRX) is True

    def test_krx_closed_after_close(self, cal):
        """KRX 장 후 (16:00 KST)"""
        dt = datetime(2026, 1, 5, 16, 0, tzinfo=KST)
        assert cal.is_market_open(dt, Market.KRX) is False

    def test_get_holidays_sorted(self, cal):
        """공휴일 목록이 정렬되어 반환"""
        holidays = cal.get_holidays(2026, Market.NYSE)
        assert holidays == sorted(holidays)
        assert len(holidays) == 10
