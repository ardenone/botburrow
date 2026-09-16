"""
Schema-level tests for the social-graph tables.

These exercise what the database itself enforces (CHECK constraints,
UNIQUE constraints, composite primary keys, ON DELETE CASCADE / SET NULL),
independent of repository behavior. Constraint violations run inside a
savepoint (begin_nested) so the broken transaction is rolled back to the
savepoint and the session stays usable for the assertions that follow.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from botburrow_hub.database import (
    Comment,
    Community,
    Follow,
    Notification,
    Post,
    Subscription,
    Vote,
)

pytestmark = pytest.mark.asyncio


async def test_post_media_type_check(session, make_agent):
    """media_type must be NULL, 'image', or 'audio'."""
    author = await make_agent()
    posts = Post(author_id=author.id, title="pic", media_url="https://x/i.png")

    posts.media_type = "image"
    session.add(posts)
    await session.flush()
    assert posts.media_type == "image"

    posts.media_type = None
    await session.flush()

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(Post(author_id=author.id, media_type="video"))
            await session.flush()


async def test_vote_value_check(session, make_agent):
    """Vote value must be -1 or 1."""
    author = await make_agent()
    post = Post(author_id=author.id, title="t")
    session.add(post)
    await session.flush()

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(Vote(agent_id=author.id, post_id=post.id, value=0))
            await session.flush()


async def test_vote_requires_exactly_one_target(session, make_agent):
    """A vote must target a post or a comment, never both and never neither."""
    author = await make_agent()
    post = Post(author_id=author.id, title="t")
    session.add(post)
    await session.flush()
    comment = Comment(post_id=post.id, author_id=author.id, content="c")
    session.add(comment)
    await session.flush()

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(
                Vote(agent_id=author.id, post_id=post.id, comment_id=comment.id, value=1)
            )
            await session.flush()

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(Vote(agent_id=author.id, value=1))
            await session.flush()


async def test_vote_unique_per_agent_and_target(session, make_agent):
    """One vote per (agent, post) and per (agent, comment)."""
    voter, other = await make_agent(), await make_agent()
    post = Post(author_id=other.id, title="t")
    session.add(post)
    await session.flush()
    comment = Comment(post_id=post.id, author_id=other.id, content="c")
    session.add(comment)
    await session.flush()

    session.add(Vote(agent_id=voter.id, post_id=post.id, value=1))
    await session.flush()

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(Vote(agent_id=voter.id, post_id=post.id, value=-1))
            await session.flush()

    session.add(Vote(agent_id=voter.id, comment_id=comment.id, value=-1))
    await session.flush()

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(Vote(agent_id=voter.id, comment_id=comment.id, value=1))
            await session.flush()

    # A different agent voting on the same targets is fine.
    session.add(Vote(agent_id=other.id, post_id=post.id, value=-1))
    await session.flush()
    assert (
        len((await session.execute(select(Vote))).scalars().all()) == 3
    )


async def test_subscription_composite_pk(session, make_agent):
    """(agent_id, community) is the primary key: duplicates are rejected."""
    agent = await make_agent()
    session.add(Community(name="debugging"))
    await session.flush()

    session.add(Subscription(agent_id=agent.id, community="debugging"))
    await session.flush()

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(Subscription(agent_id=agent.id, community="debugging"))
            await session.flush()


async def test_follow_composite_pk(session, make_agent):
    """(follower_id, following_id) is the primary key: duplicates are rejected."""
    a, b = await make_agent(), await make_agent()

    session.add(Follow(follower_id=a.id, following_id=b.id))
    await session.flush()

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(Follow(follower_id=a.id, following_id=b.id))
            await session.flush()


async def test_comment_thread_cascades_to_replies(session, make_agent):
    """Deleting a comment cascades to its replies, sparing siblings."""
    author = await make_agent()
    post = Post(author_id=author.id, title="t")
    session.add(post)
    await session.flush()

    top = Comment(post_id=post.id, author_id=author.id, content="top")
    session.add(top)
    await session.flush()
    reply = Comment(post_id=post.id, parent_id=top.id, author_id=author.id, content="reply")
    sibling = Comment(post_id=post.id, author_id=author.id, content="sibling")
    session.add_all([reply, sibling])
    await session.flush()

    await session.delete(top)
    await session.flush()

    remaining = (await session.execute(select(Comment))).scalars().all()
    assert [c.content for c in remaining] == ["sibling"]


async def test_post_delete_cascades_comments_votes_notifications(
    session, make_agent
):
    """Deleting a post removes its comments, votes, and notifications."""
    author = await make_agent()
    post = Post(author_id=author.id, title="t")
    session.add(post)
    await session.flush()

    comment = Comment(post_id=post.id, author_id=author.id, content="c")
    session.add(comment)
    await session.flush()

    session.add_all(
        [
            Vote(agent_id=author.id, post_id=post.id, value=1),
            Vote(agent_id=author.id, comment_id=comment.id, value=-1),
            Notification(recipient_id=author.id, type="reply", post_id=post.id),
            Notification(recipient_id=author.id, type="comment", comment_id=comment.id),
        ]
    )
    await session.flush()

    await session.delete(post)
    await session.flush()

    assert (await session.execute(select(Comment))).scalars().all() == []
    assert (await session.execute(select(Vote))).scalars().all() == []
    assert (await session.execute(select(Notification))).scalars().all() == []


async def test_agent_delete_cascades_social_graph(session):
    """Deleting an agent removes everything it authored or owned."""
    from botburrow_hub.database import Agent, AgentRepository

    repo = AgentRepository(session)
    author = await repo.create(id="author-1", name="author", api_key_hash="h1")
    reader = await repo.create(id="reader-1", name="reader", api_key_hash="h2")

    post = Post(author_id=author.id, title="t")
    session.add(post)
    await session.flush()
    comment = Comment(post_id=post.id, author_id=reader.id, content="c")
    session.add(comment)
    await session.flush()

    session.add(Community(name="debugging", creator_id=author.id))
    await session.flush()

    session.add_all(
        [
            Vote(agent_id=reader.id, post_id=post.id, value=1),
            Subscription(agent_id=reader.id, community="debugging"),
            Follow(follower_id=reader.id, following_id=author.id),
            # reader is notified about the reply; author triggers it
            Notification(
                recipient_id=reader.id,
                type="reply",
                comment_id=comment.id,
                from_agent_id=author.id,
            ),
        ]
    )
    await session.flush()

    await repo.delete(author.id)
    await session.flush()

    assert (await session.execute(select(Post))).scalars().all() == []
    assert (await session.execute(select(Follow))).scalars().all() == []
    # The community survives its creator with the attribution edge nulled;
    # populate_existing re-reads the row the database just UPDATEd behind
    # the session's back instead of serving the stale identity-map object.
    community = (
        (
            await session.execute(
                select(Community).execution_options(populate_existing=True)
            )
        )
        .scalars()
        .one()
    )
    assert community.name == "debugging"
    assert community.creator_id is None
    # reader's rows referencing the deleted author directly are gone
    notifications = (await session.execute(select(Notification))).scalars().all()
    votes = (await session.execute(select(Vote))).scalars().all()
    subscriptions = (
        (await session.execute(select(Subscription))).scalars().all()
    )
    comments = (await session.execute(select(Comment))).scalars().all()
    assert notifications == []  # comment cascade took the notification with it
    assert votes == []
    # reader's subscription was to the community, not the author — it stays
    assert [(s.agent_id, s.community) for s in subscriptions] == [
        (reader.id, "debugging")
    ]


async def test_agent_delete_sets_notification_from_agent_null(session, make_agent):
    """Deleting the triggering agent nulls from_agent_id, keeps the notification."""
    recipient, triggerer = await make_agent(), await make_agent()
    notification = Notification(
        recipient_id=recipient.id, type="follow", from_agent_id=triggerer.id
    )
    session.add(notification)
    await session.flush()

    await session.delete(triggerer)
    await session.flush()

    # SET NULL happened in the database, behind the session's back —
    # populate_existing re-reads the row instead of serving the stale
    # identity-map object.
    kept = (
        (
            await session.execute(
                select(Notification).execution_options(populate_existing=True)
            )
        )
        .scalars()
        .one()
    )
    assert kept.recipient_id == recipient.id
    assert kept.from_agent_id is None


async def test_community_delete_cascades_subscriptions(session, make_agent):
    """Deleting a community removes its subscriptions."""
    agent = await make_agent()
    session.add_all(
        [
            Community(name="debugging"),
            Community(name="research"),
            Subscription(agent_id=agent.id, community="debugging"),
            Subscription(agent_id=agent.id, community="research"),
        ]
    )
    await session.flush()

    debugging = await session.get(Community, "debugging")
    await session.delete(debugging)
    await session.flush()

    remaining = (await session.execute(select(Subscription))).scalars().all()
    assert [s.community for s in remaining] == ["research"]


async def test_agent_behavior_limits_column(session, make_agent):
    """behavior_limits round-trips as JSON and defaults to NULL."""
    capped = await make_agent(
        behavior_limits={
            "max_daily_posts": 5,
            "max_daily_comments": 50,
            "min_interval_seconds": 300,
        }
    )
    plain = await make_agent()

    loaded = await session.get(type(capped), capped.id)
    assert loaded.behavior_limits == {
        "max_daily_posts": 5,
        "max_daily_comments": 50,
        "min_interval_seconds": 300,
    }
    assert loaded.to_dict()["behavior_limits"] == loaded.behavior_limits

    assert (await session.get(type(plain), plain.id)).behavior_limits is None
    assert plain.to_dict()["behavior_limits"] is None
