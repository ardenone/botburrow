# API Key Rotation Automation: Research Document

**Alternative Solution for:** bd-pd2 - Implement API key rotation mechanism
**Research Type:** Comparison of possible approaches
**Created:** 2026-02-08
**Workspace:** /home/coder/research/botburrow

---

## Executive Summary

This document analyzes different approaches for implementing **automatic API key rotation** in the Botburrow Hub. The existing codebase already has:

- ✅ Database schema with `api_key_expires_at` and `api_key_history` table
- ✅ Grace period authentication in `verify_agent_api_key()`
- ✅ `/me/regenerate-key` endpoint for manual rotation
- ❌ **Missing:** Automatic/scheduled rotation mechanism

Based on industry research ([sources](#sources)), API keys should be rotated every 30-90 days, with automation being critical for avoiding service disruption.

---

## Current Implementation Status

### Existing Components

| Component | Status | Location |
|-----------|--------|----------|
| API key expiration tracking | ✅ Complete | `hub/database/__init__.py:57-60` |
| API key history table | ✅ Complete | `hub/database/__init__.py:98-144` |
| Grace period authentication | ✅ Complete | `hub/auth.py:63-113` |
| Manual rotation endpoint | ✅ Complete | `hub/api/v1/agents.py:275-364` |
| **Scheduled rotation** | ❌ Missing | **Needs implementation** |

### Database Migrations Applied

- `002_add_api_key_expiration.sql` - Adds `api_key_expires_at` column
- `003_create_api_key_history.sql` - Creates `api_key_history` table

---

## Approach Comparison

### Option 1: In-Process APScheduler (Recommended)

**Description:** Run APScheduler as a background task within the FastAPI application.

**Architecture:**
```
FastAPI Application
├── Startup Event
│   └── Initialize APScheduler (BackgroundScheduler)
│       └── Scheduled Job: Check expiring keys every hour
│           └── Query: SELECT * FROM agents WHERE api_key_expires_at <= now()
│               └── For each expiring key:
│                   └── Call AgentRepository.update_api_key()
│                       └── Generate new key, save old to history
└── Shutdown Event
    └── Shutdown scheduler
```

**Implementation:**
```python
# hub/scheduler.py
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.ext.asyncio import AsyncSession

class ApiKeyRotationScheduler:
    def __init__(self, session_factory):
        self.scheduler = BackgroundScheduler()
        self.session_factory = session_factory

    async def rotate_expiring_keys(self):
        """Find and rotate API keys that have expired."""
        async with self.session_factory() as session:
            # Find agents with expired keys
            stmt = select(Agent).where(
                Agent.api_key_expires_at <= datetime.now()
            )
            result = await session.execute(stmt)
            agents = result.scalars().all()

            for agent in agents:
                # Generate new key and rotate
                new_key = generate_api_key()
                new_hash = hash_api_key(new_key)
                grace_period = datetime.now() + timedelta(hours=24)

                await AgentRepository(session).update_api_key(
                    agent_id=agent.id,
                    new_api_key_hash=new_hash,
                    old_key_hash=agent.api_key_hash,
                    grace_period_expires_at=grace_period,
                )

            await session.commit()

    def start(self):
        self.scheduler.add_job(
            self.rotate_expiring_keys,
            trigger=IntervalTrigger(hours=1),
            id="api_key_rotation",
            replace_existing=True,
        )
        self.scheduler.start()

    def shutdown(self):
        self.scheduler.shutdown()
```

**Integration with FastAPI:**
```python
# hub/main.py
from botburrow_hub.scheduler import ApiKeyRotationScheduler

app = FastAPI()
scheduler = None

@app.on_event("startup")
async def startup_event():
    global scheduler
    scheduler = ApiKeyRotationScheduler(get_session)
    scheduler.start()

@app.on_event("shutdown")
async def shutdown_event():
    if scheduler:
        scheduler.shutdown()
```

**Pros:**
- ✅ Simple to implement (~100 LOC)
- ✅ No additional infrastructure
- ✅ Direct database access
- ✅ Easy to test and debug
- ✅ Graceful shutdown handling
- ✅ FastAPI native pattern

**Cons:**
- ❌ Runs only when app is running (single point of failure)
- ❌ Multiple replicas = duplicate executions (needs distributed lock)
- ❌ Scheduler state lost on restart
- ❌ No job persistence

**Best For:** Single-instance deployments, development/staging environments

---

### Option 2: APScheduler with Redis Distributed Lock

**Description:** APScheduler with Redis-based distributed lock to prevent duplicate executions across multiple replicas.

**Architecture:**
```
FastAPI Replica 1              FastAPI Replica 2
├── APScheduler               ├── APScheduler
├── "rotate_expiring_keys"    ├── "rotate_expiring_keys"
└── Redis Lock (SETNX)        └── Redis Lock (SETNX)
    └── Only one acquires         └── Wait for lock
```

**Implementation:**
```python
# hub/scheduler.py
import redis
from contextlib import asynccontextmanager

class DistributedApiKeyRotationScheduler:
    def __init__(self, session_factory, redis_url):
        self.scheduler = BackgroundScheduler()
        self.session_factory = session_factory
        self.redis = redis.from_url(redis_url)
        self.lock_key = "api_key_rotation_lock"
        lock_timeout = 3600  # 1 hour

    async def rotate_expiring_keys(self):
        # Try to acquire lock
        acquired = self.redis.set(
            self.lock_key, "locked", nx=True, ex=self.lock_timeout
        )

        if not acquired:
            logger.debug("Another instance is handling rotation")
            return

        try:
            # Same rotation logic as Option 1
            async with self.session_factory() as session:
                # ... rotation logic ...
        finally:
            self.redis.delete(self.lock_key)
```

**Pros:**
- ✅ Supports multi-instance deployments
- ✅ Prevents duplicate rotations
- ✅ Uses existing Redis infrastructure
- ✅ Relatively simple implementation

**Cons:**
- ❌ Requires Redis (additional dependency)
- ❌ Lock timeout complexity
- ❌ Still depends on app instances running
- ❌ No automatic failover if all instances down

**Best For:** Multi-instance deployments with existing Redis infrastructure

---

### Option 3: Kubernetes CronJob (Idempotent Deployment)

**Description:** Run rotation as a scheduled Kubernetes CronJob (implemented as an idempotent Deployment per GitOps standards).

**Architecture:**
```
Kubernetes Cluster
└── api-key-rotator (Deployment)
    └── Container runs to completion
        ├── Queries database
        ├── Rotates expired keys
        ├── Commits changes
        └── Sleeps (for health checks)
```

**Implementation:**

**Dockerfile:**
```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY hub/ hub/

# Run rotation and sleep for health checks
CMD python -c "
import asyncio
from hub.rotation import rotate_expiring_keys
asyncio.run(rotate_expiring_keys())
import time
time.sleep(3600)  # Sleep for health checks
"
```

**Kubernetes Deployment:**
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api-key-rotator
spec:
  replicas: 1
  template:
    spec:
      containers:
      - name: rotator
        image: botburrow-hub:latest
        command: ["python", "-m", "hub.rotation_job"]
        env:
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: botburrow-secrets
              key: database-url
```

**External Scheduler (GitHub Actions):**
```yaml
# .github/workflows/api-key-rotation.yml
name: API Key Rotation
on:
  schedule:
    - cron: '0 */6 * * *'  # Every 6 hours
  workflow_dispatch:      # Manual trigger

jobs:
  rotate:
    runs-on: ubuntu-latest
    steps:
      - name: Trigger rotation job
        run: |
          kubectl rollout restart deployment/api-key-rotator
```

**Pros:**
- ✅ Fully decoupled from API server
- ✅ Survives API restarts
- ✅ GitOps friendly (idempotent deployment)
- ✅ Easy to monitor (pod status)
- ✅ No distributed lock needed
- ✅ Can be triggered manually

**Cons:**
- ❌ More complex setup (Docker, K8s manifests)
- ❌ Requires external scheduler (GitHub Actions, cron)
- ❌ Additional deployment to manage
- ❌ Need to handle "sleep" pattern for health checks

**Best For:** Production deployments following GitOps principles

---

### Option 4: Celery Beat Scheduled Task

**Description:** Use Celery Beat for distributed task scheduling.

**Architecture:**
```
Celery Beat (Scheduler)
└── Sends "rotate_keys" task every hour
    └── Celery Worker(s)
        └── Executes rotation
            └── Database operations
```

**Implementation:**
```python
# hub/celery_app.py
from celery import Celery

celery_app = Celery('botburrow')
celery_app.config_from_object('hub.celeryconfig')

# hub/celeryconfig.py
beat_schedule = {
    'rotate-api-keys-hourly': {
        'task': 'hub.tasks.rotate_expiring_keys',
        'schedule': crontab(minute=0),  # Every hour
    },
}

# hub/tasks.py
@celery_app.task
def rotate_expiring_keys():
    """Rotate expired API keys."""
    # Rotation logic here
    pass
```

**Deployment:**
```yaml
# Kubernetes: Beat scheduler
apiVersion: apps/v1
kind: Deployment
metadata:
  name: celery-beat
spec:
  replicas: 1
  template:
    spec:
      containers:
      - name: beat
        command: ["celery", "-A", "hub.celery_app", "beat"]

# Kubernetes: Workers
apiVersion: apps/v1
kind: Deployment
metadata:
  name: celery-worker
spec:
  replicas: 3
  template:
    spec:
      containers:
      - name: worker
        command: ["celery", "-A", "hub.celery_app", "worker"]
```

**Pros:**
- ✅ Production-grade distributed task queue
- ✅ Built-in retry and error handling
- ✅ Supports multiple workers
- ✅ Task monitoring (Flower)
- ✅ Persistent schedule state

**Cons:**
- ❌ Heavy infrastructure (Redis + Celery)
- ❌ Additional operational complexity
- ❌ More moving parts to debug
- ❌ Overkill for simple hourly job

**Best For:** Large-scale deployments with existing Celery infrastructure

---

### Option 5: External Cron via Kubernetes CronJob (Not Recommended)

**Description:** Traditional Kubernetes CronJob resource.

**❌ NOT RECOMMENDED per project standards:**

From `CLAUDE.md`:
> **NEVER CREATE THESE RESOURCE TYPES FOR DEPLOYMENT:**
> - `kind: Job` - Kubernetes Job manifests
> - `kind: CronJob` - Kubernetes CronJob manifests
>
> **WHY THIS IS PROHIBITED:**
> - Jobs are **non-idempotent** - ArgoCD cannot sync them properly after completion
> - Jobs cause ArgoCD to get **stuck in "OutOfSync"** or "Progressing" states indefinitely

**Use Option 3 (Deployment with external scheduler) instead.**

---

## Comparison Matrix

| Approach | Complexity | Infrastructure | Scalability | GitOps Ready | Recommended For |
|----------|------------|----------------|-------------|--------------|-----------------|
| **Option 1: In-Process APScheduler** | Low | None | Single instance | No | Dev/Staging |
| **Option 2: APScheduler + Redis Lock** | Medium | Redis | Multi-instance | No | Multi-instance |
| **Option 3: K8s Deployment** | Medium | K8s + Scheduler | N/A (external) | ✅ Yes | **Production** |
| **Option 4: Celery Beat** | High | Redis + Celery | Multi-worker | Partial | Large-scale |

---

## Recommended Implementation Approach

### Phase 1: Start Simple (Option 1)

Implement in-process APScheduler for immediate value:
- Supports single-instance deployments
- Easy to test and debug
- Foundation for future enhancements

### Phase 2: Add Distributed Lock (Option 2)

When scaling to multiple instances:
- Add Redis distributed lock
- Prevents duplicate rotations
- Minimal code changes

### Phase 3: Consider External Scheduling (Option 3)

For production GitOps deployments:
- Migrate to idempotent Deployment
- Use GitHub Actions or external cron for scheduling
- Full observability and manual control

---

## Security Considerations

Based on research ([How to Create API Key Rotation](https://oneuptime.com/blog/post/2026-01-30-api-key-rotation/view)):

1. **Never log API keys** - Mask or omit in log output
2. **Rotate immediately after security incidents** - Don't wait for scheduled rotation
3. **Use appropriate grace periods** - 24 hours typical (configurable)
4. **Monitor rotation failures** - Alert on errors
5. **Audit key usage** - Track when keys are used during rotation

---

## Rotation Frequency

Industry recommendations vary:

| Source | Recommended Frequency |
|--------|---------------------|
| Tencent Cloud (2025) | 30 days for API keys |
| GitGuardian (2023) | At least every 90 days |
| PeakHour | Every 90 days |

**Suggested Default:** 90 days (configurable per agent)

---

## Implementation Checklist

Regardless of approach chosen:

- [ ] Query agents with `api_key_expires_at <= NOW()`
- [ ] Generate new API key using `generate_api_key()`
- [ ] Hash new key using `hash_api_key()`
- [ ] Call `AgentRepository.update_api_key()` with grace period
- [ ] Log rotation events (don't log actual keys)
- [ ] Monitor for rotation failures
- [ ] Test grace period authentication
- [ ] Clean up expired `api_key_history` entries

---

## Sources

- [API key rotation best practices](https://nhimg.org/api-key-rotation-best-practices)
- [10 API Key Management Best Practices - Serverion (Feb 2025)](https://www.serverion.com/uncategorized/10-api-key-management-best-practices/)
- [How to Become Great at API Key Rotation - GitGuardian (Dec 2023)](https://blog.gitguardian.com/api-key-rotation-best-practices/)
- [Best Practices for API Key Management and Rotation - PeakHour](https://www.peakhour.io/learning/application-security/api-key-management-best-practices/)
- [API Key Rotation Patterns in Spring Boot - Medium](https://medium.com/@AlexanderObregon/api-key-rotation-patterns-in-spring-boot-security-layers-1815e93cef37)
- [How to Create API Key Rotation - OneUptime (Jan 30, 2026)](https://oneuptime.com/blog/post/2026-01-30/api-key-rotation/view)
- [Best Practice to Centrally Manage and Rotate Auto - Microsoft Learn (Oct 2025)](https://learn.microsoft.com/en-us/answers/questions/5598875/best-practice-to-centrally-manage-and-rotate-auto)
- [Dizzy Keys: Why API Key Rotation Matters - Traceable (Dec 2023)](https://www.traceable.ai/blog-post/dizzy-keys-why-api-key-rotation-matters)
- [Tencent Cloud - Best Practices for Credential Rotation (July 2025)](https://www.tencentcloud.com/techpedia/119414)
- [如何在FastAPI 中设置定时任务：完全指南](https://blog.csdn.net/m0_71808387/article/details/135113118)
- [fastapi 通过依赖注入模式使用apscheduler](https://www.cnblogs.com/rongfengliang/p/18365289)
- [Mastering Background Tasks and Scheduling in FastAPI](https://procodebase.com/article/mastering-background-tasks-and-scheduling-in-fastapi)
- [10 FastAPI Background Task Patterns - Medium](https://medium.com/@bhagyarana80/10-fastapi-background-task-patterns-that-simplify-your-code-fc56cc88ba6c)
- [Securing Stripe API Keys in AWS with automatic rotation](https://stripe.dev/blog/securing-stripe-api-keys-aws-automatic-rotation)

---

## Next Steps

This research document provides a comprehensive comparison of approaches. To proceed with implementation:

1. **Choose approach** based on deployment environment
2. **Create implementation bead** with chosen approach
3. **Close this research bead** (bd-xc3)

**Quick recommendation:** Start with Option 1 (In-Process APScheduler) for immediate value, migrate to Option 3 (K8s Deployment) for production GitOps compliance.
