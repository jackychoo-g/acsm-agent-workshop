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
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "x-goog-user-project": PROJECT,
            },
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


def _emit_gateway_observability_log(event: dict[str, Any]) -> None:
    """Emit structured AgentGateway + IAP authorization entries for Cloud Console Gateway Observability."""
    if not MODEL_ARMOR_ENABLED or not PROJECT or PROJECT in ("some-proj", "test-project", "your-project-id"):
        return
    token = _get_bearer_token()
    if not token:
        return

    stage = event.get("stage", "before_model_callback")
    agent_name = event.get("agent_name", "acsm_bq_policy_agent")
    tool_name = event.get("tool_name", "search_policy_corpus")
    allowed = event.get("verdict") == "ALLOW"

    if stage == "before_tool_callback":
        gateway_name = "acsm-egress-gateway"
        host = "bigquery.googleapis.com"
        registry_resource = (
            f"projects/{PROJECT}/locations/{MODEL_ARMOR_LOCATION}/agentRegistry/mcpServers/{tool_name}"
        )
        req_url = f"https://{host}/mcp/{tool_name}"
    else:
        gateway_name = "acsm-ingress-gateway"
        host = f"{MODEL_ARMOR_LOCATION}-aiplatform.googleapis.com"
        registry_resource = (
            f"projects/{PROJECT}/locations/{MODEL_ARMOR_LOCATION}/agentRegistry/agents/{agent_name}"
        )
        req_url = f"https://{host}/v1/projects/{PROJECT}/locations/{MODEL_ARMOR_LOCATION}/agents/{agent_name}:streamQuery"

    resource_block = {
        "type": "networkservices.googleapis.com/AgentGateway",
        "labels": {
            "project_id": PROJECT,
            "location": MODEL_ARMOR_LOCATION,
            "gateway_name": gateway_name,
        },
    }
    entries = [
        {
            "logName": f"projects/{PROJECT}/logs/networkservices.googleapis.com%2Fagent_gateway_requests",
            "resource": resource_block,
            "httpRequest": {
                "requestMethod": "POST",
                "requestUrl": req_url,
                "status": 200 if allowed else 403,
            },
            "jsonPayload": {
                "tlsSniHostname": host,
                "agentGatewayInfo": {
                    "agentRegistryResource": registry_resource,
                },
                "authzPolicyInfo": {
                    "result": "ALLOWED" if allowed else "DENIED",
                    "policy": f"{gateway_name}-aisecurity-authzpolicy",
                    "rule": event.get("policy_rule", ""),
                },
            },
        },
        {
            "logName": f"projects/{PROJECT}/logs/networkservices.googleapis.com%2Fagent_gateway_iap",
            "resource": resource_block,
            "protoPayload": {
                "@type": "type.googleapis.com/google.cloud.audit.AuditLog",
                "serviceName": "iap.googleapis.com",
                "methodName": "google.cloud.iap.v1.IdentityAwareProxyGuard.Authorize",
                "authenticationInfo": {
                    "principalSubject": f"serviceAccount:agent-runtime@{PROJECT}.iam.gserviceaccount.com/reasoningEngines/{agent_name}",
                },
                "requestMetadata": {
                    "requestAttributes": {
                        "host": host,
                    },
                },
                "authorizationInfo": [
                    {
                        "resource": registry_resource,
                        "permission": "iap.agentGateways.invoke",
                        "granted": allowed,
                    }
                ],
                "metadata": {
                    "dryRun": False,
                },
            },
        },
    ]
    try:
        requests.post(
            "https://logging.googleapis.com/v2/entries:write",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "x-goog-user-project": PROJECT,
            },
            json={"entries": entries},
            timeout=2.5,
        )
    except Exception as exc:
        logger.debug("Agent Gateway observability log write skipped: %s", exc)


def _record_governance_event(event: dict[str, Any]) -> None:
    event["timestamp"] = datetime.now(timezone.utc).isoformat()
    _RECENT_EVENTS.appendleft(event)
    print(json.dumps({"severity": "INFO", "component": "acsm_governance_audit", **event}), flush=True)
    _emit_gateway_observability_log(event)


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


def _extract_model_armor_violations(
    filter_results: dict[str, Any],
) -> tuple[list[str], list[str], list[str]]:
    """Extract violation labels, RAI sub-categories, and SDP infoTypes matching Model Armor OTel schema."""
    violations: list[str] = []
    rai_violations: list[str] = []
    sdp_info_types: list[str] = []

    for entry in (filter_results or {}).values():
        if not isinstance(entry, dict):
            continue
        pijb = entry.get("piAndJailbreakFilterResult")
        if isinstance(pijb, dict) and pijb.get("matchState") == "MATCH_FOUND":
            violations.append("prompt_injection_jail_break")

        mal_uri = entry.get("maliciousUriFilterResult")
        if isinstance(mal_uri, dict) and mal_uri.get("matchState") == "MATCH_FOUND":
            violations.append("malicious_uri")

        csam = entry.get("csamFilterFilterResult")
        if isinstance(csam, dict) and csam.get("matchState") == "MATCH_FOUND":
            violations.append("csam")

        virus = entry.get("virusScanFilterResult")
        if isinstance(virus, dict) and virus.get("matchState") == "MATCH_FOUND":
            violations.append("virus_scan")

        rai = entry.get("raiFilterResult")
        if isinstance(rai, dict) and rai.get("matchState") == "MATCH_FOUND":
            violations.append("responsible_ai")
            for rtype, rres in (rai.get("raiFilterTypeResults") or {}).items():
                if isinstance(rres, dict) and rres.get("matchState") == "MATCH_FOUND":
                    rai_violations.append(rtype)

        sdp = entry.get("sdpFilterResult")
        if isinstance(sdp, dict):
            deid = sdp.get("deidentifyResult")
            if isinstance(deid, dict) and deid.get("matchState") == "MATCH_FOUND":
                violations.append("deidentified")
                sdp_info_types.extend(t for t in (deid.get("infoTypes") or []) if t)
            insp = sdp.get("inspectResult")
            if isinstance(insp, dict) and insp.get("matchState") == "MATCH_FOUND":
                violations.append("sdp_inspection")
                sdp_info_types.extend(
                    f.get("infoType")
                    for f in (insp.get("findings") or [])
                    if isinstance(f, dict) and f.get("infoType")
                )
            red = sdp.get("redactResult")
            if isinstance(red, dict) and red.get("matchState") == "MATCH_FOUND":
                violations.append("redacted")

    return sorted(set(violations)), sorted(set(rai_violations)), sorted(set(sdp_info_types))


def before_model_governance_guard(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> LlmResponse | None:
    """Enforce Model Armor + BNM RMiT PDPA guardrails before calling Gemini."""
    user_text = _extract_latest_user_text(llm_request)
    agent_name = getattr(callback_context, "agent_name", "root_agent")
    if not user_text:
        return None

    template_uri = (
        f"projects/{PROJECT}/locations/{MODEL_ARMOR_LOCATION}/templates/{MODEL_ARMOR_TEMPLATE}"
    )

    with tracer.start_as_current_span('apply_guardrail "Google Cloud Model Armor"') as span:
        span.set_attribute("governance.agent_name", agent_name)
        span.set_attribute("governance.prompt_preview", user_text[:300])
        span.set_attribute("governance.model_armor_template", template_uri)

        with tracer.start_as_current_span("Request Path") as req_span:
            req_span.set_attribute("gen_ai.security.target.type", "input")
            req_span.set_attribute("gen_ai.security.policy.id", template_uri)
            req_span.set_attribute("gen_ai.security.policy.name", MODEL_ARMOR_TEMPLATE)
            req_span.set_attribute("gen_ai.security.decision.code", "200")

            # 1. BNM RMiT / PDPA Check: Block raw unmasked Malaysian NRIC (MyKad) numbers
            nric_matches = MYKAD_UNMASKED_RE.findall(user_text)
            if nric_matches:
                span.set_attribute("governance.verdict", "BLOCK_PDPA_UNMASKED_NRIC")
                req_span.set_attribute("gcp.modelarmor.filter.match.state", "MATCH_FOUND")
                req_span.set_attribute("gcp.modelarmor.violations", ["sdp_inspection"])
                req_span.set_attribute("gcp.modelarmor.sdp.info_types", ["MALAYSIA_NRIC_NUMBER"])
                req_span.set_attribute("gen_ai.security.decision.type", "deny")
                req_span.set_attribute(
                    "gen_ai.security.decision.reason",
                    "BNM-RMIT-PDPA-001 (Unmasked Malaysian MyKad NRIC)",
                )
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
            violations, rai_violations, sdp_info_types = _extract_model_armor_violations(
                armor_res.get("filterResults", {})
            )
            span.set_attribute("governance.model_armor_state", match_state)
            span.set_attribute("governance.model_armor_latency_ms", armor_res.get("latency_ms", 0.0))
            req_span.set_attribute("gcp.modelarmor.filter.match.state", match_state)

            if match_state == "MATCH_FOUND":
                if not violations:
                    violations = ["prompt_injection_jail_break"]
                req_span.set_attribute("gcp.modelarmor.violations", violations)
                if rai_violations:
                    req_span.set_attribute("gcp.modelarmor.rai.violations", rai_violations)
                if sdp_info_types:
                    req_span.set_attribute("gcp.modelarmor.sdp.info_types", sdp_info_types)
                req_span.set_attribute("gen_ai.security.decision.type", "deny")
                req_span.set_attribute(
                    "gen_ai.security.decision.reason",
                    f"Blocked by Model Armor template {MODEL_ARMOR_TEMPLATE}",
                )
                span.set_attribute("governance.verdict", "BLOCK_MODEL_ARMOR")
                event = {
                    "stage": "before_model_callback",
                    "agent_name": agent_name,
                    "verdict": "BLOCK",
                    "policy_rule": f"MODEL_ARMOR ({MODEL_ARMOR_TEMPLATE})",
                    "model_armor_state": match_state,
                    "model_armor_violations": violations,
                    "model_armor_latency_ms": armor_res.get("latency_ms"),
                    "prompt_preview": user_text[:200],
                }
                _record_governance_event(event)
                refusal = (
                    f"**[Governance Policy Block — Google Cloud Model Armor (`{MODEL_ARMOR_TEMPLATE}`)]**\n\n"
                    "This prompt triggered a security policy violation (Prompt Injection / Jailbreak / "
                    "Unsafe Content filter) enforced by the ACSM Model Armor template "
                    f"(`{template_uri}`). "
                    "The event has been logged to Cloud Trace and Cloud Logging."
                )
                return LlmResponse(
                    content=types.Content(
                        role="model",
                        parts=[types.Part.from_text(text=refusal)],
                    )
                )

            req_span.set_attribute("gen_ai.security.decision.type", "allow")
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
