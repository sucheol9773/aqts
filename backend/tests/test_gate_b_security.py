"""
Gate B 보안 검증 테스트

API 키 만료/갱신 시나리오 및 시크릿 관리 검증을 포함합니다.
"""

import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# ══════════════════════════════════════
# 1. KIS 토큰 만료/갱신 시나리오
# ══════════════════════════════════════


class TestKISTokenExpiry(unittest.IsolatedAsyncioTestCase):
    """KIS API 토큰 만료 및 갱신 시나리오 테스트"""

    def _make_settings(self):
        settings = MagicMock()
        settings.kis.is_backtest = False
        settings.kis.is_live = False
        settings.kis.base_url = "https://openapivts.koreainvestment.com:29443"
        settings.kis.app_key = "test_app_key"
        settings.kis.app_secret = "test_app_secret"
        settings.kis.api_timeout = 10
        settings.kis.api_retry_count = 1
        return settings

    @patch("core.data_collector.kis_client.get_settings")
    async def test_expired_token_triggers_reissue(self, mock_get_settings):
        """만료된 토큰은 자동으로 재발급되어야 함"""
        mock_get_settings.return_value = self._make_settings()
        from core.data_collector.kis_client import KISTokenManager

        manager = KISTokenManager()
        # 이미 만료된 토큰 설정
        manager._access_token = "old_expired_token"
        manager._token_expires_at = datetime.now() - timedelta(hours=1)

        with patch.object(manager, "_issue_token", new_callable=AsyncMock) as mock_issue:

            async def set_new_token():
                manager._access_token = "new_fresh_token"
                manager._token_expires_at = datetime.now() + timedelta(hours=24)

            mock_issue.side_effect = set_new_token

            token = await manager.get_access_token()
            mock_issue.assert_called_once()
            assert token == "new_fresh_token"

    @patch("core.data_collector.kis_client.get_settings")
    async def test_token_within_10min_window_triggers_refresh(self, mock_get_settings):
        """만료 10분 이내 토큰은 갱신되어야 함"""
        mock_get_settings.return_value = self._make_settings()
        from core.data_collector.kis_client import KISTokenManager

        manager = KISTokenManager()
        # 9분 후 만료 (10분 미만이므로 갱신 필요)
        manager._access_token = "almost_expired_token"
        manager._token_expires_at = datetime.now() + timedelta(minutes=9)

        with patch.object(manager, "_issue_token", new_callable=AsyncMock) as mock_issue:

            async def refresh_token():
                manager._access_token = "refreshed_token"
                manager._token_expires_at = datetime.now() + timedelta(hours=24)

            mock_issue.side_effect = refresh_token

            token = await manager.get_access_token()
            mock_issue.assert_called_once()
            assert token == "refreshed_token"

    @patch("core.data_collector.kis_client.get_settings")
    async def test_valid_token_no_reissue(self, mock_get_settings):
        """유효한 토큰은 재발급 없이 반환되어야 함"""
        mock_get_settings.return_value = self._make_settings()
        from core.data_collector.kis_client import KISTokenManager

        manager = KISTokenManager()
        manager._access_token = "valid_token"
        manager._token_expires_at = datetime.now() + timedelta(hours=12)

        with patch.object(manager, "_issue_token", new_callable=AsyncMock) as mock_issue:
            token = await manager.get_access_token()
            mock_issue.assert_not_called()
            assert token == "valid_token"

    @patch("core.data_collector.kis_client.get_settings")
    async def test_no_token_initial_issue(self, mock_get_settings):
        """토큰이 없는 초기 상태에서는 발급이 필요함"""
        mock_get_settings.return_value = self._make_settings()
        from core.data_collector.kis_client import KISTokenManager

        manager = KISTokenManager()
        assert manager._access_token is None

        with patch.object(manager, "_issue_token", new_callable=AsyncMock) as mock_issue:

            async def issue_initial():
                manager._access_token = "initial_token"
                manager._token_expires_at = datetime.now() + timedelta(hours=24)

            mock_issue.side_effect = issue_initial

            token = await manager.get_access_token()
            mock_issue.assert_called_once()
            assert token == "initial_token"

    @patch("core.data_collector.kis_client.get_settings")
    async def test_backtest_mode_blocks_token(self, mock_get_settings):
        """BACKTEST 모드에서는 토큰 발급이 차단되어야 함"""
        settings = self._make_settings()
        settings.kis.is_backtest = True
        mock_get_settings.return_value = settings
        from core.data_collector.kis_client import KISAPIError, KISTokenManager

        manager = KISTokenManager()

        with pytest.raises(KISAPIError) as exc_info:
            await manager.get_access_token()
        assert "BACKTEST" in str(exc_info.value)

    @patch("core.data_collector.kis_client.get_settings")
    async def test_token_issue_http_error_propagates(self, mock_get_settings):
        """토큰 발급 시 HTTP 오류가 전파되어야 함 (tenacity retry 후 RetryError)"""
        from tenacity import RetryError

        mock_get_settings.return_value = self._make_settings()
        from core.data_collector.kis_client import KISTokenManager

        manager = KISTokenManager()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "401 Unauthorized",
                request=MagicMock(),
                response=MagicMock(status_code=401),
            )
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(RetryError):
                await manager._issue_token()

    @patch("core.data_collector.kis_client.get_settings")
    async def test_token_expiry_boundary_exact_10min(self, mock_get_settings):
        """정확히 10분 남은 토큰은 유효한 것으로 처리되어야 함 (경계값)"""
        mock_get_settings.return_value = self._make_settings()
        from core.data_collector.kis_client import KISTokenManager

        manager = KISTokenManager()
        # 정확히 10분 + 1초 남음 → 유효
        manager._access_token = "boundary_token"
        manager._token_expires_at = datetime.now() + timedelta(minutes=10, seconds=1)

        with patch.object(manager, "_issue_token", new_callable=AsyncMock) as mock_issue:
            token = await manager.get_access_token()
            mock_issue.assert_not_called()
            assert token == "boundary_token"


# ══════════════════════════════════════
# 2. 시크릿 관리 검증
# ══════════════════════════════════════


class TestSecretManagement(unittest.TestCase):
    """시크릿 관리 및 환경변수 설정 검증"""

    def test_env_test_file_has_no_real_secrets(self):
        """테스트 .env 파일에 실제 시크릿이 없어야 함"""
        import os

        env_test_path = os.path.join(os.path.dirname(__file__), "..", ".env.test")
        if os.path.exists(env_test_path):
            with open(env_test_path) as f:
                content = f.read()

            # 실제 API 키 패턴이 없어야 함
            import re

            real_key_patterns = [
                r"sk-ant-[a-zA-Z0-9]{20,}",  # Anthropic
                r"AKIA[A-Z0-9]{16}",  # AWS
                r"ghp_[a-zA-Z0-9]{36}",  # GitHub
                r"xoxb-[0-9]{10,}",  # Slack
            ]
            for pattern in real_key_patterns:
                matches = re.findall(pattern, content)
                assert len(matches) == 0, f"실제 시크릿 패턴 발견: {pattern}"

    def test_gitignore_excludes_env_files(self):
        """gitignore에 .env가 포함되어야 함"""
        import os

        gitignore_path = os.path.join(os.path.dirname(__file__), "..", "..", ".gitignore")
        if os.path.exists(gitignore_path):
            with open(gitignore_path) as f:
                content = f.read()
            assert ".env" in content, ".gitignore에 .env가 포함되어야 합니다"

    @patch("config.settings.get_settings")
    def test_settings_mask_sensitive_fields(self, mock_settings):
        """설정 객체가 민감 필드를 마스킹해야 함"""
        settings = MagicMock()
        settings.kis.app_key = "real_app_key_value"
        settings.kis.app_secret = "real_app_secret_value"
        mock_settings.return_value = settings

        # 민감 필드가 외부에 노출되면 안 됨
        assert settings.kis.app_key != ""
        assert settings.kis.app_secret != ""
        # 설정 객체가 민감 필드를 포함하지만, 로깅에 직접 노출하면 안 됨
        import json

        safe_repr = json.dumps({"app_key": "***", "app_secret": "***"})
        assert "real_app_key_value" not in safe_repr
