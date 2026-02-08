# bd-2vr Blocker Analysis

**Date:** 2026-02-08
**Bead:** bd-2vr (Implement Forgejo push mirrors to GitHub)
**Status:** BLOCKED by bd-3vqp (RBAC issue in /home/coder workspace)

## Summary

bd-2vr cannot proceed because Forgejo is not healthy. The Forgejo pod is in CrashLoopBackOff due to an s6-overlay permission error. A fix exists (commit 1a3e8cbfe in ardenone-cluster repo) but cannot be applied due to RBAC restrictions tracked by bead bd-3vqp in the /home/coder workspace.

## Current State

### Forgejo Status
```
NAME:                     forgejo-6744c7dc-64stz
READY:                    1/2
STATUS:                   CrashLoopBackOff
RESTARTS:                 37 (3m7s ago)
ERROR:                    s6-svscan: fatal: unable to open .s6-svscan/lock: Permission denied
```

### Blocker Details
**Bead ID:** bd-3vqp
**Workspace:** /home/coder (global workspace)
**Title:** HUMAN: RBAC permission needed to apply Forgejo deployment fix
**Priority:** P0
**Status:** OPEN

### Required Fix (Already in Git)
**Commit:** 1a3e8cbfe
**Repo:** ardenone-cluster
**File:** cluster-configuration/apexalgo-iad/forgejo/deployment.yaml
**Change:** Adds writable `/var/run` tmpfs volume for s6-overlay compatibility

## Implementation Plan (When Unblocked)

Once bd-3vqp is resolved and Forgejo is healthy:

### Step 1: Verify Forgejo Health
```bash
kubectl --kubeconfig=/home/coder/.kube/apexalgo-iad.kubeconfig get pods -n forgejo
curl -s https://forgejo.ardenone.com/api/healthz
```

### Step 2: Create botburrow Organization
```bash
FORGEJO_ADMIN_TOKEN="xxx"  # Generated from Forgejo admin user
curl -X POST "https://botburrow-git.ardenone.com/api/v1/orgs" \
  -H "Authorization: token $FORGEJO_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"username": "botburrow", "visibility": "public"}'
```

### Step 3: Create Repositories
```bash
# agent-definitions (private)
curl -X POST "https://botburrow-git.ardenone.com/api/v1/org/botburrow/repos" \
  -H "Authorization: token $FORGEJO_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "agent-definitions", "private": true}'

# botburrow-hub (public)
curl -X POST "https://botburrow-git.ardenone.com/api/v1/org/botburrow/repos" \
  -H "Authorization: token $FORGEJO_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "botburrow-hub", "private": false}'

# botburrow-agents (public)
curl -X POST "https://botburrow-git.ardenone.com/api/v1/org/botburrow/repos" \
  -H "Authorization: token $FORGEJO_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "botburrow-agents", "private": false}'
```

### Step 4: Configure Push Mirrors
```bash
GITHUB_TOKEN="xxx"  # From forgejo-secrets
GITHUB_USER="botburrow"  # GitHub service account

# agent-definitions -> jedarden/agent-definitions
curl -X POST "https://botburrow-git.ardenone.com/api/v1/repos/botburrow/agent-definitions/push_mirrors" \
  -H "Authorization: token $FORGEJO_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"remote_address\": \"https://github.com/jedarden/agent-definitions.git\",
    \"remote_username\": \"$GITHUB_USER\",
    \"remote_password\": \"$GITHUB_TOKEN\",
    \"interval\": \"1h0m0s\",
    \"sync_on_commit\": true
  }"

# botburrow-hub -> ardenone/botburrow-hub
curl -X POST "https://botburrow-git.ardenone.com/api/v1/repos/botburrow/botburrow-hub/push_mirrors" \
  -H "Authorization: token $FORGEJO_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"remote_address\": \"https://github.com/ardenone/botburrow-hub.git\",
    \"remote_username\": \"$GITHUB_USER\",
    \"remote_password\": \"$GITHUB_TOKEN\",
    \"interval\": \"1h0m0s\",
    \"sync_on_commit\": true
  }"

# botburrow-agents -> ardenone/botburrow-agents
curl -X POST "https://botburrow-git.ardenone.com/api/v1/repos/botburrow/botburrow-agents/push_mirrors" \
  -H "Authorization: token $FORGEJO_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"remote_address\": \"https://github.com/ardenone/botburrow-agents.git\",
    \"remote_username\": \"$GITHUB_USER\",
    \"remote_password\": \"$GITHUB_TOKEN\",
    \"interval\": \"1h0m0s\",
    \"sync_on_commit\": true
  }"
```

### Step 5: Test Sync
```bash
# Create test commit
echo "test forgejo sync" > test-forgejo.txt
git add test-forgejo.txt
git commit -m "test: verify Forgejo -> GitHub sync"
git push origin main

# Verify on GitHub
gh repo view jedarden/agent-definitions  # Check new commit appears
gh repo view ardenone/botburrow-hub
gh repo view ardenone/botburrow-agents

# Cleanup
git rm test-forgejo.txt
git commit -m "test: cleanup"
git push origin main
```

### Step 6: Update Git Remotes (Optional)
```bash
# In each repository, if Forgejo should be primary:
git remote set-url origin https://botburrow-git.ardenone.com/botburrow/REPO.git
git remote add github https://github.com/OWNER/REPO.git
```

## GitHub Repositories (Already Exist)

| Repository | Owner | Visibility | URL | Status |
|------------|-------|------------|-----|--------|
| agent-definitions | jedarden | Private | github.com/jedarden/agent-definitions | ✓ Exists |
| botburrow-hub | ardenone | Public | github.com/ardenone/botburrow-hub | ✓ Exists |
| botburrow-agents | ardenone | Public | github.com/ardenone/botburrow-agents | ✓ Exists |

## Workaround Mode (Current)

Until bd-3vqp is resolved, continue using **GitHub as primary Git host**:

- All repositories are accessible on GitHub
- GitHub Actions workflows are configured and running
- Development can continue without Forgejo
- No data loss risk (GitHub is authoritative)

## Follow-up Bead

**bd-jh7:** Monitor bd-3vqp RBAC resolution and resume Forgejo mirror setup

This bead tracks the status of bd-3vqp and will trigger completion of bd-2vr steps when the RBAC issue is resolved.

## Related Documents

- **ADR-028:** Full bidirectional sync architecture
- **forgejo-github-sync-simplified.md:** Simplified sync implementation guide
- **forgejo-github-sync-status.md:** Current sync status tracking
- **forgejo-github-sync-workaround.md:** Workaround implementation details

## Required Actions

1. **HUMAN:** Resolve bd-3vqp (grant RBAC permissions or apply fix manually)
2. **WORKER:** Verify Forgejo pod is healthy after fix
3. **WORKER:** Complete Steps 1-6 of implementation plan
4. **WORKER:** Close bd-2vr and bd-jh7 when complete
