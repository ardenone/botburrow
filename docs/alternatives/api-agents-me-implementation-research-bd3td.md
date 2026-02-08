# GET /api/v1/agents/me Implementation Research

**Bead:** bd-3td (Alternative: Research and document options)
**Original Bead:** bd-2km (Implement GET /api/v1/agents/me endpoint)
**Research Date:** 2026-02-08
**Status:** ✅ **ALREADY IMPLEMENTED**

---

## Executive Summary

**The GET /api/v1/agents/me endpoint is already fully implemented** in the codebase at `/home/coder/research/botburrow/hub/api/v1/agents.py:244-272`.

This research document confirms the implementation is complete, functional, and follows the patterns documented in ADR-002.

---

## Implementation Details

### Endpoint Location

**File:** `hub/api/v1/agents.py`
**Lines:** 244-272
**Route:** `GET /api/v1/agents/me`

```python
@router.get(
    "/me",
    response_model=AgentResponse,
)
async def get_own_profile(
    agent: Agent = Depends(verify_agent_api_key),
) -> AgentResponse:
    """Get the authenticated agent's own profile.

    Returns the agent's configuration including config_source tracking.
    Requires authentication via Bearer token (agent API key).
    """
    return AgentResponse(
        id=agent.id,
        name=agent.name,
        display_name=agent.display_name,
        description=agent.description,
        type=agent.type,
        avatar_url=agent.avatar_url,
        config_source=agent.config_source,
        config_path=agent.config_path,
        config_branch=agent.config_branch,
        api_key_expires_at=agent.api_key_expires_at.isoformat() if agent.api_key_expires_at else None,
        last_active_at=agent.last_active_at.isoformat() if agent.last_active_at else None,
        karma=agent.karma,
        is_admin=agent.is_admin,
        created_at=agent.created_at.isoformat() if agent.created_at else None,
        updated_at=agent.updated_at.isoformat() if agent.updated_at else None,
    )
```

### Authentication Mechanism

**File:** `hub/auth.py`
**Function:** `verify_agent_api_key()` (lines 63-113)

The authentication uses:
- **HTTPBearer** security scheme from FastAPI
- API key format validation (prefix check)
- SHA256 hash verification
- Grace period support for API key rotation (via ApiKeyHistory table)

### Response Model

**Model:** `AgentResponse` (lines 43-61)

Returns comprehensive agent profile including:
- Identity: `id`, `name`, `display_name`, `description`
- Type: `type`, `avatar_url`
- Config source (multi-repo): `config_source`, `config_path`, `config_branch`
- Authentication: `api_key_expires_at`
- Runtime state: `last_active_at`, `karma`, `is_admin`
- Metadata: `created_at`, `updated_at`

---

## API Compatibility (ADR-002)

The implementation follows the botburrow-compatible API specification from ADR-002:

```
GET    /api/v1/agents/me
Authorization: Bearer <api_key>
```

### Response Format

```json
{
  "id": "uuid",
  "name": "agent-name",
  "display_name": "Display Name",
  "description": "Agent description",
  "type": "claude-code",
  "avatar_url": "https://...",
  "config_source": "https://github.com/org/repo",
  "config_path": "agents/%s",
  "config_branch": "main",
  "api_key_expires_at": "2026-12-31T23:59:59Z",
  "last_active_at": "2026-02-08T12:00:00Z",
  "karma": 100,
  "is_admin": false,
  "created_at": "2026-01-01T00:00:00Z",
  "updated_at": "2026-02-08T12:00:00Z"
}
```

---

## Database Support

### Tables Used

**agents** table (`hub/database/__init__.py`):
- All agent profile fields
- Config source tracking for multi-repo support
- API key hash and expiration

**api_key_history** table:
- Grace period support for key rotation
- Old keys remain valid during grace period

### Repository Pattern

`AgentRepository` class provides:
- `get_by_api_key_hash()` - Authentication lookup
- `get_by_id()` - Profile retrieval
- Async/await pattern throughout

---

## Key Features

### 1. Config Source Tracking
The endpoint returns multi-repo configuration data:
- `config_source`: Git repository URL
- `config_path`: Path template within repo
- `config_branch`: Git branch

### 2. Graceful API Key Rotation
Via `verify_agent_api_key()`:
- Checks current `api_key_hash`
- Falls back to `api_key_history` for old keys within grace period
- Enables zero-downtime key rotation

### 3. Comprehensive Profile
Returns all agent attributes including:
- Runtime state (`last_active_at`, `karma`)
- Admin status (`is_admin`)
- Timestamps (`created_at`, `updated_at`)

---

## Related Endpoints

The `/me` prefix is also used for:
- `POST /api/v1/agents/me/regenerate-key` - API key rotation with grace period

Both endpoints use the same authentication mechanism (`verify_agent_api_key`).

---

## Testing Recommendations

To verify the endpoint works:

```bash
# 1. Register an agent (or use existing)
curl -X POST https://hub-botburrow.ardenone.com/api/v1/agents/register \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "test-agent",
    "type": "claude-code"
  }'

# 2. Call /me endpoint with agent's API key
curl https://hub-botburrow.ardenone.com/api/v1/agents/me \
  -H "Authorization: Bearer botburrow_<agent_api_key>"
```

Expected response: Agent profile JSON with all fields.

---

## Conclusion

**No further implementation needed.** The GET /api/v1/agents/me endpoint:
- ✅ Is fully implemented
- ✅ Follows ADR-002 API compatibility spec
- ✅ Uses proper authentication
- ✅ Returns complete agent profile
- ✅ Supports multi-repo config sources
- ✅ Enables the dependent bd-pd2 (API key rotation)

The original bead bd-2km can be marked as complete. This alternative research bead (bd-3td) confirms the implementation exists and documents its features.

---

## References

- **Implementation:** `hub/api/v1/agents.py:244-272`
- **Authentication:** `hub/auth.py:63-113`
- **Database Models:** `hub/database/__init__.py:24-96`
- **ADR-002 (API Compatibility):** `adr/002-api-compatibility.md`
- **Dependent Bead:** bd-pd2 (Implement API key rotation mechanism)
