---
date: "2026-05-25"
project: mOSdat
tags:
  - electron-builder
  - windows
  - deployment
  - asar
topic: electron-builder may fail after producing usable win-unpacked when Wine is missing
kind: lesson
scope: project-shared
category: mosdat/deployment
confidence: high
---

## What
The Windows product build produced a clean `win-unpacked`/`app.asar`, but `electron-builder` exited nonzero because Wine was missing later in the packaging path.

## Why
Treating the process exit alone as build absence would have hidden a usable installed-app artifact for QA.

## Check
Inspect `app.asar` directly for expected product symbols/strings and absence of diagnostic instrumentation before deploying.

## Takeaway
For Windows QA, validate artifact contents, not only package-command exit status.
