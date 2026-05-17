---
date: "2026-05-16"
project: mosdat
topic: Root Cause Analysis
kind: war-story
scope: project-shared
confidence: medium
---

| 3325-link-formats | **FAIL** | N/A | VNC WebSocket timeout |
| 3325-cold-start | **FAIL** | N/A | VNC WebSocket timeout |

### Root Cause Analysis

All five scenarios failed at the **VNC transport layer**, before executing any test steps.

**Evidence**:
1. **Proxmox API succeeds**: vncproxy endpoint returns HTTP 200, ticket issued
