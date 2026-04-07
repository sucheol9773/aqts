"""
파라미터 민감도 분석 API 라우트

POST /run — 민감도 분석 실행
GET /latest — 최근 분석 결과
GET /tornado — 토네이도 차트 데이터
"""

import asyncio
from datetime import datetime

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException

from api.middleware.rbac import require_operator, require_viewer
from config.logging import logger
from core.param_sensitivity import (
    ParamSensitivityEngine,
    SensitivityAnalyzer,
    SensitivityRun,
    SensitivityRunRequest,
)

router = APIRouter()

# In-memory 저장소 (MVP)
_latest_run: SensitivityRun | None = None


@router.post("/run")
async def run_sensitivity(
    request: SensitivityRunRequest,
    current_user=Depends(require_operator),
):
    """민감도 분석 실행"""
    global _latest_run

    logger.info(f"Sensitivity analysis requested: {request.strategy_version}")

    # MVP: 샘플 데이터 생성
    signals, prices = _generate_sample_data(request.tickers)

    engine = ParamSensitivityEngine(
        sweep_method=request.sweep_method,
        max_trials=500,
    )

    # 동기 실행을 executor에서 실행
    loop = asyncio.get_event_loop()
    run = await loop.run_in_executor(
        None,
        lambda: engine.run(
            strategy_version=request.strategy_version,
            signals=signals,
            prices=prices,
            use_oat=True,
        ),
    )

    _latest_run = run

    return {
        "status": "success",
        "data": run.to_summary_dict(),
    }


@router.get("/latest")
async def get_latest(current_user=Depends(require_viewer)):
    """최근 분석 결과 조회"""
    if _latest_run is None:
        raise HTTPException(status_code=404, detail="분석 결과가 없습니다")

    return {
        "status": "success",
        "data": _latest_run.to_dict(),
    }


@router.get("/tornado")
async def get_tornado(
    metric: str = "sharpe",
    current_user=Depends(require_viewer),
):
    """토네이도 차트 데이터"""
    if _latest_run is None:
        raise HTTPException(status_code=404, detail="분석 결과가 없습니다")

    if metric not in ("sharpe", "cagr", "mdd"):
        raise HTTPException(status_code=400, detail="metric은 sharpe, cagr, mdd 중 하나여야 합니다")

    analyzer = SensitivityAnalyzer(
        param_ranges=_latest_run.param_ranges,
        base_values={pr.name: pr.base_value for pr in _latest_run.param_ranges},
    )
    ranking = analyzer.tornado_ranking(_latest_run.elasticities, metric=metric)

    return {
        "status": "success",
        "metric": metric,
        "data": ranking,
    }


def _generate_sample_data(
    tickers: list[str],
    days: int = 252,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """MVP용 샘플 데이터 생성 (추후 DataCollector로 교체)"""
    np.random.seed(42)
    dates = pd.bdate_range(end=datetime.now(), periods=days)

    prices_data = {}
    signals_data = {}

    for ticker in tickers:
        # 랜덤 워크 가격
        returns = np.random.normal(0.0005, 0.02, days)
        price = 50000 * np.cumprod(1 + returns)
        prices_data[ticker] = price

        # 랜덤 시그널 (-1 ~ +1)
        signal = np.random.uniform(-0.5, 0.5, days)
        # 모멘텀 효과 추가
        for i in range(5, days):
            if returns[i - 1] > 0.01:
                signal[i] = min(signal[i] + 0.3, 1.0)
            elif returns[i - 1] < -0.01:
                signal[i] = max(signal[i] - 0.3, -1.0)
        signals_data[ticker] = signal

    prices = pd.DataFrame(prices_data, index=dates)
    signals = pd.DataFrame(signals_data, index=dates)

    return signals, prices
