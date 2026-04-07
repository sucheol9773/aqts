"""
동적 앙상블 API 라우터

동적 앙상블 시그널 조회/실행 엔드포인트를 제공합니다.

엔드포인트:
  GET  /api/ensemble/cached          - Redis 캐시에서 최신 앙상블 결과 조회
  GET  /api/ensemble/cached/{ticker} - 특정 종목의 캐시된 앙상블 결과 조회
  POST /api/ensemble/run             - 단일 종목 동적 앙상블 실시간 실행
  POST /api/ensemble/batch           - 유니버스 전체 동적 앙상블 배치 실행
"""

import json
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.middleware.rbac import require_operator, require_viewer
from api.schemas.common import APIResponse
from api.schemas.ensemble import (
    EnsembleBatchResponse,
    EnsembleCachedResponse,
    EnsembleCacheSummary,
    EnsembleSignalResponse,
    EnsembleWeights,
)
from config.logging import logger
from core.strategy_ensemble.runner import DynamicEnsembleRunner
from db.database import RedisManager, get_db_session

router = APIRouter()


# ══════════════════════════════════════
# 캐시 조회 엔드포인트
# ══════════════════════════════════════


@router.get("/cached", response_model=APIResponse[EnsembleCacheSummary])
async def get_cached_summary(
    current_user=Depends(require_viewer),
):
    """
    캐시된 앙상블 결과 요약 조회

    스케줄러의 MARKET_OPEN 이벤트에서 생성된
    최신 앙상블 결과 요약을 Redis에서 조회합니다.
    """
    try:
        redis = RedisManager.get_client()
        raw = await redis.get("ensemble:latest:_summary")

        if raw is None:
            return APIResponse(
                success=True,
                data=EnsembleCacheSummary(),
                message="캐시된 앙상블 결과가 없습니다",
            )

        summary = json.loads(raw)
        return APIResponse(
            success=True,
            data=EnsembleCacheSummary(**summary),
        )

    except RuntimeError as e:
        # Redis 미연결
        logger.warning(f"[EnsembleAPI] Redis 미연결: {e}")
        return APIResponse(
            success=False,
            message="Redis가 연결되지 않았습니다",
        )
    except Exception as e:
        logger.error(f"[EnsembleAPI] 캐시 요약 조회 실패: {e}")
        return APIResponse(success=False, message=str(e))


@router.get("/cached/{ticker}", response_model=APIResponse[EnsembleCachedResponse])
async def get_cached_ticker(
    ticker: str,
    current_user=Depends(require_viewer),
):
    """
    특정 종목의 캐시된 앙상블 결과 조회

    Redis에서 스케줄러가 캐시한 종목별 앙상블 결과를 조회합니다.
    """
    try:
        redis = RedisManager.get_client()
        key = f"ensemble:latest:{ticker.upper()}"
        raw = await redis.get(key)

        if raw is None:
            return APIResponse(
                success=False,
                message=f"{ticker}: 캐시된 앙상블 결과가 없습니다",
            )

        data = json.loads(raw)

        # 에러 결과인 경우
        if "error" in data:
            return APIResponse(
                success=False,
                message=f"{ticker}: {data['error']}",
            )

        return APIResponse(
            success=True,
            data=EnsembleCachedResponse(
                ticker=data.get("ticker", ticker),
                ensemble_signal=data["ensemble_signal"],
                regime=data["regime"],
                weights=data.get("weights"),
                adx=data.get("adx"),
                vol_percentile=data.get("vol_percentile"),
                vol_scalar=data.get("vol_scalar"),
                ohlcv_days=data.get("ohlcv_days"),
                cached=True,
            ),
        )

    except RuntimeError as e:
        logger.warning(f"[EnsembleAPI] Redis 미연결: {e}")
        return APIResponse(success=False, message="Redis가 연결되지 않았습니다")
    except Exception as e:
        logger.error(f"[EnsembleAPI] 캐시 조회 실패 ({ticker}): {e}")
        return APIResponse(success=False, message=str(e))


# ══════════════════════════════════════
# 실시간 실행 엔드포인트
# ══════════════════════════════════════


@router.post("/run", response_model=APIResponse[EnsembleSignalResponse])
async def run_single_ensemble(
    ticker: str = Query(..., description="종목코드 (예: 005930, AAPL)"),
    country: str = Query(default="KR", description="국가 코드 (KR/US)"),
    lookback_days: int = Query(default=300, ge=200, le=500, description="OHLCV 조회 일수"),
    current_user=Depends(require_operator),
    db: AsyncSession = Depends(get_db_session),
):
    """
    단일 종목 동적 앙상블 실시간 실행

    DB에서 OHLCV를 조회하여 실시간으로 동적 앙상블 시그널을 계산합니다.
    캐시와 무관하게 항상 최신 데이터로 계산합니다.
    """
    try:
        runner = DynamicEnsembleRunner(db_session=db)
        result = await runner.run(
            ticker=ticker.strip(),
            country=country.upper(),
            lookback_days=lookback_days,
        )

        summary = result.to_summary_dict()

        return APIResponse(
            success=True,
            data=EnsembleSignalResponse(
                ticker=summary["ticker"],
                country=summary["country"],
                ensemble_signal=summary["ensemble_signal"],
                regime=summary["regime"],
                weights=EnsembleWeights(**summary["weights"]),
                adx=summary["adx"],
                vol_percentile=summary["vol_percentile"],
                vol_scalar=summary["vol_scalar"],
                ohlcv_days=summary["ohlcv_days"],
            ),
        )

    except ValueError as e:
        logger.warning(f"[EnsembleAPI] {ticker} 실행 실패 (데이터): {e}")
        return APIResponse(success=False, message=str(e))
    except Exception as e:
        logger.error(f"[EnsembleAPI] {ticker} 실행 실패: {e}")
        return APIResponse(success=False, message=f"앙상블 실행 실패: {str(e)}")


@router.post("/batch", response_model=APIResponse[EnsembleBatchResponse])
async def run_batch_ensemble(
    country: Optional[str] = Query(
        default=None,
        description="국가 필터 (KR/US, None이면 전체)",
    ),
    lookback_days: int = Query(default=300, ge=200, le=500, description="OHLCV 조회 일수"),
    cache_results: bool = Query(default=True, description="결과를 Redis에 캐시할지 여부"),
    current_user=Depends(require_operator),
    db: AsyncSession = Depends(get_db_session),
):
    """
    유니버스 전체 동적 앙상블 배치 실행

    활성 유니버스의 모든 종목에 대해 동적 앙상블을 실행합니다.
    스케줄러의 MARKET_OPEN과 동일한 로직이지만 수동 트리거입니다.
    """
    try:
        from core.scheduler_handlers import (
            _cache_ensemble_results,
            _load_universe_grouped,
        )

        tickers_by_country = await _load_universe_grouped(db)

        # 국가 필터 적용
        if country:
            country = country.upper()
            tickers_by_country = {k: v for k, v in tickers_by_country.items() if k == country}

        total_tickers = sum(len(tks) for tks in tickers_by_country.values())

        if total_tickers == 0:
            return APIResponse(
                success=True,
                data=EnsembleBatchResponse(total_tickers=0, succeeded=0, failed=0),
                message="활성 종목이 없습니다",
            )

        logger.info(f"[EnsembleAPI] 배치 실행 시작: {total_tickers}개 종목")

        results: dict[str, EnsembleSignalResponse] = {}
        errors: dict[str, str] = {}
        cache_data: dict[str, dict] = {}
        succeeded = 0
        failed = 0

        for ctry, tickers in tickers_by_country.items():
            for ticker_info in tickers:
                tk = ticker_info["ticker"]
                try:
                    runner = DynamicEnsembleRunner(db_session=db)
                    runner_result = await runner.run(
                        ticker=tk,
                        country=ctry,
                        lookback_days=lookback_days,
                    )
                    summary = runner_result.to_summary_dict()

                    results[tk] = EnsembleSignalResponse(
                        ticker=summary["ticker"],
                        country=summary["country"],
                        ensemble_signal=summary["ensemble_signal"],
                        regime=summary["regime"],
                        weights=EnsembleWeights(**summary["weights"]),
                        adx=summary["adx"],
                        vol_percentile=summary["vol_percentile"],
                        vol_scalar=summary["vol_scalar"],
                        ohlcv_days=summary["ohlcv_days"],
                    )
                    cache_data[tk] = summary
                    succeeded += 1

                except Exception as e:
                    failed += 1
                    errors[tk] = str(e)
                    logger.warning(f"[EnsembleAPI] {tk} 배치 실패: {e}")

        # Redis 캐시
        if cache_results and cache_data:
            await _cache_ensemble_results(cache_data)

        logger.info(f"[EnsembleAPI] 배치 완료: " f"{succeeded}/{total_tickers} 성공, {failed} 실패")

        return APIResponse(
            success=True,
            data=EnsembleBatchResponse(
                total_tickers=total_tickers,
                succeeded=succeeded,
                failed=failed,
                results=results,
                errors=errors,
            ),
            message=f"{total_tickers}개 종목 앙상블 분석 완료",
        )

    except Exception as e:
        logger.error(f"[EnsembleAPI] 배치 실행 실패: {e}")
        return APIResponse(success=False, message=f"배치 실행 실패: {str(e)}")
