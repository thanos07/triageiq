"""
app/agents/runbook_agent.py

RunbookAgent: third stage of the triage pipeline.

Responsibilities:
  - Match the incident to relevant runbook entries from the knowledge base
  - Recommend ordered, specific remediation steps
  - Flag dangerous/destructive steps with CAUTION language
  - Recommend escalation when confidence is low or incident is high-risk
  - Never invent steps not grounded in the provided runbook knowledge

Reads:  state.normalized_incident, state.severity_result, state.root_cause_result
Writes: state.runbook_result, state.stage_audit_trail
"""

import json
import os
import time
from typing import Optional

from app.llm.base import BaseLLMProvider
from app.llm.prompt_templates import (
    build_runbook_prompt,
    SYSTEM_RUNBOOK,
    RUNBOOK_FALLBACK,
    SEVERITY_FALLBACK,
    ROOT_CAUSE_FALLBACK,
)
from app.schemas.workflow import WorkflowState, RunbookResult, StageAuditEntry
from app.utils.logger import get_logger
from app.utils.audit_logger import AuditStage, AuditStatus
from app.config import settings

logger = get_logger(__name__)

# Path to the runbook knowledge base JSON file
_RUNBOOK_PATH = os.path.join(
    os.path.dirname(__file__), "..", "data", "runbooks.json"
)


class RunbookAgent:
    """
    Retrieves relevant runbook steps and recommends remediation actions.

    Runbook retrieval strategy (MVP — no vector DB needed):
      1. Load all runbooks from runbooks.json (cached after first load)
      2. Score each runbook by keyword overlap with the incident text
      3. Select the top 2 matching runbooks
      4. Inject their content as context into the LLM prompt
      5. LLM synthesizes the best steps from that context

    This lightweight approach avoids embedding infrastructure while still
    demonstrating the RAG pattern at a portfolio level.

    Usage:
        agent = RunbookAgent(llm_provider)
        state = agent.run(state)
    """

    def __init__(self, llm: BaseLLMProvider):
        self._llm = llm
        self._runbooks: Optional[list[dict]] = None  # lazy-loaded cache

    def run(self, state: WorkflowState) -> WorkflowState:
        """
        Execute runbook retrieval and recommendation.

        Reads:  state.normalized_incident, state.severity_result, state.root_cause_result
        Writes: state.runbook_result, state.stage_audit_trail
        Always returns state — never raises.
        """
        stage = AuditStage.RUNBOOK
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

        logger.info(
            "RunbookAgent starting",
            extra={"incident_id": state.incident_id},
        )

        try:
            # Step 1: Retrieve relevant runbooks via keyword matching
            runbook_context = self._retrieve_runbook_context(
                incident=incident,
                severity_dict=severity_dict,
                root_cause_dict=root_cause_dict,
            )

            # Step 2: LLM synthesizes recommended steps from context
            prompt = build_runbook_prompt(
                incident, severity_dict, root_cause_dict, runbook_context
            )
            response = self._llm.complete_json(
                prompt=prompt,
                system_prompt=SYSTEM_RUNBOOK,
                fallback=RUNBOOK_FALLBACK,
            )

            if response.success and response.parsed_json:
                result = self._parse_and_validate(response.parsed_json, severity_dict)
                audit_status = AuditStatus.SUCCESS
                logger.info(
                    "RunbookAgent complete",
                    extra={
                        "incident_id": state.incident_id,
                        "matched_runbook": result.matched_runbook,
                        "action_count": len(result.actions),
                        "escalate": result.escalate,
                        "confidence": result.confidence,
                    },
                )
            else:
                logger.warning(
                    "RunbookAgent using fallback",
                    extra={"incident_id": state.incident_id, "error": response.error},
                )
                result = self._build_fallback_result()
                audit_status = AuditStatus.FALLBACK

        except Exception as e:
            logger.error(
                f"RunbookAgent unexpected error: {e}",
                extra={"incident_id": state.incident_id},
            )
            result = self._build_fallback_result()
            audit_status = AuditStatus.FAILED
            response = type("R", (), {"retry_count": 0, "latency_ms": 0, "error": str(e)})()

        latency_ms = int((time.monotonic() - start_ms) * 1000)
        state.runbook_result = result

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

    # ── Runbook retrieval ──────────────────────────────────────────────────────

    def _load_runbooks(self) -> list[dict]:
        """Load and cache the runbook knowledge base from disk."""
        if self._runbooks is not None:
            return self._runbooks
        try:
            path = os.path.abspath(_RUNBOOK_PATH)
            with open(path, "r") as f:
                self._runbooks = json.load(f)
            logger.info(f"Loaded {len(self._runbooks)} runbooks from {path}")
        except Exception as e:
            logger.error(f"Failed to load runbooks: {e}")
            self._runbooks = []
        return self._runbooks

    def _score_runbook(self, runbook: dict, search_text: str) -> int:
        """
        Score a runbook by keyword overlap with the search text.

        Simple TF-style scoring: +1 per keyword hit, +2 for category match.
        This is deliberately simple — good enough for an MVP demo.
        In production you would use embeddings + cosine similarity.
        """
        text_lower = search_text.lower()
        score = 0

        for keyword in runbook.get("keywords", []):
            if keyword.lower() in text_lower:
                score += 1

        # Bonus for category match
        category = runbook.get("category", "").lower()
        if category and category in text_lower:
            score += 2

        return score

    def _retrieve_runbook_context(
        self,
        incident: dict,
        severity_dict: dict,
        root_cause_dict: dict,
    ) -> str:
        """
        Retrieve the top 2 most relevant runbooks and format them as context.

        Builds a combined search string from incident title, description,
        category, and probable root cause, then scores all runbooks.

        Returns a formatted string ready to inject into the LLM prompt.
        """
        runbooks = self._load_runbooks()
        if not runbooks:
            return "No runbook knowledge base available. Use general SRE best practices."

        # Build search text from all available context
        search_parts = [
            incident.get("title", ""),
            incident.get("description", ""),
            severity_dict.get("incident_category", ""),
            root_cause_dict.get("probable_cause", ""),
            incident.get("service_name", ""),
        ]
        search_text = " ".join(p for p in search_parts if p)

        # Score and rank all runbooks
        scored = [
            (self._score_runbook(rb, search_text), rb)
            for rb in runbooks
        ]
        scored.sort(key=lambda x: x[0], reverse=True)

        # Take top 2 (or general escalation runbook as fallback)
        top_runbooks = [rb for score, rb in scored[:2] if score > 0]

        # Always include the general escalation runbook if low confidence
        if not top_runbooks:
            general = next(
                (rb for rb in runbooks if rb.get("id") == "rb-012"), None
            )
            if general:
                top_runbooks = [general]

        if not top_runbooks:
            return "No matching runbook found. Apply general incident response practices."

        # Format as readable context for the prompt
        context_parts = []
        for rb in top_runbooks:
            actions_text = "\n".join(
                f"  {i+1}. {a}" for i, a in enumerate(rb.get("actions", []))
            )
            caution = rb.get("caution", "")
            caution_text = f"\nCAUTION: {caution}" if caution else ""
            escalation_triggers = rb.get("escalation_triggers", [])
            triggers_text = (
                "\nEscalate immediately if: " + "; ".join(escalation_triggers)
                if escalation_triggers else ""
            )
            context_parts.append(
                f"RUNBOOK: {rb['name']}\n"
                f"Category: {rb['category']}\n"
                f"Steps:\n{actions_text}"
                f"{caution_text}"
                f"{triggers_text}"
            )

        return "\n\n---\n\n".join(context_parts)

    # ── Output validation ──────────────────────────────────────────────────────

    def _parse_and_validate(self, raw: dict, severity_dict: dict) -> RunbookResult:
        """Validate LLM output into a typed RunbookResult."""
        actions = raw.get("actions", [])
        if isinstance(actions, list):
            actions = [str(a) for a in actions if a][:10]  # cap at 10 steps
        else:
            actions = RUNBOOK_FALLBACK["actions"]

        if not actions:
            actions = RUNBOOK_FALLBACK["actions"]

        confidence = _clamp_confidence(raw.get("confidence", 0.10))
        escalate = bool(raw.get("escalate", False))

        # Force escalation if severity is critical and evidence was weak
        if severity_dict.get("severity_level") == "critical" and confidence < 0.50:
            escalate = True

        escalation_reason = raw.get("escalation_reason")
        if escalate and not escalation_reason:
            escalation_reason = "Low confidence in automated analysis — human review required."

        matched_runbook = raw.get("matched_runbook")
        if matched_runbook and not isinstance(matched_runbook, str):
            matched_runbook = None

        return RunbookResult(
            matched_runbook=matched_runbook,
            actions=actions,
            escalate=escalate,
            escalation_reason=escalation_reason,
            confidence=confidence,
            fallback_used=bool(raw.get("fallback_used", False)),
        )

    def _build_fallback_result(self) -> RunbookResult:
        return RunbookResult(**RUNBOOK_FALLBACK)


def _clamp_confidence(value) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = settings.min_confidence_score
    return round(
        max(settings.min_confidence_score, min(settings.max_confidence_score, v)),
        4,
    )
