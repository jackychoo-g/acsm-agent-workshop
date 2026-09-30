"""List (or delete) the Agent Runtime agent that belongs to you.

Agents are matched by display name, which is how agents-cli decides between
create and update. Usage:
  python -m scripts.agent_status acsm-agent-<owner>            # show
  python -m scripts.agent_status acsm-agent-<owner> --delete   # delete (Makefile asks for CONFIRM=yes)
"""

import sys

import google.auth
from google.auth.transport.requests import AuthorizedSession

from app import config


def main() -> None:
    name = sys.argv[1]
    delete = "--delete" in sys.argv
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    http = AuthorizedSession(creds)
    base = f"https://{config.REGION}-aiplatform.googleapis.com/v1beta1"
    url = f"{base}/projects/{config.RUNTIME_PROJECT}/locations/{config.REGION}/reasoningEngines"

    mine, token = [], None
    while True:
        params = {"pageSize": 100, "filter": f'display_name="{name}"'}
        if token:
            params["pageToken"] = token
        r = http.get(url, params=params, timeout=30)
        r.raise_for_status()
        body = r.json()
        mine += [e for e in body.get("reasoningEngines", []) if e.get("displayName") == name]
        token = body.get("nextPageToken")
        if not token:
            break

    if not mine:
        print(f"No agent named {name} in {config.RUNTIME_PROJECT}/{config.REGION}.")
        print("If a deploy timed out, run `make deploy` again.")
        return
    if len(mine) > 1:
        print(f"WARNING: {len(mine)} agents share the name {name}. agents-cli updates the first one it finds.")

    for e in mine:
        spec = e.get("spec", {})
        print(f"name        : {e.get('displayName')}")
        print(f"id          : {e['name']}")
        print(f"created     : {e.get('createTime')}")
        print(f"updated     : {e.get('updateTime')}")
        print(f"runtime SA  : {spec.get('serviceAccount') or '(default service agent)'}")
        print()

    if delete:
        import time

        from scripts.platform_integrations import cleanup_integrations

        if len(mine) > 1:
            sys.exit("Refusing to delete: more than one agent has this name. Ask the instructor.")
        cleanup_integrations(name, mine[0]["name"])
        for attempt in range(1, 6):
            r = http.delete(f"{base}/{mine[0]['name']}", params={"force": "true"}, timeout=60)
            if r.status_code in (400, 409) and attempt < 5:
                print(f"Waiting for background session/memory operation to settle before delete (attempt {attempt}/5)...")
                time.sleep(10)
                continue
            r.raise_for_status()
            print(f"Delete requested for {mine[0]['name']} (operation {r.json().get('name', '?')}).")
            break


if __name__ == "__main__":
    main()
