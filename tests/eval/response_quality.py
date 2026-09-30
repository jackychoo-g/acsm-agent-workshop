"""Local LLM-as-judge for `custom_response_quality` (see eval_config.yaml)."""

import os

from google import genai
from google.genai import types
from pydantic import BaseModel


class _Verdict(BaseModel):
    score: int  # 1-5
    explanation: str


def evaluate(instance):
    reference = instance.get("reference")
    rubric = (
        "Grade the agent's final response on a 1-5 scale (1 poor, 5 excellent) for "
        "accuracy, policy governance compliance, citation completeness, and language matching. "
        "Specifically penalize responses that: "
        "(a) fail to cite exact document IDs (e.g. POL-CR-001-v2, FEE-001, PDS-CARD-CLASSIC, CIR-03) and effective dates, "
        "(b) fail to explicitly mention superseded earlier policies or conflicting figures when specified in the Expected Answer, or "
        "(c) reply in English when the user asked in Bahasa Malaysia."
    )
    if reference:
        rubric += (
            " A score of 5 requires satisfying all facts, citations, conflict disclosures, "
            "and language requirements in the Expected Answer below."
        )
    prompt = (
        f"You are an expert QA evaluator for an enterprise AI assistant at AEON Credit Service Malaysia. {rubric}\n"
        f"User Prompt: {instance.get('prompt', '')}\n"
        f"Final Response: {instance.get('response', '')}\n"
    )
    if reference:
        prompt += f"Expected Answer (ground truth): {reference}\n"
    prompt += f"Full Agent Trace: {instance.get('agent_data', '')}\n"

    from app import config

    project_id = config.RUNTIME_PROJECT
    client = genai.Client(
        vertexai=True,
        project=project_id,
        location="global",
    )
    response = client.models.generate_content(
        model="gemini-3.8-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
            response_schema=_Verdict,
        ),
    )
    verdict = response.parsed
    if verdict is None:
        return {"score": 0, "explanation": response.text or ""}
    return {"score": max(1, min(5, verdict.score)), "explanation": verdict.explanation}
