# ADR-024: Capability Grants

## Status

**Proposed**

## Context

Botburrow agents are coding CLIs (Claude Code, Goose, Aider) invoked to serve personas. These tools need access to external services:

- `gh` CLI for GitHub operations
- `aws` CLI for cloud resources
- Database connections
- Internal APIs

**Problem**: How do we grant these permissions without exposing credentials to agents?

## Decision

**Capability-based permission system using MCP servers as credential brokers.**

```
┌─────────────────────────────────────────────────────────────────────┐
│  CAPABILITY GRANT FLOW                                              │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  Agent Definition (R2)                                       │    │
│  │                                                               │    │
│  │  capabilities:                                               │    │
│  │    grants:                                                   │    │
│  │      - github:read           # Read repos, PRs, issues      │    │
│  │      - github:write          # Create PRs, comments         │    │
│  │      - aws:s3:read           # Read from specific buckets   │    │
│  │      - postgres:app-db:read  # Query app database           │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                           │                                          │
│                           │ Runner loads config                     │
│                           ▼                                          │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  Runner validates grants against policy                      │    │
│  │                                                               │    │
│  │  "Does claude-coder-1 have github:write?"                   │    │
│  │  → Check agent-permissions ConfigMap                        │    │
│  │  → Yes, approved by admin                                   │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                           │                                          │
│                           │ Start MCP servers with grants           │
│                           ▼                                          │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  MCP Server (github)                                         │    │
│  │                                                               │    │
│  │  Initialized with:                                          │    │
│  │  • Agent identity (from ServiceAccount)                     │    │
│  │  • Granted scopes: [read, write]                            │    │
│  │  • Credential: GitHub PAT (from K8s Secret)                 │    │
│  │                                                               │    │
│  │  Agent calls: mcp.github.create_pr(...)                     │    │
│  │  MCP server:                                                │    │
│  │    1. Verify agent has github:write grant                   │    │
│  │    2. Inject PAT into request                               │    │
│  │    3. Execute GitHub API call                               │    │
│  │    4. Return result (without exposing PAT)                  │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                      │
│  Agent NEVER sees: PAT, AWS keys, database passwords               │
│  Agent ONLY sees: MCP tool responses                                │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Grant Schema

### Agent Definition

```yaml
# agent-definitions/agents/claude-coder-1/config.yaml

name: claude-coder-1
type: claude-code

capabilities:
  grants:
    # GitHub access
    - github:read                    # Read repos, PRs, issues, actions
    - github:write                   # Create/update PRs, issues, comments
    - github:actions                 # Trigger workflows

    # AWS access (scoped to specific resources)
    - aws:s3:read:artifacts-bucket   # Read from artifacts-bucket only
    - aws:s3:write:artifacts-bucket  # Write to artifacts-bucket only

    # Database access
    - postgres:app-db:read           # SELECT only on app-db
    - postgres:analytics:read        # SELECT only on analytics

    # Internal services
    - http:internal-api:*            # Full access to internal API

  # MCP servers to start (subset based on grants)
  mcp_servers:
    - github      # Started because github:* grants exist
    - aws         # Started because aws:* grants exist
    - postgres    # Started because postgres:* grants exist
```

### Permission Policy (Cluster-level)

```yaml
# k8s/apexalgo-iad/agent-permissions.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: agent-permissions
  namespace: botburrow-agents
data:
  permissions.yaml: |
    # Which grants each agent is allowed to request
    agents:
      claude-coder-1:
        allowed_grants:
          - github:*           # Full GitHub access
          - aws:s3:*:artifacts-bucket
          - postgres:app-db:read
        denied_grants:
          - aws:iam:*          # Never IAM access
          - postgres:*:write   # No write to any DB

      research-agent:
        allowed_grants:
          - github:read        # Read-only GitHub
          - http:internal-api:read
        denied_grants:
          - github:write
          - aws:*
          - postgres:*

    # Default for unspecified agents
    default:
      allowed_grants:
        - github:read
      denied_grants:
        - aws:*
        - postgres:*
```

---

## Grant Types

### GitHub (`github:*`)

| Grant | Capabilities |
|-------|--------------|
| `github:read` | List repos, read files, PRs, issues, actions status |
| `github:write` | Create/update PRs, issues, comments, branches |
| `github:actions` | Trigger workflows, cancel runs |
| `github:admin` | Repo settings, webhooks (rarely granted) |

**MCP Server Implementation:**

```python
# mcp-servers/github/server.py

class GitHubMCPServer:
    def __init__(self, agent_id: str, grants: list[str], token: str):
        self.agent_id = agent_id
        self.grants = set(grants)
        self.token = token  # From K8s Secret, never exposed to agent
        self.gh = Github(token)

    def create_pull_request(self, repo: str, title: str, body: str, head: str, base: str):
        # Check grant
        if "github:write" not in self.grants:
            raise PermissionDenied(f"Agent {self.agent_id} lacks github:write grant")

        # Execute with injected credentials
        repository = self.gh.get_repo(repo)
        pr = repository.create_pull(title=title, body=body, head=head, base=base)

        # Return result without exposing token
        return {"number": pr.number, "url": pr.html_url}

    def get_file_contents(self, repo: str, path: str):
        if "github:read" not in self.grants:
            raise PermissionDenied(f"Agent {self.agent_id} lacks github:read grant")

        repository = self.gh.get_repo(repo)
        content = repository.get_contents(path)
        return {"content": content.decoded_content.decode(), "sha": content.sha}
```

### AWS (`aws:*`)

| Grant | Capabilities |
|-------|--------------|
| `aws:s3:read:<bucket>` | GetObject, ListBucket on specific bucket |
| `aws:s3:write:<bucket>` | PutObject, DeleteObject on specific bucket |
| `aws:lambda:invoke:<fn>` | Invoke specific Lambda function |
| `aws:ssm:read` | Read SSM parameters |

**Resource scoping is mandatory** - no wildcards for AWS.

### PostgreSQL (`postgres:*`)

| Grant | Capabilities |
|-------|--------------|
| `postgres:<db>:read` | SELECT on all tables in database |
| `postgres:<db>:write` | INSERT, UPDATE, DELETE |
| `postgres:<db>:ddl` | CREATE, ALTER, DROP (rarely granted) |

**MCP Server uses connection pooling** - agent never sees connection string.

### HTTP (`http:*`)

| Grant | Capabilities |
|-------|--------------|
| `http:<service>:read` | GET requests only |
| `http:<service>:write` | POST, PUT, DELETE |
| `http:<service>:*` | All methods |

For internal services that don't have dedicated MCP servers.

---

## Credential Storage

```yaml
# k8s/apexalgo-iad/secrets/mcp-credentials.yaml
apiVersion: v1
kind: Secret
metadata:
  name: mcp-credentials
  namespace: botburrow-agents
type: Opaque
stringData:
  github-pat: ghp_xxxxxxxxxxxx
  aws-access-key: AKIA...
  aws-secret-key: xxxxxxxx
  postgres-app-db-url: postgres://user:pass@host:5432/app
  postgres-analytics-url: postgres://user:pass@host:5432/analytics
```

**Secrets are:**
- Mounted into MCP server pods only
- Never mounted into agent sandbox containers
- Rotated via External Secrets Operator or similar

---

## Runtime Flow

```python
# runner/activation.py

class AgentActivation:
    async def setup_capabilities(self, agent: AgentConfig) -> list[MCPServer]:
        """Start MCP servers based on agent's granted capabilities."""

        # 1. Load cluster permission policy
        policy = await self.load_permission_policy()

        # 2. Validate agent's requested grants against policy
        validated_grants = []
        for grant in agent.capabilities.grants:
            if policy.is_allowed(agent.name, grant):
                validated_grants.append(grant)
            else:
                self.logger.warning(
                    f"Agent {agent.name} requested {grant} but denied by policy"
                )

        # 3. Determine which MCP servers to start
        servers_needed = self.grants_to_servers(validated_grants)

        # 4. Start MCP servers with appropriate credentials
        mcp_servers = []
        for server_type in servers_needed:
            server = await self.start_mcp_server(
                server_type=server_type,
                agent_id=agent.name,
                grants=[g for g in validated_grants if g.startswith(f"{server_type}:")],
                # Credentials loaded from K8s Secrets
                credentials=await self.get_server_credentials(server_type)
            )
            mcp_servers.append(server)

        return mcp_servers

    def grants_to_servers(self, grants: list[str]) -> set[str]:
        """Map grants to required MCP servers."""
        servers = set()
        for grant in grants:
            prefix = grant.split(":")[0]  # github:write -> github
            servers.add(prefix)
        return servers
```

---

## CLI Tool Mapping

For agents that expect CLI tools like `gh`:

| CLI | MCP Equivalent | Notes |
|-----|----------------|-------|
| `gh pr create` | `mcp.github.create_pull_request()` | Same functionality, no token exposure |
| `gh issue list` | `mcp.github.list_issues()` | |
| `aws s3 cp` | `mcp.aws.s3_get_object()` | |
| `psql -c "SELECT"` | `mcp.postgres.query()` | |
| `curl internal-api` | `mcp.http.request()` | |

**Agent's system prompt includes tool mappings:**

```markdown
## Available Tools

You have access to GitHub via MCP tools (not the `gh` CLI directly):

- `github.create_pull_request(repo, title, body, head, base)` - Create a PR
- `github.list_pull_requests(repo, state)` - List PRs
- `github.get_file(repo, path)` - Read file contents
- `github.create_issue(repo, title, body)` - Create an issue

Do NOT attempt to run `gh` commands directly - use these MCP tools instead.
```

---

## Sandbox Isolation

```yaml
# Agent sandbox has NO credentials mounted
apiVersion: v1
kind: Pod
metadata:
  name: agent-sandbox-abc123
spec:
  serviceAccountName: agent-sandbox  # Minimal permissions
  containers:
    - name: agent
      image: botburrow-sandbox:latest
      env:
        # MCP server addresses only - no credentials
        - name: MCP_GITHUB_URL
          value: "http://localhost:9001"
        - name: MCP_AWS_URL
          value: "http://localhost:9002"
      volumeMounts: []  # No secret mounts!

    # MCP servers run as sidecars with credentials
    - name: mcp-github
      image: botburrow-mcp-github:latest
      env:
        - name: GITHUB_TOKEN
          valueFrom:
            secretKeyRef:
              name: mcp-credentials
              key: github-pat
        - name: AGENT_ID
          value: "claude-coder-1"
        - name: GRANTS
          value: "github:read,github:write"
```

**Key isolation:**
- Agent container: No credentials, only MCP URLs
- MCP sidecars: Have credentials, enforce grants
- Network policy: Agent can only reach MCP sidecars, not external services directly

---

## Adding New Capabilities

To grant access to a new service:

1. **Create MCP server** for the service
2. **Define grant schema** (read/write/admin levels)
3. **Add credentials** to K8s Secrets
4. **Update permission policy** for which agents can request it
5. **Update agent definitions** with new grants

---

## Consequences

### Positive
- **Zero credential exposure** - Agents never see tokens/passwords
- **Fine-grained control** - Scoped grants per agent
- **Audit trail** - MCP servers log all operations
- **Revocation** - Change policy, agents lose access immediately
- **Rotation** - Rotate secrets without touching agent definitions

### Negative
- **MCP server overhead** - Need to maintain server for each integration
- **CLI compatibility** - Agents can't use `gh` directly, need MCP equivalents
- **Latency** - Extra hop through MCP server

### Mitigations
- Use existing MCP servers from community (mcp-server-github, etc.)
- Document tool mappings clearly in agent prompts
- MCP sidecars are local, latency is minimal (~1ms)
