"""
CD 파이프라인 SSH 하드닝 검증 테스트.

.github/workflows/cd.yml 에서 SSH 접속 시 StrictHostKeyChecking 을 활용하고
known_hosts 를 올바르게 설정하는지 정적 분석으로 검증한다.
"""

from pathlib import Path

import pytest
import yaml

CD_WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "cd.yml"


@pytest.fixture(scope="module")
def cd_content() -> str:
    return CD_WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def cd_yaml() -> dict:
    with CD_WORKFLOW.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class TestSSHHardeningStructure:
    def test_cd_workflow_exists(self):
        assert CD_WORKFLOW.exists(), f"{CD_WORKFLOW} 가 존재해야 한다."

    def test_no_strict_host_key_checking_no(self, cd_content):
        """StrictHostKeyChecking no 가 없어야 한다 (MITM 방지)"""
        # 대소문자 무시, 공백 변형 고려
        lower = cd_content.lower()
        assert "stricthostkeychecking no" not in lower, (
            "cd.yml 에 'StrictHostKeyChecking no' 가 존재. " "known_hosts 기반 호스트 키 검증을 사용해야 한다."
        )
        assert "stricthostkeychecking=no" not in lower, "cd.yml 에 'StrictHostKeyChecking=no' 가 존재."

    def test_gcp_host_key_secret_referenced(self, cd_content):
        """GCP_HOST_KEY 시크릿이 참조되어야 한다"""
        assert "GCP_HOST_KEY" in cd_content, (
            "cd.yml 에 GCP_HOST_KEY 참조가 없음. " "SSH 호스트 키 검증을 위해 필요하다."
        )

    def test_known_hosts_file_configured(self, cd_content):
        """known_hosts 파일이 설정되어야 한다"""
        assert "known_hosts" in cd_content, "cd.yml 에 known_hosts 설정이 없음."

    def test_ssh_key_has_restricted_permissions(self, cd_content):
        """SSH 키 파일에 chmod 600 이 적용되어야 한다"""
        assert "chmod 600" in cd_content or "chmod 0600" in cd_content, (
            "cd.yml 에 SSH 키 chmod 600 이 없음. " "SSH 키 파일 권한을 제한해야 한다."
        )

    def test_ssh_private_key_secret_referenced(self, cd_content):
        """SSH_PRIVATE_KEY 시크릿이 참조되어야 한다"""
        assert "SSH_PRIVATE_KEY" in cd_content, "cd.yml 에 SSH_PRIVATE_KEY 참조가 없음."


class TestSSHHardeningSecrets:
    """cd.yml 에 필요한 시크릿이 정의되어 있는지 검증"""

    def test_secrets_section_includes_ssh_keys(self, cd_yaml):
        """workflow 에서 SSH 관련 시크릿을 사용하는지 확인"""
        content_str = yaml.dump(cd_yaml)
        assert "SSH_PRIVATE_KEY" in content_str
        assert "GCP_HOST_KEY" in content_str
