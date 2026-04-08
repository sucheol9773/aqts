"""PortfolioPosition ORM 모델 — PortfolioLedger 의 영속 계층.

설계 근거: docs/security/security-integrity-roadmap.md §7.3 / §9.
in-memory ``PortfolioLedger`` 와 1:1 매핑되며, 본 테이블에 저장되는
row 는 항상 ``quantity > 0`` 임을 ``CHECK`` 제약으로 강제한다 (0 잔량은
row 자체를 ``DELETE`` 로 제거).
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, String, text
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base


class PortfolioPosition(Base):
    """체결 누적 결과로 보유 중인 종목 1건."""

    __tablename__ = "portfolio_positions"
    __table_args__ = (
        CheckConstraint(
            "quantity > 0",
            name="ck_portfolio_positions_quantity_positive",
        ),
    )

    ticker: Mapped[str] = mapped_column(String(32), primary_key=True)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
