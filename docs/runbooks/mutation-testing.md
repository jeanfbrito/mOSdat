# Mutation Testing

## Target

≥80% of mutants killed for the `automation/` package.

## Running locally

```bash
pip install -e ".[dev]"
mutmut run --paths-to-mutate automation/
mutmut results
```

A summary table shows survived (bad) and killed (good) mutants per file.
Exit code is non-zero if any mutants survived — this is expected during
active development; the threshold is enforced by human review, not CI.

## Running via CI

The workflow is `workflow_dispatch` only (it is slow — typically 10–30 min
depending on test suite size).  Trigger it from the Actions tab or via:

```bash
gh workflow run mutation.yml
```

## Interpreting survivors

A **survived mutant** means the test suite did not catch a code change.
This is a signal that test coverage for that code path is weak.

Steps to investigate a survivor:

1. `mutmut show <id>` — view the mutation diff.
2. Identify which function was mutated.
3. Write a test that would fail with that mutation applied.
4. Re-run `mutmut run` to confirm the new test kills it.

Common survivor patterns:

- Off-by-one in range/slice — add boundary tests.
- Boolean operator swap (`and` ↔ `or`) — add tests for both branches.
- Return value replacement — assert the return value explicitly.

## Fixing the threshold

If the killed percentage drops below 80%, do not disable the workflow.
Instead, add targeted tests until the threshold is met, then re-run.
