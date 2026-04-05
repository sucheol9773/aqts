"""
RL 환경 설정 (Reinforcement Learning Configuration)

AQTS RL 에이전트 훈련을 위한 모든 설정값을 포함합니다.
환경 특성, 관찰/행동 공간, 보상 함수, 훈련 파라미터를 정의합니다.
"""

from dataclasses import dataclass


@dataclass
class RLConfig:
    """
    RL 에이전트 훈련 설정

    Environment:
        - initial_capital: 초기 자본금
        - commission_rate: 수수료 (예: 0.00015 = 0.015%)
        - tax_rate: 매도세 (예: 0.0023 = 0.23%)
        - slippage_rate: 슬리피지 (예: 0.001 = 0.1%)
        - max_drawdown_limit: 최대 낙폭 제한 (예: 0.20 = -20%)

    Observation:
        - lookback_window: 신호 생성에 필요한 최소 데이터 포인트
        - returns_window: 수익률 계산 윈도우
        - volatility_window: 변동성 계산 윈도우

    Reward:
        - risk_penalty: 낙폭 패널티 가중치
        - cost_penalty: 거래 비용 패널티 가중치

    Training:
        - total_timesteps: 전체 학습 스텝
        - learning_rate: 학습률
        - batch_size: 배치 크기
        - n_epochs: PPO 에포크 수
        - gamma: 할인율
        - gae_lambda: GAE 람다값
        - clip_range: PPO 클립 범위

    Evaluation:
        - eval_episodes: 평가 에피소드 수
        - eval_freq: 평가 빈도 (스텝 단위)
    """

    # ── Environment ──
    initial_capital: float = 50_000_000.0
    commission_rate: float = 0.00015  # 0.015%
    tax_rate: float = 0.0023  # 0.23%
    slippage_rate: float = 0.001  # 0.1%
    max_drawdown_limit: float = 0.20

    # ── Observation ──
    lookback_window: int = 60
    returns_window: int = 5
    volatility_window: int = 20

    # ── Reward ──
    risk_penalty: float = 2.0
    cost_penalty: float = 1.0

    # ── Training ──
    total_timesteps: int = 500_000
    learning_rate: float = 3e-4
    batch_size: int = 256
    n_epochs: int = 10
    gamma: float = 0.99
    gae_lambda: float = 0.95
    clip_range: float = 0.2

    # ── Evaluation ──
    eval_episodes: int = 10
    eval_freq: int = 10_000
