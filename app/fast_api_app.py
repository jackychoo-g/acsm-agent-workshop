import contextlib
import mimetypes
import os
from collections.abc import AsyncIterator
from pathlib import Path

from a2a.server.tasks import InMemoryTaskStore
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from google.adk.cli.fast_api import get_fast_api_app
from google.adk.runners import Runner
from google.cloud import storage

from app.app_utils import services
from app.app_utils.a2a import attach_a2a_routes
from app.app_utils.reasoning_engine_adapter import (
    attach_reasoning_engine_routes,
)
from app.governance.policy_guard import (
    get_governance_status,
    get_recent_governance_events,
)

load_dotenv()

# Enable full unredacted OpenTelemetry message capture for Cloud Trace & Cloud Logging
os.environ.setdefault("GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY", "true")
os.environ.setdefault("ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS", "true")
os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "SPAN_AND_EVENT")

otel_to_cloud = os.environ.get(
    "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY", "true"
).lower() in ("true", "1")
allow_origins = (
    os.getenv("ALLOW_ORIGINS", "").split(",") if os.getenv("ALLOW_ORIGINS") else None
)

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS_DIR = Path(AGENT_DIR) / "corpus"
from app import config  # noqa: E402

BUCKET_NAME = config.RAG_BUCKET
PROJECT_ID = config.RUNTIME_PROJECT


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    from app.agent import app as adk_app
    from app.agent import root_agent

    runner = Runner(
        app=adk_app,
        session_service=services.get_session_service(),
        memory_service=services.get_memory_service(),
        artifact_service=services.get_artifact_service(),
        auto_create_session=True,
    )
    app.state.runner = runner
    app.state.agent_app_name = adk_app.name
    await attach_a2a_routes(
        app,
        agent=root_agent,
        runner=runner,
        task_store=InMemoryTaskStore(),
        rpc_path=f"/a2a/{adk_app.name}",
    )
    yield


app: FastAPI = get_fast_api_app(
    agents_dir=AGENT_DIR,
    web=True,
    artifact_service_uri=services.ARTIFACT_SERVICE_URI,
    memory_service_uri=services.MEMORY_SERVICE_URI,
    allow_origins=allow_origins,
    session_service_uri=services.SESSION_SERVICE_URI,
    otel_to_cloud=otel_to_cloud,
    auto_create_session=True,
    lifespan=lifespan,
)
app.title = "acsm-agent-workshop"
app.description = "ACSM Dual-RAG Underwriting & Policy Agents (BigQuery Vector Search & RAG Engine on Gemini Enterprise Agent Platform)"

attach_reasoning_engine_routes(app)


@app.get("/sources/{file_path:path}", tags=["rag-sources"])
def serve_rag_source_document(file_path: str) -> Response:
    """Serve ACSM RAG corpus documents inline so PDFs open directly at #page=N."""
    safe_rel = Path(file_path)
    if ".." in safe_rel.parts:
        raise HTTPException(status_code=400, detail="Invalid file path")

    local_candidate = (CORPUS_DIR / safe_rel).resolve()
    content_type = mimetypes.guess_type(local_candidate.name)[0] or "application/octet-stream"
    if local_candidate.suffix.lower() in (".eml", ".csv", ".md"):
        content_type = "text/plain; charset=utf-8"

    if local_candidate.exists() and local_candidate.is_file():
        return FileResponse(
            path=str(local_candidate),
            media_type=content_type,
            headers={"Content-Disposition": f'inline; filename="{local_candidate.name}"'},
        )

    # Fallback: stream directly from gs://<ACSM_RAG_BUCKET>/raw/<file_path>
    try:
        storage_client = storage.Client(project=PROJECT_ID)
        blob = storage_client.bucket(BUCKET_NAME).blob(f"raw/{file_path}")
        if not blob.exists():
            raise HTTPException(status_code=404, detail=f"Document not found: {file_path}")
        data = blob.download_as_bytes()
        return Response(
            content=data,
            media_type=blob.content_type or content_type,
            headers={"Content-Disposition": f'inline; filename="{safe_rel.name}"'},
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error fetching source document: {exc}") from exc


@app.get("/governance/status", tags=["governance"])
def governance_status_endpoint() -> dict:
    """Return active Model Armor, PDPA semantic policies, Agent Gateways, and telemetry posture."""
    return get_governance_status()


@app.get("/governance/audit-trail", tags=["governance"])
def governance_audit_trail_endpoint(limit: int = 25) -> dict:
    """Return recent real-time governance evaluation verdicts (ALLOW / BLOCK)."""
    events = get_recent_governance_events(limit=limit)
    return {"count": len(events), "events": events}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
