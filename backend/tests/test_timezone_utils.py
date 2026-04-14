"""core.utils.timezone 유틸리티 테스트.

KST 변환 함수의 정확성을 검증한다.
"""

from datetime import date, datetime, timedelta, timezone

from core.utils.timezone import KST, now_kst, to_kst, to_kst_iso, today_kst_str


class TestKSTConstant:
    """KST 상수 검증."""

    def test_kst_offset_is_9_hours(self):
        assert KST.utcoffset(None) == timedelta(hours=9)

    def test_kst_is_timezone_instance(self):
        assert isinstance(KST, timezone)


class TestToKst:
    """to_kst() 변환 검증."""

    def test_none_returns_none(self):
        assert to_kst(None) is None

    def test_utc_datetime_converted_to_kst(self):
        utc_dt = datetime(2026, 4, 14, 15, 0, 0, tzinfo=timezone.utc)
        kst_dt = to_kst(utc_dt)
        assert kst_dt.hour == 0  # 15:00 UTC → 00:00+1 KST (다음날)
        assert kst_dt.day == 15
        assert kst_dt.tzinfo == KST

    def test_naive_datetime_treated_as_utc(self):
        naive_dt = datetime(2026, 4, 14, 6, 0, 0)
        kst_dt = to_kst(naive_dt)
        assert kst_dt.hour == 15  # 06:00 UTC → 15:00 KST
        assert kst_dt.tzinfo == KST

    def test_already_kst_datetime_unchanged(self):
        kst_dt = datetime(2026, 4, 14, 21, 0, 0, tzinfo=KST)
        result = to_kst(kst_dt)
        assert result.hour == 21
        assert result.tzinfo == KST

    def test_date_object_returned_as_is(self):
        d = date(2026, 4, 14)
        result = to_kst(d)
        assert result == d
        assert isinstance(result, date)
        assert not isinstance(result, datetime)

    def test_other_timezone_converted(self):
        est = timezone(timedelta(hours=-5))
        est_dt = datetime(2026, 4, 14, 10, 0, 0, tzinfo=est)
        kst_dt = to_kst(est_dt)
        # 10:00 EST = 15:00 UTC = 00:00+1 KST
        assert kst_dt.hour == 0
        assert kst_dt.day == 15


class TestToKstIso:
    """to_kst_iso() ISO 문자열 변환 검증."""

    def test_none_returns_none(self):
        assert to_kst_iso(None) is None

    def test_returns_kst_iso_string(self):
        utc_dt = datetime(2026, 4, 14, 3, 30, 0, tzinfo=timezone.utc)
        iso = to_kst_iso(utc_dt)
        assert iso == "2026-04-14T12:30:00+09:00"

    def test_date_object_returns_date_string(self):
        d = date(2026, 4, 14)
        assert to_kst_iso(d) == "2026-04-14"

    def test_contains_kst_offset(self):
        utc_dt = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        iso = to_kst_iso(utc_dt)
        assert "+09:00" in iso


class TestNowKst:
    """now_kst() 현재 시각 검증."""

    def test_returns_kst_timezone(self):
        result = now_kst()
        assert result.tzinfo == KST

    def test_returns_datetime_instance(self):
        result = now_kst()
        assert isinstance(result, datetime)


class TestTodayKstStr:
    """today_kst_str() 날짜 문자열 검증."""

    def test_default_format(self):
        result = today_kst_str()
        # YYYY-MM-DD 형식 검증
        assert len(result) == 10
        assert result[4] == "-"
        assert result[7] == "-"

    def test_custom_format(self):
        result = today_kst_str("%Y%m%d")
        assert len(result) == 8
        assert result.isdigit()

    def test_matches_now_kst_date(self):
        expected = now_kst().strftime("%Y-%m-%d")
        assert today_kst_str() == expected
