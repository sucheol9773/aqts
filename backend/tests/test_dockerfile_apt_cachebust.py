"""
Dockerfile apt 레이어 cache-bust 회귀 테스트.

배경 (CVE-2026-31790, 2026-04-09):
    grype 가 빌드된 이미지에서 libssl3/openssl 3.0.18-1~deb12u2 (High) 를 검출.
    실제로는 Dockerfile 에 `apt-get upgrade -y` 가 이미 있었으나 GHA buildx
    cache (`cache-from: type=gha`) 가 apt upgrade RUN 레이어를 재사용하면서
    debian 보안 피드(deb12u3) 를 흡수하지 못한 것이 원인이었다.

Fix:
    Dockerfile 두 stage(builder, runtime) 의 apt RUN 레이어 앞에
    ``ARG APT_UPGRADE_DATE`` 를 선언하고, CI(ci.yml) 에서 오늘 날짜(UTC)
    를 build-arg 로 주입한다. date 값이 바뀌면 apt 레이어 해시가 달라져
    일(day)-단위로 cache-bust 되지만, pip/torch 등 상위 레이어 캐시는
    유지되어 빌드 시간 impact 가 거의 없다.

본 테스트는 다음을 정적으로 강제한다:
    1. Dockerfile 의 builder/runtime stage 양쪽에 ``ARG APT_UPGRADE_DATE``
       선언이 있고, 해당 선언이 `apt-get upgrade` RUN 블록 직전에 위치한다.
    2. ci.yml 의 backend build step 이 ``APT_UPGRADE_DATE`` build-arg 를
       오늘 날짜 출력으로 주입한다.
    3. Dockerfile 의 apt RUN 블록이 `apt-upgrade-date=${APT_UPGRADE_DATE}`
       참조를 포함하여 ARG 값이 레이어 해시에 실제로 반영된다.

이 테스트가 깨지면 buildx cache 가 다시 apt 레이어를 재사용하면서 debian
보안 피드를 놓치기 시작한다는 신호이므로, 수정 없이 통과시키지 말 것.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "backend" / "Dockerfile"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"


@pytest.fixture(scope="module")
def dockerfile_text() -> str:
    assert DOCKERFILE.exists(), f"Dockerfile 이 없음: {DOCKERFILE}"
    return DOCKERFILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ci_text() -> str:
    assert CI_WORKFLOW.exists(), f"ci.yml 이 없음: {CI_WORKFLOW}"
    return CI_WORKFLOW.read_text(encoding="utf-8")


class TestDockerfileAptCacheBust:
    """Dockerfile 양쪽 stage 의 ARG APT_UPGRADE_DATE 선언 + RUN 참조 검증."""

    def test_builder_stage_declares_apt_upgrade_date_arg(self, dockerfile_text: str) -> None:
        # builder stage 진입 이후, runtime stage(두 번째 FROM) 진입 이전 구간에
        # ARG APT_UPGRADE_DATE 가 존재해야 한다.
        lines = dockerfile_text.splitlines()
        from_indices = [i for i, line in enumerate(lines) if line.strip().startswith("FROM ")]
        assert len(from_indices) >= 2, "builder + runtime 2 stage 가 필요"
        builder_segment = "\n".join(lines[from_indices[0] : from_indices[1]])
        assert re.search(
            r"^ARG\s+APT_UPGRADE_DATE(\s*=|\s*$)", builder_segment, re.MULTILINE
        ), "builder stage 에 ARG APT_UPGRADE_DATE 선언이 없음"

    def test_runtime_stage_declares_apt_upgrade_date_arg(self, dockerfile_text: str) -> None:
        lines = dockerfile_text.splitlines()
        from_indices = [i for i, line in enumerate(lines) if line.strip().startswith("FROM ")]
        runtime_segment = "\n".join(lines[from_indices[1] :])
        assert re.search(
            r"^ARG\s+APT_UPGRADE_DATE(\s*=|\s*$)", runtime_segment, re.MULTILINE
        ), "runtime stage 에 ARG APT_UPGRADE_DATE 선언이 없음"

    def test_apt_run_blocks_reference_cachebust_var(self, dockerfile_text: str) -> None:
        # ARG 선언만 있고 RUN 에서 참조하지 않으면 레이어 해시에 반영되지 않아
        # cache-bust 효과가 사라진다. 두 stage 모두 RUN 내부에서 변수 참조가
        # 있어야 한다 — ``echo "apt-upgrade-date=${APT_UPGRADE_DATE}"`` 패턴.
        occurrences = re.findall(r"apt-upgrade-date=\$\{?APT_UPGRADE_DATE\}?", dockerfile_text)
        assert len(occurrences) >= 2, f"apt RUN 에서 APT_UPGRADE_DATE 참조가 2회 미만: {len(occurrences)}"

    def test_apt_upgrade_still_present_in_both_stages(self, dockerfile_text: str) -> None:
        # cache-bust 만 있고 실제 apt upgrade 가 빠지면 의미 없음.
        upgrade_count = len(re.findall(r"apt-get upgrade -y", dockerfile_text))
        assert upgrade_count >= 2, f"apt-get upgrade -y 가 2회 미만: {upgrade_count}"


class TestCIInjectsAptUpgradeDate:
    """ci.yml 의 build step 이 APT_UPGRADE_DATE build-arg 를 주입하는지 검증."""

    def test_ci_has_apt_date_step(self, ci_text: str) -> None:
        # "Compute apt upgrade date" step 이 존재하고 GITHUB_OUTPUT 에 today 를
        # 기록해야 한다.
        assert "Compute apt upgrade date" in ci_text, "ci.yml 에 apt upgrade date 계산 step 이 없음"
        assert re.search(
            r'today=\$\(date\s+-u\s+\+%Y-%m-%d\)"?\s*>>\s*"?\$GITHUB_OUTPUT"?',
            ci_text,
        ), "apt_date step 이 today=YYYY-MM-DD 를 GITHUB_OUTPUT 에 기록하지 않음"

    def test_ci_passes_build_arg_to_backend_image(self, ci_text: str) -> None:
        # docker/build-push-action step 에 build-args 로 APT_UPGRADE_DATE 가
        # steps.apt_date.outputs.today 를 참조하며 전달되어야 한다.
        assert re.search(
            r"build-args:\s*\|\s*\n\s*APT_UPGRADE_DATE=\$\{\{\s*steps\.apt_date\.outputs\.today\s*\}\}",
            ci_text,
        ), "ci.yml build-args 에 APT_UPGRADE_DATE 주입이 없음"
