#!/usr/bin/env python3
"""
ECOS API stat_code / item_code 탐색 스크립트

목적: GDP, 실업률, 경상수지의 정확한 stat_code와 item_code를 찾기 위해
      ECOS StatisticTableList + StatisticItemList + StatisticSearch API를
      순차적으로 호출한다.

사용법 (서버에서):
  docker compose exec scheduler python /app/scripts/discover_ecos_codes.py

또는:
  docker compose exec scheduler python -c "$(cat scripts/discover_ecos_codes.py)"
"""

import asyncio
import json
import os
import sys

import httpx

API_KEY = os.environ.get("ECOS_API_KEY", "")
BASE_URL = "https://ecos.bok.or.kr/api"

# ═══════════════════════════════════════
# 1. 후보 stat_code 목록 — 탐색 대상
# ═══════════════════════════════════════

# GDP 후보
GDP_CANDIDATES = [
    "200Y001",  # 국민소득 주요지표 (연간)
    "200Y002",  # 국내총생산에 대한 지출 (연간)
    "200Y003",  # 경제성장률 (연간)
    "200Y004",  # 국민소득 주요지표 (분기)
    "111Y002",  # (현재 설정, 실제로는 금융기관유동성)
    "111Y055",  # 국민소득통계 (분기)
    "111Y056",  # 국민소득통계
    "200Y008",  # 분기 GDP 후보
    "200Y011",  # 분기 GDP 후보
]

# 실업률 후보
UNEMPLOYMENT_CANDIDATES = [
    "901Y027",  # 경제활동인구 총괄 (실업률 % 포함 가능)
    "902Y014",  # 경제활동별 인구 (현재 설정)
    "901Y009",  # 소비자물가지수 (참고용)
    "920Y001",  # 고용동향
    "920Y014",  # 고용동향 상세
]

# 경상수지 후보
CURRENT_ACCOUNT_CANDIDATES = [
    "301Y013",  # 국제수지 (구)
    "301Y017",  # 국제수지 (신, BPM6)
    "721Y017",  # (현재 설정, 실패)
]


async def fetch_json(url: str) -> dict:
    """URL에서 JSON 응답을 가져온다."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


async def get_stat_item_list(stat_code: str) -> list[dict]:
    """StatisticItemList API로 특정 통계표의 항목 목록 조회."""
    url = f"{BASE_URL}/StatisticItemList/{API_KEY}/json/kr/1/100/{stat_code}"
    try:
        data = await fetch_json(url)
        if "RESULT" in data:
            err = data["RESULT"]
            return [{"error": f"{err.get('CODE')}: {err.get('MESSAGE')}"}]
        items = data.get("StatisticItemList", {}).get("row", [])
        return items
    except Exception as e:
        return [{"error": str(e)}]


async def test_stat_search(
    stat_code: str,
    item_code: str,
    cycle: str,
    start_date: str,
    end_date: str,
) -> dict:
    """StatisticSearch API로 실제 데이터 조회 테스트."""
    url = (
        f"{BASE_URL}/StatisticSearch/{API_KEY}/json/kr/1/5/"
        f"{stat_code}/{cycle}/{start_date}/{end_date}/{item_code}"
    )
    try:
        data = await fetch_json(url)
        if "RESULT" in data:
            err = data["RESULT"]
            return {
                "status": "error",
                "code": err.get("CODE"),
                "message": err.get("MESSAGE"),
            }
        rows = data.get("StatisticSearch", {}).get("row", [])
        if not rows:
            return {"status": "empty", "message": "no rows"}
        # 최신 5건의 TIME, DATA_VALUE, ITEM_NAME1 반환
        results = []
        for r in rows[-5:]:
            results.append(
                {
                    "TIME": r.get("TIME"),
                    "VALUE": r.get("DATA_VALUE"),
                    "ITEM_NAME": r.get("ITEM_NAME1", ""),
                    "UNIT_NAME": r.get("UNIT_NAME", ""),
                }
            )
        return {"status": "ok", "count": len(rows), "latest": results}
    except Exception as e:
        return {"status": "exception", "message": str(e)}


async def explore_candidates(
    label: str,
    candidates: list[str],
    target_keywords: list[str],
    test_params: list[dict],
):
    """후보 stat_code들을 순회하며 항목 목록 + 데이터 조회를 수행한다."""
    print(f"\n{'='*60}")
    print(f" {label}")
    print(f"{'='*60}")

    for stat_code in candidates:
        print(f"\n--- {stat_code} ---")

        # 1) StatisticItemList
        items = await get_stat_item_list(stat_code)
        if items and "error" in items[0]:
            print(f"  ItemList: {items[0]['error']}")
            continue

        # 키워드 매칭되는 항목 필터
        matched = []
        all_items = []
        for item in items:
            name = item.get("ITEM_NAME1", "") or item.get("ITEM_NAME", "")
            code = item.get("ITEM_CODE1", "") or item.get("ITEM_CODE", "")
            cycle = item.get("CYCLE", "")
            all_items.append(f"{code}={name}(cycle={cycle})")
            for kw in target_keywords:
                if kw in name:
                    matched.append(
                        {"code": code, "name": name, "cycle": cycle}
                    )

        print(f"  총 항목수: {len(items)}")
        if len(all_items) <= 20:
            for desc in all_items:
                print(f"    {desc}")
        else:
            for desc in all_items[:10]:
                print(f"    {desc}")
            print(f"    ... ({len(all_items) - 10}개 생략)")

        if matched:
            print(f"  ** 키워드 매칭 ({target_keywords}): **")
            for m in matched:
                print(f"    → item_code={m['code']}, name={m['name']}, cycle={m['cycle']}")

        # 2) 매칭된 항목에 대해 실제 조회 테스트
        for params in test_params:
            item_code = params.get("item_code")
            if item_code == "__matched__":
                # 매칭된 항목 코드를 사용
                for m in matched[:3]:  # 최대 3개만
                    result = await test_stat_search(
                        stat_code,
                        m["code"],
                        params["cycle"],
                        params["start"],
                        params["end"],
                    )
                    print(
                        f"  Search({stat_code}/{m['code']}/{params['cycle']}"
                        f"/{params['start']}~{params['end']}): {json.dumps(result, ensure_ascii=False)}"
                    )
            else:
                result = await test_stat_search(
                    stat_code,
                    item_code,
                    params["cycle"],
                    params["start"],
                    params["end"],
                )
                print(
                    f"  Search({stat_code}/{item_code}/{params['cycle']}"
                    f"/{params['start']}~{params['end']}): {json.dumps(result, ensure_ascii=False)}"
                )


async def main():
    if not API_KEY:
        print("ERROR: ECOS_API_KEY 환경변수가 설정되지 않았습니다.")
        sys.exit(1)

    print("═══════════════════════════════════════════════")
    print(" ECOS stat_code / item_code 탐색")
    print(f" API_KEY 길이: {len(API_KEY)}")
    print("═══════════════════════════════════════════════")

    # ── GDP 탐색 ──
    await explore_candidates(
        label="GDP (국내총생산) 탐색",
        candidates=GDP_CANDIDATES,
        target_keywords=["국내총생산", "GDP", "성장률", "실질"],
        test_params=[
            # 분기 데이터 (Q) — YYYYMM 형식
            {"item_code": "__matched__", "cycle": "Q", "start": "202301", "end": "202512"},
            # 연간 데이터 (A) — YYYY 형식
            {"item_code": "__matched__", "cycle": "A", "start": "2022", "end": "2025"},
        ],
    )

    # ── 실업률 탐색 ──
    await explore_candidates(
        label="실업률 (Unemployment Rate %) 탐색",
        candidates=UNEMPLOYMENT_CANDIDATES,
        target_keywords=["실업률", "실업", "고용률", "경제활동참가율"],
        test_params=[
            # 월간 데이터
            {"item_code": "__matched__", "cycle": "M", "start": "202401", "end": "202604"},
        ],
    )

    # ── 경상수지 탐색 ──
    await explore_candidates(
        label="경상수지 (Current Account) 탐색",
        candidates=CURRENT_ACCOUNT_CANDIDATES,
        target_keywords=["경상수지", "경상", "수지"],
        test_params=[
            # 월간 데이터
            {"item_code": "__matched__", "cycle": "M", "start": "202401", "end": "202604"},
        ],
    )

    print("\n\n═══════════════════════════════════════════════")
    print(" 탐색 완료")
    print("═══════════════════════════════════════════════")
    print("\n위 결과에서 status='ok'인 조합이 실제 사용 가능한 코드입니다.")
    print("ECOS_SERIES_MAP 수정 시 해당 stat_code + item_code를 사용하세요.")


if __name__ == "__main__":
    asyncio.run(main())
