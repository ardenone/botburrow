"""
Tests for behavior limits and the persistent rate tracker.

Covers daily caps, per-thread caps, min interval, UTC date rollover,
state persistence across restarts, corrupt-state recovery, discovery
cadence, and the notification poison-pill counter.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from runner.limits import MAX_NOTIFICATION_ATTEMPTS, RateTracker

LIMITS = {
    "max_daily_posts": 2,
    "max_daily_comments": 3,
    "max_responses_per_thread": 2,
    "min_interval_seconds": 60,
}

T0 = datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc)


def make_tracker(tmp_path, limits=LIMITS, state=None):
    state_file = Path(tmp_path) / "state.json"
    tracker = RateTracker(state_file, limits)
    if state:
        tracker.state.update(state)
    return tracker


class TestCommentLimits:
    def test_allows_then_counts(self, tmp_path):
        tracker = make_tracker(tmp_path)
        assert tracker.can_comment("p1", now=T0) == (True, "")
        tracker.record_comment("p1", now=T0)
        assert tracker.state["comments_today"] == 1
        assert tracker.state["thread_responses"]["p1"] == 1

    def test_daily_cap(self, tmp_path):
        tracker = make_tracker(tmp_path)
        for i in range(LIMITS["max_daily_comments"]):
            tracker.record_comment(f"p{i}", now=T0)
        allowed, reason = tracker.can_comment("px", now=T0 + timedelta(seconds=61))
        assert not allowed
        assert "daily comment limit" in reason

    def test_thread_cap(self, tmp_path):
        tracker = make_tracker(tmp_path)
        tracker.record_comment("p1", now=T0)
        tracker.record_comment("p1", now=T0 + timedelta(seconds=60))
        allowed, reason = tracker.can_comment(
            "p1", now=T0 + timedelta(seconds=120)
        )
        assert not allowed
        assert "thread response limit" in reason

    def test_min_interval_blocks(self, tmp_path):
        tracker = make_tracker(tmp_path)
        tracker.record_comment("p1", now=T0)
        allowed, reason = tracker.can_comment("p2", now=T0 + timedelta(seconds=10))
        assert not allowed
        assert "min_interval_seconds" in reason

    def test_min_interval_elapses(self, tmp_path):
        tracker = make_tracker(tmp_path)
        tracker.record_comment("p1", now=T0)
        allowed, _ = tracker.can_comment("p2", now=T0 + timedelta(seconds=60))
        assert allowed


class TestPostLimits:
    def test_daily_cap(self, tmp_path):
        tracker = make_tracker(tmp_path)
        tracker.record_post(now=T0)
        tracker.record_post(now=T0 + timedelta(seconds=60))
        allowed, reason = tracker.can_post(now=T0 + timedelta(seconds=120))
        assert not allowed
        assert "daily post limit" in reason


class TestDateRollover:
    def test_counters_reset_next_day(self, tmp_path):
        tracker = make_tracker(tmp_path)
        tracker.record_comment("p1", now=T0)
        tracker.record_post(now=T0)
        tomorrow = T0 + timedelta(days=1)
        allowed, _ = tracker.can_comment("p2", now=tomorrow)
        assert allowed
        assert tracker.state["comments_today"] == 0
        assert tracker.state["posts_today"] == 0
        # Thread history survives the day — max_responses_per_thread is not
        # a daily concept.
        assert tracker.state["thread_responses"]["p1"] == 1


class TestPersistence:
    def test_state_survives_reload(self, tmp_path):
        tracker = make_tracker(tmp_path)
        tracker.record_comment("p1", now=T0)
        tracker.record_discovery(now=T0)

        reloaded = RateTracker(Path(tmp_path) / "state.json", LIMITS)
        assert reloaded.state["comments_today"] == 1
        assert reloaded.state["thread_responses"]["p1"] == 1
        assert reloaded.state["last_discovery_at"] == T0.isoformat()

    def test_corrupt_state_starts_fresh(self, tmp_path):
        state_file = Path(tmp_path) / "state.json"
        state_file.write_text("{not json")
        tracker = RateTracker(state_file, LIMITS)
        assert tracker.state["comments_today"] == 0
        tracker.record_comment("p1", now=T0)  # and remains writable
        assert tracker.state["comments_today"] == 1

    def test_save_is_atomic(self, tmp_path):
        tracker = make_tracker(tmp_path)
        tracker.record_comment("p1", now=T0)
        assert not (Path(tmp_path) / "state.tmp").exists()
        assert json.loads((Path(tmp_path) / "state.json").read_text())[
            "comments_today"] == 1


class TestDiscoveryCadence:
    def test_due_when_never_ran(self, tmp_path):
        assert make_tracker(tmp_path).discovery_due(3600, now=T0)

    def test_not_due_inside_interval(self, tmp_path):
        tracker = make_tracker(tmp_path)
        tracker.record_discovery(now=T0)
        assert not tracker.discovery_due(3600, now=T0 + timedelta(minutes=30))

    def test_due_after_interval(self, tmp_path):
        tracker = make_tracker(tmp_path)
        tracker.record_discovery(now=T0)
        assert tracker.discovery_due(3600, now=T0 + timedelta(minutes=61))


class TestRespondedTracking:
    def test_already_responded(self, tmp_path):
        tracker = make_tracker(tmp_path)
        tracker.record_comment("p1", now=T0)
        assert tracker.already_responded("p1")
        assert not tracker.already_responded("p2")

    def test_mark_discovery_handled(self, tmp_path):
        tracker = make_tracker(tmp_path)
        tracker.mark_discovery_handled("p9")
        assert tracker.already_responded("p9")

    def test_handled_history_pruned(self, tmp_path):
        tracker = make_tracker(tmp_path)
        for i in range(600):
            tracker.mark_discovery_handled(f"p{i}")
        assert len(tracker.state["discovery_responded"]) == 500
        assert "p599" in tracker.state["discovery_responded"]


class TestNotificationAttempts:
    def test_attempts_accumulate(self, tmp_path):
        tracker = make_tracker(tmp_path)
        for _ in range(MAX_NOTIFICATION_ATTEMPTS):
            assert not tracker.attempts_exhausted("n1")
            tracker.record_attempt("n1")
        assert tracker.attempts_exhausted("n1")

    def test_clear_on_success(self, tmp_path):
        tracker = make_tracker(tmp_path)
        tracker.record_attempt("n1")
        tracker.clear_attempt("n1")
        assert not tracker.attempts_exhausted("n1")

    def test_attempt_counts_are_per_notification(self, tmp_path):
        tracker = make_tracker(tmp_path)
        tracker.record_attempt("n1")
        assert not tracker.attempts_exhausted("n2")
