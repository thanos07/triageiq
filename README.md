# Enterprise Incident Triage Copilot

> A portfolio-grade, enterprise-inspired AI operations assistant that triages incidents, infers probable root causes, recommends runbook steps, and generates stakeholder summaries — with full audit trail and human-in-the-loop review.

**Honest framing:** This is an MVP portfolio project built to demonstrate agentic workflow orchestration, hosted LLM integration, runbook retrieval, governance, and operational thinking. It is not a production system.

## Demo Flow

```
Submit incident → 4-agent triage pipeline → structured result + audit trail → human review
```

- Manual entry, CSV upload, or JSON upload
- 4 specialized agents: Severity → Root Cause → Runbook → Summary
- Confidence scores on every output — low confidence triggers escalation
- Audit trail: latency, model, retries, fallback status per stage
- Human review gate — approve or reject before acting

## Stack

| Layer | Technology |
|---|---|
| Backend API | FastAPI + Uvicorn |
| Frontend | Streamlit |
| Database | SQLite via SQLAlchemy |
| LLM | Groq-llama-3.3-70b |
| Retry | Tenacity (exponential backoff) |
| Tests | Pytest — 156 tests, zero API calls |
| Packaging | Docker + Docker Compose |

## Quickstart

```bash
git clone https://github.com/YOUR_USERNAME/enterprise-incident-triage-copilot
cd enterprise-incident-triage-copilot
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env         # add your ANTHROPIC_API_KEY
uvicorn main:app --reload --port 8000   # Terminal 1
streamlit run streamlit_app.py          # Terminal 2
```

- API docs: http://localhost:8000/docs
- UI: http://localhost:8501

## Docker

```bash
cp .env.example .env   # add ANTHROPIC_API_KEY
docker compose up --build
```

## Tests

```bash
pytest tests/ -v   # 156 tests, no API key required
```

## Key Design Decisions

**Plain Python over LangGraph** — linear fixed pipeline; LangGraph adds complexity without benefit here.

**Confidence bounding** — scores clamped to [0.10, 0.95]; evidence-weak causes cap at 0.50.

**Fallback safety** — every agent catches its own errors; pipeline continues as partial_failure.

**Governance override** — critical severity + confidence < 0.50 forces escalation regardless of agent output.

## Limitations (honest)

- SQLite not suitable for concurrent production use
- No task queue — pipeline runs in FastAPI background tasks
- Runbook retrieval uses keyword scoring, not embeddings
- No API authentication

## License

MIT
