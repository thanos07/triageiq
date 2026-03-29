"""
app/agents/root_cause_agent.py

RootCauseAgent: second stage of the triage pipeline.

Responsibilities:
  - Infer the most probable root cause from incident details + severity context
  - Classify evidence strength (high / medium / low)
  - Explicitly state uncertainty when evidence is weak
  - Never fabricate causal chains not supported by the description

Reads:  state.normalized_incident, state.severity_result
Writes: state.root_cause_result, state.stage_audit_trail
"""

import time

from app.llm.base import BaseLLMProvider
from app.llm.prompt_templates import (
    build_root_cause_prompt,
    SYSTEM_ROOT_CAUSE,
    ROOT_CAUSE_FALLBACK,
    SEVERITY_FALLBACK,
)
from app.schemas.workflow import WorkflowState, RootCauseResult, StageAuditEntry
from app.utils.logger import get_logger
from app.utils.audit_logger import AuditStage, AuditStatus
from app.config import settings

logger = get_logger(__name__)


class RootCauseAgent:
    """
    Infers the probable root cause of an incident.

    Depends on SeverityAgent having run first (reads severity_result for context).
    Falls back gracefully if severity_result is missing.

    Usage:
        agent = RootCauseAgent(llm_provider)
        state = agent.run(state)
    """

    VALID_EVIDENCE_STRENGTHS = {"high", "medium", "low"}

    def __init__(self, llm: BaseLLMProvider):
        self._llm = llm

    def run(self, state: WorkflowState) -> WorkflowState:
        """
        Execute root cause inference.

        Reads:  state.normalized_incident, state.severity_result
        Writes: state.root_cause_result, state.stage_audit_trail
        Always returns state — never raises.
        """
        stage = AuditStage.ROOT_CAUSE
        start_ms = time.monotonic()
        incident = state.normalized_incident or {}

        # Use severity output if available; otherwise use the fallback dict
        severity_dict = (
            state.severity_result.model_dump()
            if state.severity_result
            else SEVERITY_FALLBACK
        )

        logger.info(
            "RootCauseAgent starting",
            extra={"incident_id": state.incident_id},
        )

        try:
            prompt = build_root_cause_prompt(incident, severity_dict)
            response = self._llm.complete_json(
                prompt=prompt,
                system_prompt=SYSTEM_ROOT_CAUSE,
                fallback=ROOT_CAUSE_FALLBACK,
            )

            if response.success and response.parsed_json:
                result = self._parse_and_validate(response.parsed_json)
                audit_status = AuditStatus.SUCCESS
                logger.info(
                    "RootCauseAgent complete",
                    extra={
                        "incident_id": state.incident_id,
                        "evidence_strength": result.evidence_strength,
                        "confidence": result.confidence,
                    },
                )
            else:
                logger.warning(
                    "RootCauseAgent using fallback",
                    extra={"incident_id": state.incident_id, "error": response.error},
                )
                result = self._build_fallback_result()
                audit_status = AuditStatus.FALLBACK

        except Exception as e:
            logger.error(
                f"RootCauseAgent unexpected error: {e}",
                extra={"incident_id": state.incident_id},
            )
            result = self._build_fallback_result()
            audit_status = AuditStatus.FAILED
            response = type("R", (), {"retry_count": 0, "latency_ms": 0, "error": str(e)})()

        latency_ms = int((time.monotonic() - start_ms) * 1000)
        state.root_cause_result = result

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

    def _parse_and_validate(self, raw: dict) -> RootCauseResult:
        """
        Validate and clamp the LLM output into a typed RootCauseResult.
        Handles missing or malformed fields defensively.
        """
        evidence_strength = str(raw.get("evidence_strength", "low")).lower()
        if evidence_strength not in self.VALID_EVIDENCE_STRENGTHS:
            evidence_strength = "low"

        confidence = _clamp_confidence(raw.get("confidence", 0.10))

        # If evidence is low, confidence should be capped at 0.50
        if evidence_strength == "low":
            confidence = min(confidence, 0.50)

        probable_cause = str(raw.get("probable_cause", ROOT_CAUSE_FALLBACK["probable_cause"]))
        if not probable_cause.strip():
            probable_cause = ROOT_CAUSE_FALLBACK["probable_cause"]

        # Validate contributing_factors is a list of strings
        raw_factors = raw.get("contributing_factors", [])
        if isinstance(raw_factors, list):
            factors = [str(f) for f in raw_factors[:5]]  # cap at 5
        else:
            factors = []

        return RootCauseResult(
            probable_cause=probable_cause[:1000],
            evidence_strength=evidence_strength,
            confidence=confidence,
            uncertainty_note=str(raw.get("uncertainty_note", ""))[:500],
            fallback_used=bool(raw.get("fallback_used", False)),
        )

    def _build_fallback_result(self) -> RootCauseResult:
        return RootCauseResult(**ROOT_CAUSE_FALLBACK)


def _clamp_confidence(value) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = settings.min_confidence_score
    return round(
        max(settings.min_confidence_score, min(settings.max_confidence_score, v)),
        4,
    )
