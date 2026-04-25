"""StrategyExecutionLog ORM 모델 — 전략 앙상블 실행 이력.

DynamicEnsembleRunner 실행 완료 시 기록되며, 레짐 판정·앙상블
신호·게이트 결과·실행 상태를 포함한다. 레짐별 성과 분석, 게이트
차단 패턴 추적, 실행 시간 모니터링에 사용한다.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, Float, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base


class StrategyExecutionLog(Base):
    """전략 앙상블 실행 이력 1건."""

    __tablename__ = "strategy_execution_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(100), nullable=False)

    # 레짐 판정
    regime: Mapped[str] = mapped_column(String(32), nullable=False)
    regime_confidence: Mapped[float] = mapped_column(Float, nullable=False)

    # 앙상블 신호
    ensemble_signal: Mapped[float] = mapped_column(Float, nullable=False)
    ensemble_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    weights_used: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 행동 결정
    final_action: Mapped[str] = mapped_column(String(10), nullable=False)
    signals_generated: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    orders_submitted: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    # 게이트 결과
    gate_results: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 실행 상태
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    execution_duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))

    def __repr__(self) -> str:
        return f"<StrategyExecutionLog {self.ticker} {self.strategy_name} " f"{self.final_action} {self.status}>"
