"""
app/utils/observability.py

LLMOps observability helpers for the Incident Triage Copilot.

Provides:
  - PipelineMetrics: collects and summarizes per-run stats
  - emit_pipeline_event: structured JSON event for a completed run
  - compute_llmops_summary: aggregate stats across multiple runs

These are demo-grade LLMOps features — they show the pattern of what a
production system (Langfuse, Arize, W&B) would capture, implemented
simply enough to run on a laptop.
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Any

from app.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class StageMetric:
    """Metrics for a single pipeline stage."""
    stage:       str
    status:      str
    latency_ms:  int   = 0
    confidence:  float = 0.0
    retry_count: int   = 0
    used_fallback: bool = False
    error:       Optional[str] = None


@dataclass
class PipelineMetrics:
    """
    Collects metrics across all stages of one pipeline run.

    Created at the start of a run, stages appended as they complete,
    then summarized via to_dict() after the final stage.
    """
    incident_id:    str
    started_at:     float = field(default_factory=time.monotonic)
    stages:         list[StageMetric] = field(default_factory=list)
    llm_model:      str = ""

    def record_stage(
        self,
        stage: str,
        status: str,
        latency_ms: int = 0,
        confidence: float = 0.0,
        retry_count: int = 0,
        used_fallback: bool = False,
        error: Optional[str] = None,
    ) -> None:
        self.stages.append(StageMetric(
            stage=stage,
            status=status,
            latency_ms=latency_ms,
            confidence=confidence,
            retry_count=retry_count,
            used_fallback=used_fallback,
            error=error,
        ))

    @property
    def total_latency_ms(self) -> int:
        return sum(s.latency_ms for s in self.stages)

    @property
    def total_retries(self) -> int:
        return sum(s.retry_count for s in self.stages)

    @property
    def failed_stage_count(self) -> int:
        return sum(1 for s in self.stages if s.status in ("failed", "fallback"))

    @property
    def fallback_stage_names(self) -> list[str]:
        return [s.stage for s in self.stages if s.used_fallback or s.status == "fallback"]

    @property
    def failed_stage_names(self) -> list[str]:
        return [s.stage for s in self.stages if s.status == "failed"]

    @property
    def mean_confidence(self) -> float:
        scores = [s.confidence for s in self.stages if s.confidence > 0]
        return round(sum(scores) / len(scores), 4) if scores else 0.0

    @property
    def wall_time_s(self) -> float:
        return round(time.monotonic() - self.started_at, 3)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a structured dict for logging and storage."""
        return {
            "incident_id":        self.incident_id,
            "llm_model":          self.llm_model,
            "total_latency_ms":   self.total_latency_ms,
            "wall_time_s":        self.wall_time_s,
            "stage_count":        len(self.stages),
            "failed_stage_count": self.failed_stage_count,
            "total_retries":      self.total_retries,
            "mean_confidence":    self.mean_confidence,
            "fallback_stages":    self.fallback_stage_names,
            "failed_stages":      self.failed_stage_names,
            "stages": [
                {
                    "stage":        s.stage,
                    "status":       s.status,
                    "latency_ms":   s.latency_ms,
                    "confidence":   round(s.confidence, 4),
                    "retry_count":  s.retry_count,
                    "used_fallback": s.used_fallback,
                    "error":        s.error,
                }
                for s in self.stages
            ],
        }


def emit_pipeline_event(
    incident_id: str,
    pipeline_status: str,
    overall_confidence: float,
    processing_time_s: float,
    stage_count: int,
    failed_stage_count: int,
    total_retries: int,
    low_confidence_flag: bool,
    escalate: bool,
    llm_model: str,
) -> dict[str, Any]:
    """
    Emit a structured JSON event for a completed pipeline run.

    This is the primary LLMOps event — one per incident triage run.
    In production, this would be shipped to Langfuse, Datadog, or
    a custom data warehouse. Here it is logged at INFO level.

    Returns the event dict (also logged).
    """
    event = {
        "event_type":          "pipeline_complete",
        "timestamp":           datetime.now(timezone.utc).isoformat(),
        "incident_id":         incident_id,
        "pipeline_status":     pipeline_status,
        "overall_confidence":  round(overall_confidence, 4),
        "processing_time_s":   round(processing_time_s, 3),
        "stage_count":         stage_count,
        "failed_stage_count":  failed_stage_count,
        "total_retries":       total_retries,
        "low_confidence_flag": low_confidence_flag,
        "escalation_recommended": escalate,
        "llm_model":           llm_model,
        # Derived quality signals
        "pipeline_health":     _pipeline_health(pipeline_status, failed_stage_count, low_confidence_flag),
    }

    logger.info("Pipeline event emitted", extra=event)
    return event


def _pipeline_health(
    status: str,
    failed_stages: int,
    low_conf: bool,
) -> str:
    """
    Derive a simple health label for a pipeline run.
    Used for dashboard colouring and alerting.
    """
    if status == "failed" or failed_stages >= 3:
        return "degraded"
    if failed_stages > 0 or low_conf:
        return "partial"
    return "healthy"


def compute_llmops_summary(audit_events: list[dict]) -> dict[str, Any]:
    """
    Compute aggregate LLMOps stats from a list of audit event dicts.

    Useful for a dashboard showing system-wide health across all incidents.

    Args:
        audit_events: List of AuditEvent dicts (from DB or API).

    Returns:
        Summary dict with mean latency, mean confidence, failure rate, etc.
    """
    if not audit_events:
        return {"total_events": 0}

    latencies   = [e["latency_ms"] for e in audit_events if e.get("latency_ms")]
    confidences = [e["confidence"] for e in audit_events if e.get("confidence")]
    retries     = [e.get("retry_count", 0) for e in audit_events]
    statuses    = [e.get("status", "") for e in audit_events]

    failure_count  = sum(1 for s in statuses if s in ("failed", "fallback"))
    success_count  = sum(1 for s in statuses if s == "success")

    return {
        "total_events":       len(audit_events),
        "success_count":      success_count,
        "failure_count":      failure_count,
        "fallback_rate":      round(failure_count / len(audit_events), 4) if audit_events else 0,
        "mean_latency_ms":    round(sum(latencies) / len(latencies)) if latencies else 0,
        "p95_latency_ms":     _percentile(latencies, 95) if latencies else 0,
        "mean_confidence":    round(sum(confidences) / len(confidences), 4) if confidences else 0,
        "total_retries":      sum(retries),
        "stages_with_retries": sum(1 for r in retries if r > 0),
    }


def _percentile(values: list[int | float], pct: int) -> int:
    """Simple percentile calculation without numpy dependency."""
    if not values:
        return 0
    sorted_vals = sorted(values)
    idx = max(0, int(len(sorted_vals) * pct / 100) - 1)
    return int(sorted_vals[idx])
