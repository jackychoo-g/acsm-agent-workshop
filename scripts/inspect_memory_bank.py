"""Show the managed Sessions and Memory Bank facts stored on your deployed agent.

Usage: make memory [USER_ID=officer-<owner>]   (reads your agent ID from deployment_metadata.json)
"""

import asyncio
import json
import sys

import google.auth
from google.adk.memory.vertex_ai_memory_bank_service import VertexAiMemoryBankService
from google.adk.sessions.vertex_ai_session_service import VertexAiSessionService
from google.auth.transport.requests import AuthorizedSession

from app import config


async def main() -> None:
    default_user = f"officer-{config.OWNER}"
    user_id = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else default_user
    try:
        with open("deployment_metadata.json", encoding="utf-8") as f:
            rid = json.load(f)["remote_agent_runtime_id"]
    except FileNotFoundError:
        sys.exit("deployment_metadata.json not found. Run `make deploy` first.")
    parts = rid.split("/")
    project, location, engine_id = parts[1], parts[3], parts[-1]

    sessions = await VertexAiSessionService(
        project=project, location=location, agent_engine_id=engine_id
    ).list_sessions(app_name="app", user_id=user_id)
    memories = await VertexAiMemoryBankService(
        project=project, location=location, agent_engine_id=engine_id
    ).search_memory(
        app_name="app",
        user_id=user_id,
        query="officer name branch product applications dependants preferences",
    )

    print(f"Agent: {rid}")
    print(f"Sessions for '{user_id}' ({len(sessions.sessions)}): {[s.id for s in sessions.sessions]}")
    facts = [
        p.text
        for m in memories.memories
        for p in (m.content.parts if m.content and m.content.parts else [])
        if getattr(p, "text", None)
    ]
    print(f"Memory Bank recall for '{user_id}' ({len(facts)}):")
    for fact in facts:
        print(f"  - {fact}")

    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    http = AuthorizedSession(creds)
    url = f"https://{location}-aiplatform.googleapis.com/v1beta1/{rid}/memories"
    resp = http.get(url, timeout=30)
    if resp.status_code == 200:
        all_memories = resp.json().get("memories", [])
        print(f"Total persisted Memory Bank records on agent ({len(all_memories)}):")
        for item in all_memories:
            scope = item.get("scope", {})
            print(f"  - [{scope.get('user_id', '?')}] {item.get('fact', '')}")


if __name__ == "__main__":
    asyncio.run(main())
