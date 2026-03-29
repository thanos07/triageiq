"""
app/agents/summary_agent.py

SummaryAgent: fourth and final stage of the triage pipeline.

Responsibilities:
  - Synthesize all prior agent outputs into a single stakeholder summary
  - Write for a non-technical audience (managers, executives, CX teams)
  - State impact, probable cause, and next recommended action
  - Reflect confidence honestly — no false certainty in the language

Reads:  state.normalized_incident, state.severity_result,
        state.root_cause_result, state.runbook_result
Writes: state.summary_result, state.stage_audit_trail
"""

import time

from app.llm.base import BaseLLMProvider
from app.llm.prompt_templates import (
    build_summary_prompt,
    SYSTEM_SUMMARY,
    SUMMARY_FALLBACK,
    SEVERITY_FALLBACK,
    ROOT_CAUSE_FALLBACK,
    RUNBOOK_FALLBACK,
)
from app.schemas.workflow import WorkflowState, SummaryResult, StageAuditEntry
from app.utils.logger import get_logger
from app.utils.audit_logger import AuditStage, AuditStatus
from app.config import settings

logger = get_logger(__name__)


class SummaryAgent:
    """
    Generates a plain-language stakeholder summary from the full triage output.

    This agent has the broadest context — it reads all three prior results
    and synthesizes them into a single coherent narrative.

    Usage:
        agent = SummaryAgent(llm_provider)
        state = agent.run(state)
    """

    def __init__(self, llm: BaseLLMProvider):
        self._llm = llm

    def run(self, state: WorkflowState) -> WorkflowState:
        """
        Execute stakeholder summary generation.

        Reads:  state.normalized_incident, state.severity_result,
                state.root_cause_result, state.runbook_result
        Writes: state.summary_result, state.stage_audit_trail
        Always returns state — never raises.
        """
        stage = AuditStage.SUMMARY
        start_ms = time.monotonic()
        incident = state.normalized_incident or {}

        severity_dict = (
            state.severity_result.model_dump()
            if state.severity_result
            else SEVERITY_FALLBACK
        )
        root_cause_dict = (
            state.root_cause_result.model_dump()
            if state.root_cause_result
            else ROOT_CAUSE_FALLBACK
        )
        runbook_dict = (
            state.runbook_result.model_dump()
            if state.runbook_result
            else RUNBOOK_FALLBACK
        )

        logger.info(
            "SummaryAgent starting",
            extra={"incident_id": state.incident_id},
        )

        try:
            prompt = build_summary_prompt(
                incident, severity_dict, root_cause_dict, runbook_dict
            )
            response = self._llm.complete_json(
                prompt=prompt,
                system_prompt=SYSTEM_SUMMARY,
                fallback=SUMMARY_FALLBACK,
            )

            if response.success and response.parsed_json:
                result = self._parse_and_validate(
                    response.parsed_json,
                    severity_dict=severity_dict,
                    runbook_dict=runbook_dict,
                )
                audit_status = AuditStatus.SUCCESS
                logger.info(
                    "SummaryAgent complete",
                    extra={
                        "incident_id": state.incident_id,
                        "confidence": result.confidence,
                        "summary_chars": len(result.summary_text),
                    },
                )
            else:
                logger.warning(
                    "SummaryAgent using fallback",
                    extra={"incident_id": state.incident_id, "error": response.error},
                )
                result = self._build_fallback_result()
                audit_status = AuditStatus.FALLBACK

        except Exception as e:
            logger.error(
                f"SummaryAgent unexpected error: {e}",
                extra={"incident_id": state.incident_id},
            )
            result = self._build_fallback_result()
            audit_status = AuditStatus.FAILED
            response = type("R", (), {"retry_count": 0, "latency_ms": 0, "error": str(e)})()

        latency_ms = int((time.monotonic() - start_ms) * 1000)
        state.summary_result = result

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

    def _parse_and_validate(
        self,
        raw: dict,
        severity_dict: dict,
        runbook_dict: dict,
    ) -> SummaryResult:
        """Validate and enrich LLM output into a typed SummaryResult."""
        summary_text = str(raw.get("summary_text", "")).strip()
        if not summary_text:
            summary_text = SUMMARY_FALLBACK["summary_text"]

        probable_impact = str(raw.get("probable_impact", "")).strip()
        if not probable_impact:
            probable_impact = SUMMARY_FALLBACK["probable_impact"]

        next_action = str(raw.get("next_action", "")).strip()
        if not next_action:
            # Derive a sensible default from escalation status
            if runbook_dict.get("escalate"):
                next_action = "Escalate to on-call engineer immediately."
            else:
                next_action = SUMMARY_FALLBACK["next_action"]

        confidence = _clamp_confidence(raw.get("confidence", 0.10))

        # If escalation is recommended, cap confidence to signal uncertainty
        if runbook_dict.get("escalate") and confidence > 0.70:
            confidence = min(confidence, 0.70)

        return SummaryResult(
            summary_text=summary_text[:2000],
            probable_impact=probable_impact[:500],
            next_action=next_action[:300],
            confidence=confidence,
            fallback_used=bool(raw.get("fallback_used", False)),
        )

    def _build_fallback_result(self) -> SummaryResult:
        return SummaryResult(**SUMMARY_FALLBACK)


def _clamp_confidence(value) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = settings.min_confidence_score
    return round(
        max(settings.min_confidence_score, min(settings.max_confidence_score, v)),
        4,
    )
