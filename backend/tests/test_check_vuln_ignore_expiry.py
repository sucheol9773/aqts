"""취약점 ignore 목록 만료일 검사기 회귀 테스트.

정책: CLAUDE.md §9 "화이트리스트 만료일 자동 검증"
배경: 2026-04-23 PR #38 hotfix (CVE-2026-3298) 후속 작업

검증 범위
=========
1. **유효 만료일은 통과**: 모든 엔트리가 미래 날짜면 0 errors.
2. **만료/결손 엔트리는 error**: 과거 날짜, 만료일 없음, 형식 위반 모두 error.
3. **경계 케이스**: 오늘 당일은 통과 (not yet expired), 다양한 주석 포맷, 여러 CVE 혼재.
4. **파서 오탐 방지**: 주석 안 날짜 중 첫 매치만 만료일, 식별자 안의 숫자는 무시.
5. **실제 저장소 회귀 고정**: 현재 `.grype.yaml` / `backend/.pip-audit-ignore` 는
   2026-06-06 만료이므로 오늘(2026-04-23) 기준 통과 상태 유지.
6. **main() 진입 경로**: 필수 파일 누락 시 exit 1, 정상 시 exit 0.

본 테스트는 parity 검사기(`test_check_vuln_ignore_parity.py`) 와 동일한 6 그룹
구조를 유지한다 (Stage 2/3 계보: check_bool_literals / check_rbac_coverage).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER_PATH = REPO_ROOT / "scripts" / "check_vuln_ignore_expiry.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_vuln_ignore_expiry", CHECKER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CHECKER = _load_checker()


def _write_grype(tmp_path: Path, body: str) -> Path:
    """테스트용 .grype.yaml 생성."""
    path = tmp_path / ".grype.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def _write_pip(tmp_path: Path, body: str) -> Path:
    """테스트용 .pip-audit-ignore 생성."""
    path = tmp_path / ".pip-audit-ignore"
    path.write_text(body, encoding="utf-8")
    return path


TODAY = date(2026, 4, 23)
FUTURE = (TODAY + timedelta(days=60)).isoformat()
FAR_FUTURE = (TODAY + timedelta(days=365)).isoformat()
YESTERDAY = (TODAY - timedelta(days=1)).isoformat()
LONG_AGO = (TODAY - timedelta(days=100)).isoformat()


# ═════════════════════════════════════════════════════════════════════════
# 1. 유효 만료일 — 통과 (0 errors).
# ═════════════════════════════════════════════════════════════════════════
def test_all_future_dates_pass(tmp_path: Path) -> None:
    grype = _write_grype(
        tmp_path,
        "ignore:\n"
        f"  - vulnerability: CVE-2025-15281  # grype-only {FUTURE}\n"
        f"  - vulnerability: GHSA-vfmq-68hx-4jfw  # {FAR_FUTURE} 업그레이드 대기\n",
    )
    entries = CHECKER.parse_grype(grype)
    errors = CHECKER.check_expiry(entries, TODAY)
    assert errors == []


def test_empty_files_pass(tmp_path: Path) -> None:
    """엔트리가 없으면 만료 검사 대상도 없어 통과."""
    grype = _write_grype(tmp_path, "ignore:\n")
    pip = _write_pip(tmp_path, "# 빈 목록\n")
    entries = [*CHECKER.parse_grype(grype), *CHECKER.parse_pip_audit(pip)]
    errors = CHECKER.check_expiry(entries, TODAY)
    assert errors == []


# ═════════════════════════════════════════════════════════════════════════
# 2. 만료/결손 — error.
# ═════════════════════════════════════════════════════════════════════════
def test_expired_grype_entry_is_error(tmp_path: Path) -> None:
    """과거 만료일은 경과 일수와 함께 error."""
    grype = _write_grype(
        tmp_path,
        f"ignore:\n  - vulnerability: CVE-2025-15281  # grype-only {YESTERDAY}\n",
    )
    errors = CHECKER.check_expiry(CHECKER.parse_grype(grype), TODAY)
    assert len(errors) == 1
    assert "CVE-2025-15281" in errors[0]
    assert YESTERDAY in errors[0]
    assert "1일 경과" in errors[0]


def test_expired_pip_entry_is_error(tmp_path: Path) -> None:
    pip = _write_pip(
        tmp_path,
        f"GHSA-jr27-m4p2-rc6r  # {LONG_AGO} python-jose 마이그레이션 대기\n",
    )
    errors = CHECKER.check_expiry(CHECKER.parse_pip_audit(pip), TODAY)
    assert len(errors) == 1
    assert "GHSA-jr27-m4p2-rc6r" in errors[0]
    assert "100일 경과" in errors[0]


def test_missing_expiry_is_error(tmp_path: Path) -> None:
    """주석에 날짜가 없는 엔트리는 error (영구 예외 금지 규칙)."""
    pip = _write_pip(
        tmp_path,
        "GHSA-7gcm-g887-7qv7  # 근거만 있고 만료일 누락\n",
    )
    errors = CHECKER.check_expiry(CHECKER.parse_pip_audit(pip), TODAY)
    assert len(errors) == 1
    assert "GHSA-7gcm-g887-7qv7" in errors[0]
    assert "만료일 없음" in errors[0]


# ═════════════════════════════════════════════════════════════════════════
# 3. 경계 케이스.
# ═════════════════════════════════════════════════════════════════════════
def test_today_is_not_expired(tmp_path: Path) -> None:
    """오늘 당일 만료는 아직 유효 (`<` 비교로 경계 제외)."""
    today_iso = TODAY.isoformat()
    grype = _write_grype(
        tmp_path,
        f"ignore:\n  - vulnerability: CVE-2026-3298  # grype-only {today_iso}\n",
    )
    errors = CHECKER.check_expiry(CHECKER.parse_grype(grype), TODAY)
    assert errors == []


def test_malformed_date_is_error(tmp_path: Path) -> None:
    """``2026-02-30`` 같은 달력상 존재하지 않는 날짜는 만료일 없음으로 처리."""
    grype = _write_grype(
        tmp_path,
        "ignore:\n  - vulnerability: CVE-2025-15281  # grype-only 2026-02-30\n",
    )
    errors = CHECKER.check_expiry(CHECKER.parse_grype(grype), TODAY)
    assert len(errors) == 1
    assert "만료일 없음" in errors[0]


def test_mixed_valid_and_expired_entries(tmp_path: Path) -> None:
    """유효 + 만료 혼재 시 만료만 error 로 선별."""
    grype = _write_grype(
        tmp_path,
        "ignore:\n"
        f"  - vulnerability: CVE-2025-15281  # grype-only {FUTURE}\n"
        f"  - vulnerability: CVE-2026-4046   # grype-only {YESTERDAY}\n"
        f"  - vulnerability: CVE-2026-4437   # grype-only {FAR_FUTURE}\n",
    )
    errors = CHECKER.check_expiry(CHECKER.parse_grype(grype), TODAY)
    assert len(errors) == 1
    assert "CVE-2026-4046" in errors[0]


# ═════════════════════════════════════════════════════════════════════════
# 4. 파서 오탐 방지.
# ═════════════════════════════════════════════════════════════════════════
def test_multiple_dates_in_comment_uses_first(tmp_path: Path) -> None:
    """주석에 날짜가 여러 개면 첫 매치를 만료일로 사용."""
    grype = _write_grype(
        tmp_path,
        "ignore:\n  - vulnerability: CVE-2025-15281  " f"# grype-only {FUTURE} (2024-01-01 공개, 만료 연장 2회)\n",
    )
    entries = CHECKER.parse_grype(grype)
    assert len(entries) == 1
    assert entries[0].expiry == date.fromisoformat(FUTURE)


def test_identifier_year_not_mistaken_as_date(tmp_path: Path) -> None:
    """CVE-2026-... 의 "2026" 이 날짜로 오인되지 않는지 — 주석 없는 라인은
    만료일 없음으로 처리되어야 한다."""
    grype = _write_grype(
        tmp_path,
        "ignore:\n  - vulnerability: CVE-2026-4046\n",
    )
    entries = CHECKER.parse_grype(grype)
    assert len(entries) == 1
    assert entries[0].expiry is None


# ═════════════════════════════════════════════════════════════════════════
# 5. 실제 저장소 회귀 고정.
# ═════════════════════════════════════════════════════════════════════════
def test_real_repo_files_pass_expiry_today(tmp_path: Path) -> None:
    """현재 저장소의 `.grype.yaml` / `backend/.pip-audit-ignore` 는 2026-06-06
    만료이므로 오늘(2026-04-23) 기준 통과해야 한다. 본 테스트는 만료일이
    다가와 CI 가 빨갛게 되기 전에 PR 리뷰어가 인지하도록 하는 선제적 고정."""
    entries = [*CHECKER.parse_grype(), *CHECKER.parse_pip_audit()]
    assert len(entries) > 0, "화이트리스트 엔트리 수는 0 보다 커야 한다"
    errors = CHECKER.check_expiry(entries, TODAY)
    assert errors == [], f"만료 임박/경과 엔트리: {errors}"


# ═════════════════════════════════════════════════════════════════════════
# 6. main() 진입 경로.
# ═════════════════════════════════════════════════════════════════════════
def test_main_on_real_repo_returns_zero() -> None:
    """스크립트를 실제로 subprocess 실행하여 exit 0 확인."""
    proc = subprocess.run(
        [sys.executable, str(CHECKER_PATH)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    assert "vuln-ignore expiry OK" in proc.stdout


def test_main_missing_grype_file_returns_one(monkeypatch, tmp_path: Path) -> None:
    """필수 파일 누락 시 exit 1. ``main()`` 이 파일 존재를 먼저 검사한다는
    계약을 고정."""
    monkeypatch.setattr(CHECKER, "GRYPE_PATH", tmp_path / "nonexistent.yaml")
    monkeypatch.setattr(CHECKER, "PIP_AUDIT_PATH", tmp_path / "also-nonexistent")
    assert CHECKER.main() == 1
