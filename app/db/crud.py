"""
app/db/crud.py

All database read/write operations for the Incident Triage Copilot.

Agents, services, and API routes call these functions.
Nothing else touches the database directly — this is the only DB interface.

Why this pattern?
  - Easy to unit-test (mock this module, not SQLAlchemy internals)
  - One place to add caching, logging, or validation later
  - Keeps route handlers and agents clean and focused
"""

from typing import Optional
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.db.models import Incident, WorkflowResult, AuditEvent, ReviewDecision
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# INCIDENT CRUD
# ════════════════════════════════════════════════════════════════════════════

def create_incident(db: Session, incident_data: dict) -> Incident:
    """
    Persist a new incident record.

    Args:
        db:             Active database session.
        incident_data:  Dict matching Incident column names.

    Returns:
        The newly created Incident ORM object.
    """
    incident = Incident(**incident_data)
    db.add(incident)
    db.commit()
    db.refresh(incident)
    logger.info("Created incident", extra={"incident_id": incident.id})
    return incident


def get_incident(db: Session, incident_id: str) -> Optional[Incident]:
    """Fetch a single incident by ID. Returns None if not found."""
    return db.query(Incident).filter(Incident.id == incident_id).first()


def list_incidents(db: Session, limit: int = 50, offset: int = 0) -> list[Incident]:
    """
    Return a paginated list of incidents, newest first.

    Args:
        db:     Active database session.
        limit:  Max records to return (default 50).
        offset: Skip this many records (for pagination).
    """
    return (
        db.query(Incident)
        .order_by(Incident.submitted_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


def update_incident_status(
    db: Session,
    incident_id: str,
    status: str,
) -> Optional[Incident]:
    """
    Update the workflow_status field of an incident.

    Args:
        db:          Active database session.
        incident_id: UUID of the incident.
        status:      New status string (e.g. 'running', 'complete', 'failed').
    """
    incident = get_incident(db, incident_id)
    if not incident:
        logger.warning("update_incident_status: incident not found", extra={"incident_id": incident_id})
        return None
    incident.workflow_status = status
    db.commit()
    db.refresh(incident)
    return incident


# ════════════════════════════════════════════════════════════════════════════
# WORKFLOW RESULT CRUD
# ════════════════════════════════════════════════════════════════════════════

def create_workflow_result(db: Session, incident_id: str) -> WorkflowResult:
    """
    Create an empty WorkflowResult row for an incident at pipeline start.
    Agent stages fill in the individual JSON columns as they complete.
    """
    result = WorkflowResult(incident_id=incident_id)
    db.add(result)
    db.commit()
    db.refresh(result)
    return result


def get_workflow_result(db: Session, incident_id: str) -> Optional[WorkflowResult]:
    """Fetch the WorkflowResult for a given incident."""
    return (
        db.query(WorkflowResult)
        .filter(WorkflowResult.incident_id == incident_id)
        .first()
    )


def update_workflow_result(
    db: Session,
    incident_id: str,
    updates: dict,
) -> Optional[WorkflowResult]:
    """
    Partially update a WorkflowResult row with the provided fields.

    Args:
        db:          Active database session.
        incident_id: UUID of the incident.
        updates:     Dict of column names → new values to set.
    """
    result = get_workflow_result(db, incident_id)
    if not result:
        logger.warning("update_workflow_result: no result row found", extra={"incident_id": incident_id})
        return None
    for key, value in updates.items():
        setattr(result, key, value)
    db.commit()
    db.refresh(result)
    return result


# ════════════════════════════════════════════════════════════════════════════
# AUDIT EVENT CRUD
# ════════════════════════════════════════════════════════════════════════════

def create_audit_event(db: Session, event_data: dict) -> AuditEvent:
    """
    Persist a single audit event.

    Args:
        db:         Active database session.
        event_data: Dict built by audit_logger.build_audit_event().
    """
    # Remove keys that aren't AuditEvent columns
    allowed_keys = {
        "incident_id", "stage", "status", "confidence",
        "latency_ms", "retry_count", "llm_model",
        "error_message", "payload_summary", "timestamp",
    }
    clean = {k: v for k, v in event_data.items() if k in allowed_keys}

    # Convert ISO string timestamp to datetime if needed
    if "timestamp" in clean and isinstance(clean["timestamp"], str):
        from dateutil.parser import parse as parse_dt
        clean["timestamp"] = parse_dt(clean["timestamp"])

    audit_event = AuditEvent(**clean)
    db.add(audit_event)
    db.commit()
    db.refresh(audit_event)
    return audit_event


def list_audit_events(db: Session, incident_id: str) -> list[AuditEvent]:
    """Return all audit events for an incident, in chronological order."""
    return (
        db.query(AuditEvent)
        .filter(AuditEvent.incident_id == incident_id)
        .order_by(AuditEvent.timestamp.asc())
        .all()
    )


# ════════════════════════════════════════════════════════════════════════════
# REVIEW DECISION CRUD
# ════════════════════════════════════════════════════════════════════════════

def create_review_decision(
    db: Session,
    incident_id: str,
    decision: str,
    reviewer_note: Optional[str] = None,
) -> ReviewDecision:
    """
    Record a human review decision (approved / rejected).
    Also updates the workflow_result.review_status field to match.

    Args:
        db:            Active database session.
        incident_id:   UUID of the incident being reviewed.
        decision:      'approved' or 'rejected'.
        reviewer_note: Optional free-text comment from the reviewer.
    """
    review = ReviewDecision(
        incident_id=incident_id,
        decision=decision,
        reviewer_note=reviewer_note,
        decided_at=datetime.now(timezone.utc),
    )
    db.add(review)

    # Keep the WorkflowResult in sync
    result = get_workflow_result(db, incident_id)
    if result:
        result.review_status = decision

    # Keep the Incident in sync
    incident = get_incident(db, incident_id)
    if incident:
        incident.workflow_status = f"reviewed_{decision}"

    db.commit()
    db.refresh(review)
    logger.info(
        "Review decision recorded",
        extra={"incident_id": incident_id, "decision": decision},
    )
    return review


def get_latest_review(db: Session, incident_id: str) -> Optional[ReviewDecision]:
    """Return the most recent review decision for an incident."""
    return (
        db.query(ReviewDecision)
        .filter(ReviewDecision.incident_id == incident_id)
        .order_by(ReviewDecision.decided_at.desc())
        .first()
    )
