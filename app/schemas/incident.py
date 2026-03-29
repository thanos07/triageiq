"""
app/schemas/incident.py

Pydantic v2 data contracts for incidents.

Three layers of schema:
  1. IncidentInput      — what the user submits (loose, permissive)
  2. NormalizedIncident — internal canonical form (strict, typed)
  3. IncidentResponse   — what the API returns (combines both)

Why three schemas?
  - Input validation is separate from internal representation.
  - The normalized form is what agents receive — they never see raw input.
  - Responses include computed fields the caller needs.
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional, Literal
from pydantic import BaseModel, Field, field_validator, model_validator
import uuid


# ── Allowed enum-style string literals ────────────────────────────────────────

SeverityLevel = Literal["critical", "high", "medium", "low", "unknown"]
Environment   = Literal["production", "staging", "development", "unknown"]
WorkflowStatus = Literal[
    "pending", "running", "complete", "failed",
    "partial_failure", "reviewed_approved", "reviewed_rejected"
]
ReviewStatus = Literal["awaiting_human_review", "approved", "rejected"]


# ── 1. Incident Input Schema (what users submit) ───────────────────────────────

class IncidentInput(BaseModel):
    """
    Schema for manually submitted incidents.
    Fields are intentionally permissive — we normalize downstream.
    Only title and description are required.
    """

    title: str = Field(
        ...,
        min_length=5,
        max_length=512,
        description="Short, human-readable incident title",
        examples=["Database latency spike in prod — orders service degraded"],
    )
    description: str = Field(
        ...,
        min_length=10,
        max_length=4096,
        description="Detailed description of what is happening",
        examples=["P99 query latency exceeded 8s at 14:32 UTC. Affects checkout flow."],
    )
    source: Optional[str] = Field(
        default="manual",
        max_length=128,
        description="How this incident was reported (manual, csv, json, pagerduty, etc.)",
    )
    service_name: Optional[str] = Field(
        default=None,
        max_length=256,
        description="Name of the affected service or system",
        examples=["orders-service", "auth-api", "payment-gateway"],
    )
    environment: Optional[str] = Field(
        default=None,
        max_length=64,
        description="Environment where the incident occurred",
        examples=["production", "staging", "development"],
    )
    raw_severity: Optional[str] = Field(
        default=None,
        max_length=64,
        description="Severity as reported by the submitter (free text — agents will normalize)",
        examples=["P1", "critical", "SEV-2", "high"],
    )

    @field_validator("title", "description", mode="before")
    @classmethod
    def strip_whitespace(cls, v: str) -> str:
        return v.strip() if isinstance(v, str) else v

    @field_validator("environment", mode="before")
    @classmethod
    def normalize_environment(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.lower().strip()
        mapping = {
            "prod": "production", "production": "production",
            "stage": "staging",   "staging": "staging",
            "dev":  "development","development": "development",
        }
        return mapping.get(v, v)

    model_config = {"str_strip_whitespace": True}


class CSVIncidentRow(BaseModel):
    """
    Schema for a single row parsed from a CSV upload.
    More permissive than IncidentInput — all fields optional except title.
    """
    title:        str
    description:  Optional[str] = ""
    source:       Optional[str] = "csv"
    service_name: Optional[str] = None
    environment:  Optional[str] = None
    raw_severity: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def handle_empty_strings(cls, values: dict) -> dict:
        """Convert empty strings to None for optional fields."""
        for key in ("service_name", "environment", "raw_severity"):
            if values.get(key) == "":
                values[key] = None
        return values


# ── 2. Normalized Incident (internal canonical form) ──────────────────────────

class NormalizedIncident(BaseModel):
    """
    The canonical internal representation of an incident.
    Created by the normalization service from raw IncidentInput.
    This is what gets stored in the DB and passed to agents.

    Every field has a concrete type and sensible default — no surprises.
    """

    incident_id:  str = Field(default_factory=lambda: str(uuid.uuid4()))
    title:        str
    description:  str
    source:       str = "manual"
    service_name: str = "unknown"
    environment:  Environment = "unknown"
    raw_severity: str = "unknown"
    submitted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # These are filled in by agents later; start as None
    normalized_severity:  Optional[SeverityLevel] = None
    incident_category:    Optional[str]            = None
    probable_root_cause:  Optional[str]            = None
    runbook_actions:      Optional[list[str]]       = None
    stakeholder_summary:  Optional[str]             = None
    workflow_status:      WorkflowStatus            = "pending"

    model_config = {"arbitrary_types_allowed": True}


# ── 3. API Response Schemas ────────────────────────────────────────────────────

class IncidentResponse(BaseModel):
    """What the API returns when an incident is fetched or listed."""
    id:              str
    title:           str
    description:     str
    source:          Optional[str]
    service_name:    Optional[str]
    environment:     Optional[str]
    raw_severity:    Optional[str]
    submitted_at:    datetime
    workflow_status: str

    model_config = {"from_attributes": True}


class IncidentListResponse(BaseModel):
    """Paginated list of incidents."""
    total:     int
    incidents: list[IncidentResponse]


class SubmitIncidentResponse(BaseModel):
    """Returned immediately after incident submission."""
    incident_id: str
    message:     str
    workflow_status: str = "pending"


class BulkUploadResponse(BaseModel):
    """Returned after a CSV or JSON bulk upload."""
    submitted:  int
    failed:     int
    incident_ids: list[str]
    errors:     list[str] = []
