"""
app/db/models.py

SQLAlchemy ORM table definitions for the Incident Triage Copilot.

Tables:
  - Incident         : raw incident records and metadata
  - WorkflowResult   : per-agent outputs, confidence scores, final result
  - AuditEvent       : one record per pipeline stage per incident
  - ReviewDecision   : human approval / rejection record
"""

import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Text, Float, Integer,
    DateTime, ForeignKey, JSON, Boolean,
)
from app.db.database import Base


def _now() -> datetime:
    """UTC timestamp helper for column defaults."""
    return datetime.now(timezone.utc)


def _uuid() -> str:
    """Generate a new UUID string."""
    return str(uuid.uuid4())


# ── Incident ──────────────────────────────────────────────────────────────────
class Incident(Base):
    """
    Core incident record.
    Created when an incident is submitted (manual, CSV, or JSON upload).
    Updated as it moves through the workflow.
    """
    __tablename__ = "incidents"

    id              = Column(String, primary_key=True, default=_uuid)
    title           = Column(String(512), nullable=False)
    description     = Column(Text, nullable=False)
    source          = Column(String(128), nullable=True)   # e.g. "manual", "csv", "json"
    service_name    = Column(String(256), nullable=True)
    environment     = Column(String(64),  nullable=True)   # prod / staging / dev
    raw_severity    = Column(String(64),  nullable=True)   # as provided by submitter
    submitted_at    = Column(DateTime, default=_now)
    raw_input       = Column(JSON, nullable=True)          # original payload stored as-is

    # Workflow lifecycle status
    # Values: pending | running | complete | failed | partial_failure
    workflow_status = Column(String(64), default="pending")

    def __repr__(self) -> str:
        return f"<Incident id={self.id} title={self.title!r} status={self.workflow_status}>"


# ── WorkflowResult ────────────────────────────────────────────────────────────
class WorkflowResult(Base):
    """
    Stores the outputs of each agent stage for a given incident.
    One WorkflowResult row per incident (updated as the pipeline progresses).
    """
    __tablename__ = "workflow_results"

    id              = Column(String, primary_key=True, default=_uuid)
    incident_id     = Column(String, ForeignKey("incidents.id"), nullable=False, unique=True)

    # Each agent's output is stored as a JSON blob
    # This keeps the schema flexible as we iterate on agent outputs
    normalized_data     = Column(JSON, nullable=True)
    severity_output     = Column(JSON, nullable=True)   # level, urgency, category, confidence
    root_cause_output   = Column(JSON, nullable=True)   # cause, confidence, evidence_strength
    runbook_output      = Column(JSON, nullable=True)   # steps, escalate, confidence
    summary_output      = Column(JSON, nullable=True)   # text, impact, next_action, confidence

    # Aggregate stats
    overall_confidence  = Column(Float, nullable=True)
    processing_time_s   = Column(Float, nullable=True)  # total wall-clock seconds

    completed_at        = Column(DateTime, nullable=True)

    # Human review gate
    # Values: awaiting_human_review | approved | rejected
    review_status       = Column(String(64), default="awaiting_human_review")

    def __repr__(self) -> str:
        return (
            f"<WorkflowResult incident_id={self.incident_id} "
            f"review={self.review_status} confidence={self.overall_confidence}>"
        )


# ── AuditEvent ────────────────────────────────────────────────────────────────
class AuditEvent(Base):
    """
    One row per agent stage execution.
    Multiple rows per incident (one per stage: severity, root_cause, etc.).

    This is the audit trail displayed in the UI and used for LLMOps inspection.
    """
    __tablename__ = "audit_events"

    id              = Column(String, primary_key=True, default=_uuid)
    incident_id     = Column(String, ForeignKey("incidents.id"), nullable=False)

    # Which stage: ingestion | normalization | severity | root_cause | runbook | summary | pipeline
    stage           = Column(String(64), nullable=False)

    # Outcome: started | success | failed | skipped | fallback
    status          = Column(String(32), nullable=False)

    confidence      = Column(Float,   nullable=True)
    latency_ms      = Column(Integer, nullable=True)
    retry_count     = Column(Integer, default=0)
    llm_model       = Column(String(128), nullable=True)
    error_message   = Column(Text, nullable=True)

    # Safe subset of stage inputs/outputs (not full prompts — no PII leakage)
    payload_summary = Column(JSON, nullable=True)

    timestamp       = Column(DateTime, default=_now)

    def __repr__(self) -> str:
        return (
            f"<AuditEvent incident_id={self.incident_id} "
            f"stage={self.stage} status={self.status}>"
        )


# ── ReviewDecision ────────────────────────────────────────────────────────────
class ReviewDecision(Base):
    """
    Records the human reviewer's decision on a processed incident.
    One row per review action (there could be multiple if a reviewer changes
    their mind, though the UI only shows the latest).
    """
    __tablename__ = "review_decisions"

    id              = Column(String, primary_key=True, default=_uuid)
    incident_id     = Column(String, ForeignKey("incidents.id"), nullable=False)

    # Values: approved | rejected
    decision        = Column(String(32), nullable=False)
    reviewer_note   = Column(Text, nullable=True)
    decided_at      = Column(DateTime, default=_now)

    def __repr__(self) -> str:
        return (
            f"<ReviewDecision incident_id={self.incident_id} "
            f"decision={self.decision}>"
        )
