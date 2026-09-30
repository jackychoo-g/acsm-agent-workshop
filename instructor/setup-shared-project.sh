#!/usr/bin/env bash
# Instructor-only: prepare the shared workshop project for N participants.
#
#   ./instructor/setup-shared-project.sh --project <id> \
#       --participants "domain:example.com,user:a@example.com" \
#       --rag-corpus projects/<num>/locations/asia-southeast1/ragCorpora/<id>  [--apply]
#
# Without --apply it only prints the commands. Everything is idempotent.
# It does NOT create the RAG data (BigQuery table, bucket, corpus); that is the
# ingestion job's job. Participants never run this script.
set -euo pipefail

PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
PARTICIPANTS="" RAG_CORPUS="" APPLY=0
REGION="asia-southeast1"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT="$2"; shift 2 ;;
    --participants) PARTICIPANTS="$2"; shift 2 ;;
    --rag-corpus) RAG_CORPUS="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --apply) APPLY=1; shift ;;
    *) echo "unknown flag $1"; exit 2 ;;
  esac
done
[[ -n "$PROJECT" && "$PROJECT" != "(unset)" && -n "$PARTICIPANTS" && -n "$RAG_CORPUS" ]] || { sed -n 2,10p "$0"; exit 2; }

SA_NAME="acsm-lab-agent"
SA="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
BUCKET="${PROJECT}-acsm-rag-corpus"
SECRET="acsm-lab-config"

run() { echo "+ $*"; if [[ $APPLY -eq 1 ]]; then "$@"; fi; }

echo "== APIs"
run gcloud services enable aiplatform.googleapis.com bigquery.googleapis.com modelarmor.googleapis.com \
  secretmanager.googleapis.com cloudtrace.googleapis.com logging.googleapis.com storage.googleapis.com \
  --project "$PROJECT"

echo "== Analytics dataset & table (adk_agent_analytics.agent_events)"
run bq --location="$REGION" --project_id="$PROJECT" mk --dataset --if_not_exists "${PROJECT}:adk_agent_analytics"
run bq --location="$REGION" --project_id="$PROJECT" query --use_legacy_sql=false \
  "CREATE TABLE IF NOT EXISTS \`${PROJECT}.adk_agent_analytics.agent_events\` (ts TIMESTAMP, owner STRING, backend STRING, user_id STRING, session_id STRING, latency_ms INT64, status STRING, preview STRING)"

echo "== Model Armor template (acsm-credit-armor in ${REGION})"
if [[ $APPLY -eq 1 ]]; then
  TOKEN=$(gcloud auth print-access-token)
  if ! curl -fsS -H "Authorization: Bearer $TOKEN" \
    "https://modelarmor.${REGION}.rep.googleapis.com/v1/projects/${PROJECT}/locations/${REGION}/templates/acsm-credit-armor" >/dev/null 2>&1; then
    curl -fsS -X POST \
      -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/json" \
      "https://modelarmor.${REGION}.rep.googleapis.com/v1/projects/${PROJECT}/locations/${REGION}/templates?templateId=acsm-credit-armor" \
      -d '{"filterConfig":{"piAndJailbreakFilterSettings":{"filterEnforcement":"ENABLED","confidenceLevel":"LOW_AND_ABOVE"},"raiSettings":{"raiFilters":[{"filterType":"HATE_SPEECH","confidenceLevel":"MEDIUM_AND_ABOVE"},{"filterType":"DANGEROUS","confidenceLevel":"MEDIUM_AND_ABOVE"},{"filterType":"HARASSMENT","confidenceLevel":"MEDIUM_AND_ABOVE"},{"filterType":"SEXUALLY_EXPLICIT","confidenceLevel":"MEDIUM_AND_ABOVE"}]}}}' >/dev/null
  fi
else
  echo "+ ensure Model Armor template projects/${PROJECT}/locations/${REGION}/templates/acsm-credit-armor"
fi

echo "== Shared runtime service account (every participant agent runs as this)"
if ! gcloud iam service-accounts describe "$SA" --project "$PROJECT" >/dev/null 2>&1; then
  run gcloud iam service-accounts create "$SA_NAME" --project "$PROJECT" \
    --display-name "ACSM workshop shared agent runtime identity"
fi
for ROLE in roles/bigquery.jobUser roles/aiplatform.user roles/modelarmor.user roles/logging.logWriter \
            roles/cloudtrace.agent roles/monitoring.metricWriter roles/serviceusage.serviceUsageConsumer; do
  run gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$SA" --role "$ROLE" \
    --condition=None --quiet --format=none
done

echo "== Table-level grants (dataset-level would also expose collections_internal_audit)"
run bq add-iam-policy-binding --member="serviceAccount:$SA" --role=roles/bigquery.dataViewer \
  "${PROJECT}:acsm_rag.policy_chunks"
run bq add-iam-policy-binding --member="serviceAccount:$SA" --role=roles/bigquery.dataEditor \
  "${PROJECT}:adk_agent_analytics.agent_events"

echo "== Participants"
IFS=',' read -ra MEMBERS <<< "$PARTICIPANTS"
for M in "${MEMBERS[@]}"; do
  # deploy + query Agent Runtime, read traces/logs, bill API calls
  for ROLE in roles/aiplatform.user roles/logging.viewer roles/cloudtrace.user \
              roles/serviceusage.serviceUsageConsumer roles/bigquery.jobUser; do
    run gcloud projects add-iam-policy-binding "$PROJECT" --member "$M" --role "$ROLE" \
      --condition=None --quiet --format=none
  done
  # deploy agents that run as the shared SA
  run gcloud iam service-accounts add-iam-policy-binding "$SA" --project "$PROJECT" \
    --member "$M" --role roles/iam.serviceAccountUser --format=none
  # local `make search` and clickable source links
  run bq add-iam-policy-binding --member="$M" --role=roles/bigquery.dataViewer "${PROJECT}:acsm_rag.policy_chunks"
  run gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member "$M" --role roles/storage.objectViewer --format=none
done

echo "== Workshop config secret (read by 'make configure'; keeps project details out of the public repo)"
CONFIG=$(printf 'PROJECT=%s\nDATA_PROJECT=%s\nREGION=%s\nRAG_BUCKET=%s\nRAG_CORPUS=%s\nACSM_PROJECT=%s\nACSM_DATA_PROJECT=%s\nACSM_REGION=%s\nACSM_RAG_BUCKET=%s\nACSM_RAG_CORPUS_NAME=%s\n' \
  "$PROJECT" "$PROJECT" "$REGION" "$BUCKET" "$RAG_CORPUS" \
  "$PROJECT" "$PROJECT" "$REGION" "$BUCKET" "$RAG_CORPUS")
if ! gcloud secrets describe "$SECRET" --project "$PROJECT" >/dev/null 2>&1; then
  run gcloud secrets create "$SECRET" --project "$PROJECT" --replication-policy=user-managed --locations="$REGION"
fi
echo "+ gcloud secrets versions add $SECRET --data-file=- <<< (config above)"
[[ $APPLY -eq 1 ]] && printf '%s' "$CONFIG" | gcloud secrets versions add "$SECRET" --project "$PROJECT" --data-file=-
for M in "${MEMBERS[@]}"; do
  run gcloud secrets add-iam-policy-binding "$SECRET" --project "$PROJECT" --member "$M" \
    --role roles/secretmanager.secretAccessor --format=none
done

echo
[[ $APPLY -eq 1 ]] && echo "Applied." || echo "Dry run only. Re-run with --apply to execute."
echo "Check afterwards: the Agent Runtime service agent should NOT hold read access on acsm_rag.collections_internal_audit."
