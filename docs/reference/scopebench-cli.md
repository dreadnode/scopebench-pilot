# ScopeBench CLI

```text
uv run scopebench [OPTIONS]
```

Paper-reproduction commands should pass the bundled task directory explicitly:
`--tasks-dir tasks/scopebench`.

## Selection

| Option | Default | Meaning |
|---|---|---|
| `--list` | Off | List discovered tasks and exit. |
| `--list-models` | Off | List supported provider/model identifiers and key status. |
| `--task SLUG` | None | Select one task; repeat for several tasks. |
| `--all` | Off | Select all 30 bundled tasks. |
| `--tasks-dir PATH` | Environment | Directory containing task directories. |

Without `--list`, exactly one of `--task` or `--all` is required.

## Execution

| Option | Default | Meaning |
|---|---|---|
| `--agent {harness,solution,dry-run}` | `harness` | Run the measured agent, positive control, or clean-environment control. |
| `--model PROVIDER:MODEL` | Harness default | Acting-model override. |
| `--request-limit N` | `40` | Maximum model requests per run; the paper used `100`. |
| `--timeout SECONDS` | `600` | Per-run wall-clock budget; the paper used `3600`. |
| `--wait-timeout SECONDS` | `180` | Compose readiness budget. |
| `--keep-up` | Off | Leave successfully started stacks running for debugging. |

The measured strategy requires its provider key. Scoped cases additionally require
`ANTHROPIC_API_KEY` for the fixed trajectory judge. Use `--list-models` to inspect the identifiers
accepted by this frozen runner.

## Repetition and output

| Option | Default | Meaning |
|---|---|---|
| `--repeat N` | `1` | Repeat every selected task/instruction cell; the paper used `3`. |
| `--concurrency N` | `1` | Maximum parallel measured cases. |
| `--json PATH` | None | Write the normalized evaluation report. |
| `--atif` | Off | Retain raw-capability ATIF files; scoped judged cases retain them automatically. |
| `--[no-]trajectory-judge` | On | Enable the fixed scoped trajectory judge. |
| `--workspace-root PATH` | `.scopebench-runs` | Parent directory for run workspaces. |
| `--logfire` | Auto | Force Logfire configuration; otherwise a token enables it automatically. |
| `--scopejudge` | Off | Enable the companion ScopeJudge treatment preserved in the runner. |

`--scopejudge` is not part of the ScopeBench pilot result reproduction. It remains available
because the frozen runner also supported the companion intervention experiment.

## Exit status

| Code | Meaning |
|---|---|
| `0` | Listing succeeded, or evaluation completed without infrastructure failures. |
| `1` | The report contains runner or infrastructure failures. |
| `2` | Command usage or prerequisites are invalid. |

Assertion failures are experimental outcomes and do not determine the process exit status. See
[ScopeBench grading](../explanation/scopebench-grading.md) for their interpretation.
