#!/usr/bin/env python3
"""환경변수 bool 표기 표준화 정적 검사기 (AST 기반 Python 검사부).

정책: ``docs/conventions/boolean-config.md``

검사 항목
---------
1. Python 코드의 ad-hoc bool 파싱 (``env_bool()`` 우회) 패턴 차단.
2. ``.env*``, ``docker-compose*.yml``, ``.github/workflows/*.yml`` 안의 bool
   환경변수 값이 표준 표기(``'true'``/``'false'``)인지 확인.

Phase 1 에서는 이미 알려진 bool env 키 화이트리스트만 강제하고, 그 외는
경고로만 출력한다 (Phase 2 에서 error 로 승격 예정).

구현 방식
---------
Python 검사부는 ``ast.parse()`` 결과를 순회하면서 다음 세 패턴을 노드 판정
으로 차단한다:

    A. ``os.environ.get(...) == "true"`` 또는 ``os.getenv(...) != "false"`` 와
       같이 환경변수 호출 결과를 문자열과 ``==``/``!=`` 로 비교.
    B. ``os.environ.get(...).lower()`` 또는 ``os.getenv(...).lower()`` 와
       같이 반환값에 ``.lower()`` 를 체이닝하는 호출.
    C. ``os.environ.get(...) in ("true", "1", ...)`` 와 같이 컨테이너 멤버십
       검사로 bool 을 파싱하는 호출.

정적 방어선은 반드시 AST 기반이어야 한다. 기존 regex 기반 구현은 다음
결손이 있었다:

    1. 중첩 괄호: ``os.environ.get("X", fallback()) == "true"`` 는
       ``[^)]*`` 가 첫 ``)`` 에서 끊겨 누락.
    2. 멀티라인 호출: 인자를 여러 줄에 걸쳐 쓰는 호출은 per-line regex 가
       본 줄 외에는 매칭하지 못해 누락.
    3. 문자열 내부 false positive: 문자열 리터럴 안의
       ``"os.environ.get(X) == 'true'"`` 같은 부분을 regex 가 매칭.
    4. 비교 순서 역전: ``"true" == os.environ.get(...)`` 는 ``==\\s*["\']``
       패턴과 어긋나 누락.

회고 레퍼런스는 ``check_loguru_style.py`` (2026-04-15 regex→AST 전환) 이다.

설정 파일 (``.env*``, ``docker-compose*.yml``, ``.github/workflows/*.yml``) 은
Python AST 파싱 대상이 아니므로 기존 KV-regex 방식을 유지한다.

Exit code: 0 = PASS, 1 = FAIL.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

# scripts/ 는 패키지가 아니므로 공통 util 을 import 하기 위해 현재 디렉토리를
# sys.path 에 명시적으로 추가. pyproject.toml [tool.ruff.lint] 가 E402 를
# ignore 하므로 아래 import 가 본 줄 뒤에 오는 것이 허용된다.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _check_utils import iter_python_files  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# 알려진 bool 환경변수 (표준 표기 강제 대상).
BOOL_ENV_KEYS = {
    "TESTING",
    "OTEL_ENABLED",
    "SCHEDULER_ENABLED",
    "AQTS_STRICT_BOOL",
    "COLLECTOR_OTLP_ENABLED",
    # Commit 3: 알림 재시도 루프 비활성화 플래그 (기본 true).
    # docs/operations/alerting-audit-2026-04.md §6.3 참조.
    "ALERT_RETRY_LOOP_ENABLED",
    # WebSocket 보안 예외: 운영+LIVE에서 ws:// 임시 허용
    "KIS_WS_INSECURE_ALLOW",
}

# 정적 검사 면제 파일 (env_bool 자체 구현 / 본 검사기 자신 등).
PYTHON_EXEMPT = {
    ROOT / "backend" / "core" / "utils" / "env.py",
    ROOT / "scripts" / "check_bool_literals.py",
}

# Python 파일 스캔 루트. iter_python_files() 가 venv/build/cache 를 제외한다
# (상세: scripts/_check_utils.py).
PYTHON_ROOTS = [ROOT / "backend", ROOT / "scripts"]
ENV_FILE_GLOBS = [".env", ".env.example", ".env.*"]
COMPOSE_GLOBS = ["docker-compose*.yml", ".github/workflows/*.yml"]

# 환경변수 호출의 함수 경로.
_ENV_CALL_FUNCS: frozenset[str] = frozenset({"os.environ.get", "os.getenv"})


def _attr_chain(node: ast.AST) -> str | None:
    """``a.b.c`` 형태의 속성/이름 체인을 점으로 연결한 문자열로 반환.

    ``os.environ.get`` 같은 chained attribute 를 식별할 때 사용한다.
    하위 노드가 ``Name`` / ``Attribute`` 가 아니면 None.
    """
    parts: list[str] = []
    cur: ast.AST | None = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    return None


def _is_env_call(node: ast.AST) -> bool:
    """노드가 ``os.environ.get(...)`` 또는 ``os.getenv(...)`` 호출인지."""
    if not isinstance(node, ast.Call):
        return False
    chain = _attr_chain(node.func)
    return chain in _ENV_CALL_FUNCS


def _is_string_constant(node: ast.AST) -> bool:
    """노드가 문자열 ``Constant`` 인지."""
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _is_string_container(node: ast.AST) -> bool:
    """노드가 하나 이상의 문자열 상수를 원소로 갖는 Tuple/List/Set 인지.

    ``in`` 비교의 우변이 이 형태면 ad-hoc bool 파싱 후보로 판정한다.
    """
    if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return False
    return any(_is_string_constant(el) for el in node.elts)


def _classify(node: ast.AST) -> str | None:
    """AST 노드가 ad-hoc 환경변수 bool 파싱 패턴이면 짧은 분류명을 반환.

    분류:
        - ``"compare_eq"`` : ``env_call() == "true"`` 류 (NotEq 포함).
        - ``"lower_chain"``: ``env_call().lower()`` 류.
        - ``"in_container"``: ``env_call() in ("true", "1", ...)`` 류.
        - ``None`` : 해당 패턴 아님.

    패턴 A/B/C 각각 1 회 판정으로 분기한다. 중첩 호출 (예: ``.lower()`` 를 한
    뒤 다시 ``==`` 하는 경우) 은 가장 바깥 노드에서 분류되어 한 번만 보고
    된다.
    """
    # 패턴 B: <env_call>.lower() 호출.
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "lower" and _is_env_call(func.value):
            return "lower_chain"

    # 패턴 A/C: Compare 노드.
    if isinstance(node, ast.Compare):
        left = node.left
        # 첫 op + 첫 comparator 만 검사한다. 체이닝된 비교는 Python 관례상
        # bool 파싱에 사용되지 않으므로 first-op 로 제한해도 실사례를 놓치지
        # 않는다.
        if not node.ops or not node.comparators:
            return None
        op = node.ops[0]
        right = node.comparators[0]

        if isinstance(op, (ast.Eq, ast.NotEq)):
            # 좌/우 어느 쪽이든 env_call vs 문자열 상수 조합이면 위반.
            if _is_env_call(left) and _is_string_constant(right):
                return "compare_eq"
            if _is_env_call(right) and _is_string_constant(left):
                return "compare_eq"
            return None

        if isinstance(op, (ast.In, ast.NotIn)):
            if _is_env_call(left) and _is_string_container(right):
                return "in_container"
            return None

    return None


def _scan_file(path: Path) -> list[tuple[int, str, str]]:
    """단일 파일에서 ad-hoc 파싱 위반을 수집.

    Returns:
        ``(line_no, classification, snippet)`` 리스트.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    source_lines = source.splitlines()
    violations: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        classification = _classify(node)
        if classification is None:
            continue
        line_no = node.lineno
        idx = line_no - 1
        snippet = source_lines[idx].strip() if 0 <= idx < len(source_lines) else ""
        violations.append((line_no, classification, snippet[:200]))
    return violations


def check_python_files() -> list[str]:
    """Python 파일 전체에서 ad-hoc 파싱 위반 메시지를 수집."""
    errors: list[str] = []
    for py_root in PYTHON_ROOTS:
        for path in iter_python_files(py_root):
            if path in PYTHON_EXEMPT:
                continue
            for lineno, _classification, snippet in _scan_file(path):
                errors.append(
                    f"{path.relative_to(ROOT)}:{lineno}: ad-hoc bool "
                    f"parsing detected; use core.utils.env.env_bool() "
                    f"instead\n    {snippet}"
                )
    return errors


_KV_RE = re.compile(r'^\s*(?P<key>[A-Z_][A-Z0-9_]*)\s*[:=]\s*["\']?(?P<value>[^"\'\s#]+)')


def check_config_files() -> list[str]:
    """환경변수 설정 파일(.env*, docker-compose*.yml, workflows/*.yml) 검사.

    AST 범위 밖이므로 KV-regex 로 유지한다.
    """
    errors: list[str] = []
    globs = ENV_FILE_GLOBS + COMPOSE_GLOBS
    for pattern in globs:
        for path in ROOT.glob(pattern):
            if not path.is_file():
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except Exception:
                continue
            for lineno, raw in enumerate(lines, 1):
                m = _KV_RE.match(raw)
                if not m:
                    continue
                key = m.group("key")
                value = m.group("value")
                if key not in BOOL_ENV_KEYS:
                    continue
                if value not in ("true", "false"):
                    errors.append(
                        f"{path.relative_to(ROOT)}:{lineno}: {key}={value} "
                        f"is not standard; use 'true' or 'false'"
                    )
    return errors


def main() -> int:
    errors = check_python_files() + check_config_files()
    if errors:
        print("✗ BOOL LITERAL CHECK FAILED")
        for e in errors:
            print(f"  {e}")
        print(f"\n  Total: {len(errors)} error(s)")
        print("  See: docs/conventions/boolean-config.md")
        return 1
    print("✓ BOOL LITERAL CHECK PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
