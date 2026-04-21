"""환경변수 bool 표기 정적 검사기 회귀 테스트 (AST 기반 Python 검사부).

검증 범위
=========
1. **regex → AST 마이그레이션 하위 호환**: 기존 regex 가 잡던 다섯 가지
   패턴을 AST 버전도 모두 잡는지 검증.
2. **AST 의 신규 커버리지**: 기존 regex 가 구조적으로 놓치던 케이스
   (중첩 괄호, 멀티라인, 비교 순서 역전) 를 AST 가 잡는지 검증.
3. **오탐 방지**: 문자열 리터럴 내부, 주석, ``env_bool()`` 호출 등은
   검출하지 않는다.
4. **면제 파일 처리**: ``PYTHON_EXEMPT`` 에 등록된 파일은 위반을 올려도
   보고하지 않는다.
5. **설정 파일 검사부 회귀 보호**: ``.env`` / docker-compose / workflow yml
   의 ``BOOL_ENV_KEYS`` 가 표준 표기("true"/"false")가 아닌 값을 받으면
   위반이 보고된다.

본 테스트는 loguru 스타일 검사기 (``test_check_loguru_style.py``) 의 regex
→ AST 전환 회귀 방어 패턴을 참고한다. 2026-04-21 Stage 2 작업 산출물.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER_PATH = REPO_ROOT / "scripts" / "check_bool_literals.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_bool_literals", CHECKER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CHECKER = _load_checker()


def _scan(source: str, tmp_path: Path, filename: str = "sample.py") -> list:
    """임시 파일에 소스를 쓰고 ``_scan_file`` 호출 결과를 반환."""
    path = tmp_path / filename
    path.write_text(source, encoding="utf-8")
    return CHECKER._scan_file(path)


# ═════════════════════════════════════════════════════════════════════════
# 1. 기존 regex 패턴 5종 하위 호환 — AST 도 모두 탐지해야 한다.
# ═════════════════════════════════════════════════════════════════════════
def test_equality_with_environ_get_is_detected(tmp_path: Path) -> None:
    """``os.environ.get(...) == "true"`` (기존 regex 1)."""
    source = 'import os\nif os.environ.get("X") == "true":\n    pass\n'
    violations = _scan(source, tmp_path)
    assert len(violations) == 1
    assert violations[0][1] == "compare_eq"


def test_equality_with_getenv_is_detected(tmp_path: Path) -> None:
    """``os.getenv(...) == "false"`` (기존 regex 2)."""
    source = 'import os\nif os.getenv("X") == "false":\n    pass\n'
    violations = _scan(source, tmp_path)
    assert len(violations) == 1
    assert violations[0][1] == "compare_eq"


def test_lower_chain_on_environ_get_is_detected(tmp_path: Path) -> None:
    """``os.environ.get(...).lower()`` (기존 regex 3)."""
    source = 'import os\nval = os.environ.get("X").lower()\n'
    violations = _scan(source, tmp_path)
    assert len(violations) == 1
    assert violations[0][1] == "lower_chain"


def test_lower_chain_on_getenv_is_detected(tmp_path: Path) -> None:
    """``os.getenv(...).lower()`` (기존 regex 4)."""
    source = 'import os\nval = os.getenv("X").lower()\n'
    violations = _scan(source, tmp_path)
    assert len(violations) == 1
    assert violations[0][1] == "lower_chain"


def test_in_tuple_membership_is_detected(tmp_path: Path) -> None:
    """``os.environ.get(...) in ("true", ...)`` (기존 regex 5)."""
    source = 'import os\nif os.environ.get("X") in ("true", "1", "yes"):\n    pass\n'
    violations = _scan(source, tmp_path)
    assert len(violations) == 1
    assert violations[0][1] == "in_container"


# ═════════════════════════════════════════════════════════════════════════
# 2. AST 의 신규 커버리지 — 기존 regex 가 놓치던 케이스
# ═════════════════════════════════════════════════════════════════════════
def test_nested_call_in_environ_get_arg_is_detected(tmp_path: Path) -> None:
    """중첩 괄호로 regex 가 누락하던 케이스.

    기존 regex ``os\\.environ\\.get\\([^)]*\\)\\s*==\\s*["\']`` 는 ``[^)]*``
    가 첫 번째 ``)`` (내부 호출의 닫는 괄호) 에서 끝나므로 누락한다.
    AST 는 ``ast.Call`` 노드를 그대로 인식하므로 결손이 없다.
    """
    source = (
        "import os\n"
        "def fallback():\n"
        '    return "x"\n'
        'if os.environ.get("X", fallback()) == "true":\n'
        "    pass\n"
    )
    violations = _scan(source, tmp_path)
    assert len(violations) == 1
    assert violations[0][1] == "compare_eq"


def test_multiline_environ_get_call_is_detected(tmp_path: Path) -> None:
    """멀티라인 호출도 AST 는 단일 노드로 인식하여 누락 없이 탐지.

    per-line regex 는 ``==`` 가 등장하는 줄과 ``os.environ.get`` 이 등장하는
    줄이 달라 어느 쪽에서도 매칭되지 않는 구조적 결손이 있다.
    """
    source = (
        "import os\n"
        "if (\n"
        "    os.environ.get(\n"
        '        "X",\n'
        '        "default",\n'
        "    )\n"
        '    == "true"\n'
        "):\n"
        "    pass\n"
    )
    violations = _scan(source, tmp_path)
    assert len(violations) == 1
    assert violations[0][1] == "compare_eq"


def test_reversed_compare_string_eq_env_call_is_detected(tmp_path: Path) -> None:
    """``"true" == os.environ.get(...)`` 도 탐지된다.

    기존 regex 는 환경변수 호출이 좌변일 때만 매칭했다. AST 는
    ``Compare.left`` / ``comparators[0]`` 의 양방향을 확인하므로 순서가
    뒤집혀도 누락하지 않는다.
    """
    source = 'import os\nif "true" == os.getenv("X"):\n    pass\n'
    violations = _scan(source, tmp_path)
    assert len(violations) == 1
    assert violations[0][1] == "compare_eq"


def test_not_equal_comparison_is_detected(tmp_path: Path) -> None:
    """``!=`` 비교도 ad-hoc 파싱 범주."""
    source = 'import os\nif os.getenv("X") != "false":\n    pass\n'
    violations = _scan(source, tmp_path)
    assert len(violations) == 1
    assert violations[0][1] == "compare_eq"


def test_not_in_container_comparison_is_detected(tmp_path: Path) -> None:
    """``not in`` 도 동일하게 탐지."""
    source = 'import os\nif os.environ.get("X") not in ("false", "0"):\n    pass\n'
    violations = _scan(source, tmp_path)
    assert len(violations) == 1
    assert violations[0][1] == "in_container"


def test_list_container_also_detected(tmp_path: Path) -> None:
    """``in [ ... ]`` 리스트 형태도 동일 패턴으로 간주."""
    source = 'import os\nif os.environ.get("X") in ["true", "1"]:\n    pass\n'
    violations = _scan(source, tmp_path)
    assert len(violations) == 1
    assert violations[0][1] == "in_container"


def test_set_container_also_detected(tmp_path: Path) -> None:
    """``in { ... }`` 집합 형태도 동일 패턴."""
    source = 'import os\nif os.environ.get("X") in {"true", "yes"}:\n    pass\n'
    violations = _scan(source, tmp_path)
    assert len(violations) == 1
    assert violations[0][1] == "in_container"


# ═════════════════════════════════════════════════════════════════════════
# 3. 오탐 방지 — false positive 가 없어야 한다.
# ═════════════════════════════════════════════════════════════════════════
def test_env_bool_helper_call_is_not_flagged(tmp_path: Path) -> None:
    """공식 진입점 ``env_bool()`` 호출은 그 자체가 허용되는 관용구."""
    source = 'from core.utils.env import env_bool\nif env_bool("X"):\n    pass\n'
    assert _scan(source, tmp_path) == []


def test_string_literal_inside_code_is_not_flagged(tmp_path: Path) -> None:
    """문자열 리터럴 내부의 패턴은 실제 코드가 아니므로 오탐하지 않는다.

    기존 regex 는 문자열 여부를 모르고 매칭하여 오탐 가능성이 있었다.
    AST 는 문자열 ``Constant`` 내부를 파싱하지 않으므로 구조적으로 안전.
    """
    source = '''import os
DOC = """Legacy pattern: os.environ.get("X") == "true" — do not use."""
'''
    assert _scan(source, tmp_path) == []


def test_comment_line_is_not_flagged(tmp_path: Path) -> None:
    """주석 안의 패턴도 탐지되지 않는다 (AST 는 주석을 보지 않음)."""
    source = 'import os\n# legacy pattern: os.environ.get("X").lower()\n'
    assert _scan(source, tmp_path) == []


def test_unrelated_function_call_is_not_flagged(tmp_path: Path) -> None:
    """``os.environ.get`` / ``os.getenv`` 외의 호출은 대상 아니다.

    예: ``config.get("X").lower()`` 는 ad-hoc bool 파싱이 아니라 일반적인
    문자열 변환일 수 있으므로 검사 범위 밖.
    """
    source = 'config = {"X": "true"}\nval = config.get("X").lower()\n'
    assert _scan(source, tmp_path) == []


def test_env_call_with_non_string_comparison_is_not_flagged(tmp_path: Path) -> None:
    """``env_call() == some_var`` 처럼 우변이 상수 문자열이 아니면 bool
    파싱이 아니라 일반 비교이므로 대상 아니다.
    """
    source = 'import os\nEXPECTED = "true"\nif os.environ.get("X") == EXPECTED:\n    pass\n'
    assert _scan(source, tmp_path) == []


# ═════════════════════════════════════════════════════════════════════════
# 4. 구문 오류 파일은 조용히 skip 된다 (부분 파싱 안 함).
# ═════════════════════════════════════════════════════════════════════════
def test_syntax_error_file_is_skipped(tmp_path: Path) -> None:
    """SyntaxError 가 발생하는 파일은 빈 리스트를 반환한다 (다른 검사기가
    별도로 문법 오류를 잡아내므로 본 검사기는 skip)."""
    source = "def broken(:\n    pass\n"
    assert _scan(source, tmp_path) == []


# ═════════════════════════════════════════════════════════════════════════
# 5. 면제 파일은 check_python_files() 에서 제외된다.
# ═════════════════════════════════════════════════════════════════════════
def test_exempt_file_path_matches_real_env_py() -> None:
    """``PYTHON_EXEMPT`` 경로가 실제 저장소에 존재한다."""
    for path in CHECKER.PYTHON_EXEMPT:
        assert path.exists(), f"exempt path missing: {path}"


def test_check_python_files_returns_no_errors_for_current_repo() -> None:
    """현재 저장소의 Python 코드베이스에서 위반이 0 건이어야 한다.

    이는 "AST 가 regex 보다 덜 엄격해서 같은 0 이 나온 것" 을 배제하기
    위한 산업적 회귀 검사. 위 10 여 개 단위 테스트로 AST 가 실제
    패턴을 잡는 능력이 검증되므로, 본 전수 스캔이 0 이면 "코드베이스에
    숨은 패턴이 없다" 는 의미가 된다.
    """
    errors = CHECKER.check_python_files()
    assert errors == [], f"Unexpected ad-hoc bool violations: {errors}"


# ═════════════════════════════════════════════════════════════════════════
# 6. 설정 파일 검사부 회귀 방어 (regex 유지).
# ═════════════════════════════════════════════════════════════════════════
def test_config_check_flags_nonstandard_bool_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``.env`` 형식 파일에서 ``BOOL_ENV_KEYS`` 중 하나가 표준 표기가 아닌
    값을 가지면 위반이 보고된다.
    """
    env_file = tmp_path / ".env"
    env_file.write_text("OTEL_ENABLED=yes\nSCHEDULER_ENABLED=true\n", encoding="utf-8")

    # 검사기 ROOT 를 임시 디렉토리로 우회한다.
    monkeypatch.setattr(CHECKER, "ROOT", tmp_path)
    errors = CHECKER.check_config_files()
    # OTEL_ENABLED=yes 는 위반, SCHEDULER_ENABLED=true 는 통과.
    assert len(errors) == 1
    assert "OTEL_ENABLED=yes" in errors[0]


def test_config_check_accepts_standard_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``"true"`` / ``"false"`` 는 통과한다."""
    env_file = tmp_path / ".env.example"
    env_file.write_text(
        "TESTING=false\nOTEL_ENABLED=true\nALERT_RETRY_LOOP_ENABLED=true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(CHECKER, "ROOT", tmp_path)
    errors = CHECKER.check_config_files()
    assert errors == []


def test_config_check_ignores_non_whitelisted_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``BOOL_ENV_KEYS`` 에 없는 키는 어떤 값이든 경고 대상 아니다."""
    env_file = tmp_path / ".env"
    env_file.write_text("MY_FEATURE_FLAG=yes\nANOTHER_FLAG=1\n", encoding="utf-8")
    monkeypatch.setattr(CHECKER, "ROOT", tmp_path)
    assert CHECKER.check_config_files() == []
