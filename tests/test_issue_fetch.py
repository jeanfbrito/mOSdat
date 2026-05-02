"""Tests for automation.issue_fetch module."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from automation.issue_fetch import (
    IssueContext,
    _cache_path,
    _fetch_from_github,
    _is_cache_fresh,
    _parse_url_or_id,
    _read_cache,
    _write_cache,
    fetch_issue,
)


class Test_parse_url_or_id:
    """Test URL/ID parsing logic."""

    def test_full_url(self):
        """Accept https://github.com/<org>/<repo>/issues/<n>."""
        org, repo, issue_id = _parse_url_or_id(
            "https://github.com/RocketChat/Rocket.Chat.Electron/issues/3308"
        )
        assert org == "RocketChat"
        assert repo == "Rocket.Chat.Electron"
        assert issue_id == "3308"

    def test_shorthand_org_repo_id(self):
        """Accept org/repo#id."""
        org, repo, issue_id = _parse_url_or_id("RocketChat/Rocket.Chat.Electron#3308")
        assert org == "RocketChat"
        assert repo == "Rocket.Chat.Electron"
        assert issue_id == "3308"

    def test_bare_id_default_repo(self):
        """Accept bare id; default to RocketChat/Rocket.Chat.Electron."""
        org, repo, issue_id = _parse_url_or_id("3308")
        assert org == "RocketChat"
        assert repo == "Rocket.Chat.Electron"
        assert issue_id == "3308"

    def test_bare_id_env_override(self, monkeypatch):
        """Bare id uses MOSDAT_DEFAULT_ISSUE_REPO if set."""
        monkeypatch.setenv("MOSDAT_DEFAULT_ISSUE_REPO", "OtherOrg/OtherRepo")
        org, repo, issue_id = _parse_url_or_id("999")
        assert org == "OtherOrg"
        assert repo == "OtherRepo"
        assert issue_id == "999"

    def test_invalid_input(self):
        """Reject malformed input."""
        with pytest.raises(ValueError):
            _parse_url_or_id("not-a-valid-id")


class Test_cache_write_read:
    """Test cache file roundtrip."""

    def test_write_and_read_cache(self, tmp_path, monkeypatch):
        """Cache write/read preserves all fields exactly."""
        # Monkeypatch CACHE_DIR to tmp_path
        import automation.issue_fetch as mod

        monkeypatch.setattr(mod, "CACHE_DIR", tmp_path)

        ctx_orig = IssueContext(
            id="3308",
            url="https://github.com/RocketChat/Rocket.Chat.Electron/issues/3308",
            title="Screen share picker opens on every app launch",
            labels=["type: bug", "priority: high"],
            suspected_prs=["3266"],
            linked_prs=["3313", "3314"],
            body_markdown="This is the issue body.\nWith multiple lines.\n",
            fetched_at="2026-05-02T19:10:00Z",
        )

        # Write cache
        cache_path = _cache_path("3308")
        _write_cache(cache_path, ctx_orig)

        # Read cache back
        ctx_read = _read_cache(cache_path)

        # All fields match
        assert ctx_read.id == ctx_orig.id
        assert ctx_read.url == ctx_orig.url
        assert ctx_read.title == ctx_orig.title
        assert ctx_read.labels == ctx_orig.labels
        assert ctx_read.suspected_prs == ctx_orig.suspected_prs
        assert ctx_read.linked_prs == ctx_orig.linked_prs
        assert ctx_read.body_markdown == ctx_orig.body_markdown
        assert ctx_read.fetched_at == ctx_orig.fetched_at

    def test_cache_file_format(self, tmp_path, monkeypatch):
        """Cache file has correct YAML front-matter structure."""
        import automation.issue_fetch as mod

        monkeypatch.setattr(mod, "CACHE_DIR", tmp_path)

        ctx = IssueContext(
            id="123",
            url="https://example.com/123",
            title="Test",
            body_markdown="Body text",
        )

        cache_path = _cache_path("123")
        _write_cache(cache_path, ctx)

        # Read raw file and verify structure
        content = cache_path.read_text()
        assert content.startswith("---\n")
        parts = content.split("---")
        assert len(parts) >= 3

        # Parse YAML
        fm = yaml.safe_load(parts[1])
        assert fm["id"] == "123"
        assert fm["title"] == "Test"


class Test_is_cache_fresh:
    """Test cache freshness check."""

    def test_cache_fresh_within_ttl(self, tmp_path, monkeypatch):
        """Cache is fresh if younger than TTL."""
        import automation.issue_fetch as mod

        monkeypatch.setattr(mod, "CACHE_DIR", tmp_path)
        cache_path = tmp_path / "test.context.md"
        cache_path.write_text("test")

        # Verify it's fresh
        assert _is_cache_fresh(cache_path) is True

    def test_cache_stale_beyond_ttl(self, tmp_path, monkeypatch):
        """Cache is stale if older than TTL."""
        import automation.issue_fetch as mod

        monkeypatch.setattr(mod, "CACHE_DIR", tmp_path)
        monkeypatch.setattr(mod, "CACHE_TTL_DAYS", 7)

        cache_path = tmp_path / "test.context.md"
        cache_path.write_text("test")

        # Set mtime to 8 days ago
        now_ts = datetime.now(tz=timezone.utc).timestamp()
        old_ts = now_ts - (8 * 24 * 3600)
        cache_path.stat()
        import os

        os.utime(cache_path, (old_ts, old_ts))

        # Verify it's stale
        assert _is_cache_fresh(cache_path) is False

    def test_cache_missing(self, tmp_path, monkeypatch):
        """Missing cache file is not fresh."""
        import automation.issue_fetch as mod

        monkeypatch.setattr(mod, "CACHE_DIR", tmp_path)
        cache_path = tmp_path / "nonexistent.context.md"
        assert _is_cache_fresh(cache_path) is False


class Test_fetch_from_github:
    """Test GitHub REST API fetch and parsing."""

    def test_fetch_parses_json(self, monkeypatch):
        """Fetch parses GitHub JSON response correctly."""
        mock_response = MagicMock()
        mock_response.__enter__.return_value = mock_response
        mock_response.__exit__.return_value = None
        mock_response.read.return_value = json.dumps(
            {
                "title": "Screen share picker opens on every app launch",
                "body": """This is the issue body.

Likely PR #3266 caused this.
See also #3313 and #3314.

End of body.""",
                "labels": [
                    {"name": "type: bug"},
                    {"name": "priority: high"},
                ],
            }
        ).encode("utf-8")

        with patch("urllib.request.urlopen", return_value=mock_response):
            ctx = _fetch_from_github("RocketChat", "Rocket.Chat.Electron", "3308")

        assert ctx.id == "3308"
        assert ctx.title == "Screen share picker opens on every app launch"
        assert ctx.labels == ["type: bug", "priority: high"]
        assert ctx.suspected_prs == ["3266"]
        assert "3313" in ctx.linked_prs
        assert "3314" in ctx.linked_prs
        # Issue's own ID should not be in linked_prs
        assert "3308" not in ctx.linked_prs

    def test_suspected_pr_regex_patterns(self, monkeypatch):
        """Suspected-PR regex matches various patterns."""
        test_cases = [
            ("Likely PR #3266", ["3266"]),
            ("Suspected PR #3266", ["3266"]),
            ("caused by #3266", ["3266"]),
            ("introduced by #3266", ["3266"]),
            ("likely PR #3266", ["3266"]),  # case-insensitive
            ("See also #3313", []),  # plain reference, not suspected
            ("Multiple: likely PR #1, suspected PR #2", ["1", "2"]),
        ]

        for body_text, expected_suspected in test_cases:
            mock_response = MagicMock()
            mock_response.__enter__.return_value = mock_response
            mock_response.__exit__.return_value = None
            mock_response.read.return_value = json.dumps(
                {
                    "title": "Test",
                    "body": body_text,
                    "labels": [],
                }
            ).encode("utf-8")

            with patch("urllib.request.urlopen", return_value=mock_response):
                ctx = _fetch_from_github("Org", "Repo", "123")

            assert ctx.suspected_prs == expected_suspected, f"Failed for: {body_text}"

    def test_auth_header_from_env(self, monkeypatch):
        """GITHUB_TOKEN env var is added as Authorization header."""
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_token_123")

        mock_response = MagicMock()
        mock_response.__enter__.return_value = mock_response
        mock_response.__exit__.return_value = None
        mock_response.read.return_value = json.dumps(
            {"title": "Test", "body": "", "labels": []}
        ).encode("utf-8")

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            _fetch_from_github("Org", "Repo", "1")

        # Check that Authorization header was included
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        assert req.get_header("Authorization") == "Bearer ghp_test_token_123"

    def test_no_auth_header_without_token(self, monkeypatch):
        """No Authorization header when GITHUB_TOKEN not set."""
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        mock_response = MagicMock()
        mock_response.__enter__.return_value = mock_response
        mock_response.__exit__.return_value = None
        mock_response.read.return_value = json.dumps(
            {"title": "Test", "body": "", "labels": []}
        ).encode("utf-8")

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            _fetch_from_github("Org", "Repo", "1")

        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        # Authorization header should not be set
        assert req.get_header("Authorization") is None

    def test_fetch_timeout(self, monkeypatch):
        """Fetch raises RuntimeError on timeout."""
        import urllib.error

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("Connection timeout"),
        ):
            with pytest.raises(RuntimeError, match="Failed to fetch"):
                _fetch_from_github("Org", "Repo", "1")


class Test_fetch_issue:
    """Test main fetch_issue function."""

    def test_fresh_cache_hit_no_urllib(self, tmp_path, monkeypatch):
        """Fresh cache hit does NOT fire urllib."""
        import automation.issue_fetch as mod

        monkeypatch.setattr(mod, "CACHE_DIR", tmp_path)

        # Pre-populate cache with fresh data
        cache_path = tmp_path / "3308.context.md"
        ctx_cached = IssueContext(
            id="3308",
            url="https://github.com/Org/Repo/issues/3308",
            title="Cached Title",
            body_markdown="Cached body",
        )
        _write_cache(cache_path, ctx_cached)

        # Mock urlopen to ensure it's NOT called
        with patch("urllib.request.urlopen") as mock_urlopen:
            result = fetch_issue("3308")

        # urlopen should not be called
        mock_urlopen.assert_not_called()
        assert result.title == "Cached Title"

    def test_cache_miss_fetches_from_github(self, tmp_path, monkeypatch):
        """Cache miss triggers GitHub fetch."""
        import automation.issue_fetch as mod

        monkeypatch.setattr(mod, "CACHE_DIR", tmp_path)

        mock_response = MagicMock()
        mock_response.__enter__.return_value = mock_response
        mock_response.__exit__.return_value = None
        mock_response.read.return_value = json.dumps(
            {
                "title": "Fresh from GitHub",
                "body": "Fresh body",
                "labels": [],
            }
        ).encode("utf-8")

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            result = fetch_issue("9999")

        # urlopen should have been called
        mock_urlopen.assert_called()
        assert result.title == "Fresh from GitHub"

        # Cache should now exist
        assert (tmp_path / "9999.context.md").exists()

    def test_refresh_flag_bypasses_cache(self, tmp_path, monkeypatch):
        """refresh=True bypasses cache freshness check."""
        import automation.issue_fetch as mod

        monkeypatch.setattr(mod, "CACHE_DIR", tmp_path)

        # Pre-populate cache with old data
        cache_path = tmp_path / "3308.context.md"
        ctx_cached = IssueContext(
            id="3308",
            url="https://github.com/Org/Repo/issues/3308",
            title="Old Title",
            body_markdown="Old body",
        )
        _write_cache(cache_path, ctx_cached)

        mock_response = MagicMock()
        mock_response.__enter__.return_value = mock_response
        mock_response.__exit__.return_value = None
        mock_response.read.return_value = json.dumps(
            {
                "title": "New Title",
                "body": "New body",
                "labels": [],
            }
        ).encode("utf-8")

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            result = fetch_issue("3308", refresh=True)

        # urlopen should have been called despite fresh cache
        mock_urlopen.assert_called()
        assert result.title == "New Title"
