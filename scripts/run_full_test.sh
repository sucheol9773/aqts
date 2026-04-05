#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════
# AQTS 전체 시장 OOS + 백테스트 일괄 실행 스크립트
#
# 사용법:
#   ./scripts/run_full_test.sh              # KR + US 전체
#   ./scripts/run_full_test.sh kr           # KR만
#   ./scripts/run_full_test.sh us           # US만
#   ./scripts/run_full_test.sh kr us --skip-backtest  # OOS만
#   ./scripts/run_full_test.sh kr us --skip-oos       # 백테스트만
#
# 결과 저장 위치:
#   results/full_test/YYYYMMDD_HHMMSS/
#     ├── kr/
#     │   ├── oos_summary.csv
#     │   ├── oos_detail.csv
#     │   └── backtest.csv
#     ├── us/
#     │   ├── oos_summary.csv
#     │   ├── oos_detail.csv
#     │   └── backtest.csv
#     └── summary.txt        # 전체 요약
# ══════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# ── 타임스탬프 & 결과 디렉토리 ──
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RESULT_DIR="results/full_test/${TIMESTAMP}"

# ── 인자 파싱 ──
MARKETS=()
SKIP_OOS=false
SKIP_BACKTEST=false

for arg in "$@"; do
    case "$arg" in
        kr|us) MARKETS+=("$arg") ;;
        --skip-oos) SKIP_OOS=true ;;
        --skip-backtest) SKIP_BACKTEST=true ;;
        --help|-h)
            echo "사용법: $0 [kr] [us] [--skip-oos] [--skip-backtest]"
            echo "  인자 없으면 KR + US 전체 실행"
            exit 0
            ;;
        *) echo "알 수 없는 인자: $arg"; exit 1 ;;
    esac
done

# 시장 미지정 시 전체
if [ ${#MARKETS[@]} -eq 0 ]; then
    MARKETS=("kr" "us")
fi

echo "═══════════════════════════════════════════════════"
echo "  AQTS 전체 시장 테스트"
echo "═══════════════════════════════════════════════════"
echo "  시장:     ${MARKETS[*]}"
echo "  OOS:      $( [ "$SKIP_OOS" = true ] && echo "건너뜀" || echo "실행" )"
echo "  백테스트: $( [ "$SKIP_BACKTEST" = true ] && echo "건너뜀" || echo "실행" )"
echo "  결과:     ${RESULT_DIR}/"
echo "  시작:     $(date '+%Y-%m-%d %H:%M:%S')"
echo "═══════════════════════════════════════════════════"
echo

# ── 결과 디렉토리 생성 ──
for market in "${MARKETS[@]}"; do
    mkdir -p "${RESULT_DIR}/${market}"
done

# ── 실행 함수 ──
run_oos() {
    local market=$1
    local out_dir="${RESULT_DIR}/${market}"
    echo "══ [${market^^}] OOS Walk-Forward 실행 ══"
    local start_time=$(date +%s)

    python scripts/run_walk_forward.py \
        --market "$market" \
        --all \
        --output "$out_dir" \
        2>&1 | tee "${out_dir}/oos_log.txt"

    local end_time=$(date +%s)
    local elapsed=$(( end_time - start_time ))
    echo "  ⏱  ${market^^} OOS 완료: ${elapsed}초"
    echo
}

run_backtest() {
    local market=$1
    local out_dir="${RESULT_DIR}/${market}"
    echo "══ [${market^^}] 백테스트 실행 ══"
    local start_time=$(date +%s)

    python scripts/run_backtest.py \
        --market "$market" \
        --all \
        --output "${out_dir}/backtest.csv" \
        2>&1 | tee "${out_dir}/backtest_log.txt"

    local end_time=$(date +%s)
    local elapsed=$(( end_time - start_time ))
    echo "  ⏱  ${market^^} 백테스트 완료: ${elapsed}초"
    echo
}

# ── 실행 ──
TOTAL_START=$(date +%s)

for market in "${MARKETS[@]}"; do
    if [ "$SKIP_OOS" = false ]; then
        run_oos "$market"
    fi

    if [ "$SKIP_BACKTEST" = false ]; then
        run_backtest "$market"
    fi
done

TOTAL_END=$(date +%s)
TOTAL_ELAPSED=$(( TOTAL_END - TOTAL_START ))

# ── 요약 생성 ──
SUMMARY_FILE="${RESULT_DIR}/summary.txt"
{
    echo "═══════════════════════════════════════════════════"
    echo "  AQTS 전체 테스트 요약"
    echo "═══════════════════════════════════════════════════"
    echo "  시각:   $(date '+%Y-%m-%d %H:%M:%S')"
    echo "  시장:   ${MARKETS[*]}"
    echo "  총 소요: ${TOTAL_ELAPSED}초 ($(( TOTAL_ELAPSED / 60 ))분 $(( TOTAL_ELAPSED % 60 ))초)"
    echo

    for market in "${MARKETS[@]}"; do
        echo "── ${market^^} 결과 ──"

        # OOS 요약
        oos_summary=$(find "${RESULT_DIR}/${market}" -name "oos_summary_*.csv" 2>/dev/null | head -1)
        if [ -n "$oos_summary" ]; then
            echo "  [OOS]"
            column -t -s, "$oos_summary" 2>/dev/null || cat "$oos_summary"
        else
            echo "  [OOS] 결과 없음"
        fi
        echo

        # 백테스트 요약
        bt_file="${RESULT_DIR}/${market}/backtest.csv"
        if [ -f "$bt_file" ]; then
            echo "  [백테스트]"
            column -t -s, "$bt_file" 2>/dev/null || cat "$bt_file"
        else
            echo "  [백테스트] 결과 없음"
        fi
        echo
    done
} > "$SUMMARY_FILE"

# 터미널에도 요약 출력
cat "$SUMMARY_FILE"

echo "═══════════════════════════════════════════════════"
echo "  전체 완료: ${TOTAL_ELAPSED}초 ($(( TOTAL_ELAPSED / 60 ))분 $(( TOTAL_ELAPSED % 60 ))초)"
echo "  결과 위치: ${RESULT_DIR}/"
echo "═══════════════════════════════════════════════════"
