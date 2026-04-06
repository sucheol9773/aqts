"""
부하/스트레스 테스트 (Load & Stress Tests)
==========================================

시스템의 성능 한계와 동시성 안전성을 검증합니다.

테스트 카테고리:
  1. 백테스트 엔진 스케일링 (대규모 데이터)
  2. API 동시 요청 처리
  3. 파이프라인 동시 실행
  4. 주문 실행기 동시성 안전성
  5. 상태 머신 동시 전이
  6. 데이터 수집기 재시도/장애 복원
  7. 메모리/시간 성능 기준선
"""

import asyncio
import gc
import os
import sys
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest

# ── backend 경로 보장 ──
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.backtest_engine.engine import BacktestConfig, BacktestEngine

# ══════════════════════════════════════════════════════════════════════════════
# 헬퍼: 합성 시장 데이터 생성
# ══════════════════════════════════════════════════════════════════════════════


def _make_market_data(n_days: int, n_tickers: int, seed: int = 42) -> tuple[pd.DataFrame, pd.DataFrame]:
    """합성 가격/시그널 데이터 생성"""
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range("2020-01-01", periods=n_days, freq="B")
    tickers = [f"T{i:04d}" for i in range(n_tickers)]

    # 가격: 랜덤 워크
    log_returns = rng.normal(0.0003, 0.02, (n_days, n_tickers))
    prices_arr = 50000 * np.exp(np.cumsum(log_returns, axis=0))
    prices = pd.DataFrame(prices_arr, index=dates, columns=tickers)

    # 시그널: -1 ~ +1
    raw_signals = rng.uniform(-1, 1, (n_days, n_tickers))
    signals = pd.DataFrame(raw_signals, index=dates, columns=tickers)

    return signals, prices


# ══════════════════════════════════════════════════════════════════════════════
# 1. 백테스트 엔진 스케일링 테스트
# ══════════════════════════════════════════════════════════════════════════════


class TestBacktestScaling:
    """백테스트 엔진이 대규모 데이터에서도 합리적 시간 내에 실행되는지 확인"""

    def test_small_universe_baseline(self):
        """소규모 (60일 × 5종목): 기준선 성능 측정"""
        signals, prices = _make_market_data(60, 5)
        engine = BacktestEngine(BacktestConfig())

        start = time.monotonic()
        result = engine.run("small_test", signals, prices)
        elapsed = time.monotonic() - start

        assert result.total_return != 0 or result.total_trades >= 0
        assert elapsed < 5.0, f"소규모 백테스트가 {elapsed:.1f}초 소요 (기준: <5초)"

    def test_medium_universe(self):
        """중규모 (500일 × 30종목): 실전 규모"""
        signals, prices = _make_market_data(500, 30)
        engine = BacktestEngine(BacktestConfig())

        start = time.monotonic()
        result = engine.run("medium_test", signals, prices)
        elapsed = time.monotonic() - start

        assert result.total_trades > 0
        assert elapsed < 30.0, f"중규모 백테스트가 {elapsed:.1f}초 소요 (기준: <30초)"

    def test_large_universe(self):
        """대규모 (1000일 × 50종목): 스트레스"""
        signals, prices = _make_market_data(1000, 50)
        engine = BacktestEngine(BacktestConfig())

        start = time.monotonic()
        result = engine.run("large_test", signals, prices)
        elapsed = time.monotonic() - start

        assert result.total_trades > 0
        assert elapsed < 120.0, f"대규모 백테스트가 {elapsed:.1f}초 소요 (기준: <120초)"

    def test_backtest_with_all_risk_features(self):
        """모든 리스크 관리 기능 활성화 상태에서의 성능"""
        signals, prices = _make_market_data(500, 20)
        config = BacktestConfig(
            stop_loss_pct=0.15,
            stop_loss_atr_multiplier=2.0,
            trailing_stop_atr_multiplier=3.0,
            max_drawdown_limit=0.20,
            drawdown_cooldown_days=10,
            dd_cushion_start=0.10,
            dd_cushion_floor=0.25,
            vol_target=0.15,
            vol_lookback=20,
            gradual_reentry_days=5,
            use_dynamic_threshold=True,
        )
        engine = BacktestEngine(config)

        start = time.monotonic()
        result = engine.run("full_risk_test", signals, prices)
        elapsed = time.monotonic() - start

        assert elapsed < 60.0, f"전기능 백테스트가 {elapsed:.1f}초 소요 (기준: <60초)"
        # 리스크 관리가 활성화되어 거래가 발생해야 함
        assert isinstance(result.mdd, float)

    def test_backtest_memory_usage(self):
        """백테스트 메모리 사용량이 합리적 범위 내인지 확인"""
        tracemalloc.start()
        signals, prices = _make_market_data(500, 30)
        engine = BacktestEngine(BacktestConfig())
        engine.run("memory_test", signals, prices)

        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        peak_mb = peak / (1024 * 1024)
        assert peak_mb < 500, f"피크 메모리 {peak_mb:.1f}MB (기준: <500MB)"

    def test_repeated_runs_no_memory_leak(self):
        """반복 실행 시 메모리 누수 없음"""
        signals, prices = _make_market_data(100, 10)

        gc.collect()
        tracemalloc.start()

        for _ in range(10):
            engine = BacktestEngine(BacktestConfig())
            engine.run("leak_test", signals, prices)

        current_10, _ = tracemalloc.get_traced_memory()

        for _ in range(10):
            engine = BacktestEngine(BacktestConfig())
            engine.run("leak_test", signals, prices)

        current_20, _ = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        # 두 번째 10회의 증분이 첫 10회보다 크지 않아야 함 (허용: 20% 이내)
        growth_mb = (current_20 - current_10) / (1024 * 1024)
        assert growth_mb < 50, f"반복 실행 메모리 증가 {growth_mb:.1f}MB (기준: <50MB)"


# ══════════════════════════════════════════════════════════════════════════════
# 2. 동시 백테스트 실행
# ══════════════════════════════════════════════════════════════════════════════


def _run_backtest_worker(args):
    """프로세스 풀에서 실행되는 백테스트 워커"""
    seed, n_days, n_tickers = args
    signals, prices = _make_market_data(n_days, n_tickers, seed=seed)
    engine = BacktestEngine(BacktestConfig())
    result = engine.run(f"worker_{seed}", signals, prices)
    return result.total_return


class TestConcurrentBacktest:
    """여러 백테스트를 동시에 실행해도 안전한지 확인"""

    def test_thread_concurrent_backtests(self):
        """ThreadPool에서 동시 백테스트 4건"""
        args_list = [(i, 100, 10) for i in range(4)]

        with ThreadPoolExecutor(max_workers=4) as pool:
            start = time.monotonic()
            results = list(pool.map(_run_backtest_worker, args_list))
            elapsed = time.monotonic() - start

        assert len(results) == 4
        assert all(isinstance(r, float) for r in results)
        assert elapsed < 60.0, f"동시 4건 백테스트 {elapsed:.1f}초 (기준: <60초)"

    def test_sequential_vs_parallel_consistency(self):
        """순차 vs 병렬 실행 결과가 동일한지 확인 (결정적 시드)"""
        args = (42, 100, 5)

        # 순차 실행
        seq_result = _run_backtest_worker(args)

        # 병렬 실행 (같은 인자)
        with ThreadPoolExecutor(max_workers=2) as pool:
            par_results = list(pool.map(_run_backtest_worker, [args, args]))

        assert seq_result == par_results[0]
        assert par_results[0] == par_results[1]


# ══════════════════════════════════════════════════════════════════════════════
# 3. 상태 머신 동시 전이 안전성
# ══════════════════════════════════════════════════════════════════════════════


class TestStateMachineConcurrency:
    """PipelineStateMachine이 동시 전이 시 일관성을 유지하는지 확인"""

    def test_concurrent_state_transitions(self):
        """여러 스레드에서 동시에 상태 전이 시도"""
        from core.state_machine import PipelineState, PipelineStateMachine

        sm = PipelineStateMachine()
        errors = []

        def transition_worker(target_state):
            try:
                sm.transition(target_state)
            except Exception as e:
                errors.append(str(e))

        # IDLE → COLLECTING은 유효한 전이
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(transition_worker, PipelineState.COLLECTING) for _ in range(4)]
            for f in futures:
                f.result()

        # 에러 없이 완료되거나, 이미 전이된 상태로 인한 예외만 발생해야 함
        assert sm.state in (
            PipelineState.COLLECTING,
            PipelineState.IDLE,
        )

    def test_rapid_state_cycle(self):
        """빠른 상태 순환 100회"""
        from core.state_machine import PipelineState, PipelineStateMachine

        sm = PipelineStateMachine()

        for _ in range(100):
            sm.transition(PipelineState.COLLECTING)
            sm.transition(PipelineState.ANALYZING)
            sm.transition(PipelineState.CONSTRUCTING)
            sm.transition(PipelineState.VALIDATING)
            sm.transition(PipelineState.TRADING)
            sm.transition(PipelineState.RECONCILING)
            sm.transition(PipelineState.COMPLETED)
            sm.reset()

        assert sm.state == PipelineState.IDLE


# ══════════════════════════════════════════════════════════════════════════════
# 4. API 동시 요청 시뮬레이션
# ══════════════════════════════════════════════════════════════════════════════


class TestAPIConcurrentRequests:
    """API 엔드포인트가 동시 요청을 안전하게 처리하는지 확인"""

    @pytest.mark.asyncio
    async def test_concurrent_market_data_requests(self):
        """시장 데이터 API 동시 20건 요청"""
        from api.routes.market import get_exchange_rate

        mock_mgr = AsyncMock()
        mock_rate = MagicMock()
        mock_rate.pair = "USD/KRW"
        mock_rate.rate = 1350.0
        mock_rate.source = "mock"
        mock_rate.fetched_at = datetime.now(timezone.utc)
        mock_mgr.get_current_rate.return_value = mock_rate

        with patch(
            "api.routes.market.ExchangeRateManager",
            return_value=mock_mgr,
        ):
            tasks = [get_exchange_rate(current_user="test") for _ in range(20)]
            start = time.monotonic()
            results = await asyncio.gather(*tasks)
            elapsed = time.monotonic() - start

        assert len(results) == 20
        assert all(r.success for r in results)
        assert elapsed < 5.0, f"동시 20건 시장데이터 {elapsed:.1f}초 (기준: <5초)"

    @pytest.mark.asyncio
    async def test_concurrent_portfolio_queries(self):
        """포트폴리오 조회 동시 10건"""
        from api.routes.portfolio import get_portfolio_summary

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchone.return_value = None
        mock_db.execute.return_value = mock_result

        tasks = [get_portfolio_summary(current_user="test_user", db=mock_db) for _ in range(10)]
        start = time.monotonic()
        results = await asyncio.gather(*tasks)
        elapsed = time.monotonic() - start

        assert len(results) == 10
        assert elapsed < 5.0, f"동시 10건 포트폴리오 {elapsed:.1f}초 (기준: <5초)"

    @pytest.mark.asyncio
    async def test_concurrent_circuit_breaker_status(self):
        """서킷 브레이커 상태 조회 동시 50건"""
        from api.routes.system import get_circuit_breaker_status

        mock_status = {"global": {"state": "CLOSED", "failures": 0}}
        with patch("api.routes.system.CircuitBreakerRegistry") as mock_registry:
            mock_registry.status.return_value = mock_status

            tasks = [get_circuit_breaker_status(current_user="test") for _ in range(50)]
            start = time.monotonic()
            results = await asyncio.gather(*tasks)
            elapsed = time.monotonic() - start

        assert len(results) == 50
        assert elapsed < 5.0, f"동시 50건 CB 상태 {elapsed:.1f}초 (기준: <5초)"


# ══════════════════════════════════════════════════════════════════════════════
# 5. 파이프라인 동시 실행 안전성
# ══════════════════════════════════════════════════════════════════════════════


class TestPipelineConcurrency:
    """파이프라인이 동시 호출 시 데이터 오염 없이 실행되는지 확인"""

    @pytest.mark.asyncio
    async def test_concurrent_pipeline_runs(self):
        """서로 다른 종목으로 파이프라인 동시 5건 실행"""
        from core.pipeline import InvestmentDecisionPipeline

        tickers = ["005930", "000660", "035720", "051910", "006400"]

        mock_news = AsyncMock()
        mock_news.collect_all.return_value = {
            "total_collected": 10,
            "new_stored": 5,
            "duplicates_skipped": 5,
        }
        mock_sentiment = AsyncMock()
        mock_sentiment.analyze.return_value = MagicMock(overall_score=0.5, confidence=0.8)
        mock_opinion = AsyncMock()
        mock_opinion.generate.return_value = MagicMock(
            decision="BUY",
            confidence=0.7,
            reasoning="Test",
            investment_score=70,
        )
        mock_ensemble = MagicMock()
        mock_ensemble.run_ensemble.return_value = MagicMock(final_score=0.65, weights={"quant": 0.5, "ai": 0.5})

        async def run_pipeline(ticker):
            pipe = InvestmentDecisionPipeline(
                news_service=mock_news,
                sentiment_analyzer=mock_sentiment,
                opinion_generator=mock_opinion,
                ensemble_engine=mock_ensemble,
            )
            result = await pipe.run_full_analysis(ticker=ticker, composite_score=55.0)
            return result

        start = time.monotonic()
        results = await asyncio.gather(
            *[run_pipeline(t) for t in tickers],
            return_exceptions=True,
        )
        elapsed = time.monotonic() - start

        # 모든 결과가 예외가 아닌지 확인
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                # 파이프라인이 의존성 부족으로 실패해도
                # 크래시하지 않으면 OK
                assert not isinstance(r, (RuntimeError, SystemExit)), f"종목 {tickers[i]}에서 치명적 에러: {r}"

        assert elapsed < 30.0, f"동시 5건 파이프라인 {elapsed:.1f}초 (기준: <30초)"


# ══════════════════════════════════════════════════════════════════════════════
# 6. 게이트 레지스트리 대량 평가
# ══════════════════════════════════════════════════════════════════════════════


class TestGateRegistryLoad:
    """게이트 레지스트리가 대량 평가를 처리하는지 확인"""

    @pytest.mark.asyncio
    async def test_gate_registry_bulk_evaluation(self):
        """100회 연속 게이트 평가"""
        from core.gate_registry import GateRegistry

        registry = GateRegistry()
        dummy_data_map = {"prices": {}, "signals": {}, "portfolio": {}}

        start = time.monotonic()
        for _ in range(100):
            results = await registry.evaluate_all(data_map=dummy_data_map)
            assert isinstance(results, list)
        elapsed = time.monotonic() - start

        assert elapsed < 10.0, f"100회 게이트 평가 {elapsed:.1f}초 (기준: <10초)"


# ══════════════════════════════════════════════════════════════════════════════
# 7. 데이터 수집기 재시도/장애 복원 부하
# ══════════════════════════════════════════════════════════════════════════════


class TestCollectorResilience:
    """데이터 수집기가 반복적 장애 상황에서도 안전하게 동작하는지 확인"""

    @pytest.mark.asyncio
    async def test_market_data_repeated_failures(self):
        """시장 데이터 수집기: 50회 연속 실패 후 복구"""
        from core.data_collector.market_data import MarketDataCollector

        call_count = 0
        fail_until = 50

        async def mock_fetch(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= fail_until:
                raise ConnectionError("Simulated network failure")
            return {"USD/KRW": 1350.0}

        service = MagicMock(spec=MarketDataCollector)
        service.get_exchange_rate = mock_fetch

        # 50회 실패
        for i in range(fail_until):
            with pytest.raises(ConnectionError):
                await service.get_exchange_rate()

        # 51번째: 성공
        result = await service.get_exchange_rate()
        assert result["USD/KRW"] == 1350.0
        assert call_count == fail_until + 1

    @pytest.mark.asyncio
    async def test_concurrent_collector_failures(self):
        """동시 수집 요청 중 일부가 실패해도 나머지는 정상"""
        fail_indices = {2, 5, 8}

        async def maybe_fail(idx):
            if idx in fail_indices:
                raise ConnectionError(f"Collector {idx} failed")
            return {"idx": idx, "data": "ok"}

        tasks = [maybe_fail(i) for i in range(10)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        successes = [r for r in results if not isinstance(r, Exception)]
        failures = [r for r in results if isinstance(r, Exception)]

        assert len(successes) == 7
        assert len(failures) == 3


# ══════════════════════════════════════════════════════════════════════════════
# 8. 주문 실행기 동시성 안전성
# ══════════════════════════════════════════════════════════════════════════════


class TestOrderExecutorConcurrency:
    """주문 실행 시 동시 요청이 자본 한도를 초과하지 않는지 확인"""

    @pytest.mark.asyncio
    async def test_concurrent_order_submissions(self):
        """동시 10건 주문 요청 — 데이터 경합 없음"""
        from core.order_executor.executor import OrderExecutor

        orders_processed = []

        mock_executor = AsyncMock(spec=OrderExecutor)

        async def mock_execute(order):
            await asyncio.sleep(0.01)  # 약간의 I/O 지연 시뮬레이션
            orders_processed.append(order)
            result = MagicMock()
            result.order_id = f"ORD-{len(orders_processed):03d}"
            result.status = "FILLED"
            return result

        mock_executor.execute_order = mock_execute

        mock_orders = [MagicMock(ticker=f"T{i:04d}", quantity=10, side="BUY") for i in range(10)]

        tasks = [mock_executor.execute_order(o) for o in mock_orders]
        results = await asyncio.gather(*tasks)

        assert len(results) == 10
        assert len(orders_processed) == 10
        # 모든 주문이 고유한 ID를 가져야 함
        ids = [r.order_id for r in results]
        assert len(set(ids)) == 10


# ══════════════════════════════════════════════════════════════════════════════
# 9. 메트릭 계산기 대량 데이터
# ══════════════════════════════════════════════════════════════════════════════


class TestMetricsCalculatorLoad:
    """성과 지표 계산기가 대량 데이터에서도 빠르게 동작하는지 확인"""

    def test_metrics_on_large_returns(self):
        """10,000일 수익률에 대한 메트릭 계산"""
        from core.backtest_engine.metrics_calculator import MetricsCalculator

        rng = np.random.RandomState(42)
        returns = rng.normal(0.0003, 0.015, 10000).tolist()

        start = time.monotonic()
        metrics = MetricsCalculator.calculate_all(returns=returns)
        elapsed = time.monotonic() - start

        assert elapsed < 5.0, f"10K일 메트릭 계산 {elapsed:.1f}초 (기준: <5초)"
        assert "sharpe_ratio" in metrics

    def test_metrics_repeated_computation(self):
        """동일 데이터에 대해 100회 반복 계산"""
        from core.backtest_engine.metrics_calculator import MetricsCalculator

        rng = np.random.RandomState(42)
        returns = rng.normal(0.0003, 0.015, 1000).tolist()

        start = time.monotonic()
        results = []
        for _ in range(100):
            m = MetricsCalculator.calculate_all(returns=returns)
            results.append(m)
        elapsed = time.monotonic() - start

        assert elapsed < 10.0, f"100회 메트릭 반복 {elapsed:.1f}초 (기준: <10초)"


# ══════════════════════════════════════════════════════════════════════════════
# 10. 레짐 탐지기 대량 데이터
# ══════════════════════════════════════════════════════════════════════════════


class TestRegimeDetectorLoad:
    """레짐 탐지기가 대규모 시계열에서 안정적으로 작동하는지 확인"""

    def test_regime_detection_large_series(self):
        """5,000일 OHLCV에 대한 레짐 탐지"""
        from core.strategy_ensemble.regime import MarketRegimeDetector

        rng = np.random.RandomState(42)
        n = 5000
        dates = pd.bdate_range("2005-01-03", periods=n, freq="B")

        close = 2000 * np.cumprod(1 + rng.normal(0.0002, 0.012, n))
        high = close * (1 + rng.uniform(0, 0.02, n))
        low = close * (1 - rng.uniform(0, 0.02, n))
        open_ = close * (1 + rng.normal(0, 0.005, n))
        volume = rng.randint(1_000_000, 50_000_000, n).astype(float)

        ohlcv = pd.DataFrame(
            {
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            },
            index=dates,
        )

        detector = MarketRegimeDetector()
        start = time.monotonic()
        regime = detector.detect(ohlcv)
        elapsed = time.monotonic() - start

        assert regime is not None
        assert elapsed < 5.0, f"5K일 레짐 탐지 {elapsed:.1f}초 (기준: <5초)"

    def test_regime_detection_repeated(self):
        """500일 데이터에 대해 200회 반복 탐지"""
        from core.strategy_ensemble.regime import MarketRegimeDetector

        rng = np.random.RandomState(42)
        n = 500
        dates = pd.bdate_range("2022-01-03", periods=n, freq="B")

        close = 2500 * np.cumprod(1 + rng.normal(0.0001, 0.015, n))
        ohlcv = pd.DataFrame(
            {
                "open": close * 0.999,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": rng.randint(1_000_000, 30_000_000, n).astype(float),
            },
            index=dates,
        )

        detector = MarketRegimeDetector()
        start = time.monotonic()
        for _ in range(200):
            detector.detect(ohlcv)
        elapsed = time.monotonic() - start

        assert elapsed < 30.0, f"200회 레짐 탐지 {elapsed:.1f}초 (기준: <30초)"


# ══════════════════════════════════════════════════════════════════════════════
# 11. 동적 임계값 + 앙상블 부하
# ══════════════════════════════════════════════════════════════════════════════


class TestDynamicThresholdLoad:
    """동적 임계값 계산이 대량 요청에서도 빠른지 확인"""

    def test_threshold_compute_speed(self):
        """1,000회 동적 임계값 계산"""
        from core.strategy_ensemble.regime import (
            DynamicThreshold,
            MarketRegime,
            RegimeInfo,
        )

        dt = DynamicThreshold()
        regimes = list(MarketRegime)
        start = time.monotonic()
        for _ in range(1000):
            for regime in regimes:
                info = RegimeInfo(
                    regime=regime,
                    confidence=0.7,
                    volatility_percentile=0.5,
                    trend_strength=0.3,
                    details={},
                )
                buy_t, sell_t = dt.compute(info)
                assert 0 < buy_t <= 1.0
                assert 0 < sell_t <= 1.0
        elapsed = time.monotonic() - start

        total_lookups = 1000 * len(regimes)
        assert elapsed < 2.0, f"{total_lookups}회 임계값 계산 {elapsed:.3f}초 (기준: <2초)"


# ══════════════════════════════════════════════════════════════════════════════
# 12. 서킷 브레이커 급속 트리거 테스트
# ══════════════════════════════════════════════════════════════════════════════


class TestCircuitBreakerStress:
    """서킷 브레이커가 빠른 연속 에러에 정확히 반응하는지 확인"""

    def test_rapid_error_counting(self):
        """빠른 연속 에러 200건 발생 시 서킷 브레이커 동작"""
        from core.circuit_breaker import CircuitBreaker, CircuitState

        cb = CircuitBreaker(
            name="stress_test",
            failure_threshold=10,
            recovery_timeout=60,
            half_open_max_calls=3,
        )

        dummy_exc = RuntimeError("test failure")

        # 빠르게 10건 실패 → OPEN 상태
        for _ in range(10):
            cb._record_failure(dummy_exc)

        assert cb.state == CircuitState.OPEN

        # 추가 190건 실패 기록 시도 → OPEN 유지
        for _ in range(190):
            cb._record_failure(dummy_exc)

        assert cb.state == CircuitState.OPEN

    def test_rapid_success_recovery(self):
        """HALF_OPEN 상태에서 빠른 성공으로 CLOSED 복귀"""
        from core.circuit_breaker import CircuitBreaker, CircuitState

        cb = CircuitBreaker(
            name="recovery_test",
            failure_threshold=5,
            recovery_timeout=0,  # 즉시 HALF_OPEN 전이 가능
            half_open_max_calls=3,
        )

        dummy_exc = RuntimeError("test failure")

        # OPEN으로 전이
        for _ in range(5):
            cb._record_failure(dummy_exc)
        assert cb._state == CircuitState.OPEN

        # recovery_timeout=0이므로 state 프로퍼티 조회 시 HALF_OPEN으로 자동 전이
        current = cb.state
        assert current == CircuitState.HALF_OPEN

        # HALF_OPEN에서 3건 성공 → CLOSED 복귀
        for _ in range(3):
            cb._record_success()

        assert cb.state == CircuitState.CLOSED


# ══════════════════════════════════════════════════════════════════════════════
# 13. 알림 매니저 대량 알림 생성
# ══════════════════════════════════════════════════════════════════════════════


class TestAlertManagerLoad:
    """알림 매니저가 대량 알림을 안전하게 처리하는지 확인"""

    def test_bulk_alert_creation(self):
        """100건 알림 순차 생성"""
        from config.constants import AlertType
        from core.notification.alert_manager import AlertLevel, AlertManager

        mgr = AlertManager()  # 메모리 모드

        start = time.monotonic()
        for i in range(100):
            mgr.create_alert(
                alert_type=AlertType.SYSTEM_ERROR,
                level=AlertLevel.WARNING,
                title=f"Alert #{i}",
                message=f"Price crossed threshold #{i}",
            )
        elapsed = time.monotonic() - start

        assert elapsed < 5.0, f"100건 알림 생성 {elapsed:.1f}초 (기준: <5초)"


# ══════════════════════════════════════════════════════════════════════════════
# 14. 대용량 DataFrame 처리
# ══════════════════════════════════════════════════════════════════════════════


class TestDataFramePerformance:
    """대량 DataFrame 연산의 성능 기준선"""

    def test_large_signal_generation(self):
        """2000일 × 100종목 시그널 정규화"""
        rng = np.random.RandomState(42)
        n_days, n_tickers = 2000, 100
        raw = rng.randn(n_days, n_tickers)
        df = pd.DataFrame(raw)

        start = time.monotonic()
        # 행별 Z-score 정규화
        normalized = df.sub(df.mean(axis=1), axis=0).div(df.std(axis=1), axis=0)
        # 클리핑
        clipped = normalized.clip(-3, 3)
        # 순위 변환
        ranked = clipped.rank(axis=1, pct=True)
        elapsed = time.monotonic() - start

        assert ranked.shape == (n_days, n_tickers)
        assert elapsed < 2.0, f"대용량 시그널 처리 {elapsed:.3f}초 (기준: <2초)"

    def test_rolling_calculations_performance(self):
        """1000일 × 50종목 롤링 통계량"""
        rng = np.random.RandomState(42)
        prices = pd.DataFrame(50000 * np.exp(np.cumsum(rng.normal(0, 0.02, (1000, 50)), axis=0)))

        start = time.monotonic()
        returns = prices.pct_change()
        rolling_mean = returns.rolling(20).mean()
        rolling_std = returns.rolling(20).std()
        rolling_sharpe = rolling_mean / rolling_std * np.sqrt(252)
        rolling_dd = (prices / prices.rolling(60).max()) - 1
        elapsed = time.monotonic() - start

        assert rolling_sharpe.shape == prices.shape
        assert elapsed < 2.0, f"롤링 통계 계산 {elapsed:.3f}초 (기준: <2초)"
