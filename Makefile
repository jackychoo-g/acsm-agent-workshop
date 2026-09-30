# ACSM agent workshop: guided deploy + exploration
#
# First time:   make bootstrap && make configure && make whoami
# Deploy:       make deploy            (agent name: acsm-agent-<your name>)
# Talk to it:   make chat Q="What is the NDI floor for 3 dependants?"
#
# Shared workshop values (project, bucket) are not stored in this public
# repo. `make configure` reads them from Secret Manager into .lab.env (gitignored).

SHELL := /bin/bash
-include .lab.env

PROJECT      ?= $(shell gcloud config get-value project 2>/dev/null)
REGION       ?= asia-southeast1
DATA_PROJECT ?= $(PROJECT)
LAB_SA       ?= acsm-lab-agent@$(PROJECT).iam.gserviceaccount.com
RAG_BUCKET   ?= $(DATA_PROJECT)-acsm-rag-corpus
MODEL_ARMOR_LOCATION ?= $(REGION)
MODEL_ARMOR_TEMPLATE ?= acsm-credit-armor
AGENTS_CLI   ?= $(shell PATH="$(HOME)/.local/bin:$$PATH" command -v agents-cli 2>/dev/null || echo $(HOME)/.local/bin/agents-cli)
DEPLOY_TIMEOUT ?= 900
CONFIG_SECRET  ?= acsm-lab-config

# Participant name -> agent name. `make deploy` requires `OWNER=<name>` explicitly.
ACCOUNT := $(shell gcloud config get-value account 2>/dev/null)
OWNER   ?= $(ACSM_OWNER)
OWNER_DEFAULT := $(shell echo "$(ACCOUNT)" | cut -d@ -f1)
OWNER_EFFECTIVE := $(if $(OWNER),$(OWNER),$(OWNER_DEFAULT))
OWNER_SLUG := $(shell echo "$(OWNER_EFFECTIVE)" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9-]+/-/g; s/^-+//; s/-+$$//' | cut -c1-30)
AGENT_NAME := acsm-agent-$(OWNER_SLUG)

ENV_VARS := ACSM_PROJECT=$(PROJECT),ACSM_DATA_PROJECT=$(DATA_PROJECT),ACSM_REGION=$(REGION),ACSM_OWNER=$(OWNER_SLUG),ACSM_RAG_BUCKET=$(RAG_BUCKET),ACSM_ENABLE_MODEL_ARMOR=true,ACSM_MODEL_ARMOR_LOCATION=$(MODEL_ARMOR_LOCATION),ACSM_MODEL_ARMOR_TEMPLATE=$(MODEL_ARMOR_TEMPLATE),LOGS_BUCKET_NAME=$(RAG_BUCKET),GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=true,ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=true,OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=SPAN_AND_EVENT,OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental,OTEL_INSTRUMENTATION_GENAI_UPLOAD_FORMAT=jsonl,OTEL_INSTRUMENTATION_GENAI_COMPLETION_HOOK=upload,OTEL_INSTRUMENTATION_GENAI_UPLOAD_BASE_PATH=gs://$(RAG_BUCKET)/completions,BQ_ANALYTICS_DATASET_ID=adk_agent_analytics

# Local runs read the same settings as the deployed agent.
LOCAL_ENV := ACSM_PROJECT=$(PROJECT) ACSM_DATA_PROJECT=$(DATA_PROJECT) ACSM_REGION=$(REGION) ACSM_OWNER=$(OWNER_SLUG) ACSM_RAG_BUCKET=$(RAG_BUCKET) ACSM_MODEL_ARMOR_LOCATION=$(MODEL_ARMOR_LOCATION) ACSM_MODEL_ARMOR_TEMPLATE=$(MODEL_ARMOR_TEMPLATE) GOOGLE_CLOUD_PROJECT=$(PROJECT)

.PHONY: help bootstrap configure whoami check search test-contract verify \
        check-task1 check-task2 check-task3 check-task4 \
        local-chat playground deploy register-apphub ensure-gateways publish-ge status chat chat-audit \
        trace memory memory-demo audit-logs cleanup destroy \
        eval-baseline eval-candidate eval-compare eval-cloud hillclimb-gepa test-governance

help:
	@echo "Setup:    bootstrap  configure  whoami"
	@echo "Build:    check-task1  check-task2  check-task3  check-task4  verify"
	@echo "Local:    local-chat Q=\"...\"  playground"
	@echo "Explore:  search  test-contract  test-governance"
	@echo "Deploy:   deploy OWNER=<name>  register-apphub  ensure-gateways  publish-ge  status"
	@echo "Interact: chat Q=\"...\"  chat-audit  memory-demo  memory  trace  audit-logs"
	@echo "Evaluate: eval-baseline  eval-candidate  eval-compare  eval-cloud  hillclimb-gepa"
	@echo "Finish:   cleanup (or destroy) OWNER=<name> CONFIRM=yes"

bootstrap:
	@./bootstrap.sh

configure:
	@test -n "$(PROJECT)" || { echo "No project set. Run: gcloud config set project <workshop-project-id>"; exit 1; }
	@gcloud secrets versions access latest --secret=$(CONFIG_SECRET) --project=$(PROJECT) > .lab.env.tmp \
	  && mv .lab.env.tmp .lab.env && echo "Wrote .lab.env from secret $(CONFIG_SECRET) in $(PROJECT)" \
	  || { rm -f .lab.env.tmp; echo "Could not read secret $(CONFIG_SECRET). Ask the instructor to check your access."; exit 1; }
	@gcloud auth application-default set-quota-project $(PROJECT) >/dev/null 2>&1 || true

whoami:
	@echo "gcloud account : $(ACCOUNT)"
	@echo "ADC account    : $$(gcloud auth application-default print-access-token >/dev/null 2>&1 && echo ok || echo 'NOT SET - run: gcloud auth application-default login')"
	@echo "project        : $(PROJECT)"
	@echo "region         : $(REGION)"
	@echo "owner          : $(OWNER_SLUG)$(if $(OWNER),, (default from account; pass OWNER=<your-name> on make deploy))"
	@echo "agent name     : $(AGENT_NAME)"
	@echo "runtime SA     : $(LAB_SA)"
	@echo "RAG table      : $(DATA_PROJECT).acsm_rag.policy_chunks"

check:
	@test -n "$(PROJECT)" || { echo "No project. Run: gcloud config set project <id>"; exit 1; }
	@test -n "$(OWNER)" || { echo "Before deploying, ask the participant for their name and run: make deploy OWNER=<participant-name>"; exit 1; }
	@test -n "$(OWNER_SLUG)" || { echo "Invalid OWNER '$(OWNER)'. Use letters, digits or hyphens: make deploy OWNER=<participant-name>"; exit 1; }
	@test -f .lab.env || { echo "Missing .lab.env. Run: make configure"; exit 1; }
	@test -x "$(AGENTS_CLI)" || { echo "agents-cli not found. Run: make bootstrap"; exit 1; }

# Run the agent on your machine with the same settings as the deployed one.
# Memory Bank and Sessions are in-memory locally; BigQuery analytics is off.
LOCAL_RUN_ENV := $(LOCAL_ENV) ACSM_DISABLE_BQ_ANALYTICS=true

PORT      ?= 8000
LOCAL_APP := acsm_bq_rag

# Terminal 1. Restart it (Ctrl+C, make playground) after each task to load your changes.
playground:
	@echo "Starting the local playground. When it says 'Uvicorn running', open http://localhost:$(PORT)"
	@echo "and pick '$(LOCAL_APP)' in the agent drop-down. Cloud Shell: Web Preview on port $(PORT). Ctrl+C to stop."
	@$(LOCAL_RUN_ENV) $(AGENTS_CLI) playground --port $(PORT)

# Terminal 2. Sends one prompt to the running playground. SESSION=<id> continues a conversation.
local-chat:
	@curl -s -o /dev/null http://127.0.0.1:$(PORT)/list-apps || { echo "No playground on port $(PORT). Run 'make playground' in another terminal first."; exit 1; }
	@$(AGENTS_CLI) run --url http://127.0.0.1:$(PORT) --mode adk --app-name $(LOCAL_APP) $(if $(SESSION),--session-id $(SESSION)) \
	  "$(or $(Q),What is the minimum NDI floor for an applicant with 3 dependants? Cite the source.)"

search:
	@$(LOCAL_ENV) uv run python -m scripts.try_search "minimum NDI floor for applicant with 3 dependants"

check-task1:
	@$(LOCAL_ENV) uv run pytest -q tests/contract/test_tasks.py -k test_task1

check-task2:
	@$(LOCAL_ENV) uv run pytest -q tests/contract/test_tasks.py -k test_task2

check-task3:
	@$(LOCAL_ENV) uv run pytest -q tests/contract/test_tasks.py -k test_task3

check-task4:
	@$(LOCAL_ENV) uv run pytest -q tests/contract/test_tasks.py -k test_task4

test-contract:
	@$(LOCAL_ENV) uv run pytest -q tests/contract

verify: test-contract

deploy: check
	@echo "Deploying $(AGENT_NAME) to Agent Runtime in $(PROJECT) as $(LAB_SA) (about 4 minutes)..."
	@rm -f deployment_metadata.json
	@timeout $(DEPLOY_TIMEOUT) $(AGENTS_CLI) deploy -d agent_runtime \
	  --service-name $(AGENT_NAME) \
	  --service-account $(LAB_SA) \
	  --region $(REGION) --project $(PROJECT) --no-confirm-project \
	  --update-env-vars="$(ENV_VARS)" \
	|| { echo ""; echo "Deploy did not finish within $(DEPLOY_TIMEOUT)s or failed."; \
	     echo "Run 'make status'. If no agent named $(AGENT_NAME) is listed, run 'make deploy' again:"; \
	     echo "a retry creates a fresh agent and never touches anyone else's."; exit 1; }
	@$(LOCAL_ENV) uv run python -m scripts.platform_integrations register-apphub $(AGENT_NAME) || true

register-apphub:
	@test -f deployment_metadata.json || { echo "No deployment_metadata.json. Run make deploy first."; exit 1; }
	@$(LOCAL_ENV) uv run python -m scripts.platform_integrations register-apphub $(AGENT_NAME)

ensure-gateways:
	@$(LOCAL_ENV) uv run python -m scripts.platform_integrations ensure-gateways

publish-ge:
	@test -f deployment_metadata.json || { echo "No deployment_metadata.json. Run make deploy first."; exit 1; }
	@APP_ID="$(GE_APP_ID)"; \
	 if [ -z "$$APP_ID" ]; then \
	   APP_ID=$$($(LOCAL_ENV) uv run python -m scripts.platform_integrations resolve-ge-app); \
	 fi; \
	 test -n "$$APP_ID" || { echo "No Gemini Enterprise app found in $(PROJECT)."; exit 1; }; \
	 $(AGENTS_CLI) publish gemini-enterprise \
	   --gemini-enterprise-app-id "$$APP_ID" \
	   --display-name "$(AGENT_NAME)" \
	   --description "ACSM Underwriting & Policy Agent ($(OWNER_SLUG))" \
	   --project $(PROJECT)

register-ge: publish-ge

status:
	@$(LOCAL_ENV) uv run python -m scripts.agent_status $(AGENT_NAME)

chat:
	@test -f deployment_metadata.json || { echo "No deployment_metadata.json. Run make deploy first."; exit 1; }
	@RID=$$(python3 -c 'import json;print(json.load(open("deployment_metadata.json"))["remote_agent_runtime_id"])'); \
	 $(AGENTS_CLI) run --mode a2a --url "https://$(REGION)-aiplatform.googleapis.com/v1/$$RID" "$(or $(Q),What is the minimum NDI floor for an applicant with 3 dependants? Cite the source.)" \
	 || echo "First call after the agent sat idle can time out while it starts. Run the same command again."

chat-audit:
	@$(MAKE) --no-print-directory chat Q="Use the restricted audit log tool to list audit findings for Johor Bahru."

memory-demo:
	@echo "=== Session 1: Teaching Memory Bank about the officer's branch & product focus ==="
	@$(MAKE) --no-print-directory chat Q="Please remember this about me: my name is Officer Farhan from the Johor Bahru branch, and I handle Personal Financing applications."
	@echo "Waiting 10s for Vertex AI Memory Bank fact extraction..."
	@sleep 10
	@echo ""
	@echo "=== Session 2 (new session): Recalling officer profile from Memory Bank ==="
	@$(MAKE) --no-print-directory chat Q="Which branch am I from, what is my name, and which financing applications do I handle?"
	@echo ""
	@echo "=== Persisted Memory Bank Records ==="
	@$(MAKE) --no-print-directory memory

trace:
	@RID=$$(python3 -c 'import json;print(json.load(open("deployment_metadata.json"))["remote_agent_runtime_id"].split("/")[-1])'); \
	 echo "Traces:  https://console.cloud.google.com/traces/list?project=$(PROJECT)"; \
	 echo "Logs:    https://console.cloud.google.com/logs/query;query=resource.labels.reasoning_engine_id%3D%22$$RID%22?project=$(PROJECT)"; \
	 echo "Console: https://console.cloud.google.com/vertex-ai/agents/agent-engines/locations/$(REGION)/agent-engines/$$RID?project=$(PROJECT)"

memory:
	@$(LOCAL_ENV) uv run python scripts/inspect_memory_bank.py "$(USER_ID)"

audit-logs:
	@$(LOCAL_ENV) uv run python -m scripts.audit_logs $(OWNER_SLUG)

test-governance:
	@$(LOCAL_ENV) ACSM_DISABLE_BQ_ANALYTICS=true uv run python scripts/test_governance.py

eval-baseline:
	rm -rf artifacts/traces artifacts/baseline
	$(LOCAL_ENV) ACSM_PROMPT_MODE=baseline $(AGENTS_CLI) eval run --dataset tests/eval/datasets/acsm_golden.json --output artifacts/baseline

eval-candidate:
	rm -rf artifacts/traces artifacts/candidate
	$(LOCAL_ENV) ACSM_PROMPT_MODE=hillclimb $(AGENTS_CLI) eval run --dataset tests/eval/datasets/acsm_golden.json --output artifacts/candidate

eval-compare:
	$(AGENTS_CLI) eval compare $$(ls -t artifacts/baseline/results_*.json | head -n1) $$(ls -t artifacts/candidate/results_*.json | head -n1)

eval-cloud:
	@test -f deployment_metadata.json || { echo "No deployment_metadata.json. Run make deploy first."; exit 1; }
	@$(LOCAL_ENV) AGENTS_CLI="$(AGENTS_CLI)" uv run python -m scripts.platform_integrations submit-eval $(AGENT_NAME) tests/eval/datasets/acsm_golden.json

hillclimb-gepa:
	$(LOCAL_ENV) ACSM_PROMPT_MODE=baseline uv run adk optimize ./app --sampler_config_file_path tests/eval/gepa_sampler_config.json --optimizer_config_file_path tests/eval/gepa_optimizer_config.json --log_level INFO

cleanup:
	@test "$(CONFIRM)" = "yes" || { echo "This deletes agent $(AGENT_NAME). Run: make cleanup CONFIRM=yes"; exit 1; }
	@$(LOCAL_ENV) uv run python -m scripts.agent_status $(AGENT_NAME) --delete

destroy: cleanup
