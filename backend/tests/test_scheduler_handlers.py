"""
스케줄러 핸들러 유닛테스트

TradingScheduler에 등록되는 파이프라인 핸들러 검증.

테스트 범위:
- register_pipeline_handlers: 5개 핸들러 정상 등록
- handle_pre_market: OHLCV 수집 + 뉴스/공시 수집 호출 검증
- handle_market_open: 동적 앙상블 배치 실행 검증
- _cache_ensemble_results: Redis 캐시 검증
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.scheduler_handlers import (
    _cache_ensemble_results,
    handle_pre_market,
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


class TestHandlePreMarketNewsCollection:
    """handle_pre_market 뉴스 수집 wiring 검증"""

    @pytest.mark.asyncio
    async def test_pre_market_calls_news_collector(self):
        """handle_pre_market이 NewsCollectorService.collect_and_store를 호출하는지"""
        mock_news_result = {
            "total_collected": 50,
            "new_stored": 30,
            "duplicates_skipped": 20,
        }

        with (
            patch("core.scheduler_handlers.async_session_factory") as mock_session_factory,
            patch("core.scheduler_handlers.DailyOHLCVCollector") as mock_ohlcv_cls,
            patch("core.scheduler_handlers.NewsCollectorService") as mock_news_cls,
            patch("core.health_checker.HealthChecker") as mock_health_cls,
            patch("core.trading_guard.TradingGuard") as mock_guard_cls,
        ):
            # OHLCV mock
            mock_session = AsyncMock()
            mock_session_ctx = AsyncMock()
            mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.__aexit__ = AsyncMock(return_value=None)
            mock_session_factory.return_value = mock_session_ctx
            mock_collector = AsyncMock()
            mock_report = MagicMock()
            mock_report.errors = []
            mock_report.to_dict.return_value = {}
            mock_collector.collect_all.return_value = mock_report
            mock_ohlcv_cls.return_value = mock_collector

            # News mock
            mock_news = AsyncMock()
            mock_news.collect_and_store.return_value = mock_news_result
            mock_news_cls.return_value = mock_news

            # Health mock
            mock_health = AsyncMock()
            mock_health_result = MagicMock()
            mock_health_result.overall_status.value = "healthy"
            mock_health_result.ready_for_trading = True
            mock_health.run_full_check.return_value = mock_health_result
            mock_health_cls.return_value = mock_health

            # Guard mock
            mock_guard = MagicMock()
            mock_guard_cls.return_value = mock_guard

            result = await handle_pre_market()

        mock_news.collect_and_store.assert_called_once()
        assert result["news_collection"] == mock_news_result

    @pytest.mark.asyncio
    async def test_pre_market_news_failure_does_not_block(self):
        """뉴스 수집 실패가 다른 단계를 차단하지 않는지"""
        with (
            patch("core.scheduler_handlers.async_session_factory") as mock_session_factory,
            patch("core.scheduler_handlers.DailyOHLCVCollector") as mock_ohlcv_cls,
            patch("core.scheduler_handlers.NewsCollectorService") as mock_news_cls,
            patch("core.health_checker.HealthChecker") as mock_health_cls,
            patch("core.trading_guard.TradingGuard") as mock_guard_cls,
        ):
            # OHLCV mock
            mock_session = AsyncMock()
            mock_session_ctx = AsyncMock()
            mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.__aexit__ = AsyncMock(return_value=None)
            mock_session_factory.return_value = mock_session_ctx
            mock_collector = AsyncMock()
            mock_report = MagicMock()
            mock_report.errors = []
            mock_report.to_dict.return_value = {}
            mock_collector.collect_all.return_value = mock_report
            mock_ohlcv_cls.return_value = mock_collector

            # News mock — 실패
            mock_news = AsyncMock()
            mock_news.collect_and_store.side_effect = Exception("RSS timeout")
            mock_news_cls.return_value = mock_news

            # Health mock
            mock_health = AsyncMock()
            mock_health_result = MagicMock()
            mock_health_result.overall_status.value = "healthy"
            mock_health_result.ready_for_trading = True
            mock_health.run_full_check.return_value = mock_health_result
            mock_health_cls.return_value = mock_health

            # Guard mock
            mock_guard = MagicMock()
            mock_guard_cls.return_value = mock_guard

            result = await handle_pre_market()

        # 뉴스 실패 에러가 기록되지만
        assert "news_collection_error" in result
        # 건전성 검사와 TradingGuard 리셋은 정상 실행
        assert result["health_status"] == "healthy"
        assert result["daily_reset"] is True


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


class TestHandlePreMarketEconomicCollection:
    """handle_pre_market 경제지표 수집 wiring 검증"""

    @pytest.mark.asyncio
    async def test_pre_market_calls_economic_collector(self):
        """handle_pre_market이 EconomicCollectorService.collect_and_store를 호출하는지"""
        mock_econ_result = {
            "fred_count": 9,
            "ecos_count": 0,
            "total": 9,
        }

        with (
            patch("core.scheduler_handlers.async_session_factory") as mock_session_factory,
            patch("core.scheduler_handlers.DailyOHLCVCollector") as mock_ohlcv_cls,
            patch("core.scheduler_handlers.NewsCollectorService") as mock_news_cls,
            patch("core.data_collector.economic_collector.EconomicCollectorService") as mock_econ_cls,
            patch("core.health_checker.HealthChecker") as mock_health_cls,
            patch("core.trading_guard.TradingGuard") as mock_guard_cls,
        ):
            # OHLCV mock
            mock_session = AsyncMock()
            mock_session_ctx = AsyncMock()
            mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.__aexit__ = AsyncMock(return_value=None)
            mock_session_factory.return_value = mock_session_ctx
            mock_collector = AsyncMock()
            mock_report = MagicMock()
            mock_report.errors = []
            mock_report.to_dict.return_value = {}
            mock_collector.collect_all.return_value = mock_report
            mock_ohlcv_cls.return_value = mock_collector

            # News mock
            mock_news = AsyncMock()
            mock_news.collect_and_store.return_value = {
                "total_collected": 0,
                "new_stored": 0,
                "duplicates_skipped": 0,
            }
            mock_news_cls.return_value = mock_news

            # Economic mock
            mock_econ = AsyncMock()
            mock_econ.collect_and_store.return_value = mock_econ_result
            mock_econ_cls.return_value = mock_econ

            # Health mock
            mock_health = AsyncMock()
            mock_health_result = MagicMock()
            mock_health_result.overall_status.value = "healthy"
            mock_health_result.ready_for_trading = True
            mock_health.run_full_check.return_value = mock_health_result
            mock_health_cls.return_value = mock_health

            # Guard mock
            mock_guard = MagicMock()
            mock_guard_cls.return_value = mock_guard

            result = await handle_pre_market()

        mock_econ.collect_and_store.assert_called_once()
        assert result["economic_collection"] == mock_econ_result
        assert result["economic_collection"]["fred_count"] == 9

    @pytest.mark.asyncio
    async def test_pre_market_economic_failure_does_not_block(self):
        """경제지표 수집 실패가 건전성 검사와 TradingGuard 리셋을 차단하지 않는지"""
        with (
            patch("core.scheduler_handlers.async_session_factory") as mock_session_factory,
            patch("core.scheduler_handlers.DailyOHLCVCollector") as mock_ohlcv_cls,
            patch("core.scheduler_handlers.NewsCollectorService") as mock_news_cls,
            patch("core.data_collector.economic_collector.EconomicCollectorService") as mock_econ_cls,
            patch("core.health_checker.HealthChecker") as mock_health_cls,
            patch("core.trading_guard.TradingGuard") as mock_guard_cls,
        ):
            # OHLCV mock
            mock_session = AsyncMock()
            mock_session_ctx = AsyncMock()
            mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.__aexit__ = AsyncMock(return_value=None)
            mock_session_factory.return_value = mock_session_ctx
            mock_collector = AsyncMock()
            mock_report = MagicMock()
            mock_report.errors = []
            mock_report.to_dict.return_value = {}
            mock_collector.collect_all.return_value = mock_report
            mock_ohlcv_cls.return_value = mock_collector

            # News mock
            mock_news = AsyncMock()
            mock_news.collect_and_store.return_value = {
                "total_collected": 0,
                "new_stored": 0,
                "duplicates_skipped": 0,
            }
            mock_news_cls.return_value = mock_news

            # Economic mock — 실패
            mock_econ = AsyncMock()
            mock_econ.collect_and_store.side_effect = Exception("FRED API timeout")
            mock_econ_cls.return_value = mock_econ

            # Health mock
            mock_health = AsyncMock()
            mock_health_result = MagicMock()
            mock_health_result.overall_status.value = "healthy"
            mock_health_result.ready_for_trading = True
            mock_health.run_full_check.return_value = mock_health_result
            mock_health_cls.return_value = mock_health

            # Guard mock
            mock_guard = MagicMock()
            mock_guard_cls.return_value = mock_guard

            result = await handle_pre_market()

        # 경제지표 실패 에러가 기록되지만
        assert "economic_collection_error" in result
        # 건전성 검사와 TradingGuard 리셋은 정상 실행
        assert result["health_status"] == "healthy"
        assert result["daily_reset"] is True
