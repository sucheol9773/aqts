"""RBAC 가드 정적 검사기 회귀 테스트.

정책: ``docs/security/rbac-policy.md`` 및 CLAUDE.md "인증(authn) ≠ 인가(authz) 분리 원칙"

검증 범위
=========
1. **정책 하위 호환**: 적절한 ``require_viewer|operator|admin`` 의존성이
   적용된 라우트는 통과한다. 함수 파라미터 ``Depends(...)`` 형태와 데코레이터
   ``dependencies=[Depends(...), ...]`` 형태 모두 인정된다.
2. **위반 검출**: mutation 에 가드가 전혀 없거나 ``require_viewer`` 만
   적용된 경우, read 에 가드가 없는 경우를 모두 검출한다.
3. **오탐 방지**: WHITELIST (자기 세션 관리 / 공개 엔드포인트), 라우터가
   아닌 데코레이터, HTTP 메서드가 아닌 라우터 호출, ``Depends(...)`` 가
   아닌 기본값은 영향을 주지 않는다.
4. **구문 오류 처리**: SyntaxError 파일은 `parse error: ...` 단일 메시지
   로 보고된다 (다른 파일 스캔을 막지 않는다).
5. **실제 저장소 회귀 고정**: 현재 ``backend/api/routes/`` 에 대해 0 errors.
   ``WHITELIST`` 의 ``(file, function)`` 쌍이 실제로 존재 (stale whitelist
   방지) 한다.
6. **main() 진입 경로**: routes 디렉토리 누락 시 exit 1, 정상 시 exit 0.

본 테스트는 2026-04-22 Stage 3 작업 산출물. Stage 2 (``test_check_bool_literals.py``)
의 6 그룹 패턴과 동일 구조를 유지한다.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER_PATH = REPO_ROOT / "scripts" / "check_rbac_coverage.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_rbac_coverage", CHECKER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CHECKER = _load_checker()


def _check(source: str, tmp_path: Path, filename: str = "sample.py") -> list[str]:
    """임시 파일에 소스를 쓰고 ``check_file`` 호출 결과를 반환."""
    path = tmp_path / filename
    path.write_text(source, encoding="utf-8")
    return CHECKER.check_file(path)


# ═════════════════════════════════════════════════════════════════════════
# 1. 정책 하위 호환 — 적절한 가드가 적용된 라우트는 통과한다.
# ═════════════════════════════════════════════════════════════════════════
def test_mutation_with_require_operator_param_dep_is_allowed(tmp_path: Path) -> None:
    """POST 라우트에 파라미터 ``Depends(require_operator)`` 가 있으면 통과."""
    source = (
        "from fastapi import APIRouter, Depends\n"
        "router = APIRouter()\n"
        "def require_operator():\n    pass\n"
        '@router.post("/x")\n'
        "async def create_x(user=Depends(require_operator)):\n"
        "    return {}\n"
    )
    assert _check(source, tmp_path) == []


def test_mutation_with_require_admin_param_dep_is_allowed(tmp_path: Path) -> None:
    """DELETE 라우트에 파라미터 ``Depends(require_admin)`` 이 있으면 통과."""
    source = (
        "from fastapi import APIRouter, Depends\n"
        "router = APIRouter()\n"
        "def require_admin():\n    pass\n"
        '@router.delete("/x/{id}")\n'
        "async def delete_x(id: str, user=Depends(require_admin)):\n"
        "    return {}\n"
    )
    assert _check(source, tmp_path) == []


def test_mutation_with_decorator_level_dependency_is_allowed(tmp_path: Path) -> None:
    """``@router.post(..., dependencies=[Depends(require_admin)])`` 형태도 통과."""
    source = (
        "from fastapi import APIRouter, Depends\n"
        "router = APIRouter()\n"
        "def require_admin():\n    pass\n"
        '@router.post("/x", dependencies=[Depends(require_admin)])\n'
        "async def create_x():\n"
        "    return {}\n"
    )
    assert _check(source, tmp_path) == []


def test_read_with_require_viewer_is_allowed(tmp_path: Path) -> None:
    """GET 라우트에 ``Depends(require_viewer)`` 가 있으면 통과."""
    source = (
        "from fastapi import APIRouter, Depends\n"
        "router = APIRouter()\n"
        "def require_viewer():\n    pass\n"
        '@router.get("/x")\n'
        "async def list_x(user=Depends(require_viewer)):\n"
        "    return []\n"
    )
    assert _check(source, tmp_path) == []


def test_read_with_stricter_guard_is_allowed(tmp_path: Path) -> None:
    """GET 라우트에 ``require_admin`` 같은 더 엄격한 가드가 있어도 통과."""
    source = (
        "from fastapi import APIRouter, Depends\n"
        "router = APIRouter()\n"
        "def require_admin():\n    pass\n"
        '@router.get("/audit-log")\n'
        "async def audit_log(user=Depends(require_admin)):\n"
        "    return []\n"
    )
    assert _check(source, tmp_path) == []


# ═════════════════════════════════════════════════════════════════════════
# 2. 위반 검출 — 가드 누락 / 잘못된 강도.
# ═════════════════════════════════════════════════════════════════════════
def test_mutation_with_only_require_viewer_is_flagged(tmp_path: Path) -> None:
    """mutation 에 ``require_viewer`` 만 적용된 경우는 정책 위반.

    읽기 전용 가드로 상태 변경 라우트를 보호하는 것은 사실상 무방비.
    """
    source = (
        "from fastapi import APIRouter, Depends\n"
        "router = APIRouter()\n"
        "def require_viewer():\n    pass\n"
        '@router.put("/x/{id}")\n'
        "async def update_x(id: str, user=Depends(require_viewer)):\n"
        "    return {}\n"
    )
    errors = _check(source, tmp_path)
    assert len(errors) == 1
    assert "update_x" in errors[0]
    assert "require_viewer" in errors[0]
    assert "require_operator 또는 require_admin" in errors[0]


def test_mutation_without_any_guard_is_flagged(tmp_path: Path) -> None:
    """mutation 라우트에 가드가 전혀 없으면 위반."""
    source = (
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        '@router.post("/x")\n'
        "async def create_x():\n"
        "    return {}\n"
    )
    errors = _check(source, tmp_path)
    assert len(errors) == 1
    assert "create_x" in errors[0]
    assert "의존성 누락" in errors[0]


def test_read_without_any_guard_is_flagged(tmp_path: Path) -> None:
    """read(GET) 라우트에 가드가 전혀 없으면 위반."""
    source = (
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        '@router.get("/x")\n'
        "async def list_x():\n"
        "    return []\n"
    )
    errors = _check(source, tmp_path)
    assert len(errors) == 1
    assert "list_x" in errors[0]
    assert "의존성 누락" in errors[0]


def test_sync_function_route_is_also_checked(tmp_path: Path) -> None:
    """``def`` (sync) 라우트 핸들러도 동일하게 검사된다.

    검사기는 ``FunctionDef`` 와 ``AsyncFunctionDef`` 를 모두 순회한다.
    """
    source = (
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        '@router.post("/x")\n'
        "def create_x():\n"
        "    return {}\n"
    )
    errors = _check(source, tmp_path)
    assert len(errors) == 1
    assert "create_x" in errors[0]


# ═════════════════════════════════════════════════════════════════════════
# 3. 오탐 방지 — false positive 가 없어야 한다.
# ═════════════════════════════════════════════════════════════════════════
def test_whitelisted_auth_login_without_guard_is_allowed(tmp_path: Path) -> None:
    """``(auth.py, login)`` 은 WHITELIST 이므로 가드 없이도 통과.

    비인증 엔드포인트 (로그인 자체) 에 가드를 요구하면 모순.
    """
    source = (
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        '@router.post("/login")\n'
        "async def login():\n"
        "    return {}\n"
    )
    assert _check(source, tmp_path, filename="auth.py") == []


def test_whitelisted_auth_get_me_without_guard_is_allowed(tmp_path: Path) -> None:
    """``(auth.py, get_me)`` — 자기 세션 조회는 WHITELIST 이므로 통과."""
    source = (
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        '@router.get("/me")\n'
        "async def get_me():\n"
        "    return {}\n"
    )
    assert _check(source, tmp_path, filename="auth.py") == []


def test_non_router_decorator_is_ignored(tmp_path: Path) -> None:
    """``@app.get(...)`` 처럼 ``router`` 가 아닌 객체의 데코레이터는 검사 범위 밖.

    ``_is_router_decorator`` 는 ``func.value.id == "router"`` 인 경우만 True.
    """
    source = (
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        '@app.get("/health")\n'
        "async def health():\n"
        "    return {}\n"
    )
    assert _check(source, tmp_path) == []


def test_non_http_method_router_call_is_ignored(tmp_path: Path) -> None:
    """``@router.include_router(...)`` / ``@router.middleware(...)`` 같은
    non-HTTP-method 호출은 ``MUTATION_METHODS`` / ``READ_METHODS`` 에
    속하지 않으므로 검사 범위 밖.
    """
    source = (
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "sub = APIRouter()\n"
        '@router.include_router(sub, prefix="/v1")\n'
        "def _unused():\n"
        "    pass\n"
    )
    # include_router 는 데코레이터로 쓰지 않지만, 문법상 데코레이터로
    # 쓰여도 HTTP 메서드가 아니므로 검사되지 않는다.
    assert _check(source, tmp_path) == []


def test_non_depends_default_does_not_crash(tmp_path: Path) -> None:
    """``Depends(...)`` 가 아닌 기본값(숫자/문자열/None) 은 가드로 집계되지
    않지만 파서가 죽지도 않는다.

    파라미터 default 에 ``= None`` 처럼 상수를 쓴 라우트에서 검사기가
    crash 하지 않도록 회귀 방어.
    """
    source = (
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        '@router.get("/x")\n'
        "async def get_x(limit: int = 10, cursor: str = None):\n"
        "    return []\n"
    )
    errors = _check(source, tmp_path)
    # 가드가 없으므로 위반 1 건. crash 가 아닌 정상 위반 보고 경로.
    assert len(errors) == 1
    assert "의존성 누락" in errors[0]


def test_function_without_any_decorator_is_ignored(tmp_path: Path) -> None:
    """라우트 데코레이터 자체가 없는 일반 함수는 검사 대상 아니다."""
    source = "def helper(x):\n" "    return x * 2\n" "async def aio_helper(x):\n" "    return x + 1\n"
    assert _check(source, tmp_path) == []


def test_get_current_user_only_is_still_flagged(tmp_path: Path) -> None:
    """``get_current_user`` 만 쓰는 라우트는 **인증만 있고 인가 가드 없음**
    이므로 위반으로 보고되어야 한다 (CLAUDE.md "인증 ≠ 인가" 원칙).

    이는 검사기의 존재 이유 그 자체 — ``get_current_user`` 가 RBAC 를
    대체한다는 흔한 오해를 차단한다.
    """
    source = (
        "from fastapi import APIRouter, Depends\n"
        "router = APIRouter()\n"
        "def get_current_user():\n    pass\n"
        '@router.post("/x")\n'
        "async def create_x(user=Depends(get_current_user)):\n"
        "    return {}\n"
    )
    errors = _check(source, tmp_path)
    assert len(errors) == 1
    assert "의존성 누락" in errors[0]


# ═════════════════════════════════════════════════════════════════════════
# 4. 구문 오류 처리 — 파싱 실패 파일은 한 줄 메시지로 보고.
# ═════════════════════════════════════════════════════════════════════════
def test_syntax_error_file_reports_parse_error(tmp_path: Path) -> None:
    """SyntaxError 가 발생한 파일은 ``<name>: parse error: ...`` 한 줄을
    에러 리스트로 반환한다. 다른 파일 스캔을 막지 않기 위해 예외 대신
    보고 형태를 선택한 구현 — 회귀 고정.
    """
    source = "def broken(:\n    pass\n"
    errors = _check(source, tmp_path, filename="broken.py")
    assert len(errors) == 1
    assert errors[0].startswith("broken.py: parse error:")


# ═════════════════════════════════════════════════════════════════════════
# 5. 실제 저장소 회귀 고정.
# ═════════════════════════════════════════════════════════════════════════
def test_current_repo_routes_have_zero_violations() -> None:
    """현재 ``backend/api/routes/`` 전 파일 스캔에서 0 errors.

    본 회귀 고정은 "향후 누군가 라우트 추가 시 RBAC 가드를 빠뜨리면 pytest
    가 먼저 잡는다" 는 방어선이다. CLAUDE.md "인증 ≠ 인가" 원칙의 자동 집행.
    """
    routes_dir = CHECKER.ROUTES_DIR
    assert routes_dir.exists(), f"routes dir missing: {routes_dir}"
    all_errors: list[str] = []
    for py_file in sorted(routes_dir.glob("*.py")):
        if py_file.name == "__init__.py":
            continue
        all_errors.extend(CHECKER.check_file(py_file))
    assert all_errors == [], f"RBAC 가드 누락 감지: {all_errors}"


def test_whitelist_entries_refer_to_existing_files_and_functions() -> None:
    """``WHITELIST`` 의 ``(file, function)`` 쌍이 모두 실재해야 한다.

    stale whitelist 방지 — 파일/함수명이 바뀌거나 삭제됐는데 whitelist 에
    남아 있으면 향후 실제 누락이 whitelist 와 우연히 겹쳐 silent miss 가
    생길 수 있다.
    """
    routes_dir = CHECKER.ROUTES_DIR
    for file_name, func_name in CHECKER.WHITELIST:
        target = routes_dir / file_name
        assert target.exists(), f"WHITELIST 파일 없음: {file_name}"
        tree = ast.parse(target.read_text(encoding="utf-8"))
        names = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        assert func_name in names, f"WHITELIST 의 함수가 실제 파일에 없음: {file_name}::{func_name}"


# ═════════════════════════════════════════════════════════════════════════
# 6. main() 진입 경로.
# ═════════════════════════════════════════════════════════════════════════
def test_main_returns_1_when_routes_dir_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``ROUTES_DIR`` 가 존재하지 않으면 ``main()`` 은 stderr 에 에러를
    찍고 exit code 1 을 반환한다.
    """
    missing = tmp_path / "nonexistent_routes"
    monkeypatch.setattr(CHECKER, "ROUTES_DIR", missing)
    assert CHECKER.main() == 1
    captured = capsys.readouterr()
    assert "routes 디렉토리 없음" in captured.err


def test_main_returns_0_for_current_repo(capsys: pytest.CaptureFixture[str]) -> None:
    """현재 저장소에서 ``main()`` 이 ``[PASS]`` 메시지와 함께 0 을 반환한다.

    test_current_repo_routes_have_zero_violations 와 상호 보완: 그 테스트는
    ``check_file`` 을 직접 순회하여 errors 리스트를 검증하고, 이 테스트는
    main() 진입점의 출력/exit 코드를 검증한다.
    """
    assert CHECKER.main() == 0
    captured = capsys.readouterr()
    assert "[PASS] RBAC coverage check" in captured.out
