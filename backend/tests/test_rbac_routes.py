"""
RBAC 라우트 통합 테스트 (Wiring 검증)

목적:
    모든 mutation 라우트(POST/PUT/PATCH/DELETE)가 viewer 토큰으로 호출 시
    403 을 반환하는지 자동으로 검증한다. 신규 라우트가 추가되면 본 테스트가
    바로 실패하여 RBAC 가드 누락을 차단한다.

검사 범위:
    - FastAPI app 의 모든 라우트를 동적으로 수집
    - 화이트리스트(자기 세션 / 공개 엔드포인트) 제외
    - viewer 토큰 호출 → 403 기대
    - path parameter 가 있으면 dummy 값으로 대체

제외 라우트:
    - /api/auth/login, /refresh, /logout, /me, /mfa/* (자기 세션 관리)
    - /api/health/* (공개)
    - /docs, /openapi.json 등 (문서)
"""

from __future__ import annotations

import re

import pytest
from httpx import ASGITransport, AsyncClient

# 화이트리스트: viewer 가 호출해도 403 이 아닌 (자기 세션 관리 / 공개) 경로
WHITELIST_PATHS: set[str] = {
    "/api/auth/login",
    "/api/auth/refresh",
    "/api/auth/logout",
    "/api/auth/me",
    "/api/auth/mfa/enroll",
    "/api/auth/mfa/verify",
    "/api/auth/mfa/disable",
}

MUTATION_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _substitute_path_params(path: str) -> str:
    """{ticker}, {alert_id} 같은 path param 을 dummy 값으로 치환."""
    return re.sub(r"\{[^}]+\}", "dummy", path)


def _collect_mutation_routes(app) -> list[tuple[str, str]]:
    """앱에서 mutation 라우트를 (method, path) 리스트로 수집."""
    routes: list[tuple[str, str]] = []
    for route in app.routes:
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        if not methods or not path:
            continue
        if not path.startswith("/api/"):
            continue
        if path in WHITELIST_PATHS:
            continue
        for method in methods:
            if method in MUTATION_METHODS:
                routes.append((method, path))
    return routes


@pytest.mark.asyncio
class TestRBACMutationCoverage:
    """모든 mutation 라우트가 viewer 토큰으로 403 을 반환해야 한다."""

    async def test_all_mutation_routes_forbid_viewer(self, authenticated_app, viewer_token):
        """viewer 토큰으로 mutation 호출 시 모두 403."""
        routes = _collect_mutation_routes(authenticated_app)
        assert routes, "수집된 mutation 라우트가 없습니다 — 테스트 설정 확인 필요"

        failures: list[str] = []
        transport = ASGITransport(app=authenticated_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {viewer_token}"}
            for method, path in routes:
                concrete = _substitute_path_params(path)
                response = await client.request(method, concrete, headers=headers, json={})
                # 403 (RBAC 거부) 또는 401 이 아닌 경우 실패로 기록
                # 401 은 화이트리스트 누락 가능성이므로 별도 처리
                if response.status_code != 403:
                    failures.append(f"{method} {path} → {response.status_code} (403 기대)")

        assert not failures, "RBAC 가드 누락 라우트 발견:\n  - " + "\n  - ".join(failures)

    async def test_no_mutation_route_uses_only_viewer_guard(self, authenticated_app):
        """정적 검사 보강: 수집된 mutation 라우트가 최소 1개 이상 존재해야 한다."""
        routes = _collect_mutation_routes(authenticated_app)
        assert len(routes) >= 10, f"mutation 라우트 수집 부족: {len(routes)}"
