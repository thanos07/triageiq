"""
app/services/normalizer.py

Incident normalization service.

Converts raw IncidentInput (loose user data) into a NormalizedIncident
(clean, typed, canonical form) that agents can reliably consume.

Why normalize?
  - Agents should never have to handle missing fields or type inconsistencies.
  - Field aliasing is done once here (e.g. "P1" → "critical").
  - Sensible defaults are applied so agents always get complete data.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from app.schemas.incident import IncidentInput, NormalizedIncident, CSVIncidentRow
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── Severity normalization map ────────────────────────────────────────────────
# Maps any free-text severity string to our canonical levels.
SEVERITY_MAP: dict[str, str] = {
    # Priority labels (common in PagerDuty, Jira, etc.)
    "p0": "critical", "p1": "critical",
    "p2": "high",
    "p3": "medium",
    "p4": "low",
    # SEV labels
    "sev0": "critical", "sev-0": "critical",
    "sev1": "critical", "sev-1": "critical",
    "sev2": "high",     "sev-2": "high",
    "sev3": "medium",   "sev-3": "medium",
    "sev4": "low",      "sev-4": "low",
    # Plain English
    "critical": "critical",
    "high": "high",
    "medium": "medium",
    "moderate": "medium",
    "low": "low",
    "minor": "low",
    "info": "low",
    "informational": "low",
    # Unknown / unset
    "unknown": "unknown",
    "": "unknown",
}

# ── Environment normalization map ─────────────────────────────────────────────
ENVIRONMENT_MAP: dict[str, str] = {
    "prod": "production", "production": "production",
    "stage": "staging",   "staging": "staging",
    "dev": "development", "development": "development",
    "test": "development","qa": "staging",
}


def _normalize_severity(raw: Optional[str]) -> str:
    """
    Map a free-text severity string to a canonical level.
    Returns 'unknown' if the input is missing or unrecognized.
    """
    if not raw:
        return "unknown"
    key = raw.strip().lower()
    return SEVERITY_MAP.get(key, "unknown")


def _normalize_environment(raw: Optional[str]) -> str:
    """
    Map a free-text environment string to a canonical value.
    Returns 'unknown' if missing or unrecognized.
    """
    if not raw:
        return "unknown"
    key = raw.strip().lower()
    return ENVIRONMENT_MAP.get(key, key)  # preserve unrecognized values as-is


def normalize_incident(
    input_data: IncidentInput,
    incident_id: Optional[str] = None,
) -> NormalizedIncident:
    """
    Convert a raw IncidentInput into a NormalizedIncident.

    This is the single entry point for normalization.
    Agents never call this — only the ingestion service does.

    Args:
        input_data:  Validated IncidentInput from the API layer.
        incident_id: Pre-assigned UUID (optional — generated here if not provided).

    Returns:
        A fully populated NormalizedIncident ready for the pipeline.
    """
    iid = incident_id or str(uuid.uuid4())

    normalized = NormalizedIncident(
        incident_id=iid,
        title=input_data.title.strip(),
        description=input_data.description.strip(),
        source=input_data.source or "manual",
        service_name=input_data.service_name or "unknown",
        environment=_normalize_environment(input_data.environment),
        raw_severity=input_data.raw_severity or "unknown",
        submitted_at=datetime.now(timezone.utc),
        workflow_status="pending",
    )

    logger.info(
        "Incident normalized",
        extra={
            "incident_id": iid,
            "service": normalized.service_name,
            "environment": normalized.environment,
            "severity_raw": normalized.raw_severity,
        },
    )

    return normalized


def normalize_from_csv_row(row: CSVIncidentRow, incident_id: Optional[str] = None) -> NormalizedIncident:
    """
    Convert a parsed CSV row into a NormalizedIncident.
    Internally converts to IncidentInput first for shared validation logic.
    """
    input_data = IncidentInput(
        title=row.title,
        description=row.description or "(no description provided)",
        source=row.source or "csv",
        service_name=row.service_name,
        environment=row.environment,
        raw_severity=row.raw_severity,
    )
    return normalize_incident(input_data, incident_id=incident_id)


def normalize_from_dict(data: dict, source: str = "json") -> NormalizedIncident:
    """
    Normalize an incident from a raw dict (e.g. JSON upload row).
    Unknown fields are silently ignored by Pydantic.
    """
    data.setdefault("source", source)
    input_data = IncidentInput(**data)
    return normalize_incident(input_data)
