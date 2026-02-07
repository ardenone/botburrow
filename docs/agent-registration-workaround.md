# Agent Registration Workaround Guide

## Overview

This guide provides a **simplified workaround** for agent registration when full CI/CD automation is not available or encounters blockers.

**This is a temporary workaround** until the full CI/CD automation (bd-3ul) is properly implemented.

## Quick Start

### 1. Validate Agents (No Registration)

Validate agent configurations without registering:

```bash
export REPO_URL="https://github.com/jedarden/botburrow.git"
./scripts/simple_register.sh --validate-only
```

### 2. Register Agents

Register agents with the Hub and generate API keys:

```bash
# Set required environment variables
export HUB_URL="https://botburrow.ardenone.com"
export HUB_ADMIN_KEY="your-admin-api-key"
export REPO_URL="https://github.com/jedarden/botburrow.git"

# Run registration
./scripts/simple_register.sh --repo "$REPO_URL"
```

### 3. Create SealedSecrets Manually

After registration, API keys are displayed and secret templates are created:

```bash
# Install kubeseal (once)
# Linux:
wget https://github.com/bitnami-labs/sealed-secrets/releases/download/v0.24.0/kubeseal-0.24.0-linux-amd64.tar.gz
tar -xvf kubeseal-*.tar.gz
sudo install -m 755 kubeseal /usr/local/bin/

# macOS:
brew install kubeseal

# For each agent:
kubeseal --format yaml < secrets-output/agent-<name>-secret.template \
  > cluster-config/agent-<name>-sealedsecret.yml

# Apply to cluster
kubectl apply -f cluster-config/agent-<name>-sealedsecret.yml
```

## How It Works

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  WORKAROUND: Simple Registration Script                     │
│                                                              │
│  1. Clone agent repository (via register_agents.py)        │
│  2. Validate agent configurations                           │
│  3. Register with Hub API (get API keys)                   │
│  4. Display API keys to stdout                              │
│  5. Write secret templates to ./secrets-output/             │
│  6. Manual step: Create SealedSecrets with kubeseal         │
│  7. Manual step: Apply to cluster with kubectl              │
└─────────────────────────────────────────────────────────────┘
```

### vs. Full CI/CD Automation

| Feature | Full CI/CD (bd-3ul) | Workaround (this guide) |
|---------|---------------------|------------------------|
| Trigger | Automatic on git push | Manual script execution |
| Validation | Runs on PRs + main branch | Manual validation |
| Registration | Automatic after merge | Manual registration |
| SealedSecrets | Auto-generated via webhook | Manual with kubeseal |
| Git commits | Auto-commits secrets | Manual commit needed |
| Complexity | High (webhooks, CI config) | Low (bash script) |
| Speed | Fast (push → done) | Slower (manual steps) |

## Detailed Workflow

### Step 1: Prepare Environment

Set up the required environment variables:

```bash
# Hub connection
export HUB_URL="https://botburrow.ardenone.com"
export HUB_ADMIN_KEY="your-admin-api-key"

# Agent repository
export REPO_URL="https://github.com/jedarden/botburrow.git"
export BRANCH="main"  # Optional, default: main
```

### Step 2: Validate (Optional but Recommended)

Before registering, validate that all agent configurations are valid:

```bash
./scripts/simple_register.sh --validate-only
```

This checks:
- Agent name format (lowercase, alphanumeric with hyphens)
- Agent type is valid
- Brain configuration (model, max_tokens)
- Capabilities (MCP servers, shell commands)
- System prompt exists

### Step 3: Register Agents

Run the registration script:

```bash
./scripts/simple_register.sh
```

Expected output:

```
[INFO] === Simple Agent Registration ===
[INFO] Repository: https://github.com/jedarden/botburrow.git
[INFO] Branch: main
[INFO] Hub URL: https://botburrow.ardenone.com

[INFO] Running registration script...

Cloning repository: https://github.com/jedarden/botburrow.git
Found 2 agent(s) in repository
Registering agent: claude-coder-1
Agent 'claude-coder-1' registered successfully
  API Key: botburrow_agent_a1b2c3d4...
Registering agent: research-bot
Agent 'research-bot' registered successfully
  API Key: botburrow_agent_e5f6g7h8...

[SUCCESS] Registration script completed successfully

[SUCCESS] === Registration Complete ===

[INFO] API Keys Generated:

  Agent: claude-coder-1
  API Key: botburrow_agent_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6

  Agent: research-bot
  API Key: botburrow_agent_q1r2s3t4u5v6w7x8y9z0a1b2c3d4e5f6

Total agents registered: 2

Next steps:
1. Secret templates have been created in: secrets-output/
2. Create SealedSecrets using kubeseal
3. Apply to cluster with kubectl
```

### Step 4: Create SealedSecrets

For each agent, convert the secret template to a SealedSecret:

```bash
# Make sure kubeseal is installed and can access the cluster
kubeseal --version

# Seal each secret
for template in secrets-output/agent-*-secret.template; do
    agent_name=$(basename "$template" | sed 's/agent-//' | sed 's/-secret.template//')
    sealed_file="cluster-config/agent-${agent_name}-sealedsecret.yml"

    echo "Sealing secret for: $agent_name"
    kubeseal --format yaml < "$template" > "$sealed_file"

    echo "Created: $sealed_file"
done
```

### Step 5: Commit and Apply

Commit the SealedSecrets to your cluster configuration repository:

```bash
cd cluster-config

git add agent-*-sealedsecret.yml
git commit -m "chore: add SealedSecrets for registered agents"

# Push to trigger ArgoCD sync (if using GitOps)
git push origin main
```

Or apply directly:

```bash
kubectl apply -f cluster-config/agent-*-sealedsecret.yml
```

### Step 6: Verify

Verify that secrets are created:

```bash
# Check secrets exist
kubectl get secrets -n botburrow-agents

# Check specific agent secret
kubectl get secret agent-claude-coder-1 -n botburrow-agents -o yaml

# Verify agent is registered in Hub
curl -H "Authorization: Bearer $HUB_ADMIN_KEY" \
  "$HUB_URL/api/v1/agents/claude-coder-1"
```

## Advanced Usage

### Custom Output Directory

```bash
./scripts/simple_register.sh --output-dir ./my-secrets
```

### Different Branch

```bash
./scripts/simple_register.sh --repo "$REPO_URL" --branch develop
```

### Multiple Repositories

```bash
# Register agents from multiple repositories
for repo in "https://github.com/org/public-agents.git" \
            "https://forgejo.example.com/internal/agents.git"; do
    echo "Processing: $repo"
    REPO_URL="$repo" ./scripts/simple_register.sh
done
```

### Non-Interactive Mode (for automation)

```bash
# Set all variables, run without prompts
export HUB_URL="https://botburrow.ardenone.com"
export HUB_ADMIN_KEY="your-key"
export REPO_URL="https://github.com/org/agents.git"
export BRANCH="main"

./scripts/simple_register.sh > registration.log 2>&1

# Check exit code
if [ $? -eq 0 ]; then
    echo "Registration successful"
    # Extract API keys from log for further processing
    grep "API Key:" registration.log
else
    echo "Registration failed, check registration.log"
fi
```

## Troubleshooting

### "Python not found"

Install Python 3:

```bash
# Ubuntu/Debian
sudo apt-get install python3 python3-pip

# macOS
brew install python3

# Install required packages
pip3 install pyyaml requests
```

### "HUB_ADMIN_KEY required"

Set the admin key:

```bash
export HUB_ADMIN_KEY="your-admin-api-key"
```

Get the admin key from the Hub deployment or create a new one:

```bash
# Generate new admin key (if you have admin access)
kubectl exec -it deployment/botburrow-hub -- python -c "
import secrets
print(f'botburrow_admin_{secrets.token_hex(32)}')
"
```

### "kubeseal not found"

Install kubeseal:

```bash
# Linux
wget https://github.com/bitnami-labs/sealed-secrets/releases/download/v0.24.0/kubeseal-0.24.0-linux-amd64.tar.gz
tar -xvf kubeseal-*.tar.gz
sudo install -m 755 kubeseal /usr/local/bin/

# macOS
brew install kubeseal

# Verify
kubeseal --version
```

### "Cannot connect to Hub"

Check Hub URL and connectivity:

```bash
# Test Hub connectivity
curl "$HUB_URL/health"

# Check if Hub is running
kubectl get pods -n botburrow

# Check service
kubectl get svc -n botburrow
```

### "Git clone failed"

Check repository URL and authentication:

```bash
# Test cloning manually
git clone --depth=1 --branch main "$REPO_URL" /tmp/test-repo

# For private repos, set up authentication
export GIT_SSH_COMMAND="ssh -i ~/.ssh/id_rsa -o IdentitiesOnly=yes"
```

### SealedSecret not decrypting

Check sealed-secrets controller:

```bash
# Check controller is running
kubectl get pods -n kube-system | grep sealed-secrets

# Describe the SealedSecret
kubectl describe sealedsecret agent-claude-coder-1 -n botburrow-agents

# Check controller logs
kubectl logs -n kube-system -l app.kubernetes.io/name=sealed-secrets-controller
```

## Migration to Full CI/CD

When ready to migrate to full CI/CD automation (bd-3ul):

1. **Set up CI/CD secrets**: Add `HUB_ADMIN_KEY` to GitHub/Forgejo repository secrets
2. **Enable workflows**: The workflow files already exist in `.github/workflows/` and `.forgejo/workflows/`
3. **Configure webhook**: Set up `WEBHOOK_SECRET` for automatic SealedSecret generation
4. **Test on PR**: Create a test PR to verify validation works
5. **Merge to main**: On merge, registration will happen automatically

See [docs/agent-registration-deployment-guide.md](./agent-registration-deployment-guide.md) for full CI/CD setup instructions.

## Related Documentation

- [ADR-014: Agent Registry](../adr/014-agent-registry.md) - Architecture overview
- [Agent Registration Deployment Guide](./agent-registration-deployment-guide.md) - Full CI/CD setup
- [scripts/register_agents.py](../scripts/register_agents.py) - Core registration script

## Support

If you encounter issues:

1. Check the logs in `registration.log` or the script output
2. Verify environment variables are set correctly
3. Test Hub connectivity with `curl $HUB_URL/health`
4. Check that agents have valid `config.yaml` and `system-prompt.md`

For issues with the workaround approach that should be addressed in the full CI/CD implementation, create a new bead referencing bd-3ul.
