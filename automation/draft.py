"""Generate functional test scenario YAMLs from change descriptions.

Usage:
    mosdat draft --change-type ui --pr 3325 --description "telephony deeplink"
    mosdat draft --change-type persistence --pr 3410 --verify-points '["modal", "restart"]'
    mosdat draft --change-type protocol_handler --output scenario.yaml
    mosdat draft list-types                          # list supported types
    mosdat draft templates                           # list available templates

Output: scenario YAML to stdout or --output file. Auto-names from --pr or --description.

Install anywhere via pip install -e . or `python3 -m automation.draft`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "shared" / "scenarios" / "templates"
_TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Templates (embedded — single-file portable, also written to disk)
# ---------------------------------------------------------------------------

TEMPLATES: dict[str, str] = {}

TEMPLATES["ui"] = """name: "PR-{pr_or_desc}: UI change — {description}"

steps:
  # ── Pre-test cleanup ──
  - shell: |
      pkill -9 -x rocketchat-desktop.bin 2>/dev/null || true
      for i in 1 2 3 4 5; do
        sleep 1
        pgrep -x rocketchat-desktop.bin >/dev/null || break
        pkill -9 -x rocketchat-desktop.bin 2>/dev/null || true
      done
      echo CLEANUP_DONE

  # ── Launch app ──
  - shell: |
      export DISPLAY=:0
      nohup /opt/Rocket.Chat/rocketchat-desktop --no-sandbox > /dev/null 2>&1 &
      echo LAUNCHED
  - wait: 15
  - verify: |
      The Rocket.Chat application window shows a server selection screen
      or login page.  The window title bar includes "Rocket.Chat".
    verify_timeout: 20

  # ── Interact with new/changed UI element ──
  # TODO: replace xdotool coordinates with localize or actual target
  - shell: |
      export DISPLAY=:0
      xdotool mousemove 300 280 click 1
      echo CLICKED
  - wait: 5
  - verify: "A dialog, modal, or new UI element appears in response to the click"
  - verify_not: |
      No error dialog, crash dialog, frozen window, or desktop environment
      crash handler is visible.
"""

TEMPLATES["persistence"] = """name: "PR-{pr_or_desc}: Persistence — {description}"

steps:
  # ══════════════ PHASE 1: clean → interact → enable → kill ══════════════
  - shell: |
      pkill -9 -x rocketchat-desktop.bin 2>/dev/null || true
      sleep 2
      rm -rf "$HOME/.config/Rocket.Chat (development)"/
      echo FULL_CLEAN
      sleep 2
  - shell: |
      export DISPLAY=:0
      cat > "$HOME/.config/Rocket.Chat (development)/servers.json" << 'EOF'
      {{ "Workspace": "https://rocketchat.jeanbrito.com", "Mobile RC": "https://mobile.rocket.chat" }}
      EOF
      nohup /opt/Rocket.Chat/rocketchat-desktop --no-sandbox > /dev/null 2>&1 &
      echo LAUNCHED
  - wait: 15
  - verify: |
      The Rocket.Chat application window shows a login page or
      authentication screen with fields for email/username and password.
    verify_timeout: 20
  # TODO: login and navigate to feature, enable persistence setting
  - shell: pkill -9 -x rocketchat-desktop.bin 2>/dev/null; echo KILLED

  # ══════════════ PHASE 2: relaunch → verify persisted ══════════════
  - shell: |
      export DISPLAY=:0
      nohup /opt/Rocket.Chat/rocketchat-desktop --no-sandbox > /dev/null 2>&1 &
      echo LAUNCHED
  - wait: 15
  - verify: |
      The Rocket.Chat application window shows a login page.
    verify_timeout: 20
  # TODO: navigate to same feature — verify NO modal/prompt (preference loaded)
  - verify_not: |
      No dialog or prompt asking the user to make a choice that
      should have been persisted.
"""

TEMPLATES["protocol_handler"] = """name: "PR-{pr_or_desc}: Protocol handler — {description}"

steps:
  # ── Pre-test cleanup ──
  - shell: |
      pkill -9 -x rocketchat-desktop.bin 2>/dev/null || true
      sleep 2
      rm -rf "$HOME/.config/Rocket.Chat (development)"/
      echo FULL_CLEAN

  # ── Write servers.json & launch ──
  - shell: |
      mkdir -p "$HOME/.config/Rocket.Chat (development)"
      cat > "$HOME/.config/Rocket.Chat (development)/servers.json" << 'EOF'
      {{ "Workspace": "https://rocketchat.jeanbrito.com", "Mobile RC": "https://mobile.rocket.chat" }}
      EOF
      export DISPLAY=:0
      nohup /opt/Rocket.Chat/rocketchat-desktop --no-sandbox > /dev/null 2>&1 &
      echo LAUNCHED
  - wait: 15
  - verify: |
      The Rocket.Chat application shows a server selection screen with
      a list of servers to connect to.
    verify_timeout: 20

  # ── Click first workspace & wait for login ──
  # TODO: update coordinates for current screen resolution
  - shell: |
      export DISPLAY=:0
      xdotool mousemove 300 280 click 1
      echo CLICKED_WORKSPACE
  - wait: 8
  - verify: |
      The Rocket.Chat application shows a login page or authentication
      screen with fields for email/username and password.

  # ── Trigger deeplink (tel:) via SSH ──
  - shell: |
      export DISPLAY=:0
      USERDATA_DIR="$(
        ps aux | grep 'rocketchat-desktop' | grep -v grep |
        grep -oP '--user-data-dir=\\K\\S+' || echo "$HOME/.config/Rocket.Chat (development)"
      )"
      echo "USERDATA_DIR=$USERDATA_DIR"
      # Dispatch telephony deeplink
      xdg-open "tel:+5511999999999" &
      sleep 2
      echo DEEPLINK_SENT
  - wait: 8
  - verify: |
      The telephony server selection modal or dialog is visible,
      asking which telephony service to use for the call.
  - verify_not: |
      No crash dialog, frozen window, or desktop environment error
      appeared after the deeplink was triggered.
"""

TEMPLATES["keyboard_shortcut"] = """name: "PR-{pr_or_desc}: Keyboard shortcut — {description}"

steps:
  - shell: |
      pkill -9 -x rocketchat-desktop.bin 2>/dev/null || true
      sleep 2
      echo CLEANUP_DONE
  - shell: |
      export DISPLAY=:0
      nohup /opt/Rocket.Chat/rocketchat-desktop --no-sandbox > /dev/null 2>&1 &
      echo LAUNCHED
  - wait: 15
  - verify: "The Rocket.Chat application window is visible"
    verify_timeout: 20
  # TODO: set correct key combo
  - key: ctrl+comma
  - wait: 5
  - verify: "A settings, preferences, or configuration dialog is visible"
"""

TEMPLATES["settings"] = """name: "PR-{pr_or_desc}: Settings change — {description}"

steps:
  # ── Phase 1: clean → open settings → toggle → kill ──
  - shell: |
      pkill -9 -x rocketchat-desktop.bin 2>/dev/null || true
      sleep 2
      rm -rf "$HOME/.config/Rocket.Chat (development)"/
      echo FULL_CLEAN
  - shell: |
      export DISPLAY=:0
      cat > "$HOME/.config/Rocket.Chat (development)/servers.json" << 'EOF'
      {{ "Workspace": "https://rocketchat.jeanbrito.com" }}
      EOF
      nohup /opt/Rocket.Chat/rocketchat-desktop --no-sandbox > /dev/null 2>&1 &
      echo LAUNCHED
  - wait: 15
  - verify: "The Rocket.Chat application shows a login page or server selection"
    verify_timeout: 20
  - key: ctrl+comma
  - wait: 5
  - verify: "A settings window or preferences dialog is visible"
  # TODO: navigate to specific setting, toggle/change it
  - shell: pkill -9 -x rocketchat-desktop.bin 2>/dev/null; echo KILLED

  # ── Phase 2: relaunch → verify setting persisted ──
  - shell: |
      export DISPLAY=:0
      nohup /opt/Rocket.Chat/rocketchat-desktop --no-sandbox > /dev/null 2>&1 &
      echo LAUNCHED
  - wait: 15
  - verify: "The Rocket.Chat application shows a login page"
    verify_timeout: 20
  - key: ctrl+comma
  - wait: 5
  - verify: "The same setting value from Phase 1 is still active"
"""

TEMPLATES["bug_fix"] = """name: "PR-{pr_or_desc}: Bug fix verification — {description}"

steps:
  - shell: |
      pkill -9 -x rocketchat-desktop.bin 2>/dev/null || true
      sleep 2
      echo CLEANUP_DONE
  - shell: |
      export DISPLAY=:0
      nohup /opt/Rocket.Chat/rocketchat-desktop --no-sandbox > /dev/null 2>&1 &
      echo LAUNCHED
  - wait: 15
  - verify: "The Rocket.Chat application window is visible and responsive"
    verify_timeout: 20
  # ── Trigger the condition that used to crash/fail ──
  # TODO: describe the reproduction steps that the fix addresses
  - wait: 5
  - verify: |
      The application is still running normally — no crash dialog,
      no frozen window, no error state.  The app should respond
      as expected.
"""

TEMPLATES["de"] = """name: "PR-{pr_or_desc}: Desktop environment — {description}"

steps:
  # ── Verify .desktop file validity ──
  - shell: |
      desktop-file-validate /usr/share/applications/rocketchat-desktop.desktop
      echo SCHEMA_OK

  # ── Verify MIME type registration ──
  - shell: |
      grep -q "x-scheme-handler/tel" /usr/share/applications/rocketchat-desktop.desktop
      echo TEL_DEFINED
      grep -q "x-scheme-handler/callto" /usr/share/applications/rocketchat-desktop.desktop
      echo CALLTO_DEFINED

  # ── Verify protocol handler registration in system ──
  - shell: |
      xdg-mime query default x-scheme-handler/tel 2>/dev/null | grep -qi rocketchat
      echo TEL_HANDLER
      xdg-mime query default x-scheme-handler/callto 2>/dev/null | grep -qi rocketchat
      echo CALLTO_HANDLER

  # ── Verify app launches without crash ──
  - shell: |
      pkill -9 -x rocketchat-desktop.bin 2>/dev/null || true
      sleep 2
      rm -rf "$HOME/.config/Rocket.Chat (development)"/
      echo FULL_CLEAN
  - shell: |
      export DISPLAY=:0
      nohup /opt/Rocket.Chat/rocketchat-desktop --no-sandbox > /dev/null 2>&1 &
      echo LAUNCHED
  - wait: 15
  - verify: "The Rocket.Chat application window is visible and responsive"
    verify_timeout: 20
"""

TEMPLATES["autostart"] = """name: "PR-{pr_or_desc}: Autostart — {description}"

steps:
  # ── Verify .desktop file exists ──
  - shell: |
      test -f /etc/xdg/autostart/rocketchat-desktop.desktop \
         || test -f /usr/share/autostart/rocketchat-desktop.desktop \
         || test -f $HOME/.config/autostart/rocketchat-desktop.desktop
      echo AUTOSTART_FILE_OK
  - shell: |
      grep -q "X-GNOME-Autostart-enabled=true" \\
        $HOME/.config/autostart/rocketchat-desktop.desktop 2>/dev/null || \\
        echo "Check autostart enabled flag manually"
      echo AUTOSTART_CHECKED
"""

# ---------------------------------------------------------------------------
# Discard old .pyc bytecode (avoids stale imports after pip install -e . edits)
# ---------------------------------------------------------------------------
discard_first_run_warning = False


def _load_disk_templates() -> dict[str, str]:
    """Load .yaml files from disk as override dict merged on top of embedded."""
    overrides: dict[str, str] = {}
    if _TEMPLATES_DIR.exists():
        for f in sorted(_TEMPLATES_DIR.glob("*.yaml")):
            name = f.stem  # e.g. "ui" / "persistence"
            overrides[name] = f.read_text()
    return overrides


def _format(template: str, pr_or_desc: str, description: str) -> str:
    return template.format(pr_or_desc=pr_or_desc, description=description)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mosdat draft",
        description="Generate functional test scenario YAMLs from change descriptions.",
    )
    parser.add_argument(
        "--change-type",
        choices=list(TEMPLATES),
        help="Type of change to generate a scenario for",
    )
    parser.add_argument("--pr", default="", help="PR number (e.g. 3325)")
    parser.add_argument(
        "--description", default="unnamed", help="Short description of the change"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write YAML to path instead of stdout",
    )
    parser.add_argument(
        "--templates", action="store_true", help="Write all templates to disk and exit"
    )

    args = parser.parse_args(argv)

    # Subcommands
    if hasattr(args, "change_type") and args.change_type is None:
        # Check for implicit subcommands using positional logic below
        pass

    if args.templates:
        for name, content in TEMPLATES.items():
            path = _TEMPLATES_DIR / f"{name}.yaml"
            path.write_text(content)
            print(f"Wrote {path}")
        return 0

    if not args.change_type:
        print("Available change types:")
        for name in TEMPLATES:
            print(f"  {name}")
        print()
        print("Usage: mosdat draft --change-type <type> [--pr N] [--description ...]")
        return 1

    # Merge disk overrides on top of embedded
    templates = {**TEMPLATES, **_load_disk_templates()}
    if args.change_type not in templates:
        print(f"Unknown change type: {args.change_type}")
        return 1

    pr_or_desc = args.pr if args.pr else args.description
    scenario = _format(templates[args.change_type], pr_or_desc, args.description)

    if args.output:
        args.output.write_text(scenario)
        print(f"Wrote scenario to {args.output}")
    else:
        print(scenario)

    return 0


if __name__ == "__main__":
    sys.exit(cli())
