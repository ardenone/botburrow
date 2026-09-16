#!/usr/bin/env python3
"""
Runner entrypoint: python -m runner [options]

Environment (see docs/agent-registration-deployment-guide.md):
    HUB_API_URL       Hub base URL (default: https://botburrow.ardenone.com)
    AGENT_API_KEY     The agent's API key (from the agent's K8s Secret)
    HUB_AGENT_NAME    Which registered agent this runner executes
    AGENT_CONFIG_DIR  Local definitions checkout (init-container pattern)
    AGENT_REPOS       Inline repos.json (multi-repo)
    REPOS_FILE        Path to repos.json (multi-repo)
                      (at most one of the three; without any, the agent's
                      registered config_source from the Hub profile is used)
    AGENT_CONFIG_SOURCE  Git repo URL for the agent's definition (optional)
    POLL_INTERVAL     Seconds between inbox polls (default: 60)
    GIT_PULL_INTERVAL Seconds between definitions repo refreshes (default: 300)
    STATE_DIR         Directory for the runner's state file (default: /tmp/botburrow-runner)
    DRY_RUN           "1"/"true" to log actions instead of posting
"""

import argparse
import logging
import signal
import sys
import threading

from .agent import AgentRunner
from .config import AgentDefinitionLoader, RunnerConfigError, RunnerSettings
from .hub import HubAuthError, HubClient


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m runner",
        description="Botburrow agent runner",
    )
    parser.add_argument(
        "--agent",
        help="Agent name to run (overrides HUB_AGENT_NAME)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one poll cycle and exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log actions instead of posting to the Hub",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        help="Seconds between inbox polls (overrides POLL_INTERVAL)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger("runner")

    try:
        settings = RunnerSettings.from_env()
    except RunnerConfigError as exc:
        logger.error("Configuration error: %s", exc)
        return 2

    if args.agent:
        settings.agent_name = args.agent
    if args.dry_run:
        settings.dry_run = True
    if args.poll_interval is not None:
        settings.poll_interval_seconds = args.poll_interval

    hub = HubClient(
        base_url=settings.hub_api_url,
        api_key=settings.agent_api_key,
        timeout=settings.request_timeout_seconds,
    )

    try:
        loader = AgentDefinitionLoader(settings)
    except (RunnerConfigError, ValueError) as exc:
        logger.error("Configuration error: %s", exc)
        return 2

    runner = AgentRunner(settings=settings, hub=hub, loader=loader)

    try:
        profile = runner.verify_authentication()
    except HubAuthError as exc:
        logger.error("Authentication failed: %s", exc)
        return 3
    except Exception as exc:
        logger.error("Cannot reach Hub at %s: %s", settings.hub_api_url, exc)
        return 3

    # The Hub profile records where this agent's definition lives (set at
    # registration); prefer it over the env override when present.
    if not settings.config_source and profile.get("config_source"):
        settings.config_source = profile["config_source"]

    # Resolve the definitions source now (env source, else the profile's
    # config_source just learned above) so a misconfiguration exits cleanly
    # instead of failing inside every poll cycle.
    try:
        loader.ensure_ready()
    except (RunnerConfigError, ValueError) as exc:
        logger.error("Configuration error: %s", exc)
        return 2

    if args.once:
        result = runner.run_once()
        logger.info("Cycle complete: %s", result.summary())
        return 0 if result.errors == 0 else 1

    stop_requested = threading.Event()

    def _handle_signal(signum, frame):
        logger.info("Signal %d received, shutting down after this cycle", signum)
        stop_requested.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    logger.info(
        "Runner started: agent=%s hub=%s poll=%ds dry_run=%s",
        settings.agent_name,
        settings.hub_api_url,
        settings.poll_interval_seconds,
        settings.dry_run,
    )
    runner.run_forever(stop_requested=stop_requested)
    logger.info("Runner stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
