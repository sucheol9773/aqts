"""소유권 경계 위반 정적 검사기 회귀 테스트.

정책: ``agent_docs/governance.md §2`` 팀 소유권 매트릭스.

검증 범위
=========
1. **정상 편집 통과**: 팀이 자기 소유 파일만 수정하면 0 errors.
2. **경계 위반 검출**: 다른 팀 소유 파일을 수정하면 BOUNDARY error.
3. **리드 전용 파일 차단**: governance.md §2.5 파일 수정 시 LEAD-ONLY error.
4. **공유 경로 허용**: mailboxes, 루트 README 등은 모든 팀 허용.
5. **--allow-cross 플래그**: BOUNDARY 는 경고로 전환, LEAD-ONLY 는 유지.
6. **브랜치 판별 불가 시 SKIP**: 리드 브랜치에서는 exit 2.
7. **main() 진입 경로**: 팀 브랜치에서 위반 시 exit 1, 정상 시 exit 0.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER_PATH = REPO_ROOT / "scripts" / "check_ownership_boundary.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location(
        "check_ownership_boundary", CHECKER_PATH
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CHECKER = _load_checker()


# ═════════════════════════════════════════════════════════════════════════
# 1. 패턴 매칭 유틸리티 (matches_any)
# ═════════════════════════════════════════════════════════════════════════


class TestMatchesAny:
    """matches_any 함수의 기본 동작 검증."""

    def test_exact_match(self):
        assert CHECKER.matches_any("backend/scheduler_main.py", ["backend/scheduler_main.py"])

    def test_glob_star(self):
        assert CHECKER.matches_any("scripts/run_backtest.py", ["scripts/run_*.py"])

    def test_double_star(self):
        assert CHECKER.matches_any(
            "backend/core/notification/alert_manager.py",
            ["backend/core/notification/**"],
        )

    def test_no_match(self):
        assert not CHECKER.matches_any("backend/api/routes/auth.py", ["backend/core/**"])

    def test_double_star_nested(self):
        assert CHECKER.matches_any(
            "backend/core/strategy_ensemble/sub/deep/file.py",
            ["backend/core/strategy_ensemble/**"],
        )


# ═════════════════════════════════════════════════════════════════════════
# 2. 정상 편집 통과 (팀이 자기 소유 파일만 수정)
# ═════════════════════════════════════════════════════════════════════════


class TestOwnedFilesPass:
    """각 팀이 자기 소유 파일을 수정하면 0 errors."""

    def test_team1_strategy(self):
        files = [
            "backend/core/strategy_ensemble/runner.py",
            "backend/core/backtest_engine/engine.py",
            "scripts/run_backtest.py",
        ]
        errors = CHECKER.check_ownership(1, files)
        assert errors == []

    def test_team2_scheduler(self):
        files = [
            "backend/scheduler_main.py",
            "backend/core/notification/alert_manager.py",
            "docker-compose.yml",
            ".github/workflows/ci.yml",
        ]
        errors = CHECKER.check_ownership(2, files)
        assert errors == []

    def test_team3_api(self):
        files = [
            "backend/api/routes/orders.py",
            "backend/db/models/user.py",
            "backend/core/order_executor/executor.py",
            "backend/core/data_collector/news_collector.py",
        ]
        errors = CHECKER.check_ownership(3, files)
        assert errors == []

    def test_team4_tests(self):
        files = [
            "backend/tests/test_backtest_engine.py",
            "scripts/check_rbac_coverage.py",
            "docs/FEATURE_STATUS.md",
        ]
        errors = CHECKER.check_ownership(4, files)
        assert errors == []


# ═════════════════════════════════════════════════════════════════════════
# 3. 경계 위반 검출
# ═════════════════════════════════════════════════════════════════════════


class TestBoundaryViolation:
    """다른 팀 소유 파일 수정 시 BOUNDARY error."""

    def test_team1_edits_scheduler(self):
        files = ["backend/scheduler_main.py"]
        errors = CHECKER.check_ownership(1, files)
        assert len(errors) == 1
        assert "BOUNDARY" in errors[0]
        assert "팀 2" in errors[0]

    def test_team2_edits_api(self):
        files = ["backend/api/routes/orders.py"]
        errors = CHECKER.check_ownership(2, files)
        assert len(errors) == 1
        assert "BOUNDARY" in errors[0]
        assert "팀 3" in errors[0]

    def test_team3_edits_tests(self):
        files = ["backend/tests/test_something.py"]
        errors = CHECKER.check_ownership(3, files)
        assert len(errors) == 1
        assert "BOUNDARY" in errors[0]
        assert "팀 4" in errors[0]

    def test_team4_edits_strategy(self):
        files = ["backend/core/strategy_ensemble/runner.py"]
        errors = CHECKER.check_ownership(4, files)
        assert len(errors) == 1
        assert "BOUNDARY" in errors[0]
        assert "팀 1" in errors[0]

    def test_mixed_own_and_foreign(self):
        """자기 파일 + 남의 파일 혼합 시 남의 파일만 error."""
        files = [
            "backend/core/strategy_ensemble/runner.py",  # 팀 1 소유
            "backend/scheduler_main.py",  # 팀 2 소유
        ]
        errors = CHECKER.check_ownership(1, files)
        assert len(errors) == 1
        assert "scheduler_main.py" in errors[0]


# ═════════════════════════════════════════════════════════════════════════
# 4. 리드 전용 파일 차단
# ═════════════════════════════════════════════════════════════════════════


class TestLeadOnlyFiles:
    """governance.md §2.5 리드 전용 파일은 모든 팀에서 차단."""

    @pytest.mark.parametrize("team", [1, 2, 3, 4])
    def test_claude_md(self, team):
        errors = CHECKER.check_ownership(team, ["CLAUDE.md"])
        assert len(errors) == 1
        assert "LEAD-ONLY" in errors[0]

    @pytest.mark.parametrize("team", [1, 2, 3, 4])
    def test_development_policies(self, team):
        errors = CHECKER.check_ownership(team, ["agent_docs/development-policies.md"])
        assert len(errors) == 1
        assert "LEAD-ONLY" in errors[0]

    def test_settings_py(self):
        errors = CHECKER.check_ownership(2, ["backend/config/settings.py"])
        assert len(errors) == 1
        assert "LEAD-ONLY" in errors[0]

    def test_env_example(self):
        errors = CHECKER.check_ownership(1, [".env.example"])
        assert len(errors) == 1
        assert "LEAD-ONLY" in errors[0]

    def test_docs_archive(self):
        errors = CHECKER.check_ownership(3, ["docs/archive/old-doc.md"])
        assert len(errors) == 1
        assert "LEAD-ONLY" in errors[0]


# ═════════════════════════════════════════════════════════════════════════
# 5. 공유 경로 허용
# ═════════════════════════════════════════════════════════════════════════


class TestSharedPaths:
    """모든 팀이 수정 가능한 공유 경로는 통과."""

    @pytest.mark.parametrize("team", [1, 2, 3, 4])
    def test_mailbox_messages(self, team):
        files = [f"agent_docs/mailboxes/team{team}/inbox/msg.md"]
        errors = CHECKER.check_ownership(team, files)
        assert errors == []

    @pytest.mark.parametrize("team", [1, 2, 3, 4])
    def test_cross_team_mailbox(self, team):
        """다른 팀 메일박스에 메시지 작성도 허용."""
        other = (team % 4) + 1
        files = [f"agent_docs/mailboxes/team{other}/inbox/msg.md"]
        errors = CHECKER.check_ownership(team, files)
        assert errors == []


# ═════════════════════════════════════════════════════════════════════════
# 6. --allow-cross 플래그
# ═════════════════════════════════════════════════════════════════════════


class TestAllowCross:
    """--allow-cross 시 BOUNDARY 는 경고, LEAD-ONLY 는 error 유지."""

    def test_boundary_becomes_warning(self):
        """교차 편집이 --allow-cross 로 허용됨."""
        with (
            patch.object(CHECKER, "get_current_team", return_value=1),
            patch.object(
                CHECKER,
                "get_changed_files",
                return_value=["backend/scheduler_main.py"],
            ),
        ):
            exit_code = CHECKER.main(["--allow-cross"])
        assert exit_code == 0

    def test_lead_only_still_blocked(self):
        """--allow-cross 에서도 리드 전용은 차단."""
        with (
            patch.object(CHECKER, "get_current_team", return_value=1),
            patch.object(
                CHECKER, "get_changed_files", return_value=["CLAUDE.md"]
            ),
        ):
            exit_code = CHECKER.main(["--allow-cross"])
        assert exit_code == 1


# ═════════════════════════════════════════════════════════════════════════
# 7. 브랜치 판별 / main() 진입
# ═════════════════════════════════════════════════════════════════════════


class TestMainEntry:
    """main() 함수의 진입 경로 검증."""

    def test_non_team_branch_skips(self):
        """리드/기능 브랜치에서는 SKIP (exit 2)."""
        with patch.object(CHECKER, "get_current_team", return_value=None):
            assert CHECKER.main([]) == 2

    def test_unknown_team_skips(self):
        """존재하지 않는 팀 번호는 SKIP."""
        with patch.object(CHECKER, "get_current_team", return_value=99):
            assert CHECKER.main([]) == 2

    def test_no_changes_passes(self):
        with (
            patch.object(CHECKER, "get_current_team", return_value=1),
            patch.object(CHECKER, "get_changed_files", return_value=[]),
        ):
            assert CHECKER.main([]) == 0

    def test_violation_returns_1(self):
        with (
            patch.object(CHECKER, "get_current_team", return_value=1),
            patch.object(
                CHECKER,
                "get_changed_files",
                return_value=["backend/api/routes/orders.py"],
            ),
        ):
            assert CHECKER.main([]) == 1

    def test_clean_returns_0(self):
        with (
            patch.object(CHECKER, "get_current_team", return_value=1),
            patch.object(
                CHECKER,
                "get_changed_files",
                return_value=["backend/core/strategy_ensemble/runner.py"],
            ),
        ):
            assert CHECKER.main([]) == 0


# ═════════════════════════════════════════════════════════════════════════
# 8. 소유자 미지정 파일은 경고하지 않음
# ═════════════════════════════════════════════════════════════════════════


class TestUnownedFiles:
    """어떤 팀에도 매핑되지 않는 새 파일은 통과."""

    def test_new_root_file(self):
        errors = CHECKER.check_ownership(1, ["some_new_script.sh"])
        assert errors == []

    def test_new_directory(self):
        errors = CHECKER.check_ownership(2, ["new_dir/something.py"])
        assert errors == []
