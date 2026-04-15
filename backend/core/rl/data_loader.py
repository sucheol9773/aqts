"""
RL 학습 데이터 로더 (RL Training Data Loader)

DB(TimescaleDB)와 CSV에서 OHLCV 데이터를 로드하고
RL 학습에 적합한 형태로 전처리합니다.

데이터 소스 우선순위:
1. DB (market_ohlcv 테이블) — 프로덕션
2. CSV 파일 — 오프라인/로컬 학습
3. 합성 데이터 — 테스트/개발

사용법:
    loader = RLDataLoader()
    data = loader.load_from_db(tickers=["005930", "000660"])
    data = loader.load_from_csv("data/ohlcv/")
    data = loader.generate_synthetic(n_tickers=5, n_days=2000)
"""

from pathlib import Path

import numpy as np
import pandas as pd

from config.logging import logger


class RLDataLoader:
    """
    RL 학습용 OHLCV 데이터 로더

    DB, CSV, 합성 데이터 3가지 소스를 지원하며
    데이터 검증과 전처리를 수행합니다.
    """

    # 최소 학습 데이터 길이 (lookback 60 + 최소 252 거래일)
    MIN_DATA_LENGTH = 312

    def load_from_db(
        self,
        db_url: str | None = None,
        tickers: list[str] | None = None,
        start_date: str = "2015-01-01",
        end_date: str | None = None,
    ) -> dict[str, pd.DataFrame]:
        """
        DB에서 OHLCV 데이터 로드

        Args:
            db_url: DB 연결 URL (None이면 환경변수에서 구성)
            tickers: 종목 코드 리스트 (None이면 전체)
            start_date: 시작일
            end_date: 종료일

        Returns:
            {ticker: DataFrame} 딕셔너리
        """
        from sqlalchemy import create_engine, text

        if db_url is None:
            db_url = self._build_db_url()

        engine = create_engine(db_url)

        # 티커 목록 조회
        if tickers is None:
            with engine.connect() as conn:
                result = conn.execute(
                    text("SELECT DISTINCT ticker FROM market_ohlcv " "WHERE date >= :start ORDER BY ticker"),
                    {"start": start_date},
                )
                tickers = [row[0] for row in result]

        logger.info(f"Loading {len(tickers)} tickers from DB...")

        data = {}
        for ticker in tickers:
            query = text(
                "SELECT date, open, high, low, close, volume "
                "FROM market_ohlcv "
                "WHERE ticker = :ticker AND date >= :start "
                + ("AND date <= :end " if end_date else "")
                + "ORDER BY date"
            )
            params = {"ticker": ticker, "start": start_date}
            if end_date:
                params["end"] = end_date

            with engine.connect() as conn:
                df = pd.read_sql(query, conn, params=params, parse_dates=["date"])

            if len(df) >= self.MIN_DATA_LENGTH:
                df = df.set_index("date")
                df = self._validate_and_clean(df, ticker)
                if df is not None:
                    data[ticker] = df
            else:
                logger.warning(f"Skipping {ticker}: {len(df)} rows < {self.MIN_DATA_LENGTH} minimum")

        logger.info(f"Loaded {len(data)} tickers (min {self.MIN_DATA_LENGTH} rows each)")
        return data

    def load_from_csv(
        self,
        csv_dir: str,
        tickers: list[str] | None = None,
    ) -> dict[str, pd.DataFrame]:
        """
        CSV 디렉토리에서 OHLCV 데이터 로드

        Args:
            csv_dir: CSV 파일이 있는 디렉토리 경로
            tickers: 로드할 종목 코드 (None이면 전체)

        Returns:
            {ticker: DataFrame} 딕셔너리

        CSV 파일 형식:
            - 파일명: {ticker}.csv (예: 005930.csv)
            - 컬럼: date, open, high, low, close, volume
        """
        csv_path = Path(csv_dir)
        if not csv_path.exists():
            logger.error(f"CSV directory not found: {csv_dir}")
            return {}

        csv_files = sorted(csv_path.glob("*.csv"))
        if tickers:
            csv_files = [f for f in csv_files if f.stem in tickers]

        logger.info(f"Loading {len(csv_files)} CSV files from {csv_dir}...")

        data = {}
        for csv_file in csv_files:
            ticker = csv_file.stem
            try:
                df = pd.read_csv(csv_file, parse_dates=["date"])
                df = df.set_index("date")
                df = df.sort_index()

                if len(df) >= self.MIN_DATA_LENGTH:
                    df = self._validate_and_clean(df, ticker)
                    if df is not None:
                        data[ticker] = df
                else:
                    logger.warning(f"Skipping {ticker}: {len(df)} rows < minimum")
            except Exception as e:
                logger.warning(f"Failed to load {csv_file}: {e}")

        logger.info(f"Loaded {len(data)} tickers from CSV")
        return data

    def generate_synthetic(
        self,
        n_tickers: int = 5,
        n_days: int = 2000,
        seed: int = 42,
    ) -> dict[str, pd.DataFrame]:
        """
        합성 OHLCV 데이터 생성 (테스트/개발용)

        다양한 시장 특성을 가진 합성 종목을 생성합니다:
        - 상승 추세, 하락 추세, 횡보, 고변동성, 레짐 전환

        Args:
            n_tickers: 생성할 종목 수
            n_days: 종목당 거래일 수
            seed: 랜덤 시드

        Returns:
            {ticker: DataFrame} 딕셔너리
        """
        rng = np.random.RandomState(seed)
        dates = pd.bdate_range("2018-01-01", periods=n_days)

        # 다양한 특성의 합성 종목
        profiles = [
            {"name": "TREND_UP", "trend": 0.0008, "noise": 0.015},
            {"name": "TREND_DOWN", "trend": -0.0005, "noise": 0.015},
            {"name": "SIDEWAYS", "trend": 0.0001, "noise": 0.012},
            {"name": "HIGH_VOL", "trend": 0.0005, "noise": 0.035},
            {"name": "REGIME_SWITCH", "trend": 0.0003, "noise": 0.02},
        ]

        data = {}
        for i in range(n_tickers):
            profile = profiles[i % len(profiles)]
            ticker = f"SYN_{profile['name']}_{i:02d}"

            trend = profile["trend"]
            noise = profile["noise"]

            # 레짐 전환 시뮬레이션
            if "REGIME" in profile["name"]:
                returns = np.zeros(n_days)
                for j in range(n_days):
                    # 500일 주기로 레짐 전환
                    phase = (j // 500) % 3
                    if phase == 0:
                        returns[j] = 0.001 + noise * 0.7 * rng.randn()
                    elif phase == 1:
                        returns[j] = -0.0005 + noise * 1.5 * rng.randn()
                    else:
                        returns[j] = 0.0002 + noise * rng.randn()
            else:
                returns = trend + noise * rng.randn(n_days)

            close = 50000.0 * np.cumprod(1 + returns)
            high = close * (1 + abs(noise * rng.randn(n_days) * 0.5))
            low = close * (1 - abs(noise * rng.randn(n_days) * 0.5))
            open_ = close * (1 + noise * rng.randn(n_days) * 0.3)
            volume = 500_000 * (1 + 0.5 * rng.randn(n_days))
            volume = np.maximum(volume, 10000)

            df = pd.DataFrame(
                {
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                },
                index=dates,
            )
            data[ticker] = df

        logger.info(f"Generated {n_tickers} synthetic tickers ({n_days} days each)")
        return data

    def _validate_and_clean(self, df: pd.DataFrame, ticker: str) -> pd.DataFrame | None:
        """
        데이터 검증 및 전처리

        - 필수 컬럼 확인
        - NaN/Inf 제거
        - 가격 0 이하 제거
        - 날짜 정렬
        """
        required_cols = {"open", "high", "low", "close", "volume"}
        if not required_cols.issubset(set(df.columns)):
            missing = required_cols - set(df.columns)
            logger.warning(f"{ticker}: missing columns {missing}")
            return None

        # 숫자형 변환
        for col in required_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # NaN 행 제거
        before_len = len(df)
        df = df.dropna(subset=list(required_cols))

        # 가격 0 이하 제거
        df = df[(df["close"] > 0) & (df["open"] > 0)]

        # volume 음수 → 0
        df["volume"] = df["volume"].clip(lower=0)

        after_len = len(df)
        if before_len != after_len:
            logger.info(f"{ticker}: cleaned {before_len - after_len} rows")

        if len(df) < self.MIN_DATA_LENGTH:
            logger.warning(f"{ticker}: only {len(df)} rows after cleaning")
            return None

        return df.sort_index()

    @staticmethod
    def _build_db_url() -> str:
        """환경변수에서 DB URL 구성.

        단일 진실원천: ``config.settings.DatabaseSettings`` (env_prefix=``DB_``).
        기존에는 ``POSTGRES_*`` 를 직접 읽었으나 이는 ``.env.example`` /
        ``DatabaseSettings`` 와 드리프트를 일으켜 운영-개발 환경 불일치의
        원인이 됐다. 공용 파서를 재사용해 env 키 해석을 한 군데로 모은다
        (RBAC Wiring Rule 의 설정 도메인 확장 — "정의 ≠ 적용").
        """
        from config.settings import DatabaseSettings

        return DatabaseSettings().sync_url
