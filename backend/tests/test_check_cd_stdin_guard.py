"""CD stdin 소진 방지 정적 가드 회귀 테스트.

검증 범위:
    1. 2026-04-09 회귀 사례 두 건(``docker exec -i`` / ``-T`` 없는
       ``docker compose run``) 이 heredoc 내부에 있으면 반드시 검출된다.
    2. heredoc 컨텍스트 밖의 동일 패턴은 오탐하지 않는다 (일반 CI 단계,
       로컬 shell 스크립트 포함).
    3. ``kubectl exec -i``, ``-T`` 없는 전경 ``docker run`` 도 heredoc 내부에서
       검출된다.
    4. 수정된 올바른 패턴(``-T`` + ``</dev/null``, ``docker exec ... </dev/null``,
       ``docker run -d``) 은 통과한다.

본 테스트는 ``scripts/check_cd_stdin_guard.py`` 의 규칙 변경이
과거 회귀 상황을 더 이상 잡지 못하게 되는 경우(false negative)와,
정상 코드가 갑자기 가드에 걸리는 경우(false positive) 모두를 차단한다.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# ─────────────────────────────────────────────────────────────────────────
# Import helper: ``scripts/`` 는 패키지가 아니므로 파일 경로로 직접 import.
# ─────────────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[2]
GUARD_PATH = REPO_ROOT / "scripts" / "check_cd_stdin_guard.py"


def _load_guard():
    spec = importlib.util.spec_from_file_location("check_cd_stdin_guard", GUARD_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GUARD = _load_guard()


def _scan(text: str) -> list[tuple[int, str]]:
    """스크립트의 ``scan_file`` 을 호출해 violation 목록을 반환."""
    lines = text.splitlines(keepends=True)
    logical = GUARD.join_continuations(lines)
    return GUARD.scan_file(logical)


# ═════════════════════════════════════════════════════════════════════════
# 회귀 사례 1: docker exec -i (051c453/6500bcb/a48c4c8)
# ═════════════════════════════════════════════════════════════════════════
class TestDockerExecDashI:
    def test_detects_inside_ssh_heredoc(self):
        script = (
            "ssh -T host bash -s << 'DEPLOY'\n"
            "set -e\n"
            "HAS_VER=$(docker exec -i aqts-postgres bash -c 'psql ...')\n"
            "echo next\n"
            "DEPLOY\n"
        )
        violations = _scan(script)
        assert len(violations) == 1
        assert "docker exec -i" in violations[0][1]

    def test_detects_long_flag(self):
        script = "ssh host bash -s <<EOF\n" "docker exec --interactive aqts-backend ls\n" "EOF\n"
        violations = _scan(script)
        assert len(violations) == 1
        assert "docker exec" in violations[0][1]

    def test_passes_with_redirect_fix(self):
        """§4.7 fix: `-i` 제거 + `</dev/null` 격리."""
        script = (
            "ssh -T host bash -s << 'DEPLOY'\n"
            "HAS_VER=$(docker exec aqts-postgres bash -c 'psql ...' </dev/null)\n"
            "DEPLOY\n"
        )
        assert _scan(script) == []

    def test_no_false_positive_outside_heredoc(self):
        """일반 GitHub Actions 단계의 ``run: |`` 블록은 heredoc 이 아님."""
        script = (
            "jobs:\n"
            "  test:\n"
            "    steps:\n"
            "      - name: probe\n"
            "        run: |\n"
            "          docker exec -i some-container ls\n"
        )
        # YAML ``run: |`` 는 스크립트 검사 대상이 아니라 heredoc 컨텍스트가
        # 없기 때문에 본 가드는 flagging 하지 않는다. heredoc 바깥의
        # `docker exec -i` 는 stdin 소진 위험이 없으므로 오탐하지 않아야 한다.
        assert _scan(script) == []


# ═════════════════════════════════════════════════════════════════════════
# 회귀 사례 2: -T 없는 docker compose run (8fcd6c6)
# ═════════════════════════════════════════════════════════════════════════
class TestDockerComposeRunWithoutT:
    def test_detects_inside_heredoc(self):
        script = (
            "ssh -T host bash -s << 'DEPLOY'\n"
            "docker compose -f docker-compose.yml run --rm backend alembic upgrade head\n"
            "echo next\n"
            "DEPLOY\n"
        )
        violations = _scan(script)
        assert len(violations) == 1
        assert "docker compose run" in violations[0][1]

    def test_detects_legacy_hyphen_form(self):
        script = "ssh host bash -s <<EOF\n" "docker-compose run --rm backend alembic upgrade head\n" "EOF\n"
        violations = _scan(script)
        assert len(violations) == 1

    def test_detects_line_continuation(self):
        """백슬래시로 이어진 논리 라인도 한 줄로 취급."""
        script = (
            "ssh -T host bash -s << 'DEPLOY'\n"
            "docker compose -f docker-compose.yml run --rm backend \\\n"
            "  alembic -c alembic.ini upgrade head\n"
            "DEPLOY\n"
        )
        violations = _scan(script)
        assert len(violations) == 1

    def test_passes_with_dash_T_and_redirect(self):
        """§4.8 fix: `-T` + `</dev/null`."""
        script = (
            "ssh -T host bash -s << 'DEPLOY'\n"
            "docker compose -f docker-compose.yml run --rm -T backend \\\n"
            "  alembic -c alembic.ini upgrade head </dev/null\n"
            "DEPLOY\n"
        )
        assert _scan(script) == []

    def test_no_false_positive_outside_heredoc(self):
        """일반 쉘 스크립트의 독립 `docker compose run` 은 위험 없음."""
        script = "#!/usr/bin/env bash\n" "set -e\n" "docker compose run --rm backend pytest tests/\n"
        assert _scan(script) == []


# ═════════════════════════════════════════════════════════════════════════
# 보조 규칙: kubectl exec -i, -T 없는 docker run
# ═════════════════════════════════════════════════════════════════════════
class TestAuxiliaryRules:
    def test_kubectl_exec_i_inside_heredoc(self):
        script = "ssh host bash -s << 'K'\n" "kubectl exec -i my-pod -- sh -c 'ls'\n" "K\n"
        violations = _scan(script)
        assert len(violations) == 1
        assert "kubectl exec" in violations[0][1]

    def test_docker_run_foreground_inside_heredoc(self):
        script = "ssh host bash -s <<END\n" "docker run --rm alpine:3.19 echo hello\n" "END\n"
        violations = _scan(script)
        assert len(violations) == 1
        assert "docker run" in violations[0][1]

    def test_docker_run_detached_is_allowed_inside_heredoc(self):
        """-d 는 백그라운드 실행으로 stdin 을 소비하지 않는다."""
        script = "ssh host bash -s <<END\n" "docker run -d --name x alpine:3.19 tail -f /dev/null\n" "END\n"
        assert _scan(script) == []

    def test_docker_run_with_dash_T_is_allowed(self):
        script = "ssh host bash -s <<END\n" "docker run --rm -T alpine:3.19 echo hello </dev/null\n" "END\n"
        assert _scan(script) == []

    def test_no_false_positive_local_script_docker_run(self):
        """pre_deploy_check.sh 의 `docker run --rm whoami` 같은 패턴."""
        script = "#!/usr/bin/env bash\n" "USER_CHECK=$(docker run --rm aqts-backend:pre-check whoami)\n"
        assert _scan(script) == []


# ═════════════════════════════════════════════════════════════════════════
# heredoc 추적 정확도
# ═════════════════════════════════════════════════════════════════════════
class TestHeredocTracking:
    def test_tag_must_match_to_exit(self):
        """heredoc 종료는 태그가 단독으로 등장하는 줄에서만."""
        script = (
            "ssh host bash -s << 'DEPLOY'\n"
            "docker exec -i x ls\n"
            "# DEPLOY 라는 문자열은 주석 안에 있지만 종료가 아니다\n"
            "echo DEPLOY_STILL_INSIDE\n"
            "DEPLOY\n"
            "# heredoc 바깥 — 여기 docker exec -i 는 flag 되지 않는다\n"
            "docker exec -i outside ls\n"
        )
        violations = _scan(script)
        assert len(violations) == 1

    def test_non_subshell_heredoc_is_ignored(self):
        """``cat << EOF`` 같은 heredoc 은 bash 서브쉘이 아니므로 무시."""
        script = (
            "cat > /tmp/config << 'CFG'\n" "docker exec -i x ls\n" "docker compose run --rm y alembic upgrade\n" "CFG\n"
        )
        assert _scan(script) == []

    def test_comment_with_pattern_is_ignored(self):
        """heredoc 내부 주석에 들어간 패턴은 실행되지 않으므로 flag 되지 않는다."""
        script = "ssh host bash -s <<END\n" "# docker exec -i x ls  ← 주석이라 무시\n" "echo ok\n" "END\n"
        assert _scan(script) == []


# ═════════════════════════════════════════════════════════════════════════
# 실제 리포지토리 상태 검증
# ═════════════════════════════════════════════════════════════════════════
class TestRepositoryClean:
    """현재 리포지토리가 가드에 의해 clean 상태임을 보장.

    이 테스트가 실패하면 누군가 회귀 패턴을 재도입한 것이다.
    """

    def test_current_repo_passes(self):
        files = GUARD.iter_target_files()
        all_violations: list[tuple[Path, int, str]] = []
        for path in files:
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            lines = text.splitlines(keepends=True)
            logical = GUARD.join_continuations(lines)
            for line_no, msg in GUARD.scan_file(logical):
                all_violations.append((path, line_no, msg))

        if all_violations:
            detail = "\n".join(f"  {p.relative_to(REPO_ROOT)}:{ln}: {m}" for p, ln, m in all_violations)
            pytest.fail("CD stdin guard found violations in repository:\n" + detail)

    def test_scans_cd_yml_and_post_deploy_smoke(self):
        """핵심 CD 파일이 실제로 스캔 대상에 포함되는지 확인."""
        files = {p.relative_to(REPO_ROOT) for p in GUARD.iter_target_files()}
        assert Path(".github/workflows/cd.yml") in files
        assert Path("scripts/post_deploy_smoke.sh") in files


# ═════════════════════════════════════════════════════════════════════════
# Rule 5: heredoc 내부에서 하위 스크립트 호출은 fd 0 상속 차단이 필요
# ═════════════════════════════════════════════════════════════════════════
class TestRule5InheritedScriptInvocation:
    """2026-04-09 감사(§4.11)에서 식별된 잠재 갭.

    heredoc 내부에서 ``bash X.sh`` 를 호출하면 자식 bash 가 부모의 fd 0
    (heredoc 스트림) 을 상속한다. X.sh 내부에 장래 ``docker exec -i`` 같은
    stdin 소비 라인이 추가되는 순간 §4.7/§4.8 과 동일한 은폐 경로가
    재생성된다. Rule 5 는 호출 지점에서 ``</dev/null`` 로 격리를 강제한다.
    """

    def test_detects_bash_script_without_redirect(self):
        script = (
            "ssh -T host bash -s << 'VERIFY'\n"
            "echo setup\n"
            "bash scripts/post_deploy_smoke.sh\n"
            "echo done\n"
            "VERIFY\n"
        )
        violations = _scan(script)
        assert len(violations) == 1
        assert "하위 스크립트" in violations[0][1]

    def test_detects_sh_script_without_redirect(self):
        script = "ssh host bash -s <<END\n" "sh scripts/run.sh\n" "END\n"
        violations = _scan(script)
        assert len(violations) == 1

    def test_detects_dot_slash_script_without_redirect(self):
        script = "ssh host bash -s <<END\n" "./scripts/run.sh\n" "END\n"
        violations = _scan(script)
        assert len(violations) == 1

    def test_passes_with_dev_null_redirect(self):
        """§4.11 fix: `</dev/null` 로 상속 사슬 격리."""
        script = "ssh -T host bash -s << 'VERIFY'\n" "bash scripts/post_deploy_smoke.sh </dev/null\n" "VERIFY\n"
        assert _scan(script) == []

    def test_passes_with_pipe_input(self):
        """`echo x | bash script.sh` 도 stdin 이 파이프로 치환되어 안전."""
        script = "ssh host bash -s <<END\n" "echo arg | bash scripts/run.sh\n" "END\n"
        assert _scan(script) == []

    def test_passes_with_file_redirect(self):
        """`bash script.sh < input.txt` 도 stdin 이 파일로 치환되어 안전."""
        script = "ssh host bash -s <<END\n" "bash scripts/run.sh < /tmp/input.txt\n" "END\n"
        assert _scan(script) == []

    def test_no_false_positive_outside_heredoc(self):
        """일반 CI 단계의 독립 `bash script.sh` 는 heredoc fd 0 상속이 없다."""
        script = "jobs:\n" "  t:\n" "    steps:\n" "      - run: |\n" "          bash scripts/post_deploy_smoke.sh\n"
        assert _scan(script) == []

    def test_no_false_positive_bash_dash_s_heredoc_start(self):
        """`bash -s << TAG` 자체(heredoc 시작 라인)는 flag 되지 않아야 한다."""
        # 시작 라인은 heredoc 밖에서 평가되므로 check_line 자체가 호출되지
        # 않는다. 하위 스크립트 확장자(.sh)도 없어 Rule 5 regex 는 미스.
        script = "ssh -T host bash -s << 'EOF'\n" "echo hello\n" "EOF\n"
        assert _scan(script) == []
