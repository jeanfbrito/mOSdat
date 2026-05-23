# One-shot Windows-side prereq install for the UIA driver.
# Run ONCE on the target Windows VM (e.g. windows10/windows11) before
# invoking the worker:
#   ssh user@vm "powershell -ExecutionPolicy Bypass -File C:\tmp\mosdat_uia_setup.ps1"
# Idempotent: re-running is a fast no-op if pip packages already present.
#
# Requires: Windows OpenSSH enabled (probed in task #47), Python 3.11+
# already on PATH (install via python.org or `winget install Python.Python.3.11`).
$ErrorActionPreference = "Stop"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "python not found in PATH — install Python 3.11+ from python.org or winget install Python.Python.3.11"
    exit 1
}

Write-Host "[setup] python: $((python --version) 2>&1)"

python -m pip install --upgrade pip
python -m pip install pywinauto pywin32 comtypes psutil

# Sanity checks — every import the worker performs at startup.
python -c "import pywinauto; print('pywinauto', pywinauto.__version__)"
python -c "import comtypes; print('comtypes', comtypes.__version__)"
python -c "import win32api; print('pywin32 ok')"
python -c "import psutil; print('psutil', psutil.__version__)"

# Smoke: Desktop(backend='uia') must instantiate without throwing.
python -c "from pywinauto import Desktop; d = Desktop(backend='uia'); print('Desktop(backend=uia) ok, windows:', len(d.windows()))"

Write-Host "[setup] uia setup OK"
