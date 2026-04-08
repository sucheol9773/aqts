"""Bootstrap 두 곳(`main.py`, `scheduler_main.py`) 이 PortfolioLedger 를 SQL
영속 계층으로 구성하고 ``hydrate`` 를 호출하는지 AST 정적 검사로 강제한다.

Wiring Rule (정의 ≠ 적용) 의 ledger 도메인 확장이다. ``configure_portfolio_ledger``
가 단 한 번이라도 호출되지 않은 경로가 머지되면, 운영 부트스트랩이
in-memory ledger 로 회귀하여 재시작 시마다 broker 와의 mismatch 가 발생하고
``TradingGuard`` 가 즉시 kill switch 를 발화시키게 된다. 본 테스트는
그 회귀를 import 단계에서 차단한다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _calls_in_file(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


@pytest.mark.parametrize(
    "module_name",
    ["main.py", "scheduler_main.py"],
)
def test_bootstrap_configures_portfolio_ledger(module_name: str):
    path = BACKEND_ROOT / module_name
    assert path.exists(), f"{path} not found"
    calls = _calls_in_file(path)
    assert "configure_portfolio_ledger" in calls, (
        f"{module_name} 가 configure_portfolio_ledger 를 호출하지 않음 — " "PortfolioLedger DB 영속화 wiring 회귀."
    )
    assert "hydrate" in calls, (
        f"{module_name} 가 ledger.hydrate() 를 호출하지 않음 — "
        "부팅 시 cache 가 채워지지 않으면 첫 reconcile 사이클이 무조건 mismatch."
    )


@pytest.mark.parametrize(
    "module_name",
    ["main.py", "scheduler_main.py"],
)
def test_bootstrap_imports_sql_portfolio_repository(module_name: str):
    path = BACKEND_ROOT / module_name
    source = path.read_text(encoding="utf-8")
    assert (
        "SqlPortfolioLedgerRepository" in source
    ), f"{module_name} 가 SqlPortfolioLedgerRepository 를 import/사용하지 않음."
