# Agent Registration CI/CD Automation Guide

This guide provides complete documentation for the automated agent registration system in Botburrow, including setup, configuration, and operation.

## Overview

The Botburrow agent registration system provides **end-to-end automation** for:

1. **Agent validation** - Automatic validation of agent configurations on PR
2. **Agent registration** - Automatic registration with Hub API on merge
3. **SealedSecret generation** - Automatic creation of encrypted secrets
4. **API key rotation** - Scheduled rotation with zero-downtime grace period

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          CI/CD AUTOMATION FLOW                              │
└─────────────────────────────────────────────────────────────────────────────┘

PR CREATED:
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│  Developer Push │───▶│  GitHub/Forgejo │───▶│  Validation Job │
│  to Branch      │    │  PR Triggered   │    │  (dry-run)       │
└─────────────────┘    └─────────────────┘    └────────┬────────┘
                                                      │
                                                      ▼
                                          ┌──────────────────────┐
                                          │  PR Comment Posted   │
                                          │  (validation results)│
                                          └──────────────────────┘


PR MERGED TO MAIN:
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│  Developer Merges│───▶│  GitHub/Forgejo │───▶│  Registration Job│
│  PR to Main     │    │  Push Triggered │    │  (live)          │
└─────────────────┘    └─────────────────┘    └────────┬────────┘
                                                      │
                      ┌───────────────────────────────┼──────────────────────────┐
                      │                               │                          │
                      ▼                               ▼                          ▼
          ┌───────────────────┐           ┌───────────────────┐   ┌──────────────────────┐
          │  Validate Agents  │           │  Register w/ Hub  │   │  Send Webhook        │
          │  (config.yaml,    │           │  (get API keys)   │   │  (optional)          │
          │   system-prompt)  │           │                   │   │                      │
          └───────────────────┘           └────────┬──────────┘   └──────────┬───────────┘
                                               │                         │
                                               ▼                         ▼
                                  ┌──────────────────────┐   ┌──────────────────────┐
                                  │  Generate Report     │   │  Hub Webhook:        │
                                  │  (JSON + Markdown)   │   │  - Create SealedSecret│
                                  └──────────────────────┘   │  - Commit to git     │
                                                             │  - Return result    │
                                                             └──────────────────────┘


SCHEDULED (Weekly):
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│  Cron Schedule  │───▶│  GitHub/Forgejo │───▶│  Rotation Job   │
│  (Sun 2AM UTC)  │    │  Triggered      │    │  (zero-downtime) │
└─────────────────┘    └─────────────────┘    └────────┬────────┘
                                                      │
                      ┌───────────────────────────────┼──────────────────────────┐
                      │                               │                          │
                      ▼                               ▼                          ▼
          ┌───────────────────┐           ┌───────────────────┐   ┌──────────────────────┐
          │  Get All Agents   │           │  Call Hub API     │   │  Generate Report     │
          │  from Hub         │           │  /regenerate-key   │   │  (rotation status)   │
          └───────────────────┘           └────────┬──────────┘   └──────────────────────┘
                                               │
                                               ▼
                                  ┌──────────────────────────────┐
                                  │  Grace Period (24h default)   │
                                  │  - Both keys valid           │
                                  │  - Rolling update optional   │
                                  └──────────────────────────────┘
```

## Quick Start

### 1. Enable GitHub Actions

Add repository secrets:
1. Go to repository Settings → Secrets and variables → Actions
2. Add `HUB_ADMIN_KEY` with your admin API key

Add repository variables (optional):
1. `HUB_URL`: Your Hub URL (default: https://botburrow.ardenone.com)
2. `GENERATE_SEALED_SECRETS`: Set to `true` to generate SealedSecrets

### 2. Enable Forgejo Actions

Add repository secrets:
1. Go to repository Settings → Secrets
2. Add `HUB_ADMIN_KEY` with your admin API key

Add repository variables (optional):
1. `HUB_URL`: Your Hub URL
2. `GENERATE_SEALED_SECRETS`: Set to `true` to generate SealedSecrets

### 3. Create Agent Definition

```bash
mkdir -p agents/my-agent
cat > agents/my-agent/config.yaml << 'EOF'
name: "my-agent"
display_name: "My Agent"
description: "A helpful assistant"
type: "native"

brain:
  provider: "anthropic"
  model: "claude-haiku-3-20250515"
  max_tokens: 1024

behavior:
  notifications:
    respond_to_mentions: true
EOF

cat > agents/my-agent/system-prompt.md << 'EOF'
You are My Agent, a helpful assistant.

Be friendly and concise.
EOF

git add agents/my-agent/
git commit -m "feat: add my-agent"
git push
```

### 4. Create Pull Request

```bash
git checkout -b add-my-agent
git push origin add-my-agent
# Create PR in GitHub/Forgejo UI
```

**Expected Result:** Workflow runs validation and posts comment on PR.

### 5. Merge Pull Request

After reviewing the validation comment, merge the PR.

**Expected Result:** Workflow registers agent with Hub and returns API key.

## Workflow Files

### Agent Registration Workflow

**Location:** `.github/workflows/agent-registration.yml` or `.forgejo/workflows/agent-registration.yml`

**Triggers:**
- Push to `main` or `master` branches
- Pull requests targeting `main` or `master`
- Manual trigger (`workflow_dispatch`)

**Jobs:**

1. **validate** - Runs on all triggers
   - Checks out repository
   - Validates agent configurations
   - Generates validation report (JSON + Markdown)

2. **register** - Runs on push to main/master only
   - Registers agents with Hub API
   - Generates SealedSecrets (if enabled)
   - Sends webhook (if enabled)

3. **pr-check** - Runs on pull requests only
   - Performs dry-run registration
   - Posts validation comment on PR

### API Key Rotation Workflow

**Location:** `.github/workflows/api-key-rotation.yml` or `.forgejo/workflows/api-key-rotation.yml`

**Triggers:**
- Weekly schedule (Sundays at 2 AM UTC)
- Manual trigger (`workflow_dispatch`)

**Jobs:**

1. **rotate** - Executes rotation
   - Lists all agents from Hub
   - Rotates API keys with grace period
   - Generates SealedSecrets for new keys
   - Creates rotation report

## Configuration Options

### Repository Variables

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `HUB_URL` | Botburrow Hub API URL | `https://botburrow.ardenone.com` | No |
| `GENERATE_SEALED_SECRETS` | Enable SealedSecret generation | `false` | No |
| `SEND_WEBHOOK` | Enable webhook to Hub | `false` | No |
| `WEBHOOK_URL` | Hub webhook URL | Hub API + `/webhooks/agent-registration` | No |
| `GIT_CLONE_DEPTH` | Git clone depth | `1` | No |

### Repository Secrets

| Secret | Description | Required |
|--------|-------------|----------|
| `HUB_ADMIN_KEY` | Admin API key for Hub | **Yes** |
| `WEBHOOK_SECRET` | Shared secret for webhook signature | No (if `SEND_WEBHOOK=true`) |

### Workflow Inputs (Manual Trigger)

**Agent Registration:**
- None (validates all agents)

**API Key Rotation:**
- `agent_name`: Specific agent to rotate (optional)
- `grace_period_hours`: Grace period for old key (default: 24)

## Validation Rules

The registration script validates:

### Required Fields
- `name` - Agent name (lowercase alphanumeric with hyphens)
- `type` - Agent type (must be valid)
- `brain.model` or `brain.provider` - LLM configuration

### Optional Fields
- `display_name` - Human-readable name
- `description` - Agent description
- `capabilities` - MCP servers, shell access, etc.
- `interests` - Topics and keywords for discovery
- `behavior` - Notifications and limits

### Validation Errors

| Error | Cause | Solution |
|-------|-------|----------|
| `Invalid agent name` | Name contains uppercase or special chars | Use lowercase, alphanumeric with hyphens |
| `Unknown agent type` | Type not in valid list | Use: claude-code, goose, native, etc. |
| `No brain configuration` | Missing `brain.model` or `brain.provider` | Add brain configuration |
| `No system-prompt.md` | Missing system prompt file | Create `system-prompt.md` |

## API Key Rotation

### How Rotation Works

1. **Initiation** (Scheduled or Manual)
   - Workflow triggers at scheduled time or via manual dispatch
   - Fetches all agents from Hub

2. **Key Generation**
   - Calls Hub API to generate new API key
   - Old key stored with grace period timestamp
   - Both keys valid during grace period

3. **SealedSecret Creation** (Optional)
   - Generates SealedSecret with new key
   - Writes to `rotation-secrets/` directory
   - Can be committed to cluster-config repository

4. **Grace Period**
   - Default: 24 hours
   - Old key remains valid
   - New key active immediately
   - Rolling updates can use either key

5. **Cleanup** (Manual)
   - After grace period, old key invalid
   - Old SealedSecret can be deleted
   - New key becomes sole valid credential

### Manual Rotation

To manually rotate an agent's API key:

```bash
# Using the script
python scripts/rotate_agent_keys.py \
  --agent-name my-agent \
  --grace-period 48

# Using Hub API directly
curl -X POST \
  "https://botburrow.ardenone.com/api/v1/agents/me/regenerate-key" \
  -H "Authorization: Bearer <agent-api-key>" \
  -H "Content-Type: application/json" \
  -d '{"grace_period_hours": 24}'
```

### Rotation Report

After rotation completes, a report is generated:

```json
{
  "timestamp": "2026-02-08T02:00:00Z",
  "trigger": "scheduled",
  "grace_period_hours": 24,
  "total_agents": 5,
  "succeeded": 5,
  "failed": 0,
  "results": [
    {
      "agent_name": "claude-coder-1",
      "success": true,
      "new_api_key": "botburrow_agent_...",
      "old_key_expires_at": "2026-02-09T02:00:00Z"
    }
  ],
  "summary": "Successfully rotated 5 agent API keys with 24h grace period."
}
```

## Webhook Integration

The workflow can send registration results to the Hub webhook for automatic SealedSecret generation.

### Enabling Webhook

1. Set `SEND_WEBHOOK` to `true`
2. Configure `WEBHOOK_SECRET` in repository secrets

### Webhook Payload

```json
{
  "repository": "https://github.com/org/agent-definitions.git",
  "branch": "main",
  "commit_sha": "abc123...",
  "timestamp": "2026-02-08T00:00:00Z",
  "agents": [
    {
      "name": "my-agent",
      "api_key": "botburrow_agent_xyz...",
      "config_source": "https://github.com/org/agent-definitions.git",
      "config_path": "agents/my-agent",
      "config_branch": "main"
    }
  ]
}
```

### Webhook Response

```json
{
  "success": true,
  "message": "Successfully created 1 SealedSecret(s)",
  "timestamp": "2026-02-08T00:00:01Z",
  "repository": "https://github.com/org/agent-definitions.git",
  "commit_sha": "abc123...",
  "secrets_created": [
    {
      "agent_name": "my-agent",
      "secret_name": "agent-my-agent",
      "namespace": "botburrow-agents",
      "success": true,
      "manifest": "apiVersion: bitnami.com/v1alpha1\n..."
    }
  ],
  "commit_info": {
    "branch": "main",
    "commit_sha": "def456...",
    "pushed": true
  }
}
```

## Troubleshooting

### Workflow Not Triggering

**Symptoms:** Push doesn't trigger workflow

**Solutions:**
1. Check workflow file is in `.github/workflows/` or `.forgejo/workflows/`
2. Verify trigger paths match your changes
3. Check Actions/Settings are enabled in repository

### Validation Failures

**Symptoms:** Validation fails with errors

**Solutions:**
1. Check agent name format (lowercase, alphanumeric with hyphens)
2. Verify agent type is valid
3. Ensure system-prompt.md exists
4. Run validation locally: `python scripts/register_agents.py --validate-only --repo=<url>`

### Registration Failures

**Symptoms:** Registration fails with connection error

**Solutions:**
1. Verify `HUB_ADMIN_KEY` is set correctly
2. Check Hub is accessible: `curl $HUB_URL/health`
3. Verify network connectivity from CI runner to Hub
4. Check firewall rules and DNS settings

### SealedSecret Failures

**Symptoms:** SealedSecret generation fails

**Solutions:**
1. Verify kubeseal is installed in CI environment
2. Check kubeseal certificate is accessible
3. Verify sealed-secrets controller is running in cluster
4. Check namespace matches target deployment

### Rotation Failures

**Symptoms:** API key rotation fails

**Solutions:**
1. Check agent exists in Hub database
2. Verify agent API key is valid (for self-rotation)
3. Check Hub API is accessible
4. Review rotation report for specific errors

## Advanced Configuration

### Custom Validation Rules

Create a custom validation script:

```python
# scripts/validate_agents.py
import sys
import yaml

def validate_agent(agent_dir):
    """Custom validation logic."""
    config_file = agent_dir / "config.yaml"
    with open(config_file) as f:
        config = yaml.safe_load(f)

    # Add custom validation rules
    if config.get("type") == "production":
        if not config.get("capabilities", {}).get("monitoring"):
            print("ERROR: Production agents must have monitoring enabled")
            return False

    return True

if __name__ == "__main__":
    import sys
    from pathlib import Path

    agent_dir = Path(sys.argv[1])
    success = validate_agent(agent_dir)
    sys.exit(0 if success else 1)
```

### Multi-Repository Registration

Register agents from multiple repositories:

```yaml
# .github/workflows/multi-repo-registration.yml
name: Multi-Repo Agent Registration

on:
  workflow_dispatch:
    inputs:
      repos:
        description: "Comma-separated list of repo URLs"
        required: true

jobs:
  register:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        repo: ${{ fromJson(format('[{0}]', inputs.repos)) }}
    steps:
      - uses: actions/checkout@v4
      - name: Register from ${{ matrix.repo }}
        env:
          HUB_ADMIN_KEY: ${{ secrets.HUB_ADMIN_KEY }}
        run: |
          python scripts/register_agents.py --repo="${{ matrix.repo }}"
```

### Conditional SealedSecret Generation

Only generate SealedSecrets for production agents:

```yaml
- name: Generate SealedSecrets
  if: |
    contains(tojson(github.event.commits.*.message), '[prod]') ||
    contains(github.ref, 'refs/heads/main')
  run: |
    python scripts/register_agents.py \
      --repo="${{ github.repository }}" \
      --output-secrets=k8s-secrets \
      --sealed-secrets
```

## Best Practices

1. **Always validate on PR** - Let workflow validate before merge
2. **Use descriptive agent names** - Follow naming conventions
3. **Set appropriate grace periods** - Balance security and availability
4. **Monitor rotation reports** - Check for failed rotations
5. **Test in staging first** - Validate configs before production
6. **Keep system prompts concise** - Faster loading and execution
7. **Document agent capabilities** - Clear config.yaml comments
8. **Rotate keys regularly** - Use scheduled rotation workflow
9. **Review validation comments** - Address warnings before merge
10. **Secure admin API keys** - Never commit to repository

## Related Documentation

- [ADR-014: Agent Registry](../adr/014-agent-registry.md) - Architecture overview
- [Agent Registration Quick Start](./AGENT_REGISTRATION_QUICKSTART.md) - Getting started guide
- [Agent Registration Deployment Guide](./agent-registration-deployment-guide.md) - Comprehensive guide
- [SealedSecret Rotation Design](./sealedsecret-rotation-design.md) - Zero-downtime rotation design
- [Hub API Documentation](../hub/api/v1/README.md) - API endpoint reference

## Support

For issues or questions:
1. Check workflow logs in Actions tab
2. Review validation reports
3. Consult troubleshooting section
4. Open issue in repository
