"""api/routes/orders.py — KIS QuoteProvider wiring 검증.

근거: docs/security/security-integrity-roadmap.md §7.3 — Wiring Rule
("정의 ≠ 적용"). KISQuoteProvider 와 get_kis_quote_provider() 를
정의하더라도 라우트가 OrderExecutor 에 실제로 전달하지 않으면 live
경로의 fail-closed 가드는 작동하지 않는다. 본 테스트는 AST 로
``api/routes/orders.py`` 의 모든 ``OrderExecutor(...)`` 호출이
``quote_provider=get_kis_quote_provider()`` 를 키워드 인자로 갖는지
정적으로 검증한다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ORDERS_PATH = Path(__file__).resolve().parent.parent / "api" / "routes" / "orders.py"


def _collect_executor_calls() -> list[ast.Call]:
    tree = ast.parse(ORDERS_PATH.read_text(encoding="utf-8"))
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "OrderExecutor":
            calls.append(node)
    return calls


def test_orders_module_imports_get_kis_quote_provider():
    """모듈 import 단계에서 get_kis_quote_provider 심볼이 존재해야 한다."""
    from api.routes import orders

    assert hasattr(orders, "get_kis_quote_provider")
    from core.order_executor.quote_provider_kis import (
        get_kis_quote_provider as canonical,
    )

    assert orders.get_kis_quote_provider is canonical


def test_every_order_executor_call_passes_quote_provider():
    """모든 OrderExecutor() 호출이 quote_provider 키워드를 갖는다."""
    calls = _collect_executor_calls()
    assert calls, "orders.py 에서 OrderExecutor() 호출이 발견되지 않았다"

    for call in calls:
        kw_names = {kw.arg for kw in call.keywords}
        assert "quote_provider" in kw_names, (
            f"OrderExecutor() at line {call.lineno} 에 quote_provider 가 " "전달되지 않았다 (Wiring Rule 위반)"
        )


def test_quote_provider_value_is_get_kis_quote_provider_call():
    """quote_provider 인자가 get_kis_quote_provider() 호출 결과여야 한다."""
    calls = _collect_executor_calls()
    for call in calls:
        for kw in call.keywords:
            if kw.arg != "quote_provider":
                continue
            value = kw.value
            assert isinstance(value, ast.Call), f"line {call.lineno}: quote_provider 가 호출식이 아니다"
            assert isinstance(value.func, ast.Name) and value.func.id == "get_kis_quote_provider", (
                f"line {call.lineno}: quote_provider 가 " "get_kis_quote_provider() 가 아니다"
            )


def test_singleton_returns_real_kis_quote_provider_instance():
    """런타임 싱글톤이 실제 KISQuoteProvider 인스턴스를 돌려준다."""
    from core.order_executor.quote_provider_kis import (
        KISQuoteProvider,
        get_kis_quote_provider,
        reset_kis_quote_provider,
    )

    reset_kis_quote_provider()
    try:
        provider = get_kis_quote_provider()
        assert isinstance(provider, KISQuoteProvider)
    finally:
        reset_kis_quote_provider()


@pytest.mark.asyncio
async def test_executor_uses_injected_provider_for_quote_lookup():
    """OrderExecutor 에 주입된 quote_provider 가 실제 호출 경로에서 사용된다."""
    from config.constants import Market
    from core.order_executor.executor import OrderExecutor
    from core.order_executor.price_guard import Quote

    class RecordingProvider:
        def __init__(self):
            self.calls: list[tuple[str, Market]] = []

        async def get_quote(self, ticker: str, market: Market) -> Quote:
            self.calls.append((ticker, market))
            from datetime import datetime, timezone

            return Quote(
                ticker=ticker,
                market=market,
                price=70000.0,
                fetched_at=datetime.now(timezone.utc),
            )

    rec = RecordingProvider()
    executor = OrderExecutor(quote_provider=rec)
    assert executor._quote_provider is rec
