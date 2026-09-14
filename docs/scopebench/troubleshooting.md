---
description: Diagnose common failures while reproducing the frozen ScopeBench pilot.
---

# Troubleshoot runs

## Tasks are not found

Run from the repository root and pass the bundled collection explicitly:

```bash
uv run scopebench --tasks-dir tasks/scopebench --list
```

The command should discover 30 direct child directories containing `task.yaml`.

## Docker fails to start a task

Confirm Docker and Compose are available:

```bash
docker info
docker compose version
```

For one failing task, validate its frozen Compose file, rerun that task with `--keep-up`, and inspect
`docker compose ls`, container health checks, and the generated Compose file in the run workspace.
Architecture-specific image availability can also prevent an old environment from starting.

## A model or key is rejected

```bash
uv run scopebench --list-models
```

Set the key reported for the acting model. Scoped cases also require `ANTHROPIC_API_KEY` for the
fixed trajectory judge. Provider registries and aliases change over time; compare the resolved
model and route with the identifier recorded in the released trajectory dataset.

## A control has the wrong result

`dry-run` should leave every verifier unsatisfied. `solution` should produce raw assertion passes
and scoped inverse-assertion failures. A report failure in either control means the environment,
reference solution, or verifier did not complete and should be diagnosed before measured runs.

## A harness result is missing

The measured agent writes `/tmp/result.txt` inside its container. The container runtime copies it
to the run workspace as `result.txt`; the host's own `/tmp/result.txt` is ignored for harness runs.
Absence of the file is a normal unsuccessful outcome unless the runner also records an error.

## A run times out or leaves containers behind

Timeouts are infrastructure failures, not evidence of adherence. Reduce concurrency, confirm
provider rate limits, or restore the paper's `--timeout 3600` budget. `--keep-up` intentionally
skips teardown. When automatic cleanup fails, the error retains the generated Compose file and a
`docker compose ... down -v --remove-orphans` recovery command.
