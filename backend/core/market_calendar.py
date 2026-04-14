"""
마켓 캘린더 (F-10-01-A)

KRX 및 NYSE 영업일 캘린더 통합 관리

지원 거래소:
- KRX: 한국거래소 (주말 + 한국 공휴일)
- NYSE: 뉴욕증권거래소 (주말 + 미국 연방 공휴일 + NYSE 특별 휴장)

주요 기능:
- is_trading_day(date, market): 특정 시장 거래일 여부
- next_trading_day(date, market): 다음 거래일
- prev_trading_day(date, market): 이전 거래일
- trading_days_between(start, end, market): 구간 내 거래일 목록
- is_market_open(datetime, market): 현재 장 운영 중 여부
- next_close_time(market): 다음 장 마감 시각

미국 공휴일 규칙:
- 고정 날짜: 1/1, 7/4, 12/25, 6/19 (Juneteenth)
- 이동 공휴일: MLK Day, Presidents' Day, Memorial Day,
  Labor Day, Thanksgiving Day (매년 계산)
- NYSE 조기 폐장: 7/3, 11월 추수감사절 전날, 12/24 (13:00 ET)
"""

from datetime import date, datetime, time, timedelta, timezone
from enum import Enum

# ══════════════════════════════════════
# 시간대 정의
# ══════════════════════════════════════
from core.utils.timezone import KST

EST = timezone(timedelta(hours=-5))
EDT = timezone(timedelta(hours=-4))


class Market(str, Enum):
    """거래소"""

    KRX = "KRX"  # 한국거래소
    NYSE = "NYSE"  # 뉴욕증권거래소


# ══════════════════════════════════════
# 장 시간 정의
# ══════════════════════════════════════
MARKET_HOURS = {
    Market.KRX: {
        "open": time(9, 0),  # 09:00 KST
        "close": time(15, 30),  # 15:30 KST
        "tz": KST,
    },
    Market.NYSE: {
        "open": time(9, 30),  # 09:30 ET
        "close": time(16, 0),  # 16:00 ET
        "early_close": time(13, 0),  # 13:00 ET (조기 폐장일)
        "tz": EST,  # 기본 EST (DST 기간에는 EDT)
    },
}


# ══════════════════════════════════════
# 한국 공휴일 (하드코딩 — 연 1회 갱신)
# ══════════════════════════════════════
KR_HOLIDAYS = {
    # 2025
    date(2025, 1, 1),
    date(2025, 1, 28),
    date(2025, 1, 29),
    date(2025, 1, 30),
    date(2025, 3, 1),
    date(2025, 5, 5),
    date(2025, 5, 6),
    date(2025, 6, 6),
    date(2025, 8, 15),
    date(2025, 10, 3),
    date(2025, 10, 6),
    date(2025, 10, 7),
    date(2025, 10, 8),
    date(2025, 10, 9),
    date(2025, 12, 25),
    # 2026
    date(2026, 1, 1),
    date(2026, 2, 16),
    date(2026, 2, 17),
    date(2026, 2, 18),
    date(2026, 3, 1),
    date(2026, 5, 5),
    date(2026, 5, 24),
    date(2026, 6, 6),
    date(2026, 8, 15),
    date(2026, 9, 24),
    date(2026, 9, 25),
    date(2026, 9, 26),
    date(2026, 10, 3),
    date(2026, 10, 9),
    date(2026, 12, 25),
}


# ══════════════════════════════════════
# 미국 공휴일 계산
# ══════════════════════════════════════
def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """
    특정 월의 n번째 요일 반환

    Args:
        year: 연도
        month: 월 (1~12)
        weekday: 요일 (0=월, 6=일)
        n: 몇 번째 (1~5)
    """
    first = date(year, month, 1)
    # 첫 번째 해당 요일까지의 오프셋
    offset = (weekday - first.weekday()) % 7
    first_weekday = first + timedelta(days=offset)
    return first_weekday + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """특정 월의 마지막 요일 반환"""
    if month == 12:
        last_day = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(year, month + 1, 1) - timedelta(days=1)

    offset = (last_day.weekday() - weekday) % 7
    return last_day - timedelta(days=offset)


def _observed_holiday(d: date) -> date:
    """
    관찰 공휴일 규칙 적용 (NYSE 표준)

    - 토요일이면 금요일로 이동
    - 일요일이면 월요일로 이동
    """
    if d.weekday() == 5:  # 토
        return d - timedelta(days=1)
    elif d.weekday() == 6:  # 일
        return d + timedelta(days=1)
    return d


def get_nyse_holidays(year: int) -> set[date]:
    """
    특정 연도의 NYSE 휴장일 세트 반환

    고정 공휴일 (관찰 규칙 적용):
    - New Year's Day (1/1)
    - Independence Day (7/4)
    - Christmas Day (12/25)
    - Juneteenth (6/19) — 2021년부터

    이동 공휴일:
    - MLK Day: 1월 셋째 월요일
    - Presidents' Day: 2월 셋째 월요일
    - Good Friday: 부활절 전 금요일
    - Memorial Day: 5월 마지막 월요일
    - Labor Day: 9월 첫째 월요일
    - Thanksgiving: 11월 넷째 목요일
    """
    holidays = set()

    # 고정 공휴일 (관찰 규칙)
    holidays.add(_observed_holiday(date(year, 1, 1)))  # New Year's
    holidays.add(_observed_holiday(date(year, 7, 4)))  # Independence Day
    holidays.add(_observed_holiday(date(year, 12, 25)))  # Christmas
    holidays.add(_observed_holiday(date(year, 6, 19)))  # Juneteenth

    # 이동 공휴일
    holidays.add(_nth_weekday(year, 1, 0, 3))  # MLK Day: 1월 3째 월
    holidays.add(_nth_weekday(year, 2, 0, 3))  # Presidents' Day: 2월 3째 월
    holidays.add(_last_weekday(year, 5, 0))  # Memorial Day: 5월 마지막 월
    holidays.add(_nth_weekday(year, 9, 0, 1))  # Labor Day: 9월 1째 월
    holidays.add(_nth_weekday(year, 11, 3, 4))  # Thanksgiving: 11월 4째 목

    # Good Friday (부활절 - 2일)
    easter = _calculate_easter(year)
    holidays.add(easter - timedelta(days=2))

    return holidays


def get_nyse_early_close_dates(year: int) -> set[date]:
    """
    NYSE 조기 폐장일 (13:00 ET)

    - 7/3 (독립기념일 전날, 평일인 경우)
    - 11월 추수감사절 다음날 (금요일)
    - 12/24 (크리스마스 이브, 평일인 경우)
    """
    early_close = set()

    # 7/3 (평일이면)
    jul3 = date(year, 7, 3)
    if jul3.weekday() < 5:
        early_close.add(jul3)

    # 추수감사절 다음날 (금요일)
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    early_close.add(thanksgiving + timedelta(days=1))

    # 12/24 (평일이면)
    dec24 = date(year, 12, 24)
    if dec24.weekday() < 5:
        early_close.add(dec24)

    return early_close


def _calculate_easter(year: int) -> date:
    """
    부활절 계산 (Anonymous Gregorian algorithm)

    Gauss 부활절 알고리즘 — 그레고리력 기반
    """
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


# NYSE 공휴일 캐시 (연도별)
_NYSE_HOLIDAY_CACHE: dict[int, set[date]] = {}
_NYSE_EARLY_CLOSE_CACHE: dict[int, set[date]] = {}


def _get_nyse_holidays_cached(year: int) -> set[date]:
    """캐시된 NYSE 공휴일"""
    if year not in _NYSE_HOLIDAY_CACHE:
        _NYSE_HOLIDAY_CACHE[year] = get_nyse_holidays(year)
    return _NYSE_HOLIDAY_CACHE[year]


def _get_nyse_early_close_cached(year: int) -> set[date]:
    """캐시된 NYSE 조기 폐장일"""
    if year not in _NYSE_EARLY_CLOSE_CACHE:
        _NYSE_EARLY_CLOSE_CACHE[year] = get_nyse_early_close_dates(year)
    return _NYSE_EARLY_CLOSE_CACHE[year]


# ══════════════════════════════════════
# 마켓 캘린더 API
# ══════════════════════════════════════
class MarketCalendar:
    """
    통합 마켓 캘린더

    KRX와 NYSE의 영업일/장 시간을 통합 관리합니다.

    Usage:
        cal = MarketCalendar()
        cal.is_trading_day(date.today(), Market.NYSE)
        cal.next_trading_day(date.today(), Market.KRX)
        cal.is_early_close(date.today(), Market.NYSE)
    """

    def is_trading_day(self, d: date, market: Market) -> bool:
        """
        특정 시장의 거래일 여부 판별

        Args:
            d: 확인할 날짜
            market: 거래소

        Returns:
            True=거래일, False=휴장일
        """
        # 주말
        if d.weekday() >= 5:
            return False

        if market == Market.KRX:
            return d not in KR_HOLIDAYS
        elif market == Market.NYSE:
            return d not in _get_nyse_holidays_cached(d.year)

        return True

    def is_holiday(self, d: date, market: Market) -> bool:
        """공휴일 여부 (주말 제외)"""
        if d.weekday() >= 5:
            return False  # 주말은 공휴일로 분류하지 않음

        if market == Market.KRX:
            return d in KR_HOLIDAYS
        elif market == Market.NYSE:
            return d in _get_nyse_holidays_cached(d.year)

        return False

    def is_early_close(self, d: date, market: Market) -> bool:
        """조기 폐장일 여부 (NYSE만 해당)"""
        if market != Market.NYSE:
            return False
        return d in _get_nyse_early_close_cached(d.year)

    def next_trading_day(self, d: date, market: Market) -> date:
        """다음 거래일 반환"""
        candidate = d + timedelta(days=1)
        while not self.is_trading_day(candidate, market):
            candidate += timedelta(days=1)
        return candidate

    def prev_trading_day(self, d: date, market: Market) -> date:
        """이전 거래일 반환"""
        candidate = d - timedelta(days=1)
        while not self.is_trading_day(candidate, market):
            candidate -= timedelta(days=1)
        return candidate

    def trading_days_between(
        self,
        start: date,
        end: date,
        market: Market,
    ) -> list[date]:
        """
        구간 내 거래일 목록 반환 (start, end 포함)

        Args:
            start: 시작 날짜
            end: 종료 날짜
            market: 거래소

        Returns:
            거래일 리스트
        """
        days = []
        current = start
        while current <= end:
            if self.is_trading_day(current, market):
                days.append(current)
            current += timedelta(days=1)
        return days

    def trading_day_count(
        self,
        start: date,
        end: date,
        market: Market,
    ) -> int:
        """구간 내 거래일 수"""
        return len(self.trading_days_between(start, end, market))

    def get_close_time(self, d: date, market: Market) -> time:
        """
        특정 날짜의 장 마감 시각 반환

        NYSE 조기 폐장일이면 13:00 ET 반환
        """
        hours = MARKET_HOURS[market]
        if market == Market.NYSE and self.is_early_close(d, market):
            return hours["early_close"]
        return hours["close"]

    def is_market_open(self, dt: datetime, market: Market) -> bool:
        """
        현재 시간에 장이 운영 중인지 확인

        Args:
            dt: 확인할 시각 (timezone-aware)
            market: 거래소

        Returns:
            True=장 중, False=장 외
        """
        if not self.is_trading_day(dt.date(), market):
            return False

        hours = MARKET_HOURS[market]
        tz = hours["tz"]

        # timezone 변환
        local_dt = dt.astimezone(tz)
        local_time = local_dt.time()

        open_time = hours["open"]
        close_time = self.get_close_time(dt.date(), market)

        return open_time <= local_time <= close_time

    def get_holidays(self, year: int, market: Market) -> list[date]:
        """특정 연도의 공휴일 목록 (정렬)"""
        if market == Market.KRX:
            return sorted(d for d in KR_HOLIDAYS if d.year == year)
        elif market == Market.NYSE:
            return sorted(_get_nyse_holidays_cached(year))
        return []

    def is_dst(self, d: date) -> bool:
        """
        미국 서머타임(DST) 여부

        3월 둘째 일요일 ~ 11월 첫째 일요일
        """
        dst_start = _nth_weekday(d.year, 3, 6, 2)  # 3월 2째 일요일
        dst_end = _nth_weekday(d.year, 11, 6, 1)  # 11월 1째 일요일
        return dst_start <= d < dst_end

    def get_market_tz(self, d: date, market: Market) -> timezone:
        """
        특정 날짜의 시장 시간대 (DST 반영)

        NYSE는 DST 기간에 EDT(-4), 그 외 EST(-5)
        """
        if market == Market.NYSE and self.is_dst(d):
            return EDT
        return MARKET_HOURS[market]["tz"]
