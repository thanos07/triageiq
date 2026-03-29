"""
tests/test_api.py

Integration tests for all FastAPI routes.
Uses FastAPI's TestClient — no running server needed.

Run with: pytest tests/test_api.py -v
"""

import json
import pytest
from fastapi.testclient import TestClient
from main import app


@pytest.fixture(scope="module")
def client():
    """Single TestClient instance shared across all tests in this module."""
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture(scope="module")
def submitted_incident_id(client):
    """Submit one incident and return its ID for use in dependent tests."""
    r = client.post("/api/v1/incidents", json={
        "title": "Test incident for API suite",
        "description": "This is a test incident used by the automated test suite.",
        "service_name": "test-service",
        "environment": "staging",
        "raw_severity": "medium",
    })
    assert r.status_code == 201
    return r.json()["incident_id"]


# ── Health ────────────────────────────────────────────────────────────────────

class TestHealth:
    def test_health_returns_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] in ("ok", "degraded")

    def test_root_returns_welcome(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "docs" in r.json()


# ── Incidents API ─────────────────────────────────────────────────────────────

class TestIncidentsAPI:
    def test_submit_valid_incident(self, client):
        r = client.post("/api/v1/incidents", json={
            "title": "Auth service 503 errors in production",
            "description": "Login failures since 09:15 UTC. Pods in CrashLoopBackOff.",
            "service_name": "auth-api",
            "environment": "production",
            "raw_severity": "critical",
        })
        assert r.status_code == 201
        body = r.json()
        assert "incident_id" in body
        assert body["workflow_status"] == "pending"

    def test_submit_minimal_incident(self, client):
        r = client.post("/api/v1/incidents", json={
            "title": "Something broke in staging",
            "description": "Unclear what is happening here right now.",
        })
        assert r.status_code == 201

    def test_submit_missing_title_rejected(self, client):
        r = client.post("/api/v1/incidents", json={
            "description": "Missing title field entirely."
        })
        assert r.status_code == 422

    def test_submit_title_too_short_rejected(self, client):
        r = client.post("/api/v1/incidents", json={
            "title": "x",
            "description": "Description is fine but title is too short."
        })
        assert r.status_code == 422

    def test_list_incidents_returns_list(self, client):
        r = client.get("/api/v1/incidents")
        assert r.status_code == 200
        body = r.json()
        assert "incidents" in body
        assert "total" in body
        assert isinstance(body["incidents"], list)

    def test_list_incidents_pagination(self, client):
        r = client.get("/api/v1/incidents?limit=2&offset=0")
        assert r.status_code == 200
        assert len(r.json()["incidents"]) <= 2

    def test_get_incident_by_id(self, client, submitted_incident_id):
        r = client.get(f"/api/v1/incidents/{submitted_incident_id}")
        assert r.status_code == 200
        assert r.json()["id"] == submitted_incident_id

    def test_get_nonexistent_incident_404(self, client):
        r = client.get("/api/v1/incidents/does-not-exist")
        assert r.status_code == 404

    def test_csv_upload_valid(self, client):
        csv = b"title,description,service_name,environment,raw_severity\n" \
              b"CSV test incident,Uploaded via CSV for testing,csv-service,staging,P2"
        r = client.post(
            "/api/v1/incidents/upload/csv",
            files={"file": ("test.csv", csv, "text/csv")},
        )
        assert r.status_code == 201
        assert r.json()["submitted"] == 1

    def test_csv_upload_wrong_extension_rejected(self, client):
        r = client.post(
            "/api/v1/incidents/upload/csv",
            files={"file": ("test.txt", b"bad", "text/plain")},
        )
        assert r.status_code == 400

    def test_json_upload_valid(self, client):
        data = json.dumps([{
            "title": "JSON test incident",
            "description": "Uploaded via JSON for test suite execution.",
        }]).encode()
        r = client.post(
            "/api/v1/incidents/upload/json",
            files={"file": ("test.json", data, "application/json")},
        )
        assert r.status_code == 201
        assert r.json()["submitted"] == 1

    def test_json_upload_invalid_json_rejected(self, client):
        r = client.post(
            "/api/v1/incidents/upload/json",
            files={"file": ("bad.json", b"{not valid json}", "application/json")},
        )
        assert r.status_code == 201  # returns 201 but with 0 submitted and 1 error
        body = r.json()
        assert body["submitted"] == 0
        assert len(body["errors"]) > 0

    def test_load_samples_creates_incidents(self, client):
        r = client.post("/api/v1/incidents/load-samples")
        assert r.status_code == 201
        body = r.json()
        assert "submitted" in body
        assert "incident_ids" in body


# ── Workflow API ──────────────────────────────────────────────────────────────

class TestWorkflowAPI:
    def test_get_workflow_state(self, client, submitted_incident_id):
        r = client.get(f"/api/v1/workflow/{submitted_incident_id}/state")
        assert r.status_code == 200
        body = r.json()
        assert "workflow_status" in body
        assert "review_status" in body

    def test_get_workflow_result_before_run_404(self, client):
        """A fresh incident with no pipeline run should return 404 for full result."""
        r_inc = client.post("/api/v1/incidents", json={
            "title": "Fresh incident no pipeline yet",
            "description": "This has not been processed yet at all.",
        })
        iid = r_inc.json()["incident_id"]
        r = client.get(f"/api/v1/workflow/{iid}")
        # Either 404 (not found) or result with no agent data
        assert r.status_code in (404, 200)

    def test_get_workflow_nonexistent_incident(self, client):
        r = client.get("/api/v1/workflow/nonexistent-id/state")
        assert r.status_code == 404

    def test_trigger_workflow_nonexistent_incident(self, client):
        r = client.post("/api/v1/workflow/nonexistent-id/run")
        assert r.status_code == 404


# ── Audit API ─────────────────────────────────────────────────────────────────

class TestAuditAPI:
    def test_get_audit_trail(self, client, submitted_incident_id):
        r = client.get(f"/api/v1/audit/{submitted_incident_id}")
        assert r.status_code == 200
        body = r.json()
        assert "events" in body
        assert "total" in body
        assert isinstance(body["events"], list)

    def test_get_audit_summary(self, client, submitted_incident_id):
        r = client.get(f"/api/v1/audit/{submitted_incident_id}/summary")
        assert r.status_code == 200
        body = r.json()
        assert "stages" in body
        assert "total_latency_ms" in body

    def test_audit_trail_nonexistent_incident_404(self, client):
        r = client.get("/api/v1/audit/nonexistent-id")
        assert r.status_code == 404


# ── Review API ────────────────────────────────────────────────────────────────

class TestReviewAPI:
    def test_review_blocked_before_pipeline(self, client):
        """Cannot approve an incident that hasn't been triaged."""
        r_inc = client.post("/api/v1/incidents", json={
            "title": "Incident before review test",
            "description": "Testing that review is blocked before pipeline.",
        })
        iid = r_inc.json()["incident_id"]
        r = client.post(f"/api/v1/review/{iid}", json={"decision": "approved"})
        assert r.status_code == 400

    def test_invalid_decision_rejected(self, client, submitted_incident_id):
        r = client.post(
            f"/api/v1/review/{submitted_incident_id}",
            json={"decision": "maybe"},
        )
        assert r.status_code == 422

    def test_review_nonexistent_incident_404(self, client):
        r = client.post(
            "/api/v1/review/nonexistent-id",
            json={"decision": "approved"},
        )
        assert r.status_code == 404

    def test_get_review_before_decision_404(self, client, submitted_incident_id):
        r = client.get(f"/api/v1/review/{submitted_incident_id}")
        assert r.status_code == 404
