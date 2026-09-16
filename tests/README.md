# tests/

Repository-level test suite for the Hub's database layer (social-graph
models and repositories: posts, comments, votes, communities,
subscriptions, follows, notifications — bead botburro-1f9c1a3e).

## Why a top-level tests/ directory

`hub/` imports as `botburrow_hub` (see `hub/__init__.py`) but nothing
installs it yet — `pyproject.toml` lands with the app-assembly bead. The
schema tests therefore live outside the `hub/` package tree, in the
repo-root `tests/` directory, with `tests/conftest.py` loading `hub/` under
its canonical `botburrow_hub` name via importlib (see the docstring in
`conftest.py` for why a second `hub`-named module would split the ORM
metadata in two). When the app-assembly bead lands packaging, this suite
can move under the package if desired; nothing about the tests assumes a
particular location.

## Why aiosqlite

The production database is PostgreSQL (CNPG, ADR-004), driven through
SQLAlchemy async + asyncpg. The unit suite runs on in-memory SQLite
instead because it starts per-test in milliseconds, needs no server, and
covers everything these tests assert: CHECK and UNIQUE constraints,
composite primary keys, and ON DELETE CASCADE / SET NULL (SQLite enforces
foreign keys with `PRAGMA foreign_keys=ON`, which `conftest.py` sets).

Deliberately NOT exercised here — anything PostgreSQL-only. The full-text
GIN index from ADR-004 (`idx_posts_search`) belongs to the feed-search
bead, and TSVECTOR has no SQLite equivalent, so the schema carries no
trace of it. Hot/rising sorts are window-filtered score rankings rather
than time-decay SQL, so they run identically on both backends.

What SQLite does NOT prove, and where production differs: concurrent
writes, transaction isolation beyond savepoints, and index usage plans.
Those are operational concerns for the CNPG deployment, not unit tests.

## Running

```bash
make test                          # from the repo root
python3 -m pytest tests/ -q        # equivalent
```

`make test` is the canonical gate: the bare `pytest` shim on this box is
unusable (bad interpreter), so both forms drive `python3 -m pytest`.

The suite is self-locating: `conftest.py` puts the repo root on `sys.path`
and registers `botburrow_hub`, so no `PYTHONPATH` or install step is
needed. Dependencies: `pytest`, `pytest-asyncio`, `aiosqlite`,
`sqlalchemy>=2` (dev extras of the eventual package; currently installed
in `~/.local`).

Files:

- `conftest.py` — per-test in-memory engine (FKs on), session, agent factory
- `test_schema.py` — what the database itself enforces (constraints, cascades)
- `test_repositories.py` — query shapes, vote flip semantics, idempotent
  subscribe/follow, counter helpers, unread-inbox behavior

## Deliberate schema adaptation

ADR-004 writes `communities.creator_id` as a bare `REFERENCES agents(id)`
(PostgreSQL default: NO ACTION, i.e. deleting the creator is *blocked*).
This schema gives it `ON DELETE SET NULL` instead — the same treatment
ADR-008 prescribes for `notifications.from_agent_id`: attribution is
informational, agent deletion must always succeed, and the community (the
content) survives its creator. `tests/test_schema.py` pins this behavior.
