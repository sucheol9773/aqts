"""
post_deploy_smoke.sh 정적 회귀 테스트.

배경 (2026-04-08 regression):
    ① scheduler 가 backend Dockerfile 의 HEALTHCHECK(`curl /api/system/health`)
       를 그대로 상속받아 구조적 unhealthy 상태에 빠졌다.
    ② CD 가 `up -d` 로 재시작해도 compose 의 "no change detected" 최적화로
       새 image digest 가 적용되지 않아 backend/scheduler digest drift 가
       발생했다.
    ③ handle_post_market 의 전일 snapshot fallback 로직이 오염된 snapshot
       을 그대로 사용해 daily report 가 왜곡됐다.

이 세 가지 회귀를 "배포 직후" 단일 관문에서 재검출하는 것이
``scripts/post_deploy_smoke.sh`` 의 목적이다. 본 테스트는 스크립트의
계약 문자열을 정적으로 강제하여 누군가 실수로 어느 한 가드를 제거하거나
조건을 완화했을 때 pytest 가 즉시 실패하도록 한다.

강제 항목:
    * C1: aqts-backend / aqts-scheduler 두 컨테이너에 대한 Running check
    * C2a: backend ↔ scheduler digest 비교
    * C2b: 두 컨테이너의 org.opencontainers.image.revision 라벨이 서버
      git HEAD 와 일치 — 2026-04-09 §4.7/§4.8 회귀(같은 구 digest 로 고정된
      채 C2a 가 위양성 통과한 경로)에 대한 2차 방어선
    * C3: scheduler healthcheck 가 heartbeat 기반인지 확인 + legacy
      curl healthcheck 검출 브랜치 존재
    * C4: /tmp/scheduler.heartbeat mtime 비교 + max age env var
    * C5: /api/system/health HTTP 200 확인
    * exit code: FAIL > 0 → exit 1, else exit 0
    * CD 워크플로(.github/workflows/cd.yml)의 verify step 이 본 smoke
      스크립트를 호출함 (``bash scripts/post_deploy_smoke.sh``)

계약을 약화시키는 방향의 수정(검사 생략, threshold 완화 등)은 반드시
본 테스트를 함께 수정해야 하며, 그 수정은 회고 문서(``docs/operations/
daily-report-regression-2026-04-08.md``) 에도 기록되어야 한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SMOKE_SCRIPT = REPO_ROOT / "scripts" / "post_deploy_smoke.sh"
CD_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "cd.yml"


@pytest.fixture(scope="module")
def smoke_text() -> str:
    assert SMOKE_SCRIPT.exists(), f"smoke 스크립트가 없음: {SMOKE_SCRIPT}"
    return SMOKE_SCRIPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def cd_text() -> str:
    assert CD_WORKFLOW.exists(), f"cd.yml 이 없음: {CD_WORKFLOW}"
    return CD_WORKFLOW.read_text(encoding="utf-8")


class TestSmokeScriptShape:
    """스크립트 본문이 기본 구조를 갖추었는지 확인."""

    def test_is_bash_with_strict_mode(self, smoke_text: str) -> None:
        assert smoke_text.startswith("#!/usr/bin/env bash"), "shebang 이 bash 가 아님"
        assert "set -euo pipefail" in smoke_text, "set -euo pipefail 누락 — 엄격 모드 강제"

    def test_exit_code_reflects_failures(self, smoke_text: str) -> None:
        # FAIL > 0 이면 exit 1, 아니면 exit 0. 조건 분기가 존재해야 함.
        assert "FAIL=0" in smoke_text
        assert "exit 1" in smoke_text
        assert "exit 0" in smoke_text
        assert "FAIL -eq 0" in smoke_text or "FAIL == 0" in smoke_text


class TestContractC1Containers:
    def test_checks_backend_and_scheduler_running(self, smoke_text: str) -> None:
        assert "aqts-backend" in smoke_text
        assert "aqts-scheduler" in smoke_text
        assert "State.Running" in smoke_text, "Running 상태 검사 누락"


class TestContractC2aDigestMatch:
    def test_has_c2a_section_header(self, smoke_text: str) -> None:
        assert "[C2a]" in smoke_text, "C2a 섹션 헤더가 없음 (C2 → C2a 재명명 누락)"

    def test_compares_backend_and_scheduler_image_digest(self, smoke_text: str) -> None:
        # backend / scheduler 의 .Image(digest) 를 각각 inspect 하고 비교해야 함.
        assert smoke_text.count("docker inspect --format='{{.Image}}' aqts-backend") >= 1
        assert smoke_text.count("docker inspect --format='{{.Image}}' aqts-scheduler") >= 1
        assert (
            '"$BACKEND_IMG" == "$SCHEDULER_IMG"' in smoke_text
            or "BACKEND_IMG" in smoke_text
            and "SCHEDULER_IMG" in smoke_text
        ), "digest 비교 식이 없음"


class TestContractC2bRevisionLabel:
    """C2b: OCI revision label ↔ 서버 git HEAD 교차 일치.

    2026-04-09 §4.7/§4.8 회귀에서 두 컨테이너가 동일한 구 digest 로
    고정되어 있어 C2a 는 drift 없음으로 위양성 통과했다. C2b 는
    각 컨테이너의 ``org.opencontainers.image.revision`` 라벨(docker/
    metadata-action 이 CI 빌드 시점 git SHA 로 주입)을 서버 git HEAD 와
    비교하여 "새 이미지가 한 번도 기동되지 않은" 상태를 직접 검출한다.
    """

    def test_has_c2b_section_header(self, smoke_text: str) -> None:
        assert "[C2b]" in smoke_text, "C2b 섹션 헤더 누락"

    def test_reads_revision_label_from_both_containers(self, smoke_text: str) -> None:
        assert "org.opencontainers.image.revision" in smoke_text
        assert "BACKEND_REV" in smoke_text
        assert "SCHEDULER_REV" in smoke_text
        # 두 컨테이너 각각에 대해 라벨 조회가 존재해야 함.
        assert smoke_text.count("org.opencontainers.image.revision") >= 2

    def test_reads_server_git_head(self, smoke_text: str) -> None:
        assert "SERVER_HEAD" in smoke_text
        assert "rev-parse HEAD" in smoke_text
        # AQTS_REPO_DIR 환경변수로 git 경로를 override 가능해야 함.
        assert "AQTS_REPO_DIR" in smoke_text

    def test_has_all_four_failure_branches(self, smoke_text: str) -> None:
        # (1) 서버 HEAD 조회 실패
        assert "서버 git HEAD 조회 실패" in smoke_text
        # (2) 라벨 부재 (CI metadata-action 미구성)
        assert "revision 라벨 부재" in smoke_text
        # (3) 두 컨테이너 간 revision drift
        assert "revision drift" in smoke_text
        # (4) 컨테이너 revision 과 서버 HEAD 불일치 (force-recreate 누락)
        assert "server drift" in smoke_text

    def test_compares_backend_rev_to_server_head(self, smoke_text: str) -> None:
        assert '"$BACKEND_REV" != "$SERVER_HEAD"' in smoke_text
        assert '"$BACKEND_REV" != "$SCHEDULER_REV"' in smoke_text


class TestContractC3HealthcheckConfig:
    def test_checks_scheduler_healthcheck_is_heartbeat_based(self, smoke_text: str) -> None:
        # Healthcheck.Test 를 읽어서 heartbeat 기반인지 확인해야 함.
        assert "Config.Healthcheck.Test" in smoke_text
        assert "scheduler_heartbeat" in smoke_text or "scheduler.heartbeat" in smoke_text

    def test_detects_legacy_curl_healthcheck(self, smoke_text: str) -> None:
        # backend Dockerfile 의 legacy HEALTHCHECK(curl /api/system/health) 가
        # scheduler 에 상속된 경우를 명시적으로 감지해야 함 (2026-04-08 회귀
        # 의 원인 #1).
        assert "curl" in smoke_text and "api/system/health" in smoke_text, "legacy curl healthcheck 감지 브랜치 누락"


class TestContractC4HeartbeatLiveness:
    def test_reads_heartbeat_mtime_from_container(self, smoke_text: str) -> None:
        assert "/tmp/scheduler.heartbeat" in smoke_text
        assert "stat -c %Y" in smoke_text, "mtime 조회가 누락됨"

    def test_enforces_max_age(self, smoke_text: str) -> None:
        assert "SCHEDULER_HEARTBEAT_MAX_AGE_SEC" in smoke_text
        # 기본값은 120초 — 너무 느슨하게 풀리지 않도록 상한 검사.
        assert ":-120}" in smoke_text, "heartbeat max age 기본값이 120s 가 아님"

    def test_rejects_future_mtime(self, smoke_text: str) -> None:
        # AGE < 0 즉 mtime 이 미래인 경우 fail — 시계 불일치 방어.
        assert "AGE < 0" in smoke_text


class TestContractC5HealthEndpoint:
    def test_curls_backend_health_endpoint(self, smoke_text: str) -> None:
        assert "/api/system/health" in smoke_text
        # HTTP code 명시적 200 비교.
        assert '"$HEALTH_HTTP" == "200"' in smoke_text or "HTTP_HTTP" in smoke_text


class TestCDWorkflowIntegration:
    def test_cd_verify_step_invokes_smoke_script(self, cd_text: str) -> None:
        # verify step heredoc 내부에서 smoke 스크립트를 직접 실행해야 함.
        assert (
            "bash scripts/post_deploy_smoke.sh" in cd_text
        ), "cd.yml verify step 이 post_deploy_smoke.sh 를 호출하지 않음"
