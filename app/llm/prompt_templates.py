"""
app/llm/prompt_templates.py

All LLM prompt templates for the Incident Triage Copilot.

Design principles:
  - ALL prompts live here — agents import from this file, never define inline.
  - Every prompt specifies exactly what JSON schema to return.
  - Prompts instruct the model to express uncertainty honestly.
  - No prompt claims certainty it cannot have.
  - Prompts are versioned via the module — git history tracks prompt changes.

Governance notes:
  - Prompts explicitly forbid hallucinated certainty ("Do not guess").
  - Runbook prompt forbids dangerous destructive actions without escalation.
  - Summary prompt is written for non-technical stakeholder consumption.
  - All prompts request confidence scores to enable the governance layer.
"""

from typing import Any


# ══════════════════════════════════════════════════════════════════════════════
# SYSTEM PROMPTS
# Shared instruction context injected before the user prompt.
# ══════════════════════════════════════════════════════════════════════════════

SYSTEM_TRIAGE_BASE = """
You are an expert Site Reliability Engineer (SRE) and incident response specialist.
You are assisting with structured incident triage for an enterprise operations team.

Your responses must be:
- Factual and based only on the information provided
- Honest about uncertainty — if evidence is weak, say so explicitly
- Calibrated: do not claim high confidence unless the evidence strongly supports it
- Safe: never suggest destructive remediation steps without explicit caution and escalation language
- Concise: focused on actionable intelligence, not filler

You are part of an automated triage pipeline. A human reviewer will review your output
before any actions are taken. Your role is to assist, not to act autonomously.
""".strip()


SYSTEM_SEVERITY = f"""
{SYSTEM_TRIAGE_BASE}

Your specific task: classify the severity and urgency of the described incident,
and determine its broad category. You must express honest confidence in your assessment.
""".strip()


SYSTEM_ROOT_CAUSE = f"""
{SYSTEM_TRIAGE_BASE}

Your specific task: infer the most probable root cause of this incident from the
available evidence. You must clearly state when evidence is weak or incomplete,
and you must never fabricate causal chains that are not supported by the description.
""".strip()


SYSTEM_RUNBOOK = f"""
{SYSTEM_TRIAGE_BASE}

Your specific task: recommend specific, actionable remediation steps based on the
incident details and likely root cause. You must use the runbook knowledge provided.
Safety rule: If any step could cause data loss, service disruption, or is irreversible,
you MUST flag it with a caution note. Escalate if the situation is unclear or high-risk.
""".strip()


SYSTEM_SUMMARY = f"""
{SYSTEM_TRIAGE_BASE}

Your specific task: write a clear, concise summary for non-technical stakeholders
(managers, executives, customer success teams). Avoid jargon. Explain the impact
in plain language. Do not include internal reasoning or technical implementation details.
""".strip()


# ══════════════════════════════════════════════════════════════════════════════
# SEVERITY AGENT PROMPT
# ══════════════════════════════════════════════════════════════════════════════

def build_severity_prompt(incident: dict[str, Any]) -> str:
    """
    Build the prompt for SeverityAgent.

    Args:
        incident: NormalizedIncident dict with title, description, etc.

    Returns:
        A complete prompt string requesting a JSON severity assessment.
    """
    return f"""
Analyze the following incident and classify its severity.

INCIDENT DETAILS:
- Title: {incident.get('title', 'N/A')}
- Description: {incident.get('description', 'N/A')}
- Service: {incident.get('service_name', 'unknown')}
- Environment: {incident.get('environment', 'unknown')}
- Reported Severity: {incident.get('raw_severity', 'unknown')}

TASK:
Classify this incident and return a JSON object with exactly these fields:

{{
  "severity_level": "<one of: critical | high | medium | low | unknown>",
  "urgency": "<one of: immediate | high | medium | low>",
  "incident_category": "<one of: database | network | authentication | infrastructure | payments | messaging | security | application | unknown>",
  "confidence": <float between 0.10 and 0.95>,
  "reasoning": "<2-3 sentence explanation of your classification>",
  "fallback_used": false
}}

SEVERITY DEFINITIONS:
- critical: Service is down or severely degraded, affecting production users or revenue
- high: Major functionality impaired, workaround exists but experience significantly degraded
- medium: Partial degradation, limited user impact, workaround available
- low: Minor issue, no immediate user impact

CONFIDENCE GUIDANCE:
- Use 0.85-0.95 only when the description clearly and unambiguously maps to a severity level
- Use 0.60-0.84 when the description is suggestive but incomplete
- Use 0.40-0.59 when key context is missing (environment, affected scope, etc.)
- Use 0.10-0.39 when evidence is very sparse or contradictory

Return only the JSON object. No explanation outside the JSON.
""".strip()


# ══════════════════════════════════════════════════════════════════════════════
# ROOT CAUSE AGENT PROMPT
# ══════════════════════════════════════════════════════════════════════════════

def build_root_cause_prompt(
    incident: dict[str, Any],
    severity_result: dict[str, Any],
) -> str:
    """
    Build the prompt for RootCauseAgent.

    Args:
        incident:        NormalizedIncident dict.
        severity_result: Output dict from SeverityAgent (provides context).

    Returns:
        A complete prompt string requesting a JSON root cause inference.
    """
    return f"""
Analyze the following incident and infer the most probable root cause.

INCIDENT DETAILS:
- Title: {incident.get('title', 'N/A')}
- Description: {incident.get('description', 'N/A')}
- Service: {incident.get('service_name', 'unknown')}
- Environment: {incident.get('environment', 'unknown')}

SEVERITY CONTEXT (from prior analysis):
- Classified Severity: {severity_result.get('severity_level', 'unknown')}
- Incident Category: {severity_result.get('incident_category', 'unknown')}

TASK:
Infer the most probable root cause and return a JSON object with exactly these fields:

{{
  "probable_cause": "<concise statement of the most likely root cause, 1-3 sentences>",
  "evidence_strength": "<one of: high | medium | low>",
  "confidence": <float between 0.10 and 0.95>,
  "uncertainty_note": "<explicit statement of what is unknown or uncertain — leave empty string if confidence is high>",
  "contributing_factors": ["<factor 1>", "<factor 2>"],
  "fallback_used": false
}}

EVIDENCE STRENGTH DEFINITIONS:
- high: The description explicitly identifies or strongly implies the cause
- medium: The description is consistent with this cause but other causes are possible
- low: This is an educated guess — the description does not clearly indicate the cause

IMPORTANT RULES:
- Do not fabricate technical details that are not in the description
- If the description is vague, you MUST use evidence_strength "low" and explain in uncertainty_note
- It is better to honestly say "insufficient evidence" than to invent a cause
- Contributing factors should be directly derivable from the description

Return only the JSON object. No explanation outside the JSON.
""".strip()


# ══════════════════════════════════════════════════════════════════════════════
# RUNBOOK AGENT PROMPT
# ══════════════════════════════════════════════════════════════════════════════

def build_runbook_prompt(
    incident: dict[str, Any],
    severity_result: dict[str, Any],
    root_cause_result: dict[str, Any],
    runbook_context: str,
) -> str:
    """
    Build the prompt for RunbookAgent.

    Args:
        incident:          NormalizedIncident dict.
        severity_result:   SeverityAgent output dict.
        root_cause_result: RootCauseAgent output dict.
        runbook_context:   Pre-retrieved runbook text injected as context.

    Returns:
        A complete prompt string requesting a JSON runbook recommendation.
    """
    return f"""
You are recommending remediation steps for an active incident.

INCIDENT DETAILS:
- Title: {incident.get('title', 'N/A')}
- Description: {incident.get('description', 'N/A')}
- Service: {incident.get('service_name', 'unknown')}
- Environment: {incident.get('environment', 'unknown')}

PRIOR ANALYSIS:
- Severity: {severity_result.get('severity_level', 'unknown')} ({severity_result.get('urgency', 'unknown')} urgency)
- Category: {severity_result.get('incident_category', 'unknown')}
- Probable Root Cause: {root_cause_result.get('probable_cause', 'unknown')}
- Evidence Strength: {root_cause_result.get('evidence_strength', 'low')}

AVAILABLE RUNBOOK KNOWLEDGE:
{runbook_context}

TASK:
Recommend remediation steps based on the runbook knowledge and incident context.
Return a JSON object with exactly these fields:

{{
  "matched_runbook": "<name of the most relevant runbook, or null if none matched>",
  "actions": [
    "<step 1: specific, actionable instruction>",
    "<step 2>",
    "<step 3>"
  ],
  "escalate": <true if you recommend immediate human escalation, false otherwise>,
  "escalation_reason": "<reason for escalation if escalate is true, else null>",
  "confidence": <float between 0.10 and 0.95>,
  "fallback_used": false
}}

RULES:
- Actions must be specific and actionable — not vague advice like "check the logs"
- Include 3-8 actions ordered from most immediate to longer-term
- If any action could cause data loss or service disruption, prefix it with "CAUTION: "
- Recommend escalation (escalate: true) if:
    * Root cause evidence strength is "low"
    * Severity is "critical" and cause is unknown
    * The incident involves financial data, security, or compliance
    * You are not confident in the recommended steps
- Do not invent runbook steps not grounded in the provided runbook knowledge

Return only the JSON object. No explanation outside the JSON.
""".strip()


# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY AGENT PROMPT
# ══════════════════════════════════════════════════════════════════════════════

def build_summary_prompt(
    incident: dict[str, Any],
    severity_result: dict[str, Any],
    root_cause_result: dict[str, Any],
    runbook_result: dict[str, Any],
) -> str:
    """
    Build the prompt for SummaryAgent.

    Args:
        incident:          NormalizedIncident dict.
        severity_result:   SeverityAgent output dict.
        root_cause_result: RootCauseAgent output dict.
        runbook_result:    RunbookAgent output dict.

    Returns:
        A complete prompt string requesting a JSON stakeholder summary.
    """
    actions_preview = runbook_result.get("actions", [])[:3]
    actions_text = "\n".join(f"  - {a}" for a in actions_preview) if actions_preview else "  - No specific actions recommended"

    return f"""
Write a clear, non-technical summary of this incident for stakeholders.

INCIDENT DETAILS:
- Title: {incident.get('title', 'N/A')}
- Service: {incident.get('service_name', 'unknown')}
- Environment: {incident.get('environment', 'unknown')}

TRIAGE RESULTS:
- Severity: {severity_result.get('severity_level', 'unknown').upper()}
- Urgency: {severity_result.get('urgency', 'unknown')}
- Category: {severity_result.get('incident_category', 'unknown')}
- Probable Root Cause: {root_cause_result.get('probable_cause', 'Not determined')}
- Evidence Confidence: {root_cause_result.get('evidence_strength', 'low')}
- Escalation Recommended: {runbook_result.get('escalate', False)}

TOP REMEDIATION STEPS:
{actions_text}

TASK:
Write a stakeholder summary and return a JSON object with exactly these fields:

{{
  "summary_text": "<2-4 sentence non-technical summary of the incident, what happened, and current status>",
  "probable_impact": "<1-2 sentences describing what users or business processes are affected>",
  "next_action": "<single most important next step, written as a clear instruction to the operations team>",
  "confidence": <float between 0.10 and 0.95 reflecting overall analysis confidence>,
  "fallback_used": false
}}

RULES FOR SUMMARY:
- Write for a non-technical audience (managers, executives, customer success)
- Avoid technical jargon (no "CrashLoopBackOff", "SQS", "kubectl", etc.)
- State clearly what is known vs. what is still being investigated
- Do not overstate certainty — if confidence is low, reflect that in the language
- Keep summary_text under 100 words
- next_action should be a clear, single imperative sentence

Return only the JSON object. No explanation outside the JSON.
""".strip()


# ══════════════════════════════════════════════════════════════════════════════
# FALLBACK DEFAULTS
# Safe defaults used when LLM calls fail or produce unparseable output.
# These ensure agents always return valid structured data.
# ══════════════════════════════════════════════════════════════════════════════

SEVERITY_FALLBACK: dict[str, Any] = {
    "severity_level": "unknown",
    "urgency": "unknown",
    "incident_category": "unknown",
    "confidence": 0.10,
    "reasoning": "Severity classification unavailable — LLM call failed. Manual review required.",
    "fallback_used": True,
}

ROOT_CAUSE_FALLBACK: dict[str, Any] = {
    "probable_cause": "Root cause could not be determined automatically. Manual investigation required.",
    "evidence_strength": "low",
    "confidence": 0.10,
    "uncertainty_note": "Automated analysis failed. A human reviewer should inspect logs and recent changes.",
    "contributing_factors": [],
    "fallback_used": True,
}

RUNBOOK_FALLBACK: dict[str, Any] = {
    "matched_runbook": None,
    "actions": [
        "Automated runbook matching failed — manual runbook lookup required.",
        "Check service logs for error patterns.",
        "Review recent deployments and configuration changes.",
        "Engage the on-call engineer for the affected service.",
        "Open an incident channel and document findings.",
    ],
    "escalate": True,
    "escalation_reason": "Automated analysis failed — human escalation required.",
    "confidence": 0.10,
    "fallback_used": True,
}

SUMMARY_FALLBACK: dict[str, Any] = {
    "summary_text": (
        "An incident has been detected and is currently under investigation. "
        "Automated analysis was unable to complete. "
        "The operations team has been notified and a human reviewer is required."
    ),
    "probable_impact": "Impact scope is not yet determined. Manual assessment in progress.",
    "next_action": "Assign a human engineer to investigate this incident immediately.",
    "confidence": 0.10,
    "fallback_used": True,
}
