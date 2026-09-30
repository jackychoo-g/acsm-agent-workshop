# Code walkthrough

Follow one question through the code: *"What is the minimum NDI floor for an applicant with 3 dependants?"* Each stop links to the lines that handle it. Line numbers are for the commit you cloned.

## Stop 0: settings

[`app/config.py`](app/config.py) is the only place settings come from. It reads environment variables first, then `.lab.env` (written by `make configure`), and never overrides a value that is already set. `make deploy` passes the same values to the deployed agent with `--update-env-vars`, so local and deployed runs read identical settings.

Worth noticing: the default prompt mode is `hillclimb` ([line 56](app/config.py#L56)). The `baseline` prompt exists so the eval targets have something worse to compare against.

## Stop 1: which agent answers

[`app/agent.py` lines 198–208](app/agent.py#L198-L208) pick the root agent from `ACSM_RAG_BACKEND` and wrap it in an `App` with the BigQuery analytics plugin.

- `bigquery` (default) → [`create_bq_rag_agent`](app/agent.py#L106-L126)
- `rag_engine` → [`create_rag_engine_agent`](app/agent.py#L129-L149), deployed as `acsm-agent-<you>-rag`

The two are identical apart from the retrieval tool. That's deliberate: you can compare BigQuery `VECTOR_SEARCH` and RAG Engine on the same questions with nothing else changing.

Model calls go to the `global` endpoint ([lines 33–35](app/agent.py#L33-L35)). BigQuery, RAG Engine, Memory Bank and Model Armor stay in `asia-southeast1`.

## Stop 2: governance runs before the model sees anything

Every agent registers two callbacks ([lines 123–124](app/agent.py#L123-L124)).

[`before_model_governance_guard`](app/governance/policy_guard.py#L125-L213) runs on each model call:

1. **MyKad/PDPA check.** An unmasked NRIC (`YYMMDD-PB-####`, [regex at line 36](app/governance/policy_guard.py#L36)) is refused without calling the model. The logged preview is masked.
2. **Model Armor.** The prompt is screened against the template in `.lab.env`. A match returns a refusal.
3. Either way a span `acsm.governance.evaluate_prompt` records the verdict, so blocks show up in Cloud Trace.

Returning an `LlmResponse` from a `before_model_callback` short-circuits the model call. That's the whole mechanism.

[`before_tool_governance_guard`](app/governance/policy_guard.py#L216-L258) checks tool arguments for injection patterns ([line 37](app/governance/policy_guard.py#L37)) before any tool runs.

> Model Armor fails open: if the agent can't get a token, the call is allowed and logged. For production you'd decide whether that is acceptable.

## Stop 3: memory is loaded

`preload_memory` is in the tool list ([line 120](app/agent.py#L120)). On each turn ADK fetches memories for the current user from Memory Bank and adds them to the prompt. On Agent Runtime the session and memory services are wired up by the platform; locally, [`app/app_utils/services.py`](app/app_utils/services.py#L27-L71) falls back to in-memory versions.

## Stop 4: retrieval

The model decides to call [`search_policy_corpus`](app/tools/policy_search.py#L64-L124):

1. Embed the question with `gemini-embedding-001`, task type `RETRIEVAL_QUERY` ([lines 84–92](app/tools/policy_search.py#L84-L92)).
2. Run `VECTOR_SEARCH` with cosine distance ([lines 96–112](app/tools/policy_search.py#L96-L112)). `include_internal=False` filters to public documents before the search, which is how the customer-facing guardrail works.
3. The query job runs in the workshop project; the table is fully qualified, so the data can live in another project.

The RAG Engine version is [`search_rag_engine_corpus`](app/tools/rag_engine_search.py#L42-L115). It calls `retrieveContexts` and maps the answer into the **same return shape**, so the agent prompt doesn't care which backend it has.

## Stop 5: the citation contract

[`_attach_citation`](app/tools/policy_search.py#L46-L61) turns each row into a `source_url` and a ready-made `citation_markdown` link. The prompt rule at [line 55](app/agent.py#L55) tells the model to paste those links verbatim under `### Sources`, so the model never builds a URL itself.

The `#page=N` anchor is added only when the page is actually known ([lines 36–43](app/tools/policy_search.py#L36-L43)). RAG Engine returns `pageSpan`. The BigQuery table has no `page` column yet, so BigQuery citations link to the document without a page anchor until the ingestion job adds one. An earlier version estimated pages from chunk position and was wrong for most PDF chunks, so it was removed.

`tests/contract/test_contract.py` checks this shape on both backends.

## Stop 6: identity decides access, not code

Ask `make chat-audit`. The model calls [`lookup_restricted_audit_log`](app/tools/policy_search.py#L127-L160). The code is a normal BigQuery query. It fails because the shared service account has no grant on the audit table; the policy table grant is table-level, not dataset-level, precisely so this boundary holds.

The tool returns the denial instead of raising ([lines 148–159](app/tools/policy_search.py#L148-L159)). An uncaught exception would abort the run and you'd get an empty reply.

Takeaway: to give an agent more data, you change IAM on its identity. You don't change the agent. `labs/02-agent-identity.md` shows the per-agent identity version.

## Stop 7: the turn is written to memory

[`_persist_session_to_memory`](app/agent.py#L77-L82) runs as `after_agent_callback`. Memory Bank extracts facts from the session in the background. Check with `make memory USER_ID=<id>` a minute later.

## Stop 8: audit trail

Three records exist for the same question:

| Where | What | How to see it |
|---|---|---|
| Cloud Trace | spans for the model call, each tool, and the governance verdicts, with prompt and response content | `make trace` |
| BigQuery `adk_agent_analytics.agent_events` | one row per event, tagged with `owner`, backend and prompt mode ([lines 177–182](app/agent.py#L177-L182)) | `make audit-logs` |
| Cloud Logging | runtime logs for your agent's resource ID | link from `make trace` |

Content capture in spans is switched on at [lines 36–39](app/agent.py#L36-L39). For a real bank deployment, review that choice against your data classification before enabling it.

## Stop 9: improving the prompt

`make eval-baseline` and `make eval-candidate` run the golden set in [`tests/eval/datasets/acsm_golden.json`](tests/eval/datasets/acsm_golden.json) against both prompt modes; `make eval-compare` shows the difference. `make hillclimb-gepa` searches for a better instruction automatically. The six rules at [lines 49–57](app/agent.py#L49-L57) are the result of that kind of iteration.
