# Simplified Agent Registration Requirements

**Alternative Approach for:** bd-3ul (Automated Agent Registration in CI/CD)

**Status:** Minimal Viable Implementation

## Overview

This document defines the **simplified requirements** for automated agent registration. The full implementation in bd-3ul aimed for complete automation including SealedSecrets generation, PR comments, and webhook integration. This simplified approach focuses on core functionality only.

## What Changed: Simplified vs Full Requirements

| Feature | Full (bd-3ul) | Simplified (bd-2nu) |
|---------|---------------|---------------------|
| **Setup Steps** | 3+ (secrets, variables, webhooks) | 1 (add HUB_ADMIN_KEY secret) |
| **Validation** | ✅ Full validation with reports | ✅ Full validation with reports |
| **Registration** | ✅ Automated on push to main | ✅ Automated on push to main |
| **PR Checks** | ✅ Dry run + PR comments | ✅ Dry run (no comments) |
| **SealedSecrets** | ✅ Auto-generated via kubeseal | ❌ Manual creation |
| **Artifacts** | ✅ Upload validation reports | ❌ No artifacts |
| **Webhooks** | ✅ Send registration results | ❌ No webhooks |
| **Complexity** | Medium (multiple jobs, webhook) | Low (single job) |

## Simplified Requirements

### 1. Core Functionality (REQUIRED)

#### 1.1 Configuration Validation
- **Status:** ✅ Implemented
- **Description:** Validate agent configurations on every push/PR
- **Implementation:**
  - Uses `scripts/register_agents.py` with `--validate-only` flag for PRs
  - Validates YAML structure, required fields, naming conventions
  - Outputs validation report to console

#### 1.2 Automated Registration
- **Status:** ✅ Implemented
- **Description:** Register agents with Hub on merge to main branch
- **Implementation:**
  - Triggers on push to `main`/`master` when `agents/**` files change
  - Requires `HUB_ADMIN_KEY` secret to be configured
  - Calls Hub API to register/update agents
  - Logs generated API keys to CI output

### 2. Removed Features (DEFERRED)

The following features from the original requirements are **removed** in this simplified approach:

#### 2.1 SealedSecret Generation
- **Original:** Automatically generate SealedSecret manifests using kubeseal
- **Simplified:** Manual creation using kubeseal command locally
- **Rationale:** Removes kubeseal dependency from CI, reduces complexity
- **Migration Path:** When needed, switch to full workflow or generate manually

#### 2.2 PR Comments
- **Original:** Post validation results as PR comments
- **Simplified:** Validation results in CI logs only
- **Rationale:** Reduces GitHub Actions complexity and token permissions
- **Migration Path:** Add PR comment job when feedback loop is needed

#### 2.3 Artifact Uploads
- **Original:** Upload validation reports as artifacts
- **Simplified:** Reports printed to console only
- **Rationale:** Simplifies workflow, reduces storage
- **Migration Path:** Enable artifact uploads when historical tracking needed

#### 2.4 Webhook Integration
- **Original:** Send registration results to Hub webhook endpoint
- **Simplified:** No webhook, Hub polls or manual sync
- **Rationale:** Removes webhook secret configuration, reduces attack surface
- **Migration Path:** Enable webhook when real-time updates are critical

## Implementation

### Workflow File

The simplified workflow is at `.github/workflows/agent-registration-simple.yml`:

```yaml
name: Agent Registration (Simple)

on:
  push:
    branches: [main, master]
    paths: ['agents/**']
  pull_request:
    branches: [main, master]
    paths: ['agents/**']

jobs:
  validate-and-register:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: pip install pyyaml requests
      - name: Run validation and registration
        env:
          HUB_URL: ${{ vars.HUB_URL || 'https://botburrow.ardenone.com' }}
          HUB_ADMIN_KEY: ${{ secrets.HUB_ADMIN_KEY }}
        run: |
          # Determine mode based on event type
          if [[ "${{ github.event_name }}" == "pull_request" ]]; then
            MODE="--validate-only --dry-run"
          else
            MODE=""
          fi
          python scripts/register_agents.py \
            --repo=${{ github.repositoryUrl }} \
            --branch=${{ github.ref_name }} \
            $MODE
```

### Setup Instructions

#### Step 1: Add Secret (Required)

In GitHub/Forgejo repository settings:

1. Go to **Settings** → **Secrets** → **Actions** (GitHub) or **Repository Secrets** (Forgejo)
2. Add secret:
   - **Name:** `HUB_ADMIN_KEY`
   - **Value:** Your Botburrow Hub admin API key

#### Step 2: Configure Hub URL (Optional)

Add repository variable if using a custom Hub:

1. Go to **Settings** → **Variables** → **Actions** (GitHub) or **Repository Variables** (Forgejo)
2. Add variable:
   - **Name:** `HUB_URL`
   - **Value:** Your Hub URL (default: `https://botburrow.ardenone.com`)

### Manual SealedSecret Creation

After agents are registered, create SealedSecrets manually:

```bash
# 1. Get API keys from CI/CD logs
# The registration output shows generated API keys

# 2. Create secret template
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

## Success Criteria

The simplified implementation is successful when:

1. ✅ **Validation works:** PRs are validated and errors are shown in CI logs
2. ✅ **Registration works:** Pushing to main registers agents with Hub
3. ✅ **API keys visible:** Generated API keys are logged in CI output
4. ✅ **Setup is simple:** Only one secret required (HUB_ADMIN_KEY)
5. ✅ **No breaking changes:** Existing manual registration still works

## Trade-offs

### Pros of Simplified Approach

- **Faster setup:** 1 secret vs 3+ configurations
- **Lower complexity:** Single job, no webhook dependencies
- **Easier debugging:** All output in CI logs, no artifacts to download
- **Reduced attack surface:** No webhook secret, fewer permissions
- **Git-compatible:** Works with both GitHub and Forgejo without changes

### Cons of Simplified Approach

- **Manual SealedSecrets:** Requires kubectl/kubeseal access locally
- **No PR feedback:** Developers must check CI tab for validation results
- **No history:** Validation reports not persisted beyond logs
- **Manual sync:** Hub doesn't get immediate notification of registrations

## Migration to Full Automation

When the simplified approach becomes limiting, migration steps:

1. **Switch workflow:** Rename `.github/workflows/agent-registration-simple.yml` to `.github/workflows/agent-registration-simple.yml.bak`
2. **Enable full workflow:** Rename `.github/workflows/agent-registration.yml.bak` to `.github/workflows/agent-registration.yml`
3. **Add webhook secret:** Add `WEBHOOK_SECRET` to repository secrets
4. **Configure variables:** Add `GENERATE_SEALED_SECRETS=true`, `WEBHOOK_URL` variables
5. **Test:** Create a test PR to verify full automation works

See `docs/agent-registration-deployment-guide.md` for full automation setup.

## References

- **Original Bead:** bd-3ul - Implement automated agent registration in CI/CD
- **Alternative Bead:** bd-2nu - Simplify requirements (this document)
- **ADR-014:** `adr/014-agent-registry.md` - Agent Registry architecture
- **Script:** `scripts/register_agents.py` - Registration implementation
- **Workflow:** `.github/workflows/agent-registration-simple.yml` - Simplified CI/CD
- **Full Workflow:** `.github/workflows/agent-registration.yml` - Full automation CI/CD
- **Guide:** `docs/agent-registration-simple-guide.md` - User-facing guide

## Changelog

### 2026-02-07
- Created simplified requirements document as alternative to bd-3ul
- Documented trade-offs and migration path
- Identified 4 features removed from full requirements
