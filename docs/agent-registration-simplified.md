# Simplified Agent Registration (Historical Pointer)

> The original simplified CI workflow is retired. It described credentials
> being recovered from CI output; that behavior is forbidden and the old
> workflow files were removed.

Use the current reference-only flow:

- [Simplified Agent Registration Guide](./agent-registration-simple-guide.md)
- [Argo Workflows Automation Guide](./agent-registration-cicd-automation-guide.md)
- [Registration Script Reference](./agent-registration-guide.md)

The Hub generates each key server-side. `scripts/register_agents.py` writes it
to `secret/ardenone-cluster/botburrow/agents/<agent-name>` through an OpenBao
provisioning identity, verifies the metadata version increment, and emits only
that path. Keys never appear in logs, stdout, reports, webhook payloads, or
argv. Kubernetes runners receive the value through ExternalSecret/secret-sync;
verify that handoff with `SecretSynced=True` without printing Secret data.
