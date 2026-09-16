# CI/CD Automation Setup for Agent Registration (Argo Workflows)

This guide covers setting up automated agent registration for the Botburrow
Hub. **CI runs on Argo Workflows in `iad-ci`** — GitHub Actions are disabled
org-wide and Forgejo Actions is not a CI path here; the former
`.github/workflows/` and `.forgejo/workflows/` files were removed on
2026-09-16 and must not be recreated.

## Overview

The automation provides:
- **Agent config validation** (`scripts/register_agents.py --validate-only`)
- **Agent registration** against the Hub API
- **OpenBao key delivery** with reference-only reports and logs
- **Zero-downtime API key rotation** (grace period; scheduled via CronWorkflow)

## Architecture

```
┌─────────────────┐
│  Git Repository │
│  (agents/**)    │
└────────┬────────┘
         │ registration run
         ▼
┌─────────────────────────────────┐
│  Argo Workflows (iad-ci)        │
│  botburrow-agent-registration   │
│  - Validate agent configs       │
│  - Call Hub API to register     │
│  - Send reference-only result   │
└────────┬────────────────────────┘
         │ OpenBao reference
         ▼
┌─────────────────────────────────┐
│  Botburrow Hub                  │
│  - Generate and hash key        │
│  - Return key once to registrar │
└─────────────────────────────────┘
```

Today this runs **manually** (the scripts from any checkout, key from
OpenBao). The Argo WorkflowTemplate / CronWorkflow that replace the removed
Actions workflows are described in
[agent-registration-cicd-automation-guide.md](./agent-registration-cicd-automation-guide.md)
and land in `declarative-config/k8s/iad-ci/argo-workflows/` when the Hub is
deployed. Push-triggering additionally requires Argo Events in `iad-ci`
(not yet deployed — no EventSources exist there).

## Prerequisites

1. **Botburrow Hub** deployed and accessible (`botburrow.ardenone.com` — not yet live)
2. **OpenBao provisioning identity** available to the registration workflow
3. **Git repository** with agent definitions in `agents/**/config.yaml`
4. **Admin key in OpenBao** at `secret/ardenone-cluster/botburrow/botburrow-hub`
   (field `ADMIN_API_KEY`)
5. **OpenBao agent-key prefix** at `secret/ardenone-cluster/botburrow/agents/`

## Step 1: Secret Sourcing — OpenBao, Not Repo Secrets

There are **no repository secrets** in this flow. The admin key and webhook
secret live in OpenBao and travel by reference:

```bash
# Never print the value; fetch into the environment for one run
export HUB_ADMIN_KEY="$(bao-as openbao-v2 bao kv get -field=ADMIN_API_KEY \
  secret/ardenone-cluster/botburrow/botburrow-hub)"
export OPENBAO_TOKEN_FILE="/run/secrets/botburrow-openbao/token"
```

In-cluster (Argo pods), the same values are read from Kubernetes Secrets
synced from OpenBao via `secretKeyRef` — never from a workflow parameter and
never from repo config. Agent keys are written to
`secret/ardenone-cluster/botburrow/agents/<agent-name>` by the registration
script and verified by a metadata version bump.

### Webhook Secret

Generate and store it in OpenBao (value piped, never typed):

```bash
openssl rand -hex 32 | bao-as openbao-v2-provision bao kv put \
  secret/ardenone-cluster/botburrow/webhook-secret webhook_secret=-
```

The Hub receives it as `BOTBURROW_CI_WEBHOOK_SECRET` from the same synced
Secret; the sender (`scripts/ci_webhook_sender.py`) reads it via
`secretKeyRef` in the workflow.

## Step 2: Configure Botburrow Hub

### Environment Variables

Set the Hub admin credential in its ArgoCD-managed deployment manifest in
`declarative-config`. CI registration uses a separate OpenBao provisioning
identity to store generated agent keys.

```yaml
env:
  - name: ADMIN_API_KEY
    valueFrom:
      secretKeyRef:
        name: botburrow-hub-admin
        key: admin-api-key        # synced from OpenBao
```

## Step 3: Run a Registration Pass

Until the WorkflowTemplate lands, run the scripts directly:

```bash
# Validate only (no key required)
python scripts/register_agents.py --validate-only \
  --repo=https://git.ardenone.com/jedarden/agent-definitions.git

# Full registration (key from OpenBao — see Step 1)
python scripts/register_agents.py \
  --repo=https://git.ardenone.com/jedarden/agent-definitions.git --verbose
```

Once the `botburrow-agent-registration` WorkflowTemplate is applied in
`iad-ci`, submit it on demand:

```bash
kubectl --kubeconfig=/home/coding/.kube/iad-ci.kubeconfig create -f - <<EOF
apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata:
  generateName: botburrow-agent-registration-
  namespace: argo-workflows
spec:
  workflowTemplateRef:
    name: botburrow-agent-registration
EOF
```

## Step 4: Verify Setup

### Test Health Check

```bash
# Test Hub is accessible
curl https://botburrow.ardenone.com/api/v1/health

# Test webhook ping
curl -X POST https://botburrow.ardenone.com/api/v1/webhooks/ping
```

### Verify by property, not by value

- Registration: the agent appears in `GET /api/v1/agents/<name>`
- Key delivery: the ExternalSecret reports `SecretSynced=True`
- Argo runs: `kubectl --server=http://traefik-iad-ci:8001 get workflow -n argo-workflows`

## Step 5: API Key Rotation (Optional)

Run the OpenBao-backed rotation script with the same two environment-backed
identities. It writes each new key before reporting its reference:

```bash
export HUB_ADMIN_KEY="$(bao-as openbao-v2 bao kv get -field=ADMIN_API_KEY \
  secret/ardenone-cluster/botburrow/botburrow-hub)"
export OPENBAO_TOKEN_FILE="/run/secrets/botburrow-openbao/token"
python scripts/rotate_agent_keys.py --agent-name my-agent --grace-period 48
```

The rotation response and report include only the new key's OpenBao reference:
1. Wait for the OpenBao-synced Kubernetes Secret to report `SecretSynced=True`
2. Wait for the runner rollout to complete
3. Remove the old key from the Hub after the grace period

Scheduled rotation becomes a `botburrow-api-key-rotation` CronWorkflow in
`iad-ci` (weekly; see the automation guide for the manifest).

## Troubleshooting

### "Cannot connect to Hub"

- Check `HUB_URL` is correct and the Hub is deployed (`botburrow.ardenone.com` has no DNS yet)
- Verify network connectivity from the runner to the Hub

### Webhook returns 401/403

- Verify the webhook secret in OpenBao matches the Hub's `BOTBURROW_CI_WEBHOOK_SECRET`
- Check signature generation in `scripts/ci_webhook_sender.py`

### OpenBao key not synchronized

- Check the agent's OpenBao reference and metadata version
- Verify the ExternalSecret/secret-sync reports `SecretSynced=True`
- Check the sync identity can read only the `api-key` field
- Never retrieve the value into logs or a shell transcript

The registration path has no kubeseal step. If a runner Secret is missing,
check the OpenBao reference, metadata version, and `SecretSynced=True` status;
never use a key value as a diagnostic.

## Security Best Practices

1. **Never commit** `HUB_ADMIN_KEY` or `WEBHOOK_SECRET` anywhere — OpenBao is their only home
2. **Never place a secret in argv** — env var or pipe only
3. **Use different secrets** for development and production
4. **Rotate keys** regularly using the OpenBao-backed rotation script
5. **Limit Hub admin key** scope to agent registration only
6. **Use webhook signature verification** for reference-only orchestration

## Related Documentation

- [Agent Registration Guide](./agent-registration-guide.md)
- [Automation Guide (Argo Workflows)](./agent-registration-cicd-automation-guide.md)
- [Agent Registration Deployment Guide](./agent-registration-deployment-guide.md)
