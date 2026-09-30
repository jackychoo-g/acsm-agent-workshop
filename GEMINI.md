# AGENTS.md

Instructions for any coding agent (Antigravity, Gemini Code Assist, Gemini CLI) opened in this repo. This repo is the complete reference build. Help the participant read, run and explain it. Don't redesign it.

## Before any deploy

1. Ask the participant for their name. Don't guess it from the gcloud account.
2. Deploy only with `make deploy OWNER=<name>`. That produces the agent `acsm-agent-<name>` running as the shared service account. Never call `agents-cli deploy` directly and never drop `--service-account` or `--service-name`.
3. Run `make whoami` and show the output so the participant can confirm project, owner and agent name.

## Hard limits

- Don't run Terraform, `gcloud projects add-iam-policy-binding`, `gcloud iam ...`, or anything else that changes IAM, APIs or org policy. The instructor owns the project.
- Don't create, update or delete an agent whose name is not `acsm-agent-<this participant>`. `make cleanup CONFIRM=yes` only deletes the participant's own agent.
- Models: `gemini-3.8-flash` for generation, `gemini-embedding-001` for embeddings. No other model IDs.
- Region: `asia-southeast1`. Model Armor also runs in `asia-southeast1`.
- No project IDs, bucket names, emails or keys in code or commits. Settings come from `app/config.py`, which reads the environment and `.lab.env`.

## Contracts to keep

- `search_policy_corpus` returns `{"backend": str, "match_count": int, "matches": [...]}`. Every match carries `doc_id`, `title`, `version`, `effective_date`, `source_url` and `citation_markdown`. The agent cites with `citation_markdown` so the link is clickable. `tests/contract/test_contract.py` checks this.
- `source_url` only gets a `#page=N` anchor when the page is known (a real `page` column in BigQuery). Never estimate a page.
- `lookup_restricted_audit_log` must return `{"status": "PERMISSION_DENIED", ...}` instead of raising. The denial is the point of the demo.
- Governance runs as `before_model_callback` and `before_tool_callback` on every agent in `app/agent.py`.

## Useful commands

`make help` lists everything. For explanations, point to `CODE_WALKTHROUGH.md` and open the linked lines.
