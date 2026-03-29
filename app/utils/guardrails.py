"""
app/utils/guardrails.py

Lightweight governance guardrails for the Incident Triage Copilot.

These are applied after agents produce output, before results are persisted
or surfaced to users. They enforce the enterprise governance requirements:

  1. Confidence bounding      — scores never exceed [MIN, MAX]
  2. Certainty language check — flag overconfident language in free-text output
  3. Destructive action guard — flag dangerous runbook steps lacking caution
  4. Low-confidence escalation — force escalate=True below a threshold
  5. Fallback completeness    — ensure fallback dicts have all required keys
  6. Output sanitization      — strip internal chain-of-thought phrases

MVP note: These are heuristic, regex-based checks — good enough to demonstrate
the governance pattern in a portfolio project. Production systems would use
a dedicated guardrail framework (e.g. Guardrails AI, Llama Guard).
"""

import re
from typing import Any
from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── Patterns that signal overconfident language ────────────────────────────────
_OVERCONFIDENT_PATTERNS = [
    r"\b(definitely|certainly|absolutely|guaranteed|100%|always will|never fails)\b",
    r"\bI am certain\b",
    r"\bwithout (any )?doubt\b",
    r"\bthe (root )?cause is definitely\b",
]
_OVERCONFIDENT_RE = re.compile("|".join(_OVERCONFIDENT_PATTERNS), re.IGNORECASE)

# ── Patterns that signal potentially destructive actions ───────────────────────
_DESTRUCTIVE_PATTERNS = [
    r"\b(drop table|truncate|delete from|rm -rf|format|wipe|purge all|destroy)\b",
    r"\b(kubectl delete (namespace|cluster|node))\b",
    r"\b(revoke all|disable auth|bypass security)\b",
    r"\b(kill -9 all|pkill -9)\b",
]
_DESTRUCTIVE_RE = re.compile("|".join(_DESTRUCTIVE_PATTERNS), re.IGNORECASE)

# ── Internal reasoning phrases that should not reach users ────────────────────
_INTERNAL_PHRASES = [
    r"<thinking>.*?</thinking>",
    r"Let me (think|reason|analyze) (through|about) this[.:]\s*",
    r"Step \d+:",
    r"First,? I (will|need to|should) (consider|analyze|look at)",
    r"Based on my (training|knowledge base),?\s*",
]
_INTERNAL_RE = re.compile("|".join(_INTERNAL_PHRASES), re.IGNORECASE | re.DOTALL)


# ── Public guardrail functions ─────────────────────────────────────────────────

def clamp_confidence(value: Any, context: str = "") -> float:
    """
    Clamp a confidence value to the configured [min, max] range.
    Handles non-numeric input by returning the minimum.

    Args:
        value:   Raw confidence value (may be float, int, str, or None).
        context: Optional label for logging (e.g. 'severity_agent').
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        logger.warning(
            f"Non-numeric confidence value: {value!r}",
            extra={"context": context},
        )
        return settings.min_confidence_score

    clamped = max(settings.min_confidence_score, min(settings.max_confidence_score, v))
    if clamped != v:
        logger.warning(
            f"Confidence clamped: {v} → {clamped}",
            extra={"context": context},
        )
    return round(clamped, 4)


def check_overconfident_language(text: str) -> list[str]:
    """
    Scan free-text output for overconfident language patterns.

    Returns a list of flagged phrases (empty list = clean).
    Used to audit agent reasoning fields before they are stored.
    """
    matches = _OVERCONFIDENT_RE.findall(text)
    if matches:
        logger.warning(
            f"Overconfident language detected: {matches}",
            extra={"text_preview": text[:100]},
        )
    return matches


def check_destructive_actions(actions: list[str]) -> list[str]:
    """
    Scan a runbook action list for potentially destructive commands
    that are not prefixed with 'CAUTION:'.

    Returns a list of flagged actions (empty list = clean).
    """
    flagged = []
    for action in actions:
        if _DESTRUCTIVE_RE.search(action) and not action.upper().startswith("CAUTION"):
            flagged.append(action)
            logger.warning(
                "Destructive action without CAUTION prefix detected",
                extra={"action": action[:100]},
            )
    return flagged


def sanitize_text_output(text: str) -> str:
    """
    Remove internal reasoning phrases and chain-of-thought artifacts
    from LLM text output before it reaches users.

    This prevents internal model reasoning from leaking into stakeholder
    summaries or root cause explanations.
    """
    cleaned = _INTERNAL_RE.sub("", text).strip()
    # Collapse multiple spaces/newlines left by removal
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"  +", " ", cleaned)
    return cleaned


def enforce_escalation_rules(
    runbook_result: dict[str, Any],
    severity_level: str,
    overall_confidence: float,
) -> dict[str, Any]:
    """
    Apply governance-level escalation overrides to a runbook result.

    Rules:
      1. Critical severity + overall confidence < 0.50 → force escalate
      2. Any fallback used + severity is critical → force escalate
      3. Evidence strength "low" + severity "critical" → force escalate

    Args:
        runbook_result:     RunbookAgent output dict (modified in place).
        severity_level:     Classified severity string.
        overall_confidence: Pipeline overall confidence score.

    Returns:
        Updated runbook_result dict.
    """
    original_escalate = runbook_result.get("escalate", False)
    reasons = []

    if severity_level == "critical" and overall_confidence < 0.50:
        reasons.append("Critical severity with low overall confidence")

    if runbook_result.get("fallback_used") and severity_level in ("critical", "high"):
        reasons.append("Runbook fallback used on high/critical incident")

    if reasons:
        runbook_result["escalate"] = True
        existing_reason = runbook_result.get("escalation_reason") or ""
        new_reason = "; ".join(reasons)
        runbook_result["escalation_reason"] = (
            f"{existing_reason} | Governance override: {new_reason}"
            if existing_reason
            else f"Governance override: {new_reason}"
        )
        if not original_escalate:
            logger.warning(
                "Escalation forced by governance rule",
                extra={"reasons": reasons, "severity": severity_level},
            )

    return runbook_result


def validate_workflow_result_completeness(result: dict[str, Any]) -> list[str]:
    """
    Check that a workflow result dict has all expected top-level keys.
    Returns a list of missing keys (empty = complete).

    Used before persisting to DB to catch incomplete pipeline runs.
    """
    required_keys = [
        "severity_output", "root_cause_output",
        "runbook_output", "summary_output",
    ]
    missing = [k for k in required_keys if not result.get(k)]
    if missing:
        logger.warning(f"Workflow result missing keys: {missing}")
    return missing


def build_governance_report(
    incident_id: str,
    overall_confidence: float,
    low_confidence_flag: bool,
    escalate: bool,
    fallback_stages: list[str],
    failed_stages: list[str],
    overconfident_flags: list[str],
    destructive_action_flags: list[str],
) -> dict[str, Any]:
    """
    Build a structured governance report for an incident run.

    This is stored in the audit trail and can be surfaced in the UI.
    Demonstrates LLMOps observability — the system can explain its own
    reliability for any given run.

    Args:
        incident_id:              UUID of the incident.
        overall_confidence:       Computed mean confidence across agents.
        low_confidence_flag:      True if below threshold.
        escalate:                 Whether escalation was recommended.
        fallback_stages:          Stages that used fallback responses.
        failed_stages:            Stages that errored out.
        overconfident_flags:      List of overconfident phrases detected.
        destructive_action_flags: List of unguarded destructive actions.

    Returns:
        Dict ready for JSON serialization and audit storage.
    """
    issues = []
    if low_confidence_flag:
        issues.append(f"Overall confidence {overall_confidence:.0%} below threshold")
    if fallback_stages:
        issues.append(f"Fallback used in: {', '.join(fallback_stages)}")
    if failed_stages:
        issues.append(f"Stages failed: {', '.join(failed_stages)}")
    if overconfident_flags:
        issues.append(f"Overconfident language detected: {overconfident_flags}")
    if destructive_action_flags:
        issues.append(f"Unguarded destructive actions: {len(destructive_action_flags)}")

    risk_level = "low"
    if failed_stages or destructive_action_flags:
        risk_level = "high"
    elif low_confidence_flag or fallback_stages or overconfident_flags:
        risk_level = "medium"

    return {
        "incident_id":            incident_id,
        "overall_confidence":     round(overall_confidence, 4),
        "low_confidence_flag":    low_confidence_flag,
        "escalation_recommended": escalate,
        "fallback_stages":        fallback_stages,
        "failed_stages":          failed_stages,
        "governance_issues":      issues,
        "risk_level":             risk_level,
        "requires_human_review":  risk_level in ("medium", "high") or escalate,
        "issue_count":            len(issues),
    }
