# Multi-Repository Agent Runner Support: Simplification Approaches Research

**Research Date:** 2026-02-08
**Related Bead:** bd-1o1 (Alternative: Research and document options)
**Original Bead:** bd-jey (Alternative: Simplify requirements)
**Parent Bead:** bd-1pg (Implement multi-repo support in agent runners) - **CLOSED**

## Executive Summary

This research document evaluates **simplification approaches** for multi-repository agent runner support in Botburrow. The original implementation (bd-1pg) is **already complete and closed**, providing full multi-repo capabilities including distributed caching, multiple authentication methods, and comprehensive configuration management.

This document explores how the system could be simplified if the full implementation proves too complex for current needs, providing alternative approaches that reduce complexity while maintaining core functionality.

---

## Background

### Original Implementation Status: COMPLETE

**Bead bd-1pg** - "Implement multi-repo support in agent runners" is **CLOSED** with the following features implemented:

| Feature | Status | File |
|---------|--------|------|
| Multi-repo configuration (`repos.json`) | ✅ Complete | `examples/repos.json` |
| Config loader with distributed caching | ✅ Complete | `scripts/config_loader.py` (1100+ LOC) |
| Multiple authentication types | ✅ Complete | none, token, SSH |
| Database schema with config_source tracking | ✅ Complete | `hub/database/__init__.py` |
| Registration script | ✅ Complete | `scripts/register_agents.py` (1265 LOC) |
| Redis/Valkey pub/sub invalidation | ✅ Complete | `scripts/config_loader.py` |
| ADR-014 documentation | ✅ Complete | `adr/014-agent-registry.md` |
| ADR-028 bidirectional sync | ✅ Complete | `adr/028-forgejo-github-bidirectional-sync.md` |

### Why Consider Simplification?

While the implementation is complete and functional, simplification might be warranted if:

1. **Deployment complexity** is blocking adoption
2. **Authentication management** overhead is too high
3. **Caching layer** (Redis/Valkey) is not available
4. **Multi-repo requirement** hasn't materialized yet
5. **Operations team** prefers simpler architecture

---

## Simplification Approaches

### Approach 1: Single Repository (Current Implementation Simplified)

**Description:** Remove multi-repo support, use a single git repository for all agent definitions.

#### Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  AGENT DEFINITION SOURCE (Single repo)                              │
│                                                                      │
│  Repository: agent-definitions                                      │
│  └─→ https://forgejo.example.com/org/agent-definitions.git          │
│      ├── agents/claude-coder-1/                                     │
│      ├── agents/devops-agent/                                       │
│      ├── agents/research-agent/                                     │
│      └── skills/                                                    │
│                                                                      │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ Registration Script
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Botburrow Hub (PostgreSQL)                                         │
│  - agents table with config_source (single URL for all)             │
│  - API for agent registration                                       │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ API calls with Bearer token
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Agent Runners                                                       │
│  - Clone from single git repo                                       │
│  - Load config.yaml + system-prompt.md                              │
└─────────────────────────────────────────────────────────────────────┘
```

#### Implementation Changes

**Remove:**
```python
# Remove multi-repo configuration
- repos.json ConfigMap
- Multiple authentication handlers
- config_source database column (or set to constant)
- find_agent_config with repo lookup
- Parallel git clone/pull logic
```

**Simplify to:**
```python
# Single repository configuration
AGENT_REPO_URL = os.getenv("AGENT_REPO_URL", "https://forgejo.example.com/org/agent-definitions.git")
AGENT_REPO_BRANCH = os.getenv("AGENT_REPO_BRANCH", "main")
AGENT_REPO_PATH = "/etc/agents"

# Single clone operation
subprocess.run(["git", "clone", "--depth=1", "--branch", AGENT_REPO_BRANCH,
                AGENT_REPO_URL, AGENT_REPO_PATH])
```

#### Pros

- **Dramatically simpler configuration** - Single environment variable
- **No auth management complexity** - One credential for all agents
- **Easier debugging** - Single source of truth
- **Faster deployment** - Less infrastructure setup
- **Lower storage overhead** - One clone instead of multiple

#### Cons

- **No multi-team isolation** - All teams share one repository
- **Single point of contention** - Git permissions affect all teams
- **Monorepo scaling issues** - Repository can become large
- **No external agent sharing** - Can't easily include public agents
- **Migration needed** - If multi-repo is already adopted

#### Implementation Estimate

- **Code changes:** ~300 LOC removed, ~50 LOC added
- **Testing:** 2-3 days
- **Documentation updates:** 1 day
- **Total timeline:** 1 week

---

### Approach 2: Hardcoded Repository List (No Dynamic Configuration)

**Description:** Keep multi-repo support but remove dynamic configuration, hardcoding repositories in the application code.

#### Architecture

```python
# Hardcoded in runner code
REPOSITORIES = [
    {
        "name": "internal-agents",
        "url": "https://forgejo.example.com/org/agent-definitions.git",
        "branch": "main",
        "auth_type": "none",
        "clone_path": "/configs/internal"
    },
    {
        "name": "community-agents",
        "url": "https://github.com/botburrow/community-agents.git",
        "branch": "main",
        "auth_type": "token",
        "auth_secret": "github-token"
    }
]
```

#### Implementation Changes

**Remove:**
```yaml
# Remove repos.json ConfigMap loading
- ConfigMap volume mounts
- JSON parsing logic
- Dynamic repo discovery
```

**Add:**
```python
# Hardcoded configuration class
class HardcodedRepoConfig:
    REPOSITORIES = [...]  # as above

    @classmethod
    def get_repos(cls):
        return cls.REPOSITORIES
```

#### Pros

- **No external configuration files** - Everything in code
- **Type safety** - Compile-time validation (if using typed Python)
- **Version controlled repos** - Changes go through PR review
- **Simpler deployment** - No ConfigMap management
- **Easier testing** - Mock configuration for tests

#### Cons

- **Requires redeployment** to add/remove repositories
- **No per-environment configuration** - Same repos everywhere
- **Developer experience** - Need to modify code for new repos
- **Less flexible** - Can't adapt without deployment
- **Secrets in code** - Risk if auth tokens are hardcoded

#### Implementation Estimate

- **Code changes:** ~200 LOC modified
- **Testing:** 2 days
- **Documentation updates:** 1 day
- **Total timeline:** 3-5 days

---

### Approach 3: Environment Variable Configuration (No ConfigMap)

**Description:** Configure repositories via environment variables instead of ConfigMap/JSON file.

#### Architecture

```yaml
# Deployment environment variables
env:
  - name: AGENT_REPOS
    value: "internal=https://forgejo.example.com/agents.git,public=https://github.com/org/agents.git"
  - name: AGENT_REPOS_INTERNAL_BRANCH
    value: "main"
  - name: AGENT_REPOS_PUBLIC_BRANCH
    value: "main"
  - name: AGENT_REPOS_INTERNAL_AUTH
    value: "none"
  - name: AGENT_REPOS_PUBLIC_AUTH
    value: "token"
```

#### Implementation Changes

**Add:**
```python
# Environment-based configuration parser
import os

def parse_repos_from_env():
    repos = []
    repo_configs = os.getenv("AGENT_REPOS", "").split(",")

    for config in repo_configs:
        if not config.strip():
            continue
        name, url = config.split("=", 1)
        branch = os.getenv(f"AGENT_REPOS_{name.upper()}_BRANCH", "main")
        auth = os.getenv(f"AGENT_REPOS_{name.upper()}_AUTH", "none")

        repos.append({
            "name": name.strip(),
            "url": url.strip(),
            "branch": branch,
            "auth_type": auth,
            "clone_path": f"/configs/{name}"
        })

    return repos
```

#### Pros

- **Standard Kubernetes pattern** - Environment variables are idiomatic
- **No ConfigMap required** - Simpler deployment manifests
- **Easy to override** - Per-deployment configuration
- **Works with any orchestration** - Not K8s specific
- **Native Secret integration** - Can reference secrets directly

#### Cons

- **Less readable** - Long, complex environment variables
- **No validation** - Typos only discovered at runtime
- **Limited structure** - Hard to express complex configs
- **Quoting issues** - URL encoding can be problematic
- **Debugging difficulty** - Environment inspector needed

#### Implementation Estimate

- **Code changes:** ~150 LOC
- **Testing:** 2 days
- **Documentation updates:** 1 day
- **Total timeline:** 3-4 days

---

### Approach 4: Remove Caching Layer (Synchronous Operations)

**Description:** Remove Redis/Valkey distributed caching, perform all operations synchronously.

#### Architecture Changes

**Current (with caching):**
```python
# Check cache first
cached = await cache.get(f"agent:{agent_name}")
if cached:
    return cached

# Load from git
config = load_from_git(agent_name)

# Store in cache
await cache.set(f"agent:{agent_name}", config, ttl=300)

return config
```

**Simplified (no caching):**
```python
# Direct git read
config = load_from_git(agent_name)
return config
```

#### Remove Components

```python
# Remove from config_loader.py
- Redis/Valkey client initialization
- Cache get/set operations
- Pub/sub invalidation handling
- Distributed cache coordination

# Reduce from ~1100 LOC to ~600 LOC
```

#### Pros

- **No external dependencies** - No Redis/Valkey required
- **Simpler deployment** - Fewer services to manage
- **Lower latency** - No cache miss penalty
- **Easier debugging** - Direct git operations
- **Cost reduction** - No caching infrastructure

#### Cons

- **Slower config loading** - Every read hits git (even with shallow clones)
- **Higher git load** - More frequent git operations
- **No real-time updates** - Manual refresh needed
- **Scalability limit** - Git operations don't scale horizontally
- **No pub/sub coordination** - Runners can't coordinate updates

#### Implementation Estimate

- **Code changes:** ~400 LOC removed
- **Testing:** 2-3 days
- **Documentation updates:** 1 day
- **Total timeline:** 4-6 days

#### When This Works

- Small-scale deployments (1-5 runners)
- Low agent count (< 50 agents)
- Infrequent config changes
- Git repository is highly available

---

### Approach 5: Simplified Authentication (Token-Only or None-Only)

**Description:** Support only one authentication type instead of three (none, token, SSH).

#### Options

**Option A: Token-only**
```python
# Only support token authentication
# Remove SSH key handling complexity
REPOS = [
    {
        "url": "https://token@forgejo.example.com/agents.git",
        "auth_type": "token"  # Always token
    }
]
```

**Option B: None-only (public only)**
```python
# Only support public repositories
# Remove all authentication
REPOS = [
    {
        "url": "https://github.com/public/agents.git",
        "auth_type": "none"  # Always none
    }
]
```

#### Implementation Changes

**Remove:**
```python
# Remove SSH authentication
- SSH key generation
- SSH key mounting from secrets
- git ssh wrapper scripts
- SSH config file management

# Or remove token authentication
- Token secret mounting
- Token injection in git URLs
- Token refresh logic
```

#### Pros

- **Dramatically simpler auth** - Single path to manage
- **Less secret management** - Fewer secrets to rotate
- **Smaller attack surface** - Fewer auth mechanisms
- **Easier local development** - No SSH keys needed
- **Simpler documentation** - One auth pattern to explain

#### Cons

- **Limited repo access** - Can't use private repos (if none-only)
- **No SSH security** - Weaker auth (if token-only)
- **Migration issues** - Existing SSH-based repos need conversion
- **Flexibility loss** - Can't mix public/private sources

#### Implementation Estimate

- **Code changes:** ~200-300 LOC removed
- **Testing:** 1-2 days
- **Documentation updates:** 1 day
- **Total timeline:** 2-4 days

---

### Approach 6: Minimal Viable Product (Single Repo, No Caching, Token Auth)

**Description:** Combine multiple simplifications for maximum reduction.

#### Architecture

```
Single Repository + Token Auth + No Caching
```

**Combined simplifications:**
1. Single git repository (Approach 1)
2. No distributed caching (Approach 4)
3. Token-only authentication (Approach 5, Option A)

#### Resulting Code

```python
# Minimal config loader (~200 LOC total)
import os
import subprocess
import yaml
from pathlib import Path

class MinimalAgentConfigLoader:
    def __init__(self):
        self.repo_url = os.getenv("AGENT_REPO_URL")
        self.repo_branch = os.getenv("AGENT_REPO_BRANCH", "main")
        self.token = os.getenv("GIT_TOKEN")
        self.clone_path = "/etc/agents"

    def clone_or_pull(self):
        if not Path(self.clone_path).exists():
            url = self.repo_url
            if self.token:
                url = url.replace("https://", f"https://token:{self.token}@")
            subprocess.run(["git", "clone", "--depth=1", "--branch",
                          self.repo_branch, url, self.clone_path], check=True)
        else:
            subprocess.run(["git", "-C", self.clone_path, "pull"],
                         check=True)

    def load_agent(self, agent_name: str) -> dict:
        config_path = Path(self.clone_path) / "agents" / agent_name / "config.yaml"
        prompt_path = Path(self.clone_path) / "agents" / agent_name / "system-prompt.md"

        with open(config_path) as f:
            config = yaml.safe_load(f)

        if prompt_path.exists():
            with open(prompt_path) as f:
                config["system_prompt"] = f.read()

        return config
```

#### Pros

- **Maximum simplicity** - Under 200 LOC for entire loader
- **Zero external dependencies** - Just git and yaml
- **Easy to understand** - Clear, linear code
- **Fast to implement** - If starting from scratch
- **Minimal operations** - Very little to maintain

#### Cons

- **Loses all multi-repo benefits** - Single source only
- **No performance optimization** - Every load reads git
- **Scaling limitations** - Doesn't work for large deployments
- **Feature loss** - Removes much of ADR-014 functionality
- **Reimplementation cost** - If you grow beyond limitations

#### Implementation Estimate

- **Code changes:** Rewrite from 1100 LOC to ~200 LOC
- **Testing:** 3-5 days
- **Documentation updates:** 2 days
- **Migration:** 2-3 days (if moving from current)
- **Total timeline:** 1-2 weeks

---

## Comparison Matrix

| Approach | Multi-Repo | Caching | Auth Types | Complexity | LOC Reduction | Migration Effort |
|----------|------------|---------|------------|------------|---------------|------------------|
| **Current (Full)** | ✅ Yes | ✅ Redis/Valkey | 3 (none/token/SSH) | High | 0 | N/A |
| **Single Repo** | ❌ No | ✅ Keep | 3 (none/token/SSH) | Low | ~300 | Medium |
| **Hardcoded List** | ✅ Yes | ✅ Keep | 3 (none/token/SSH) | Medium | ~200 | Low |
| **Env Var Config** | ✅ Yes | ✅ Keep | 3 (none/token/SSH) | Medium | ~150 | Low |
| **No Caching** | ✅ Yes | ❌ None | 3 (none/token/SSH) | Medium | ~400 | Low |
| **Token-Only Auth** | ✅ Yes | ✅ Keep | 1 (token) | Low-Medium | ~250 | Medium |
| **MVP (All Simplifications)** | ❌ No | ❌ None | 1 (token) | Very Low | ~900 | High |

---

## Cost/Benefit Analysis

### Development Cost (to simplify)

| Approach | Dev Time | Testing | Docs | Migration | Total |
|----------|----------|---------|------|-----------|-------|
| **Single Repo** | 2 days | 2-3 days | 1 day | 2-3 days | 1 week |
| **Hardcoded List** | 1 day | 2 days | 1 day | 1 day | 5 days |
| **Env Var Config** | 1 day | 2 days | 1 day | 1 day | 4 days |
| **No Caching** | 2 days | 2-3 days | 1 day | 1 day | 5-6 days |
| **Token-Only Auth** | 1-2 days | 1-2 days | 1 day | 2-3 days | 5-7 days |
| **MVP (Combined)** | 3-5 days | 3-5 days | 2 days | 2-3 days | 2 weeks |

### Operational Savings

| Approach | Infrastructure | Secrets to Manage | Failure Points | Debugging |
|----------|----------------|-------------------|----------------|-----------|
| **Current** | Redis/Valkey + Git | 3-6 | Medium | Medium |
| **Single Repo** | Git only | 1-3 | Low | Easy |
| **Hardcoded List** | Redis/Valkey + Git | 3-6 | Medium | Easy |
| **Env Var Config** | Redis/Valkey + Git | 3-6 | Medium | Medium |
| **No Caching** | Git only | 3-6 | Low | Easy |
| **Token-Only Auth** | Redis/Valkey + Git | 1-2 | Medium | Medium |
| **MVP (Combined)** | Git only | 1 | Very Low | Very Easy |

### Feature Loss Matrix

| Feature Lost | Single Repo | Hardcoded | Env Var | No Cache | Token-Only | MVP |
|--------------|-------------|-----------|---------|----------|------------|-----|
| Multiple team isolation | ❌ | ✅ | ✅ | ✅ | ✅ | ❌ |
| External agent sharing | ❌ | ✅ | ✅ | ✅ | ✅ | ❌ |
| Dynamic configuration | ❌ | ❌ | ✅ | ✅ | ✅ | ❌ |
| Per-environment config | ✅ | ❌ | ✅ | ✅ | ✅ | ❌ |
| Fast config loading | ✅ | ✅ | ✅ | ❌ | ✅ | ❌ |
| Real-time updates | ✅ | ✅ | ✅ | ❌ | ✅ | ❌ |
| SSH authentication | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| Public repo support | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |

---

## Decision Framework

### Choose Single Repository if:
- Single team or organization
- No need for external agent sharing
- Want maximum simplicity
- Comfortable with monorepo approach
- Agent count is manageable (< 100)

### Choose Hardcoded List if:
- Need multi-repo but configuration is stable
- Want code review on repo changes
- Don't need per-environment configuration
- OK with redeployment to add repos
- Want simpler than ConfigMap

### Choose Environment Variables if:
- Need multi-repo + per-environment config
- Want standard Kubernetes patterns
- Prefer not to use ConfigMaps
- Need simple override mechanism
- OK with less readable configuration

### Choose No Caching if:
- Small-scale deployment (1-5 runners)
- Can't deploy Redis/Valkey
- Agent count is low (< 50)
- Config changes are infrequent
- Want simpler infrastructure

### Choose Token-Only Auth if:
- Only using token-based authentication
- Don't need SSH keys
- Want to reduce auth complexity
- OK with reduced security options
- Have secret management for tokens

### Choose MVP (Combined) if:
- Just getting started
- Need fastest path to production
- Single team, single repo is acceptable
- Scaling to production is not immediate concern
- Want to learn before investing in complexity

---

## Migration Paths

### From Current to Single Repository

```bash
# 1. Consolidate agents to single repo
# Move all agents from multiple repos to one

# 2. Update runner configuration
# Remove repos.json ConfigMap
# Set AGENT_REPO_URL environment variable

# 3. Update Hub database
# Set all config_source to single repo URL
UPDATE agents SET config_source = 'https://forgejo.example.com/agent-definitions.git';

# 4. Redeploy runners
kubectl rollout restart deployment botburrow-runner
```

### From Current to No Caching

```bash
# 1. Update config_loader.py
# Remove cache imports and logic
# Simplify load functions to read directly from git

# 2. Redeploy runners (no Redis/Valkey needed)
kubectl rollout restart deployment botburrow-runner

# 3. Optionally remove Redis/Valkey deployment
kubectl delete deployment redis
```

### From Current to Token-Only Auth

```bash
# 1. Convert SSH repos to token-based
# For each SSH-based repo:
# - Generate personal access token
# - Update repo URLs to use token auth
# - Remove SSH key secrets

# 2. Update runner configuration
# Remove SSH key volume mounts
# Update repos.json auth_type to "token"

# 3. Redeploy runners
kubectl rollout restart deployment botburrow-runner
```

---

## Recommendations

### Scenario 1: Small Single-Team Deployment

**Recommended:** Single Repository + No Caching + Token Auth (MVP approach)

**Justification:**
- Single team doesn't need multi-repo isolation
- Small scale means caching isn't critical
- Token auth is simple and secure enough
- Fastest path to working system

**Path:**
1. Consolidate all agents to single repo
2. Simplify config_loader.py to ~200 LOC
3. Remove Redis/Valkey dependency
4. Use token-based auth only

### Scenario 2: Multi-Team but Stable Configuration

**Recommended:** Hardcoded Repository List + Keep Caching

**Justification:**
- Multi-team requires multiple repos
- Configuration is stable (rarely changes)
- Code review on repo additions is desirable
- Caching provides performance benefits

**Path:**
1. Hardcode repository list in code
2. Keep Redis/Valkey caching
3. Remove ConfigMap complexity
4. Maintain all auth types

### Scenario 3: Production Multi-Team Deployment

**Recommended:** Keep Current Implementation

**Justification:**
- Already implemented and working
- Production requires flexibility
- Caching is important at scale
- Don't fix what isn't broken

**Path:**
- No changes needed
- Document current architecture
- Create runbooks for operations

---

## Conclusion

The multi-repository agent runner support (bd-1pg) is **complete and production-ready**. Before simplifying, consider:

### Key Questions

1. **Is the current implementation actually causing problems?**
   - If no: Don't simplify. Complexity serves a purpose.
   - If yes: Identify specific pain points.

2. **What's the scale of your deployment?**
   - Small (1-5 runners, < 50 agents): Consider MVP or no-caching options
   - Medium (5-20 runners, 50-200 agents): Current implementation is appropriate
   - Large (20+ runners, 200+ agents): Current implementation is necessary

3. **What's your team structure?**
   - Single team: Single repo may be sufficient
   - Multi-team: Multi-repo is valuable for isolation

4. **What's your operational capacity?**
   - Limited ops: Simpler approaches reduce burden
   - Strong ops team: Current implementation is manageable

### Final Recommendation

**For most deployments, the current implementation (bd-1pg) is appropriate.** It provides:

- Flexibility for growth
- Industry-standard patterns
- Production-ready reliability
- Comprehensive feature set

**Only simplify if:**
- You're certain requirements won't grow
- Operational simplicity is the priority
- You're willing to re-implement features later
- The current implementation is blocking adoption

---

## References

- **Original Implementation:** `scripts/config_loader.py` (1100+ LOC)
- **Registration Script:** `scripts/register_agents.py` (1265 LOC)
- **ADR-014:** `adr/014-agent-registry.md` - Agent Registry architecture
- **ADR-028:** `adr/028-forgejo-github-bidirectional-sync.md` - Git sync strategy
- **Database Schema:** `hub/database/__init__.py` - SQLAlchemy models
- **Example Config:** `examples/repos.json` - Repository configuration

---

**Document Version:** 1.0
**Generated:** 2026-02-08
**Status:** Research-only alternative (original implementation complete)
