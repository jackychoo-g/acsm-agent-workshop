"""ACSM BigQuery VECTOR_SEARCH RAG agent (selectable in ADK /dev-ui)."""

from google.adk.apps import App
from app.agent import build_bq_analytics_plugin, create_bq_rag_agent

root_agent = create_bq_rag_agent(name="acsm_bq_rag")

app = App(
    root_agent=root_agent,
    name="acsm_bq_rag",
    plugins=build_bq_analytics_plugin(),
)
