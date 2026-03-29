"""
main.py

FastAPI application entrypoint for the Enterprise Incident Triage Copilot.

This file:
  1. Creates the FastAPI app instance
  2. Registers all API routers
  3. Initializes the database on startup
  4. Provides a health check endpoint
  5. Is the target for `uvicorn main:app`

Routers are registered here but defined in app/api/.
We keep this file thin — only wiring, no business logic.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db.database import init_db, check_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── Lifespan: runs at startup and shutdown ────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan handler.
    Code before `yield` runs at startup; code after runs at shutdown.
    """
    logger.info(f"Starting {settings.app_name} [{settings.app_env}]")
    logger.info(f"LLM model: {settings.llm_model}")

    # Initialize database tables
    init_db()

    # Verify DB is reachable
    if check_db_connection():
        logger.info("Database connection OK")
    else:
        logger.error("Database connection FAILED — check DATABASE_URL in .env")

    yield  # Application runs here

    logger.info("Shutting down Incident Triage Copilot")


# ── FastAPI app instance ──────────────────────────────────────────────────────
app = FastAPI(
    title=settings.app_name,
    description=(
        "A portfolio-grade, enterprise-inspired incident triage copilot. "
        "Demonstrates agentic workflow orchestration, hosted LLM integration, "
        "runbook retrieval, auditability, and human-in-the-loop review. "
        "Built as an MVP — not a production system."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)


# ── CORS middleware ───────────────────────────────────────────────────────────
# Allows the Streamlit UI (running on a different port) to call the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Tighten this in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Register API routers ──────────────────────────────────────────────────────
# Routers are imported here. They're defined in app/api/.
# Each router handles one domain (incidents, workflow, audit, review).
# We import inside the function to avoid circular imports at module load time.

def register_routers(application: FastAPI) -> None:
    from app.api.incidents import router as incidents_router
    from app.api.workflow   import router as workflow_router
    from app.api.audit      import router as audit_router
    from app.api.review     import router as review_router

    application.include_router(incidents_router, prefix="/api/v1", tags=["Incidents"])
    application.include_router(workflow_router,  prefix="/api/v1", tags=["Workflow"])
    application.include_router(audit_router,     prefix="/api/v1", tags=["Audit"])
    application.include_router(review_router,    prefix="/api/v1", tags=["Review"])


register_routers(app)


# ── Health check ─────────────────────────────────────────────────────────────
@app.get("/health", tags=["System"])
def health_check():
    """
    Simple health check endpoint.
    Returns the app status and DB connectivity.
    Used by Docker HEALTHCHECK and demo scripts.
    """
    db_ok = check_db_connection()
    return {
        "status": "ok" if db_ok else "degraded",
        "app": settings.app_name,
        "env": settings.app_env,
        "llm_model": settings.llm_model,
        "database": "ok" if db_ok else "unreachable",
    }


@app.get("/", tags=["System"])
def root():
    """Root endpoint — redirects users to the API docs."""
    return {
        "message": f"Welcome to {settings.app_name}",
        "docs": "/docs",
        "health": "/health",
    }
