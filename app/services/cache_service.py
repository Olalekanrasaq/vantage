"""Versioned Redis cache for dashboard data.

Every manager has a version number. Cache keys include it, so bumping the
version (on any change to that manager's data) makes old entries unreachable.
"""
import logging
import pickle
import time
from datetime import date
from math import ceil
from types import SimpleNamespace

from redis.exceptions import RedisError
from sqlalchemy import event, inspect as sa_inspect
from sqlalchemy.orm import Session

from app.extensions import rds

log = logging.getLogger(__name__)

CACHE_TTL = 3600          # safety net: entries expire after 1 hour regardless
REDIS_BACKOFF = 30        # after a Redis error, skip Redis for this many seconds
_redis_down_until = 0.0


def _redis_ok():
    return time.monotonic() >= _redis_down_until


def _redis_failed(exc):
    global _redis_down_until
    _redis_down_until = time.monotonic() + REDIS_BACKOFF
    log.warning("Redis unavailable, dashboard cache bypassed for %ss: %s", REDIS_BACKOFF, exc)


# ── Versions ──────────────────────────────────────────────────────────────────

def _ver_key(manager_id):
    return f"ver:mgr:{manager_id}"


def manager_version(manager_id):
    """Current version string, or None if Redis is unavailable."""
    if not _redis_ok():
        return None
    try:
        return rds.client.get(_ver_key(manager_id)) or "0"
    except RedisError as e:
        _redis_failed(e)
        return None


def bump_manager_version(*manager_ids):
    """Invalidate every cached dashboard entry for these managers."""
    ids = {m for m in manager_ids if m}
    if not ids:
        return
    try:
        pipe = rds.client.pipeline()
        for mid in ids:
            pipe.incr(_ver_key(mid))
        pipe.execute()
    except RedisError as e:
        log.error("Could not bump cache version for %s: %s", ids, e)


# ── Get-or-compute ────────────────────────────────────────────────────────────

def cached(manager_id, key_parts, compute):
    """Return the cached value for key_parts, or compute(), store and return it."""
    ver = manager_version(manager_id)
    if ver is None:
        return compute()

    key = "dash:" + ":".join(str(p) for p in (manager_id, ver, date.today().isoformat(), *key_parts))
    try:
        raw = rds.raw.get(key)
        if raw is not None:
            return pickle.loads(raw)
    except RedisError as e:
        _redis_failed(e)
        return compute()
    except Exception:
        log.exception("Unreadable cache entry %s; recomputing", key)

    value = compute()
    try:
        rds.raw.setex(key, CACHE_TTL, pickle.dumps(value))
    except RedisError as e:
        _redis_failed(e)
    return value


# ── Cacheable stand-in for a Flask-SQLAlchemy Pagination ─────────────────────

class CachedPage:
    """Same attributes templates use on a Pagination: items, page, pages, total,
    has_prev/has_next, prev_num/next_num, iter_pages()."""

    def __init__(self, items, page, per_page, total):
        self.items = items
        self.page = page
        self.per_page = per_page
        self.total = total

    def __iter__(self):
        return iter(self.items)

    @property
    def pages(self):
        return ceil(self.total / self.per_page) if self.per_page and self.total else 0

    @property
    def has_prev(self):
        return self.page > 1

    @property
    def has_next(self):
        return self.page < self.pages

    @property
    def prev_num(self):
        return self.page - 1 if self.has_prev else None

    @property
    def next_num(self):
        return self.page + 1 if self.has_next else None

    def iter_pages(self, *, left_edge=2, left_current=2, right_current=4, right_edge=2):
        pages_end = self.pages + 1
        if pages_end == 1:
            return
        left_end = min(1 + left_edge, pages_end)
        yield from range(1, left_end)
        if left_end == pages_end:
            return
        mid_start = max(left_end, self.page - left_current)
        mid_end = min(self.page + right_current + 1, pages_end)
        if mid_start - left_end > 0:
            yield None
        yield from range(mid_start, mid_end)
        if mid_end == pages_end:
            return
        right_start = max(mid_end, pages_end - right_edge)
        if right_start - mid_end > 0:
            yield None
        yield from range(right_start, pages_end)


def freeze_page(pagination):
    """Convert a live Pagination of ORM rows into a cacheable CachedPage of plain rows."""
    if pagination is None:
        return None
    rows = [
        SimpleNamespace(**{c.key: getattr(obj, c.key) for c in sa_inspect(obj).mapper.column_attrs})
        for obj in pagination.items
    ]
    return CachedPage(rows, pagination.page, pagination.per_page, pagination.total)


# ── Automatic invalidation on ORM changes ────────────────────────────────────

@event.listens_for(Session, "after_flush")
def _collect_changed_managers(session, flush_context):
    ids = session.info.setdefault("changed_managers", set())
    for obj in (*session.new, *session.dirty, *session.deleted):
        mid = getattr(obj, "manager_id", None)
        if mid:
            ids.add(mid)


@event.listens_for(Session, "after_commit")
def _bump_after_commit(session):
    ids = session.info.pop("changed_managers", None)
    if ids:
        bump_manager_version(*ids)


@event.listens_for(Session, "after_rollback")
def _discard_on_rollback(session):
    session.info.pop("changed_managers", None)