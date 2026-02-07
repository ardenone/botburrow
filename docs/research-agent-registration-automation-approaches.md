# Research: Automated Agent Registration in CI/CD - Approaches Comparison

**Research Date:** 2026-02-07
**Updated:** 2026-02-07
**Related Bead:** bd-bd9 (Alternative: Research and document options)
**Original Bead:** bd-3ul - Implement automated agent registration in CI/CD

## Executive Summary

This document researches and compares different approaches for automating agent registration in CI/CD pipelines for the Botburrow system. The research finds that **the full CI/CD automation is already implemented** with GitHub Actions and Forgejo Actions workflows, but a **simplified workaround approach** exists for when full automation encounters blockers.

## Current State Assessment

### Already Implemented Components

1. **GitHub Actions Workflow** (`.github/workflows/agent-registration.yml`)
   - ✅ Validates agent configurations on push/PR
   - ✅ Registers agents automatically on merge to main
   - ✅ Generates SealedSecrets (optional)
   - ✅ Posts validation reports as PR comments
   - ✅ Dry-run mode for PR validation

2. **Forgejo Actions Workflow** (`.forgejo/workflows/agent-registration.yml`)
   - ✅ Same functionality as GitHub Actions
   - ✅ Configured for Forgejo git hosting
   - ✅ Container-based execution

3. **Core Registration Script** (`scripts/register_agents.py`)
   - ✅ Full validation with comprehensive error reporting
   - ✅ Multi-repository support
   - ✅ Authentication handling (none, token, SSH)
   - ✅ SealedSecret generation capability
   - ✅ JSON and Markdown report output
   - ✅ CI/CD integration hooks

4. **Documentation**
   - ✅ Agent Registration Guide (`docs/agent-registration-guide.md`)
   - ✅ Workaround Guide (`docs/agent-registration-workaround.md`)
   - ✅ Deployment Guide (`docs/agent-registration-deployment-guide.md`)

5. **Workaround Script** (`scripts/simple_register.sh`)
   - ✅ Simplified bash wrapper for manual registration
   - ✅ Displays API keys to stdout
   - ✅ Creates secret templates for manual sealing

## Problem Statement

The original bead bd-3ul requested CI/CD automation for agent registration. The implementation is complete, but workers encountered blockers requiring alternative approaches:

**Blockers Encountered:**
1. GitHub/Forgejo Actions require repository secrets to be configured
2. SealedSecret generation requires kubeseal and cluster access
3. Webhook integration for automatic SealedSecret commits needs additional setup
4. Multi-repository authentication complexity

## Approaches Comparison

### Approach 1: Full CI/CD Automation (Implemented - bd-3ul)

**Status:** ✅ Implemented and ready to use

**Description:** Complete automation via GitHub/Forgejo Actions workflows.

**Workflow:**
```
Push to agent-definitions repo
  ↓
CI/CD workflow triggered automatically
  ↓
Validate agent configurations
  ↓
On main branch: Register with Hub API
  ↓
Generate SealedSecrets (optional)
  ↓
Send webhook to commit SealedSecrets (optional)
  ↓
Registration complete
```

**Components:**
- GitHub Actions workflow (`.github/workflows/agent-registration.yml`)
- Forgejo Actions workflow (`.forgejo/workflows/agent-registration.yml`)
- Hub API integration
- SealedSecret generation
- PR validation with comments

**Advantages:**
- ✅ Fully automated - push and done
- ✅ PR validation prevents broken configs
- ✅ Idempotent - safe to re-run
- ✅ Comprehensive validation reports
- ✅ Multi-repository support
- ✅ Dry-run mode for testing

**Disadvantages:**
- ❌ Requires CI/CD secrets configuration
- ❌ SealedSecret generation requires cluster access from CI
- ❌ Webhook setup for automatic SealedSecret commits
- ❌ Higher initial setup complexity

**Setup Requirements:**
1. Add `HUB_ADMIN_KEY` to repository secrets
2. Configure `HUB_URL` repository variable
3. (Optional) Set up `WEBHOOK_SECRET` for SealedSecret webhooks
4. Enable workflows in repository settings

**When to Use:**
- Production deployments
- Teams with CI/CD access
- Projects requiring validation gates
- Multi-user collaboration

---

### Approach 2: Simplified Manual Workaround (bd-2b8)

**Status:** ✅ Implemented as fallback

**Description:** Manual script execution with simplified workflow.

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

**Components:**
- `scripts/simple_register.sh` - Bash wrapper
- `scripts/register_agents.py` - Core registration logic
- Manual kubeseal execution

**Advantages:**
- ✅ No CI/CD configuration required
- ✅ Works immediately with admin key
- ✅ Transparent - API keys shown in terminal
- ✅ Lower complexity
- ✅ Good for small teams/single users

**Disadvantages:**
- ❌ Manual execution required
- ❌ API keys displayed in terminal/logs
- ❌ Manual SealedSecret creation
- ❌ No PR validation
- ❌ Error-prone (forgotten steps)

**When to Use:**
- Quick testing/development
- CI/CD not available
- Initial setup before automation
- Single-admin deployments

---

### Approach 3: Hybrid Semi-Automated (Proposed)

**Status:** ⚠️ Not implemented - potential future enhancement

**Description:** CI/CD validates and registers, but secrets are handled separately.

**Workflow:**
```
Push to agent-definitions repo
  ↓
CI/CD validates configurations (PR checks)
  ↓
CI/CD registers agents on merge (no secrets)
  ↓
Separate process generates and commits SealedSecrets
  ↓
Manual or scheduled job applies secrets
```

**Components:**
- CI/CD for validation only
- Hub API for registration (without secret storage)
- Separate secret management workflow

**Advantages:**
- ✅ Validation automation without complex secret handling
- ✅ Separation of concerns (validation vs secrets)
- ✅ Can use external secret managers (HashiCorp Vault, etc.)
- ✅ More flexible secret rotation

**Disadvantages:**
- ❌ Not end-to-end automated
- ❌ Requires separate secret management system
- ❌ Additional workflow to maintain

**When to Use:**
- Organizations with existing secret management
- Regulatory environments requiring segregation
- Teams using external secret managers

---

### Approach 4: GitOps-Only Automation (Proposed)

**Status:** ⚠️ Not implemented - alternative architecture

**Description:** Everything stored in git, no registration API calls.

**Workflow:**
```
Developer commits agent config to repo
  ↓
Agent definition includes API key (encrypted/at-rest)
  ↓
ArgoCD syncs to cluster
  ↓
Runner discovers agents directly from git
  ↓
No Hub registration required
```

**Components:**
- Agent configs with encrypted API keys
- ArgoCD/FluxCD for deployment
- Git-based agent discovery

**Advantages:**
- ✅ Pure GitOps
- ✅ No external Hub API dependency
- ✅ Full audit trail in git
- ✅ Simple rollback

**Disadvantages:**
- ❌ Requires Hub schema changes (git-discovery mode)
- ❌ Runners need git polling for all agents
- ❌ No centralized agent registry
- ❌ API key encryption in git complexity
- ❌ Major architecture change

**When to Use:**
- Pure GitOps environments
- Teams willing to modify Hub architecture
- Deployments where Hub API access is limited

---

### Approach 5: Simplified CI/CD Automation (Minimal Viable)

**Status:** ✅ Implemented as alternative to full workflow

**Description:** Minimal CI/CD automation with reduced complexity.

**Workflow:**
```
Push to agent-definitions repo
  ↓
CI/CD workflow triggered automatically
  ↓
Validate agent configurations (PR: dry-run, Main: full)
  ↓
On main branch: Register with Hub API
  ↓
Registration complete (API keys displayed in logs)
```

**Components:**
- Simplified GitHub Actions workflow (`.github/workflows/agent-registration-simple.yml`)
- Simplified Forgejo Actions workflow (`.forgejo/workflows/agent-registration-simple.yml`)
- Hub API integration
- Basic validation

**Advantages:**
- ✅ Fully automated - push and done
- ✅ Minimal setup (only HUB_ADMIN_KEY secret required)
- ✅ PR validation prevents broken configs
- ✅ Single combined job (validate + register)
- ✅ No kubeseal dependency
- ✅ No webhook setup required

**Disadvantages:**
- ❌ No automatic SealedSecret generation
- ❌ No PR comments with validation reports
- ❌ No artifact uploads
- ❌ API keys shown in CI logs (though masked)
- ❌ Manual secret creation required

**Setup Requirements:**
1. Add `HUB_ADMIN_KEY` to repository secrets
2. (Optional) Configure `HUB_URL` repository variable
3. Enable simplified workflow in repository

**When to Use:**
- Quick CI/CD setup without complex dependencies
- Teams comfortable with manual secret management
- Projects that don't need PR validation comments
- Lower complexity requirements than full automation

**File Locations:**
- GitHub: `.github/workflows/agent-registration-simple.yml`
- Forgejo: `.forgejo/workflows/agent-registration-simple.yml`

---

## Comparison Matrix

| Aspect | Full CI/CD | Simplified CI/CD | Manual Workaround | Hybrid Semi-Auto | GitOps-Only |
|--------|-----------|------------------|-------------------|------------------|-------------|
| **Automation Level** | Full | Full | Manual | Partial | Full (git-synced) |
| **Setup Complexity** | Medium | Low | Low | Medium | High |
| **Secret Management** | Automated SealedSecrets | Manual (API in logs) | Manual kubeseal | External/separate | Encrypted in git |
| **PR Validation** | ✅ Yes + Comments | ✅ Yes (no comments) | ❌ No | ✅ Yes | ✅ Yes |
| **Error Handling** | Automated | Automated | Manual | Semi-automated | Automated |
| **Multi-Repo Support** | ✅ Yes | ✅ Yes | ✅ Yes | ✅ Yes | ✅ Yes |
| **CI/CD Required** | ✅ Yes | ✅ Yes | ❌ No | ✅ Yes | ❌ No (ArgoCD only) |
| **Hub API Required** | ✅ Yes | ✅ Yes | ✅ Yes | ✅ Yes | ❌ No |
| **Architecture Changes** | ❌ No | ❌ No | ❌ No | ❌ No | ✅ Yes |
| **Implementation Status** | ✅ Done | ✅ Done | ✅ Done | ❌ Not done | ❌ Not done |
| **Best For** | Production | Quick CI/CD setup | Quick setups | Complex orgs | Pure GitOps |

## Security Comparison

| Security Aspect | Full CI/CD | Simplified CI/CD | Manual Workaround | Hybrid | GitOps-Only |
|-----------------|-----------|------------------|-------------------|--------|--------------|
| API Key Exposure | ❌ CI logs (masked) | ⚠️ CI logs (visible) | ⚠️ Terminal display | ⚠️ Depends on external | ✅ Encrypted in git |
| Secret Storage | ✅ SealedSecrets | ⚠️ Manual required | ⚠️ Manual (error-prone) | ⚠️ External | ✅ Encrypted |
| Access Control | ✅ CI/CD permissions | ✅ CI/CD permissions | ✅ Admin key only | ✅ CI + external | ✅ Git permissions |
| Audit Trail | ✅ CI logs + git | ⚠️ Terminal only | ✅ CI + external | ✅ Git only |
| Key Rotation | ✅ Automated (future) | ⚠️ Manual | ✅ External handles | ✅ Git commit |

## Recommendations

### For Production Deployments
**Use: Full CI/CD Automation (Approach 1)**

Justification:
- Already implemented and tested
- Provides validation gates
- Automated with manual fallback available
- Industry standard practice

**Setup Steps:**
```bash
# 1. Add repository secrets in GitHub/Forgejo
HUB_ADMIN_KEY=<your-admin-key>
WEBHOOK_SECRET=<webhook-signing-secret>  # optional

# 2. Configure variables
HUB_URL=https://botburrow.ardenone.com
GENERATE_SEALED_SECRETS=true  # optional

# 3. Test with PR
# Create a test PR and verify validation runs

# 4. Merge to main
# Automatic registration occurs on merge
```

### For Quick Testing/Development
**Use: Manual Workaround (Approach 2)**

Justification:
- Fastest to get started
- Transparent for debugging
- Good for single-admin setups
- Already documented

**Usage:**
```bash
export HUB_ADMIN_KEY="<your-key>"
./scripts/simple_register.sh --repo "$REPO_URL"
```

### For Future Enhancement
**Consider: Hybrid Semi-Automated (Approach 3)**

Justification:
- Leverages existing infrastructure
- Compatible with external secret managers
- Good for enterprise environments

**Implementation Path:**
1. Keep existing CI/CD validation
2. Add optional external secret manager integration
3. Create secret sync job

### Not Recommended
**GitOps-Only (Approach 4)** - Requires significant architecture changes with limited benefit over existing approach.

## Technical Implementation Details

### Full CI/CD Implementation Status

| Component | Status | Location | Notes |
|-----------|--------|----------|-------|
| GitHub Actions workflow | ✅ Complete | `.github/workflows/agent-registration.yml` | 262 lines, 3 jobs |
| Forgejo Actions workflow | ✅ Complete | `.forgejo/workflows/agent-registration.yml` | 215 lines, 3 jobs |
| Registration script | ✅ Complete | `scripts/register_agents.py` | 1265 lines |
| Validation reports | ✅ Complete | JSON + Markdown output | AgentValidationReport class |
| SealedSecret generation | ✅ Complete | generate_sealed_secret() | Requires kubeseal |
| PR comment posting | ✅ Complete | GitHub Actions + Forgejo API | Dry-run reports |
| Multi-repo support | ✅ Complete | RepoConfig, GitRepository classes | Token/SSH auth |

### Known Limitations

1. **SealedSecret Generation in CI**
   - Requires kubeseal in CI environment
   - Needs cluster access from CI runner
   - Workaround: manual sealing or webhook

2. **Webhook Integration**
   - Optional feature for automatic SealedSecret commits
   - Requires additional webhook endpoint on Hub
   - Not required for basic automation

3. **Private Repository Authentication**
   - SSH keys and tokens must be in CI/CD secrets
   - Multiple repos = multiple secrets to manage
   - Mitigation: use deploy key with limited access

## Decision Framework

### Choose Full CI/CD if:
- ✅ Multiple developers contributing
- ✅ PR review process in place
- ✅ CI/CD infrastructure available
- ✅ Production deployment
- ✅ Want automated validation

### Choose Manual Workaround if:
- ✅ Single admin or small team
- ✅ Quick testing needed
- ✅ CI/CD not available
- ✅ Learning the system
- ✅ Don't want PR validation gates

### Choose Simplified CI/CD if:
- ✅ Want CI/CD automation with minimal setup
- ✅ Comfortable with manual secret management
- ✅ Don't need PR validation comments
- ✅ Quick path to automation
- ✅ No kubeseal/webhook complexity

### Choose Hybrid if:
- ✅ External secret manager required
- ✅ Regulatory segregation of duties
- ✅ Existing secret management infrastructure
- ✅ Need custom secret rotation policies

## Cost/Benefit Analysis

| Approach | Implementation Cost | Maintenance Cost | Security Benefit | Automation Benefit |
|----------|---------------------|------------------|------------------|-------------------|
| Full CI/CD | Low (done) | Low | High | High |
| Simplified CI/CD | Very Low (done) | Low | Medium | High |
| Manual | Zero | Medium (manual steps) | Medium | Low |
| Hybrid | Medium | Medium | High (external) | Medium |
| GitOps-Only | High | Low | High | High |

## Migration Path

### From Manual to Full CI/CD

```bash
# Step 1: Verify CI/CD workflows are in place
ls -la .github/workflows/agent-registration.yml
ls -la .forgejo/workflows/agent-registration.yml

# Step 2: Configure repository secrets
# In GitHub/Forgejo UI: Settings → Secrets → Add
# HUB_ADMIN_KEY = <your-admin-key>

# Step 3: Configure variables (optional)
HUB_URL=https://botburrow.ardenone.com
GENERATE_SEALED_SECRETS=true

# Step 4: Test with validation-only run
git push origin test-branch
# Check Actions tab for workflow results

# Step 5: Merge to main for full registration
```

## Conclusion

The **Full CI/CD Automation approach (bd-3ul) is complete and recommended** for production use. The Manual Workaround (bd-2b8) serves as an excellent fallback for development and testing scenarios.

### Key Findings

1. **Implementation is complete** - Both GitHub and Forgejo Actions workflows exist and are functional
2. **Documentation exists** - Comprehensive guides for manual and automated approaches
3. **No blocker for automation** - The automation works; setup just requires secrets configuration
4. **Workaround is valuable** - Provides a simpler path for quick setups and testing

### Recommended Next Steps

1. **For production with full features:** Use Full CI/CD Automation - configure CI/CD secrets and enable the full workflows
2. **For quick CI/CD setup with minimal complexity:** Use Simplified CI/CD - just add HUB_ADMIN_KEY secret
3. **For testing/development:** Use the manual workaround script
4. **For enhancement:** Consider hybrid approach if external secret management is needed

## References

- [ADR-014: Agent Registry & Seeding](../adr/014-agent-registry.md) - Architecture decision
- [Agent Registration Guide](./agent-registration-guide.md) - Full documentation
- [Workaround Guide](./agent-registration-workaround.md) - Manual process
- [Deployment Guide](./agent-registration-deployment-guide.md) - CI/CD setup
- [GitHub Actions Workflow (Full)](../.github/workflows/agent-registration.yml) - Implementation
- [GitHub Actions Workflow (Simplified)](../.github/workflows/agent-registration-simple.yml) - Minimal implementation
- [Forgejo Actions Workflow (Full)](../.forgejo/workflows/agent-registration.yml) - Implementation
- [Forgejo Actions Workflow (Simplified)](../.forgejo/workflows/agent-registration-simple.yml) - Minimal implementation
- [Registration Script](../scripts/register_agents.py) - Core logic

---

**Document Version:** 1.1
**Last Updated:** 2026-02-07
**Author:** Research for bd-bd9 (Alternative: Research and document options)
**Note:** This research documents that bd-3ul (Full CI/CD Automation) is already implemented and ready for use.
