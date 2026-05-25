---
date: "2026-05-23"
project: mosdat
topic: Routine author provides as much semantic info as available. System degrades gracefully. Today everything funnels into
kind: decision
scope: project-shared
confidence: low
---

elif target.coords:   xdotool click    # explicit
  else:                 localize_click   # VLM last resort
```
Routine author provides as much semantic info as available. System degrades gracefully. Today everything funnels into VLM because no upper tier exists.

### 3. Unified WaitFor primitive
Windows-MCP folds polling into one call: text OR window OR element OR focused-element, timeout, interval, returns which fired. mOSdat likely has wait+retry decomposed. Folding collapses routine line count + localizes timing failures to one step.

### 4. Enriched capture payload
