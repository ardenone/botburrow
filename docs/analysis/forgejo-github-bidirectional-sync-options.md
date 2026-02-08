# Forgejo ↔ GitHub Bidirectional Sync: Options Analysis

**Generated for:** bd-145 (Set up Forgejo ↔ GitHub bidirectional sync)
**Alternative approach:** bd-ik1 (Research-only documentation)
**Generated:** 2026-02-08
**Workspace:** /home/coder/research/botburrow

---

## Executive Summary

**Key Finding:** True bidirectional synchronization (Git + Issues + PRs + metadata) between Forgejo and GitHub **does not exist as a turnkey solution**. Current options range from built-in one-way mirroring to custom webhook-based solutions.

**Recommended Approach:** For Botburrow's use case, consider the options below based on your specific requirements (Git-only vs. full repository sync including issues/PRs).

---

## Option 1: Built-in Repository Mirroring (Forgejo Native)

### Description
Forgejo has built-in push and pull mirroring capabilities. This is the simplest, most reliable option for **Git-only synchronization**.

### How It Works

**Push Mirror (Forgejo → GitHub):**
1. Settings → Repository → Mirror Settings → Add Push Mirror
2. Enter GitHub repository URL with authentication
3. Select "Sync when new commits are pushed" for real-time updates

**Pull Mirror (GitHub → Forgejo):**
1. Create → New Migration → Select service (GitHub)
2. Check "This repository will be a mirror"
3. Set synchronization interval

### Pros
- **Native Forgejo feature** - no external dependencies
- **Simple setup** - UI-based configuration
- **Reliable** - maintained by Forgejo project
- **Real-time option** - can sync on push (Forgejo 1.18+)
- **Authenticated** - supports PAT or SSH keys
- **Branch filtering** - can sync specific branches only

### Cons
- **Git-only** - does NOT sync issues, PRs, labels, wiki, or releases
- **Not truly bidirectional** - you set up separate push and pull mirrors
- **Force push** - push mirror uses force push, can overwrite remote changes
- **Pull mirror limitation** - can only be configured during repository creation

### Implementation Complexity
**Low** - 5-10 minutes per repository via UI

### Best For
- Simple code backup/mirroring
- CI/CD pipelines that only need Git sync
- Teams that can manage issues separately on each platform

### Sources
- [Forgejo Repository Mirrors Documentation](https://forgejo.org/docs/next/user/repo-mirror/)

---

## Option 2: Webhook + API Custom Service

### Description
Build a custom service that receives webhooks from both Forgejo and GitHub, then uses their respective APIs to synchronize changes bidirectionally.

### How It Works

```
┌─────────┐        push webhook        ┌──────────────┐        API call         ┌────────┐
│ Forgejo │ ──────────────────────────>│              │ ──────────────────────>│ GitHub │
└─────────┘                            │  Sync        │                        └────────┘
                                       │  Service     │
┌─────────┐        push webhook        │  (Python/    │        API call         ┌────────┐
│ GitHub  │ <──────────────────────────│  Node.js/    │ <──────────────────────│ Forgejo│
└─────────┘                            │  Go)         │                        └────────┘
                                       └──────────────┘
```

### Pros
- **Fully bidirectional** - can sync Git, issues, PRs, comments, labels
- **Real-time** - webhook-driven, no polling delays
- **Customizable** - implement only the sync features you need
- **Conflict resolution** - implement your own merge strategies
- **Platform independence** - not tied to any Forgejo/GitHub release

### Cons
- **High implementation effort** - requires custom development
- **Maintenance burden** - you own the code and bugs
- **API rate limits** - must handle GitHub/Forgejo rate limiting
- **Authentication management** - secure storage of multiple tokens
- **Failure handling** - need retry logic, dead letter queues

### Implementation Sketch

```python
# Pseudo-code for webhook sync service
@app.post("/webhook/forgejo")
async def forgejo_webhook(payload: dict):
    event_type = payload.get("event_type")

    if event_type == "push":
        await sync_commit_to_github(payload)
    elif event_type == "pull_request":
        await sync_pr_to_github(payload)
    elif event_type == "issues":
        await sync_issue_to_github(payload)

@app.post("/webhook/github")
async def github_webhook(payload: dict):
    # Similar handling for GitHub events
    await sync_to_forgejo(payload)
```

### Recommended Tech Stack
- **Language:** Python (with [pyforgejo](https://pypi.org/project/pyforgejo/) + [PyGithub](https://github.com/PyGithub/PyGithub))
- **Deployment:** Containerized service (can run in your existing K8s cluster)
- **Message Queue:** Redis or RabbitMQ for reliable event processing

### Implementation Complexity
**High** - 20-40 hours initial development + ongoing maintenance

### Best For
- Teams with development resources
- Requirements for full repository sync (including issues/PRs)
- Real-time synchronization needs

### Sources
- [Forgejo Webhooks Documentation](https://forgejo.org/docs/next/user/webhooks/)
- [pyforgejo Python Client](https://pypi.org/project/pyforgejo/)
- [Bidirectional GitHub Repository Sync Guide](https://faun.pub/bidirectional-github-repository-sync-when-your-white-label-game-needs-automation-31dede07c606)

---

## Option 3: Forgesync (Forgejo → GitHub One-Way)

### Description
Forgesync is a community tool that automatically synchronizes all Forgejo repositories to GitHub. It sets up and manages Forgejo's built-in push mirrors.

### How It Works
1. Run forgesync periodically (cron job)
2. It queries Forgejo API for all repositories
3. Creates/updates push mirrors on each repository
4. Handles authentication and configuration

### Pros
- **Bulk sync** - manages multiple repositories automatically
- **Metadata sync** - syncs repo descriptions, topics, visibility
- **Auto-creation** - creates target GitHub repos if needed
- **Multiple deployment options** - Nix, container, standalone
- **Open source** - MIT licensed

### Cons
- **One-way only** - Forgejo → GitHub, not bidirectional
- **Git-only** - does not sync issues, PRs, comments
- **Requires tokens** - needs SOURCE_TOKEN, TARGET_TOKEN, MIRROR_TOKEN
- **External dependency** - community-maintained tool

### Implementation
```bash
# Container deployment
docker run -d \
  -e SOURCE_TOKEN="forgejo-token" \
  -e TARGET_TOKEN="github-token" \
  -e MIRROR_TOKEN="github-mirror-token" \
  ghcr.io/lukaswrz/forgesync:latest
```

### Implementation Complexity
**Low** - 1-2 hours for initial setup

### Best For
- Bulk mirroring of many repos from Forgejo to GitHub
- One-way backup/preservation scenarios
- Teams comfortable with container deployment

### Sources
- [forgesync GitHub Repository](https://github.com/lukaswrz/forgesync)

---

## Option 4: Multi-Platform Git Tools (multigit)

### Description
Command-line tools that manage pushing/pulling to multiple Git platforms simultaneously.

### How It Works
Instead of setting up mirrors, you configure multiple remotes and push to all of them:

```bash
# Using multigit
mg sync  # Pushes to all configured platforms
```

### Pros
- **Simple concept** - uses standard Git remotes
- **Multi-platform** - supports GitHub, GitLab, Bitbucket, Forgejo, Codeberg
- **Developer-controlled** - each developer can use it locally
- **No server needed** - runs on developer machines or CI

### Cons
- **Manual operation** - requires explicit action (not automatic)
- **Git-only** - no issue/PR synchronization
- **Per-repo setup** - must configure for each repository
- **No web UI** - CLI-based only

### Implementation Complexity
**Low** - 30 minutes per repository

### Best For
- Individual developers or small teams
- Ad-hoc synchronization needs
- CI/CD pipelines that can include multi-git push

### Sources
- [TIVerse/multigit GitHub Repository](https://github.com/TIVerse/multigit)

---

## Option 5: ForgeFed / ActivityPub Federation (Future)

### Description
ForgeFed is an ActivityPub-based federation protocol that enables decentralized collaboration between different forges (Forgejo, GitLab, Gitea, etc.).

### Current Status
- **In development** - not production-ready
- **GitHub unlikely to participate** - proprietary platform
- **Primarily for open-source forges** - GitHub has no incentive to adopt

### Why Consider
- **Future-proof** - if ForgeFed gains adoption
- **True federation** - designed for cross-platform collaboration
- **Wider ecosystem** - not just GitHub, but GitLab, Codeberg, etc.

### Recommendation
**Monitor but don't implement yet.** This is worth watching but not ready for production use.

### Sources
- [ForgeFed Website](https://forgefed.org/)
- [ForgeFed GitHub Repository](https://github.com/forgefed/forgefed)
- [Forgejo Federation Issue](https://codeberg.org/forgejo/forgejo/issues/59)

---

## Comparison Matrix

| Option | Bidirectional? | Git Sync | Issues/PRs Sync | Complexity | Maintenance |
|--------|----------------|----------|-----------------|------------|-------------|
| **Built-in Mirrors** | Partial (separate configs) | ✅ | ❌ | Low | Forgejo team |
| **Webhook + API** | ✅ Full | ✅ | ✅ Custom | High | You |
| **Forgesync** | ❌ One-way | ✅ | ❌ | Low | Community |
| **multigit** | ✅ Manual | ✅ | ❌ | Low | You |
| **ForgeFed** | ✅ (Future) | ✅ | ✅ | Unknown | Community |

---

## Decision Framework

### Choose **Built-in Mirrors** if:
- You only need Git synchronization (issues/PRs can stay separate)
- You want the simplest, most reliable solution
- You're okay with separate configurations for each direction

### Choose **Webhook + API Service** if:
- You need true bidirectional sync including issues and PRs
- You have development resources for custom implementation
- You need real-time synchronization
- You want full control over sync behavior and conflict resolution

### Choose **Forgesync** if:
- You primarily need Forgejo → GitHub mirroring
- You have many repositories to manage
- You want automated bulk synchronization

### Choose **multigit** if:
- You're a small team or individual developer
- You're okay with manual sync operations
- You want a simple CLI-based solution

### **Wait on ForgeFed**:
- Not production-ready yet
- GitHub participation unlikely

---

## Recommended Next Steps for Botburrow

### Questions to Answer Before Deciding:

1. **What needs to sync?**
   - Just Git commits? (Use built-in mirrors)
   - Issues and PRs too? (Need webhook + API solution)

2. **What's the primary platform?**
   - Forgejo is primary, GitHub is mirror? (Use push mirror)
   - GitHub is primary, Forgejo is mirror? (Use pull mirror)
   - Both are equal? (Need bidirectional solution)

3. **What are your resources?**
   - Time for custom development? (Webhook + API)
   - Need something working today? (Built-in mirrors)

4. **How many repositories?**
   - 1-5 repos? Manual setup is fine
   - 10+ repos? Consider automation (Forgesync, webhook service)

### Suggested Approach:

1. **Start with built-in mirrors** for immediate Git sync
2. **Evaluate if issue/PR sync is needed** - if not, you're done
3. **If full sync needed**, assess whether to:
   - Build custom webhook service
   - Use external tool/integration platform
   - Wait for ForgeFed ecosystem to mature

---

## Additional Resources

### Official Documentation
- [Forgejo Repository Mirrors](https://forgejo.org/docs/next/user/repo-mirror/)
- [Forgejo Webhooks](https://forgejo.org/docs/next/user/webhooks/)
- [Forgejo Actions Reference](https://forgejo.org/docs/next/user/actions/reference/)

### Community Tools
- [forgesync](https://github.com/lukaswrz/forgesync) - Bulk Forgejo → GitHub sync
- [multigit](https://github.com/TIVerse/multigit) - Multi-platform Git CLI
- [GITHUB2FORGEJO](https://github.com/PatNei/GITHUB2FORGEJO) - GitHub → Forgejo migration script
- [pyforgejo](https://pypi.org/project/pyforgejo/) - Python Forgejo API client

### Related Reading
- [Bidirectional GitHub Repository Sync (Faun)](https://faun.pub/bidirectional-github-repository-sync-when-your-white-label-game-needs-automation-31dede07c606)
- [Two-way mirroring feature request](https://codeberg.org/forgejo/forgejo/issues/7556)
- [Keeping git repositories on different hosts in sync](https://softwareengineering.stackexchange.com/questions/195456/keeping-git-repositories-on-different-hosts-in-sync)

---

**Document Status:** Complete
**Next Action:** Present options to human for decision on implementation approach
