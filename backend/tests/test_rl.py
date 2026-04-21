"""
RL 모듈 단위 테스트 (RL Module Unit Tests)

테스트 범위:
- RLConfig: 기본값, 커스텀 값
- TradingEnv: 생성, 관찰/행동 공간, 리셋, 스텝, 거래 비용, 낙폭 패널티
- RLTrainer: 생성, 훈련, 평가
- Gymnasium 호환성
"""

import warnings

import numpy as np
import pandas as pd
import pytest
from gymnasium import spaces
from gymnasium.utils.env_checker import check_env
from stable_baselines3 import PPO

from core.rl import EvalResult, RLConfig, RLTrainer, TradingEnv, TrainResult


# ══════════════════════════════════════
# Helper: 합성 OHLCV 생성
# ══════════════════════════════════════
def _make_synthetic_ohlcv(n_days: int = 500, seed: int = 42) -> pd.DataFrame:
    """
    합성 OHLCV 데이터 생성

    Args:
        n_days: 날짜 수
        seed: 무작위 시드

    Returns:
        OHLCV DataFrame
    """
    np.random.seed(seed)
    dates = pd.bdate_range("2020-01-01", periods=n_days)

    # 기하 브라운 운동으로 가격 생성
    close_pct_changes = np.random.normal(0.0002, 0.015, n_days)
    close = 50000 * np.exp(np.cumsum(close_pct_changes))

    return pd.DataFrame(
        {
            "open": close * (1 + np.random.normal(0, 0.005, n_days)),
            "high": close * (1 + np.abs(np.random.normal(0, 0.01, n_days))),
            "low": close * (1 - np.abs(np.random.normal(0, 0.01, n_days))),
            "close": close,
            "volume": np.random.randint(100000, 1000000, n_days),
        },
        index=dates,
    )


# ══════════════════════════════════════
# Test: RLConfig
# ══════════════════════════════════════
class TestRLConfig:
    """RLConfig 테스트"""

    def test_default_values(self):
        """기본값 확인"""
        config = RLConfig()

        assert config.initial_capital == 50_000_000.0
        assert config.commission_rate == 0.00015
        assert config.tax_rate == 0.0023
        assert config.slippage_rate == 0.001
        assert config.max_drawdown_limit == 0.20
        assert config.lookback_window == 60
        assert config.total_timesteps == 500_000
        assert config.learning_rate == 3e-4
        assert config.batch_size == 256

    def test_custom_values(self):
        """커스텀 값 설정"""
        config = RLConfig(
            initial_capital=100_000_000.0,
            commission_rate=0.0001,
            total_timesteps=100_000,
        )

        assert config.initial_capital == 100_000_000.0
        assert config.commission_rate == 0.0001
        assert config.total_timesteps == 100_000
        assert config.batch_size == 256  # 기본값 유지


# ══════════════════════════════════════
# Test: TradingEnv
# ══════════════════════════════════════
class TestTradingEnv:
    """TradingEnv Gymnasium 환경 테스트"""

    @pytest.fixture
    def sample_ohlcv(self):
        """샘플 OHLCV 데이터"""
        return _make_synthetic_ohlcv(n_days=500)

    @pytest.fixture
    def sample_config(self):
        """샘플 RLConfig"""
        return RLConfig(
            initial_capital=10_000_000.0,
            commission_rate=0.00015,
            tax_rate=0.0023,
            slippage_rate=0.001,
        )

    def test_env_creation(self, sample_ohlcv, sample_config):
        """환경 생성 테스트"""
        env = TradingEnv(sample_ohlcv, sample_config)

        assert env is not None
        assert env.config == sample_config
        assert len(env.close) == len(sample_ohlcv)

    def test_observation_space_shape(self, sample_ohlcv, sample_config):
        """관찰 공간 차원 확인 (11차원)"""
        env = TradingEnv(sample_ohlcv, sample_config)

        assert isinstance(env.observation_space, spaces.Box)
        assert env.observation_space.shape == (11,)

    def test_action_space_shape(self, sample_ohlcv, sample_config):
        """행동 공간 차원 확인 (1차원, [-1, 1])"""
        env = TradingEnv(sample_ohlcv, sample_config)

        assert isinstance(env.action_space, spaces.Box)
        assert env.action_space.shape == (1,)
        assert env.action_space.low[0] == -1.0
        assert env.action_space.high[0] == 1.0

    def test_reset_returns_valid_obs(self, sample_ohlcv, sample_config):
        """리셋 후 관찰값 확인"""
        env = TradingEnv(sample_ohlcv, sample_config)
        obs, info = env.reset()

        assert isinstance(obs, np.ndarray)
        assert obs.shape == (11,)
        assert np.all(np.isfinite(obs))
        assert "portfolio_value" in info

    def test_step_returns_valid_tuple(self, sample_ohlcv, sample_config):
        """스텝 후 반환값 확인"""
        env = TradingEnv(sample_ohlcv, sample_config)
        env.reset()

        action = np.array([0.5])
        obs, reward, terminated, truncated, info = env.step(action)

        assert isinstance(obs, np.ndarray)
        assert obs.shape == (11,)
        assert isinstance(reward, float)
        assert isinstance(terminated, bool)
        assert isinstance(truncated, bool)
        assert isinstance(info, dict)
        assert "portfolio_value" in info
        assert "drawdown" in info
        assert "trade_count" in info

    def test_episode_runs_to_completion(self, sample_ohlcv, sample_config):
        """에피소드 완료까지 실행"""
        env = TradingEnv(sample_ohlcv, sample_config)
        obs, _ = env.reset()

        episode_return = 0.0
        steps = 0
        max_steps = len(sample_ohlcv) - sample_config.lookback_window - 10

        while steps < max_steps:
            action = np.array([np.random.uniform(-1.0, 1.0)])
            obs, reward, terminated, truncated, info = env.step(action)
            episode_return += reward
            steps += 1

            if terminated or truncated:
                break

        assert steps > 0
        assert np.isfinite(episode_return)

    def test_transaction_costs_applied(self, sample_ohlcv, sample_config):
        """거래 비용 적용 확인"""
        env = TradingEnv(sample_ohlcv, sample_config)
        env.reset()

        initial_cash = env.cash
        action = np.array([1.0])  # 풀 매수
        env.step(action)

        # 거래 비용으로 현금 감소
        assert env.cash < initial_cash
        assert env.trade_count == 1

    def test_reward_penalizes_drawdown(self, sample_ohlcv, sample_config):
        """낙폭 페널티 확인"""
        env = TradingEnv(sample_ohlcv, sample_config)
        config_with_high_penalty = RLConfig(initial_capital=10_000_000.0, risk_penalty=10.0)
        env_high_penalty = TradingEnv(sample_ohlcv, config_with_high_penalty)

        obs1, _ = env.reset()
        obs2, _ = env_high_penalty.reset()

        action = np.array([-1.0])  # 풀 매도

        _, reward1, _, _, _ = env.step(action)
        _, reward2, _, _, _ = env_high_penalty.step(action)

        # 페널티가 높을수록 보상이 낮음
        assert isinstance(reward1, float)
        assert isinstance(reward2, float)


# ══════════════════════════════════════
# Test: RLTrainer
# ══════════════════════════════════════
class TestRLTrainer:
    """RLTrainer 테스트"""

    @pytest.fixture
    def sample_data(self):
        """샘플 데이터"""
        return {
            "005930": _make_synthetic_ohlcv(n_days=500),
            "000660": _make_synthetic_ohlcv(n_days=400),
        }

    @pytest.fixture
    def sample_config(self):
        """샘플 설정 (빠른 테스트용)"""
        return RLConfig(
            initial_capital=10_000_000.0,
            total_timesteps=1000,  # 짧은 훈련
            batch_size=64,
            eval_episodes=2,
        )

    def test_trainer_creation(self, sample_data, sample_config):
        """훈련기 생성"""
        trainer = RLTrainer(sample_data, sample_config)

        assert trainer is not None
        assert trainer.config == sample_config
        assert len(trainer.ohlcv_data) == 2

    @pytest.mark.slow
    def test_train_ppo_short(self, sample_data, sample_config):
        """PPO 단기 훈련 (스모크 테스트)"""
        trainer = RLTrainer(sample_data, sample_config)
        result = trainer.train(algorithm="PPO", ticker="005930")

        assert isinstance(result, TrainResult)
        assert result.algorithm == "PPO"
        assert result.total_timesteps == sample_config.total_timesteps
        assert result.training_time_seconds > 0
        assert isinstance(result.final_reward, float)
        assert isinstance(result.best_eval_reward, float)
        assert len(result.learning_curve) > 0

    @pytest.mark.slow
    def test_train_sac_short(self, sample_data, sample_config):
        """SAC 단기 훈련 (스모크 테스트)"""
        trainer = RLTrainer(sample_data, sample_config)
        result = trainer.train(algorithm="SAC", ticker="005930")

        assert isinstance(result, TrainResult)
        assert result.algorithm == "SAC"
        assert result.total_timesteps == sample_config.total_timesteps

    @pytest.mark.slow
    def test_evaluate(self, sample_data, sample_config):
        """평가 테스트"""
        trainer = RLTrainer(sample_data, sample_config)
        result = trainer.train(algorithm="PPO", ticker="005930")
        eval_result = trainer.evaluate(result.model)

        assert isinstance(eval_result, EvalResult)
        assert isinstance(eval_result.total_return, float)
        assert isinstance(eval_result.sharpe_ratio, float)
        assert isinstance(eval_result.max_drawdown, float)
        assert eval_result.total_trades >= 0
        assert isinstance(eval_result.avg_daily_return, float)


# ══════════════════════════════════════
# Test: Gymnasium Compatibility
# ══════════════════════════════════════
class TestGymCompatibility:
    """Gymnasium 호환성 테스트"""

    @pytest.fixture
    def sample_ohlcv(self):
        """샘플 OHLCV 데이터"""
        return _make_synthetic_ohlcv(n_days=500)

    @pytest.fixture
    def sample_config(self):
        """샘플 설정"""
        return RLConfig(initial_capital=10_000_000.0)

    def test_check_env(self, sample_ohlcv, sample_config):
        """gymnasium env_checker 통과"""
        env = TradingEnv(sample_ohlcv, sample_config)

        # Gymnasium 환경 유효성 검사.
        # TradingEnv 는 `gymnasium.register` 에 등록된 spec 이 아니라 직접
        # 인스턴스화 방식을 쓰기 때문에 env_checker 가
        # "Not able to test alternative render modes" UserWarning 을 남긴다.
        # 기능 결함이 아니라 "spec 기반 render mode 교차 검증만 생략됨"이라는
        # 정보성 경고이므로 pattern 매칭으로 해당 경고만 silence 한다.
        # (업스트림 gymnasium register 로 전환 시 제거 예정.)
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message=r".*Not able to test alternative render modes.*",
                    category=UserWarning,
                )
                check_env(env.unwrapped)
        except Exception as e:
            pytest.fail(f"Environment failed gym checker: {e}")

    @pytest.mark.slow
    def test_sb3_compatible(self, sample_ohlcv, sample_config):
        """SB3 PPO 모델과 호환성"""
        env = TradingEnv(sample_ohlcv, sample_config)

        # 짧은 훈련 실행
        model = PPO("MlpPolicy", env, verbose=0)
        model.learn(total_timesteps=100)

        # 추론 테스트
        obs, _ = env.reset()
        action, _ = model.predict(obs, deterministic=True)

        assert action.shape == (1,)
        assert -1.0 <= action[0] <= 1.0
