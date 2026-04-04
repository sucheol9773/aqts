"""
DART 재무제표 수집 모듈 (Financial Statements Collector)

F-01-06 명세 구현:
- DART API 단일회사 재무제표 조회 (운영 중 갱신용)
- txt 일괄 다운로드 파싱 (초기 데이터 적재용)
- 파생 지표 계산 (PER, PBR, ROE, ROA, 부채비율, EV/EBITDA)
- 팩터 분석기에 전달할 DataFrame 생성
"""

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import httpx
import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config.constants import DART_API_BASE_URL
from config.logging import logger
from config.settings import get_settings


# ══════════════════════════════════════
# 데이터 컨테이너
# ══════════════════════════════════════
@dataclass
class FinancialStatement:
    """재무제표 데이터 컨테이너"""

    corp_code: str  # DART 고유번호 (8자리)
    ticker: str  # 종목코드
    corp_name: str  # 회사명
    bsns_year: int  # 사업연도
    reprt_code: str  # 보고서코드
    fs_div: str  # 재무제표구분 (OFS=개별, CFS=연결)
    revenue: Optional[float] = None  # 매출액
    operating_income: Optional[float] = None  # 영업이익
    net_income: Optional[float] = None  # 당기순이익
    total_assets: Optional[float] = None  # 총자산
    total_liabilities: Optional[float] = None  # 총부채
    total_equity: Optional[float] = None  # 자본총계
    eps: Optional[float] = None  # 주당순이익
    collected_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        """dataclass를 딕셔너리로 변환"""
        data = asdict(self)
        # datetime을 문자열로 변환
        data["collected_at"] = self.collected_at.isoformat() if self.collected_at else None
        return data

    @property
    def is_available(self) -> bool:
        """필수 필드 채움 여부"""
        return self.ticker and self.bsns_year and self.reprt_code


@dataclass
class DerivedMetrics:
    """파생 지표 컨테이너"""

    ticker: str
    per: Optional[float] = None  # Price-to-Earnings Ratio
    pbr: Optional[float] = None  # Price-to-Book Ratio
    roe: Optional[float] = None  # Return on Equity
    roa: Optional[float] = None  # Return on Assets
    debt_ratio: Optional[float] = None  # 부채비율
    ev_ebitda: Optional[float] = None  # EV/EBITDA
    calculated_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        """dataclass를 딕셔너리로 변환"""
        data = asdict(self)
        data["calculated_at"] = self.calculated_at.isoformat() if self.calculated_at else None
        return data

    @property
    def is_available(self) -> bool:
        """최소 하나 이상의 지표 보유 여부"""
        return any(
            [
                self.per is not None,
                self.pbr is not None,
                self.roe is not None,
                self.roa is not None,
                self.debt_ratio is not None,
                self.ev_ebitda is not None,
            ]
        )


# ══════════════════════════════════════
# DART 보고서 코드 매핑
# ══════════════════════════════════════
REPORT_CODE_MAP = {
    "1분기": "11013",
    "반기": "11012",
    "3분기": "11014",
    "사업보고서": "11011",
}

REPORT_CODE_INVERSE = {v: k for k, v in REPORT_CODE_MAP.items()}

# DART 계정과목 매핑 (응답 필드명 -> 내부 필드명)
ACCOUNT_MAP = {
    "매출액": "revenue",
    "매출": "revenue",
    "영업이익": "operating_income",
    "당기순이익": "net_income",
    "총자산": "total_assets",
    "총부채": "total_liabilities",
    "자본총계": "total_equity",
    "기본주당순이익": "eps",
    "주당순이익": "eps",
}


# ══════════════════════════════════════
# DART 재무제표 수집 서비스
# ══════════════════════════════════════
class FinancialCollectorService:
    """DART 재무제표 수집 및 처리 서비스"""

    def __init__(self, db_session: AsyncSession):
        """
        초기화

        Args:
            db_session: SQLAlchemy AsyncSession
        """
        self._db = db_session
        self._settings = get_settings()
        self._dart_api_key = self._settings.external.dart_api_key
        self._http_client = None
        self._api_retry_count = 3
        self._api_timeout = 10

    async def __aenter__(self):
        """Context manager 진입"""
        self._http_client = httpx.AsyncClient(timeout=self._api_timeout)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager 탈출"""
        if self._http_client:
            await self._http_client.aclose()

    # ══════════════════════════════════════
    # DART API 단일회사 조회
    # ══════════════════════════════════════
    async def fetch_single_company(
        self,
        corp_code: str,
        bsns_year: int,
        reprt_code: str,
        fs_div: str = "CFS",
    ) -> Optional[FinancialStatement]:
        """
        DART API에서 단일회사 재무제표 조회

        Args:
            corp_code: DART 고유번호 (8자리, 예: "00126380")
            bsns_year: 사업연도 (예: 2023)
            reprt_code: 보고서코드 (11013=1분기, 11012=반기, 11014=3분기, 11011=사업보고서)
            fs_div: 재무제표구분 (OFS=개별, CFS=연결, 기본값: CFS)

        Returns:
            FinancialStatement 또는 None
        """
        if not self._dart_api_key:
            logger.error("DART_API_KEY not configured")
            return None

        logger.info(
            f"Fetching DART financial data: corp_code={corp_code}, "
            f"bsns_year={bsns_year}, reprt_code={reprt_code}, fs_div={fs_div}"
        )

        # 기본 정보 조회 (ticker, corp_name)
        corp_info = await self._fetch_corp_info(corp_code)
        if not corp_info:
            logger.warning(f"Could not find company info for corp_code: {corp_code}")
            return None

        ticker = corp_info.get("ticker")
        corp_name = corp_info.get("corp_name")

        # 재무제표 조회
        financial_data = await self._fetch_financial_data(corp_code, bsns_year, reprt_code, fs_div)

        if not financial_data:
            logger.warning(f"No financial data found for {corp_code} ({bsns_year}, {reprt_code})")
            return None

        # 결과 조합
        stmt = FinancialStatement(
            corp_code=corp_code,
            ticker=ticker,
            corp_name=corp_name,
            bsns_year=bsns_year,
            reprt_code=reprt_code,
            fs_div=fs_div,
            **financial_data,
        )

        logger.info(
            f"Successfully fetched DART data for {corp_name} ({ticker}): "
            f"revenue={financial_data.get('revenue')}, "
            f"net_income={financial_data.get('net_income')}"
        )

        return stmt

    async def _fetch_corp_info(self, corp_code: str) -> Optional[dict]:
        """
        회사 기본정보 조회 (ticker, corp_name)

        DART 고유번호로부터 종목코드와 회사명을 조회합니다.
        (실제 구현시 별도 매핑 테이블이 필요하거나 KIS API 활용)
        """
        query = text("""
            SELECT ticker, corp_name FROM company_info WHERE corp_code = :corp_code LIMIT 1
        """)
        result = await self._db.execute(query, {"corp_code": corp_code})
        row = result.fetchone()

        if row:
            return {"ticker": row[0], "corp_name": row[1]}

        logger.warning(f"Company info not found in DB for corp_code: {corp_code}")
        return None

    async def _fetch_financial_data(
        self,
        corp_code: str,
        bsns_year: int,
        reprt_code: str,
        fs_div: str,
    ) -> Optional[dict]:
        """
        DART API에서 재무제표 데이터 조회

        Args:
            corp_code: DART 고유번호
            bsns_year: 사업연도
            reprt_code: 보고서코드
            fs_div: 재무제표구분

        Returns:
            {revenue, operating_income, net_income, ...} 또는 None
        """
        if not self._http_client:
            logger.error("HTTP client not initialized. Use async context manager.")
            return None

        url = f"{DART_API_BASE_URL}/fnlttSinglAcntAll.json"
        params = {
            "crtfc_key": self._dart_api_key,
            "corp_code": corp_code,
            "bsns_year": str(bsns_year),
            "reprt_code": reprt_code,
            "fs_div": fs_div,
        }

        # 재시도 로직
        for attempt in range(self._api_retry_count):
            try:
                response = await self._http_client.get(url, params=params)
                response.raise_for_status()

                data = response.json()

                if data.get("status") != "000":
                    logger.warning(
                        f"DART API returned status {data.get('status')}: " f"{data.get('message', 'Unknown error')}"
                    )
                    return None

                # 응답 파싱
                items = data.get("list", [])
                if not items:
                    logger.warning(f"Empty response from DART API for {corp_code}")
                    return None

                return self._parse_financial_items(items)

            except httpx.HTTPStatusError as e:
                logger.warning(
                    f"DART API HTTP error (attempt {attempt + 1}/{self._api_retry_count}): " f"{e.response.status_code}"
                )
                if attempt < self._api_retry_count - 1:
                    await asyncio.sleep(1)
                else:
                    return None

            except Exception as e:
                logger.error(f"Error fetching DART data (attempt {attempt + 1}/{self._api_retry_count}): {e}")
                if attempt < self._api_retry_count - 1:
                    await asyncio.sleep(1)
                else:
                    return None

        return None

    def _parse_financial_items(self, items: list) -> dict:
        """
        DART API 응답 파싱

        Args:
            items: DART API의 list 필드

        Returns:
            {revenue, operating_income, ...} 형태의 딕셔너리
        """
        result = {}

        for item in items:
            account_nm = item.get("account_nm", "").strip()

            # 계정과목 매핑
            field_name = None
            for key, value in ACCOUNT_MAP.items():
                if key in account_nm:
                    field_name = value
                    break

            if not field_name:
                continue

            # 당기금액 추출
            try:
                amount_str = item.get("thstrm_amount", "0").strip()
                if amount_str and amount_str != "-":
                    amount = float(amount_str)
                    result[field_name] = amount
            except (ValueError, TypeError):
                logger.debug(f"Could not parse amount for {account_nm}")
                continue

        return result

    # ══════════════════════════════════════
    # txt 파일 파싱
    # ══════════════════════════════════════
    async def parse_bulk_txt(self, file_path: Path) -> List[FinancialStatement]:
        """
        DART 다운로드 txt 파일 파싱

        txt 파일은 탭 구분자 형식으로:
        corp_code | ticker | corp_name | bsns_year | reprt_code | fs_div |
        revenue | operating_income | net_income | total_assets | total_liabilities |
        total_equity | eps

        Args:
            file_path: txt 파일 경로

        Returns:
            FinancialStatement 리스트
        """
        logger.info(f"Parsing DART bulk txt file: {file_path}")

        statements = []

        try:
            # 탭 구분자로 읽기
            df = pd.read_csv(
                file_path,
                sep="\t",
                dtype={
                    "corp_code": str,
                    "ticker": str,
                    "corp_name": str,
                    "bsns_year": "Int64",
                    "reprt_code": str,
                    "fs_div": str,
                },
            )

            for _, row in df.iterrows():
                try:
                    # NaN 값을 None으로 변환
                    def _to_float(v):
                        if pd.isna(v):
                            return None
                        try:
                            return float(v)
                        except (ValueError, TypeError):
                            return None

                    stmt = FinancialStatement(
                        corp_code=str(row["corp_code"]).zfill(8),
                        ticker=str(row["ticker"]),
                        corp_name=str(row["corp_name"]),
                        bsns_year=int(row["bsns_year"]),
                        reprt_code=str(row["reprt_code"]),
                        fs_div=str(row["fs_div"]),
                        revenue=_to_float(row.get("revenue")),
                        operating_income=_to_float(row.get("operating_income")),
                        net_income=_to_float(row.get("net_income")),
                        total_assets=_to_float(row.get("total_assets")),
                        total_liabilities=_to_float(row.get("total_liabilities")),
                        total_equity=_to_float(row.get("total_equity")),
                        eps=_to_float(row.get("eps")),
                    )

                    if stmt.is_available:
                        statements.append(stmt)

                except Exception as e:
                    logger.warning(f"Error parsing row {row.name}: {e}")
                    continue

            logger.info(f"Parsed {len(statements)} financial statements from {file_path}")
            return statements

        except Exception as e:
            logger.error(f"Failed to parse txt file {file_path}: {e}")
            raise

    # ══════════════════════════════════════
    # DB 저장
    # ══════════════════════════════════════
    async def save_to_db(self, statements: List[FinancialStatement]) -> int:
        """
        재무제표를 DB에 저장

        Args:
            statements: FinancialStatement 리스트

        Returns:
            저장된 레코드 수
        """
        if not statements:
            return 0

        logger.info(f"Saving {len(statements)} financial statements to DB")

        query = text("""
            INSERT INTO financial_statements
            (corp_code, ticker, corp_name, bsns_year, reprt_code, fs_div,
             revenue, operating_income, net_income, total_assets, total_liabilities,
             total_equity, eps, collected_at)
            VALUES
            (:corp_code, :ticker, :corp_name, :bsns_year, :reprt_code, :fs_div,
             :revenue, :operating_income, :net_income, :total_assets, :total_liabilities,
             :total_equity, :eps, :collected_at)
            ON CONFLICT (corp_code, bsns_year, reprt_code, fs_div) DO UPDATE SET
                ticker = EXCLUDED.ticker,
                corp_name = EXCLUDED.corp_name,
                revenue = EXCLUDED.revenue,
                operating_income = EXCLUDED.operating_income,
                net_income = EXCLUDED.net_income,
                total_assets = EXCLUDED.total_assets,
                total_liabilities = EXCLUDED.total_liabilities,
                total_equity = EXCLUDED.total_equity,
                eps = EXCLUDED.eps,
                collected_at = EXCLUDED.collected_at
        """)

        try:
            records = [stmt.to_dict() for stmt in statements]
            await self._db.execute(query, records)
            await self._db.commit()

            logger.info(f"Successfully saved {len(statements)} statements to DB")
            return len(statements)

        except Exception as e:
            logger.error(f"Failed to save statements to DB: {e}")
            await self._db.rollback()
            raise

    # ══════════════════════════════════════
    # 파생 지표 계산
    # ══════════════════════════════════════
    def calculate_derived_metrics(
        self,
        ticker: str,
        financial_data: FinancialStatement,
        market_data: dict,
    ) -> DerivedMetrics:
        """
        파생 지표 계산

        Args:
            ticker: 종목코드
            financial_data: FinancialStatement
            market_data: {
                'current_price': float,
                'shares_outstanding': float (발행주식수),
                'ebitda': float (optional),
                'net_debt': float (optional, net_cash)
            }

        Returns:
            DerivedMetrics
        """
        metrics = DerivedMetrics(ticker=ticker)

        if not financial_data.is_available:
            logger.warning(f"Insufficient financial data for {ticker}")
            return metrics

        current_price = market_data.get("current_price")
        shares_outstanding = market_data.get("shares_outstanding")
        ebitda = market_data.get("ebitda")
        net_debt = market_data.get("net_debt", 0)

        # PER = 주가 / EPS
        if current_price and financial_data.eps and financial_data.eps > 0:
            metrics.per = current_price / financial_data.eps

        # PBR = 주가 / BPS (BPS = 자본총계 / 발행주식수)
        if current_price and shares_outstanding and financial_data.total_equity and financial_data.total_equity > 0:
            bps = financial_data.total_equity / shares_outstanding
            if bps > 0:
                metrics.pbr = current_price / bps

        # ROE = 당기순이익 / 자본총계
        if financial_data.net_income and financial_data.total_equity:
            if financial_data.total_equity != 0:
                metrics.roe = financial_data.net_income / financial_data.total_equity

        # ROA = 당기순이익 / 총자산
        if financial_data.net_income and financial_data.total_assets:
            if financial_data.total_assets != 0:
                metrics.roa = financial_data.net_income / financial_data.total_assets

        # 부채비율 = 총부채 / 자본총계
        if financial_data.total_liabilities and financial_data.total_equity:
            if financial_data.total_equity != 0:
                metrics.debt_ratio = financial_data.total_liabilities / financial_data.total_equity

        # EV/EBITDA = (시가총액 + 순차입금) / EBITDA
        if current_price and shares_outstanding and ebitda and ebitda > 0:
            market_cap = current_price * shares_outstanding
            ev = market_cap + net_debt
            metrics.ev_ebitda = ev / ebitda

        logger.debug(
            f"Calculated metrics for {ticker}: PER={metrics.per}, PBR={metrics.pbr}, "
            f"ROE={metrics.roe}, ROA={metrics.roa}, debt_ratio={metrics.debt_ratio}"
        )

        return metrics

    # ══════════════════════════════════════
    # 팩터 분석기용 DataFrame 생성
    # ══════════════════════════════════════
    async def get_factor_data(
        self,
        tickers: List[str],
        include_market_data: bool = True,
    ) -> pd.DataFrame:
        """
        팩터 분석기에 전달할 DataFrame 생성

        factor_analyzer.py의 calculate_composite_scores(df)에 입력하기 위한
        DataFrame을 생성합니다.

        Args:
            tickers: 종목코드 리스트
            include_market_data: 시장 데이터 포함 여부 (True시 beta, volatility 등)

        Returns:
            DataFrame with columns:
            [ticker, per, pbr, ev_ebitda, roe, roa, debt_ratio, market_cap,
             return_12m, return_1m, volatility_60d, beta]
        """
        logger.info(f"Building factor data for {len(tickers)} tickers " f"(include_market_data={include_market_data})")

        result_rows = []

        for ticker in tickers:
            try:
                # 최신 재무제표 조회
                stmt_query = text("""
                    SELECT
                        ticker, revenue, operating_income, net_income,
                        total_assets, total_liabilities, total_equity, eps
                    FROM financial_statements
                    WHERE ticker = :ticker
                    ORDER BY bsns_year DESC, reprt_code DESC
                    LIMIT 1
                """)

                result = await self._db.execute(stmt_query, {"ticker": ticker})
                stmt_row = result.fetchone()

                if not stmt_row:
                    logger.debug(f"No financial data found for {ticker}")
                    continue

                # 기본 재무 지표
                row_data = {
                    "ticker": ticker,
                    "per": None,
                    "pbr": None,
                    "ev_ebitda": None,
                    "roe": None,
                    "roa": None,
                    "debt_ratio": None,
                    "market_cap": None,
                }

                # 현재가 조회 (DB에서)
                price_query = text("""
                    SELECT close FROM market_ohlcv
                    WHERE ticker = :ticker AND market = 'KRX' AND interval = '1d'
                    ORDER BY time DESC
                    LIMIT 1
                """)
                price_result = await self._db.execute(price_query, {"ticker": ticker})
                price_row = price_result.fetchone()
                current_price = float(price_row[0]) if price_row else None

                if not current_price:
                    logger.debug(f"No current price found for {ticker}")
                    continue

                # 파생 지표 계산
                net_income = stmt_row[3]
                total_equity = stmt_row[6]
                total_assets = stmt_row[4]
                eps = stmt_row[7]

                if eps and eps > 0:
                    row_data["per"] = current_price / eps

                if total_equity and total_equity > 0:
                    row_data["roe"] = net_income / total_equity if net_income else None

                if total_assets and total_assets > 0:
                    row_data["roa"] = net_income / total_assets if net_income else None

                if total_equity and total_equity > 0:
                    liabilities = stmt_row[5]
                    row_data["debt_ratio"] = liabilities / total_equity if liabilities else None

                # 시가총액 (shares_outstanding은 별도 조회 필요)
                # 여기서는 None으로 두며, 호출처에서 보충
                row_data["market_cap"] = None

                # 시장 데이터 포함시
                if include_market_data:
                    market_query = text("""
                        SELECT
                            close,
                            (SELECT (close / LAG(close, 252) OVER (ORDER BY time) - 1)
                             FROM market_ohlcv AS m2
                             WHERE m2.ticker = :ticker AND m2.interval = '1d'
                             ORDER BY m2.time DESC LIMIT 1) AS return_12m,
                            (SELECT (close / LAG(close, 20) OVER (ORDER BY time) - 1)
                             FROM market_ohlcv AS m2
                             WHERE m2.ticker = :ticker AND m2.interval = '1d'
                             ORDER BY m2.time DESC LIMIT 1) AS return_1m,
                            STDDEV(
                                close / LAG(close) OVER (ORDER BY time) - 1
                            ) FILTER (WHERE time >= CURRENT_TIMESTAMP - interval '60 days')
                             AS volatility_60d
                        FROM market_ohlcv
                        WHERE ticker = :ticker AND interval = '1d'
                        ORDER BY time DESC
                        LIMIT 1
                    """)

                    market_result = await self._db.execute(market_query, {"ticker": ticker})
                    market_row = market_result.fetchone()

                    if market_row:
                        row_data["return_12m"] = market_row[1]
                        row_data["return_1m"] = market_row[2]
                        row_data["volatility_60d"] = market_row[3]
                        # beta는 벤치마크 대비 계산 필요 (여기서는 None)
                        row_data["beta"] = None
                    else:
                        row_data["return_12m"] = None
                        row_data["return_1m"] = None
                        row_data["volatility_60d"] = None
                        row_data["beta"] = None

                result_rows.append(row_data)

            except Exception as e:
                logger.error(f"Error building factor data for {ticker}: {e}")
                continue

        # DataFrame으로 변환
        df = pd.DataFrame(result_rows)

        if df.empty:
            logger.warning("No factor data could be built")
            return df

        # 컬럼 순서 정렬
        required_cols = [
            "ticker",
            "per",
            "pbr",
            "ev_ebitda",
            "roe",
            "roa",
            "debt_ratio",
            "market_cap",
        ]
        optional_cols = ["return_12m", "return_1m", "volatility_60d", "beta"]

        available_cols = [c for c in required_cols if c in df.columns]
        available_cols.extend([c for c in optional_cols if c in df.columns])
        df = df[available_cols]

        logger.info(f"Built factor data for {len(df)} tickers: " f"columns={list(df.columns)}")

        return df
