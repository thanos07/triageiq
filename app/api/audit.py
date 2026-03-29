"""
app/api/audit.py — Audit trail retrieval routes.

Routes:
  GET /api/v1/audit/{incident_id}       — full audit trail for one incident
  GET /api/v1/audit/{incident_id}/summary — lightweight summary stats
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db import crud
from app.schemas.audit import AuditTrailResponse, AuditEventResponse
from app.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()


@router.get("/audit/{incident_id}", response_model=AuditTrailResponse)
def get_audit_trail(incident_id: str, db: Session = Depends(get_db)):
    """
    Return the full audit trail for an incident — one event per pipeline stage.

    Each event includes: stage, status, confidence, latency_ms, retry_count,
    llm_model, error_message, and timestamp.
    """
    # Verify incident exists
    incident = crud.get_incident(db, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail=f"Incident not found: {incident_id}")

    events = crud.list_audit_events(db, incident_id)
    return AuditTrailResponse(
        incident_id=incident_id,
        events=[AuditEventResponse.model_validate(e) for e in events],
        total=len(events),
    )


@router.get("/audit/{incident_id}/summary")
def get_audit_summary(incident_id: str, db: Session = Depends(get_db)):
    """
    Return a lightweight summary of the audit trail — useful for the UI dashboard.
    Includes per-stage latency, confidence, and status without full payloads.
    """
    incident = crud.get_incident(db, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail=f"Incident not found: {incident_id}")

    events = crud.list_audit_events(db, incident_id)
    if not events:
        return {"incident_id": incident_id, "stages": [], "total_latency_ms": 0}

    stages = [
        {
            "stage":      e.stage,
            "status":     e.status,
            "confidence": round(e.confidence, 3) if e.confidence else None,
            "latency_ms": e.latency_ms,
            "retry_count": e.retry_count,
            "has_error":  bool(e.error_message),
        }
        for e in events
    ]

    total_latency = sum(
        e.latency_ms for e in events if e.latency_ms is not None
    )

    failed_stages = [s for s in stages if s["status"] in ("failed", "fallback")]

    return {
        "incident_id":    incident_id,
        "stages":         stages,
        "total_latency_ms": total_latency,
        "failed_stage_count": len(failed_stages),
        "all_succeeded": len(failed_stages) == 0,
    }
