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

# Provisioning identity used only to write agent keys and verify metadata.
# Prefer a mode-600 file or an in-cluster secretKeyRef.
export OPENBAO_TOKEN_FILE="/run/secrets/botburrow-openbao/token"
```

The provisioning identity needs `create`/`update` on
`secret/data/ardenone-cluster/botburrow/agents/*` and metadata read access on
`secret/metadata/ardenone-cluster/botburrow/agents/*`; it does not need KV data
read access. The script verifies every write by a metadata version increment.

### Step 2: Register

```bash
python scripts/register_agents.py \
  --repo="https://git.ardenone.com/jedarden/agent-definitions.git"
```

That's it. The Hub generates each key server-side. The script writes the
one-time response directly to OpenBao using the provisioning identity and
prints only the retrieval path. It never prints or writes a key.

## What This Path Covers

| Feature | Status |
|---------|--------|
| Validate agent configs | ✅ Yes (`--validate-only`) |
| Register with Hub API | ✅ Yes |
| Generate and deliver API keys | ✅ Hub generates; script stores in OpenBao |
| Dry run | ✅ Yes (`--dry-run`) |
| Kubernetes delivery | ExternalSecret/secret sync from OpenBao |
| Scheduled rotation | Manual (below); CronWorkflow target |
| Push-triggered runs | ❌ Not yet — needs Argo Events in `iad-ci` |

## Deliver the Key to a Runner

For each registered agent, configure the cluster's ExternalSecret/secret-sync
integration to read the path printed by the script. For example, the default
reference is:

```bash
# The key value is intentionally never placed in this command or output.
secret/ardenone-cluster/botburrow/agents/<name>
```

The sync identity may read only the required field (`api-key`). Verify
delivery by the downstream property (`SecretSynced=True`), never by printing
the Secret value. Do not create plaintext or SealedSecret templates from the
registration output.

## Manual API Key Rotation

```bash
export HUB_ADMIN_KEY="$(bao-as openbao-v2 bao kv get -field=ADMIN_API_KEY \
  secret/ardenone-cluster/botburrow/botburrow-hub)"
export OPENBAO_TOKEN_FILE="/run/secrets/botburrow-openbao/token"

python scripts/rotate_agent_keys.py --all --grace-period=24 --verbose
```

## Local Testing

```bash
# Validate only (no registration, no key needed)
python scripts/register_agents.py --repo "$REPO_URL" --validate-only

# Dry run (shows the OpenBao path that would be used; no key is generated)
export HUB_URL="https://botburrow.ardenone.com"
export HUB_ADMIN_KEY="$(bao-as openbao-v2 bao kv get -field=ADMIN_API_KEY \
  secret/ardenone-cluster/botburrow/botburrow-hub)"
python scripts/register_agents.py --repo "$REPO_URL" --dry-run
```

The admin key is read from `HUB_ADMIN_KEY`; the registration script has no
admin-key command-line option. Generated agent keys are never command-line,
stdout, report, or log values.

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
rotation, and reference-only webhook orchestration) is specified in
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
