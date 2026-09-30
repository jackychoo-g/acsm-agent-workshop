# infra/

Participants do not run anything in this folder.

| Path | What it is | Who uses it |
|---|---|---|
| `../instructor/setup-shared-project.sh` | Creates the shared runtime service account, table-level grants, participant roles and the `acsm-lab-config` secret. Dry run unless `--apply`. | Instructor, once per workshop project |
| `terraform/single-project/` | The `agents-cli` scaffold Terraform (APIs, telemetry bucket, a separate `*-app` service account). Kept for reference and for teams that want to take the agent to their own project later. It is **not** what the workshop uses; the workshop runs every agent as `acsm-lab-agent@<project>`. | Take-home |
| `gateways/*.yaml` | Agent Gateway definitions for the take-home lab in `labs/03-agent-gateway.md`. `${PROJECT_ID}` is substituted with `envsubst`. | Take-home |

## Terraform (take-home only)

```bash
cd infra/terraform/single-project
cp vars/example.tfvars vars/my.tfvars   # set project_id; my.tfvars is gitignored
terraform init
terraform plan -var-file=vars/my.tfvars
```

`iam.tf` grants `roles/cloudbuild.builds.builder` to the default compute service account and `roles/storage.admin` to the app service account. Review both before applying in a regulated project; neither is needed by the workshop agent.
