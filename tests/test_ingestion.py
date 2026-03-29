"""
tests/test_ingestion.py

Unit tests for ingestion and normalization services.
Run with: pytest tests/test_ingestion.py -v
"""

import json
import pytest
from app.schemas.incident import IncidentInput
from app.services.normalizer import (
    normalize_incident, normalize_from_csv_row, normalize_from_dict,
    _normalize_severity, _normalize_environment,
)
from app.schemas.incident import CSVIncidentRow
from app.services.ingestion import (
    parse_csv_upload, parse_json_upload, load_sample_incidents,
)


# ── Severity normalization ────────────────────────────────────────────────────

class TestSeverityNormalization:
    @pytest.mark.parametrize("raw,expected", [
        ("P1", "critical"), ("p1", "critical"),
        ("P2", "high"), ("sev-1", "critical"),
        ("SEV2", "high"), ("critical", "critical"),
        ("high", "high"), ("medium", "medium"),
        ("low", "low"), ("minor", "low"),
        ("", "unknown"), (None, "unknown"),
        ("unknown", "unknown"), ("anything_else", "unknown"),
    ])
    def test_severity_mapping(self, raw, expected):
        assert _normalize_severity(raw) == expected


# ── Environment normalization ─────────────────────────────────────────────────

class TestEnvironmentNormalization:
    @pytest.mark.parametrize("raw,expected", [
        ("prod", "production"), ("production", "production"),
        ("stage", "staging"), ("staging", "staging"),
        ("dev", "development"), ("development", "development"),
        ("qa", "staging"), (None, "unknown"), ("", "unknown"),
    ])
    def test_environment_mapping(self, raw, expected):
        assert _normalize_environment(raw) == expected


# ── normalize_incident ────────────────────────────────────────────────────────

class TestNormalizeIncident:
    def test_basic_normalization(self):
        inp = IncidentInput(
            title="DB latency spike in production",
            description="P99 query latency exceeded 8 seconds.",
            service_name="orders-service",
            environment="prod",
            raw_severity="P1",
        )
        result = normalize_incident(inp)
        assert result.title == "DB latency spike in production"
        assert result.environment == "production"
        assert result.service_name == "orders-service"
        assert result.raw_severity == "P1"
        assert result.incident_id is not None
        assert result.workflow_status == "pending"

    def test_missing_optional_fields_use_defaults(self):
        inp = IncidentInput(
            title="Something broke",
            description="We are not sure what happened here.",
        )
        result = normalize_incident(inp)
        assert result.service_name == "unknown"
        assert result.environment == "unknown"
        assert result.raw_severity == "unknown"
        assert result.source == "manual"

    def test_custom_incident_id_preserved(self):
        inp = IncidentInput(title="Test incident title", description="Test description here in detail.")
        result = normalize_incident(inp, incident_id="my-custom-uuid")
        assert result.incident_id == "my-custom-uuid"

    def test_whitespace_stripped_from_title(self):
        inp = IncidentInput(title="  Spaces around title  ", description="Some description text here.")
        result = normalize_incident(inp)
        assert result.title == "Spaces around title"

    def test_environment_normalized_at_input_level(self):
        inp = IncidentInput(title="Test incident env", description="Testing environment normalization.", environment="PROD")
        result = normalize_incident(inp)
        assert result.environment == "production"


# ── CSV parsing ───────────────────────────────────────────────────────────────

class TestCSVParsing:
    def test_valid_csv_parsed_correctly(self):
        csv = b"title,description,service_name,environment,raw_severity\n" \
              b"DB spike,P99 > 8s latency in production,orders-service,production,P1"
        incidents, errors = parse_csv_upload(csv)
        assert len(incidents) == 1
        assert len(errors) == 0
        assert incidents[0].title == "DB spike"
        assert incidents[0].service_name == "orders-service"

    def test_multiple_rows(self):
        csv = (
            b"title,description\n"
            b"Incident A,First incident description here\n"
            b"Incident B,Second incident description here\n"
            b"Incident C,Third incident description here\n"
        )
        incidents, errors = parse_csv_upload(csv)
        assert len(incidents) == 3
        assert len(errors) == 0

    def test_missing_required_column_returns_error(self):
        csv = b"description,service_name\nSome desc,my-service"
        incidents, errors = parse_csv_upload(csv)
        assert len(incidents) == 0
        assert len(errors) == 1
        assert "title" in errors[0]

    def test_empty_optional_fields_handled(self):
        csv = b"title,description,service_name\nTest incident,Test description,"
        incidents, errors = parse_csv_upload(csv)
        assert len(incidents) == 1
        assert len(errors) == 0

    def test_invalid_bytes_returns_error(self):
        incidents, errors = parse_csv_upload(b"\xff\xfe invalid csv bytes")
        # Should either parse with error or return an error — not raise
        assert isinstance(incidents, list)
        assert isinstance(errors, list)

    def test_extra_columns_silently_ignored(self):
        csv = b"title,description,unknown_column,another_unknown\nTest incident title,Full description here,foo,bar"
        incidents, errors = parse_csv_upload(csv)
        assert len(incidents) == 1
        assert len(errors) == 0


# ── JSON parsing ──────────────────────────────────────────────────────────────

class TestJSONParsing:
    def test_valid_json_array(self):
        data = json.dumps([
            {"title": "Auth failure", "description": "503s on login endpoint"},
            {"title": "Memory spike", "description": "OOMKilled pods in k8s cluster"},
        ]).encode()
        incidents, errors = parse_json_upload(data)
        assert len(incidents) == 2
        assert len(errors) == 0

    def test_single_json_object(self):
        data = json.dumps(
            {"title": "Single incident", "description": "One incident only here."}
        ).encode()
        incidents, errors = parse_json_upload(data)
        assert len(incidents) == 1
        assert len(errors) == 0

    def test_invalid_json_returns_error(self):
        incidents, errors = parse_json_upload(b"{this is not json}")
        assert len(incidents) == 0
        assert len(errors) == 1

    def test_missing_title_field_causes_row_error(self):
        data = json.dumps([{"description": "No title here at all"}]).encode()
        incidents, errors = parse_json_upload(data)
        assert len(incidents) == 0
        assert len(errors) == 1

    def test_partial_success_on_mixed_data(self):
        data = json.dumps([
            {"title": "Good incident", "description": "Valid description here"},
            {"description": "No title — should fail"},  # missing title
        ]).encode()
        incidents, errors = parse_json_upload(data)
        assert len(incidents) == 1
        assert len(errors) == 1

    def test_empty_array_returns_empty(self):
        data = json.dumps([]).encode()
        incidents, errors = parse_json_upload(data)
        assert len(incidents) == 0
        assert len(errors) == 0


# ── Sample incidents ──────────────────────────────────────────────────────────

class TestSampleIncidents:
    def test_sample_incidents_load(self):
        incidents = load_sample_incidents()
        assert len(incidents) == 10

    def test_all_sample_incidents_have_required_fields(self):
        incidents = load_sample_incidents()
        for inc in incidents:
            assert inc.title
            assert inc.description
            assert inc.incident_id
            assert inc.workflow_status == "pending"

    def test_sample_incident_environments_normalized(self):
        incidents = load_sample_incidents()
        valid_envs = {"production", "staging", "development", "unknown"}
        for inc in incidents:
            assert inc.environment in valid_envs, f"Bad env: {inc.environment}"
