"""
tests/test_guardrails.py

Unit tests for governance guardrails.
Run with: pytest tests/test_guardrails.py -v
"""

import pytest
from app.utils.guardrails import (
    clamp_confidence,
    check_overconfident_language,
    check_destructive_actions,
    sanitize_text_output,
    enforce_escalation_rules,
    validate_workflow_result_completeness,
    build_governance_report,
)


# ── clamp_confidence ──────────────────────────────────────────────────────────

class TestClampConfidence:
    def test_normal_value_passes_through(self):
        assert clamp_confidence(0.75) == 0.75

    def test_above_max_is_clamped(self):
        assert clamp_confidence(1.5) == 0.95

    def test_below_min_is_clamped(self):
        assert clamp_confidence(-0.1) == 0.10

    def test_exactly_max_passes(self):
        assert clamp_confidence(0.95) == 0.95

    def test_exactly_min_passes(self):
        assert clamp_confidence(0.10) == 0.10

    def test_none_returns_min(self):
        assert clamp_confidence(None) == 0.10

    def test_string_number_coerced(self):
        assert clamp_confidence("0.80") == 0.80

    def test_non_numeric_string_returns_min(self):
        assert clamp_confidence("high") == 0.10

    def test_result_rounded_to_4_decimals(self):
        result = clamp_confidence(0.123456789)
        assert result == round(0.123456789, 4)


# ── check_overconfident_language ──────────────────────────────────────────────

class TestOverconfidentLanguage:
    def test_clean_text_returns_empty(self):
        text = "The probable cause is autovacuum misconfiguration."
        assert check_overconfident_language(text) == []

    def test_definitely_is_flagged(self):
        text = "The root cause is definitely a memory leak."
        flags = check_overconfident_language(text)
        assert len(flags) > 0

    def test_certainly_is_flagged(self):
        assert check_overconfident_language("This will certainly fix the issue.") != []

    def test_guaranteed_is_flagged(self):
        assert check_overconfident_language("This is guaranteed to work.") != []

    def test_case_insensitive(self):
        assert check_overconfident_language("DEFINITELY a database issue.") != []

    def test_uncertainty_language_is_clean(self):
        text = "This is probably caused by connection pool exhaustion, though evidence is limited."
        assert check_overconfident_language(text) == []


# ── check_destructive_actions ─────────────────────────────────────────────────

class TestDestructiveActions:
    def test_safe_actions_pass(self):
        actions = [
            "Check active connections in the database",
            "Review deployment history with kubectl",
            "Restart the pod after verifying health",
        ]
        assert check_destructive_actions(actions) == []

    def test_rm_rf_without_caution_flagged(self):
        actions = ["Run rm -rf /var/log/old to clear disk space"]
        flagged = check_destructive_actions(actions)
        assert len(flagged) == 1

    def test_drop_table_without_caution_flagged(self):
        actions = ["drop table sessions to reset state"]
        assert len(check_destructive_actions(actions)) == 1

    def test_caution_prefixed_destructive_passes(self):
        actions = ["CAUTION: rm -rf /tmp/cache — only if confirmed safe by DBA"]
        assert check_destructive_actions(actions) == []

    def test_kubectl_delete_namespace_flagged(self):
        actions = ["kubectl delete namespace staging to clean up"]
        assert len(check_destructive_actions(actions)) == 1

    def test_mixed_list_flags_only_dangerous(self):
        actions = [
            "Check pod logs",
            "drop table temp_events",
            "Restart the service",
        ]
        flagged = check_destructive_actions(actions)
        assert len(flagged) == 1
        assert "temp_events" in flagged[0]


# ── sanitize_text_output ──────────────────────────────────────────────────────

class TestSanitizeTextOutput:
    def test_clean_text_unchanged(self):
        text = "The authentication service is down due to a recent deployment."
        assert sanitize_text_output(text) == text

    def test_thinking_tags_removed(self):
        text = "<thinking>Internal reasoning here.</thinking>The service is down."
        result = sanitize_text_output(text)
        assert "<thinking>" not in result
        assert "The service is down." in result

    def test_step_prefixes_removed(self):
        text = "Step 1: Check the logs. Step 2: Restart the service."
        result = sanitize_text_output(text)
        assert "Step 1:" not in result

    def test_extra_whitespace_collapsed(self):
        text = "Line one.\n\n\n\n\nLine two."
        result = sanitize_text_output(text)
        assert "\n\n\n" not in result


# ── enforce_escalation_rules ──────────────────────────────────────────────────

class TestEnforceEscalationRules:
    def _base_result(self):
        return {
            "escalate": False,
            "escalation_reason": None,
            "fallback_used": False,
            "confidence": 0.80,
        }

    def test_no_override_when_not_needed(self):
        result = self._base_result()
        updated = enforce_escalation_rules(result, "high", 0.80)
        assert updated["escalate"] is False

    def test_critical_low_confidence_forces_escalation(self):
        result = self._base_result()
        updated = enforce_escalation_rules(result, "critical", 0.40)
        assert updated["escalate"] is True
        assert "Governance override" in updated["escalation_reason"]

    def test_critical_high_confidence_not_forced(self):
        result = self._base_result()
        updated = enforce_escalation_rules(result, "critical", 0.85)
        assert updated["escalate"] is False

    def test_fallback_on_critical_forces_escalation(self):
        result = self._base_result()
        result["fallback_used"] = True
        updated = enforce_escalation_rules(result, "critical", 0.80)
        assert updated["escalate"] is True

    def test_fallback_on_high_forces_escalation(self):
        result = self._base_result()
        result["fallback_used"] = True
        updated = enforce_escalation_rules(result, "high", 0.80)
        assert updated["escalate"] is True

    def test_fallback_on_low_does_not_force(self):
        result = self._base_result()
        result["fallback_used"] = True
        updated = enforce_escalation_rules(result, "low", 0.80)
        assert updated["escalate"] is False

    def test_existing_escalation_reason_preserved(self):
        result = self._base_result()
        result["escalation_reason"] = "Human judgment required"
        updated = enforce_escalation_rules(result, "critical", 0.30)
        assert "Human judgment required" in updated["escalation_reason"]
        assert "Governance override" in updated["escalation_reason"]


# ── validate_workflow_result_completeness ─────────────────────────────────────

class TestWorkflowResultCompleteness:
    def test_complete_result_passes(self):
        result = {
            "severity_output":   {"level": "high"},
            "root_cause_output": {"cause": "memory leak"},
            "runbook_output":    {"actions": []},
            "summary_output":    {"text": "..."},
        }
        assert validate_workflow_result_completeness(result) == []

    def test_missing_one_key_flagged(self):
        result = {
            "severity_output":   {"level": "high"},
            "root_cause_output": {"cause": "memory leak"},
            "runbook_output":    {"actions": []},
            # summary_output missing
        }
        missing = validate_workflow_result_completeness(result)
        assert "summary_output" in missing

    def test_all_missing_returns_all_keys(self):
        missing = validate_workflow_result_completeness({})
        assert len(missing) == 4

    def test_none_value_treated_as_missing(self):
        result = {
            "severity_output":   None,
            "root_cause_output": {"cause": "..."},
            "runbook_output":    {"actions": []},
            "summary_output":    {"text": "..."},
        }
        missing = validate_workflow_result_completeness(result)
        assert "severity_output" in missing


# ── build_governance_report ───────────────────────────────────────────────────

class TestBuildGovernanceReport:
    def test_healthy_run_produces_low_risk(self):
        report = build_governance_report(
            incident_id="test-001",
            overall_confidence=0.85,
            low_confidence_flag=False,
            escalate=False,
            fallback_stages=[],
            failed_stages=[],
            overconfident_flags=[],
            destructive_action_flags=[],
        )
        assert report["risk_level"] == "low"
        assert report["issue_count"] == 0
        assert report["requires_human_review"] is False

    def test_failed_stages_produces_high_risk(self):
        report = build_governance_report(
            incident_id="test-002",
            overall_confidence=0.30,
            low_confidence_flag=True,
            escalate=True,
            fallback_stages=["severity"],
            failed_stages=["root_cause"],
            overconfident_flags=[],
            destructive_action_flags=[],
        )
        assert report["risk_level"] == "high"
        assert report["requires_human_review"] is True
        assert report["issue_count"] > 0

    def test_low_confidence_produces_medium_risk(self):
        report = build_governance_report(
            incident_id="test-003",
            overall_confidence=0.35,
            low_confidence_flag=True,
            escalate=False,
            fallback_stages=[],
            failed_stages=[],
            overconfident_flags=[],
            destructive_action_flags=[],
        )
        assert report["risk_level"] == "medium"
        assert report["requires_human_review"] is True

    def test_destructive_actions_produce_high_risk(self):
        report = build_governance_report(
            incident_id="test-004",
            overall_confidence=0.85,
            low_confidence_flag=False,
            escalate=False,
            fallback_stages=[],
            failed_stages=[],
            overconfident_flags=[],
            destructive_action_flags=["drop table events"],
        )
        assert report["risk_level"] == "high"
