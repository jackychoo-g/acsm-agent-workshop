"""Workshop contract. Both repos ship this file; repo A uses it as the acceptance gate.

Static checks run offline. Retrieval checks call the real tools with your ADC
credentials, so they prove the citation fields come from live data.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
APP = ROOT / "app"
FORBIDDEN_MODELS = re.compile(r"gemini-(1\.0|1\.5|2\.0|2\.5)|claude-3|gemma-2", re.I)


def _app_sources() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in APP.rglob("*.py"))


# ── static ──────────────────────────────────────────────────────────────────

def test_deploy_uses_shared_service_account_and_owner_name() -> None:
    mk = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "--service-account $(LAB_SA)" in mk
    assert "--service-name $(AGENT_NAME)" in mk
    assert "AGENT_NAME := acsm-agent-$(OWNER_SLUG)" in mk
    assert "--agent-identity" not in mk, "participants deploy with the shared SA, not per-agent identity"


def test_model_ids_are_current() -> None:
    from app import config

    assert config.MODEL == "gemini-3.8-flash"
    assert config.EMBED_MODEL == "gemini-embedding-001"
    assert not FORBIDDEN_MODELS.search(_app_sources())


# Project IDs, numbers and domains from past sessions. The lab must run in any
# project, so none of these may appear in a tracked file.
KNOWN_ENVIRONMENT_VALUES = re.compile(
    r"\b(acsm-ge|aeon-credit-demo|seven-eleven|13239371417)\b|altostrat", re.I
)


def _tracked_text_files() -> list[pathlib.Path]:
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True, check=True
        ).stdout.split("\n")
        paths = [ROOT / f for f in out if f]
    except (OSError, subprocess.CalledProcessError):
        paths = [p for p in ROOT.rglob("*") if ".git" not in p.parts and ".venv" not in p.parts]
    this_file = pathlib.Path(__file__).resolve()
    return [p for p in paths if p.is_file() and p.resolve() != this_file and p.name != "uv.lock"]


def test_no_known_project_ids_in_tracked_files() -> None:
    hits = []
    for p in _tracked_text_files():
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if KNOWN_ENVIRONMENT_VALUES.search(line):
                hits.append(f"{p.relative_to(ROOT)}:{n}")
    assert not hits, f"hardcoded project/environment values: {hits}"


def test_no_hardcoded_projects_or_links() -> None:
    src = _app_sources() + "\n" + "\n".join(
        p.read_text(encoding="utf-8") for p in (ROOT / "scripts").rglob("*.py")
    )
    mk = (ROOT / "Makefile").read_text(encoding="utf-8")
    for text in (src, mk):
        assert not re.search(r"projects/[a-z0-9-]+/locations/", text)
    # Links must be built from retrieval data, never pasted in as literals.
    assert not re.search(r"['\"]https://storage\.cloud\.google\.com/[a-z0-9-]+/", src)
    uv_lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert "airlock-proxy" not in uv_lock and "uplink.goog" not in uv_lock


def test_audit_denial_is_returned_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    from google.api_core.exceptions import Forbidden

    from app.tools import policy_search

    class _Denied:
        def query(self, *a, **k):
            raise Forbidden("Access Denied: Table collections_internal_audit")

    monkeypatch.setattr(policy_search, "_bq", lambda: _Denied())
    out = policy_search.lookup_restricted_audit_log("Penang")
    assert out["status"] == "PERMISSION_DENIED"
    assert "collections_internal_audit" in out["table"]


# ── live retrieval (needs gcloud auth + make configure) ─────────────────────

def _check_citations(result: dict) -> None:
    assert result["match_count"] > 0, "retrieval returned no matches"
    for m in result["matches"]:
        assert m["source_url"].startswith("https://storage.cloud.google.com/"), m["source_url"]
        assert m["source_url"] in m["citation_markdown"], "citation_markdown must link to source_url"
        assert m.get("doc_id"), "every match needs a doc_id"


@pytest.mark.live
def test_bigquery_tool_returns_citations() -> None:
    from app.tools.policy_search import search_policy_corpus

    _check_citations(search_policy_corpus("minimum NDI floor for 3 dependants", top_k=3))
