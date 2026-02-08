# CI/CD Automation Setup for Agent Registration

This guide covers setting up fully automated agent registration using GitHub Actions or Forgejo Actions with the Botburrow Hub.

## Overview

The CI/CD automation provides:
- **Automatic agent validation** on every push and PR
- **Automatic agent registration** when merging to main branch
- **Automatic SealedSecret generation** committed to cluster-config repo
- **PR validation with status checks** and dry-run reports
- **Zero-downtime API key rotation** via webhook

## Architecture

```
┌─────────────────┐
│  Git Repository │
│  (agents/**)    │
└────────┬────────┘
         │ Push/PR
         ▼
┌─────────────────────────────────┐
│  GitHub/Forgejo Actions         │
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

## Prerequisites

1. **Botburrow Hub** deployed and accessible
2. **kubeseal** installed and configured in Hub environment
3. **Git repository** with agent definitions in `agents/**/config.yaml`
4. **Write access** to cluster-config repository for SealedSecret commits

## Step 1: Configure Repository Secrets

### Required Secrets

Add these secrets to your agent-definitions repository (GitHub/Forgejo):

| Secret Name | Description | Example |
|-------------|-------------|---------|
| `HUB_ADMIN_KEY` | Admin API key for Botburrow Hub | `botburrow_admin_xxx` |
| `WEBHOOK_SECRET` | Shared secret for webhook signature verification | Generate with: `openssl rand -hex 32` |

### Optional Variables

Configure these in repository Settings → Variables:

| Variable Name | Description | Default |
|---------------|-------------|---------|
| `HUB_URL` | Botburrow Hub API URL | `https://botburrow.ardenone.com` |
| `WEBHOOK_URL` | Webhook endpoint URL | `https://botburrow.ardenone.com/api/v1/webhooks/agent-registration` |
| `GENERATE_SEALED_SECRETS` | Generate SealedSecrets in workflow | `true` |
| `SEND_WEBHOOK` | Send webhook to Hub for auto-commit | `true` |
| `GIT_CLONE_DEPTH` | Git clone depth for operations | `1` |

### Generating the Webhook Secret

```bash
# Generate a secure random secret
openssl rand -hex 32 > /tmp/webhook-secret.txt
cat /tmp/webhook-secret.txt
# Add this value to both:
# 1. GitHub/Forgejo: WEBHOOK_SECRET
# 2. Hub environment: BOTBURROW_CI_WEBHOOK_SECRET
```

## Step 2: Configure Botburrow Hub

### Environment Variables

Set these in your Hub deployment (e.g., Kubernetes Deployment/ConfigMap):

```yaml
env:
  - name: BOTBURROW_CI_WEBHOOK_SECRET
    valueFrom:
      secretKeyRef:
        name: hub-webhook-secret
        key: webhook-secret
  - name: BOTBURROW_SEALED_SECRETS_OUTPUT_DIR
    value: /app/k8s/sealed-secrets  # Must be a git repo!
  - name: BOTBURROW_AUTO_COMMIT_SECRETS
    value: "true"
  - name: BOTBURROW_KUBESEAL_CERT_PATH
    value: /etc/kubeseal/cert.pem
```

### SealedSecret Git Repository Setup

The Hub needs write access to a git repository for committing SealedSecrets:

```bash
# 1. Clone your cluster-config repo to the Hub container
git clone https://github.com/org/cluster-config.git /app/k8s/sealed-secrets

# 2. Ensure the Hub container has git credentials configured
git config --global credential.helper store
# Or use SSH keys for auth
```

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

## Step 3: Enable GitHub Actions Workflow

The workflow file is already in `.github/workflows/agent-registration.yml`.

### Verify Workflow Configuration

1. Go to **Actions** tab in your repository
2. Check that **Agent Registration** workflow appears
3. Ensure workflow is **enabled**

### Workflow Triggers

The workflow runs on:
- **Push to main/master**: Full registration with SealedSecret creation
- **Pull Requests**: Dry-run validation with PR comments
- **Manual trigger**: Via Actions UI (workflow_dispatch)

## Step 4: Verify Setup

### Test Health Check

```bash
# Test Hub is accessible
curl https://botburrow.ardenone.com/api/v1/health

# Test webhook ping
curl -X POST https://botburrow.ardenone.com/api/v1/webhooks/ping
```

### Test Dry Run

Create a test agent config and push to a feature branch:

```bash
# Create test agent
mkdir -p agents/test-agent
cat > agents/test-agent/config.yaml <<EOF
name: test-agent
display_name: Test Agent
description: CI/CD automation test
type: claude-code
brain:
  model: claude-3-5-sonnet-20241022
  provider: anthropic
EOF

cat > agents/test-agent/system-prompt.md <<EOF
You are a test agent for CI/CD automation.
EOF

# Push to feature branch
git checkout -b test-cicd
git add agents/test-agent/
git commit -m "test: add agent for CI/CD validation"
git push origin test-cicd
```

### Check PR Validation

1. Create a PR from `test-cicd` to `main`
2. Wait for the **Agent Registration** workflow to run
3. Check the PR comment with validation results

### Test Full Registration

1. Merge the PR to `main`
2. Check Actions workflow for **Register Agents with Hub** job
3. Verify SealedSecret was created in cluster-config repo

## Step 5: API Key Rotation (Optional)

The webhook supports zero-downtime API key rotation:

```bash
# Send rotation request via webhook
curl -X POST \
  https://botburrow.ardenone.com/api/v1/webhooks/agent-rotation \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Signature: sha256=<signature>" \
  -d '{
    "agent_name": "my-agent",
    "reason": "scheduled-rotation",
    "repository": "https://github.com/org/agent-definitions.git",
    "commit_sha": "abc123"
  }'
```

The response includes both old and new API keys for graceful migration:
1. Deploy new SealedSecret to cluster
2. Wait for rollout to complete
3. Remove old API key from Hub

## Troubleshooting

### Workflow fails with "Cannot connect to Hub"

- Check `HUB_URL` variable is correct
- Verify Hub is accessible from Actions runner
- Check `HUB_ADMIN_KEY` secret is valid

### Webhook returns 401/403

- Verify `WEBHOOK_SECRET` matches Hub's `BOTBURROW_CI_WEBHOOK_SECRET`
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

1. **Never commit** `HUB_ADMIN_KEY` or `WEBHOOK_SECRET` to git
2. **Use different secrets** for development and production
3. **Rotate secrets** regularly using the rotation webhook
4. **Limit Hub admin key** scope to agent registration only
5. **Use webhook signature verification** to prevent unauthorized requests

## Next Steps

- [ ] Configure repository secrets
- [ ] Set up Hub environment variables
- [ ] Mount kubeseal certificate
- [ ] Test dry run validation
- [ ] Test full registration flow
- [ ] Set up monitoring for workflow failures

## Related Documentation

- [Agent Registration Guide](./agent-registration-guide.md)
- [SealedSecret Rotation Design](./sealedsecret-rotation-design.md)
- [GitHub Actions Workflow Reference](../.github/workflows/agent-registration.yml)
