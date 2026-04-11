"""
KIS WebSocket 전송 보안 부팅 가드 테스트.

정책: docs/security/kis-websocket-security.md
- 운영(production) + LIVE에서 ws:// → 부팅 차단
- 예외: KIS_WS_INSECURE_ALLOW=true + 유효 티켓 + 만료일 미경과
- 개발/DEMO/BACKTEST → ws:// 허용 (경고만)
- wss:// → 항상 통과
"""

from __future__ import annotations

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
        # 예외 없이 통과
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

    def test_ws_prod_live_blocked_by_default(self, monkeypatch) -> None:
        """운영+LIVE에서 ws://는 기본적으로 부팅 차단."""
        monkeypatch.delenv("KIS_WS_INSECURE_ALLOW", raising=False)
        kis = _make_settings()
        with pytest.raises(RuntimeError, match="보안 차단"):
            kis.validate_websocket_security("production")

    def test_ws_prod_live_insecure_allow_false_blocks(self, monkeypatch) -> None:
        """KIS_WS_INSECURE_ALLOW=false → 차단."""
        monkeypatch.setenv("KIS_WS_INSECURE_ALLOW", "false")
        kis = _make_settings()
        with pytest.raises(RuntimeError, match="보안 차단"):
            kis.validate_websocket_security("production")


class TestWebSocketSecurityException:
    """예외 허용 경로 테스트."""

    def test_insecure_allow_without_ticket_blocks(self, monkeypatch) -> None:
        """KIS_WS_INSECURE_ALLOW=true이지만 티켓 없으면 차단."""
        monkeypatch.setenv("KIS_WS_INSECURE_ALLOW", "true")
        kis = _make_settings(ws_exception_ticket="", ws_exception_expires_at="2026-12-31")
        with pytest.raises(RuntimeError, match="TICKET.*비어있습니다"):
            kis.validate_websocket_security("production")

    def test_insecure_allow_without_expires_blocks(self, monkeypatch) -> None:
        """KIS_WS_INSECURE_ALLOW=true이지만 만료일 없으면 차단."""
        monkeypatch.setenv("KIS_WS_INSECURE_ALLOW", "true")
        kis = _make_settings(ws_exception_ticket="CHG-2026-0042", ws_exception_expires_at="")
        with pytest.raises(RuntimeError, match="EXPIRES_AT.*비어있습니다"):
            kis.validate_websocket_security("production")

    def test_insecure_allow_with_expired_date_blocks(self, monkeypatch) -> None:
        """만료일이 경과하면 차단."""
        monkeypatch.setenv("KIS_WS_INSECURE_ALLOW", "true")
        kis = _make_settings(
            ws_exception_ticket="CHG-2026-0042",
            ws_exception_expires_at="2020-01-01",
        )
        with pytest.raises(RuntimeError, match="예외가 만료되었습니다"):
            kis.validate_websocket_security("production")

    def test_insecure_allow_with_invalid_date_format_blocks(self, monkeypatch) -> None:
        """만료일 형식 오류 시 차단."""
        monkeypatch.setenv("KIS_WS_INSECURE_ALLOW", "true")
        kis = _make_settings(
            ws_exception_ticket="CHG-2026-0042",
            ws_exception_expires_at="not-a-date",
        )
        with pytest.raises(RuntimeError, match="형식 오류"):
            kis.validate_websocket_security("production")

    def test_insecure_allow_with_valid_exception_passes(self, monkeypatch) -> None:
        """유효한 예외 설정 시 통과 (경고 로그)."""
        monkeypatch.setenv("KIS_WS_INSECURE_ALLOW", "true")
        kis = _make_settings(
            ws_exception_ticket="CHG-2026-0042",
            ws_exception_expires_at="2099-12-31",
        )
        # RuntimeError 없이 통과해야 함
        kis.validate_websocket_security("production")

    def test_empty_url_passes(self) -> None:
        """WebSocket URL이 비어있으면 검증 건너뜀."""
        kis = _make_settings(live_websocket_url="")
        kis.validate_websocket_security("production")
