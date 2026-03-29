"""
app/utils/audit_logger.py

Lightweight audit event system for the Incident Triage Copilot.

Every time an agent stage runs (starts, succeeds, or fails), this module
emits a structured JSON audit event. These events are:
  1. Written to the application log (INFO level)
  2. Stored in the audit_events database table (handled by db/crud.py)
  3. Returned as part of the workflow result for the UI to display

This gives us the LLMOps / governance "audit trail" that interviewers
and enterprise teams expect to see.

Usage:
    from app.utils.audit_logger import build_audit_event, AuditStage, AuditStatus

    event = build_audit_event(
        incident_id="uuid-...",
        stage=AuditStage.SEVERITY,
        status=AuditStatus.SUCCESS,
        confidence=0.82,
        latency_ms=1240,
        retry_count=0,
        llm_model="claude-3-5-haiku-20241022",
    )
"""

from enum import Enum
from datetime import datetime, timezone
from typing import Optional, Any
from app.utils.logger import get_logger

logger = get_logger(__name__)


class AuditStage(str, Enum):
    """Names for each pipeline stage that produces an audit event."""
    INGESTION   = "ingestion"
    NORMALIZATION = "normalization"
    SEVERITY    = "severity"
    ROOT_CAUSE  = "root_cause"
    RUNBOOK     = "runbook"
    SUMMARY     = "summary"
    PIPELINE    = "pipeline"


class AuditStatus(str, Enum):
    """Outcome of a pipeline stage."""
    STARTED  = "started"
    SUCCESS  = "success"
    FAILED   = "failed"
    SKIPPED  = "skipped"
    FALLBACK = "fallback"   # ran but used safe fallback due to low-quality output


def build_audit_event(
    incident_id: str,
    stage: AuditStage,
    status: AuditStatus,
    confidence: Optional[float] = None,
    latency_ms: Optional[int] = None,
    retry_count: int = 0,
    llm_model: Optional[str] = None,
    error_message: Optional[str] = None,
    payload_summary: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Build a structured audit event dictionary.

    This does NOT write to the database — the pipeline/crud layer does that.
    This function just creates the dict and logs it.

    Args:
        incident_id:     UUID of the incident being processed.
        stage:           Which pipeline stage this event is for.
        status:          Outcome of the stage (success, failed, etc.).
        confidence:      Confidence score output by the agent (0.0–1.0).
        latency_ms:      Wall-clock time the stage took in milliseconds.
        retry_count:     How many LLM retries occurred during this stage.
        llm_model:       Name of the LLM model used.
        error_message:   Exception or error description if status=FAILED.
        payload_summary: Safe subset of stage inputs/outputs for audit log
                         (keep PII-free and concise).

    Returns:
        A dict representing the audit event, ready to be persisted.
    """
    event: dict[str, Any] = {
        "event_type": "agent_stage",
        "incident_id": incident_id,
        "stage": stage.value,
        "status": status.value,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "retry_count": retry_count,
    }

    if confidence is not None:
        event["confidence"] = round(confidence, 4)

    if latency_ms is not None:
        event["latency_ms"] = latency_ms

    if llm_model:
        event["llm_model"] = llm_model

    if error_message:
        event["error_message"] = error_message

    if payload_summary:
        event["payload_summary"] = payload_summary

    # Emit to structured log so it appears in log streams / stdout
    log_level = "warning" if status in (AuditStatus.FAILED, AuditStatus.FALLBACK) else "info"
    getattr(logger, log_level)(
        f"Audit: [{stage.value}] {status.value}",
        extra={
            "incident_id": incident_id,
            "stage": stage.value,
            "status": status.value,
            "confidence": confidence,
            "latency_ms": latency_ms,
            "retry_count": retry_count,
        }
    )

    return event
