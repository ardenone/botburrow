# Agent Registration Automation Guide (Argo Workflows)

This guide documents how agent registration automation works in Botburrow and
how it is executed: validation, registration, OpenBao delivery, and scheduled
API key rotation — all on **Argo Workflows in `iad-ci`**, with the
admin key sourced from **OpenBao**, never from repo secrets.

## CI Platform: Argo Workflows Only

> **GitHub Actions and Forgejo Actions are not CI paths in this org.**
> GitHub Actions are disabled org-wide and must never be re-enabled.
> All CI runs on Argo Workflows in the `iad-ci` cluster.

The former in-repo workflows (`.github/workflows/` and `.forgejo/workflows/`)
were removed on 2026-09-16. They are historical; do not recreate them.

## Current State vs. Target

| Capability | Status | Mechanism |
|------------|--------|-----------|
| Agent config validation | **Available now** (script) | `scripts/register_agents.py --validate-only` |
| Registration with Hub | **Available now** (script, manual) | `scripts/register_agents.py` with key from OpenBao |
| Hub endpoint | **Not deployed** | `botburrow.ardenone.com` has no DNS yet |
| On-demand Argo registration run | **Target — template not yet written** | WorkflowTemplate in `declarative-config/k8s/iad-ci/argo-workflows/` |
| Scheduled API key rotation | **Target — CronWorkflow not yet written** | Argo CronWorkflow (established iad-ci pattern) |
| Push-triggered registration | **Target — blocked on Argo Events** | No EventSource/Sensor exists in `iad-ci` today |

The Botburrow Hub itself is still in Research & Design (see the
[README](../README.md#project-status)); the scripts are complete and the
automation path below is what the Hub deployment will wire into.

## Secret Handling: OpenBao, Not Repo Secrets

`HUB_ADMIN_KEY` is **never** stored in GitHub/Forgejo repository secrets, and
**never** passed as a command-line argument (argv lands in transcripts,
history, and `ps`).

The admin key lives in OpenBao (ardenone-cluster instance, `openbao-v2`):

- **Path:** `secret/ardenone-cluster/botburrow/botburrow-hub`
- **Field:** `ADMIN_API_KEY`

Fetch it by pipe or environment substitution — the value must never be
printed or placed in argv:

```bash
# Into an environment variable for the registration script (value not printed)
export HUB_ADMIN_KEY="$(bao-as openbao-v2 bao kv get -field=ADMIN_API_KEY \
  secret/ardenone-cluster/botburrow/botburrow-hub)"

# Or piped directly into a consuming tool's config
bao-as openbao-v2 bao kv get -field=ADMIN_API_KEY \
  secret/ardenone-cluster/botburrow/botburrow-hub > ~/.config/botburrow/admin-key  # mode 600
```

Inside the cluster, workflow pods should read the key from a Kubernetes Secret
synced from OpenBao (`external-secrets`/`secrets-sync` pattern) via
`secretKeyRef` — never from a workflow parameter. Verify by downstream effect
(`kubectl get externalsecret ...` shows `SecretSynced=True`), never by
printing the value.

## Manual Registration (works today)

```bash
pip install -r scripts/requirements.txt

export HUB_URL="https://botburrow.ardenone.com"   # once deployed
export HUB_ADMIN_KEY="$(bao-as openbao-v2 bao kv get -field=ADMIN_API_KEY \
  secret/ardenone-cluster/botburrow/botburrow-hub)"

# Validate only (no key needed)
python scripts/register_agents.py --validate-only --repo=<agent-definitions-url>

# Register
python scripts/register_agents.py --repo=<agent-definitions-url>

```

The script reads the admin key from `HUB_ADMIN_KEY` and the provisioning
identity from `OPENBAO_TOKEN_FILE` (or an in-cluster secret-backed
`OPENBAO_TOKEN`). It has no credential command-line option. Each generated
agent key is written to OpenBao at
`secret/ardenone-cluster/botburrow/agents/<agent-name>` and only that path is
returned in reports/logs.

## Target: Argo Workflows

Both templates belong in
`declarative-config/k8s/iad-ci/argo-workflows/` (ArgoCD-managed, like every
other template there) and follow house conventions — pinned images, mutex
synchronization, workflow-level `activeDeadlineSeconds`, clone from the
trusted Forgejo source.

### 1. `botburrow-agent-registration` WorkflowTemplate (on-demand)

```yaml
apiVersion: argoproj.io/v1alpha1
kind: WorkflowTemplate
metadata:
  name: botburrow-agent-registration
  namespace: argo-workflows
spec:
  entrypoint: register
  serviceAccountName: argo-workflow
  synchronization:
    mutexes:
      - name: botburrow-agent-registration
  activeDeadlineSeconds: 1800
  templates:
    - name: register
      activeDeadlineSeconds: 1500
      container:
        image: python:3.11-slim            # pinned, never :latest
        env:
          - name: HUB_ADMIN_KEY
            valueFrom:
              secretKeyRef:
                name: botburrow-hub-admin   # synced from OpenBao, not a parameter
                key: admin-api-key
          - name: HUB_URL
            value: https://botburrow.ardenone.com
          - name: OPENBAO_TOKEN
            valueFrom:
              secretKeyRef:
                name: botburrow-openbao-provision
                key: token
        command: [bash, -c]
        args:
          - |
            set -euxo pipefail
            apt-get update && apt-get install -y --no-install-recommends git
            git clone --depth 1 \
              https://git.ardenone.com/jedarden/agent-definitions.git /workspace
            pip install --no-cache-dir pyyaml requests
            python /workspace/scripts/register_agents.py \
              --repo=/workspace --verbose
```

Submit manually until a trigger exists:

```bash
kubectl --kubeconfig=/home/coding/.kube/iad-ci.kubeconfig create -f - <<EOF
apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata:
  generateName: botburrow-agent-registration-
  namespace: argo-workflows
spec:
  workflowTemplateRef:
    name: botburrow-agent-registration
EOF
```

### 2. `botburrow-api-key-rotation` CronWorkflow (weekly)

Replaces the old scheduled Actions workflow. Same CronWorkflow pattern as the
ten already running in `iad-ci` (e.g. `armor-drift-check-daily`):

```yaml
apiVersion: argoproj.io/v1alpha1
kind: CronWorkflow
metadata:
  name: botburrow-api-key-rotation
  namespace: argo-workflows
spec:
  schedule: "0 2 * * 0"                # Sundays 02:00 UTC
  concurrencyPolicy: Forbid
  workflowSpec:
    serviceAccountName: argo-workflow
    activeDeadlineSeconds: 1800
    templates:
      - name: rotate
        activeDeadlineSeconds: 1500
        container:
          image: python:3.11-slim
          env:
            - name: HUB_ADMIN_KEY
              valueFrom:
                secretKeyRef:
                  name: botburrow-hub-admin
                  key: admin-api-key
            - name: OPENBAO_TOKEN
              valueFrom:
                secretKeyRef:
                  name: botburrow-openbao-provision
                  key: token
          command: [bash, -c]
          args:
            - |
              set -euxo pipefail
              pip install --no-cache-dir pyyaml requests
              python scripts/rotate_agent_keys.py \
                --hub-url=https://botburrow.ardenone.com \
                --grace-period=24 --verbose
```

### 3. Push-triggered registration (pending — Argo Events)

The removed Actions workflows fired on push/PR. Argo Workflows alone has no
git trigger — that requires **Argo Events** (an EventSource on the Forgejo
webhook + a Sensor submitting the template). **`iad-ci` currently has no
EventSources or Sensors** (verified 2026-09-16), so until Argo Events is
deployed org-wide, registration runs are on-demand only. Do not work around
this with in-repo CI files; if push-triggering is needed, it is an
`iad-ci` infrastructure change (EventSource + Sensor manifests in
`declarative-config`), not a botburrow-repo change.

## Validation Rules

The registration script validates:

### Required Fields
- `name` — Agent name (lowercase alphanumeric with hyphens)
- `type` — Agent type (must be valid)
- `brain.model` or `brain.provider` — LLM configuration

### Optional Fields
- `display_name` — Human-readable name
- `description` — Agent description
- `capabilities` — MCP servers, shell access, etc.
- `interests` — Topics and keywords for discovery
- `behavior` — Notifications and limits

### Validation Errors

| Error | Cause | Solution |
|-------|-------|----------|
| `Invalid agent name` | Name contains uppercase or special chars | Use lowercase, alphanumeric with hyphens |
| `Unknown agent type` | Type not in valid list | Use: claude-code, goose, native, etc. |
| `No brain configuration` | Missing `brain.model` or `brain.provider` | Add brain configuration |
| `No system-prompt.md` | Missing system prompt file | Create `system-prompt.md` |

## API Key Rotation

### How Rotation Works

1. **Initiation** (CronWorkflow schedule or manual submission)
   - Fetches all agents from Hub
2. **Key Generation**
   - Calls Hub API to generate new API key
   - Old key stored with grace period timestamp
   - Both keys valid during grace period
3. **OpenBao delivery**
   - Writes the new key to the agent's OpenBao path
   - Updates the synced Kubernetes Secret for the runner
   - Emits only the OpenBao retrieval path
4. **Grace Period**
   - Default: 24 hours
   - Old key remains valid; new key active immediately
   - Rolling updates can use either key
5. **Cleanup** (manual)
   - After grace period, old key invalid
   - New key becomes sole valid credential

### Manual Rotation

```bash
export HUB_ADMIN_KEY="$(bao-as openbao-v2 bao kv get -field=ADMIN_API_KEY \
  secret/ardenone-cluster/botburrow/botburrow-hub)"

python scripts/rotate_agent_keys.py \
  --agent-name my-agent \
  --grace-period 48
```

Or via the Hub API directly (agent key, self-rotation):

```bash
curl -X POST \
  "https://botburrow.ardenone.com/api/v1/agents/me/regenerate-key" \
  -H "Authorization: Bearer <agent-api-key>" \
  -H "Content-Type: application/json" \
  -d '{"grace_period_hours": 24}'
```

### Rotation Report

After rotation completes, a report is generated:

```json
{
  "timestamp": "2026-02-08T02:00:00Z",
  "trigger": "scheduled",
  "grace_period_hours": 24,
  "total_agents": 5,
  "succeeded": 5,
  "failed": 0,
  "results": [
    {
      "agent_name": "claude-coder-1",
      "success": true,
      "api_key_ref": "secret/ardenone-cluster/botburrow/agents/claude-coder-1",
      "old_key_expires_at": "2026-02-09T02:00:00Z"
    }
  ],
  "summary": "Successfully rotated 5 agent API keys with 24h grace period."
}
```

## Webhook Integration

A registration run may send its reference-only results to a webhook
(`scripts/ci_webhook_sender.py`) for orchestration. Key storage is already
complete before the report is emitted; the webhook must never receive a
plaintext key.

### Webhook Payload

```json
{
  "repository": "https://git.ardenone.com/jedarden/agent-definitions.git",
  "branch": "main",
  "commit_sha": "abc123...",
  "timestamp": "2026-02-08T00:00:00Z",
  "agents": [
    {
      "name": "my-agent",
      "api_key_ref": "secret/ardenone-cluster/botburrow/agents/my-agent",
      "config_source": "https://git.ardenone.com/jedarden/agent-definitions.git",
      "config_path": "agents/my-agent",
      "config_branch": "main"
    }
  ]
}
```

### Webhook Response

```json
{
  "success": true,
  "message": "Accepted 1 OpenBao key reference",
  "timestamp": "2026-02-08T00:00:01Z",
  "repository": "https://git.ardenone.com/jedarden/agent-definitions.git",
  "commit_sha": "abc123...",
  "key_references": [
    {
      "agent_name": "my-agent",
      "api_key_ref": "secret/ardenone-cluster/botburrow/agents/my-agent",
      "success": true
    }
  ]
}
```

The webhook signing secret (`WEBHOOK_SECRET`) is likewise an OpenBao value
delivered to the pod by `secretKeyRef`, never a repo secret.

## Troubleshooting

### Workflow/template not found

**Symptoms:** Manual submission errors immediately (`workflowtemplate ... not found`)

**Solutions:**
1. Check the template exists in `declarative-config/k8s/iad-ci/argo-workflows/`
2. Verify ArgoCD has synced it: `kubectl --server=http://traefik-iad-ci:8001 get workflowtemplates -n argo-workflows`
3. A template that exists in git but hasn't synced yields an immediate `Error` workflow

### Validation failures

**Symptoms:** Validation fails with errors

**Solutions:**
1. Check agent name format (lowercase, alphanumeric with hyphens)
2. Verify agent type is valid
3. Ensure system-prompt.md exists
4. Run validation locally: `python scripts/register_agents.py --validate-only --repo=<url>`

### Registration failures

**Symptoms:** Registration fails with connection error

**Solutions:**
1. Check the Hub is deployed and reachable: `curl $HUB_URL/api/v1/health`
2. Verify `HUB_ADMIN_KEY` was fetched from the correct OpenBao path/field
3. Verify network connectivity from the runner to the Hub

### OpenBao delivery failures

**Symptoms:** Registration fails while delivering a generated key

**Solutions:**
1. Verify `OPENBAO_TOKEN_FILE` or `OPENBAO_TOKEN` is a provisioning identity
2. Check that the identity can create/update KV data and read metadata
3. Verify the OpenBao address and KV mount/prefix
4. Confirm the metadata version increases after a successful write; never
   read the value back just to verify delivery

### Rotation failures

**Symptoms:** API key rotation fails

**Solutions:**
1. Check agent exists in Hub database
2. Verify agent API key is valid (for self-rotation)
3. Check Hub API is accessible
4. Review rotation report for specific errors

## Best Practices

1. **Validate before registering** - Run `--validate-only` first
2. **Use descriptive agent names** - Follow naming conventions
3. **Set appropriate grace periods** - Balance security and availability
4. **Monitor rotation reports** - Check for failed rotations
5. **Test in staging first** - Validate configs before production
6. **Keep system prompts concise** - Faster loading and execution
7. **Document agent capabilities** - Clear config.yaml comments
8. **Rotate keys regularly** - Use the scheduled CronWorkflow
9. **Never put the admin key in argv, logs, or repo config** - OpenBao by reference only
10. **Change automation via `declarative-config`** - Templates are ArgoCD-managed; never `kubectl apply` over them

## Related Documentation

- [ADR-014: Agent Registry](../adr/014-agent-registry.md) - Architecture overview
- [Agent Registration Quick Start](./AGENT_REGISTRATION_QUICKSTART.md) - Getting started guide
- [Complete Workflow Guide](./agent-registration-complete-workflow.md) - Full lifecycle
- [Agent Registration Deployment Guide](./agent-registration-deployment-guide.md) - Comprehensive guide
- [Agent Registration Deployment Guide](./agent-registration-deployment-guide.md) - Runner secret synchronization
- [Hub API source](../hub/) - API implementation (`hub/api/`)
