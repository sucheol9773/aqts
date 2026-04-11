"""
KIS WebSocket 전송 보안 부팅 가드 테스트.

정책: docs/security/kis-websocket-security.md
- 운영(production) + LIVE에서 ws:// → 부팅 차단
- 예외: KIS_WS_INSECURE_ALLOW=true + 유효 티켓 + 만료일 미경과
- 개발/DEMO/BACKTEST → ws:// 허용 (경고만)
- wss:// → 항상 통과
- ws:// / wss:// 외 스킴 → 즉시 차단
- 만료일 경계: YYYY-MM-DD는 당일 23:59:59 UTC까지 유효
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from config.settings import KISSettings


def _make_settings(**overrides) -> KISSettings:
    """테스트용 KISSettings 인스턴스 생성."""
    defaults = {
        "trading_mode": "LIVE",
        "live_websocket_url": "ws://ops.koreainvestment.com:21000",
        "demo_websocket_url": "ws://ops.koreainvestment.com:31000",
    }
    defaults.update(overrides)
    return KISSettings(**defaults)


class TestWebSocketSecurityGuard:
    """운영+LIVE에서 ws:// 차단 테스트."""

    def test_wss_always_passes(self) -> None:
        """wss:// URL은 어떤 환경에서든 통과."""
        kis = _make_settings(live_websocket_url="wss://ops.koreainvestment.com:21000")
        kis.validate_websocket_security("production")

    def test_ws_in_dev_passes(self) -> None:
        """개발 환경에서 ws://는 경고만, 차단 없음."""
        kis = _make_settings()
        kis.validate_websocket_security("development")

    def test_ws_in_demo_mode_passes(self) -> None:
        """DEMO 모드에서 ws://는 차단 없음."""
        kis = _make_settings(trading_mode="DEMO")
        kis.validate_websocket_security("production")

    def test_ws_in_backtest_mode_passes(self) -> None:
        """BACKTEST 모드에서 ws://는 차단 없음."""
        kis = _make_settings(trading_mode="BACKTEST")
        kis.validate_websocket_security("production")

    def test_ws_prod_live_blocked_by_default(self) -> None:
        """운영+LIVE에서 ws://는 기본(ws_insecure_allow=false)으로 부팅 차단."""
        kis = _make_settings()
        with pytest.raises(RuntimeError, match="보안 차단"):
            kis.validate_websocket_security("production")

    def test_ws_prod_live_insecure_allow_false_blocks(self) -> None:
        """ws_insecure_allow=false 명시 → 차단."""
        kis = _make_settings(ws_insecure_allow="false")
        with pytest.raises(RuntimeError, match="보안 차단"):
            kis.validate_websocket_security("production")

    def test_empty_url_passes(self) -> None:
        """WebSocket URL이 비어있으면 검증 건너뜀."""
        kis = _make_settings(live_websocket_url="")
        kis.validate_websocket_security("production")


class TestWebSocketSchemeAllowlist:
    """URL scheme allowlist 검증."""

    def test_http_scheme_blocked(self) -> None:
        """http:// 스킴은 즉시 차단."""
        kis = _make_settings(live_websocket_url="http://example.com:21000")
        with pytest.raises(RuntimeError, match="스킴이 허용 목록에 없습니다"):
            kis.validate_websocket_security("development")

    def test_ftp_scheme_blocked(self) -> None:
        """ftp:// 스킴은 즉시 차단."""
        kis = _make_settings(live_websocket_url="ftp://example.com/data")
        with pytest.raises(RuntimeError, match="스킴이 허용 목록에 없습니다"):
            kis.validate_websocket_security("development")

    def test_no_scheme_blocked(self) -> None:
        """스킴 없는 URL은 차단."""
        kis = _make_settings(live_websocket_url="ops.koreainvestment.com:21000")
        with pytest.raises(RuntimeError, match="스킴이 허용 목록에 없습니다"):
            kis.validate_websocket_security("development")

    def test_ws_scheme_allowed(self) -> None:
        """ws:// 스킴은 allowlist 통과 (환경별 후속 검증은 별도)."""
        kis = _make_settings(live_websocket_url="ws://ops.koreainvestment.com:21000")
        # 개발 환경이면 ws://도 통과
        kis.validate_websocket_security("development")

    def test_wss_scheme_allowed(self) -> None:
        """wss:// 스킴은 항상 통과."""
        kis = _make_settings(live_websocket_url="wss://ops.koreainvestment.com:21000")
        kis.validate_websocket_security("production")


class TestWebSocketSecurityException:
    """예외 허용 경로 테스트."""

    def test_insecure_allow_without_ticket_blocks(self) -> None:
        """ws_insecure_allow=true이지만 티켓 없으면 차단."""
        kis = _make_settings(
            ws_insecure_allow="true",
            ws_exception_ticket="",
            ws_exception_expires_at="2099-12-31",
        )
        with pytest.raises(RuntimeError, match="TICKET.*비어있습니다"):
            kis.validate_websocket_security("production")

    def test_insecure_allow_without_expires_blocks(self) -> None:
        """ws_insecure_allow=true이지만 만료일 없으면 차단."""
        kis = _make_settings(
            ws_insecure_allow="true",
            ws_exception_ticket="CHG-2026-0042",
            ws_exception_expires_at="",
        )
        with pytest.raises(RuntimeError, match="EXPIRES_AT.*비어있습니다"):
            kis.validate_websocket_security("production")

    def test_insecure_allow_with_expired_date_blocks(self) -> None:
        """만료일이 경과하면 차단."""
        kis = _make_settings(
            ws_insecure_allow="true",
            ws_exception_ticket="CHG-2026-0042",
            ws_exception_expires_at="2020-01-01",
        )
        with pytest.raises(RuntimeError, match="예외가 만료되었습니다"):
            kis.validate_websocket_security("production")

    def test_insecure_allow_with_invalid_date_format_blocks(self) -> None:
        """만료일 형식 오류 시 차단."""
        kis = _make_settings(
            ws_insecure_allow="true",
            ws_exception_ticket="CHG-2026-0042",
            ws_exception_expires_at="not-a-date",
        )
        with pytest.raises(RuntimeError, match="형식 오류"):
            kis.validate_websocket_security("production")

    def test_insecure_allow_with_valid_exception_passes(self) -> None:
        """유효한 예외 설정 시 통과 (경고 로그)."""
        kis = _make_settings(
            ws_insecure_allow="true",
            ws_exception_ticket="CHG-2026-0042",
            ws_exception_expires_at="2099-12-31",
        )
        kis.validate_websocket_security("production")

    def test_insecure_allow_invalid_value_raises(self) -> None:
        """ws_insecure_allow에 비표준 값 → ValueError."""
        kis = _make_settings(ws_insecure_allow="yes")
        with pytest.raises(ValueError, match="유효하지 않습니다"):
            kis.validate_websocket_security("production")


class TestExpirationDateBoundary:
    """만료일 경계 정책: 당일 23:59:59 UTC까지 유효."""

    def test_same_day_at_noon_is_valid(self) -> None:
        """만료 당일 정오에는 아직 유효."""
        today_str = "2026-06-30"
        # 2026-06-30 12:00:00 UTC
        fake_now = datetime(2026, 6, 30, 12, 0, 0, tzinfo=timezone.utc)
        kis = _make_settings(
            ws_insecure_allow="true",
            ws_exception_ticket="CHG-2026-0042",
            ws_exception_expires_at=today_str,
        )
        with patch("config.settings.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.strptime = datetime.strptime
            # 만료 당일이므로 통과해야 함
            kis.validate_websocket_security("production")

    def test_same_day_at_2359_is_valid(self) -> None:
        """만료 당일 23:59:00 UTC에는 아직 유효."""
        today_str = "2026-06-30"
        fake_now = datetime(2026, 6, 30, 23, 59, 0, tzinfo=timezone.utc)
        kis = _make_settings(
            ws_insecure_allow="true",
            ws_exception_ticket="CHG-2026-0042",
            ws_exception_expires_at=today_str,
        )
        with patch("config.settings.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.strptime = datetime.strptime
            kis.validate_websocket_security("production")

    def test_next_day_at_midnight_is_expired(self) -> None:
        """만료일 다음 날 00:00:00 UTC → 차단."""
        today_str = "2026-06-30"
        # 2026-07-01 00:00:00 UTC
        fake_now = datetime(2026, 7, 1, 0, 0, 0, tzinfo=timezone.utc)
        kis = _make_settings(
            ws_insecure_allow="true",
            ws_exception_ticket="CHG-2026-0042",
            ws_exception_expires_at=today_str,
        )
        with patch("config.settings.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.strptime = datetime.strptime
            with pytest.raises(RuntimeError, match="예외가 만료되었습니다"):
                kis.validate_websocket_security("production")
