"""
파라미터 스윕 생성기

Grid 및 Random 방식으로 파라미터 조합을 생성합니다.
기본 파라미터 범위 정의를 포함합니다.
"""

import itertools
import random
from typing import Optional

from config.logging import logger

from .models import ParamCategory, ParamRange, SweepMethod

# ══════════════════════════════════════
# 기본 파라미터 범위 정의
# ══════════════════════════════════════

DEFAULT_PARAM_RANGES: list[ParamRange] = [
    # 팩터 가중치 (5개)
    ParamRange(
        name="factor_value_weight",
        category=ParamCategory.FACTOR_WEIGHT,
        base_value=0.25,
        min_value=0.05,
        max_value=0.50,
        step=0.05,
        description="Value 팩터 가중치",
    ),
    ParamRange(
        name="factor_momentum_weight",
        category=ParamCategory.FACTOR_WEIGHT,
        base_value=0.20,
        min_value=0.05,
        max_value=0.45,
        step=0.05,
        description="Momentum 팩터 가중치",
    ),
    ParamRange(
        name="factor_quality_weight",
        category=ParamCategory.FACTOR_WEIGHT,
        base_value=0.20,
        min_value=0.05,
        max_value=0.45,
        step=0.05,
        description="Quality 팩터 가중치",
    ),
    # 기술적 지표
    ParamRange(
        name="rsi_period",
        category=ParamCategory.TECHNICAL,
        base_value=14,
        min_value=7,
        max_value=28,
        step=7,
        description="RSI 기간",
    ),
    ParamRange(
        name="bollinger_period",
        category=ParamCategory.TECHNICAL,
        base_value=20,
        min_value=10,
        max_value=40,
        step=10,
        description="볼린저 밴드 기간",
    ),
    ParamRange(
        name="bollinger_std",
        category=ParamCategory.TECHNICAL,
        base_value=2.0,
        min_value=1.0,
        max_value=3.0,
        step=0.5,
        description="볼린저 밴드 표준편차 배수",
    ),
    # 시그널 임계값
    ParamRange(
        name="rsi_oversold",
        category=ParamCategory.SIGNAL_THRESHOLD,
        base_value=30,
        min_value=20,
        max_value=40,
        step=5,
        description="RSI 과매도 임계값",
    ),
    ParamRange(
        name="rsi_overbought",
        category=ParamCategory.SIGNAL_THRESHOLD,
        base_value=70,
        min_value=60,
        max_value=80,
        step=5,
        description="RSI 과매수 임계값",
    ),
    # 비용
    ParamRange(
        name="commission_rate",
        category=ParamCategory.COST,
        base_value=0.00015,
        min_value=0.0,
        max_value=0.001,
        step=0.0002,
        description="수수료율",
    ),
    ParamRange(
        name="slippage_rate",
        category=ParamCategory.COST,
        base_value=0.001,
        min_value=0.0,
        max_value=0.005,
        step=0.001,
        description="슬리피지율",
    ),
]


class SweepGenerator:
    """파라미터 스윕 조합 생성기"""

    def __init__(
        self,
        param_ranges: Optional[list[ParamRange]] = None,
        method: SweepMethod = SweepMethod.GRID,
        max_trials: int = 500,
        seed: int = 42,
    ):
        self._ranges = param_ranges or DEFAULT_PARAM_RANGES
        self._method = method
        self._max_trials = max_trials
        self._seed = seed

        # 범위 유효성 검증
        for pr in self._ranges:
            if not pr.validate():
                raise ValueError(f"Invalid param range: {pr.name} (min={pr.min_value}, max={pr.max_value})")

    @property
    def param_names(self) -> list[str]:
        return [pr.name for pr in self._ranges]

    @property
    def base_values(self) -> dict[str, float]:
        return {pr.name: pr.base_value for pr in self._ranges}

    def generate(self) -> list[dict[str, float]]:
        """파라미터 조합 목록 생성"""
        if self._method == SweepMethod.GRID:
            return self._generate_grid()
        else:
            return self._generate_random()

    def generate_one_at_a_time(self) -> list[dict[str, float]]:
        """
        One-at-a-time (OAT) 스윕: 한 번에 하나의 파라미터만 변경

        탄성치 계산에 최적화된 방식.
        """
        base = self.base_values
        trials = [base.copy()]  # 기본값 포함

        for pr in self._ranges:
            for val in pr.grid_values():
                if abs(val - pr.base_value) < 1e-12:
                    continue  # 기본값은 이미 포함
                trial = base.copy()
                trial[pr.name] = val
                trials.append(trial)

        logger.info(f"OAT sweep: {len(trials)} trials for {len(self._ranges)} params")
        return trials

    def _generate_grid(self) -> list[dict[str, float]]:
        """격자 탐색 조합 생성"""
        all_values = [pr.grid_values() for pr in self._ranges]
        names = self.param_names

        combinations = list(itertools.product(*all_values))

        if len(combinations) > self._max_trials:
            logger.warning(
                f"Grid sweep: {len(combinations)} combinations exceed max_trials={self._max_trials}, "
                f"sampling {self._max_trials} random combinations"
            )
            rng = random.Random(self._seed)
            combinations = rng.sample(combinations, self._max_trials)

        trials = [dict(zip(names, combo)) for combo in combinations]
        logger.info(f"Grid sweep: {len(trials)} trials generated")
        return trials

    def _generate_random(self) -> list[dict[str, float]]:
        """무작위 샘플링 조합 생성"""
        rng = random.Random(self._seed)
        trials = []

        for _ in range(self._max_trials):
            trial = {}
            for pr in self._ranges:
                val = rng.uniform(pr.min_value, pr.max_value)
                # step이 있으면 step 단위로 반올림
                if pr.step and pr.step > 0:
                    val = round(round(val / pr.step) * pr.step, 10)
                trial[pr.name] = val
            trials.append(trial)

        logger.info(f"Random sweep: {len(trials)} trials generated")
        return trials
