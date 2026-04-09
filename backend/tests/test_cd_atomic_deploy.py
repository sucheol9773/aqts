"""
CD 원자적 배포(atomic deploy) 회귀 테스트.

2026-04-08 POST_MARKET 회귀의 근본 원인은 scheduler 컨테이너가 backend 와
다른 이미지 digest 로 계속 실행된 것이었다 (image drift). 재발 방지를 위해
`.github/workflows/cd.yml` 의 deploy/rollback/verify 경로가 다음 항목을
모두 포함하는지 정적으로 어서트한다:

1. EXPECTED_IMAGE_ID 를 `docker image inspect` 로 잠가둔다.
2. `docker compose up -d --force-recreate` 로 backend/scheduler 를 강제
   recreate 하여 compose 의 "변경 없음" 최적화를 무력화한다.
3. 배포 직후 `docker inspect --format '{{.Image}}'` 로 backend/scheduler 의
   실행 중 image digest 를 EXPECTED_IMAGE_ID 와 비교한다.
4. 위 1-3 을 롤백 경로와 verify 단계에서도 동일하게 강제한다.

워크플로 파일 실제 실행은 단위테스트로 재현 불가능하므로, 필수 문자열
존재를 AST 수준에서 확인하는 회귀 테스트로 wiring 을 고정한다. 정의 ≠
적용 원칙(CLAUDE.md RBAC Wiring Rule)을 CD 도메인에 확장한 것이다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CD_YML = REPO_ROOT / ".github" / "workflows" / "cd.yml"


@pytest.fixture(scope="module")
def cd_content() -> str:
    assert CD_YML.exists(), f"cd.yml not found at {CD_YML}"
    return CD_YML.read_text(encoding="utf-8")


class TestDeployAtomicity:
    def test_expected_image_id_captured_after_pull(self, cd_content: str) -> None:
        """pull 직후 EXPECTED_IMAGE_ID 를 docker image inspect 로 잠가둔다."""
        assert 'EXPECTED_IMAGE_ID=$(docker image inspect "${IMAGE_REF}"' in cd_content

    def test_force_recreate_used_for_backend_and_scheduler(self, cd_content: str) -> None:
        """compose 의 변경 감지 최적화를 무력화하여 두 컨테이너 원자적 교체."""
        assert "--force-recreate --no-deps backend scheduler" in cd_content

    def test_backend_image_id_asserted_against_expected(self, cd_content: str) -> None:
        """backend 의 실행 중 digest 가 EXPECTED_IMAGE_ID 와 일치해야 한다."""
        assert "BACKEND_IMAGE_ID=$(docker inspect --format '{{.Image}}' aqts-backend" in cd_content
        assert '"${BACKEND_IMAGE_ID}" != "${EXPECTED_IMAGE_ID}"' in cd_content

    def test_scheduler_image_id_asserted_against_expected(self, cd_content: str) -> None:
        """scheduler 의 실행 중 digest 가 EXPECTED_IMAGE_ID 와 일치해야 한다."""
        assert "SCHEDULER_IMAGE_ID=$(docker inspect --format '{{.Image}}' aqts-scheduler" in cd_content
        assert '"${SCHEDULER_IMAGE_ID}" != "${EXPECTED_IMAGE_ID}"' in cd_content

    def test_deploy_fails_on_digest_mismatch(self, cd_content: str) -> None:
        """digest 불일치 시 exit 1 로 배포 실패 → rollback 경로 진입."""
        # Step 5e 블록 안에 exit 1 이 최소 두 번 있어야 한다 (backend + scheduler 각각)
        marker = "Step 5e: Assert atomic digest"
        assert marker in cd_content
        step_start = cd_content.index(marker)
        # Step 6 시작 지점까지 범위 제한
        step_end = cd_content.index("Step 6: Wait for health check", step_start)
        block = cd_content[step_start:step_end]
        assert block.count("exit 1") >= 2


class TestRollbackAtomicity:
    def test_rollback_captures_expected_image_id(self, cd_content: str) -> None:
        """rollback 경로도 동일하게 EXPECTED_IMAGE_ID 를 잠가둔다."""
        # 롤백 스크립트 블록을 범위로 제한
        rb_start = cd_content.index("ROLLBACK_SCRIPT")
        rb_block = cd_content[rb_start:]
        assert 'EXPECTED_IMAGE_ID=$(docker image inspect "${IMAGE_REF}"' in rb_block

    def test_rollback_uses_force_recreate(self, cd_content: str) -> None:
        """rollback 에서도 --force-recreate 로 원자적 교체."""
        rb_start = cd_content.index("ROLLBACK_SCRIPT")
        rb_block = cd_content[rb_start:]
        assert "--force-recreate --no-deps backend scheduler" in rb_block

    def test_rollback_asserts_both_digests(self, cd_content: str) -> None:
        """rollback 후 backend/scheduler digest 를 모두 어서트."""
        rb_start = cd_content.index("ROLLBACK_SCRIPT")
        rb_block = cd_content[rb_start:]
        assert "Rollback: assert atomic digest" in rb_block
        assert "BACKEND_IMAGE_ID=$(docker inspect --format" in rb_block
        assert "SCHEDULER_IMAGE_ID=$(docker inspect --format" in rb_block


class TestVerifyCrossCheck:
    def test_verify_step_cross_checks_backend_vs_scheduler(self, cd_content: str) -> None:
        """verify 단계는 Step 5e 와 독립적으로 두 컨테이너 digest 일치 재확인."""
        vf_start = cd_content.index("VERIFY_SCRIPT")
        vf_block = cd_content[vf_start:]
        assert "Atomic Digest Cross-Check" in vf_block
        # verify 단계는 GA heredoc 내부이므로 $ 가 \$ 로 이스케이프됨
        assert r"BACKEND_IMAGE_ID=\$(docker inspect --format" in vf_block
        assert r"SCHEDULER_IMAGE_ID=\$(docker inspect --format" in vf_block
        assert r'"\${BACKEND_IMAGE_ID}" != "\${SCHEDULER_IMAGE_ID}"' in vf_block

    def test_verify_fails_on_missing_container(self, cd_content: str) -> None:
        """backend 또는 scheduler 컨테이너가 없으면 verify 실패."""
        vf_start = cd_content.index("VERIFY_SCRIPT")
        vf_block = cd_content[vf_start:]
        assert r'-z "\${BACKEND_IMAGE_ID}"' in vf_block
        assert r'-z "\${SCHEDULER_IMAGE_ID}"' in vf_block


class TestComposeImageAlignment:
    """docker-compose.yml 에서 backend/scheduler 가 동일 이미지 참조를 써야만
    atomic deploy 자체가 성립한다. 한쪽이 다른 이미지 태그를 참조하면
    --force-recreate 와 digest 어서트로도 drift 를 잡을 수 없다."""

    def test_backend_and_scheduler_share_image_reference(self) -> None:
        compose_path = REPO_ROOT / "docker-compose.yml"
        content = compose_path.read_text(encoding="utf-8")
        expected_image = "ghcr.io/${IMAGE_NAMESPACE:?IMAGE_NAMESPACE is required}/aqts-backend:${IMAGE_TAG:-latest}"
        # 두 서비스 모두 이 라인을 한 번씩 써야 한다 → 총 2회 등장
        assert content.count(expected_image) == 2, (
            f"backend/scheduler 가 동일 이미지 참조를 공유하지 않는다. "
            f"expected 2 occurrences, found {content.count(expected_image)}"
        )
