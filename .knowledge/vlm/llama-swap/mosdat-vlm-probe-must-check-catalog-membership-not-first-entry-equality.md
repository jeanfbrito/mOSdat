---
date: "2026-05-02"
project: mOSdat
tags:
  - mosdat
  - vlm
  - llama-swap
  - openai-api
topic: 'mosdat: VLM probe must check catalog membership not first-entry equality'
kind: lesson
scope: project-shared
category: vlm/llama-swap
confidence: high
accessed: 1
last_accessed: "2026-05-03"
---

## Lesson (2026-05-02)
First L7 implementation of `probe_model()` returned `models[0]["id"]` from `/v1/models` and compared for equality with `expected_model`. Always failed unless expected was alphabetically first.

## Why
`/v1/models` on llama-swap (and most OpenAI-compatible servers) returns the FULL configured catalog, not the active model. There is no standard endpoint to query "currently loaded model" — llama-swap rotates on demand based on first request.

## Fix (commit 63aeb88)
Replace `probe_model() -> str` with `list_models() -> list[str]`. Caller checks `expected in models`. Mismatch → exit 3 with helpful error message listing first 5 models in catalog.

## Takeaway
For any "is the right model configured?" check against an OpenAI-compatible endpoint, use catalog membership, not single-id equality. The /v1/models response shape is not a "served model" indicator.
