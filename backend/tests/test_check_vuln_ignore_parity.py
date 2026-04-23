"""취약점 ignore 목록 parity 검사기 회귀 테스트.

정책: CLAUDE.md §9 "grype.yaml ↔ backend/.pip-audit-ignore parity 정적 검사기"
배경: 2026-04-22 `fix/grype-yaml-glibc-lxml-parity` lxml GHSA silent miss 회고

검증 범위
=========
1. **동일 ID 집합은 통과**: 두 파일에 같은 CVE/GHSA 가 들어있으면 0 errors.
2. **단방향 존재 + 마커 없음 = error**: grype 에만 있고 `grype-only` 마커 없으면
   error, pip-audit 에만 있고 `pip-audit-only` 마커 없어도 error.
3. **단방향 존재 + 마커 = 허용**: OS 패키지처럼 구조적으로 한쪽만 가능한
   항목은 해당 방향 마커가 있으면 통과.
4. **파서 오탐 방지**: 주석, 섹션 헤더, 만료일 주석 안의 CVE-like 토큰은
   무시. 인용 변형(`'CVE-...'`, `"GHSA-..."`) 도 수용.
5. **실제 저장소 회귀 고정**: 현재 `.grype.yaml` / `backend/.pip-audit-ignore`
   에 대해 0 errors (parity 통과 상태 유지).
6. **main() 진입 경로**: 필수 파일 누락 시 exit 1, 정상 시 exit 0.

본 테스트는 2026-04-22 `chore/check-vuln-ignore-parity` 작업 산출물. Stage 3
(`test_check_rbac_coverage.py`) 와 동일한 6 그룹 구조를 유지한다.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER_PATH = REPO_ROOT / "scripts" / "check_vuln_ignore_parity.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_vuln_ignore_parity", CHECKER_PATH)
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


# ═════════════════════════════════════════════════════════════════════════
# 1. 동일 ID 집합 — 통과 (0 errors).
# ═════════════════════════════════════════════════════════════════════════
def test_identical_id_sets_pass(tmp_path: Path) -> None:
    grype = _write_grype(
        tmp_path,
        "ignore:\n"
        "  - vulnerability: GHSA-7gcm-g887-7qv7  # 2026-06-06\n"
        "  - vulnerability: GHSA-vfmq-68hx-4jfw  # 2026-06-06\n",
    )
    pip = _write_pip(
        tmp_path,
        "GHSA-7gcm-g887-7qv7  # 2026-06-06 OTel 업그레이드 대기\n"
        "GHSA-vfmq-68hx-4jfw  # 2026-06-06 lxml 업그레이드 대기\n",
    )
    errors = CHECKER.check_parity(CHECKER.parse_grype(grype), CHECKER.parse_pip_audit(pip))
    assert errors == []


def test_empty_files_pass(tmp_path: Path) -> None:
    """두 파일 모두 빈 ignore 목록도 통과 (둘 다 없음 = 대칭)."""
    grype = _write_grype(tmp_path, "ignore:\n")
    pip = _write_pip(tmp_path, "# 빈 목록\n")
    errors = CHECKER.check_parity(CHECKER.parse_grype(grype), CHECKER.parse_pip_audit(pip))
    assert errors == []


# ═════════════════════════════════════════════════════════════════════════
# 2. 단방향 존재 + 마커 없음 — error.
# ═════════════════════════════════════════════════════════════════════════
def test_grype_only_without_marker_is_error(tmp_path: Path) -> None:
    """grype 에만 있고 `grype-only` 마커 없으면 error."""
    grype = _write_grype(
        tmp_path,
        "ignore:\n  - vulnerability: CVE-2025-15281  # 2026-06-06\n",
    )
    pip = _write_pip(tmp_path, "")
    errors = CHECKER.check_parity(CHECKER.parse_grype(grype), CHECKER.parse_pip_audit(pip))
    assert len(errors) == 1
    assert "CVE-2025-15281" in errors[0]
    assert ".grype.yaml 에만 존재" in errors[0]


def test_pip_audit_only_without_marker_is_error(tmp_path: Path) -> None:
    """pip-audit 에만 있고 `pip-audit-only` 마커 없으면 error."""
    grype = _write_grype(tmp_path, "ignore:\n")
    pip = _write_pip(
        tmp_path,
        "GHSA-abcd-1234-efgh  # 2026-06-06 근거\n",
    )
    errors = CHECKER.check_parity(CHECKER.parse_grype(grype), CHECKER.parse_pip_audit(pip))
    assert len(errors) == 1
    assert "GHSA-abcd-1234-efgh" in errors[0]
    assert ".pip-audit-ignore 에만 존재" in errors[0]


def test_multiple_missing_entries_yield_multiple_errors(tmp_path: Path) -> None:
    """차집합이 복수이면 각각 error 라인이 생성된다 (정렬 순서)."""
    grype = _write_grype(
        tmp_path,
        "ignore:\n"
        "  - vulnerability: CVE-2025-15281  # 2026-06-06\n"
        "  - vulnerability: CVE-2026-4046   # 2026-06-06\n",
    )
    pip = _write_pip(tmp_path, "")
    errors = CHECKER.check_parity(CHECKER.parse_grype(grype), CHECKER.parse_pip_audit(pip))
    assert len(errors) == 2
    # 정렬 순서 확인
    assert "CVE-2025-15281" in errors[0]
    assert "CVE-2026-4046" in errors[1]


# ═════════════════════════════════════════════════════════════════════════
# 3. 단방향 존재 + 마커 = 허용.
# ═════════════════════════════════════════════════════════════════════════
def test_grype_only_with_marker_is_allowed(tmp_path: Path) -> None:
    """OS 패키지 CVE 처럼 의도된 grype-only 는 `grype-only` 마커로 허용."""
    grype = _write_grype(
        tmp_path,
        "ignore:\n  - vulnerability: CVE-2025-15281  # grype-only 2026-06-06 debian 12 미백포트\n",
    )
    pip = _write_pip(tmp_path, "")
    errors = CHECKER.check_parity(CHECKER.parse_grype(grype), CHECKER.parse_pip_audit(pip))
    assert errors == []


def test_pip_audit_only_with_marker_is_allowed(tmp_path: Path) -> None:
    """순수 Python 패키지 취약점은 `pip-audit-only` 로 허용 가능."""
    grype = _write_grype(tmp_path, "ignore:\n")
    pip = _write_pip(
        tmp_path,
        "GHSA-abcd-1234-efgh  # pip-audit-only 2026-06-06 pure python dep\n",
    )
    errors = CHECKER.check_parity(CHECKER.parse_grype(grype), CHECKER.parse_pip_audit(pip))
    assert errors == []


def test_mixed_markers_independently_evaluated(tmp_path: Path) -> None:
    """방향별 마커는 독립적으로 판정."""
    grype = _write_grype(
        tmp_path,
        "ignore:\n  - vulnerability: CVE-2025-15281  # grype-only 2026-06-06\n",
    )
    pip = _write_pip(
        tmp_path,
        "GHSA-abcd-1234-efgh  # pip-audit-only 2026-06-06\n",
    )
    errors = CHECKER.check_parity(CHECKER.parse_grype(grype), CHECKER.parse_pip_audit(pip))
    assert errors == []


def test_grype_only_marker_does_not_cover_pip_audit_only(tmp_path: Path) -> None:
    """pip-audit 항목에 `grype-only` 마커가 붙어도 `pip-audit-only` 로는 인정 안 됨."""
    grype = _write_grype(tmp_path, "ignore:\n")
    pip = _write_pip(
        tmp_path,
        "GHSA-abcd-1234-efgh  # grype-only 2026-06-06 잘못된 마커\n",
    )
    errors = CHECKER.check_parity(CHECKER.parse_grype(grype), CHECKER.parse_pip_audit(pip))
    assert len(errors) == 1
    assert "GHSA-abcd-1234-efgh" in errors[0]


# ═════════════════════════════════════════════════════════════════════════
# 4. 파서 오탐 방지.
# ═════════════════════════════════════════════════════════════════════════
def test_grype_quoted_id_is_parsed(tmp_path: Path) -> None:
    """`- vulnerability: "CVE-..."` 인용 변형도 파싱."""
    grype = _write_grype(
        tmp_path,
        'ignore:\n  - vulnerability: "CVE-2025-15281"  # grype-only 2026-06-06\n',
    )
    parsed = CHECKER.parse_grype(grype)
    assert "CVE-2025-15281" in parsed
    assert parsed["CVE-2025-15281"] is True


def test_grype_comment_block_is_ignored(tmp_path: Path) -> None:
    """섹션 헤더 주석 안의 CVE-like 토큰은 파싱 대상 아님."""
    grype = _write_grype(
        tmp_path,
        "ignore:\n"
        "  # CVE-2099-99999 이 주석은 파싱되면 안 됨\n"
        "  - vulnerability: CVE-2025-15281  # grype-only 2026-06-06\n",
    )
    parsed = CHECKER.parse_grype(grype)
    assert "CVE-2099-99999" not in parsed
    assert "CVE-2025-15281" in parsed


def test_pip_audit_comment_line_is_ignored(tmp_path: Path) -> None:
    """선두가 `#` 인 주석 라인의 CVE 토큰은 파싱 대상 아님."""
    pip = _write_pip(
        tmp_path,
        "# CVE-2099-99999 헤더 주석 — 파싱되면 안 됨\nGHSA-7gcm-g887-7qv7  # 2026-06-06\n",
    )
    parsed = CHECKER.parse_pip_audit(pip)
    assert "CVE-2099-99999" not in parsed
    assert "GHSA-7gcm-g887-7qv7" in parsed


# ═════════════════════════════════════════════════════════════════════════
# 5. 실제 저장소 회귀 고정 — 현재 상태는 parity 통과.
# ═════════════════════════════════════════════════════════════════════════
def test_real_repo_files_pass_parity() -> None:
    """실제 `.grype.yaml` / `backend/.pip-audit-ignore` 가 parity 통과 상태 유지.

    본 테스트가 실패하면 ignore 엔트리 추가 시 parity 를 깬 것이다. 두 파일을
    동시에 업데이트하거나 `grype-only`/`pip-audit-only` 마커로 예외 표시해야
    한다 — CLAUDE.md §9 2026-04-22 회고 참조.
    """
    grype = CHECKER.parse_grype()
    pip_audit = CHECKER.parse_pip_audit()
    errors = CHECKER.check_parity(grype, pip_audit)
    assert errors == [], "\n".join(errors)


# ═════════════════════════════════════════════════════════════════════════
# 6. main() 진입 경로.
# ═════════════════════════════════════════════════════════════════════════
def test_main_on_real_repo_returns_zero() -> None:
    """실제 저장소 상태에서 CLI 실행 시 exit 0 + stdout 에 parity OK."""
    result = subprocess.run(
        [sys.executable, str(CHECKER_PATH)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "parity OK" in result.stdout


def test_main_missing_grype_file_returns_one(tmp_path: Path, monkeypatch) -> None:
    """필수 화이트리스트 파일이 없으면 exit 1."""
    # 임시 경로로 모듈 상수를 치환 → 파일 없음 시뮬레이션.
    monkeypatch.setattr(CHECKER, "GRYPE_PATH", tmp_path / "nonexistent-grype.yaml")
    # PIP_AUDIT_PATH 도 임시 경로로 바꿔 테스트가 실제 파일을 건드리지 않도록.
    monkeypatch.setattr(CHECKER, "PIP_AUDIT_PATH", tmp_path / "nonexistent-pip-audit-ignore")
    rc = CHECKER.main()
    assert rc == 1
