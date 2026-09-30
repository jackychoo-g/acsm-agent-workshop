# ACSM agent workshop (full code)

A working ADK agent for AEON Credit policy questions, deployed to Agent Runtime on Gemini Enterprise Agent Platform. It answers from the policy corpus with clickable citations, remembers users across sessions with Memory Bank, screens prompts with Model Armor and a MyKad/PDPA guard, and logs every run for audit.

This is the guided path: the code is complete, you deploy it, talk to it, and read how it works. The build-it-yourself version is the [`acsm-agent-build`](https://github.com/jackychoo-g/acsm-agent-build) repo.

## What you need

- A Google account the instructor has added to the workshop project.
- Either **Cloud Shell** (nothing to install; gcloud is already signed in) or a laptop with `git`, `make`, Python 3.11+ and the [gcloud CLI](https://cloud.google.com/sdk/docs/install).

## 1. Sign in: two credentials, not one

gcloud keeps two separate credentials on your machine. The workshop needs both.

| Credential | Set with | Used by |
|---|---|---|
| gcloud CLI credential | `gcloud auth login` | `gcloud ...` commands, `make configure` |
| Application Default Credentials (ADC) | `gcloud auth application-default login` | Python client libraries: the agent code when you run it locally, and `agents-cli deploy` |

A third identity matters once the agent is deployed: it runs as the shared service account `acsm-lab-agent@<project>`, **not** as you. What the agent can read in BigQuery is decided by that service account's grants. You'll see this when the audit-log question comes back `PERMISSION_DENIED`.

```bash
gcloud auth login --update-adc --no-launch-browser   # sets both CLI and ADC credentials in one step
gcloud config set project <workshop-project-id>      # the instructor gives you this
```

`--no-launch-browser` prints a link. Open it, sign in, and paste the code back into the terminal. (On Cloud Shell, where the CLI is already signed in, you can also run `gcloud auth application-default login --no-launch-browser`.)

## 2. Set up

```bash
git clone https://github.com/jackychoo-g/acsm-agent-workshop.git
cd acsm-agent-workshop
make bootstrap    # installs uv and agents-cli into ~/.local/bin, then the Python deps
make configure    # reads workshop settings from Secret Manager into .lab.env
make whoami       # check project, owner and agent name before you deploy
```

`make whoami` shows the name your agent will get: `acsm-agent-<owner>`. The owner defaults to the part of your email before `@`. To pick your own, add `OWNER=<name>` to any command. Use the same name every time, or you'll create a second agent.

## 3. Try retrieval locally

```bash
make search              # BigQuery vector search over policy_chunks
make search-rag-engine   # the same question against the RAG Engine corpus
make test-contract       # checks the citation contract, the audit denial and the config
```

## 4. Deploy

```bash
make deploy OWNER=<your-name>
```

Takes about 4 minutes. Everyone deploys into the same project, but each agent has its own name, so you never overwrite anyone else's. If it times out, run `make status`: if your agent isn't listed, run `make deploy` again.

## 5. Talk to it

```bash
make chat Q="What is the minimum NDI floor for an applicant with 3 dependants? Cite the source."
make chat Q="Berapakah had maksimum DSR untuk pemohon bergaji RM 4,500 sebulan?"
make chat-audit          # asks for the restricted audit log: expect PERMISSION_DENIED, explained
```

The first call after the agent has been idle can time out while it starts. Run it again.

## 6. Look inside

```bash
make status        # your agent's resource ID, service account and last update
make trace         # links to Cloud Trace, Cloud Logging and the console page for your agent
make memory USER_ID=workshop-user   # what Memory Bank stored for a user
make audit-logs    # your agent's runs from the analytics table, filtered by owner
make test-governance               # Model Armor and MyKad/PDPA guard, run locally
```

Then open [CODE_WALKTHROUGH.md](CODE_WALKTHROUGH.md). It links each step of a request to the exact lines that handle it.

## 7. Evaluate and improve (optional)

```bash
make eval-baseline && make eval-candidate && make eval-compare
make hillclimb-gepa   # prompt optimisation with GEPA, takes several minutes
```

## 8. Clean up

```bash
make cleanup CONFIRM=yes   # deletes acsm-agent-<you> only
```

## After the workshop

The `labs/` folder has take-home guides that need your own project:
[Cloud Run + IAP](labs/01-cloud-run-iap.md), [Agent Identity](labs/02-agent-identity.md), [Agent Gateway](labs/03-agent-gateway.md).

## Layout

```
app/                 agent, tools, governance, config
  agent.py           agents and callbacks
  tools/             BigQuery and RAG Engine retrieval, restricted audit lookup
  governance/        Model Armor + MyKad/PDPA guard
  config.py          every setting, read from the environment and .lab.env
scripts/             helpers behind the make targets
tests/contract/      citation contract and config checks
tests/eval/          golden dataset, eval config, GEPA configs
infra/               instructor and take-home infrastructure (participants don't run it)
instructor/          one-time shared project setup
labs/                take-home guides
```
