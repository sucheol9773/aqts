"""
Central audit record for 7-step decision chain
DecisionRecord (Pydantic BaseModel) + DecisionRecordStore (in-memory storage)
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class DecisionRecord(BaseModel):
    """Central audit record capturing all 7 steps of decision making.

    Attributes:
        decision_id: UUID identifier for this decision
        timestamp: When the decision was initiated
        step1_input_snapshot: Raw market data, news data, financial data
        step2_features: Factor scores, technical indicators
        step3_signals: Individual strategy signals
        step4_ensemble: Combined weights and model outputs
        step5_portfolio: Target positions and weights
        step6_risk_check: TradingGuard 7-layer results
        step7_execution: Order placement and fill results
        gate_results: 9 gate pass/block results from Stage 2-B
        status: PENDING, COMPLETE, or PARTIAL
    """

    decision_id: str = Field(..., description="UUID for this decision")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="When decision was initiated")

    # 7-step decision chain
    step1_input_snapshot: Optional[Dict[str, Any]] = Field(None, description="Raw market/news/financial data")
    step2_features: Optional[Dict[str, Any]] = Field(None, description="Factor scores, technical indicators")
    step3_signals: Optional[List[Dict[str, Any]]] = Field(None, description="Individual strategy signals")
    step4_ensemble: Optional[Dict[str, Any]] = Field(None, description="Combined weights and ensemble output")
    step5_portfolio: Optional[Dict[str, Any]] = Field(None, description="Target positions and portfolio weights")
    step6_risk_check: Optional[Dict[str, Any]] = Field(None, description="TradingGuard 7-layer risk results")
    step7_execution: Optional[List[Dict[str, Any]]] = Field(None, description="Order placement and fill results")

    # External gate results reference
    gate_results: Optional[List[Dict[str, Any]]] = Field(None, description="9 gate pass/block results from Stage 2-B")

    # Status tracking
    status: str = Field(default="PENDING", description="PENDING, COMPLETE, or PARTIAL")

    model_config = {"arbitrary_types_allowed": True}


class DecisionRecordStore:
    """In-memory storage for DecisionRecords.

    Simple dict-based store. Ready for MongoDB migration.
    Thread-safe operations.
    """

    def __init__(self):
        """Initialize empty in-memory store."""
        self._store: Dict[str, DecisionRecord] = {}

    def create(self, decision_id: Optional[str] = None) -> DecisionRecord:
        """Create a new DecisionRecord.

        Args:
            decision_id: Optional UUID. Auto-generated if not provided.

        Returns:
            DecisionRecord: Newly created record with PENDING status
        """
        if decision_id is None:
            decision_id = str(uuid4())

        record = DecisionRecord(
            decision_id=decision_id,
            timestamp=datetime.utcnow(),
            status="PENDING",
        )

        self._store[decision_id] = record
        return record

    def update_step(self, decision_id: str, step_name: str, data: Any) -> Optional[DecisionRecord]:
        """Update a specific step in a decision record.

        Args:
            decision_id: The decision to update
            step_name: Step name (step1_input_snapshot, step2_features, ..., step7_execution, gate_results)
            data: Step data to store

        Returns:
            Updated DecisionRecord or None if decision_id not found

        Raises:
            ValueError: If step_name is invalid
        """
        valid_steps = {
            "step1_input_snapshot",
            "step2_features",
            "step3_signals",
            "step4_ensemble",
            "step5_portfolio",
            "step6_risk_check",
            "step7_execution",
            "gate_results",
        }

        if step_name not in valid_steps:
            raise ValueError(f"Invalid step_name: {step_name}. Must be one of {valid_steps}")

        record = self._store.get(decision_id)
        if record is None:
            return None

        # Create updated copy with new step data
        record_dict = record.model_dump()
        record_dict[step_name] = data

        # Update status if all 7 main steps are complete
        steps_complete = [
            "step1_input_snapshot",
            "step2_features",
            "step3_signals",
            "step4_ensemble",
            "step5_portfolio",
            "step6_risk_check",
            "step7_execution",
        ]
        if all(record_dict.get(step) is not None for step in steps_complete):
            record_dict["status"] = "COMPLETE"
        elif any(record_dict.get(step) is not None for step in steps_complete):
            record_dict["status"] = "PARTIAL"

        updated_record = DecisionRecord(**record_dict)
        self._store[decision_id] = updated_record
        return updated_record

    def get(self, decision_id: str) -> Optional[DecisionRecord]:
        """Retrieve a decision record by ID.

        Args:
            decision_id: The decision ID to retrieve

        Returns:
            DecisionRecord or None if not found
        """
        return self._store.get(decision_id)

    def query(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[DecisionRecord]:
        """Query decision records by date range.

        Args:
            start_date: Start of date range (inclusive). If None, no lower bound.
            end_date: End of date range (inclusive). If None, no upper bound.
            limit: Maximum number of records to return

        Returns:
            List of DecisionRecords sorted by timestamp descending, limited to limit
        """
        results = list(self._store.values())

        # Filter by date range
        if start_date is not None:
            results = [r for r in results if r.timestamp >= start_date]

        if end_date is not None:
            results = [r for r in results if r.timestamp <= end_date]

        # Sort by timestamp descending (most recent first)
        results.sort(key=lambda r: r.timestamp, reverse=True)

        # Apply limit
        return results[:limit]

    def clear(self) -> None:
        """Clear all records from store (for testing)."""
        self._store.clear()

    def count(self) -> int:
        """Get total count of records in store."""
        return len(self._store)


# Global store instance
_store_instance: Optional[DecisionRecordStore] = None


def get_decision_store() -> DecisionRecordStore:
    """Get or create the global DecisionRecordStore instance."""
    global _store_instance
    if _store_instance is None:
        _store_instance = DecisionRecordStore()
    return _store_instance
