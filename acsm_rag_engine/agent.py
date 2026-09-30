"""Agent 2: ACSM RAG Engine on Gemini Enterprise Agent Platform (selectable in ADK /dev-ui)."""

from google.adk.apps import App
from app.agent import build_bq_analytics_plugin, create_rag_engine_agent

root_agent = create_rag_engine_agent(name="acsm_rag_engine")

app = App(
    root_agent=root_agent,
    name="acsm_rag_engine",
    plugins=build_bq_analytics_plugin(),
)
