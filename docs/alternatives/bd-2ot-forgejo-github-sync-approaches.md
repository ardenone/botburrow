# Forgejo <-> GitHub Bidirectional Sync: Approaches Comparison

**Alternative Research For:** bd-145 - Set up Forgejo <-> GitHub bidirectional sync
**Research Date:** 2026-02-08
**Status:** Research-only alternative (original bead closed)
**Current Situation:** Forgejo pod in CrashLoopBackOff, workaround implemented

---

## Executive Summary

The botburrow project needs bidirectional synchronization between Forgejo (self-hosted, primary) and GitHub (external mirror). The original bead **bd-145** was **closed** after implementing a workaround approach due to Forgejo deployment issues (CrashLoopBackOff from s6-overlay permission error).

**This document provides a comprehensive comparison of approaches** for Forgejo-GitHub bidirectional sync to inform future architectural decisions. It covers the planned implementation (ADR-028), the current workaround, and alternative approaches that could be considered.

---

## Current Status

| Component | Status | Notes |
|-----------|--------|-------|
| Forgejo Pod | **CrashLoopBackOff** | s6-overlay permission error, fix committed but blocked on RBAC |
| GitHub Repos | **Operational** | agent-definitions, botburrow-hub, botburrow-agents all exist |
| Bidirectional Sync | **Not Implemented** | Workaround uses GitHub as primary only |
| ADR-028 | **Proposed** | Full bidirectional sync architecture designed but not implemented |

**Active Issues:**
- **bd-2we**: HUMAN: RBAC permission needed to apply Forgejo deployment fix
- **bd-i1c**: Alternative: Use workaround approach (closed - workaround documented)

---

## Approach 1: Full Bidirectional Sync (ADR-028)

### Description

Forgejo serves as the primary (authoritative) git host with automatic push mirrors to GitHub. GitHub can pull back for disaster recovery. This is the architecture specified in ADR-028.

### Architecture

```
NORMAL OPERATION (Push Sync):
Developer/Pipeline -> Forgejo (Primary) --[push mirror]--> GitHub (Mirror)
                         |
                         +-- sync_on_commit: true
                         +-- interval: 1h (fallback)

DISASTER RECOVERY (Pull Sync):
GitHub (Source) <-[pull mirror]- Forgejo (Restores)
                          |
                          +-- On pod restart with empty /data
                          +-- Via /repos/migrate API
```

### Implementation Components

| Component | Purpose | Status |
|-----------|---------|--------|
| Push Mirror API | Auto-push commits to GitHub | Not configured |
| Mirror Setup Sidecar | Configure mirrors on startup | Not deployed |
| Pull Mirror (Recovery) | Restore repos from GitHub | Not configured |
| GitHub Webhook (Optional) | Near-real-time sync back to Forgejo | Not configured |

### Setup Requirements

1. **Forgejo Deployment** (currently blocked):
   ```yaml
   # Requires RBAC permission to apply fix
   # Fix committed in: ardenone-cluster, commit 1a3e8cbfe
   ```

2. **GitHub Personal Access Token**:
   - Permissions: Contents (Read/Write)
   - Repository access: agent-definitions, botburrow-hub, botburrow-agents

3. **Forgejo Configuration**:
   ```bash
   POST /api/v1/repos/{owner}/{repo}/push_mirrors
   {
     "remote_address": "https://github.com/{owner}/{repo}.git",
     "remote_username": "{github_username}",
     "remote_password": "{github_pat}",
     "interval": "1h0m0s",
     "sync_on_commit": true
   }
   ```

### Pros

| Benefit | Impact |
|---------|--------|
| **Primary control** | Forgejo is authoritative, fully self-hosted |
| **Automatic backup** | Every commit immediately pushed to GitHub |
| **Disaster recovery** | Pod restart restores all repos from GitHub |
| **Public visibility** | GitHub mirror provides external access |
| **CI/CD integration** | GitHub Actions can run from mirrored repos |
| **Activity attribution** | Commits appear in GitHub activity feed |
| **GitOps compatible** | Works with ArgoCD/Flux patterns |

### Cons

| Drawback | Severity | Mitigation |
|----------|----------|------------|
| **Forgejo dependency** | Critical | Need working Forgejo deployment |
| **Eventual consistency** | Medium | Optional webhook for near-real-time sync |
| **Token management** | Medium | Periodic rotation, SealedSecrets |
| **Conflict potential** | Low | Manual resolution, conflict detection |
| **Complex setup** | Low | Automated via mirror-setup sidecar |

### Complexity

| Aspect | Level | Notes |
|--------|-------|-------|
| **Initial Setup** | Medium | Requires Forgejo running, GitHub PAT |
| **Maintenance** | Low | Automated sync, token rotation needed |
| **Disaster Recovery** | Low | Automated pull mirror on restart |
| **Troubleshooting** | Medium | Need to check mirror logs, API status |

---

## Approach 2: Manual Multi-Remote Git Workflow (Current Workaround)

### Description

Developers manually push to both GitHub and Forgejo remotes. No automatic synchronization. This is the current workaround being used while Forgejo is unavailable.

### Architecture

```
Developer -> Local Git
              |
              +-- git push origin main    (GitHub)
              +-- git push forgejo main   (Forgejo, when available)

OR: git push origin main && git push forgejo main
OR: git alias for simultaneous push
```

### Implementation

```bash
# Add Forgejo remote (when available)
git remote add forgejo https://botburrow-git.ardenone.com/botburrow/botburrow.git

# Push to both
git push origin main && git push forgejo main

# Or create alias
git config --global alias.pushall '!f() { git push origin "$@" && git push forgejo "$@"; }; f'
git pushall main
```

### Pros

| Benefit | Impact |
|---------|--------|
| **No Forgejo dependency** | Can work with GitHub only |
| **Simple setup** | Just add git remote |
| **Full control** | Explicit control over push targets |
| **No automation complexity** | Manual process, no sidecars needed |
| **Works immediately** | No waiting for Forgejo fix |

### Cons

| Drawback | Severity | Mitigation |
|----------|----------|------------|
| **Manual process** | High | Easy to forget one remote |
| **No automatic sync** | High | Must remember to push to both |
| **No disaster recovery** | Medium | Manual restore process |
| **Error-prone** | Medium | Can get repos out of sync |
| **No activity attribution** | Low | GitHub activity shows direct pushes only |

### Complexity

| Aspect | Level | Notes |
|--------|-------|-------|
| **Initial Setup** | Low | Just add git remotes |
| **Maintenance** | High | Manual push to both remotes |
| **Disaster Recovery** | High | Manual clone/push process |
| **Troubleshooting** | Low | Standard git operations |

### When to Use

- **Transition period**: While Forgejo is being fixed
- **Development**: Quick iterations on single branches
- **Simple projects**: With few developers and branches
- **Emergency**: When automatic sync is broken

---

## Approach 3: GitHub Webhook to Forgejo

### Description

GitHub sends webhook events to Forgejo on push events. Forgejo pulls changes automatically. This provides near-real-time sync from GitHub to Forgejo.

### Architecture

```
GitHub Push -> Webhook -> Forgejo Webhook Endpoint
                              |
                              +-- Trigger pull from GitHub
                              +-- Update repository
                              +-- Merge/Rebase as configured
```

### Implementation

**GitHub Webhook Configuration:**
```yaml
# GitHub Repository Settings -> Webhooks -> Add webhook
Payload URL: https://botburrow-git.ardenone.com/api/v1/webhooks/github
Content type: application/json
Secret: <webhook_secret>
Events: Push events
```

**Forgejo Webhook Handler:**
```go
// Forgejo webhook endpoint (needs to be exposed)
POST /api/v1/webhooks/github
Headers: X-Hub-Signature-256
Body: {
  "ref": "refs/heads/main",
  "repository": {
    "clone_url": "https://github.com/owner/repo.git"
  },
  "pusher": {...}
}
```

### Pros

| Benefit | Impact |
|---------|--------|
| **Near-real-time sync** | Push to GitHub triggers immediate pull |
| **Bidirectional awareness** | Changes from either side propagate |
| **Event-driven** | No polling, efficient |
| **Works with Forgejo push mirrors** | Complements push sync |
| **Transparent to users** | Automatic after setup |

### Cons

| Drawback | Severity | Mitigation |
|----------|----------|------------|
| **Forgejo must be running** | Critical | Webhook endpoint must be available |
| **Public exposure** | Medium | Need proper auth/secret |
| **Conflict complexity** | High | Need merge/rebase strategy |
| **Webhook delivery issues** | Medium | Retry logic, monitoring |
| **Authentication setup** | Medium | Manage webhook secrets |

### Complexity

| Aspect | Level | Notes |
|--------|-------|-------|
| **Initial Setup** | High | Need webhook endpoint, auth |
| **Maintenance** | Medium | Monitor webhook delivery |
| **Disaster Recovery** | Medium | Webhooks auto-recreate from backup |
| **Troubleshooting** | High | Webhook delivery logs needed |

---

## Approach 4: External Sync Service (GitHub Actions / Cron Job)

### Description

An external service (GitHub Actions, cron job, or standalone service) periodically pulls from both repositories and pushes changes to maintain synchronization.

### Architecture

```
Scheduler (GitHub Actions / Cron)
              |
              +-- Clone Forgejo repo
              +-- Fetch GitHub repo
              +-- Detect divergent commits
              +-- Merge changes (strategy-dependent)
              +-- Push to both repos
```

### Implementation (GitHub Actions)

```yaml
# .github/workflows/sync-repos.yml
name: Sync Repositories
on:
  schedule:
    - cron: '*/30 * * * *'  # Every 30 minutes
  workflow_dispatch:  # Manual trigger

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout Forgejo
        uses: actions/checkout@v3
        with:
          repository: botburrow/botburrow
          token: ${{ secrets.FORGEJO_TOKEN }}
          fetch-depth: 0

      - name: Add GitHub remote
        run: git remote add github https://github.com/owner/repo.git

      - name: Fetch GitHub
        run: git fetch github main

      - name: Merge changes
        run: |
          git merge github/main --no-edit --allow-unrelated-histories || \
          echo "No merge or conflicts need resolution"

      - name: Push to GitHub
        run: git push github main

      - name: Push to Forgejo
        run: git push origin main
```

### Pros

| Benefit | Impact |
|---------|--------|
| **No Forgejo dependency** | Runs on GitHub infrastructure |
| **Customizable logic** | Full control over merge strategy |
| **Monitoring built-in** | GitHub Actions logs |
| **Manual trigger** | Can sync on-demand |
| **Free/cheap** | GitHub Actions free tier |

### Cons

| Drawback | Severity | Mitigation |
|----------|----------|------------|
| **Not real-time** | Medium | Scheduled intervals |
| **Conflict complexity** | High | Need robust merge strategy |
| **Credential management** | Medium | Store tokens in GitHub Secrets |
| **Race conditions** | Medium | Frequent syncs cause conflicts |
| **External dependency** | Low | Relies on GitHub Actions |

### Complexity

| Aspect | Level | Notes |
|--------|-------|-------|
| **Initial Setup** | Medium | Create workflow, configure tokens |
| **Maintenance** | Medium | Monitor workflow runs |
| **Disaster Recovery** | Low | Workflow stored in git |
| **Troubleshooting** | Low | GitHub Actions logs visible |

---

## Approach 5: Git Remote with Multiple URLs

### Description

Configure a single git remote with multiple push URLs. This is a simpler variant of the manual multi-remote workflow.

### Architecture

```
git remote origin
  -- pushurl: https://github.com/owner/repo.git
  -- pushurl: https://forgejo.example.com/owner/repo.git

git push origin main  -> pushes to BOTH URLs
```

### Implementation

```bash
# Set multiple push URLs
git remote set-url --add --push origin https://github.com/owner/repo.git
git remote set-url --add --push origin https://forgejo.example.com/owner/repo.git

# Verify
git remote -v
# origin  https://github.com/owner/repo.git (fetch)
# origin  https://github.com/owner/repo.git (push)
# origin  https://forgejo.example.com/owner/repo.git (push)

# Single push command
git push origin main
```

### Pros

| Benefit | Impact |
|---------|--------|
| **Simple setup** | Just configure git remote |
| **Single command** | One push to both remotes |
| **No automation** | No sidecars or services needed |
| **Git-native** | Uses standard git features |
| **Works immediately** | No waiting for Forgejo fix |

### Cons

| Drawback | Severity | Mitigation |
|----------|----------|------------|
| **No automatic sync** | High | Must push to trigger sync |
| **Partial failure handling** | Medium | One push can fail, other succeed |
| **No pull sync** | High | Only pushes, doesn't pull changes |
| **Error-prone** | Medium | Can get out of sync |
| **No disaster recovery** | Medium | Manual restore needed |

### Complexity

| Aspect | Level | Notes |
|--------|-------|-------|
| **Initial Setup** | Low | Configure git remote URLs |
| **Maintenance** | Low | No ongoing maintenance |
| **Disaster Recovery** | High | Manual process |
| **Troubleshooting** | Low | Standard git operations |

---

## Approach 6: Kubernetes Git Sync Sidecar

### Description

A Kubernetes sidecar container that continuously synchronizes git repositories. Runs alongside application pods.

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Kubernetes Pod                                              │
│                                                              │
│  ┌─────────────────┐  ┌─────────────────────────────────┐  │
│  │  Application    │  │  Git Sync Sidecar               │  │
│  │  Container      │  │  - git-sync or custom script   │  │
│  │                 │  │  - Periodic pull/push          │  │
│  │                 │  │  - Shared volume for repo      │  │
│  └─────────────────┘  └─────────────────────────────────┘  │
│         │                         │                        │
│         └─────────────────────────┘                        │
│                    Shared Volume                            │
└─────────────────────────────────────────────────────────────┘
```

### Implementation

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: app-with-git-sync
spec:
  template:
    spec:
      volumes:
      - name: repo
        emptyDir: {}
      containers:
      - name: app
        image: myapp:latest
        volumeMounts:
        - name: repo
          mountPath: /repo
      - name: git-sync
        image: registry.k8s.io/git-sync:v4.2.0
        env:
        - name: GIT_SYNC_REPO
          value: https://github.com/owner/repo.git
        - name: GIT_SYNC_BRANCH
          value: main
        - name: GIT_SYNC_PERIOD
          value: "30s"
        - name: GIT_SYNC_PUSH
          value: "true"  # If using custom sync logic
        volumeMounts:
        - name: repo
          mountPath: /git
```

### Pros

| Benefit | Impact |
|---------|--------|
| **Kubernetes-native** | Runs in cluster, no external services |
| **Continuous sync** | Periodic automatic synchronization |
| **No external dependencies** | Self-contained in pod |
| **Configurable** | Branch, period, auth via env vars |
| **Observable** | Kubernetes logs/metrics |

### Cons

| Drawback | Severity | Mitigation |
|----------|----------|------------|
| **Pod lifecycle coupling** | Medium | Sync depends on pod running |
| **Complex conflict handling** | High | Need custom logic for merges |
| **Resource overhead** | Low | Additional container |
| **Credential management** | Medium | Need to store git credentials |
| **Push sync complexity** | High | git-sync primarily pull, need custom |

### Complexity

| Aspect | Level | Notes |
|--------|-------|-------|
| **Initial Setup** | Medium | Configure sidecar, volumes |
| **Maintenance** | Medium | Monitor sync logs |
| **Disaster Recovery** | Low | Restarts automatically |
| **Troubleshooting** | Medium | Sidecar logs, Kubernetes events |

---

## Comparison Matrix

| Feature | ADR-028 Full Sync | Manual Multi-Remote | GitHub Webhook | External Sync Service | Multiple Push URLs | Sidecar |
|---------|------------------|---------------------|----------------|----------------------|-------------------|----------|
| **Real-time sync** | Push: Yes / Pull: On restart | No | Near real-time | Scheduled (min) | No | Periodic |
| **Bidirectional** | Yes (pull on restart) | Yes (manual) | Yes (webhook) | Yes (logic) | Push only | Depends |
| **Automation** | Full | None | Full | Full | Partial | Full |
| **Forgejo dependency** | Required | Optional | Required | None | Optional | None |
| **Setup complexity** | Medium | Low | High | Medium | Low | Medium |
| **Maintenance** | Low | High | Medium | Medium | Low | Medium |
| **Disaster recovery** | Automated | Manual | Manual | Manual | Manual | Automated |
| **Conflict handling** | Manual | Manual | Complex | Customizable | Manual | Customizable |
| **Cost** | None | None | None | Free tier | None | Resources |
| **Monitoring** | Forgejo logs | Manual | Webhook logs | Actions logs | Git errors | Pod logs |

---

## Decision Framework

### Use Full Bidirectional Sync (ADR-028) when:

- Forgejo is operational and stable
- Need automated disaster recovery
- Want Forgejo as authoritative source
- Have resources for initial setup
- Need near-real-time push sync

### Use Manual Multi-Remote Workflow when:

- Forgejo is unavailable (current situation)
- Simple project with few developers
- Want full control over push timing
- Cannot automate sync for any reason

### Use GitHub Webhook when:

- Need near-real-time sync from GitHub to Forgejo
- Forgejo is publicly accessible
- Can handle complex merge scenarios
- Want event-driven architecture

### Use External Sync Service when:

- Don't want Forgejo dependency
- Need custom merge logic
| Already using GitHub Actions
- Want monitoring and logs built-in

### Use Multiple Push URLs when:

| Want simpler manual workflow
- Single command to push to both
- Don't need pull synchronization
- Prefer git-native solution

### Use Kubernetes Sidecar when:

| Already running Kubernetes workloads
- Need continuous sync within cluster
- Want isolation from external services
| Can handle custom merge logic

---

## Recommendations

### Current Situation (Forgejo CrashLoopBackOff)

**Recommended: Manual Multi-Remote Workflow**

```bash
# Use GitHub as primary
git push origin main

# Add Forgejo remote when available
git remote add forgejo https://botburrow-git.ardenone.com/botburrow/botburrow.git

# Push to both when needed
git push origin main && git push forgejo main
```

### Once Forgejo is Operational

**Recommended: Full Bidirectional Sync (ADR-028)**

1. Apply Forgejo deployment fix (requires RBAC resolution - bd-2we)
2. Configure push mirrors via API or UI
3. Test sync with test commits
4. Verify disaster recovery procedure
5. Close bd-145 as complete

### Long-term Alternative

**Consider: GitHub Webhook + External Sync Service**

For maximum reliability and independence from Forgejo availability:
- Use push mirrors for Forgejo -> GitHub
- Use GitHub webhook for GitHub -> Forgejo
- Use GitHub Actions as backup/monitoring

---

## Implementation Checklist

### For ADR-028 Full Bidirectional Sync

- [ ] Resolve bd-2we (RBAC permission for Forgejo fix)
- [ ] Apply Forgejo deployment update
- [ ] Verify Forgejo pod is running
- [ ] Create GitHub Personal Access Token
- [ ] Store PAT in SealedSecret
- [ ] Configure push mirrors for each repo
- [ ] Test push sync: commit to Forgejo -> verify on GitHub
- [ ] Test pull recovery: delete pod -> verify restore
- [ ] Configure monitoring/alerting
- [ ] Document procedures

### For Manual Multi-Remote Workflow

- [ ] Add Forgejo remote to all repos
- [ ] Create git alias for simultaneous push
- [ ] Document workflow in developer guide
- [ ] Train team on two-remote workflow
- [ ] Create verification script

### For GitHub Webhook Approach

- [ ] Expose Forgejo webhook endpoint
- [ ] Create webhook secret
- [ ] Configure GitHub webhooks
- [ ] Implement webhook handler
- [ ] Test webhook delivery
- [ ] Implement conflict resolution
- [ ] Configure monitoring

---

## Related Documents

| Document | Location | Purpose |
|----------|----------|---------|
| **ADR-028** | `/home/coder/research/botburrow/adr/028-forgejo-github-bidirectional-sync.md` | Full bidirectional sync architecture |
| **Workaround Doc** | `/home/coder/research/botburrow/docs/forgejo-github-sync-workaround.md` | Current workaround implementation |
| **Simplified Guide** | `/home/coder/research/botburrow/docs/forgejo-github-sync-simplified.md` | Simplified setup instructions |
| **Status Document** | `/home/coder/research/botburrow/docs/forgejo-github-sync-status.md` | Current status tracking |
| **Forgejo Deployment** | `cluster-configuration/apexalgo-iad/forgejo/` | Kubernetes deployment manifests |

---

## Next Steps

1. **Resolve Forgejo Deployment** (bd-2we)
   - Get RBAC permissions to apply fix
   - Verify pod comes up healthy
   - Proceed with ADR-028 implementation

2. **OR Continue with Workaround**
   - Accept GitHub-as-primary workflow
   - Document procedures
   - Revisit Forgejo sync later

3. **Close This Bead**
   - Mark bd-2ot as complete
   - Create follow-up bead if needed
   - Commit research documentation

---

**Document End**

*This research document was created as part of bead bd-2ot (Alternative: Research and document options) on 2026-02-08.*
