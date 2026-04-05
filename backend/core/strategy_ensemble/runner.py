"""
동적 앙상블 실행기 (Dynamic Ensemble Runner)

OHLCV 데이터 조회 → 벡터화 시그널 생성 → 동적 앙상블 계산까지
전체 흐름을 오케스트레이션하는 서비스.

파이프라인 및 스케줄러에서 단일 호출로 동적 앙상블 시그널을 얻을 수 있습니다.

사용법:
    runner = DynamicEnsembleRunner(db_session)
    result = await runner.run("005930", country="KR")
    print(result.ensemble_signal, result.regime, result.weights)

DB 없이 직접 OHLCV를 전달하는 것도 가능:
    result = runner.run_with_ohlcv(ohlcv_df)
"""

from dataclasses import dataclass
from typing import Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config.logging import logger
from core.quant_engine.vectorized_signals import VectorizedSignalGenerator
from core.strategy_ensemble.dynamic_ensemble import (
    DynamicEnsembleResult,
    DynamicEnsembleService,
)


@dataclass
class RunnerResult:
    """동적 앙상블 실행 결과 (메타데이터 포함)"""

    ticker: str
    ensemble: DynamicEnsembleResult
    signals: dict[str, pd.Series]  # MR/TF/RP 시그널 시계열
    ohlcv_days: int  # 사용된 OHLCV 일수
    country: str

    @property
    def ensemble_signal(self) -> float:
        return self.ensemble.ensemble_signal

    @property
    def regime(self):
        return self.ensemble.regime

    @property
    def weights(self) -> dict[str, float]:
        return self.ensemble.weights

    def to_summary_dict(self) -> dict:
        """API 응답 / 로그용 요약"""
        return {
            "ticker": self.ticker,
            "country": self.country,
            "ensemble_signal": round(self.ensemble_signal, 4),
            "regime": self.regime.value,
            "weights": {k: round(v, 4) for k, v in self.weights.items()},
            "adx": round(self.ensemble.adx, 2),
            "vol_percentile": round(self.ensemble.vol_percentile, 4),
            "vol_scalar": round(self.ensemble.vol_scalar, 4),
            "ohlcv_days": self.ohlcv_days,
        }


class DynamicEnsembleRunner:
    """
    동적 앙상블 실행기

    DB에서 OHLCV를 조회하고, 벡터화 시그널을 생성한 뒤,
    DynamicEnsembleService로 동적 앙상블을 계산합니다.
    """

    # 최소 200일 이상의 OHLCV 데이터 필요 (ADX 14일 + 60일 성과 윈도우 등)
    MIN_OHLCV_DAYS = 200

    def __init__(
        self,
        db_session: Optional[AsyncSession] = None,
        ensemble_params: Optional[dict] = None,
        min_window: int = 60,
    ):
        self._db = db_session
        self._signal_gen = VectorizedSignalGenerator(min_window=min_window)
        self._ensemble_svc = DynamicEnsembleService(params=ensemble_params)

    async def run(
        self,
        ticker: str,
        country: str = "KR",
        lookback_days: int = 300,
    ) -> RunnerResult:
        """
        DB에서 OHLCV를 조회하여 동적 앙상블 실행

        Args:
            ticker: 종목코드 (예: "005930", "AAPL")
            country: 국가 코드 ("KR" 또는 "US")
            lookback_days: 조회할 과거 일수 (기본 300영업일)

        Returns:
            RunnerResult

        Raises:
            ValueError: DB 세션 미설정 또는 데이터 부족
        """
        if self._db is None:
            raise ValueError(
                "DB session이 설정되지 않았습니다. " "run_with_ohlcv()를 사용하거나 db_session을 전달하세요."
            )

        ohlcv = await self._fetch_ohlcv(ticker, country, lookback_days)

        if len(ohlcv) < self.MIN_OHLCV_DAYS:
            raise ValueError(f"{ticker}: OHLCV 데이터 부족 " f"({len(ohlcv)}일 < 최소 {self.MIN_OHLCV_DAYS}일)")

        return self._compute(ticker, ohlcv, country)

    def run_with_ohlcv(
        self,
        ohlcv: pd.DataFrame,
        ticker: str = "UNKNOWN",
        country: str = "KR",
    ) -> RunnerResult:
        """
        OHLCV DataFrame을 직접 전달하여 동적 앙상블 실행 (DB 불필요)

        Args:
            ohlcv: OHLCV DataFrame (columns: open, high, low, close, volume)
            ticker: 종목코드 (로깅용)
            country: 국가 코드

        Returns:
            RunnerResult
        """
        if len(ohlcv) < self.MIN_OHLCV_DAYS:
            raise ValueError(f"{ticker}: OHLCV 데이터 부족 " f"({len(ohlcv)}일 < 최소 {self.MIN_OHLCV_DAYS}일)")

        return self._compute(ticker, ohlcv, country)

    def _compute(
        self,
        ticker: str,
        ohlcv: pd.DataFrame,
        country: str,
    ) -> RunnerResult:
        """벡터화 시그널 생성 → 동적 앙상블 계산"""
        logger.info(f"[DynamicEnsemble] {ticker} ({country}): " f"OHLCV {len(ohlcv)}일, 시그널 생성 시작")

        # 1. 벡터화 시그널 생성
        signals = self._signal_gen.generate(ohlcv)

        mr_signal = signals["MEAN_REVERSION"]
        tf_signal = signals["TREND_FOLLOWING"]
        rp_signal = signals["RISK_PARITY"]

        # 2. 동적 앙상블 계산
        ensemble_result = self._ensemble_svc.compute(ohlcv, mr_signal, tf_signal, rp_signal)

        logger.info(
            f"[DynamicEnsemble] {ticker}: "
            f"regime={ensemble_result.regime.value}, "
            f"signal={ensemble_result.ensemble_signal:.4f}, "
            f"weights=TF:{ensemble_result.weights['TF']:.2f}/"
            f"MR:{ensemble_result.weights['MR']:.2f}/"
            f"RP:{ensemble_result.weights['RP']:.2f}, "
            f"vol_scalar={ensemble_result.vol_scalar:.4f}"
        )

        return RunnerResult(
            ticker=ticker,
            ensemble=ensemble_result,
            signals=signals,
            ohlcv_days=len(ohlcv),
            country=country,
        )

    async def _fetch_ohlcv(
        self,
        ticker: str,
        country: str,
        lookback_days: int,
    ) -> pd.DataFrame:
        """DB에서 OHLCV 데이터 조회"""
        market_filter = self._get_market_filter(country)

        query = text(
            """
            SELECT time, open, high, low, close, volume
            FROM market_ohlcv
            WHERE ticker = :ticker
              AND market IN :markets
              AND interval = '1d'
            ORDER BY time DESC
            LIMIT :limit
        """
        )

        result = await self._db.execute(
            query,
            {
                "ticker": ticker,
                "markets": tuple(market_filter),
                "limit": lookback_days,
            },
        )
        rows = result.fetchall()

        if not rows:
            raise ValueError(f"{ticker}: DB에 OHLCV 데이터가 없습니다")

        df = pd.DataFrame(
            rows,
            columns=["time", "open", "high", "low", "close", "volume"],
        )
        df["time"] = pd.to_datetime(df["time"])
        df = df.sort_values("time").set_index("time")

        # float 변환
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)

        logger.debug(
            f"[DynamicEnsemble] {ticker}: "
            f"DB에서 {len(df)}일 OHLCV 조회 완료 "
            f"({df.index[0].date()} ~ {df.index[-1].date()})"
        )

        return df

    @staticmethod
    def _get_market_filter(country: str) -> list[str]:
        """국가별 시장 필터"""
        if country == "KR":
            return ["KRX"]
        elif country == "US":
            return ["NASDAQ", "NYSE", "AMEX"]
        else:
            raise ValueError(f"지원하지 않는 국가: {country}")
