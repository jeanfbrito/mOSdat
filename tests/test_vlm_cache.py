"""Tests for VLM verify result cache (I6).

Covers:
- round-trip put/get
- TTL expiry (expired entries return None)
- MOSDAT_VLM_NOCACHE env var disables caching in VLMClient.verify
- concurrent puts from multiple threads (no corruption)
"""

import os
import threading
import time

# Clear sibling-test stub pollution BEFORE importing automation.*.
# test_input, test_negative stub automation.vlm.* and automation.transport.*
# at their module import time during pytest collection.
import sys as _sys
for _name in list(_sys.modules):
    if (
        _name.startswith("automation.transport")
        or _name.startswith("automation.vlm")
        or _name in (
            "automation",
            "automation.config",
            "automation.proxmox.api",
            "automation.proxmox.vm",
            "automation.runners.functional",
        )
    ):
        _sys.modules.pop(_name, None)

from pathlib import Path

import pytest

from automation.transport.vlm_cache import VLMCache


# ── Helpers ────────────────────────────────────────────────────────────────

def _make_cache(tmp_path: Path, ttl: int = 3600) -> VLMCache:
    return VLMCache(cache_dir=tmp_path / "vlm_cache", ttl=ttl)


# ── cache_key ──────────────────────────────────────────────────────────────

def test_cache_key_is_deterministic():
    key1 = VLMCache.cache_key(b"img", "prompt", "model")
    key2 = VLMCache.cache_key(b"img", "prompt", "model")
    assert key1 == key2


def test_cache_key_length():
    key = VLMCache.cache_key(b"img", "prompt", "model")
    assert len(key) == 32


def test_cache_key_differs_on_image(tmp_path):
    key1 = VLMCache.cache_key(b"img1", "same prompt", "model")
    key2 = VLMCache.cache_key(b"img2", "same prompt", "model")
    assert key1 != key2


def test_cache_key_differs_on_prompt():
    key1 = VLMCache.cache_key(b"img", "prompt A", "model")
    key2 = VLMCache.cache_key(b"img", "prompt B", "model")
    assert key1 != key2


def test_cache_key_differs_on_model():
    key1 = VLMCache.cache_key(b"img", "prompt", "model-a")
    key2 = VLMCache.cache_key(b"img", "prompt", "model-b")
    assert key1 != key2


# ── round-trip ─────────────────────────────────────────────────────────────

def test_put_and_get_returns_verdict(tmp_path):
    cache = _make_cache(tmp_path)
    key = VLMCache.cache_key(b"screenshot", "is the login page visible", "qwen3")
    cache.put(key, verdict=True, raw="yes the login is visible", model="qwen3")
    result = cache.get(key)
    assert result is not None
    verdict, raw = result
    assert verdict is True
    assert raw == "yes the login is visible"


def test_put_and_get_false_verdict(tmp_path):
    cache = _make_cache(tmp_path)
    key = VLMCache.cache_key(b"dark_screen", "is the modal open", "qwen3")
    cache.put(key, verdict=False, raw="no modal visible", model="qwen3")
    result = cache.get(key)
    assert result is not None
    verdict, raw = result
    assert verdict is False


def test_get_missing_key_returns_none(tmp_path):
    cache = _make_cache(tmp_path)
    result = cache.get("nonexistent_key_1234567890123456")
    assert result is None


def test_put_overwrites_existing(tmp_path):
    cache = _make_cache(tmp_path)
    key = VLMCache.cache_key(b"img", "prompt", "model")
    cache.put(key, verdict=True, raw="yes", model="model")
    cache.put(key, verdict=False, raw="no", model="model")
    result = cache.get(key)
    assert result is not None
    verdict, raw = result
    assert verdict is False
    assert raw == "no"


# ── TTL expiry ─────────────────────────────────────────────────────────────

def test_get_respects_ttl_not_expired(tmp_path):
    """Entry within TTL window should be returned."""
    cache = _make_cache(tmp_path, ttl=60)
    key = VLMCache.cache_key(b"img", "prompt", "m")
    cache.put(key, verdict=True, raw="yes", model="m")
    result = cache.get(key)
    assert result is not None


def test_get_respects_ttl_expired(tmp_path):
    """Entry with a created_at in the past beyond TTL should return None."""
    import sqlite3
    cache = _make_cache(tmp_path, ttl=60)
    key = VLMCache.cache_key(b"img", "expired prompt", "m")
    # Insert entry with created_at 120 seconds ago (beyond 60s TTL)
    old_ts = int(time.time()) - 120
    conn = sqlite3.connect(str(tmp_path / "vlm_cache" / "vlm_cache.sqlite"))
    conn.execute(
        "INSERT INTO vlm_cache (key, verdict, raw_response, model, created_at) VALUES (?,?,?,?,?)",
        (key, 1, "yes", "m", old_ts),
    )
    conn.commit()
    conn.close()
    result = cache.get(key)
    assert result is None


# ── invalidate / clear ────────────────────────────────────────────────────

def test_invalidate_removes_entry(tmp_path):
    cache = _make_cache(tmp_path)
    key = VLMCache.cache_key(b"img", "prompt", "m")
    cache.put(key, verdict=True, raw="yes", model="m")
    cache.invalidate(key)
    assert cache.get(key) is None


def test_clear_removes_all_entries(tmp_path):
    cache = _make_cache(tmp_path)
    for i in range(5):
        key = VLMCache.cache_key(f"img{i}".encode(), "prompt", "m")
        cache.put(key, verdict=True, raw="yes", model="m")
    deleted = cache.clear()
    assert deleted == 5
    # Verify all gone
    for i in range(5):
        key = VLMCache.cache_key(f"img{i}".encode(), "prompt", "m")
        assert cache.get(key) is None


def test_prune_removes_old_entries(tmp_path):
    """Prune removes entries with created_at beyond the given age threshold."""
    import sqlite3
    cache = _make_cache(tmp_path, ttl=3600)
    key_old = VLMCache.cache_key(b"old_img", "p", "m")
    key_new = VLMCache.cache_key(b"new_img", "p", "m")

    # Insert key_old with a timestamp 120 seconds ago
    old_ts = int(time.time()) - 120
    conn = sqlite3.connect(str(tmp_path / "vlm_cache" / "vlm_cache.sqlite"))
    conn.execute(
        "INSERT INTO vlm_cache (key, verdict, raw_response, model, created_at) VALUES (?,?,?,?,?)",
        (key_old, 1, "yes", "m", old_ts),
    )
    conn.commit()
    conn.close()

    # Insert key_new normally (recent timestamp)
    cache.put(key_new, verdict=False, raw="no", model="m")

    # Prune entries older than 60 seconds; key_old (120s) should be pruned
    deleted = cache.prune(older_than=60)
    assert deleted == 1
    assert cache.get(key_old) is None
    # New entry should survive
    assert cache.get(key_new) is not None


# ── stats ─────────────────────────────────────────────────────────────────

def test_stats_entries_count(tmp_path):
    cache = _make_cache(tmp_path)
    for i in range(3):
        key = VLMCache.cache_key(f"s{i}".encode(), "q", "m")
        cache.put(key, verdict=True, raw="yes", model="m")
        cache.get(key)
    s = cache.stats()
    assert s["entries"] == 3
    assert s["hits"] == 3
    assert s["misses"] == 0
    assert s["hit_rate"] == 1.0


def test_stats_miss_counted(tmp_path):
    cache = _make_cache(tmp_path)
    cache.get("00000000000000000000000000000000")
    s = cache.stats()
    assert s["misses"] == 1
    assert s["hits"] == 0


# ── MOSDAT_VLM_NOCACHE env var ─────────────────────────────────────────────

def test_vlm_client_nocache_env(monkeypatch, tmp_path):
    """When MOSDAT_VLM_NOCACHE=1 the cache module should not be consulted."""
    # We test via the module-level flag, not by spinning up a real VLM endpoint.
    monkeypatch.setenv("MOSDAT_VLM_NOCACHE", "1")

    # Re-import client module to pick up monkeypatched env
    import importlib
    import automation.vlm.client as client_mod
    importlib.reload(client_mod)

    assert client_mod._cache_disabled is True

    # Cleanup: reset to avoid polluting other tests
    monkeypatch.delenv("MOSDAT_VLM_NOCACHE", raising=False)
    importlib.reload(client_mod)


def test_set_cache_enabled_toggle():
    """set_cache_enabled(False) should disable the cache flag."""
    from automation.vlm import client as client_mod
    original = client_mod._cache_disabled
    try:
        client_mod.set_cache_enabled(False)
        assert client_mod._cache_disabled is True
        client_mod.set_cache_enabled(True)
        assert client_mod._cache_disabled is False
    finally:
        client_mod._cache_disabled = original


# ── concurrent puts ────────────────────────────────────────────────────────

def test_concurrent_puts_no_corruption(tmp_path):
    """Multiple threads writing different keys simultaneously should all succeed."""
    cache = _make_cache(tmp_path)
    n_threads = 10
    n_entries_per_thread = 20
    errors: list[Exception] = []

    def worker(thread_id: int) -> None:
        try:
            for i in range(n_entries_per_thread):
                img_bytes = f"thread{thread_id}_img{i}".encode()
                key = VLMCache.cache_key(img_bytes, f"prompt{i}", "model")
                cache.put(key, verdict=(i % 2 == 0), raw=f"raw{i}", model="model")
                result = cache.get(key)
                assert result is not None, f"thread {thread_id} entry {i}: get returned None right after put"
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Thread errors: {errors}"

    s = cache.stats()
    assert s["entries"] == n_threads * n_entries_per_thread


def test_concurrent_same_key_put(tmp_path):
    """Multiple threads writing the SAME key should not cause DB corruption."""
    cache = _make_cache(tmp_path)
    key = VLMCache.cache_key(b"shared_img", "shared_prompt", "model")
    errors: list[Exception] = []

    def worker() -> None:
        try:
            for _ in range(50):
                cache.put(key, verdict=True, raw="yes", model="model")
                result = cache.get(key)
                assert result is not None
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Thread errors: {errors}"
    # Final value must be readable
    assert cache.get(key) is not None
