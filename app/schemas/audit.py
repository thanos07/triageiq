"""
app/schemas/audit.py

Pydantic schemas for audit events and human review decisions.
"""

from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel


class AuditEventResponse(BaseModel):
    """Serialized audit event for API and UI consumption."""
    id:              str
    incident_id:     str
    stage:           str
    status:          str
    confidence:      Optional[float]
    latency_ms:      Optional[int]
    retry_count:     int
    llm_model:       Optional[str]
    error_message:   Optional[str]
    payload_summary: Optional[dict[str, Any]]
    timestamp:       datetime

    model_config = {"from_attributes": True}


class AuditTrailResponse(BaseModel):
    """Full audit trail for an incident."""
    incident_id: str
    events:      list[AuditEventResponse]
    total:       int


class ReviewDecisionRequest(BaseModel):
    """Body for the human review POST endpoint."""
    decision:      str   # 'approved' or 'rejected'
    reviewer_note: Optional[str] = None

    def is_valid_decision(self) -> bool:
        return self.decision in ("approved", "rejected")


class ReviewDecisionResponse(BaseModel):
    """Returned after a review decision is recorded."""
    incident_id:   str
    decision:      str
    reviewer_note: Optional[str]
    decided_at:    datetime
    message:       str

    model_config = {"from_attributes": True}
