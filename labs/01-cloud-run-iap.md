# Take-home 1: Cloud Run with IAP

Run the same agent on Cloud Run with the ADK web UI, behind Identity-Aware Proxy. Needs **your own project** where you can change IAM. It does not work in the shared workshop project.

## Why this is different from the workshop

The workshop agents on Agent Runtime aren't behind IAP. Callers need the IAM permission `aiplatform.reasoningEngines.query` on the project, and `make chat` uses your own credential. IAP is for a browser UI: it signs users in with Google and only lets through the ones you list.

## Deploy

```bash
gcloud config set project <your-project>
make configure CONFIG_SECRET=<your-secret>   # or write .lab.env by hand, see .env.example
agents-cli deploy -d cloud_run --iap --region asia-southeast1 \
  --project <your-project> --no-confirm-project \
  --update-env-vars="ACSM_PROJECT=<your-project>,ACSM_DATA_PROJECT=<data-project>,ACSM_REGION=asia-southeast1"
```

If IAP isn't on after the deploy, turn it on for the service:

```bash
gcloud beta run services update <service-name> --region=asia-southeast1 --iap
```

## Let people in

```bash
gcloud iap web add-iam-policy-binding --resource-type=cloud-run \
  --service=<service-name> --region=asia-southeast1 \
  --member="group:<team>@<your-domain>" --role="roles/iap.httpsResourceAccessor"
```

Prefer a group over a domain-wide grant, so leavers lose access when they leave the group.

## Do I need an OAuth client?

- **Users in the same Google Workspace / Cloud Identity organisation as the project:** no. IAP uses a Google-managed OAuth client. Nothing to create, nothing to store.
- **Users from a different organisation:** yes. Create one OAuth client per project in the console (it can't be created by API), then set it in the IAP settings for the project. IAP holds the client secret. Don't put it in Secret Manager or in this repo.

## Things to check before you show it to anyone

- The service account the Cloud Run service runs as needs the same data grants as `acsm-lab-agent`: `dataViewer` on the policy table only, not the dataset.
- Sessions on Cloud Run are in memory unless you point ADK at a session service. `app/app_utils/services.py` reads `SESSION_SERVICE_URI` and `MEMORY_SERVICE_URI`.
- Delete it when you're done: `gcloud run services delete <service-name> --region=asia-southeast1`.
