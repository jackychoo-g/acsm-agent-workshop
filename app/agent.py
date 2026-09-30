"""ACSM Underwriting & Product Policy Agents (Agent 1: BigQuery Vector Search; Agent 2: Agent Platform RAG Engine)."""

from __future__ import annotations

import logging
import os

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.tools import preload_memory
from google.genai import types

from app.governance.policy_guard import (
    before_model_governance_guard,
    before_tool_governance_guard,
)
from app.tools.policy_search import (
    lookup_restricted_audit_log,
    search_policy_corpus,
)
from app.tools.rag_engine_search import search_rag_engine_corpus

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

from app import config

# Model calls use the global endpoint; data, RAG and Memory Bank stay in config.REGION.
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
# Full prompt/response capture in Cloud Trace spans, for the audit walkthrough.
os.environ.setdefault("GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY", "true")
os.environ.setdefault("ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS", "true")
os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "SPAN_AND_EVENT")

MODEL = config.MODEL

# ── Step 1.4 Baseline Instruction (naive RAG prompt before hillclimbing) ─────
BASELINE_INSTRUCTION = """You are a general assistant for AEON Credit Service (M) Berhad.
Use `search_policy_corpus` to answer questions in one brief sentence.
Do not cite internal document codes, policy versions, effective dates, or conflicting older documents, and always reply in English."""

# ── Shared Governance & Citation Rules for Hillclimbed Agents ────────────────
_SHARED_GOVERNANCE_RULES = """
Follow these six mandatory governance and citation rules on every response:
1. Temporal & Version Precedence: Inspect `doc_id`, `version`, and `effective_date` on every retrieved chunk. Always cite the exact `doc_id` (e.g., `POL-CR-001-v2`), `version`, and `effective_date` in your answer. Credit Underwriting Policy v2 (`POL-CR-001-v2`, effective 2026-01-01) and dated branch circulars (`CIR-01`, `CIR-03`) supersede Policy v1 (`POL-CR-001-v1`, effective 2025-01-01). Always apply the newer threshold and explicitly cite both `POL-CR-001-v2` and the superseded `POL-CR-001-v1` rule.
2. Fee Schedule Authority & Conflict Flagging: When two documents disagree on a fee or charge (for example, the Schedule of Fees `FEE-001` effective 2026-03-01 vs an earlier Product Disclosure Sheet `PDS-CARD-CLASSIC` effective 2025-06-01), treat `FEE-001` as authoritative, state its figure, and explicitly cite and flag the stale figure in `PDS-CARD-CLASSIC` so the officer is never misled.
3. Cross-Lingual Citation: If the user asks in Bahasa Malaysia, reply in natural Bahasa Malaysia while citing the exact `doc_id` (e.g., `POL-CR-001-v2`, `FAQ-CARD-MS`) and `clause_id`.
4. Access Classification Guardrail: If the user specifies they are a customer or asks for public-only information, pass `include_internal=False` to the search tool and cite only public documents (`PDS-*`, `FAQ-*`, `FEE-001`); never quote internal SOPs (`SOP-*`), collections scripts (`COL-*`), or compliance procedures (`CMP-*`).
5. Clickable Source Links (Mandatory): Every retrieved match includes a pre-formatted `citation_markdown` field containing a clickable Markdown link to the source document in Cloud Storage (with `#page=N` for PDFs). Always include a `### Sources` section at the bottom of your response and render the exact `citation_markdown` links verbatim as a bulleted list so the user can click straight to the source document and page.
6. Retrieval Efficiency: Execute at most 1 to 2 targeted retrieval tool calls per user turn. Synthesize your answer immediately from the retrieved chunks rather than issuing repeated follow-up searches.
"""

BQ_HILLCLIMB_INSTRUCTION = (
    """You are Agent 1 (`acsm-bq-rag-agent`), the BigQuery Vector Search Underwriting & Product Policy Assistant for AEON Credit Service (M) Berhad.
Always call `search_policy_corpus` (backed by BigQuery `VECTOR_SEARCH` on the shared `acsm_rag.policy_chunks` table) before answering any policy, product, circular, or fee question, and call `lookup_restricted_audit_log` (or delegate to `bq_audit_specialist`) when asked about branch credit exception audits.
"""
    + _SHARED_GOVERNANCE_RULES
)

RAG_ENGINE_HILLCLIMB_INSTRUCTION = (
    """You are Agent 2 (`acsm-rag-engine-agent`), the Agent Platform RAG Engine Underwriting & Product Policy Assistant for AEON Credit Service (M) Berhad.
Always call `search_rag_engine_corpus` (backed by the managed RAG Engine on Gemini Enterprise Agent Platform corpus) before answering any policy, product, circular, or fee question, and call `lookup_restricted_audit_log` (or delegate to `rag_engine_audit_specialist`) when asked about branch credit exception audits.
"""
    + _SHARED_GOVERNANCE_RULES
)

ACTIVE_MODE = config.PROMPT_MODE
ACTIVE_BACKEND = config.RAG_BACKEND


async def _persist_session_to_memory(callback_context: CallbackContext) -> None:
    """Trigger Memory Bank extraction at the end of each agent turn."""
    try:
        await callback_context.add_session_to_memory()
    except Exception as exc:
        logger.debug("Memory persistence skipped: %s", exc)


def _build_audit_subagent(parent_prefix: str) -> Agent:
    """Specialist sub-agent for restricted branch audit lookups (renders in Console Topology graph)."""
    return Agent(
        name=f"{parent_prefix}_audit_specialist",
        description="Compliance specialist sub-agent that queries restricted branch credit exception audit logs in BigQuery (`collections_internal_audit`).",
        model=Gemini(
            model=MODEL,
            retry_options=types.HttpRetryOptions(attempts=3),
        ),
        instruction=(
            "You are the ACSM Branch Audit & Compliance Specialist sub-agent. "
            "Always call `lookup_restricted_audit_log` to retrieve branch exception audit records "
            "and report the exact `audit_id`, `branch`, `product`, `finding`, and `audit_date`. "
            "If the tool returns `status: PERMISSION_DENIED`, tell the user access was denied for this agent's identity and quote the `explanation`."
        ),
        tools=[lookup_restricted_audit_log],
        before_model_callback=before_model_governance_guard,
        before_tool_callback=before_tool_governance_guard,
    )


def create_bq_rag_agent(name: str = "root_agent") -> Agent:
    """Build Agent 1: BigQuery VECTOR_SEARCH RAG Agent."""
    instruction = BASELINE_INSTRUCTION if ACTIVE_MODE == "baseline" else BQ_HILLCLIMB_INSTRUCTION
    return Agent(
        name=name,
        description=f"ACSM Underwriting & Policy Agent (BigQuery VECTOR_SEARCH), owner={config.OWNER}.",
        model=Gemini(
            model=MODEL,
            retry_options=types.HttpRetryOptions(attempts=3),
        ),
        instruction=instruction,
        tools=[
            search_policy_corpus,
            lookup_restricted_audit_log,
            preload_memory,
        ],
        sub_agents=[_build_audit_subagent("bq")],
        before_model_callback=before_model_governance_guard,
        before_tool_callback=before_tool_governance_guard,
        after_agent_callback=_persist_session_to_memory,
    )


def create_rag_engine_agent(name: str = "root_agent") -> Agent:
    """Build Agent 2: RAG Engine on Gemini Enterprise Agent Platform Agent."""
    instruction = BASELINE_INSTRUCTION if ACTIVE_MODE == "baseline" else RAG_ENGINE_HILLCLIMB_INSTRUCTION
    return Agent(
        name=name,
        description=f"ACSM Underwriting & Policy Agent (RAG Engine), owner={config.OWNER}.",
        model=Gemini(
            model=MODEL,
            retry_options=types.HttpRetryOptions(attempts=3),
        ),
        instruction=instruction,
        tools=[
            search_rag_engine_corpus,
            lookup_restricted_audit_log,
            preload_memory,
        ],
        sub_agents=[_build_audit_subagent("rag_engine")],
        before_model_callback=before_model_governance_guard,
        before_tool_callback=before_tool_governance_guard,
        after_agent_callback=_persist_session_to_memory,
    )


_BQ_PLUGIN_SINGLETON: list | None = None


def build_bq_analytics_plugin() -> list:
    """Configure BigQueryAgentAnalyticsPlugin for full request/response/tool audit logging."""
    global _BQ_PLUGIN_SINGLETON
    if os.environ.get("ACSM_DISABLE_BQ_ANALYTICS", "").lower() in ("true", "1"):
        return []
    if _BQ_PLUGIN_SINGLETON is not None:
        return _BQ_PLUGIN_SINGLETON
    try:
        from google.adk.plugins.bigquery_agent_analytics_plugin import (
            BigQueryAgentAnalyticsPlugin,
            BigQueryLoggerConfig,
        )

        cfg = BigQueryLoggerConfig(
            enabled=True,
            table_id="agent_events",
            batch_size=1,
            batch_flush_interval=1.0,
            log_session_metadata=True,
            auto_schema_upgrade=False,
            create_views=False,
            enable_otel_correlation=True,
            custom_tags={
                "workshop": "acsm-agent-workshop",
                "owner": config.OWNER,
                "rag_backend": ACTIVE_BACKEND,
                "prompt_mode": ACTIVE_MODE,
            },
        )
        _BQ_PLUGIN_SINGLETON = [
            BigQueryAgentAnalyticsPlugin(
                project_id=config.DATA_PROJECT,
                dataset_id=config.ANALYTICS_DATASET,
                config=cfg,
                location=config.REGION,
            )
        ]
        return _BQ_PLUGIN_SINGLETON
    except Exception as exc:
        logger.warning("BigQueryAgentAnalyticsPlugin initialization skipped: %s", exc)
        return []


root_agent = (
    create_rag_engine_agent("root_agent")
    if ACTIVE_BACKEND == "rag_engine"
    else create_bq_rag_agent("root_agent")
)

app = App(
    root_agent=root_agent,
    name="app",
    plugins=build_bq_analytics_plugin(),
)
