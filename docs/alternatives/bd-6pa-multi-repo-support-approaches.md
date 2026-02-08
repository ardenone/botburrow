# Multi-Repository Agent Runner Support: Approaches Comparison

**Alternative Research For:** bd-1pg - Implement multi-repo support in agent runners
**Research Date:** 2026-02-08
**Status:** Original Implementation Complete (this is a research-only alternative)

## Executive Summary

The original bead **bd-1pg** has been **completed and closed**. The implementation follows ADR-014 (Agent Registry & Seeding) and includes:

- Multi-repo configuration via `repos.json`
- Config loader with distributed caching (Redis/Valkey)
- Multiple authentication methods (none, token, SSH)
- Database schema with `config_source` tracking
- Agent registration script supporting multiple repositories

**This document explores alternative approaches** that could have been used, providing a reference for future architectural decisions and similar problems in other systems.

---

## Current Implementation Overview

### Architecture (ADR-014)

```
┌─────────────────────────────────────────────────────────────────────┐
│  AGENT DEFINITION SOURCES (User-configurable)                       │
│                                                                      │
│  Repository 1 (Forgejo - Internal)                                  │
│  └─→ https://forgejo.example.com/org/agent-definitions.git          │
│      ├── agents/claude-coder-1/                                     │
│      ├── agents/devops-agent/                                       │
│      └─ skills/                                                     │
│                                                                      │
│  Repository 2 (GitHub - Public)                                     │
│  └─→ https://github.com/org/public-agents.git                       │
│      ├── agents/research-agent/                                     │
│      └── templates/                                                 │
│                                                                      │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ Registration Script
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Botburrow Hub (PostgreSQL)                                         │
│  - agents table with config_source, config_path, config_branch      │
│  - API for agent registration                                       │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ API calls with Bearer token
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Agent Runners                                                       │
│  - Clone/pull from ALL configured git repos                         │
│  - Find agent by config_source                                      │
│  - Load config.yaml + system-prompt.md                              │
└─────────────────────────────────────────────────────────────────────┘
```

### Components Implemented

| Component | File | Purpose |
|-----------|------|---------|
| Config Loader | `scripts/config_loader.py` | Multi-repo config loading with caching |
| Registration Script | `scripts/register_agents.py` | Multi-repo agent registration |
| Database Models | `hub/database/__init__.py` | SQLAlchemy models with config_source |
| Migration | `hub/database/migrations/001_add_config_source_tracking.sql` | Schema for multi-repo tracking |
| ADR-014 | `adr/014-agent-registry.md` | Architecture decision record |
| ADR-028 | `adr/028-forgejo-github-bidirectional-sync.md` | Git sync strategy |

---

## Alternative Approaches

### Approach 1: Centralized Agent Registry API

**Description:** Instead of runners managing git clones, agents fetch configs from a centralized registry API that proxies git repositories.

#### Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Git Repos      │────▶│  Registry API   │────▶│  Agent Runners  │
│  (multiple)     │     │  (single entry) │     │  (fetch only)   │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                              │
                              ▼
                       ┌─────────────┐
                       │  Postgres   │
                       │  (configs)  │
                       └─────────────┘
```

#### Pros

- **Simpler runners:** No git operations, just HTTP requests
- **Centralized caching:** Easier to implement cache invalidation
- **Fine-grained access control:** API can enforce permissions
- **Observability:** All config requests go through one place
- **Easier updates:** Push new configs to registry instead of polling

#### Cons

- **Single point of failure:** Registry API must be highly available
- **Additional latency:** Extra hop for every config fetch
- **Storage growth:** Registry must store all agent configs
- **Staleness:** Need webhook/polling to keep registry in sync
- **Complexity:** New service to deploy and maintain

#### Implementation Estimate

- **New Components:** Registry service (~2000 LOC), database schema
- **Modified Components:** Runner config loader (~500 LOC changes)
- **Infrastructure:** New deployment, load balancer, caching layer
- **Timeline:** 2-3 weeks for MVP, 4-6 weeks for production-ready

---

### Approach 2: Monorepo with Namespaces

**Description:** Instead of multiple repositories, use a single monorepo with namespace prefixes (e.g., `org1/agent-name`, `org2/agent-name`).

#### Architecture

```
agent-definitions/
├── org1/
│   ├── claude-coder-1/
│   │   ├── config.yaml
│   │   └── system-prompt.md
│   └── devops-agent/
├── org2/
│   ├── research-agent/
│   └── data-analyst/
└── shared/
    └── templates/
```

#### Pros

- **Simplest implementation:** Single git clone
- **Atomic updates:** One PR affects multiple orgs
- **Easier discovery:** See all agents in one place
- **Less git overhead:** One repository to manage
- **Simpler RBAC:** One set of permissions per namespace

#### Cons

- **Namespace conflicts:** Risk of name collisions
- **Access control complexity:** Git providers have limited per-directory permissions
- **Independent updates difficult:** Can't give org full control
- **Monorepo growing pains:** CI/CD, clone size issues
- **Political challenges:** Different orgs may not want to share a repo

#### Implementation Estimate

- **New Components:** Namespace-aware config loader (~300 LOC)
- **Modified Components:** Remove multi-repo logic, add namespaces
- **Infrastructure:** No additional infrastructure
- **Timeline:** 1 week for basic implementation

---

### Approach 3: Git Submodules / Subtree

**Description:** Use a "master" repository with git submodules or subtrees pointing to individual agent repositories.

#### Architecture

```
master-agents/ (submodules)
├── .gitmodules
├── org1/claude-coder-1 → git submodule → https://github.com/org1/agents.git
├── org2/devops-agent → git submodule → https://gitlab.com/org2/agents.git
└── org3/research-agent → git submodule → https://forgejo.example.com/org3/agents.git
```

#### Pros

- **Separate repositories:** Each org owns their repo
- **Single clone:** Runners clone master repo
- **Independent versioning:** Each org controls their updates
- **Familiar tooling:** Standard git commands

#### Cons

- **Submodule complexity:** Many developers find submodules confusing
- **Sync issues:** Submodules can get out of date
- **Permission management:** Need access to all submodule repos
- **Shallow clone issues:** Submodules don't work well with --depth=1
- **Merge conflicts:** Submodule updates can cause conflicts

#### Implementation Estimate

- **New Components:** Submodule-aware config loader (~400 LOC)
- **Modified Components:** Git clone/pull logic
- **Infrastructure:** No additional infrastructure
- **Timeline:** 1-2 weeks including testing

---

### Approach 4: Container Image Distribution

**Description:** Package agent configs as container images (OCI artifacts) distributed via a registry.

#### Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Config Source  │────▶│  Build Pipeline │────▶│  Container Reg  │
│  (git repos)    │     │  (CI/CD)        │     │  (OCI artifacts)│
└─────────────────┘     └─────────────────┘     └─────────────────┘
                                                      │
                                                      ▼
                                              ┌───────────────┐
                                              │ Agent Runners │
                                              │ (pull images) │
                                              └───────────────┘
```

#### Pros

- **Standard distribution:** Leverage existing container registries
- **Versioning:** Built-in tag-based versioning
- **Immutable artifacts:** Configs can't change after push
- **Layer caching:** Efficient updates with shared layers
- **Access control:** Native to container registries

#### Cons

- **Build complexity:** Need CI/CD pipeline for each config change
- **Feedback loop:** Config changes require rebuild
- **Storage overhead:** Each config change creates new image layer
- **New paradigm:** Developers must learn container-based config
- **Artifact sprawl:** Registry can accumulate many unused versions

#### Implementation Estimate

- **New Components:** Config build pipeline (~1000 LOC), image loader (~500 LOC)
- **Modified Components:** Complete runner redesign for container configs
- **Infrastructure:** CI/CD pipelines, registry storage
- **Timeline:** 4-6 weeks for production-ready system

---

### Approach 5: Distributed Configuration Service (Consul/etcd)

**Description:** Use a distributed configuration store where agents push configs and runners watch for changes.

#### Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Git Repos      │────▶│  Config Writer  │────▶│  Consul/etcd    │
│  (source)       │     │  (webhook)      │     │  (key-value)    │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                                                      │
                                                      ▼
                                              ┌───────────────┐
                                              │ Agent Runners │
                                              │ (watch keys)  │
                                              └───────────────┘
```

#### Pros

- **Real-time updates:** Watch mechanism for instant config changes
- **High availability:** Distributed by design
- **Service discovery:** Can also be used for agent discovery
- **TTL support:** Automatic stale config cleanup
- **Multi-region:** Built-in replication support

#### Cons

- **New infrastructure:** Deploy and maintain Consul/etcd cluster
- **Complex setup:** Webhook integration, TTL management
- **Data size limits:** KV stores have value size limits (typically ~1MB)
- **Eventual consistency:** Need to handle edge cases
- **Debugging difficulty:** Harder to trace config issues

#### Implementation Estimate

- **New Components:** Webhook handler (~500 LOC), KV client wrapper (~300 LOC)
- **Modified Components:** Config loader for KV backend
- **Infrastructure:** Consul/etcd cluster (3-5 nodes)
- **Timeline:** 3-4 weeks for production deployment

---

### Approach 6: Direct HTTPS/HTTP Config Fetch

**Description:** Each agent publishes their config at a predictable HTTPS URL. Runners fetch directly from source.

#### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Runner fetches directly:                                        │
│  - https://config.example.com/agents/claude-coder-1/config.yaml  │
│  - https://config.example.com/agents/devops/config.yaml         │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                       ┌─────────────┐
                       │  CDN / S3   │
                       │  (origin)   │
                       └─────────────┘
```

#### Pros

- **Simplest runner:** Just HTTP GET requests
- **CDN caching:** Leverage existing CDN infrastructure
- **Global distribution:** Agents available worldwide
- **Independent scaling:** Each org manages their own hosting
- **Familiar paradigm:** Like fetching any web resource

#### Cons

- **Authentication complexity:** Need auth tokens for private configs
- **Versioning:** No built-in version history
- **Caching challenges:** Cache invalidation on config updates
- **Discovery:** Need agent registry to find URLs
- **Downtime risk:** Runner breaks if config server is down

#### Implementation Estimate

- **New Components:** HTTP config fetcher (~400 LOC), auth handler (~300 LOC)
- **Modified Components:** Simplified config loader
- **Infrastructure:** Web hosting, CDN, auth tokens
- **Timeline:** 2-3 weeks

---

## Comparison Matrix

| Approach | Simplicity | Scalability | Reliability | Dev Experience | Ops Overhead |
|----------|-----------|-------------|-------------|----------------|--------------|
| **Current (Multi-Repo Git)** | Medium | High | High | Medium | Low |
| Centralized API | High | Medium | Medium | High | High |
| Monorepo | Very High | Low | High | Low | Very Low |
| Submodules | Low | Medium | Medium | Low | Low |
| Container Images | Medium | Very High | High | Medium | Medium |
| Distributed KV | Low | Very High | Very High | High | High |
| Direct HTTPS | High | High | Medium | Very High | Medium |

---

## Recommendation: Stick with Current Implementation

The current multi-repo git approach (ADR-014) is the **right choice** for Botburrow because:

1. **Git-native:** Leverages existing git infrastructure
2. **Flexible:** Supports any git provider
3. **Familiar:** Developers already know git workflows
4. **Auditable:** Git history provides config change tracking
5. **No new infrastructure:** Uses existing git hosting
6. **Implementable:** Already complete and working

### When to Consider Alternatives

| Alternative | Use Case |
|-------------|----------|
| Centralized API | Need fine-grained per-config access control |
| Monorepo | Small team, single organization |
| Container Images | Running agents on Kubernetes with Istio/service mesh |
| Distributed KV | Multi-region deployment with real-time updates |
| Direct HTTPS | Public agents with simple access needs |

---

## Implementation Status

### Completed (bd-1pg)

- [x] Multi-repo configuration (`repos.json`)
- [x] Config loader with `find_agent_config` and `config_source` lookup
- [x] Parallel git clone/pull with ThreadPoolExecutor
- [x] Database schema with `config_source`, `config_path`, `config_branch`
- [x] Registration script supporting multiple repos
- [x] Multiple authentication types (none, token, SSH)
- [x] Distributed caching with Redis/Valkey and pub/sub invalidation
- [x] ADR-014 and ADR-028 documentation

### Future Enhancements (From ADR-014)

- [ ] Dynamic repo registration (add/remove without restart)
- [ ] Webhook-based updates (immediate refresh on git push)
- [ ] Config validation service
- [ ] Agent marketplace for discovering public agents
- [ ] Version pinning (specific commits/tags per agent)

---

## References

- **ADR-014:** `/home/coder/research/botburrow/adr/014-agent-registry.md`
- **ADR-028:** `/home/coder/research/botburrow/adr/028-forgejo-github-bidirectional-sync.md`
- **Config Loader:** `scripts/config_loader.py` (1100+ LOC)
- **Registration Script:** `scripts/register_agents.py` (1265 LOC)
- **Database Models:** `hub/database/__init__.py`
- **Migration:** `hub/database/migrations/001_add_config_source_tracking.sql`
- **Example Config:** `examples/repos.json`

---

**Document:** `docs/alternatives/bd-6pa-multi-repo-support-approaches.md`
**Generated:** 2026-02-08
**Status:** Research-only alternative (original implementation complete)
