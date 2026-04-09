import re
from datetime import datetime
from pathlib import Path
from typing import TextIO

from .config import ProjectConfig
from .state import State, TestStatus


def generate_report(state: State, config: ProjectConfig) -> None:
    report_path = config.results_dir / "REPORT.md"

    with open(report_path, "w") as f:
        _write_header(f, state, config)
        _write_summary(f, state)
        _write_no_gpu_results(f, state, config)
        _write_gpu_results(f, state, config)
        if config.report.critical_tests:
            _write_critical_tests(f, state, config)
        _write_gpu_failure_analysis(f, state, config)
        _write_footer(f, state)

    print(f"[mOSdat] Report generated: {report_path}")


def _write_header(f: TextIO, state: State, config: ProjectConfig) -> None:
    f.write(f"# {config.report.title}\n\n")
    f.write(f"**App**: {config.app.name}\n")
    f.write(f"**Version**: {state.version}\n")
    f.write(f"**Date**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    f.write(f"**Results Directory**: `{config.results_dir}`\n\n")


def _write_summary(f: TextIO, state: State) -> None:
    passed = sum(1 for r in state.results.values() if r.status == TestStatus.PASSED)
    failed = sum(1 for r in state.results.values() if r.status == TestStatus.FAILED)
    skipped = sum(1 for r in state.results.values() if r.status == TestStatus.SKIPPED)
    total = len(state.results)

    f.write("## Summary\n\n")
    f.write("| Metric | Count |\n")
    f.write("|--------|-------|\n")
    f.write(f"| Total Tests | {total} |\n")
    f.write(f"| Passed | {passed} |\n")
    f.write(f"| Failed | {failed} |\n")
    f.write(f"| Skipped | {skipped} |\n\n")

    if failed == 0:
        f.write("**Status: ALL TESTS PASSED**\n\n")
    else:
        f.write(f"**Status: {failed} TESTS FAILED**\n\n")


def _write_no_gpu_results(f: TextIO, state: State, config: ProjectConfig) -> None:
    f.write("## Without GPU Results\n\n")
    f.write("| OS | Package | Status | Exit Code |\n")
    f.write("|----|---------|--------|-----------|\n")

    for vm in config.vms:
        for pkg in vm.packages:
            key = f"{vm.name}-{pkg.format}-no-gpu"
            result = state.results.get(key)
            if result:
                status_emoji = _status_emoji(result.status)
                exit_code = result.exit_code if result.exit_code is not None else "N/A"
                f.write(f"| {vm.name} | {pkg.format} | {status_emoji} {result.status.value} | {exit_code} |\n")
            else:
                f.write(f"| {vm.name} | {pkg.format} | - not run | - |\n")

    f.write("\n")


def _write_gpu_results(f: TextIO, state: State, config: ProjectConfig) -> None:
    f.write("## GPU Results\n\n")
    f.write("| OS | Package | Status | Exit Code |\n")
    f.write("|----|---------|--------|-----------|\n")

    for vm in config.vms:
        for pkg in vm.packages:
            key = f"{vm.name}-{pkg.format}-gpu"
            result = state.results.get(key)
            if result:
                status_emoji = _status_emoji(result.status)
                exit_code = result.exit_code if result.exit_code is not None else "N/A"
                f.write(f"| {vm.name} | {pkg.format} | {status_emoji} {result.status.value} | {exit_code} |\n")
            else:
                f.write(f"| {vm.name} | {pkg.format} | - not run | - |\n")

    f.write("\n")


def _parse_test_from_log(log_file: str, test_name: str) -> str:
    log_path = Path(log_file)
    if not log_path.exists():
        return "NOT_RUN"

    content = log_path.read_text()
    pattern = rf"RESULT:{re.escape(test_name)}:(PASS|FAIL|SKIP)(?::([A-Z_]+))?(?::(\d+))?"
    match = re.search(pattern, content)
    if match:
        result = match.group(1)
        detail = match.group(2) or ""
        if result == "FAIL" and detail:
            return f"FAIL ({detail})"
        return result
    return "NOT_FOUND"


def _write_critical_tests(f: TextIO, state: State, config: ProjectConfig) -> None:
    for test_name in config.report.critical_tests:
        f.write(f"## Critical Test: {test_name}\n\n")
        if config.report.critical_description:
            f.write(f"{config.report.critical_description}\n\n")

        all_passed = True

        f.write("| OS | Package | GPU Config | Result |\n")
        f.write("|----|---------|------------|--------|\n")

        for vm in config.vms:
            for pkg in vm.packages:
                for gpu in [False, True]:
                    gpu_label = "gpu" if gpu else "no-gpu"
                    key = f"{vm.name}-{pkg.format}-{gpu_label}"
                    result = state.results.get(key)

                    if result and result.log_file:
                        test_result = _parse_test_from_log(result.log_file, test_name)
                        if test_result == "PASS":
                            f.write(f"| {vm.name} | {pkg.format} | {gpu_label} | PASS |\n")
                        elif "FAIL" in test_result:
                            f.write(f"| {vm.name} | {pkg.format} | {gpu_label} | **{test_result}** |\n")
                            all_passed = False
                        else:
                            f.write(f"| {vm.name} | {pkg.format} | {gpu_label} | {test_result} |\n")
                    else:
                        f.write(f"| {vm.name} | {pkg.format} | {gpu_label} | - |\n")

        f.write("\n")

        if all_passed:
            f.write(f"**{test_name} validated across all tested configurations.**\n\n")
        else:
            f.write(f"**WARNING: {test_name} FAILED on some configurations. Review logs.**\n\n")


def _write_gpu_failure_analysis(f: TextIO, state: State, config: ProjectConfig) -> None:
    # Find GPU test scenarios from config
    gpu_tests = [t for t in config.tests if t.gpu]
    if not gpu_tests:
        return

    gpu_test_names = [t.name for t in gpu_tests]

    f.write("## GPU Test Failure Analysis\n\n")
    f.write("GPU tests may fail for reasons unrelated to the application:\n\n")

    header = "| OS | Package | " + " | ".join(gpu_test_names) + " | Notes |\n"
    separator = "|----|---------|" + "|".join("-" * (len(n) + 2) for n in gpu_test_names) + "|-------|\n"
    f.write(header)
    f.write(separator)

    for vm in config.vms:
        for pkg in vm.packages:
            key = f"{vm.name}-{pkg.format}-gpu"
            result = state.results.get(key)

            if result and result.log_file:
                test_results = []
                notes = []
                for t_name in gpu_test_names:
                    t_result = _parse_test_from_log(result.log_file, t_name)
                    test_results.append(t_result)

                    # Check known issues
                    for issue_key, issue_desc in config.report.known_issues.items():
                        parts = issue_key.split("+")
                        if len(parts) == 2:
                            pkg_match, test_match = parts
                            if pkg.format == pkg_match and test_match in t_name and "FAIL" in t_result:
                                notes.append(issue_desc)

                notes_str = ", ".join(notes) if notes else "-"
                results_str = " | ".join(test_results)
                f.write(f"| {vm.name} | {pkg.format} | {results_str} | {notes_str} |\n")
            else:
                dashes = " | ".join("-" for _ in gpu_test_names)
                f.write(f"| {vm.name} | {pkg.format} | {dashes} | not run |\n")

    f.write("\n")


def _write_footer(f: TextIO, state: State) -> None:
    f.write("---\n\n")
    f.write(f"*Report generated by mOSdat at {datetime.now().isoformat()}*\n")


def _status_emoji(status: TestStatus) -> str:
    return {
        TestStatus.PASSED: "",
        TestStatus.FAILED: "",
        TestStatus.SKIPPED: "",
        TestStatus.PENDING: "",
        TestStatus.RUNNING: "",
    }.get(status, "")
