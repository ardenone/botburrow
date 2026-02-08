# Forgejo-GitHub Sync: Executive Summary & Decision Guide

**Alternative Research For:** bd-145 (CLOSED) - Set up Forgejo ↔ GitHub bidirectional sync
**Research Bead:** bd-ik1 (Alternative: Research and document options)
**Date:** 2026-02-08
**Status:** Existing research consolidated for human decision

---

## TL;DR

**Extensive research already exists.** This document summarizes existing documentation and provides a clear decision framework.

**Current State:** GitHub is working as primary. Forgejo pod is CrashLoopBackOff due to RBAC blocking fix application.

**Decision Needed:** Which approach should we take for Forgejo-GitHub sync going forward?

---

## Existing Documentation (Already Comprehensive)

| Document | Location | Purpose |
|----------|----------|---------|
| **ADR-028** | `adr/028-forgejo-github-bidirectional-sync.md` | Full bidirectional sync architecture specification |
| **Approaches Comparison** | `docs/alternatives/bd-2ot-forgejo-github-sync-approaches.md` | 6 approaches with detailed pros/cons, complexity matrices |
| **MVP Requirements** | `docs/forgejo-github-sync-mvp.md` | Minimal viable sync requirements |
| **Workaround Doc** | `docs/forgejo-github-sync-workaround.md` | Current workaround implementation |
| **Simplified Guide** | `docs/forgejo-github-sync-simplified.md` | Simplified setup instructions |
| **Status Tracking** | `docs/forgejo-github-sync-status.md` | Current deployment status |

**Conclusion:** No additional research is needed. The existing documentation is comprehensive and actionable.

---

## Decision: Which Approach Should We Use?

### Option A: Full Bidirectional Sync (ADR-028) - **Recommended for Production**

**What it is:**
- Forgejo as primary (authoritative) git host
- Automatic push mirrors to GitHub (sync_on_commit: true)
- Automatic pull from GitHub on Forgejo restart (disaster recovery)
- Optional GitHub webhook for near-real-time sync back to Forgejo

**Pros:**
- Fully self-hosted control
- Automatic disaster recovery
- Every commit immediately backed up to GitHub
- Public visibility via GitHub mirror
- CI/CD via GitHub Actions

**Cons:**
- Requires working Forgejo deployment
- More complex setup

**Prerequisites:**
- [ ] Resolve bd-2we (RBAC permission to apply Forgejo fix)
- [ ] Forgejo pod becomes healthy
- [ ] GitHub PAT with repo permissions

**Implementation:** See `docs/forgejo-github-sync-simplified.md` for step-by-step setup.

---

### Option B: GitHub-First (Current Workaround) - **Recommended for Now**

**What it is:**
- GitHub as primary git host
- Manual push to Forgejo when available
- No automatic sync

**Pros:**
- Works NOW without any changes
- Zero risk of data loss
- Full CI/CD via GitHub Actions
- Simple to understand

**Cons:**
- No automatic sync to Forgejo
- Must remember to push to both remotes manually
- No disaster recovery automation

**When to use:**
- While Forgejo deployment is blocked
- For simple projects with few developers
- If you decide Forgejo isn't needed

**Status:** Currently active. See `docs/forgejo-github-sync-workaround.md`.

---

### Option C: GitHub Actions Sync - **Alternative Without Forgejo Dependency**

**What it is:**
- GitHub Actions workflow periodically syncs repos
- Customizable merge logic
- Runs on GitHub infrastructure

**Pros:**
- No Forgejo dependency
- Customizable logic
- Built-in monitoring
- Free/cheap

**Cons:**
- Not real-time
- Complex conflict handling
- Credential management
- External dependency on GitHub Actions

**When to use:**
- Don't want Forgejo dependency
- Need custom merge logic
- Already using GitHub Actions heavily

**Reference:** See `docs/alternatives/bd-2ot-forgejo-github-sync-approaches.md` section 4.

---

## Current Blockers

| Bead | Title | Status |
|------|-------|--------|
| bd-2we | HUMAN: RBAC permission needed to apply Forgejo deployment fix | Open - awaiting human input |
| bd-3vqp | RBAC blocks Forgejo deployment update | Open - awaiting human input |

**To unblock:** Grant RBAC permissions to apply the Forgejo deployment fix (commit 1a3e8cbfe in ardenone-cluster repo).

---

## Comparison Matrix (Quick Reference)

| Feature | Full Sync (ADR-028) | GitHub-First | GitHub Actions |
|---------|-------------------|--------------|----------------|
| **Real-time sync** | Push: Yes / Pull: On restart | No | Scheduled (min) |
| **Bidirectional** | Yes | Manual | Yes (logic) |
| **Automation** | Full | None | Full |
| **Forgejo required** | Yes | Optional | No |
| **Setup complexity** | Medium | Low | Medium |
| **Maintenance** | Low | High | Medium |
| **Disaster recovery** | Automated | Manual | Manual |
| **Current status** | Blocked (Forgejo down) | **Active** | Available |

---

## Recommended Next Steps (Choose One)

### Path 1: Full Bidirectional Sync (Production-Ready)

**If you want Forgejo as primary with automatic sync:**

1. Resolve bd-2we (get RBAC permissions)
2. Apply Forgejo deployment fix
3. Follow steps in `docs/forgejo-github-sync-simplified.md`
4. Verify sync with test commits
5. Close bd-2vr (Implement Forgejo push mirrors)

**Time to implement:** ~1 hour (after RBAC unblocked)

---

### Path 2: Stay on GitHub-First (Keep Current Workaround)

**If you're okay with GitHub as primary:**

1. No action needed
2. Continue using GitHub as primary
3. Add Forgejo remote later when pod is fixed (if needed)
4. Optionally close bd-2vr as "won't fix"

**Time to implement:** 0 minutes (already done)

---

### Path 3: GitHub Actions Sync (No Forgejo Dependency)

**If you want automation without Forgejo:**

1. Create GitHub Actions workflow in each repo
2. Configure sync logic (merge strategy)
3. Store Forgejo credentials in GitHub Secrets
4. Test workflow manually, then enable schedule

**Time to implement:** ~2 hours

---

## Active Beads Related to This Decision

| Bead | Title | Status | Action Required |
|------|-------|--------|-----------------|
| bd-2we | HUMAN: RBAC permission for Forgejo fix | Open | **Human decision needed** |
| bd-3vqp | RBAC blocks Forgejo deployment | Open | Depends on bd-2we |
| bd-2vr | Implement Forgejo push mirrors | Open | Blocked on Forgejo health |
| bd-ik1 | This bead - research summary | In Progress | Close after human review |

---

## Human Decision Required

**Question:** Which approach should we take for Forgejo-GitHub sync?

**Options:**
1. **Full Bidirectional Sync (ADR-028)** - Wait for Forgejo fix, implement full automation
2. **GitHub-First (Current)** - Stay on workaround, add Forgejo later manually
3. **GitHub Actions Sync** - Implement automation without Forgejo dependency
4. **Defer Decision** - Keep current workaround, revisit later

**To respond:** Update bead bd-ik1 or bd-2we with your decision, or create a new decision bead.

---

## Related Documentation Index

**Primary Documents:**
- `adr/028-forgejo-github-bidirectional-sync.md` - Architecture spec
- `docs/alternatives/bd-2ot-forgejo-github-sync-approaches.md` - 6 approaches detailed
- `docs/forgejo-github-sync-simplified.md` - Simplified setup guide

**Supporting Documents:**
- `docs/forgejo-github-sync-status.md` - Current deployment status
- `docs/forgejo-github-sync-workaround.md` - Current workaround
- `docs/forgejo-github-sync-mvp.md` - MVP requirements

**Related Repos:**
- `ardenone-cluster` (commit 1a3e8cbfe) - Forgejo deployment fix

---

**Document End**

*Generated for bead bd-ik1 on 2026-02-08*
*Consolidates existing research from bd-2ot, bd-i1c, bd-16n, and ADR-028*
