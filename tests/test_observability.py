"""
tests/test_observability.py

Tests for observability and LLMOps utilities.
Run with: pytest tests/test_observability.py -v
"""

import pytest
from app.utils.observability import (
    PipelineMetrics, emit_pipeline_event,
    compute_llmops_summary, _pipeline_health,
)


class TestPipelineMetrics:
    def test_empty_metrics(self):
        m = PipelineMetrics(incident_id="test-001")
        assert m.total_latency_ms == 0
        assert m.total_retries == 0
        assert m.failed_stage_count == 0
        assert m.mean_confidence == 0.0

    def test_record_stages(self):
        m = PipelineMetrics(incident_id="test-001")
        m.record_stage("severity",   "success", latency_ms=800,  confidence=0.85)
        m.record_stage("root_cause", "success", latency_ms=1200, confidence=0.72)
        m.record_stage("runbook",    "success", latency_ms=900,  confidence=0.80)
        m.record_stage("summary",    "success", latency_ms=600,  confidence=0.78)

        assert m.total_latency_ms == 3500
        assert m.failed_stage_count == 0
        assert abs(m.mean_confidence - (0.85 + 0.72 + 0.80 + 0.78) / 4) < 0.0001

    def test_failed_stages_counted(self):
        m = PipelineMetrics(incident_id="test-002")
        m.record_stage("severity",   "success",  latency_ms=800, confidence=0.85)
        m.record_stage("root_cause", "failed",   latency_ms=100, confidence=0.0)
        m.record_stage("runbook",    "fallback",  latency_ms=50,  confidence=0.10)
        assert m.failed_stage_count == 2

    def test_fallback_stage_names_collected(self):
        m = PipelineMetrics(incident_id="test-003")
        m.record_stage("severity",   "success",  latency_ms=800, confidence=0.85, used_fallback=False)
        m.record_stage("root_cause", "fallback", latency_ms=50,  confidence=0.10, used_fallback=True)
        assert "root_cause" in m.fallback_stage_names
        assert "severity" not in m.fallback_stage_names

    def test_to_dict_has_all_keys(self):
        m = PipelineMetrics(incident_id="test-004", llm_model="claude-3-5-haiku")
        m.record_stage("severity", "success", latency_ms=500, confidence=0.80)
        d = m.to_dict()
        for key in ["incident_id", "llm_model", "total_latency_ms", "stage_count",
                    "failed_stage_count", "mean_confidence", "stages"]:
            assert key in d

    def test_retry_count_accumulated(self):
        m = PipelineMetrics(incident_id="test-005")
        m.record_stage("severity",   "success", latency_ms=800,  confidence=0.85, retry_count=0)
        m.record_stage("root_cause", "success", latency_ms=2400, confidence=0.75, retry_count=2)
        assert m.total_retries == 2


class TestEmitPipelineEvent:
    def test_event_has_required_fields(self):
        event = emit_pipeline_event(
            incident_id="test-001",
            pipeline_status="complete",
            overall_confidence=0.82,
            processing_time_s=4.5,
            stage_count=5,
            failed_stage_count=0,
            total_retries=1,
            low_confidence_flag=False,
            escalate=False,
            llm_model="claude-3-5-haiku",
        )
        required = [
            "event_type", "incident_id", "pipeline_status",
            "overall_confidence", "processing_time_s",
            "stage_count", "failed_stage_count", "pipeline_health",
        ]
        for key in required:
            assert key in event

    def test_event_type_is_pipeline_complete(self):
        event = emit_pipeline_event(
            incident_id="x", pipeline_status="complete",
            overall_confidence=0.8, processing_time_s=3.0,
            stage_count=4, failed_stage_count=0, total_retries=0,
            low_confidence_flag=False, escalate=False, llm_model="test",
        )
        assert event["event_type"] == "pipeline_complete"

    def test_pipeline_health_derived_correctly(self):
        event = emit_pipeline_event(
            incident_id="x", pipeline_status="partial_failure",
            overall_confidence=0.4, processing_time_s=3.0,
            stage_count=4, failed_stage_count=1, total_retries=0,
            low_confidence_flag=True, escalate=True, llm_model="test",
        )
        assert event["pipeline_health"] in ("partial", "degraded")


class TestPipelineHealth:
    def test_healthy_run(self):
        assert _pipeline_health("complete", 0, False) == "healthy"

    def test_low_confidence_is_partial(self):
        assert _pipeline_health("complete", 0, True) == "partial"

    def test_one_failed_stage_is_partial(self):
        assert _pipeline_health("partial_failure", 1, False) == "partial"

    def test_all_failed_is_degraded(self):
        assert _pipeline_health("failed", 4, True) == "degraded"

    def test_three_or_more_failures_degraded(self):
        assert _pipeline_health("partial_failure", 3, False) == "degraded"


class TestComputeLLMOpsSummary:
    def test_empty_input(self):
        result = compute_llmops_summary([])
        assert result["total_events"] == 0

    def test_basic_summary(self):
        events = [
            {"status": "success", "latency_ms": 800,  "confidence": 0.85, "retry_count": 0},
            {"status": "success", "latency_ms": 1200, "confidence": 0.72, "retry_count": 1},
            {"status": "fallback","latency_ms": 50,   "confidence": 0.10, "retry_count": 0},
            {"status": "failed",  "latency_ms": 100,  "confidence": 0.10, "retry_count": 2},
        ]
        result = compute_llmops_summary(events)
        assert result["total_events"] == 4
        assert result["success_count"] == 2
        assert result["failure_count"] == 2
        assert result["fallback_rate"] == 0.5
        assert result["total_retries"] == 3
        assert result["stages_with_retries"] == 2

    def test_mean_latency_computed(self):
        events = [
            {"status": "success", "latency_ms": 1000, "confidence": 0.80, "retry_count": 0},
            {"status": "success", "latency_ms": 2000, "confidence": 0.80, "retry_count": 0},
        ]
        result = compute_llmops_summary(events)
        assert result["mean_latency_ms"] == 1500

    def test_p95_latency_computed(self):
        events = [
            {"status": "success", "latency_ms": i * 100, "confidence": 0.8, "retry_count": 0}
            for i in range(1, 11)
        ]
        result = compute_llmops_summary(events)
        assert result["p95_latency_ms"] > 0
