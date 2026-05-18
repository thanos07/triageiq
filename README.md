# TriageIQ

> A portfolio-grade AI Operations / SRE incident triage copilot that classifies incident severity, infers probable root cause, recommends runbook actions, generates stakeholder summaries, and maintains an audit trail with human-in-the-loop review.

**Live demo:** https://triageiq.streamlit.app/

---

## 1. Project Overview

**TriageIQ** is an AI-powered incident triage assistant built to demonstrate enterprise-style **Agentic AI**, **LLMOps**, and **SRE workflow automation**.

When an incident is submitted, the system runs a structured multi-agent workflow:

```
Incident submitted
      ↓
Severity Agent
      ↓
Root Cause Agent
      ↓
Runbook Agent
      ↓
Summary Agent
      ↓
Audit Trail + Human Review
```

The project ships in **two deployment modes** from the same codebase:

- **Single-process mode** — Streamlit imports the agents/orchestrator/DB modules directly as a Python library. No HTTP layer. This is what's deployed at triageiq.streamlit.app and is the simplest way to run the app.
- **Dual-process mode** — FastAPI serves a REST API on port 8000; Streamlit talks to it over HTTP. Useful for backend development, API testing, or fronting the same backend with multiple clients.

Both modes share the exact same agent pipeline, schemas, database, and audit logic.

---

## 2. What This Project Does

TriageIQ helps answer four important incident-management questions:

1. **How severe is the incident?**
2. **What is the probable root cause?**
3. **What runbook steps should be followed?**
4. **What summary should be shared with stakeholders?**

It also records audit metadata such as:

- agent stage
- model used
- confidence score
- latency
- retry count
- fallback status
- human approval/rejection

---

## 3. Key Features

- **Manual incident submission**
- **CSV and JSON incident upload**
- **Four-agent triage pipeline**
  - Severity classification
  - Root-cause inference
  - Runbook recommendation
  - Stakeholder summary generation
- **Groq LLM support for free/fast inference**
- **Anthropic Claude support as optional fallback**
- **SQLite persistence**
- **Audit trail for every workflow stage**
- **Human-in-the-loop review**
- **Streamlit dashboard**
- **FastAPI backend** (optional — runs the same business logic over HTTP)
- **Single-process or dual-process deployment** from one codebase
- **Docker and Docker Compose support**
- **Pytest-based test suite**

---

## 4. Tech Stack

| Layer            | Technology                                |
| ---------------- | ----------------------------------------- |
| Frontend         | Streamlit                                 |
| Backend API      | FastAPI + Uvicorn (optional)              |
| Database         | SQLite + SQLAlchemy                       |
| LLM Provider     | Groq Llama / Anthropic Claude             |
| Configuration    | Pydantic Settings + `.env` / Streamlit Secrets |
| Retry Logic      | Tenacity                                  |
| Testing          | Pytest                                    |
| Packaging        | Docker + Docker Compose                   |
| Deployment       | Streamlit Community Cloud / Docker        |

---

## 5. System Architecture

### Single-process mode (default — deployed on Streamlit Cloud)

```
                 ┌──────────────────────┐
                 │   Streamlit UI        │
                 │  localhost:8501       │
                 └──────────┬───────────┘
                            │
                            │  (direct Python calls)
                            ▼
                 ┌──────────────────────┐
                 │ Incident Orchestrator │
                 └──────────┬───────────┘
                            │
      ┌─────────────────────┼─────────────────────┐
      ▼                     ▼                     ▼
Severity Agent      Root Cause Agent       Runbook Agent
      │                     │                     │
      └─────────────────────┼─────────────────────┘
                            ▼
                    Summary Agent
                            │
                            ▼
                 ┌──────────────────────┐
                 │ SQLite + Audit Trail  │
                 └──────────────────────┘
```

In this mode the Streamlit app imports `app.orchestration.pipeline`, `app.db.crud`, and `app.services.ingestion` directly. Pipeline execution happens in a background thread; the UI polls the DB for status. No FastAPI process is needed.

### Dual-process mode (optional — for backend development)

```
                 ┌──────────────────────┐
                 │   Streamlit UI        │
                 │  localhost:8501       │
                 └──────────┬───────────┘
                            │  HTTP
                            ▼
                 ┌──────────────────────┐
                 │   FastAPI Backend     │
                 │  localhost:8000       │
                 └──────────┬───────────┘
                            │
                            ▼
                 ┌──────────────────────┐
                 │ Incident Orchestrator │
                 └──────────┬───────────┘
                            │
                            ▼  (same agents, same DB)
                       ... [as above]
```

Useful when you want to develop the API independently, integrate with another client, or demonstrate the REST contract.

---

## 6. Repository Structure

```
triageiq/
├── .python-version            ← pins Python 3.11 for Streamlit Cloud
├── app/
│   ├── agents/
│   │   ├── severity_agent.py
│   │   ├── root_cause_agent.py
│   │   ├── runbook_agent.py
│   │   └── summary_agent.py
│   ├── api/                   ← FastAPI routers (only used in dual-process mode)
│   │   ├── incidents.py
│   │   ├── workflow.py
│   │   ├── audit.py
│   │   └── review.py
│   ├── data/
│   │   ├── runbooks.json
│   │   └── sample_incidents.json
│   ├── db/
│   │   ├── database.py        ← SessionLocal, init_db, get_db
│   │   ├── crud.py            ← all DB read/write operations
│   │   └── models.py
│   ├── llm/
│   │   ├── base.py
│   │   ├── anthropic_provider.py
│   │   └── groq_provider.py
│   ├── orchestration/
│   │   └── pipeline.py        ← IncidentPipeline (the core orchestrator)
│   ├── schemas/
│   ├── services/
│   │   ├── ingestion.py       ← CSV/JSON parsing
│   │   └── normalizer.py
│   └── utils/
├── tests/
├── main.py                    ← FastAPI entrypoint (dual-process mode only)
├── streamlit_app.py           ← Streamlit UI + in-process router (single-process mode)
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## 7. How the Agent Pipeline Works

### 7.1 Severity Agent

Classifies the incident into a severity level such as:

```
Critical / High / Medium / Low
```

It considers the incident title, description, affected service, environment, and business impact.

### 7.2 Root Cause Agent

Infers the most probable root cause category.

Example outputs:

```
Database issue
Deployment regression
Network latency
Third-party dependency failure
Authentication failure
Resource saturation
```

### 7.3 Runbook Agent

Recommends operational actions using available runbook data.

Example:

```
1. Check service health dashboard
2. Inspect recent deployments
3. Validate database latency
4. Review logs and traces
5. Escalate to service owner if unresolved
```

### 7.4 Summary Agent

Generates a concise stakeholder-friendly incident summary.

Example:

```
The payment service is experiencing elevated latency in production.
Engineering is investigating a possible database or downstream dependency issue.
Current recommendation is to validate recent deployments, inspect database metrics,
and monitor customer impact.
```

---

## 8. LLM Provider Logic

The project supports two LLM providers:

### Groq

Recommended for portfolio demos because it provides fast inference and has a free tier.

Required variable:

```
GROQ_API_KEY=your-groq-api-key
```

Default model:

```
GROQ_MODEL=llama-3.1-8b-instant
```

### Anthropic Claude

Optional fallback provider.

Required variable:

```
ANTHROPIC_API_KEY=your-anthropic-api-key
```

Default model:

```
LLM_MODEL=claude-3-5-haiku-20241022
```

### Provider Selection

The app uses Groq if `GROQ_API_KEY` is set. If Groq is not configured, it falls back to Anthropic.

---

## 9. Local Setup

### 9.1 Clone the Repository

```bash
git clone https://github.com/thanos07/triageiq.git
cd triageiq
```

### 9.2 Create Virtual Environment

```bash
python3.11 -m venv venv
source venv/bin/activate
```

For Windows PowerShell:

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
```

### 9.3 Install Dependencies

```bash
pip install -r requirements.txt
```

### 9.4 Configure Environment Variables

Copy the example environment file:

```bash
cp .env.example .env
```

Update `.env` with your API key:

```
APP_NAME=TriageIQ
APP_ENV=development
LOG_LEVEL=INFO

DATABASE_URL=sqlite:///./data/triage.db

GROQ_API_KEY=your-groq-api-key-here
GROQ_MODEL=llama-3.1-8b-instant

ANTHROPIC_API_KEY=
LLM_MODEL=claude-3-5-haiku-20241022

LLM_MAX_TOKENS=1024
LLM_TIMEOUT_SECONDS=30
LLM_MAX_RETRIES=3

LOW_CONFIDENCE_THRESHOLD=0.50
MIN_CONFIDENCE_SCORE=0.10
MAX_CONFIDENCE_SCORE=0.95
```

**Never commit `.env`.** Only commit `.env.example`.

---

## 10. Run the Application

### Option A — Single-process mode (simplest)

This is how the app runs on Streamlit Cloud. Only Streamlit runs; the agents and database are imported directly.

```bash
streamlit run streamlit_app.py
```

Open http://localhost:8501. The sidebar should show `🟢 DB · 🟢 LLM (Groq)`. No FastAPI needed.

### Option B — Dual-process mode (with FastAPI)

Use this when you want to develop or test the REST API in isolation.

**Terminal 1 — FastAPI backend:**

```bash
uvicorn main:app --reload --port 8000
```

- Backend: http://localhost:8000
- Swagger docs: http://localhost:8000/docs
- Health check: http://localhost:8000/health

**Terminal 2 — Streamlit frontend:**

The current `streamlit_app.py` defaults to single-process mode. To make it talk to the FastAPI backend instead, you can either run a previous version of the frontend or modify the in-process `api()` router to forward calls over HTTP. The FastAPI endpoints themselves are documented in section 11 below.

---

## 11. Deploy to Streamlit Community Cloud

This is the deployment used at https://triageiq.streamlit.app/.

### 11.1 One-time setup

1. Push your repo to GitHub (already done if you're reading this).
2. Go to https://share.streamlit.io/ and sign in with GitHub.
3. Click **New app** → pick `thanos07/triageiq` → main file: `streamlit_app.py` → **Deploy**.

### 11.2 Configure secrets

Streamlit Cloud doesn't read `.env` files. Add your API key in the deployed app:

**Manage app → Settings → Secrets**, then paste (TOML format):

```toml
GROQ_API_KEY = "gsk_your_key_here"
# Optional fallback:
# ANTHROPIC_API_KEY = "sk-ant-your_key_here"
```

Pydantic-settings reads these as environment variables automatically — no code change needed.

### 11.3 Python version

The included `.python-version` file pins Python 3.11. Streamlit Cloud's default (Python 3.13) doesn't have wheels for some of the pinned dependency versions, so this file is required.

### 11.4 What to expect after deploy

- First load: build takes ~2-3 minutes (pip install).
- Sidebar should show `🟢 DB · 🟢 LLM (Groq)`.
- Click **Load sample data** in the sidebar to populate 10 example incidents.
- SQLite is **ephemeral** on Streamlit Cloud — the DB resets every time the app restarts or redeploys. For demo purposes this is fine; "Load sample data" reseeds in one click.

---

## 12. API Endpoints (Dual-process mode only)

When running with `uvicorn main:app`, the following REST endpoints are available. In single-process mode these same operations are dispatched in-process by the `api()` helper inside `streamlit_app.py`.

### Incident APIs

```
POST /api/v1/incidents
GET  /api/v1/incidents
GET  /api/v1/incidents/{incident_id}
POST /api/v1/incidents/upload/csv
POST /api/v1/incidents/upload/json
POST /api/v1/incidents/load-samples
```

### Workflow APIs

```
POST /api/v1/workflow/{incident_id}/run
GET  /api/v1/workflow/{incident_id}
GET  /api/v1/workflow/{incident_id}/state
```

### Audit APIs

```
GET /api/v1/audit/{incident_id}
GET /api/v1/audit/{incident_id}/summary
```

### Review APIs

```
POST /api/v1/review/{incident_id}
GET  /api/v1/review/{incident_id}
```

---

## 13. Example Incident Payload

```json
{
  "title": "Payment service latency spike",
  "description": "Users are unable to complete checkout. Payment requests are timing out in production.",
  "service_name": "payment-service",
  "environment": "production",
  "raw_severity": "P1"
}
```

---

## 14. Example Workflow Output

```json
{
  "incident_id": "abc-123",
  "severity_output": {
    "severity_level": "critical",
    "confidence": 0.87,
    "reasoning": "Production checkout is impacted and users cannot complete payment."
  },
  "root_cause_output": {
    "probable_cause": "database_or_downstream_dependency",
    "confidence": 0.72,
    "evidence_strength": "medium"
  },
  "runbook_output": {
    "actions": [
      "Check payment-service logs",
      "Inspect database latency",
      "Validate recent deployments",
      "Check downstream payment gateway status",
      "Escalate to payment-service owner"
    ]
  },
  "summary_output": {
    "summary_text": "The payment service is degraded in production, affecting checkout completion. Engineering is investigating database and downstream dependency latency."
  },
  "overall_confidence": 0.76,
  "low_confidence_flag": false,
  "review_status": "awaiting_human_review"
}
```

---

## 15. Run with Docker

### 15.1 Prepare Environment

```bash
cp .env.example .env
```

Add your Groq or Anthropic key in `.env`.

### 15.2 Start Services

```bash
docker compose up --build
```

- FastAPI: http://localhost:8000
- Streamlit: http://localhost:8501

The Docker setup runs the dual-process mode (FastAPI + Streamlit in separate containers) which is closer to a production architecture.

---

## 16. Run Tests

```bash
pytest tests/ -v
```

The test suite is designed to run without making real LLM API calls by using mock/fallback behavior.

---

## 17. Why Plain Python Instead of LangGraph?

This project uses a fixed linear workflow:

```
Severity → Root Cause → Runbook → Summary
```

Because the flow is predictable and sequential, plain Python orchestration is simpler, easier to debug, and easier to explain.

LangGraph would be more useful if the workflow required:

- dynamic branching
- loops
- multi-agent negotiation
- tool-calling graphs
- complex state transitions

For this MVP, plain Python keeps the design lightweight and clear.

---

## 18. Design Decisions

### 18.1 Single-process / Dual-process duality

The same codebase deploys in two modes. The agents, orchestrator, DB, and schemas have zero coupling to HTTP — they only depend on a SQLAlchemy session. FastAPI routes and the Streamlit in-process router are both **thin adapters** over the same service layer:

```
                   Streamlit UI
                   /          \
        in-process              HTTP
        router                  client
            ↓                       ↓
            └────── api() ──────────┘    (same call shape)
                       ↓
                  service layer
            (crud.py, pipeline.py, ingestion.py)
                       ↓
                  SQLAlchemy / SQLite
```

This means the Streamlit app can be deployed standalone (no FastAPI process), but the FastAPI backend remains useful for API testing, integration, or future multi-client setups — without code duplication.

### 18.2 LLM Abstraction

Agents do not directly depend on a specific LLM SDK. The project uses a shared LLM provider interface so the backend can switch between Groq, Anthropic, or future providers.

### 18.3 Confidence Bounding

Confidence values are bounded to avoid unrealistic scores:

```
Minimum confidence: 0.10
Maximum confidence: 0.95
Low-confidence threshold: 0.50
```

### 18.4 Fallback Safety

Each agent is designed to return safe fallback output if the LLM fails. This prevents one failed model call from crashing the entire workflow.

### 18.5 Human Review

The AI does not directly execute production actions. It recommends actions, and a human can approve or reject the result.

### 18.6 Auditability

Every important workflow step is logged so the system can explain:

```
What happened?
Which stage ran?
What did the model return?
How confident was it?
Did fallback happen?
Did a human approve it?
```

---

## 19. What This Project Demonstrates

This project demonstrates practical knowledge of:

- Agentic AI workflow design
- Service-layer architecture that supports multiple deployment modes
- FastAPI backend development
- Streamlit dashboard development
- LLM provider abstraction
- Prompted JSON generation
- Retry and fallback handling
- Audit logging
- Human-in-the-loop governance
- SQLite persistence
- Dockerized deployment
- Streamlit Community Cloud deployment
- Pytest-based validation
- Enterprise AI/SRE thinking

---

## 20. Limitations

This is an MVP and has intentional limitations:

- **SQLite is not ideal for high-concurrency production use** and is **ephemeral on Streamlit Community Cloud** (filesystem resets on every restart). The "Load sample data" button reseeds in one click.
- No authentication or authorization is implemented.
- No background queue such as Celery or Redis Queue.
- Runbook retrieval is lightweight and not a full vector database RAG system.
- LLM outputs are recommendations, not guaranteed root-cause truth.
- Human review is required before taking operational action.
- No integration with PagerDuty, Jira, Slack, Datadog, Grafana, or Kubernetes yet.

---

## 21. Possible Future Improvements

- Add authentication and role-based access control.
- Swap SQLite for managed Postgres (Neon, Supabase, or RDS) to make Streamlit Cloud data persistent — code already supports this via `DATABASE_URL`.
- Add vector-database-based runbook retrieval.
- Integrate with PagerDuty, Jira, Slack, or ServiceNow.
- Add Kubernetes / Grafana / Datadog incident-context ingestion.
- Add Celery or Redis Queue for async workflow execution.
- Add OpenTelemetry traces.
- Add model evaluation metrics for triage accuracy.
- Add feedback loop from human review to improve prompts and rules.

---

## 22. Portfolio / Resume Description

> Built TriageIQ, an AI-powered enterprise incident triage copilot using FastAPI, Streamlit, SQLite, and LLM agents. The system ingests incidents through manual entry, CSV, or JSON upload and runs a four-stage triage pipeline for severity classification, root-cause inference, runbook recommendation, and stakeholder summarization. It includes confidence scoring, audit logging, retry/fallback behavior, and human-in-the-loop review for safe AI-assisted operations. Designed with a clean service-layer architecture that deploys in two modes — Streamlit-only (in-process) on Streamlit Community Cloud, or FastAPI + Streamlit (dual-process) via Docker — from the same codebase.

---

## 23. Interview Explanation

TriageIQ is an AI incident triage assistant. When an incident occurs, the user submits incident details through the UI or API. The backend runs a four-agent pipeline: the first agent classifies severity, the second identifies a probable root cause, the third recommends runbook steps, and the fourth creates a stakeholder summary. The system stores all results in SQLite, maintains an audit trail, and allows a human reviewer to approve or reject the AI-generated recommendation.

One of the design choices I'm most happy with is the clean separation between the service layer (agents, orchestrator, DB) and the transport layer (HTTP or in-process). That means the same business logic deploys two ways: as a standalone Streamlit app on Streamlit Community Cloud, or as a FastAPI backend with a separate Streamlit client. The agents never know which mode they're running in.

---

## 24. Important Security Note

Never commit secrets.

Do not push:

```
.env
data/triage.db
data/test_triage.db
__pycache__/
.pytest_cache/
.streamlit/secrets.toml
```

Only push:

```
.env.example
```

---

## 25. License

MIT License

---

## 26. Disclaimer

This project is built for learning, portfolio demonstration, and experimentation with AI-assisted incident management.

It is not production-ready and should not be used as the sole decision-maker for real operational incidents.
