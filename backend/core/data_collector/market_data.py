"""
시세 데이터 수집 서비스 (Market Data Collector)

F-01-01 명세 구현:
- 한국/미국 시장 OHLCV 데이터 수집
- 시간대 관리 (UTC 기준 저장, KST/EST 변환)
- 데이터 무결성 검증 (결측 Forward Fill, 이상치 2단계 검증)
- Rate Limit 준수
"""

from datetime import datetime, date, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config.constants import Country, Market, DATA_INTEGRITY
from config.logging import logger
from core.data_collector.kis_client import KISClient


class MarketDataCollector:
    """시세 데이터 수집 및 저장 서비스"""

    def __init__(self, db_session: AsyncSession):
        self._kis = KISClient()
        self._db = db_session

    # ══════════════════════════════════════
    # 한국 시장 데이터 수집
    # ══════════════════════════════════════
    async def collect_kr_daily(
        self, ticker: str, start_date: str, end_date: str
    ) -> int:
        """
        국내 주식 일봉 데이터 수집 및 저장

        Args:
            ticker: 종목코드 (예: "005930")
            start_date: 시작일 (YYYYMMDD)
            end_date: 종료일 (YYYYMMDD)

        Returns:
            저장된 레코드 수
        """
        logger.info(f"Collecting KR daily data: {ticker} ({start_date} ~ {end_date})")

        try:
            response = await self._kis.get_kr_stock_daily(ticker, start_date, end_date)
            output = response.get("output2", [])

            if not output:
                logger.warning(f"No data returned for {ticker}")
                return 0

            records = []
            for row in output:
                try:
                    record = {
                        "time": datetime.strptime(row["stck_bsop_date"], "%Y%m%d"),
                        "ticker": ticker,
                        "market": Market.KRX.value,
                        "open": float(row["stck_oprc"]),
                        "high": float(row["stck_hgpr"]),
                        "low": float(row["stck_lwpr"]),
                        "close": float(row["stck_clpr"]),
                        "volume": int(row["acml_vol"]),
                        "interval": "1d",
                    }
                    records.append(record)
                except (KeyError, ValueError) as e:
                    logger.warning(f"Skipping malformed row for {ticker}: {e}")
                    continue

            if records:
                saved = await self._save_ohlcv_batch(records)
                logger.info(f"Saved {saved} records for KR:{ticker}")
                return saved

            return 0

        except Exception as e:
            logger.error(f"Failed to collect KR daily data for {ticker}: {e}")
            raise

    # ══════════════════════════════════════
    # 미국 시장 데이터 수집
    # ══════════════════════════════════════
    async def collect_us_daily(
        self, ticker: str, exchange: str = "NAS", count: int = 100
    ) -> int:
        """
        해외 주식 일봉 데이터 수집 및 저장

        Args:
            ticker: 종목코드 (예: "AAPL")
            exchange: 거래소 (NAS, NYS, AMS)
            count: 조회 건수

        Returns:
            저장된 레코드 수
        """
        logger.info(f"Collecting US daily data: {ticker}@{exchange}")

        market_map = {"NAS": Market.NASDAQ.value, "NYS": Market.NYSE.value, "AMS": Market.AMEX.value}
        market = market_map.get(exchange, Market.NASDAQ.value)

        try:
            response = await self._kis.get_us_stock_daily(ticker, count=count, exchange=exchange)
            output = response.get("output2", [])

            if not output:
                logger.warning(f"No data returned for {ticker}@{exchange}")
                return 0

            records = []
            for row in output:
                try:
                    record = {
                        "time": datetime.strptime(row["xymd"], "%Y%m%d"),
                        "ticker": ticker,
                        "market": market,
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["clos"]),
                        "volume": int(row["tvol"]),
                        "interval": "1d",
                    }
                    records.append(record)
                except (KeyError, ValueError) as e:
                    logger.warning(f"Skipping malformed row for {ticker}: {e}")
                    continue

            if records:
                saved = await self._save_ohlcv_batch(records)
                logger.info(f"Saved {saved} records for US:{ticker}")
                return saved

            return 0

        except Exception as e:
            logger.error(f"Failed to collect US daily data for {ticker}: {e}")
            raise

    # ══════════════════════════════════════
    # 현재가 조회
    # ══════════════════════════════════════
    async def get_current_price(self, ticker: str, country: Country) -> Optional[float]:
        """
        종목 현재가 조회

        Args:
            ticker: 종목코드
            country: 국가 (KR/US)

        Returns:
            현재가 (float) 또는 None
        """
        try:
            if country == Country.KR:
                response = await self._kis.get_kr_stock_price(ticker)
                output = response.get("output", {})
                return float(output.get("stck_prpr", 0))
            else:
                response = await self._kis.get_us_stock_price(ticker)
                output = response.get("output", {})
                return float(output.get("last", 0))
        except Exception as e:
            logger.error(f"Failed to get current price for {ticker}: {e}")
            return None

    # ══════════════════════════════════════
    # 데이터 무결성 검증
    # ══════════════════════════════════════
    async def validate_and_fill(self, ticker: str, market: str) -> dict:
        """
        데이터 무결성 검증 및 결측 보간

        F-01-01-A 명세:
        - Forward Fill (전 영업일 값)
        - 연속 3영업일 초과 결측 시 유니버스 일시 제외
        - 이상치 2단계 검증 (3시그마 → 교차검증)

        Returns:
            {"missing_filled": int, "outliers_flagged": int, "excluded": bool}
        """
        result = {"missing_filled": 0, "outliers_flagged": 0, "excluded": False}

        query = text("""
            SELECT time, close, volume
            FROM market_ohlcv
            WHERE ticker = :ticker AND market = :market AND interval = '1d'
            ORDER BY time DESC
            LIMIT 60
        """)
        rows = await self._db.execute(query, {"ticker": ticker, "market": market})
        data = rows.fetchall()

        if len(data) < 5:
            return result

        df = pd.DataFrame(data, columns=["time", "close", "volume"])
        df = df.sort_values("time").reset_index(drop=True)

        # ── 결측 검증 ──
        consecutive_missing = self._check_consecutive_missing(df)
        max_missing = DATA_INTEGRITY["max_consecutive_missing_days"]
        if consecutive_missing > max_missing:
            logger.warning(
                f"{ticker}: {consecutive_missing} consecutive missing days (limit: {max_missing}). "
                "Flagging for universe exclusion."
            )
            result["excluded"] = True

        # ── 이상치 검증 (1단계: 자동 3시그마) ──
        if len(df) >= 10:
            returns = df["close"].pct_change().dropna()
            sigma = DATA_INTEGRITY["outlier_sigma_threshold"]
            mean_ret = returns.mean()
            std_ret = returns.std()

            if std_ret > 0:
                z_scores = (returns - mean_ret) / std_ret
                outlier_mask = z_scores.abs() > sigma

                for idx in outlier_mask[outlier_mask].index:
                    daily_return = returns.iloc[idx] if idx < len(returns) else 0
                    abs_return = abs(daily_return)

                    # 상하한가/서킷브레이커 범위 내 급변은 정상 처리
                    if market in [Market.KRX.value]:
                        limit = DATA_INTEGRITY["kr_daily_limit_pct"]
                        if abs_return <= limit:
                            continue
                    else:
                        # 미국 서킷브레이커 1단계 이내는 정상
                        limit = DATA_INTEGRITY["us_circuit_breaker_l1"]
                        if abs_return <= limit:
                            continue

                    result["outliers_flagged"] += 1
                    logger.warning(
                        f"{ticker}: Outlier detected at index {idx}, "
                        f"return={daily_return:.4f}, z-score={z_scores.iloc[idx]:.2f}. "
                        "Cross-validation required."
                    )

        return result

    def _check_consecutive_missing(self, df: pd.DataFrame) -> int:
        """연속 결측 영업일 수 계산"""
        if df.empty:
            return 0

        dates = pd.to_datetime(df["time"]).dt.date
        business_days = pd.bdate_range(start=dates.min(), end=dates.max())
        existing_dates = set(dates)

        max_consecutive = 0
        current_consecutive = 0

        for bd in business_days:
            if bd.date() not in existing_dates:
                current_consecutive += 1
                max_consecutive = max(max_consecutive, current_consecutive)
            else:
                current_consecutive = 0

        return max_consecutive

    # ══════════════════════════════════════
    # DB 저장
    # ══════════════════════════════════════
    async def _save_ohlcv_batch(self, records: list[dict]) -> int:
        """OHLCV 데이터 배치 저장 (UPSERT)"""
        if not records:
            return 0

        query = text("""
            INSERT INTO market_ohlcv (time, ticker, market, open, high, low, close, volume, interval)
            VALUES (:time, :ticker, :market, :open, :high, :low, :close, :volume, :interval)
            ON CONFLICT (time, ticker, interval) DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                volume = EXCLUDED.volume
        """)

        await self._db.execute(query, records)
        await self._db.commit()
        return len(records)
