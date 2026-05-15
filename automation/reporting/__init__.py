"""Reporting facade with lazy attribute access.

Lazy ``__getattr__`` avoids circular-import failures when tests load
sibling modules (``dashboard``, ``report``) via ``importlib`` spec
hackery before the package itself is fully initialized.
"""

from __future__ import annotations

__all__ = [
    "generate_report",
    "generate_index",
    "collect_runs",
    "load_run",
]


def __getattr__(name: str):
    if name == "generate_report":
        from .report import generate_report
        return generate_report
    if name in ("generate_index", "collect_runs", "load_run"):
        from .aggregate import generate_index, collect_runs, load_run
        return {"generate_index": generate_index, "collect_runs": collect_runs, "load_run": load_run}[name]
    raise AttributeError(f"module 'automation.reporting' has no attribute {name!r}")
