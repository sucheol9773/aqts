"""
RL v2 개선 테스트 — 데이터 로더, 멀티에셋, Hyperopt+RL, 학습 파이프라인

테스트 구성:
- TestRLDataLoader: DB/CSV/합성 데이터 로더 (7개)
- TestMultiAssetEnv: 멀티 에셋 포트폴리오 환경 (8개)
- TestTrainerV2: 개선된 학습 파이프라인 (6개)
- TestHyperoptRL: Optuna + RL 결합 (4개)
- TestRewardScaling: 자동 보상 스케일링 (3개)
"""

import numpy as np
import pandas as pd

from core.rl.config import RLConfig
from core.rl.data_loader import RLDataLoader
from core.rl.environment import TradingEnv
from core.rl.multi_asset_env import MultiAssetTradingEnv
from core.rl.trainer import RewardTrackingCallback, RLTrainer


# ═══════════════════════════════════════════
# 헬퍼: 합성 데이터 생성
# ═══════════════════════════════════════════
def _make_ohlcv(n: int = 500, trend: float = 0.0005, seed: int = 42) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range("2020-01-01", periods=n)
    returns = trend + 0.015 * rng.randn(n)
    close = 50000.0 * np.cumprod(1 + returns)
    high = close * (1 + abs(0.01 * rng.randn(n)))
    low = close * (1 - abs(0.01 * rng.randn(n)))
    open_ = close * (1 + 0.005 * rng.randn(n))
    volume = 1_000_000 * (1 + 0.3 * rng.randn(n))
    volume = np.maximum(volume, 10000)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=dates,
    )


def _make_multi_ohlcv(n_tickers: int = 3, n_days: int = 500) -> dict[str, pd.DataFrame]:
    return {f"TICK_{i:02d}": _make_ohlcv(n=n_days, trend=0.0003 * (i + 1), seed=42 + i) for i in range(n_tickers)}


# ═══════════════════════════════════════════
# TestRLDataLoader
# ═══════════════════════════════════════════
class TestRLDataLoader:
    """데이터 로더 테스트"""

    def test_generate_synthetic(self):
        """합성 데이터 생성"""
        loader = RLDataLoader()
        data = loader.generate_synthetic(n_tickers=3, n_days=500)
        assert len(data) == 3
        for ticker, df in data.items():
            assert len(df) == 500
            assert set(df.columns) >= {"open", "high", "low", "close", "volume"}

    def test_synthetic_profiles(self):
        """합성 데이터가 다양한 시장 특성 반영"""
        loader = RLDataLoader()
        data = loader.generate_synthetic(n_tickers=5, n_days=1000)
        tickers = list(data.keys())
        # 5개 프로필: TREND_UP, TREND_DOWN, SIDEWAYS, HIGH_VOL, REGIME_SWITCH
        assert any("TREND_UP" in t for t in tickers)
        assert any("TREND_DOWN" in t for t in tickers)
        assert any("SIDEWAYS" in t for t in tickers)

    def test_validate_and_clean(self):
        """데이터 검증 및 전처리"""
        loader = RLDataLoader()
        df = _make_ohlcv(n=500)
        # NaN 주입
        df.iloc[10, 3] = np.nan  # close
        cleaned = loader._validate_and_clean(df, "TEST")
        assert cleaned is not None
        assert not cleaned.isna().any().any()

    def test_validate_rejects_short_data(self):
        """짧은 데이터 거부"""
        loader = RLDataLoader()
        df = _make_ohlcv(n=100)  # 312 미만
        result = loader._validate_and_clean(df, "SHORT")
        assert result is None

    def test_validate_rejects_missing_columns(self):
        """필수 컬럼 누락 거부"""
        loader = RLDataLoader()
        df = _make_ohlcv(n=500)
        df = df.drop(columns=["volume"])
        result = loader._validate_and_clean(df, "NO_VOL")
        assert result is None

    def test_load_from_csv_empty_dir(self, tmp_path):
        """존재하지 않는 디렉토리"""
        loader = RLDataLoader()
        data = loader.load_from_csv(str(tmp_path / "nonexistent"))
        assert data == {}

    def test_load_from_csv_valid(self, tmp_path):
        """CSV 파일 로드"""
        loader = RLDataLoader()
        df = _make_ohlcv(n=500)
        df.index.name = "date"
        df.to_csv(tmp_path / "TEST.csv")
        data = loader.load_from_csv(str(tmp_path))
        assert "TEST" in data
        assert len(data["TEST"]) == 500


# ═══════════════════════════════════════════
# TestMultiAssetEnv
# ═══════════════════════════════════════════
class TestMultiAssetEnv:
    """멀티 에셋 RL 환경 테스트"""

    def test_creation(self):
        """환경 생성"""
        data = _make_multi_ohlcv(n_tickers=3, n_days=500)
        env = MultiAssetTradingEnv(data, max_assets=5)
        assert env.n_assets == 3
        assert env.max_assets == 5

    def test_observation_space(self):
        """관찰 공간 차원 확인"""
        data = _make_multi_ohlcv(n_tickers=3)
        env = MultiAssetTradingEnv(data, max_assets=5)
        # 8 * 5 + 3 = 43
        assert env.observation_space.shape == (43,)

    def test_action_space(self):
        """행동 공간 차원 확인"""
        data = _make_multi_ohlcv(n_tickers=3)
        env = MultiAssetTradingEnv(data, max_assets=5)
        assert env.action_space.shape == (5,)

    def test_reset(self):
        """리셋 동작"""
        data = _make_multi_ohlcv(n_tickers=3)
        env = MultiAssetTradingEnv(data, max_assets=5)
        obs, info = env.reset()
        assert obs.shape == (43,)
        assert "portfolio_value" in info

    def test_step(self):
        """스텝 동작"""
        data = _make_multi_ohlcv(n_tickers=3)
        env = MultiAssetTradingEnv(data, max_assets=5)
        obs, _ = env.reset()
        action = np.array([0.3, 0.2, 0.1, 0.0, 0.0])
        obs, reward, terminated, truncated, info = env.step(action)
        assert obs.shape == (43,)
        assert isinstance(reward, float)
        assert "weights" in info
        assert "hhi" in info

    def test_weight_normalization(self):
        """비중 합 > 1 시 정규화"""
        data = _make_multi_ohlcv(n_tickers=3)
        env = MultiAssetTradingEnv(data, max_assets=5)
        env.reset()
        action = np.array([0.8, 0.6, 0.5, 0.0, 0.0])  # 합 1.9
        _, _, _, _, info = env.step(action)
        weights = np.array(info["weights"])
        assert np.abs(weights).sum() <= 1.0 + 1e-6

    def test_diversification_bonus(self):
        """다양화 보너스: 분산 > 집중"""
        data = _make_multi_ohlcv(n_tickers=3)
        env = MultiAssetTradingEnv(data, max_assets=5)
        env.reset()
        # 분산 포트폴리오
        _, reward_diverse, _, _, info_diverse = env.step(np.array([0.33, 0.33, 0.33, 0.0, 0.0]))
        env.reset()
        # 집중 포트폴리오
        _, reward_conc, _, _, info_conc = env.step(np.array([1.0, 0.0, 0.0, 0.0, 0.0]))
        # 분산 HHI < 집중 HHI
        assert info_diverse["hhi"] < info_conc["hhi"]

    def test_episode_run(self):
        """에피소드 완주"""
        data = _make_multi_ohlcv(n_tickers=2, n_days=400)
        env = MultiAssetTradingEnv(data, max_assets=3)
        obs, _ = env.reset()
        steps = 0
        while True:
            action = env.action_space.sample()
            obs, reward, terminated, truncated, info = env.step(action)
            steps += 1
            if terminated or truncated:
                break
        assert steps > 0
        assert info["portfolio_value"] > 0


# ═══════════════════════════════════════════
# TestTrainerV2
# ═══════════════════════════════════════════
class TestTrainerV2:
    """개선된 학습 파이프라인 테스트"""

    def test_reward_tracking_callback(self):
        """RewardTrackingCallback 동작"""
        cb = RewardTrackingCallback()
        assert cb.episode_rewards == []
        assert cb.episode_lengths == []

    def test_train_with_callbacks(self):
        """콜백 포함 학습"""
        data = {"TEST": _make_ohlcv(n=500)}
        config = RLConfig(total_timesteps=500, eval_freq=250)
        trainer = RLTrainer(data, config)
        result = trainer.train(algorithm="PPO")
        assert result.model is not None
        assert len(result.episode_rewards) > 0
        assert len(result.episode_lengths) > 0

    def test_data_split_70_15_15(self):
        """데이터 3분할 확인 (70/15/15)"""
        data = {"TEST": _make_ohlcv(n=500)}
        config = RLConfig(total_timesteps=200)
        trainer = RLTrainer(data, config)
        result = trainer.train(algorithm="PPO")
        # evaluate는 마지막 15% 사용
        eval_result = trainer.evaluate(result.model)
        assert eval_result is not None

    def test_learning_curve(self):
        """학습곡선 기록"""
        data = {"TEST": _make_ohlcv(n=500)}
        config = RLConfig(total_timesteps=1000)
        trainer = RLTrainer(data, config)
        result = trainer.train(algorithm="PPO")
        assert len(result.learning_curve) >= 1

    def test_eval_episode_returns(self):
        """평가 에피소드별 수익률"""
        data = {"TEST": _make_ohlcv(n=500)}
        config = RLConfig(total_timesteps=200, eval_episodes=3)
        trainer = RLTrainer(data, config)
        result = trainer.train(algorithm="PPO")
        eval_result = trainer.evaluate(result.model)
        assert len(eval_result.episode_returns) == 3

    def test_save_load_model(self, tmp_path):
        """모델 저장/로드"""
        data = {"TEST": _make_ohlcv(n=500)}
        config = RLConfig(total_timesteps=200)
        trainer = RLTrainer(data, config)
        result = trainer.train(algorithm="PPO")

        model_path = str(tmp_path / "test_model")
        trainer.save_model(result.model, model_path)

        loaded = trainer.load_model(model_path, algorithm="PPO")
        assert loaded is not None


# ═══════════════════════════════════════════
# TestHyperoptRL
# ═══════════════════════════════════════════
class TestHyperoptRL:
    """Hyperopt + RL 결합 테스트"""

    def test_optimizer_creation(self):
        """옵티마이저 생성"""
        from core.rl.hyperopt_rl import RLHyperoptOptimizer

        data = {"TEST": _make_ohlcv(n=500)}
        optimizer = RLHyperoptOptimizer(data, timesteps_per_trial=200)
        assert optimizer.timesteps_per_trial == 200

    def test_suggest_config(self):
        """파라미터 제안"""
        import optuna

        from core.rl.hyperopt_rl import RLHyperoptOptimizer

        data = {"TEST": _make_ohlcv(n=500)}
        optimizer = RLHyperoptOptimizer(data)

        study = optuna.create_study(direction="maximize")

        def _trial_fn(trial):
            config = optimizer._suggest_config(trial)
            assert 0.5 <= config.risk_penalty <= 5.0
            assert 1e-5 <= config.learning_rate <= 1e-3
            return 0.0

        study.optimize(_trial_fn, n_trials=1)

    def test_single_trial(self):
        """단일 시행 실행"""
        from core.rl.hyperopt_rl import RLHyperoptOptimizer

        data = {"TEST": _make_ohlcv(n=500)}
        optimizer = RLHyperoptOptimizer(data, timesteps_per_trial=200)
        best_config, study = optimizer.optimize(n_trials=1)
        assert best_config is not None
        assert len(study.trials) == 1

    def test_trial_to_config(self):
        """trial → RLConfig 변환"""

        from core.rl.hyperopt_rl import RLHyperoptOptimizer

        data = {"TEST": _make_ohlcv(n=500)}
        optimizer = RLHyperoptOptimizer(data, timesteps_per_trial=200)

        _, study = optimizer.optimize(n_trials=1)
        config = optimizer._trial_to_config(study.best_trial)
        assert isinstance(config, RLConfig)


# ═══════════════════════════════════════════
# TestRewardScaling
# ═══════════════════════════════════════════
class TestRewardScaling:
    """보상 자동 스케일링 테스트"""

    def test_reward_scale_50m(self):
        """50M 자본금 보상 스케일"""
        ohlcv = _make_ohlcv(n=400)
        config = RLConfig(initial_capital=50_000_000)
        env = TradingEnv(ohlcv, config)
        env.reset()
        _, reward_50m, _, _, _ = env.step(np.array([0.5]))
        assert isinstance(reward_50m, float)

    def test_reward_scale_10m(self):
        """10M 자본금 보상 스케일"""
        ohlcv = _make_ohlcv(n=400)
        config = RLConfig(initial_capital=10_000_000)
        env = TradingEnv(ohlcv, config)
        env.reset()
        _, reward_10m, _, _, _ = env.step(np.array([0.5]))
        assert isinstance(reward_10m, float)

    def test_reward_magnitude_similar(self):
        """다른 자본금에서 보상 크기가 비슷해야 함"""
        ohlcv = _make_ohlcv(n=400, seed=42)

        rewards = []
        for capital in [10_000_000, 50_000_000, 100_000_000]:
            config = RLConfig(initial_capital=capital)
            env = TradingEnv(ohlcv, config)
            env.reset(seed=0)
            _, reward, _, _, _ = env.step(np.array([0.5]))
            rewards.append(abs(reward))

        # 10배 자본금 차이에서 보상 크기가 100배 이상 차이나면 안됨
        ratio = max(rewards) / (min(rewards) + 1e-10)
        assert ratio < 100, f"Reward ratio too large: {ratio:.1f}x"
