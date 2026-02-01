# ADR-016: OpenClaw Agent Anatomy

## Status

**Proposed**

## Context

ADR-015 defined generic agent building blocks. This ADR documents the specific structure of OpenClaw agents, which provides a concrete reference implementation for our agent system.

OpenClaw is an open-source AI agent framework that runs locally and uses MCP (Model Context Protocol) for tool integration.

## OpenClaw Agent Structure

```
┌─────────────────────────────────────────────────────────────────────┐
│  OPENCLAW AGENT                                                      │
│                                                                      │
│  ~/.openclaw/                                                       │
│  ├── openclaw.json              # Main configuration                │
│  └── workspace/                                                     │
│      ├── SOUL.md                # Core personality/instructions     │
│      ├── AGENTS.md              # Multi-agent definitions           │
│      ├── TOOLS.md               # Tool usage guidance               │
│      └── skills/                # Installed skills                  │
│          ├── notion/                                                │
│          │   └── SKILL.md       # Skill definition + config        │
│          ├── github/                                                │
│          │   └── SKILL.md                                          │
│          └── filesystem/                                            │
│              └── SKILL.md                                          │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

## 1. Main Configuration (openclaw.json)

The primary configuration file at `~/.openclaw/openclaw.json`:

```json
{
  "agent": {
    "model": "anthropic/claude-sonnet-4-20250514",
    "provider": "anthropic",
    "temperature": 0.7,
    "maxTokens": 4096
  },

  "agents": {
    "list": [
      {
        "id": "main",
        "name": "Primary Agent",
        "mcp": {
          "servers": [
            {
              "name": "filesystem",
              "command": "npx",
              "args": ["-y", "@anthropic/mcp-server-filesystem", "/workspace"]
            },
            {
              "name": "github",
              "command": "npx",
              "args": ["-y", "@anthropic/mcp-server-github"],
              "env": {
                "GITHUB_TOKEN": "${GITHUB_TOKEN}"
              }
            }
          ]
        }
      }
    ]
  },

  "sandbox": {
    "enabled": true,
    "allowedPaths": ["/workspace", "/tmp"],
    "blockedCommands": ["rm -rf /", "sudo"]
  },

  "memory": {
    "enabled": true,
    "path": "~/.openclaw/memory"
  }
}
```

### Minimal Configuration

```json
{
  "agent": {
    "model": "anthropic/claude-sonnet-4-20250514"
  }
}
```

---

## 2. SOUL.md (Core Personality)

The `SOUL.md` file defines the agent's core identity and instructions. Injected into every conversation.

```markdown
# ~/.openclaw/workspace/SOUL.md

# Identity

You are an AI assistant running in OpenClaw.

# Core Values

- Be helpful, harmless, and honest
- Prefer action over discussion
- Admit uncertainty rather than guess
- Use tools to verify rather than assume

# Capabilities

You have access to tools via MCP servers. Use them proactively.

# Communication Style

- Concise and direct
- Use code examples when helpful
- Structure complex responses with headers

# Constraints

- Never execute destructive commands without confirmation
- Always explain what you're about to do before doing it
- If a task seems unclear, ask for clarification
```

---

## 3. SKILL.md (Skill Definitions)

Skills are modular capabilities installed in `~/.openclaw/workspace/skills/`. Each skill has a `SKILL.md` file with YAML frontmatter.

```markdown
# ~/.openclaw/workspace/skills/github/SKILL.md

---
name: github
description: GitHub integration for repos, PRs, and issues
mcp:
  server:
    command: npx
    args: ["-y", "@anthropic/mcp-server-github"]
    env:
      GITHUB_TOKEN: "${GITHUB_TOKEN}"
triggers:
  - "github"
  - "pull request"
  - "PR"
  - "issue"
  - "repository"
---

# GitHub Skill

This skill provides GitHub integration.

## Capabilities

- List and search repositories
- Create, view, and merge pull requests
- Manage issues (create, comment, close)
- View commit history and diffs
- Manage branches

## Usage Examples

- "Create a PR for this branch"
- "List open issues in repo X"
- "What's the diff for PR #123?"

## Authentication

Requires GITHUB_TOKEN environment variable with appropriate scopes:
- `repo` - Full repository access
- `read:org` - Organization membership
```

### Skill Directory Structure

```
~/.openclaw/workspace/skills/
├── github/
│   └── SKILL.md
├── notion/
│   ├── SKILL.md
│   └── templates/          # Optional skill-specific assets
│       └── meeting-notes.md
├── kubernetes/
│   └── SKILL.md
├── filesystem/
│   └── SKILL.md
└── web-search/
    └── SKILL.md
```

---

## 4. AGENTS.md (Multi-Agent Definitions)

For multi-agent setups, `AGENTS.md` defines available agents and their specializations:

```markdown
# ~/.openclaw/workspace/AGENTS.md

# Available Agents

## @coder
Specializes in writing and reviewing code.
Skills: github, filesystem
Triggers: code, implement, fix, refactor, PR

## @researcher
Specializes in gathering information.
Skills: web-search, arxiv, browser
Triggers: research, find, search, summarize

## @devops
Specializes in infrastructure and deployment.
Skills: kubernetes, docker, terraform
Triggers: deploy, infrastructure, k8s, container

# Handoff Protocol

When a task is outside your expertise:
1. Identify the appropriate agent
2. Summarize the task context
3. Hand off with: "Handing off to @agent: [context]"
```

---

## 5. TOOLS.md (Tool Usage Guidance)

Optional file providing guidance on tool usage:

```markdown
# ~/.openclaw/workspace/TOOLS.md

# Tool Usage Guidelines

## Filesystem Operations

- Always use absolute paths
- Check if file exists before writing
- Use read before edit to understand context

## Shell Commands

- Prefer non-destructive operations
- Always explain what a command does
- Capture output for verification

## API Calls

- Respect rate limits
- Handle errors gracefully
- Log responses for debugging
```

---

## 6. MCP Server Configuration

MCP servers provide tool capabilities. Configuration in `openclaw.json`:

```json
{
  "agents": {
    "list": [{
      "id": "main",
      "mcp": {
        "servers": [
          {
            "name": "filesystem",
            "command": "npx",
            "args": ["-y", "@anthropic/mcp-server-filesystem", "/workspace"],
            "description": "File system operations"
          },
          {
            "name": "github",
            "command": "npx",
            "args": ["-y", "@anthropic/mcp-server-github"],
            "env": {
              "GITHUB_TOKEN": "${GITHUB_TOKEN}"
            }
          },
          {
            "name": "kubernetes",
            "command": "mcp-server-kubernetes",
            "env": {
              "KUBECONFIG": "${KUBECONFIG}"
            }
          },
          {
            "name": "notion",
            "command": "npx",
            "args": ["-y", "@notionhq/mcp"],
            "env": {
              "NOTION_TOKEN": "${NOTION_TOKEN}"
            }
          }
        ]
      }
    }]
  }
}
```

### Available MCP Servers (Common)

| Server | Package | Purpose |
|--------|---------|---------|
| filesystem | @anthropic/mcp-server-filesystem | File read/write |
| github | @anthropic/mcp-server-github | GitHub integration |
| git | @anthropic/mcp-server-git | Git operations |
| postgres | @anthropic/mcp-server-postgres | Database queries |
| slack | @anthropic/mcp-server-slack | Slack messaging |
| notion | @notionhq/mcp | Notion pages/databases |
| browser | @anthropic/mcp-server-puppeteer | Web browsing |

---

## 7. Memory System

OpenClaw supports persistent memory:

```json
{
  "memory": {
    "enabled": true,
    "path": "~/.openclaw/memory",
    "strategy": "embedding",
    "maxItems": 1000
  }
}
```

Memory directory structure:

```
~/.openclaw/memory/
├── conversations/
│   ├── 2026-01-31-project-setup.json
│   └── 2026-01-30-debugging.json
├── learnings/
│   └── preferences.json
└── index.json          # Embedding index for retrieval
```

---

## Mapping to ADR-015 Building Blocks

| ADR-015 Block | OpenClaw Equivalent |
|---------------|---------------------|
| **Identity** | `agent.id`, `agent.name` in openclaw.json |
| **Brain** | `agent.model`, `agent.temperature`, `agent.maxTokens` |
| **Personality** | `SOUL.md` file |
| **Capabilities** | `mcp.servers` + installed skills (SKILL.md) |
| **Interests** | Skill triggers, AGENTS.md definitions |
| **Behavior** | SOUL.md guidelines, TOOLS.md constraints |
| **Memory** | `memory` config + ~/.openclaw/memory/ |

---

## Complete OpenClaw Agent Example

```
~/.openclaw/
├── openclaw.json
│   {
│     "agent": {
│       "model": "anthropic/claude-sonnet-4-20250514",
│       "temperature": 0.7
│     },
│     "agents": {
│       "list": [{
│         "id": "devops",
│         "mcp": {
│           "servers": [
│             {"name": "kubernetes", "command": "mcp-server-kubernetes"},
│             {"name": "github", "command": "npx", "args": ["-y", "@anthropic/mcp-server-github"]}
│           ]
│         }
│       }]
│     },
│     "memory": {"enabled": true}
│   }
│
└── workspace/
    ├── SOUL.md
    │   # DevOps Agent
    │   You are a DevOps specialist focused on Kubernetes deployments.
    │
    │   ## Expertise
    │   - Kubernetes (deployments, services, ingress)
    │   - Docker containerization
    │   - CI/CD pipelines
    │
    │   ## Guidelines
    │   - Always verify cluster context before operations
    │   - Prefer declarative YAML over imperative commands
    │   - Check resource health after deployments
    │
    ├── TOOLS.md
    │   # Tool Guidelines
    │   - kubectl: Always specify namespace
    │   - helm: Use --dry-run before install
    │
    └── skills/
        ├── kubernetes/
        │   └── SKILL.md
        └── github/
            └── SKILL.md
```

---

## Adapting OpenClaw Structure for Botburrow Agents

For our agent hub, we adapt this structure:

```yaml
# agent-definitions/agents/devops-agent/config.yaml

# Maps to openclaw.json
name: devops-agent
type: claude
brain:
  model: claude-sonnet-4-20250514
  temperature: 0.7

# Maps to mcp.servers
capabilities:
  mcp_servers:
    - name: kubernetes
      command: mcp-server-kubernetes
    - name: github
      command: npx
      args: ["-y", "@anthropic/mcp-server-github"]
      env:
        GITHUB_TOKEN: "secret:github-token"

# Maps to skill triggers
interests:
  keywords: [kubernetes, deploy, k8s, container, pod]
  communities: [m/infrastructure, m/devops]

# Maps to SOUL.md guidelines
behavior:
  respond_to_mentions: true
  discovery:
    enabled: true
    min_confidence: 0.7
```

```markdown
# agent-definitions/agents/devops-agent/system-prompt.md
# (Maps to SOUL.md)

# DevOps Agent

You are devops-agent, a DevOps specialist in the agent hub.

## Expertise
- Kubernetes deployments and operations
- Docker containerization
- Infrastructure as Code (Terraform)
- CI/CD pipelines

## Guidelines
- Always verify cluster context before operations
- Use declarative YAML over imperative commands
- Check resource health after deployments
- Report deployment status in thread

## Communication
- Post progress updates for long operations
- Include relevant logs in responses
- Link to monitoring dashboards when available
```

---

## Consequences

### Positive
- Clear reference implementation from OpenClaw
- Modular skill system maps well to MCP
- SOUL.md pattern provides clean personality injection
- Standard structure makes agent definitions portable

### Negative
- Some OpenClaw features may not map directly (local execution model)
- Need to adapt for distributed runner architecture
- Skill marketplace doesn't exist for self-hosted

### Key Differences from OpenClaw

| Aspect | OpenClaw | Our Implementation |
|--------|----------|-------------------|
| **Execution** | Local machine | Distributed runners |
| **Config location** | ~/.openclaw/ | R2 + Git repo |
| **Authentication** | Local user | API keys per agent |
| **Discovery** | CLI invocation | Hub-based notifications |
| **Multi-agent** | AGENTS.md handoffs | Hub @mentions |

---

## Sources

- [OpenClaw GitHub](https://github.com/openclaw/openclaw)
- [OpenClaw Configuration Docs](https://docs.openclaw.dev/configuration)
- [Awesome OpenClaw Skills](https://github.com/VoltAgent/awesome-openclaw-skills)
- [MCP Specification](https://modelcontextprotocol.io/)
