#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# ECOS API 수집 검증 스크립트
#
# 배포 후 ECOS 날짜 형식 + 응답 파싱 수정이 실제로 동작하는지 확인.
#
# 사용법:
#   docker compose exec scheduler bash /app/scripts/verify_ecos.sh
#   또는 서버에서:
#   docker compose exec scheduler python -c "$(cat scripts/verify_ecos.py)"
#
# 이 스크립트는 python one-liner로 실행합니다.
# ═══════════════════════════════════════════════════════════════════════

set -euo pipefail

echo "═══════════════════════════════════════════════"
echo " ECOS + 환율 DB 영속화 검증"
echo " $(TZ=Asia/Seoul date '+%Y-%m-%d %H:%M:%S KST')"
echo "═══════════════════════════════════════════════"

echo ""
echo "━━━ 1. ECOS 수집 테스트 ━━━"
docker compose exec -T scheduler python -c "
import asyncio, os, sys

async def test_ecos():
    from core.data_collector.economic_collector import ECOSCollector, EconomicCollectorService

    # ECOS API 키 확인
    api_key = os.environ.get('ECOS_API_KEY', '')
    if not api_key:
        print('  ✗ ECOS_API_KEY 미설정')
        return False

    print(f'  ✓ ECOS_API_KEY 로드됨 (길이: {len(api_key)})')

    # ECOSCollector 개별 수집 테스트
    collector = ECOSCollector()
    result = await collector.collect_all()
    print(f'  → ECOS 수집 결과: {len(result)}건')

    if len(result) > 0:
        for ind in result:
            print(f'    ✓ {ind.indicator_name} ({ind.indicator_code}): {ind.value} [{ind.time}]')
        return True
    else:
        print('  ✗ ECOS 수집 0건 — API 응답 확인 필요')
        return False

ok = asyncio.run(test_ecos())
sys.exit(0 if ok else 1)
"
ECOS_OK=$?

echo ""
echo "━━━ 2. FRED 수집 테스트 ━━━"
docker compose exec -T scheduler python -c "
import asyncio, sys

async def test_fred():
    from core.data_collector.economic_collector import FREDCollector

    collector = FREDCollector()
    result = await collector.collect_all()
    print(f'  → FRED 수집 결과: {len(result)}건')

    if len(result) > 0:
        for ind in result:
            print(f'    ✓ {ind.indicator_name}: {ind.value}')
        return True
    else:
        print('  ✗ FRED 수집 0건')
        return False

ok = asyncio.run(test_fred())
sys.exit(0 if ok else 1)
"
FRED_OK=$?

echo ""
echo "━━━ 3. 통합 수집 + DB 저장 테스트 ━━━"
docker compose exec -T scheduler python -c "
import asyncio, sys

async def test_store():
    from core.data_collector.economic_collector import EconomicCollectorService

    svc = EconomicCollectorService()
    result = await svc.collect_and_store()
    print(f'  → 수집 결과: FRED={result[\"fred_count\"]}, ECOS={result[\"ecos_count\"]}, Total={result[\"total\"]}')

    if result['total'] > 0:
        print(f'  ✓ DB 저장 성공 ({result[\"total\"]}건)')
        return True
    else:
        print('  ✗ 수집 0건 — 저장 대상 없음')
        return False

ok = asyncio.run(test_store())
sys.exit(0 if ok else 1)
"
STORE_OK=$?

echo ""
echo "━━━ 4. DB 검증 ━━━"
docker compose exec -T postgres psql -U aqts_user -d aqts -c "
SELECT indicator_name, indicator_code, value, time, source
FROM economic_indicators
ORDER BY time DESC
LIMIT 15;
" </dev/null

echo ""
echo "━━━ 5. 환율 DB 영속화 검증 ━━━"
docker compose exec -T scheduler python -c "
import asyncio, sys

async def test_fx():
    from core.portfolio_manager.exchange_rate import ExchangeRateManager

    mgr = ExchangeRateManager()
    rate = await mgr.get_current_rate('USD/KRW', persist=True)
    print(f'  → 환율: {rate.rate} (source={rate.source})')
    print(f'  ✓ persist=True 호출 완료')
    return True

ok = asyncio.run(test_fx())
sys.exit(0 if ok else 1)
"
FX_OK=$?

echo ""
docker compose exec -T postgres psql -U aqts_user -d aqts -c "
SELECT time, currency_pair, rate, source
FROM exchange_rates
ORDER BY time DESC
LIMIT 5;
" </dev/null

echo ""
echo "═══════════════════════════════════════════════"
echo " 결과 요약"
echo "═══════════════════════════════════════════════"
TOTAL_OK=0
TOTAL_FAIL=0

for r in $ECOS_OK $FRED_OK $STORE_OK $FX_OK; do
    if [ "$r" -eq 0 ]; then
        TOTAL_OK=$((TOTAL_OK + 1))
    else
        TOTAL_FAIL=$((TOTAL_FAIL + 1))
    fi
done

echo "  PASS: $TOTAL_OK  FAIL: $TOTAL_FAIL"
if [ "$TOTAL_FAIL" -eq 0 ]; then
    echo "  ✓ 전체 통과"
else
    echo "  ✗ 일부 실패 — 위 로그 확인"
fi
