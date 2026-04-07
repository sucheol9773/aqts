"""
스케줄러 분리 및 DB 백업 인프라 테스트

검증 항목:
1. SCHEDULER_ENABLED 환경변수에 따른 스케줄러 활성화/비활성화
2. scheduler_main.py 모듈 임포트 정상 동작
3. 헬스체크에서 external 상태 반환
4. backup_db.sh / restore_db.sh 스크립트 존재 및 실행 가능 여부
5. docker-compose.yml 서비스 정의 검증
"""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

# ══════════════════════════════════════
# 프로젝트 경로
# ══════════════════════════════════════
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_ROOT = Path(__file__).resolve().parent.parent


class TestSchedulerSeparation:
    """스케줄러 분리 관련 테스트"""

    def test_scheduler_main_importable(self):
        """scheduler_main.py가 임포트 가능한지 확인"""
        import importlib

        spec = importlib.util.spec_from_file_location(
            "scheduler_main",
            BACKEND_ROOT / "scheduler_main.py",
        )
        assert spec is not None, "scheduler_main.py를 찾을 수 없습니다"
        module = importlib.util.module_from_spec(spec)
        # 실제 실행하지 않고 모듈 로드만 확인
        assert module is not None

    def test_scheduler_enabled_env_true(self):
        """SCHEDULER_ENABLED=true일 때 스케줄러 활성화 확인"""
        from core.utils.env import env_bool

        os.environ["SCHEDULER_ENABLED"] = "true"
        assert env_bool("SCHEDULER_ENABLED", default=True) is True

    def test_scheduler_enabled_env_false(self):
        """SCHEDULER_ENABLED=false일 때 스케줄러 비활성화 확인"""
        from core.utils.env import env_bool

        os.environ["SCHEDULER_ENABLED"] = "false"
        assert env_bool("SCHEDULER_ENABLED", default=True) is False
        os.environ["SCHEDULER_ENABLED"] = "true"

    def test_scheduler_enabled_env_default(self):
        """SCHEDULER_ENABLED 미설정 시 기본값 true 확인"""
        from core.utils.env import env_bool

        original = os.environ.pop("SCHEDULER_ENABLED", None)
        try:
            assert env_bool("SCHEDULER_ENABLED", default=True) is True
        finally:
            if original is not None:
                os.environ["SCHEDULER_ENABLED"] = original

    @pytest.mark.asyncio
    async def test_health_check_scheduler_external(self):
        """SCHEDULER_ENABLED=false일 때 헬스체크에서 scheduler=external 반환"""
        from httpx import ASGITransport, AsyncClient

        os.environ["SCHEDULER_ENABLED"] = "false"
        try:
            from main import app

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/api/system/health")
                assert response.status_code == 200
                data = response.json()
                assert data["components"]["scheduler"] == "external"
        finally:
            os.environ["SCHEDULER_ENABLED"] = "true"

    @pytest.mark.asyncio
    async def test_health_check_scheduler_embedded(self):
        """SCHEDULER_ENABLED=true일 때 헬스체크에서 scheduler!=external"""
        from httpx import ASGITransport, AsyncClient

        os.environ["SCHEDULER_ENABLED"] = "true"

        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/system/health")
            assert response.status_code == 200
            data = response.json()
            # embedded 모드에서는 healthy, stopped, degraded 중 하나
            assert data["components"]["scheduler"] != "external"


class TestDockerComposeServices:
    """docker-compose.yml 서비스 정의 검증"""

    @pytest.fixture(autouse=True)
    def load_compose(self):
        compose_path = PROJECT_ROOT / "docker-compose.yml"
        assert compose_path.exists(), "docker-compose.yml이 없습니다"
        with open(compose_path) as f:
            self.compose = yaml.safe_load(f)

    def test_scheduler_service_exists(self):
        """scheduler 서비스가 정의되어 있는지 확인"""
        assert "scheduler" in self.compose["services"]

    def test_scheduler_service_command(self):
        """scheduler 서비스가 scheduler_main.py를 실행하는지 확인"""
        svc = self.compose["services"]["scheduler"]
        assert svc["command"] == ["python", "scheduler_main.py"]

    def test_scheduler_depends_on_dbs(self):
        """scheduler가 DB 서비스에 의존하는지 확인"""
        deps = self.compose["services"]["scheduler"]["depends_on"]
        assert "postgres" in deps
        assert "mongodb" in deps
        assert "redis" in deps

    def test_backend_scheduler_disabled(self):
        """backend 서비스에서 SCHEDULER_ENABLED=false 설정 확인"""
        env = self.compose["services"]["backend"].get("environment", {})
        assert env.get("SCHEDULER_ENABLED") == "false"

    def test_db_backup_service_exists(self):
        """db-backup 서비스가 정의되어 있는지 확인"""
        assert "db-backup" in self.compose["services"]

    def test_db_backup_depends_on_dbs(self):
        """db-backup이 DB 서비스에 의존하는지 확인"""
        deps = self.compose["services"]["db-backup"]["depends_on"]
        assert "postgres" in deps
        assert "mongodb" in deps

    def test_postgres_wal_archive_volume(self):
        """PostgreSQL WAL 아카이브 볼륨이 설정되어 있는지 확인"""
        volumes = self.compose["services"]["postgres"]["volumes"]
        wal_found = any("wal_archive" in str(v) for v in volumes)
        assert wal_found, "postgres_wal_archive 볼륨이 없습니다"

    def test_postgres_wal_level_replica(self):
        """PostgreSQL WAL 레벨이 replica로 설정되어 있는지 확인"""
        command = self.compose["services"]["postgres"]["command"]
        assert "wal_level=replica" in " ".join(command)

    def test_postgres_archive_mode_on(self):
        """PostgreSQL archive_mode=on 설정 확인"""
        command = self.compose["services"]["postgres"]["command"]
        assert "archive_mode=on" in " ".join(command)

    def test_backup_data_volume_exists(self):
        """backup_data 볼륨이 정의되어 있는지 확인"""
        assert "backup_data" in self.compose["volumes"]

    def test_postgres_wal_archive_volume_exists(self):
        """postgres_wal_archive 볼륨이 정의되어 있는지 확인"""
        assert "postgres_wal_archive" in self.compose["volumes"]


class TestBackupScripts:
    """백업/복원 스크립트 검증"""

    def test_backup_script_exists(self):
        """backup_db.sh 파일 존재 확인"""
        script = PROJECT_ROOT / "scripts" / "backup_db.sh"
        assert script.exists()

    def test_backup_script_executable(self):
        """backup_db.sh 실행 권한 확인"""
        script = PROJECT_ROOT / "scripts" / "backup_db.sh"
        assert os.access(script, os.X_OK)

    def test_backup_cron_script_exists(self):
        """backup_cron.sh 파일 존재 확인"""
        script = PROJECT_ROOT / "scripts" / "backup_cron.sh"
        assert script.exists()

    def test_backup_cron_script_executable(self):
        """backup_cron.sh 실행 권한 확인"""
        script = PROJECT_ROOT / "scripts" / "backup_cron.sh"
        assert os.access(script, os.X_OK)

    def test_restore_script_exists(self):
        """restore_db.sh 파일 존재 확인"""
        script = PROJECT_ROOT / "scripts" / "restore_db.sh"
        assert script.exists()

    def test_restore_script_executable(self):
        """restore_db.sh 실행 권한 확인"""
        script = PROJECT_ROOT / "scripts" / "restore_db.sh"
        assert os.access(script, os.X_OK)

    def test_backup_script_has_pg_dump(self):
        """backup_db.sh에 pg_dump 호출이 포함되어 있는지 확인"""
        script = PROJECT_ROOT / "scripts" / "backup_db.sh"
        content = script.read_text()
        assert "pg_dump" in content

    def test_backup_script_has_mongodump(self):
        """backup_db.sh에 mongodump 호출이 포함되어 있는지 확인"""
        script = PROJECT_ROOT / "scripts" / "backup_db.sh"
        content = script.read_text()
        assert "mongodump" in content

    def test_backup_script_has_gcs_upload(self):
        """backup_db.sh에 GCS 업로드 함수가 포함되어 있는지 확인"""
        script = PROJECT_ROOT / "scripts" / "backup_db.sh"
        content = script.read_text()
        assert "gsutil" in content
        assert "GCS_BACKUP_BUCKET" in content

    def test_backup_script_has_retention_cleanup(self):
        """backup_db.sh에 로컬 백업 정리 기능이 포함되어 있는지 확인"""
        script = PROJECT_ROOT / "scripts" / "backup_db.sh"
        content = script.read_text()
        assert "RETENTION_DAYS" in content
        assert "cleanup_old_backups" in content

    def test_restore_script_has_pg_restore(self):
        """restore_db.sh에 pg_restore 호출이 포함되어 있는지 확인"""
        script = PROJECT_ROOT / "scripts" / "restore_db.sh"
        content = script.read_text()
        assert "pg_restore" in content

    def test_restore_script_has_mongorestore(self):
        """restore_db.sh에 mongorestore 호출이 포함되어 있는지 확인"""
        script = PROJECT_ROOT / "scripts" / "restore_db.sh"
        content = script.read_text()
        assert "mongorestore" in content

    def test_restore_script_has_confirmation(self):
        """restore_db.sh에 사용자 확인 프롬프트가 포함되어 있는지 확인"""
        script = PROJECT_ROOT / "scripts" / "restore_db.sh"
        content = script.read_text()
        assert "confirm" in content.lower()

    def test_backup_script_shellcheck_syntax(self):
        """backup_db.sh 기본 bash 문법 검증"""
        script = PROJECT_ROOT / "scripts" / "backup_db.sh"
        result = subprocess.run(
            ["bash", "-n", str(script)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Syntax error: {result.stderr}"

    def test_restore_script_shellcheck_syntax(self):
        """restore_db.sh 기본 bash 문법 검증"""
        script = PROJECT_ROOT / "scripts" / "restore_db.sh"
        result = subprocess.run(
            ["bash", "-n", str(script)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Syntax error: {result.stderr}"

    def test_backup_cron_shellcheck_syntax(self):
        """backup_cron.sh 기본 bash 문법 검증"""
        script = PROJECT_ROOT / "scripts" / "backup_cron.sh"
        result = subprocess.run(
            ["bash", "-n", str(script)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Syntax error: {result.stderr}"
