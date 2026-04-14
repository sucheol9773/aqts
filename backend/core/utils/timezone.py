"""한국 표준시(KST) 변환 유틸리티.

AQTS 시간대 정책
================
- **DB 저장**: UTC (timezone-aware, ``TIMESTAMP WITH TIME ZONE``)
- **사용자 노출**: KST (API 응답, Telegram, 로그 메시지, Redis 상태)

사용 예::

    from core.utils.timezone import KST, to_kst, to_kst_iso, now_kst

    # UTC datetime → KST datetime
    kst_dt = to_kst(utc_dt)

    # UTC datetime → KST ISO 문자열 (API 응답용)
    iso_str = to_kst_iso(utc_dt)  # "2026-04-14T21:30:00+09:00"

    # 현재 KST 시각
    now = now_kst()
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional, Union

# ── 상수 ──────────────────────────────────────────────────────────────────────
KST = timezone(timedelta(hours=9))
"""한국 표준시 (UTC+9)."""


# ── 변환 함수 ─────────────────────────────────────────────────────────────────
def to_kst(dt: Optional[Union[datetime, date]]) -> Optional[Union[datetime, date]]:
    """UTC(또는 임의 timezone) datetime을 KST로 변환한다.

    - ``None`` 이면 ``None`` 반환.
    - ``date`` 객체(시간 없음)이면 시간대 변환 없이 그대로 반환.
    - naive datetime(tzinfo 없음)이면 UTC로 간주하고 KST로 변환.
    - aware datetime이면 ``astimezone(KST)`` 로 변환.
    """
    if dt is None:
        return None
    # date 객체(datetime의 서브클래스가 아닌 순수 date)는 시간대 변환 불가
    if isinstance(dt, date) and not isinstance(dt, datetime):
        return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST)


def to_kst_iso(dt: Optional[Union[datetime, date]]) -> Optional[str]:
    """UTC datetime을 KST ISO 8601 문자열로 변환한다.

    API 응답에서 ``.isoformat()`` 대신 이 함수를 사용한다.
    ``date`` 객체는 ``"2026-04-14"`` 형식으로 반환한다.

    Returns:
        ``"2026-04-14T21:30:00+09:00"`` 또는 ``"2026-04-14"`` 형식, 또는 ``None``.
    """
    converted = to_kst(dt)
    if converted is None:
        return None
    return converted.isoformat()


def now_kst() -> datetime:
    """현재 KST 시각을 반환한다."""
    return datetime.now(KST)


def today_kst_str(fmt: str = "%Y-%m-%d") -> str:
    """오늘 날짜를 KST 기준 문자열로 반환한다.

    Redis 키, 파일명 등에서 ``datetime.now(timezone.utc).strftime(...)`` 대신
    이 함수를 사용하여 KST 날짜 기준으로 통일한다.
    """
    return now_kst().strftime(fmt)
