# Forgejo ↔ GitHub Sync: Workaround Implementation

## Status
**Workaround** for bd-i1c (Alternative: Use workaround approach)

Original task bd-145 was closed. This document describes the workaround approach actually used instead of full bidirectional sync.

## Problem Statement

Forgejo pod is in CrashLoopBackOff due to s6-overlay permission error. The full bidirectional sync architecture (ADR-028) requires Forgejo to be running.

## Workaround: Manual Multi-Remote Git Workflow

Instead of waiting for Forgejo to be fixed and configured with automatic push mirrors, we use a simple multi-remote git workflow.

### Architecture (Workaround)

```
Normal Operation:
Developer → Local Git → Push to BOTH GitHub AND Forgejo (manual)
           └─→ git push origin main      (GitHub)
           └─→ git push forgejo main     (Forgejo, when available)

No automatic sync. Each push goes to both remotes explicitly.
```

### Setup

#### 1. Add Forgejo as Remote

```bash
cd /home/coder/research/botburrow

# Add Forgejo remote (use --mirror if needed)
git remote add forgejo https://botburrow-git.ardenone.com/botburrow/botburrow.git

# Verify remotes
git remote -v
```

#### 2. Push to Both Remotes

**Option A: Push separately**
```bash
# Push to GitHub (origin)
git push origin main

# Push to Forgejo (when available)
git push forgejo main
```

**Option B: Push simultaneously**
```bash
# Push to both at once
git push origin main && git push forgejo main
```

**Option C: Git alias for convenience**
```bash
# Add alias to ~/.gitconfig
git config --global alias.pushall '!f() { git push origin "$@" && git push forgejo "$@"; }; f'

# Use: git pushall main
git pushall main
```

### Current Status (2026-02-08)

| Item | Status |
|------|--------|
| Forgejo Pod | ❌ CrashLoopBackOff (s6-overlay permission error) |
| GitHub Repos | ✅ All exist (agent-definitions, botburrow-hub, botburrow-agents) |
| Forgejo Remotes | ⏸️ Can add locally, but pod not accepting pushes |
| Automatic Sync | ❌ Not configured (Forgejo not running) |

### Workaround Limitations

1. **No automatic sync** - Must manually push to both remotes
2. **Forgejo unavailable** - Pod is down, pushes will fail
3. **No disaster recovery** - Can't pull from GitHub to Forgejo automatically
4. **Manual process** - Easy to forget one remote

### When Forgejo Becomes Available

Once the Forgejo pod is fixed (requires RBAC permissions to apply deployment update):

1. **Verify Forgejo is running:**
   ```bash
   kubectl --kubeconfig=/home/coder/.kube/apexalgo-iad.kubeconfig get pods -n forgejo
   ```

2. **Push to Forgejo:**
   ```bash
   git push forgejo main
   ```

3. **Configure push mirrors (optional):**
   Follow ADR-028 for automatic push mirrors from Forgejo to GitHub.

## Alternative: GitHub-Only Workflow (Recommended While Forgejo Down)

Given Forgejo is currently unavailable, the simplest workaround is to use GitHub as the primary repository:

```bash
# Work with GitHub only
git push origin main
git pull origin main

# Add Forgejo remote later when pod is fixed
```

This provides:
- Full Git functionality
- CI/CD via GitHub Actions
- No dependency on Forgejo availability
- Easy migration to Forgejo later (add remote, push)

## Verification

To verify the workaround is working:

```bash
# Check remotes
git remote -v

# Check current branch
git branch -vv

# Push to GitHub
git push origin main

# (After Forgejo is fixed) Push to Forgejo
git push forgejo main
```

## Related Documents

- **ADR-028**: Full bidirectional sync architecture (not implemented)
- **Status Document**: `/home/coder/research/botburrow/docs/forgejo-github-sync-status.md`
- **Simplified Guide**: `/home/coder/research/botburrow/docs/forgejo-github-sync-simplified.md`

## Next Steps

1. **Use GitHub-only workflow** while Forgejo is down
2. **Resolve Forgejo deployment** - Requires RBAC permissions (bd-2we)
3. **Add Forgejo remote** when pod is running
4. **Reconsider ADR-028** - Decide if full bidirectional sync is needed
5. **Close bd-i1c** - Mark this workaround as complete
