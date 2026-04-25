"""Order ORM 모델 — 주문 실행 이력 영속 계층.

001_initial_schema.py 에서 생성된 orders 테이블 + 008 확장 컬럼을
ORM 으로 매핑한다. 주문 실행 결과(OrderResult)와 감사 체인(decision_id,
strategy_id)을 DB 레벨에서 추적하여 post-mortem 분석과 브로커 대사에
사용한다.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import DateTime, Integer, Numeric, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base


class Order(Base):
    """주문 실행 이력 1건."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(50), unique=True)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    market: Mapped[str] = mapped_column(String(10), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    order_type: Mapped[str] = mapped_column(String(10), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4), nullable=True)
    filled_quantity: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    filled_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="PENDING")
    trigger_type: Mapped[str] = mapped_column(String(20), nullable=False, server_default="MANUAL")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    filled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── 008 확장 컬럼 ──
    slippage_bps: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 2), nullable=True)
    commission: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, server_default="0")
    decision_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    strategy_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<Order {self.order_id} {self.ticker} {self.side} {self.status}>"
