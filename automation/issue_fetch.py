"""Fetch and cache GitHub issue context for bug-confirmation scenarios.

Cache lives at shared/scenarios/issues/<id>.context.md (committed).
Re-fetched if older than 7 days OR refresh=True.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import yaml

CACHE_TTL_DAYS = 7
CACHE_DIR = Path(__file__).resolve().parent.parent / "shared" / "scenarios" / "issues"


@dataclass
class IssueContext:
    id: str                    # "3308"
    url: str                   # full URL
    title: Optional[str] = None
    labels: list[str] = field(default_factory=list)
    suspected_prs: list[str] = field(default_factory=list)   # ["3266"]
    linked_prs: list[str] = field(default_factory=list)      # ["3313"]
    body_markdown: str = ""
    fetched_at: Optional[str] = None  # ISO8601 UTC


def fetch_issue(url_or_id: str, *, refresh: bool = False) -> IssueContext:
    """Return IssueContext for the given GitHub issue.

    Accepts either:
      - full URL: https://github.com/<org>/<repo>/issues/<n>
      - bare numeric id: "3308" — assumes repo from MOSDAT_DEFAULT_ISSUE_REPO env or
        default RocketChat/Rocket.Chat.Electron.

    Caches to shared/scenarios/issues/<id>.context.md. Cache hits return parsed
    cached content; misses re-fetch via the GitHub REST API (public issues only;
    no auth header).
    """
    org, repo, issue_id = _parse_url_or_id(url_or_id)
    cache_path = _cache_path(issue_id)

    # Check cache freshness
    if not refresh and cache_path.exists() and _is_cache_fresh(cache_path):
        return _read_cache(cache_path)

    # Fetch from GitHub
    ctx = _fetch_from_github(org, repo, issue_id)
    _write_cache(cache_path, ctx)
    return ctx


def _cache_path(issue_id: str) -> Path:
    """Return path to cache file for the given issue ID."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{issue_id}.context.md"


def _is_cache_fresh(path: Path) -> bool:
    """Return True if cache file is fresher than TTL."""
    if not path.exists():
        return False
    mtime = path.stat().st_mtime
    mtime_dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
    now = datetime.now(tz=timezone.utc)
    age = now - mtime_dt
    return age < timedelta(days=CACHE_TTL_DAYS)


def _read_cache(path: Path) -> IssueContext:
    """Parse cached context file (YAML front-matter + body)."""
    content = path.read_text(encoding="utf-8")
    # Split on --- lines
    parts = content.split("---")
    if len(parts) < 3:
        raise ValueError(f"Invalid cache file format: {path}")

    # parts[0] is empty (before first ---)
    # parts[1] is YAML front-matter
    # parts[2:] is body markdown
    front_matter = yaml.safe_load(parts[1])
    body_markdown = "---".join(parts[2:]).lstrip("\n")

    ctx = IssueContext(
        id=front_matter.get("id", ""),
        url=front_matter.get("url", ""),
        title=front_matter.get("title"),
        labels=front_matter.get("labels", []),
        suspected_prs=front_matter.get("suspected_prs", []),
        linked_prs=front_matter.get("linked_prs", []),
        body_markdown=body_markdown,
        fetched_at=front_matter.get("fetched_at"),
    )
    return ctx


def _write_cache(path: Path, ctx: IssueContext) -> None:
    """Write cache file with YAML front-matter."""
    path.parent.mkdir(parents=True, exist_ok=True)

    front_matter = {
        "id": ctx.id,
        "url": ctx.url,
        "title": ctx.title,
        "labels": ctx.labels,
        "suspected_prs": ctx.suspected_prs,
        "linked_prs": ctx.linked_prs,
        "fetched_at": ctx.fetched_at or datetime.now(tz=timezone.utc).isoformat(),
    }

    yaml_block = yaml.dump(front_matter, default_flow_style=False, sort_keys=False)
    content = f"---\n{yaml_block}---\n\n{ctx.body_markdown}"
    path.write_text(content, encoding="utf-8")


def _parse_url_or_id(s: str) -> tuple[str, str, str]:
    """Parse input into (org, repo, id).

    Accepts:
      - Full URL: https://github.com/<org>/<repo>/issues/<n>
      - Shorthand: org/repo#n
      - Bare ID: n (uses env var MOSDAT_DEFAULT_ISSUE_REPO or default)
    """
    s = s.strip()

    # Try full URL
    match = re.match(
        r"https://github\.com/([^/]+)/([^/]+)/issues/(\d+)",
        s,
    )
    if match:
        return match.group(1), match.group(2), match.group(3)

    # Try org/repo#n
    match = re.match(r"([^/]+)/([^#]+)#(\d+)", s)
    if match:
        return match.group(1), match.group(2), match.group(3)

    # Bare ID — pull org/repo from env or default
    if re.match(r"^\d+$", s):
        default_repo = os.environ.get(
            "MOSDAT_DEFAULT_ISSUE_REPO", "RocketChat/Rocket.Chat.Electron"
        )
        parts = default_repo.split("/", 1)
        if len(parts) != 2:
            raise ValueError(
                f"MOSDAT_DEFAULT_ISSUE_REPO must be 'org/repo', got: {default_repo}"
            )
        return parts[0], parts[1], s

    raise ValueError(
        f"Could not parse issue identifier: {s}. "
        "Expected: full URL, org/repo#id, or bare numeric id."
    )


def _fetch_from_github(org: str, repo: str, issue_id: str) -> IssueContext:
    """Fetch issue from GitHub REST API and parse into IssueContext."""
    url = f"https://api.github.com/repos/{org}/{repo}/issues/{issue_id}"

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "mosdat-issue-confirm/1.0",
    }

    # Add auth token if available (increases rate limit from 60 to 5000 req/hr)
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise RuntimeError(f"Failed to fetch {url}: {e}")
    except Exception as e:
        raise RuntimeError(f"Error fetching or parsing {url}: {e}")

    # Extract title
    title = data.get("title", "")

    # Extract labels
    labels = [label["name"] for label in data.get("labels", [])]

    # Extract body
    body = data.get("body", "")

    # Parse suspected PRs (regex: "Likely PR #3266", "Suspected PR #3266", "caused by #3266", etc.)
    suspected_prs = []
    suspected_pattern = r"(?i)(?:likely|suspected|caused by|introduced by)\s+(?:pr\s*)?#(\d+)"
    for match in re.finditer(suspected_pattern, body):
        suspected_prs.append(match.group(1))

    # Parse linked PRs (all issue/PR numbers mentioned, dedupe, exclude the issue's own id)
    linked_prs = set()
    # Match standalone #nnnn patterns
    link_pattern = r"(?:^|\s)#(\d+)"
    for match in re.finditer(link_pattern, body):
        pr_id = match.group(1)
        if pr_id != issue_id:  # exclude the issue itself
            linked_prs.add(pr_id)

    ctx = IssueContext(
        id=issue_id,
        url=f"https://github.com/{org}/{repo}/issues/{issue_id}",
        title=title,
        labels=labels,
        suspected_prs=suspected_prs,
        linked_prs=sorted(linked_prs),
        body_markdown=body,
        fetched_at=datetime.now(tz=timezone.utc).isoformat(),
    )

    return ctx
