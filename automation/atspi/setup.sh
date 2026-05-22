#!/usr/bin/env bash
# One-shot VM-side prereq install for the AT-SPI driver.
# Run ONCE on the target Linux VM (e.g. ubuntu2204) before invoking the worker.
#   ssh user@vm 'bash /tmp/mosdat_atspi_setup.sh'
# Idempotent: re-running is a fast no-op.
set -euo pipefail

PKGS=(
  gir1.2-atspi-2.0
  libatspi2.0-0
  python3-gi
  python3-pil       # PIL for screenshot capture
  wmctrl
  imagemagick       # provides `import` for screenshots
  at-spi2-core      # ensures at-spi-bus-launcher is present
)

MISSING=()
for p in "${PKGS[@]}"; do
  dpkg -s "$p" >/dev/null 2>&1 || MISSING+=("$p")
done

if [ ${#MISSING[@]} -gt 0 ]; then
  echo "[setup] installing: ${MISSING[*]}"
  sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${MISSING[@]}"
else
  echo "[setup] all packages present"
fi

# Frame hash uses stdlib hashlib.blake2b — no extra dep.

# Sanity: AT-SPI bus reachable.
if dbus-send --print-reply --dest=org.a11y.Bus /org/a11y/bus org.a11y.Bus.GetAddress >/dev/null 2>&1; then
  echo "[setup] a11y bus: OK"
else
  echo "[setup] a11y bus: NOT REACHABLE — POC may fail; try logging in to a GNOME session"
fi

echo "[setup] done"
