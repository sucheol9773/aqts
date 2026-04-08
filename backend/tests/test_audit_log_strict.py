"""
P0-4 감사 로그 fail-closed 유닛 테스트.

log() 는 fail-open (예외 삼킴, 카운터 증가 mode=soft),
log_strict() 는 fail-closed (AuditWriteFailure 재전파, 카운터 증가 mode=strict).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.monitoring.metrics import AUDIT_WRITE_FAILURES_TOTAL
from db.repositories.audit_log import AuditLogger, AuditWriteFailure


def _counter_value(action_type: str, mode: str) -> float:
    return AUDIT_WRITE_FAILURES_TOTAL.labels(action_type=action_type, mode=mode)._value.get()


@pytest.mark.asyncio
async def test_log_strict_success_does_not_raise():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=None)
    db.commit = AsyncMock(return_value=None)
    logger_ = AuditLogger(db)

    await logger_.log_strict(
        action_type="ORDER_CREATED",
        module="order_executor",
        description="ok",
    )
    db.execute.assert_awaited_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_log_strict_raises_on_db_failure_and_increments_counter():
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=RuntimeError("db down"))
    db.commit = AsyncMock(return_value=None)
    db.rollback = AsyncMock(return_value=None)
    logger_ = AuditLogger(db)

    before = _counter_value("ORDER_CREATED", "strict")

    with pytest.raises(AuditWriteFailure):
        await logger_.log_strict(
            action_type="ORDER_CREATED",
            module="order_executor",
            description="will fail",
        )

    db.rollback.assert_awaited()
    after = _counter_value("ORDER_CREATED", "strict")
    assert after == before + 1.0


@pytest.mark.asyncio
async def test_log_fail_open_swallows_exception_and_increments_counter():
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=RuntimeError("db down"))
    db.commit = AsyncMock(return_value=None)
    db.rollback = AsyncMock(return_value=None)
    logger_ = AuditLogger(db)

    before = _counter_value("ORDER_QUERIED", "soft")

    # Must NOT raise.
    await logger_.log(
        action_type="ORDER_QUERIED",
        module="orders_read",
        description="read path",
    )

    db.rollback.assert_awaited()
    after = _counter_value("ORDER_QUERIED", "soft")
    assert after == before + 1.0


@pytest.mark.asyncio
async def test_log_strict_commit_failure_also_raises():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=None)
    db.commit = AsyncMock(side_effect=RuntimeError("commit failed"))
    db.rollback = AsyncMock(return_value=None)
    logger_ = AuditLogger(db)

    with pytest.raises(AuditWriteFailure):
        await logger_.log_strict(
            action_type="ORDER_CREATED",
            module="order_executor",
            description="commit will fail",
        )
