#!/usr/bin/env python3
"""RBAC 가드 정적 검사기.

정책: ``docs/security/rbac-policy.md`` 및 CLAUDE.md "인증(authn) ≠ 인가(authz) 분리 원칙"

검사 항목:
1. ``backend/api/routes/`` 하위 모든 라우터 파일을 AST 로 파싱.
2. 모든 ``@router.get|post|put|patch|delete`` 데코레이터가 붙은 핸들러에
   ``Depends(require_viewer|require_operator|require_admin)`` 의존성이
   직접 또는 데코레이터의 ``dependencies=[...]`` 인자를 통해 적용되어 있는지 확인.
3. 화이트리스트(자기 세션 관리 / 공개 엔드포인트)는 예외 처리.

원칙:
  - mutation (POST/PUT/PATCH/DELETE) → require_operator 또는 require_admin
  - read (GET) → require_viewer 또는 더 엄격한 가드
  - get_current_user 직사용은 화이트리스트 라우트에만 허용

Exit code: 0 = PASS, 1 = FAIL.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROUTES_DIR = ROOT / "backend" / "api" / "routes"

# 화이트리스트: get_current_user 직사용이 허용되는 (file, function) 조합
# (자기 세션 관리 / 공개 엔드포인트)
WHITELIST: set[tuple[str, str]] = {
    ("auth.py", "login"),  # 비인증
    ("auth.py", "refresh_token"),  # 토큰만으로 동작
    ("auth.py", "logout"),
    ("auth.py", "get_me"),
    ("auth.py", "mfa_enroll"),
    ("auth.py", "mfa_verify"),
    ("auth.py", "mfa_disable"),
}

REQUIRE_NAMES = {"require_viewer", "require_operator", "require_admin"}
MUTATION_METHODS = {"post", "put", "patch", "delete"}
READ_METHODS = {"get"}


def _is_router_decorator(deco: ast.expr) -> tuple[str | None, list[ast.keyword]]:
    """@router.<method>(...) 형태인지 검사하고 (method_name, keywords) 반환."""
    if not isinstance(deco, ast.Call):
        return None, []
    func = deco.func
    if not isinstance(func, ast.Attribute):
        return None, []
    if not isinstance(func.value, ast.Name) or func.value.id != "router":
        return None, []
    return func.attr.lower(), deco.keywords


def _extract_dependency_names_from_call(call: ast.Call) -> set[str]:
    """Depends(name) 호출에서 name 식별자 추출."""
    names: set[str] = set()
    if not isinstance(call.func, ast.Name) or call.func.id != "Depends":
        return names
    for arg in call.args:
        if isinstance(arg, ast.Name):
            names.add(arg.id)
    return names


def _params_dependency_names(func: ast.AsyncFunctionDef | ast.FunctionDef) -> set[str]:
    """함수 파라미터의 default 에 들어있는 Depends(...) 호출에서 이름 수집."""
    names: set[str] = set()
    defaults = func.args.defaults + func.args.kw_defaults
    for d in defaults:
        if isinstance(d, ast.Call):
            names |= _extract_dependency_names_from_call(d)
    return names


def _decorator_dependency_names(keywords: list[ast.keyword]) -> set[str]:
    """@router.X(..., dependencies=[Depends(name), ...]) 에서 이름 수집."""
    names: set[str] = set()
    for kw in keywords:
        if kw.arg != "dependencies":
            continue
        if not isinstance(kw.value, (ast.List, ast.Tuple)):
            continue
        for elt in kw.value.elts:
            if isinstance(elt, ast.Call):
                names |= _extract_dependency_names_from_call(elt)
    return names


def check_file(path: Path) -> list[str]:
    errors: list[str] = []
    rel = path.name
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as e:
        return [f"{rel}: parse error: {e}"]

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            method, keywords = _is_router_decorator(deco)
            if method is None:
                continue
            if method not in MUTATION_METHODS and method not in READ_METHODS:
                continue

            param_deps = _params_dependency_names(node)
            deco_deps = _decorator_dependency_names(keywords)
            all_deps = param_deps | deco_deps

            applied = all_deps & REQUIRE_NAMES
            wl_key = (rel, node.name)

            if applied:
                if method in MUTATION_METHODS and applied == {"require_viewer"}:
                    errors.append(
                        f"{rel}::{node.name}: mutation @router.{method} 에 require_viewer 만 적용됨 "
                        f"(require_operator 또는 require_admin 필요)"
                    )
                continue

            # 가드 미적용 — 화이트리스트 확인
            if wl_key in WHITELIST:
                continue

            errors.append(
                f"{rel}::{node.name}: @router.{method} 에 require_viewer/operator/admin 의존성 누락"
            )

    return errors


def main() -> int:
    if not ROUTES_DIR.exists():
        print(f"[ERROR] routes 디렉토리 없음: {ROUTES_DIR}", file=sys.stderr)
        return 1

    all_errors: list[str] = []
    files = sorted(ROUTES_DIR.glob("*.py"))
    for f in files:
        if f.name == "__init__.py":
            continue
        all_errors.extend(check_file(f))

    if all_errors:
        print(f"[FAIL] RBAC coverage check: {len(all_errors)} errors")
        for e in all_errors:
            print(f"  - {e}")
        return 1

    print(f"[PASS] RBAC coverage check ({len(files)} files scanned)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
