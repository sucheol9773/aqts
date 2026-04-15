#!/usr/bin/env python3
"""loguru 스타일 정적 검사기 (AST 기반).

정책
----
``config.logging`` 에서 export 되는 ``logger`` 는 loguru 객체이며,
**stdlib logging 의 ``%`` posarg 포맷을 해석하지 않는다**. 즉 ::

    logger.info("positions=%d", n)      # ← 잘못된 예 (literal 로 남음)
    logger.error("failure: %s", exc)    # ← 잘못된 예

위 호출은 런타임 에러를 발생시키지 않고 **조용히 메시지를 literal 로 기록**
하기 때문에, 관측 가능성 결손 (silent miss) 이 된다. 회고 참조:
``docs/operations/phase1-demo-verification-2026-04-11.md §10.15``.

올바른 사용은 loguru 의 ``{}`` 포맷 또는 f-string 이다 ::

    logger.info(f"positions={n}")
    logger.info("positions={}", n)

stdlib ``logging`` 을 import 하고 ``logging.getLogger(...)`` 로 얻은 logger
는 ``%`` posarg 를 지원하므로 검사에서 제외한다.

구현 방식
---------
파일 내용을 ``ast.parse()`` 로 파싱하고, ``Call`` 노드 중 다음 조건을 모두
만족하는 호출을 위반으로 집계한다:

    1. ``func`` 가 ``Attribute`` 이고 ``value.id == "logger"``,
       ``attr`` 가 loguru 로그 레벨 메서드 중 하나.
    2. 파일이 ``from config.logging import logger`` 또는
       ``from loguru import logger`` 를 import 한다.
    3. 첫 번째 positional arg 가 ``Constant`` (문자열) 이고 ``%d``/``%s``/
       ``%f`` 등 stdlib logging 스타일 포맷 지시자를 포함한다.
    4. 두 번째 이상의 positional arg 가 존재한다 (메시지 뒤에 posarg 가
       붙어있는 경우만 silent miss 를 만든다; 단순히 문자열 내부에 ``%`` 가
       들어간 것은 무해하다).

regex 기반 구현은 메시지 문자열 내부의 괄호/따옴표 이스케이프 등에서 누락이
발생할 수 있으므로 AST 로 전환했다. 회고: backend/main.py:207 이 regex
검사를 통과했지만 실제 운영 로그에서 literal ``%d`` 로 출력된 사례.

검사 범위
---------
``backend/**/*.py`` 전체를 대상으로 하되, 위 조건 2 를 통과하지 않는 파일은
stdlib logging 사용 가능성이 있으므로 제외한다 (분리 보장).

Exit code: 0 = PASS, 1 = FAIL.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"

# loguru 로그 레벨 메서드 화이트리스트.
_LOGURU_LEVELS: frozenset[str] = frozenset(
    {
        "trace",
        "debug",
        "info",
        "success",
        "warning",
        "error",
        "critical",
        "exception",
        "log",
    }
)

# stdlib logging 스타일 % 포맷 지시자.
# %%는 이스케이프이므로 단독으로 등장한 %<글자> 만 검출한다.
_PERCENT_DIRECTIVE = re.compile(r"(?<!%)%[-+ 0-9.#]*[diouxXeEfFgGcrsa]")


def _imports_loguru_logger(tree: ast.Module) -> bool:
    """파일이 loguru/ config.logging 에서 ``logger`` 를 import 하는지 판정.

    Args:
        tree: 파일의 AST Module 노드.

    Returns:
        True = loguru logger 사용, False = stdlib logging 가능성 있음.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module not in {"config.logging", "loguru"}:
                continue
            for alias in node.names:
                if alias.name == "logger":
                    return True
    return False


def _is_logger_call(node: ast.Call) -> str | None:
    """``logger.<level>(...)`` 형태의 호출이면 level 문자열을 반환."""
    func = node.func
    if not isinstance(func, ast.Attribute):
        return None
    if not isinstance(func.value, ast.Name):
        return None
    if func.value.id != "logger":
        return None
    if func.attr not in _LOGURU_LEVELS:
        return None
    return func.attr


def _first_arg_message(node: ast.Call, level: str) -> ast.Constant | None:
    """첫 번째 positional arg 가 문자열 Constant 이면 반환, 아니면 None.

    ``logger.log(level, message, ...)`` 는 메시지가 두 번째 arg 이므로 별도
    처리한다. 그 외 레벨 메서드는 메시지가 첫 번째 arg.
    """
    arg_index = 1 if level == "log" else 0
    if len(node.args) <= arg_index:
        return None
    first = node.args[arg_index]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first
    return None


def _has_extra_posargs(node: ast.Call, level: str) -> bool:
    """메시지 뒤에 추가 positional arg 가 있는지 판정."""
    message_index = 1 if level == "log" else 0
    return len(node.args) > message_index + 1


def _scan_file(path: Path) -> list[tuple[int, str, str]]:
    """단일 파일에서 위반 호출을 수집.

    Returns:
        (line_no, level, snippet) 리스트.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        # 문법 오류가 있는 파일은 별도 루트가 처리 — 여기서는 skip.
        return []
    if not _imports_loguru_logger(tree):
        return []

    source_lines = source.splitlines()
    violations: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        level = _is_logger_call(node)
        if level is None:
            continue
        message_const = _first_arg_message(node, level)
        if message_const is None:
            continue
        if not _PERCENT_DIRECTIVE.search(message_const.value):
            continue
        if not _has_extra_posargs(node, level):
            continue
        # 위반 — 호출의 시작 라인과 스니펫 기록.
        line_no = node.lineno
        idx = line_no - 1
        snippet = source_lines[idx].strip() if 0 <= idx < len(source_lines) else ""
        violations.append((line_no, level, snippet[:160]))
    return violations


def scan() -> list[tuple[Path, int, str, str]]:
    """백엔드 전체를 순회하면서 위반을 수집."""
    results: list[tuple[Path, int, str, str]] = []
    for path in sorted(BACKEND.rglob("*.py")):
        for line_no, level, snippet in _scan_file(path):
            results.append((path.relative_to(ROOT), line_no, level, snippet))
    return results


def main() -> int:
    violations = scan()
    if not violations:
        print("✓ LOGURU STYLE CHECK PASSED — no stdlib '%' posarg usage with loguru logger")
        return 0
    print("✗ LOGURU STYLE CHECK FAILED")
    print(
        f"  Found {len(violations)} loguru call(s) using stdlib-style '%' posarg format."
    )
    print(
        "  loguru does NOT interpret '%d'/'%s'/'%f' posargs — use f-string or '{}' format."
    )
    print(
        "  Reference: docs/operations/phase1-demo-verification-2026-04-11.md §10.15"
    )
    for path, line_no, level, snippet in violations:
        print(f"    {path}:{line_no} [{level}]: {snippet}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
