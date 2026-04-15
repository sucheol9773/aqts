"""loguru 스타일 정적 검사기 회귀 테스트.

검증 범위:
    1. loguru ``logger`` 를 import 한 파일에서 ``%d``/``%s``/``%f`` posarg
       오용이 검출된다.
    2. **메시지 문자열 내부에 괄호 `(` `)` 가 포함된 경우에도 검출된다**.
       regex 기반 구현에서는 `[^,()]*?` 패턴 때문에 괄호 앞에서 매칭이
       끊겨 누락되던 회귀 사례 (backend/main.py:207 "positions=%d)") 를
       AST 기반 구현이 반드시 잡아야 한다.
    3. f-string / loguru ``{}`` 포맷 / 메시지 문자열 속 단순 ``%`` (posarg
       없음) 는 오탐하지 않는다.
    4. stdlib ``logging.getLogger(...)`` 로 얻은 logger 는 검사 대상이
       아니므로 스캔되지 않는다.
    5. ``logger.log(LEVEL, "...%s...", arg)`` 의 메시지 위치(2번째 arg)도
       올바르게 검사된다.

본 테스트는 정적 검사기의 회귀를 차단한다. 특히 (2) 는 2026-04-15 에
regex → AST 마이그레이션을 유발한 실제 운영 회귀와 1:1 대응된다.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER_PATH = REPO_ROOT / "scripts" / "check_loguru_style.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_loguru_style", CHECKER_PATH)
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
# 회귀 사례 — 메시지 문자열에 괄호가 포함된 경우
# ═════════════════════════════════════════════════════════════════════════
def test_regression_paren_in_message_is_detected(tmp_path: Path) -> None:
    """backend/main.py:207 회귀: 메시지 속 괄호로 regex 가 누락하던 케이스.

    AST 기반 구현은 메시지 문자열 내부 내용을 문자열 Constant 로 해석하므로
    괄호/이스케이프/따옴표와 무관하게 ``%d`` posarg 를 정확히 검출한다.
    """
    source = (
        "from config.logging import logger\n"
        "def f(n):\n"
        '    logger.info("PortfolioLedger hydrated from DB (positions=%d)", n)\n'
    )
    violations = _scan(source, tmp_path)
    assert len(violations) == 1
    line_no, level, snippet = violations[0]
    assert level == "info"
    assert line_no == 3
    assert "%d" in snippet


def test_percent_s_with_key_in_message_is_detected(tmp_path: Path) -> None:
    """orders.py 회귀: ``key=%s`` 형태의 단일 posarg 오용 검출."""
    source = (
        "from config.logging import logger\n"
        "def f(key):\n"
        '    logger.error("Order idempotency store_result failed key=%s", key)\n'
    )
    violations = _scan(source, tmp_path)
    assert len(violations) == 1
    assert violations[0][1] == "error"


def test_percent_f_format_is_detected(tmp_path: Path) -> None:
    """``%.2f`` 포맷 지시자 검출."""
    source = (
        "from config.logging import logger\n"
        "def f(a, b):\n"
        '    logger.info("broker_total=%.2f internal_total=%.2f", a, b)\n'
    )
    violations = _scan(source, tmp_path)
    assert len(violations) == 1


def test_multi_arg_audit_pattern_is_detected(tmp_path: Path) -> None:
    """audit_log.py 회귀: 다중 posarg 오용 검출."""
    source = (
        "from config.logging import logger\n"
        "def f(action, module, e):\n"
        "    logger.critical(\n"
        '        "Audit write failed action=%s module=%s err=%s",\n'
        "        action,\n"
        "        module,\n"
        "        e,\n"
        "    )\n"
    )
    violations = _scan(source, tmp_path)
    assert len(violations) == 1
    assert violations[0][1] == "critical"


# ═════════════════════════════════════════════════════════════════════════
# 올바른 사용 — 오탐 방지
# ═════════════════════════════════════════════════════════════════════════
def test_fstring_is_not_flagged(tmp_path: Path) -> None:
    """f-string 호출은 posarg 가 없으므로 검출되지 않는다."""
    source = "from config.logging import logger\n" "def f(n):\n" '    logger.info(f"positions={n}")\n'
    assert _scan(source, tmp_path) == []


def test_loguru_brace_format_is_not_flagged(tmp_path: Path) -> None:
    """loguru ``{}`` 포맷은 허용되므로 검출되지 않는다."""
    source = "from config.logging import logger\n" "def f(n):\n" '    logger.info("positions={}", n)\n'
    assert _scan(source, tmp_path) == []


def test_percent_in_message_without_posarg_is_not_flagged(tmp_path: Path) -> None:
    """메시지 속 ``%`` 기호 단독 사용(추가 posarg 없음)은 오탐하지 않는다."""
    source = "from config.logging import logger\n" "def f():\n" '    logger.info("usage 95% reached")\n'
    assert _scan(source, tmp_path) == []


def test_escaped_double_percent_is_not_flagged(tmp_path: Path) -> None:
    """``%%`` (이스케이프) 는 포맷 지시자가 아니므로 오탐하지 않는다."""
    source = "from config.logging import logger\n" "def f():\n" '    logger.info("literal %% sign")\n'
    assert _scan(source, tmp_path) == []


def test_stdlib_logging_is_not_scanned(tmp_path: Path) -> None:
    """stdlib ``logging`` 으로 얻은 logger 는 검사 대상이 아니다."""
    source = "import logging\n" 'logger = logging.getLogger("x")\n' "def f(n):\n" '    logger.info("positions=%d", n)\n'
    assert _scan(source, tmp_path) == []


# ═════════════════════════════════════════════════════════════════════════
# logger.log(level, message, ...) 특수 케이스
# ═════════════════════════════════════════════════════════════════════════
def test_logger_log_method_message_posarg(tmp_path: Path) -> None:
    """``logger.log(LEVEL, "...%s...", arg)`` 의 메시지(2번째 arg) 검사."""
    source = "from config.logging import logger\n" "def f(n):\n" '    logger.log("INFO", "count=%d", n)\n'
    violations = _scan(source, tmp_path)
    assert len(violations) == 1
    assert violations[0][1] == "log"


# ═════════════════════════════════════════════════════════════════════════
# 백엔드 전체 스캔 — 회귀 시 위반 0 을 유지
# ═════════════════════════════════════════════════════════════════════════
def test_backend_repository_has_zero_violations() -> None:
    """전체 백엔드 코드베이스는 위반이 0 건이어야 한다."""
    violations = CHECKER.scan()
    assert violations == [], f"Unexpected loguru style violations: {violations}"
