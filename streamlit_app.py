"""
streamlit_app.py

Enterprise Incident Triage Copilot — Streamlit Frontend

Sections:
  1. Submit Incident   — manual text entry
  2. Upload Incidents  — CSV / JSON file upload
  3. Incident List     — browse and select incidents
  4. Triage Dashboard  — run pipeline, view all agent outputs
  5. Audit Trail       — per-stage LLMOps viewer

Run with:
    streamlit run streamlit_app.py
"""

import time
import json
import threading
import uuid
from urllib.parse import urlparse, parse_qs

import streamlit as st
from datetime import datetime

# ── In-process service layer (no FastAPI required) ────────────────────────────
# This file used to call FastAPI over HTTP. It now imports the app modules
# directly. The original `api(method, path, ...)` helper is preserved as an
# in-process router so the UI code below is untouched.
from app.db.database import SessionLocal, init_db, check_db_connection
from app.db import crud
from app.orchestration.pipeline import IncidentPipeline, PipelineError
from app.schemas.incident import (
    IncidentInput,
    IncidentResponse,
)
from app.services.normalizer import normalize_incident
from app.services.ingestion import (
    parse_csv_upload,
    parse_json_upload,
    load_sample_incidents,
)
from app.config import settings


# ── Page setup (MUST be the first Streamlit call) ─────────────────────────────
st.set_page_config(
    page_title="Incident Triage Copilot",
    page_icon="🔺",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ── One-time DB initialization (runs once per Streamlit process) ──────────────
@st.cache_resource
def _bootstrap() -> bool:
    """Create tables on first run. cache_resource ensures this only runs once."""
    init_db()
    return True


_bootstrap()


# ── Config ─────────────────────────────────────────────────────────────────────
POLL_INTERVAL_S = 1.5
POLL_MAX_ATTEMPTS = 40

# ── Global CSS ─────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* ── Fonts ── */
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@300;400;500;600&display=swap');

html, body, [class*="css"] {
    font-family: 'IBM Plex Sans', sans-serif;
}

/* ── Layout ── */
.block-container { padding-top: 1.8rem; padding-bottom: 2rem; }
section[data-testid="stSidebar"] { background: #0f1117; border-right: 1px solid #1e2130; }
section[data-testid="stSidebar"] * { color: #c8ccd8 !important; }
section[data-testid="stSidebar"] .stSelectbox label { color: #8890a4 !important; font-size: 0.75rem; }

/* ── Cards ── */
.card {
    background: #16192a;
    border: 1px solid #1e2540;
    border-radius: 8px;
    padding: 1.2rem 1.4rem;
    margin-bottom: 1rem;
}
.card-title {
    font-size: 0.7rem;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: #5a6480;
    margin-bottom: 0.5rem;
}
.card-value {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 1.5rem;
    font-weight: 500;
    color: #e8eaf0;
}

/* ── Severity badges ── */
.badge {
    display: inline-block;
    padding: 0.18rem 0.6rem;
    border-radius: 4px;
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    font-family: 'IBM Plex Mono', monospace;
}
.badge-critical { background: #3d0f0f; color: #ff6b6b; border: 1px solid #5a1a1a; }
.badge-high     { background: #2d1a00; color: #ff9f43; border: 1px solid #4a2e00; }
.badge-medium   { background: #1a2000; color: #c9e265; border: 1px solid #2d3800; }
.badge-low      { background: #001a2d; color: #54a0ff; border: 1px solid #00294a; }
.badge-unknown  { background: #1a1a2d; color: #8890a4; border: 1px solid #2a2a40; }
.badge-complete { background: #001a0f; color: #26de81; border: 1px solid #003018; }
.badge-running  { background: #1a1a00; color: #fed330; border: 1px solid #2d2d00; }
.badge-pending  { background: #12121e; color: #778ca3; border: 1px solid #1e1e30; }
.badge-failed   { background: #200010; color: #ff5f7e; border: 1px solid #3d001a; }
.badge-approved { background: #001a0f; color: #26de81; border: 1px solid #003018; }
.badge-rejected { background: #3d0f0f; color: #ff6b6b; border: 1px solid #5a1a1a; }
.badge-awaiting { background: #12121e; color: #778ca3; border: 1px solid #1e1e30; }

/* ── Confidence bar ── */
.conf-bar-wrap { background: #1e2130; border-radius: 4px; height: 6px; width: 100%; margin-top: 6px; }
.conf-bar-fill { height: 6px; border-radius: 4px; }

/* ── Pipeline stage row ── */
.stage-row {
    display: flex; align-items: center; gap: 10px;
    padding: 0.55rem 0.8rem;
    border-radius: 6px;
    background: #12141f;
    border: 1px solid #1e2130;
    margin-bottom: 6px;
    font-size: 0.84rem;
}
.stage-icon { font-size: 1rem; width: 1.4rem; text-align: center; }
.stage-name { flex: 1; color: #c8ccd8; font-weight: 500; }
.stage-conf { font-family: 'IBM Plex Mono', monospace; font-size: 0.78rem; color: #778ca3; }
.stage-lat  { font-family: 'IBM Plex Mono', monospace; font-size: 0.72rem; color: #4a5270; }

/* ── Agent output panels ── */
.agent-panel {
    background: #12141f;
    border: 1px solid #1e2130;
    border-radius: 8px;
    padding: 1rem 1.2rem;
    margin-bottom: 0.8rem;
}
.agent-label {
    font-size: 0.68rem;
    font-weight: 600;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: #4a5270;
    margin-bottom: 0.4rem;
}
.agent-content { color: #c8ccd8; font-size: 0.9rem; line-height: 1.6; }

/* ── Action steps ── */
.action-step {
    display: flex; gap: 10px; align-items: flex-start;
    padding: 0.4rem 0.6rem;
    background: #0f111a;
    border-left: 2px solid #1e2540;
    border-radius: 0 4px 4px 0;
    margin-bottom: 5px;
    font-size: 0.84rem;
    color: #b0b8cc;
    font-family: 'IBM Plex Mono', monospace;
}
.action-step.caution {
    border-left-color: #ff9f43;
    color: #ffd190;
    background: #1a1200;
}
.step-num { color: #4a5270; min-width: 1.4rem; }

/* ── Audit row ── */
.audit-row {
    display: grid;
    grid-template-columns: 120px 70px 90px 80px 80px 1fr;
    gap: 8px;
    align-items: center;
    padding: 0.45rem 0.8rem;
    border-radius: 5px;
    font-size: 0.8rem;
    border-bottom: 1px solid #1a1c2a;
}
.audit-row:hover { background: #12141f; }
.audit-header {
    font-size: 0.65rem;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: #3a4060;
}
.mono { font-family: 'IBM Plex Mono', monospace; }

/* ── Summary box ── */
.summary-box {
    background: linear-gradient(135deg, #0f1a0f 0%, #0a1018 100%);
    border: 1px solid #1a3020;
    border-radius: 8px;
    padding: 1.2rem 1.4rem;
}
.summary-text { color: #a8dfc0; font-size: 0.92rem; line-height: 1.7; }

/* ── Escalation banner ── */
.escalation-banner {
    background: #2d1500;
    border: 1px solid #5a3000;
    border-radius: 6px;
    padding: 0.7rem 1rem;
    color: #ffbe76;
    font-size: 0.86rem;
    margin-bottom: 0.8rem;
}

/* ── Low confidence warning ── */
.low-conf-banner {
    background: #1a1200;
    border: 1px solid #3d2d00;
    border-radius: 6px;
    padding: 0.7rem 1rem;
    color: #feca57;
    font-size: 0.85rem;
    margin-bottom: 0.8rem;
}

/* ── Sidebar nav ── */
.nav-item {
    padding: 0.5rem 0.8rem;
    border-radius: 5px;
    margin-bottom: 3px;
    cursor: pointer;
    font-size: 0.86rem;
    color: #8890a4;
    transition: all 0.15s;
}
.nav-item:hover { background: #1a1d2e; color: #c8ccd8; }

/* ── Misc ── */
.muted { color: #4a5270; font-size: 0.8rem; }
.page-title {
    font-size: 1.3rem;
    font-weight: 600;
    color: #e8eaf0;
    margin-bottom: 0.2rem;
}
.page-sub { color: #5a6480; font-size: 0.85rem; margin-bottom: 1.4rem; }
.divider { border: none; border-top: 1px solid #1a1c2a; margin: 1.2rem 0; }
.inc-row {
    display: flex; gap: 10px; align-items: center;
    padding: 0.55rem 0.8rem;
    border-radius: 6px;
    background: #12141f;
    border: 1px solid #1a1c2a;
    margin-bottom: 5px;
    cursor: pointer;
    font-size: 0.84rem;
}
.inc-title { flex: 1; color: #c8ccd8; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.inc-meta  { color: #4a5270; font-size: 0.75rem; font-family: 'IBM Plex Mono', monospace; }
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# In-process router — replaces the previous HTTP `api()` helper.
#
# The UI code below still calls `api("get", "/incidents")` exactly as before.
# Instead of hitting FastAPI over HTTP, this dispatches to the same service
# functions that the FastAPI routes call. Each call gets its own short-lived
# SQLAlchemy session so behavior matches the FastAPI request lifecycle.
#
# Pipeline runs happen in a background thread so the UI's existing polling
# loops continue to work without modification.
# ══════════════════════════════════════════════════════════════════════════════

def _session():
    """Yield a short-lived DB session. Mirrors FastAPI's get_db dependency."""
    db = SessionLocal()
    try:
        return db
    except Exception:
        db.close()
        raise


def _orm_to_dict(obj) -> dict:
    """Serialize a SQLAlchemy Incident row into the dict shape the UI expects."""
    return IncidentResponse.model_validate(obj).model_dump(mode="json")


def _run_pipeline_async(incident_id: str) -> None:
    """Fire-and-forget pipeline runner (mirrors FastAPI BackgroundTasks)."""
    def worker():
        db = SessionLocal()
        try:
            IncidentPipeline(db).run(incident_id)
        except PipelineError as e:
            try:
                crud.update_incident_status(db, incident_id, "failed")
            except Exception:
                pass
        except Exception:
            try:
                crud.update_incident_status(db, incident_id, "failed")
            except Exception:
                pass
        finally:
            db.close()

    threading.Thread(target=worker, daemon=True).start()


# ── Route handlers (each opens its own session) ───────────────────────────────

def _submit_incident(body: dict) -> dict:
    payload = IncidentInput(**body)
    incident_id = str(uuid.uuid4())
    normalized = normalize_incident(payload, incident_id=incident_id)
    db = _session()
    try:
        crud.create_incident(db, {
            "id": incident_id, "title": normalized.title,
            "description": normalized.description, "source": normalized.source,
            "service_name": normalized.service_name, "environment": normalized.environment,
            "raw_severity": normalized.raw_severity, "submitted_at": normalized.submitted_at,
            "raw_input": payload.model_dump(), "workflow_status": "pending",
        })
        crud.create_workflow_result(db, incident_id)
    finally:
        db.close()
    return {"incident_id": incident_id, "workflow_status": "pending",
            "message": "Incident submitted."}


def _list_incidents(limit: int = 20, offset: int = 0) -> dict:
    db = _session()
    try:
        rows = crud.list_incidents(db, limit=limit, offset=offset)
        return {"total": len(rows), "incidents": [_orm_to_dict(r) for r in rows]}
    finally:
        db.close()


def _get_incident(incident_id: str) -> dict:
    db = _session()
    try:
        incident = crud.get_incident(db, incident_id)
        if not incident:
            raise LookupError(f"Incident not found: {incident_id}")
        return _orm_to_dict(incident)
    finally:
        db.close()


def _bulk_persist(normalized_incidents, source_tag: str,
                  parse_errors: list[str]) -> dict:
    """Shared helper for CSV / JSON / sample uploads."""
    incident_ids, db_errors = [], []
    db = _session()
    try:
        for n in normalized_incidents:
            try:
                crud.create_incident(db, {
                    "id": n.incident_id, "title": n.title, "description": n.description,
                    "source": n.source, "service_name": n.service_name,
                    "environment": n.environment, "raw_severity": n.raw_severity,
                    "submitted_at": n.submitted_at,
                    "raw_input": {"source": source_tag},
                    "workflow_status": "pending",
                })
                crud.create_workflow_result(db, n.incident_id)
                incident_ids.append(n.incident_id)
            except Exception as e:
                db_errors.append(f"DB error for '{n.title[:40]}': {e}")
    finally:
        db.close()
    return {
        "submitted": len(incident_ids),
        "failed": len(parse_errors) + len(db_errors),
        "incident_ids": incident_ids,
        "errors": parse_errors + db_errors,
    }


def _upload_csv(file_bytes: bytes) -> dict:
    if not file_bytes:
        raise ValueError("File is empty")
    normalized, parse_errors = parse_csv_upload(file_bytes)
    return _bulk_persist(normalized, "csv_upload", parse_errors)


def _upload_json(file_bytes: bytes) -> dict:
    if not file_bytes:
        raise ValueError("File is empty")
    normalized, parse_errors = parse_json_upload(file_bytes)
    return _bulk_persist(normalized, "json_upload", parse_errors)


def _load_samples() -> dict:
    normalized = load_sample_incidents()
    if not normalized:
        raise RuntimeError("Failed to load sample incidents")
    db = _session()
    try:
        existing_titles = {i.title for i in crud.list_incidents(db, limit=200)}
    finally:
        db.close()
    fresh = [n for n in normalized if n.title not in existing_titles]
    skipped = [f"Skipped (exists): {n.title[:60]}"
               for n in normalized if n.title in existing_titles]
    return _bulk_persist(fresh, "sample_data", skipped)


def _trigger_workflow(incident_id: str) -> dict:
    db = _session()
    try:
        incident = crud.get_incident(db, incident_id)
        if not incident:
            raise LookupError(f"Incident not found: {incident_id}")
        if incident.workflow_status == "running":
            raise RuntimeError("Pipeline is already running for this incident")
        crud.update_incident_status(db, incident_id, "running")
    finally:
        db.close()
    _run_pipeline_async(incident_id)
    return {"incident_id": incident_id, "status": "running",
            "message": "Triage pipeline started."}


def _get_workflow_result(incident_id: str) -> dict:
    db = _session()
    try:
        result = crud.get_workflow_result(db, incident_id)
        if not result:
            raise LookupError(
                f"No workflow result found for incident: {incident_id}")
        events = crud.list_audit_events(db, incident_id)
        audit_trail = [{
            "stage": e.stage, "status": e.status, "confidence": e.confidence,
            "latency_ms": e.latency_ms, "retry_count": e.retry_count,
            "llm_model": e.llm_model, "error_message": e.error_message,
            "timestamp": e.timestamp.isoformat() if e.timestamp else None,
        } for e in events]
        incident = crud.get_incident(db, incident_id)
        pipeline_status = incident.workflow_status if incident else "unknown"
        overall_conf = result.overall_confidence or 0.0
        low_flag = (overall_conf < settings.low_confidence_threshold
                    and overall_conf > 0)
        return {
            "incident_id": incident_id,
            "pipeline_status": pipeline_status,
            "overall_confidence": overall_conf,
            "low_confidence_flag": low_flag,
            "processing_time_s": result.processing_time_s,
            "review_status": result.review_status or "awaiting_human_review",
            "severity_output": result.severity_output,
            "root_cause_output": result.root_cause_output,
            "runbook_output": result.runbook_output,
            "summary_output": result.summary_output,
            "audit_trail": audit_trail,
        }
    finally:
        db.close()


def _get_workflow_state(incident_id: str) -> dict:
    db = _session()
    try:
        incident = crud.get_incident(db, incident_id)
        if not incident:
            raise LookupError(f"Incident not found: {incident_id}")
        result = crud.get_workflow_result(db, incident_id)
        return {
            "incident_id": incident_id,
            "workflow_status": incident.workflow_status,
            "review_status": (result.review_status if result
                              else "awaiting_human_review"),
            "overall_confidence": (result.overall_confidence if result else None),
        }
    finally:
        db.close()


def _get_audit_trail(incident_id: str) -> dict:
    db = _session()
    try:
        incident = crud.get_incident(db, incident_id)
        if not incident:
            raise LookupError(f"Incident not found: {incident_id}")
        events = crud.list_audit_events(db, incident_id)
        serialized = [{
            "id": e.id, "incident_id": e.incident_id, "stage": e.stage,
            "status": e.status, "confidence": e.confidence,
            "latency_ms": e.latency_ms, "retry_count": e.retry_count,
            "llm_model": e.llm_model, "error_message": e.error_message,
            "payload_summary": e.payload_summary,
            "timestamp": e.timestamp.isoformat() if e.timestamp else None,
        } for e in events]
        return {"incident_id": incident_id, "events": serialized,
                "total": len(serialized)}
    finally:
        db.close()


def _get_audit_summary(incident_id: str) -> dict:
    db = _session()
    try:
        events = crud.list_audit_events(db, incident_id)
        if not events:
            return {"incident_id": incident_id, "stages": [],
                    "total_latency_ms": 0}
        stages = [{
            "stage": e.stage, "status": e.status,
            "confidence": round(e.confidence, 3) if e.confidence else None,
            "latency_ms": e.latency_ms, "retry_count": e.retry_count,
            "has_error": bool(e.error_message),
        } for e in events]
        total_latency = sum(e.latency_ms for e in events
                            if e.latency_ms is not None)
        failed = [s for s in stages if s["status"] in ("failed", "fallback")]
        return {
            "incident_id": incident_id, "stages": stages,
            "total_latency_ms": total_latency,
            "failed_stage_count": len(failed),
            "all_succeeded": len(failed) == 0,
        }
    finally:
        db.close()


def _submit_review(incident_id: str, body: dict) -> dict:
    decision = body.get("decision")
    if decision not in ("approved", "rejected"):
        raise ValueError(
            f"Invalid decision '{decision}'. Must be 'approved' or 'rejected'.")
    db = _session()
    try:
        incident = crud.get_incident(db, incident_id)
        if not incident:
            raise LookupError(f"Incident not found: {incident_id}")
        result = crud.get_workflow_result(db, incident_id)
        if not result or incident.workflow_status in ("pending", "running"):
            raise RuntimeError(
                "Cannot review an incident that has not completed triage.")
        review = crud.create_review_decision(
            db, incident_id=incident_id, decision=decision,
            reviewer_note=body.get("reviewer_note"),
        )
        return {
            "incident_id": incident_id, "decision": review.decision,
            "reviewer_note": review.reviewer_note,
            "decided_at": (review.decided_at.isoformat()
                           if review.decided_at else None),
            "message": f"Incident {decision}. Review recorded.",
        }
    finally:
        db.close()


def _get_review(incident_id: str) -> dict:
    db = _session()
    try:
        review = crud.get_latest_review(db, incident_id)
        if not review:
            raise LookupError(
                f"No review decision found for incident: {incident_id}")
        return {
            "incident_id": incident_id, "decision": review.decision,
            "reviewer_note": review.reviewer_note,
            "decided_at": (review.decided_at.isoformat()
                           if review.decided_at else None),
            "message": f"Latest decision: {review.decision}",
        }
    finally:
        db.close()


# ── Dispatcher (the new `api()` ) ─────────────────────────────────────────────

def api(method: str, path: str, **kwargs):
    """
    In-process router. Same signature and (data, error) return shape as the
    old HTTP helper, so all call sites in the UI below work unchanged.

    Supported kwargs:
        json={...}                            — body payload
        files={"file": (name, bytes, mime)}   — file upload
    """
    method = method.lower()
    # Strip query string and use it as kwargs for list endpoints
    parsed = urlparse(path)
    raw_path = parsed.path
    qs = {k: v[0] for k, v in parse_qs(parsed.query).items()}

    body = kwargs.get("json")
    files = kwargs.get("files")
    parts = [p for p in raw_path.strip("/").split("/") if p]

    try:
        # ── Incidents ──────────────────────────────────────────────────────
        if method == "get" and parts == ["incidents"]:
            return _list_incidents(int(qs.get("limit", 20)),
                                   int(qs.get("offset", 0))), None
        if method == "get" and len(parts) == 2 and parts[0] == "incidents":
            return _get_incident(parts[1]), None
        if method == "post" and parts == ["incidents"]:
            return _submit_incident(body or {}), None
        if method == "post" and parts == ["incidents", "upload", "csv"]:
            return _upload_csv(files["file"][1]), None
        if method == "post" and parts == ["incidents", "upload", "json"]:
            return _upload_json(files["file"][1]), None
        if method == "post" and parts == ["incidents", "load-samples"]:
            return _load_samples(), None

        # ── Workflow ───────────────────────────────────────────────────────
        if (method == "post" and len(parts) == 3
                and parts[0] == "workflow" and parts[2] == "run"):
            return _trigger_workflow(parts[1]), None
        if (method == "get" and len(parts) == 3
                and parts[0] == "workflow" and parts[2] == "state"):
            return _get_workflow_state(parts[1]), None
        if method == "get" and len(parts) == 2 and parts[0] == "workflow":
            return _get_workflow_result(parts[1]), None

        # ── Audit ──────────────────────────────────────────────────────────
        if (method == "get" and len(parts) == 3
                and parts[0] == "audit" and parts[2] == "summary"):
            return _get_audit_summary(parts[1]), None
        if method == "get" and len(parts) == 2 and parts[0] == "audit":
            return _get_audit_trail(parts[1]), None

        # ── Review ─────────────────────────────────────────────────────────
        if method == "post" and len(parts) == 2 and parts[0] == "review":
            return _submit_review(parts[1], body or {}), None
        if method == "get" and len(parts) == 2 and parts[0] == "review":
            return _get_review(parts[1]), None

        return None, f"Unknown route: {method.upper()} {raw_path}"

    except LookupError as e:
        return None, str(e)
    except (ValueError, RuntimeError) as e:
        return None, str(e)
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def check_api() -> bool:
    """Compatibility shim — checks DB instead of HTTP API."""
    return check_db_connection()


# ══════════════════════════════════════════════════════════════════════════════
# UI helpers
# ══════════════════════════════════════════════════════════════════════════════

def severity_badge(level: str) -> str:
    lvl = (level or "unknown").lower()
    return f'<span class="badge badge-{lvl}">{lvl}</span>'


def status_badge(status: str) -> str:
    s = (status or "unknown").lower()
    css = {
        "complete": "complete", "running": "running", "pending": "pending",
        "failed": "failed", "partial_failure": "failed",
        "approved": "approved", "rejected": "rejected",
        "awaiting_human_review": "awaiting", "reviewed_approved": "approved",
        "reviewed_rejected": "rejected",
    }
    label = {
        "partial_failure": "partial", "awaiting_human_review": "awaiting",
        "reviewed_approved": "approved", "reviewed_rejected": "rejected",
    }.get(s, s)
    cls = css.get(s, "pending")
    return f'<span class="badge badge-{cls}">{label}</span>'


def conf_bar(score: float, color: str = "#26de81") -> str:
    pct = max(0, min(100, int(score * 100)))
    bg = "#ff6b6b" if score < 0.4 else ("#fed330" if score < 0.6 else color)
    return (
        f'<div class="conf-bar-wrap">'
        f'<div class="conf-bar-fill" style="width:{pct}%;background:{bg}"></div>'
        f'</div>'
    )


def stage_icon(status: str) -> str:
    return {"success": "✓", "failed": "✗", "fallback": "⚠", "skipped": "—"}.get(status, "·")


def stage_color(status: str) -> str:
    return {
        "success": "#26de81", "failed": "#ff6b6b",
        "fallback": "#fed330", "skipped": "#778ca3",
    }.get(status, "#778ca3")


def poll_for_result(incident_id: str) -> dict | None:
    """Poll the state endpoint until pipeline completes or max attempts reached."""
    for _ in range(POLL_MAX_ATTEMPTS):
        data, err = api("get", f"/workflow/{incident_id}/state")
        if err or not data:
            time.sleep(POLL_INTERVAL_S)
            continue
        status = data.get("workflow_status", "")
        if status not in ("pending", "running"):
            return data
        time.sleep(POLL_INTERVAL_S)
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Sidebar navigation
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("""
    <div style="padding: 0.5rem 0 1.4rem 0;">
        <div style="font-size:1.1rem;font-weight:600;color:#e8eaf0;letter-spacing:0.02em;">
            🔺 Incident Triage
        </div>
        <div style="font-size:0.72rem;color:#3a4060;margin-top:3px;font-family:'IBM Plex Mono',monospace;">
            COPILOT · ENTERPRISE DEMO
        </div>
    </div>
    """, unsafe_allow_html=True)

    page = st.radio(
        "Navigation",
        ["Submit Incident", "Upload File", "Incident List", "Triage Dashboard", "Audit Trail"],
        label_visibility="collapsed",
    )

    st.markdown("<hr style='border-color:#1a1c2a;margin:1rem 0'>", unsafe_allow_html=True)

    # Backend status indicator (DB + LLM provider)
    db_ok = check_db_connection()
    has_llm_key = bool(settings.groq_api_key or settings.anthropic_api_key)
    provider = ("Groq" if settings.groq_api_key
                else "Anthropic" if settings.anthropic_api_key
                else "None")

    db_dot = "🟢" if db_ok else "🔴"
    llm_dot = "🟢" if has_llm_key else "🟠"
    st.markdown(
        f"<div style='font-size:0.75rem;color:#4a5270'>"
        f"{db_dot} DB &nbsp;·&nbsp; {llm_dot} LLM ({provider})"
        f"</div>",
        unsafe_allow_html=True,
    )
    if not has_llm_key:
        st.caption("Set GROQ_API_KEY or ANTHROPIC_API_KEY in secrets")

    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("⟳  Load sample data", use_container_width=True):
        data, err = api("post", "/incidents/load-samples")
        if err:
            st.error(err)
        else:
            st.success(f"{data['submitted']} sample incidents loaded")
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# Page 1: Submit Incident
# ══════════════════════════════════════════════════════════════════════════════

if page == "Submit Incident":
    st.markdown('<div class="page-title">Submit Incident</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-sub">Manually enter incident details for AI triage</div>', unsafe_allow_html=True)

    with st.form("submit_form", clear_on_submit=False):
        title = st.text_input(
            "Incident title *",
            placeholder="e.g. Database latency spike — orders service P99 > 8s in production",
        )
        description = st.text_area(
            "Description *",
            height=140,
            placeholder=(
                "Describe what is happening, when it started, affected scope, "
                "relevant error messages, and any recent changes..."
            ),
        )

        col1, col2, col3 = st.columns(3)
        with col1:
            service = st.text_input("Service name", placeholder="orders-service")
        with col2:
            environment = st.selectbox("Environment", ["production", "staging", "development", "unknown"])
        with col3:
            severity = st.selectbox("Reported severity", ["P1", "P2", "P3", "critical", "high", "medium", "low", "unknown"])

        run_now = st.checkbox("▶ Run triage immediately after submission", value=True)
        submitted = st.form_submit_button("Submit incident", use_container_width=True, type="primary")

    if submitted:
        if not title or len(title.strip()) < 5:
            st.error("Title must be at least 5 characters.")
        elif not description or len(description.strip()) < 10:
            st.error("Description must be at least 10 characters.")
        else:
            payload = {
                "title": title.strip(),
                "description": description.strip(),
                "service_name": service.strip() or None,
                "environment": environment,
                "raw_severity": severity,
            }
            with st.spinner("Submitting incident..."):
                data, err = api("post", "/incidents", json=payload)

            if err:
                st.error(err)
            else:
                incident_id = data["incident_id"]
                st.success(f"Incident submitted — ID: `{incident_id}`")
                st.session_state["last_incident_id"] = incident_id

                if run_now:
                    with st.spinner("Starting triage pipeline..."):
                        trig, terr = api("post", f"/workflow/{incident_id}/run")
                    if terr:
                        st.error(f"Failed to trigger pipeline: {terr}")
                    else:
                        progress_bar = st.progress(0, text="Running triage pipeline…")
                        for i in range(POLL_MAX_ATTEMPTS):
                            state, _ = api("get", f"/workflow/{incident_id}/state")
                            if state and state.get("workflow_status") not in ("pending", "running"):
                                progress_bar.progress(100, text="Pipeline complete")
                                break
                            progress_bar.progress(
                                min(95, int((i / POLL_MAX_ATTEMPTS) * 100)),
                                text="Running triage pipeline…",
                            )
                            time.sleep(POLL_INTERVAL_S)

                        st.success("✓ Triage complete — go to Triage Dashboard to view results")
                        st.session_state["dashboard_id"] = incident_id
                        if st.button("Open Triage Dashboard →"):
                            st.session_state["page"] = "Triage Dashboard"
                            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# Page 2: Upload File
# ══════════════════════════════════════════════════════════════════════════════

elif page == "Upload File":
    st.markdown('<div class="page-title">Upload Incidents</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-sub">Bulk import incidents via CSV or JSON file</div>', unsafe_allow_html=True)

    tab_csv, tab_json = st.tabs(["CSV Upload", "JSON Upload"])

    with tab_csv:
        st.markdown("""
        <div class="agent-panel">
        <div class="agent-label">Expected CSV columns</div>
        <div class="agent-content" style="font-family:'IBM Plex Mono',monospace;font-size:0.8rem;color:#778ca3">
        title (required), description, service_name, environment, raw_severity
        </div>
        </div>
        """, unsafe_allow_html=True)

        csv_file = st.file_uploader("Choose CSV file", type=["csv"])
        run_csv = st.checkbox("▶ Run triage on all uploaded incidents", value=False, key="run_csv")

        if csv_file and st.button("Upload CSV", type="primary"):
            with st.spinner("Uploading and parsing..."):
                data, err = api("post", "/incidents/upload/csv",
                               files={"file": (csv_file.name, csv_file.getvalue(), "text/csv")})
            if err:
                st.error(err)
            else:
                st.success(f"✓ {data['submitted']} incidents created")
                if data["errors"]:
                    with st.expander(f"⚠ {len(data['errors'])} rows skipped"):
                        for e in data["errors"]:
                            st.caption(e)

                if run_csv and data["incident_ids"]:
                    progress = st.progress(0, text="Running triage on uploaded incidents...")
                    for idx, iid in enumerate(data["incident_ids"]):
                        api("post", f"/workflow/{iid}/run")
                        poll_for_result(iid)
                        progress.progress(
                            int((idx + 1) / len(data["incident_ids"]) * 100),
                            text=f"Triaged {idx+1}/{len(data['incident_ids'])}..."
                        )
                    st.success("All incidents triaged — view in Incident List")

    with tab_json:
        st.markdown("""
        <div class="agent-panel">
        <div class="agent-label">Expected JSON format</div>
        <div class="agent-content" style="font-family:'IBM Plex Mono',monospace;font-size:0.8rem;color:#778ca3">
        [{"title": "...", "description": "...", "service_name": "...", "environment": "...", "raw_severity": "..."}]
        </div>
        </div>
        """, unsafe_allow_html=True)

        json_file = st.file_uploader("Choose JSON file", type=["json"])
        run_json = st.checkbox("▶ Run triage on all uploaded incidents", value=False, key="run_json")

        if json_file and st.button("Upload JSON", type="primary"):
            with st.spinner("Uploading and parsing..."):
                data, err = api("post", "/incidents/upload/json",
                               files={"file": (json_file.name, json_file.getvalue(), "application/json")})
            if err:
                st.error(err)
            else:
                st.success(f"✓ {data['submitted']} incidents created")
                if data["errors"]:
                    with st.expander(f"⚠ {len(data['errors'])} rows skipped"):
                        for e in data["errors"]:
                            st.caption(e)

                if run_json and data["incident_ids"]:
                    progress = st.progress(0, text="Running triage on uploaded incidents...")
                    for idx, iid in enumerate(data["incident_ids"]):
                        api("post", f"/workflow/{iid}/run")
                        poll_for_result(iid)
                        progress.progress(
                            int((idx + 1) / len(data["incident_ids"]) * 100),
                            text=f"Triaged {idx+1}/{len(data['incident_ids'])}..."
                        )
                    st.success("All incidents triaged")


# ══════════════════════════════════════════════════════════════════════════════
# Page 3: Incident List
# ══════════════════════════════════════════════════════════════════════════════

elif page == "Incident List":
    st.markdown('<div class="page-title">Incident List</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-sub">All submitted incidents — click to open in Triage Dashboard</div>', unsafe_allow_html=True)

    data, err = api("get", "/incidents?limit=50")
    if err:
        st.error(err)
    elif not data or data["total"] == 0:
        st.info("No incidents yet. Submit one or load sample data from the sidebar.")
    else:
        incidents = data["incidents"]

        # Status filter
        col_f1, col_f2 = st.columns([3, 1])
        with col_f2:
            status_filter = st.selectbox(
                "Filter", ["all", "pending", "running", "complete", "failed"],
                label_visibility="collapsed"
            )

        filtered = incidents if status_filter == "all" else [
            i for i in incidents if i["workflow_status"] == status_filter
        ]

        st.markdown(f"<div class='muted'>{len(filtered)} incidents</div><br>", unsafe_allow_html=True)

        # Header row
        st.markdown("""
        <div class="audit-row audit-header" style="grid-template-columns:1fr 110px 100px 90px 120px;">
            <span>TITLE</span><span>SERVICE</span><span>ENV</span>
            <span>SEVERITY</span><span>STATUS</span>
        </div>
        """, unsafe_allow_html=True)

        for inc in filtered:
            svc   = inc.get("service_name") or "—"
            env   = inc.get("environment") or "—"
            sev   = inc.get("raw_severity") or "—"
            stat  = inc.get("workflow_status") or "pending"
            title = inc.get("title", "Untitled")[:72]
            iid   = inc["id"]

            col_row, col_btn = st.columns([8, 1])
            with col_row:
                st.markdown(f"""
                <div class="inc-row">
                  <span class="inc-title">{title}</span>
                  <span class="inc-meta">{svc[:18]}</span>
                  <span class="inc-meta">{env[:10]}</span>
                  <span class="inc-meta">{sev}</span>
                  {status_badge(stat)}
                </div>
                """, unsafe_allow_html=True)
            with col_btn:
                if st.button("Open", key=f"open_{iid}", use_container_width=True):
                    st.session_state["dashboard_id"] = iid
                    st.session_state["_nav"] = "Triage Dashboard"
                    st.rerun()

    # Handle cross-page navigation from button click
    if st.session_state.get("_nav") == "Triage Dashboard":
        del st.session_state["_nav"]
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# Page 4: Triage Dashboard
# ══════════════════════════════════════════════════════════════════════════════

elif page == "Triage Dashboard":
    st.markdown('<div class="page-title">Triage Dashboard</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-sub">Run triage, view agent outputs, and submit review decisions</div>', unsafe_allow_html=True)

    # ── Incident selector ────────────────────────────────────────────────────
    data, err = api("get", "/incidents?limit=100")
    if err:
        st.error(err)
        st.stop()
    if not data or data["total"] == 0:
        st.info("No incidents yet. Submit one or load sample data from the sidebar.")
        st.stop()

    incidents = data["incidents"]
    inc_options = {
        f"{i['title'][:60]}  [{i['workflow_status']}]": i["id"]
        for i in incidents
    }

    # Pre-select from session state if coming from list view
    default_idx = 0
    if "dashboard_id" in st.session_state:
        for idx, iid in enumerate(inc_options.values()):
            if iid == st.session_state["dashboard_id"]:
                default_idx = idx
                break

    selected_label = st.selectbox(
        "Select incident",
        list(inc_options.keys()),
        index=default_idx,
        label_visibility="collapsed",
    )
    incident_id = inc_options[selected_label]
    st.session_state["dashboard_id"] = incident_id

    # Fetch base incident
    inc_data, inc_err = api("get", f"/incidents/{incident_id}")
    if inc_err:
        st.error(inc_err)
        st.stop()

    # ── Incident header ──────────────────────────────────────────────────────
    st.markdown(f"""
    <div class="card" style="margin-top:0.8rem">
        <div class="card-title">Incident</div>
        <div style="font-size:1.05rem;font-weight:600;color:#e8eaf0;margin-bottom:0.4rem">
            {inc_data['title']}
        </div>
        <div style="color:#778ca3;font-size:0.84rem;line-height:1.6">
            {inc_data.get('description','')[:300]}{'…' if len(inc_data.get('description',''))>300 else ''}
        </div>
        <div style="margin-top:0.7rem;display:flex;gap:1rem;flex-wrap:wrap">
            <span class="muted">Service: <span style="color:#a0a8bc">{inc_data.get('service_name') or '—'}</span></span>
            <span class="muted">Env: <span style="color:#a0a8bc">{inc_data.get('environment') or '—'}</span></span>
            <span class="muted">Reported severity: <span style="color:#a0a8bc">{inc_data.get('raw_severity') or '—'}</span></span>
            <span class="muted">ID: <span style="color:#3a4060;font-family:'IBM Plex Mono',monospace;font-size:0.75rem">{incident_id[:16]}…</span></span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Pipeline trigger ─────────────────────────────────────────────────────
    col_run, col_status = st.columns([2, 3])
    with col_run:
        if st.button("▶  Run Triage Pipeline", use_container_width=True, type="primary"):
            trig, terr = api("post", f"/workflow/{incident_id}/run")
            if terr:
                st.error(terr)
            else:
                progress_bar = st.progress(0, text="Initializing pipeline…")
                stage_labels = ["Normalizing", "Severity", "Root Cause", "Runbook", "Summary"]
                for i in range(POLL_MAX_ATTEMPTS):
                    state_data, _ = api("get", f"/workflow/{incident_id}/state")
                    if state_data:
                        ws = state_data.get("workflow_status", "running")
                        if ws not in ("pending", "running"):
                            progress_bar.progress(100, text="Pipeline complete ✓")
                            time.sleep(0.3)
                            break
                    pct = min(95, int((i / POLL_MAX_ATTEMPTS) * 100))
                    label_idx = min(len(stage_labels) - 1, int(i / (POLL_MAX_ATTEMPTS / len(stage_labels))))
                    progress_bar.progress(pct, text=f"{stage_labels[label_idx]}…")
                    time.sleep(POLL_INTERVAL_S)
                st.rerun()

    # ── Fetch workflow result ────────────────────────────────────────────────
    result, rerr = api("get", f"/workflow/{incident_id}")

    if rerr or not result:
        with col_status:
            ws = inc_data.get("workflow_status", "pending")
            st.markdown(f"Status: {status_badge(ws)}", unsafe_allow_html=True)
            if ws == "pending":
                st.caption("Pipeline has not run yet — click Run Triage Pipeline.")
        st.stop()

    pipeline_status  = result.get("pipeline_status", "unknown")
    overall_conf     = result.get("overall_confidence", 0.0) or 0.0
    low_conf_flag    = result.get("low_confidence_flag", False)
    review_status    = result.get("review_status", "awaiting_human_review")
    sev_out          = result.get("severity_output") or {}
    rc_out           = result.get("root_cause_output") or {}
    rb_out           = result.get("runbook_output") or {}
    sum_out          = result.get("summary_output") or {}
    audit_trail      = result.get("audit_trail") or []

    with col_status:
        st.markdown(
            f"Status: {status_badge(pipeline_status)} &nbsp; Review: {status_badge(review_status)}",
            unsafe_allow_html=True,
        )

    if pipeline_status not in ("complete", "partial_failure", "reviewed_approved", "reviewed_rejected"):
        st.info("Pipeline has not completed yet.")
        st.stop()

    # ── Banners ──────────────────────────────────────────────────────────────
    if low_conf_flag:
        st.markdown(f"""
        <div class="low-conf-banner">
        ⚠ <strong>Low confidence ({overall_conf:.0%})</strong> — Automated analysis is uncertain.
        Human review is strongly recommended before acting on these recommendations.
        </div>
        """, unsafe_allow_html=True)

    if rb_out.get("escalate"):
        reason = rb_out.get("escalation_reason") or "Low confidence in automated analysis."
        st.markdown(f"""
        <div class="escalation-banner">
        🔺 <strong>Escalation recommended</strong> — {reason}
        </div>
        """, unsafe_allow_html=True)

    # ── KPI row ──────────────────────────────────────────────────────────────
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        sev_level = sev_out.get("severity_level", "unknown")
        st.markdown(f"""
        <div class="card">
            <div class="card-title">Severity</div>
            {severity_badge(sev_level)}
            <div class="muted" style="margin-top:6px">
                {sev_out.get('urgency','—')} urgency
            </div>
        </div>""", unsafe_allow_html=True)
    with k2:
        st.markdown(f"""
        <div class="card">
            <div class="card-title">Category</div>
            <div class="card-value" style="font-size:1.1rem;text-transform:capitalize">
                {sev_out.get('incident_category','unknown')}
            </div>
        </div>""", unsafe_allow_html=True)
    with k3:
        st.markdown(f"""
        <div class="card">
            <div class="card-title">Overall confidence</div>
            <div class="card-value">{overall_conf:.0%}</div>
            {conf_bar(overall_conf)}
        </div>""", unsafe_allow_html=True)
    with k4:
        proc_t = result.get("processing_time_s")
        proc_label = f"{proc_t:.1f}s" if proc_t else "—"
        st.markdown(f"""
        <div class="card">
            <div class="card-title">Processing time</div>
            <div class="card-value">{proc_label}</div>
        </div>""", unsafe_allow_html=True)

    st.markdown("<hr class='divider'>", unsafe_allow_html=True)

    # ── Pipeline stage visualization ─────────────────────────────────────────
    st.markdown("**Pipeline stages**", unsafe_allow_html=True)

    stage_meta = {
        "normalization": ("Normalization",  "📋"),
        "severity":      ("SeverityAgent",  "🎯"),
        "root_cause":    ("RootCauseAgent", "🔍"),
        "runbook":       ("RunbookAgent",   "📖"),
        "summary":       ("SummaryAgent",   "📝"),
    }

    if audit_trail:
        for entry in audit_trail:
            stage   = entry.get("stage", "")
            status  = entry.get("status", "")
            conf    = entry.get("confidence")
            lat     = entry.get("latency_ms")
            label, icon = stage_meta.get(stage, (stage.title(), "·"))
            icon_char   = stage_icon(status)
            color       = stage_color(status)
            conf_str    = f"{conf:.0%}" if conf is not None else "—"
            lat_str     = f"{lat}ms" if lat is not None else "—"

            st.markdown(f"""
            <div class="stage-row">
                <span class="stage-icon" style="color:{color}">{icon} {icon_char}</span>
                <span class="stage-name">{label}</span>
                <span class="stage-conf">conf {conf_str}</span>
                <span class="stage-lat">{lat_str}</span>
                {f'<span class="badge badge-failed" style="font-size:0.65rem">fallback</span>' if status == "fallback" else ""}
            </div>
            """, unsafe_allow_html=True)
    else:
        st.caption("No stage data available.")

    st.markdown("<hr class='divider'>", unsafe_allow_html=True)

    # ── Agent outputs (2-column layout) ──────────────────────────────────────
    left, right = st.columns(2)

    with left:
        # Root cause
        st.markdown("**Root cause analysis**", unsafe_allow_html=True)
        ev_strength = rc_out.get("evidence_strength", "low")
        ev_color = {"high": "#26de81", "medium": "#fed330", "low": "#ff6b6b"}.get(ev_strength, "#778ca3")
        cause = rc_out.get("probable_cause", "Not determined")
        uncertainty = rc_out.get("uncertainty_note", "")
        rc_conf = rc_out.get("confidence", 0.0) or 0.0

        st.markdown(f"""
        <div class="agent-panel">
            <div class="agent-label">Probable cause</div>
            <div class="agent-content">{cause}</div>
            <div style="margin-top:0.6rem;display:flex;gap:1rem;align-items:center">
                <span class="muted">Evidence: <span style="color:{ev_color};font-weight:600">{ev_strength}</span></span>
                <span class="muted">Confidence: {rc_conf:.0%}</span>
            </div>
            {f'<div style="margin-top:0.5rem;color:#feca57;font-size:0.8rem">⚠ {uncertainty}</div>' if uncertainty else ""}
        </div>
        """, unsafe_allow_html=True)

        # Stakeholder summary
        st.markdown("**Stakeholder summary**", unsafe_allow_html=True)
        sum_text   = sum_out.get("summary_text", "Not available")
        impact     = sum_out.get("probable_impact", "")
        next_act   = sum_out.get("next_action", "")
        sum_conf   = sum_out.get("confidence", 0.0) or 0.0

        st.markdown(f"""
        <div class="summary-box">
            <div class="agent-label">Summary</div>
            <div class="summary-text">{sum_text}</div>
            {f'<div style="margin-top:0.7rem;border-top:1px solid #1a3020;padding-top:0.6rem"><span class="muted">Impact: </span><span style="color:#a8dfc0;font-size:0.85rem">{impact}</span></div>' if impact else ""}
            {f'<div style="margin-top:0.5rem"><span class="muted">Next action: </span><span style="color:#26de81;font-size:0.85rem;font-weight:500">{next_act}</span></div>' if next_act else ""}
            <div style="margin-top:0.6rem">{conf_bar(sum_conf, "#26de81")}</div>
            <div class="muted" style="margin-top:4px">Confidence: {sum_conf:.0%}</div>
        </div>
        """, unsafe_allow_html=True)

    with right:
        # Runbook recommendations
        matched = rb_out.get("matched_runbook")
        actions = rb_out.get("actions") or []
        rb_conf = rb_out.get("confidence", 0.0) or 0.0

        st.markdown(f"""**Runbook recommendation**{f' — <span style="color:#5a6480;font-size:0.8rem">{matched}</span>' if matched else ""}""",
                    unsafe_allow_html=True)

        if actions:
            actions_html = ""
            for idx, action in enumerate(actions, 1):
                is_caution = action.upper().startswith("CAUTION")
                extra_cls  = "caution" if is_caution else ""
                actions_html += f"""
                <div class="action-step {extra_cls}">
                    <span class="step-num">{idx:02d}</span>
                    <span>{action}</span>
                </div>"""
            st.markdown(f"""
            <div class="agent-panel">
                <div class="agent-label">Actions  ·  confidence {rb_conf:.0%}</div>
                {actions_html}
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown('<div class="agent-panel"><div class="agent-content muted">No runbook steps available.</div></div>', unsafe_allow_html=True)

        # Severity detail
        sev_reasoning = sev_out.get("reasoning", "")
        sev_conf      = sev_out.get("confidence", 0.0) or 0.0
        st.markdown("**Severity detail**")
        st.markdown(f"""
        <div class="agent-panel">
            <div style="display:flex;gap:10px;align-items:center;margin-bottom:0.5rem">
                {severity_badge(sev_out.get('severity_level','unknown'))}
                <span class="muted">{sev_out.get('urgency','—')} urgency · {sev_out.get('incident_category','—')}</span>
            </div>
            <div class="agent-content" style="font-size:0.84rem">{sev_reasoning}</div>
            {conf_bar(sev_conf)}
            <div class="muted" style="margin-top:4px">Confidence: {sev_conf:.0%}</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<hr class='divider'>", unsafe_allow_html=True)

    # ── Human review panel ────────────────────────────────────────────────────
    st.markdown("**Human review**")

    review_col, note_col = st.columns([1, 2])

    with review_col:
        st.markdown(f"""
        <div class="agent-panel">
            <div class="agent-label">Review status</div>
            {status_badge(review_status)}
            <div class="muted" style="margin-top:8px;font-size:0.78rem">
                A human reviewer must approve or reject this triage result before any actions are taken.
            </div>
        </div>
        """, unsafe_allow_html=True)

    with note_col:
        if review_status in ("awaiting_human_review",):
            with st.form(f"review_form_{incident_id}"):
                reviewer_note = st.text_area(
                    "Reviewer note (optional)",
                    height=80,
                    placeholder="Add context, corrections, or reasoning for your decision...",
                )
                rcol1, rcol2 = st.columns(2)
                with rcol1:
                    approve = st.form_submit_button("✓ Approve", use_container_width=True, type="primary")
                with rcol2:
                    reject  = st.form_submit_button("✗ Reject",  use_container_width=True)

            if approve or reject:
                decision = "approved" if approve else "rejected"
                rev_data, rev_err = api(
                    "post", f"/review/{incident_id}",
                    json={"decision": decision, "reviewer_note": reviewer_note or None}
                )
                if rev_err:
                    st.error(rev_err)
                else:
                    st.success(f"Decision recorded: **{decision}**")
                    st.rerun()
        else:
            # Fetch and show existing review
            rev_data, _ = api("get", f"/review/{incident_id}")
            if rev_data:
                dec_color = "#26de81" if rev_data.get("decision") == "approved" else "#ff6b6b"
                st.markdown(f"""
                <div class="agent-panel">
                    <div class="agent-label">Decision</div>
                    <div style="color:{dec_color};font-weight:600;font-size:1rem;margin-bottom:4px">
                        {rev_data.get('decision','').upper()}
                    </div>
                    {f'<div class="agent-content" style="font-size:0.85rem">{rev_data.get("reviewer_note","")}</div>' if rev_data.get("reviewer_note") else ""}
                    <div class="muted" style="margin-top:6px">{rev_data.get('decided_at','')[:19]}</div>
                </div>
                """, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# Page 5: Audit Trail
# ══════════════════════════════════════════════════════════════════════════════

elif page == "Audit Trail":
    st.markdown('<div class="page-title">Audit Trail</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-sub">Per-stage LLMOps inspection — latency, confidence, retries, and errors</div>', unsafe_allow_html=True)

    # Incident selector
    data, err = api("get", "/incidents?limit=100")
    if err:
        st.error(err)
        st.stop()
    if not data or data["total"] == 0:
        st.info("No incidents yet.")
        st.stop()

    incidents = data["incidents"]
    inc_opts = {f"{i['title'][:65]}": i["id"] for i in incidents}
    default_id = st.session_state.get("dashboard_id")
    default_idx = 0
    if default_id:
        for idx, iid in enumerate(inc_opts.values()):
            if iid == default_id:
                default_idx = idx
                break

    selected = st.selectbox("Select incident", list(inc_opts.keys()),
                            index=default_idx, label_visibility="collapsed")
    incident_id = inc_opts[selected]

    # Fetch audit trail
    trail_data, trail_err = api("get", f"/audit/{incident_id}")
    summary_data, _ = api("get", f"/audit/{incident_id}/summary")

    if trail_err:
        st.error(trail_err)
        st.stop()

    events = trail_data.get("events", [])
    if not events:
        st.info("No audit events yet — run the triage pipeline first.")
        st.stop()

    # Summary stats row
    if summary_data:
        total_lat = summary_data.get("total_latency_ms", 0)
        failed_ct = summary_data.get("failed_stage_count", 0)
        all_ok    = summary_data.get("all_succeeded", False)

        s1, s2, s3, s4 = st.columns(4)
        with s1:
            st.markdown(f"""<div class="card">
                <div class="card-title">Total stages</div>
                <div class="card-value">{len(events)}</div>
            </div>""", unsafe_allow_html=True)
        with s2:
            st.markdown(f"""<div class="card">
                <div class="card-title">Total latency</div>
                <div class="card-value">{total_lat}ms</div>
            </div>""", unsafe_allow_html=True)
        with s3:
            fail_color = "#ff6b6b" if failed_ct > 0 else "#26de81"
            st.markdown(f"""<div class="card">
                <div class="card-title">Failed stages</div>
                <div class="card-value" style="color:{fail_color}">{failed_ct}</div>
            </div>""", unsafe_allow_html=True)
        with s4:
            ok_text  = "All passed" if all_ok else "Has failures"
            ok_color = "#26de81" if all_ok else "#ff6b6b"
            st.markdown(f"""<div class="card">
                <div class="card-title">Result</div>
                <div class="card-value" style="color:{ok_color};font-size:1rem">{ok_text}</div>
            </div>""", unsafe_allow_html=True)

    st.markdown("<hr class='divider'>", unsafe_allow_html=True)

    # Audit table header
    st.markdown("""
    <div class="audit-row audit-header">
        <span>STAGE</span><span>STATUS</span>
        <span>CONFIDENCE</span><span>LATENCY</span>
        <span>RETRIES</span><span>MODEL / ERROR</span>
    </div>
    """, unsafe_allow_html=True)

    for ev in events:
        stage   = ev.get("stage", "")
        status  = ev.get("status", "")
        conf    = ev.get("confidence")
        lat     = ev.get("latency_ms")
        retries = ev.get("retry_count", 0)
        model   = ev.get("llm_model") or "—"
        error   = ev.get("error_message") or ""
        ts      = ev.get("timestamp", "")[:19]

        conf_str = f"{conf:.1%}" if conf is not None else "—"
        lat_str  = f"{lat}ms" if lat is not None else "—"
        icon     = stage_icon(status)
        color    = stage_color(status)
        model_short = model.replace("claude-", "cl-")[:24] if model != "—" else "—"
        tail = f'<span style="color:#ff6b6b;font-size:0.75rem">{error[:60]}</span>' if error else f'<span style="color:#3a4060">{model_short}</span>'

        retry_badge = f'<span class="badge badge-failed" style="font-size:0.65rem">{retries}x</span>' if retries > 0 else "0"

        st.markdown(f"""
        <div class="audit-row">
            <span style="color:#c8ccd8;font-weight:500">{stage}</span>
            <span style="color:{color};font-family:'IBM Plex Mono',monospace;font-size:0.78rem">{icon} {status}</span>
            <span class="mono" style="color:#a0a8bc">{conf_str}</span>
            <span class="mono" style="color:#778ca3">{lat_str}</span>
            <span class="mono" style="color:#4a5270">{retry_badge}</span>
            {tail}
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # Raw JSON expander
    with st.expander("View raw audit JSON"):
        st.json(events)
