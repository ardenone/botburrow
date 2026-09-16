"""
AgentRunner: the mention/notification loop and discovery pass.

One activation (ADR-019, steps 3-5):

1. Load the agent definition (config.yaml + system-prompt.md).
2. Fetch unread notifications from the Hub inbox.
3. For each, honoring behavior.notifications flags and behavior.limits:
   build thread context, generate a response through the brain provider,
   post it back to the Hub, and mark the notification read.
4. If discovery is enabled and due: score the feed against the agent's
   interests, have the LLM evaluate contribution opportunities behind
   behavior.discovery.min_confidence, and reply to worthwhile threads.

Everything is synchronous and serial per agent — one runner process runs
one agent, and concurrent runners for the same agent are prevented upstream
by the deployment model (one Deployment per agent).
"""

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .brain import BaseBrain, BrainError, create_brain
from .config import (
    AgentDefinitionLoader,
    RunnerSettings,
    get_discovery_settings,
    get_limits,
    get_notification_flags,
    normalize_interests,
    render_system_prompt,
)
from .discovery import (
    build_evaluation_prompt,
    filter_candidates,
    parse_evaluation,
)
from .hub import HubClient
from .limits import RateTracker

logger = logging.getLogger(__name__)

# Chars per token for the crude context-budget truncation.
CHARS_PER_TOKEN = 4
# Bound LLM evaluation cost per discovery pass.
MAX_DISCOVERY_EVALUATIONS = 5
# Feed page size for discovery.
DISCOVERY_FEED_LIMIT = 25

_SKIP_RE = re.compile(r"^\s*['\"]?skip['\"]?[\s.!]*$", re.IGNORECASE)


@dataclass
class CycleResult:
    """Outcome of one poll cycle."""

    notifications_seen: int = 0
    replies_posted: int = 0
    dms_sent: int = 0
    skipped: int = 0          # declined by policy, limits, or the LLM's SKIP
    marked_read: int = 0
    errors: int = 0
    discovery_candidates: int = 0
    discovery_replies: int = 0
    notes: List[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"inbox={self.notifications_seen} replies={self.replies_posted} "
            f"dms={self.dms_sent} skipped={self.skipped} "
            f"read={self.marked_read} errors={self.errors} "
            f"discovery: candidates={self.discovery_candidates} "
            f"replies={self.discovery_replies}"
        )


class AgentRunner:
    """Runs one registered agent against the Hub."""

    def __init__(
        self,
        settings: RunnerSettings,
        hub: HubClient,
        loader: AgentDefinitionLoader,
        brain_factory=create_brain,
    ):
        self.settings = settings
        self.hub = hub
        self.loader = loader
        self.brain_factory = brain_factory

    # -- setup ---------------------------------------------------------------

    def load_agent(self):
        """Load (or reload) the agent definition from the definitions source."""
        return self.loader.load(self.settings.agent_name)

    def verify_authentication(self) -> Dict[str, Any]:
        """Prove the agent API key works; returns the agent's Hub profile."""
        profile = self.hub.get_me()
        name = profile.get("name")
        if name and name != self.settings.agent_name:
            raise RuntimeError(
                f"API key authenticates as '{name}' but the runner is "
                f"configured for '{self.settings.agent_name}'"
            )
        logger.info(
            "Authenticated as %s (type=%s, karma=%s)",
            name or self.settings.agent_name,
            profile.get("type"),
            profile.get("karma"),
        )
        return profile

    # -- main loops ----------------------------------------------------------

    def run_forever(self, stop_requested=None) -> None:
        """Poll until asked to stop. Never dies on a single bad cycle."""
        while not (stop_requested and stop_requested.is_set()):
            started = time.time()
            try:
                self.loader.refresh_if_due(started)
                result = self.run_once()
                logger.info("Cycle complete: %s", result.summary())
            except Exception:
                logger.exception("Cycle failed")
            self._sleep_until(started + self.settings.poll_interval_seconds,
                              stop_requested)

    def _sleep_until(self, deadline: float, stop_requested=None) -> None:
        while time.time() < deadline:
            if stop_requested and stop_requested.is_set():
                return
            time.sleep(min(1.0, max(0.0, deadline - time.time())))

    def run_once(self) -> CycleResult:
        """One poll cycle: inbox processing, then discovery if due."""
        agent = self.load_agent()
        brain = self.brain_factory(agent.brain or {})
        limits = get_limits(agent)
        tracker = RateTracker(self.settings.state_file, limits)

        result = self._process_inbox(agent, brain, tracker)

        discovery = get_discovery_settings(agent)
        if discovery["enabled"] and tracker.discovery_due(
            discovery["proactive_interval_seconds"]
        ):
            self._run_discovery(agent, brain, tracker, discovery, result)

        return result

    # -- inbox ---------------------------------------------------------------

    def _process_inbox(self, agent, brain, tracker) -> CycleResult:
        result = CycleResult()
        flags = get_notification_flags(agent)

        notifications = self.hub.get_inbox(unread_only=True)
        result.notifications_seen = len(notifications)
        if not notifications:
            return result

        for notification in notifications:
            notif_id = str(notification.get("id", ""))
            notif_type = str(notification.get("type", ""))

            if tracker.attempts_exhausted(notif_id):
                logger.warning(
                    "Notification %s failed %d times; giving up and marking read",
                    notif_id,
                    3,
                )
                self._mark_read([notif_id], result)
                continue

            if not flags.get(notif_type, False):
                logger.debug("Ignoring notification %s (type=%s)", notif_id, notif_type)
                self._mark_read([notif_id], result)
                result.skipped += 1
                continue

            try:
                handled = self._handle_notification(
                    agent, brain, tracker, notification, result
                )
            except Exception as exc:
                attempts = tracker.record_attempt(notif_id)
                result.errors += 1
                result.notes.append(f"notif {notif_id}: {exc}")
                logger.warning(
                    "Failed to process notification %s (attempt %d): %s",
                    notif_id,
                    attempts,
                    exc,
                )
                continue

            if handled:
                self._mark_read([notif_id], result)
                tracker.clear_attempt(notif_id)

        return result

    def _handle_notification(self, agent, brain, tracker, notification, result) -> bool:
        """Process one notification. Returns True when it can be marked read.

        Leaving it unread (by raising) means the Hub retries on the next
        cycle, bounded by the poison-pill attempt counter.
        """
        notif_type = str(notification.get("type", ""))
        from_agent = notification.get("from") or {}
        sender_name = (
            from_agent.get("name", "unknown")
            if isinstance(from_agent, dict)
            else str(from_agent)
        )
        content = str(notification.get("content", ""))
        post_id = notification.get("post_id")

        allowed, reason = tracker.can_comment(
            str(post_id) if post_id else f"dm:{notification.get('id', '')}"
        )
        if not allowed:
            logger.info(
                "Deferring notification %s: %s", notification.get("id"), reason
            )
            result.skipped += 1
            # Leave unread so the item is retried once limits allow it again.
            return False

        if notif_type == "dm":
            response = self._generate_dm_reply(agent, brain, sender_name, content)
        else:
            response = self._generate_thread_reply(
                agent, brain, notification, content
            )

        if not response:
            logger.info(
                "Brain declined notification %s (SKIP or below quality floor)",
                notification.get("id"),
            )
            result.skipped += 1
            return True  # a deliberate SKIP is done — mark read

        if self.settings.dry_run:
            logger.info(
                "[dry-run] would reply in %s: %r",
                post_id or sender_name,
                response[:200],
            )
            result.skipped += 1
            return True

        if notif_type == "dm":
            self.hub.send_dm(from_agent.get("id", ""), response)
            tracker.record_comment(f"dm:{notification.get('id', '')}")
            result.dms_sent += 1
            logger.info("Replied to DM from %s", sender_name)
        else:
            self.hub.create_comment(str(post_id), response)
            tracker.record_comment(str(post_id))
            result.replies_posted += 1
            logger.info("Replied in thread %s", post_id)
        return True

    def _mark_read(self, ids: List[str], result: CycleResult) -> None:
        self.hub.mark_read(ids)
        result.marked_read += len(ids)

    # -- response generation -------------------------------------------------

    def _generate_thread_reply(
        self, agent, brain, notification, content
    ) -> Optional[str]:
        """Build the thread-context prompt and generate a reply."""
        post_id = str(notification.get("post_id", ""))
        thread_text = ""
        if post_id:
            try:
                thread_text = self._format_thread(
                    agent, self.hub.get_post(post_id)
                )
            except Exception as exc:
                # Context is best-effort; a failed fetch must not block a reply.
                logger.warning("Could not fetch thread %s: %s", post_id, exc)

        from_agent = notification.get("from") or {}
        sender_name = (
            from_agent.get("name", "unknown")
            if isinstance(from_agent, dict)
            else str(from_agent)
        )

        prompt = f"""You received a notification:
Type: {notification.get('type', '')}
From: {sender_name}

Thread context:
{thread_text or '(thread context unavailable)'}

New message:
{content}

Write your reply as a comment in this thread. Be helpful and stay in
character. If, and only if, a response is genuinely not needed, reply
with exactly: SKIP"""

        response = self._generate(agent, brain, prompt)
        return self._quality_filter(agent, response)

    def _generate_dm_reply(self, agent, brain, sender_name, content) -> Optional[str]:
        prompt = f"""You received a direct message from {sender_name}:

{content}

Write your reply. If, and only if, a response is genuinely not needed,
reply with exactly: SKIP"""
        response = self._generate(agent, brain, prompt)
        return self._quality_filter(agent, response)

    def _generate(self, agent, brain: BaseBrain, user_prompt: str) -> str:
        return brain.generate(render_system_prompt(agent), [
            {"role": "user", "content": user_prompt}
        ])

    def _quality_filter(self, agent, response: Optional[str]) -> Optional[str]:
        """SKIP sentinel (ADR-009) and the quality floor, if configured."""
        if not response or not response.strip():
            return None
        if _SKIP_RE.match(response.strip().splitlines()[0]) and len(
            response.strip().splitlines()
        ) == 1:
            return None

        quality = (agent.behavior or {}).get("quality") or {}
        min_length = int(quality.get("min_response_length", 0))
        if min_length and len(response.strip()) < min_length:
            logger.info(
                "Response below quality floor (%d < %d chars)",
                len(response.strip()),
                min_length,
            )
            return None
        return response.strip()

    def _format_thread(self, agent, post: Dict[str, Any]) -> str:
        """Render a post + comments for the prompt, within the context budget."""
        brain_cfg = agent.brain or {}
        max_chars = int(brain_cfg.get("max_context_tokens", 100_000)) * CHARS_PER_TOKEN

        author = post.get("author") or {}
        lines = [
            f'Post "{post.get("title", "")}" by '
            f'{author.get("name", "unknown")} in {post.get("community", "")}:',
            post.get("content", ""),
            "",
            "Comments:",
        ]

        comments = list(post.get("comments") or [])
        # Newest last in the prompt; drop oldest first when over budget.
        selected: List[Dict[str, Any]] = []
        budget = max_chars - sum(len(line) + 1 for line in lines)
        for comment in reversed(comments):
            block = f"- {self._author_name(comment)}: {comment.get('content', '')}"
            if len(block) + 1 > budget:
                break
            selected.append(comment)
            budget -= len(block) + 1
        selected.reverse()

        if not selected:
            lines.append("(no comments yet)")
        for comment in selected:
            lines.append(
                f"- {self._author_name(comment)}: {comment.get('content', '')}"
            )
        return "\n".join(lines)

    @staticmethod
    def _author_name(item: Dict[str, Any]) -> str:
        author = item.get("author") or {}
        return author.get("name", "unknown") if isinstance(author, dict) else str(author)

    # -- discovery -----------------------------------------------------------

    def _run_discovery(
        self, agent, brain, tracker, discovery_settings, result
    ) -> None:
        """One discovery pass (ADR-010): score the feed, evaluate, maybe reply."""
        # Record the attempt first: a failed pass should not retry every poll.
        tracker.record_discovery()

        interests = normalize_interests(agent)
        if not any(interests.values()):
            logger.debug("Discovery enabled but the agent has no interests")
            return

        try:
            feed = self.hub.list_posts(sort="new", limit=DISCOVERY_FEED_LIMIT)
        except Exception as exc:
            logger.warning("Discovery feed fetch failed: %s", exc)
            result.errors += 1
            return

        candidates = filter_candidates(
            feed,
            interests,
            agent.name,
            already_responded=tracker.already_responded,
            discovery_settings=discovery_settings,
        )
        result.discovery_candidates = len(candidates)
        if not candidates:
            return

        min_confidence = discovery_settings["min_confidence"]
        logger.info(
            "Discovery: %d candidate posts (min_confidence=%.2f)",
            len(candidates),
            min_confidence,
        )

        for candidate in candidates[:MAX_DISCOVERY_EVALUATIONS]:
            post_id = str(candidate.post.get("id", ""))
            allowed, reason = tracker.can_comment(post_id)
            if not allowed:
                logger.debug(
                    "Discovery: post %s not commentable now (%s)", post_id, reason
                )
                if "daily comment limit" in reason:
                    break  # caps are spent; nothing else can go out today
                continue

            evaluation = self._generate(
                agent, brain, build_evaluation_prompt(agent.name, interests, candidate)
            )
            respond, confidence = parse_evaluation(evaluation)

            if not respond or confidence < min_confidence:
                logger.debug(
                    "Discovery: skipping post %s (respond=%s confidence=%.2f)",
                    post_id,
                    respond,
                    confidence,
                )
                tracker.mark_discovery_handled(post_id)
                continue

            reply = self._generate_discovery_reply(agent, brain, candidate)
            if not reply:
                tracker.mark_discovery_handled(post_id)
                result.skipped += 1
                continue

            if self.settings.dry_run:
                logger.info(
                    "[dry-run] would reply to discovery post %s: %r",
                    post_id,
                    reply[:200],
                )
                tracker.mark_discovery_handled(post_id)
                continue

            try:
                self.hub.create_comment(post_id, reply)
            except Exception as exc:
                logger.warning("Discovery reply to %s failed: %s", post_id, exc)
                result.errors += 1
                continue

            tracker.record_comment(post_id)
            tracker.mark_discovery_handled(post_id)
            result.discovery_replies += 1
            logger.info("Discovery: replied to post %s", post_id)

    def _generate_discovery_reply(self, agent, brain, candidate) -> Optional[str]:
        """Generate the actual reply for a post the evaluation approved."""
        post = candidate.post
        thread_text = self._format_thread(agent, post)

        prompt = f"""You found a post worth contributing to (matched your
interests: {", ".join(candidate.matched_on)}):

{thread_text}

Write your reply as a comment in this thread. Add genuine value: answer
the question, share relevant experience, or point to specifics. Do not
repeat what other commenters already said. If you find you cannot add
value after all, reply with exactly: SKIP"""

        return self._quality_filter(agent, self._generate(agent, brain, prompt))
