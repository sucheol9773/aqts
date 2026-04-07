"""/api/alerts 라우트 종단 통합 테스트.

검증 대상 (CLAUDE.md Wiring Rule):
    - create_and_persist_alert 로 저장된 알림이 실제로 GET /api/alerts 조회 결과에 포함되는지
    - in-memory 폴백 경로 (컬렉션 미주입)
    - MongoDB 경로 (컬렉션 주입, insert_one 이 호출되고 find cursor 가 소비됨)
    - AlertResponse 스키마에 실제 Alert 데이터가 매핑되는지 (status 필드 포함)
    - unread_count 가 올바르게 계산되는지
    - 필터 파라미터(alert_type, level) 가 동작하는지
    - /stats 엔드포인트가 by_level 분포를 올바르게 반환하는지

이번 트랙에서 영속화 경로(create_and_persist_alert → MongoDB insert)가 반대편의
조회 경로(/api/alerts → get_alerts) 와 맞물려 끝에서 끝까지 동작하는지 한 번도
통합 검증된 적이 없었다.
"""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from api.middleware.auth import AuthenticatedUser
from api.middleware.rbac import require_operator, require_viewer
from api.routes.alerts import get_alert_manager
from config.constants import AlertType
from core.notification.alert_manager import (
    AlertLevel,
    AlertManager,
    AlertStatus,
)


class _AsyncCursorStub:
    """AsyncIOMotorCursor 를 흉내내는 최소 stub.

    get_alerts() 는 `cursor.find(query).sort(...).skip(...).limit(...)` 를 호출하고
    `async for doc in cursor` 로 소비한다. 각 체이닝 메서드는 self 를 반환해야 하고,
    __aiter__ 는 내부 리스트를 순회해야 한다.
    """

    def __init__(self, docs):
        self._docs = list(docs)

    def find(self, query=None):
        return self

    def sort(self, *args, **kwargs):
        return self

    def skip(self, n):
        return self

    def limit(self, n):
        return self

    def __aiter__(self):
        async def _gen():
            for d in self._docs:
                yield d

        return _gen()


class _FakeMongoCollection:
    """insert_one 호출을 캡처하고 find 로 동일 문서를 돌려주는 단순 fake 컬렉션."""

    def __init__(self):
        self._docs: list[dict] = []
        self.insert_calls = 0

    async def insert_one(self, doc):
        self.insert_calls += 1
        self._docs.append(doc)

    def find(self, query=None):
        # 최소한의 필터 매칭: query 가 없으면 전체, alert_type/level 만 지원
        filtered = self._docs
        if query:
            if "alert_type" in query:
                filtered = [d for d in filtered if d["alert_type"] == query["alert_type"]]
            if "level" in query:
                filtered = [d for d in filtered if d["level"] == query["level"]]
        return _AsyncCursorStub(filtered)

    async def count_documents(self, query):
        if "$ne" in query.get("status", {}):
            excluded = query["status"]["$ne"]
            return len([d for d in self._docs if d["status"] != excluded])
        if "level" in query:
            return len([d for d in self._docs if d["level"] == query["level"]])
        return len(self._docs)


def _override_viewer():
    return AuthenticatedUser(id="test-viewer", username="tester", role="admin")


class TestAlertsRouteE2E(unittest.TestCase):
    """/api/alerts 라우트 종단 통합 테스트.

    각 테스트는 깨끗한 AlertManager 인스턴스를 주입하고 dependency_overrides 로
    auth 가드를 우회한다 (auth 경로는 test_rbac_routes 에서 별도 검증).
    """

    def setUp(self):
        import main

        self.app = main.app
        # auth 우회
        self.app.dependency_overrides[require_viewer] = _override_viewer
        self.app.dependency_overrides[require_operator] = _override_viewer

    def tearDown(self):
        self.app.dependency_overrides.pop(require_viewer, None)
        self.app.dependency_overrides.pop(require_operator, None)
        self.app.dependency_overrides.pop(get_alert_manager, None)

    def _inject(self, manager: AlertManager) -> None:
        self.app.dependency_overrides[get_alert_manager] = lambda: manager

    # ── in-memory 경로 ──

    def test_in_memory_persist_then_list_returns_alert(self):
        """컬렉션 미주입 상태에서 create_and_persist_alert → /api/alerts 로 조회."""
        import asyncio

        manager = AlertManager()
        asyncio.get_event_loop().run_until_complete(
            manager.create_and_persist_alert(
                alert_type=AlertType.SYSTEM_ERROR,
                level=AlertLevel.ERROR,
                title="KIS API 자동 복원 연속 실패",
                message="consecutive 5",
                metadata={"consecutive_failures": 5},
            )
        )
        self._inject(manager)

        client = TestClient(self.app, raise_server_exceptions=False)
        resp = client.get("/api/alerts/")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["unread_count"] == 1
        assert len(data["alerts"]) == 1
        alert = data["alerts"][0]
        assert alert["alert_type"] == "SYSTEM_ERROR"
        assert alert["level"] == "ERROR"
        assert alert["title"] == "KIS API 자동 복원 연속 실패"
        assert alert["status"] == AlertStatus.PENDING.value

    def test_in_memory_filter_by_alert_type_and_level(self):
        import asyncio

        manager = AlertManager()
        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            manager.create_and_persist_alert(
                alert_type=AlertType.SYSTEM_ERROR,
                level=AlertLevel.ERROR,
                title="KIS down",
                message="",
            )
        )
        loop.run_until_complete(
            manager.create_and_persist_alert(
                alert_type=AlertType.DAILY_REPORT,
                level=AlertLevel.INFO,
                title="daily",
                message="",
            )
        )
        self._inject(manager)

        client = TestClient(self.app, raise_server_exceptions=False)

        # alert_type 필터
        resp = client.get("/api/alerts/", params={"alert_type": "SYSTEM_ERROR"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["alerts"]) == 1
        assert data["alerts"][0]["alert_type"] == "SYSTEM_ERROR"

        # level 필터
        resp = client.get("/api/alerts/", params={"level": "INFO"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["alerts"]) == 1
        assert data["alerts"][0]["level"] == "INFO"

    def test_in_memory_stats_endpoint_by_level_distribution(self):
        import asyncio

        manager = AlertManager()
        loop = asyncio.get_event_loop()
        for lv in (AlertLevel.ERROR, AlertLevel.ERROR, AlertLevel.INFO):
            loop.run_until_complete(
                manager.create_and_persist_alert(
                    alert_type=AlertType.SYSTEM_ERROR,
                    level=lv,
                    title="t",
                    message="m",
                )
            )
        self._inject(manager)

        client = TestClient(self.app, raise_server_exceptions=False)
        resp = client.get("/api/alerts/stats")
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["total"] == 3
        assert data["unread"] == 3
        assert data["by_level"]["ERROR"] == 2
        assert data["by_level"]["INFO"] == 1

    # ── MongoDB 경로 ──

    def test_mongodb_persist_then_list_returns_same_alert(self):
        """컬렉션 주입 상태에서 insert_one 호출 + find 경로 소비가 끝에서 끝까지 동작."""
        import asyncio

        manager = AlertManager()
        fake_collection = _FakeMongoCollection()
        manager.set_collection(fake_collection)

        asyncio.get_event_loop().run_until_complete(
            manager.create_and_persist_alert(
                alert_type=AlertType.SYSTEM_ERROR,
                level=AlertLevel.CRITICAL,
                title="KIS critical",
                message="x",
                metadata={"consecutive_failures": 10},
            )
        )
        # 영속화 확인
        assert fake_collection.insert_calls == 1

        self._inject(manager)

        client = TestClient(self.app, raise_server_exceptions=False)
        resp = client.get("/api/alerts/")
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["unread_count"] == 1
        assert len(data["alerts"]) == 1
        alert = data["alerts"][0]
        assert alert["alert_type"] == "SYSTEM_ERROR"
        assert alert["level"] == "CRITICAL"
        assert alert["title"] == "KIS critical"

    def test_mongodb_stats_uses_count_documents(self):
        import asyncio

        manager = AlertManager()
        fake_collection = _FakeMongoCollection()
        manager.set_collection(fake_collection)

        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            manager.create_and_persist_alert(
                alert_type=AlertType.SYSTEM_ERROR,
                level=AlertLevel.ERROR,
                title="a",
                message="",
            )
        )
        loop.run_until_complete(
            manager.create_and_persist_alert(
                alert_type=AlertType.DAILY_REPORT,
                level=AlertLevel.INFO,
                title="b",
                message="",
            )
        )

        self._inject(manager)
        client = TestClient(self.app, raise_server_exceptions=False)
        resp = client.get("/api/alerts/stats")
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["total"] == 2
        assert data["unread"] == 2
        assert data["by_level"]["ERROR"] == 1
        assert data["by_level"]["INFO"] == 1

    def test_mark_alert_read_via_route(self):
        """읽음 처리 경로 검증 (in-memory)."""
        import asyncio

        manager = AlertManager()
        alert = asyncio.get_event_loop().run_until_complete(
            manager.create_and_persist_alert(
                alert_type=AlertType.SYSTEM_ERROR,
                level=AlertLevel.ERROR,
                title="t",
                message="m",
            )
        )
        self._inject(manager)

        client = TestClient(self.app, raise_server_exceptions=False)
        resp = client.put(f"/api/alerts/{alert.id}/read")
        assert resp.status_code == 200, resp.text
        assert resp.json()["success"] is True
        # 실제 상태가 READ 로 변경되었는지 확인
        assert manager._in_memory_alerts[0].status == AlertStatus.READ


if __name__ == "__main__":
    unittest.main()
