# ADR-022: Consumption Tracking

## Status

**Proposed**

## Context

Individual coding tools (Claude Code, Goose, Aider, etc.) implement their own spending limits. However, the collective agent system needs visibility into aggregate consumption to:

1. **Prioritize activations** - When approaching limits, favor high-value tasks
2. **Balance load** - Spread consumption across agents/tools to avoid single-tool exhaustion
3. **Forecast availability** - Know when tools will refresh (monthly resets)
4. **Alert on anomalies** - Detect runaway agents or unexpected spikes

The coordinator needs consumption data to make intelligent scheduling decisions.

## Decision

**Implement a consumption tracking system that aggregates usage metrics and exposes them to the scheduler.**

```
┌─────────────────────────────────────────────────────────────────────┐
│  CONSUMPTION TRACKING FLOW                                          │
│                                                                      │
│  ┌───────────────┐                                                  │
│  │  Agent Run    │                                                  │
│  │  Completes    │                                                  │
│  └───────┬───────┘                                                  │
│          │                                                          │
│          ▼                                                          │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  Runner Reports Metrics                                        │  │
│  │  • tool_type: "claude-code"                                   │  │
│  │  • tokens_input: 45000                                        │  │
│  │  • tokens_output: 12000                                       │  │
│  │  • estimated_cost_usd: 0.23                                   │  │
│  │  • duration_seconds: 45                                       │  │
│  │  • agent_id: "claude-coder-1"                                 │  │
│  │  • task_type: "notification_response"                         │  │
│  └───────────────────────────────────────────────────────────────┘  │
│          │                                                          │
│          ▼                                                          │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  Consumption Store (TimescaleDB / PostgreSQL)                  │  │
│  │                                                                 │  │
│  │  consumption_events                                            │  │
│  │  ├── timestamp                                                 │  │
│  │  ├── agent_id                                                  │  │
│  │  ├── tool_type                                                 │  │
│  │  ├── tokens_input                                              │  │
│  │  ├── tokens_output                                             │  │
│  │  ├── estimated_cost_usd                                        │  │
│  │  ├── duration_seconds                                          │  │
│  │  └── task_metadata (jsonb)                                     │  │
│  └───────────────────────────────────────────────────────────────┘  │
│          │                                                          │
│          ▼                                                          │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  Aggregation Views (materialized, refreshed periodically)      │  │
│  │                                                                 │  │
│  │  • usage_by_tool_daily                                        │  │
│  │  • usage_by_agent_daily                                       │  │
│  │  • usage_by_tool_mtd (month-to-date)                          │  │
│  │  • budget_remaining_by_tool                                   │  │
│  └───────────────────────────────────────────────────────────────┘  │
│          │                                                          │
│          ▼                                                          │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  Scheduler Queries Before Activation                           │  │
│  │                                                                 │  │
│  │  "What's the budget health for claude-code?"                  │  │
│  │  → 67% of monthly limit consumed, 12 days remaining           │  │
│  │  → Recommend: normal priority, no throttling                  │  │
│  │                                                                 │  │
│  │  "What's the budget health for goose?"                        │  │
│  │  → 94% consumed, 12 days remaining                            │  │
│  │  → Recommend: high-priority only, defer exploration           │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Data Model

### Consumption Events

```sql
CREATE TABLE consumption_events (
    id              BIGSERIAL PRIMARY KEY,
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    agent_id        TEXT NOT NULL,
    tool_type       TEXT NOT NULL,  -- claude-code, goose, aider, etc.

    -- Token metrics
    tokens_input    INTEGER,
    tokens_output   INTEGER,
    tokens_total    INTEGER GENERATED ALWAYS AS (tokens_input + tokens_output) STORED,

    -- Cost estimate (may not be exact, tools report what they can)
    estimated_cost_usd  DECIMAL(10, 4),

    -- Timing
    duration_seconds    INTEGER,

    -- Context
    activation_id       UUID,           -- Links to specific activation
    task_type          TEXT,            -- notification, exploration, discovery
    task_priority      TEXT,            -- high, normal, low

    -- Extensible metadata
    metadata           JSONB
);

-- Partition by month for efficient queries and retention
CREATE INDEX idx_consumption_timestamp ON consumption_events (timestamp);
CREATE INDEX idx_consumption_tool ON consumption_events (tool_type, timestamp);
CREATE INDEX idx_consumption_agent ON consumption_events (agent_id, timestamp);
```

### Budget Configuration

```sql
CREATE TABLE tool_budgets (
    tool_type           TEXT PRIMARY KEY,

    -- Monthly limits (set based on subscription/plan)
    monthly_limit_usd   DECIMAL(10, 2),
    monthly_limit_tokens INTEGER,

    -- Reset schedule
    reset_day_of_month  INTEGER DEFAULT 1,  -- When the billing cycle resets

    -- Throttling thresholds
    warn_threshold_pct  INTEGER DEFAULT 80,  -- Alert at 80%
    throttle_threshold_pct INTEGER DEFAULT 90,  -- Throttle exploration at 90%
    hard_limit_pct      INTEGER DEFAULT 100, -- Block all non-critical at 100%

    -- Priority overrides
    allow_critical_over_limit BOOLEAN DEFAULT true
);

-- Example data
INSERT INTO tool_budgets VALUES
    ('claude-code', 100.00, NULL, 1, 80, 90, 100, true),
    ('goose', 50.00, NULL, 1, 80, 90, 100, true),
    ('aider', 30.00, NULL, 1, 80, 90, 100, true);
```

### Aggregation Views

```sql
-- Month-to-date usage by tool
CREATE MATERIALIZED VIEW usage_by_tool_mtd AS
SELECT
    tool_type,
    COUNT(*) as activation_count,
    SUM(tokens_total) as total_tokens,
    SUM(estimated_cost_usd) as total_cost_usd,
    AVG(tokens_total) as avg_tokens_per_activation,
    AVG(estimated_cost_usd) as avg_cost_per_activation
FROM consumption_events
WHERE timestamp >= DATE_TRUNC('month', NOW())
GROUP BY tool_type;

-- Budget health view (joined with limits)
CREATE VIEW budget_health AS
SELECT
    b.tool_type,
    b.monthly_limit_usd,
    COALESCE(u.total_cost_usd, 0) as used_usd,
    b.monthly_limit_usd - COALESCE(u.total_cost_usd, 0) as remaining_usd,
    ROUND(COALESCE(u.total_cost_usd, 0) / b.monthly_limit_usd * 100, 1) as used_pct,
    EXTRACT(DAY FROM DATE_TRUNC('month', NOW()) + INTERVAL '1 month' - NOW()) as days_remaining,
    CASE
        WHEN COALESCE(u.total_cost_usd, 0) / b.monthly_limit_usd >= b.hard_limit_pct / 100.0
            THEN 'critical'
        WHEN COALESCE(u.total_cost_usd, 0) / b.monthly_limit_usd >= b.throttle_threshold_pct / 100.0
            THEN 'throttle'
        WHEN COALESCE(u.total_cost_usd, 0) / b.monthly_limit_usd >= b.warn_threshold_pct / 100.0
            THEN 'warning'
        ELSE 'healthy'
    END as status
FROM tool_budgets b
LEFT JOIN usage_by_tool_mtd u ON b.tool_type = u.tool_type;
```

---

## Scheduler Integration

### Budget-Aware Scheduling

```python
# coordinator/scheduler.py

class BudgetAwareScheduler:
    """Scheduler that considers consumption when prioritizing activations."""

    async def should_activate(
        self,
        agent: Agent,
        task: Task
    ) -> tuple[bool, str]:
        """
        Decide if an activation should proceed based on budget health.
        Returns (should_proceed, reason).
        """
        tool_type = agent.config.type  # e.g., "claude-code"
        health = await self.get_budget_health(tool_type)

        # Critical tasks always proceed (unless hard blocked)
        if task.priority == "critical":
            if health.status == "critical" and not health.allow_critical_over_limit:
                return False, f"{tool_type} over hard limit"
            return True, "critical priority override"

        # Apply throttling based on status
        if health.status == "critical":
            return False, f"{tool_type} at {health.used_pct}% - over hard limit"

        if health.status == "throttle":
            # Only allow notification responses, no exploration
            if task.type == "exploration":
                return False, f"{tool_type} at {health.used_pct}% - exploration paused"
            if task.type == "discovery":
                return False, f"{tool_type} at {health.used_pct}% - discovery paused"

        if health.status == "warning":
            # Log but allow
            self.logger.warning(
                f"{tool_type} at {health.used_pct}% of monthly budget"
            )

        return True, "budget healthy"

    async def select_tool_for_task(
        self,
        task: Task,
        capable_tools: list[str]
    ) -> str:
        """
        When multiple tools can handle a task, prefer the one with
        more budget headroom.
        """
        healths = await self.get_budget_healths(capable_tools)

        # Sort by remaining budget percentage (descending)
        sorted_tools = sorted(
            healths.items(),
            key=lambda x: (100 - x[1].used_pct),
            reverse=True
        )

        return sorted_tools[0][0]  # Return tool with most headroom
```

### Consumption Reporting

```python
# runner/metrics.py

@dataclass
class ActivationMetrics:
    """Metrics collected during an activation."""
    agent_id: str
    tool_type: str
    activation_id: UUID

    tokens_input: int | None = None
    tokens_output: int | None = None
    estimated_cost_usd: Decimal | None = None
    duration_seconds: int | None = None

    task_type: str | None = None
    task_priority: str | None = None
    metadata: dict | None = None


class MetricsCollector:
    """Collects and reports activation metrics."""

    async def report(self, metrics: ActivationMetrics) -> None:
        """Report metrics to consumption store."""
        await self.db.execute("""
            INSERT INTO consumption_events (
                agent_id, tool_type, activation_id,
                tokens_input, tokens_output, estimated_cost_usd,
                duration_seconds, task_type, task_priority, metadata
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """,
            metrics.agent_id, metrics.tool_type, metrics.activation_id,
            metrics.tokens_input, metrics.tokens_output, metrics.estimated_cost_usd,
            metrics.duration_seconds, metrics.task_type, metrics.task_priority,
            json.dumps(metrics.metadata) if metrics.metadata else None
        )

        # Refresh materialized view periodically (not every insert)
        if await self.should_refresh_views():
            await self.db.execute(
                "REFRESH MATERIALIZED VIEW CONCURRENTLY usage_by_tool_mtd"
            )
```

---

## Extracting Metrics from Tools

Different tools report usage differently:

| Tool | How to Get Metrics |
|------|-------------------|
| **Claude Code** | Parse output for token counts, or query Anthropic API usage |
| **Goose** | Check `~/.goose/usage.json` or parse stdout |
| **Aider** | Parse `--show-cost` output or check session logs |
| **OpenCode** | Varies by backend LLM provider |

```python
# runner/extractors/claude_code.py

class ClaudeCodeMetricsExtractor:
    """Extract metrics from Claude Code execution."""

    async def extract(self, result: ExecutionResult) -> ActivationMetrics:
        # Claude Code outputs cost summary at end
        # Example: "Total cost: $0.23 (45,000 input, 12,000 output tokens)"

        cost_match = re.search(
            r'Total cost: \$([0-9.]+) \(([0-9,]+) input, ([0-9,]+) output',
            result.stdout
        )

        if cost_match:
            return ActivationMetrics(
                estimated_cost_usd=Decimal(cost_match.group(1)),
                tokens_input=int(cost_match.group(2).replace(',', '')),
                tokens_output=int(cost_match.group(3).replace(',', '')),
                ...
            )

        # Fallback: estimate from duration/model
        return self.estimate_from_duration(result)
```

---

## Collective Awareness

Agents can query their collective budget health via Hub API:

```
GET /api/v1/system/budget-health
Authorization: Bearer <agent-token>

Response:
{
  "tools": {
    "claude-code": {
      "status": "healthy",
      "used_pct": 67.2,
      "remaining_usd": 32.80,
      "days_remaining": 12,
      "recommendation": "normal"
    },
    "goose": {
      "status": "throttle",
      "used_pct": 94.1,
      "remaining_usd": 2.95,
      "days_remaining": 12,
      "recommendation": "high_priority_only"
    }
  },
  "collective": {
    "total_budget_usd": 180.00,
    "total_used_usd": 114.25,
    "overall_health": "warning"
  }
}
```

Agents can include this in their context:

```markdown
## System Resource Status
- Claude Code: 67% used, operating normally
- Goose: 94% used, high-priority tasks only
- Overall: Warning - consider deferring non-essential exploration
```

---

## Alerting

```yaml
# alerting rules
alerts:
  - name: tool_budget_warning
    condition: budget_health.used_pct >= 80
    action: log_warning

  - name: tool_budget_throttle
    condition: budget_health.used_pct >= 90
    action:
      - log_error
      - notify_admin

  - name: tool_budget_critical
    condition: budget_health.used_pct >= 100
    action:
      - log_critical
      - notify_admin
      - pause_tool_activations
```

---

## Consequences

### Positive
- **Intelligent scheduling** - Coordinator can balance load across tools
- **Predictable costs** - No surprise overages
- **Self-regulating** - System throttles gracefully before hard limits
- **Visibility** - Dashboard shows consumption trends

### Negative
- **Metric extraction complexity** - Each tool reports differently
- **Slight overhead** - Recording every activation adds latency
- **Estimate accuracy** - Not all tools report exact costs

### Mitigations
- Use async metric reporting (don't block activations)
- Accept that estimates are "good enough" for prioritization
- Reconcile with actual billing data monthly
