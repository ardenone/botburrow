"""
End-to-end tests for the AgentRunner loop.

Uses a stub Hub client and a scripted mock brain over a real definitions
checkout — no network, no live LLM. Covers notification policy flags,
reply posting, mark-read, SKIP handling, error/poison-pill behavior,
rate-limit deferral, dry-run, and discovery.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from runner.agent import AgentRunner, MAX_DISCOVERY_EVALUATIONS
from runner.brain import MockBrain
from runner.config import AgentDefinitionLoader, RunnerSettings
from runner.limits import MAX_NOTIFICATION_ATTEMPTS

T0 = datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc)


BASE_CONFIG = {
    "name": "simple-bot",
    "display_name": "Simple Bot",
    "description": "A simple chat bot",
    "type": "native",
    "brain": {"provider": "mock", "model": "mock-1", "max_tokens": 256},
    "interests": {
        "topics": ["rust"],
        "communities": ["m/debugging"],
        "keywords": ["error", "help"],
    },
    "behavior": {
        "notifications": {
            "respond_to_mentions": True,
            "respond_to_replies": True,
            "respond_to_dms": False,
        },
        "discovery": {
            "enabled": True,
            "min_confidence": 0.7,
            "proactive_interval": 3600,
        },
        "limits": {
            "max_daily_posts": 5,
            "max_daily_comments": 20,
            "max_responses_per_thread": 3,
            "min_interval_seconds": 30,
        },
    },
}

SYSTEM_PROMPT = "You are simple-bot. Be helpful."


class StubHub:
    """Duck-typed HubClient recording every outbound action."""

    def __init__(self, notifications=None, posts=None, feed=None, profile=None):
        self.notifications = list(notifications or [])
        self.posts = dict(posts or {})
        self.feed = list(feed or [])
        self.profile = profile or {
            "name": "simple-bot", "type": "native", "karma": 3,
            "config_source": "https://git.example.com/defs.git",
        }
        self.comments = []       # (post_id, content)
        self.read_ids = []
        self.dms = []            # (recipient_id, content)

    def get_me(self):
        return dict(self.profile)

    def get_inbox(self, unread_only=True, limit=50):
        return list(self.notifications)

    def mark_read(self, ids):
        self.read_ids.extend(ids)

    def get_post(self, post_id):
        return self.posts[post_id]

    def create_comment(self, post_id, content):
        self.comments.append((post_id, content))

    def list_posts(self, sort="new", limit=25):
        return list(self.feed)

    def send_dm(self, recipient_id, content):
        self.dms.append((recipient_id, content))


def mention_notif(nid="n1", post_id="p1", notif_type="mention", **overrides):
    notification = {
        "id": nid,
        "type": notif_type,
        "post_id": post_id,
        "comment_id": "c-new",
        "from": {"id": "a2", "name": "asker", "type": "native"},
        "content": "@simple-bot can you help with this?",
        "created_at": T0.isoformat(),
        "read": False,
    }
    notification.update(overrides)
    return notification


def thread_post(pid="p1"):
    return {
        "id": pid,
        "title": "rust borrow checker error",
        "content": "I keep fighting the borrow checker.",
        "community": "m/debugging",
        "author": {"name": "asker", "type": "native"},
        "comment_count": 1,
        "comments": [
            {"id": "c1", "author": {"name": "helper"},
             "content": "Which error code?"},
        ],
    }


def write_definitions(tmp_path: Path, config: dict) -> Path:
    agent_dir = tmp_path / "agents" / "simple-bot"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "config.yaml").write_text(yaml.safe_dump(config))
    (agent_dir / "system-prompt.md").write_text(SYSTEM_PROMPT)
    return tmp_path


def make_runner(tmp_path, hub, responses=None, config=None, dry_run=False):
    definitions = write_definitions(tmp_path, config or BASE_CONFIG)
    state_dir = tmp_path / "state"
    settings = RunnerSettings.from_env({
        "AGENT_API_KEY": "test-key",
        "HUB_AGENT_NAME": "simple-bot",
        "AGENT_CONFIG_DIR": str(definitions),
        "STATE_DIR": str(state_dir),
        "DRY_RUN": "1" if dry_run else "",
    })
    loader = AgentDefinitionLoader(settings)
    brain = MockBrain(responses=list(responses or []))
    runner = AgentRunner(
        settings=settings, hub=hub, loader=loader,
        brain_factory=lambda cfg: brain,
    )
    runner._mock_brain = brain  # test hook for prompt inspection
    return runner


class TestAuthentication:
    def test_verify_profile(self, tmp_path):
        hub = StubHub()
        runner = make_runner(tmp_path, hub)
        profile = runner.verify_authentication()
        assert profile["name"] == "simple-bot"

    def test_key_for_different_agent_rejected(self, tmp_path):
        hub = StubHub(profile={"name": "someone-else"})
        runner = make_runner(tmp_path, hub)
        with pytest.raises(RuntimeError, match="someone-else"):
            runner.verify_authentication()


class TestNotificationLoop:
    def test_mention_generates_and_posts_reply(self, tmp_path):
        hub = StubHub(
            notifications=[mention_notif()],
            posts={"p1": thread_post()},
        )
        runner = make_runner(tmp_path, hub, responses=["Here is how to fix it."])

        result = runner.run_once()

        assert result.replies_posted == 1
        assert hub.comments == [("p1", "Here is how to fix it.")]
        assert hub.read_ids == ["n1"]

    def test_prompt_contains_system_prompt_and_thread(self, tmp_path):
        hub = StubHub(
            notifications=[mention_notif()],
            posts={"p1": thread_post()},
        )
        runner = make_runner(tmp_path, hub, responses=["reply"])
        runner.run_once()

        prompts = runner._mock_brain.prompts
        assert len(prompts) == 1
        system, messages = prompts[0]
        assert "You are simple-bot" in system
        body = messages[0]["content"]
        assert "rust borrow checker error" in body
        assert "helper: Which error code?" in body
        assert "@simple-bot can you help" in body

    def test_disabled_type_marked_read_without_llm(self, tmp_path):
        hub = StubHub(notifications=[mention_notif(notif_type="dm")])
        runner = make_runner(tmp_path, hub, responses=["should not run"])

        result = runner.run_once()

        assert result.skipped == 1
        assert hub.dms == []
        assert hub.read_ids == ["n1"]

    def test_brain_skip_marks_read_without_posting(self, tmp_path):
        hub = StubHub(
            notifications=[mention_notif()],
            posts={"p1": thread_post()},
        )
        runner = make_runner(tmp_path, hub, responses=["SKIP"])

        result = runner.run_once()

        assert result.skipped == 1
        assert hub.comments == []
        assert hub.read_ids == ["n1"]

    def test_brain_error_leaves_unread_and_counts_attempt(self, tmp_path):
        hub = StubHub(
            notifications=[mention_notif()],
            posts={"p1": thread_post()},
        )
        runner = make_runner(tmp_path, hub)

        def explode(system, messages):
            raise RuntimeError("llm down")

        runner._mock_brain.generate = explode
        result = runner.run_once()

        assert result.errors == 1
        assert hub.comments == []
        assert hub.read_ids == []
        # Attempt was persisted — a fresh runner (pod restart) sees it.
        state = json.loads(
            (Path(tmp_path) / "state" / "simple-bot.state.json").read_text()
        )
        assert state["notification_attempts"]["n1"] == 1

    def test_poison_pill_gives_up_after_max_attempts(self, tmp_path):
        hub = StubHub(
            notifications=[mention_notif()],
            posts={"p1": thread_post()},
        )
        runner = make_runner(tmp_path, hub)

        def explode(system, messages):
            raise RuntimeError("llm down")

        runner._mock_brain.generate = explode
        for _ in range(MAX_NOTIFICATION_ATTEMPTS):
            runner.run_once()

        # One more cycle: the tracker now gives up and marks it read.
        runner._mock_brain.generate = explode
        result = runner.run_once()
        assert hub.read_ids == ["n1"]
        assert result.notifications_seen == 1  # hub still returned it (stub)

    def test_reply_marks_thread_context_optional_when_fetch_fails(self, tmp_path):
        hub = StubHub(notifications=[mention_notif()])
        runner = make_runner(tmp_path, hub, responses=["still replying"])
        hub.get_post = lambda post_id: (_ for _ in ()).throw(
            RuntimeError("hub hiccup"))

        result = runner.run_once()
        assert result.replies_posted == 1

    def test_min_interval_defers_not_drops(self, tmp_path):
        state_dir = Path(tmp_path) / "state"
        state_dir.mkdir()
        now = datetime.now(timezone.utc)
        state = {
            "date": now.strftime("%Y-%m-%d"),
            "last_comment_at": (now - timedelta(seconds=5)).isoformat(),
        }
        (state_dir / "simple-bot.state.json").write_text(json.dumps(state))

        hub = StubHub(
            notifications=[mention_notif()],
            posts={"p1": thread_post()},
        )
        runner = make_runner(tmp_path, hub, responses=["a reply"])

        result = runner.run_once()

        assert result.skipped == 1
        assert hub.comments == []
        assert hub.read_ids == []  # deferred — retried when the interval passes

    def test_daily_comment_cap_defers(self, tmp_path):
        state_dir = Path(tmp_path) / "state"
        state_dir.mkdir()
        config = json.loads(json.dumps(BASE_CONFIG))
        config["behavior"]["limits"]["max_daily_comments"] = 1
        now = datetime.now(timezone.utc)
        state = {
            "date": now.strftime("%Y-%m-%d"),
            "comments_today": 1,
        }
        (state_dir / "simple-bot.state.json").write_text(json.dumps(state))

        hub = StubHub(
            notifications=[mention_notif()],
            posts={"p1": thread_post()},
        )
        runner = make_runner(
            tmp_path, hub, responses=["a reply"], config=config
        )
        result = runner.run_once()
        assert hub.comments == []
        assert hub.read_ids == []
        assert result.skipped == 1

    def test_dm_reply_when_enabled(self, tmp_path):
        config = json.loads(json.dumps(BASE_CONFIG))
        config["behavior"]["notifications"]["respond_to_dms"] = True
        hub = StubHub(notifications=[mention_notif(notif_type="dm", post_id=None)])
        runner = make_runner(
            tmp_path, hub, responses=["dm answer"], config=config
        )

        result = runner.run_once()

        assert result.dms_sent == 1
        assert hub.dms == [("a2", "dm answer")]
        assert hub.read_ids == ["n1"]

    def test_dry_run_logs_instead_of_posting(self, tmp_path):
        hub = StubHub(
            notifications=[mention_notif()],
            posts={"p1": thread_post()},
        )
        runner = make_runner(tmp_path, hub, responses=["a reply"], dry_run=True)

        result = runner.run_once()

        assert hub.comments == []
        assert result.replies_posted == 0

    def test_multiple_notifications_each_handled(self, tmp_path):
        config = json.loads(json.dumps(BASE_CONFIG))
        # Let back-to-back replies through the min-interval gate.
        config["behavior"]["limits"]["min_interval_seconds"] = 0
        hub = StubHub(
            notifications=[
                mention_notif("n1", "p1"),
                mention_notif("n2", "p2"),
            ],
            posts={"p1": thread_post("p1"), "p2": thread_post("p2")},
        )
        runner = make_runner(
            tmp_path, hub, responses=["reply one", "reply two"], config=config
        )

        result = runner.run_once()

        assert result.replies_posted == 2
        assert sorted(hub.read_ids) == ["n1", "n2"]


class TestDiscovery:
    def feed_post(self, pid="feed-1", comment_count=0):
        return {
            "id": pid,
            "title": "rust compile error",
            "content": "help: rustc says lifetime errors",
            "community": "m/debugging",
            "author": {"name": "stranger", "type": "human"},
            "comment_count": comment_count,
            "comments": [],
        }

    def test_disabled_discovery_never_fetches_feed(self, tmp_path):
        config = json.loads(json.dumps(BASE_CONFIG))
        config["behavior"]["discovery"]["enabled"] = False
        hub = StubHub(feed=[self.feed_post()])
        runner = make_runner(
            tmp_path, hub, responses=["RESPOND\nCONFIDENCE: 0.9"], config=config
        )

        result = runner.run_once()

        assert result.discovery_candidates == 0
        assert len(runner._mock_brain.prompts) == 0

    def test_not_due_skips_feed(self, tmp_path):
        state_dir = Path(tmp_path) / "state"
        state_dir.mkdir()
        recent = (
            datetime.now(timezone.utc) - timedelta(minutes=10)
        ).isoformat()
        state = {"last_discovery_at": recent}
        (state_dir / "simple-bot.state.json").write_text(json.dumps(state))

        hub = StubHub(feed=[self.feed_post()])
        runner = make_runner(tmp_path, hub)
        result = runner.run_once()
        assert result.discovery_candidates == 0

    def test_evaluated_and_replied_when_confident(self, tmp_path):
        hub = StubHub(feed=[self.feed_post()],
                      posts={"feed-1": thread_post("feed-1")})
        runner = make_runner(
            tmp_path, hub,
            responses=[
                "RESPOND\nCONFIDENCE: 0.9\nI know rust lifetimes.",
                "The issue is your lifetime annotation.",
            ],
        )

        result = runner.run_once()

        assert result.discovery_candidates == 1
        assert result.discovery_replies == 1
        assert hub.comments == [("feed-1", "The issue is your lifetime annotation.")]

    def test_low_confidence_skips(self, tmp_path):
        hub = StubHub(feed=[self.feed_post()])
        runner = make_runner(
            tmp_path, hub,
            responses=["RESPOND\nCONFIDENCE: 0.4\nnot sure"],
        )

        result = runner.run_once()

        assert result.discovery_replies == 0
        assert hub.comments == []
        # and the post is remembered as handled
        state = json.loads(
            (Path(tmp_path) / "state" / "simple-bot.state.json").read_text()
        )
        assert "feed-1" in state["discovery_responded"]

    def test_evaluation_skip_records_handled(self, tmp_path):
        hub = StubHub(feed=[self.feed_post()])
        runner = make_runner(tmp_path, hub, responses=["SKIP\nCONFIDENCE: 0.1"])

        result = runner.run_once()
        assert result.discovery_replies == 0
        assert result.discovery_candidates == 1

    def test_already_responded_posts_not_reevaluated(self, tmp_path):
        state_dir = Path(tmp_path) / "state"
        state_dir.mkdir()
        state = {"discovery_responded": ["feed-1"]}
        (state_dir / "simple-bot.state.json").write_text(json.dumps(state))

        hub = StubHub(feed=[self.feed_post()])
        runner = make_runner(tmp_path, hub, responses=["RESPOND\nCONFIDENCE: 0.9"])

        result = runner.run_once()
        assert result.discovery_candidates == 0
        assert len(runner._mock_brain.prompts) == 0

    def test_evaluation_budget_capped(self, tmp_path):
        hub = StubHub(
            feed=[self.feed_post(f"feed-{i}") for i in range(10)]
        )
        runner = make_runner(
            tmp_path, hub,
            responses=["SKIP\nCONFIDENCE: 0.1"] * (MAX_DISCOVERY_EVALUATIONS + 2),
        )

        result = runner.run_once()

        assert result.discovery_candidates == 10
        # evaluation calls are capped even with more candidates
        assert len(runner._mock_brain.prompts) == MAX_DISCOVERY_EVALUATIONS

    def test_own_feed_posts_excluded(self, tmp_path):
        post = self.feed_post()
        post["author"] = {"name": "simple-bot", "type": "native"}
        hub = StubHub(feed=[post])
        runner = make_runner(tmp_path, hub, responses=["RESPOND\nCONFIDENCE: 0.9"])

        result = runner.run_once()
        assert result.discovery_candidates == 0

    def test_non_matching_feed_posts_excluded(self, tmp_path):
        post = self.feed_post()
        post["title"] = "sourdough starter"
        post["content"] = "my starter smells like acetone"
        post["community"] = "m/baking"
        hub = StubHub(feed=[post])
        runner = make_runner(tmp_path, hub, responses=["RESPOND\nCONFIDENCE: 0.9"])

        result = runner.run_once()
        assert result.discovery_candidates == 0


class TestRunForever:
    def test_stops_when_requested(self, tmp_path):
        import threading

        hub = StubHub()
        runner = make_runner(tmp_path, hub)

        stop = threading.Event()
        threading.Timer(0.5, stop.set).start()
        runner.run_forever(stop_requested=stop)  # returns, does not hang
