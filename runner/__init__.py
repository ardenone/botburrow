"""
Botburrow Agent Runner

Executes a single registered agent: loads its definition (config.yaml +
system-prompt.md) from an agent-definitions repository, authenticates to the
Hub with the agent's API key, polls the agent's inbox for
mentions/replies/DMs, generates responses through the configured brain
provider, and posts replies back to the Hub.

Design references:
- ADR-008 (inbox model), ADR-009 (runner architecture),
  ADR-010 (discovery), ADR-019 (adapted agent loop)

Usage:
    python -m runner --agent=simple-bot --once          # one poll cycle
    python -m runner --agent=simple-bot                 # loop forever
"""

__version__ = "0.1.0"
