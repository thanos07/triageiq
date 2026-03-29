"""
app/schemas/workflow.py

Pydantic schemas for the agentic workflow.

Key design: WorkflowState is the single shared object that flows through
the pipeline. Each agent reads from it and writes its result back to it.
No global state, no side channels — just this one typed object.
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional, Any
from pydantic import BaseModel, Field, field_validator
from app.config import settings


# ── Agent result schemas ───────────────────────────────────────────────────────
# Each agent produces one of these typed outputs.
# Stored as JSON blobs in workflow_results table columns.

class SeverityResult(BaseModel):
    """Output from SeverityAgent."""
    severity_level:    str   = "unknown"    # critical | high | medium | low | unknown
    urgency:           str   = "unknown"    # immediate | high | medium | low
    incident_category: str   = "unknown"    # e.g. database, network, auth, infrastructure
    confidence:        float = 0.0
    reasoning:         str   = ""           # brief internal note (not shown in UI directly)
    fallback_used:     bool  = False        # True if LLM failed and we used safe defaults


class RootCauseResult(BaseModel):
    """Output from RootCauseAgent."""
    probable_cause:    str   = "Unable to determine root cause from available information."
    evidence_strength: str   = "low"        # high | medium | low
    confidence:        float = 0.0
    uncertainty_note:  str   = ""           # explicit note about what is unknown
    fallback_used:     bool  = False


class RunbookResult(BaseModel):
    """Output from RunbookAgent."""
    matched_runbook:   Optional[str]  = None   # name of the matched runbook entry
    actions:           list[str]      = []      # ordered list of remediation steps
    escalate:          bool           = False   # True if agent recommends escalation
    escalation_reason: Optional[str]  = None
    confidence:        float          = 0.0
    fallback_used:     bool           = False


class SummaryResult(BaseModel):
    """Output from SummaryAgent."""
    summary_text:    str   = ""      # stakeholder-facing summary paragraph
    probable_impact: str   = ""      # what is / could be affected
    next_action:     str   = ""      # single recommended next step
    confidence:      float = 0.0
    fallback_used:   bool  = False


# ── Stage audit entry (lives inside WorkflowState) ────────────────────────────

class StageAuditEntry(BaseModel):
    """
    Lightweight audit record written by the pipeline for each stage.
    This mirrors the AuditEvent DB model but lives in-memory during the run.
    """
    stage:         str
    status:        str       # started | success | failed | skipped | fallback
    confidence:    Optional[float] = None
    latency_ms:    Optional[int]   = None
    retry_count:   int             = 0
    llm_model:     Optional[str]   = None
    error_message: Optional[str]   = None
    timestamp:     datetime        = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── WorkflowState — the shared pipeline state object ──────────────────────────

class WorkflowState(BaseModel):
    """
    The single shared state object that flows through the pipeline.

    The pipeline creates one of these at the start of a run and passes it
    through each agent. Agents read previous results and write their own.

    After the pipeline completes, this object is persisted to the DB.
    """

    # ── Identity ──────────────────────────────────────────────────────────────
    incident_id: str

    # ── Input (set by normalizer before agents run) ───────────────────────────
    normalized_incident: Optional[dict[str, Any]] = None

    # ── Agent outputs (set by each agent as it completes) ─────────────────────
    severity_result:    Optional[SeverityResult]  = None
    root_cause_result:  Optional[RootCauseResult] = None
    runbook_result:     Optional[RunbookResult]   = None
    summary_result:     Optional[SummaryResult]   = None

    # ── Pipeline metadata ─────────────────────────────────────────────────────
    pipeline_status:    str = "running"    # running | complete | failed | partial_failure
    started_at:         datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at:       Optional[datetime] = None
    processing_time_s:  Optional[float]    = None

    # ── Governance ────────────────────────────────────────────────────────────
    overall_confidence: float = 0.0        # computed as mean of agent confidences
    low_confidence_flag: bool = False      # True if overall_confidence < threshold
    stage_audit_trail:  list[StageAuditEntry] = []

    # ── Human review ─────────────────────────────────────────────────────────
    review_status: str = "awaiting_human_review"

    model_config = {"arbitrary_types_allowed": True}

    def compute_overall_confidence(self) -> float:
        """
        Compute overall confidence as the mean of all completed agent scores.
        Clamps result to [MIN_CONFIDENCE_SCORE, MAX_CONFIDENCE_SCORE].
        """
        scores = []
        for result in [
            self.severity_result,
            self.root_cause_result,
            self.runbook_result,
            self.summary_result,
        ]:
            if result is not None and hasattr(result, "confidence"):
                scores.append(result.confidence)

        if not scores:
            return settings.min_confidence_score

        mean = sum(scores) / len(scores)
        clamped = max(settings.min_confidence_score, min(settings.max_confidence_score, mean))
        self.overall_confidence = round(clamped, 4)
        self.low_confidence_flag = self.overall_confidence < settings.low_confidence_threshold
        return self.overall_confidence

    def add_audit_entry(self, entry: StageAuditEntry) -> None:
        """Append a stage audit entry to the trail."""
        self.stage_audit_trail.append(entry)

    def mark_complete(self) -> None:
        """Called by the pipeline when all stages finish."""
        self.completed_at = datetime.now(timezone.utc)
        self.processing_time_s = round(
            (self.completed_at - self.started_at).total_seconds(), 3
        )
        self.compute_overall_confidence()

        failed_stages = [e for e in self.stage_audit_trail if e.status == "failed"]
        if failed_stages and len(failed_stages) < len(self.stage_audit_trail):
            self.pipeline_status = "partial_failure"
        elif failed_stages:
            self.pipeline_status = "failed"
        else:
            self.pipeline_status = "complete"


# ── API response schemas ───────────────────────────────────────────────────────

class WorkflowResultResponse(BaseModel):
    """Full workflow result returned by the API."""
    incident_id:        str
    pipeline_status:    str
    overall_confidence: float
    low_confidence_flag: bool
    processing_time_s:  Optional[float]
    review_status:      str
    severity_output:    Optional[dict]
    root_cause_output:  Optional[dict]
    runbook_output:     Optional[dict]
    summary_output:     Optional[dict]
    audit_trail:        list[dict] = []

    model_config = {"from_attributes": True}


class TriggerWorkflowResponse(BaseModel):
    """Returned immediately when a workflow run is triggered."""
    incident_id:  str
    message:      str
    status:       str
