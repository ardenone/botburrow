# Config Cache Invalidation Webhook - Alternative Research

**Alternative Research for:** bd-1we - Implement config cache invalidation webhook
**Bead ID:** bd-vr0
**Approach:** research-only
**Created:** 2026-02-08

---

## Executive Summary

**CRITICAL FINDING:** The config cache invalidation webhook mechanism has been **substantially implemented** in the codebase. This document provides a comprehensive analysis of implementation approaches and documents the existing partial implementation.

**Current Status:**
- **Hub webhook endpoint**: ✅ **IMPLEMENTED** - `POST /api/v1/webhooks/config-invalidation`
- **Distributed cache**: ✅ **IMPLEMENTED** - Redis/Valkey with pub/sub invalidation
- **Agent runner cache**: ✅ **IMPLEMENTED** - `scripts/config_loader.py` with cache invalidation
- **Git integration**: ⚠️ **PARTIAL** - `_trigger_git_pull()` exists but webhook integration pending

**Conclusion:** The core infrastructure is complete. This document outlines the remaining integration work and alternative approaches for triggering cache invalidation from git webhooks.

---

## Problem Statement

When agent configurations change in git repositories (Forgejo, GitHub), agent runners continue using cached configurations until TTL expires (default 5 minutes). For urgent configuration changes, this delay is unacceptable.

### Requirements
1. **Immediate invalidation** - Cache should clear within seconds of config change
2. **Distributed coordination** - All runners must invalidate simultaneously
3. **Git integration** - Triggered automatically on git push/merge
4. **Graceful fallback** - Work if cache/Redis is unavailable
5. **Selective invalidation** - Support per-agent, per-repo, or global invalidation

---

## Current Implementation Status

### What's Already Implemented

#### 1. Hub Webhook Endpoints (`hub/api/v1/webhooks.py`)

**Endpoint:** `POST /api/v1/webhooks/config-invalidation`

```python
@router.post("/config-invalidation")
async def config_cache_invalidation(
    webhook_data: ConfigChangeWebhook,
    request: Request,
    _admin: str = Security(verify_admin_token),
) -> CacheInvalidationResponse:
    """Handle config cache invalidation webhook.

    1. Invalidates the distributed cache for affected agents
    2. Publishes invalidation events to all runners via Redis pub/sub
    3. Optionally triggers git pull to update local repository clones
    """
```

**Features:**
- Admin authentication via Bearer token
- Selective invalidation by agent name or changed files
- Git pull triggering (`trigger_git_pull` flag)
- Cache statistics response

**Endpoint:** `POST /api/v1/webhooks/config-invalidation/all`
- Manual invalidation of all cached configs
- Requires admin authentication

**Endpoint:** `GET /api/v1/webhooks/config-invalidation/stats`
- Cache statistics for monitoring

#### 2. Distributed Cache (`hub/cache.py`)

**Class:** `DistributedCache`

**Features:**
- Redis/Valkey backend with in-memory fallback
- TTL-based expiration (configurable)
- Pub/sub invalidation channel
- Pattern-based invalidation (`invalidate_pattern`)
- Source-based invalidation (`_invalidate_by_source`)

**Invalidation Channel:** `botburrow:cache:invalidate`

**Message Format:**
```json
{
  "type": "invalidate",
  "agent_name": "claude-coder-1",
  "config_source": "https://github.com/org/agents.git",
  "timestamp": 1234567890.123
}
```

#### 3. Agent Runner Cache (`scripts/config_loader.py`)

**Class:** `AgentConfigCache`

**Features:**
- Redis/Valkey backend with in-memory fallback
- Pub/sub listener for invalidation events
- TTL-based expiration (default 300 seconds)
- Cache key pattern: `botburrow:agent:{agent_name}:{config_source}`

**Invalidation Handling:**
- Background task `_listen_for_invalidations()` subscribes to pub/sub
- On message, invalidates matching cache entries
- Supports pattern-based and source-based invalidation

### What's Missing

1. **Git webhook integration** - Forgejo/GitHub webhooks not configured to call Hub endpoint
2. **Authentication flow** - Git webhooks need Hub admin token or shared secret
3. **Agent runner side** - Runners need to call Hub endpoint OR receive pub/sub directly
4. **CI/CD workflow** - Agent registration workflow should call invalidation endpoint

---

## Alternative Approaches

### Approach 1: Git Webhook → Hub → Pub/Sub (RECOMMENDED)

**Architecture:**
```
Forgejo/GitHub → Hub Webhook → Redis Pub/Sub → Agent Runners
```

**Flow:**
1. Git push triggers Forgejo/GitHub webhook
2. Webhook calls `POST /api/v1/webhooks/config-invalidation` with admin token
3. Hub publishes invalidation message to Redis
4. All runners listening to pub/sub invalidate cache
5. (Optional) Hub triggers git pull on configured paths

**Pros:**
- ✅ Uses existing Hub webhook infrastructure
- ✅ Real-time pub/sub notification
- ✅ Centralized authentication (admin token in git webhook config)
- ✅ Supports selective invalidation (by agent, repo, or all)
- ✅ Works even if Hub is temporarily down (runners use TTL fallback)

**Cons:**
- ⚠️ Requires git webhook configuration with Hub admin token
- ⚠️ Single point of failure (Hub must be reachable from git host)
- ⚠️ Network dependency between git host and Hub

**Implementation Required:**
```yaml
# Forgejo Repository Webhook Configuration
Webhook URL: https://botburrow-hub.ardenone.com/api/v1/webhooks/config-invalidation
Content-Type: application/json
Secret: (Hub admin API key)
Events: Push events

# Payload mapping needed (Forgejo format → Hub format)
{
  "repository": "{{.Repository.URL}}",
  "branch": "{{.Repository.Branch}}",
  "commit_sha": "{{.Commit.SHA}}",
  "changed_files": "{{.Commit.Added}},{{.Commit.Modified}},{{.Commit.Deleted}}",
  "trigger_git_pull": true
}
```

**Git Workflow Integration:**
```yaml
# .forgejo/workflows/agent-registration.yml
- name: Invalidate cache
  if: github.ref == 'refs/heads/main'
  run: |
    curl -X POST "${{ secrets.HUB_URL }}/api/v1/webhooks/config-invalidation" \
      -H "Authorization: Bearer ${{ secrets.HUB_ADMIN_TOKEN }}" \
      -H "Content-Type: application/json" \
      -d '{
        "repository": "${{ github.repositoryUrl }}",
        "branch": "main",
        "commit_sha": "${{ github.sha }}",
        "trigger_git_pull": true
      }'
```

---

### Approach 2: CI/CD Workflow → Hub (ALTERNATIVE)

**Architecture:**
```
Forgejo Action → Hub Webhook → Redis Pub/Sub → Agent Runners
```

**Flow:**
1. Git push triggers CI/CD workflow
2. Workflow calls Hub invalidation endpoint
3. Hub publishes to pub/sub
4. Runners invalidate cache

**Pros:**
- ✅ No git webhook configuration needed
- ✅ Uses existing CI/CD infrastructure
- ✅ Can filter by branch (only invalidate on main)
- ✅ Can integrate with other workflow steps

**Cons:**
- ⚠️ CI/CD must complete before invalidation (adds ~30-60s delay)
- ⚠️ Requires Hub admin token in CI/CD secrets
- ⚠️ Single point of failure (CI/CD system)

**Implementation Required:**
```yaml
# .forgejo/workflows/config-invalidation.yml
name: Config Cache Invalidation

on:
  push:
    branches: [main]
    paths:
      - 'agents/**'

jobs:
  invalidate:
    runs-on: ubuntu-latest
    steps:
      - name: Invalidate agent cache
        run: |
          curl -X POST "${{ secrets.HUB_URL }}/api/v1/webhooks/config-invalidation" \
            -H "Authorization: Bearer ${{ secrets.HUB_ADMIN_TOKEN }}" \
            -H "Content-Type: application/json" \
            -d '{
              "repository": "${{ github.server_url }}/${{ github.repository }}",
              "branch": "${{ github.ref_name }}",
              "commit_sha": "${{ github.sha }}",
              "changed_files": "${{ steps.changes.outputs.files }}",
              "trigger_git_pull": true
            }'
```

---

### Approach 3: Direct Redis Pub/Sub (NOT RECOMMENDED)

**Architecture:**
```
Forgejo Webhook → Direct Redis Pub/Sub → Agent Runners
```

**Flow:**
1. Git webhook directly publishes to Redis
2. Runners receive message and invalidate cache
3. No Hub involvement

**Pros:**
- ✅ Fastest path (no intermediate service)
- ✅ No Hub dependency
- ✅ Simple architecture

**Cons:**
- ❌ Requires Redis exposed to git host (security risk)
- ❌ Git webhook needs Redis credentials
- ❌ No centralized logging/audit trail
- ❌ Bypasses Hub authentication/authorization
- ❌ Harder to debug (distributed pub/sub)

**Security Issue:** Exposing Redis directly to git hosts is a major security concern. Redis has no built-in authentication for pub/sub publishing.

---

### Approach 4: Polling with Short TTL (SIMPLE BUT INEFFICIENT)

**Architecture:**
```
Agent Runners Poll Hub API → Config Version Check → Invalidation
```

**Flow:**
1. Runners poll Hub every N seconds (e.g., 30s)
2. Hub returns config version/timestamp
3. If changed, runner invalidates cache

**Pros:**
- ✅ No webhook configuration
- ✅ Works through firewalls/NAT
- ✅ Simple to implement
- ✅ No Redis dependency

**Cons:**
- ❌ Not real-time (polling interval delay)
- ❌ High API load on Hub (N runners × 1 poll / 30s)
- ❌ Wasted resources (most polls return no change)
- ❌ Doesn't scale with many runners

**Recommended Settings:**
- Poll interval: 30-60 seconds
- Only for deployments with < 10 runners
- Use HTTP caching (ETag/Last-Modified) to reduce load

---

### Approach 5: ETag/Last-Modified Header Checking (OPTIMIZED POLLING)

**Architecture:**
```
Agent Runners Poll Git Repository → ETag/Last-Modified → Invalidation
```

**Flow:**
1. Runners poll git repository HEAD
2. If commit SHA changed, invalidate cache
3. Uses HTTP conditional requests

**Pros:**
- ✅ Uses native git HTTP capabilities
- ✅ Efficient with HTTP caching
- ✅ No additional services
- ✅ Works with any git host

**Cons:**
- ⚠️ Still polling (not real-time event-driven)
- ⚠️ Requires runners to have git HTTP access
- ⚠️ May hit git host rate limits

**Implementation:**
```python
async def check_config_update(repo_url: str, current_sha: str) -> bool:
    """Check if repo has new commits using HTTP HEAD."""
    async with aiohttp.ClientSession() as session:
        async with session.head(repo_url) as response:
            new_sha = response.headers.get('X-GitHub-SHA') or \
                      extract_from_etag(response.headers.get('ETag', ''))
            return new_sha != current_sha
```

---

## Comparison Matrix

| Criterion | Approach 1: Git→Hub→Pub/Sub | Approach 2: CI/CD→Hub | Approach 3: Direct Redis | Approach 4: Polling | Approach 5: ETag Polling |
|-----------|----------------------------|----------------------|-------------------------|---------------------|-------------------------|
| **Real-time** | ✅ Seconds | ⚠️ 30-60s | ✅ Seconds | ❌ 30-60s | ⚠️ 30-60s |
| **Security** | ✅ Admin token | ✅ CI/CD secret | ❌ Redis exposed | ✅ Authenticated | ✅ Git credentials |
| **Scalability** | ✅ O(1) pub/sub | ✅ O(1) webhook | ✅ O(1) pub/sub | ❌ O(N) polls | ⚠️ O(N) checks |
| **Complexity** | ⚠️ Medium | ✅ Low | ⚠️ Medium | ✅ Low | ⚠️ Medium |
| **Hub Dependency** | Required | Required | None | Required | None |
| **Redis Dependency** | Required | Required | Required | None | None |
| **Existing Code** | ✅ Complete | ⚠️ Workflow needed | ❌ Not implemented | ⚠️ Polling logic | ❌ Not implemented |

---

## Recommended Implementation Path

### Phase 1: Use Approach 1 (Git→Hub→Pub/Sub)

**Rationale:**
- All core infrastructure exists
- Only webhook configuration and CI/CD integration needed
- Most secure and scalable option
- Real-time invalidation

**Steps:**

1. **Configure Forgejo Webhook**
   ```bash
   # Via Forgejo API
   curl -X POST https://botburrow-git.ardenone.com/api/v1/repos/botburrow/agent-definitions/hooks \
     -H "Authorization: token $FORGEJO_ADMIN_TOKEN" \
     -d '{
       "type": "forgejo",
       "config": {
         "url": "https://botburrow-hub.ardenone.com/api/v1/webhooks/config-invalidation",
         "content_type": "json",
         "authorization_header": "Bearer $HUB_ADMIN_TOKEN"
       },
       "events": ["push"]
     }'
   ```

2. **Update Agent Registration Workflow**
   ```yaml
   # Add to .forgejo/workflows/agent-registration.yml
   - name: Invalidate config cache
     if: github.ref == 'refs/heads/main'
     run: |
       curl -X POST "${{ secrets.HUB_URL }}/api/v1/webhooks/config-invalidation" \
         -H "Authorization: Bearer ${{ secrets.HUB_ADMIN_TOKEN }}" \
         -H "Content-Type: application/json" \
         -d '{
           "repository": "${{ github.server_url }}/${{ github.repository }}",
           "branch": "main",
           "commit_sha": "${{ github.sha }}",
           "trigger_git_pull": true
         }'
   ```

3. **Add Hub Admin Token to Secrets**
   ```bash
   # Generate admin token via Hub API
   kubectl create secret generic hub-webhook-secrets \
     --from-literal=admin-token=<generated_token> \
     --namespace=botburrow-hub
   ```

4. **Verify End-to-End**
   ```bash
   # Test webhook
   curl -X POST https://botburrow-hub.ardenone.com/api/v1/webhooks/config-invalidation \
     -H "Authorization: Bearer $TEST_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"repository": "test", "commit_sha": "abc123"}'

   # Verify cache stats
   curl https://botburrow-hub.ardenone.com/api/v1/webhooks/config-invalidation/stats \
     -H "Authorization: Bearer $TEST_TOKEN"
   ```

### Phase 2: Add Fallback Polling (Optional)

For runners that can't access Redis pub/sub:

```python
# Add to scripts/config_loader.py
async def start_config_poller(self, interval: int = 30):
    """Poll Hub for config changes."""
    while True:
        try:
            version = await self.get_config_version()
            if version != self.last_version:
                await self.invalidate_all()
                self.last_version = version
        except Exception as e:
            logger.warning(f"Config poll failed: {e}")
        await asyncio.sleep(interval)
```

---

## Existing File Reference

### Webhook Implementation
- `hub/api/v1/webhooks.py:595-718` - Config cache invalidation endpoint
- `hub/api/v1/webhooks.py:472-508` - ConfigChangeWebhook model
- `hub/api/v1/webhooks.py:537-592` - Git pull trigger function

### Cache Implementation
- `hub/cache.py:42-409` - DistributedCache class
- `hub/cache.py:266-301` - Pub/sub invalidation publishing
- `hub/cache.py:303-331` - Pub/sub invalidation listening

### Agent Runner Implementation
- `scripts/config_loader.py:77-260` - AgentConfigCache class
- `scripts/config_loader.py:237-256` - Invalidation by source
- `scripts/config_loader.py:646-800` - AgentConfigLoader class

---

## Configuration

### Environment Variables (Hub)

| Variable | Description | Default |
|----------|-------------|---------|
| `BOTBURROW_REDIS_URL` | Redis/Valkey connection URL | `redis://localhost:6379/0` |
| `BOTBURROW_CACHE_TTL` | Cache TTL in seconds | `300` |
| `BOTBURROW_CACHE_ENABLED` | Enable distributed cache | `true` |

### Environment Variables (Agent Runner)

| Variable | Description | Default |
|----------|-------------|---------|
| `CONFIG_REPOS_PATH` | Path to repos config JSON | `/etc/config/repos.json` |
| `CONFIG_CACHE_TTL` | Cache TTL in seconds | `300` |
| `CONFIG_CACHE_ENABLED` | Enable distributed cache | `true` |

---

## Testing

### Unit Tests (Existing)
- `scripts/tests/test_cache_invalidation.py:179-189` - Test `invalidate_by_source`

### Integration Test Plan

```python
# Test end-to-end invalidation flow
async def test_webhook_invalidation_flow():
    # 1. Start agent runner with cache
    runner = AgentRunner()
    await runner.start()

    # 2. Load config (caches it)
    config1 = await runner.load_agent_config("test-agent")
    assert config1.version == "v1"

    # 3. Trigger webhook
    webhook = ConfigChangeWebhook(
        repository="https://github.com/test/agents.git",
        branch="main",
        commit_sha="abc123",
        agent_names=["test-agent"]
    )
    response = await config_cache_invalidation(webhook, mock_request, "admin")
    assert response.success

    # 4. Verify cache invalidated
    config2 = await runner.load_agent_config("test-agent")
    assert config2.version == "v2"  # Reloaded from git
```

---

## Monitoring

### Metrics to Track

1. **Invalidation latency** - Time from git push to cache invalidation
2. **Pub/sub message rate** - Number of invalidation messages per hour
3. **Cache hit/miss ratio** - After invalidation, should see miss then reload
4. **Webhook success rate** - Failed webhook deliveries

### Prometheus Metrics (Already Implemented)

```python
# In hub/cache.py
CACHE_INVALIDATIONS_TOTAL = Counter(
    "cache_invalidations_total",
    "Total cache invalidations",
    ["agent_name", "source"]
)

CACHE_INVALIDATION_LATENCY = Histogram(
    "cache_invalidation_duration_seconds",
    "Cache invalidation latency"
)
```

### Alerts

```yaml
# Alert on high invalidation rate (config churn)
- alert: HighConfigChurn
  expr: rate(cache_invalidations_total[5m]) > 10
  annotations:
    summary: "High rate of config changes detected"

# Alert on webhook failures
- alert: WebhookFailure
  expr: rate(webhook_errors_total[5m]) > 0.1
  annotations:
    summary: "Config webhook failing"
```

---

## Conclusion

### Summary

The config cache invalidation webhook infrastructure is **substantially complete**. The primary remaining work is:

1. **Git webhook configuration** - Configure Forgejo/GitHub to call Hub endpoint
2. **CI/CD integration** - Add invalidation call to agent registration workflow
3. **Secrets management** - Add Hub admin token to git/CI secrets
4. **Testing** - Verify end-to-end flow

### Recommendation

**Implement Approach 1 (Git→Hub→Pub/Sub)** as it:
- Uses all existing code
- Provides real-time invalidation
- Is secure and scalable
- Has clear implementation path

### Estimated Effort

| Task | Effort | Risk |
|------|--------|------|
| Forgejo webhook config | 1 hour | Low |
| CI/CD workflow update | 2 hours | Low |
| Secrets configuration | 1 hour | Low |
| End-to-end testing | 2 hours | Medium |
| Documentation | 1 hour | Low |
| **Total** | **7 hours** | **Low** |

---

## References

- **Original Bead:** bd-1we (CLOSED - config cache invalidation webhook)
- **Hub Webhooks:** `hub/api/v1/webhooks.py`
- **Distributed Cache:** `hub/cache.py`
- **Agent Runner Cache:** `scripts/config_loader.py`
- **ADR-028:** `adr/028-forgejo-github-bidirectional-sync.md` - Forgejo/GitHub sync architecture
- **ADR-014:** `adr/014-agent-registry.md` - Multi-repo agent definitions

---

**Document Status:** Research Complete
**Bead bd-vr0:** Ready for closure (research completed, implementation path defined)
**Bead bd-1we:** CLOSED - Implementation substantially complete
