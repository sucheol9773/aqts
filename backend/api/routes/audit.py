"""
Stage 4 Decision Audit Trail API Routes

Provides query endpoints for accessing the 7-step decision audit chain + GateResults
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query

from api.middleware.rbac import require_operator, require_viewer
from api.schemas.common import APIResponse
from config.logging import logger
from core.audit import DecisionRecord, get_decision_store

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.post("/decisions/{decision_id}", response_model=APIResponse[DecisionRecord])
async def create_decision(
    decision_id: Optional[str] = None,
    current_user=Depends(require_operator),
):
    """Create a new decision record.

    Args:
        decision_id: Optional UUID. Auto-generated if not provided.
        current_user: Current authenticated user

    Returns:
        APIResponse with newly created DecisionRecord
    """
    try:
        store = get_decision_store()
        record = store.create(decision_id=decision_id)
        return APIResponse(success=True, data=record)
    except Exception as e:
        logger.error(f"Decision creation error: {e}")
        return APIResponse(success=False, message=f"Decision creation failed: {str(e)}")


@router.get("/decisions/{decision_id}", response_model=APIResponse[DecisionRecord])
async def get_decision(
    decision_id: str,
    current_user=Depends(require_viewer),
):
    """Get a full 7-step decision chain + GateResults by decision_id.

    Args:
        decision_id: The decision ID to retrieve
        current_user: Current authenticated user

    Returns:
        APIResponse with full DecisionRecord or 404 error
    """
    try:
        store = get_decision_store()
        record = store.get(decision_id)

        if record is None:
            return APIResponse(success=False, message=f"Decision {decision_id} not found")

        return APIResponse(success=True, data=record)
    except Exception as e:
        logger.error(f"Decision retrieval error: {e}")
        return APIResponse(success=False, message=f"Decision retrieval failed: {str(e)}")


@router.get("/decisions/", response_model=APIResponse[List[DecisionRecord]])
async def list_decisions(
    start_date: Optional[str] = Query(None, description="Start date (ISO format)"),
    end_date: Optional[str] = Query(None, description="End date (ISO format)"),
    limit: int = Query(100, ge=1, le=1000, description="Max results"),
    current_user=Depends(require_viewer),
):
    """List recent decision records with optional date range filtering.

    Args:
        start_date: Optional start date in ISO format
        end_date: Optional end date in ISO format
        limit: Maximum number of results (1-1000, default 100)
        current_user: Current authenticated user

    Returns:
        APIResponse with list of DecisionRecords
    """
    try:
        store = get_decision_store()

        # Parse date range if provided
        parsed_start = None
        parsed_end = None

        if start_date:
            try:
                parsed_start = datetime.fromisoformat(start_date)
            except ValueError:
                return APIResponse(success=False, message=f"Invalid start_date format: {start_date}")

        if end_date:
            try:
                parsed_end = datetime.fromisoformat(end_date)
            except ValueError:
                return APIResponse(success=False, message=f"Invalid end_date format: {end_date}")

        records = store.query(start_date=parsed_start, end_date=parsed_end, limit=limit)
        return APIResponse(success=True, data=records)
    except Exception as e:
        logger.error(f"Decision list error: {e}")
        return APIResponse(success=False, message=f"Decision list failed: {str(e)}")


@router.post("/decisions/{decision_id}/steps/{step_name}")
async def update_decision_step(
    decision_id: str,
    step_name: str,
    step_data: dict,
    current_user=Depends(require_operator),
):
    """Update a specific step in a decision record.

    Args:
        decision_id: The decision ID to update
        step_name: Step name (step1_input_snapshot, step2_features, etc.)
        step_data: Data to store for this step
        current_user: Current authenticated user

    Returns:
        APIResponse with updated DecisionRecord or error
    """
    try:
        store = get_decision_store()
        record = store.update_step(decision_id, step_name, step_data)

        if record is None:
            return APIResponse(success=False, message=f"Decision {decision_id} not found")

        return APIResponse(success=True, data=record)
    except ValueError as e:
        logger.error(f"Invalid step: {e}")
        return APIResponse(success=False, message=str(e))
    except Exception as e:
        logger.error(f"Decision update error: {e}")
        return APIResponse(success=False, message=f"Decision update failed: {str(e)}")
