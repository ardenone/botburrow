"""
Behavior limits and activity tracking (ADR-010 anti-spam measures).

Enforces the ``behavior.limits`` block from config.yaml:

    limits:
      max_daily_posts: 5
      max_daily_comments: 50
      max_responses_per_thread: 3
      min_interval_seconds: 60

Counters persist to a JSON state file so a runner restart (pod reschedule)
does not reset the daily caps. Daily counters roll over at UTC midnight.
The state file holds only counters and IDs — never credentials.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Give up on a notification after this many failed processing attempts so a
# poison-pill item cannot wedge the inbox loop forever.
MAX_NOTIFICATION_ATTEMPTS = 3

STATE_SCHEMA_VERSION = 1


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _today(now: Optional[datetime] = None) -> str:
    return (now or _utc_now()).strftime("%Y-%m-%d")


class RateTracker:
    """Persistent activity counters for one agent."""

    def __init__(self, state_file: Path, limits: Dict[str, Any]):
        self.state_file = Path(state_file)
        self.limits = limits
        self.state: Dict[str, Any] = self._load()

    # -- persistence ---------------------------------------------------------

    def _empty_state(self) -> Dict[str, Any]:
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "date": _today(),
            "posts_today": 0,
            "comments_today": 0,
            "last_comment_at": None,
            "last_post_at": None,
            "last_discovery_at": None,
            "thread_responses": {},     # post_id -> our reply count
            "discovery_responded": [],  # post_ids declined/handled via discovery
            "notification_attempts": {},  # notification_id -> attempt count
        }

    def _load(self) -> Dict[str, Any]:
        try:
            with open(self.state_file) as f:
                state = json.load(f)
            if not isinstance(state, dict):
                raise ValueError("state is not an object")
            # Unknown fields are kept; missing fields filled from empty state.
            merged = self._empty_state()
            merged.update(state)
            return merged
        except FileNotFoundError:
            return self._empty_state()
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "Runner state file %s unreadable (%s); starting fresh",
                self.state_file,
                exc,
            )
            return self._empty_state()

    def save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2))
        tmp.replace(self.state_file)

    def _roll_date(self, now: Optional[datetime] = None) -> None:
        """Reset daily counters at UTC midnight."""
        if self.state.get("date") != _today(now):
            self.state["date"] = _today(now)
            self.state["posts_today"] = 0
            self.state["comments_today"] = 0

    # -- comments ------------------------------------------------------------

    def can_comment(
        self, post_id: str, now: Optional[datetime] = None
    ) -> Tuple[bool, str]:
        """Whether a comment in post_id's thread is allowed right now."""
        self._roll_date(now)
        now = now or _utc_now()

        if self.state["comments_today"] >= self.limits["max_daily_comments"]:
            return False, (
                f"daily comment limit reached "
                f"({self.limits['max_daily_comments']})"
            )

        replied = self.state["thread_responses"].get(post_id, 0)
        if replied >= self.limits["max_responses_per_thread"]:
            return False, (
                f"thread response limit reached "
                f"({self.limits['max_responses_per_thread']})"
            )

        last = self.state.get("last_comment_at")
        if last:
            elapsed = (now - datetime.fromisoformat(last)).total_seconds()
            if elapsed < self.limits["min_interval_seconds"]:
                return False, (
                    f"min_interval_seconds not elapsed "
                    f"({elapsed:.0f}s < {self.limits['min_interval_seconds']}s)"
                )
        return True, ""

    def record_comment(self, post_id: str, now: Optional[datetime] = None) -> None:
        self._roll_date(now)
        now = now or _utc_now()
        self.state["comments_today"] += 1
        self.state["last_comment_at"] = now.isoformat()
        thread_key = str(post_id)
        self.state["thread_responses"][thread_key] = (
            self.state["thread_responses"].get(thread_key, 0) + 1
        )
        self.save()

    # -- posts ---------------------------------------------------------------

    def can_post(self, now: Optional[datetime] = None) -> Tuple[bool, str]:
        self._roll_date(now)
        now = now or _utc_now()
        if self.state["posts_today"] >= self.limits["max_daily_posts"]:
            return False, f"daily post limit reached ({self.limits['max_daily_posts']})"
        last = self.state.get("last_post_at")
        if last:
            elapsed = (now - datetime.fromisoformat(last)).total_seconds()
            if elapsed < self.limits["min_interval_seconds"]:
                return False, "min_interval_seconds not elapsed"
        return True, ""

    def record_post(self, now: Optional[datetime] = None) -> None:
        self._roll_date(now)
        now = now or _utc_now()
        self.state["posts_today"] += 1
        self.state["last_post_at"] = now.isoformat()
        self.save()

    # -- discovery -----------------------------------------------------------

    def discovery_due(
        self,
        proactive_interval_seconds: int,
        now: Optional[datetime] = None,
    ) -> bool:
        last = self.state.get("last_discovery_at")
        if not last:
            return True
        now = now or _utc_now()
        elapsed = (now - datetime.fromisoformat(last)).total_seconds()
        return elapsed >= proactive_interval_seconds

    def record_discovery(self, now: Optional[datetime] = None) -> None:
        self.state["last_discovery_at"] = (now or _utc_now()).isoformat()
        self.save()

    def already_responded(self, post_id: str) -> bool:
        return (
            post_id in self.state["discovery_responded"]
            or self.state["thread_responses"].get(post_id, 0) > 0
        )

    def mark_discovery_handled(self, post_id: str) -> None:
        """Remember a discovery post we evaluated, so we do not re-evaluate."""
        if post_id not in self.state["discovery_responded"]:
            self.state["discovery_responded"].append(post_id)
            self._prune_discovery_history()
            self.save()

    def _prune_discovery_history(self, keep: int = 500) -> None:
        history = self.state["discovery_responded"]
        if len(history) > keep:
            self.state["discovery_responded"] = history[-keep:]

    # -- notification attempts (poison-pill guard) ---------------------------

    def attempts_exhausted(self, notification_id: str) -> bool:
        return (
            self.state["notification_attempts"].get(notification_id, 0)
            >= MAX_NOTIFICATION_ATTEMPTS
        )

    def record_attempt(self, notification_id: str) -> int:
        """Count a failed processing attempt; returns the new count."""
        attempts = self.state["notification_attempts"]
        attempts[notification_id] = attempts.get(notification_id, 0) + 1
        self.save()
        return attempts[notification_id]

    def clear_attempt(self, notification_id: str) -> None:
        self.state["notification_attempts"].pop(notification_id, None)
        self.save()
