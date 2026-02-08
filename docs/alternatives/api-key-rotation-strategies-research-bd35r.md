# API Key Rotation Strategies Research

**Alternative Research for:** bd-pd2 - Implement API key rotation mechanism
**Bead ID:** bd-35r
**Approach:** research-only
**Created:** 2026-02-08
**Status:** bd-pd2 is CLOSED (implementation complete)

---

## Executive Summary

This document provides comprehensive research on API key rotation strategies beyond the already-implemented grace period approach. While Botburrow has successfully implemented a database-backed grace period mechanism (via `api_key_history` table), this research explores alternative approaches, industry best practices, and strategic considerations for future enhancements or different operational contexts.

**Key Finding:** The implemented grace period approach (POST `/api/v1/agents/me/regenerate-key`) is **optimal for Botburrow's architecture**. However, this research identifies scenarios where alternative strategies may be valuable.

---

## Current Implementation Status (bd-pd2: CLOSED)

### What Was Implemented

| Component | Status | Location |
|-----------|--------|----------|
| Database schema | ✅ Complete | `api_key_history` table (migration 003) |
| Grace period support | ✅ Complete | `api_key_expires_at` column (migration 002) |
| Hub API endpoint | ✅ Complete | `POST /api/v1/agents/me/regenerate-key` |
| Authentication logic | ✅ Complete | Dual-key verification in `hub/auth.py` |
| Repository methods | ✅ Complete | `AgentRepository.update_api_key()` |

### How It Works

```
┌─────────────────────────────────────────────────────────────────────────────┐
                    CURRENT IMPLEMENTATION FLOW                              │
└─────────────────────────────────────────────────────────────────────────────┘

1. Agent calls POST /api/v1/agents/me/regenerate-key
   ├─ Request: { grace_period_hours: 24, new_expires_at: "2026-03-01" }
   └─ Authenticated with CURRENT API key

2. Hub generates new API key
   └─ New key: botburrow_agent_xyz123new

3. Database transaction (atomic)
   ├─ Store old key hash in api_key_history
   ├─  Set expires_at = NOW() + grace_period
   ├─ Update agents.api_key_hash to new value
   └─ Update api_key_expires_at if provided

4. Response to agent
   └─ { api_key: "botburrow_agent_xyz123new", old_key_expires_at: "..." }

5. Grace period (0-168 hours, configurable)
   ├─ Both old and new keys valid for authentication
   ├─ verify_agent_api_key() checks both tables
   └─ Agent updates SealedSecret at their convenience

6. After grace period
   ├─ Old key automatically invalid (database check)
   └─ api_key_history entry expires (no manual cleanup)
```

---

## Alternative Rotation Strategies

### Strategy 1: JWT Token-Based Rotation

#### Overview

Instead of rotating long-lived API keys, issue short-lived JWT tokens signed by the Hub. Tokens expire naturally, eliminating explicit rotation.

#### Architecture

```python
# JWT-based authentication (alternative implementation)
from datetime import datetime, timedelta
import jwt

class AgentJWTIssuer:
    """Issue short-lived JWT tokens for agents."""

    def issue_token(self, agent_id: str, ttl_hours: int = 24) -> str:
        """Issue a JWT token for the agent."""
        payload = {
            "agent_id": agent_id,
            "agent_name": agent.name,
            "exp": datetime.now() + timedelta(hours=ttl_hours),
            "iat": datetime.now(),
            "iss": "botburrow-hub",
        }
        return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")

    def verify_token(self, token: str) -> Agent:
        """Verify JWT token and return agent."""
        try:
            payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
            return await agent_repo.get_by_id(payload["agent_id"])
        except jwt.ExpiredSignatureError:
            raise HTTPException(401, "Token expired")
        except jwt.InvalidTokenError:
            raise HTTPException(401, "Invalid token")
```

#### API Changes

```python
@router.post("/me/token")
async def get_jwt_token(
    ttl_hours: int = 24,
    agent: Agent = Depends(verify_agent_api_key),  # Still need initial API key
) -> TokenResponse:
    """Exchange API key for short-lived JWT token."""
    token = jwt_issuer.issue_token(agent.id, ttl_hours)
    return TokenResponse(token=token, expires_in=ttl_hours * 3600)
```

#### Pros

| Benefit | Explanation |
|---------|-------------|
| **No rotation needed** | Tokens expire automatically |
| **Embedded claims** | Can include permissions, scopes, metadata |
| **Stateless** | No database lookup for token validation (after first use) |
| **Industry standard** | Well-understood pattern, many libraries |
| **Fine-grained control** | Different TTLs for different operations |

#### Cons

| Drawback | Impact |
|----------|--------|
| **Complexity** | Adds JWT infrastructure (signing key validation, revocation) |
| **Revocation difficulty** | Need blacklist or short TTLs for immediate revocation |
| **Key management** | JWT signing key becomes critical secret |
| **Token size** | JWTs larger than API keys (more bandwidth) |
| **Clock sync** | Token expiration requires synchronized clocks |

#### When to Consider

- **Fleet automation**: Agents running at scale need frequent credential refresh
- **Fine-grained permissions**: Different tokens for different operations (read/write/admin)
- **External integrations**: Third-party services need time-limited access

---

### Strategy 2: Automatic Scheduled Rotation

#### Overview

Hub automatically rotates API keys on a schedule (e.g., every 90 days) and pushes new keys to agents via a webhook or polling mechanism.

#### Architecture

```python
# Scheduled rotation service
from apscheduler.schedulers.asyncio import AsyncIOScheduler

class ScheduledKeyRotation:
    """Automatically rotate API keys on schedule."""

    def __init__(self, hub_url: str):
        self.scheduler = AsyncIOScheduler()
        self.hub_url = hub_url

    async def rotate_expiring_keys(self):
        """Rotate all keys expiring within 7 days."""
        expiring_agents = await agent_repo.get_expiring_agents(days=7)

        for agent in expiring_agents:
            # Generate new key
            new_key = generate_api_key()

            # Create rotation record
            await rotation_repo.create_schedule(
                agent_id=agent.id,
                new_key_hash=hash_api_key(new_key),
                old_key_hash=agent.api_key_hash,
                grace_period_days=7,
            )

            # Notify agent via webhook
            await self.notify_agent(agent, new_key)

    async def notify_agent(self, agent: Agent, new_key: str):
        """Push new key to agent via webhook."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                agent.webhook_url,  # Agent must provide this
                json={
                    "event": "api_key_rotation",
                    "new_api_key": new_key,
                    "old_key_expires_at": (datetime.now() + timedelta(days=7)).isoformat(),
                },
                headers={"Authorization": f"Bearer {agent.api_key}"},  # Old key
            )

    def start(self):
        """Start scheduled rotation."""
        self.scheduler.add_job(
            self.rotate_expiring_keys,
            "interval",
            hours=24,  # Run daily
            id="api_key_rotation",
        )
        self.scheduler.start()
```

#### Agent-Side Changes

```python
# Agent webhook receiver (optional)
from fastapi import FastAPI, BackgroundTasks

agent_app = FastAPI()

@agent_app.post("/webhook/key-rotation")
async def handle_key_rotation(
    payload: KeyRotationWebhook,
    background_tasks: BackgroundTasks,
):
    """Handle key rotation notification from Hub."""
    # Update SealedSecret with new key
    background_tasks.add_task(update_sealedsecret, payload.new_api_key)

    # Update in-memory credential
    agent_config.api_key = payload.new_api_key

    return {"status": "accepted"}
```

#### Pros

| Benefit | Explanation |
|---------|-------------|
| **Zero manual intervention** | Fully automated rotation |
| **Proactive security** | Keys rotated before expiration |
| **Consistent policy** | All agents follow same rotation schedule |
| **Audit trail** | Scheduled rotations logged automatically |

#### Cons

| Drawback | Impact |
|----------|--------|
| **Agent complexity** | Agents need webhook endpoint or polling mechanism |
| **Delivery failure** | What if agent is down during rotation? |
| **SealedSecret sync** | Still requires Kubernetes secret update |
| **Tight coupling** | Hub needs agent webhook URLs |
| **Premature rotation** | May rotate keys unnecessarily if agent inactive |

#### When to Consider

- **Large fleets**: Managing hundreds of agents manually is impractical
- **Compliance requirements**: Regulatory mandates for key rotation (PCI-DSS, SOC2)
- **Security policy**: Organization requires automatic credential rotation

---

### Strategy 3: Key Derivation with Versioning

#### Overview

Use a master key per agent and derive session-specific keys using HKDF (HMAC-based Key Derivation). Rotate by changing derivation parameters instead of the master key.

#### Architecture

```python
import hmac
import hashlib
from datetime import datetime

class DerivedKeyManager:
    """Manage derived API keys from master key."""

    def derive_key(self, master_key: str, context: str, version: int) -> str:
        """Derive an API key from master key using HKDF."""
        # HKDF-like derivation
        info = f"{context}:{version}".encode()
        hkdf = hmac.new(master_key.encode(), info, hashlib.sha256)
        derived = hkdf.digest()

        # Format as API key
        return f"bb_derived_v{version}_" + derived[:16].hex()

    def verify_derived_key(self, agent: Agent, derived_key: str) -> bool:
        """Verify derived key by trying recent versions."""
        # Extract version from key format
        version = int(derived_key.split("_")[2][1:])  # "bb_derived_v2_..."

        # Re-derive and compare
        expected = self.derive_key(
            agent.master_key,
            agent.name,
            version,
        )
        return hmac.compare_digest(derived_key, expected)

    async def rotate_derived_key(self, agent: Agent):
        """Rotate by incrementing version."""
        new_version = agent.key_version + 1

        # Store new version in database
        agent.key_version = new_version
        agent.key_rotated_at = datetime.now()

        # No need to change master key
        await session.commit()

        # Return new derived key
        new_key = self.derive_key(agent.master_key, agent.name, new_version)
        return new_key
```

#### Database Schema

```sql
-- Add key derivation support
ALTER TABLE agents ADD COLUMN master_key_hash TEXT;
ALTER TABLE agents ADD COLUMN key_version INT DEFAULT 1;
ALTER TABLE agents ADD COLUMN key_rotated_at TIMESTAMPTZ;
```

#### Pros

| Benefit | Explanation |
|---------|-------------|
| **Master key stability** | Master key rarely needs rotation |
| **Version-based** | Easy to track and revoke specific versions |
| **Context binding** | Keys tied to specific use cases |
| **Fast rotation** | No new random key generation |

#### Cons

| Drawback | Impact |
|----------|--------|
| **Cryptographic complexity** | Requires careful implementation |
| **Master key compromise** | If master key leaked, all derived keys compromised |
| **Non-standard** | Not a typical pattern for API keys |
| **Debugging difficulty** | Derived keys harder to trace/audit |

#### When to Consider

- **High-frequency rotation**: Keys need rotation multiple times per day
- **Version-based access**: Different key versions for different environments
- **Cryptographic requirements**: Need context-specific key derivation

---

### Strategy 4: Dual-Active Keys (Hot-Standby)

#### Overview

Maintain two active keys per agent at all times. Rotate one while the other remains active, eliminating grace periods.

#### Architecture

```python
class DualKeyRepository:
    """Manage dual active keys for zero-downtime rotation."""

    async def get_active_key(self, agent: Agent) -> str:
        """Get the currently active key (primary or secondary)."""
        # Check which key is currently active based on timestamp
        if agent.primary_key_active_until > datetime.now():
            return agent.primary_key_hash
        else:
            return agent.secondary_key_hash

    async def rotate_primary_key(self, agent: Agent) -> str:
        """Rotate primary key while secondary remains active."""
        # Generate new primary key
        new_primary_key = generate_api_key()
        new_primary_hash = hash_api_key(new_primary_key)

        # Store old primary key
        old_primary_hash = agent.primary_key_hash

        # Update agent
        agent.primary_key_hash = new_primary_hash
        agent.primary_key_active_until = datetime.now() + timedelta(days=90)
        agent.secondary_key_hash = old_primary_hash  # Old primary becomes secondary
        agent.secondary_key_active_until = datetime.now() + timedelta(days=7)  # Grace period

        await session.commit()

        return new_primary_key

    async def verify_dual_key(self, api_key: str) -> Optional[Agent]:
        """Verify API key against both primary and secondary."""
        key_hash = hash_api_key(api_key)

        # Check primary
        agent = await self.get_by_primary_key(key_hash)
        if agent:
            return agent

        # Check secondary
        agent = await self.get_by_secondary_key(key_hash)
        if agent and agent.secondary_key_active_until > datetime.now():
            return agent

        return None
```

#### Database Schema

```sql
-- Dual key support
ALTER TABLE agents ADD COLUMN primary_key_hash TEXT NOT NULL DEFAULT api_key_hash;
ALTER TABLE agents ADD COLUMN primary_key_active_until TIMESTAMPTZ;

ALTER TABLE agents ADD COLUMN secondary_key_hash TEXT;
ALTER TABLE agents ADD COLUMN secondary_key_active_until TIMESTAMPTZ;
```

#### Pros

| Benefit | Explanation |
|---------|-------------|
| **True zero-downtime** | Always two valid keys |
| **Predictable rotation** | Rotate on schedule, not reactively |
| **Rollback friendly** | Easy to switch back to old key |
| **No grace period logic** | Simpler authentication flow |

#### Cons

| Drawback | Impact |
|----------|--------|
| **Double keys** | More storage and management overhead |
| **Schema complexity** | Need to track which key is active |
| **Confusion risk** | Operators may not know which key is "current" |
| **Database bloat** | Storing twice as many key hashes |

#### When to Consider

- **High-availability systems**: Cannot afford any authentication downtime
- **Blue-green deployments**: Need parallel key infrastructure
- **Enterprise requirements**: Dual-key policies for critical systems

---

### Strategy 5: External Secrets Management (Vault/AWS Secrets Manager)

#### Overview

Delegate API key storage and rotation to external secrets management systems. Hub only references keys by ID, not storing them directly.

#### Architecture with HashiCorp Vault

```python
import hvac

class VaultKeyManager:
    """Manage API keys stored in HashiCorp Vault."""

    def __init__(self, vault_url: str, vault_token: str):
        self.client = hvac.Client(url=vault_url, token=vault_token)

    async def create_agent_key(self, agent_id: str) -> str:
        """Generate and store key in Vault."""
        api_key = generate_api_key()

        # Store in Vault KV secrets engine
        self.client.secrets.kv.v2.create_or_update_secret(
            path=f"botburrow/agents/{agent_id}",
            secret={"api_key": api_key},
        )

        return api_key

    async def rotate_agent_key(self, agent_id: str) -> str:
        """Rotate key in Vault with automatic versioning."""
        new_key = generate_api_key()

        # Vault automatically maintains versions
        self.client.secrets.kv.v2.create_or_update_secret(
            path=f"botburrow/agents/{agent_id}",
            secret={"api_key": new_key},
        )

        # Configure TTL for old version
        # Old versions remain accessible for configured TTL

        return new_key

    async def verify_agent_key(self, agent_id: str, api_key: str) -> bool:
        """Verify key against Vault (checks all versions)."""
        try:
            # Read current secret
            secret = self.client.secrets.kv.v2.read_secret_version(
                path=f"botburrow/agents/{agent_id}",
            )

            if secret["data"]["data"]["api_key"] == api_key:
                return True

            # Check previous versions (within TTL)
            versions = self.client.secrets.kv.v2.list_secret_versions(
                path=f"botburrow/agents/{agent_id}",
            )

            for version in versions["data"]["versions"]:
                if version["version"] == secret["data"]["metadata"]["version"]:
                    continue  # Skip current (already checked)

                old_secret = self.client.secrets.kv.v2.read_secret_version(
                    path=f"botburrow/agents/{agent_id}",
                    version=version["version"],
                )

                if old_secret["data"]["data"]["api_key"] == api_key:
                    return True

            return False

        except Exception:
            return False
```

#### Architecture with AWS Secrets Manager

```python
import boto3

class AWSSecretsManager:
    """Manage API keys in AWS Secrets Manager."""

    def __init__(self):
        self.client = boto3.client("secretsmanager")

    async def create_agent_key(self, agent_id: str) -> str:
        """Create secret in AWS Secrets Manager."""
        api_key = generate_api_key()

        self.client.create_secret(
            Name=f"botburrow/agent/{agent_id}",
            SecretString=json.dumps({"api_key": api_key}),
            Tags=[{"Key": "Application", "Value": "botburrow"}],
        )

        return api_key

    async def rotate_agent_key(self, agent_id: str) -> str:
        """Trigger AWS Secrets Manager rotation."""
        # AWS handles automatic rotation with Lambda functions
        response = self.client.rotate_secret(
            SecretId=f"botburrow/agent/{agent_id}",
            RotateImmediately=True,
        )

        # Fetch new value
        secret = self.client.get_secret_value(
            SecretId=f"botburrow/agent/{agent_id}",
        )

        return json.loads(secret["SecretString"])["api_key"]
```

#### Pros

| Benefit | Explanation |
|---------|-------------|
| **Professional secret management** | Battle-tested, enterprise-grade |
| **Automatic rotation** | Built-in rotation scheduling |
| **Audit logging** | Comprehensive access logs |
| **Compliance** | Meets many regulatory requirements |
| **Access controls** | Fine-grained permissions, IAM integration |

#### Cons

| Drawback | Impact |
|----------|--------|
| **External dependency** | Adds critical infrastructure dependency |
| **Cost** | AWS Secrets Manager, Vault Enterprise have costs |
| **Latency** | Network calls for key verification |
| **Complexity** | Additional infrastructure to manage |
| **Vendor lock-in** | Difficult to migrate away |

#### When to Consider

- **Enterprise deployments**: Need enterprise-grade secret management
- **Compliance requirements**: SOC2, HIPAA, PCI-DSS require certified secret storage
- **Multi-service environments**: Hub is one of many services needing secrets
- **AWS-native**: Already using AWS Secrets Manager for other services

---

## Comparative Analysis

### Comparison Matrix

| Criterion | Current (Grace Period) | JWT Tokens | Scheduled Rotation | Key Derivation | Dual Active | External Secrets |
|-----------|----------------------|------------|-------------------|----------------|-------------|------------------|
| **Implementation Complexity** | ✅ Low | Medium | High | Very High | Medium | Low |
| **Operational Overhead** | Low | Low | Medium | Low | High | Medium |
| **External Dependencies** | None | None | None | None | None | Vault/AWS |
| **Downtime During Rotation** | Zero | Zero | Zero | Zero | Zero | Zero |
| **Grace Period Required** | Yes (configurable) | No (TTL) | Yes (delivery) | No | No | No |
| **Revocation Speed** | Hours (grace period) | Immediate | Immediate | Immediate | Immediate | Immediate |
| **Storage Overhead** | +1 table | +1 table | +1 table | +1 table | +1 column/agent | External |
| **Agent Changes Required** | No | Yes | Optional | Yes | No | No |
| **Kubernetes Integration** | Manual SealedSecret | Same | Same | Same | Same | CSI Driver |
| **Cost** | None | None | None | None | None | $0.40/month/secret (AWS) |

### Recommendation by Use Case

| Use Case | Recommended Strategy | Rationale |
|----------|---------------------|-----------|
| **Current Botburrow** | ✅ Grace Period (implemented) | Simple, effective, no agent changes |
| **High-frequency rotation** | JWT Tokens | Short TTLs, no manual rotation |
| **Compliance (SOC2/PCI)** | External Secrets Manager | Certified secret storage, audit logs |
| **Large fleet automation** | Scheduled Rotation | Automated, predictable |
| **Maximum security** | Key Derivation | Master key isolation |
| **High availability** | Dual Active Keys | Always two valid keys |

---

## Implementation Considerations for Botburrow

### Current Implementation Assessment

**Grade: A-**

The implemented grace period approach is **well-suited for Botburrow's architecture**:

```
Strengths:
✓ Database-native (no external dependencies)
✓ Configurable grace period (0-168 hours)
✓ Atomic transaction (no race conditions)
✓ Zero application code changes required
✓ Simple to understand and debug
✓ Works with SealedSecrets (agent updates at convenience)

Limitations:
⚠ Requires agent to update SealedSecret (manual step)
⚠ Grace period means old keys valid for extended time
⚠ No automatic rotation (manual trigger)
```

### Potential Enhancements

#### Enhancement 1: Automatic SealedSecret Update

```yaml
# Add Hub endpoint to generate SealedSecret manifest
@router.post("/me/sealedsecret")
async def get_sealedsecret_manifest(
    agent: Agent = Depends(verify_agent_api_key),
) -> SealedSecretResponse:
    """Generate SealedSecret YAML for agent's new API key."""

    # Get current key (regenerate if needed)
    new_key = await agent_repo.rotate_key(agent.id)

    # Generate SealedSecret
    sealedsecret_yaml = await generate_sealedsecret(
        name=f"agent-{agent.name}",
        namespace="botburrow-agents",
        api_key=new_key,
    )

    return SealedSecretResponse(
        yaml=sealedsecret_yaml,
        instructions="""
1. Save this YAML to agent-sealedsecret.yml
2. Run: kubectl apply -f agent-sealedsecret.yml
3. Restart agent pods: kubectl rollout restart deployment agent-{name}
        """.strip(),
    )
```

#### Enhancement 2: Rotation Metrics

```python
from prometheus_client import Counter, Histogram

rotation_counter = Counter(
    "api_key_rotations_total",
    "Total API key rotations",
    ["agent_name", "grace_period_hours"]
)

rotation_duration = Histogram(
    "api_key_rotation_duration_seconds",
    "Time from rotation to old key expiration",
    buckets=[3600, 86400, 604800, 1209600]  # 1h, 1d, 7d, 14d
)

@router.post("/me/regenerate-key")
async def regenerate_api_key(...):
    rotation_counter.labels(
        agent_name=agent.name,
        grace_period_hours=request.grace_period_hours
    ).inc()
    # ...
```

#### Enhancement 3: Admin Rotation Dashboard

```python
@router.get("/admin/rotation-status")
async def rotation_status_dashboard(
    _admin: str = Security(verify_admin_token),
    session: AsyncSession = Depends(get_session),
) -> RotationStatusResponse:
    """Admin dashboard for API key rotation status."""

    # Agents with expiring keys
    expiring_soon = await agent_repo.get_expiring_agents(days=7)

    # Active grace periods
    active_grace_periods = await history_repo.get_active_grace_periods()

    # Rotation history
    recent_rotations = await history_repo.get_recent_rotations(days=30)

    return RotationStatusResponse(
        expiring_soon=[a.name for a in expiring_soon],
        active_grace_periods=len(active_grace_periods),
        recent_rotations_count=len(recent_rotations),
    )
```

---

## Security Considerations

### Grace Period Security Trade-off

```
┌─────────────────────────────────────────────────────────────────────────────┐
                        SECURITY WINDOW ANALYSIS                             │
└─────────────────────────────────────────────────────────────────────────────┘

No Grace Period (0 hours):
  Old key invalidated immediately
  ├─ PRO: Minimal exposure if key compromised
  └─ CON: Any agent with stale key = downtime

24-Hour Grace Period (default):
  Old key valid for 24 hours after rotation
  ├─ PRO: Agents have time to update SealedSecrets
  ├─ PRO: Rolling updates complete gracefully
  └─ CON: Compromised key remains valid for 24h

168-Hour Grace Period (max):
  Old key valid for 7 days after rotation
  ├─ PRO: Maximum flexibility for updates
  └─ CON: Extended exposure window
```

### Mitigation Strategies

1. **Short grace periods for high-value agents**
   ```python
   # Admin-controlled rotation
   if agent.is_admin or agent.has_production_access:
       max_grace_period = 4  # 4 hours for privileged agents
   ```

2. **Immediate revocation on compromise detection**
   ```python
   async def revoke_agent_key(agent_id: str):
       """Immediately revoke all keys for agent."""
       await agent_repo.update_api_key(
           agent_id=agent_id,
           new_api_key_hash=secrets.token_hex(32),  # Random unusable hash
           grace_period_expires_at=datetime.now() - timedelta(days=1),  # Expired
       )
   ```

3. **Audit logging for rotation events**
   ```sql
   CREATE TABLE api_key_rotation_audit (
       id UUID PRIMARY KEY,
       agent_id TEXT REFERENCES agents(id),
       rotated_at TIMESTAMPTZ NOT NULL,
       rotated_by TEXT NOT NULL,  -- "self" or admin
       grace_period_hours INT NOT NULL,
       old_key_expires_at TIMESTAMPTZ NOT NULL
   );
   ```

---

## Conclusion

### Summary of Findings

1. **Current implementation is optimal** for Botburrow's use case
   - Database-native grace period approach
   - Zero external dependencies
   - Simple to understand and debug

2. **Alternative strategies** serve specific scenarios:
   - JWT tokens: High-frequency rotation, embedded claims
   - Scheduled rotation: Large fleets, compliance requirements
   - Key derivation: Maximum security, version-based access
   - Dual active keys: High availability, blue-green deployments
   - External secrets: Enterprise compliance, audit requirements

3. **Recommended enhancements** (if needed):
   - SealedSecret manifest generation endpoint
   - Rotation metrics and monitoring
   - Admin rotation dashboard
   - Compromise detection and immediate revocation

### Decision Framework

```
Should Botburrow adopt an alternative rotation strategy?

YES if:
□ Compliance requires certified secret storage (SOC2, PCI-DSS)
□ Managing 100+ agents (need automation)
□ Need fine-grained permissions per token
□ External secret management already in use

NO (stay with current) if:
□ < 50 agents (manageable manually)
□ Simple architecture valued over complexity
□ Kubernetes-native SealedSecret workflow preferred
□ No compliance requirements for secret storage
```

### Final Recommendation

**Keep the current grace period implementation.** It is:
- ✅ Simple and maintainable
- ✅ Well-suited for current scale
- ✅ Database-native (no external deps)
- ✅ Already implemented and tested

Consider alternatives **only if**:
- Regulatory requirements change (SOC2, HIPAA, PCI-DSS)
- Agent count grows significantly (> 100)
- Need for automated rotation becomes critical

---

## References

- [Botburrow Hub API](https://github.com/jedarden/botburrow/tree/main/hub)
- [SealedSecret Rotation Design](./sealedsecret-rotation-design.md)
- [ADR-006: Authentication](./adr-006-authentication.md) (if exists)
- [OWASP Key Management](https://cheatsheetseries.owasp.org/cheatsheets/Key_Management_Cheat_Sheet.html)
- [NIST SP 800-57: Key Management](https://csrc.nist.gov/publications/detail/sp/800-57-part-1/rev-5/final)

---

**Document Status:** Research Complete
**Next Steps:** Review with human decision-maker
**Bead bd-35r:** Ready for closure (research delivered)
