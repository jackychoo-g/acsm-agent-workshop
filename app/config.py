"""Single source of runtime settings. Every value comes from the environment.

Two projects can differ:
  * RUNTIME_PROJECT: where the agent runs and where BigQuery query jobs are billed.
  * DATA_PROJECT: where the shared RAG dataset, bucket and corpus live.
In the workshop both are the same shared project; they stay separate so the
code keeps working if the data moves to its own project later.
"""

from __future__ import annotations

import os
import re

from dotenv import load_dotenv

# Tools import this module before agent.py runs, so .env is loaded here.
# Real environment variables (set by `make` or Agent Runtime) win over .env.
load_dotenv(override=False)


def _project_id(*names: str) -> str:
    # Agent Runtime sets GOOGLE_CLOUD_PROJECT to the project *number*; BigQuery
    # table references need the project *ID*, so numeric values are skipped.
    for name in names:
        value = os.environ.get(name, "").strip()
        if value and not value.isdigit():
            return value
    raise RuntimeError(
        f"Set one of {', '.join(names)} to your workshop project ID. "
        "Run `make whoami` to see what the Makefile will pass."
    )


RUNTIME_PROJECT = _project_id("ACSM_PROJECT", "GOOGLE_CLOUD_PROJECT")
PROJECT = RUNTIME_PROJECT
DATA_PROJECT = _project_id("ACSM_DATA_PROJECT", "ACSM_PROJECT", "GOOGLE_CLOUD_PROJECT")
REGION = os.environ.get("ACSM_REGION", "asia-southeast1")

MODEL = os.environ.get("ACSM_MODEL", "gemini-3.8-flash")
EMBED_MODEL = "gemini-embedding-001"
EMBED_DIM = 768

RAG_DATASET = os.environ.get("ACSM_RAG_DATASET", "acsm_rag")
CHUNKS_TABLE = f"{DATA_PROJECT}.{RAG_DATASET}.policy_chunks"
AUDIT_TABLE = f"{DATA_PROJECT}.{RAG_DATASET}.collections_internal_audit"
RAG_BUCKET = os.environ.get("ACSM_RAG_BUCKET", f"{DATA_PROJECT}-acsm-rag-corpus")
RAG_CORPUS_NAME = os.environ.get("ACSM_RAG_CORPUS_NAME", "")
RAG_BACKEND = os.environ.get("ACSM_RAG_BACKEND", "bigquery").lower()

ANALYTICS_DATASET = os.environ.get("BQ_ANALYTICS_DATASET_ID", "adk_agent_analytics")

MODEL_ARMOR_ENABLED = os.environ.get("ACSM_ENABLE_MODEL_ARMOR", "true").lower() in ("true", "1", "yes")
MODEL_ARMOR_LOCATION = os.environ.get("ACSM_MODEL_ARMOR_LOCATION", "us-central1")
MODEL_ARMOR_TEMPLATE = os.environ.get("ACSM_MODEL_ARMOR_TEMPLATE", "acsm-credit-armor")

PROMPT_MODE = os.environ.get("ACSM_PROMPT_MODE", "hillclimb").lower()

# Participant tag, stamped into analytics rows and the agent description so a
# shared project can tell 30 identical agents apart.
OWNER = re.sub(r"[^a-z0-9-]", "-", os.environ.get("ACSM_OWNER", "local").lower())[:30].strip("-") or "local"
