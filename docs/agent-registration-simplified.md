# Simplified Agent Registration (MVP)

> **Superseded (2026-09-16):** The `.github/workflows/` and
> `.forgejo/workflows/` files referenced below have been removed — GitHub
> Actions are disabled org-wide and Forgejo Actions is not a CI path. The
> simplified registration path is now the manual script run with the key
> from OpenBao; see
> [agent-registration-simple-guide.md](./agent-registration-simple-guide.md)
> and [agent-registration-cicd-automation-guide.md](./agent-registration-cicd-automation-guide.md).
> This document is kept as a historical record of the MVP decision.

## Overview

This document describes the **minimal viable implementation** of automated agent registration in CI/CD, reducing scope to core functionality while deferring nice-to-have features.

## Solution: Already Implemented

The simplified approach **already exists** as `.github/workflows/agent-registration-simple.yml`. This workflow provides:

### Core Features (Included)

1. **Validation on every push/PR** - Catches configuration errors early
2. **Automated registration on merge to main/master** - No manual steps required
3. **Mode switching** - Automatically validates (PRs) or registers (main branch)
4. **Basic error reporting** - Fails workflow on validation errors

### What Was Removed (Nice-to-Have Features)

1. **SealedSecret generation** - API keys must be manually extracted from Hub logs
2. **PR comments** - No automated validation results in PR threads
3. **Artifact uploads** - No persistent storage of validation reports
4. **Webhook integration** - No external notifications
5. **Separate jobs** - Single job combines validation and registration
6. **PR status checks** - No GitHub status API integration

## Usage

### Prerequisites

1. **Add HUB_ADMIN_KEY to repository secrets:**
   ```
   GitHub Repository → Settings → Secrets and variables → Actions → New repository secret
   Name: HUB_ADMIN_KEY
   Value: <your-admin-api-key>
   ```

2. **Optionally set HUB_URL (if not using default):**
   ```
   GitHub Repository → Settings → Secrets and variables → Actions → Variables → New repository variable
   Name: HUB_URL
   Value: https://your-hub-url.com
   ```

### Workflow Behavior

```
┌─────────────────────────────────────────────────────────────────┐
│  Push to agents/ directory                                      │
└────────────────────────────┬────────────────────────────────────┘
                             │
                    ┌────────▼────────┐
                    │  Is this a PR?  │
                    └────────┬────────┘
                             │
              ┌──────────────┴──────────────┐
              │                             │
             YES                           NO
              │                             │
              ▼                             ▼
    ┌─────────────────┐           ┌─────────────────┐
    │ VALIDATION mode │           │ Is branch main  │
    │ (dry-run)       │           │ or master?      │
    └─────────────────┘           └────────┬────────┘
                                           │
                              ┌────────────┴────────────┐
                              │                         │
                             YES                       NO
                              │                         │
                              ▼                         ▼
                    ┌─────────────────┐       ┌─────────────────┐
                    │ REGISTRATION    │       │ VALIDATION mode │
                    │ mode            │       │ (validate-only) │
                    │ (updates Hub)   │       └─────────────────┘
                    └─────────────────┘
```

### Setup Steps

1. **Enable the workflow:**
   - The workflow is already in `.github/workflows/agent-registration-simple.yml`
   - It triggers on push to `agents/**` directory

2. **Test with a PR:**
   ```bash
   # Create a branch and modify an agent config
   git checkout -b test-agent-update
   # Edit agents/your-agent/config.yaml
   git commit -am "test: update agent config"
   git push origin test-agent-update
   # Create PR and check workflow results
   ```

3. **Merge to main for registration:**
   ```bash
   # Merge PR
   # Check workflow logs for registered API keys
   ```

## Comparison: Full vs Simplified

| Feature | Full Workflow | Simplified Workflow |
|---------|---------------|---------------------|
| Validation | ✅ Separate job | ✅ Combined with registration |
| Registration | ✅ On main push | ✅ On main push |
| PR Comments | ✅ Detailed dry-run | ❌ Not included |
| SealedSecrets | ✅ Auto-generated | ❌ Manual extraction |
| Artifacts | ✅ 7-day retention | ❌ Not included |
| Webhooks | ✅ Optional | ❌ Not included |
| Mode Switching | ❌ Separate jobs | ✅ Smart mode detection |
| Complexity | High | Low |
| Dependencies | kubeseal, webhook script | pyyaml, requests only |

## API Key Management (Simplified)

Since SealedSecret generation is removed:

### Option 1: Extract from Hub logs
```bash
# After workflow runs, API keys are in the registration output
# Copy from workflow logs or Hub response
```

### Option 2: Manual SealedSecret creation
```bash
# Get API key from Hub (via API or logs)
# Create SealedSecret manually:
echo -n "your-api-key" | kubeseal --format yaml > agent-name-sealedsecret.yml
```

### Option 3: Use the full workflow for initial setup
```bash
# For one-time setup, temporarily use full workflow
# Then switch back to simplified for ongoing operation
```

## Why Simplified?

### Pros
- **Faster setup** - No kubeseal, webhook infrastructure
- **Easier debugging** - Single job, simple logic
- **Reduced dependencies** - Only Python + pyyaml + requests
- **Lower maintenance** - Fewer moving parts

### Cons
- **Manual API key handling** - Must extract from logs
- **No PR feedback** - Must check Actions tab for validation results
- **No persistent reports** - Validation results lost after workflow completes

## Migration Path

If you need to upgrade from simplified to full workflow later:

1. Add HUB_ADMIN_KEY secret (already done)
2. Add kubeseal to runner path (for SealedSecrets)
3. Create webhook endpoint (for notifications)
4. Switch to `.github/workflows/agent-registration.yml`
5. Enable optional features via variables:
   - `GENERATE_SEALED_SECRETS=true`
   - `SEND_WEBHOOK=true`

## Bead Context

This implementation fulfills bead **bd-3tz** (Alternative: Simplify requirements), which was created as a simplified-scope alternative to bead **bd-1ky** (Alternative: Research and document options), which in turn was an alternative to the original bead **bd-3ul** (Implement automated agent registration in CI/CD).

The simplified workflow demonstrates that the core requirement - automated agent registration in CI/CD - can be achieved with minimal complexity, deferring advanced features until they're proven necessary.
