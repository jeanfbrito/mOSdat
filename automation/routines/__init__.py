"""automation.routines — parameterized, tested, reusable procedure library (R1)."""

# R7: schema versioning
# Bump when the Routine schema gains incompatible changes.
# Older routines remain loadable as long as _migrate_to_current() in loader.py
# converts them to the current shape before Pydantic validation.
CURRENT_SCHEMA_VERSION: str = "v1"

# All versions that this mosdat build can load (including via migration).
# When adding a new version, append it here AND write a migration function
# in loader.py::_migrate_to_current.
SUPPORTED_SCHEMA_VERSIONS: list[str] = ["v1"]
