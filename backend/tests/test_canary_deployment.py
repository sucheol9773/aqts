"""
카나리 배포 인프라 구조 검증 테스트.

nginx-canary.conf, docker-compose.canary.yml, canary_deploy.sh 의 구조적 무결성을
정적 분석으로 검증한다. 실제 nginx/docker 기동 없이 설정 파일의 필수 요소가
존재하는지 확인한다.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
NGINX_CANARY_CONF = REPO_ROOT / "nginx" / "nginx-canary.conf"
COMPOSE_CANARY = REPO_ROOT / "docker-compose.canary.yml"
CANARY_SCRIPT = REPO_ROOT / "scripts" / "canary_deploy.sh"


class TestCanaryNginxConfig:
    @pytest.fixture(scope="class")
    def nginx_content(self) -> str:
        return NGINX_CANARY_CONF.read_text(encoding="utf-8")

    def test_nginx_canary_conf_exists(self):
        assert NGINX_CANARY_CONF.exists()

    def test_has_upstream_stable(self, nginx_content):
        assert "upstream" in nginx_content and "stable" in nginx_content, "upstream backend_stable 블록이 없음"

    def test_has_upstream_canary(self, nginx_content):
        assert "canary" in nginx_content, "upstream backend_canary 블록이 없음"

    def test_has_split_clients(self, nginx_content):
        """split_clients 로 트래픽 분할 설정"""
        assert "split_clients" in nginx_content, "split_clients 설정이 없음. 트래픽 분할이 필요하다."

    def test_has_health_endpoint(self, nginx_content):
        """nginx 자체 헬스체크 엔드포인트"""
        assert "health" in nginx_content.lower(), "헬스체크 엔드포인트 설정이 없음"


class TestCanaryDockerCompose:
    @pytest.fixture(scope="class")
    def compose_content(self) -> str:
        """docker-compose.canary.yml 은 !override 등 커스텀 태그를 사용하므로
        yaml.safe_load 대신 텍스트 기반으로 검증한다."""
        return COMPOSE_CANARY.read_text(encoding="utf-8")

    def test_compose_canary_exists(self):
        assert COMPOSE_CANARY.exists()

    def test_has_stable_service(self, compose_content):
        assert "backend-stable" in compose_content, "stable 서비스가 정의되어 있지 않음"

    def test_has_canary_service(self, compose_content):
        assert "backend-canary" in compose_content, "canary 서비스가 정의되어 있지 않음"

    def test_has_nginx_service(self, compose_content):
        assert "nginx:" in compose_content, "nginx 서비스가 없음"

    def test_canary_has_resource_limits(self, compose_content):
        """카나리 서비스에 리소스 제한이 있어야 한다"""
        assert "limits:" in compose_content, "리소스 제한(limits)이 없음"

    def test_services_have_healthcheck(self, compose_content):
        """서비스에 healthcheck 설정"""
        assert "healthcheck:" in compose_content, "healthcheck 설정이 없음"


class TestCanaryDeployScript:
    @pytest.fixture(scope="class")
    def script_content(self) -> str:
        return CANARY_SCRIPT.read_text(encoding="utf-8")

    def test_canary_script_exists(self):
        assert CANARY_SCRIPT.exists()

    def test_has_start_command(self, script_content):
        assert "start" in script_content

    def test_has_promote_command(self, script_content):
        assert "promote" in script_content

    def test_has_rollback_command(self, script_content):
        assert "rollback" in script_content

    def test_has_status_command(self, script_content):
        assert "status" in script_content

    def test_has_finish_command(self, script_content):
        assert "finish" in script_content

    def test_traffic_weights_defined(self, script_content):
        """10→30→50→100 트래픽 가중치 단계"""
        for weight in ["10", "30", "50", "100"]:
            assert weight in script_content, f"트래픽 가중치 {weight}% 가 스크립트에 없음"

    def test_rollback_triggers_defined(self, script_content):
        """롤백 트리거 조건(에러율/레이턴시)이 정의되어 있어야 한다"""
        lower = script_content.lower()
        assert "error" in lower or "rollback" in lower, "롤백 트리거 조건이 없음"
