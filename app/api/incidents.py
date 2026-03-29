"""
app/api/incidents.py — Incident submission and retrieval routes.

Routes:
  POST   /api/v1/incidents              — submit a single incident
  GET    /api/v1/incidents              — list all incidents (paginated)
  GET    /api/v1/incidents/{id}         — fetch one incident
  POST   /api/v1/incidents/upload/csv   — bulk upload via CSV
  POST   /api/v1/incidents/upload/json  — bulk upload via JSON
  POST   /api/v1/incidents/load-samples — load built-in sample data
"""

import uuid
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db import crud
from app.schemas.incident import (
    IncidentInput, IncidentResponse, IncidentListResponse,
    SubmitIncidentResponse, BulkUploadResponse,
)
from app.services.normalizer import normalize_incident
from app.services.ingestion import parse_csv_upload, parse_json_upload, load_sample_incidents
from app.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()


@router.post("/incidents", response_model=SubmitIncidentResponse, status_code=201)
def submit_incident(payload: IncidentInput, db: Session = Depends(get_db)):
    """Submit a single incident. Returns incident_id. Trigger /workflow/{id}/run to triage."""
    incident_id = str(uuid.uuid4())
    normalized = normalize_incident(payload, incident_id=incident_id)
    incident_data = {
        "id": incident_id, "title": normalized.title,
        "description": normalized.description, "source": normalized.source,
        "service_name": normalized.service_name, "environment": normalized.environment,
        "raw_severity": normalized.raw_severity, "submitted_at": normalized.submitted_at,
        "raw_input": payload.model_dump(), "workflow_status": "pending",
    }
    crud.create_incident(db, incident_data)
    crud.create_workflow_result(db, incident_id)
    logger.info("Incident submitted", extra={"incident_id": incident_id})
    return SubmitIncidentResponse(
        incident_id=incident_id,
        message="Incident submitted. Call /api/v1/workflow/{incident_id}/run to start triage.",
        workflow_status="pending",
    )


@router.get("/incidents", response_model=IncidentListResponse)
def list_incidents(
    limit:  int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """Paginated list of incidents, newest first."""
    incidents = crud.list_incidents(db, limit=limit, offset=offset)
    return IncidentListResponse(
        total=len(incidents),
        incidents=[IncidentResponse.model_validate(i) for i in incidents],
    )


@router.get("/incidents/{incident_id}", response_model=IncidentResponse)
def get_incident(incident_id: str, db: Session = Depends(get_db)):
    """Fetch one incident by ID."""
    incident = crud.get_incident(db, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail=f"Incident not found: {incident_id}")
    return IncidentResponse.model_validate(incident)


@router.post("/incidents/upload/csv", response_model=BulkUploadResponse, status_code=201)
async def upload_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload incidents from a CSV file. Required column: title."""
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a .csv")
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="File is empty")
    normalized_incidents, parse_errors = parse_csv_upload(file_bytes)
    incident_ids, db_errors = [], []
    for n in normalized_incidents:
        try:
            crud.create_incident(db, {
                "id": n.incident_id, "title": n.title, "description": n.description,
                "source": n.source, "service_name": n.service_name,
                "environment": n.environment, "raw_severity": n.raw_severity,
                "submitted_at": n.submitted_at, "raw_input": {"source": "csv_upload"},
                "workflow_status": "pending",
            })
            crud.create_workflow_result(db, n.incident_id)
            incident_ids.append(n.incident_id)
        except Exception as e:
            db_errors.append(f"DB error for '{n.title[:40]}': {e}")
    return BulkUploadResponse(
        submitted=len(incident_ids), failed=len(parse_errors + db_errors),
        incident_ids=incident_ids, errors=parse_errors + db_errors,
    )


@router.post("/incidents/upload/json", response_model=BulkUploadResponse, status_code=201)
async def upload_json(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload incidents from a JSON file (array or single object)."""
    if not file.filename or not file.filename.lower().endswith(".json"):
        raise HTTPException(status_code=400, detail="File must be a .json")
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="File is empty")
    normalized_incidents, parse_errors = parse_json_upload(file_bytes)
    incident_ids, db_errors = [], []
    for n in normalized_incidents:
        try:
            crud.create_incident(db, {
                "id": n.incident_id, "title": n.title, "description": n.description,
                "source": n.source, "service_name": n.service_name,
                "environment": n.environment, "raw_severity": n.raw_severity,
                "submitted_at": n.submitted_at, "raw_input": {"source": "json_upload"},
                "workflow_status": "pending",
            })
            crud.create_workflow_result(db, n.incident_id)
            incident_ids.append(n.incident_id)
        except Exception as e:
            db_errors.append(f"DB error for '{n.title[:40]}': {e}")
    return BulkUploadResponse(
        submitted=len(incident_ids), failed=len(parse_errors + db_errors),
        incident_ids=incident_ids, errors=parse_errors + db_errors,
    )


@router.post("/incidents/load-samples", response_model=BulkUploadResponse, status_code=201)
def load_sample_data(db: Session = Depends(get_db)):
    """Load the 10 built-in sample incidents. Skips titles that already exist."""
    normalized_incidents = load_sample_incidents()
    if not normalized_incidents:
        raise HTTPException(status_code=500, detail="Failed to load sample incidents")
    existing_titles = {i.title for i in crud.list_incidents(db, limit=200)}
    incident_ids, skipped = [], []
    for n in normalized_incidents:
        if n.title in existing_titles:
            skipped.append(f"Skipped (exists): {n.title[:60]}")
            continue
        try:
            crud.create_incident(db, {
                "id": n.incident_id, "title": n.title, "description": n.description,
                "source": "sample", "service_name": n.service_name,
                "environment": n.environment, "raw_severity": n.raw_severity,
                "submitted_at": n.submitted_at, "raw_input": {"source": "sample_data"},
                "workflow_status": "pending",
            })
            crud.create_workflow_result(db, n.incident_id)
            incident_ids.append(n.incident_id)
        except Exception as e:
            skipped.append(f"Error: {e}")
    return BulkUploadResponse(
        submitted=len(incident_ids), failed=0,
        incident_ids=incident_ids, errors=skipped,
    )
