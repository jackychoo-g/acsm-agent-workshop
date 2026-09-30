"""Call the retrieval tool directly (no LLM) and print the citations it returns.

Shows that source links are data returned by retrieval, not text the model invents.
Usage: python -m scripts.try_search "<question>"
"""

import sys

from app.tools.policy_search import search_policy_corpus


def main() -> None:
    query = " ".join(sys.argv[1:])
    if not query:
        sys.exit('Usage: python -m scripts.try_search "<question>"')
    result = search_policy_corpus(query=query, top_k=3)
    print(f"backend={result['backend']}  matches={result['match_count']}  query={query!r}\n")
    for i, m in enumerate(result["matches"], 1):
        print(f"[{i}] {m['citation_markdown']}")
        text = (m.get("chunk_text") or m.get("text") or "").replace("\n", " ")
        print(f"    {text[:180]}...\n")


if __name__ == "__main__":
    main()
