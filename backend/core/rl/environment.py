"""
AQTS 커스텀 Gymnasium 환경 (Custom Gymnasium Trading Environment)

트레이딩 문제를 강화학습으로 풀기 위한 Gymnasium 호환 환경입니다.

관찰 공간 (Observation Space):
    - 시장 특성: returns_5d, volatility_20d, ADX, vol_percentile, momentum
    - 전략 시그널: MR signal, TF signal, RP signal
    - 포트폴리오: portfolio_return, current_drawdown, cash_ratio
    = 총 11차원 연속 공간

행동 공간 (Action Space):
    - 연속: [-1, +1] 단일 스칼라
    - -1 = 풀 매도, 0 = 무행동, +1 = 풀 매수

보상 함수 (Reward):
    - 일일 수익률 - risk_penalty * max(drawdown - threshold, 0) - transaction_costs
"""

import gymnasium
import numpy as np
import pandas as pd
from gymnasium import spaces

from core.quant_engine.vectorized_signals import VectorizedSignalGenerator
from core.rl.config import RLConfig


class TradingEnv(gymnasium.Env):
    """
    AQTS 트레이딩 강화학습 환경

    Gymnasium 표준을 따르며, 실제 거래 데이터와 거래 비용을 반영합니다.
    """

    metadata = {"render_modes": ["human"]}

    def __init__(self, ohlcv: pd.DataFrame, config: RLConfig = None):
        """
        Args:
            ohlcv: OHLCV DataFrame (columns: open, high, low, close, volume)
            config: RLConfig 설정 객체
        """
        super().__init__()
        self.config = config or RLConfig()

        # 데이터 준비
        self._prepare_data(ohlcv)

        # 관찰 공간: 11차원 (정규화된 범위)
        self.observation_space = spaces.Box(low=-10.0, high=10.0, shape=(11,), dtype=np.float32)

        # 행동 공간: [-1, 1] 단일 연속값
        self.action_space = spaces.Box(low=-1.0, high=1.0, shape=(1,), dtype=np.float32)

        # 상태 초기화
        self.current_step = 0
        self.start_step = self.config.lookback_window
        self.episode_start_step = self.start_step

        # 포트폴리오 상태
        self.portfolio_value = self.config.initial_capital
        self.peak_value = self.config.initial_capital
        self.position = 0.0  # -1.0 (매도) ~ +1.0 (매수)
        self.cash = self.config.initial_capital
        self.trade_count = 0

    def _prepare_data(self, ohlcv: pd.DataFrame):
        """
        OHLCV 데이터로부터 신호와 특성을 미리 계산합니다.

        Args:
            ohlcv: OHLCV DataFrame
        """
        self.ohlcv = ohlcv.copy()
        self.dates = self.ohlcv.index
        self.close = self.ohlcv["close"].values.astype(np.float64)

        # 시그널 생성
        signal_gen = VectorizedSignalGenerator(min_window=self.config.lookback_window)
        signals = signal_gen.generate(self.ohlcv)
        self.mr_signal = signals["MEAN_REVERSION"].values.astype(np.float32)
        self.tf_signal = signals["TREND_FOLLOWING"].values.astype(np.float32)
        self.rp_signal = signals["RISK_PARITY"].values.astype(np.float32)

        # 시장 특성 계산
        self._compute_market_features()

    def _compute_market_features(self):
        """시장 특성 계산: returns, volatility, ADX, momentum"""
        returns = np.diff(self.close) / self.close[:-1]
        returns = np.insert(returns, 0, 0.0)

        # 수익률 (5일)
        self.returns_5d = pd.Series(returns).rolling(window=self.config.returns_window).mean().values
        self.returns_5d = np.nan_to_num(self.returns_5d, 0.0).astype(np.float32)

        # 변동성 (20일)
        self.volatility_20d = pd.Series(returns).rolling(window=self.config.volatility_window).std().values
        self.volatility_20d = np.nan_to_num(self.volatility_20d, 0.0).astype(np.float32)

        # ADX 근사 (14일 ATR 기반)
        high = self.ohlcv["high"].values
        low = self.ohlcv["low"].values
        tr = np.maximum(
            high - low,
            np.maximum(
                np.abs(high - np.roll(self.close, 1)),
                np.abs(low - np.roll(self.close, 1)),
            ),
        )
        atr = pd.Series(tr).rolling(window=14).mean().values
        atr = np.nan_to_num(atr, 1.0)
        self.adx = atr / self.close
        self.adx = np.clip(self.adx, 0.0, 1.0).astype(np.float32)

        # 변동성 백분위 (20일)
        vol_percentile = []
        for i in range(len(self.volatility_20d)):
            if i < self.config.volatility_window:
                vol_percentile.append(0.5)
            else:
                window_vol = self.volatility_20d[i - self.config.volatility_window : i]
                pct = np.mean(self.volatility_20d[i] <= window_vol)
                vol_percentile.append(pct)
        self.vol_percentile = np.array(vol_percentile, dtype=np.float32)

        # 모멘텀 (5일 / 20일)
        sma5 = pd.Series(self.close).rolling(window=5).mean().values
        sma20 = pd.Series(self.close).rolling(window=20).mean().values
        self.momentum = np.divide(sma5, sma20, where=sma20 != 0, out=np.ones_like(sma5))
        self.momentum = np.clip((self.momentum - 1.0) * 10, -1.0, 1.0).astype(np.float32)

    def _get_observation(self) -> np.ndarray:
        """현재 11차원 관찰값 반환"""
        idx = self.current_step

        portfolio_return = (self.portfolio_value - self.config.initial_capital) / (self.config.initial_capital + 1e-8)
        current_drawdown = (self.peak_value - self.portfolio_value) / (self.peak_value + 1e-8)
        cash_ratio = self.cash / (self.portfolio_value + 1e-8)

        obs = np.array(
            [
                self.returns_5d[idx],
                self.volatility_20d[idx],
                self.adx[idx],
                self.vol_percentile[idx],
                self.momentum[idx],
                self.mr_signal[idx],
                self.tf_signal[idx],
                self.rp_signal[idx],
                float(portfolio_return),
                float(current_drawdown),
                float(cash_ratio),
            ],
            dtype=np.float32,
        )

        return obs

    def step(self, action: np.ndarray):
        """
        한 거래일 진행

        Args:
            action: [-1, 1] 범위의 스칼라 (포트폴리오 시그널)

        Returns:
            observation, reward, terminated, truncated, info
        """
        target_position = float(action[0])

        # 현재 및 다음 날 가격
        current_price = self.close[self.current_step]
        next_price = self.close[self.current_step + 1]
        daily_return = (next_price - current_price) / current_price

        # 거래 비용 계산
        position_change = abs(target_position - self.position)
        transaction_cost = (
            position_change
            * self.config.initial_capital
            * (self.config.commission_rate + self.config.tax_rate + self.config.slippage_rate)
        )

        # 현금 업데이트
        self.cash -= transaction_cost

        # 포지션 변경 시 현금 이동
        if self.position != target_position:
            position_value_change = (target_position - self.position) * (self.portfolio_value - self.cash)
            self.cash -= position_value_change
            self.position = target_position
            self.trade_count += 1

        # 포트폴리오 가치 업데이트
        stock_value = (self.portfolio_value - self.cash) * (1 + daily_return)
        self.portfolio_value = self.cash + stock_value

        # 최고값 업데이트
        if self.portfolio_value > self.peak_value:
            self.peak_value = self.portfolio_value

        # 보상 계산
        pnl = self.portfolio_value * daily_return
        drawdown = max(0.0, self.peak_value - self.portfolio_value) / (self.peak_value + 1e-8)
        dd_penalty = self.config.risk_penalty * max(0.0, drawdown - self.config.max_drawdown_limit)
        cost_penalty = self.config.cost_penalty * (transaction_cost / 1e6)

        reward = pnl / 1e6 - dd_penalty - cost_penalty

        # 에피소드 종료 조건
        terminated = bool(self.current_step >= len(self.close) - 2)
        truncated = bool(drawdown > self.config.max_drawdown_limit * 2)

        self.current_step += 1

        # 관찰 및 정보
        obs = self._get_observation()
        info = {
            "portfolio_value": float(self.portfolio_value),
            "drawdown": float(drawdown),
            "trade_count": int(self.trade_count),
        }

        return obs, float(reward), terminated, truncated, info

    def reset(self, seed=None, options=None):
        """
        에피소드 초기화

        Args:
            seed: 무작위 시드
            options: 추가 옵션

        Returns:
            observation, info
        """
        super().reset(seed=seed)

        # 시작 위치 랜덤화 (최소 60일 이후부터 시작 가능)
        max_start = len(self.close) - 252  # 1년 데이터 필요
        if max_start > self.config.lookback_window:
            self.episode_start_step = self.np_random.integers(self.config.lookback_window, max_start)
        else:
            self.episode_start_step = self.config.lookback_window

        self.current_step = self.episode_start_step

        # 포트폴리오 상태 초기화
        self.portfolio_value = self.config.initial_capital
        self.peak_value = self.config.initial_capital
        self.position = 0.0
        self.cash = self.config.initial_capital
        self.trade_count = 0

        obs = self._get_observation()
        info = {"portfolio_value": float(self.portfolio_value)}

        return obs, info

    def render(self):
        """렌더링 (선택사항)"""
        pass
