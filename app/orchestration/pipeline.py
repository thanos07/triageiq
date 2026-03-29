"""
app/orchestration/pipeline.py

IncidentPipeline: the orchestrator that runs all four agents in sequence.

This is the heart of the project. It:
  1. Creates a WorkflowState for the incident
  2. Normalizes the incident into canonical form
  3. Runs SeverityAgent → RootCauseAgent → RunbookAgent → SummaryAgent
  4. Handles per-stage errors gracefully (one failed stage does not abort the pipeline)
  5. Computes overall confidence and pipeline status
  6. Persists the final WorkflowState to the database
  7. Writes all audit events to the database

Design decision — plain Python over LangGraph:
  This pipeline has a fixed linear structure: always runs the same 4 stages
  in the same order. LangGraph adds value for dynamic branching, cycles, and
  multi-agent collaboration. For a fixed linear pipeline, plain Python is
  simpler, more readable, and easier to debug in an interview setting.

Usage:
    pipeline = IncidentPipeline(db_session)
    result = pipeline.run(incident_id)
"""

import time
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.agents.severity_agent   import SeverityAgent
from app.agents.root_cause_agent import RootCauseAgent
from app.agents.runbook_agent    import RunbookAgent
from app.agents.summary_agent    import SummaryAgent
from app.llm import get_llm_provider, BaseLLMProvider
from app.schemas.workflow import WorkflowState, StageAuditEntry
from app.schemas.incident import NormalizedIncident
from app.db import crud
from app.utils.logger import get_logger
from app.utils.audit_logger import AuditStage, AuditStatus, build_audit_event
from app.config import settings

logger = get_logger(__name__)


class PipelineError(Exception):
    """Raised when the pipeline cannot even start (missing incident, etc.)."""
    pass


class IncidentPipeline:
    """
    Orchestrates the full 4-stage incident triage pipeline.

    One instance per pipeline run is recommended (lightweight to construct).
    The LLM provider and agents are created fresh each run so config changes
    in .env are always picked up without restart.

    Args:
        db:    Active SQLAlchemy session for persisting results and audit events.
        llm:   Optional LLM provider override (used in tests with MockLLMProvider).
    """

    def __init__(self, db: Session, llm: Optional[BaseLLMProvider] = None):
        self._db  = db
        self._llm = llm or get_llm_provider()

        # Instantiate all four agents with the same LLM provider
        self._severity_agent   = SeverityAgent(self._llm)
        self._root_cause_agent = RootCauseAgent(self._llm)
        self._runbook_agent    = RunbookAgent(self._llm)
        self._summary_agent    = SummaryAgent(self._llm)

    def run(self, incident_id: str) -> WorkflowState:
        """
        Run the full triage pipeline for a given incident.

        Args:
            incident_id: UUID of an incident that exists in the DB.

        Returns:
            Completed WorkflowState with all agent outputs and audit trail.

        Raises:
            PipelineError: If the incident does not exist or has no workflow result row.
        """
        pipeline_start = time.monotonic()

        logger.info(
            "Pipeline starting",
            extra={"incident_id": incident_id, "model": self._llm.model_name},
        )

        # ── 1. Load incident from DB ───────────────────────────────────────────
        incident = crud.get_incident(self._db, incident_id)
        if not incident:
            raise PipelineError(f"Incident not found: {incident_id}")

        # ── 2. Mark incident as running ────────────────────────────────────────
        crud.update_incident_status(self._db, incident_id, "running")

        # ── 3. Initialize WorkflowState ────────────────────────────────────────
        state = WorkflowState(incident_id=incident_id)

        # ── 4. Normalize incident into canonical dict ──────────────────────────
        state = self._run_normalization_stage(state, incident)

        # ── 5. Ensure a WorkflowResult row exists in the DB ───────────────────
        existing_result = crud.get_workflow_result(self._db, incident_id)
        if not existing_result:
            crud.create_workflow_result(self._db, incident_id)

        # ── 6. Run agents in sequence ──────────────────────────────────────────
        # Each agent catches its own errors internally — the pipeline
        # continues even if one stage fails (partial_failure status).
        state = self._run_stage("SeverityAgent",   self._severity_agent,   state)
        state = self._run_stage("RootCauseAgent",  self._root_cause_agent, state)
        state = self._run_stage("RunbookAgent",    self._runbook_agent,    state)
        state = self._run_stage("SummaryAgent",    self._summary_agent,    state)

        # ── 7. Finalize state ──────────────────────────────────────────────────
        state.mark_complete()

        total_time = time.monotonic() - pipeline_start
        logger.info(
            "Pipeline complete",
            extra={
                "incident_id": incident_id,
                "status": state.pipeline_status,
                "overall_confidence": state.overall_confidence,
                "low_confidence_flag": state.low_confidence_flag,
                "processing_time_s": round(total_time, 3),
                "stages_run": len(state.stage_audit_trail),
            },
        )

        # ── 8. Persist results and audit trail ────────────────────────────────
        self._persist_results(state)
        self._persist_audit_trail(state)

        # ── 9. Update incident status ─────────────────────────────────────────
        crud.update_incident_status(self._db, incident_id, state.pipeline_status)

        return state

    # ── Stage runner ────────────────────────────────────────────────────────────

    def _run_stage(self, name: str, agent, state: WorkflowState) -> WorkflowState:
        """
        Run a single agent stage with outer error protection.

        Each agent already handles its own internal errors and writes a
        StageAuditEntry. This outer wrapper catches any truly unexpected
        exception that escapes the agent (defensive programming).

        Args:
            name:  Human-readable stage name for logging.
            agent: The agent instance to run.
            state: Current WorkflowState to pass in and receive back.

        Returns:
            Updated WorkflowState (or original state if the outer wrapper caught an error).
        """
        logger.info(f"Running stage: {name}", extra={"incident_id": state.incident_id})
        try:
            return agent.run(state)
        except Exception as e:
            # This should never happen because agents catch internally,
            # but we add this as a final safety net.
            logger.error(
                f"Unhandled error in {name}: {e}",
                extra={"incident_id": state.incident_id},
            )
            # Add a failed audit entry so the UI shows what happened
            state.add_audit_entry(StageAuditEntry(
                stage=name.lower().replace("agent", "").strip("_"),
                status=AuditStatus.FAILED.value,
                error_message=f"Unhandled exception: {str(e)}",
                llm_model=self._llm.model_name,
            ))
            return state

    # ── Normalization stage ──────────────────────────────────────────────────

    def _run_normalization_stage(
        self,
        state: WorkflowState,
        incident,  # SQLAlchemy Incident ORM object
    ) -> WorkflowState:
        """
        Convert the ORM Incident object into the normalized dict that agents consume.

        This is not an LLM call — it's pure data mapping.
        Stored in state.normalized_incident as a plain dict.
        """
        start_ms = time.monotonic()
        try:
            normalized_dict = {
                "incident_id":   incident.id,
                "title":         incident.title,
                "description":   incident.description,
                "source":        incident.source or "manual",
                "service_name":  incident.service_name or "unknown",
                "environment":   incident.environment or "unknown",
                "raw_severity":  incident.raw_severity or "unknown",
                "submitted_at":  incident.submitted_at.isoformat() if incident.submitted_at else None,
            }

            # Merge in any normalized fields already stored in workflow_result
            result_row = crud.get_workflow_result(self._db, incident.id)
            if result_row and result_row.normalized_data:
                normalized_dict.update(result_row.normalized_data)

            state.normalized_incident = normalized_dict
            latency_ms = int((time.monotonic() - start_ms) * 1000)

            state.add_audit_entry(StageAuditEntry(
                stage=AuditStage.NORMALIZATION.value,
                status=AuditStatus.SUCCESS.value,
                latency_ms=latency_ms,
                payload_summary={"fields": list(normalized_dict.keys())},
            ))

        except Exception as e:
            logger.error(f"Normalization failed: {e}", extra={"incident_id": incident.id})
            state.normalized_incident = {
                "incident_id":  incident.id,
                "title":        incident.title,
                "description":  incident.description,
                "service_name": "unknown",
                "environment":  "unknown",
                "raw_severity": "unknown",
            }
            state.add_audit_entry(StageAuditEntry(
                stage=AuditStage.NORMALIZATION.value,
                status=AuditStatus.FAILED.value,
                error_message=str(e),
            ))

        return state

    # ── Persistence ──────────────────────────────────────────────────────────

    def _persist_results(self, state: WorkflowState) -> None:
        """
        Write all agent outputs and pipeline metadata to the WorkflowResult row.
        """
        updates = {
            "normalized_data":   state.normalized_incident,
            "overall_confidence": state.overall_confidence,
            "processing_time_s":  state.processing_time_s,
            "completed_at":       state.completed_at or datetime.now(timezone.utc),
            "review_status":      "awaiting_human_review",
        }

        if state.severity_result:
            updates["severity_output"] = state.severity_result.model_dump()

        if state.root_cause_result:
            updates["root_cause_output"] = state.root_cause_result.model_dump()

        if state.runbook_result:
            updates["runbook_output"] = state.runbook_result.model_dump()

        if state.summary_result:
            updates["summary_output"] = state.summary_result.model_dump()

        try:
            crud.update_workflow_result(self._db, state.incident_id, updates)
            logger.info(
                "Workflow results persisted",
                extra={"incident_id": state.incident_id},
            )
        except Exception as e:
            logger.error(
                f"Failed to persist workflow results: {e}",
                extra={"incident_id": state.incident_id},
            )

    def _persist_audit_trail(self, state: WorkflowState) -> None:
        """
        Write each StageAuditEntry from the WorkflowState to the audit_events table.
        """
        for entry in state.stage_audit_trail:
            try:
                event_data = {
                    "incident_id":   state.incident_id,
                    "stage":         entry.stage,
                    "status":        entry.status,
                    "confidence":    entry.confidence,
                    "latency_ms":    entry.latency_ms,
                    "retry_count":   entry.retry_count,
                    "llm_model":     entry.llm_model,
                    "error_message": entry.error_message,
                    "timestamp":     entry.timestamp,
                }
                crud.create_audit_event(self._db, event_data)
            except Exception as e:
                logger.error(
                    f"Failed to persist audit event for stage={entry.stage}: {e}",
                    extra={"incident_id": state.incident_id},
                )

        logger.info(
            f"Audit trail persisted: {len(state.stage_audit_trail)} events",
            extra={"incident_id": state.incident_id},
        )
