# API Key Expiration Column Research

**Alternative Research for:** bd-2br - Add api_key_expires_at column to agents table
**Bead ID:** bd-3sh
**Approach:** research-only
**Created:** 2026-02-08
**Status:** **IMPLEMENTATION COMPLETE** (bd-2br is CLOSED)

---

## Executive Summary

**CRITICAL FINDING:** The `api_key_expires_at` feature has been **fully implemented and deployed**. This research document confirms the implementation is complete and documents the approach taken.

**Status:**
- ✅ **bd-2br is CLOSED** - Original task completed
- ✅ **Migration 002 deployed** - `api_key_expires_at` column added to `agents` table
- ✅ **Database model updated** - SQLAlchemy Agent model includes the field
- ✅ **API endpoints updated** - Registration and key regeneration support expiration
- ✅ **Documentation exists** - Related research on API key rotation strategies (bd-35r)

**Conclusion:** This alternative research bead should be **closed** as the original implementation is complete and production-ready.

---

## Implementation Status

### 1. Database Schema (Migration 002)

**File:** `hub/database/migrations/002_add_api_key_expiration.sql`

```sql
-- Add api_key_expires_at column (nullable, with index)
ALTER TABLE agents
ADD COLUMN IF NOT EXISTS api_key_expires_at TIMESTAMPTZ;

-- Add index on api_key_expires_at for efficient queries of expiring keys
CREATE INDEX IF NOT EXISTS idx_agents_api_key_expires_at
ON agents(api_key_expires_at);

-- Add comment for documentation
COMMENT ON COLUMN agents.api_key_expires_at IS 'API key expiration timestamp for scheduled rotation';
```

**Implementation Details:**
- Column type: `TIMESTAMPTZ` (timezone-aware timestamp)
- Nullable: Yes (allows non-expiring keys)
- Indexed: Yes (for efficient querying of expiring keys)
- Comment: Added for documentation

### 2. Database Model (SQLAlchemy)

**File:** `hub/database/__init__.py:57-60`

```python
api_key_expires_at: Mapped[Optional[datetime]] = mapped_column(
    TIMESTAMP(timezone=True), nullable=True, index=True,
    comment="API key expiration timestamp for scheduled rotation"
)
```

**Model Integration:**
- Field type: `TIMESTAMP(timezone=True)` with `Optional` datetime
- Included in `Agent.to_dict()` for API responses
- Used in `AgentRepository.create()` for agent creation

### 3. API Endpoints

#### Agent Registration (POST /api/v1/agents/register)

**File:** `hub/api/v1/agents.py:40`

```python
class AgentRegisterRequest(BaseModel):
    api_key_expires_at: Optional[str] = Field(
        None,
        description="API key expiration timestamp (ISO 8601)"
    )
```

**Behavior:**
- Accepts optional `api_key_expires_at` in ISO 8601 format
- Validates timestamp format on registration
- Stores parsed datetime in database

#### Key Regeneration (POST /api/v1/agents/me/regenerate-key)

**File:** `hub/api/v1/agents.py:97-100`

```python
class RegenerateKeyRequest(BaseModel):
    new_expires_at: Optional[str] = Field(
        None,
        description="New API key expiration timestamp (ISO 8601). If not provided, key does not expire."
    )
```

**Behavior:**
- Allows updating `api_key_expires_at` during key rotation
- Supports setting expiration on regenerated keys
- Validates ISO 8601 format

### 4. Related Features (Migration 003)

**File:** `hub/database/migrations/003_create_api_key_history.sql`

For complete API key rotation support, Migration 003 adds:

- `api_key_history` table for tracking old keys during rotation
- Grace period support (old keys valid for configurable time)
- Composite indexes for efficient grace period queries
- Integration with authentication flow (`hub/auth.py`)

---

## Design Approach Analysis

### Chosen Approach: Simple Nullable Column with Index

**Implementation Pattern:**
```sql
ALTER TABLE agents ADD COLUMN api_key_expires_at TIMESTAMPTZ;
CREATE INDEX idx_agents_api_key_expires_at ON agents(api_key_expires_at);
```

**Why This Approach Was Chosen:**

| Aspect | Decision | Rationale |
|--------|----------|-----------|
| **Column Type** | `TIMESTAMPTZ` | Timezone-aware timestamps for distributed systems |
| **Nullable** | Yes | Allows non-expiring keys for flexibility |
| **Indexed** | Yes | Efficient queries for "expiring soon" lookups |
| **Separate Table** | No | Simple enough to live on agents table |
| **Default Value** | None | Explicit expiration required per key |

---

## Alternative Approaches (Not Taken)

### Alternative 1: Separate api_key_expirations Table

```sql
-- NOT IMPLEMENTED
CREATE TABLE api_key_expirations (
    agent_id TEXT PRIMARY KEY REFERENCES agents(id),
    expires_at TIMESTAMPTZ NOT NULL,
    notified_at TIMESTAMPTZ,
    rotation_scheduled_at TIMESTAMPTZ
);
```

**Pros:**
- Separation of concerns
- Additional metadata fields (notification timestamps)
- No schema changes to agents table

**Cons:**
- JOIN required for every agent query
- Additional complexity
- Not needed for current requirements

**Why Not Chosen:** Current requirements are simple enough for a single column.

### Alternative 2: JSONB Metadata Column

```sql
-- NOT IMPLEMENTED
ALTER TABLE agents ADD COLUMN metadata JSONB;
-- Store as: {"api_key_expires_at": "2026-03-01T00:00:00Z"}
```

**Pros:**
- Flexible schema for future metadata
- No column-specific migrations

**Cons:**
- No index support without complex JSONB indexes
- Type safety lost
- Query complexity increased

**Why Not Chosen:** Strong typing and indexing are more important than schema flexibility.

### Alternative 3: Expiration-Based Partitioning

```sql
-- NOT IMPLEMENTED
CREATE TABLE agents_active (
    CHECK (api_key_expires_at > NOW())
) INHERITS (agents);

CREATE TABLE agents_expired (
    CHECK (api_key_expires_at <= NOW())
) INHERITS (agents);
```

**Pros:**
- Natural data archival
- Fast queries on active agents

**Cons:**
- Complex partition management
- Overkill for current scale
- PostgreSQL inheritance limitations

**Why Not Chosen:** Current scale doesn't warrant partitioning complexity.

---

## Query Patterns Enabled

### Find Expiring Agents (Within 7 Days)

```sql
SELECT id, name, api_key_expires_at
FROM agents
WHERE api_key_expires_at IS NOT NULL
  AND api_key_expires_at BETWEEN NOW() AND NOW() + INTERVAL '7 days'
ORDER BY api_key_expires_at ASC;
```

### Find Expired Agents

```sql
SELECT id, name, api_key_expires_at
FROM agents
WHERE api_key_expires_at IS NOT NULL
  AND api_key_expires_at < NOW()
ORDER BY api_key_expires_at DESC;
```

### Count Agents Without Expiration

```sql
SELECT COUNT(*) as non_expiring_count
FROM agents
WHERE api_key_expires_at IS NULL;
```

---

## Integration with API Key Rotation

The `api_key_expires_at` column enables scheduled API key rotation as documented in:

**Related Research:** `docs/alternatives/api-key-rotation-strategies-research-bd35r.md`

### Rotation Workflow

1. **Scheduled check** (e.g., daily cron):
   ```python
   expiring_agents = await session.execute(
       select(Agent).where(
           Agent.api_key_expires_at < datetime.now() + timedelta(days=7)
       )
   )
   ```

2. **Rotate expiring keys** via `POST /api/v1/agents/me/regenerate-key`:
   ```python
   response = await client.post(
       "/api/v1/agents/me/regenerate-key",
       json={
           "grace_period_hours": 24,
           "new_expires_at": (datetime.now() + timedelta(days=90)).isoformat()
       }
   )
   ```

3. **Update SealedSecret** with new key and expiration

---

## Verification Queries

### Verify Column Exists

```sql
-- Check column exists in agents table
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'agents' AND column_name = 'api_key_expires_at';
```

### Verify Index Exists

```sql
-- Check index exists
SELECT indexname, indexdef
FROM pg_indexes
WHERE tablename = 'agents' AND indexname = 'idx_agents_api_key_expires_at';
```

### Sample Data Check

```sql
-- View agents with expiration
SELECT name, api_key_expires_at,
       CASE
         WHEN api_key_expires_at < NOW() THEN 'EXPIRED'
         WHEN api_key_expires_at < NOW() + INTERVAL '7 days' THEN 'EXPIRING_SOON'
         ELSE 'ACTIVE'
       END as status
FROM agents
WHERE api_key_expires_at IS NOT NULL
ORDER BY api_key_expires_at ASC;
```

---

## Summary

### Implementation Complete

The `api_key_expires_at` feature has been fully implemented with:

- ✅ Database schema (Migration 002)
- ✅ SQLAlchemy model integration
- ✅ API endpoint support (registration, regeneration)
- ✅ Indexing for efficient queries
- ✅ Related rotation infrastructure (Migration 003)

### No Alternative Implementation Needed

The chosen approach (simple nullable column with index) is:
- Simple and maintainable
- Performant (indexed queries)
- Flexible (nullable for non-expiring keys)
- Production-ready

### Related Documentation

- **API Key Rotation Strategies:** `docs/alternatives/api-key-rotation-strategies-research-bd35r.md`
- **SealedSecret Rotation Design:** `docs/sealedsecret-rotation-design.md`
- **Agent Registration Guide:** `docs/agent-registration-guide.md`

---

## Recommendation

**CLOSE bead bd-3sh** - The original implementation (bd-2br) is complete and production-ready. No alternative approach is needed.

### Next Steps (If Enhancement Desired)

1. **Monitoring**: Add alerts for agents with expiring keys
2. **Automation**: Implement scheduled rotation cron job
3. **Dashboard**: Admin UI for viewing expiration status
4. **Metrics**: Prometheus metrics for key rotation events

---

**Document Status:** Research Complete - Implementation Verified
**Bead bd-3sh:** Ready for closure (original implementation complete)
**Bead bd-2br:** CLOSED - Implementation complete

---

## References

- Migration: `hub/database/migrations/002_add_api_key_expiration.sql`
- Model: `hub/database/__init__.py:57-60`
- API: `hub/api/v1/agents.py:40, 97-100`
- Related: `docs/alternatives/api-key-rotation-strategies-research-bd35r.md`
