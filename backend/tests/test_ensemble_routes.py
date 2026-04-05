"""
동적 앙상블 API 라우트 유닛테스트

다음 4개 엔드포인트를 테스트합니다:
  1. GET  /cached          - 캐시 요약 조회
  2. GET  /cached/{ticker} - 종목별 캐시 조회
  3. POST /run             - 단일 종목 실시간 실행
  4. POST /batch           - 유니버스 배치 실행

모든 외부 의존성 (Redis, DB, DynamicEnsembleRunner) 은 mock으로 처리됩니다.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
class TestGetCachedSummary:
    """GET /cached 캐시 요약 조회"""

    async def test_returns_summary_from_redis(self):
        """Redis에 요약이 있으면 정상 반환"""
        from api.routes.ensemble import get_cached_summary

        mock_redis = AsyncMock()
        mock_redis.get.return_value = json.dumps(
            {
                "updated_at": "2026-04-06T01:00:00+00:00",
                "total_tickers": 3,
                "tickers": ["005930", "AAPL", "035720"],
            }
        )

        with patch(
            "api.routes.ensemble.RedisManager.get_client",
            return_value=mock_redis,
        ):
            resp = await get_cached_summary(current_user="test")

        assert resp.success is True
        assert resp.data.total_tickers == 3
        assert "005930" in resp.data.tickers

    async def test_returns_empty_when_no_cache(self):
        """Redis에 캐시가 없으면 빈 요약 반환"""
        from api.routes.ensemble import get_cached_summary

        mock_redis = AsyncMock()
        mock_redis.get.return_value = None

        with patch(
            "api.routes.ensemble.RedisManager.get_client",
            return_value=mock_redis,
        ):
            resp = await get_cached_summary(current_user="test")

        assert resp.success is True
        assert resp.data.total_tickers == 0

    async def test_returns_error_when_redis_disconnected(self):
        """Redis 미연결 시 에러 응답"""
        from api.routes.ensemble import get_cached_summary

        with patch(
            "api.routes.ensemble.RedisManager.get_client",
            side_effect=RuntimeError("Redis not connected"),
        ):
            resp = await get_cached_summary(current_user="test")

        assert resp.success is False
        assert "Redis" in resp.message


@pytest.mark.asyncio
class TestGetCachedTicker:
    """GET /cached/{ticker} 종목별 캐시 조회"""

    async def test_returns_cached_result(self):
        """캐시된 종목 결과 정상 반환"""
        from api.routes.ensemble import get_cached_ticker

        cached_data = {
            "ticker": "005930",
            "country": "KR",
            "ensemble_signal": 0.1523,
            "regime": "TRENDING_UP",
            "weights": {"MR": 0.2, "TF": 0.5, "RP": 0.3},
            "adx": 32.15,
            "vol_percentile": 0.45,
            "vol_scalar": 0.88,
            "ohlcv_days": 300,
        }

        mock_redis = AsyncMock()
        mock_redis.get.return_value = json.dumps(cached_data)

        with patch(
            "api.routes.ensemble.RedisManager.get_client",
            return_value=mock_redis,
        ):
            resp = await get_cached_ticker(ticker="005930", current_user="test")

        assert resp.success is True
        assert resp.data.ensemble_signal == 0.1523
        assert resp.data.regime == "TRENDING_UP"
        assert resp.data.cached is True

    async def test_returns_not_found_when_no_cache(self):
        """캐시에 없는 종목은 실패 응답"""
        from api.routes.ensemble import get_cached_ticker

        mock_redis = AsyncMock()
        mock_redis.get.return_value = None

        with patch(
            "api.routes.ensemble.RedisManager.get_client",
            return_value=mock_redis,
        ):
            resp = await get_cached_ticker(ticker="MISSING", current_user="test")

        assert resp.success is False
        assert "MISSING" in resp.message

    async def test_returns_error_for_failed_ticker(self):
        """에러가 캐시된 종목은 실패 응답"""
        from api.routes.ensemble import get_cached_ticker

        mock_redis = AsyncMock()
        mock_redis.get.return_value = json.dumps({"error": "OHLCV 데이터 부족 (150일 < 200일)"})

        with patch(
            "api.routes.ensemble.RedisManager.get_client",
            return_value=mock_redis,
        ):
            resp = await get_cached_ticker(ticker="FAIL", current_user="test")

        assert resp.success is False
        assert "데이터 부족" in resp.message


@pytest.mark.asyncio
class TestRunSingleEnsemble:
    """POST /run 단일 종목 실시간 실행"""

    async def test_run_success(self):
        """정상 실행 시 앙상블 결과 반환"""
        from api.routes.ensemble import run_single_ensemble

        mock_result = MagicMock()
        mock_result.to_summary_dict.return_value = {
            "ticker": "005930",
            "country": "KR",
            "ensemble_signal": 0.1234,
            "regime": "TRENDING_UP",
            "weights": {"MR": 0.2, "TF": 0.5, "RP": 0.3},
            "adx": 28.5,
            "vol_percentile": 0.55,
            "vol_scalar": 0.92,
            "ohlcv_days": 300,
        }

        with patch("api.routes.ensemble.DynamicEnsembleRunner") as MockRunner:
            instance = AsyncMock()
            instance.run.return_value = mock_result
            MockRunner.return_value = instance

            mock_db = AsyncMock()
            resp = await run_single_ensemble(
                ticker="005930",
                country="KR",
                lookback_days=300,
                current_user="test",
                db=mock_db,
            )

        assert resp.success is True
        assert resp.data.ticker == "005930"
        assert resp.data.ensemble_signal == 0.1234
        assert resp.data.regime == "TRENDING_UP"
        assert resp.data.weights.TF == 0.5

    async def test_run_insufficient_data(self):
        """데이터 부족 시 실패 응답"""
        from api.routes.ensemble import run_single_ensemble

        with patch("api.routes.ensemble.DynamicEnsembleRunner") as MockRunner:
            instance = AsyncMock()
            instance.run.side_effect = ValueError("005930: OHLCV 데이터 부족 (100일 < 200일)")
            MockRunner.return_value = instance

            mock_db = AsyncMock()
            resp = await run_single_ensemble(
                ticker="005930",
                country="KR",
                lookback_days=300,
                current_user="test",
                db=mock_db,
            )

        assert resp.success is False
        assert "데이터 부족" in resp.message

    async def test_run_unexpected_error(self):
        """예상치 못한 에러 시 실패 응답"""
        from api.routes.ensemble import run_single_ensemble

        with patch("api.routes.ensemble.DynamicEnsembleRunner") as MockRunner:
            instance = AsyncMock()
            instance.run.side_effect = RuntimeError("DB 연결 실패")
            MockRunner.return_value = instance

            mock_db = AsyncMock()
            resp = await run_single_ensemble(
                ticker="005930",
                country="KR",
                lookback_days=300,
                current_user="test",
                db=mock_db,
            )

        assert resp.success is False
        assert "실행 실패" in resp.message


@pytest.mark.asyncio
class TestRunBatchEnsemble:
    """POST /batch 유니버스 배치 실행"""

    async def test_batch_with_empty_universe(self):
        """활성 종목이 없을 때 빈 결과 반환"""
        from api.routes.ensemble import run_batch_ensemble

        with patch(
            "core.scheduler_handlers._load_universe_grouped",
            new_callable=AsyncMock,
            return_value={},
        ):
            mock_db = AsyncMock()
            resp = await run_batch_ensemble(
                country=None,
                lookback_days=300,
                cache_results=False,
                current_user="test",
                db=mock_db,
            )

        assert resp.success is True
        assert resp.data.total_tickers == 0

    async def test_batch_with_country_filter(self):
        """국가 필터 적용 시 해당 국가만 실행"""
        from api.routes.ensemble import run_batch_ensemble

        mock_result = MagicMock()
        mock_result.to_summary_dict.return_value = {
            "ticker": "005930",
            "country": "KR",
            "ensemble_signal": 0.15,
            "regime": "SIDEWAYS",
            "weights": {"MR": 0.33, "TF": 0.33, "RP": 0.34},
            "adx": 20.0,
            "vol_percentile": 0.5,
            "vol_scalar": 1.0,
            "ohlcv_days": 300,
        }

        with (
            patch(
                "core.scheduler_handlers._load_universe_grouped",
                new_callable=AsyncMock,
                return_value={
                    "KR": [{"ticker": "005930", "market": "KRX"}],
                    "US": [{"ticker": "AAPL", "market": "NASDAQ"}],
                },
            ),
            patch("api.routes.ensemble.DynamicEnsembleRunner") as MockRunner,
            patch(
                "core.scheduler_handlers._cache_ensemble_results",
                new_callable=AsyncMock,
            ),
        ):
            instance = AsyncMock()
            instance.run.return_value = mock_result
            MockRunner.return_value = instance

            mock_db = AsyncMock()
            resp = await run_batch_ensemble(
                country="KR",
                lookback_days=300,
                cache_results=False,
                current_user="test",
                db=mock_db,
            )

        assert resp.success is True
        assert resp.data.total_tickers == 1
        assert resp.data.succeeded == 1
        assert "005930" in resp.data.results

    async def test_batch_partial_failure(self):
        """일부 종목 실패 시 성공/실패 카운트 정확"""
        from api.routes.ensemble import run_batch_ensemble

        mock_result = MagicMock()
        mock_result.to_summary_dict.return_value = {
            "ticker": "005930",
            "country": "KR",
            "ensemble_signal": 0.15,
            "regime": "SIDEWAYS",
            "weights": {"MR": 0.33, "TF": 0.33, "RP": 0.34},
            "adx": 20.0,
            "vol_percentile": 0.5,
            "vol_scalar": 1.0,
            "ohlcv_days": 300,
        }

        call_count = 0

        async def mock_run(ticker, country, lookback_days):
            nonlocal call_count
            call_count += 1
            if ticker == "FAIL_TICKER":
                raise ValueError("데이터 부족")
            return mock_result

        with (
            patch(
                "core.scheduler_handlers._load_universe_grouped",
                new_callable=AsyncMock,
                return_value={
                    "KR": [
                        {"ticker": "005930", "market": "KRX"},
                        {"ticker": "FAIL_TICKER", "market": "KRX"},
                    ],
                },
            ),
            patch("api.routes.ensemble.DynamicEnsembleRunner") as MockRunner,
            patch(
                "core.scheduler_handlers._cache_ensemble_results",
                new_callable=AsyncMock,
            ),
        ):
            instance = AsyncMock()
            instance.run.side_effect = mock_run
            MockRunner.return_value = instance

            mock_db = AsyncMock()
            resp = await run_batch_ensemble(
                country=None,
                lookback_days=300,
                cache_results=False,
                current_user="test",
                db=mock_db,
            )

        assert resp.success is True
        assert resp.data.total_tickers == 2
        assert resp.data.succeeded == 1
        assert resp.data.failed == 1
        assert "FAIL_TICKER" in resp.data.errors
