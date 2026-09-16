"""
Tests for interest-based discovery (ADR-010).

Covers scoring (community/keyword/topic weights, own-post exclusion,
word-boundary matching), candidate filtering (responded, question/discussion
flavor), evaluation prompt shape, and RESPOND/CONFIDENCE parsing.
"""

from runner.discovery import (
    DiscoveryCandidate,
    build_evaluation_prompt,
    filter_candidates,
    parse_evaluation,
    score_post,
)

INTERESTS = {
    "topics": ["rust", "kubernetes"],
    "communities": ["m/debugging"],
    "keywords": ["error", "help"],
}

DISCOVERY_SETTINGS = {
    "enabled": True,
    "min_confidence": 0.7,
    "respond_to_questions": True,
    "respond_to_discussions": False,
}


def make_post(pid="p1", title="Borrow checker trouble", content="Getting errors",
              community="m/rust", author="someone", comment_count=0):
    return {
        "id": pid,
        "title": title,
        "content": content,
        "community": community,
        "author": {"name": author, "type": "human"},
        "comment_count": comment_count,
    }


class TestScoring:
    def test_no_match_returns_none(self):
        post = make_post(title="Gardening tips",
                         content="How do tomatoes like coffee grounds?")
        assert score_post(post, INTERESTS, "me") is None

    def test_community_match_scores_highest(self):
        post = make_post(community="m/debugging")
        candidate = score_post(post, INTERESTS, "me")
        assert candidate.score == 3
        assert "community:m/debugging" in candidate.matched_on

    def test_keyword_and_topic_stack(self):
        post = make_post(content="rust error when compiling")
        candidate = score_post(post, INTERESTS, "me")
        assert candidate.score == 3  # topic rust (1) + keyword error (2)

    def test_own_posts_excluded(self):
        post = make_post(author="me", content="rust error help")
        assert score_post(post, INTERESTS, "me") is None

    def test_word_boundaries_respected(self):
        # "trust" contains "rust" but is not a rust mention.
        post = make_post(content="I trust this process completely")
        assert score_post(post, INTERESTS, "me") is None

    def test_case_insensitive(self):
        post = make_post(content="RUST Error while building")
        candidate = score_post(post, INTERESTS, "me")
        assert candidate is not None


class TestFiltering:
    def test_sorts_by_score(self):
        weak = make_post("weak", content="rust things")           # topic only
        strong = make_post("strong", community="m/debugging")     # community
        candidates = filter_candidates(
            [weak, strong], INTERESTS, "me", lambda pid: False, DISCOVERY_SETTINGS
        )
        assert [c.post["id"] for c in candidates] == ["strong", "weak"]

    def test_already_responded_excluded(self):
        post = make_post("p1", community="m/debugging")
        candidates = filter_candidates(
            [post], INTERESTS, "me", lambda pid: pid == "p1", DISCOVERY_SETTINGS
        )
        assert candidates == []

    def test_discussions_filtered_when_disabled(self):
        post = make_post("p1", community="m/debugging", comment_count=3)
        candidates = filter_candidates(
            [post], INTERESTS, "me", lambda pid: False, DISCOVERY_SETTINGS
        )
        assert candidates == []

    def test_questions_kept_when_enabled(self):
        post = make_post("p1", community="m/debugging", comment_count=0)
        candidates = filter_candidates(
            [post], INTERESTS, "me", lambda pid: False, DISCOVERY_SETTINGS
        )
        assert len(candidates) == 1

    def test_discussions_kept_when_enabled(self):
        post = make_post("p1", community="m/debugging", comment_count=3)
        settings = {**DISCOVERY_SETTINGS, "respond_to_discussions": True}
        candidates = filter_candidates(
            [post], INTERESTS, "me", lambda pid: False, settings
        )
        assert len(candidates) == 1


class TestEvaluationPrompt:
    def test_prompt_includes_post_and_interests(self):
        candidate = DiscoveryCandidate(
            post=make_post(), score=5, matched_on=["topic:rust"])
        prompt = build_evaluation_prompt("my-agent", INTERESTS, candidate)
        assert "my-agent" in prompt
        assert "rust" in prompt
        assert "Borrow checker trouble" in prompt
        assert "RESPOND or SKIP" in prompt
        assert "CONFIDENCE" in prompt


class TestParseEvaluation:
    def test_respond_with_confidence(self):
        text = "RESPOND\nCONFIDENCE: 0.85\nI know rust async well."
        assert parse_evaluation(text) == (True, 0.85)

    def test_skip(self):
        assert parse_evaluation("SKIP\nCONFIDENCE: 0.1\nAlready answered.") == (
            False,
            0.1,
        )

    def test_missing_confidence_is_zero(self):
        assert parse_evaluation("RESPOND\nLooks interesting.") == (True, 0.0)

    def test_out_of_range_clamped(self):
        assert parse_evaluation("RESPOND\nCONFIDENCE: 1.7")[1] == 1.0
        assert parse_evaluation("RESPOND\nCONFIDENCE: -3")[1] == 0.0

    def test_garbage_is_skip_zero(self):
        assert parse_evaluation("oops") == (False, 0.0)
        assert parse_evaluation("") == (False, 0.0)
