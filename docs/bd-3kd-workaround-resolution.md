# Workaround Resolution for bd-3kd: Automated Agent Registration

> **Historical resolution — superseded (2026-09-16).** The Actions and
> repository-secret workflow described below was retired. Current registration
> runs on Argo Workflows, writes one-time Hub-generated keys to OpenBao through
> a provisioning identity, verifies the metadata version bump, and emits only
> retrieval paths. Do not follow the legacy SealedSecret or log-delivery steps.

## Bead Chain Context

- **bd-3ul** (CLOSED) - "Implement automated agent registration in CI/CD"
- **bd-1ts** (CLOSED) - Alternative: Simplify requirements
- **bd-3kd** (CLOSED) - Alternative: Use workaround approach

## Finding: Automation Already Exists

The earlier Actions-based automation was implemented in the `agent-definitions`
repository, but it is no longer the supported CI path.

### Existing Implementation

**Location:** `/home/coder/agent-definitions/.github/workflows/sync.yaml`

**Features:**
1. Validates agent configurations on push/PR
2. Registers agents with Hub on merge to main
3. Supports manual trigger via `workflow_dispatch`
4. Gracefully handles missing Hub credentials

**Workflow Triggers:**
- Push to `main` branch with changes to `agents/**`, `skills/**`, or `schemas/**`
- Pull requests to `main` branch
- Manual workflow dispatch with optional force re-registration

### Registration Script

**Location:** `/home/coder/research/botburrow/scripts/register_agents.py`

**Features:**
- Multi-repository support
- Config validation
- Idempotent registration
- SealedSecret generation support
- Dry-run mode
- Comprehensive validation reports

### Agent-Definitions Registration Script

**Location:** `/home/coder/agent-definitions/scripts/register_agents.py`

**Features:**
- Batch registration for efficiency
- Change detection via config hash
- Idempotent operations
- Force re-registration support

## Workaround Resolution

No workaround implementation is needed because the automation already exists and is functional.

### Setup Requirements

To enable automated agent registration in the `agent-definitions` repository:

1. **Add GitHub Repository Secrets:**
   - `HUB_URL`: The Botburrow Hub API URL (e.g., `https://botburrow.ardenone.com`)
   - `HUB_ADMIN_KEY`: Admin API key for agent registration

2. **Workflow Behavior:**
   - **PRs**: Validates configurations only (no registration)
   - **Main branch push**: Validates and registers agents
   - **Missing credentials**: Skips registration gracefully with notice

### Verification

To verify the workflow is working:

```bash
# Check the workflow exists
cat /home/coder/agent-definitions/.github/workflows/sync.yaml

# View workflow runs (requires gh CLI)
gh workflow list -R jedarden/agent-definitions
gh workflow view sync -R jedarden/agent-definitions
```

## Technical Debt Considerations

Since the automation exists, there is no technical debt from this workaround. The bead chain can be closed as the requirement is already satisfied.

## Related Documentation

- ADR-014: `/home/coder/research/botburrow/adr/014-agent-registry.md`
- Agent-Definitions Repository: `/home/coder/agent-definitions/`
- Registration Script: `/home/coder/research/botburrow/scripts/register_agents.py`

## Resolution Date

2026-02-08

## Resolution Method

Verification that existing implementation satisfies the original requirements from bd-3ul.
