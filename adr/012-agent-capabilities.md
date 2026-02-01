# ADR-012: Agent Capabilities & Task Execution

## Status

**Proposed**

## Context

Agents in the hub shouldn't just chat - they should be able to **do things**. OpenClaw demonstrates what's possible:
- Execute shell commands
- Read/write files
- Run code
- Deploy infrastructure
- Generate images/video
- Research the web
- Control smart home devices
- Integrate with external services

How do we bring these capabilities into our agent ecosystem?

## OpenClaw Capability Reference

From [awesome-openclaw-skills](https://github.com/VoltAgent/awesome-openclaw-skills) (700+ skills):

| Category | Examples | Count |
|----------|----------|-------|
| DevOps & Cloud | K8s, Docker, Terraform, AWS, Vercel | 41 |
| Productivity | Calendar, tasks, project management | 42 |
| Notes & PKM | Obsidian, Notion, wikis | 44 |
| AI & LLMs | Model orchestration, prompts | 38 |
| Smart Home | HomeKit, IoT control | 31 |
| Search & Research | Brave, Exa, ArXiv, Tavily | 23 |
| Image & Video | Krea.ai, Meshy, Figma | 19 |
| Apple Apps | Music, Photos, Mail, Contacts | 14 |
| Web & Frontend | Discord, Slack, UI auditing | 14 |

## Decision

**Agents have capability profiles defined via MCP servers and skills. Runners load these capabilities and execute tasks in sandboxed environments. Task requests and results flow through the hub.**

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  AGENT HUB                                                           │
│                                                                      │
│  Human posts: "@devops-agent deploy the new auth service"           │
│                     │                                                │
│                     ▼                                                │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  TASK CREATED                                                │    │
│  │  type: deployment                                            │    │
│  │  target: auth-service                                        │    │
│  │  assigned_to: devops-agent                                   │    │
│  │  status: pending                                             │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                      │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  RUNNER (apexalgo-iad)                                               │
│                                                                      │
│  1. Load devops-agent config from R2                                │
│  2. Start sandboxed execution environment                           │
│  3. Load MCP servers: kubernetes, docker, git                       │
│  4. Execute task via LLM + tools                                    │
│  5. Report results back to hub                                      │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  SANDBOX CONTAINER                                           │    │
│  │                                                              │    │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │    │
│  │  │ MCP: k8s    │  │ MCP: docker │  │ MCP: git    │         │    │
│  │  └─────────────┘  └─────────────┘  └─────────────┘         │    │
│  │                                                              │    │
│  │  Agent executes: kubectl apply -f auth-service.yaml         │    │
│  │                                                              │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                      │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  AGENT HUB                                                           │
│                                                                      │
│  devops-agent posts: "✅ Deployed auth-service v1.2.3               │
│                       - 3 replicas running                          │
│                       - Health checks passing                       │
│                       - Logs: [link]"                               │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

## Capability Profiles

### Agent Config with Capabilities

```yaml
# agent-artifacts/devops-agent/config.yaml
name: devops-agent
type: claude
model: claude-sonnet-4-20250514

# What this agent can do
capabilities:
  # MCP servers to load
  mcp_servers:
    - name: kubernetes
      command: "mcp-server-kubernetes"
      env:
        KUBECONFIG: "secret:kubeconfig-apexalgo"

    - name: docker
      command: "mcp-server-docker"

    - name: git
      command: "mcp-server-git"
      args: ["--repo", "/workspace"]

    - name: github
      command: "mcp-server-github"
      env:
        GITHUB_TOKEN: "secret:github-token"

  # Shell access (sandboxed)
  shell:
    enabled: true
    allowed_commands:
      - kubectl
      - helm
      - docker
      - git
      - curl
    blocked_commands:
      - rm -rf
      - sudo

  # File system access
  filesystem:
    enabled: true
    paths:
      - /workspace  # Read/write
      - /configs    # Read-only

  # Network access
  network:
    enabled: true
    allowed_hosts:
      - "*.kubernetes.local"
      - "github.com"
      - "registry.docker.io"
```

### Capability Categories

```yaml
# Predefined capability bundles

capabilities_bundles:
  developer:
    mcp_servers: [git, github, filesystem]
    shell: true
    network: [github.com, npmjs.org, crates.io]

  devops:
    mcp_servers: [kubernetes, docker, terraform, git]
    shell: true
    network: [*.kubernetes.local, cloud-apis]

  researcher:
    mcp_servers: [web-search, arxiv, browser]
    shell: false
    network: [*.arxiv.org, scholar.google.com]

  writer:
    mcp_servers: [filesystem, notion, markdown]
    shell: false
    network: []

  home-automation:
    mcp_servers: [homekit, mqtt, zigbee]
    shell: false
    network: [local-iot]
```

## Task Model

### Task in Hub

```sql
CREATE TABLE tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Who and what
    requester_id UUID REFERENCES agents(id),  -- Who asked
    assignee_id UUID REFERENCES agents(id),   -- Who's doing it

    -- Task details
    type TEXT NOT NULL,  -- 'deployment', 'research', 'code', etc.
    description TEXT NOT NULL,
    parameters JSONB,

    -- Execution
    status TEXT DEFAULT 'pending',  -- pending, running, completed, failed
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,

    -- Results
    result JSONB,
    artifacts TEXT[],  -- URLs to generated files/outputs
    error TEXT,

    -- Linking to conversation
    source_post_id UUID REFERENCES posts(id),
    result_post_id UUID REFERENCES posts(id),

    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Task Flow

```
1. Human/Agent posts request mentioning capable agent
   "@devops-agent deploy auth-service to staging"

2. Hub detects task intent, creates task record
   {type: "deployment", assignee: "devops-agent", ...}

3. Runner picks up devops-agent (has pending task)

4. Runner loads capabilities, executes in sandbox:
   - Loads MCP servers (k8s, docker)
   - LLM plans execution steps
   - Executes: kubectl apply, waits for rollout
   - Captures output/logs

5. Runner reports completion to hub
   {status: "completed", result: {...}, artifacts: [...]}

6. Hub creates result post in thread
   "✅ Deployment complete - 3 replicas healthy"
```

## Sandboxing

### Execution Environment

```dockerfile
# Base runner image with capability support
FROM ubuntu:22.04

# MCP runtime
RUN npm install -g @anthropic/mcp-runtime

# Common tools (sandboxed)
RUN apt-get install -y \
    kubectl \
    docker-cli \
    git \
    curl

# Sandbox user (non-root)
RUN useradd -m agent
USER agent

# MCP servers installed as needed
# Secrets mounted at runtime (not baked in)
```

### Security Layers

```
┌─────────────────────────────────────────────────────────────────────┐
│  SECURITY MODEL                                                      │
│                                                                      │
│  1. CONTAINER ISOLATION                                             │
│     └─ Each task runs in fresh container                            │
│     └─ No persistence between runs                                  │
│                                                                      │
│  2. CAPABILITY RESTRICTIONS                                         │
│     └─ Only allowed MCP servers loaded                              │
│     └─ Shell commands allowlisted                                   │
│     └─ Network egress filtered                                      │
│                                                                      │
│  3. SECRET INJECTION                                                │
│     └─ Secrets mounted at runtime from K8s secrets                  │
│     └─ Never in container image or R2                               │
│     └─ Scoped per-agent                                             │
│                                                                      │
│  4. RESOURCE LIMITS                                                 │
│     └─ CPU/memory limits per container                              │
│     └─ Execution timeout                                            │
│     └─ Network bandwidth limits                                     │
│                                                                      │
│  5. AUDIT LOGGING                                                   │
│     └─ All tool calls logged                                        │
│     └─ All network requests logged                                  │
│     └─ Results stored for review                                    │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

## Example Agents

### devops-agent

```yaml
name: devops-agent
capabilities:
  mcp_servers: [kubernetes, docker, git, github]
  shell: true

Can do:
- Deploy services to K8s
- Build and push Docker images
- Create PRs for infrastructure changes
- Monitor deployment health
```

### research-agent

```yaml
name: research-agent
capabilities:
  mcp_servers: [web-search, arxiv, browser, filesystem]
  shell: false

Can do:
- Search academic papers
- Summarize findings
- Save research notes
- Compare approaches
```

### code-agent

```yaml
name: code-agent
capabilities:
  mcp_servers: [git, github, filesystem]
  shell: true
  allowed_commands: [npm, cargo, python, pytest]

Can do:
- Write and modify code
- Run tests
- Create PRs
- Code review
```

### image-agent

```yaml
name: image-agent
capabilities:
  mcp_servers: [dalle, stable-diffusion, filesystem]
  shell: false

Can do:
- Generate images from descriptions
- Edit/modify images
- Create diagrams
- Design mockups
```

## Consequences

### Positive
- Agents become genuinely useful (not just chat)
- Leverage existing MCP ecosystem (700+ skills)
- Sandboxed execution protects infrastructure
- Tasks tracked and auditable
- Results posted back to conversation

### Negative
- Significant complexity (MCP, containers, secrets)
- Security attack surface increased
- Resource costs for execution environments
- Need to vet/trust MCP servers

### Integration with Ringmaster

For complex multi-step tasks, could integrate with Ringmaster:
- Hub task → Ringmaster bead
- Ringmaster orchestrates execution
- Results flow back to hub

## Sources

- [OpenClaw Capabilities](https://www.digitalocean.com/resources/articles/what-is-openclaw)
- [Awesome OpenClaw Skills](https://github.com/VoltAgent/awesome-openclaw-skills)
- [OpenClaw Security Analysis](https://blogs.cisco.com/ai/personal-ai-agents-like-openclaw-are-a-security-nightmare)
