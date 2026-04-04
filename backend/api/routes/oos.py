"""
OOS 검증 API 라우터

4개 엔드포인트:
- POST /api/system/oos/run      → OOS 실행 요청 (비동기 job)
- GET  /api/system/oos/{run_id} → 실행 결과 조회
- GET  /api/system/oos/latest   → 최근 실행 결과
- GET  /api/system/oos/gate-status → 게이트 상태 요약

실행은 run_in_executor로 별도 스레드에서 처리.
run_id를 즉시 반환 (polling 패턴).
"""

import asyncio
from typing import Optional

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, Body
from starlette.requests import Request

from api.middleware.auth import get_current_user
from api.schemas.common import APIResponse
from config.logging import logger
from core.oos.models import OOSRunRequest, OOSStatus
from core.oos.job_manager import OOSJobManager

router = APIRouter()


def _generate_sample_data(
    tickers: list[str],
    n_days: int = 756,  # ~3 years
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    샘플 시그널/가격 데이터 생성

    실제로는 DataCollector에서 로드하지만,
    MVP에서는 합성 데이터로 시연.
    """
    np.random.seed(42)
    dates = pd.bdate_range(end=pd.Timestamp.now(), periods=n_days)

    # 가격: 랜덤 워크 기반
    prices_data = {}
    for ticker in tickers:
        base = 50000 + np.random.randint(-10000, 10000)
        returns = np.random.normal(0.0003, 0.015, n_days)
        prices_data[ticker] = base * np.cumprod(1 + returns)

    prices = pd.DataFrame(prices_data, index=dates)

    # 시그널: -1 ~ +1 (약간의 트렌드 포함)
    signals_data = {}
    for ticker in tickers:
        price_returns = prices[ticker].pct_change().fillna(0)
        # 모멘텀 기반 시그널 + 노이즈
        momentum = price_returns.rolling(20).mean().fillna(0) * 100
        noise = np.random.normal(0, 0.2, n_days)
        raw_signal = momentum + noise
        signals_data[ticker] = np.clip(raw_signal, -1, 1)

    signals = pd.DataFrame(signals_data, index=dates)

    return signals, prices


@router.post("/run", response_model=APIResponse[dict])
async def create_oos_run(
    request: Request,
    run_request: OOSRunRequest = Body(...),
    current_user: str = Depends(get_current_user),
):
    """
    OOS 검증 실행 요청

    Walk-forward OOS를 비동기로 실행하고 run_id를 즉시 반환합니다.
    동일 파라미터의 진행 중인 실행이 있으면 기존 run_id를 반환합니다.
    """
    try:
        manager = OOSJobManager()

        # idempotency 체크
        existing = manager.find_existing_run(
            strategy_version=run_request.strategy_version,
            train_months=run_request.train_months,
            test_months=run_request.test_months,
            tickers=run_request.tickers,
        )
        if existing:
            return APIResponse(
                success=True,
                data={
                    "run_id": existing.run_id,
                    "status": existing.status.value,
                    "message": "Existing run found with same parameters",
                },
            )

        # 데이터 생성 (MVP: 합성 데이터, 실제로는 DataCollector)
        # 필요 기간: train + test * 예상 윈도우 수
        n_days = (run_request.train_months + run_request.test_months * 4) * 21
        signals, prices = _generate_sample_data(
            tickers=run_request.tickers,
            n_days=max(n_days, 504),  # 최소 2년
        )

        # run_in_executor로 비동기 실행
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            manager.submit_run,
            run_request.strategy_version,
            run_request.train_months,
            run_request.test_months,
            run_request.tickers,
            signals,
            prices,
            None,  # market_data
        )

        logger.info(
            f"OOS run created: {result.run_id}, "
            f"status={result.status.value}, user={current_user}"
        )

        return APIResponse(
            success=True,
            data={
                "run_id": result.run_id,
                "status": result.status.value,
                "overall_gate": result.overall_gate,
                "message": "OOS validation completed",
            },
        )

    except Exception as e:
        logger.error(f"OOS run creation failed: {e}")
        return APIResponse(
            success=False,
            message=f"OOS 실행 요청 실패: {str(e)}",
        )


@router.get("/latest", response_model=APIResponse[dict])
async def get_latest_oos_run(
    current_user: str = Depends(get_current_user),
):
    """
    최근 OOS 실행 결과 조회

    가장 최근에 완료된 OOS 실행의 전체 결과를 반환합니다.
    """
    manager = OOSJobManager()
    latest = manager.get_latest()

    if latest is None:
        return APIResponse(
            success=True,
            data={"message": "No OOS runs found"},
        )

    return APIResponse(
        success=True,
        data=latest.to_dict(),
    )


@router.get("/gate-status", response_model=APIResponse[dict])
async def get_oos_gate_status(
    current_user: str = Depends(get_current_user),
):
    """
    OOS 게이트 상태 요약

    최근 실행들의 게이트 통과 상태와 배포 가능 여부를 반환합니다.
    """
    manager = OOSJobManager()
    return APIResponse(
        success=True,
        data=manager.get_gate_status(),
    )


@router.get("/{run_id}", response_model=APIResponse[dict])
async def get_oos_run(
    run_id: str,
    current_user: str = Depends(get_current_user),
):
    """
    특정 OOS 실행 결과 조회

    run_id에 해당하는 OOS 검증 결과의 전체 상세를 반환합니다.
    """
    manager = OOSJobManager()
    run = manager.get_run(run_id)

    if run is None:
        return APIResponse(
            success=False,
            message=f"OOS run not found: {run_id}",
        )

    return APIResponse(
        success=True,
        data=run.to_dict(),
    )
