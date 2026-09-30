"""Call a retrieval tool directly (no LLM) and print the citations it returns.

Shows that source links are data returned by retrieval, not text the model invents.
Usage: python -m scripts.try_search {bigquery|rag_engine} "<question>"
"""

import sys

from app.tools.policy_search import search_policy_corpus
from app.tools.rag_engine_search import search_rag_engine_corpus


def main() -> None:
    backend, query = sys.argv[1], " ".join(sys.argv[2:])
    tool = search_policy_corpus if backend == "bigquery" else search_rag_engine_corpus
    result = tool(query=query, top_k=3)
    print(f"backend={result['backend']}  matches={result['match_count']}  query={query!r}\n")
    for i, m in enumerate(result["matches"], 1):
        print(f"[{i}] {m['citation_markdown']}")
        text = (m.get("chunk_text") or m.get("text") or "").replace("\n", " ")
        print(f"    {text[:180]}...\n")


if __name__ == "__main__":
    main()
