# TriageIQ 

> A portfolio-grade AI Operations / SRE incident triage copilot that classifies incident severity, infers probable root cause, recommends runbook actions, generates stakeholder summaries, and maintains an audit trail with human-in-the-loop review.

---

## 1. Project Overview

**TriageIQ** is an AI-powered incident triage assistant built to demonstrate enterprise-style **Agentic AI**, **LLMOps**, and **SRE workflow automation**.

When an incident is submitted, the system runs a structured multi-agent workflow:

```text
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

The project is designed as an **MVP portfolio project**, not a production incident-management platform.

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
- **FastAPI backend**
- **Docker and Docker Compose support**
- **Pytest-based test suite**

---

## 4. Tech Stack

| Layer | Technology |
|---|---|
| Backend API | FastAPI + Uvicorn |
| Frontend | Streamlit |
| Database | SQLite + SQLAlchemy |
| LLM Provider | Groq Llama / Anthropic Claude |
| Configuration | Pydantic Settings + `.env` |
| Retry Logic | Tenacity |
| Testing | Pytest |
| Packaging | Docker + Docker Compose |

---

## 5. System Architecture

```text
                 ┌──────────────────────┐
                 │   Streamlit UI        │
                 │  localhost:8501       │
                 └──────────┬───────────┘
                            │
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

---

## 6. Repository Structure

```text
triageiq/
├── app/
│   ├── agents/
│   │   ├── severity_agent.py
│   │   ├── root_cause_agent.py
│   │   ├── runbook_agent.py
│   │   └── summary_agent.py
│   ├── api/
│   │   ├── incidents.py
│   │   ├── workflow.py
│   │   ├── audit.py
│   │   └── review.py
│   ├── data/
│   │   ├── runbooks.json
│   │   └── sample_incidents.json
│   ├── db/
│   ├── llm/
│   │   ├── base.py
│   │   ├── anthropic_provider.py
│   │   └── groq_provider.py
│   ├── orchestration/
│   │   └── pipeline.py
│   ├── schemas/
│   ├── services/
│   └── utils/
├── tests/
├── main.py
├── streamlit_app.py
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

```text
Critical / High / Medium / Low
```

It considers the incident title, description, affected service, environment, and business impact.

---

### 7.2 Root Cause Agent

Infers the most probable root cause category.

Example outputs:

```text
Database issue
Deployment regression
Network latency
Third-party dependency failure
Authentication failure
Resource saturation
```

---

### 7.3 Runbook Agent

Recommends operational actions using available runbook data.

Example:

```text
1. Check service health dashboard
2. Inspect recent deployments
3. Validate database latency
4. Review logs and traces
5. Escalate to service owner if unresolved
```

---

### 7.4 Summary Agent

Generates a concise stakeholder-friendly incident summary.

Example:

```text
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

```env
GROQ_API_KEY=your-groq-api-key
```

Default model:

```env
GROQ_MODEL=llama-3.1-8b-instant
```

### Anthropic Claude

Optional fallback provider.

Required variable:

```env
ANTHROPIC_API_KEY=your-anthropic-api-key
```

Default model:

```env
LLM_MODEL=claude-3-5-haiku-20241022
```

### Provider Selection

The app uses Groq if `GROQ_API_KEY` is set.

If Groq is not configured, it falls back to Anthropic.

---

## 9. Local Setup

### 9.1 Clone the Repository

```bash
git clone https://github.com/thanos07/triageiq.git
cd triageiq
```

---

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

---

### 9.3 Install Dependencies

```bash
pip install -r requirements.txt
```

If you are using Groq and your current `requirements.txt` does not include Groq, add this line:

```text
groq>=0.9.0
```

Then reinstall:

```bash
pip install -r requirements.txt
```

---

### 9.4 Configure Environment Variables

Copy the example environment file:

```bash
cp .env.example .env
```

For Groq-based setup, update `.env` like this:

```env
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

Important:

```text
Do not commit .env to GitHub.
Only commit .env.example.
```

---

## 10. Run the Application

### Terminal 1 — Start FastAPI Backend

```bash
uvicorn main:app --reload --port 8000
```

Backend will run at:

```text
http://localhost:8000
```

API documentation:

```text
http://localhost:8000/docs
```

Health check:

```text
http://localhost:8000/health
```

---

### Terminal 2 — Start Streamlit Frontend

```bash
streamlit run streamlit_app.py
```

Frontend will run at:

```text
http://localhost:8501
```

---

## 11. API Endpoints

### Incident APIs

```text
POST /api/v1/incidents
GET  /api/v1/incidents
GET  /api/v1/incidents/{incident_id}
POST /api/v1/incidents/upload/csv
POST /api/v1/incidents/upload/json
POST /api/v1/incidents/load-samples
```

### Workflow APIs

```text
POST /api/v1/workflow/{incident_id}/run
GET  /api/v1/workflow/{incident_id}
```

### Audit APIs

```text
GET /api/v1/audit/{incident_id}
```

### Review APIs

```text
POST /api/v1/review/{incident_id}
```

---

## 12. Example Incident Payload

```json
{
  "title": "Payment service latency spike",
  "description": "Users are unable to complete checkout. Payment requests are timing out in production.",
  "service": "payment-service",
  "environment": "production",
  "reported_by": "sre-oncall",
  "source": "manual"
}
```

---

## 13. Example Workflow Output

```json
{
  "incident_id": 1,
  "severity": {
    "level": "critical",
    "confidence": 0.87,
    "reasoning": "Production checkout is impacted and users cannot complete payment."
  },
  "root_cause": {
    "category": "database_or_downstream_dependency",
    "confidence": 0.72,
    "evidence": [
      "Payment timeout",
      "Production impact",
      "Checkout failure"
    ]
  },
  "runbook": {
    "recommended_steps": [
      "Check payment-service logs",
      "Inspect database latency",
      "Validate recent deployments",
      "Check downstream payment gateway status",
      "Escalate to payment-service owner"
    ]
  },
  "summary": {
    "stakeholder_summary": "The payment service is degraded in production, affecting checkout completion. Engineering is investigating database and downstream dependency latency."
  },
  "overall_confidence": 0.76,
  "requires_human_review": true
}
```

---

## 14. Run with Docker

### 14.1 Prepare Environment

```bash
cp .env.example .env
```

Add your Groq or Anthropic key in `.env`.

---

### 14.2 Start Services

```bash
docker compose up --build
```

FastAPI:

```text
http://localhost:8000
```

Streamlit:

```text
http://localhost:8501
```

---

### 14.3 Docker Environment Note

If you are using Groq, make sure `docker-compose.yml` passes Groq variables:

```yaml
environment:
  - GROQ_API_KEY=${GROQ_API_KEY}
  - GROQ_MODEL=${GROQ_MODEL:-llama-3.1-8b-instant}
```

---

## 15. Run Tests

```bash
pytest tests/ -v
```

The test suite is designed to run without making real LLM API calls by using mock/fallback behavior.

---

## 16. Why Plain Python Instead of LangGraph?

This project uses a fixed linear workflow:

```text
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

## 17. Design Decisions

### 17.1 LLM Abstraction

Agents do not directly depend on a specific LLM SDK.

Instead, the project uses a shared LLM provider interface so the backend can switch between Groq, Anthropic, or future providers.

---

### 17.2 Confidence Bounding

Confidence values are bounded to avoid unrealistic scores.

Example:

```text
Minimum confidence: 0.10
Maximum confidence: 0.95
Low-confidence threshold: 0.50
```

---

### 17.3 Fallback Safety

Each agent is designed to return safe fallback output if the LLM fails.

This prevents one failed model call from crashing the entire workflow.

---

### 17.4 Human Review

The AI does not directly execute production actions.

It recommends actions, and a human can approve or reject the result.

---

### 17.5 Auditability

Every important workflow step is logged so the system can explain:

```text
What happened?
Which stage ran?
What did the model return?
How confident was it?
Did fallback happen?
Did a human approve it?
```

---

## 18. What This Project Demonstrates

This project demonstrates practical knowledge of:

- Agentic AI workflow design
- FastAPI backend development
- Streamlit dashboard development
- LLM provider abstraction
- Prompted JSON generation
- Retry and fallback handling
- Audit logging
- Human-in-the-loop governance
- SQLite persistence
- Dockerized deployment
- Pytest-based validation
- Enterprise AI/SRE thinking

---

## 19. Limitations

This is an MVP and has intentional limitations:

- SQLite is not ideal for high-concurrency production use.
- No authentication or authorization is implemented.
- No background queue such as Celery or Redis Queue.
- Runbook retrieval is lightweight and not a full vector database RAG system.
- LLM outputs are recommendations, not guaranteed root-cause truth.
- Human review is required before taking operational action.
- No integration with PagerDuty, Jira, Slack, Datadog, Grafana, or Kubernetes yet.

---

## 20. Possible Future Improvements

- Add authentication and role-based access control.
- Add vector database based runbook retrieval.
- Integrate with PagerDuty, Jira, Slack, or ServiceNow.
- Add Kubernetes/Grafana/Datadog incident context ingestion.
- Add Celery or Redis Queue for async workflow execution.
- Add PostgreSQL for production-ready persistence.
- Add OpenTelemetry traces.
- Add model evaluation metrics for triage accuracy.
- Add deployment on cloud platforms.
- Add feedback loop from human review to improve prompts and rules.

---

## 21. Portfolio / Resume Description

```text
Built TriageIQ, an AI-powered enterprise incident triage copilot using FastAPI, Streamlit, SQLite, and LLM agents. The system ingests incidents through manual entry, CSV, or JSON upload and runs a four-stage triage pipeline for severity classification, root-cause inference, runbook recommendation, and stakeholder summarization. It includes confidence scoring, audit logging, retry/fallback behavior, and human-in-the-loop review for safe AI-assisted operations.
```

---

## 22. Interview Explanation

TriageIQ is an AI incident triage assistant. When an incident occurs, the user submits incident details through the UI or API. The backend runs a four-agent pipeline. The first agent classifies severity, the second identifies a probable root cause, the third recommends runbook steps, and the fourth creates a stakeholder summary. The system stores all results in SQLite, maintains an audit trail, and allows a human reviewer to approve or reject the AI-generated recommendation.

---

## 23. Important Security Note

Never commit secrets.

Do not push:

```text
.env
data/triage.db
data/test_triage.db
__pycache__/
.pytest_cache/
```

Only push:

```text
.env.example
```

---

## 24. License

MIT License

---

## 25. Disclaimer

This project is built for learning, portfolio demonstration, and experimentation with AI-assisted incident management.

It is not production-ready and should not be used as the sole decision-maker for real operational incidents.
