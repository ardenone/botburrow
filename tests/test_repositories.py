"""
Repository-level tests for the social-graph tables.

These exercise PostRepository, CommentRepository, VoteRepository,
CommunityRepository, SubscriptionRepository, FollowRepository and
NotificationRepository: the query shapes (sorting, filtering, pagination),
the vote flip/remove semantics, the idempotent subscribe/follow paths, the
denormalized-counter helpers, and the unread-inbox behavior.

Rows for ordering-sensitive reads are inserted directly with explicit
created_at values: SQLite's CURRENT_TIMESTAMP has one-second resolution, so
several rows created in a burst would tie and make "newest first"
nondeterministic. Repositories are used wherever the behavior under test is
the repository's own.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from botburrow_hub.database import (
    Comment,
    Follow,
    Notification,
    Post,
    CommentRepository,
    CommunityRepository,
    FollowRepository,
    NotificationRepository,
    PostRepository,
    SubscriptionRepository,
    VoteRepository,
)

pytestmark = pytest.mark.asyncio


def _ago(hours: float) -> datetime:
    """A timezone-aware timestamp `hours` in the past."""
    return datetime.now(timezone.utc) - timedelta(hours=hours)


async def _make_post(session, author_id, **kwargs):
    """Insert a Post directly, so tests can pin created_at."""
    kwargs.setdefault("created_at", _ago(1))
    post = Post(author_id=author_id, **kwargs)
    session.add(post)
    await session.flush()
    return post


async def _make_comment(session, post_id, author_id, content="c", **kwargs):
    comment = Comment(
        post_id=post_id, author_id=author_id, content=content, **kwargs
    )
    session.add(comment)
    await session.flush()
    return comment


# ---------------------------------------------------------------- posts


async def test_post_repository_roundtrip(session, make_agent):
    author = await make_agent()
    repo = PostRepository(session)
    post = await repo.create(
        author_id=author.id,
        title="hello",
        content="world",
        community="debugging",
        media_url="https://x/i.png",
        media_type="image",
        media_description="a picture",
    )

    loaded = await repo.get_by_id(post.id)
    assert loaded is not None
    assert loaded.title == "hello"
    assert loaded.score == 0
    assert loaded.comment_count == 0
    assert loaded.community == "debugging"

    as_dict = loaded.to_dict()
    assert as_dict["id"] == str(loaded.id)
    assert as_dict["media_type"] == "image"
    assert as_dict["comment_count"] == 0


async def test_post_list_sort_new_then_top(session, make_agent):
    author = await make_agent()
    old = await _make_post(session, author.id, title="old", created_at=_ago(72))
    new = await _make_post(session, author.id, title="new", created_at=_ago(1))

    posts = PostRepository(session)
    assert [p.id for p in await posts.list(sort="new")] == [new.id, old.id]

    await posts.adjust_score(old.id, 10)
    top = await posts.list(sort="top")
    assert [p.id for p in top] == [old.id, new.id]  # score wins over recency


async def test_post_list_sort_top_tiebreak_is_recency(session, make_agent):
    author = await make_agent()
    a = await _make_post(session, author.id, title="a", created_at=_ago(5))
    b = await _make_post(session, author.id, title="b", created_at=_ago(2))

    posts = PostRepository(session)
    await posts.adjust_score(a.id, 3)
    await posts.adjust_score(b.id, 3)
    assert [p.id for p in await posts.list(sort="top")] == [b.id, a.id]


async def test_post_list_sort_hot_ignores_stale_posts(session, make_agent):
    author = await make_agent()
    stale = await _make_post(session, author.id, title="stale", created_at=_ago(72))
    fresh = await _make_post(session, author.id, title="fresh", created_at=_ago(1))
    await PostRepository(session).adjust_score(stale.id, 100)  # stale but high

    posts = PostRepository(session)
    assert [p.id for p in await posts.list(sort="hot")] == [fresh.id]


async def test_post_list_sort_rising_needs_score(session, make_agent):
    author = await make_agent()
    scored = await _make_post(session, author.id, title="scored", created_at=_ago(1))
    flat = await _make_post(session, author.id, title="flat", created_at=_ago(1))
    stale_hit = await _make_post(
        session, author.id, title="stale_hit", created_at=_ago(48)
    )
    await PostRepository(session).adjust_score(scored.id, 2)
    await PostRepository(session).adjust_score(stale_hit.id, 50)

    posts = PostRepository(session)
    rising = await posts.list(sort="rising")
    assert [p.id for p in rising] == [scored.id]  # flat has no score,
    # stale_hit is outside the rising window despite its score


async def test_post_list_sort_rejects_unknown_sort(session, make_agent):
    author = await make_agent()
    await _make_post(session, author.id)
    with pytest.raises(ValueError, match="sort"):
        await PostRepository(session).list(sort="controversial")


async def test_post_list_filters(session, make_agent):
    a, b = await make_agent(), await make_agent()
    in_debugging = await _make_post(
        session, a.id, title="d", community="debugging"
    )
    in_main = await _make_post(session, a.id, title="m")  # community IS NULL
    by_b = await _make_post(session, b.id, title="b", community="debugging")

    posts = PostRepository(session)
    assert {p.id for p in await posts.list(community="debugging")} == {
        in_debugging.id,
        by_b.id,
    }
    assert [p.id for p in await posts.list(main_feed=True)] == [in_main.id]
    assert {p.id for p in await posts.list(author_id=b.id)} == {by_b.id}
    with pytest.raises(ValueError, match="mutually exclusive"):
        await posts.list(community="debugging", main_feed=True)


async def test_post_list_pagination(session, make_agent):
    author = await make_agent()
    created = [
        await _make_post(session, author.id, title=f"p{i}", created_at=_ago(i))
        for i in range(5)
    ]
    posts = PostRepository(session)

    page = await posts.list(sort="new", offset=1, limit=2)
    assert [p.id for p in page] == [created[1].id, created[2].id]


async def test_post_counter_helpers_and_delete(session, make_agent):
    author = await make_agent()
    posts = PostRepository(session)
    post = await posts.create(author_id=author.id, title="t")

    await posts.increment_comment_count(post.id)
    await posts.adjust_score(post.id, 1)
    await posts.adjust_score(post.id, 1)
    fresh = (
        await session.execute(select(Post).where(Post.id == post.id))
    ).scalar_one()
    assert (fresh.comment_count, fresh.score) == (1, 2)

    assert await posts.delete(post.id) is True
    assert await posts.delete(post.id) is False
    assert await posts.get_by_id(post.id) is None


# ------------------------------------------------------------- comments


async def test_comment_repository_sort_and_pagination(session, make_agent):
    author = await make_agent()
    post = await _make_post(session, author.id)
    c_old = await _make_comment(
        session, post.id, author.id, content="old", created_at=_ago(3)
    )
    c_new = await _make_comment(
        session, post.id, author.id, content="new", created_at=_ago(1)
    )
    # A comment on a different post must not leak in.
    other_post = await _make_post(session, author.id, title="other")
    await _make_comment(session, other_post.id, author.id, content="elsewhere")

    comments = CommentRepository(session)
    assert [c.id for c in await comments.list_by_post(post.id, sort="new")] == [
        c_new.id,
        c_old.id,
    ]

    await comments.adjust_score(c_old.id, 5)
    top = await comments.list_by_post(post.id, sort="top")
    assert [c.id for c in top] == [c_old.id, c_new.id]

    page = await comments.list_by_post(post.id, sort="new", offset=1, limit=1)
    assert [c.id for c in page] == [c_old.id]

    with pytest.raises(ValueError, match="sort"):
        await comments.list_by_post(post.id, sort="hot")


async def test_comment_threading_keeps_parent_link(session, make_agent):
    author = await make_agent()
    post = await _make_post(session, author.id)
    comments = CommentRepository(session)
    top = await comments.create(post_id=post.id, author_id=author.id, content="top")
    reply = await comments.create(
        post_id=post.id, author_id=author.id, content="reply", parent_id=top.id
    )
    loaded = await comments.get_by_id(reply.id)
    assert loaded.parent_id == top.id
    assert loaded.to_dict()["parent_id"] == str(top.id)


# ---------------------------------------------------------------- votes


async def test_vote_cast_create_flip_remove_on_post(session, make_agent):
    author, voter = await make_agent(), await make_agent()
    post = await _make_post(session, author.id)
    votes = VoteRepository(session)
    posts = PostRepository(session)

    result = await votes.cast(voter.id, 1, post_id=post.id)
    assert (result.action, result.score_delta) == ("created", 1)
    assert result.vote.value == 1

    # Same direction again: unvotes.
    result = await votes.cast(voter.id, 1, post_id=post.id)
    assert (result.action, result.score_delta) == ("removed", -1)
    assert result.vote is None
    assert await votes.get_existing(voter.id, post_id=post.id) is None

    # Downvote from no vote, then flip back up.
    result = await votes.cast(voter.id, -1, post_id=post.id)
    assert (result.action, result.score_delta) == ("created", -1)
    result = await votes.cast(voter.id, 1, post_id=post.id)
    assert (result.action, result.score_delta) == ("flipped", 2)
    existing = await votes.get_existing(voter.id, post_id=post.id)
    assert existing.value == 1

    # On a fresh post, replaying up-up-down-up must leave the recorded net
    # delta and the target score in exact agreement: created(+1),
    # removed(-1), created(-1), flipped(+2) → score 1. (Re-read with
    # populate_existing: adjust_score is a bulk UPDATE, so the cached post
    # object would otherwise show its original score.)
    ledger = await _make_post(session, author.id, title="ledger")
    score = 0
    for action in (1, 1, -1, 1):
        result = await votes.cast(voter.id, action, post_id=ledger.id)
        score += result.score_delta
        await posts.adjust_score(ledger.id, result.score_delta)
    fresh = (
        await session.execute(
            select(Post)
            .where(Post.id == ledger.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert fresh.score == score == 1


async def test_vote_cast_on_comment_is_independent(session, make_agent):
    author, voter = await make_agent(), await make_agent()
    post = await _make_post(session, author.id)
    comment = await _make_comment(session, post.id, author.id)

    votes = VoteRepository(session)
    result = await votes.cast(voter.id, -1, comment_id=comment.id)
    assert (result.action, result.score_delta) == ("created", -1)
    assert result.vote.comment_id == comment.id
    assert result.vote.post_id is None

    existing = await votes.get_existing(voter.id, comment_id=comment.id)
    assert existing is not None
    assert await votes.get_existing(voter.id, post_id=post.id) is None


async def test_vote_cast_rejects_bad_input(session, make_agent):
    author = await make_agent()
    post = await _make_post(session, author.id)
    votes = VoteRepository(session)

    with pytest.raises(ValueError, match="-1 or 1"):
        await votes.cast(author.id, 0, post_id=post.id)
    with pytest.raises(ValueError, match="exactly one"):
        await votes.cast(author.id, 1)
    with pytest.raises(ValueError, match="exactly one"):
        await votes.cast(author.id, 1, post_id=post.id, comment_id=post.id)


# ----------------------------------------------------------- communities


async def test_community_repository_create_get_list(session, make_agent):
    agent = await make_agent()
    communities = CommunityRepository(session)
    await communities.create("debugging", description="bugs", creator_id=agent.id)
    await communities.create("research")

    got = await communities.get("debugging")
    assert got.description == "bugs"
    assert got.creator_id == agent.id
    assert got.subscriber_count == 0
    assert await communities.get("nope") is None

    # list() orders by subscriber_count DESC (then name); counts travel on
    # the rows the SubscriptionRepository maintains.
    subs = SubscriptionRepository(session)
    await subs.subscribe(agent.id, "research")
    listed = await communities.list()
    assert [c.name for c in listed] == ["research", "debugging"]
    assert listed[0].subscriber_count == 1


# --------------------------------------------------------- subscriptions


async def test_subscription_is_idempotent_and_maintains_count(
    session, make_agent
):
    agent = await make_agent()
    communities = CommunityRepository(session)
    await communities.create("debugging")
    subs = SubscriptionRepository(session)

    first = await subs.subscribe(agent.id, "debugging")
    again = await subs.subscribe(agent.id, "debugging")
    assert again.agent_id == first.agent_id  # same row, no duplicate
    assert (await communities.get("debugging")).subscriber_count == 1

    assert await subs.unsubscribe(agent.id, "debugging") is True
    assert (await communities.get("debugging")).subscriber_count == 0
    assert await subs.unsubscribe(agent.id, "debugging") is False
    assert (await communities.get("debugging")).subscriber_count == 0

    await communities.create("research")
    await subs.subscribe(agent.id, "debugging")
    await subs.subscribe(agent.id, "research")
    assert await subs.list_for_agent(agent.id) == ["debugging", "research"]


async def test_subscription_list_for_agent_is_sorted(session, make_agent):
    agent = await make_agent()
    subs = SubscriptionRepository(session)
    for name in ("research", "debugging", "algorithms"):
        await CommunityRepository(session).create(name)
        await subs.subscribe(agent.id, name)
    assert await subs.list_for_agent(agent.id) == [
        "algorithms",
        "debugging",
        "research",
    ]


# --------------------------------------------------------------- follows


async def test_follow_repository_idempotent_and_directional(session, make_agent):
    a, b = await make_agent(), await make_agent()
    follows = FollowRepository(session)

    edge = await follows.follow(a.id, b.id)
    assert await follows.follow(a.id, b.id) is not None  # no duplicate, no error
    assert await follows.is_following(a.id, b.id) is True
    assert await follows.is_following(b.id, a.id) is False  # direction matters

    assert await follows.list_following(a.id) == [b.id]
    assert await follows.list_followers(b.id) == [a.id]
    assert await follows.list_following(b.id) == []
    assert await follows.list_followers(a.id) == []

    assert await follows.unfollow(a.id, b.id) is True
    assert await follows.unfollow(a.id, b.id) is False
    assert await follows.is_following(a.id, b.id) is False
    assert edge.follower_id == a.id


async def test_follow_lists_order_newest_first(session, make_agent):
    a = await make_agent()
    follows = FollowRepository(session)
    # Follow targets are agents; create them so the FK holds.
    targets = [await make_agent() for _ in range(3)]
    for i, target in enumerate(targets):
        await follows.follow(a.id, target.id)
        # created_at has one-second resolution in SQLite; stagger explicitly.
        await session.execute(
            update(Follow)
            .where(
                Follow.follower_id == a.id, Follow.following_id == target.id
            )
            .values(created_at=_ago(i))
        )

    assert await follows.list_following(a.id) == [t.id for t in targets]


# --------------------------------------------------------- notifications


async def _make_notification(session, recipient_id, **kwargs):
    kwargs.setdefault("created_at", _ago(1))
    notification = Notification(recipient_id=recipient_id, **kwargs)
    session.add(notification)
    await session.flush()
    return notification


async def test_notification_create_truncates_preview(session, make_agent):
    recipient = await make_agent()
    repo = NotificationRepository(session)
    long = "x" * 500
    notification = await repo.create(
        recipient_id=recipient.id, type="mention", content_preview=long
    )
    assert len(notification.content_preview) == repo.PREVIEW_MAX_LENGTH == 200
    assert notification.read is False
    assert notification.read_at is None


async def test_notification_list_unread_filter_and_pagination(
    session, make_agent
):
    recipient = await make_agent()
    other = await make_agent()
    repo = NotificationRepository(session)

    oldest = await _make_notification(
        session, recipient.id, type="follow", created_at=_ago(3)
    )
    middle = await _make_notification(
        session, recipient.id, type="mention", created_at=_ago(2)
    )
    newest = await _make_notification(
        session, recipient.id, type="reply", created_at=_ago(1)
    )
    await _make_notification(session, other.id, type="reply", created_at=_ago(1))

    inbox = await repo.list_for_recipient(recipient.id)
    assert [n.id for n in inbox] == [newest.id, middle.id, oldest.id]

    # Mark the newest read via the update path, then filter.
    assert await repo.mark_read([newest.id]) == 1
    unread = await repo.list_for_recipient(recipient.id, unread_only=True)
    assert [n.id for n in unread] == [middle.id, oldest.id]

    page = await repo.list_for_recipient(
        recipient.id, unread_only=True, offset=1, limit=1
    )
    assert [n.id for n in page] == [oldest.id]

    # The other agent's inbox was untouched.
    assert len(await repo.list_for_recipient(other.id)) == 1


async def test_notification_mark_read_counts_and_scopes(session, make_agent):
    r1, r2 = await make_agent(), await make_agent()
    repo = NotificationRepository(session)
    n1 = await repo.create(recipient_id=r1.id, type="reply")
    n2 = await repo.create(recipient_id=r1.id, type="mention")
    foreign = await repo.create(recipient_id=r2.id, type="reply")

    # Marking an already-read notification does not count again.
    assert await repo.mark_read([n1.id]) == 1
    assert await repo.mark_read([n1.id]) == 0

    # recipient_id scope keeps one agent from marking another's inbox.
    assert await repo.mark_read([foreign.id], recipient_id=r1.id) == 0
    assert await repo.mark_read([foreign.id], recipient_id=r2.id) == 1

    assert await repo.mark_read([n2.id, foreign.id], recipient_id=r1.id) == 1
    assert await repo.mark_read([], recipient_id=r1.id) == 0

    read_at = (
        await session.execute(
            select(Notification.read_at).where(Notification.id == n1.id)
        )
    ).scalar_one()
    assert read_at is not None

    assert await repo.unread_count(r1.id) == 0
    assert await repo.total_count(r1.id) == 2


async def test_notification_mark_all_read_and_counts(session, make_agent):
    r1, r2 = await make_agent(), await make_agent()
    repo = NotificationRepository(session)
    for kind in ("reply", "mention", "follow"):
        await repo.create(recipient_id=r1.id, type=kind)
    await repo.create(recipient_id=r2.id, type="reply")

    assert await repo.unread_count(r1.id) == 3
    assert await repo.total_count(r1.id) == 3
    assert await repo.mark_all_read(r1.id) == 3
    assert await repo.mark_all_read(r1.id) == 0  # second sweep marks nothing
    assert await repo.unread_count(r1.id) == 0
    assert await repo.total_count(r1.id) == 3
    # The other inbox is untouched.
    assert await repo.unread_count(r2.id) == 1
