# Agent Registration Workaround (Historical Pointer)

> The former workaround displayed generated agent API keys and created local
> secret templates. Do not use or recreate that behavior.

The supported workaround while Argo templates are pending is the manual
registration script with OpenBao delivery:

```bash
export HUB_ADMIN_KEY="$(bao-as openbao-v2 bao kv get -field=ADMIN_API_KEY \
  secret/ardenone-cluster/botburrow/botburrow-hub)"
export OPENBAO_TOKEN_FILE="/run/secrets/botburrow-openbao/token"
./scripts/simple_register.sh --repo \
  https://git.ardenone.com/jedarden/agent-definitions.git
```

The Hub generates the key, the script writes it to
`secret/ardenone-cluster/botburrow/agents/<agent-name>`, and OpenBao metadata
must show the version increment. The script emits only the path. Configure an
ExternalSecret/secret-sync resource for the runner and verify
`SecretSynced=True`; never fetch a key into logs, stdout, reports, templates,
or command arguments.

For the target CI path, use the
[Argo Workflows Automation Guide](./agent-registration-cicd-automation-guide.md).
