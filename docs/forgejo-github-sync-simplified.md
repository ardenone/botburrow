# Forgejo ↔ GitHub Sync: Workaround Implementation

## Status
**Workaround Implementation** for bd-i1c (Alternative: Use workaround approach)

This is a pragmatic workaround that uses GitHub as the primary Git host while Forgejo is blocked on RBAC issues.

## Workaround Strategy

**Current Situation:**
- Forgejo pod is in CrashLoopBackOff due to s6-overlay permission error
- Fix exists in commit 1a3e8cbfe but cannot be applied due to RBAC restrictions (bd-3vqp)
- The workaround allows development work to continue using GitHub as primary

**Workaround Approach:**
- Use GitHub as primary Git host for all botburrow repositories
- Document migration path for when Forgejo becomes available
- Maintain synchronization readiness for future Forgejo deployment

## GitHub Repository Status (Verified ✓)

| Repository | Owner | Visibility | URL | Status |
|------------|-------|------------|-----|--------|
| agent-definitions | jedarden | Private | github.com/jedarden/agent-definitions | ✓ Exists |
| botburrow-hub | ardenone | Public | github.com/ardenone/botburrow-hub | ✓ Exists |
| botburrow-agents | ardenone | Public | github.com/ardenone/botburrow-agents | ✓ Exists |

## Architecture (Simplified)

```
Normal Operation:
Developer → Forgejo (push) → GitHub (push mirror, sync_on_commit)

Disaster Recovery:
Manual pull from GitHub via Forgejo UI or API
```

**Key Simplifications from ADR-028:**
- No automated mirror-setup sidecar
- No GitHub webhook for real-time sync back to Forgejo
- Manual disaster recovery instead of automated pull mirror on restart
- Focus on push sync (Forgejo → GitHub) as primary use case

## Current Workaround Mode: GitHub-First

### Using GitHub as Primary Git Host

Until Forgejo is healthy, all repositories operate directly from GitHub:

```bash
# botburrow research repo
cd /home/coder/research/botburrow
git remote -v  # origin points to github.com/ardenone/botburrow

# agent-definitions repo
git clone https://github.com/jedarden/agent-definitions.git

# botburrow-hub repo
git clone https://github.com/ardenone/botburrow-hub.git

# botburrow-agents repo
git clone https://github.com/ardenone/botburrow-agents.git
```

### GitHub Actions CI/CD

All repositories have GitHub Actions workflows configured:
- `.github/workflows/` runs on push to GitHub
- Tests, validation, and deployment automation
- No changes needed - workflows run as designed

## Migration Path: When Forgejo Becomes Available

### Prerequisites for Migration

1. **Resolve bd-3vqp** - Get RBAC permissions to apply Forgejo deployment fix
2. **Apply deployment fix** - `kubectl apply -f cluster-configuration/apexalgo-iad/forgejo/deployment.yaml`
3. **Verify Forgejo is healthy** - Pod should be Running (not CrashLoopBackOff)

### Migration Steps (When Forgejo is Ready)

#### Step 1: Create Forgejo Repositories

```bash
# Generate admin token
FORGEJO_ADMIN_TOKEN="xxx"  # From Forgejo UI or API

# Create botburrow organization
curl -X POST "https://botburrow-git.ardenone.com/api/v1/orgs" \
  -H "Authorization: token $FORGEJO_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"username": "botburrow", "visibility": "public"}'

# Create repositories
for repo in agent-definitions botburrow-hub botburrow-agents; do
  private="false"
  if [ "$repo" = "agent-definitions" ]; then
    private="true"
  fi
  curl -X POST "https://botburrow-git.ardenone.com/api/v1/org/botburrow/repos" \
    -H "Authorization: token $FORGEJO_ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"$repo\", \"private\": $private}"
done
```

#### Step 2: Push Initial Content to Forgejo

```bash
# For each repo, add Forgejo remote and push
cd /path/to/repo
git remote add forgejo https://botburrow-git.ardenone.com/botburrow/REPO.git
git push forgejo main
```

#### Step 3: Configure Push Mirrors (Forgejo → GitHub)

```bash
GITHUB_TOKEN="your-github-pat"
GITHUB_USER="your-github-username"

# agent-definitions
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

# botburrow-hub
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

# botburrow-agents
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

#### Step 4: Update Git Remotes (Optional)

To make Forgejo the primary remote:

```bash
# In each repository
git remote set-url origin https://botburrow-git.ardenone.com/botburrow/REPO.git
git remote add github https://github.com/OWNER/REPO.git

# Push to Forgejo becomes default
git push origin main  # Pushes to Forgejo
```

#### Step 5: Verify Bidirectional Sync

```bash
# Test push to Forgejo
echo "test forgejo sync" > test-forgejo.txt
git add test-forgejo.txt
git commit -m "test: verify Forgejo → GitHub sync"
git push origin main

# Verify on GitHub
gh repo view OWNER/REPO  # Should show new commit

# Clean up test file
git rm test-forgejo.txt
git commit -m "test: cleanup"
git push origin main
```

## Disaster Recovery (GitHub as Source of Truth)

Since GitHub is the primary host during workaround mode:

- **All code is safe on GitHub** - No risk of data loss from Forgejo being down
- **Forgejo data loss is irrelevant** - Can be fully restored from GitHub when ready
- **No manual intervention needed** - GitHub repos remain authoritative

## Verification Checklist

### Workaround Mode (Current)
- [x] GitHub repos exist and verified
  - [x] jedarden/agent-definitions (private)
  - [x] ardenone/botburrow-hub (public)
  - [x] ardenone/botburrow-agents (public)
- [x] GitHub Actions workflows configured
- [x] Documentation updated with migration path

### Migration to Forgejo (Future)
- [ ] Resolve bd-3vqp (RBAC permissions)
- [ ] Apply Forgejo deployment fix
- [ ] Verify Forgejo pod is Running
- [ ] Create Forgejo repositories
- [ ] Configure push mirrors to GitHub
- [ ] Test bidirectional sync

## Current Status Summary

| Item | Status | Notes |
|------|--------|-------|
| **GitHub Repos** | ✅ Verified | All three repos exist |
| **GitHub Actions** | ✅ Working | CI/CD pipelines active |
| **Forgejo Deployment** | ❌ Blocked | RBAC issue (bd-3vqp) |
| **Push Mirror Config** | ⏸️ Deferred | Waiting for Forgejo |
| **Bidirectional Sync** | ⏸️ Deferred | Waiting for Forgejo |

## Related Documents

- **ADR-028**: Full bidirectional sync architecture
- **Status Document**: `/home/coder/research/botburrow/docs/forgejo-github-sync-status.md`
- **Deployment Fix**: Committed in ardenone-cluster repo (commit 1a3e8cbfe)
- **RBAC Issue**: bead bd-3vqp in /home/coder workspace

## Benefits of Workaround Approach

### Immediate Benefits
1. **Unblocks development** - Work can continue without waiting for Forgejo
2. **Zero data loss risk** - GitHub is reliable and externally backed up
3. **Full CI/CD functionality** - GitHub Actions workflows run as intended
4. **No migration urgency** - Can migrate to Forgejo when convenient

### Reduced Complexity
1. **No mirror configuration** - Skip push mirror setup until Forgejo is ready
2. **No webhook setup** - Skip GitHub webhook configuration
3. **Simplified operations** - Single Git host reduces operational overhead

### Clear Migration Path
1. **Documented steps** - Migration procedure is clearly documented above
2. **No breaking changes** - Migration is additive, not disruptive
3. **Rollback-friendly** - Can stay on GitHub if Forgejo migration has issues
