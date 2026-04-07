"""KIS startup jittered token issue 단위 테스트.

검증 대상:
    - jitter_max > 0 이면 sleep_fn 이 [0, jitter_max) 구간 난수로 호출됨
    - jitter_max <= 0 이면 sleep_fn 미호출 (즉시 발급)
    - 성공 시 token issue 가 정확히 1회 호출되고 client 가 반환됨
    - factory / get_access_token 예외는 그대로 전파 (lifespan 이 잡아 degraded 처리)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.data_collector.kis_client import KISAPIError, KISClient
from core.data_collector.kis_startup import (
    DEFAULT_JITTER_MAX_SECONDS,
    jittered_token_issue,
)


def _make_client_factory(token_side_effect=None) -> tuple:
    """KISClient mock + factory 페어를 만든다."""
    client = MagicMock(spec=KISClient)
    token_manager = MagicMock()
    token_manager.get_access_token = AsyncMock(side_effect=token_side_effect)
    client._token_manager = token_manager

    factory = MagicMock(return_value=client)
    return factory, client, token_manager


class TestJitteredTokenIssue:
    """jittered_token_issue() 핵심 동작."""

    @pytest.mark.asyncio
    async def test_default_jitter_max_constant(self):
        """기본 jitter 상한이 15초로 노출되어 있어야 한다 (운영 정책)."""
        assert DEFAULT_JITTER_MAX_SECONDS == 15.0

    @pytest.mark.asyncio
    async def test_zero_jitter_skips_sleep_and_issues_immediately(self):
        factory, client, token_manager = _make_client_factory()
        sleep_fn = AsyncMock()
        random_fn = MagicMock()

        result = await jittered_token_issue(
            client_factory=factory,
            jitter_max_seconds=0,
            sleep_fn=sleep_fn,
            random_fn=random_fn,
        )

        assert result is client
        sleep_fn.assert_not_called()
        random_fn.assert_not_called()
        token_manager.get_access_token.assert_awaited_once()
        factory.assert_called_once()

    @pytest.mark.asyncio
    async def test_negative_jitter_also_skips_sleep(self):
        """음수 jitter 도 비활성과 동일하게 동작 (방어적 처리)."""
        factory, client, token_manager = _make_client_factory()
        sleep_fn = AsyncMock()

        result = await jittered_token_issue(
            client_factory=factory,
            jitter_max_seconds=-5,
            sleep_fn=sleep_fn,
        )

        assert result is client
        sleep_fn.assert_not_called()
        token_manager.get_access_token.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_positive_jitter_sleeps_for_random_value(self):
        factory, client, token_manager = _make_client_factory()
        sleep_fn = AsyncMock()
        random_fn = MagicMock(return_value=7.5)

        result = await jittered_token_issue(
            client_factory=factory,
            jitter_max_seconds=15.0,
            sleep_fn=sleep_fn,
            random_fn=random_fn,
        )

        assert result is client
        random_fn.assert_called_once_with(0.0, 15.0)
        sleep_fn.assert_awaited_once_with(7.5)
        token_manager.get_access_token.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_token_issue_failure_propagates(self):
        """KISAPIError 가 그대로 전파되어 lifespan 이 degraded 처리할 수 있어야 함."""
        factory, _client, _ = _make_client_factory(token_side_effect=KISAPIError(code="EGW00133", message="rate limit"))
        sleep_fn = AsyncMock()

        with pytest.raises(KISAPIError) as exc_info:
            await jittered_token_issue(
                client_factory=factory,
                jitter_max_seconds=0,
                sleep_fn=sleep_fn,
            )

        assert exc_info.value.code == "EGW00133"

    @pytest.mark.asyncio
    async def test_factory_exception_propagates(self):
        """KISClient 생성 자체가 실패해도 예외가 그대로 전파."""

        def failing_factory():
            raise RuntimeError("settings missing")

        sleep_fn = AsyncMock()

        with pytest.raises(RuntimeError, match="settings missing"):
            await jittered_token_issue(
                client_factory=failing_factory,
                jitter_max_seconds=0,
                sleep_fn=sleep_fn,
            )

    @pytest.mark.asyncio
    async def test_jitter_uses_uniform_distribution_bounds(self):
        """random_fn 이 정확히 (0.0, jitter_max_seconds) 로 호출되는지 검증."""
        factory, _, _ = _make_client_factory()
        sleep_fn = AsyncMock()
        random_fn = MagicMock(return_value=3.14)

        await jittered_token_issue(
            client_factory=factory,
            jitter_max_seconds=30.0,
            sleep_fn=sleep_fn,
            random_fn=random_fn,
        )

        random_fn.assert_called_once_with(0.0, 30.0)
        sleep_fn.assert_awaited_once_with(3.14)
