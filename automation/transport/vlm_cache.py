"""VLM verify result cache — SQLite-backed, keyed by (image SHA256, prompt SHA256, model).

Usage::

    from automation.transport.vlm_cache import VLMCache

    cache = VLMCache()
    key = cache.cache_key(image_bytes, prompt, model)
    hit = cache.get(key)
    if hit is None:
        verdict, raw = call_vlm(...)
        cache.put(key, verdict, raw, model)
    else:
        verdict, raw = hit

Thread-safety: one ``sqlite3.Connection`` per thread (via ``threading.local``).
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Default TTL: 24 hours in seconds
_DEFAULT_TTL = 86400

# Default cache directory: respect XDG_CACHE_HOME if set
_DEFAULT_CACHE_DIR = Path(
    os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
) / "mosdat"

_DB_NAME = "vlm_cache.sqlite"

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS vlm_cache (
    key         TEXT PRIMARY KEY,
    verdict     INTEGER NOT NULL,
    raw_response TEXT NOT NULL,
    model       TEXT NOT NULL,
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS vlm_cache_created_idx ON vlm_cache (created_at);
"""


class VLMCache:
    """Thread-safe SQLite cache for VLM verify results."""

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        ttl: int = _DEFAULT_TTL,
    ) -> None:
        self._dir = Path(cache_dir) if cache_dir else _DEFAULT_CACHE_DIR
        self._db_path = self._dir / _DB_NAME
        self._ttl = ttl
        self._local = threading.local()
        self._lock = threading.Lock()
        self._stats = {"hits": 0, "misses": 0}
        self._ensure_dir()
        # Initialize schema on the calling thread's connection
        self._get_conn()

    def _ensure_dir(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def _get_conn(self) -> sqlite3.Connection:
        """Return a per-thread sqlite3 connection, creating it if necessary."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(_CREATE_SQL)
            conn.commit()
            self._local.conn = conn
        return self._local.conn

    # ── Public API ──────────────────────────────────────────────────────────

    @staticmethod
    def cache_key(image_bytes: bytes, prompt: str, model: str) -> str:
        """Return cache key: first 32 chars of sha256(image) || sha256(prompt) || sha256(model)."""
        img_sha = hashlib.sha256(image_bytes).hexdigest()
        prompt_sha = hashlib.sha256(prompt.encode()).hexdigest()
        model_sha = hashlib.sha256(model.encode()).hexdigest()
        combined = img_sha + prompt_sha + model_sha
        return hashlib.sha256(combined.encode()).hexdigest()[:32]

    def get(self, key: str) -> Optional[Tuple[bool, str]]:
        """Look up key. Returns (verdict: bool, raw_response: str) or None on miss/TTL expiry."""
        conn = self._get_conn()
        cutoff = int(time.time()) - self._ttl
        row = conn.execute(
            "SELECT verdict, raw_response, created_at FROM vlm_cache WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            with self._lock:
                self._stats["misses"] += 1
            logger.debug("[vlm-cache] MISS key=%s", key[:8])
            return None
        verdict_int, raw, created_at = row
        if created_at < cutoff:
            age_h = (int(time.time()) - created_at) / 3600
            with self._lock:
                self._stats["misses"] += 1
            logger.debug("[vlm-cache] MISS key=%s (expired %.1fh ago)", key[:8], age_h - self._ttl / 3600)
            return None
        remaining_h = (created_at + self._ttl - int(time.time())) / 3600
        with self._lock:
            self._stats["hits"] += 1
        logger.info("[vlm-cache] HIT key=%s ttl=%.0fh", key[:8], remaining_h)
        return bool(verdict_int), raw

    def put(self, key: str, verdict: bool, raw: str, model: str) -> None:
        """Store a result. Overwrites any existing entry for the same key."""
        conn = self._get_conn()
        now = int(time.time())
        conn.execute(
            "INSERT OR REPLACE INTO vlm_cache (key, verdict, raw_response, model, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (key, int(verdict), raw, model, now),
        )
        conn.commit()
        logger.debug("[vlm-cache] PUT key=%s verdict=%s", key[:8], verdict)

    def invalidate(self, key: str) -> None:
        """Remove a single entry by key."""
        conn = self._get_conn()
        conn.execute("DELETE FROM vlm_cache WHERE key = ?", (key,))
        conn.commit()
        logger.debug("[vlm-cache] INVALIDATE key=%s", key[:8])

    def clear(self) -> int:
        """Remove all cache entries. Returns count deleted."""
        conn = self._get_conn()
        cur = conn.execute("DELETE FROM vlm_cache")
        conn.commit()
        deleted = cur.rowcount
        logger.info("[vlm-cache] CLEAR deleted=%d", deleted)
        return deleted

    def prune(self, older_than: int) -> int:
        """Remove entries older than ``older_than`` seconds. Returns count deleted."""
        conn = self._get_conn()
        cutoff = int(time.time()) - older_than
        cur = conn.execute("DELETE FROM vlm_cache WHERE created_at < ?", (cutoff,))
        conn.commit()
        deleted = cur.rowcount
        logger.info("[vlm-cache] PRUNE older_than=%ds deleted=%d", older_than, deleted)
        return deleted

    def stats(self) -> dict:
        """Return cache statistics dict."""
        conn = self._get_conn()
        count = conn.execute("SELECT COUNT(*) FROM vlm_cache").fetchone()[0]
        oldest = conn.execute("SELECT MIN(created_at) FROM vlm_cache").fetchone()[0]
        size_bytes = self._db_path.stat().st_size if self._db_path.exists() else 0
        with self._lock:
            hits = self._stats["hits"]
            misses = self._stats["misses"]
        total = hits + misses
        hit_rate = hits / total if total > 0 else 0.0
        return {
            "entries": count,
            "size_bytes": size_bytes,
            "hits": hits,
            "misses": misses,
            "hit_rate": hit_rate,
            "oldest_ts": oldest,
            "db_path": str(self._db_path),
            "ttl": self._ttl,
        }


# Module-level singleton — created lazily so import is free of side-effects.
_cache: Optional[VLMCache] = None
_cache_lock = threading.Lock()


def get_default_cache() -> VLMCache:
    """Return (creating if needed) the module-level default cache singleton."""
    global _cache
    if _cache is None:
        with _cache_lock:
            if _cache is None:
                _cache = VLMCache()
    return _cache
