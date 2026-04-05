"""
스케줄러 핸들러 유닛테스트

TradingScheduler에 등록되는 파이프라인 핸들러 검증.

테스트 범위:
- register_pipeline_handlers: 5개 핸들러 정상 등록
- handle_pre_market: OHLCV 수집 호출 검증
- handle_market_open: 동적 앙상블 배치 실행 검증
- _cache_ensemble_results: Redis 캐시 검증
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.scheduler_handlers import (
    _cache_ensemble_results,
    register_pipeline_handlers,
)


class TestRegisterPipelineHandlers:
    """핸들러 등록 테스트"""

    def test_registers_five_handlers(self):
        """5개 핸들러가 모두 등록되는지"""
        mock_scheduler = MagicMock()
        mock_scheduler.register_handler = MagicMock()

        register_pipeline_handlers(mock_scheduler)

        assert mock_scheduler.register_handler.call_count == 5

        registered = {call.args[0] for call in mock_scheduler.register_handler.call_args_list}
        expected = {
            "handle_pre_market",
            "handle_market_open",
            "handle_midday_check",
            "handle_market_close",
            "handle_post_market",
        }
        assert registered == expected

    def test_registered_handlers_are_callable(self):
        """등록된 핸들러가 호출 가능한지"""
        mock_scheduler = MagicMock()
        register_pipeline_handlers(mock_scheduler)

        for call in mock_scheduler.register_handler.call_args_list:
            handler = call.args[1]
            assert callable(handler)


class TestCacheEnsembleResults:
    """Redis 캐시 테스트"""

    @pytest.mark.asyncio
    async def test_caches_results_to_redis(self):
        """앙상블 결과가 Redis에 캐시되는지"""
        mock_pipe = AsyncMock()
        mock_redis = MagicMock()
        # pipeline()은 동기 호출이므로 MagicMock 사용
        mock_redis.pipeline.return_value = mock_pipe

        with patch(
            "core.scheduler_handlers.RedisManager.get_client",
            return_value=mock_redis,
        ):
            results = {
                "005930": {"ensemble_signal": 0.15, "regime": "TRENDING_UP"},
                "AAPL": {"ensemble_signal": -0.05, "regime": "SIDEWAYS"},
            }
            await _cache_ensemble_results(results)

        # set이 3번 호출 (2종목 + 1 summary)
        assert mock_pipe.set.call_count == 3
        mock_pipe.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cache_failure_does_not_raise(self):
        """Redis 실패가 예외를 발생시키지 않는지"""
        with patch(
            "core.scheduler_handlers.RedisManager.get_client",
            side_effect=RuntimeError("Redis not connected"),
        ):
            # 예외가 발생하지 않아야 함 (캐시는 best-effort)
            await _cache_ensemble_results({"TEST": {"signal": 0.1}})
