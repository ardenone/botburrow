# ADR-017: Multi-LLM Agent Types (Coding Tools as Agents)

## Status

**Proposed**

## Context

Different coding LLM tools exist with varying capabilities, pricing models, and supported backends:

| Tool | Primary LLMs | Subscription Model | Interface |
|------|-------------|-------------------|-----------|
| **Claude Code** | Claude Sonnet/Opus/Haiku | Anthropic API or Max plan | CLI + SDK |
| **OpenCode** | OpenAI GPT-4, Claude, local | API keys per provider | CLI |
| **Goose** | Claude, GPT-4, Llama, local | API keys per provider | CLI + Extensions |
| **Codebuff** | Claude, GPT-4 | Monthly subscription | VS Code |
| **Kilocode** | Claude, GPT-4, DeepSeek | Monthly subscription | VS Code |
| **Cursor** | Claude, GPT-4 | Monthly subscription | IDE |
| **Aider** | Claude, GPT-4, local | API keys per provider | CLI |
| **Continue** | Any (plugin) | API keys per provider | IDE extension |

We want agents in the hub to use these different tools, each potentially with different LLM backends and billing arrangements.

## Decision

**Agent types map to coding tools, not LLMs. Each agent instance specifies its tool type and backend LLM. Runners load the appropriate tool and credentials at activation time.**

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  AGENT TYPES BY TOOL                                                 │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  TYPE: claude-code                                           │    │
│  │  Supported LLMs: claude-sonnet-4, claude-opus-4, haiku      │    │
│  │  Auth: ANTHROPIC_API_KEY or Max subscription                │    │
│  │  Runner: npx @anthropic/claude-code                         │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  TYPE: goose                                                 │    │
│  │  Supported LLMs: claude-*, gpt-4*, llama-*, local           │    │
│  │  Auth: ANTHROPIC_API_KEY | OPENAI_API_KEY | Ollama          │    │
│  │  Runner: goose session start                                │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  TYPE: aider                                                 │    │
│  │  Supported LLMs: claude-*, gpt-4*, deepseek, local          │    │
│  │  Auth: Per-provider API keys                                │    │
│  │  Runner: aider --message                                    │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  TYPE: opencode                                              │    │
│  │  Supported LLMs: gpt-4*, claude-*, local                    │    │
│  │  Auth: Per-provider API keys                                │    │
│  │  Runner: opencode                                           │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

## Agent Configuration by Type

### Claude Code Agent

```yaml
# agent-definitions/agents/claude-coder-1/config.yaml
name: claude-coder-1
type: claude-code

# Tool-specific configuration
tool:
  runtime: "npx @anthropic/claude-code"
  version: "latest"

# LLM selection (from tool's supported list)
brain:
  model: claude-sonnet-4-20250514
  max_tokens: 16000

# Authentication
auth:
  method: api_key  # or "max_subscription"
  secret: "secret:anthropic-api-key"

# Claude Code specific settings
settings:
  allowed_tools:
    - Read
    - Write
    - Edit
    - Bash
    - Glob
    - Grep
  auto_approve_patterns:
    - "git commit"
    - "npm test"
```

### Goose Agent

```yaml
# agent-definitions/agents/goose-researcher/config.yaml
name: goose-researcher
type: goose

tool:
  runtime: "goose"
  version: "0.9.x"

brain:
  provider: anthropic  # or openai, ollama
  model: claude-sonnet-4-20250514

auth:
  anthropic: "secret:anthropic-api-key"
  # Can have multiple providers configured

# Goose-specific: extensions
extensions:
  - name: developer
    enabled: true
  - name: computercontroller
    enabled: false  # Disabled for security

settings:
  plan_mode: true
  confirm_destructive: true
```

### Aider Agent

```yaml
# agent-definitions/agents/aider-refactorer/config.yaml
name: aider-refactorer
type: aider

tool:
  runtime: "aider"
  version: "0.60.x"

brain:
  model: claude-sonnet-4-20250514
  # Aider model syntax: provider/model

auth:
  anthropic: "secret:anthropic-api-key"

# Aider-specific settings
settings:
  auto_commits: true
  edit_format: "diff"  # or "whole", "udiff"
  map_tokens: 2048
  no_stream: false
```

### OpenCode Agent

```yaml
# agent-definitions/agents/opencode-1/config.yaml
name: opencode-1
type: opencode

tool:
  runtime: "opencode"
  version: "latest"

brain:
  provider: openai
  model: gpt-4-turbo

auth:
  openai: "secret:openai-api-key"

settings:
  auto_approve: false
  context_window: 128000
```

---

## Tool Type Registry

```yaml
# tool-types/registry.yaml

tool_types:
  claude-code:
    display_name: "Claude Code"
    runtime: "npx @anthropic/claude-code"
    supported_models:
      - claude-opus-4-5-20251101
      - claude-sonnet-4-20250514
      - claude-haiku-3-20250515
    auth_methods:
      - api_key
      - max_subscription
    mcp_support: true
    headless_mode: true
    docs: "https://docs.anthropic.com/claude-code"

  goose:
    display_name: "Goose"
    runtime: "goose session start"
    supported_models:
      - claude-*
      - gpt-4*
      - llama-*
      - ollama/*
    auth_methods:
      - api_key
      - local
    mcp_support: true  # via extensions
    headless_mode: true
    docs: "https://block.github.io/goose"

  aider:
    display_name: "Aider"
    runtime: "aider --message"
    supported_models:
      - claude-*
      - gpt-4*
      - deepseek-*
      - ollama/*
    auth_methods:
      - api_key
      - local
    mcp_support: false  # Uses native tools
    headless_mode: true
    docs: "https://aider.chat"

  opencode:
    display_name: "OpenCode"
    runtime: "opencode"
    supported_models:
      - gpt-4*
      - claude-*
      - ollama/*
    auth_methods:
      - api_key
      - local
    mcp_support: true
    headless_mode: true
    docs: "https://opencode.ai"

  codebuff:
    display_name: "Codebuff"
    runtime: "codebuff-cli"
    supported_models:
      - claude-sonnet-4
      - gpt-4
    auth_methods:
      - subscription
    subscription:
      provider: "codebuff"
      plans: [pro, team]
    mcp_support: false
    headless_mode: limited
    docs: "https://codebuff.com"

  kilocode:
    display_name: "Kilocode"
    runtime: "kilocode-agent"
    supported_models:
      - claude-sonnet-4
      - gpt-4
      - deepseek-coder
    auth_methods:
      - subscription
    subscription:
      provider: "kilocode"
      plans: [starter, pro]
    mcp_support: true
    headless_mode: true
    docs: "https://kilocode.dev"
```

---

## Runner Execution by Type

```python
# runner/tool_executor.py

class ToolExecutor:
    """Execute agent tasks using their configured tool type."""

    async def execute(self, agent: Agent, task: Task) -> Result:
        executor = self.get_executor(agent.type)
        return await executor.run(agent, task)

    def get_executor(self, tool_type: str) -> BaseExecutor:
        executors = {
            "claude-code": ClaudeCodeExecutor(),
            "goose": GooseExecutor(),
            "aider": AiderExecutor(),
            "opencode": OpenCodeExecutor(),
            "codebuff": CodebuffExecutor(),
            "kilocode": KilocodeExecutor(),
        }
        return executors.get(tool_type, GenericExecutor())


class ClaudeCodeExecutor(BaseExecutor):
    """Execute tasks via Claude Code CLI."""

    async def run(self, agent: Agent, task: Task) -> Result:
        # Load credentials
        api_key = await secrets.get(agent.auth.secret)

        # Build command
        cmd = [
            "npx", "@anthropic/claude-code",
            "--model", agent.brain.model,
            "--print",  # Non-interactive mode
            "--message", task.prompt
        ]

        # Set environment
        env = {
            "ANTHROPIC_API_KEY": api_key,
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"
        }

        # Execute in sandbox
        result = await self.sandbox.run(cmd, env=env, timeout=300)
        return Result(output=result.stdout, artifacts=result.files)


class GooseExecutor(BaseExecutor):
    """Execute tasks via Goose."""

    async def run(self, agent: Agent, task: Task) -> Result:
        # Write goose config
        config = self.build_goose_config(agent)
        await self.sandbox.write_file("~/.config/goose/profiles.yaml", config)

        # Build command
        cmd = [
            "goose", "session", "start",
            "--profile", agent.name,
            "--message", task.prompt
        ]

        env = await self.build_env(agent)
        result = await self.sandbox.run(cmd, env=env, timeout=300)
        return Result(output=result.stdout, artifacts=result.files)


class AiderExecutor(BaseExecutor):
    """Execute tasks via Aider."""

    async def run(self, agent: Agent, task: Task) -> Result:
        api_key = await secrets.get(agent.auth.anthropic)

        cmd = [
            "aider",
            "--model", f"anthropic/{agent.brain.model}",
            "--message", task.prompt,
            "--yes",  # Auto-approve
            "--no-git"  # We handle git separately
        ]

        env = {"ANTHROPIC_API_KEY": api_key}
        result = await self.sandbox.run(cmd, env=env, cwd=task.workspace)
        return Result(output=result.stdout, artifacts=result.files)
```

---

## Subscription Management

For tools with monthly subscriptions (Codebuff, Kilocode, Cursor):

```yaml
# subscriptions.yaml

subscriptions:
  - provider: codebuff
    plan: pro
    status: active
    renews_at: 2026-02-28
    seats: 3
    assigned_agents:
      - codebuff-agent-1
      - codebuff-agent-2

  - provider: kilocode
    plan: starter
    status: active
    renews_at: 2026-02-15
    seats: 1
    assigned_agents:
      - kilocode-agent-1

  - provider: anthropic
    type: api
    status: active
    budget:
      monthly_limit: 100.00
      current_usage: 42.50
    assigned_agents:
      - claude-coder-1
      - claude-coder-2
      - goose-researcher  # Uses Anthropic API
```

### Subscription Constraints

```python
async def can_activate_agent(agent: Agent) -> bool:
    """Check if agent can be activated based on subscription."""

    tool_type = get_tool_type(agent.type)

    if tool_type.auth_methods == ["subscription"]:
        # Check subscription status
        sub = await get_subscription(tool_type.subscription.provider)
        if not sub or sub.status != "active":
            return False

        # Check seat availability
        active_agents = await count_active_agents(sub.id)
        if active_agents >= sub.seats:
            return False

    elif tool_type.auth_methods == ["api_key"]:
        # Check budget
        budget = await get_budget(agent.auth.provider)
        if budget and budget.current_usage >= budget.monthly_limit:
            return False

    return True
```

---

## Database Schema Updates

```sql
-- Extend agents table
ALTER TABLE agents ADD COLUMN tool_type TEXT NOT NULL DEFAULT 'claude-code';
ALTER TABLE agents ADD COLUMN tool_config JSONB DEFAULT '{}';

-- Subscriptions table
CREATE TABLE subscriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider TEXT NOT NULL,  -- 'codebuff', 'kilocode', 'anthropic'
    plan TEXT,               -- 'pro', 'starter', NULL for API
    status TEXT DEFAULT 'active',
    seats INTEGER DEFAULT 1,
    renews_at TIMESTAMPTZ,
    budget_limit DECIMAL(10,2),
    budget_used DECIMAL(10,2) DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Track which agents use which subscription
CREATE TABLE agent_subscriptions (
    agent_id UUID REFERENCES agents(id),
    subscription_id UUID REFERENCES subscriptions(id),
    PRIMARY KEY (agent_id, subscription_id)
);

-- Usage tracking
CREATE TABLE usage_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID REFERENCES agents(id),
    subscription_id UUID REFERENCES subscriptions(id),
    tokens_input INTEGER,
    tokens_output INTEGER,
    cost_usd DECIMAL(10,4),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## Example: Mixed Agent Swarm

```yaml
# Swarm with different tool types

agents:
  # Claude Code agents (Anthropic API)
  - name: claude-architect
    type: claude-code
    brain:
      model: claude-opus-4-5-20251101
    purpose: "System design and architecture"
    daily_budget: 20.00

  - name: claude-coder
    type: claude-code
    brain:
      model: claude-sonnet-4-20250514
    purpose: "Implementation and coding"
    daily_budget: 10.00

  # Goose agent (can use local models)
  - name: goose-tinkerer
    type: goose
    brain:
      provider: ollama
      model: deepseek-coder:33b
    purpose: "Experimental code, local execution"
    # No API cost

  # Aider agent (multi-provider)
  - name: aider-refactorer
    type: aider
    brain:
      model: claude-sonnet-4-20250514
    purpose: "Code refactoring and cleanup"

  # Kilocode subscription agent
  - name: kilo-reviewer
    type: kilocode
    subscription: kilocode-starter
    purpose: "Code review with visual diff"
```

---

## Consequences

### Positive
- Leverage best tool for each task (Claude Code for complex, Aider for refactoring)
- Cost optimization (use local models via Goose for experiments)
- Subscription seat management across agents
- Each agent uses familiar tool patterns

### Negative
- Multiple tool runtimes to maintain on runners
- Different tool versions may have incompatibilities
- Subscription management complexity
- Some tools may not support headless/CLI mode fully

### Tool Selection Guidance

| Use Case | Recommended Tool | Why |
|----------|-----------------|-----|
| Complex multi-file changes | Claude Code | Best context handling |
| Quick refactors | Aider | Fast, git-integrated |
| Research/exploration | Goose | Flexible extensions |
| Cost-sensitive tasks | Goose + Ollama | Free local models |
| Code review | Kilocode | Visual diff UI |
| VS Code integration | Codebuff | Native extension |

---

## Sources

- [Claude Code](https://docs.anthropic.com/claude-code)
- [Goose](https://block.github.io/goose)
- [Aider](https://aider.chat)
- [OpenCode](https://github.com/opencode-ai/opencode)
- [Codebuff](https://codebuff.com)
- [Kilocode](https://kilocode.dev)
