"""RAG Engine retrieval tool. Same citation contract as the BigQuery tool.

RAG Engine returns the source file URI and the page span of each chunk, so
the `#page=N` anchor here comes straight from the service.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

import google.auth
from google.auth.transport.requests import AuthorizedSession

from app import config

_MANIFEST_PATH = Path(__file__).resolve().parent.parent / "data" / "doc_manifest.json"


@functools.cache
def _manifest() -> dict[str, dict[str, Any]]:
    # Maps "<family>/<file>" to document metadata (doc_id, title, version,
    # effective_date). Project-independent: URLs are built from config.RAG_BUCKET.
    with open(_MANIFEST_PATH, encoding="utf-8") as f:
        return json.load(f)["documents"]


@functools.cache
def _session() -> AuthorizedSession:
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return AuthorizedSession(creds)


def _rel_path(source_uri: str) -> str:
    # gs://<bucket>/rag-engine/<family>/<file>  ->  <family>/<file>
    marker = "/rag-engine/"
    return source_uri.split(marker, 1)[1] if marker in source_uri else Path(source_uri).name


def search_rag_engine_corpus(query: str, top_k: int = 5) -> dict[str, Any]:
    """Semantic search over the managed RAG Engine corpus of AEON Credit policy documents.

    Always call this tool before answering questions about underwriting rules,
    product disclosures, fees, SOPs, or circulars. Returns matching chunks with
    doc_id, version, effective_date, page span and clickable `source_url` /
    `citation_markdown` links.

    Args:
        query: Natural-language policy question or topic to search.
        top_k: Number of chunks to retrieve (default 5, max 10).
    """
    if not config.RAG_CORPUS_NAME:
        raise RuntimeError("ACSM_RAG_CORPUS_NAME is not set. Run `make whoami` to check your settings.")
    top_k = max(1, min(int(top_k), 10))
    url = (
        f"https://{config.REGION}-aiplatform.googleapis.com/v1/"
        f"projects/{config.DATA_PROJECT}/locations/{config.REGION}:retrieveContexts"
    )
    payload = {
        "vertexRagStore": {"ragResources": [{"ragCorpus": config.RAG_CORPUS_NAME}]},
        "query": {
            "text": query,
            "ragRetrievalConfig": {"topK": top_k, "filter": {"vectorDistanceThreshold": 0.65}},
        },
    }
    resp = _session().post(url, json=payload, timeout=30)
    resp.raise_for_status()

    matches: list[dict[str, Any]] = []
    for ctx in resp.json().get("contexts", {}).get("contexts", []):
        source_uri = ctx.get("sourceUri", "")
        rel = _rel_path(source_uri)
        doc = _manifest().get(rel, {})
        raw_path = doc.get("raw_file_path") or rel
        chunk = ctx.get("chunk", {})
        span = chunk.get("pageSpan", {})
        first_page = span.get("firstPage")
        is_pdf = raw_path.endswith(".pdf")
        anchor = f"#page={first_page}" if is_pdf and first_page else ""
        source_url = f"https://storage.cloud.google.com/{config.RAG_BUCKET}/raw/{raw_path}{anchor}"
        doc_id = doc.get("doc_id") or Path(rel).stem
        title = doc.get("title") or Path(rel).name
        version = doc.get("version", "")
        eff = doc.get("effective_date", "")
        page_label = f", p.{first_page}" if is_pdf and first_page else ""
        distance = ctx.get("distance") or ctx.get("score")
        matches.append(
            {
                "doc_id": doc_id,
                "title": title,
                "family": doc.get("family"),
                "version": version,
                "effective_date": eff,
                "access": doc.get("access"),
                "language": doc.get("language"),
                "file_path": raw_path,
                "page_start": first_page,
                "page_end": span.get("lastPage"),
                "chunk_id": chunk.get("chunkId"),
                "distance": round(float(distance), 4) if distance is not None else None,
                "source_uri": source_uri,
                "source_url": source_url,
                "citation_markdown": f"[{doc_id}: {title} (v{version}, eff. {eff}){page_label}]({source_url})",
                "text": ctx.get("text") or chunk.get("text", ""),
            }
        )
    return {
        "backend": "rag_engine",
        "rag_corpus": config.RAG_CORPUS_NAME,
        "query": query,
        "match_count": len(matches),
        "matches": matches,
    }
