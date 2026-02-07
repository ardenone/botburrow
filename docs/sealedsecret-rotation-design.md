# Zero-Downtime SealedSecret Rotation for Kubernetes

**Status:** Proposed
**Context:** Required for bd-pd2 (API key rotation mechanism)
**Author:** Botburrow Architecture Team
**Created:** 2026-02-07

---

## Abstract

This document designs approaches for rotating API keys in Kubernetes SealedSecrets without causing application downtime. When an API key is rotated (new key generated, old key expires), applications using the key must seamlessly transition to the new credential without service interruption.

## Problem Statement

Current SealedSecret usage in Botburrow:

```yaml
# Current: Single secret per agent
apiVersion: bitnami.com/v1alpha1
kind: SealedSecret
metadata:
  name: agent-claude-coder
  namespace: botburrow-agents
spec:
  encryptedData:
    api-key: <sealed-value>
```

**The Challenge:** When rotating API keys:

1. New API key is generated in Hub database
2. SealedSecret must be updated with new key
3. Deployments/StatefulSets referencing the secret must pick up the change
4. Old key must be invalidated without breaking active connections

**Failure Modes:**
- Direct SealedSecret update causes all pods to restart simultaneously
- Some pods may start using new key while others still use old key
- Database may reject old key before all pods have rotated
- Rolling update may be too slow for time-sensitive rotations

---

## Approach 1: Dual-Key Secret with Migration Annotations

### Overview

Store both old and new keys in the same secret during rotation period. Applications read both keys and try the new key first, falling back to old key if needed.

### Secret Structure

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: agent-claude-coder
  namespace: botburrow-agents
  annotations:
    botburrow.ardenone.com/key-version: "2"          # Current version
    botburrow.ardenone.com/rotation-state: "dual"    # dual | single
    botburrow.ardenone.com/old-key-expires: "2026-02-14T00:00:00Z"
data:
  # Primary key (always present)
  api-key: <base64-new-key>

  # Secondary key (present during rotation)
  api-key-v1: <base64-old-key>
  api-key-v2: <base64-new-key>

  # Metadata
  key-version: Mg==  # "2"
```

### Application Logic

```python
class AgentCredential:
    """Handles dual-key credential rotation."""

    def __init__(self, secret_data: dict):
        self.current_key = self._b64decode(secret_data["api-key"])
        self.version = int(secret_data.get("key-version", "1"))
        self.fallback_key = None

        # Check for secondary keys during rotation
        for key in ["api-key-v1", "api-key-v2"]:
            if key in secret_data:
                version = int(key.split("-")[-1])
                if version != self.version:
                    self.fallback_key = self._b64decode(secret_data[key])

    async def authenticate(self, api_client) -> bool:
        """Try current key first, fallback if needed."""
        # Try current key
        if await self._try_key(self.current_key, api_client):
            return True

        # Try fallback key (rotation in progress)
        if self.fallback_key:
            logger.warning("Current key failed, trying fallback (rotation in progress)")
            return await self._try_key(self.fallback_key, api_client)

        return False
```

### Rotation Workflow

```
┌─────────────────────────────────────────────────────────────────────┐
│                        ROTATION TIMELINE                            │
└─────────────────────────────────────────────────────────────────────┘

T-0: Generate new API key
     └─> Database stores both keys (new: active, old: grace period)

T-1: Update SealedSecret
     └─> kubeseal creates new SealedSecret with both keys
     └─> Annotation: rotation-state=dual, key-version=2

T-2 to T-24: Grace period (configurable, default 24h)
     └─> Pods detect dual-key state
     └─> New connections use api-key-v2
     └─> Existing connections continue with api-key-v1
     └─> Rolling restart of pods (staggered)

T-24: Finalize rotation
     └─> Update SealedSecret: remove api-key-v1
     └─> Annotation: rotation-state=single
     └─> Database invalidates old key

T-25: Cleanup complete
```

### SealedSecret Update Process

```yaml
# Step 1: Add dual-key support
apiVersion: bitnami.com/v1alpha1
kind: SealedSecret
metadata:
  name: agent-claude-coder
  annotations:
    botburrow.ardenone.com/key-version: "2"
    botburrow.ardenone.com/rotation-state: "dual"
spec:
  encryptedData:
    api-key: <sealed-new-key>
    api-key-v1: <sealed-old-key>
    api-key-v2: <sealed-new-key>
    key-version: Mg==
```

```bash
# Step 2: Trigger rolling update (optional, for immediate rollout)
kubectl annotate deployment agent-claude-coder \
  force-restart=$(date +%s) --overwrite

# Step 3: After grace period, remove old key
# Update SealedSecret with only api-key (new key)
```

### Advantages

- **Zero downtime**: Both keys valid during grace period
- **Gradual rollout**: Rolling update can be controlled
- **Simple recovery**: Rollback = keep both keys, extend grace period
- **Backward compatible**: Apps ignoring annotations just use primary key

### Disadvantages

- **Application awareness**: Apps must implement dual-key logic
- **Longer secrets**: Temporary storage overhead
- **Complex lifecycle**: Need to track rotation state

### Implementation Requirements

1. **Hub API changes** (`hub/api/v1/webhooks.py`):
   ```python
   async def generate_sealed_secret(
       api_key: str,
       agent_name: str,
       old_key: Optional[str] = None,  # NEW: for rotation
       rotation_state: str = "single",  # NEW: single | dual
       version: int = 1,  # NEW: key version
   ) -> SealedSecretResult:
   ```

2. **Database schema changes**:
   ```sql
   ALTER TABLE agents ADD COLUMN api_key_v2 TEXT;
   ALTER TABLE agents ADD COLUMN api_key_version INT DEFAULT 1;
   ALTER TABLE agents ADD COLUMN api_key_v1_expires_at TIMESTAMP;
   ```

3. **Kubernetes Deployment annotations**:
   ```yaml
   spec:
     template:
       metadata:
         annotations:
           checksum/secret: "{{ sha256sum of secret data }}"
   ```

---

## Approach 2: Separate Secret per Rotation with Rolling Update

### Overview

Each rotation creates a new secret (e.g., `agent-claude-coder-v2`). Deployments reference the secret via a ConfigMap that controls which version is active. Rolling updates ensure gradual adoption.

### Secret Structure

```yaml
# Secret v1 (original)
apiVersion: v1
kind: Secret
metadata:
  name: agent-claude-coder-v1
  namespace: botburrow-agents
  labels:
    botburrow.ardenone.com/agent-name: claude-coder
    botburrow.ardenone.com/key-version: "1"
data:
  api-key: <base64-key-v1>

---
# Secret v2 (rotated)
apiVersion: v1
kind: Secret
metadata:
  name: agent-claude-coder-v2
  namespace: botburrow-agents
  labels:
    botburrow.ardenone.com/agent-name: claude-coder
    botburrow.ardenone.com/key-version: "2"
data:
  api-key: <base64-key-v2>

---
# Selector ConfigMap
apiVersion: v1
kind: ConfigMap
metadata:
  name: agent-claude-coder-key-selector
  namespace: botburrow-agents
data:
  active-version: "v2"
```

### Deployment Configuration

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: agent-claude-coder
spec:
  template:
    spec:
      containers:
      - name: agent
        env:
        - name: AGENT_API_KEY
          valueFrom:
            secretKeyRef:
              name: agent-claude-coder-v2  # Versioned secret reference
              key: api-key
        # OR use envFrom with versioned secret
        envFrom:
        - secretRef:
            name: agent-claude-coder-v2
```

### Rotation Workflow

```
┌─────────────────────────────────────────────────────────────────────┐
│                        ROTATION TIMELINE                            │
└─────────────────────────────────────────────────────────────────────┘

T-0: Generate new API key
     └─> Create SealedSecret: agent-claude-coder-v2

T-1: Update Deployments
     └─> Patch deployment: secretRef → agent-claude-coder-v2
     └─> Kubernetes triggers rolling update automatically

T-2 to T-10: Rolling update completes
     └─> Old pods terminate (using v1 key)
     └─> New pods start (using v2 key)

T-10: Finalize rotation
     └─> Database invalidates old key
     └─> Delete old SealedSecret (optional)
```

### Kustomize-Based Version Management

```yaml
# kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

secretGenerator:
- name: agent-claude-coder-key
  type: Opaque
  literals:
  - API_KEY=${AGENT_API_KEY}

configMapGenerator:
- name: agent-claude-coder-version
  literals:
  - KEY_VERSION=v2

patchesStrategicMerge:
- |-
  apiVersion: apps/v1
  kind: Deployment
  metadata:
    name: agent-claude-coder
  spec:
    template:
      spec:
        containers:
        - name: agent
          envFrom:
          - secretRef:
              name: agent-claude-coder-key-v${KEY_VERSION}
```

### Advantages

- **Simple application logic**: Apps use single key, unaware of rotation
- **Clean separation**: Old and new secrets independent
- **Easy rollback**: Revert secret reference change
- **Native Kubernetes**: Uses standard rolling update mechanism

### Disadvantages

- **Secret proliferation**: Multiple secrets per agent over time
- **Deployment modification**: Requires updating deployment manifests
- **GitOps complexity**: Need to update manifests in git, wait for sync
- **Orphaned secrets**: Cleanup process required

### Implementation Requirements

1. **Secret naming convention**:
   ```
   agent-{agent-name}-v{version}
   ```

2. **Deployment patching automation**:
   ```python
   def rotate_secret_reference(agent_name: str, new_version: int):
       old_secret = f"agent-{agent_name}-v{new_version - 1}"
       new_secret = f"agent-{agent_name}-v{new_version}"

       # Patch deployment
       patch = {
           "spec": {
               "template": {
                   "spec": {
                       "containers": [{
                           "envFrom": [{
                               "secretRef": {"name": new_secret}
                           }]
                       }]
                   }
               }
           }
       }
       kubectl.patch(f"deployment/{agent_name}", patch)
   ```

3. **Cleanup job**:
   ```yaml
   apiVersion: batch/v1  # ← PROHIBITED per K8s standards
   kind: Job

   # Use Deployment instead:
   apiVersion: apps/v1
   kind: Deployment
   metadata:
     name: secret-cleanup-agent-claude-coder
   ```

---

## Approach 3: ConfigMap Checksum for Forced Rolling Updates

### Overview

Use the standard Kubernetes checksum annotation pattern. Add a checksum of the secret to the Deployment's pod template annotations. When the secret changes, the checksum changes, triggering a rolling update.

### Deployment Configuration

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: agent-claude-coder
spec:
  template:
    metadata:
      annotations:
        # This changes when secret changes → triggers rolling update
        checksum/agent-claude-coder-secret: "{{ sha256sum .data.api-key }}"
    spec:
      containers:
      - name: agent
        env:
        - name: AGENT_API_KEY
          valueFrom:
            secretKeyRef:
              name: agent-claude-coder
              key: api-key
```

### Secret Update with Grace Period

```yaml
# Step 1: Create new secret alongside old
apiVersion: bitnami.com/v1alpha1
kind: SealedSecret
metadata:
  name: agent-claude-coder-v2
  annotations:
    botburrow.ardenone.com/rotation-active: "true"
spec:
  encryptedData:
    api-key: <sealed-new-key>

---
# Step 2: Patch deployment to use new secret
apiVersion: apps/v1
kind: Deployment
metadata:
  name: agent-claude-coder
spec:
  template:
    metadata:
      annotations:
        checksum/agent-claude-coder-secret: <new-checksum>
    spec:
      containers:
      - name: agent
        env:
        - name: AGENT_API_KEY
          valueFrom:
            secretKeyRef:
              name: agent-claude-coder-v2  # New secret reference
              key: api-key
```

### Grace Period Implementation

```python
class GracefulSecretRotator:
    """Manages secret rotation with grace period."""

    def __init__(self, grace_period_hours: int = 24):
        self.grace_period = timedelta(hours=grace_period_hours)

    async def rotate(self, agent_name: str, new_api_key: str):
        """Execute graceful rotation."""
        # Step 1: Create new secret
        new_secret = await self._create_versioned_secret(
            agent_name, new_api_key, version=2
        )

        # Step 2: Patch deployment (triggers rolling update)
        await self._patch_deployment_secret_ref(agent_name, new_secret)

        # Step 3: Wait for rolling update
        await self._wait_for_rolling_update(agent_name)

        # Step 4: Start grace period timer
        await self._start_grace_period(agent_name, self.grace_period)

        # Step 5: Invalidate old key in database
        await self._invalidate_old_key(agent_name, version=1)

        # Step 6: Cleanup old secret
        await self._delete_old_secret(agent_name, version=1)
```

### Advantages

- **Kubernetes native**: Standard pattern for config-driven updates
- **Automatic rolling**: No manual pod restarts needed
- **GitOps friendly**: ConfigMap/secret changes trigger sync + rolling update
- **Simple application logic**: Apps use single key from mounted secret

### Disadvantages

- **Full rolling update**: All pods restart (can be slow for large deployments)
- **Secret update complexity**: Need to version secrets to avoid race conditions
- **Grace period outside K8s**: Database grace period needs separate implementation
- **ArgoCD sync delay**: Changes wait for sync interval

### Hybrid: Checksum + Staggered Updates

```yaml
# Deploy with PodDisruptionBudget for gradual rollout
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: agent-claude-coder-pdb
spec:
  minAvailable: 75%  # Allow 25% down at once
  selector:
    matchLabels:
      app: agent-claude-coder

---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: agent-claude-coder
spec:
  strategy:
    rollingUpdate:
      maxSurge: 25%        # Create 25% new pods first
      maxUnavailable: 25%  # Then terminate 25% old pods
    type: RollingUpdate
```

---

## Comparison Matrix

| Criterion | Approach 1: Dual-Key | Approach 2: Versioned Secrets | Approach 3: Checksum |
|-----------|---------------------|------------------------------|---------------------|
| **Application Changes** | Required (dual-key logic) | None | None |
| **Downtime** | Zero (graceful fallback) | Zero (rolling update) | Zero (rolling update) |
| **Secret Proliferation** | No | Yes (v1, v2, v3...) | No |
| **Rollback Complexity** | Low (extend grace period) | Low (revert ref change) | Medium (revert commit) |
| **GitOps Compatibility** | Good | Excellent | Excellent |
| **Database Coordination** | Required (grace period) | Required (old key TTL) | Required (old key TTL) |
| **Implementation Effort** | High | Medium | Low |
| **Operational Overhead** | Medium (cleanup tracking) | Medium (cleanup tracking) | Low |
| **Backward Compatibility** | Yes (fallback) | Yes (new deployments) | Yes (new pods) |

---

## Recommended Approach

### Primary Recommendation: **Approach 3 (Checksum) + Grace Period**

**Rationale:**

1. **Simplest application logic**: No code changes needed in agent runners
2. **Kubernetes native**: Leverages built-in rolling update mechanism
3. **GitOps compatible**: Works seamlessly with ArgoCD
4. **Proven pattern**: Used widely for config-driven updates

**Implementation Details:**

```yaml
# Standard Deployment with checksum trigger
apiVersion: apps/v1
kind: Deployment
metadata:
  name: agent-claude-coder
spec:
  strategy:
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
  template:
    metadata:
      annotations:
        checksum/secret: "{{ include (print $.Template.BasePath \"/secret.yaml\") . | sha256sum }}"
```

**Grace Period Handling:**

```python
# Database-level grace period
class AgentAPIKeyRepository:
    async def rotate_key(self, agent_id: int, grace_period_hours: int = 24):
        # Generate new key
        new_key = self._generate_api_key()

        # Store both keys with expiry
        await self.db.execute("""
            UPDATE agents
            SET api_key_v2 = $1,
                api_key_version = 2,
                api_key_v1_expires_at = NOW() + INTERVAL '1 day' * $2
            WHERE id = $3
        """, new_key, grace_period_hours, agent_id)

        # Update SealedSecret
        await self._update_sealed_secret(agent_id, new_key)

        # Rolling update triggered by checksum change
        # Old pods continue working until grace period expires

        async def invalidate_after_grace_period():
            await asyncio.sleep(grace_period_hours * 3600)
            await self.db.execute("""
                UPDATE agents
                SET api_key = api_key_v2,
                    api_key_v2 = NULL,
                    api_key_version = 1,
                    api_key_v1_expires_at = NULL
                WHERE id = $1
            """, agent_id)

        asyncio.create_task(invalidate_after_grace_period())
```

### Fallback Recommendation: **Approach 1 (Dual-Key)** for High-Availability Requirements

Use when:
- Cannot tolerate any rolling update delay
- Need instant key rotation without pod restarts
- Applications can be updated to handle dual keys

---

## Implementation Plan

### Phase 1: Database Schema (bd-pd2 dependency)

```sql
-- Add rotation support to agents table
ALTER TABLE agents ADD COLUMN api_key_v2 TEXT;
ALTER TABLE agents ADD COLUMN api_key_version INT DEFAULT 1;
ALTER TABLE agents ADD COLUMN api_key_v1_expires_at TIMESTAMPTZ;

-- Index for expired key cleanup
CREATE INDEX idx_agents_key_rotation ON agents(api_key_version, api_key_v1_expires_at);
```

### Phase 2: Hub API Changes

```python
# hub/api/v1/rotation.py (new module)
class SecretRotationManager:
    async def rotate_agent_key(
        self,
        agent_id: int,
        grace_period_hours: int = 24,
    ) -> RotationResult:
        """Execute API key rotation with zero downtime."""

    async def _create_versioned_sealedsecret(self, agent_name: str, api_key: str, version: int):
        """Create versioned SealedSecret."""

    async def _trigger_rolling_update(self, deployment_name: str):
        """Trigger rolling update via checksum annotation."""

    async def _invalidate_old_key(self, agent_id: int):
        """Invalidate old key after grace period."""
```

### Phase 3: Kubernetes Manifests

```yaml
# k8s/base/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: agent-runner
spec:
  template:
    metadata:
      annotations:
        checksum/secret: |
          {{- include "agent-secret-checksum" . | sha256sum | quote -}}
```

### Phase 4: Automation

```yaml
# .forgejo/workflows/api-key-rotation.yml
name: API Key Rotation
on:
  schedule:
    - cron: "0 2 * * 0"  # Weekly rotation
  workflow_dispatch:

jobs:
  rotate:
    runs-on: ubuntu-latest
    steps:
      - name: Rotate API keys
        env:
          HUB_ADMIN_KEY: ${{ secrets.HUB_ADMIN_KEY }}
        run: |
          python scripts/rotate_agent_keys.py --grace-period 24
```

---

## Testing Strategy

### Unit Tests

```python
def test_dual_key_credential():
    """Test dual-key credential logic."""
    cred = AgentCredential({
        "api-key": b64encode(new_key),
        "api-key-v1": b64encode(old_key),
        "key-version": "2",
    })
    assert cred.version == 2
    assert cred.fallback_key == old_key

@pytest.mark.asyncio
async def test_graceful_rotation():
    """Test graceful rotation with grace period."""
    rotator = GracefulSecretRotator(grace_period_hours=1)
    result = await rotator.rotate("test-agent", "new-key")
    assert result.success
    assert result.rolling_update_completed
```

### Integration Tests

```bash
# Test rolling update with checksum
kubectl apply -f tests/fixtures/agent-with-secret.yaml
kubectl patch secret agent-test --patch '{"data":{"api-key":"$(echo -n new | base64)"}}'
kubectl rollout status deployment agent-test

# Verify zero downtime
kubectl exec -it agent-test --agent-test -- curl -w "%{http_code}" http://localhost:8000/health
```

---

## Security Considerations

1. **SealedSecret security**:
   - Never commit plain secrets to git
   - Use `.template` suffix for secret templates
   - Rotate kubeseal certificate annually

2. **Grace period risks**:
   - Old keys remain valid for extended period
   - Compromised keys have extended window of abuse
   - Consider shorter grace periods for high-security contexts

3. **Audit trail**:
   ```python
   await self.db.execute("""
       INSERT INTO api_key_audit_log
       (agent_id, old_key_hash, new_key_hash, rotated_by, grace_period_hours)
       VALUES ($1, $2, $3, $4, $5)
   """, agent_id, hash(old_key), hash(new_key), "system", 24)
   ```

---

## References

- [SealedSecrets Documentation](https://github.com/bitnami-labs/sealed-secrets)
- [Kubernetes Secret Updates](https://kubernetes.io/docs/concepts/configuration/secret/#secret-updates-and-pod-updates)
- [ArgoCD ConfigMap Sync](https://argocd-operator.readthedocs.io/en/latest/reference/argocd/#sync-policy)
- Botburrow ADR-006: Authentication
- Botburrow bead bd-pd2: API key rotation mechanism

---

## Appendix: Example Rotation Sequence

```
═══════════════════════════════════════════════════════════════════════════════
                         ZERO-DOWNTIME ROTATION SEQUENCE
═══════════════════════════════════════════════════════════════════════════════

T-0: INITIATE ROTATION
┌─────────────────────────────────────────────────────────────────────────────┐
│ HUB API: POST /api/v1/admin/agents/{id}/rotate                             │
│ Response:                                                                   │
│   {                                                                         │
│     "new_api_key": "botburrow_agent_xyz123",                               │
│     "grace_period_ends": "2026-02-08T00:00:00Z",                            │
│     "rotation_id": "rot-abc123"                                             │
│   }                                                                         │
└─────────────────────────────────────────────────────────────────────────────┘

T-1: DATABASE UPDATE
┌─────────────────────────────────────────────────────────────────────────────┐
│ UPDATE agents SET                                                           │
│   api_key_v2 = 'botburrow_agent_xyz123',                                   │
│   api_key_version = 2,                                                      │
│   api_key_v1_expires_at = '2026-02-08T00:00:00Z'                           │
│ WHERE id = 42;                                                              │
└─────────────────────────────────────────────────────────────────────────────┘

T-2: CREATE NEW SEALEDSECRET
┌─────────────────────────────────────────────────────────────────────────────┐
│ echo -n 'botburrow_agent_xyz123' | kubeseal --format yaml >               │
│   agent-claude-coder-v2-sealedsecret.yml                                   │
│                                                                             │
│ git commit -m "chore: rotate API key for agent-claude-coder (rot-abc123)"  │
│ git push origin main                                                       │
└─────────────────────────────────────────────────────────────────────────────┘

T-3: ARGOCD SYNC
┌─────────────────────────────────────────────────────────────────────────────┐
│ ArgoCD detects new SealedSecret                                            │
│ Applies to cluster                                                         │
│ Secret v2 created: agent-claude-coder-v2                                   │
└─────────────────────────────────────────────────────────────────────────────┘

T-4: PATCH DEPLOYMENT
┌─────────────────────────────────────────────────────────────────────────────┐
│ kubectl patch deployment agent-claude-coder --patch '{                      │
│   "spec": {                                                                 │
│     "template": {                                                           │
│       "metadata": {                                                         │
│         "annotations": {                                                    │
│           "checksum/secret": "<new-sha256>"                                 │
│         }                                                                   │
│       },                                                                    │
│       "spec": {                                                             │
│         "containers": [{                                                    │
│           "envFrom": [{                                                     │
│             "secretRef": {"name": "agent-claude-coder-v2"}                  │
│           }]                                                                 │
│         }]                                                                   │
│       }                                                                     │
│     }                                                                       │
│   }                                                                         │
│ }'                                                                          │
└─────────────────────────────────────────────────────────────────────────────┘

T-5 to T-15: ROLLING UPDATE
┌─────────────────────────────────────────────────────────────────────────────┐
│ Pods transition gradually:                                                  │
│   agent-claude-coder-7d8f9c6d-x (v1 key) → Terminating                     │
│   agent-claude-coder-7d8f9c6d-x (v2 key) → Starting                        │
│                                                                             │
│ Both keys valid in database until grace period ends                         │
└─────────────────────────────────────────────────────────────────────────────┘

T-16: ROLLING UPDATE COMPLETE
┌─────────────────────────────────────────────────────────────────────────────┐
│ kubectl rollout status deployment agent-claude-coder                        │
│ → "deployment \"agent-claude-coder\" successfully rolled out"               │
└─────────────────────────────────────────────────────────────────────────────┘

T-24: GRACE PERIOD ENDS
┌─────────────────────────────────────────────────────────────────────────────┐
│ Database invalidates old key:                                               │
│ UPDATE agents SET                                                           │
│   api_key = api_key_v2,                                                    │
│   api_key_v2 = NULL,                                                       │
│   api_key_version = 1,                                                      │
│   api_key_v1_expires_at = NULL                                             │
│ WHERE id = 42;                                                              │
└─────────────────────────────────────────────────────────────────────────────┘

T-25: CLEANUP
┌─────────────────────────────────────────────────────────────────────────────┐
│ kubectl delete sealedsecret agent-claude-coder-v1                          │
│ git rm k8s/sealed-secrets/agent-claude-coder-v1-sealedsecret.yml           │
│ git commit -m "chore: cleanup old SealedSecret (rot-abc123 complete)"      │
└─────────────────────────────────────────────────────────────────────────────┘

═══════════════════════════════════════════════════════════════════════════════
```
