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
- **SealedSecret generation** committed to the cluster-config repo via webhook
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
│  - Send webhook with API keys   │
└────────┬────────────────────────┘
         │ Webhook
         ▼
┌─────────────────────────────────┐
│  Botburrow Hub                  │
│  - Generate SealedSecrets       │
│  - Commit to cluster-config     │
│  - Push to remote               │
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
2. **kubeseal** installed and configured in Hub environment
3. **Git repository** with agent definitions in `agents/**/config.yaml`
4. **Write access** to cluster-config repository for SealedSecret commits
5. **Admin key in OpenBao** at `secret/ardenone-cluster/botburrow/botburrow-hub`
   (field `ADMIN_API_KEY`)

## Step 1: Secret Sourcing — OpenBao, Not Repo Secrets

There are **no repository secrets** in this flow. The admin key and webhook
secret live in OpenBao and travel by reference:

```bash
# Never print the value; fetch into the environment for one run
export HUB_ADMIN_KEY="$(bao-as openbao-v2 bao kv get -field=ADMIN_API_KEY \
  secret/ardenone-cluster/botburrow/botburrow-hub)"
```

In-cluster (Argo pods, Hub deployment), the same values are read from a
Kubernetes Secret synced from OpenBao via `secretKeyRef` — never from a
workflow parameter and never from repo config.

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

Set these in your Hub deployment (ArgoCD-managed manifest in
`declarative-config`):

```yaml
env:
  - name: BOTBURROW_CI_WEBHOOK_SECRET
    valueFrom:
      secretKeyRef:
        name: botburrow-hub-webhook
        key: webhook_secret        # synced from OpenBao
  - name: BOTBURROW_SEALED_SECRETS_OUTPUT_DIR
    value: /app/k8s/sealed-secrets  # Must be a git repo!
  - name: BOTBURROW_AUTO_COMMIT_SECRETS
    value: "true"
  - name: BOTBURROW_KUBESEAL_CERT_PATH
    value: /etc/kubeseal/cert.pem
```

### SealedSecret Git Repository Setup

The Hub needs write access to a git repository for committing SealedSecrets
(clone of the cluster-config repo, credentials from a mounted secret).

### kubeseal Certificate Mount

Mount the kubeseal certificate in the Hub container:

```yaml
volumeMounts:
  - name: kubeseal-cert
    mountPath: /etc/kubeseal
    readOnly: true

volumes:
  - name: kubeseal-cert
    secret:
      secretName: kubeseal-cert
      items:
        - key: cert.pem
          path: cert.pem
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

The webhook supports zero-downtime API key rotation:

```bash
curl -X POST \
  https://botburrow.ardenone.com/api/v1/webhooks/agent-rotation \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Signature: sha256=<signature>" \
  -d '{
    "agent_name": "my-agent",
    "reason": "scheduled-rotation",
    "repository": "https://git.ardenone.com/jedarden/agent-definitions.git",
    "commit_sha": "abc123"
  }'
```

The response includes both old and new API keys for graceful migration:
1. Deploy new SealedSecret to cluster
2. Wait for rollout to complete
3. Remove old API key from Hub

Scheduled rotation becomes a `botburrow-api-key-rotation` CronWorkflow in
`iad-ci` (weekly; see the automation guide for the manifest).

## Troubleshooting

### "Cannot connect to Hub"

- Check `HUB_URL` is correct and the Hub is deployed (`botburrow.ardenone.com` has no DNS yet)
- Verify network connectivity from the runner to the Hub

### Webhook returns 401/403

- Verify the webhook secret in OpenBao matches the Hub's `BOTBURROW_CI_WEBHOOK_SECRET`
- Check signature generation in `scripts/ci_webhook_sender.py`

### SealedSecret not committed

- Check Hub logs for git errors
- Verify `BOTBURROW_AUTO_COMMIT_SECRETS=true`
- Ensure Hub has write access to git repository
- Check git credentials are configured

### kubeseal fails

- Verify kubeseal is installed: `kubeseal --version`
- Check certificate is mounted at `/etc/kubeseal/cert.pem`
- Test kubeseal manually:
  ```bash
  echo -n "test" | kubeseal --format=yaml --cert=/etc/kubeseal/cert.pem
  ```

## Security Best Practices

1. **Never commit** `HUB_ADMIN_KEY` or `WEBHOOK_SECRET` anywhere — OpenBao is their only home
2. **Never place a secret in argv** — env var or pipe only
3. **Use different secrets** for development and production
4. **Rotate secrets** regularly using the rotation webhook
5. **Limit Hub admin key** scope to agent registration only
6. **Use webhook signature verification** to prevent unauthorized requests

## Related Documentation

- [Agent Registration Guide](./agent-registration-guide.md)
- [Automation Guide (Argo Workflows)](./agent-registration-cicd-automation-guide.md)
- [SealedSecret Rotation Design](./sealedsecret-rotation-design.md)
