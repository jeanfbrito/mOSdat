# Worked Example: PR #XXXX — Quick Reply Feature

This meta-template walks through what the mosdat-authoring skill prompts when
asked: "Add mOSdat coverage for PR #XXXX, which adds a Quick Reply feature
(compose and send a reply directly from a notification without opening the app)."

---

## Step 1: Pre-flight

```bash
mosdat build --pr XXXX --deploy ubuntu2404 --verify-symbol quickReplyHandler
mosdat preflight examples/rocketchat.toml --vms ubuntu2404 --test 3325-master-toggle
mosdat doctor examples/rocketchat.toml --vms ubuntu2404
mosdat recipes search "notification"
mosdat recipes search "tray"
mosdat trace examples/rocketchat.toml --vms ubuntu2404
```

Expected: all green. `mosdat recipes search "notification"` surfaces one recipe:
`linux-notification-click-x11.yaml` — notes that D-Bus notification actions
require `DISPLAY=:0` and `DBUS_SESSION_BUS_ADDRESS` exported.

---

## Step 2: List Atomic Interactions

The Quick Reply feature needs these discrete interactions:

1. Kill app + wipe user data (setup baseline)
2. Launch app with a config that pre-stages a logged-in workspace session
3. Trigger a notification (simulate an incoming message via IPC or dispatch shell)
4. Click the "Reply" action button on the notification
5. Type a reply in the Quick Reply compose box
6. Send the reply (Enter or click Send)
7. Verify the reply was dispatched (compose box closes, no error)
8. Verify app is still alive

---

## Step 3: Routine Mapping

```bash
mosdat routines list
```

| Atomic interaction | Existing routine | Action |
|---|---|---|
| Kill + wipe | `cleanup-rocketchat` | Reuse |
| Launch with config | `launch-rocketchat` | Reuse (add `session_cookie` input if needed) |
| Trigger notification | none | Author new: `trigger-test-notification` |
| Click Reply action | none | Author new: `click-notification-action` |
| Type + send reply | none | Inline (feature-specific, one-off) |
| Verify app alive | `verify-app-alive` | Reuse |

Result: 3 reusable existing routines + 2 new routines to author.

---

## Step 4: New Routine — `trigger-test-notification`

```bash
# Save to: shared/routines/trigger-test-notification.yaml
```

```yaml
name: trigger-test-notification
description: Dispatch a simulated incoming-message notification to the running RC Desktop instance
schema_version: v1
tags: [notifications, tray, ui-surface, ipc]

inputs:
  - name: sender
    type: string
    required: false
    default: "Test Sender"
  - name: message
    type: string
    required: false
    default: "Hello from test harness"
  - name: room_id
    type: string
    required: true

preconditions:
  - verify: "Rocket.Chat Desktop window is open and visible — login or workspace content shown"

steps:
  - shell: |
      DISPLAY=:0 xdotool key ctrl+shift+j
  - wait: 2
  - shell: |
      DISPLAY=:0 xdotool type \
        "window.RocketChat.notifications.testNotification({sender:'{{ sender }}',msg:'{{ message }}',rid:'{{ room_id }}'})"
      DISPLAY=:0 xdotool key Return
  - wait: 3

postconditions:
  - verify: "A desktop notification or notification banner appeared — it may say '{{ sender }}' or contain a message preview"

on_failure:
  - shell: |
      DISPLAY=:0 scrot /tmp/trigger-test-notification-failure.png
  - shell: journalctl --user --since "2 minutes ago" --no-pager -n 30
```

```bash
mosdat routines test trigger-test-notification \
  --vms ubuntu2404 \
  --fixture rc-launched-1-server-telephony-on \
  --with room_id=GENERAL \
  --config examples/rocketchat.toml
```

Iterate until exit 0.

---

## Step 5: New Routine — `click-notification-action`

```bash
# Save to: shared/routines/click-notification-action.yaml
```

```yaml
name: click-notification-action
description: Click a named action button on the most recent desktop notification
schema_version: v1
tags: [notifications, tray, ui-surface, x11]

inputs:
  - name: action_label
    type: string
    required: false
    default: "Reply"

preconditions:
  - verify: "A desktop notification is visible with an action button"

steps:
  - localize: "notification action button labeled '{{ action_label }}'"
    click: true
  - wait: 1

postconditions:
  - verify: "The notification action was triggered — either the notification dismissed or a compose input appeared"

on_failure:
  - shell: |
      DISPLAY=:0 scrot /tmp/click-notification-action-failure.png

fallbacks:
  - when: "capability.wayland"
    steps:
      - shell: |
          gdbus call --session \
            --dest org.freedesktop.Notifications \
            --object-path /org/freedesktop/Notifications \
            --method org.freedesktop.Notifications.ActionInvoked 1 "{{ action_label }}"
      - wait: 1
```

```bash
mosdat routines test click-notification-action \
  --vms ubuntu2404 \
  --fixture rc-launched-1-server-telephony-on \
  --config examples/rocketchat.toml
```

---

## Step 6: Compose Scenario (~50 lines)

```bash
# Save to: shared/scenarios/functional/XXXX-quick-reply.yaml
```

```yaml
name: "Rocket.Chat Desktop — Quick Reply from notification (PR #XXXX)"
vars:
  workspace_url: "https://rocketchat.jeanbrito.com"
  test_room_id: "GENERAL"

phases:
  - id: A
    name: "notification appears with Reply action"
    from_step: 1
  - id: B
    name: "reply sent via Quick Reply compose"
    from_step: 6

steps:
  # ── A1: clean slate ───────────────────────────────────────────────────────
  - routine: cleanup-rocketchat
  - routine:
      name: launch-rocketchat
      with:
        servers:
          - { title: "Workspace", url: "{{ workspace_url }}" }

  # ── A2: trigger notification ──────────────────────────────────────────────
  - routine:
      name: trigger-test-notification
      with:
        sender: "Alice"
        message: "Can you reply?"
        room_id: "{{ test_room_id }}"

  # ── A3: assert notification appeared with Reply action ────────────────────
  - verify: "A desktop notification is visible. It shows 'Alice' as sender and has a 'Reply' action button."
    verify_timeout: 10
    retries: 3

  # ── B1: click Reply and compose ───────────────────────────────────────────
  - routine:
      name: click-notification-action
      with: { action_label: "Reply" }

  - verify: "A Quick Reply compose input is visible — a text box for typing a reply, attached to or near the notification"
    verify_timeout: 8
    retries: 3

  # ── B2: type and send ─────────────────────────────────────────────────────
  - shell: |
      DISPLAY=:0 xdotool type "automated reply from harness"
  - key: Return
  - wait: 2

  # ── B3: assert reply dispatched ───────────────────────────────────────────
  - verify: "The Quick Reply compose box is no longer visible. No error dialog is shown. The notification dismissed or compose field closed after send."
    verify_timeout: 10
    retries: 3

  # ── cleanup ───────────────────────────────────────────────────────────────
  - routine: verify-app-alive
  - routine: cleanup-rocketchat
```

---

## Result Summary

- Atomic interactions identified: 8
- Existing routines reused: 3 (`cleanup-rocketchat`, `launch-rocketchat`, `verify-app-alive`)
- New routines authored: 2 (`trigger-test-notification`, `click-notification-action`)
- Routine isolation tests: 2 (one per new routine, both green before scenario compose)
- Scenario line count: ~50 lines (vs ~200+ inline equivalent)
- Feature-specific inline steps: type reply + send + 2 targeted `verify:` steps

This matches the routines-first pattern: scenario reads as intent, routines hold the mechanics.
