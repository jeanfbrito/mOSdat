---
date: "2026-05-18"
project: mosdat
topic: test_live_dashboard_http hung 19+ min on server.shutdown() — single-threaded HTTPServer + SSE handler
kind: war-story
scope: project-shared
confidence: high
---

`tests/test_live_dashboard_http.py::test_stream_content_type` hung the entire pytest suite for 19+ minutes (sometimes indefinitely). pytest output went silent. Killing hung pytest processes was the only escape.

Root cause: `tests/test_live_dashboard_http.py::_start_server` instantiated plain `HTTPServer` (single-threaded). The SSE `/stream` test made a streaming GET that kept the handler thread blocked inside `do_GET` writing event-stream chunks. `server.shutdown()` blocks on `_is_shut_down`, which is only set when `serve_forever`'s poll loop terminates — but the poll loop can't return while the only worker is stuck. Deadlock.

Production `automation/live_dashboard.py:78` correctly uses `ThreadingHTTPServer`. The test helper diverged.

Fix: switch `_start_server` to `ThreadingHTTPServer` (matches production). Each request runs in its own thread; poll loop is free to notice the stop flag.

Takeaway: when testing an HTTP server that production deploys threaded, instantiate it threaded in tests too — single-thread + long-running handlers = `shutdown()` deadlock.
