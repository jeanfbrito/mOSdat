# Contributing to mOSdat

Thank you for your interest in contributing. This guide covers local setup, development workflow, and how to add support for a new OS to the test matrix.

## Project Overview

mOSdat is an automated testing framework for validating desktop applications across multiple Linux distributions using Proxmox VMs with GPU passthrough. It orchestrates VM provisioning, application deployment, and test execution to catch platform-specific bugs that manual testing misses. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full design.

## Local Setup

### Prerequisites

- Python 3.11+
- Proxmox VE 8.x access (configured in `.env`)
- Git clone of the project

### Installation

```bash
# Clone the repository
git clone https://github.com/jeanfbrito/mOSdat.git
cd mOSdat

# Install in development mode with dev dependencies
pip install -e ".[dev]"

# Verify installation
mosdat --help
```

### Environment Configuration

```bash
# Copy the example configuration
cp examples/rocketchat.toml <your-test-name>.toml

# Create environment file
cat > .env << 'EOF'
MOSDAT_PROXMOX_PASSWORD=<your-proxmox-password>
MOSDAT_VM_PASSWORD=<your-vm-password>
FUNCTIONAL_WORKSPACE_URL=<your-test-workspace-url>
FUNCTIONAL_TEST_USER=<test-username>
FUNCTIONAL_TEST_PASSWORD=<test-password>
EOF
chmod 600 .env
```

See `.env.example` for all available variables.

## Running Tests

### Unit and Integration Tests

```bash
# Run all tests with minimal output
pytest -q

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/test_proxmox_api.py -v
```

### Code Quality Checks

```bash
# Check code style and lint
ruff check .

# Type checking
mypy automation/

# Fix formatting issues (ruff supports --fix)
ruff check . --fix
```

### Live Smoke Tests

For running live tests against actual VMs, see [docs/runbooks/matrix-run.md](docs/runbooks/matrix-run.md). This runbook contains copy-paste commands for the full test matrix, per-OS deployment, and GPU rotation sequences.

## Pull Request Workflow

### Branch and Commit Style

1. Create a feature branch from `main`:
   ```bash
   git checkout -b feat/my-feature
   ```

2. Use conventional commits. Check recent history for style:
   ```bash
   git log --oneline -20
   ```

   Examples:
   - `feat(proxmox): add VM snapshot management`
   - `fix(gpu): resolve VFIO attachment timeout`
   - `test(scenarios): add smoke test for KDE Wayland`
   - `docs: clarify GPU rotation sequence`

3. Keep commits logical and atomic — one feature per commit.

### PR Requirements

- One logical change per PR (feature, fix, or test)
- For live test runs: attach `report.html` or results summary
- Tests must pass: `pytest -q` and `ruff check .`
- Code must type-check: `mypy automation/`

### Before Submitting

```bash
# Run the full quality pipeline
pytest -q && ruff check . && mypy automation/
```

## Code Style

Reference [AGENTS.md](AGENTS.md) for complete style guidelines:

### Do

- Use `set -euo pipefail` at the top of bash scripts
- Log output with `log()`, `log_error()`, `log_success()`
- Exit with 0 for success, 1 for failure, 2 for unknown status
- Document environment variables and file paths clearly
- Test cross-platform — check behavior on at least two different distros

### Don't

- Hardcode credentials (use `.env` or environment variables)
- Commit binary packages (`.rpm`, `.deb`, `.AppImage`)
- Modify `shared/` for OS-specific changes — create OS-specific folders instead
- Use unguarded shell globbing that silently fails when no matches exist

## Adding a New OS to the Matrix

See [docs/runbooks/adding-new-os.md](docs/runbooks/adding-new-os.md) for the complete step-by-step runbook. In brief:

1. **Provision** a new Proxmox VM using the ISO upload process
2. **Create** `os/<distro>/` directory with `config.sh` (VMID, IP, package format, package manager)
3. **Write** or copy a smoke scenario YAML (use `shared/scenarios/functional/rocketchat-smoke-linux.yaml` as template)
4. **Validate** that VNC framebuffer captures the display correctly (requires `vga=std`, not `virtio`)
5. **Check** OS-specific quirks (keyring paths, package install command, GPU config)
6. **Iterate** using `mosdat functional --until-step <n>` until tests pass
7. **Register** the VM in `examples/rocketchat.toml` under `[[vm]]` table

## Questions?

Check [AGENTS.md](AGENTS.md) for architecture and role responsibilities. For infrastructure questions, see [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).
