---
date: "2026-04-14"
project: mosdat
topic: Rocket.Chat is showing the server URL screen — exactly what the test needs! The issue was window z-order. I need to
kind: insight
scope: project-shared
confidence: medium
---

Assistant: The PowerShell SSH window (Session 1) is covering Rocket.Chat. Let me bring Rocket.Chat to the foreground using a Session 1 task:

Assistant: Rocket.Chat is showing the server URL screen — exactly what the test needs! The issue was window z-order. I need to integrate the bring-to-front action into the launch step. Let me add a `focus_app()` method to `InputInjector`:

Assistant: Now add `focus` field to `FunctionalStep` and wire it in the runner:

Assistant: Update the YAML to add `focus: "Rocket.Chat"`:
