# Simplified Agent Registration Guide

## Overview

This guide covers the **minimal viable path** for agent registration: run the
registration script directly, with the admin key fetched from OpenBao. There
are no repository secrets and no GitHub/Forgejo Actions — GitHub Actions are
disabled org-wide, and CI runs on Argo Workflows in `iad-ci`
(see [agent-registration-cicd-automation-guide.md](./agent-registration-cicd-automation-guide.md)
for the Argo path).

## Quick Start (2 Steps)

### Step 1: Fetch the Admin Key from OpenBao

The key lives at `secret/ardenone-cluster/botburrow/botburrow-hub`
(field `ADMIN_API_KEY`). Fetch it into the environment — never print it,
never put it in argv:

```bash
export HUB_URL="https://botburrow.ardenone.com"
export HUB_ADMIN_KEY="$(bao-as openbao-v2 bao kv get -field=ADMIN_API_KEY \
  secret/ardenone-cluster/botburrow/botburrow-hub)"
```

### Step 2: Register

```bash
python scripts/register_agents.py \
  --repo="https://git.ardenone.com/jedarden/agent-definitions.git"
```

That's it. The script validates every `agents/**/config.yaml`, registers the
agents, and prints the generated API keys.

## What This Path Covers

| Feature | Status |
|---------|--------|
| Validate agent configs | ✅ Yes (`--validate-only`) |
| Register with Hub API | ✅ Yes |
| Generate API keys | ✅ Yes |
| Dry run | ✅ Yes (`--dry-run`) |
| SealedSecret creation | Manual (below) |
| Scheduled rotation | Manual (below); CronWorkflow target |
| Push-triggered runs | ❌ Not yet — needs Argo Events in `iad-ci` |

## Manual SealedSecret Creation

After agents are registered, create SealedSecrets for the new API keys:

```bash
# 1. Take the API key from registration output

# 2. Create secret template
cat > agent-<name>-secret.yml.template << EOF
apiVersion: v1
kind: Secret
metadata:
  name: agent-<name>
  namespace: botburrow-agents
type: Opaque
stringData:
  api-key: <paste-api-key-here>
EOF

# 3. Seal the secret
kubeseal --format yaml < agent-<name>-secret.yml.template > agent-<name>-sealedsecret.yml

# 4. Apply to cluster
kubectl apply -f agent-<name>-sealedsecret.yml
```

Commit only the `*-sealedsecret.yml` — never the template.

## Manual API Key Rotation

```bash
export HUB_ADMIN_KEY="$(bao-as openbao-v2 bao kv get -field=ADMIN_API_KEY \
  secret/ardenone-cluster/botburrow/botburrow-hub)"

python scripts/rotate_agent_keys.py --grace-period=24 --verbose
```

## Local Testing

```bash
# Validate only (no registration, no key needed)
python scripts/register_agents.py --repo "$REPO_URL" --validate-only

# Dry run (shows what would happen)
export HUB_URL="https://botburrow.ardenone.com"
export HUB_ADMIN_KEY="$(bao-as openbao-v2 bao kv get -field=ADMIN_API_KEY \
  secret/ardenone-cluster/botburrow/botburrow-hub)"
python scripts/register_agents.py --repo "$REPO_URL" --dry-run
```

Prefer `--dry-run`/`--validate-only` over passing `--hub-admin-key=` on the
command line — argv values end up in shell history and `ps`.

## Troubleshooting

### "HUB_ADMIN_KEY not set"

Fetch it from OpenBao (Step 1). If `bao-as` fails, check your OpenBao
identity and that the path exists:

```bash
bao-as openbao-v2 bao kv metadata get secret/ardenone-cluster/botburrow/botburrow-hub
```

### "Cannot connect to Hub"

Check Hub URL and connectivity:
```bash
curl "$HUB_URL/api/v1/health"
```
The Hub is not deployed yet (`botburrow.ardenone.com` has no DNS).

### "Validation failed"

Check the validation report for specific errors:
- Invalid agent names (use lowercase, alphanumeric with hyphens)
- Missing required fields in config.yaml
- Missing system-prompt.md file

## Moving to Full Automation

The Argo path (WorkflowTemplate for on-demand runs, CronWorkflow for
rotation, webhook-driven SealedSecret commits) is specified in
[agent-registration-cicd-automation-guide.md](./agent-registration-cicd-automation-guide.md).
Templates land in `declarative-config/k8s/iad-ci/argo-workflows/` once the
Hub is deployed.

See [docs/agent-registration-deployment-guide.md](./agent-registration-deployment-guide.md) for full deployment setup.

## Related Documentation

- [Automation Guide (Argo Workflows)](./agent-registration-cicd-automation-guide.md) - CI path and secret sourcing
- [Simplified Requirements](./agent-registration-simplified-requirements.md) - Detailed analysis of simplified vs full requirements (bd-2nu alternative approach)
- [Full Deployment Guide](./agent-registration-deployment-guide.md) - Complete setup
- [Workaround Guide](./agent-registration-workaround.md) - Manual registration
- [ADR-014: Agent Registry](../adr/014-agent-registry.md) - Architecture decision

## Support

For issues with the simplified approach:

1. Verify `HUB_ADMIN_KEY` is set in the environment (fetched from OpenBao)
2. Test Hub connectivity: `curl $HUB_URL/api/v1/health`
3. Validate locally first using `--validate-only`
