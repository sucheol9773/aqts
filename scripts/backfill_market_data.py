#!/usr/bin/env python3
"""
AQTS 과거 시장 데이터 백필 스크립트

사용법:
    # 기본 (KOSPI 50 대형주, 2년치)
    python scripts/backfill_market_data.py

    # 기간 지정
    python scripts/backfill_market_data.py --start 2020-01-01 --end 2026-04-04

    # 특정 종목만
    python scripts/backfill_market_data.py --tickers 005930 000660 035420

    # 유니버스 + 시세 모두 초기화
    python scripts/backfill_market_data.py --init-universe

필요 패키지:
    pip install yfinance psycopg2-binary python-dotenv
"""

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import yfinance as yf
    import psycopg2
    from psycopg2.extras import execute_values
    from dotenv import load_dotenv
except ImportError as e:
    print(f"필요 패키지 설치: pip install yfinance psycopg2-binary python-dotenv")
    print(f"Missing: {e}")
    sys.exit(1)


# ══════════════════════════════════════
# 기본 KOSPI/KOSDAQ 대형주 유니버스
# ══════════════════════════════════════
DEFAULT_UNIVERSE = [
    # ticker, name, market, sector
    ("005930", "삼성전자", "KRX", "IT"),
    ("000660", "SK하이닉스", "KRX", "IT"),
    ("373220", "LG에너지솔루션", "KRX", "Materials"),
    ("207940", "삼성바이오로직스", "KRX", "Healthcare"),
    ("005380", "현대자동차", "KRX", "Consumer"),
    ("000270", "기아", "KRX", "Consumer"),
    ("006400", "삼성SDI", "KRX", "Materials"),
    ("051910", "LG화학", "KRX", "Materials"),
    ("035420", "NAVER", "KRX", "IT"),
    ("035720", "카카오", "KRX", "IT"),
    ("005490", "POSCO홀딩스", "KRX", "Materials"),
    ("068270", "셀트리온", "KRX", "Healthcare"),
    ("105560", "KB금융", "KRX", "Finance"),
    ("055550", "신한지주", "KRX", "Finance"),
    ("096770", "SK이노베이션", "KRX", "Energy"),
    ("028260", "삼성물산", "KRX", "Industrials"),
    ("003670", "포스코퓨처엠", "KRX", "Materials"),
    ("247540", "에코프로비엠", "KRX", "Materials"),
    ("086790", "하나금융지주", "KRX", "Finance"),
    ("012330", "현대모비스", "KRX", "Consumer"),
    ("066570", "LG전자", "KRX", "IT"),
    ("003550", "LG", "KRX", "Industrials"),
    ("034730", "SK", "KRX", "Industrials"),
    ("015760", "한국전력", "KRX", "Utilities"),
    ("032830", "삼성생명", "KRX", "Finance"),
    ("011200", "HMM", "KRX", "Industrials"),
    ("034020", "두산에너빌리티", "KRX", "Industrials"),
    ("010130", "고려아연", "KRX", "Materials"),
    ("009150", "삼성전기", "KRX", "IT"),
    ("018260", "삼성에스디에스", "KRX", "IT"),
    ("033780", "KT&G", "KRX", "Consumer"),
    ("017670", "SK텔레콤", "KRX", "Telecom"),
    ("030200", "KT", "KRX", "Telecom"),
    ("316140", "우리금융지주", "KRX", "Finance"),
    ("009540", "한국조선해양", "KRX", "Industrials"),
    ("010950", "S-Oil", "KRX", "Energy"),
    ("138040", "메리츠금융지주", "KRX", "Finance"),
    ("361610", "SK아이이테크놀로지", "KRX", "Materials"),
    ("011170", "롯데케미칼", "KRX", "Materials"),
    ("000810", "삼성화재", "KRX", "Finance"),
    ("036570", "엔씨소프트", "KRX", "IT"),
    ("251270", "넷마블", "KRX", "IT"),
    ("259960", "크래프톤", "KRX", "IT"),
    ("352820", "하이브", "KRX", "Consumer"),
    ("003490", "대한항공", "KRX", "Industrials"),
    ("047050", "포스코인터내셔널", "KRX", "Industrials"),
    ("000720", "현대건설", "KRX", "Industrials"),
    ("034220", "LG디스플레이", "KRX", "IT"),
    ("090430", "아모레퍼시픽", "KRX", "Consumer"),
    ("326030", "SK바이오팜", "KRX", "Healthcare"),
]

# ══════════════════════════════════════
# KOSDAQ 대형주 유니버스
# ══════════════════════════════════════
KOSDAQ_UNIVERSE = [
    # ticker, name, market, sector
    ("247540", "에코프로비엠", "KRX", "Materials"),  # KOSPI 이전 종목이면 중복 UPSERT
    ("383220", "에코프로", "KRX", "Materials"),
    ("028300", "HLB", "KRX", "Healthcare"),
    ("403870", "HPSP", "KRX", "IT"),
    ("086520", "에코프로에이치엔", "KRX", "Materials"),
    ("196170", "알테오젠", "KRX", "Healthcare"),
    ("005290", "동진쎄미켐", "KRX", "Materials"),
    ("145020", "휴젤", "KRX", "Healthcare"),
    ("112040", "위메이드", "KRX", "IT"),
    ("293490", "카카오게임즈", "KRX", "IT"),
    ("263750", "펄어비스", "KRX", "IT"),
    ("041510", "에스엠", "KRX", "Consumer"),
    ("035900", "JYP Ent.", "KRX", "Consumer"),
    ("122870", "와이지엔터테인먼트", "KRX", "Consumer"),
    ("328130", "루닛", "KRX", "Healthcare"),
    ("039030", "이오테크닉스", "KRX", "IT"),
    ("067310", "하나마이크론", "KRX", "IT"),
    ("095340", "ISC", "KRX", "IT"),
    ("336260", "두산테스나", "KRX", "IT"),
    ("357780", "솔브레인", "KRX", "Materials"),
    ("237690", "에스티팜", "KRX", "Healthcare"),
    ("214150", "클래시스", "KRX", "Healthcare"),
    ("253450", "스튜디오드래곤", "KRX", "Consumer"),
    ("060310", "3S", "KRX", "IT"),
    ("240810", "원익IPS", "KRX", "IT"),
    ("131970", "테스나", "KRX", "IT"),
    ("058470", "리노공업", "KRX", "IT"),
    ("042700", "한미반도체", "KRX", "IT"),
    ("078600", "대주전자재료", "KRX", "Materials"),
    ("140860", "파크시스템스", "KRX", "IT"),
]

# ══════════════════════════════════════
# 미국 대형주 + ETF 유니버스
# ══════════════════════════════════════
US_UNIVERSE = [
    # ticker, name, market, sector
    # — 대형 기술주 —
    ("AAPL", "Apple", "NASDAQ", "IT"),
    ("MSFT", "Microsoft", "NASDAQ", "IT"),
    ("GOOGL", "Alphabet", "NASDAQ", "IT"),
    ("AMZN", "Amazon", "NASDAQ", "IT"),
    ("NVDA", "NVIDIA", "NASDAQ", "IT"),
    ("META", "Meta Platforms", "NASDAQ", "IT"),
    ("TSLA", "Tesla", "NASDAQ", "Consumer"),
    ("TSM", "TSMC", "NYSE", "IT"),
    ("AVGO", "Broadcom", "NASDAQ", "IT"),
    ("AMD", "AMD", "NASDAQ", "IT"),
    # — 주요 산업 —
    ("JPM", "JPMorgan Chase", "NYSE", "Finance"),
    ("V", "Visa", "NYSE", "Finance"),
    ("UNH", "UnitedHealth", "NYSE", "Healthcare"),
    ("JNJ", "Johnson & Johnson", "NYSE", "Healthcare"),
    ("WMT", "Walmart", "NYSE", "Consumer"),
    ("PG", "Procter & Gamble", "NYSE", "Consumer"),
    ("XOM", "ExxonMobil", "NYSE", "Energy"),
    ("HD", "Home Depot", "NYSE", "Consumer"),
    ("BAC", "Bank of America", "NYSE", "Finance"),
    ("KO", "Coca-Cola", "NYSE", "Consumer"),
    # — 주요 ETF —
    ("SPY", "SPDR S&P 500 ETF", "NYSE", "ETF"),
    ("QQQ", "Invesco NASDAQ 100 ETF", "NASDAQ", "ETF"),
    ("IWM", "iShares Russell 2000 ETF", "NYSE", "ETF"),
    ("EEM", "iShares MSCI Emerging Markets", "NYSE", "ETF"),
    ("GLD", "SPDR Gold Shares", "NYSE", "ETF"),
    ("TLT", "iShares 20+ Treasury Bond", "NASDAQ", "ETF"),
    ("VIX", "iPath Series B S&P 500 VIX", "NYSE", "ETF"),
    # — 국내 ETF (KRX) —
    ("069500", "KODEX 200", "KRX", "ETF"),
    ("114800", "KODEX 인버스", "KRX", "ETF"),
    ("122630", "KODEX 레버리지", "KRX", "ETF"),
    ("252670", "KODEX 200선물인버스2X", "KRX", "ETF"),
    ("371460", "TIGER 차이나전기차SOLACTIVE", "KRX", "ETF"),
    ("133690", "TIGER 미국나스닥100", "KRX", "ETF"),
    ("360750", "TIGER 미국S&P500", "KRX", "ETF"),
    ("261240", "TIGER 미국테크TOP10 INDXX", "KRX", "ETF"),
    ("381180", "TIGER 미국필라델피아반도체나스닥", "KRX", "ETF"),
]


def ticker_to_yahoo(ticker: str, market: str) -> str:
    """종목코드를 Yahoo Finance 심볼로 변환"""
    if market == "KRX":
        # 숫자로만 된 코드 → KOSPI(.KS) 또는 KOSDAQ(.KQ)
        # yfinance에서는 .KS로 대부분 조회 가능 (KOSDAQ도 .KS로 조회됨)
        return f"{ticker}.KS"
    else:
        # 미국 종목: 심볼 그대로 사용
        return ticker


def get_db_connection():
    """PostgreSQL 연결"""
    load_dotenv()
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5432")),
        dbname=os.getenv("DB_NAME", "aqts"),
        user=os.getenv("DB_USER", "aqts_user"),
        password=os.getenv("DB_PASSWORD"),
    )


def init_universe(conn, universe: list[tuple]) -> int:
    """유니버스 테이블 초기화"""
    print(f"\n{'='*60}")
    print(f" 유니버스 초기화: {len(universe)}개 종목")
    print(f"{'='*60}")

    cur = conn.cursor()
    inserted = 0

    for ticker, name, market, sector in universe:
        try:
            country = "KR" if market == "KRX" else "US"
            asset_type = "ETF" if sector == "ETF" else "STOCK"
            cur.execute(
                """
                INSERT INTO universe (ticker, name, market, country, asset_type, sector, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, TRUE)
                ON CONFLICT (ticker, market) DO UPDATE SET
                    name = EXCLUDED.name,
                    sector = EXCLUDED.sector,
                    asset_type = EXCLUDED.asset_type,
                    is_active = TRUE,
                    updated_at = NOW()
                """,
                (ticker, name, market, country, asset_type, sector),
            )
            inserted += 1
        except Exception as e:
            print(f"  ✗ {ticker} {name}: {e}")

    conn.commit()
    cur.close()
    print(f"  ✓ {inserted}개 종목 등록 완료")
    return inserted


def backfill_ticker(conn, ticker: str, name: str, market: str, start: str, end: str) -> int:
    """단일 종목 과거 데이터 수집"""
    yahoo_ticker = ticker_to_yahoo(ticker, market)

    try:
        data = yf.download(
            yahoo_ticker,
            start=start,
            end=end,
            interval="1d",
            progress=False,
            auto_adjust=True,
        )
    except Exception as e:
        print(f"  ✗ {ticker} ({name}): yfinance 에러 - {e}")
        return 0

    if data.empty:
        print(f"  ✗ {ticker} ({name}): 데이터 없음")
        return 0

    # MultiIndex 컬럼 처리 (yfinance 0.2.x+)
    if hasattr(data.columns, "droplevel"):
        try:
            data.columns = data.columns.droplevel("Ticker")
        except (KeyError, AttributeError):
            pass

    records = []
    for idx, row in data.iterrows():
        # pandas Timestamp → Python datetime (UTC)
        dt = idx.to_pydatetime()
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        # NaN 체크
        if any(
            v != v for v in [row["Open"], row["High"], row["Low"], row["Close"]]
        ):
            continue

        records.append(
            (
                dt,
                ticker,
                market,
                float(row["Open"]),
                float(row["High"]),
                float(row["Low"]),
                float(row["Close"]),
                int(row["Volume"]) if row["Volume"] == row["Volume"] else 0,
                "1d",
            )
        )

    if not records:
        print(f"  ✗ {ticker} ({name}): 유효 레코드 없음")
        return 0

    # Batch UPSERT
    cur = conn.cursor()
    execute_values(
        cur,
        """
        INSERT INTO market_ohlcv (time, ticker, market, open, high, low, close, volume, interval)
        VALUES %s
        ON CONFLICT (time, ticker, interval) DO UPDATE SET
            open = EXCLUDED.open,
            high = EXCLUDED.high,
            low = EXCLUDED.low,
            close = EXCLUDED.close,
            volume = EXCLUDED.volume
        """,
        records,
        page_size=500,
    )
    conn.commit()
    cur.close()

    date_range = f"{records[0][0].strftime('%Y-%m-%d')} ~ {records[-1][0].strftime('%Y-%m-%d')}"
    print(f"  ✓ {ticker} ({name}): {len(records)}건 ({date_range})")
    return len(records)


def get_universe_by_group(groups: list[str]) -> list[tuple]:
    """그룹 이름으로 유니버스 목록 반환"""
    all_groups = {
        "kospi": DEFAULT_UNIVERSE,
        "kosdaq": KOSDAQ_UNIVERSE,
        "us": US_UNIVERSE,
    }

    if "all" in groups:
        groups = ["kospi", "kosdaq", "us"]

    result = []
    seen = set()
    for g in groups:
        for item in all_groups.get(g, []):
            key = (item[0], item[2])  # (ticker, market)
            if key not in seen:
                result.append(item)
                seen.add(key)
    return result


def main():
    parser = argparse.ArgumentParser(description="AQTS 과거 시장 데이터 백필")
    parser.add_argument("--start", default="2024-01-01", help="시작일 (YYYY-MM-DD)")
    parser.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"), help="종료일")
    parser.add_argument("--tickers", nargs="*", help="특정 종목코드 (미지정 시 그룹 기반)")
    parser.add_argument(
        "--group",
        nargs="*",
        default=["all"],
        help="종목 그룹: kospi, kosdaq, us, all (기본: all)",
    )
    parser.add_argument("--init-universe", action="store_true", help="유니버스 테이블 초기화")
    args = parser.parse_args()

    # 유니버스 결정
    if args.tickers:
        targets = [(t, t, "KRX", "") for t in args.tickers]
    else:
        targets = get_universe_by_group(args.group)

    print(f"{'='*60}")
    print(f" AQTS 과거 데이터 백필")
    print(f" 기간: {args.start} ~ {args.end}")
    print(f" 그룹: {', '.join(args.group)}")
    print(f" 종목 수: {len(targets)}개")
    print(f"{'='*60}")

    conn = get_db_connection()

    # 유니버스 초기화
    if args.init_universe:
        init_universe(conn, targets)

    print(f"\n 수집 시작: {len(targets)}개 종목")
    print(f"{'-'*60}")

    total_records = 0
    success = 0
    fail = 0

    for ticker, name, market, sector in targets:
        count = backfill_ticker(conn, ticker, name, market, args.start, args.end)
        total_records += count
        if count > 0:
            success += 1
        else:
            fail += 1

    conn.close()

    print(f"\n{'='*60}")
    print(f" 백필 완료")
    print(f" 성공: {success}개 종목 / 실패: {fail}개 종목")
    print(f" 총 레코드: {total_records:,}건")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
