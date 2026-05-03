__all__ = [
    "TestRunner",
    "FunctionalRunner",
    "FunctionalStep",
    "StepFailed",
    "load_test_yaml",
]


def __getattr__(name):
    if name == "TestRunner":
        from .smoke import TestRunner

        return TestRunner
    if name in {"FunctionalRunner", "StepFailed"}:
        from .functional import FunctionalRunner, StepFailed

        return {"FunctionalRunner": FunctionalRunner, "StepFailed": StepFailed}[name]
    if name in {"FunctionalStep", "load_test_yaml"}:
        from .scenario_loader import FunctionalStep, load_test_yaml

        return {"FunctionalStep": FunctionalStep, "load_test_yaml": load_test_yaml}[name]
    raise AttributeError(name)
