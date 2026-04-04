"""
OOS 비동기 작업 관리자 (Job Manager)

FastAPI BackgroundTasks 기반 비동기 OOS 실행.
MVP에서는 in-memory 상태 관리.

핵심 동작:
- POST 요청 시 run_id를 즉시 반환 (ack)
- BackgroundTask에서 walk-forward 실행
- GET으로 상태/결과 polling

동일 파라미터 해시로 기존 run_id 조회 (간이 idempotency).
"""

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from config.logging import logger
from core.oos.models import (
    JobStatus,
    OOSRun,
    OOSRunType,
    OOSStatus,
)
from core.oos.walk_forward import WalkForwardEngine


class OOSJobManager:
    """
    OOS 작업 관리자 (Singleton)

    in-memory 저장소로 실행 중/완료된 OOS 작업을 관리합니다.
    """

    _instance: Optional["OOSJobManager"] = None
    _runs: dict[str, OOSRun]      # run_id → OOSRun
    _param_index: dict[str, str]   # param_hash → run_id (idempotency)

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._runs = {}
            cls._instance._param_index = {}
            cls._instance._engine = WalkForwardEngine()
        return cls._instance

    @classmethod
    def reset(cls):
        """테스트용 싱글톤 리셋"""
        cls._instance = None

    def get_run(self, run_id: str) -> Optional[OOSRun]:
        """run_id로 실행 결과 조회"""
        return self._runs.get(run_id)

    def get_latest(self) -> Optional[OOSRun]:
        """가장 최근 실행 결과 조회"""
        if not self._runs:
            return None

        # started_at 기준 최신
        runs = sorted(
            self._runs.values(),
            key=lambda r: r.started_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return runs[0] if runs else None

    def get_gate_status(self) -> dict:
        """
        현재 게이트 상태 요약

        최근 N개 실행의 게이트 결과를 집계하여 반환.
        """
        if not self._runs:
            return {
                "total_runs": 0,
                "latest_gate": "NO_DATA",
                "gate_history": [],
                "deploy_allowed": False,
            }

        runs = sorted(
            self._runs.values(),
            key=lambda r: r.started_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

        latest = runs[0]

        # 최근 5개 실행의 게이트 결과
        gate_history = []
        for run in runs[:5]:
            gate_history.append({
                "run_id": run.run_id,
                "status": run.status.value,
                "overall_gate": run.overall_gate,
                "started_at": run.started_at.isoformat() if run.started_at else None,
            })

        # 배포 허용 여부: 최신 게이트가 PASS여야 함
        deploy_allowed = latest.overall_gate == "PASS"

        return {
            "total_runs": len(self._runs),
            "latest_gate": latest.overall_gate or "PENDING",
            "latest_run_id": latest.run_id,
            "latest_status": latest.status.value,
            "deploy_allowed": deploy_allowed,
            "gate_history": gate_history,
        }

    def _compute_param_hash(
        self,
        strategy_version: str,
        train_months: int,
        test_months: int,
        tickers: list[str],
    ) -> str:
        """파라미터 해시 (idempotency 키)"""
        key = json.dumps({
            "v": strategy_version,
            "train": train_months,
            "test": test_months,
            "tickers": sorted(tickers),
        }, sort_keys=True)
        return hashlib.md5(key.encode()).hexdigest()[:12]

    def find_existing_run(
        self,
        strategy_version: str,
        train_months: int,
        test_months: int,
        tickers: list[str],
    ) -> Optional[OOSRun]:
        """
        동일 파라미터의 기존 실행 조회

        실행 중이거나 완료된 동일 파라미터 작업이 있으면 반환.
        """
        param_hash = self._compute_param_hash(
            strategy_version, train_months, test_months, tickers
        )
        existing_id = self._param_index.get(param_hash)
        if existing_id:
            existing = self._runs.get(existing_id)
            if existing and existing.status in (
                OOSStatus.RUNNING,
                OOSStatus.PENDING,
                OOSStatus.PASS,
                OOSStatus.REVIEW,
            ):
                return existing
        return None

    def submit_run(
        self,
        strategy_version: str,
        train_months: int,
        test_months: int,
        tickers: list[str],
        signals: pd.DataFrame,
        prices: pd.DataFrame,
        market_data: Optional[dict] = None,
    ) -> OOSRun:
        """
        OOS 실행을 동기적으로 제출하고 실행

        MVP에서는 BackgroundTask 대신 직접 실행.
        API 레벨에서 run_in_executor로 비동기 래핑.

        Returns:
            완료된 OOSRun
        """
        # idempotency 체크
        existing = self.find_existing_run(
            strategy_version, train_months, test_months, tickers
        )
        if existing:
            logger.info(f"OOS run already exists: {existing.run_id}")
            return existing

        # 실행
        result = self._engine.run(
            strategy_name=f"ensemble_{strategy_version}",
            signals=signals,
            prices=prices,
            train_months=train_months,
            test_months=test_months,
            strategy_version=strategy_version,
            market_data=market_data,
        )

        # 저장
        self._runs[result.run_id] = result
        param_hash = self._compute_param_hash(
            strategy_version, train_months, test_months, tickers
        )
        self._param_index[param_hash] = result.run_id

        return result

    def list_runs(self, limit: int = 20) -> list[dict]:
        """최근 실행 목록 반환"""
        runs = sorted(
            self._runs.values(),
            key=lambda r: r.started_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return [r.to_summary_dict() for r in runs[:limit]]
