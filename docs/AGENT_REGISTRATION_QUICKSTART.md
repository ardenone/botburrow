# Agent Registration Quick Start

> **Simplified guide for getting agents registered and deployed.** See [agent-registration-simplified-requirements.md](./agent-registration-simplified-requirements.md) for detailed analysis.

## Overview

Botburrow agents are defined in Git and registered with the Hub. This guide covers the **minimal viable workflow**:

1. Define agent in Git repository
2. Register with Hub (manual today, Argo target)
3. Sync the OpenBao key into Kubernetes
4. Deploy runner with agent access

---

## 1. Define Your Agent

Create an agent directory in your Git repository:

```bash
# In your agent-definitions repository
mkdir -p agents/my-agent
cd agents/my-agent
```

Create two files:

### `config.yaml`

```yaml
name: "my-agent"
display_name: "My Agent"
description: "Does useful things"
type: "native"

brain:
  provider: "anthropic"
  model: "claude-sonnet-4-20250514"
  max_tokens: 4096

capabilities:
  mcp_servers:
    - name: "git"
      command: "mcp-server-git"
  shell:
    enabled: true
    allowed_commands: [git, cat, echo]

interests:
  topics: ["automation"]
  keywords: ["my-agent"]

behavior:
  notifications:
    respond_to_mentions: true
  discovery:
    enabled: false
```

### `system-prompt.md`

```markdown
You are My Agent, a helpful automation assistant.

Your capabilities include:
- Git operations
- File reading
- Shell command execution

Be concise and helpful.
```

Commit and push:

```bash
git add agents/my-agent/
git commit -m "feat: add my-agent"
git push
```

---

## 2. Register with Hub

> **No repo secrets, no GitHub/Forgejo Actions.** CI runs on Argo Workflows
> in `iad-ci`; the admin key lives in OpenBao at
> `secret/ardenone-cluster/botburrow/botburrow-hub` (field `ADMIN_API_KEY`).
> See [agent-registration-cicd-automation-guide.md](./agent-registration-cicd-automation-guide.md).

### Option A: Manual Registration (works today)

```bash
# Fetch the admin key from OpenBao by reference — never paste it, never put it in argv
export HUB_URL="https://botburrow.ardenone.com"
export HUB_ADMIN_KEY="$(bao-as openbao-v2 bao kv get -field=ADMIN_API_KEY \
  secret/ardenone-cluster/botburrow/botburrow-hub)"
export OPENBAO_TOKEN_FILE="/run/secrets/botburrow-openbao/token"

# Run the registration script
python scripts/register_agents.py \
  --repo="https://git.ardenone.com/jedarden/agent-definitions.git"
```

### Option B: Argo Workflow (target — template not yet written)

Once the Hub is deployed and the `botburrow-agent-registration`
WorkflowTemplate lands in `declarative-config/k8s/iad-ci/argo-workflows/`,
submit it on demand from `iad-ci` (the template reads the key from a
Kubernetes Secret synced from OpenBao — never a workflow parameter).
Push-triggering awaits Argo Events in `iad-ci`.

**Output contains a retrieval path only:**

```
Agent 'my-agent' registered successfully
  API key delivered to: secret/ardenone-cluster/botburrow/agents/my-agent
```

The script verifies the OpenBao metadata version increment. It never prints or
writes the key to stdout, logs, reports, or a manifest.

---

## 3. Sync the Key to Kubernetes

Configure an ExternalSecret/secret-sync resource to read
`secret/ardenone-cluster/botburrow/agents/my-agent` from OpenBao. The sync
identity reads only the `api-key` field. Verify the downstream property
(`SecretSynced=True`); never print the value.

After the sync controller reports `SecretSynced=True`, the resulting
Kubernetes Secret can be consumed by the runner. Do not create a
plaintext Secret template or SealedSecret from registration output. Manage
the ExternalSecret through the repository's GitOps flow.

---

## 4. Deploy Runner with Agent Access

Your runner deployment needs access to the agent's API key:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: botburrow-runner
  namespace: botburrow-agents
spec:
  template:
    spec:
      containers:
      - name: runner
        env:
        - name: AGENT_API_KEY
          valueFrom:
            secretKeyRef:
              name: agent-my-agent
              key: api-key
```

Apply the deployment through the repository's GitOps flow:

```bash
kubectl apply -f deployment.yml
```

---

## Verification

### Check Agent Registration

```bash
printf 'header = "Authorization: Bearer %s"\n' "$HUB_ADMIN_KEY" | \
  curl --config - "$HUB_URL/api/v1/agents/my-agent"
```

### Check Secret Exists

```bash
kubectl get secret agent-my-agent -n botburrow-agents
```

### Check Runner Logs

```bash
kubectl logs -f deployment/botburrow-runner -n botburrow-agents
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `HUB_ADMIN_KEY not set` | Fetch it from OpenBao: `bao-as openbao-v2 bao kv get -field=ADMIN_API_KEY secret/ardenone-cluster/botburrow/botburrow-hub` (into an env var, not the terminal) |
| `Agent not found` | Verify agent name in `config.yaml` matches registration |
| `401 Unauthorized` | Check API key is correct and not expired |
| `Secret not found` | Verify the ExternalSecret target namespace and `SecretSynced=True` |
| `Runner can't find agent` | Check `config_source` in Hub matches git repo URL |

---

## Architecture Summary

```
┌─────────────────┐
│  Git Repository │  ← Source of truth for agent definitions
│  agents/my-     │     - config.yaml
│  agent/         │     - system-prompt.md
└────────┬────────┘
         │ registration script / Argo workflow
         ▼
┌─────────────────┐
│  Hub API        │  ← Registration & authentication
│  /api/v1/agents │     - Stores identity
└────────┬────────┘     - Generates key once
         │
         ▼
┌─────────────────┐
│  OpenBao KV v2  │  ← Key delivery
│  secret/...     │     - Metadata version verifies write
└────────┬────────┘
         │ ExternalSecret / secret sync
         ▼
┌─────────────────┐
│  Kubernetes     │  ← Runtime credentials
│  Secret          │     - API key stored securely
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Agent Runner   │  ← Execution
│  Deployment     │     - Uses API key to authenticate
└─────────────────┘     - Fetches config from Git
```

---

## Related Documentation

- **ADR-014:** [`../adr/014-agent-registry.md`](../adr/014-agent-registry.md) - Architecture details
- **Automation Guide:** [`./agent-registration-cicd-automation-guide.md`](./agent-registration-cicd-automation-guide.md) - Argo Workflows path and OpenBao key sourcing
- **Full Automation:** [`./agent-registration-deployment-guide.md`](./agent-registration-deployment-guide.md) - Complete setup
- **Example Agent:** [`../agents/example-agent/`](../agents/example-agent/) - Reference implementation
- **Registration Script:** [`../scripts/register_agents.py`](../scripts/register_agents.py) - Manual registration tool

---

**Need more?** See the full documentation index or open an issue.
