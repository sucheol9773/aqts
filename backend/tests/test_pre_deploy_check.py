"""
pre_deploy_check.sh 스크립트 구조 검증 테스트.

배포 전 자동 검증 스크립트가 7단계를 모두 포함하고,
핵심 검증 명령이 누락되지 않았는지 정적 분석으로 검증한다.
"""

from pathlib import Path

import pytest

SCRIPT_FILE = Path(__file__).resolve().parents[2] / "scripts" / "pre_deploy_check.sh"


@pytest.fixture(scope="module")
def script_content() -> str:
    return SCRIPT_FILE.read_text(encoding="utf-8")


class TestPreDeployCheckStructure:
    def test_script_exists(self):
        assert SCRIPT_FILE.exists(), f"{SCRIPT_FILE} 가 존재해야 한다."

    def test_script_is_executable_shell(self, script_content):
        """셸 스크립트 shebang 확인"""
        first_line = script_content.strip().split("\n")[0]
        assert first_line.startswith("#!"), "shebang (#!) 이 없음. 실행 가능한 셸 스크립트여야 한다."
        assert "bash" in first_line or "sh" in first_line

    def test_uses_set_e_for_fail_fast(self, script_content):
        """set -e 로 실패 시 즉시 중단하는지"""
        assert (
            "set -e" in script_content or "set -eo" in script_content
        ), "set -e 가 없음. 스크립트 실패 시 즉시 중단해야 한다."


class TestPreDeployCheckStages:
    """7단계 검증이 모두 포함되어 있는지"""

    def test_git_status_check(self, script_content):
        """Stage 1: Git 상태 확인"""
        assert "git" in script_content.lower(), "git 관련 검증이 없음"

    def test_lint_check(self, script_content):
        """Stage 2: 린트/포맷 검증"""
        assert "ruff" in script_content or "lint" in script_content.lower(), "ruff 또는 lint 검증이 없음"
        assert "black" in script_content, "black 포맷 검증이 없음"

    def test_test_execution(self, script_content):
        """Stage 3: 테스트 실행"""
        assert "pytest" in script_content, "pytest 실행이 없음"

    def test_doc_sync_check(self, script_content):
        """Stage 4: 문서 동기화 확인"""
        assert "doc" in script_content.lower() or "check_doc_sync" in script_content, "문서 동기화 검증이 없음"

    def test_docker_validation(self, script_content):
        """Stage 5: Docker 빌드 검증"""
        assert "docker" in script_content.lower(), "Docker 검증이 없음"

    def test_env_vars_check(self, script_content):
        """Stage 6: 환경변수 검증"""
        # 필수 환경변수 중 하나 이상 체크하는지
        env_vars = ["DB_PASSWORD", "MONGO_PASSWORD", "REDIS_PASSWORD"]
        found = any(var in script_content for var in env_vars)
        assert found, f"필수 환경변수 검증이 없음. " f"기대: {env_vars} 중 하나 이상"

    def test_release_gates_check(self, script_content):
        """Stage 7: 릴리즈 게이트 검증"""
        assert "gate" in script_content.lower() or "release" in script_content.lower(), "릴리즈 게이트 검증이 없음"
