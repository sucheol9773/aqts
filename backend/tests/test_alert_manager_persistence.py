"""AlertManager 영속화/주입 단위 테스트.

검증 대상:
    - set_collection() 으로 런타임 컬렉션 주입
    - create_and_persist_alert: 컬렉션 미주입 시 in-memory 만, 주입 시 MongoDB insert
    - DB 쓰기 실패 시 예외 전파 (호출자가 swallow 하도록 함)
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from config.constants import AlertType
from core.notification.alert_manager import AlertLevel, AlertManager, AlertStatus


@pytest.mark.asyncio
async def test_create_and_persist_without_collection_falls_back_to_memory():
    manager = AlertManager()
    assert manager._collection is None

    alert = await manager.create_and_persist_alert(
        alert_type=AlertType.SYSTEM_ERROR,
        level=AlertLevel.ERROR,
        title="t",
        message="m",
        metadata={"k": "v"},
    )

    assert alert.alert_type == AlertType.SYSTEM_ERROR
    assert alert.level == AlertLevel.ERROR
    assert alert.status == AlertStatus.PENDING
    # in-memory 저장 확인
    assert len(manager._in_memory_alerts) == 1
    assert manager._in_memory_alerts[0] is alert


@pytest.mark.asyncio
async def test_create_and_persist_with_collection_calls_upsert():
    """create_and_persist_alert 가 save_alert → update_one(upsert=True) 를
    호출하는지 검증.

    Commit 1 에서 `save_alert` 를 `insert_one` → `update_one(upsert=True)`
    로 전환했다. 운영 코드가 중복 호출되어도 동일 id 에 대해 멱등 보장.
    """
    manager = AlertManager()
    fake_collection = AsyncMock()
    fake_collection.update_one = AsyncMock()
    manager.set_collection(fake_collection)

    alert = await manager.create_and_persist_alert(
        alert_type=AlertType.SYSTEM_ERROR,
        level=AlertLevel.CRITICAL,
        title="KIS down",
        message="x",
        metadata={"consecutive_failures": 3},
    )

    fake_collection.update_one.assert_awaited_once()
    call_args = fake_collection.update_one.await_args
    # filter: id 기준
    assert call_args.args[0] == {"id": alert.id}
    # $set 문서 내용 검증
    assert "$set" in call_args.args[1]
    inserted_doc = call_args.args[1]["$set"]
    assert inserted_doc["id"] == alert.id
    assert inserted_doc["alert_type"] == AlertType.SYSTEM_ERROR.value
    assert inserted_doc["level"] == AlertLevel.CRITICAL.value
    assert inserted_doc["title"] == "KIS down"
    assert inserted_doc["metadata"]["consecutive_failures"] == 3
    # upsert 옵션
    assert call_args.kwargs.get("upsert") is True
    # in-memory 도 함께 보관 (조회 폴백 지원)
    assert len(manager._in_memory_alerts) == 1


@pytest.mark.asyncio
async def test_create_and_persist_propagates_db_error():
    manager = AlertManager()
    fake_collection = AsyncMock()
    fake_collection.update_one = AsyncMock(side_effect=RuntimeError("mongo down"))
    manager.set_collection(fake_collection)

    with pytest.raises(RuntimeError, match="mongo down"):
        await manager.create_and_persist_alert(
            alert_type=AlertType.SYSTEM_ERROR,
            level=AlertLevel.ERROR,
            title="t",
            message="m",
        )

    # 예외가 났더라도 in-memory 에는 이미 추가되어 있어야 한다 (create_alert 가 먼저 실행됨)
    # 이는 의도적인 동작: in-memory 폴백으로 최소한 조회는 가능하게 한다.
    assert len(manager._in_memory_alerts) == 1


@pytest.mark.asyncio
async def test_main_startup_injects_alerts_collection_into_singleton(monkeypatch):
    """main.py 의 lifespan startup 이 _alert_manager 에 mongo 컬렉션을 실제로 주입하는지 검증.

    단위 테스트(test_alert_manager_persistence)는 set_collection 자체의 동작만
    검증한다. wiring 이 startup 경로에서 호출되는지는 통합 검증으로만 확인 가능하다.
    """
    from unittest.mock import MagicMock, patch

    from fastapi.testclient import TestClient

    import api.routes.alerts as alerts_module
    import main

    # AlertManager 싱글톤을 깨끗한 상태로 교체
    original_alert_manager = alerts_module._alert_manager
    fresh_manager = AlertManager()
    alerts_module._alert_manager = fresh_manager
    assert fresh_manager._collection is None

    fake_collection = MagicMock(name="fake_alerts_collection")

    # P1-정합성: lifespan 이 PortfolioLedger 를 SQL repo 로 (재)구성하므로
    # 본 통합 테스트는 hydrate 가 실제 DB 에 도달하지 않도록 fake repo 를 주입한다.
    fake_repo = MagicMock(name="fake_portfolio_repo")
    fake_repo.load_all = AsyncMock(return_value={})

    with (
        patch("main.MongoDBManager") as mock_mongo,
        patch("main.RedisManager") as mock_redis,
        patch("main.signal.signal"),
        patch("main.SqlPortfolioLedgerRepository", return_value=fake_repo),
    ):
        mock_mongo.connect = AsyncMock()
        mock_mongo.disconnect = AsyncMock()
        mock_mongo.get_collection.return_value = fake_collection
        mock_mongo.get_db.return_value.command = AsyncMock()
        mock_redis.connect = AsyncMock()
        mock_redis.get_client.return_value.ping = AsyncMock()

        try:
            with TestClient(main.app, raise_server_exceptions=False):
                # lifespan startup 이 호출되며 set_collection 이 실행되어야 함
                assert fresh_manager._collection is fake_collection
                mock_mongo.get_collection.assert_called_with("alerts")
        finally:
            alerts_module._alert_manager = original_alert_manager
            # 다음 테스트에 ledger SQL repo 가 새지 않도록 정리.
            from core.portfolio_ledger import reset_portfolio_ledger

            reset_portfolio_ledger()


def test_set_collection_can_be_called_multiple_times():
    manager = AlertManager()
    first = AsyncMock()
    second = AsyncMock()

    manager.set_collection(first)
    assert manager._collection is first
    manager.set_collection(second)
    assert manager._collection is second
    manager.set_collection(None)
    assert manager._collection is None
