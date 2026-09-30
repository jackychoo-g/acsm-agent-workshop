"""Show the managed Sessions and Memory Bank facts stored for one user on your agent.

Usage: make memory USER_ID=<id>   (reads your agent ID from deployment_metadata.json)
"""

import asyncio
import json
import sys

from google.adk.memory.vertex_ai_memory_bank_service import VertexAiMemoryBankService
from google.adk.sessions.vertex_ai_session_service import VertexAiSessionService


async def main() -> None:
    user_id = sys.argv[1] if len(sys.argv) > 1 else "workshop-user"
    try:
        with open("deployment_metadata.json", encoding="utf-8") as f:
            rid = json.load(f)["remote_agent_runtime_id"]
    except FileNotFoundError:
        sys.exit("deployment_metadata.json not found. Run `make deploy` first.")
    # projects/<num>/locations/<region>/reasoningEngines/<id>
    parts = rid.split("/")
    project, location, engine_id = parts[1], parts[3], parts[-1]

    sessions = await VertexAiSessionService(
        project=project, location=location, agent_engine_id=engine_id
    ).list_sessions(app_name="app", user_id=user_id)
    memories = await VertexAiMemoryBankService(
        project=project, location=location, agent_engine_id=engine_id
    ).search_memory(app_name="app", user_id=user_id, query="income branch product dependants")

    print(f"Agent: {rid}")
    print(f"Sessions for '{user_id}': {[s.id for s in sessions.sessions]}")
    facts = [
        p.text
        for m in memories.memories
        for p in (m.content.parts if m.content and m.content.parts else [])
        if getattr(p, "text", None)
    ]
    print(f"Memory Bank facts for '{user_id}' ({len(facts)}):")
    for fact in facts:
        print(f"  - {fact}")


if __name__ == "__main__":
    asyncio.run(main())
