# Take-home 3: Agent Gateway

Agent Gateway puts a governed network path in front of agents (client to agent) and behind them (agent to tools and MCP servers). The two definitions in `infra/gateways/` are the ones used in the reference build:

| File | Access path | Purpose |
|---|---|---|
| `acsm-ingress-gateway.yaml` | `CLIENT_TO_AGENT` | Clients reach agents only through the gateway |
| `acsm-egress-gateway.yaml` | `AGENT_TO_ANYWHERE` | Agent calls to MCP servers go through the gateway, limited to what's in Agent Registry |

Needs **your own project**.

## Fill in the project

```bash
export PROJECT_ID=<your-project>
envsubst < infra/gateways/acsm-egress-gateway.yaml > /tmp/acsm-egress-gateway.yaml
```

## Create and inspect

Check the current create syntax in the Agent Gateway documentation before running it. The surface is new and flags have changed between releases. Listing is stable:

```bash
gcloud network-services agent-gateways list --location=asia-southeast1 --project=$PROJECT_ID
gcloud alpha agent-registry agents list --location=asia-southeast1 --project=$PROJECT_ID
gcloud alpha agent-registry mcp-servers list --location=asia-southeast1 --project=$PROJECT_ID
```

## What to look at

- Which agents and MCP servers are registered, and whether an agent can call a tool that isn't.
- Where the gateway logs requests, and how that lines up with the traces from the workshop agent.
