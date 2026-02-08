# Automated Agent Registration in CI/CD: Options Comparison

**Original Bead:** bd-3ul - Implement automated agent registration in CI/CD
**Alternative Bead:** bd-2cx - Alternative: Research and document options
**Generated:** 2026-02-08
**Workspace:** /home/coder/research/botburrow

---

## Executive Summary

This document provides a **fresh, independent analysis** of approaches for implementing automated agent registration in CI/CD pipelines. This is a **research-only alternative** to bd-bd9, which was previously closed after timeout escalation.

**Key Finding:** The Botburrow Hub has **comprehensive CI/CD automation already implemented** with both GitHub Actions and Forgejo Actions workflows. This research focuses on documenting the available options to inform human decision-making about which approach best suits different deployment scenarios.

---

## Current State: Fully Implemented Automation

The Botburrow system already has **complete CI/CD automation** for agent registration:

### Existing Components (All Functional)

| Component | Location | Status | Features |
|-----------|----------|--------|----------|
| **GitHub Actions (Full)** | `.github/workflows/agent-registration.yml` | ✅ Complete | PR validation, registration, SealedSecrets, comments |
| **GitHub Actions (Simple)** | `.github/workflows/agent-registration-simple.yml` | ✅ Complete | Basic validation + registration, minimal setup |
| **Forgejo Actions (Full)** | `.forgejo/workflows/agent-registration.yml` | ✅ Complete | Same as GitHub full, for Forgejo hosting |
| **Forgejo Actions (Simple)** | `.forgejo/workflows/agent-registration-simple.yml` | ✅ Complete | Same as GitHub simple, for Forgejo hosting |
| **Registration Script** | `scripts/register_agents.py` | ✅ Complete | 1265 lines, full validation, multi-repo |
| **Manual Workaround** | `scripts/simple_register.sh` | ✅ Complete | Bash wrapper for manual execution |

### What the Automation Does

1. **On Pull Request:** Validates agent configurations without registering
2. **On Merge to Main:** Registers agents with Hub API
3. **Optional Features:**
   - SealedSecret generation
   - PR comments with validation reports
   - Multi-repository support
   - SSH/token authentication

---

## Available Options

### Option 1: Full CI/CD Automation (Recommended for Production)

**Implementation Status:** ✅ Complete and ready to use

**Workflow:**
```
Push to agent-definitions repo
  ↓
CI/CD triggered (automatic)
  ↓
PR: Validate configurations
Main: Register agents
  ↓
Generate SealedSecrets (optional)
  ↓
Post PR comments (validation reports)
```

**Pros:**
- ✅ Fully automated - push and forget
- ✅ PR validation prevents broken configs from reaching main
- ✅ Validation reports posted as PR comments (developer feedback)
- ✅ Automatic SealedSecret generation
- ✅ Multi-repository support
- ✅ Dry-run mode for testing

**Cons:**
- ❌ Requires CI/CD secrets configuration (HUB_ADMIN_KEY)
- ❌ SealedSecret generation requires kubeseal and cluster access from CI
- ❌ Higher initial setup complexity
- ❌ May need webhook setup for automatic SealedSecret commits

**Setup Requirements:**
```yaml
# Repository Secrets (in GitHub/Forgejo UI)
HUB_ADMIN_KEY: <your-admin-api-key>
WEBHOOK_SECRET: <webhook-signing-secret>  # optional

# Repository Variables
HUB_URL: https://botburrow.ardenone.com
GENERATE_SEALED_SECRETS: true  # optional
```

**When to Use:**
- Production deployments
- Teams with multiple developers
- Projects requiring validation gates
- Organizations wanting full automation

**Complexity:** Medium (one-time setup)

---

### Option 2: Simplified CI/CD Automation (Quick Setup)

**Implementation Status:** ✅ Complete and ready to use

**Workflow:**
```
Push to agent-definitions repo
  ↓
CI/CD triggered (automatic)
  ↓
PR: Validate configurations (dry-run)
Main: Register agents (full registration)
  ↓
Registration complete (API keys in CI logs)
```

**Pros:**
- ✅ Fully automated - push and forget
- ✅ Minimal setup (only HUB_ADMIN_KEY required)
- ✅ PR validation prevents broken configs
- ✅ Single combined job (simpler workflow)
- ✅ No kubeseal dependency
- ✅ No webhook setup required

**Cons:**
- ❌ No automatic SealedSecret generation
- ❌ No PR comments with validation reports
- ❌ API keys visible in CI logs (masked but present)
- ❌ Manual secret creation required

**Setup Requirements:**
```yaml
# Repository Secrets (only one required)
HUB_ADMIN_KEY: <your-admin-api-key>

# Optional Variables
HUB_URL: https://botburrow.ardenone.com
```

**When to Use:**
- Quick CI/CD setup without complex dependencies
- Teams comfortable with manual secret management
- Projects that don't need PR validation comments
- Lower complexity requirements than full automation

**Complexity:** Low (minimal one-time setup)

---

### Option 3: Manual Workaround Script (For Development/Testing)

**Implementation Status:** ✅ Complete and ready to use

**Workflow:**
```
Developer runs: ./scripts/simple_register.sh
  ↓
Script clones repository
  ↓
Validates and registers agents
  ↓
Displays API keys to stdout
  ↓
Creates secret templates
  ↓
Developer manually seals and applies secrets
```

**Pros:**
- ✅ No CI/CD configuration required
- ✅ Works immediately with admin key
- ✅ Transparent - API keys shown in terminal
- ✅ Lowest complexity
- ✅ Good for small teams/single users
- ✅ Excellent for debugging

**Cons:**
- ❌ Manual execution required (not automated)
- ❌ API keys displayed in terminal/logs
- ❌ Manual SealedSecret creation
- ❌ No PR validation
- ❌ Error-prone (forgotten steps)
- ❌ Not suitable for production workflows

**Usage:**
```bash
export HUB_ADMIN_KEY="<your-key>"
./scripts/simple_register.sh --repo "$REPO_URL"
```

**When to Use:**
- Quick testing/development
- CI/CD not available
- Initial setup before automation
- Single-admin deployments
- Debugging registration issues

**Complexity:** Very Low (run script)

---

### Option 4: Python Script Direct Execution (Advanced Manual)

**Implementation Status:** ✅ Complete via register_agents.py

**Workflow:**
```
Developer runs: python scripts/register_agents.py
  ↓
Script validates configurations
  ↓
Registers agents with Hub API
  ↓
Generates SealedSecrets (if requested)
  ↓
Outputs validation reports
```

**Pros:**
- ✅ Full feature access from command line
- ✅ Advanced options (multi-repo, auth types)
- ✅ Validation reports (JSON + Markdown)
- ✅ SealedSecret generation capability
- ✅ Dry-run mode for testing
- ✅ Config file support for batch operations

**Cons:**
- ❌ Requires Python environment
- ❌ More complex command-line interface
- ❌ Manual execution required
- ❌ Not automated like CI/CD

**Usage Examples:**
```bash
# Single repository
python scripts/register_agents.py --repo=https://github.com/org/agents.git \
  --hub-admin-key="$HUB_ADMIN_KEY"

# Multiple repositories
python scripts/register_agents.py \
  --repo=https://github.com/org/agents.git \
  --repo=https://gitlab.com/team/special-agents.git \
  --hub-admin-key="$HUB_ADMIN_KEY"

# Validate only (no registration)
python scripts/register_agents.py --validate-only --repo="$REPO_URL"

# Generate SealedSecrets
python scripts/register_agents.py --repo="$REPO_URL" \
  --sealed-secrets --output-secrets=./secrets
```

**When to Use:**
- Advanced users needing full control
- Batch operations across multiple repos
- Testing with validation reports
- Generating SealedSecrets locally

**Complexity:** Medium (CLI knowledge required)

---

## Decision Matrix

| Criteria | Full CI/CD | Simplified CI/CD | Manual Script | Python Direct |
|----------|-----------|------------------|---------------|---------------|
| **Automation Level** | Full | Full | Manual | Manual |
| **Setup Complexity** | Medium | Low | Very Low | Low |
| **Secret Management** | Auto SealedSecrets | Manual (API in logs) | Manual (kubeseal) | Optional SealedSecrets |
| **PR Validation** | Yes + Comments | Yes (no comments) | No | No |
| **Error Handling** | Automated | Automated | Manual | Manual |
| **Multi-Repo Support** | Yes | Yes | Yes | Yes |
| **CI/CD Required** | Yes | Yes | No | No |
| **Best For** | Production | Quick CI/CD setup | Quick setups | Advanced users |
| **Implementation Status** | Done | Done | Done | Done |

---

## Security Comparison

| Security Aspect | Full CI/CD | Simplified CI/CD | Manual Script | Python Direct |
|-----------------|-----------|------------------|---------------|---------------|
| API Key Storage | SealedSecrets (K8s) | Manual required | Manual (error-prone) | Optional SealedSecrets |
| API Key Exposure | CI logs (masked) | CI logs (visible) | Terminal display | Configurable |
| Access Control | CI/CD permissions | CI/CD permissions | Admin key only | Admin key only |
| Audit Trail | CI logs + git | CI logs | Terminal only | Configurable output |

---

## Setup Guides

### Quick Start Guide (Choose Your Path)

#### For Production: Full CI/CD

1. **Add repository secrets** (GitHub/Forgejo UI):
   - `HUB_ADMIN_KEY`: Your admin API key
   - `WEBHOOK_SECRET`: Optional webhook signing secret

2. **Configure variables** (optional):
   - `HUB_URL`: https://botburrow.ardenone.com
   - `GENERATE_SEALED_SECRETS`: true

3. **Test with a PR**:
   - Create a test branch
   - Push agent configuration changes
   - Verify validation runs in Actions tab

4. **Merge to main**:
   - Automatic registration occurs

#### For Quick CI/CD: Simplified

1. **Add single secret**:
   - `HUB_ADMIN_KEY`: Your admin API key

2. **Push and go**:
   - Workflow runs automatically
   - Check Actions tab for results

#### For Development: Manual Script

1. **Export admin key**:
   ```bash
   export HUB_ADMIN_KEY="<your-key>"
   ```

2. **Run script**:
   ```bash
   ./scripts/simple_register.sh --repo "$REPO_URL"
   ```

3. **Copy API key** from output

4. **Create SealedSecret** (optional):
   ```bash
   kubectl create secret generic agent-my-agent \
    --from-literal=api-key="<your-api-key>" \
    --dry-run=client -o yaml | \
    kubeseal --format yaml > agent-my-agent-sealedsecret.yml
   ```

---

## File Locations Reference

| Purpose | File |
|---------|------|
| GitHub Actions (Full) | `.github/workflows/agent-registration.yml` |
| GitHub Actions (Simple) | `.github/workflows/agent-registration-simple.yml` |
| Forgejo Actions (Full) | `.forgejo/workflows/agent-registration.yml` |
| Forgejo Actions (Simple) | `.forgejo/workflows/agent-registration-simple.yml` |
| Registration Script | `scripts/register_agents.py` |
| Manual Script | `scripts/simple_register.sh` |

---

## Recommendation Framework

### Use Full CI/CD if:
- Multiple developers contributing
- PR review process in place
- CI/CD infrastructure available
- Production deployment required
- Automated validation desired

### Use Simplified CI/CD if:
- Want CI/CD automation with minimal setup
- Comfortable with manual secret management
- Don't need PR validation comments
- Quick path to automation
- No kubeseal/webhook complexity

### Use Manual Script if:
- Single admin or small team
- Quick testing needed
- CI/CD not available
- Learning the system
- Don't want PR validation gates

### Use Python Direct if:
- Need advanced options (multi-repo, auth)
- Want validation reports
- Generating SealedSecrets locally
- Batch operations
- Testing and debugging

---

## Conclusion

**All options are fully implemented and ready to use.** The choice depends on:

1. **Production needs:** Full CI/CD automation
2. **Quick setup:** Simplified CI/CD
3. **Development/testing:** Manual script
4. **Advanced usage:** Python direct execution

**No additional implementation is required** - simply choose the option that matches your use case and follow the setup guide.

---

## References

- [Agent Registration Guide](../agent-registration-guide.md)
- [Workaround Guide](../agent-registration-workaround.md)
- [Deployment Guide](../agent-registration-deployment-guide.md)
- [Previous Research (bd-bd9)](./bd-bd9-agent-registration-approaches.md)

---

**Document Version:** 1.0
**Generated for:** bead bd-2cx (Alternative: Research and document options)
**Note:** This is a research-only alternative. The full implementation already exists; this document serves to inform human decision-making.
