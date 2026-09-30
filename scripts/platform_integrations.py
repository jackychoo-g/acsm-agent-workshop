"""Platform integrations for the deployed Agent Runtime agent:
1. register-apphub   : Register the ReasoningEngine discovered workload in App Hub (Topology tab).
2. submit-eval       : Create an EvaluationExperiment + EvaluationRun linked to the Agent Engine (Evaluation tab).
3. cleanup           : Remove App Hub workload and Gemini Enterprise registration on `make cleanup`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import google.auth
from google.auth.transport.requests import AuthorizedSession

from app import config


def _http_session() -> AuthorizedSession:
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return AuthorizedSession(creds)


def _read_metadata() -> tuple[str, str, str, str]:
    meta_path = Path("deployment_metadata.json")
    if not meta_path.exists():
        sys.exit("deployment_metadata.json not found. Run `make deploy` first.")
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    rid = data["remote_agent_runtime_id"]
    parts = rid.split("/")
    return rid, parts[1], parts[3], parts[-1]


def register_apphub(agent_name: str) -> None:
    rid, project_num, region, engine_id = _read_metadata()
    project_id = config.RUNTIME_PROJECT
    app_id = os.getenv("ACSM_APPHUB_APP_ID", "acsm-workshop-app")
    http = _http_session()
    base = f"https://apphub.googleapis.com/v1/projects/{project_id}/locations/{region}"

    # 1. Ensure project is attached as an App Hub service project and regional application exists
    sp_url = f"https://apphub.googleapis.com/v1/projects/{project_id}/locations/global/serviceProjectAttachments/{project_id}"
    if http.get(sp_url, timeout=30).status_code == 404:
        http.post(
            f"https://apphub.googleapis.com/v1/projects/{project_id}/locations/global/serviceProjectAttachments",
            params={"serviceProjectAttachmentId": project_id},
            json={"serviceProject": f"projects/{project_id}"},
            timeout=60,
        )
        for _ in range(10):
            if http.get(sp_url, timeout=30).status_code == 200:
                break
            time.sleep(2)

    app_url = f"{base}/applications/{app_id}"
    r = http.get(app_url, timeout=30)
    if r.status_code == 404:
        print(f"Creating App Hub application '{app_id}' in {project_id}/{region}...")
        create_resp = http.post(
            f"{base}/applications",
            params={"applicationId": app_id},
            json={
                "displayName": "ACSM Workshop Agent Platform",
                "description": "App Hub application grouping ACSM workshop Agent Runtime workloads for Topology visualization.",
                "scope": {"type": "REGIONAL"},
                "attributes": {
                    "environment": {"type": "DEVELOPMENT"},
                    "criticality": {"type": "MEDIUM"},
                },
            },
            timeout=60,
        )
        if create_resp.status_code not in (200, 409):
            print(f"App Hub application creation returned {create_resp.status_code}: {create_resp.text}")
            return
        for _ in range(12):
            if http.get(app_url, timeout=30).status_code == 200:
                break
            time.sleep(3)

    # 2. Look up the DiscoveredWorkload for this ReasoningEngine
    cais_uri = f"//aiplatform.googleapis.com/projects/{project_num}/locations/{region}/reasoningEngines/{engine_id}"
    discovered_name = None
    for attempt in range(1, 7):
        lookup = http.get(
            f"{base}/discoveredWorkloads:lookup",
            params={"uri": cais_uri},
            timeout=30,
        )
        if lookup.status_code == 200 and lookup.json().get("discoveredWorkload", {}).get("name"):
            discovered_name = lookup.json()["discoveredWorkload"]["name"]
            break
        if attempt < 6:
            print(f"Waiting for App Hub discovery of {engine_id} (attempt {attempt}/6)...")
            time.sleep(5)

    if not discovered_name:
        print(f"Note: App Hub has not yet indexed {cais_uri}. You can re-run `make register-apphub` in a minute.")
        return

    # 3. Register workload under the App Hub application
    workload_id = f"{agent_name}-{engine_id[-6:]}"
    workloads_url = f"{app_url}/workloads"
    existing = http.get(workloads_url, timeout=30).json().get("workloads", [])
    for w in existing:
        if w.get("discoveredWorkload") == discovered_name or w.get("name", "").endswith(f"/{workload_id}"):
            print(f"App Hub Topology workload already registered: {w['name']}")
            return

    reg = http.post(
        workloads_url,
        params={"workloadId": workload_id},
        json={
            "displayName": agent_name,
            "description": f"ACSM Agent Runtime workload ({agent_name})",
            "discoveredWorkload": discovered_name,
            "attributes": {
                "environment": {"type": "DEVELOPMENT"},
                "criticality": {"type": "MEDIUM"},
            },
        },
        timeout=60,
    )
    if reg.status_code in (200, 409):
        print(f"Registered workload '{workload_id}' in App Hub application '{app_id}' (Topology tab ready).")
    else:
        print(f"App Hub workload registration returned {reg.status_code}: {reg.text}")


def submit_cloud_eval(agent_name: str, dataset_path: str) -> None:
    rid, _, region, engine_id = _read_metadata()
    project_id = config.RUNTIME_PROJECT
    dest = f"gs://{config.RAG_BUCKET}/eval-runs/{config.OWNER}"
    labels = {
        "vertex-ai-evaluation-agent-engine-id": engine_id,
        "vertex-ai-evaluation-agent-engine-location": region,
    }

    # Execute inside the agents-cli Python environment where agentplatform._genai is installed
    agents_cli_bin = os.getenv("AGENTS_CLI") or "agents-cli"
    cli_path = Path(agents_cli_bin).resolve() if Path(agents_cli_bin).exists() else None
    python_bin = str(cli_path.parent / "python") if cli_path and (cli_path.parent / "python").exists() else "/opt/uv-tools/google-agents-cli/bin/python"
    if not Path(python_bin).exists():
        python_bin = sys.executable

    code = """
import json, sys
from agentplatform._genai.types.common import EvaluationDataset
from google.agents.cli._agent_platform import AgentPlatformClient
from google.agents.cli._output import Console
from google.agents.cli.eval.eval_utils import prepare_eval_metrics

project_id, rid, engine_id, region, agent_name, dataset_path, dest = sys.argv[1:8]
labels = {
    "vertex-ai-evaluation-agent-engine-id": engine_id,
    "vertex-ai-evaluation-agent-engine-location": region,
}
console = Console()
client_metrics, _, _ = prepare_eval_metrics(
    config_path=None,
    metrics_str="final_response_quality",
    default_metrics=["final_response_quality"],
    console=console,
)
with open(dataset_path, encoding="utf-8") as f:
    ds = EvaluationDataset.model_validate_json(f.read())

client = AgentPlatformClient(project=project_id, location="global")
exp = client.evals.create_evaluation_experiment(
    display_name=f"{agent_name}-golden-eval",
    labels=labels,
)
run_res = client.evals.create_evaluation_run(
    display_name=f"{agent_name}-golden-run",
    evaluation_experiment=exp.name,
    dataset=ds,
    dest=dest,
    metrics=client_metrics,
    agent=rid,
    labels=labels,
    config={"allow_cross_region_model": True},
)
print(f"Evaluation Experiment : {exp.name}")
print(f"Evaluation Run        : {getattr(run_res, 'name', 'submitted')}")
print(f"Console Evaluation URL: https://console.cloud.google.com/vertex-ai/agents/agent-engines/locations/{region}/agent-engines/{engine_id}/evaluation?project={project_id}")
"""
    subprocess.run(
        [python_bin, "-c", code, project_id, rid, engine_id, region, agent_name, dataset_path, dest],
        check=True,
    )


def resolve_ge_app() -> None:
    _, project_num, _, _ = _read_metadata()
    http = _http_session()
    ge_base = f"https://discoveryengine.googleapis.com/v1alpha/projects/{project_num}/locations/global/collections/default_collection/engines"
    r = http.get(ge_base, timeout=30)
    if r.status_code == 200:
        engines = r.json().get("engines", [])
        if engines:
            engine_id = engines[0]["name"].split("/")[-1]
            print(f"projects/{project_num}/locations/global/collections/default_collection/engines/{engine_id}")


def cleanup_integrations(agent_name: str, engine_uri: str | None = None) -> None:
    project_id = config.RUNTIME_PROJECT
    project_num = engine_uri.split("/")[1] if engine_uri and "/" in engine_uri else project_id
    region = config.REGION
    app_id = os.getenv("ACSM_APPHUB_APP_ID", "acsm-workshop-app")
    http = _http_session()

    # 1. Remove matching App Hub workloads
    workloads_url = f"https://apphub.googleapis.com/v1/projects/{project_id}/locations/{region}/applications/{app_id}/workloads"
    r = http.get(workloads_url, timeout=30)
    if r.status_code == 200:
        for w in r.json().get("workloads", []):
            w_name = w.get("name", "")
            disp = w.get("displayName", "")
            disc = w.get("discoveredWorkload", "")
            if disp == agent_name or w_name.split("/")[-1].startswith(f"{agent_name}-") or (engine_uri and engine_uri in disc):
                del_r = http.delete(f"https://apphub.googleapis.com/v1/{w_name}", timeout=30)
                if del_r.status_code in (200, 204):
                    print(f"Removed App Hub workload: {w_name}")

    # 2. Remove matching Gemini Enterprise agent registrations
    ge_base = f"https://discoveryengine.googleapis.com/v1alpha/projects/{project_num}/locations/global/collections/default_collection/engines"
    eng_r = http.get(ge_base, timeout=30)
    if eng_r.status_code == 200:
        for eng in eng_r.json().get("engines", []):
            agents_url = f"https://discoveryengine.googleapis.com/v1alpha/{eng['name']}/assistants/default_assistant/agents"
            ag_r = http.get(agents_url, timeout=30)
            if ag_r.status_code == 200:
                for ag in ag_r.json().get("agents", []):
                    re_name = (
                        ag.get("adkAgentDefinition", {})
                        .get("provisionedReasoningEngine", {})
                        .get("reasoningEngine", "")
                    )
                    if ag.get("displayName") == agent_name or (engine_uri and re_name == engine_uri):
                        del_ag = http.delete(f"https://discoveryengine.googleapis.com/v1alpha/{ag['name']}", timeout=30)
                        if del_ag.status_code in (200, 204):
                            print(f"Unregistered agent from Gemini Enterprise: {ag['name']}")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "register-apphub":
        register_apphub(sys.argv[2])
    elif cmd == "submit-eval":
        submit_cloud_eval(sys.argv[2], sys.argv[3])
    elif cmd == "resolve-ge-app":
        resolve_ge_app()
    elif cmd == "cleanup":
        cleanup_integrations(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    else:
        sys.exit("Usage: python -m scripts.platform_integrations [register-apphub|submit-eval|resolve-ge-app|cleanup] ...")


if __name__ == "__main__":
    main()
