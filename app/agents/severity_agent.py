"""
app/agents/severity_agent.py

SeverityAgent: first stage of the triage pipeline.

Responsibilities:
  - Classify severity level (critical / high / medium / low / unknown)
  - Infer urgency (immediate / high / medium / low)
  - Determine broad incident category (database, network, auth, etc.)
  - Return a confidence score and honest reasoning

Design:
  - Accepts the shared WorkflowState, reads normalized_incident
  - Calls the LLM via the provider abstraction (never imports Anthropic directly)
  - Validates and clamps the LLM output into a typed SeverityResult
  - On any failure, returns a safe fallback result instead of crashing
  - Records a StageAuditEntry into the WorkflowState audit trail
"""

import time
from typing import Optional

from app.llm.base import BaseLLMProvider
from app.llm.prompt_templates import (
    build_severity_prompt,
    SYSTEM_SEVERITY,
    SEVERITY_FALLBACK,
)
from app.schemas.workflow import WorkflowState, SeverityResult, StageAuditEntry
from app.utils.logger import get_logger
from app.utils.audit_logger import AuditStage, AuditStatus
from app.config import settings

logger = get_logger(__name__)


class SeverityAgent:
    """
    Classifies incident severity, urgency, and category.

    Usage:
        agent = SeverityAgent(llm_provider)
        state = agent.run(state)
    """

    # Valid values for each output field — used for post-LLM validation
    VALID_SEVERITY_LEVELS = {"critical", "high", "medium", "low", "unknown"}
    VALID_URGENCY_LEVELS  = {"immediate", "high", "medium", "low", "unknown"}
    VALID_CATEGORIES = {
        "database", "network", "authentication", "infrastructure",
        "payments", "messaging", "security", "application", "unknown",
    }

    def __init__(self, llm: BaseLLMProvider):
        self._llm = llm

    def run(self, state: WorkflowState) -> WorkflowState:
        """
        Execute the severity classification stage.

        Reads:  state.normalized_incident
        Writes: state.severity_result
                state.stage_audit_trail (appends one entry)

        Always returns state — never raises.
        """
        stage = AuditStage.SEVERITY
        start_ms = time.monotonic()
        incident = state.normalized_incident or {}

        logger.info(
            "SeverityAgent starting",
            extra={"incident_id": state.incident_id},
        )

        try:
            # Build and send the prompt
            prompt = build_severity_prompt(incident)
            response = self._llm.complete_json(
                prompt=prompt,
                system_prompt=SYSTEM_SEVERITY,
                fallback=SEVERITY_FALLBACK,
            )

            if response.success and response.parsed_json:
                result = self._parse_and_validate(response.parsed_json)
                audit_status = AuditStatus.SUCCESS
                logger.info(
                    "SeverityAgent complete",
                    extra={
                        "incident_id": state.incident_id,
                        "severity": result.severity_level,
                        "confidence": result.confidence,
                    },
                )
            else:
                # LLM failed — use safe fallback
                logger.warning(
                    "SeverityAgent using fallback",
                    extra={"incident_id": state.incident_id, "error": response.error},
                )
                result = self._build_fallback_result()
                audit_status = AuditStatus.FALLBACK

        except Exception as e:
            logger.error(
                f"SeverityAgent unexpected error: {e}",
                extra={"incident_id": state.incident_id},
            )
            result = self._build_fallback_result()
            audit_status = AuditStatus.FAILED
            response = type("R", (), {"retry_count": 0, "latency_ms": 0, "error": str(e)})()

        latency_ms = int((time.monotonic() - start_ms) * 1000)

        # Write result into shared state
        state.severity_result = result

        # Append audit entry
        state.add_audit_entry(StageAuditEntry(
            stage=stage.value,
            status=audit_status.value,
            confidence=result.confidence,
            latency_ms=latency_ms,
            retry_count=getattr(response, "retry_count", 0),
            llm_model=self._llm.model_name,
            error_message=getattr(response, "error", None),
        ))

        return state

    def _parse_and_validate(self, raw: dict) -> SeverityResult:
        """
        Parse the LLM JSON output into a SeverityResult, validating each field.
        Unknown or missing values are replaced with safe defaults.
        """
        severity_level = str(raw.get("severity_level", "unknown")).lower()
        if severity_level not in self.VALID_SEVERITY_LEVELS:
            severity_level = "unknown"

        urgency = str(raw.get("urgency", "unknown")).lower()
        if urgency not in self.VALID_URGENCY_LEVELS:
            urgency = "unknown"

        category = str(raw.get("incident_category", "unknown")).lower()
        if category not in self.VALID_CATEGORIES:
            category = "unknown"

        confidence = _clamp_confidence(raw.get("confidence", 0.10))
        fallback_used = bool(raw.get("fallback_used", False))

        return SeverityResult(
            severity_level=severity_level,
            urgency=urgency,
            incident_category=category,
            confidence=confidence,
            reasoning=str(raw.get("reasoning", ""))[:500],  # cap length
            fallback_used=fallback_used,
        )

    def _build_fallback_result(self) -> SeverityResult:
        return SeverityResult(**SEVERITY_FALLBACK)


def _clamp_confidence(value) -> float:
    """Clamp a confidence value to the configured [min, max] range."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = settings.min_confidence_score
    return round(
        max(settings.min_confidence_score, min(settings.max_confidence_score, v)),
        4,
    )
