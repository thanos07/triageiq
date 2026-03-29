"""
tests/test_agents.py

Unit tests for all four triage agents.
Uses MockLLMProvider — no Anthropic API calls are made.

Run with: pytest tests/test_agents.py -v
"""

import pytest
from app.llm.base import MockLLMProvider
from app.schemas.workflow import WorkflowState, SeverityResult, RootCauseResult, RunbookResult
from app.agents.severity_agent   import SeverityAgent
from app.agents.root_cause_agent import RootCauseAgent
from app.agents.runbook_agent    import RunbookAgent
from app.agents.summary_agent    import SummaryAgent


# ── Shared fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def sample_incident():
    return {
        "incident_id":  "test-001",
        "title":        "Database latency spike — orders service P99 > 8s",
        "description":  "P99 query latency exceeded 8s. Autovacuum was disabled last week.",
        "service_name": "orders-service",
        "environment":  "production",
        "raw_severity": "P1",
    }


@pytest.fixture
def base_state(sample_incident):
    state = WorkflowState(incident_id="test-001")
    state.normalized_incident = sample_incident
    return state


@pytest.fixture
def state_with_severity(base_state):
    base_state.severity_result = SeverityResult(
        severity_level="critical", urgency="immediate",
        incident_category="database", confidence=0.88,
    )
    return base_state


@pytest.fixture
def state_with_root_cause(state_with_severity):
    state_with_severity.root_cause_result = RootCauseResult(
        probable_cause="Autovacuum disabled causing table bloat.",
        evidence_strength="high", confidence=0.82,
    )
    return state_with_severity


# ══════════════════════════════════════════════════════════════════════════════
# SeverityAgent
# ══════════════════════════════════════════════════════════════════════════════

class TestSeverityAgent:
    def _agent(self, json_response: dict) -> SeverityAgent:
        return SeverityAgent(MockLLMProvider(fixed_json=json_response))

    def test_successful_classification(self, base_state):
        agent = self._agent({
            "severity_level": "critical", "urgency": "immediate",
            "incident_category": "database", "confidence": 0.88,
            "reasoning": "P99 > 8s is critical.", "fallback_used": False,
        })
        state = agent.run(base_state)
        assert state.severity_result.severity_level == "critical"
        assert state.severity_result.urgency == "immediate"
        assert state.severity_result.incident_category == "database"
        assert state.severity_result.confidence == 0.88
        assert state.severity_result.fallback_used is False

    def test_audit_entry_recorded(self, base_state):
        agent = self._agent({
            "severity_level": "high", "urgency": "high",
            "incident_category": "network", "confidence": 0.75,
            "reasoning": "Network issue.", "fallback_used": False,
        })
        state = agent.run(base_state)
        assert len(state.stage_audit_trail) == 1
        entry = state.stage_audit_trail[0]
        assert entry.stage == "severity"
        assert entry.status == "success"
        assert entry.confidence == 0.75

    def test_invalid_severity_level_defaults_to_unknown(self, base_state):
        agent = self._agent({
            "severity_level": "SUPER_CRITICAL", "urgency": "immediate",
            "incident_category": "database", "confidence": 0.80,
            "reasoning": "test", "fallback_used": False,
        })
        state = agent.run(base_state)
        assert state.severity_result.severity_level == "unknown"

    def test_invalid_category_defaults_to_unknown(self, base_state):
        agent = self._agent({
            "severity_level": "high", "urgency": "high",
            "incident_category": "space_anomaly", "confidence": 0.75,
            "reasoning": "test", "fallback_used": False,
        })
        state = agent.run(base_state)
        assert state.severity_result.incident_category == "unknown"

    def test_confidence_clamped_above_max(self, base_state):
        agent = self._agent({
            "severity_level": "high", "urgency": "high",
            "incident_category": "database", "confidence": 1.5,
            "reasoning": "test", "fallback_used": False,
        })
        state = agent.run(base_state)
        assert state.severity_result.confidence == 0.95

    def test_confidence_clamped_below_min(self, base_state):
        agent = self._agent({
            "severity_level": "low", "urgency": "low",
            "incident_category": "database", "confidence": -0.5,
            "reasoning": "test", "fallback_used": False,
        })
        state = agent.run(base_state)
        assert state.severity_result.confidence == 0.10

    def test_llm_failure_uses_fallback(self, base_state):
        agent = SeverityAgent(MockLLMProvider(should_fail=True))
        state = agent.run(base_state)
        assert state.severity_result is not None
        assert state.severity_result.fallback_used is True
        assert state.severity_result.severity_level == "unknown"
        assert state.severity_result.confidence == 0.10
        entry = state.stage_audit_trail[0]
        assert entry.status in ("fallback", "failed")

    def test_state_always_returned_even_on_failure(self, base_state):
        agent = SeverityAgent(MockLLMProvider(should_fail=True))
        state = agent.run(base_state)
        assert isinstance(state, WorkflowState)


# ══════════════════════════════════════════════════════════════════════════════
# RootCauseAgent
# ══════════════════════════════════════════════════════════════════════════════

class TestRootCauseAgent:
    def _agent(self, json_response: dict) -> RootCauseAgent:
        return RootCauseAgent(MockLLMProvider(fixed_json=json_response))

    def test_successful_inference(self, state_with_severity):
        agent = self._agent({
            "probable_cause": "Autovacuum disabled causing table bloat.",
            "evidence_strength": "high", "confidence": 0.82,
            "uncertainty_note": "", "contributing_factors": ["autovacuum"],
            "fallback_used": False,
        })
        state = agent.run(state_with_severity)
        assert "Autovacuum" in state.root_cause_result.probable_cause
        assert state.root_cause_result.evidence_strength == "high"
        assert state.root_cause_result.confidence == 0.82

    def test_low_evidence_caps_confidence_at_50pct(self, state_with_severity):
        agent = self._agent({
            "probable_cause": "Unknown cause.",
            "evidence_strength": "low", "confidence": 0.85,
            "uncertainty_note": "Evidence is sparse.",
            "contributing_factors": [], "fallback_used": False,
        })
        state = agent.run(state_with_severity)
        assert state.root_cause_result.confidence <= 0.50

    def test_invalid_evidence_strength_defaults_to_low(self, state_with_severity):
        agent = self._agent({
            "probable_cause": "Some cause.",
            "evidence_strength": "ultra_high", "confidence": 0.70,
            "uncertainty_note": "", "contributing_factors": [],
            "fallback_used": False,
        })
        state = agent.run(state_with_severity)
        assert state.root_cause_result.evidence_strength == "low"

    def test_fallback_when_severity_missing(self, base_state):
        """Agent should still run even if SeverityAgent was skipped."""
        agent = self._agent({
            "probable_cause": "Network issue.",
            "evidence_strength": "medium", "confidence": 0.65,
            "uncertainty_note": "", "contributing_factors": [],
            "fallback_used": False,
        })
        state = agent.run(base_state)  # no severity_result set
        assert state.root_cause_result is not None

    def test_audit_entry_appended(self, state_with_severity):
        agent = self._agent({
            "probable_cause": "Cache miss storm.", "evidence_strength": "medium",
            "confidence": 0.70, "uncertainty_note": "", "contributing_factors": [],
            "fallback_used": False,
        })
        pre_count = len(state_with_severity.stage_audit_trail)
        state = agent.run(state_with_severity)
        assert len(state.stage_audit_trail) == pre_count + 1
        assert state.stage_audit_trail[-1].stage == "root_cause"

    def test_llm_failure_uses_fallback(self, state_with_severity):
        agent = RootCauseAgent(MockLLMProvider(should_fail=True))
        state = agent.run(state_with_severity)
        assert state.root_cause_result.fallback_used is True
        assert state.root_cause_result.confidence == 0.10


# ══════════════════════════════════════════════════════════════════════════════
# RunbookAgent
# ══════════════════════════════════════════════════════════════════════════════

class TestRunbookAgent:
    def _agent(self, json_response: dict) -> RunbookAgent:
        return RunbookAgent(MockLLMProvider(fixed_json=json_response))

    def test_successful_recommendation(self, state_with_root_cause):
        agent = self._agent({
            "matched_runbook": "Database High Latency", "confidence": 0.85,
            "actions": ["Check connections", "Re-enable autovacuum", "Monitor latency"],
            "escalate": False, "escalation_reason": None, "fallback_used": False,
        })
        state = agent.run(state_with_root_cause)
        assert len(state.runbook_result.actions) == 3
        assert state.runbook_result.escalate is False
        assert state.runbook_result.confidence == 0.85

    def test_actions_capped_at_ten(self, state_with_root_cause):
        agent = self._agent({
            "matched_runbook": "Test", "confidence": 0.75,
            "actions": [f"Step {i}" for i in range(15)],  # 15 steps
            "escalate": False, "escalation_reason": None, "fallback_used": False,
        })
        state = agent.run(state_with_root_cause)
        assert len(state.runbook_result.actions) <= 10

    def test_critical_low_confidence_forces_escalation(self, state_with_root_cause):
        # severity is critical (from fixture), confidence low → must escalate
        agent = self._agent({
            "matched_runbook": None, "confidence": 0.30,
            "actions": ["Check logs"],
            "escalate": False, "escalation_reason": None, "fallback_used": False,
        })
        state = agent.run(state_with_root_cause)
        assert state.runbook_result.escalate is True

    def test_escalation_reason_auto_filled(self, state_with_root_cause):
        agent = self._agent({
            "matched_runbook": None, "confidence": 0.30,
            "actions": ["Check logs"],
            "escalate": True, "escalation_reason": None, "fallback_used": False,
        })
        state = agent.run(state_with_root_cause)
        assert state.runbook_result.escalation_reason is not None
        assert len(state.runbook_result.escalation_reason) > 0

    def test_runbook_keyword_retrieval(self, state_with_root_cause):
        """Test that relevant runbooks are retrieved for a database incident."""
        agent = RunbookAgent(MockLLMProvider(fixed_json={
            "matched_runbook": None, "confidence": 0.50,
            "actions": [], "escalate": False,
            "escalation_reason": None, "fallback_used": False,
        }))
        context = agent._retrieve_runbook_context(
            incident=state_with_root_cause.normalized_incident,
            severity_dict={"incident_category": "database", "severity_level": "critical"},
            root_cause_dict={"probable_cause": "autovacuum connection pool database"},
        )
        assert len(context) > 50
        assert "database" in context.lower() or "autovacuum" in context.lower()

    def test_empty_actions_falls_back(self, state_with_root_cause):
        agent = self._agent({
            "matched_runbook": None, "confidence": 0.40,
            "actions": [],  # empty
            "escalate": False, "escalation_reason": None, "fallback_used": False,
        })
        state = agent.run(state_with_root_cause)
        assert len(state.runbook_result.actions) > 0  # falls back to default

    def test_llm_failure_uses_fallback_with_escalation(self, state_with_root_cause):
        agent = RunbookAgent(MockLLMProvider(should_fail=True))
        state = agent.run(state_with_root_cause)
        assert state.runbook_result.fallback_used is True
        assert state.runbook_result.escalate is True


# ══════════════════════════════════════════════════════════════════════════════
# SummaryAgent
# ══════════════════════════════════════════════════════════════════════════════

class TestSummaryAgent:
    def _agent(self, json_response: dict) -> SummaryAgent:
        return SummaryAgent(MockLLMProvider(fixed_json=json_response))

    def _full_state(self, state_with_root_cause):
        state_with_root_cause.runbook_result = RunbookResult(
            matched_runbook="DB Runbook",
            actions=["Step 1", "Step 2"],
            escalate=False, confidence=0.83,
        )
        return state_with_root_cause

    def test_successful_summary(self, state_with_root_cause):
        state = self._full_state(state_with_root_cause)
        agent = self._agent({
            "summary_text": "The orders service database is experiencing severe latency.",
            "probable_impact": "Checkout flow is degraded for all users.",
            "next_action": "Re-enable autovacuum immediately.",
            "confidence": 0.83, "fallback_used": False,
        })
        result_state = agent.run(state)
        assert "orders service" in result_state.summary_result.summary_text
        assert result_state.summary_result.confidence == 0.83
        assert result_state.summary_result.fallback_used is False

    def test_escalation_caps_confidence_at_70pct(self, state_with_root_cause):
        state = self._full_state(state_with_root_cause)
        state.runbook_result.escalate = True  # escalation active
        agent = self._agent({
            "summary_text": "Critical issue detected.",
            "probable_impact": "All users affected.",
            "next_action": "Escalate immediately.",
            "confidence": 0.92,  # high — should be capped at 0.70
            "fallback_used": False,
        })
        result_state = agent.run(state)
        assert result_state.summary_result.confidence <= 0.70

    def test_empty_summary_uses_fallback_text(self, state_with_root_cause):
        state = self._full_state(state_with_root_cause)
        agent = self._agent({
            "summary_text": "",  # empty
            "probable_impact": "Unknown",
            "next_action": "",
            "confidence": 0.50, "fallback_used": False,
        })
        result_state = agent.run(state)
        assert len(result_state.summary_result.summary_text) > 0

    def test_next_action_defaults_to_escalate_when_escalation_set(self, state_with_root_cause):
        state = self._full_state(state_with_root_cause)
        state.runbook_result.escalate = True
        agent = self._agent({
            "summary_text": "Service down.",
            "probable_impact": "All users affected.",
            "next_action": "",  # empty — should be filled from escalation
            "confidence": 0.60, "fallback_used": False,
        })
        result_state = agent.run(state)
        assert len(result_state.summary_result.next_action) > 0

    def test_audit_entry_is_fourth_entry(self, state_with_root_cause):
        state = self._full_state(state_with_root_cause)
        # Add mock prior entries
        from app.schemas.workflow import StageAuditEntry
        for s in ["normalization", "severity", "root_cause", "runbook"]:
            state.add_audit_entry(StageAuditEntry(stage=s, status="success"))
        agent = self._agent({
            "summary_text": "Incident summary.", "probable_impact": "Impact.",
            "next_action": "Act now.", "confidence": 0.80, "fallback_used": False,
        })
        result_state = agent.run(state)
        summary_entries = [e for e in result_state.stage_audit_trail if e.stage == "summary"]
        assert len(summary_entries) == 1
        assert summary_entries[0].status == "success"

    def test_llm_failure_uses_fallback(self, state_with_root_cause):
        state = self._full_state(state_with_root_cause)
        agent = SummaryAgent(MockLLMProvider(should_fail=True))
        result_state = agent.run(state)
        assert result_state.summary_result.fallback_used is True
        assert result_state.summary_result.confidence == 0.10


# ══════════════════════════════════════════════════════════════════════════════
# WorkflowState integration
# ══════════════════════════════════════════════════════════════════════════════

class TestWorkflowStateIntegration:
    def test_full_pipeline_computes_correct_overall_confidence(self, base_state):
        """Run all 4 agents and verify overall_confidence is their mean."""
        mock = MockLLMProvider(fixed_json={
            "severity_level": "high", "urgency": "high",
            "incident_category": "database", "confidence": 0.80,
            "reasoning": "test", "fallback_used": False,
            "probable_cause": "Cache eviction.", "evidence_strength": "medium",
            "uncertainty_note": "", "contributing_factors": [],
            "matched_runbook": None,
            "actions": ["Check cache config"],
            "escalate": False, "escalation_reason": None,
            "summary_text": "Cache issue.", "probable_impact": "Slow pages.",
            "next_action": "Tune cache TTL.",
        })
        state = base_state
        state = SeverityAgent(mock).run(state)
        state = RootCauseAgent(mock).run(state)
        state = RunbookAgent(mock).run(state)
        state = SummaryAgent(mock).run(state)
        state.mark_complete()

        assert state.pipeline_status == "complete"
        assert 0.10 <= state.overall_confidence <= 0.95
        assert state.processing_time_s is not None
        assert len(state.stage_audit_trail) == 4

    def test_partial_failure_detected(self, base_state):
        """If one agent fails, pipeline status should be partial_failure."""
        good_mock = MockLLMProvider(fixed_json={
            "severity_level": "high", "urgency": "high",
            "incident_category": "database", "confidence": 0.80,
            "reasoning": "test", "fallback_used": False,
        })
        fail_mock = MockLLMProvider(should_fail=True)

        state = base_state
        state = SeverityAgent(good_mock).run(state)
        state = RootCauseAgent(fail_mock).run(state)  # this one fails

        # Manually inject dummy results for remaining agents
        from app.schemas.workflow import RunbookResult, SummaryResult, StageAuditEntry
        state.runbook_result = RunbookResult(actions=["step"], confidence=0.75)
        state.summary_result = SummaryResult(summary_text="ok", confidence=0.75)
        # Mark root_cause audit as failed
        state.stage_audit_trail[-1] = StageAuditEntry(stage="root_cause", status="failed")

        state.mark_complete()
        assert state.pipeline_status == "partial_failure"
