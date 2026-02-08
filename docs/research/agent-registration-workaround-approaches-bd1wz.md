# Agent Registration: Workaround Approach Research

**Research Bead:** bd-1wz - Alternative: Research and document options
**Parent Alternative:** bd-2ph - Alternative: Use workaround approach
**Original Bead:** bd-3ul - Implement automated agent registration in CI/CD
**Date:** 2026-02-08
**Status:** Research Complete

---

## Executive Summary

This research document provides a **concise comparison of available approaches** for agent registration in CI/CD, with a focus on practical workarounds when full automation encounters blockers.

**Key Finding:** **All approaches are already fully implemented** in the Botburrow codebase. The choice depends on deployment scenario and operational requirements.

---

## Available Approaches

### Option 1: Full CI/CD Automation (Production-Ready)

**Status:** ✅ Fully Implemented

**Files:**
- `.github/workflows/agent-registration.yml`
- `.forgejo/workflows/agent-registration.yml`

**What It Does:**
- Validates agent configurations on PR (with comments)
- Registers agents on merge to main
- Generates SealedSecrets (optional)
- Sends webhooks (optional)
- Multi-repository support

**Setup Required:**
```yaml
# Repository Secrets
HUB_ADMIN_KEY: <your-admin-api-key>

# Optional Variables
HUB_URL: https://botburrow.ardenone.com
GENERATE_SEALED_SECRETS: true
WEBHOOK_SECRET: <webhook-signing-secret>
```

**Best For:** Production deployments, teams with PR workflows

---

### Option 2: Simplified CI/CD Automation (Quick Setup)

**Status:** ✅ Fully Implemented

**Files:**
- `.github/workflows/agent-registration-simple.yml`
- `.forgejo/workflows/agent-registration-simple.yml`

**What It Does:**
- Validates and registers agents on push
- Single job (simpler than full)
- No SealedSecret generation
- No PR comments

**Setup Required:**
```yaml
# Repository Secrets (only one)
HUB_ADMIN_KEY: <your-admin-api-key>
```

**Best For:** Quick CI/CD setup, teams comfortable with manual secret management

---

### Option 3: Manual Workaround Script (Development/Testing)

**Status:** ✅ Fully Implemented

**Files:**
- `scripts/simple_register.sh`
- `scripts/register_agents.py`

**What It Does:**
- Manual agent registration
- Displays API keys to stdout
- Creates secret templates

**Usage:**
```bash
export HUB_ADMIN_KEY="<your-key>"
./scripts/simple_register.sh --repo "$REPO_URL"
```

**Best For:** Development, testing, debugging, single-admin setups

---

### Option 4: Direct Python Script (Advanced)

**Status:** ✅ Fully Implemented

**Files:**
- `scripts/register_agents.py`

**What It Does:**
- Full feature access from CLI
- Multi-repo support
- Validation reports (JSON + Markdown)
- SealedSecret generation

**Usage:**
```bash
python scripts/register_agents.py --repo="$REPO_URL" \
  --hub-admin-key="$HUB_ADMIN_KEY" \
  --sealed-secrets --output-secrets=./secrets
```

**Best For:** Advanced users, batch operations, local testing

---

## Quick Comparison

| Criteria | Full CI/CD | Simplified CI/CD | Manual Script | Python Direct |
|----------|-----------|------------------|---------------|---------------|
| **Automation** | Full | Full | Manual | Manual |
| **Setup** | Medium | Low | Very Low | Low |
| **Secrets** | Auto SealedSecrets | Manual | Manual | Optional |
| **PR Validation** | Yes + Comments | Yes (no comments) | No | No |
| **Best For** | Production | Quick CI/CD | Development | Advanced |

---

## Decision Guide

**Choose Full CI/CD if:**
- Production deployment required
- Multiple developers need PR validation
- Want automated SealedSecret generation
- Have CI/CD infrastructure available

**Choose Simplified CI/CD if:**
- Want automation with minimal setup
- Comfortable with manual secret management
- Don't need PR validation comments
- Quick path to automation

**Choose Manual Script if:**
- Single admin or small team
- Quick testing needed
- CI/CD not available
- Debugging registration issues

**Choose Python Direct if:**
- Need advanced options (multi-repo, auth)
- Want validation reports
- Generating SealedSecrets locally
- Batch operations

---

## Implementation Status Summary

All approaches are **fully implemented and ready to use**:

| Approach | Status | Complexity | Setup Time |
|----------|--------|------------|------------|
| Full CI/CD | ✅ Complete | Medium | ~30 min |
| Simplified CI/CD | ✅ Complete | Low | ~10 min |
| Manual Script | ✅ Complete | Very Low | ~5 min |
| Python Direct | ✅ Complete | Low | ~5 min |

---

## Existing Documentation References

- [Agent Registration Guide](../agent-registration-guide.md)
- [Workaround Guide](../agent-registration-workaround.md)
- [Deployment Guide](../agent-registration-deployment-guide.md)
- [Quick Start](../AGENT_REGISTRATION_QUICKSTART.md)
- [Previous Research (bd-bd9)](../alternatives/bd-bd9-agent-registration-approaches.md)
- [Previous Research (bd-3r5)](../alternatives/bd-3r5-agent-registration-comprehensive-options.md)

---

## Conclusion

**No new implementation is required.** All approaches are fully implemented in the Botburrow codebase. Simply choose the option that matches your use case and follow the setup guide.

**Recommendation:** Start with Simplified CI/CD for quick automation, migrate to Full CI/CD when production requirements grow.
