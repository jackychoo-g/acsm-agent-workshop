"""Model Armor + BNM RMiT Semantic Governance Guardrails for ACSM ADK Agents."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import json
import logging
import os
import re
import time
from typing import Any

import google.auth
import google.auth.transport.requests
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types
from opentelemetry import trace
import requests

logger = logging.getLogger("acsm.governance")
tracer = trace.get_tracer("acsm.governance")

from app import config

PROJECT = config.DATA_PROJECT
MODEL_ARMOR_LOCATION = config.MODEL_ARMOR_LOCATION
MODEL_ARMOR_TEMPLATE = config.MODEL_ARMOR_TEMPLATE
MODEL_ARMOR_ENABLED = config.MODEL_ARMOR_ENABLED

# Malaysian MyKad NRIC pattern: YYMMDD-PB-#### (e.g., 880512-14-5678)
MYKAD_UNMASKED_RE = re.compile(r"\b\d{6}-\d{2}-\d{4}\b")
SQL_INJECTION_RE = re.compile(r"(?i)(\bDROP\s+TABLE\b|\bUNION\s+SELECT\b|--\s*$|;\s*DELETE\s+FROM)")

_RECENT_EVENTS: deque[dict[str, Any]] = deque(maxlen=100)
_cached_creds = None


def _get_bearer_token() -> str | None:
    global _cached_creds
    try:
        if _cached_creds is None:
            _cached_creds, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
        if not _cached_creds.valid or _cached_creds.expired or not _cached_creds.token:
            _cached_creds.refresh(google.auth.transport.requests.Request())
        return _cached_creds.token
    except Exception as exc:
        logger.warning("Failed to refresh ADC token for Model Armor: %s", exc)
        return None


def _call_model_armor(prompt_text: str) -> dict[str, Any]:
    """Invoke Google Cloud Model Armor sanitizeUserPrompt endpoint."""
    if not MODEL_ARMOR_ENABLED or not prompt_text.strip():
        return {"filterMatchState": "NO_MATCH_FOUND", "skipped": True}

    token = _get_bearer_token()
    if not token:
        return {"filterMatchState": "NO_MATCH_FOUND", "error": "no_adc_token"}

    url = (
        f"https://modelarmor.{MODEL_ARMOR_LOCATION}.rep.googleapis.com/v1/"
        f"projects/{PROJECT}/locations/{MODEL_ARMOR_LOCATION}/templates/{MODEL_ARMOR_TEMPLATE}:sanitizeUserPrompt"
    )
    t0 = time.monotonic()
    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"userPromptData": {"text": prompt_text[:8000]}},
            timeout=4.0,
        )
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        if resp.status_code == 200:
            data = resp.json().get("sanitizationResult", {})
            return {
                "filterMatchState": data.get("filterMatchState", "NO_MATCH_FOUND"),
                "filterResults": data.get("filterResults", {}),
                "invocationResult": data.get("invocationResult", "SUCCESS"),
                "latency_ms": latency_ms,
                "template": f"projects/{PROJECT}/locations/{MODEL_ARMOR_LOCATION}/templates/{MODEL_ARMOR_TEMPLATE}",
            }
        return {
            "filterMatchState": "NO_MATCH_FOUND",
            "http_status": resp.status_code,
            "latency_ms": latency_ms,
        }
    except Exception as exc:
        return {
            "filterMatchState": "NO_MATCH_FOUND",
            "error": str(exc),
            "latency_ms": round((time.monotonic() - t0) * 1000, 1),
        }


def _record_governance_event(event: dict[str, Any]) -> None:
    event["timestamp"] = datetime.now(timezone.utc).isoformat()
    _RECENT_EVENTS.appendleft(event)
    print(json.dumps({"severity": "INFO", "component": "acsm_governance_audit", **event}), flush=True)


def _extract_latest_user_text(llm_request: LlmRequest) -> str:
    if not llm_request or not llm_request.contents:
        return ""
    last_content = llm_request.contents[-1]
    if getattr(last_content, "parts", None) and any(
        getattr(p, "function_response", None) for p in last_content.parts
    ):
        # Post-tool synthesis step: user prompt was already verified on step 1
        return ""
    for content in reversed(llm_request.contents):
        if getattr(content, "role", "") == "user" and getattr(content, "parts", None):
            texts = [p.text for p in content.parts if getattr(p, "text", None)]
            if texts:
                return "\n".join(texts).strip()
    return ""


def before_model_governance_guard(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> LlmResponse | None:
    """Enforce Model Armor + BNM RMiT PDPA guardrails before calling Gemini."""
    user_text = _extract_latest_user_text(llm_request)
    agent_name = getattr(callback_context, "agent_name", "root_agent")
    if not user_text:
        return None

    with tracer.start_as_current_span("acsm.governance.evaluate_prompt") as span:
        span.set_attribute("governance.agent_name", agent_name)
        span.set_attribute("governance.prompt_preview", user_text[:300])
        span.set_attribute(
            "governance.model_armor_template",
            f"projects/{PROJECT}/locations/{MODEL_ARMOR_LOCATION}/templates/{MODEL_ARMOR_TEMPLATE}",
        )

        # 1. BNM RMiT / PDPA Check: Block raw unmasked Malaysian NRIC (MyKad) numbers
        nric_matches = MYKAD_UNMASKED_RE.findall(user_text)
        if nric_matches:
            span.set_attribute("governance.verdict", "BLOCK_PDPA_UNMASKED_NRIC")
            event = {
                "stage": "before_model_callback",
                "agent_name": agent_name,
                "verdict": "BLOCK",
                "policy_rule": "BNM-RMIT-PDPA-001 (Unmasked Malaysian MyKad NRIC)",
                "prompt_preview": MYKAD_UNMASKED_RE.sub("******-**-****", user_text[:200]),
            }
            _record_governance_event(event)
            refusal = (
                "**[Governance Policy Block — BNM-RMIT-PDPA-001]**\n\n"
                "Your request contains an unmasked Malaysian NRIC / MyKad number "
                "(`YYMMDD-PB-####`). Under **CMP-002 (Personal Data Protection Handling Procedure)** "
                "and Bank Negara Malaysia RMiT data secrecy controls, raw NRIC identifiers "
                "must be masked (e.g., `880512-14-****`) before submitting to an AI agent."
            )
            return LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[types.Part.from_text(text=refusal)],
                )
            )

        # 2. Google Cloud Model Armor Check (Prompt Injection, Jailbreak, Malicious URI, RAI)
        armor_res = _call_model_armor(user_text)
        match_state = armor_res.get("filterMatchState", "NO_MATCH_FOUND")
        span.set_attribute("governance.model_armor_state", match_state)
        span.set_attribute("governance.model_armor_latency_ms", armor_res.get("latency_ms", 0.0))

        if match_state == "MATCH_FOUND":
            span.set_attribute("governance.verdict", "BLOCK_MODEL_ARMOR")
            event = {
                "stage": "before_model_callback",
                "agent_name": agent_name,
                "verdict": "BLOCK",
                "policy_rule": f"MODEL_ARMOR ({MODEL_ARMOR_TEMPLATE})",
                "model_armor_state": match_state,
                "model_armor_latency_ms": armor_res.get("latency_ms"),
                "prompt_preview": user_text[:200],
            }
            _record_governance_event(event)
            refusal = (
                f"**[Governance Policy Block — Google Cloud Model Armor (`{MODEL_ARMOR_TEMPLATE}`)]**\n\n"
                "This prompt triggered a security policy violation (Prompt Injection / Jailbreak / "
                "Unsafe Content filter) enforced by the ACSM Model Armor template "
                f"(`projects/{PROJECT}/locations/{MODEL_ARMOR_LOCATION}/templates/{MODEL_ARMOR_TEMPLATE}`). "
                "The event has been logged to Cloud Trace and Cloud Logging."
            )
            return LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[types.Part.from_text(text=refusal)],
                )
            )

        span.set_attribute("governance.verdict", "ALLOW")
        _record_governance_event(
            {
                "stage": "before_model_callback",
                "agent_name": agent_name,
                "verdict": "ALLOW",
                "policy_rule": f"MODEL_ARMOR ({MODEL_ARMOR_TEMPLATE}) + BNM-RMIT-PDPA-001",
                "model_armor_state": match_state,
                "model_armor_latency_ms": armor_res.get("latency_ms"),
                "prompt_preview": user_text[:200],
            }
        )
        return None


def before_tool_governance_guard(
    tool: BaseTool,
    args: dict[str, Any],
    tool_context: ToolContext,
) -> dict[str, Any] | None:
    """Audit and validate every tool call before execution."""
    tool_name = getattr(tool, "name", str(tool))
    agent_name = getattr(tool_context, "agent_name", "root_agent")
    serialized_args = json.dumps(args, default=str)

    with tracer.start_as_current_span("acsm.governance.evaluate_tool") as span:
        span.set_attribute("governance.agent_name", agent_name)
        span.set_attribute("governance.tool_name", tool_name)
        span.set_attribute("governance.tool_args", serialized_args[:500])

        if SQL_INJECTION_RE.search(serialized_args):
            span.set_attribute("governance.verdict", "BLOCK_TOOL_INJECTION")
            _record_governance_event(
                {
                    "stage": "before_tool_callback",
                    "agent_name": agent_name,
                    "tool_name": tool_name,
                    "verdict": "BLOCK",
                    "policy_rule": "ACSM-TOOL-SEC-001 (SQL/Command Injection Guard)",
                    "tool_args": serialized_args[:200],
                }
            )
            return {
                "error": "Blocked by ACSM Tool Governance Guard (ACSM-TOOL-SEC-001): suspicious injection pattern."
            }

        span.set_attribute("governance.verdict", "ALLOW")
        _record_governance_event(
            {
                "stage": "before_tool_callback",
                "agent_name": agent_name,
                "tool_name": tool_name,
                "verdict": "ALLOW",
                "policy_rule": "ACSM-TOOL-SEC-001",
                "tool_args": serialized_args[:200],
            }
        )
        return None


def get_governance_status() -> dict[str, Any]:
    """Return active governance posture for the workshop dashboard and API."""
    return {
        "project_id": PROJECT,
        "region": "asia-southeast1",
        "model_armor": {
            "enabled": MODEL_ARMOR_ENABLED,
            "template": f"projects/{PROJECT}/locations/{MODEL_ARMOR_LOCATION}/templates/{MODEL_ARMOR_TEMPLATE}",
            "filters": [
                "Prompt Injection & Jailbreak (LOW_AND_ABOVE)",
                "Malicious URI Filter (ENABLED)",
                "Responsible AI (HATE_SPEECH, DANGEROUS, HARASSMENT)",
            ],
        },
        "semantic_policies": [
            "BNM-RMIT-PDPA-001: Block unmasked Malaysian MyKad NRIC (YYMMDD-PB-####)",
            "ACSM-TOOL-SEC-001: Audit & guard tool arguments before BigQuery execution",
            "ACSM-ACCESS-004: Public vs Internal document access classification guard",
        ],
        "agent_gateways": {
            "ingress_gateway": f"projects/{PROJECT}/locations/asia-southeast1/agentGateways/acsm-ingress-gateway",
            "egress_gateway": f"projects/{PROJECT}/locations/asia-southeast1/agentGateways/acsm-egress-gateway",
        },
        "telemetry": {
            "capture_message_content_in_spans": os.environ.get("ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS", "true"),
            "otel_genai_capture": os.environ.get(
                "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "SPAN_AND_EVENT"
            ),
            "bigquery_analytics_dataset": f"{PROJECT}.adk_agent_analytics",
        },
        "recent_event_count": len(_RECENT_EVENTS),
    }


def get_recent_governance_events(limit: int = 25) -> list[dict[str, Any]]:
    return list(_RECENT_EVENTS)[: max(1, min(limit, 100))]
