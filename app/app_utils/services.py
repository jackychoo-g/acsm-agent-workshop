"""Process-wide ADK session, memory, and artifact services shared across
ADK web routes, A2A, and the reasoning_engine adapter.
"""

from __future__ import annotations

import functools
import os

from google.adk.artifacts import GcsArtifactService, InMemoryArtifactService
from google.adk.cli.service_registry import get_service_registry
from google.adk.cli.utils.service_factory import (
    create_memory_service_from_options,
    create_session_service_from_options,
)

SESSION_SERVICE_URI = "shared://session"
ARTIFACT_SERVICE_URI = "shared://artifact"
MEMORY_SERVICE_URI = "shared://memory"

_AGENT_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)


@functools.cache
def get_session_service():
    """Process-wide session service shared across every serving surface."""
    if uri := os.environ.get("SESSION_SERVICE_URI"):
        return create_session_service_from_options(
            base_dir=_AGENT_DIR, session_service_uri=uri
        )
    if agent_engine_id := os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ID"):
        from google.adk.sessions.vertex_ai_session_service import VertexAiSessionService

        return VertexAiSessionService(
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_LOCATION")
            or os.environ.get("ACSM_REGION", "asia-southeast1"),
            agent_engine_id=agent_engine_id,
        )
    from google.adk.sessions.in_memory_session_service import InMemorySessionService

    return InMemorySessionService()


@functools.cache
def get_memory_service():
    """Process-wide memory service: VertexAiMemoryBankService when an Agent
    Runtime ID is present (natively on Agent Runtime, or borrowed on Cloud Run),
    falling back to InMemoryMemoryService for local runs and offline eval.
    """
    if uri := os.environ.get("MEMORY_SERVICE_URI"):
        return create_memory_service_from_options(
            base_dir=_AGENT_DIR, memory_service_uri=uri
        )
    if agent_engine_id := os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ID"):
        from google.adk.memory.vertex_ai_memory_bank_service import (
            VertexAiMemoryBankService,
        )

        return VertexAiMemoryBankService(
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_LOCATION")
            or os.environ.get("ACSM_REGION", "asia-southeast1"),
            agent_engine_id=agent_engine_id,
        )
    from google.adk.memory.in_memory_memory_service import InMemoryMemoryService

    return InMemoryMemoryService()


@functools.cache
def get_artifact_service():
    """Process-wide artifact service: GCS when a bucket is set, else in-memory."""
    if bucket := os.environ.get("LOGS_BUCKET_NAME"):
        return GcsArtifactService(bucket_name=bucket)
    return InMemoryArtifactService()


_registry = get_service_registry()
_registry.register_session_service("shared", lambda uri, **kw: get_session_service())
_registry.register_memory_service("shared", lambda uri, **kw: get_memory_service())
_registry.register_artifact_service("shared", lambda uri, **kw: get_artifact_service())
