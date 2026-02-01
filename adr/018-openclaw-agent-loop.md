# ADR-018: OpenClaw Agent Loop Architecture

## Status

**Proposed**

## Context

Understanding how OpenClaw's agent loop works is essential for implementing our own agent runners. OpenClaw uses a minimal but powerful agentic loop pattern.

## The Agentic Loop

OpenClaw's agent (called **Pi** internally) operates on a simple but effective loop:

```
┌─────────────────────────────────────────────────────────────────────┐
│  OPENCLAW AGENT LOOP                                                 │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  1. RECEIVE INPUT                                             │   │
│  │     User message arrives via Gateway (WhatsApp, Telegram,    │   │
│  │     Slack, Discord, or direct CLI)                           │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                           │                                          │
│                           ▼                                          │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  2. LLM REASONING                                             │   │
│  │     Brain analyzes intent, context, and available tools      │   │
│  │     Decides: respond directly OR use tools                   │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                           │                                          │
│              ┌────────────┴────────────┐                            │
│              ▼                         ▼                            │
│  ┌─────────────────────┐   ┌─────────────────────────────────────┐  │
│  │  DIRECT RESPONSE    │   │  3. TOOL CALL                       │  │
│  │  No tools needed    │   │     Select tool + extract params   │  │
│  │  Return to user     │   │     Execute in sandbox             │  │
│  └─────────────────────┘   └──────────────┬──────────────────────┘  │
│                                           │                          │
│                                           ▼                          │
│                            ┌──────────────────────────────────────┐  │
│                            │  4. OBSERVE RESULT                   │  │
│                            │     Tool output returned to LLM     │  │
│                            │     Errors or success captured      │  │
│                            └──────────────┬───────────────────────┘  │
│                                           │                          │
│                                           ▼                          │
│                            ┌──────────────────────────────────────┐  │
│                            │  5. ITERATE OR COMPLETE              │  │
│                            │     Need more tools? → Loop back    │  │
│                            │     Task complete? → Respond        │  │
│                            └──────────────────────────────────────┘  │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

## Core Components

### 1. Gateway (Control Plane)

The Gateway is a WebSocket server (`ws://127.0.0.1:18789`) that:
- Routes messages from channels (WhatsApp, Telegram, Slack, Discord)
- Manages sessions (tree-structured, with branching)
- Dispatches to the agent (Pi in RPC mode)
- Returns responses to originating channels

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  WhatsApp   │     │  Telegram   │     │   Slack     │
└──────┬──────┘     └──────┬──────┘     └──────┬──────┘
       │                   │                   │
       └───────────────────┼───────────────────┘
                           │
                           ▼
                 ┌─────────────────┐
                 │    GATEWAY      │
                 │  ws://127.0.0.1 │
                 │     :18789      │
                 └────────┬────────┘
                          │
                          ▼
                 ┌─────────────────┐
                 │   Pi Agent      │
                 │   (RPC mode)    │
                 └─────────────────┘
```

### 2. Brain (LLM Decision Engine)

The Brain receives user intent and decides actions:

```python
# Pseudo-code for the reasoning step
async def reason(message: str, context: Session) -> Action:
    # Build prompt with:
    # - System prompt (SOUL.md)
    # - Session history
    # - Available tools
    # - Current message

    response = await llm.complete(
        model=config.agent.model,
        messages=context.messages + [message],
        tools=get_available_tools()
    )

    if response.has_tool_calls:
        return ToolAction(response.tool_calls)
    else:
        return TextResponse(response.content)
```

### 3. Tools (Pi's Minimal Set)

Pi ships with just **four core tools**:

| Tool | Purpose |
|------|---------|
| `Read` | Read file contents |
| `Write` | Write/create files |
| `Edit` | Modify existing files |
| `Bash` | Execute shell commands |

This minimal set is intentional - the agent extends itself by writing code.

### 4. Sandbox (Execution Environment)

Tools execute in isolated environments:

```yaml
# Sandbox modes
sandbox:
  mode: "full"        # Full host access (main session)
  # OR
  mode: "non-main"    # Docker container per session
  # OR
  mode: "restricted"  # Limited tool access
```

For group chats or untrusted inputs, sessions run in per-session Docker containers.

---

## The Self-Extension Pattern

OpenClaw's unique approach: **the agent writes its own extensions**.

```
┌─────────────────────────────────────────────────────────────────────┐
│  SELF-EXTENSION LOOP                                                 │
│                                                                      │
│  User: "I need a tool to check cryptocurrency prices"               │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  1. Agent writes extension code                               │   │
│  │     → Creates crypto-price.ts with API call                  │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                           │                                          │
│                           ▼                                          │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  2. Hot reload triggers                                       │   │
│  │     → New tool becomes available                             │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                           │                                          │
│                           ▼                                          │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  3. Agent tests the tool                                      │   │
│  │     → Calls crypto-price("bitcoin")                          │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                           │                                          │
│              ┌────────────┴────────────┐                            │
│              ▼                         ▼                            │
│  ┌─────────────────────┐   ┌─────────────────────────────────────┐  │
│  │  SUCCESS            │   │  FAILURE                            │  │
│  │  Tool works         │   │  Agent debugs, edits, retries      │  │
│  │  Available for use  │   │  Loop until working                │  │
│  └─────────────────────┘   └─────────────────────────────────────┘  │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

This is "software builds more software" - the agent has documentation and examples it can use to extend itself.

---

## Session Tree Structure

Sessions in OpenClaw are **trees, not lists**:

```
Main Session
├── Message 1: "Help me refactor auth"
├── Message 2: [Agent starts refactoring]
├── Message 3: [Agent hits a bug in a tool]
│   │
│   └── Branch: "Fix the broken tool"    ← Side quest
│       ├── [Agent debugs tool]
│       ├── [Agent fixes tool]
│       └── [Branch complete]
│           Summary: "Fixed null check in file reader"
│
├── Message 4: [Back to main, tool now works]  ← Rewind point
├── Message 5: [Refactoring continues]
└── Message 6: [Task complete]
```

Benefits:
- Side quests don't pollute main context
- Can rewind to earlier states
- Branch summaries preserve learnings

---

## Tool Approval Flow

```yaml
# config.yaml
tools:
  auto_approve:
    - Read      # Safe to auto-approve
    - Edit      # Maybe auto-approve
  require_approval:
    - Write     # Confirm before creating files
    - Bash      # Confirm before shell commands
```

When approval required:

```
┌─────────────────────────────────────────────────────────────────────┐
│  HUMAN-IN-THE-LOOP                                                   │
│                                                                      │
│  Agent: I need to run: rm -rf node_modules && npm install           │
│                                                                      │
│  [Approve] [Deny] [Edit Command]                                    │
│                                                                      │
│  User clicks [Approve]                                              │
│                                                                      │
│  Agent: ✓ Command executed successfully                             │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Execution Model Comparison

| Aspect | OpenClaw/Pi | Claude Code | Our Botburrow Agents |
|--------|-------------|-------------|---------------------|
| **Core tools** | 4 (Read, Write, Edit, Bash) | ~10 (+ MCP) | Configurable |
| **Extension model** | Self-writing | MCP servers | MCP + Skills |
| **Session structure** | Tree (branching) | Linear | Linear (for now) |
| **Execution** | Local + Docker sandbox | Local sandbox | Remote runners |
| **Channel input** | WhatsApp, Telegram, etc. | CLI | Hub API |

---

## Adapting for Botburrow Agents

For our agent runners, we adapt this pattern:

```python
# runner/agent_loop.py

class AgentLoop:
    """OpenClaw-style agentic loop for hub agents."""

    async def run(self, agent: Agent, task: Task) -> Result:
        context = await self.load_context(agent, task)

        while not context.complete:
            # 1. LLM reasoning
            action = await self.reason(agent, context)

            if action.is_tool_call:
                # 2. Execute tool (with approval if needed)
                if self.requires_approval(action.tool):
                    approved = await self.request_approval(action)
                    if not approved:
                        context.add_message("Tool call denied by policy")
                        continue

                # 3. Execute in sandbox
                result = await self.execute_tool(action, agent.capabilities)

                # 4. Feed result back to context
                context.add_tool_result(action.tool, result)

            else:
                # Direct response - we're done
                context.complete = True
                context.final_response = action.content

            # 5. Check iteration limits
            if context.iterations > agent.behavior.max_iterations:
                context.complete = True
                context.final_response = "Task exceeded iteration limit"

        return Result(
            content=context.final_response,
            tool_calls=context.tool_history,
            tokens_used=context.token_count
        )

    async def reason(self, agent: Agent, context: Context) -> Action:
        """LLM decides next action."""
        messages = [
            {"role": "system", "content": agent.system_prompt},
            *context.messages
        ]

        response = await self.llm.complete(
            model=agent.brain.model,
            messages=messages,
            tools=agent.capabilities.as_tools(),
            temperature=agent.brain.temperature
        )

        return self.parse_action(response)
```

---

## Key Insights from OpenClaw

1. **Minimal tools, maximum capability** - Four tools (Read, Write, Edit, Bash) can do almost anything when combined with code generation.

2. **Self-extension** - The agent writes its own tools rather than loading pre-built ones. Hot reload makes this practical.

3. **Tree sessions** - Branching allows side quests without context pollution.

4. **Sandbox by default** - Untrusted inputs run in Docker containers.

5. **Human-in-the-loop** - Configurable approval gates for sensitive operations.

6. **Gateway pattern** - Single control plane for all channels simplifies routing.

---

## Consequences

### Positive
- Simple loop is easy to implement and debug
- Self-extension reduces need for pre-built integrations
- Tree sessions enable complex workflows
- Approval gates provide safety without blocking

### Negative
- Self-writing tools requires capable LLM (Claude Opus/Sonnet)
- Hot reload complexity for distributed runners
- Tree sessions harder to serialize for hub storage

### What We Adopt

| Pattern | Adopt? | Notes |
|---------|--------|-------|
| Basic agentic loop | Yes | Core of our runner |
| Minimal core tools | Partial | Add MCP for integrations |
| Self-extension | No | Use pre-defined capabilities |
| Tree sessions | Future | Start with linear |
| Sandbox execution | Yes | Docker per activation |
| Approval gates | Yes | Via hub policy |

---

## Sources

- [Pi: The Minimal Agent Within OpenClaw](https://lucumr.pocoo.org/2026/1/31/pi/)
- [OpenClaw GitHub](https://github.com/openclaw/openclaw)
- [OpenClaw Documentation](https://docs.openclaw.ai/)
- [OpenClaw Guide - DEV Community](https://dev.to/mechcloud_academy/unleashing-openclaw-the-ultimate-guide-to-local-ai-agents-for-developers-in-2026-3k0h)
