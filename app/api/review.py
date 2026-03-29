"""
app/api/review.py — Human review decision routes.

Routes:
  POST /api/v1/review/{incident_id}  — submit an approve/reject decision
  GET  /api/v1/review/{incident_id}  — fetch the latest review decision
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db import crud
from app.schemas.audit import ReviewDecisionRequest, ReviewDecisionResponse
from app.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()


@router.post("/review/{incident_id}", response_model=ReviewDecisionResponse, status_code=201)
def submit_review(
    incident_id: str,
    payload: ReviewDecisionRequest,
    db: Session = Depends(get_db),
):
    """
    Submit a human review decision for a completed triage result.

    Decision must be 'approved' or 'rejected'.
    This updates both the review_decisions table and the
    workflow_results.review_status field.

    Returns 404 if the incident doesn't exist.
    Returns 400 if the pipeline hasn't completed yet.
    Returns 422 if decision is not 'approved' or 'rejected'.
    """
    # Validate decision value
    if not payload.is_valid_decision():
        raise HTTPException(
            status_code=422,
            detail=f"Invalid decision '{payload.decision}'. Must be 'approved' or 'rejected'."
        )

    # Verify incident exists
    incident = crud.get_incident(db, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail=f"Incident not found: {incident_id}")

    # Verify pipeline has produced a result
    result = crud.get_workflow_result(db, incident_id)
    if not result or incident.workflow_status in ("pending", "running"):
        raise HTTPException(
            status_code=400,
            detail="Cannot review an incident that has not completed triage. "
                   "Run the pipeline first."
        )

    # Persist the decision
    review = crud.create_review_decision(
        db,
        incident_id=incident_id,
        decision=payload.decision,
        reviewer_note=payload.reviewer_note,
    )

    logger.info(
        "Review decision submitted",
        extra={
            "incident_id": incident_id,
            "decision": payload.decision,
        },
    )

    return ReviewDecisionResponse(
        incident_id=incident_id,
        decision=review.decision,
        reviewer_note=review.reviewer_note,
        decided_at=review.decided_at,
        message=f"Incident {payload.decision}. Review recorded.",
    )


@router.get("/review/{incident_id}", response_model=ReviewDecisionResponse)
def get_review(incident_id: str, db: Session = Depends(get_db)):
    """
    Fetch the most recent review decision for an incident.
    Returns 404 if no review has been submitted yet.
    """
    incident = crud.get_incident(db, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail=f"Incident not found: {incident_id}")

    review = crud.get_latest_review(db, incident_id)
    if not review:
        raise HTTPException(
            status_code=404,
            detail=f"No review decision found for incident: {incident_id}"
        )

    return ReviewDecisionResponse(
        incident_id=incident_id,
        decision=review.decision,
        reviewer_note=review.reviewer_note,
        decided_at=review.decided_at,
        message=f"Latest decision: {review.decision}",
    )
