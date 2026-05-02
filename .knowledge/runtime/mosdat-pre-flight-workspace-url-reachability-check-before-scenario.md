---
date: "2026-05-01"
project: mOSdat
tags:
  - mosdat
  - preflight
  - functional
  - workspace
  - fail-fast
topic: 'mosdat: pre-flight workspace URL reachability check before scenario'
kind: lesson
scope: project-shared
category: runtime
confidence: high
accessed: 25
last_accessed: "2026-05-02"
---

## Lesson (2026-05-01)
First full smoke run failed at step 5 because `https://rocketchat.jeanbrito.com/` returned HTTP 502 (RC backend down behind reverse proxy). Test consumed ~6 minutes of VLM time across 3 retries before failing with "login page never visible" — the VLM was repeatedly told the truth (server-unreachable error UI) but each verify burned 20s.

## Why it matters
A server at the workspace URL is a hard precondition for any login-flow smoke test. Failing fast on env unreachability:
- Saves ~5 min per run when server is down
- Surfaces operator-actionable error ("start your RC server") vs cryptic "VLM verify failed"
- Doesn't muddy events.jsonl with retry noise from a known-bad state

## Implementation pattern
In `cmd_functional` (after VLM warmup, before per-VM loop):

```python
def _preflight_workspace(url: str, timeout: int = 10) -> bool:
    if not url: return True
    if "://" not in url: url = "https://" + url.rstrip("/")
    try:
        req = urllib.request.Request(url + "/api/info", method="HEAD")
        r = urllib.request.urlopen(req, timeout=timeout)
        return 200 <= r.status < 500
    except urllib.error.HTTPError as e:
        return 200 <= e.code < 500   # 4xx still proves server is alive
    except Exception:
        return False
```

Print elapsed + status, allow `--skip-workspace-check` opt-out, abort early on failure with operator-actionable message.

## Takeaway
Pre-flight any external service the scenario depends on. VLM-driven tests are expensive — every minute spent on a known-broken precondition is wasted.
