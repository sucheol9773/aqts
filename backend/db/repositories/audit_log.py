"""
감사 로그 서비스 (Audit Trail Service)

NFR-04 명세 구현:
- 모든 주문 체결, 리밸런싱, 설정 변경에 대한 감사 로그 기록
- 투자 의사결정 과정 추적

P0-4 (security-integrity-roadmap §3.4, §3.6.4)
----------------------------------------------
주문/리밸런싱 등 금전적 쓰기 경로는 `log_strict()` 를 사용해야 한다.
`log_strict()` 는 audit DB 쓰기 실패 시 `AuditWriteFailure` 를 re-raise 하여
상위 라우터가 트랜잭션 rollback + 503 `AUDIT_UNAVAILABLE` 응답을 반환할
수 있게 한다. 이 경우 주문은 체결되지 않아야 한다.

기존 `log()` 는 읽기 경로/통계성 감사에서만 허용되며, 실패 시 에러 로그만
남기고 경로를 계속한다 (fail-open). 금전적 쓰기에는 절대 사용하지 않는다.
"""

from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config.logging import logger
from core.monitoring.metrics import AUDIT_WRITE_FAILURES_TOTAL


class AuditWriteFailure(RuntimeError):
    """감사 로그 쓰기 실패 (fail-closed 경로에서 상위로 전파)."""


class AuditLogger:
    """감사 로그 기록 서비스"""

    def __init__(self, db_session: AsyncSession):
        self._db = db_session

    async def _write(
        self,
        action_type: str,
        module: str,
        description: str,
        before_state: Optional[dict],
        after_state: Optional[dict],
        metadata: Optional[dict],
    ) -> None:
        import json

        query = text(
            """
            INSERT INTO audit_logs (time, action_type, module, description, before_state, after_state, metadata)
            VALUES (NOW(), :action_type, :module, :description, :before_state, :after_state, :metadata)
        """
        )
        await self._db.execute(
            query,
            {
                "action_type": action_type,
                "module": module,
                "description": description,
                "before_state": (json.dumps(before_state, ensure_ascii=False, default=str) if before_state else None),
                "after_state": (json.dumps(after_state, ensure_ascii=False, default=str) if after_state else None),
                "metadata": (json.dumps(metadata, ensure_ascii=False, default=str) if metadata else None),
            },
        )
        await self._db.commit()
        logger.debug(f"Audit log: [{action_type}] {module} - {description}")

    async def log(
        self,
        action_type: str,
        module: str,
        description: str,
        before_state: Optional[dict] = None,
        after_state: Optional[dict] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        """Fail-open 감사 로그 (읽기/통계 경로 전용).

        실패 시 카운터만 증가시키고 호출자를 막지 않는다. 주문/리밸런싱 등
        금전적 쓰기에서는 반드시 `log_strict()` 를 사용할 것.
        """
        try:
            await self._write(action_type, module, description, before_state, after_state, metadata)
        except Exception as e:
            AUDIT_WRITE_FAILURES_TOTAL.labels(action_type=action_type, mode="soft").inc()
            logger.error(f"Failed to write audit log (fail-open): {e}")
            try:
                await self._db.rollback()
            except Exception:  # noqa: BLE001 — rollback best effort
                pass

    async def log_strict(
        self,
        action_type: str,
        module: str,
        description: str,
        before_state: Optional[dict] = None,
        after_state: Optional[dict] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        """Fail-closed 감사 로그 (주문/리밸런싱 등 금전적 쓰기 경로).

        실패 시 `AuditWriteFailure` 를 re-raise 하여 상위에서 트랜잭션
        rollback + 503 `AUDIT_UNAVAILABLE` 응답을 반환할 수 있게 한다.
        """
        try:
            await self._write(action_type, module, description, before_state, after_state, metadata)
        except Exception as e:
            AUDIT_WRITE_FAILURES_TOTAL.labels(action_type=action_type, mode="strict").inc()
            logger.critical(
                "Audit write failed (fail-closed) action=%s module=%s err=%s",
                action_type,
                module,
                e,
            )
            try:
                await self._db.rollback()
            except Exception:  # noqa: BLE001
                pass
            raise AuditWriteFailure(action_type) from e
