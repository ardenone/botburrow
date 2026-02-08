# Forgejo ↔ GitHub Sync: MVP Requirements

**For bead:** bd-16n (Alternative: Simplify requirements)
**Related to:** bd-145 (CLOSED)
**Date:** 2026-02-08

---

## Problem Statement

Forgejo pod is in CrashLoopBackOff (s6-overlay permission error). Fix is committed but blocked on RBAC (bd-2we). We need a minimal viable sync solution that works **now**.

---

## Core Functionality (MVP Only)

### What We Actually Need

| Priority | Feature | Description | Why |
|----------|---------|-------------|-----|
| **P0** | GitHub as primary | Use existing GitHub repos as authoritative source | Already working, zero setup |
| **P0** | Manual push to Forgejo | When Forgejo is available, push manually | Unblocks work immediately |
| **P1** | Document migration path | How to switch to Forgejo when ready | Clear future path |
| **P2** | Forgejo push mirrors | Auto-push to GitHub after Forgejo commits | Nice-to-have, can do later |
| **P3** | GitHub webhook → Forgejo | Near-real-time sync back to Forgejo | Optional complexity |
| **P4** | Automated disaster recovery | Auto-pull from GitHub on Forgejo restart | Over-engineering for MVP |

---

## MVP Implementation (What to Do NOW)

### Step 1: Continue Using GitHub (Already Done ✓)

```bash
# Current status - already working
cd /home/coder/research/botburrow
git remote -v  # origin → github.com/ardenone/botburrow
git push origin main  # Works fine
```

**Repos verified:**
- `github.com/jedarden/agent-definitions` (private)
- `github.com/ardenone/botburrow-hub` (public)
- `github.com/ardenone/botburrow-agents` (public)

### Step 2: Add Forgejo Remote (When Available)

```bash
# Only do this AFTER Forgejo pod is fixed
git remote add forgejo https://botburrow-git.ardenone.com/botburrow/REPO.git

# Optional: Create push alias
git config alias.pushboth '!f() { git push origin "$@" && git push forgejo "$@"; }; f'
git pushboth main  # Pushes to both
```

### Step 3: Document the Migration Path (This Document)

This document IS the migration path. No separate documentation needed.

---

## What We're NOT Doing (Deferred)

| Feature | Why Deferred |
|---------|--------------|
| Push mirrors (Forgejo → GitHub) | Requires working Forgejo pod |
| Pull mirrors (GitHub → Forgejo) | Requires working Forgejo pod |
| GitHub webhooks | Requires Forgejo webhook endpoint |
| Mirror setup sidecar | Requires working Forgejo pod |
| Automated disaster recovery | Overkill for MVP |

---

## Migration Path: When Forgejo is Ready

### Prerequisites

1. [ ] Resolve bd-2we (RBAC permission to apply Forgejo fix)
2. [ ] Apply deployment fix from ardenone-cluster (commit 1a3e8cbfe)
3. [ ] Verify Forgejo pod is Running

### Migration Steps (3 Commands)

```bash
# 1. Add Forgejo remote
git remote add forgejo https://botburrow-git.ardenone.com/botburrow/REPO.git

# 2. Push existing content to Forgejo
git push forgejo main

# 3. (Optional) Configure push mirror via Forgejo UI
# Repository Settings → Mirror Sync → Add push mirror → GitHub URL
```

That's it. Everything else (push mirrors, pull mirrors, webhooks) is optional.

---

## Verification

### Current State (GitHub-Primary)

```bash
# Verify GitHub is working
git push origin main  # Should succeed
gh repo view  # Should show repo info
```

### Future State (After Forgejo Migration)

```bash
# Verify Forgejo is accessible
curl -I https://botburrow-git.ardenone.com  # Should return 200

# Verify push works
git push forgejo main  # Should succeed

# Verify pull works
git pull forgejo main  # Should succeed
```

---

## Success Criteria

### MVP Success (Current)

- [x] GitHub repos exist and are accessible
- [x] Developers can push to GitHub
- [x] CI/CD works via GitHub Actions
- [x] Migration path is documented

### Full Success (Future)

- [ ] Forgejo pod is Running
- [ ] Git push to Forgejo works
- [ ] Push mirrors configured (optional)
- [ ] Pull mirrors configured (optional)

---

## Related Documents

| Document | Purpose |
|----------|---------|
| **This document** | MVP requirements (minimal) |
| ADR-028 | Full architecture (comprehensive) |
| forgejo-github-sync-workaround.md | Workaround details |
| bd-2ot approaches doc | 6 different approaches compared |

---

## Decision Record

**Chosen Approach:** GitHub-First (Current Workaround)

**Why:**
- Works NOW without any changes
- Zero risk of data loss
- Full CI/CD functionality
- Clear migration path

**When to Revisit:**
- After Forgejo pod is fixed (bd-2we resolved)
- OR if team decides to stay on GitHub permanently

---

**End of MVP Requirements**

*Generated for bead bd-16n on 2026-02-08*
