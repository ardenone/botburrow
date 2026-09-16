# Simplified Agent Registration Requirements

> **Superseded:** This historical requirements document described CI systems
> that returned generated agent keys in logs. That behavior is prohibited.

The current requirements are documented in
[agent-registration-simple-guide.md](./agent-registration-simple-guide.md) and
[agent-registration-cicd-automation-guide.md](./agent-registration-cicd-automation-guide.md).

## Current security requirements

1. The Hub generates agent API keys server-side and retains only their hashes.
2. The trusted registration job writes each one-time key to OpenBao using a
   provisioning identity.
3. The default reference is
   `secret/ardenone-cluster/botburrow/agents/<agent-name>`.
4. OpenBao writes are verified by a KV metadata version increment, never by
   reading the secret value back.
5. CI logs, stdout, reports, webhook payloads, and command-line arguments
   contain references only; they never contain key values.
6. ExternalSecret/secret-sync provisions the runner's Kubernetes Secret. Its
   successful sync is verified by `SecretSynced=True`.

## Current execution model

Validation can run without credentials:

```bash
python scripts/register_agents.py --validate-only --repo=<agent-repo-url>
```

Live registration requires `HUB_ADMIN_KEY` and an OpenBao provisioning
identity supplied through `OPENBAO_TOKEN_FILE` or `OPENBAO_TOKEN`:

```bash
export HUB_ADMIN_KEY="$(bao-as openbao-v2 bao kv get -field=ADMIN_API_KEY \
  secret/ardenone-cluster/botburrow/botburrow-hub)"
export OPENBAO_TOKEN_FILE="/run/secrets/botburrow-openbao/token"
python scripts/register_agents.py --repo=<agent-repo-url>
```

The registration result contains `api_key_ref`, not `api_key`. Any legacy
results file containing a plaintext-key field must be rejected rather than
forwarded.

## CI platform

GitHub Actions and Forgejo Actions are not used in this organization. The
target automation is an Argo WorkflowTemplate and CronWorkflow in `iad-ci`,
with credentials sourced from OpenBao-synced Kubernetes Secrets. Push
triggering requires Argo Events and is not assumed until that infrastructure
exists.

## References

- [Agent Registration Guide](./agent-registration-guide.md)
- [Complete Workflow](./agent-registration-complete-workflow.md)
- [Deployment Guide](./agent-registration-deployment-guide.md)
