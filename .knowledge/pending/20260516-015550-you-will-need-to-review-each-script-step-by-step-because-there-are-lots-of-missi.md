---
date: "2026-05-16"
project: mosdat
topic: you will need to review each script step by step, because there are lots of missing steps that you will need to control
kind: decision
scope: project-shared
confidence: low
---

Assistant: VM 101 started + SSH OK. 4th tester pass running all 5 scenarios in background.

User: you will need to review each script step by step, because there are lots of missing steps that you will need to control the vm using mosdat and then write the test correctly ultrathink

Assistant: Agreed. Scenarios authored blind. Need to walk real UI, capture each step, rewrite. Halting current run, switching to manual exploration.

Assistant: Killing in-flight tester (loop polling broken). Then halt RC on VM and inspect mosdat author/visual subcommands.
