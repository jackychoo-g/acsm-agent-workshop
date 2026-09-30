# Take-home 2: Agent Identity

In the workshop every agent runs as one shared service account. That's convenient for 30 people in one project, but it means every agent has the same access. Agent Identity gives each deployed agent its own principal, so you can grant one agent the audit table and not another.

Needs **your own project**. Don't try this in the shared workshop project: `--agent-identity` updates the project IAM policy during deploy, and concurrent deploys from many people overwrite each other's bindings.

## Deploy with its own identity

```bash
agents-cli deploy -d agent_runtime --agent-identity \
  --service-name acsm-agent-identity-demo \
  --region asia-southeast1 --project <your-project> --no-confirm-project \
  --update-env-vars="ACSM_PROJECT=<your-project>,ACSM_DATA_PROJECT=<data-project>,ACSM_REGION=asia-southeast1"
```

## Find the principal

The agent's effective identity is on the resource. It is a `principal://...` identifier, not a service account email:

```python
import agentplatform
c = agentplatform.Client(project="<your-project>", location="asia-southeast1",
                         http_options={"api_version": "v1beta1"})
for a in c.agent_engines.list():
    print(a.api_resource.display_name, a.api_resource.spec.effective_identity)
```

## Grant it exactly what it needs

Project roles (same set as the shared service account):

```bash
P="principal://<effective_identity>"
for r in bigquery.jobUser aiplatform.user modelarmor.user logging.logWriter cloudtrace.agent; do
  gcloud projects add-iam-policy-binding <your-project> --member="$P" --role="roles/$r" --condition=None --quiet
done
```

Then table-level `roles/bigquery.dataViewer` on `acsm_rag.policy_chunks` and, **for this agent only**, on `acsm_rag.collections_internal_audit`. Use table grants, not dataset grants, or the boundary disappears.

## Check the difference

Ask the audit question to this agent and to one running as the shared service account. Same code, different answer. Access follows identity.

## Watch out for

The Agent Runtime service agent (`service-<project-number>@gcp-sa-aiplatform-re.iam.gserviceaccount.com`) is the identity an agent uses when you give it no service account and no Agent Identity. If that service agent has read access to a sensitive table, every such agent inherits it. Check its grants.
