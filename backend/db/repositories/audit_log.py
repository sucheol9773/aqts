"""
감사 로그 서비스 (Audit Trail Service)

NFR-04 명세 구현:
- 모든 주문 체결, 리밸런싱, 설정 변경에 대한 감사 로그 기록
- 투자 의사결정 과정 추적
"""

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config.logging import logger


class AuditLogger:
    """감사 로그 기록 서비스"""

    def __init__(self, db_session: AsyncSession):
        self._db = db_session

    async def log(
        self,
        action_type: str,
        module: str,
        description: str,
        before_state: Optional[dict] = None,
        after_state: Optional[dict] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        """
        감사 로그 기록

        Args:
            action_type: 액션 유형 (ORDER_PLACED, REBALANCING_EXECUTED, PROFILE_UPDATED 등)
            module: 모듈명 (order_executor, portfolio_manager 등)
            description: 사람이 읽을 수 있는 설명
            before_state: 변경 전 상태 (JSON)
            after_state: 변경 후 상태 (JSON)
            metadata: 추가 메타데이터 (JSON)
        """
        import json

        query = text("""
            INSERT INTO audit_logs (time, action_type, module, description, before_state, after_state, metadata)
            VALUES (NOW(), :action_type, :module, :description, :before_state, :after_state, :metadata)
        """)

        try:
            await self._db.execute(
                query,
                {
                    "action_type": action_type,
                    "module": module,
                    "description": description,
                    "before_state": json.dumps(before_state, ensure_ascii=False, default=str) if before_state else None,
                    "after_state": json.dumps(after_state, ensure_ascii=False, default=str) if after_state else None,
                    "metadata": json.dumps(metadata, ensure_ascii=False, default=str) if metadata else None,
                },
            )
            await self._db.commit()
            logger.debug(f"Audit log: [{action_type}] {module} - {description}")
        except Exception as e:
            logger.error(f"Failed to write audit log: {e}")
            # 감사 로그 실패가 메인 로직을 중단시키지 않도록 함
