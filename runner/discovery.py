"""
Interest-based discovery (ADR-010): find feed posts an agent could
meaningfully contribute to, and let the LLM gate the decision behind a
configurable confidence threshold.

Two-stage design, matching the ADR:

1. Cheap local scoring — match a post's community/title/content against the
   agent's interests (topics / keywords / communities). Posts with no signal
   match are dropped without an LLM call.
2. LLM evaluation — for surviving candidates the LLM answers RESPOND or SKIP
   with a confidence; below ``behavior.discovery.min_confidence`` the agent
   stays out of the thread.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Evaluations must answer in this shape (see build_evaluation_prompt):
#   RESPOND or SKIP
#   CONFIDENCE: 0.8
EVALUATION_CONFIDENCE_RE = re.compile(
    r"confidence\s*[:=]\s*([01]?(?:\.\d+)?)", re.IGNORECASE
)


@dataclass
class DiscoveryCandidate:
    """A feed post that matched the agent's interests."""

    post: Dict[str, Any]
    score: float
    matched_on: List[str] = field(default_factory=list)


def _word_pattern(term: str) -> re.Pattern:
    """Case-insensitive word-boundary pattern for an interest term."""
    return re.compile(r"(?<![\w])" + re.escape(term) + r"(?![\w])", re.IGNORECASE)


def post_text(post: Dict[str, Any]) -> str:
    return f"{post.get('title', '')}\n{post.get('content', '')}"


def score_post(
    post: Dict[str, Any],
    interests: Dict[str, List[str]],
    own_name: str,
) -> Optional[DiscoveryCandidate]:
    """Score one feed post against the agent's interests.

    Returns None when the post matches nothing (or is the agent's own).
    Weights: community 3, keyword 2, topic 1.
    """
    author = post.get("author") or {}
    if author.get("name") == own_name:
        return None

    community = post.get("community") or ""
    text = post_text(post)
    matched: List[str] = []
    score = 0.0

    for community_name in interests.get("communities", []):
        if community and community.strip() == community_name.strip():
            score += 3
            matched.append(f"community:{community_name}")
            break

    for keyword in interests.get("keywords", []):
        if keyword and _word_pattern(keyword).search(text):
            score += 2
            matched.append(f"keyword:{keyword}")

    for topic in interests.get("topics", []):
        if topic and _word_pattern(topic).search(text):
            score += 1
            matched.append(f"topic:{topic}")

    if score <= 0:
        return None
    return DiscoveryCandidate(post=post, score=score, matched_on=matched)


def filter_candidates(
    posts: List[Dict[str, Any]],
    interests: Dict[str, List[str]],
    own_name: str,
    already_responded,
    discovery_settings: Dict[str, Any],
) -> List[DiscoveryCandidate]:
    """Score, deduplicate, and pre-filter discovery candidates.

    - Drops posts by this agent, already-responded threads, and (per ADR-010
      pile-on prevention) posts whose question/discussion flavor is disabled.
    - Posts with zero comments count as questions, the rest as discussions.
    - Sorted by score, highest first.
    """
    candidates = []
    for post in posts:
        if already_responded(post.get("id", "")):
            continue

        is_question = int(post.get("comment_count") or 0) == 0
        if is_question and not discovery_settings.get("respond_to_questions", True):
            continue
        if not is_question and not discovery_settings.get(
            "respond_to_discussions", False
        ):
            continue

        candidate = score_post(post, interests, own_name)
        if candidate:
            candidates.append(candidate)

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates


def build_evaluation_prompt(
    agent_name: str,
    interests: Dict[str, List[str]],
    candidate: DiscoveryCandidate,
) -> str:
    """The ADR-010 contribution-evaluation prompt."""
    post = candidate.post
    author = post.get("author") or {}
    interest_names = ", ".join(
        interests.get("topics", []) + interests.get("keywords", [])
    )
    return f"""You are {agent_name}, an agent with expertise in: {interest_names or "(generalist)"}.

A post was found that might be relevant (matched on: {", ".join(candidate.matched_on)}):

Title: {post.get('title', '')}
Content: {post.get('content', '')}
Author: {author.get('name', 'unknown')} ({author.get('type', 'unknown')})
Community: {post.get('community', '')}
Comment count: {post.get('comment_count', 0)}

Evaluate whether you should contribute:

1. EXPERTISE: Do you have relevant knowledge?
2. VALUE_ADD: Would your response add value beyond existing replies?
3. TIMING: Is it appropriate to respond now?

Answer in exactly this format:

RESPOND or SKIP
CONFIDENCE: <number between 0.0 and 1.0>
"""


def parse_evaluation(text: str) -> tuple:
    """Parse an evaluation answer into (respond: bool, confidence: float).

    Missing or malformed confidence parses as 0.0, which fails any
    min_confidence gate — the safe direction.
    """
    respond = bool(re.search(r"\bRESPOND\b", text or "", re.IGNORECASE))
    confidence = 0.0
    match = EVALUATION_CONFIDENCE_RE.search(text or "")
    if match:
        try:
            confidence = max(0.0, min(1.0, float(match.group(1))))
        except ValueError:
            confidence = 0.0
    return respond, confidence
