"""Per-task acceptance checks for the 4 build tasks in SPEC.md."""

from __future__ import annotations

import asyncio
import json
import pathlib

import pytest
from google.adk.models.llm_request import LlmRequest
from google.genai import types

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.mark.live
def test_task1_bigquery_retrieval_and_agent_tool() -> None:
    from app.agent import create_bq_rag_agent
    from app.tools.policy_search import search_policy_corpus

    agent = create_bq_rag_agent("test_bq")
    tool_names = [getattr(t, "name", getattr(t, "__name__", str(t))) for t in agent.tools]
    assert "search_policy_corpus" in tool_names, "Task 1: add search_policy_corpus to create_bq_rag_agent tools"

    res = search_policy_corpus("minimum NDI floor for 3 dependants", top_k=3)
    assert res["backend"] == "bigquery_vector_search"
    assert res["match_count"] > 0
    for m in res["matches"]:
        assert m["source_url"].startswith("https://storage.cloud.google.com/")
        assert m["source_url"] in m["citation_markdown"]
        assert m.get("doc_id")


def test_task2_memory_bank_wired() -> None:
    from app.agent import (
        _persist_session_to_memory,
        create_bq_rag_agent,
        create_rag_engine_agent,
    )

    for factory in (create_bq_rag_agent, create_rag_engine_agent):
        agent = factory("test_mem")
        tool_names = [getattr(t, "name", getattr(t, "__name__", str(t))) for t in agent.tools]
        assert any("preload_memory" in n for n in tool_names), (
            f"Task 2: add preload_memory to {factory.__name__} tools"
        )
        assert agent.after_agent_callback is _persist_session_to_memory, (
            f"Task 2: set after_agent_callback=_persist_session_to_memory on {factory.__name__}"
        )

    called = {"count": 0}

    class _DummyCtx:
        async def add_session_to_memory(self) -> None:
            called["count"] += 1

    asyncio.run(_persist_session_to_memory(_DummyCtx()))  # type: ignore[arg-type]
    assert called["count"] == 1, "Task 2: _persist_session_to_memory must await callback_context.add_session_to_memory()"


def test_task3_governance_callbacks_wired() -> None:
    from app.agent import (
        _build_audit_subagent,
        create_bq_rag_agent,
        create_rag_engine_agent,
    )
    from app.governance.policy_guard import (
        before_model_governance_guard,
        before_tool_governance_guard,
    )

    for agent in (
        create_bq_rag_agent("test_gov_bq"),
        create_rag_engine_agent("test_gov_rag"),
        _build_audit_subagent("test"),
    ):
        assert agent.before_model_callback is before_model_governance_guard, (
            f"Task 3: set before_model_callback=before_model_governance_guard on {agent.name}"
        )
        assert agent.before_tool_callback is before_tool_governance_guard, (
            f"Task 3: set before_tool_callback=before_tool_governance_guard on {agent.name}"
        )

    class _DummyCtx:
        agent_name = "test_gov_bq"

    req = LlmRequest(
        contents=[
            types.Content(
                role="user",
                parts=[types.Part.from_text(text="Check eligibility for NRIC 880512-14-5678")],
            )
        ]
    )
    resp = before_model_governance_guard(_DummyCtx(), req)  # type: ignore[arg-type]
    assert resp is not None, "Governance guard must block unmasked MyKad NRIC"
    assert "BNM-RMIT-PDPA-001" in (resp.content.parts[0].text or "")


def test_task4_golden_dataset_has_at_least_four_cases() -> None:
    p = ROOT / "tests" / "eval" / "datasets" / "acsm_golden.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    cases = data.get("eval_cases", [])
    assert len(cases) >= 4, f"Task 4: add at least 1 new case to acsm_golden.json (found {len(cases)})"
    ids = set()
    for c in cases:
        cid = c.get("eval_case_id", "")
        assert cid, "Every eval_case must have a non-empty eval_case_id"
        assert cid not in ids, f"Duplicate eval_case_id: {cid}"
        ids.add(cid)
        user_text = c.get("prompt", {}).get("parts", [{}])[0].get("text", "")
        ref_text = c.get("reference", {}).get("response", {}).get("parts", [{}])[0].get("text", "")
        assert len(user_text) >= 10, f"Case {cid} needs a user prompt text"
        assert len(ref_text) >= 20, f"Case {cid} needs a reference response text"
