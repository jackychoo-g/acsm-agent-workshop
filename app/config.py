"""Single source of runtime settings. Every value comes from the environment.

Two projects can differ:
  * RUNTIME_PROJECT: where the agent runs and where BigQuery query jobs are billed.
  * DATA_PROJECT: where the shared RAG dataset and document bucket live.
In the workshop both are the same shared project; they stay separate so the
code keeps working if the data moves to its own project later.
"""

from __future__ import annotations

from pathlib import Path
import os
import re
import subprocess

from dotenv import load_dotenv

# Tools import this module before agent.py runs, so .env and .lab.env (written
# by `make configure`) are loaded here. Real environment variables win.
_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(_ROOT / ".env", override=False)
load_dotenv(_ROOT / ".lab.env", override=False)


def _project_id(*names: str) -> str:
    # Agent Runtime sets GOOGLE_CLOUD_PROJECT to the project *number*; BigQuery
    # table references need the project *ID*, so numeric values are skipped.
    for name in names:
        value = os.environ.get(name, "").strip()
        if value and not value.isdigit():
            return value
    try:
        gcloud_proj = subprocess.check_output(
            ["gcloud", "config", "get-value", "project"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        ).strip()
        if gcloud_proj and gcloud_proj != "(unset)" and not gcloud_proj.isdigit():
            return gcloud_proj
    except Exception:
        pass
    raise RuntimeError(
        f"Set one of {', '.join(names)} to your workshop project ID (or run `gcloud config set project <id>` and `make configure`)."
    )


RUNTIME_PROJECT = _project_id("ACSM_PROJECT", "PROJECT", "GOOGLE_CLOUD_PROJECT")
PROJECT = RUNTIME_PROJECT
DATA_PROJECT = _project_id("ACSM_DATA_PROJECT", "DATA_PROJECT", "ACSM_PROJECT", "PROJECT", "GOOGLE_CLOUD_PROJECT")
REGION = os.environ.get("ACSM_REGION") or os.environ.get("REGION") or "asia-southeast1"

MODEL = os.environ.get("ACSM_MODEL", "gemini-3.8-flash")
EMBED_MODEL = "gemini-embedding-001"
EMBED_DIM = 768

RAG_DATASET = os.environ.get("ACSM_RAG_DATASET", "acsm_rag")
CHUNKS_TABLE = f"{DATA_PROJECT}.{RAG_DATASET}.policy_chunks"
AUDIT_TABLE = f"{DATA_PROJECT}.{RAG_DATASET}.collections_internal_audit"
RAG_BUCKET = os.environ.get("ACSM_RAG_BUCKET") or os.environ.get("RAG_BUCKET") or f"{DATA_PROJECT}-acsm-rag-corpus"

ANALYTICS_DATASET = os.environ.get("BQ_ANALYTICS_DATASET_ID", "adk_agent_analytics")

MODEL_ARMOR_ENABLED = os.environ.get("ACSM_ENABLE_MODEL_ARMOR", "true").lower() in ("true", "1", "yes")
MODEL_ARMOR_LOCATION = os.environ.get("ACSM_MODEL_ARMOR_LOCATION") or REGION
MODEL_ARMOR_TEMPLATE = os.environ.get("ACSM_MODEL_ARMOR_TEMPLATE", "acsm-credit-armor")

PROMPT_MODE = os.environ.get("ACSM_PROMPT_MODE", "hillclimb").lower()

# Participant tag, stamped into analytics rows and the agent description so a
# shared project can tell 30 identical agents apart.
OWNER = re.sub(r"[^a-z0-9-]", "-", os.environ.get("ACSM_OWNER", "local").lower())[:30].strip("-") or "local"
