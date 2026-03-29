"""
app/api/workflow.py — Pipeline trigger and result retrieval routes.

Routes:
  POST  /api/v1/workflow/{incident_id}/run   — trigger the triage pipeline
  GET   /api/v1/workflow/{incident_id}       — fetch the workflow result
  GET   /api/v1/workflow/{incident_id}/state — fetch lightweight status
"""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db import crud
from app.orchestration.pipeline import IncidentPipeline, PipelineError
from app.schemas.workflow import WorkflowResultResponse, TriggerWorkflowResponse
from app.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()


def _run_pipeline_sync(incident_id: str, db: Session) -> None:
    """
    Run the pipeline synchronously.
    Called directly for the demo — simple and reliable on a laptop.
    In production this would be dispatched to a task queue (Celery, ARQ).
    """
    try:
        pipeline = IncidentPipeline(db)
        pipeline.run(incident_id)
    except PipelineError as e:
        logger.error(f"Pipeline error for {incident_id}: {e}")
        crud.update_incident_status(db, incident_id, "failed")
    except Exception as e:
        logger.error(f"Unexpected pipeline failure for {incident_id}: {e}")
        crud.update_incident_status(db, incident_id, "failed")


@router.post("/workflow/{incident_id}/run", response_model=TriggerWorkflowResponse)
def trigger_workflow(
    incident_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Trigger the 4-stage triage pipeline for an incident.

    The pipeline runs in a FastAPI BackgroundTask so the endpoint
    returns immediately while processing happens asynchronously.
    Poll GET /workflow/{incident_id} to check completion status.

    Returns 404 if the incident doesn't exist.
    Returns 400 if the incident is already running or complete.
    """
    incident = crud.get_incident(db, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail=f"Incident not found: {incident_id}")

    if incident.workflow_status == "running":
        raise HTTPException(status_code=400, detail="Pipeline is already running for this incident")

    # Schedule pipeline in background — returns to caller immediately
    background_tasks.add_task(_run_pipeline_sync, incident_id, db)

    crud.update_incident_status(db, incident_id, "running")

    logger.info("Workflow triggered", extra={"incident_id": incident_id})

    return TriggerWorkflowResponse(
        incident_id=incident_id,
        message="Triage pipeline started. Poll GET /api/v1/workflow/{incident_id} for results.",
        status="running",
    )


@router.get("/workflow/{incident_id}", response_model=WorkflowResultResponse)
def get_workflow_result(incident_id: str, db: Session = Depends(get_db)):
    """
    Fetch the full workflow result for an incident.

    Returns all agent outputs, overall confidence, audit trail,
    and human review status. Returns 404 if no result exists yet.
    """
    result = crud.get_workflow_result(db, incident_id)
    if not result:
        raise HTTPException(
            status_code=404,
            detail=f"No workflow result found for incident: {incident_id}. "
                   "Run POST /workflow/{incident_id}/run first."
        )

    # Fetch audit trail for this incident
    audit_events = crud.list_audit_events(db, incident_id)
    audit_trail = [
        {
            "stage":         e.stage,
            "status":        e.status,
            "confidence":    e.confidence,
            "latency_ms":    e.latency_ms,
            "retry_count":   e.retry_count,
            "llm_model":     e.llm_model,
            "error_message": e.error_message,
            "timestamp":     e.timestamp.isoformat() if e.timestamp else None,
        }
        for e in audit_events
    ]

    # Determine pipeline status from incident record
    incident = crud.get_incident(db, incident_id)
    pipeline_status = incident.workflow_status if incident else "unknown"

    # Compute a low_confidence_flag from stored overall_confidence
    from app.config import settings
    overall_conf = result.overall_confidence or 0.0
    low_flag = overall_conf < settings.low_confidence_threshold and overall_conf > 0

    return WorkflowResultResponse(
        incident_id=incident_id,
        pipeline_status=pipeline_status,
        overall_confidence=overall_conf,
        low_confidence_flag=low_flag,
        processing_time_s=result.processing_time_s,
        review_status=result.review_status or "awaiting_human_review",
        severity_output=result.severity_output,
        root_cause_output=result.root_cause_output,
        runbook_output=result.runbook_output,
        summary_output=result.summary_output,
        audit_trail=audit_trail,
    )


@router.get("/workflow/{incident_id}/state")
def get_workflow_state(incident_id: str, db: Session = Depends(get_db)):
    """
    Lightweight status check — returns just the pipeline status and review state.
    Used by the UI to poll for completion without fetching the full result.
    """
    incident = crud.get_incident(db, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail=f"Incident not found: {incident_id}")
    result = crud.get_workflow_result(db, incident_id)
    return {
        "incident_id":    incident_id,
        "workflow_status": incident.workflow_status,
        "review_status":  result.review_status if result else "awaiting_human_review",
        "overall_confidence": result.overall_confidence if result else None,
    }
