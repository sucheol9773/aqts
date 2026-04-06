"""
멀티 에셋 RL 환경 (Multi-Asset Portfolio Trading Environment)

단일 종목 TradingEnv를 포트폴리오 레벨로 확장합니다.

관찰 공간 (Observation Space):
    - 종목당 8차원 시장 특성 × N개 종목
    - 포트폴리오 3차원 (return, drawdown, cash_ratio)
    = 총 (8 × N + 3)차원

행동 공간 (Action Space):
    - N차원 연속 [-1, +1]: 각 종목의 목표 비중
    - softmax 정규화로 합계 <= 1 보장 (나머지 = 현금)

보상 함수 (Reward):
    - 포트폴리오 일일 수익률 (비중 가중)
    - risk_penalty × max(drawdown - threshold, 0)
    - 거래 비용 (비중 변화량 비례)
    - 다양화 보너스: HHI(비중)가 낮을수록 보너스

사용법:
    env = MultiAssetTradingEnv(ohlcv_dict, config)
    obs, info = env.reset()
    action = np.array([0.3, 0.2, -0.1, 0.0, 0.4])  # 5종목 비중
    obs, reward, terminated, truncated, info = env.step(action)
"""

import gymnasium
import numpy as np
import pandas as pd
from gymnasium import spaces

from core.quant_engine.vectorized_signals import VectorizedSignalGenerator
from core.rl.config import RLConfig


class MultiAssetTradingEnv(gymnasium.Env):
    """
    멀티 에셋 포트폴리오 RL 환경

    N개 종목에 대한 동시 비중 결정을 학습합니다.
    """

    metadata = {"render_modes": ["human"]}

    # 종목당 관찰 특성 수
    FEATURES_PER_ASSET = 8
    # 포트폴리오 전체 특성 수
    PORTFOLIO_FEATURES = 3

    def __init__(
        self,
        ohlcv_dict: dict[str, pd.DataFrame],
        config: RLConfig = None,
        max_assets: int = 10,
    ):
        """
        Args:
            ohlcv_dict: {ticker: DataFrame} 딕셔너리
            config: RLConfig 설정
            max_assets: 최대 종목 수 (관찰 공간 고정)
        """
        super().__init__()
        self.config = config or RLConfig()
        self.max_assets = max_assets

        # 데이터 준비 (날짜 정렬 후 공통 기간 추출)
        self._prepare_multi_asset_data(ohlcv_dict)

        # 관찰 공간: (8 × max_assets + 3)
        obs_dim = self.FEATURES_PER_ASSET * self.max_assets + self.PORTFOLIO_FEATURES
        self.observation_space = spaces.Box(low=-10.0, high=10.0, shape=(obs_dim,), dtype=np.float32)

        # 행동 공간: 각 종목의 비중 [-1, 1]
        self.action_space = spaces.Box(low=-1.0, high=1.0, shape=(self.max_assets,), dtype=np.float32)

        # 상태 초기화
        self._reset_portfolio_state()

    def _prepare_multi_asset_data(self, ohlcv_dict: dict[str, pd.DataFrame]):
        """멀티 에셋 데이터 전처리"""
        # 최대 max_assets개 종목 선택 (데이터 길이순)
        sorted_tickers = sorted(ohlcv_dict.keys(), key=lambda k: len(ohlcv_dict[k]), reverse=True)
        self.tickers = sorted_tickers[: self.max_assets]
        self.n_assets = len(self.tickers)

        # 공통 날짜 추출
        common_dates = None
        for ticker in self.tickers:
            dates = set(ohlcv_dict[ticker].index)
            if common_dates is None:
                common_dates = dates
            else:
                common_dates = common_dates & dates

        common_dates = sorted(common_dates)
        self.dates = common_dates
        self.n_days = len(common_dates)

        # 종목별 데이터 배열화
        self.close_matrix = np.zeros((self.n_days, self.n_assets), dtype=np.float64)
        self.features_matrix = np.zeros((self.n_days, self.n_assets, self.FEATURES_PER_ASSET), dtype=np.float32)

        signal_gen = VectorizedSignalGenerator(min_window=self.config.lookback_window)

        for i, ticker in enumerate(self.tickers):
            df = ohlcv_dict[ticker].loc[common_dates]
            self.close_matrix[:, i] = df["close"].values

            # 시그널 생성
            signals = signal_gen.generate(df)

            # 특성 계산
            close = df["close"].values.astype(np.float64)
            returns = np.diff(close) / close[:-1]
            returns = np.insert(returns, 0, 0.0)

            returns_5d = pd.Series(returns).rolling(5).mean().fillna(0.0).values
            vol_20d = pd.Series(returns).rolling(20).std().fillna(0.0).values
            sma5 = pd.Series(close).rolling(5).mean().values
            sma20 = pd.Series(close).rolling(20).mean().values
            momentum = np.divide(sma5, sma20, where=sma20 != 0, out=np.ones_like(sma5))
            momentum = np.clip((momentum - 1.0) * 10, -1.0, 1.0)

            # ATR 근사 (ADX proxy)
            high = df["high"].values if "high" in df.columns else close
            low = df["low"].values if "low" in df.columns else close
            tr = np.maximum(
                high - low,
                np.maximum(
                    np.abs(high - np.roll(close, 1)),
                    np.abs(low - np.roll(close, 1)),
                ),
            )
            atr = pd.Series(tr).rolling(14).mean().fillna(0.0).values
            adx_proxy = np.clip(atr / (close + 1e-8), 0.0, 1.0)

            # 변동성 백분위
            vol_pct = pd.Series(vol_20d).rank(pct=True).fillna(0.5).values

            self.features_matrix[:, i, 0] = returns_5d.astype(np.float32)
            self.features_matrix[:, i, 1] = vol_20d.astype(np.float32)
            self.features_matrix[:, i, 2] = adx_proxy.astype(np.float32)
            self.features_matrix[:, i, 3] = vol_pct.astype(np.float32)
            self.features_matrix[:, i, 4] = momentum.astype(np.float32)
            self.features_matrix[:, i, 5] = signals["MEAN_REVERSION"].values.astype(np.float32)
            self.features_matrix[:, i, 6] = signals["TREND_FOLLOWING"].values.astype(np.float32)
            self.features_matrix[:, i, 7] = signals["RISK_PARITY"].values.astype(np.float32)

    def _reset_portfolio_state(self):
        """포트폴리오 상태 초기화"""
        self.current_step = self.config.lookback_window
        self.episode_start_step = self.current_step
        self.portfolio_value = self.config.initial_capital
        self.peak_value = self.config.initial_capital
        self.cash = self.config.initial_capital
        self.positions = np.zeros(self.n_assets, dtype=np.float64)  # 비중
        self.trade_count = 0

    def _get_observation(self) -> np.ndarray:
        """관찰값 구성: (8 × max_assets) + 3"""
        idx = self.current_step

        # 종목별 특성 (max_assets까지 패딩)
        asset_obs = np.zeros(self.FEATURES_PER_ASSET * self.max_assets, dtype=np.float32)
        for i in range(self.n_assets):
            start = i * self.FEATURES_PER_ASSET
            end = start + self.FEATURES_PER_ASSET
            asset_obs[start:end] = self.features_matrix[idx, i, :]

        # 포트폴리오 특성
        portfolio_return = (self.portfolio_value - self.config.initial_capital) / (self.config.initial_capital + 1e-8)
        current_drawdown = (self.peak_value - self.portfolio_value) / (self.peak_value + 1e-8)
        cash_ratio = self.cash / (self.portfolio_value + 1e-8)

        portfolio_obs = np.array([portfolio_return, current_drawdown, cash_ratio], dtype=np.float32)

        return np.concatenate([asset_obs, portfolio_obs])

    def step(self, action: np.ndarray):
        """
        한 거래일 진행

        Args:
            action: (max_assets,) 비중 배열 [-1, 1]

        Returns:
            observation, reward, terminated, truncated, info
        """
        # 비중 정규화: 절대값 합 <= 1 (나머지 현금)
        raw_weights = action[: self.n_assets]
        abs_sum = np.abs(raw_weights).sum()
        if abs_sum > 1.0:
            target_weights = raw_weights / abs_sum
        else:
            target_weights = raw_weights.copy()

        # 현재/다음 가격
        current_prices = self.close_matrix[self.current_step]
        next_prices = self.close_matrix[self.current_step + 1]
        daily_returns = (next_prices - current_prices) / (current_prices + 1e-8)

        # 거래 비용 계산
        weight_changes = np.abs(target_weights - self.positions)
        total_cost_rate = self.config.commission_rate + self.config.tax_rate + self.config.slippage_rate
        transaction_cost = np.sum(weight_changes) * self.portfolio_value * total_cost_rate

        # 비중 변경 시 거래 횟수
        if np.sum(weight_changes) > 0.01:
            self.trade_count += int(np.sum(weight_changes > 0.005))

        # 포트폴리오 수익 = 비중 가중 수익
        portfolio_return = np.sum(target_weights * daily_returns)
        self.portfolio_value *= 1 + portfolio_return
        self.portfolio_value -= transaction_cost
        self.cash = self.portfolio_value * (1 - np.abs(target_weights).sum())
        self.positions = target_weights

        # 최고값 업데이트
        if self.portfolio_value > self.peak_value:
            self.peak_value = self.portfolio_value

        # 보상 계산
        reward_scale = self.config.initial_capital / 1e6
        pnl = self.portfolio_value * portfolio_return
        drawdown = max(0.0, self.peak_value - self.portfolio_value) / (self.peak_value + 1e-8)
        dd_penalty = self.config.risk_penalty * max(0.0, drawdown - self.config.max_drawdown_limit)
        cost_penalty = self.config.cost_penalty * (transaction_cost / (reward_scale * 1e6 + 1e-8))

        # 다양화 보너스: HHI(비중) 낮을수록 보너스
        hhi = np.sum(target_weights**2)
        diversification_bonus = 0.01 * max(0.0, 1.0 / (self.n_assets) - hhi)

        reward = pnl / (reward_scale * 1e6 + 1e-8) - dd_penalty - cost_penalty + diversification_bonus

        # 종료 조건
        terminated = bool(self.current_step >= self.n_days - 2)
        truncated = bool(drawdown > self.config.max_drawdown_limit * 2)

        self.current_step += 1
        obs = self._get_observation()
        info = {
            "portfolio_value": float(self.portfolio_value),
            "drawdown": float(drawdown),
            "trade_count": int(self.trade_count),
            "weights": target_weights.tolist(),
            "hhi": float(hhi),
        }

        return obs, float(reward), terminated, truncated, info

    def reset(self, seed=None, options=None):
        """에피소드 초기화"""
        super().reset(seed=seed)

        max_start = self.n_days - 252
        if max_start > self.config.lookback_window:
            self.episode_start_step = self.np_random.integers(self.config.lookback_window, max_start)
        else:
            self.episode_start_step = self.config.lookback_window

        self.current_step = self.episode_start_step
        self.portfolio_value = self.config.initial_capital
        self.peak_value = self.config.initial_capital
        self.cash = self.config.initial_capital
        self.positions = np.zeros(self.n_assets, dtype=np.float64)
        self.trade_count = 0

        obs = self._get_observation()
        info = {"portfolio_value": float(self.portfolio_value)}
        return obs, info

    def render(self):
        """렌더링 (선택사항)"""
        pass
