---
date: "2026-05-01"
project: mOSdat
tags:
  - mosdat
  - vlm
  - llama-swap
  - vision
  - infrastructure
topic: 'mosdat: VLM endpoint vision-payload 502 (qwen3.6-35b-a3b-uncensored-vl broken)'
kind: war-story
scope: project-shared
category: infrastructure
confidence: high
accessed: 32
last_accessed: "2026-05-01"
---

## Finding (2026-05-01 live debug)
Configured VLM model `qwen3.6-35b-a3b-uncensored-vl` at `192.168.13.62:5001/v1` returns:
- HTTP 200 in 15s for text-only completions
- HTTP 502 in 1.5s for image+text completions

Other models on the same llama-swap proxy:
- `holo2-4b`: vision works, 5-12s per call
- `qwen3.6-35b-a3b-vl` (no `-uncensored`): also 502
- `nemotron-3-nano-30b-a3b-vl`: works, slow (~50s/call)

## Root cause
llama-swap config for the qwen3.6-35b vl variants either skips the multimodal projector or proxy refuses image payloads. Out of scope for mosdat.

## Workaround for live runs
Override CLI:
```
mosdat functional ... --model holo2-4b --verify-model holo2-4b
```
Or update `examples/rocketchat.toml [vlm]` to point at a working model.

## Takeaway
mosdat reports VLM endpoint errors via failover wrapper now (commit 02234d3). Validate the configured model on a test image BEFORE running scenarios.
