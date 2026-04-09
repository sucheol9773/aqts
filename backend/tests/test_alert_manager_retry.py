"""AlertManager 재시도 모델 테스트 (Commit 1).

검증 대상:
    1. `AlertStatus` 에 `SENDING`/`DEAD` 가 존재
    2. `Alert` dataclass 에 `send_attempts` / `last_send_error` /
       `last_send_attempt_at` / `last_send_status_code` 필드 존재 및
       `to_dict` 직렬화
    3. `save_alert` 가 동일 id 에 대해 `update_one(upsert=True)` 호출
       (중복 행 생성 금지)
    4. `claim_for_sending` 원자적 전이:
       - PENDING → SENDING, `send_attempts` +1
       - 이미 SENDING 인 경우 False 반환, 상태/카운트 불변
    5. `mark_sent_by_id`: SENDING → SENT, `sent_at` 설정
       - SENDING 아닐 때 False 반환, 상태 불변
    6. `mark_failed_with_retry`:
       - `send_attempts < max_attempts` → FAILED
       - `send_attempts >= max_attempts` → DEAD (경계 gte)
       - `last_send_error` 를 `MAX_ALERT_ERROR_LEN` 자에서 절단
       - `last_send_status_code` 보존

CLAUDE.md 규칙 준수:
    - 테스트 입력값은 임계값 경계를 통과/미달하도록 설정하되, 기대값 자체는
      실제 동작에 근거해 고정한다.
    - multiprocessing/외부 프로세스 의존 없음 (모델/영속화 레이어만).
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from config.constants import AlertType
from core.notification.alert_manager import (
    MAX_ALERT_ERROR_LEN,
    Alert,
    AlertLevel,
    AlertManager,
    AlertStatus,
)


# ══════════════════════════════════════════════════════════════════
# 1. AlertStatus / Alert 구조 검증
# ══════════════════════════════════════════════════════════════════
def test_alert_status_contains_sending_and_dead():
    assert AlertStatus.SENDING.value == "SENDING"
    assert AlertStatus.DEAD.value == "DEAD"


def test_alert_has_retry_fields_default_zero():
    alert = Alert(
        alert_type=AlertType.SYSTEM_ERROR,
        level=AlertLevel.ERROR,
        title="t",
        message="m",
    )
    assert alert.send_attempts == 0
    assert alert.last_send_error is None
    assert alert.last_send_attempt_at is None
    assert alert.last_send_status_code is None


def test_alert_to_dict_includes_retry_fields():
    alert = Alert(
        alert_type=AlertType.SYSTEM_ERROR,
        level=AlertLevel.ERROR,
        title="t",
        message="m",
        send_attempts=2,
        last_send_error="timeout",
        last_send_attempt_at=datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc),
        last_send_status_code=504,
    )
    d = alert.to_dict()
    assert d["send_attempts"] == 2
    assert d["last_send_error"] == "timeout"
    assert d["last_send_attempt_at"] == "2026-04-10T12:00:00+00:00"
    assert d["last_send_status_code"] == 504


def test_alert_to_dict_serializes_none_retry_fields():
    alert = Alert(
        alert_type=AlertType.SYSTEM_ERROR,
        level=AlertLevel.ERROR,
        title="t",
        message="m",
    )
    d = alert.to_dict()
    assert d["send_attempts"] == 0
    assert d["last_send_error"] is None
    assert d["last_send_attempt_at"] is None
    assert d["last_send_status_code"] is None


# ══════════════════════════════════════════════════════════════════
# 2. save_alert upsert 전환 검증
# ══════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_save_alert_uses_upsert_on_mongo():
    """save_alert 가 update_one(upsert=True) 를 호출하는지 검증.

    Commit 1 이전에는 insert_one 을 호출했기 때문에 동일 id 재호출 시
    중복 행이 생겼다. upsert 전환으로 멱등 보장.
    """
    coll = MagicMock()
    coll.update_one = AsyncMock()
    mgr = AlertManager(mongo_collection=coll)
    alert = mgr.create_alert(
        alert_type=AlertType.SYSTEM_ERROR,
        level=AlertLevel.ERROR,
        title="t",
        message="m",
    )
    await mgr.save_alert(alert)

    coll.update_one.assert_awaited_once()
    args, kwargs = coll.update_one.call_args
    # filter
    assert args[0] == {"id": alert.id}
    # $set 문서에 신규 필드 포함
    assert "$set" in args[1]
    set_doc = args[1]["$set"]
    assert set_doc["id"] == alert.id
    assert set_doc["alert_type"] == AlertType.SYSTEM_ERROR.value
    assert set_doc["level"] == AlertLevel.ERROR.value
    assert "send_attempts" in set_doc
    # upsert 옵션
    assert kwargs.get("upsert") is True


@pytest.mark.asyncio
async def test_save_alert_memory_mode_no_duplicate():
    """메모리 모드에서는 save_alert 가 no-op (create_alert 가 이미 append).

    두 번 호출해도 `_in_memory_alerts` 에 중복 생성되지 않아야 한다.
    """
    mgr = AlertManager()
    alert = mgr.create_alert(
        alert_type=AlertType.SYSTEM_ERROR,
        level=AlertLevel.ERROR,
        title="t",
        message="m",
    )
    await mgr.save_alert(alert)
    await mgr.save_alert(alert)
    assert len(mgr._in_memory_alerts) == 1


# ══════════════════════════════════════════════════════════════════
# 3. claim_for_sending — atomic 전이 (메모리 모드)
# ══════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_claim_for_sending_transitions_pending_to_sending():
    mgr = AlertManager()
    alert = mgr.create_alert(
        alert_type=AlertType.SYSTEM_ERROR,
        level=AlertLevel.ERROR,
        title="t",
        message="m",
    )
    claimed = await mgr.claim_for_sending(alert.id)

    assert claimed is True
    assert alert.status == AlertStatus.SENDING
    assert alert.send_attempts == 1
    assert alert.last_send_attempt_at is not None


@pytest.mark.asyncio
async def test_claim_for_sending_rejects_double_claim():
    """이미 SENDING 상태인 Alert 에 대한 두 번째 claim 은 False 반환.

    다중 워커 race 방어 계약. 두 번째 claim 은 상태와 카운트를 변경하지
    않아야 한다.
    """
    mgr = AlertManager()
    alert = mgr.create_alert(
        alert_type=AlertType.SYSTEM_ERROR,
        level=AlertLevel.ERROR,
        title="t",
        message="m",
    )
    first = await mgr.claim_for_sending(alert.id)
    second = await mgr.claim_for_sending(alert.id)

    assert first is True
    assert second is False
    assert alert.status == AlertStatus.SENDING
    assert alert.send_attempts == 1  # 두 번째 claim 은 증가시키지 않음


@pytest.mark.asyncio
async def test_claim_for_sending_returns_false_for_unknown_id():
    mgr = AlertManager()
    claimed = await mgr.claim_for_sending("nonexistent-id")
    assert claimed is False


@pytest.mark.asyncio
async def test_claim_for_sending_calls_mongo_update_with_inc():
    """MongoDB 경로에서 update_one 이 $inc send_attempts 를 포함하는지 검증."""
    coll = MagicMock()
    coll.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    mgr = AlertManager(mongo_collection=coll)

    ok = await mgr.claim_for_sending("alert-123")

    assert ok is True
    coll.update_one.assert_awaited_once()
    args, _ = coll.update_one.call_args
    # filter: id + status=PENDING
    assert args[0]["id"] == "alert-123"
    assert args[0]["status"] == AlertStatus.PENDING.value
    # update: $set status=SENDING, $inc send_attempts
    assert args[1]["$set"]["status"] == AlertStatus.SENDING.value
    assert args[1]["$inc"]["send_attempts"] == 1


# ══════════════════════════════════════════════════════════════════
# 4. mark_sent_by_id — SENDING → SENT
# ══════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_mark_sent_by_id_after_claim():
    mgr = AlertManager()
    alert = mgr.create_alert(
        alert_type=AlertType.SYSTEM_ERROR,
        level=AlertLevel.ERROR,
        title="t",
        message="m",
    )
    await mgr.claim_for_sending(alert.id)
    ok = await mgr.mark_sent_by_id(alert.id)

    assert ok is True
    assert alert.status == AlertStatus.SENT
    assert alert.sent_at is not None
    assert alert.last_send_error is None


@pytest.mark.asyncio
async def test_mark_sent_by_id_rejects_non_sending():
    """SENDING 전이 없이 곧바로 mark_sent 호출 → False, 상태 불변."""
    mgr = AlertManager()
    alert = mgr.create_alert(
        alert_type=AlertType.SYSTEM_ERROR,
        level=AlertLevel.ERROR,
        title="t",
        message="m",
    )
    ok = await mgr.mark_sent_by_id(alert.id)

    assert ok is False
    assert alert.status == AlertStatus.PENDING


# ══════════════════════════════════════════════════════════════════
# 5. mark_failed_with_retry — FAILED / DEAD 경계
# ══════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_mark_failed_with_retry_returns_failed_below_max():
    """첫 시도 실패 (attempts=1, max=3) → FAILED."""
    mgr = AlertManager()
    alert = mgr.create_alert(
        alert_type=AlertType.SYSTEM_ERROR,
        level=AlertLevel.ERROR,
        title="t",
        message="m",
    )
    await mgr.claim_for_sending(alert.id)  # attempts=1

    status = await mgr.mark_failed_with_retry(alert.id, error="conn refused", status_code=None, max_attempts=3)

    assert status == AlertStatus.FAILED
    assert alert.status == AlertStatus.FAILED
    assert alert.send_attempts == 1  # mark_failed 는 증가시키지 않음
    assert alert.last_send_error == "conn refused"
    assert alert.last_send_status_code is None


@pytest.mark.asyncio
async def test_mark_failed_with_retry_returns_dead_at_max_boundary():
    """gte 3 경계: 세 번째 시도 실패 시 DEAD.

    사용자 확정 방침 (2026-04-10): '3번 시도하고 포기' 의 직관에 맞춰
    `send_attempts >= max_attempts` 이면 DEAD.
    """
    mgr = AlertManager()
    alert = mgr.create_alert(
        alert_type=AlertType.SYSTEM_ERROR,
        level=AlertLevel.ERROR,
        title="t",
        message="m",
    )
    # 3회 시도 완료 상태를 시뮬레이션: claim 이 증가시키는 경로를 거치지 않고
    # 직접 attempts 를 세팅해 경계값만 검증한다. Commit 3 에서 스케줄러가
    # FAILED → PENDING 재픽업을 구현하면 자연스러운 3회 누적이 생긴다.
    alert.send_attempts = 3
    alert.status = AlertStatus.SENDING

    status = await mgr.mark_failed_with_retry(alert.id, error="504 Gateway Timeout", status_code=504, max_attempts=3)

    assert status == AlertStatus.DEAD
    assert alert.status == AlertStatus.DEAD
    assert alert.last_send_status_code == 504
    assert alert.last_send_error == "504 Gateway Timeout"


@pytest.mark.asyncio
async def test_mark_failed_with_retry_dead_for_gt_max():
    """attempts=4, max=3 → DEAD (> 경계 위)."""
    mgr = AlertManager()
    alert = mgr.create_alert(
        alert_type=AlertType.SYSTEM_ERROR,
        level=AlertLevel.ERROR,
        title="t",
        message="m",
    )
    alert.send_attempts = 4
    alert.status = AlertStatus.SENDING

    status = await mgr.mark_failed_with_retry(alert.id, error="still failing", max_attempts=3)

    assert status == AlertStatus.DEAD


@pytest.mark.asyncio
async def test_mark_failed_with_retry_failed_at_max_minus_one():
    """attempts=2, max=3 → FAILED (경계 바로 아래)."""
    mgr = AlertManager()
    alert = mgr.create_alert(
        alert_type=AlertType.SYSTEM_ERROR,
        level=AlertLevel.ERROR,
        title="t",
        message="m",
    )
    alert.send_attempts = 2
    alert.status = AlertStatus.SENDING

    status = await mgr.mark_failed_with_retry(alert.id, error="retry me", max_attempts=3)

    assert status == AlertStatus.FAILED


@pytest.mark.asyncio
async def test_mark_failed_with_retry_truncates_long_error():
    mgr = AlertManager()
    alert = mgr.create_alert(
        alert_type=AlertType.SYSTEM_ERROR,
        level=AlertLevel.ERROR,
        title="t",
        message="m",
    )
    await mgr.claim_for_sending(alert.id)
    long_err = "x" * (MAX_ALERT_ERROR_LEN + 500)

    await mgr.mark_failed_with_retry(alert.id, error=long_err, status_code=None, max_attempts=3)

    assert alert.last_send_error is not None
    assert len(alert.last_send_error) == MAX_ALERT_ERROR_LEN


@pytest.mark.asyncio
async def test_mark_failed_with_retry_unknown_id_returns_failed():
    mgr = AlertManager()
    status = await mgr.mark_failed_with_retry("nonexistent-id", error="whatever", max_attempts=3)
    assert status == AlertStatus.FAILED
