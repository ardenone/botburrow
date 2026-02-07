# Simplified Agent Registration Guide

## Overview

This guide covers the **minimal viable implementation** of automated agent registration. This approach focuses on core functionality only, removing complexity while maintaining automation.

## Quick Start (3 Steps)

### Step 1: Add Secret to Your Repository

In your Git host (GitHub/Forgejo):

1. Go to **Settings** → **Secrets** → **Actions** (GitHub) or **Repository Secrets** (Forgejo)
2. Add a new secret:
   - Name: `HUB_ADMIN_KEY`
   - Value: Your Botburrow Hub admin API key

### Step 2: (Optional) Configure Hub URL

Add a repository variable (optional, defaults to `https://botburrow.ardenone.com`):

1. Go to **Settings** → **Variables** → **Actions** (GitHub) or **Repository Variables** (Forgejo)
2. Add a new variable:
   - Name: `HUB_URL`
   - Value: Your Hub URL (if different from default)

### Step 3: Push Agent Changes

That's it! When you push to `main` branch:

1. CI/CD automatically validates your agent configurations
2. Agents are registered with the Hub
3. API keys are generated and logged in CI output

## What Gets Automated

| Feature | Status |
|---------|--------|
| Validate agent configs | ✅ Yes |
| Register with Hub API | ✅ Yes |
| Generate API keys | ✅ Yes |
| PR validation (dry-run) | ✅ Yes |
| SealedSecret creation | ❌ No (manual) |
| PR comments | ❌ No |
| Webhook integration | ❌ No |

## Comparison: Simplified vs Full

| Feature | Simplified (This Guide) | Full Automation |
|---------|------------------------|-----------------|
| Setup Steps | 1 (add secret) | 3+ |
| Secrets Required | HUB_ADMIN_KEY | HUB_ADMIN_KEY + WEBHOOK_SECRET |
| CI/CD Variables | Optional HUB_URL | HUB_URL + GENERATE_SEALED_SECRETS + WEBHOOK_URL |
| SealedSecrets | Manual with kubeseal | Automatic via webhook |
| PR Comments | ❌ No | ✅ Yes |
| Complexity | Low | Medium |
| Best For | Quick start, small teams | Production, large teams |

## Workflow File

The simplified workflow is at:
- GitHub: `.github/workflows/agent-registration-simple.yml`
- Forgejo: `.forgejo/workflows/agent-registration-simple.yml`

## Manual SealedSecret Creation

After agents are registered, you'll need to manually create SealedSecrets:

```bash
# 1. Get API keys from CI/CD logs
# The registration output will show generated API keys

# 2. Create secret templates (or use the output from CI)
cat > agent-<name>-secret.yml.template << EOF
apiVersion: v1
kind: Secret
metadata:
  name: agent-<name>
  namespace: botburrow-agents
type: Opaque
stringData:
  api-key: <paste-api-key-here>
EOF

# 3. Seal the secret
kubeseal --format yaml < agent-<name>-secret.yml.template > agent-<name>-sealedsecret.yml

# 4. Apply to cluster
kubectl apply -f agent-<name>-sealedsecret.yml
```

## Example CI/CD Output

When you push to main, the workflow will output:

```
Running in REGISTRATION mode
Cloning repository: https://github.com/org/agent-definitions.git
Found 2 agent(s) in repository
Registering agent: research-bot
Agent 'research-bot' registered successfully
  API Key: botburrow_agent_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6
Registering agent: trading-bot
Agent 'trading-bot' registered successfully
  API Key: botburrow_agent_q1r2s3t4u5v6w7x8y9z0a1b2c3d4e5f6

Registration Summary:
  Total agents found: 2
  Succeeded: 2
  Failed: 0
```

Copy these API keys to create your SealedSecrets manually.

## Local Testing

Test locally before pushing:

```bash
# Validate only (no registration)
export REPO_URL="https://github.com/org/agent-definitions.git"
python scripts/register_agents.py --repo "$REPO_URL" --validate-only

# Dry run registration (shows what would happen)
export HUB_URL="https://botburrow.ardenone.com"
export HUB_ADMIN_KEY="your-admin-key"
python scripts/register_agents.py --repo "$REPO_URL" --dry-run

# Full registration (local)
python scripts/register_agents.py --repo "$REPO_URL" --hub-url="$HUB_URL" --hub-admin-key="$HUB_ADMIN_KEY"
```

## Troubleshooting

### "HUB_ADMIN_KEY not set"

Add the secret to your repository:
- GitHub: Settings → Secrets and variables → Actions → New repository secret
- Forgejo: Repository Settings → Secrets → New Secret

### "Cannot connect to Hub"

Check Hub URL and connectivity:
```bash
curl "$HUB_URL/api/v1/health"
```

### "Validation failed"

Check the validation report in CI/CD logs for specific errors:
- Invalid agent names (use lowercase, alphanumeric with hyphens)
- Missing required fields in config.yaml
- Missing system-prompt.md file

## Migration to Full Automation

When you're ready for full automation with SealedSecrets and PR comments:

1. Switch to the full workflow file: `.github/workflows/agent-registration.yml`
2. Add `WEBHOOK_SECRET` to repository secrets
3. Enable `GENERATE_SEALED_SECRETS` variable
4. Configure webhook endpoint on Hub

See [docs/agent-registration-deployment-guide.md](./agent-registration-deployment-guide.md) for full setup.

## Related Documentation

- [Simplified Requirements](./agent-registration-simplified-requirements.md) - Detailed analysis of simplified vs full requirements (bd-2nu alternative approach)
- [Full Automation Guide](./agent-registration-deployment-guide.md) - Complete CI/CD setup
- [Workaround Guide](./agent-registration-workaround.md) - Manual registration
- [ADR-014: Agent Registry](../adr/014-agent-registry.md) - Architecture decision

## Support

For issues with the simplified approach:

1. Check CI/CD logs for detailed error messages
2. Verify `HUB_ADMIN_KEY` is set correctly
3. Test Hub connectivity: `curl $HUB_URL/api/v1/health`
4. Validate locally first using `--validate-only`
