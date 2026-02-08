# Forgejo ↔ GitHub Sync: Simplified Setup Guide

## Status
**Alternative Implementation** for bd-145 (Set up Forgejo ↔ GitHub bidirectional sync)

This is a simplified approach that focuses on minimal viable implementation.

## Prerequisites

1. **Forgejo pod must be running** - Currently blocked on RBAC permissions (see bd-2we)
2. **GitHub Personal Access Token** with repo permissions
3. **GitHub repositories** created:
   - `jedarden/agent-definitions` (private)
   - `ardenone/botburrow-hub` (public)
   - `ardenone/botburrow-agents` (public)

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

## Setup Steps

### Step 1: Ensure Forgejo is Running

The deployment fix has been committed but requires RBAC permissions to apply.

```bash
# Verify pod status
kubectl --kubeconfig=/home/coder/.kube/apexalgo-iad.kubeconfig get pods -n forgejo

# Current status (as of 2026-02-08):
# NAME: forgejo-6744c7dc-64stz
# STATUS: CrashLoopBackOff (s6-overlay permission error)
# FIX: Committed in ardenone-cluster, needs RBAC to apply
```

**Action Required:** Resolve bd-2we (HUMAN: RBAC permission needed)

### Step 2: Create GitHub Repositories

```bash
# Create GitHub repos (if not exists)
gh repo create jedarden/agent-definitions --private
gh repo create ardenone/botburrow-hub --public
gh repo create ardenone/botburrow-agents --public
```

### Step 3: Create Forgejo Repositories

Once Forgejo is running, create repositories via UI or API:

**Via Forgejo UI:**
1. Navigate to https://botburrow-git.ardenone.com
2. Login with admin credentials
3. Create organization "botburrow" (if not exists)
4. Create repositories:
   - `agent-definitions`
   - `botburrow-hub`
   - `botburrow-agents`

**Via API:**
```bash
FORGEJO_TOKEN="your-forgejo-admin-token"

# Create organization
curl -X POST "https://botburrow-git.ardenone.com/api/v1/orgs" \
  -H "Authorization: token $FORGEJO_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"username": "botburrow", "visibility": "public"}'

# Create repositories
for repo in agent-definitions botburrow-hub botburrow-agents; do
  curl -X POST "https://botburrow-git.ardenone.com/api/v1/org/botburrow/repos" \
    -H "Authorization: token $FORGEJO_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"$repo\", \"private\": false}"
done
```

### Step 4: Configure Push Mirrors

**Via Forgejo UI:**
For each repository:
1. Go to Repository Settings → Mirror Sync
2. Add push mirror:
   - Remote URL: `https://github.com/{owner}/{repo}.git`
   - Username: GitHub username
   - Password: GitHub Personal Access Token
   - Interval: 1 hour
   - ✅ Sync on Commit

**Via API:**
```bash
GITHUB_TOKEN="your-github-pat"

# agent-definitions
curl -X POST "https://botburrow-git.ardenone.com/api/v1/repos/botburrow/agent-definitions/push_mirrors" \
  -H "Authorization: token $FORGEJO_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"remote_address\": \"https://github.com/jedarden/agent-definitions.git\",
    \"remote_username\": \"jedarden\",
    \"remote_password\": \"$GITHUB_TOKEN\",
    \"interval\": \"1h0m0s\",
    \"sync_on_commit\": true
  }"

# botburrow-hub
curl -X POST "https://botburrow-git.ardenone.com/api/v1/repos/botburrow/botburrow-hub/push_mirrors" \
  -H "Authorization: token $FORGEJO_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"remote_address\": \"https://github.com/ardenone/botburrow-hub.git\",
    \"remote_username\": \"ardenone\",
    \"remote_password\": \"$GITHUB_TOKEN\",
    \"interval\": \"1h0m0s\",
    \"sync_on_commit\": true
  }"

# botburrow-agents
curl -X POST "https://botburrow-git.ardenone.com/api/v1/repos/botburrow/botburrow-agents/push_mirrors" \
  -H "Authorization: token $FORGEJO_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"remote_address\": \"https://github.com/ardenone/botburrow-agents.git\",
    \"remote_username\": \"ardenone\",
    \"remote_password\": \"$GITHUB_TOKEN\",
    \"interval\": \"1h0m0s\",
    \"sync_on_commit\": true
  }"
```

### Step 5: Test Sync

```bash
# Clone from Forgejo
git clone https://botburrow-git.ardenone.com/botburrow/botburrow-hub.git
cd botburrow-hub

# Make a test commit
echo "test sync" > test-sync.txt
git add test-sync.txt
git commit -m "test: verify sync to GitHub"

# Push to Forgejo
git push origin main

# Verify on GitHub (web or API)
gh repo view ardenone/botburrow-hub
```

## Disaster Recovery (Manual)

If Forgejo data is lost, repositories can be restored from GitHub:

**Option A: Via Forgejo UI**
1. Create new repository
2. Repository Settings → Mirror Sync
3. Add pull mirror from GitHub URL
4. Sync will pull all content

**Option B: Via API**
```bash
curl -X POST "https://botburrow-git.ardenone.com/api/v1/repos/migrate" \
  -H "Authorization: token $FORGEJO_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"clone_addr\": \"https://github.com/ardenone/botburrow-hub.git\",
    \"repo_name\": \"botburrow-hub\",
    \"repo_owner\": \"botburrow\",
    \"mirror\": true,
    \"private\": false
  }"
```

## Verification Checklist

- [ ] Forgejo pod is running (not CrashLoopBackOff)
- [ ] GitHub repos exist (agent-definitions, botburrow-hub, botburrow-agents)
- [ ] Forgejo repos exist in botburrow organization
- [ ] Push mirrors configured with sync_on_commit: true
- [ ] Test push to Forgejo appears on GitHub
- [ ] GitHub token has correct permissions (Contents: Read/Write)

## Current Status

| Item | Status |
|------|--------|
| Forgejo Deployment | ❌ Blocked on RBAC (bd-2we) |
| GitHub Repos | ❓ Need to verify/create |
| Push Mirror Config | ⏸️ Waiting for Forgejo |
| Sync Test | ⏸️ Waiting for Forgejo |

## Related Documents

- **ADR-028**: Full bidirectional sync architecture (this is the simplified version)
- **Status Document**: `/home/coder/research/botburrow/docs/forgejo-github-sync-status.md`
- **Deployment Fix**: Committed in ardenone-cluster repo (commit 1a3e8cbfe)

## Next Steps

1. **Resolve bd-2we** - Get RBAC permissions to apply Forgejo deployment fix
2. **Create GitHub repos** - Verify all three repos exist
3. **Follow setup steps** - Configure push mirrors manually
4. **Test sync** - Verify bidirectional sync works
5. **Close bd-2l0** - Mark this simplified alternative as complete
